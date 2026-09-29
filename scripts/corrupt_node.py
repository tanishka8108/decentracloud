"""
corrupt_node.py — SIMULATE a corrupted storage node (fault-tolerance demo).

It overwrites one node's copy of a file with tampered bytes while keeping
the same object name (the SHA-256 hash), exactly as if that copy had been
altered behind your back.

    python scripts/corrupt_node.py <sha256-hash> --node 1

Then open the file's Verify page:
    Node 1 -> Integrity Failed, Nodes 2/3 -> Healthy,
    and the file still downloads from a healthy node.

Repair with scripts/restore_node.py.
"""

import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env"))

import storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Corrupt one node's copy of a file (demo).")
    parser.add_argument("file_hash", help="SHA-256 hash of the file (64 hex chars)")
    parser.add_argument("--node", default="1", help="node number: 1, 2 or 3 (default 1)")
    args = parser.parse_args()

    label = "Node {}".format(args.node)
    print("Corrupting {} copy of {} ...".format(label, args.file_hash))
    try:
        storage.corrupt_copy(args.file_hash, label)
    except Exception as exc:  # noqa: BLE001
        print("FAILED: {}".format(exc))
        sys.exit(1)

    status = storage.verify_integrity(args.file_hash)
    print("\nNode status after the attack:")
    for node_label, st in status.items():
        mark = "OK " if st["status"] == "OK" else ">>> "
        print("  {} {:<8} {}".format(mark, node_label, st["status"]))
    print("\nNow open the Verify page in the app and try a download.")


if __name__ == "__main__":
    main()
