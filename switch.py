import queue
import socket
import threading

from frame import Frame
from protocol import HOST, log


class Switch:
    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.server = None
        self.buffer = queue.Queue()        # frame queue: (frame, connection it came in on)
        self.connections = []              # accepted connections
        self.lock = threading.Lock()       # protects tables and connections
        self.connected = threading.Condition(self.lock)  # notified when something connects
        self.send_lock = threading.Lock()  # several threads may send; never interleave writes
        self.running = False

    def log(self, message):
        log(self.name, message)

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((HOST, self.port))
        self.server.listen(32)
        self.running = True
        self.log(f"listening on port {self.port}")
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._dispatch_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                break  # server socket closed by stop()
            with self.lock:
                self.connections.append(conn)
                self.connected.notify_all()
            threading.Thread(target=self._worker, args=(conn,), daemon=True).start()

    def _worker(self, conn):
        while True:
            try:
                frame = Frame.read_from(conn)
            except OSError:
                frame = None
            if frame is None:
                self._remove_connection(conn)
                return
            self.buffer.put((frame, conn))

    def _dispatch_loop(self):
        while True:
            item = self.buffer.get()
            if item is None:  # shutdown signal from stop()
                return
            frame, in_conn = item
            self.handle(frame, in_conn)

    def handle(self, frame, in_conn):
        raise NotImplementedError

    def send(self, conn, frame):
        """Returns False if the connection is dead."""
        try:
            with self.send_lock:
                conn.sendall(frame.to_bytes())
            return True
        except OSError:
            self._remove_connection(conn)
            return False

    def flood(self, frame, conns, except_conn):
        for conn in conns:
            if conn is not except_conn:
                self.send(conn, frame)

    def _remove_connection(self, conn):
        with self.lock:
            if conn in self.connections:
                self.connections.remove(conn)
            self.forget(conn)
        try:
            conn.close()
        except OSError:
            pass

    def forget(self, conn):
        """Drop table entries for a closed connection (caller holds self.lock)."""

    def wait_for_connections(self, count, timeout=None):
        with self.connected:
            return self.connected.wait_for(lambda: len(self.connections) >= count, timeout)

    # Wake every blocked thread: the dispatcher via a None sentinel, accept()
    # by closing the server socket, workers by closing their connections.
    def stop(self):
        self.running = False
        self.buffer.put(None)
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        with self.lock:
            conns = list(self.connections)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.log("shut down")
