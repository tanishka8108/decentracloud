# DecentraCloud — Decentralized Cloud File Sharing with Blockchain

Micro-project for **Cloud Computing (CM51207)**, Semester V, Government Polytechnic Pune.
Maps to CO2, CO3, CO5, CO6.

A Flask application where every uploaded file is hashed with **SHA-256**, replicated to
**three Google Cloud Storage buckets** (three independent *cloud storage nodes*), and every
action is recorded in a lightweight **hash-chain blockchain ledger** for a tamper-evident
audit trail.

```
                Flask Application
                       |
                 SHA-256 Hash
                       |
          ┌────────────┼────────────┐
          ↓            ↓            ↓
      GCS Node 1   GCS Node 2   GCS Node 3
      (bucket 1)    (bucket 2)    (bucket 3)
          |            |            |
          └────────────┼────────────┘
                       ↓
                Blockchain Ledger
```

## What it demonstrates

- **Real cloud storage** — the three "nodes" are three real GCS buckets, not local folders.
- **Replication** — the same bytes are written to all three buckets on every upload
  (object name = the file's SHA-256 hash, so every bucket holds the identical object).
- **Integrity** — any copy is validated by re-computing SHA-256 before it is trusted or served.
- **Fault tolerance** — a corrupted/unavailable node is skipped automatically; the file is
  served from a healthy node.
- **Blockchain ledger** — REGISTER / UPLOAD / SHARE / DOWNLOAD / VERIFY events are hash-chained
  (an educational tamper-evident ledger — *not* Bitcoin/Ethereum).
- **Access control** — password-hashed login; only the owner can share; only the owner or
  explicitly shared users can download/verify.

## Project structure

```
decentracloud/
├── app.py               # Flask routes: auth, upload, share, download, verify, ledger
├── storage.py           # Cloud storage layer: 3 GCS buckets, replication, failover, hashing
├── blockchain.py        # Block / Blockchain classes — persisted tamper-evident ledger
├── database.py          # SQLite persistence: users, files, shares (survives restarts)
├── requirements.txt     # Python dependencies (incl. google-cloud-storage)
├── .env.example         # Configuration template (copy to .env — never commit .env)
├── scripts/
│   ├── check_setup.py   # Diagnose credentials / bucket configuration
│   ├── corrupt_node.py  # SIMULATE a corrupted node copy (fault-tolerance demo)
│   └── restore_node.py  # Repair that node from a healthy copy
├── templates/
│   ├── base.html        # Layout, navigation, styles
│   ├── login.html / register.html
│   ├── upload.html      # Shows the three target buckets
│   ├── files.html       # Dashboard: owner, hash, storage status, sharing status
│   ├── verify.html      # Per-node health, expected vs actual hashes, chain validity
│   ├── ledger.html      # Block #, timestamp, action, user, filename, hashes
│   ├── how_it_works.html# Architecture + viva concept glossary
│   └── error.html       # Friendly 404/500 page
└── (runtime files, all git-ignored)
    ├── decentracloud.db  # SQLite database
    ├── ledger.json       # persisted blockchain
    └── .secret_key       # generated Flask session key (if FLASK_SECRET_KEY not set)
```

---

## 1. Prerequisites

- Python 3.9+ (`python --version`)
- A Google Cloud account (free tier is enough; no billing is required for this demo's usage)
- `gcloud` CLI *(optional, only if you choose the ADC login method below)*

## 2. Google Cloud setup

### 2.1 Create a project

1. Open <https://console.cloud.google.com/> → **Select a project → New Project**.
2. Name it, e.g. `DecentraCloud`, and click **Create**.
3. Select the new project (project id looks like `decentracloud-123456`).

### 2.2 Enable the Cloud Storage API

1. Console → **APIs & Services → Library**.
2. Search **Cloud Storage API** → **Enable**.

   Or with the CLI:
   ```bash
   gcloud services enable storage.googleapis.com --project YOUR_PROJECT_ID
   ```

### 2.3 Create the three buckets (the three cloud storage nodes)

Cloud Storage → **Buckets → Create** (or `gcloud storage buckets create`).

Create **three** buckets — one per storage node. Bucket names are **globally unique** across
all of Google Cloud, so add your own suffix:

| Node | Example bucket name        | Env var that points to it |
|------|----------------------------|---------------------------|
| 1    | `decentracloud-node-1-abc` | `GCS_BUCKET_NODE_1`       |
| 2    | `decentracloud-node-2-abc` | `GCS_BUCKET_NODE_2`       |
| 3    | `decentracloud-node-3-abc` | `GCS_BUCKET_NODE_3`       |

Recommended settings for each bucket:

- **Location type:** *Region* (pick one region close to you, e.g. `asia-south1`).
  Keeping all three in one region is simplest; you may instead pick three regions to make the
  "geographically distributed nodes" story stronger in your viva.
- **Storage class:** *Standard*
- **Access control:** *Uniform* (recommended)
- **Public access:** leave prevented (the app never makes objects public)

CLI equivalent:
```bash
gcloud storage buckets create gs://decentracloud-node-1-YOURSUFFIX \
  --location=asia-south1 --project=YOUR_PROJECT_ID
```

### 2.4 Authentication (how the app talks to GCS)

The app uses Application Default Credentials (ADC). Choose **one**:

**Option A — simplest, for local development (recommended for the demo):**
```bash
gcloud auth application-default login
```
A browser window opens; sign in with the account that owns the project. Your own IAM roles
(you are the project owner) apply. No key file is created or committed.

**Option B — service account key (use this for servers/deployment):**
1. Console → **IAM & Admin → Service Accounts → Create service account**, e.g. `decentracloud-app`.
2. Grant role **Storage Object Admin** (`roles/storage.objectAdmin`) — this is the only
   role the application itself needs (create/read/delete objects).
   *(Optional)* also grant **Storage Bucket Viewer** (`roles/storage.bucketViewer`) so
   `scripts/check_setup.py` can probe bucket metadata.
   Grant them on the three buckets, or once at the project level for simplicity.
3. **Keys → Add key → Create new key → JSON** → download the `.json` file.
4. Put the file somewhere **outside** the repository (e.g. `C:\gcp\decentracloud-key.json`)
   and point to it in `.env`:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=C:/gcp/decentracloud-key.json
   ```

> **NEVER commit the key file or your `.env` to GitHub.** `.gitignore` already blocks
> `.env`, `.secret_key` and service-account JSON patterns — do not remove those rules.

## 3. Install and configure

```bash
cd decentracloud
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS

pip install -r requirements.txt

copy .env.example .env         # Windows
# cp .env.example .env         # Linux/macOS
```

Edit `.env` and fill in:

| Variable                 | Meaning                                              |
|--------------------------|------------------------------------------------------|
| `FLASK_SECRET_KEY`       | Long random string for session signing               |
| `GOOGLE_CLOUD_PROJECT`   | Your project id                                      |
| `GCS_BUCKET_NODE_1/2/3`  | The three bucket names you created                   |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the service-account JSON key (Option B)|
| `STORAGE_BACKEND`        | `gcs` (default) or `local` (offline dev only)        |
| `MAX_FILE_SIZE_MB`       | Upload limit (default 50)                            |
| `FLASK_DEBUG`            | `1` only during development                          |

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Check everything before starting:
```bash
python scripts/check_setup.py
```
It prints the backend, credential source and probes each bucket
(`OK` / `MISSING` / `NOT CONFIGURED` / permission notes).

## 4. Run the application

```bash
python app.py
```
Open **<http://127.0.0.1:5000>**. The terminal prints a startup summary: backend, the three
target buckets, ledger file, and any configuration problems.

## 5. Testing guide

### 5.1 Test file upload
1. **Register** two users, e.g. `alice` and `bob` (log out between registrations), then log in as `alice`.
2. **Upload** a file. The flash message shows per-node results:
   `Node 1 ✓  Node 2 ✓  Node 3 ✓`.
3. The dashboard shows the file, owner, SHA-256 hash and **Storage Status** (`3/3 nodes hold this file`).

### 5.2 Test cloud replication
Verify the same object really exists in all three buckets — the object name is the SHA-256 hash:

```bash
HASH=<sha256 shown on the dashboard/verify page>
gsutil ls gs://BUCKET_NODE_1/$HASH
gsutil ls gs://BUCKET_NODE_2/$HASH
gsutil ls gs://BUCKET_NODE_3/$HASH
```
Or in the Console: **Cloud Storage → Buckets → (each bucket)** — each contains an object
named with the 64-character hash. Inspect its **metadata**: `sha256`, `original_filename`,
`owner`, `uploaded_at`.

### 5.3 Test file sharing (access control)
1. As `alice`, use the **Share** dropdown on the dashboard → `bob` → **Share**
   (a `SHARE` block appears in the Ledger).
2. Log in as `bob` → the file appears under **Shared With Me** → **Download** works.
3. Register a third user `charlie` → downloading that file shows
   `Access denied — this file has not been shared with you.`

### 5.4 Test integrity verification
1. As owner, click **Verify** on a file.
2. Expected SHA-256, per-node status, the **actual hash of each copy**, and overall
   blockchain validity are shown:
   ```
   Node 1: ✓ Healthy
   Node 2: ✓ Healthy
   Node 3: ✓ Healthy
   Blockchain is valid
   ```
3. Open the **Ledger** page — REGISTER/UPLOAD/SHARE/DOWNLOAD/VERIFY blocks, each with
   index, timestamp, action, user, filename, file hash, previous hash and block hash.

### 5.5 Test download
Click **Download** on a file you own or that was shared with you. Before serving, the app
(1) checks permission, (2) reads the expected hash from the ledger, (3) fetches a healthy
copy, (4) re-hashes it, and (5) only downloads on a match.

### 5.6 Demonstrate failure of one storage node ⭐ (main demo)
Get the file's hash from the Verify page, then corrupt node 1's copy:

```bash
python scripts/corrupt_node.py <sha256-hash> --node 1
```

Reload the **Verify** page:

```
Node 1: ✗ Integrity Failed
Node 2: ✓ Healthy
Node 3: ✓ Healthy
✗ 2/3 nodes healthy
```

- The **Download still works** — the app detects node 1's bad copy, skips it and serves a
  healthy copy from node 2 or 3 (automatic failover), then re-verifies the hash.
- Repair and return to all-healthy:
  ```bash
  python scripts/restore_node.py <sha256-hash> --node 1
  ```

**Other ways to simulate node failure:**
- *Node unavailable:* set `GCS_BUCKET_NODE_2` to a wrong/nonexistent bucket name and restart →
  Node 2 reports `✗ Bucket missing` while uploads/downloads continue on the other nodes.
- *Node denied:* remove the service account's role on one bucket → Node 2 reports a permission error.
- *Local dev backend only:* directly edit the file `nodes/node_1/<hash>` on disk.

### 5.7 Demonstrate ledger tamper detection
Edit `ledger.json` while the app is stopped (change one field of an old block), start the app
and open the Ledger page → `✕ Block N content/hash mismatch (tampered).`
Restoring the file (or deleting it to start a fresh chain) clears the warning.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Storage setup needs attention` banner | Read the bullet list — it names the missing env var/credential |
| `No Google Cloud credentials found` | Run `gcloud auth application-default login`, or set `GOOGLE_APPLICATION_CREDENTIALS` |
| `Bucket not found` on upload | Create the bucket (2.3) or fix the name in `.env` |
| `Permission denied` on upload | Grant **Storage Object Admin** on that bucket to your identity (2.4) |
| `google-cloud-storage is not installed` | `pip install -r requirements.txt` |
| `File is too large` | Raise `MAX_FILE_SIZE_MB` in `.env` |
| `Your form expired` | Resubmit the form (CSRF token timeout) — refresh the page |
| Everything disappears after restart | Should not happen: users/files live in SQLite, the ledger in `ledger.json`. If you deleted them, re-register |
| Sessions log everyone out | You changed `FLASK_SECRET_KEY`; that's expected |

Run `python scripts/check_setup.py` first whenever the cloud path misbehaves.

## 7. Offline development (optional)

Without any Google Cloud account you can still run the UI using the original local-folder
backend:

```bash
set STORAGE_BACKEND=local     # or: export STORAGE_BACKEND=local
python app.py
```

This uses `nodes/node_1..3` folders and is **only for offline development** — the project
demonstration and submission must run with `STORAGE_BACKEND=gcs` (the default).

## 8. Security notes

- Passwords stored only as Werkzeug pbkdf2/scrypt hashes; no plain-text secrets in code.
- All configuration via environment variables (`.env` is git-ignored).
- CSRF protection on every POST form; `HttpOnly` + `SameSite=Lax` session cookies;
  session id regenerated on login; session cleared on logout.
- File names sanitized (no paths/control characters), upload size limit, username/password
  validation, per-file authorization on download/verify/share.
- For a public deployment also set `SESSION_COOKIE_SECURE=true` and run behind HTTPS with a
  production WSGI server (gunicorn/waitress) instead of `FLASK_DEBUG=1`.

## 9. Viva material

See [`docs/VIVA_GUIDE.md`](docs/VIVA_GUIDE.md) for: full architecture, file-by-file
explanations, concept glossary (local vs cloud storage, replication, decentralization,
blockchain, hashing, SHA-256, integrity, authentication vs authorization, fault tolerance,
bucket vs object), likely viva questions with answers, and a 2–3 minute project pitch.
