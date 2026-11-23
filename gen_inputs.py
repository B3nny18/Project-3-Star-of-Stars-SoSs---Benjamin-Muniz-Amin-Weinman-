"""Test-data generator (ours; the course provides its own script for grading).

Usage: python3 gen_inputs.py N1 [N2 ...] [--lines L] [--seed S]
Writes nodex_y.txt for every node, each line "dest_x_y: random text",
mixing local (same star) and global (other star) destinations.
"""

import argparse
import random
import string


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stars", type=int, nargs="+")
    parser.add_argument("--lines", type=int, default=6, help="lines per node (default 6)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    nodes = [(net, n) for net, count in enumerate(args.stars, start=1)
             for n in range(1, count + 1)]
    for net, n in nodes:
        others = [node for node in nodes if node != (net, n)]  # never send to itself
        with open(f"node{net}_{n}.txt", "w", encoding="ascii") as f:
            for _ in range(args.lines):
                dnet, dn = rng.choice(others)
                text = "".join(rng.choice(string.ascii_letters + string.digits + " ")
                               for _ in range(rng.randint(1, 40)))
                f.write(f"{dnet}_{dn}: {text.strip() or 'x'}\n")
    print(f"wrote input files for {len(nodes)} nodes")


if __name__ == "__main__":
    main()
