"""
blockchain.py
A lightweight blockchain used as a tamper-evident ledger for file events
(REGISTER / UPLOAD / SHARE / DOWNLOAD / VERIFY). This is NOT a
cryptocurrency chain and has nothing to do with Bitcoin or Ethereum — it
is a simplified educational implementation of the core blockchain idea:

    each block cryptographically commits to the hash of the block before
    it, so editing ANY historical block breaks the chain and is instantly
    detectable.

Improvements over the original version:
  * the chain is persisted to a JSON file (LEDGER_FILE, default
    ledger.json) so the audit trail survives application restarts;
  * atomic writes (temp file + rename) so a crash cannot corrupt the file;
  * a lock makes concurrent requests safe on Flask's threaded server;
  * blocks are loaded with their STORED hash, so tampering with the JSON
    file is detected by is_valid().
"""

import hashlib
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Block:
    def __init__(self, index, timestamp, action, filename, file_hash, user, previous_hash):
        self.index = index
        self.timestamp = timestamp
        self.action = action          # "REGISTER" | "UPLOAD" | "SHARE" | "DOWNLOAD" | "VERIFY" | "GENESIS"
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

    @classmethod
    def from_dict(cls, data):
        block = cls(
            index=data["index"],
            timestamp=data["timestamp"],
            action=data["action"],
            filename=data["filename"],
            file_hash=data["file_hash"],
            user=data["user"],
            previous_hash=data["previous_hash"],
        )
        # Keep the hash STORED in the file (not the freshly computed one) so
        # that any edit to the JSON is detected by is_valid().
        block.hash = data.get("hash") or block.hash
        return block


class Blockchain:
    def __init__(self, path=None):
        self.chain = []
        self.path = path
        self._lock = threading.Lock()
        self.load_status = "new"      # "new" | "loaded" | "corrupt" (fell back to a fresh chain)
        self.load_error = None

        if path and os.path.exists(path):
            if self._load():
                self.load_status = "loaded"
            else:
                self.load_status = "corrupt"

        if not self.chain:
            self._create_genesis_block()

    # ---- persistence -----------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            chain = [Block.from_dict(item) for item in raw]
            if not chain:
                return False
            self.chain = chain
            return True
        except Exception as exc:  # noqa: BLE001 - unreadable file -> start fresh, loudly
            self.load_error = str(exc) or exc.__class__.__name__
            self.chain = []
            return False

    def _save(self):
        if not self.path:
            return
        tmp_path = self.path + ".tmp"
        payload = json.dumps([b.to_dict() for b in self.chain], indent=2)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, self.path)  # atomic: never leaves a half-written ledger

    # ---- chain operations ------------------------------------------------
    def _create_genesis_block(self):
        genesis = Block(0, time.time(), "GENESIS", "-", "-", "system", "0")
        self.chain.append(genesis)
        self._save()

    def latest_block(self):
        return self.chain[-1]

    def add_block(self, action, filename, file_hash, user):
        with self._lock:
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
            self._save()
            return block

    def is_valid(self):
        """Walk the chain and verify every hash & every link — this is what
        detects tampering anywhere in the recorded history."""
        with self._lock:
            for i in range(1, len(self.chain)):
                current = self.chain[i]
                previous = self.chain[i - 1]
                if current.hash != current.compute_hash():
                    return False, f"Block {current.index} content/hash mismatch (tampered)."
                if current.previous_hash != previous.hash:
                    return False, f"Chain broken between block {previous.index} and {current.index}."
            return True, f"Blockchain is valid ({len(self.chain)} blocks)."

    def find_latest_file_hash(self, filename, owner=None):
        """Return the hash recorded at the most recent UPLOAD event for a
        file — used to check a stored/downloaded file against the ledger.

        `owner` disambiguates when two users upload files with the same name.
        """
        with self._lock:
            for block in reversed(self.chain):
                if block.filename == filename and block.action == "UPLOAD":
                    if owner is None or block.user == owner:
                        return block.file_hash
        return None

    def as_list(self):
        with self._lock:
            return [b.to_dict() for b in self.chain]
