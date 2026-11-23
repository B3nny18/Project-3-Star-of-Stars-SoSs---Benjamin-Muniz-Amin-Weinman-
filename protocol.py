import threading

HOST = "127.0.0.1"

CCS_PORT = 6000          # main core switch (CCS)
SHADOW_PORT = 6001       # shadow core switch, takes over when the CCS fails
CAS_BASE_PORT = 5000     # CAS for network x listens on 5000 + x

# One address byte holds both numbers of a node x_y:
#   high 4 bits = network number x (1-15)
#   low 4 bits  = node number y - 1 (so y can be 1-16)
# Example: node 1_1 = 0x10, node 2_6 = 0x25, node 15_16 = 0xFF.
# Network 0 (bytes 1-15) never belongs to a node; it is reserved for switch
# control frames (firewall rules, hellos, shadow sync).
MAX_NETWORKS = 15
MIN_NODES = 2
MAX_NODES = 16
CTRL_ADDR = 0x01


def make_addr(net, node):
    if not 1 <= net <= MAX_NETWORKS:
        raise ValueError(f"network must be 1-{MAX_NETWORKS}, got {net}")
    if not 1 <= node <= MAX_NODES:
        raise ValueError(f"node must be 1-{MAX_NODES}, got {node}")
    return (net << 4) | (node - 1)


def addr_net(addr):
    return addr >> 4


def addr_node(addr):
    return (addr & 0x0F) + 1


def addr_str(addr):
    if addr_net(addr) == 0:
        return "CTRL"
    return f"{addr_net(addr)}_{addr_node(addr)}"


def parse_addr(text):
    """'2_6' -> address byte."""
    net, node = text.strip().split("_")
    return make_addr(int(net), int(node))


ACK_TIMEOUT = 0b00      # no response in time-out: resend (detected by the sender itself)
ACK_CRC_ERROR = 0b01    # CRC error: resend
ACK_FIREWALLED = 0b10   # blocked by a firewall: do not resend
ACK_OK = 0b11           # positive ACK

ACK_NAMES = {ACK_TIMEOUT: "00 timeout", ACK_CRC_ERROR: "01 CRC error",
             ACK_FIREWALLED: "10 firewalled", ACK_OK: "11 ACK"}

# Control frames have SRC = DST = CTRL_ADDR. The first data byte is the type.
CTRL_HELLO_CAS = b"H"     # CAS -> CCS: "I am network x" (1 more byte: x)
CTRL_HELLO_SHADOW = b"S"  # shadow -> CCS: "I am your shadow, keep me in sync"
CTRL_RULE = b"F"          # CCS -> CAS/shadow: one firewall rule, e.g. b"F3_5" or b"F2_#"
CTRL_RULES_DONE = b"R"    # CCS -> CAS/shadow: all rules sent
CTRL_TABLE = b"T"         # CCS -> shadow: forwarding table entry (1 more byte: network)

ACK_TIMEOUT_SECONDS = 2.0   # how long a node waits for an ACK before resending
MAX_ATTEMPTS = 5            # transmissions per frame before giving up
ERROR_RATE = 0.05           # chance a node sends a corrupted frame
ACK_DROP_RATE = 0.05        # chance a node fails to acknowledge a frame

_print_lock = threading.Lock()


def log(who, message):
    # one lock for all threads so lines never interleave
    with _print_lock:
        print(f"[{who}] {message}", flush=True)
