import random
import socket
import threading
import time

from frame import Frame
from protocol import (ACK_CRC_ERROR, ACK_FIREWALLED, ACK_NAMES, ACK_OK,
                      ACK_DROP_RATE, ACK_TIMEOUT_SECONDS, CAS_BASE_PORT,
                      ERROR_RATE, HOST, MAX_ATTEMPTS, addr_str, log, make_addr,
                      parse_addr)


class Node:
    def __init__(self, net, number):
        self.net = net
        self.number = number
        self.addr = make_addr(net, number)
        self.name = f"Node {net}_{number}"
        self.port = CAS_BASE_PORT + net
        self.sock = None
        self.entries = []                    # (destination address, payload) from the input file
        self.send_lock = threading.Lock()    # sender and receiver threads both write
        self.output = None
        self.output_lock = threading.Lock()
        self.threads = []
        self.done = threading.Event()        # set when every frame is ACKed or given up on

        # the frame waiting for an answer (only one at a time: stop-and-wait)
        self.ack_lock = threading.Lock()
        self.ack_event = threading.Event()
        self.awaiting_from = None
        self.ack_type = None

        self.last_saved = {}  # source -> last payload saved, to spot retransmitted duplicates
        self.stats = {"delivered": 0, "firewalled": 0, "failed": 0, "resends": 0}

    def log(self, message):
        log(self.name, message)

    # Each line is "x_y: data"; partition splits only at the first ": "
    # so the data itself may contain colons.
    def _load_input(self):
        entries = []
        with open(f"node{self.net}_{self.number}.txt", "r", encoding="ascii") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                dst, _, data = line.partition(": ")
                entries.append((parse_addr(dst), data.encode("ascii")))
        return entries

    def _connect(self):
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((HOST, self.port))
                self.log(f"connected to CAS {self.net}")
                return sock
            except OSError:
                sock.close()
                time.sleep(0.2)

    # Phase 1: connect and start receiving.
    def start(self):
        self.entries = self._load_input()
        self.sock = self._connect()
        self.output = open(f"node{self.net}_{self.number}output.txt", "w", encoding="ascii")
        receiver = threading.Thread(target=self._receive_loop, daemon=True)
        receiver.start()
        self.threads.append(receiver)

    # Phase 2: called by main once every node is connected.
    def start_sending(self):
        sender = threading.Thread(target=self._send_loop, daemon=True)
        sender.start()
        self.threads.append(sender)

    def _send_loop(self):
        for dst, data in self.entries:
            self._deliver(Frame(dst=dst, src=self.addr, data=data))
        self.log(f"finished sending: {self.stats}")
        self.done.set()

    def _deliver(self, frame):
        """Send one buffered frame until it is ACKed, firewalled, or out of attempts."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with self.ack_lock:
                self.awaiting_from = frame.dst
                self.ack_type = None
                self.ack_event.clear()

            if random.random() < ERROR_RATE:
                self.log(f"(error injected) sending CORRUPTED copy of {frame}, attempt {attempt}")
                self._send(frame.corrupted())
            else:
                self.log(f"sending {frame}, attempt {attempt}")
                self._send(frame)

            if not self.ack_event.wait(ACK_TIMEOUT_SECONDS):
                self.log(f"no response within {ACK_TIMEOUT_SECONDS:g}s (00), resending {frame}")
                self.stats["resends"] += 1
                continue
            ack_type = self.ack_type
            if ack_type == ACK_OK:
                self.stats["delivered"] += 1
                return  # frame removed from buffer
            if ack_type == ACK_FIREWALLED:
                self.log(f"{frame} was FIREWALLED (10), not resending")
                self.stats["firewalled"] += 1
                return
            self.log(f"got {ACK_NAMES.get(ack_type, ack_type)} for {frame}, resending")
            self.stats["resends"] += 1

        self.stats["failed"] += 1
        log("SoSs ADMIN", f"ERROR: {self.name} gave up on {frame} after {MAX_ATTEMPTS} attempts")

    def _send(self, frame):
        try:
            with self.send_lock:
                self.sock.sendall(frame.to_bytes())
        except OSError:
            pass  # socket closed during shutdown

    def _receive_loop(self):
        while True:
            try:
                frame = Frame.read_from(self.sock)
            except OSError:
                frame = None  # socket closed by stop()
            if frame is None:
                return

            # flooded frames reach every node, so drop the ones meant for someone else
            if frame.dst != self.addr:
                self.log(f"discarded {frame} (not for me)")
                continue

            if frame.is_ack():
                self._handle_ack(frame)
            else:
                self._handle_data(frame)

    def _handle_ack(self, frame):
        if not frame.crc_ok():
            return  # damaged ACK counts as no ACK; the time-out will resend
        with self.ack_lock:
            if self.awaiting_from == frame.src and not self.ack_event.is_set():
                self.ack_type = frame.ack_type
                self.ack_event.set()

    def _handle_data(self, frame):
        if not frame.crc_ok():
            self.log(f"CRC ERROR in {frame}, sending NACK 01")
            self._send(Frame.make_ack(frame, ACK_CRC_ERROR))
            return

        # a resend of the last frame from this source (its ACK was lost) is not saved twice
        if self.last_saved.get(frame.src) == frame.data:
            self.log(f"duplicate {frame} (earlier ACK was lost), not saving again")
        else:
            # write before ACKing, so an ACK guarantees the data is saved
            with self.output_lock:
                self.output.write(f"{addr_str(frame.src)}: {frame.data.decode('ascii')}\n")
                self.output.flush()
            self.last_saved[frame.src] = frame.data

        if random.random() < ACK_DROP_RATE:
            self.log(f"(error injected) received {frame} but NOT acknowledging it")
            return
        self._send(Frame.make_ack(frame, ACK_OK))
        self.log(f"received {frame}, sent ACK 11")

    def stop(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # unblocks the receiver's read
        except OSError:
            pass
        self.sock.close()
        for t in self.threads:
            t.join(timeout=2)
        with self.output_lock:
            self.output.close()
        self.log("stopped")
