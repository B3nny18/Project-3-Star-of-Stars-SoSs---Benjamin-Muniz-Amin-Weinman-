# CSCI/IT-3053 Project 3: Star of Stars Network with Bridge and Firewall

## Group Members

- Benjamin Muniz
- Amin Weinman

## Overview

A Python simulation of a Star of Stars (SoSs) network over localhost TCP sockets. Each arm star has its own core switch (CAS) and 2-16 nodes. Every CAS connects to a central core switch (CCS), which bridges global traffic between the stars. The CCS reads the firewall rules, enforces the network-level rules itself, and sends every rule to the CASs so they can enforce the node-level rules locally. A shadow CCS stays in sync with the main CCS (firewall rules and forwarding table) and takes over immediately if the main CCS fails.

```
                 CCS (port 6000)  <== sync ==>  Shadow CCS (port 6001)
               /       |        \
        CAS 1 (5001)  CAS 2 (5002)  CAS 3 (5003) ...
        /  |  \        /  |  \        /  |  \
      1_1 1_2 ...    2_1 2_2 ...    3_1 3_2 ...
```

## How to Compile / Run

Requires Python 3 (standard library only, nothing to install). Python is interpreted, so there is no compile step; `make` byte-compiles every source file with warnings treated as errors to check the build.

1. Place the input files `nodex_y.txt` (x = star number, y = node number) and the firewall file `firewall.txt` in the same folder as the source code. Each input line has the form `destination: data`, for example `1_2: ABCDEFG`.
2. From inside the project folder, run:

   ```bash
   make                          # build check
   make run STARS="3 3 3"        # 3 stars with 3 nodes each
   make demo STARS="3 3 3"       # same, but the main CCS fails after 15 frames and the shadow takes over
   make clean                    # remove output files
   ```

   or directly:

   ```bash
   python3 main.py 3 4 2                       # one number per star = its node count (2-16), up to 15 stars
   python3 main.py 3 4 2 --fail-ccs 20         # main CCS fails after forwarding 20 frames
   python3 main.py 3 4 2 --firewall rules.txt  # use a different firewall file
   ```

   To generate random test input files: `make inputs STARS="3 4 2"`.

Each node writes the data it receives to `nodex_youtput.txt` (for example, `node1_2output.txt` contains `2_1: ABCDEFG`). When every node has sent all its frames and each one was acknowledged, firewalled, or given up on, the program shuts down the nodes, then the CASs, then the shadow and main CCS, and exits cleanly.

## Files

| File | Description |
|---|---|
| `main.py` | Entry point. Starts the CCS, the shadow CCS, one CAS per star, and the nodes, waits for all transmission to finish, then shuts everything down in order. |
| `protocol.py` | Shared constants: ports, address encoding, ACK types, control frame types, time-out, retry limit, error rates, and a thread-safe log function. |
| `frame.py` | `Frame` class: frame format, CRC, ACK and control frames, error injection, serialization to bytes, and reading a complete frame from a socket. |
| `firewall.py` | `FirewallRules` class: reads the firewall file and checks whether a network or node is blocked. |
| `switch.py` | `Switch` base class (from Project 2): listening socket, one worker thread per connection, global frame buffer, and the dispatcher thread. |
| `cas.py` | `CASSwitch` class: local learning switch, bridge to the CCS, node-level firewall, and failover to the shadow CCS. |
| `ccs.py` | `CCSSwitch` class: forwards global frames by star number, reads and distributes the firewall rules, enforces network-level rules, and keeps the shadow in sync. The same class runs the shadow CCS. |
| `node.py` | `Node` class: connects to its CAS, sends frames from its input file with stop-and-wait (buffer, time-out, retransmission), checks CRC, accepts only frames addressed to it, writes received data to its output file, sends ACKs, and injects errors. |
| `gen_inputs.py` | Our own random test-data generator. |
| `firewall.txt` | Example firewall rules. |
| `Makefile` | Build check, run, demo, input generation, and clean. |
| `README.md` | This file. |

## Frame Format

Every frame has a 4-byte header; ACK frames add a 1-byte ACK type, and data frames add the payload:

```
[DST][SRC][CRC][SIZE/ACK][ACK type][DATA]
```

| Field | Size | Valid Range | Purpose |
|---|---|---|---|
| DST | 1 byte | 1-255 | Address of the destination node. Switches use it to forward or flood; nodes discard frames whose DST is not their own address. |
| SRC | 1 byte | 1-255 | Address of the sending node. Switches learn which connection a node (CAS) or star (CCS) is on from this field. |
| CRC | 1 byte | 0-255 | Sum of the byte values of the whole frame, computed with CRC = 0, truncated to one byte. The destination recomputes it to detect errors. |
| SIZE/ACK | 1 byte | 0-255 | Payload length in bytes for data frames (1-255). A value of 0 marks the frame as an ACK. |
| ACK type | 1 byte | 0-3 | Only present in ACK frames. `00` no response in time-out (detected by the sender, never sent), `01` CRC error (resend), `10` firewalled (no need to resend), `11` positive ACK. |
| DATA | 1-255 bytes | ASCII text | The message payload. Absent for ACKs. |

**Addresses:** a node is numbered x_y but the address is one byte, so the high 4 bits hold the star number x (1-15) and the low 4 bits hold y - 1 (so y can be 1-16): `address = (x << 4) | (y - 1)`. For example, node 1_1 = `0x10`, node 2_6 = `0x25`. The CCS only reads the high 4 bits.

**Example:** node 2_1 sending `ABCDEFG` to node 1_2 is `11 20 14 07 41 42 43 44 45 46 47` (hex). Node 1_2's ACK back to node 2_1 is `20 11 34 00 03`.

Because TCP is a byte stream, a receiver reads exactly 4 header bytes, then exactly 1 ACK type byte (if SIZE is 0) or exactly SIZE payload bytes, so back-to-back frames are never confused.

**Control frames (switch to switch only):** addresses 1-15 (star 0) never belong to a node, so control frames use SRC = DST = `0x01`. The first data byte gives the type:

| Type | Direction | Purpose |
|---|---|---|
| `H` + star number | CAS → CCS | Hello: tells the core switch which star is on this link. |
| `S` | Shadow → CCS | Hello from the shadow: start syncing. |
| `F` + rule text | CCS → CAS / Shadow | One firewall rule, for example `F3_2` or `F2_#`. One rule per frame, so any number of rules fits. |
| `R` | CCS → CAS / Shadow | All rules have been sent. |
| `T` + star number | CCS → Shadow | Forwarding table entry: this star is on the network. |

## Firewall

The CCS reads `firewall.txt` at start-up:

```
3_2: local
2_#: local
```

- `X_#: local`: star X only exchanges local traffic. The **CCS** blocks any global frame to or from star X and sends back ACK type `10`.
- `X_Y: local`: node X_Y only exchanges local traffic. The CCS sends every rule to every CAS at start-up, and **CAS X** blocks global frames to or from node X_Y and sends back ACK type `10`.

We read "local only" as blocking global traffic in both directions, so a local-only node never waits on an ACK that would itself be blocked.

## Feature Checklist

| Feature | Status | Description |
|---|---|---|
| Project compiles/builds without warnings or errors | Complete | Python, no build step. `make` byte-compiles all files with warnings treated as errors. |
| **Switch class** | Complete | Shared `Switch` base class reused from Project 2; `CASSwitch` and `CCSSwitch` build on it. |
| CAS, CCS switches have a frame queue, and read/write appropriately | Complete | Worker threads put every received frame (with its incoming connection) into one thread-safe queue; a dispatcher thread handles them, and a send lock keeps writes from interleaving. |
| CAS, CCS switches allow multiple connections | Complete | A listening socket accepts connections and starts one worker thread per connection. |
| CAS, CCS switches flood frames when they don't know the destination | Complete | The CAS floods to its nodes; the CCS floods to all CASs. |
| CAS, CCS switches learn destinations, and don't forward packets to any port except the one required | Complete | The CAS learns node → connection; the CCS learns star → CAS connection, from each frame's SRC field. |
| CAS connects to CCS | Complete | Each CAS connects to the CCS and sends a hello with its star number. |
| CAS receives local firewall rules | Complete | Rules arrive from the CCS before any node starts sending. |
| CAS forwards traffic and ACKs properly | Complete | Local frames go to the node, global frames are bridged to the CCS, and ACKs follow the same path back. |
| CCS switch opens the firewall file and gets the rules | Complete | |
| CCS passes global traffic | Complete | Forwards by star number only. |
| CCS does the global firewalls | Complete | `X_#` rules are enforced at the CCS with ACK type `10`. |
| CCS Shadow switches run and test properly | Complete | Run with `--fail-ccs K` or `make demo`. The shadow is synced, detects the failure, and the CASs reconnect to it; frames lost in the failure are resent by their nodes. |
| **Node class** | Complete | |
| Nodes instantiate, and open connection to the switch | Complete | Nodes retry the connection until their CAS is listening. |
| Nodes open their input files, and send data to switch | Complete | Each node reads `nodex_y.txt` line by line and sends each line with stop-and-wait: buffer the frame, wait up to 2 s, resend on `01` or time-out, at most 5 attempts, then report an error to the SoSs admin. |
| Nodes open their output files, and save data that they received | Complete | Received data is written to `nodex_youtput.txt` as `source: data` before the ACK is sent. Flooded frames and duplicates are never saved. |
| Node will sometimes drop acknowledgment | Complete | 5% chance; the sender times out and resends, and the receiver recognizes the duplicate. |
| Node will sometimes create erroneous frame | Complete | 5% chance of sending a frame with one data byte changed; the destination detects the CRC error and replies `01`. |
| Node will sometimes reject traffic | Complete | Firewalled frames get ACK type `10` and are not resent; corrupted frames get `01`. |

## Shadow CCS

The shadow CCS starts before the CASs, connects to the main CCS, and receives the firewall rules and every forwarding table entry as it is learned, so it is always in sync. With `--fail-ccs K`, the main CCS drops all of its links without warning after forwarding K frames (a simulated power or hardware failure). The shadow detects the lost sync link and takes over, each CAS reconnects its uplink to the shadow and gets the rules again, and any frame lost inside the failed CCS times out at its sender and is resent through the shadow.

## Testing Performed

The program was run with layouts from `2 2` up to `16 16 2` (34 nodes), with and without firewall rules, and with the main CCS failing at different points (`--fail-ccs` from 5 to 64). A checker script compared every output file with the input files: in every run each allowed message arrived exactly once, every message the firewall should block was blocked, nothing was missing or duplicated, and the program exited cleanly. The error cases (CRC error, dropped ACK, time-out, firewall NACK, and a frame whose ACK was lost in the CCS failure) were all observed in the logs.

## Known Bugs / Limitations

No functional bugs known. Minor limitations:

- The frame format has no sequence number, so a resent frame is recognized as a duplicate by comparing it with the last message saved from the same sender. If an input file sends the exact same text to the same node twice in a row, the second copy is acknowledged but not saved again.
- If an ACK arrives after the 2 s time-out (not seen on localhost), it could be taken as the ACK for the next frame sent to the same destination.
- Only one failover is supported. If the shadow CCS also fails, global traffic stops and senders report an error after 5 attempts; local traffic keeps working.
- The ports are fixed (CCS 6000, shadow 6001, CAS x on 5000 + x) so everything knows where to connect. If another program is using them, the program exits with an error message; wait a few seconds or stop the other program.
- Console output comes from many threads, so lines can appear slightly out of order (for example, a CAS can log an ACK just before the node's "sent ACK" line). This only affects how the log looks, not the data.
- `make` and `main.py` must be run from inside the project folder, because the input and output files are in the current directory.

## Git Repository

https://github.com/B3nny18/Project-3-Star-of-Stars-SoSs---Benjamin-Muniz-Amin-Weinman-
```