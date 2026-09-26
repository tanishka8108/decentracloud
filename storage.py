"""
storage.py
Simulates a decentralized storage layer. Instead of one central folder,
every file is replicated across several independent "storage nodes"
(node_1, node_2, node_3 — think of these as different cloud regions /
providers). Files are content-addressed: the file is saved using its
own SHA-256 hash as the filename, exactly like IPFS/content-addressable
storage, so the same content always maps to the same address and any
change in content changes the address.
"""

import hashlib
import os

NODES = ["nodes/node_1", "nodes/node_2", "nodes/node_3"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_nodes():
    for node in NODES:
        os.makedirs(os.path.join(BASE_DIR, node), exist_ok=True)


def compute_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def save_file(file_bytes: bytes) -> str:
    """Replicates the file across all storage nodes, addressed by its hash.
    Returns the content hash (this is the file's permanent address)."""
    _ensure_nodes()
    file_hash = compute_hash(file_bytes)
    for node in NODES:
        path = os.path.join(BASE_DIR, node, file_hash)
        with open(path, "wb") as f:
            f.write(file_bytes)
    return file_hash


def read_file(file_hash: str):
    """Reads the file from the first healthy node that has it — demonstrates
    redundancy: if one node is missing/corrupted, another still serves it."""
    for node in NODES:
        path = os.path.join(BASE_DIR, node, file_hash)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    return None


def verify_integrity(file_hash: str):
    """Recomputes the hash of the stored bytes on each node and compares it
    to the expected content-address. If a node's copy was edited directly on
    disk (simulating tampering), this will flag exactly which node failed."""
    _ensure_nodes()
    results = {}
    for node in NODES:
        path = os.path.join(BASE_DIR, node, file_hash)
        if not os.path.exists(path):
            results[node] = "MISSING"
            continue
        with open(path, "rb") as f:
            actual_hash = compute_hash(f.read())
        results[node] = "OK" if actual_hash == file_hash else f"TAMPERED (now {actual_hash[:12]}...)"
    return results
