import socket
import threading
import time

from firewall import FirewallRules
from frame import Frame
from protocol import (ACK_FIREWALLED, CTRL_HELLO_CAS, CTRL_HELLO_SHADOW,
                      CTRL_RULE, CTRL_RULES_DONE, CTRL_TABLE, HOST, addr_net)
from switch import Switch


class CCSSwitch(Switch):
    def __init__(self, port, firewall_path=None, shadow_of=None, fail_after=None):
        super().__init__("SHADOW CCS" if shadow_of else "CCS", port)
        self.is_shadow = shadow_of is not None
        self.main_port = shadow_of
        self.firewall = FirewallRules() if self.is_shadow else FirewallRules.load(firewall_path)
        self.table = {}             # network number -> CAS connection
        self.known_networks = set()  # networks in the forwarding table (synced to the shadow)
        self.cas_conns = set()
        self.shadow_conn = None      # main CCS only: link to the shadow
        self.main_link = None        # shadow only: link to the main CCS
        self.synced = threading.Event()  # shadow only: set once it has the rules
        self.fail_after = fail_after     # main CCS only: simulated failure after N frames
        self.frames_forwarded = 0
        self.failed = False

    def start(self):
        super().start()
        if self.is_shadow:
            self.main_link = self._connect_to_main()
            threading.Thread(target=self._sync_loop, daemon=True).start()
        else:
            self.log(f"firewall rules loaded: {self.firewall}")

    def handle(self, frame, in_conn):
        if self.failed:
            return
        if frame.is_control():
            self._handle_control(frame, in_conn)
            return

        src_net, dst_net = addr_net(frame.src), addr_net(frame.dst)
        with self.lock:  
            if self.table.get(src_net) is not in_conn:
                self._learn(src_net, in_conn)

        # global firewall (data frames only; ACKs are answers to allowed frames)
        if not frame.is_ack() and self.firewall.blocks_global(frame):
            self.log(f"FIREWALL: network rule blocks {frame}, sending NACK 10")
            self.send(in_conn, Frame.make_ack(frame, ACK_FIREWALLED))
            return

        with self.lock:
            out_conn = self.table.get(dst_net)
            conns = list(self.cas_conns)
        if out_conn:
            self.log(f"forwarding {frame} to CAS {dst_net}")
            self.send(out_conn, frame)
        else:
            self.log(f"network {dst_net} unknown, flooding {frame} to all CASs")
            self.flood(frame, conns, except_conn=in_conn)

        self.frames_forwarded += 1
        if self.fail_after is not None and self.frames_forwarded >= self.fail_after:
            self.fail()

    def _learn(self, net, conn):
        """Add/refresh a forwarding entry (caller holds self.lock) and sync the shadow."""
        self.table[net] = conn
        self.cas_conns.add(conn)
        if net not in self.known_networks:
            self.known_networks.add(net)
            self.log(f"forwarding table: network {net} -> CAS {net}")
            if self.shadow_conn:
                self.send(self.shadow_conn, Frame.control(CTRL_TABLE, bytes([net])))

    def _handle_control(self, frame, in_conn):
        kind = frame.data[:1]
        if kind == CTRL_HELLO_CAS:
            net = frame.data[1]
            with self.lock:
                self._learn(net, in_conn)
            self.log(f"CAS {net} connected, sending it the firewall rules")
            self._send_rules(in_conn)
        elif kind == CTRL_HELLO_SHADOW:
            self.shadow_conn = in_conn
            self.log("shadow CCS connected, syncing rules and forwarding table")
            self._send_rules(in_conn, done=False)
            with self.lock:
                nets = sorted(self.known_networks)
            for net in nets:
                self.send(in_conn, Frame.control(CTRL_TABLE, bytes([net])))
            self.send(in_conn, Frame.control(CTRL_RULES_DONE))

    def _send_rules(self, conn, done=True):
        # one rule per control frame, so any number of rules fits
        for rule in self.firewall.rules:
            self.send(conn, Frame.control(CTRL_RULE, rule.encode("ascii")))
        if done:
            self.send(conn, Frame.control(CTRL_RULES_DONE))

    def forget(self, conn):
        self.cas_conns.discard(conn)
        for net in [n for n, c in self.table.items() if c is conn]:
            del self.table[net]  # connection gone; known_networks keeps the network

    def fail(self):
        """Simulate a power/hardware failure: drop every link without warning."""
        self.failed = True
        self.log(f"*** SIMULATED FAILURE after {self.frames_forwarded} frames: CCS going down ***")
        self.stop()

    def _connect_to_main(self):
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((HOST, self.main_port))
                break
            except OSError:
                sock.close()
                time.sleep(0.2)
        sock.sendall(Frame.control(CTRL_HELLO_SHADOW).to_bytes())
        self.log("connected to main CCS, in standby")
        return sock

    def _sync_loop(self):
        while True:
            try:
                frame = Frame.read_from(self.main_link)
            except OSError:
                frame = None
            if frame is None:
                if self.running:
                    self.log("*** main CCS failure detected: SHADOW CCS TAKING OVER "
                             f"(rules: {self.firewall}; networks: {sorted(self.known_networks)}) ***")
                return
            kind, body = frame.data[:1], frame.data[1:]
            if kind == CTRL_RULE:
                self.firewall.add(body.decode("ascii"))
            elif kind == CTRL_TABLE:
                with self.lock:
                    self.known_networks.add(body[0])
                if self.synced.is_set():
                    self.log(f"sync: network {body[0]} added to forwarding table")
            elif kind == CTRL_RULES_DONE:
                self.log(f"in sync: rules {self.firewall}; networks {sorted(self.known_networks)}")
                self.synced.set()

    def stop(self):
        was_running = self.running
        self.running = False  # first, so the sync loop knows this close is a shutdown
        if self.main_link:
            try:
                self.main_link.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.main_link.close()
        if was_running:  # already down if a simulated failure stopped it
            super().stop()
