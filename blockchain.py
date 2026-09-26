"""
blockchain.py
A lightweight blockchain used as a tamper-evident ledger for file events
(upload / share / download). This is NOT a cryptocurrency chain — it is a
simplified educational implementation of the core blockchain idea:
each block cryptographically commits to the one before it, so editing
any historical block breaks the chain and is instantly detectable.
"""

import hashlib
import json
import time


class Block:
    def __init__(self, index, timestamp, action, filename, file_hash, user, previous_hash):
        self.index = index
        self.timestamp = timestamp
        self.action = action          # "UPLOAD" | "SHARE" | "DOWNLOAD" | "REGISTER"
        self.filename = filename
        self.file_hash = file_hash    # SHA-256 of the file content (or "-" for non-file events)
        self.user = user
        self.previous_hash = previous_hash
        self.hash = self.compute_hash()

    def compute_hash(self):
        block_string = json.dumps({
            "index": self.index,
            "timestamp": self.timestamp,
            "action": self.action,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "user": self.user,
            "previous_hash": self.previous_hash,
        }, sort_keys=True)
        return hashlib.sha256(block_string.encode()).hexdigest()

    def to_dict(self):
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "action": self.action,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "user": self.user,
            "previous_hash": self.previous_hash,
            "hash": self.hash,
        }


class Blockchain:
    def __init__(self):
        self.chain = []
        self._create_genesis_block()

    def _create_genesis_block(self):
        genesis = Block(0, time.time(), "GENESIS", "-", "-", "system", "0")
        self.chain.append(genesis)

    def latest_block(self):
        return self.chain[-1]

    def add_block(self, action, filename, file_hash, user):
        prev = self.latest_block()
        block = Block(
            index=prev.index + 1,
            timestamp=time.time(),
            action=action,
            filename=filename,
            file_hash=file_hash,
            user=user,
            previous_hash=prev.hash,
        )
        self.chain.append(block)
        return block

    def is_valid(self):
        """Walk the chain and verify every hash & every link — this is what
        detects tampering anywhere in the recorded history."""
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]
            if current.hash != current.compute_hash():
                return False, f"Block {current.index} content/hash mismatch (tampered)."
            if current.previous_hash != previous.hash:
                return False, f"Chain broken between block {previous.index} and {current.index}."
        return True, "Blockchain is valid."

    def find_latest_file_hash(self, filename):
        """Return the hash recorded at the most recent UPLOAD event for a file
        — used to verify a downloaded/stored file hasn't been tampered with
        directly on disk."""
        for block in reversed(self.chain):
            if block.filename == filename and block.action == "UPLOAD":
                return block.file_hash
        return None

    def as_list(self):
        return [b.to_dict() for b in self.chain]
