# DecentraCloud — Decentralized Cloud File Sharing Using Blockchain

Micro-project prototype for **Cloud Computing (CM51207)**, Semester V,
Government Polytechnic Pune. Maps to CO2, CO3, CO5, CO6.

## What it demonstrates
- **Decentralized storage**: every uploaded file is content-addressed
  (SHA-256) and replicated across three independent storage-node folders
  (`nodes/node_1`, `node_2`, `node_3`) instead of one central location.
- **Blockchain ledger**: every register/upload/share/download event is
  written as a block that hash-chains to the previous block — the classic
  tamper-evidence property of a blockchain, implemented from first
  principles with Python's `hashlib`.
- **Cloud security**: password-hashed auth, per-file access control
  (owner + explicitly shared users), and integrity verification that
  detects a file edited directly on disk.

## Run it
```bash
cd decentracloud
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**

## Demo script for your viva
1. Register two users (e.g. `alice`, `bob`), log in as `alice`.
2. Upload a file — note it's replicated to all 3 node folders on disk.
3. Go to **Blockchain Ledger** — show the chained blocks (`UPLOAD` etc.),
   each with its own hash and a link to the previous block's hash.
4. Share the file with `bob`; log in as `bob` and download it from
   "Shared With Me".
5. **Tamper demo**: open `nodes/node_1/<file-hash>` in a text/hex editor,
   change a byte, save. Reload the file's **Verify** page — `node_1` now
   shows `TAMPERED` while `node_2`/`node_3` still show `OK`, and the file
   still downloads correctly from a healthy node. This is the core
   payoff of the project: no single point of failure or trust.

## Extending this for a stronger submission (optional, for CO6)
- Deploy on an AWS EC2 / GCP Compute Engine / Azure free-tier VM and
  screenshot the public URL working — direct evidence for CO6.
- Swap the three local folders for real distributed backends (e.g.,
  three different cloud storage buckets, or actual IPFS) to move from
  "simulated decentralization" to real decentralization.
- Swap the in-memory `users` / `files_meta` dicts for a real database
  (SQLite is a one-line change) if you want data to persist across
  restarts.

## Project structure
```
decentracloud/
├── app.py            # Flask routes: auth, upload, share, download, ledger
├── blockchain.py      # Block / Blockchain classes — the ledger
├── storage.py          # Content-addressed replication across 3 nodes
├── templates/           # UI (login, upload, files, ledger, verify)
├── nodes/                 # node_1/2/3 — simulated storage nodes (created at runtime)
└── requirements.txt
```
