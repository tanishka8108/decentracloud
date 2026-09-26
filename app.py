"""
app.py — Decentralized Cloud File Sharing System Using Blockchain
Micro-Project | Cloud Computing (CM51207)

Run:  pip install -r requirements.txt
      python app.py
Then open http://127.0.0.1:5000
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import io
import time

from blockchain import Blockchain
from storage import save_file, read_file, verify_integrity

app = Flask(__name__)
app.secret_key = "change-this-secret-key-for-real-deployments"

# --- In-memory "database" (fine for a demo/prototype; swap for a real DB in production) ---
users = {}          # username -> password_hash
files_meta = {}      # filename -> {"hash": str, "owner": str, "shared_with": set()}
ledger = Blockchain()


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("my_files"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if username in users:
            flash("Username already exists.")
            return redirect(url_for("register"))
        users[username] = generate_password_hash(password)
        ledger.add_block("REGISTER", "-", "-", username)
        flash("Registered successfully. Please log in.")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if username in users and check_password_hash(users[username], password):
            session["user"] = username
            return redirect(url_for("my_files"))
        flash("Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or f.filename == "":
            flash("Choose a file first.")
            return redirect(url_for("upload"))
        file_bytes = f.read()
        file_hash = save_file(file_bytes)
        files_meta[f.filename] = {
            "hash": file_hash,
            "owner": session["user"],
            "shared_with": set(),
        }
        ledger.add_block("UPLOAD", f.filename, file_hash, session["user"])
        flash(f"'{f.filename}' uploaded and replicated across 3 storage nodes.")
        return redirect(url_for("my_files"))
    return render_template("upload.html")


@app.route("/files")
@login_required
def my_files():
    user = session["user"]
    owned = {name: meta for name, meta in files_meta.items() if meta["owner"] == user}
    shared = {name: meta for name, meta in files_meta.items() if user in meta["shared_with"]}
    return render_template("files.html", owned=owned, shared=shared, all_users=list(users.keys()))


@app.route("/share/<filename>", methods=["POST"])
@login_required
def share(filename):
    meta = files_meta.get(filename)
    if not meta or meta["owner"] != session["user"]:
        flash("Not authorized to share this file.")
        return redirect(url_for("my_files"))
    target = request.form["target_user"].strip()
    if target not in users:
        flash("No such user.")
        return redirect(url_for("my_files"))
    meta["shared_with"].add(target)
    ledger.add_block("SHARE", filename, meta["hash"], f"{session['user']} -> {target}")
    flash(f"Shared '{filename}' with {target}.")
    return redirect(url_for("my_files"))


@app.route("/download/<filename>")
@login_required
def download(filename):
    user = session["user"]
    meta = files_meta.get(filename)
    if not meta:
        flash("File not found.")
        return redirect(url_for("my_files"))
    if meta["owner"] != user and user not in meta["shared_with"]:
        flash("Access denied — this file has not been shared with you.")
        return redirect(url_for("my_files"))

    # Tamper check: compare the ledger's recorded hash for this file to what
    # is actually stored on the nodes right now.
    expected_hash = ledger.find_latest_file_hash(filename)
    node_status = verify_integrity(meta["hash"])
    if any(status != "OK" for status in node_status.values()) and expected_hash != meta["hash"]:
        flash("WARNING: integrity check failed — stored file may have been tampered with!")

    data = read_file(meta["hash"])
    if data is None:
        flash("File missing from all storage nodes!")
        return redirect(url_for("my_files"))

    ledger.add_block("DOWNLOAD", filename, meta["hash"], user)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=filename)


@app.route("/verify/<filename>")
@login_required
def verify(filename):
    meta = files_meta.get(filename)
    if not meta:
        flash("File not found.")
        return redirect(url_for("my_files"))
    node_status = verify_integrity(meta["hash"])
    chain_valid, chain_msg = ledger.is_valid()
    return render_template(
        "verify.html", filename=filename, node_status=node_status,
        chain_valid=chain_valid, chain_msg=chain_msg, file_hash=meta["hash"],
    )


@app.route("/ledger")
@login_required
def ledger_view():
    valid, msg = ledger.is_valid()
    return render_template("ledger.html", chain=ledger.as_list(), valid=valid, msg=msg)


if __name__ == "__main__":
    app.run(debug=True)
