import struct

from protocol import ACK_NAMES, CTRL_ADDR, addr_net, addr_str


# TCP is a byte stream, so keep reading until exactly n bytes have arrived.
# Returns None if the other side closed the connection. (Reused from Project 2.)
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


class Frame:
    HEADER_SIZE = 4  # DST, SRC, CRC, SIZE/ACK (the ACK type byte is read separately)

    def __init__(self, dst, src, data=b"", ack_type=None, crc=None):
        if not 1 <= src <= 255 or not 1 <= dst <= 255:
            raise ValueError("addresses must be 1-255")
        if len(data) > 255:
            raise ValueError("payload must be at most 255 bytes")
        if len(data) == 0 and ack_type is None:
            raise ValueError("a frame with no data must be an ACK and needs an ACK type")
        self.dst = dst
        self.src = src
        self.data = data
        self.size = len(data)
        self.ack_type = ack_type if self.size == 0 else None
        # a received frame keeps the CRC it arrived with; a new frame computes its own
        self.crc = self.compute_crc() if crc is None else crc

    @classmethod
    def make_ack(cls, original, ack_type):
        # an ACK goes back to the original sender, so src and dst are swapped
        return cls(dst=original.src, src=original.dst, ack_type=ack_type)

    @classmethod
    def control(cls, kind, payload=b""):
        return cls(dst=CTRL_ADDR, src=CTRL_ADDR, data=kind + payload)

    def corrupted(self):
        """A copy whose first data byte is changed but whose CRC is not,
        so the receiver's CRC check fails (simulates a transmission error)."""
        bad = bytes([(self.data[0] + 1) & 0xFF]) + self.data[1:]
        return Frame(self.dst, self.src, bad, crc=self.crc)

    def is_ack(self):
        return self.size == 0

    def is_control(self):
        return addr_net(self.src) == 0

    def compute_crc(self):
        return sum(self._pack(crc=0)) & 0xFF

    def crc_ok(self):
        return self.crc == self.compute_crc()

    def _pack(self, crc):
        raw = struct.pack("!BBBB", self.dst, self.src, crc, self.size)
        if self.is_ack():
            raw += struct.pack("!B", self.ack_type)
        return raw + self.data

    def to_bytes(self):
        return self._pack(self.crc)

    # Read the fixed 4-byte header, then the ACK type byte (ACKs) or SIZE data bytes.
    @classmethod
    def read_from(cls, sock):
        header = recv_exact(sock, cls.HEADER_SIZE)
        if header is None:
            return None
        dst, src, crc, size = struct.unpack("!BBBB", header)
        if size == 0:
            ack = recv_exact(sock, 1)
            if ack is None:
                return None
            return cls(dst, src, ack_type=ack[0], crc=crc)
        data = recv_exact(sock, size)
        if data is None:
            return None
        return cls(dst, src, data, crc=crc)

    def __repr__(self):
        if self.is_control():
            return f"CTRL({self.data[:1].decode()})"
        route = f"{addr_str(self.src)}->{addr_str(self.dst)}"
        if self.is_ack():
            return f"ACK[{ACK_NAMES.get(self.ack_type, self.ack_type)}]({route})"
        return f"Frame({route}, {self.size}B, {self.data!r})"
