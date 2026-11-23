import argparse
import os
import sys
import threading

from cas import CASSwitch
from ccs import CCSSwitch
from node import Node
from protocol import (CCS_PORT, MAX_NETWORKS, MAX_NODES, MIN_NODES, SHADOW_PORT,
                      log)


def parse_args():
    parser = argparse.ArgumentParser(description="Star of Stars network simulator")
    parser.add_argument("stars", type=int, nargs="+", metavar="N",
                        help=f"nodes in each arm star ({MIN_NODES}-{MAX_NODES})")
    parser.add_argument("--firewall", default="firewall.txt",
                        help="firewall rules file (default: firewall.txt)")
    parser.add_argument("--fail-ccs", type=int, metavar="K", default=None,
                        help="main CCS fails after forwarding K frames")
    args = parser.parse_args()
    if len(args.stars) > MAX_NETWORKS:
        parser.error(f"at most {MAX_NETWORKS} stars")
    for count in args.stars:
        if not MIN_NODES <= count <= MAX_NODES:
            parser.error(f"each star needs {MIN_NODES}-{MAX_NODES} nodes, got {count}")
    return args


def check_input_files(stars):
    missing = [f"node{net}_{n}.txt"
               for net, count in enumerate(stars, start=1)
               for n in range(1, count + 1)
               if not os.path.isfile(f"node{net}_{n}.txt")]
    if missing:
        sys.exit(f"Error: missing input file(s): {', '.join(missing)}")


def main():
    args = parse_args()
    check_input_files(args.stars)
    if not os.path.isfile(args.firewall):
        log("Main", f"no firewall file '{args.firewall}', running with no rules")

    # 1. core switch and its shadow (the shadow syncs before anything else starts)
    ccs = CCSSwitch(CCS_PORT, firewall_path=args.firewall, fail_after=args.fail_ccs)
    shadow = CCSSwitch(SHADOW_PORT, shadow_of=CCS_PORT)
    try:
        ccs.start()
        shadow.start()
    except OSError as e:
        sys.exit(f"Error: core switch could not start: {e.strerror}. "
                 f"Wait a few seconds or stop the process using ports {CCS_PORT}-{SHADOW_PORT}.")
    shadow.synced.wait()

    # 2. one CAS per star; each gets its firewall rules from the CCS before nodes start
    cass = [CASSwitch(net) for net in range(1, len(args.stars) + 1)]
    for cas in cass:
        try:
            cas.start()
        except OSError as e:
            sys.exit(f"Error: CAS {cas.net} could not start on port {cas.port}: {e.strerror}")
    for cas in cass:
        cas.ready.wait()

    # 3. nodes; Node.start() blocks while connecting, so each starts in its own thread
    nodes = [Node(net, n)
             for net, count in enumerate(args.stars, start=1)
             for n in range(1, count + 1)]
    starters = [threading.Thread(target=node.start) for node in nodes]
    for t in starters:
        t.start()
    for t in starters:
        t.join()
    # wait until every CAS has accepted all of its nodes, so no early flood misses one
    for cas, count in zip(cass, args.stars):
        cas.wait_for_connections(count)
    log("Main", f"{len(nodes)} nodes in {len(cass)} stars connected, starting transmission")

    for node in nodes:
        node.start_sending()

    # stop-and-wait: a node is done only when every frame it sent was answered or given up
    for node in nodes:
        node.done.wait()
    log("Main", "all nodes finished sending, shutting down")

    totals = {}
    for node in nodes:
        for key, value in node.stats.items():
            totals[key] = totals.get(key, 0) + value
    log("Main", f"totals: {totals}")

    for node in nodes:
        node.stop()
    for cas in cass:
        cas.stop()
    shadow.stop()
    ccs.stop()
    log("Main", "clean exit")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user, exiting.")
        sys.exit(1)
