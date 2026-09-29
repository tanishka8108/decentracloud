"""
app.py — Decentralized Cloud File Sharing System Using Blockchain
Micro-Project | Cloud Computing (CM51207)

Architecture:
    User -> Flask -> SHA-256 -> Google Cloud Storage Node 1/2/3 -> Blockchain

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

All configuration comes from environment variables (see .env.example).
"""

import io
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

# --- Load .env BEFORE our own modules read the environment -----------------
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash

import database as db
import storage
from blockchain import Blockchain
from storage import save_file, read_file, verify_integrity, presence, compute_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Configuration (environment variables — nothing secret is hard-coded)
# --------------------------------------------------------------------------
def _env_int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _load_secret_key():
    """FLASK_SECRET_KEY from .env, else a key generated once and cached in a
    local file (git-ignored) so sessions survive application restarts."""
    key = os.getenv("FLASK_SECRET_KEY", "").strip()
    if key:
        return key
    key_file = os.path.join(BASE_DIR, ".secret_key")
    try:
        if os.path.exists(key_file):
            with open(key_file, "r", encoding="utf-8") as fh:
                cached = fh.read().strip()
            if cached:
                return cached
        generated = secrets.token_hex(32)
        with open(key_file, "w", encoding="utf-8") as fh:
            fh.write(generated)
        print("[warn] FLASK_SECRET_KEY not set — generated one and saved to .secret_key. "
              "Set FLASK_SECRET_KEY in .env for a shared/production deployment.")
        return generated
    except OSError:
        return secrets.token_hex(32)


MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 50)
LEDGER_FILE = os.getenv("LEDGER_FILE", "").strip() or os.path.join(BASE_DIR, "ledger.json")
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1").strip() or "127.0.0.1"
FLASK_PORT = _env_int("FLASK_PORT", 5000)
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

csrf = CSRFProtect(app)

# --- Persistent data (survive restarts) ------------------------------------
db.init_db()
ledger = Blockchain(LEDGER_FILE)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
MIN_PASSWORD_LEN = 6


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def _clean_filename(raw_name):
    """Keep only a safe base name (no folders, no control characters)."""
    name = (raw_name or "").replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[:200] or "untitled"


def _fmt_ts(ts):
    try:
        return time.strftime("%d %b %Y %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "-"


def _safe_presence(file_hash):
    try:
        return presence(file_hash)
    except Exception:  # noqa: BLE001 - a status check must never crash the page
        return {
            node["label"]: {"bucket": "-", "status": "ERROR", "detail": "Status check failed"}
            for node in storage.describe_nodes()
        }


def _attach_presence(files):
    """Cheap per-node existence check for the dashboard, run in parallel."""
    if not files:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda f: _safe_presence(f["file_hash"]), files))
    for meta, status in zip(files, statuses):
        meta["presence"] = status
        meta["present_count"] = sum(1 for v in status.values() if v["status"] == "PRESENT")
        meta["uploaded_at_str"] = _fmt_ts(meta.get("uploaded_at"))


@app.context_processor
def inject_storage_info():
    return {
        "storage_nodes": storage.describe_nodes(),
        "storage_backend": storage.backend_name(),
        "storage_issues": storage.config_issues(),
        "max_file_size_mb": MAX_FILE_SIZE_MB,
    }


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("my_files"))
    return redirect(url_for("login"))


@app.route("/how-it-works")
def how_it_works():
    return render_template("how_it_works.html")


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not USERNAME_RE.fullmatch(username):
            flash("Username must be 3-32 characters: letters, digits, '_', '.', '-' only.", "error")
            return redirect(url_for("register"))
        if len(password) < MIN_PASSWORD_LEN:
            flash(f"Password must be at least {MIN_PASSWORD_LEN} characters long.", "error")
            return redirect(url_for("register"))
        if db.get_user(username):
            flash("Username already exists.", "error")
            return redirect(url_for("register"))
        db.create_user(username, generate_password_hash(password))
        ledger.add_block("REGISTER", "-", "-", username)
        flash("Registered successfully. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()          # new session id on login (session-fixation guard)
            session["user"] = username
            return redirect(url_for("my_files"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Upload -> SHA-256 -> replicate to all three cloud nodes -> blockchain
# --------------------------------------------------------------------------
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or f.filename == "":
            flash("Choose a file first.", "error")
            return redirect(url_for("upload"))

        file_bytes = f.read()
        if not file_bytes:
            flash("The file is empty.", "error")
            return redirect(url_for("upload"))
        filename = _clean_filename(f.filename)
        owner = session["user"]

        # (b) read bytes  (c) SHA-256  (d) replicate to all 3 GCS buckets
        result = save_file(file_bytes, original_filename=filename, owner=owner)
        file_hash = result["hash"]
        node_results = result["results"]

        ok_nodes = [label for label, r in node_results.items() if r["status"] == "OK"]
        failed = {label: r for label, r in node_results.items() if r["status"] != "OK"}

        if not ok_nodes:
            details = "; ".join(
                f"{label}: {r.get('detail') or r['status']}" for label, r in failed.items()
            )
            flash(
                "Upload failed — no storage node accepted the file. " + details +
                "  (Run: python scripts/check_setup.py)",
                "error",
            )
            return redirect(url_for("upload"))

        # (f) metadata: filename, hash, owner, upload time, shared users, nodes
        db.upsert_file(owner, filename, file_hash, len(file_bytes), ok_nodes)

        # (g) blockchain event
        ledger.add_block("UPLOAD", filename, file_hash, owner)

        per_node = "  ".join(
            f"{label} ✓" if r["status"] == "OK" else f"{label} ✗ ({r.get('detail') or r['status']})"
            for label, r in node_results.items()
        )
        if failed:
            flash(f"'{filename}' stored on {len(ok_nodes)}/3 nodes — {per_node}", "error")
        else:
            flash(f"'{filename}' replicated to all 3 cloud nodes — {per_node}", "success")
        return redirect(url_for("my_files"))
    return render_template("upload.html")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/files")
@login_required
def my_files():
    user = session["user"]
    owned = db.owned_files(user)
    shared = db.shared_files(user)
    _attach_presence(owned)
    _attach_presence(shared)
    return render_template(
        "files.html",
        owned=owned,
        shared=shared,
        all_users=db.list_usernames(),
        owner_name=user,
    )


# --------------------------------------------------------------------------
# Sharing (access control)
# --------------------------------------------------------------------------
@app.route("/share/<int:file_id>", methods=["POST"])
@login_required
def share(file_id):
    meta = db.get_file(file_id)
    if not meta:
        flash("File not found.", "error")
        return redirect(url_for("my_files"))
    if meta["owner"] != session["user"]:
        flash("Not authorized to share this file.", "error")
        return redirect(url_for("my_files"))

    target = request.form.get("target_user", "").strip()
    if not target:
        flash("No user selected to share with. Register a second account first.", "error")
        return redirect(url_for("my_files"))
    if target == meta["owner"]:
        flash("You already own this file.", "error")
        return redirect(url_for("my_files"))
    if not db.get_user(target):
        flash("No such user.", "error")
        return redirect(url_for("my_files"))
    if not db.add_share(file_id, target):
        flash(f"'{meta['filename']}' is already shared with {target}.")
        return redirect(url_for("my_files"))

    ledger.add_block("SHARE", meta["filename"], meta["file_hash"], f"{session['user']} -> {target}")
    flash(f"Shared '{meta['filename']}' with {target}.", "success")
    return redirect(url_for("my_files"))


# --------------------------------------------------------------------------
# Download -> check permission -> check ledger -> healthy node -> re-hash
# --------------------------------------------------------------------------
@app.route("/download/<int:file_id>")
@login_required
def download(file_id):
    user = session["user"]
    meta = db.get_file(file_id)
    if not meta:
        flash("File not found.", "error")
        return redirect(url_for("my_files"))
    if meta["owner"] != user and user not in meta["shared_with"]:
        flash("Access denied — this file has not been shared with you.", "error")
        return redirect(url_for("my_files"))

    # (b) expected hash according to the blockchain ledger
    chain_hash = ledger.find_latest_file_hash(meta["filename"], owner=meta["owner"])
    if chain_hash is None:
        chain_hash = meta["file_hash"]
    if chain_hash != meta["file_hash"]:
        flash(
            "WARNING: the ledger hash and the stored hash differ — possible tampering!",
            "error",
        )

    # (c)(d) fetch a healthy copy from the cloud nodes (failover built in)
    data, node_label = read_file(meta["file_hash"])
    if data is None:
        flash("File is corrupted or unavailable on every storage node.", "error")
        return redirect(url_for("my_files"))

    # (e)(f)(g) re-hash and compare with the expected (ledger) hash
    actual_hash = compute_hash(data)
    if actual_hash != chain_hash:
        flash("Integrity check failed — download blocked (hash mismatch).", "error")
        return redirect(url_for("my_files"))

    ledger.add_block("DOWNLOAD", meta["filename"], actual_hash, user)
    app.logger.info("Download '%s' by %s served from %s", meta["filename"], user, node_label)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=meta["filename"])


# --------------------------------------------------------------------------
# Verification page (deep check of every cloud copy)
# --------------------------------------------------------------------------
@app.route("/verify/<int:file_id>")
@login_required
def verify(file_id):
    meta = db.get_file(file_id)
    if not meta:
        flash("File not found.", "error")
        return redirect(url_for("my_files"))
    if meta["owner"] != session["user"] and session["user"] not in meta["shared_with"]:
        flash("Access denied — this file has not been shared with you.", "error")
        return redirect(url_for("my_files"))

    node_status = verify_integrity(meta["file_hash"])
    chain_valid, chain_msg = ledger.is_valid()
    chain_hash = ledger.find_latest_file_hash(meta["filename"], owner=meta["owner"])
    healthy = sum(1 for v in node_status.values() if v["status"] == "OK")
    meta["uploaded_at_str"] = _fmt_ts(meta.get("uploaded_at"))

    ledger.add_block("VERIFY", meta["filename"], meta["file_hash"], session["user"])

    return render_template(
        "verify.html",
        meta=meta,
        node_status=node_status,
        chain_valid=chain_valid,
        chain_msg=chain_msg,
        file_hash=meta["file_hash"],
        chain_hash=chain_hash,
        healthy=healthy,
        total=len(node_status),
        expected_matches=chain_hash in (None, meta["file_hash"]),
    )


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------
@app.route("/ledger")
@login_required
def ledger_view():
    valid, msg = ledger.is_valid()
    chain = ledger.as_list()
    for block in chain:
        block["time_str"] = _fmt_ts(block["timestamp"])
    return render_template("ledger.html", chain=chain, valid=valid, msg=msg)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(_error):
    return render_template("error.html", code=404, title="Page not found",
                           message="The page you requested does not exist."), 404


@app.errorhandler(413)
def file_too_large(_error):
    flash(f"File is too large — the limit is {MAX_FILE_SIZE_MB} MB.", "error")
    return redirect(url_for("upload"))


@app.errorhandler(CSRFError)
def csrf_error(_error):
    flash("Your form expired — please submit it again.", "error")
    return redirect(request.referrer or url_for("home"))


@app.errorhandler(500)
def server_error(_error):
    app.logger.exception("Unhandled server error")
    return render_template("error.html", code=500, title="Server error",
                           message="Something went wrong. Check the terminal running Flask for details."), 500


# --------------------------------------------------------------------------
# Startup summary (makes configuration problems obvious immediately)
# --------------------------------------------------------------------------
def _print_startup_summary():
    print("=" * 64)
    print("DecentraCloud — decentralized cloud file sharing")
    print("  backend     : {}".format(storage.backend_name()))
    for node in storage.describe_nodes():
        print("  {:<8} -> {}".format(node["label"], node["target"]))
    print("  ledger file : {}".format(LEDGER_FILE))
    if ledger.load_status == "corrupt":
        print("  [warn] could not read the ledger file ({}); started a fresh chain."
              .format(ledger.load_error))
    elif ledger.load_status == "loaded":
        print("  ledger      : loaded {} blocks from disk".format(len(ledger.chain)))
    issues = storage.config_issues()
    if issues:
        print("  SETUP PROBLEMS:")
        for issue in issues:
            print("    - {}".format(issue))
    print("  open http://{}:{}/".format(FLASK_HOST, FLASK_PORT))
    print("=" * 64)


if __name__ == "__main__":
    _print_startup_summary()
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
