import socket
import threading
import time

from firewall import FirewallRules
from frame import Frame
from protocol import (ACK_FIREWALLED, CAS_BASE_PORT, CCS_PORT, CTRL_HELLO_CAS,
                      CTRL_RULE, CTRL_RULES_DONE, HOST, SHADOW_PORT, addr_net,
                      addr_str)
from switch import Switch

UPLINK = "uplink"  # marks frames that arrived from the CCS


class CASSwitch(Switch):
    def __init__(self, net, ccs_port=CCS_PORT, shadow_port=SHADOW_PORT):
        super().__init__(f"CAS {net}", CAS_BASE_PORT + net)
        self.net = net
        self.ccs_port = ccs_port
        self.shadow_port = shadow_port
        self.table = {}                  # local node address -> connection (learned)
        self.firewall = FirewallRules()  # filled in by the CCS
        self.uplink = None
        self.uplink_lock = threading.Lock()
        self.on_shadow = False
        self.ready = threading.Event()   # set once the firewall rules have arrived

    def start(self):
        super().start()
        self.uplink = self._connect_uplink(self.ccs_port, "CCS")
        threading.Thread(target=self._uplink_loop, daemon=True).start()

    def _connect_uplink(self, port, label):
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((HOST, port))
                break
            except OSError:
                sock.close()
                time.sleep(0.2)
        sock.sendall(Frame.control(CTRL_HELLO_CAS, bytes([self.net])).to_bytes())
        self.log(f"connected to {label} on port {port}")
        return sock

    def _uplink_loop(self):
        while True:
            try:
                frame = Frame.read_from(self.uplink)
            except OSError:
                frame = None
            if frame is None:
                if not self.running:
                    return
                if self.on_shadow:
                    self.log("lost the shadow CCS too; no core switch left")
                    return
                self.log("*** main CCS is down, switching uplink to the SHADOW CCS ***")
                new_uplink = self._connect_uplink(self.shadow_port, "shadow CCS")
                with self.uplink_lock:
                    self.uplink = new_uplink
                    self.on_shadow = True
                continue
            if frame.is_control():
                self._handle_control(frame)
            else:
                self.buffer.put((frame, UPLINK))

    def _handle_control(self, frame):
        kind, body = frame.data[:1], frame.data[1:]
        if kind == CTRL_RULE:
            self.firewall.add(body.decode("ascii"))
        elif kind == CTRL_RULES_DONE:
            if not self.ready.is_set():
                self.log(f"firewall rules from CCS: {self.firewall}")
            self.ready.set()

    def send_up(self, frame):
        with self.uplink_lock:
            uplink = self.uplink
        try:
            with self.send_lock:
                uplink.sendall(frame.to_bytes())
        except OSError:
            self.log(f"uplink down, dropped {frame}")

    def handle(self, frame, in_conn):
        if frame.is_control():
            return  # nodes never send control frames
        from_ccs = in_conn is UPLINK

        if not from_ccs:
            with self.lock:  
                if self.table.get(frame.src) is not in_conn:
                    self.table[frame.src] = in_conn
                    self.log(f"learned node {addr_str(frame.src)}")

        if addr_net(frame.dst) != self.net:
            if from_ccs:
                return  # not for this star (CCS flooded it), ignore
            if not frame.is_ack() and self.firewall.node_blocked(frame.src):
                self.log(f"FIREWALL: {addr_str(frame.src)} is local-only, blocked {frame}")
                self.send(in_conn, Frame.make_ack(frame, ACK_FIREWALLED))
                return
            self.log(f"global, bridging to CCS: {frame}")
            self.send_up(frame)
            return

        if from_ccs and not frame.is_ack() and self.firewall.node_blocked(frame.dst):
            self.log(f"FIREWALL: {addr_str(frame.dst)} is local-only, blocked {frame}")
            self.send_up(Frame.make_ack(frame, ACK_FIREWALLED))
            return

        with self.lock:
            out_conn = self.table.get(frame.dst)
            conns = list(self.connections)
        if out_conn:
            self.log(f"forwarding {frame} to {addr_str(frame.dst)}")
            self.send(out_conn, frame)
        else:
            self.log(f"{addr_str(frame.dst)} unknown, flooding {frame}")
            self.flood(frame, conns, except_conn=in_conn)

    def forget(self, conn):
        for addr in [a for a, c in self.table.items() if c is conn]:
            del self.table[addr]

    def stop(self):
        self.running = False
        with self.uplink_lock:
            uplink = self.uplink
        if uplink:
            try:
                uplink.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            uplink.close()
        super().stop()
