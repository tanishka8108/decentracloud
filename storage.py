"""
storage.py — Cloud storage layer for DecentraCloud (Google Cloud Storage).

The three "storage nodes" of this project are three INDEPENDENT Google
Cloud Storage buckets:

    Node 1  ->  bucket named in the env var GCS_BUCKET_NODE_1
    Node 2  ->  bucket named in the env var GCS_BUCKET_NODE_2
    Node 3  ->  bucket named in the env var GCS_BUCKET_NODE_3

Files are content-addressed: the object name inside every bucket is the
SHA-256 hash of the file's content (the same idea as IPFS). The same
content therefore always maps to the same object name, and ANY change in
content changes the name — which is exactly what makes tampering
detectable.

Flow
----
UPLOAD : Flask receives the bytes -> SHA-256 -> the SAME bytes are
         replicated to all three buckets IN PARALLEL -> the hash is the
         object name in each bucket.
DOWNLOAD: nodes are read in order; each copy is re-hashed and returned
         only if it matches the expected hash, so a corrupted copy is
         skipped automatically (fault tolerance / failover).

Backends (env var STORAGE_BACKEND):
    gcs    (default) — real cloud storage on Google Cloud Storage.
    local  (optional) — the original local folders, kept only so the app
           can be developed/tested offline. Never used for the demo.

This module NEVER raises for per-node problems: every operation returns
a status string so the UI can explain exactly what happened
(NOT_CONFIGURED / NO_BUCKET / MISSING / TAMPERED / ERROR / OK).
"""

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Configuration — everything comes from environment variables (.env file).
# --------------------------------------------------------------------------
BACKEND = os.getenv("STORAGE_BACKEND", "gcs").strip().lower()
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()

# (display label, bucket name) for the three cloud nodes.
GCS_NODES = [
    ("Node 1", os.getenv("GCS_BUCKET_NODE_1", "").strip()),
    ("Node 2", os.getenv("GCS_BUCKET_NODE_2", "").strip()),
    ("Node 3", os.getenv("GCS_BUCKET_NODE_3", "").strip()),
]

# Local fallback folders (only used when STORAGE_BACKEND=local).
LOCAL_NODES = [
    ("Node 1", os.path.join(BASE_DIR, "nodes", "node_1")),
    ("Node 2", os.path.join(BASE_DIR, "nodes", "node_2")),
    ("Node 3", os.path.join(BASE_DIR, "nodes", "node_3")),
]

NODE_LABELS = [label for label, _ in GCS_NODES]

# The Google Cloud libraries are imported defensively: if they are not
# installed, the app still starts and shows a clear setup message instead
# of crashing with a traceback.
try:
    from google.cloud import storage as gcs  # type: ignore
    from google.api_core import exceptions as gcs_exc  # type: ignore
    from google.auth import exceptions as auth_exc  # type: ignore
    GCS_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on environment
    gcs = None
    gcs_exc = None
    auth_exc = None
    GCS_IMPORT_ERROR = str(exc) or exc.__class__.__name__

_executor = ThreadPoolExecutor(max_workers=3)
_client = None
_client_lock = threading.Lock()
_client_error = None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def compute_hash(file_bytes: bytes) -> str:
    """SHA-256 of the content, as lowercase hex — the file's permanent address."""
    return hashlib.sha256(file_bytes).hexdigest()


def _is_sha256(value: str) -> bool:
    """Guard: only ever use a well-formed hash as an object name."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value.lower())


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backend_name() -> str:
    if BACKEND == "local":
        return "Local folders (offline development)"
    return "Google Cloud Storage (GCS)"


def _nodes():
    """Active node list for the configured backend."""
    if BACKEND == "local":
        return [{"label": label, "bucket": None, "path": path} for label, path in LOCAL_NODES]
    return [{"label": label, "bucket": name, "path": None} for label, name in GCS_NODES]


def describe_nodes():
    """Node description used by the UI (dashboard / upload page)."""
    return [
        {
            "label": node["label"],
            "backend": BACKEND,
            "target": node["path"] if BACKEND == "local" else (node["bucket"] or "not configured"),
        }
        for node in _nodes()
    ]


# --------------------------------------------------------------------------
# Google Cloud client (created lazily, once, with a clear error message)
# --------------------------------------------------------------------------
class StorageConfigError(Exception):
    """Raised when the GCS backend is selected but cannot be used."""


def _get_client():
    global _client, _client_error
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if gcs is None:
            _client_error = (
                "The google-cloud-storage library is not installed. "
                "Run: pip install -r requirements.txt"
            )
            raise StorageConfigError(_client_error)
        try:
            _client = gcs.Client(project=PROJECT) if PROJECT else gcs.Client()
        except Exception as exc:
            _client_error = (
                "Could not authenticate with Google Cloud ({}). "
                "Set GOOGLE_APPLICATION_CREDENTIALS to your service-account key file, "
                "or run: gcloud auth application-default login".format(
                    str(exc) or exc.__class__.__name__
                )
            )
            raise StorageConfigError(_client_error)
    return _client


def _classify(exc):
    """Turn any exception from a node into a (status, detail) pair."""
    text = str(exc) or exc.__class__.__name__
    if gcs is not None:
        not_found = getattr(gcs_exc, "NotFound", None)
        if not_found is not None and isinstance(exc, not_found):
            if "bucket" in text.lower():
                return "NO_BUCKET", "Bucket not found — {}".format(text)
            return "MISSING", "Object not found on this node"
        forbidden = getattr(gcs_exc, "Forbidden", None)
        if forbidden is not None and isinstance(exc, forbidden):
            return "ERROR", "Permission denied — check the service-account roles ({})".format(text)
        default_creds = getattr(auth_exc, "DefaultCredentialsError", None)
        if default_creds is not None and isinstance(exc, default_creds):
            return "ERROR", "No Google Cloud credentials found ({})".format(text)
        unavailable = getattr(gcs_exc, "ServiceUnavailable", None)
        if unavailable is not None and isinstance(exc, unavailable):
            return "ERROR", "Google Cloud Storage temporarily unavailable ({})".format(text)
        api_error = getattr(gcs_exc, "GoogleAPIError", None)
        if api_error is not None and isinstance(exc, api_error):
            return "ERROR", "Google Cloud error: {}".format(text)
    if isinstance(exc, StorageConfigError):
        return "NOT_CONFIGURED", text
    if isinstance(exc, OSError):
        return "ERROR", "Connection error: {}".format(text)
    return "ERROR", text


# --------------------------------------------------------------------------
# Per-node primitives (dispatch on backend)
# --------------------------------------------------------------------------
def _upload_to_node(node, data: bytes, object_name: str, metadata: dict):
    if BACKEND == "local":
        os.makedirs(node["path"], exist_ok=True)
        with open(os.path.join(node["path"], object_name), "wb") as fh:
            fh.write(data)
        return "OK", ""
    if not node["bucket"]:
        return "NOT_CONFIGURED", "{} has no bucket configured (set its env var)".format(node["label"])
    client = _get_client()
    bucket = client.bucket(node["bucket"])
    # If the bucket is missing or we lack permission, upload_from_string raises
    # and _classify() turns that into NO_BUCKET / ERROR with a clear message.
    blob = bucket.blob(object_name)
    blob.metadata = {k: str(v) for k, v in metadata.items()}
    blob.upload_from_string(data, content_type="application/octet-stream")
    return "OK", ""


def _download_from_node(node, object_name: str) -> bytes:
    if BACKEND == "local":
        with open(os.path.join(node["path"], object_name), "rb") as fh:
            return fh.read()
    if not node["bucket"]:
        raise StorageConfigError("{} has no bucket configured".format(node["label"]))
    client = _get_client()
    blob = client.bucket(node["bucket"]).blob(object_name)
    return blob.download_as_bytes()


def _object_exists(node, object_name: str) -> bool:
    if BACKEND == "local":
        return os.path.exists(os.path.join(node["path"], object_name))
    if not node["bucket"]:
        raise StorageConfigError("{} has no bucket configured".format(node["label"]))
    client = _get_client()
    blob = client.bucket(node["bucket"]).blob(object_name)
    return blob.exists()


# --------------------------------------------------------------------------
# Public API used by app.py
# --------------------------------------------------------------------------
def save_file(file_bytes: bytes, original_filename: str = "-", owner: str = "-"):
    """Replicate the file to ALL THREE storage nodes (in parallel).

    Returns {"hash": <sha256>, "results": {node_label: {"status", "detail"}}}.
    The caller decides what to do if some (or all) nodes failed.
    """
    file_hash = compute_hash(file_bytes)
    metadata = {
        "sha256": file_hash,
        "original_filename": original_filename,
        "owner": owner,
        "uploaded_at": _utc_now(),
        "app": "DecentraCloud",
    }

    def _one(node):
        try:
            status, detail = _upload_to_node(node, file_bytes, file_hash, metadata)
        except Exception as exc:  # noqa: BLE001 - every failure must be reported, not raised
            status, detail = _classify(exc)
        return node["label"], {"status": status, "detail": detail}

    results = dict(_executor.map(_one, _nodes()))
    return {"hash": file_hash, "results": results}


def read_file(file_hash: str):
    """Return (bytes, node_label) of the FIRST HEALTHY copy, else (None, None).

    Every downloaded copy is re-hashed; a copy whose SHA-256 does not match
    the expected hash is skipped and the next node is tried (failover).
    """
    if not _is_sha256(file_hash):
        return None, None
    for node in _nodes():
        try:
            data = _download_from_node(node, file_hash)
        except Exception:  # noqa: BLE001 - unavailable node -> try the next one
            continue
        if compute_hash(data) == file_hash:
            return data, node["label"]
        # Tampered copy: do NOT serve it, try the next node.
    return None, None


def verify_integrity(file_hash: str):
    """DEEP check: download the copy from every node and re-hash it.

    Returns {node_label: {"bucket", "status", "detail", "actual_hash"}} with
    status one of OK / MISSING / TAMPERED / NO_BUCKET / ERROR / NOT_CONFIGURED.
    """
    if not _is_sha256(file_hash):
        return {
            node["label"]: {
                "bucket": node["bucket"] or node["path"],
                "status": "ERROR",
                "detail": "Invalid hash",
                "actual_hash": None,
            }
            for node in _nodes()
        }

    def _one(node):
        target = node["bucket"] or node["path"] or "not configured"
        try:
            data = _download_from_node(node, file_hash)
        except Exception as exc:  # noqa: BLE001
            status, detail = _classify(exc)
            return node["label"], {
                "bucket": target, "status": status, "detail": detail, "actual_hash": None,
            }
        actual = compute_hash(data)
        if actual == file_hash:
            return node["label"], {
                "bucket": target, "status": "OK", "detail": "", "actual_hash": actual,
            }
        return node["label"], {
            "bucket": target,
            "status": "TAMPERED",
            "detail": "Stored copy does not match the expected hash",
            "actual_hash": actual,
        }

    return dict(_executor.map(_one, _nodes()))


def presence(file_hash: str):
    """CHEAP check (no download): does the object exist on each node?

    Used by the dashboard so it stays fast. Deep hashing happens on the
    Verify page.

    Returns {node_label: {"bucket", "status", "detail"}}
    with status PRESENT / MISSING / NO_BUCKET / ERROR / NOT_CONFIGURED.
    """
    if not _is_sha256(file_hash):
        return {
            node["label"]: {"bucket": node["bucket"] or node["path"], "status": "ERROR",
                            "detail": "Invalid hash"}
            for node in _nodes()
        }

    def _one(node):
        target = node["bucket"] or node["path"] or "not configured"
        try:
            exists = _object_exists(node, file_hash)
        except Exception as exc:  # noqa: BLE001
            status, detail = _classify(exc)
            if status in ("MISSING",):
                status = "MISSING"
            return node["label"], {"bucket": target, "status": status, "detail": detail}
        return node["label"], {
            "bucket": target,
            "status": "PRESENT" if exists else "MISSING",
            "detail": "" if exists else "Object not found on this node",
        }

    return dict(_executor.map(_one, _nodes()))


# --------------------------------------------------------------------------
# Configuration self-check (shown in the UI when setup is incomplete)
# --------------------------------------------------------------------------
_issues_cache = {"at": 0.0, "issues": []}
_ISSUES_TTL = 60  # seconds — avoids re-probing Google Cloud on every page load


def config_issues():
    """Return a list of human-readable setup problems (empty list = OK)."""
    now = time.time()
    if now - _issues_cache["at"] < _ISSUES_TTL:
        return list(_issues_cache["issues"])

    issues = []
    if BACKEND not in ("gcs", "local"):
        issues.append("STORAGE_BACKEND must be 'gcs' or 'local' (found '{}').".format(BACKEND))
    if BACKEND == "gcs":
        if gcs is None:
            issues.append(
                "google-cloud-storage is not installed — run: pip install -r requirements.txt"
            )
        for label, bucket in GCS_NODES:
            if not bucket:
                issues.append("Set GCS_BUCKET_NODE_{} in your .env file.".format(label[-1]))
        if gcs is not None and all(bucket for _, bucket in GCS_NODES):
            try:
                import google.auth  # type: ignore
                google.auth.default()
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    "No Google Cloud credentials found ({}). Run: "
                    "gcloud auth application-default login — or set "
                    "GOOGLE_APPLICATION_CREDENTIALS to your service-account key.".format(
                        str(exc) or exc.__class__.__name__
                    )
                )
    _issues_cache["at"] = now
    _issues_cache["issues"] = issues
    return list(issues)


# --------------------------------------------------------------------------
# Demo helpers — used by scripts/ to SIMULATE a node failure and repair it.
# (Not exposed through any web route; they exist for the fault-tolerance demo.)
# --------------------------------------------------------------------------
def corrupt_copy(file_hash: str, label: str):
    """Overwrite one node's copy with tampered bytes (same object name)."""
    node = _node_by_label(label)
    data = _download_from_node(node, file_hash)
    tampered = bytearray(data)
    if len(tampered) == 0:
        tampered = bytearray(b"corrupted-copy")
    else:
        tampered[len(tampered) // 2] ^= 0xFF  # flip one byte in the middle
    _upload_to_node(node, bytes(tampered), file_hash, {
        "sha256": file_hash, "note": "intentionally-corrupted-for-demo", "at": _utc_now(),
    })
    return bytes(tampered)


def repair_copy(file_hash: str, label: str):
    """Restore one node's copy from a healthy copy on another node."""
    node = _node_by_label(label)
    healthy = None
    for other in _nodes():
        if other["label"] == node["label"]:
            continue
        try:
            data = _download_from_node(other, file_hash)
        except Exception:  # noqa: BLE001
            continue
        if compute_hash(data) == file_hash:
            healthy = data
            break
    if healthy is None:
        raise RuntimeError("No healthy copy found on the other nodes to repair from.")
    _upload_to_node(node, healthy, file_hash, {
        "sha256": file_hash, "note": "repaired-from-healthy-node", "at": _utc_now(),
    })
    return healthy


def _node_by_label(label: str):
    wanted = str(label).strip()
    for node in _nodes():
        if node["label"].lower() in (wanted.lower(), "node {}".format(wanted)):
            return node
    raise ValueError("Unknown node '{}'. Use: {}".format(label, ", ".join(NODE_LABELS)))
