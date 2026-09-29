"""
restore_node.py — repair a corrupted/deleted node copy (demo reset).

Copies a healthy copy of the file from another node back onto the target
node, so the Verify page returns to all-healthy.

    python scripts/restore_node.py <sha256-hash> --node 1
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
    parser = argparse.ArgumentParser(description="Restore one node's copy from a healthy node.")
    parser.add_argument("file_hash", help="SHA-256 hash of the file (64 hex chars)")
    parser.add_argument("--node", default="1", help="node number: 1, 2 or 3 (default 1)")
    args = parser.parse_args()

    label = "Node {}".format(args.node)
    print("Restoring {} copy of {} from a healthy node ...".format(label, args.file_hash))
    try:
        storage.repair_copy(args.file_hash, label)
    except Exception as exc:  # noqa: BLE001
        print("FAILED: {}".format(exc))
        sys.exit(1)

    status = storage.verify_integrity(args.file_hash)
    print("\nNode status after the repair:")
    for node_label, st in status.items():
        mark = "OK " if st["status"] == "OK" else ">>> "
        print("  {} {:<8} {}".format(mark, node_label, st["status"]))


if __name__ == "__main__":
    main()
