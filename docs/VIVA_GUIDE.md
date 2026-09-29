# DecentraCloud — Project & Viva Guide

Everything you need to explain, demo and defend this project.

---

## A. Complete Project Architecture

```
                ┌──────────────────────────────────────────────┐
                │                 Browser (User)               │
                │   Register · Login · Upload · Share · Verify │
                └───────────────────────┬──────────────────────┘
                                        │  HTTPS (forms + CSRF token)
                ┌───────────────────────▼──────────────────────┐
                │              Flask Application (app.py)      │
                │  • Authentication  (Werkzeug password hash)  │
                │  • Authorization   (owner / shared users)    │
                │  • Validation      (username, size, filename)│
                │  • SQLite (database.py): users, files, shares│
                └───────────────────────┬──────────────────────┘
                                        │  file bytes
                            ┌───────────▼───────────┐
                            │      SHA-256 Hash     │  ← content address
                            └───────────┬───────────┘
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
   ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
   │ GCS Node 1 (bucket) │  │ GCS Node 2 (bucket) │  │ GCS Node 3 (bucket) │
   │ object = sha256 hex │  │ object = sha256 hex │  │ object = sha256 hex │
   │ metadata: owner,    │  │ metadata: owner,    │  │ metadata: owner,    │
   │ filename, time      │  │ filename, time      │  │ filename, time      │
   └──────────┬──────────┘  └──────────┬──────────┘  └──────────┬──────────┘
              └─────────────────────────┼─────────────────────────┘
                                        ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │ Blockchain Ledger (blockchain.py → ledger.json)                    │
   │ Block = {index, timestamp, action, filename, file_hash, user,      │
   │          previous_hash, hash}   — each block commits to the last   │
   └────────────────────────────────────────────────────────────────────┘
```

**Data flows**

*Upload:* bytes → SHA-256 → *parallel* upload to all 3 buckets (object name = hash) →
metadata row in SQLite → `UPLOAD` block appended to the ledger.

*Download:* permission check → expected hash from ledger → download copies node-by-node →
re-hash → match ⇒ serve; mismatch/unavailable ⇒ try next node (failover) → `DOWNLOAD` block.

*Verify:* download every node's copy → re-hash each → per-node ✓/✗ with actual hashes →
ledger validity check → `VERIFY` block.

**What stayed local vs what moved to the cloud**

| Component | Before | Now |
|---|---|---|
| File storage | 3 local folders `nodes/node_1..3` | **3 Google Cloud Storage buckets** (unchanged logic: content-addressed, replicated) |
| Users / files / shares | in-memory dicts (lost on restart) | SQLite `decentracloud.db` |
| Blockchain | in-memory list (lost on restart) | persisted `ledger.json` (atomic writes) |
| Secrets | hard-coded `secret_key` in `app.py` | environment variables (`.env`) |
| UI, routes, auth, hash-chain concept | — | **unchanged**, improved with validation/errors/CSRF |

---

## B. Updated Folder Structure

```
decentracloud/
├── app.py                 # Flask routes + config + security   [CHANGED]
├── storage.py             # GCS 3-bucket storage layer         [CHANGED]
├── blockchain.py          # Hash-chain ledger + persistence    [CHANGED]
├── database.py            # SQLite persistence (NEW)
├── requirements.txt       # + google-cloud-storage etc.        [CHANGED]
├── .env.example           # config template (NEW)
├── .gitignore             # + secrets/db/ledger rules          [CHANGED]
├── README.md              # full setup & testing guide         [CHANGED]
├── scripts/
│   ├── check_setup.py     # config/bucket diagnostics (NEW)
│   ├── corrupt_node.py    # simulate node corruption (NEW)
│   └── restore_node.py    # repair a node (NEW)
├── templates/
│   ├── base.html          # flash-message categories           [CHANGED]
│   ├── files.html         # dashboard with storage status      [CHANGED]
│   ├── verify.html        # per-node health + actual hashes    [CHANGED]
│   ├── ledger.html        # timestamps + full hash columns     [CHANGED]
│   ├── upload.html        # target buckets + size limit        [CHANGED]
│   ├── how_it_works.html  # GCS diagram + concept glossary     [CHANGED]
│   ├── login.html         # CSRF + autocomplete                [CHANGED]
│   ├── register.html      # CSRF + validation hints            [CHANGED]
│   ├── error.html         # friendly 404/500                   [NEW]
│   └── (how_it_works, etc.)
├── docs/VIVA_GUIDE.md     # this file (NEW)
└── runtime (git-ignored): decentracloud.db, ledger.json, .secret_key, nodes/
```

---

## C. List of Every Changed / Added File

**Changed:** `app.py`, `storage.py`, `blockchain.py`, `requirements.txt`, `README.md`,
`.gitignore`, `templates/base.html`, `templates/files.html`, `templates/verify.html`,
`templates/ledger.html`, `templates/upload.html`, `templates/how_it_works.html`,
`templates/login.html`, `templates/register.html`

**Added:** `database.py`, `.env.example`, `templates/error.html`,
`scripts/check_setup.py`, `scripts/corrupt_node.py`, `scripts/restore_node.py`,
`docs/VIVA_GUIDE.md`

**Removed:** nothing (all original features kept).

---

## D. Complete Code

The complete, tested code for every file above is in this repository — each file listed in
section C is the final version (all tests pass: 45 end-to-end checks + 16 GCS-path checks).

---

## E. Important Code Sections in Easy Language

### `storage.py` — the cloud storage layer
- **Node list:** three display labels mapped to `GCS_BUCKET_NODE_1/2/3` read from `.env`.
  One function `_nodes()` returns the active nodes, so the *same* higher-level code works
  for `gcs` (buckets) and `local` (folders, offline dev).
- **`compute_hash(bytes)`** — SHA-256 hex of the content. This hash *is* the address:
  object name inside every bucket = the hash (like IPFS). Same content ⇒ same address;
  one bit changed ⇒ completely different address.
- **`save_file(...)`** — computes the hash once, then uploads the identical bytes to all
  three nodes **in parallel** (`ThreadPoolExecutor`), attaching object metadata
  (`sha256, original_filename, owner, uploaded_at`). Returns per-node
  `OK / NOT_CONFIGURED / NO_BUCKET / ERROR` — it never throws, so one bad node cannot
  crash an upload; the caller warns if fewer than 3 nodes succeeded and aborts only if
  *all* failed.
- **`read_file(hash)`** — failover read: try Node 1 → download → re-hash → if it doesn't
  match, *silently skip* and try Node 2 → Node 3. Returns `(bytes, node_label)` of the
  first healthy copy, else `(None, None)`.
- **`verify_integrity(hash)`** — deep check: downloads every copy in parallel and re-hashes
  it → `OK / TAMPERED (actual_hash shown) / MISSING / NO_BUCKET / ERROR`. This powers the
  Verify page.
- **`presence(hash)`** — cheap check (no download): does the object exist? Powers the
  dashboard's `3/3 nodes` status without downloading files.
- **`_classify(exc)`** — converts Google API exceptions (NotFound, Forbidden, auth errors,
  network errors) into readable statuses so students see *"Permission denied — check the
  service-account roles"* instead of a traceback.
- **`corrupt_copy / repair_copy`** — demo helpers used by `scripts/` to simulate and repair
  a failed node. Not reachable from any web route.

### `app.py` — routes and security
- **Config:** `load_dotenv()` runs *first*, then everything reads environment variables
  (`FLASK_SECRET_KEY`, `MAX_FILE_SIZE_MB`, `FLASK_PORT`, …). If no secret key is given, one
  is generated once and cached in git-ignored `.secret_key` so sessions survive restarts.
- **Upload route:** read bytes → clean filename → `save_file(...)` (hash + 3-way
  replication) → if *no* node accepted: friendly error, **no** ledger block; otherwise save
  metadata (`db.upsert_file`, duplicate names replace the owner's old row) → `UPLOAD` block
  → flash shows `Node 1 ✓ Node 2 ✓ Node 3 ✗ (reason)`.
- **Download route:** permission check → `find_latest_file_hash(filename, owner)` from the
  ledger → `read_file` (healthy copy only) → re-hash → **must match the ledger hash** or the
  download is blocked → `DOWNLOAD` block → `send_file`.
- **Verify route:** permission check → deep `verify_integrity` → ledger `is_valid()` →
  expected vs recorded hash → `VERIFY` block → rich template.
- **Security fixes:** CSRF on every POST, username regex + password length validation,
  `session.clear()` on login/logout, sanitized filenames, `MAX_CONTENT_LENGTH`, custom
  404/413/500/CSRF error handling, no hard-coded secrets.

### `blockchain.py` — the ledger
- **Block** = `{index, timestamp, action, filename, file_hash, user, previous_hash, hash}`;
  `hash = SHA-256(json of all those fields)`. Chain = blocks linked by `previous_hash`.
- **`is_valid()`** recomputes every block hash and every link → any edit anywhere is
  detected ("content/hash mismatch" or "chain broken").
- **Persistence:** chain saved to `ledger.json` after every block using *atomic write*
  (write to `.tmp`, then rename) so a crash can't corrupt it. On startup the *stored* hash
  is loaded rather than recomputed, so editing the JSON file is detectable (demo in 5.7 of
  README).
- **`find_latest_file_hash(filename, owner)`** — the authoritative expected hash used before
  downloads/verification; `owner` disambiguates same-named files from different users.

### `database.py` — persistence
- Three SQLite tables: `users`, `files` (`UNIQUE(owner, filename)` — duplicate-name
  uploads replace the previous version instead of silently overwriting someone else's),
  `shares` (many-to-many permission list). Short-lived connections + a write lock keep
  Flask's threaded server safe.

### Templates
- **Dashboard** — logged-in user, per-file owner, hash, live per-node storage status
  (`Node 1 ✓ …`, `3/3 nodes hold this file`), sharing pills, actions.
- **Verify** — expected hash, ledger hash, per-node `✓ Healthy / ✗ Integrity Failed /
  ✗ Missing / ✗ Unavailable` with bucket names and the **actual hash of each copy**,
  `n/3 nodes healthy`, overall blockchain validity, and the exact demo commands.
- **Ledger** — block cards + full table: Block #, Timestamp, Action, User, Filename,
  File Hash, Previous Hash, Block Hash.

---

## F–K. Setup, Run and Demo Steps

See the **README** (sections 1–6): GCS project/API/bucket/auth setup (F), run locally (G),
test upload/share/verify/download (H), prove cloud replication in the three buckets (I),
prove integrity verification (J), and simulate one failed node with
`python scripts/corrupt_node.py <hash> --node 1` (K).

---

## L. Possible Viva Questions and Answers

**Q1. What is this project in one line?**
A file-sharing web app where files are hashed with SHA-256, replicated to three Google
Cloud Storage buckets, and every action is written to a tamper-evident hash-chain ledger.

**Q2. Why is it called "decentralized"? Is it a real blockchain?**
There is no single storage location holding the only copy — three independent buckets hold
identical copies, plus a separate ledger, so no single point of failure or trust. The ledger
is a lightweight educational hash-chain (blocks linked by hashes), *not* a cryptocurrency
chain like Bitcoin/Ethereum — no mining, no tokens, no peers/consensus.

**Q3. What is the difference between local storage and cloud storage?**
Local storage = files on your own machine's disk (`nodes/node_1` folders — the original
version). Cloud storage = files on provider servers accessed over the internet via API —
here three GCS buckets that Google maintains (availability, replication, billing handled
by Google).

**Q4. What is replication, and where do you do it?**
Storing identical copies in multiple places. On upload, `storage.save_file` writes the same
bytes to all three buckets in parallel; the object name is the SHA-256 hash in each.

**Q5. Bucket vs object?**
A *bucket* is the top-level container (globally unique name, region, access rules). An
*object* is one item inside it: bytes + name + metadata. Our objects are named by the
file's SHA-256 and carry metadata: sha256, original filename, owner, upload time.

**Q6. What is hashing? Why SHA-256? Why is the hash used as the address?**
Hashing = one-way function producing a fixed-length fingerprint; same input → same output,
tiny change → completely different output; irreversible. SHA-256 gives 64 hex characters
(256 bits) with strong collision resistance. Using it as the object name is
*content addressing* (like IPFS): identical content deduplicates, any tampering changes the
name so the mismatch is detected.

**Q7. What is integrity and how do you check it?**
Data unchanged. At verification/download we re-download a copy, recompute SHA-256 and
compare with the expected hash from metadata and the ledger. Match ⇒ healthy; mismatch ⇒
that node's copy is flagged `Integrity Failed` and skipped.

**Q8. Authentication vs authorization?**
Authentication = who you are (login with username + hashed password). Authorization = what
you may do: only the owner can share; only owner or explicitly shared users can
download/verify. Both are enforced server-side on every route.

**Q9. How are passwords stored?**
Never in plain text — Werkzeug applies a salted adaptive hash (pbkdf2/scrypt). Login
re-hashes the typed password and compares.

**Q10. What is fault tolerance? Show it.**
The system keeps working when components fail. Demo: corrupt node 1's copy → Verify shows
`✗ Integrity Failed` for node 1, `✓` for 2 and 3, yet Download still works — the app skips
the bad copy and serves a healthy one, then re-verifies its hash.

**Q11. What happens if a bucket is deleted or you lose permission?**
The upload/verify marks that node `✗ Bucket missing` / permission error with a readable
message; other nodes still serve the file; `scripts/check_setup.py` diagnoses configuration.

**Q12. What happens inside a block? Why is tampering detectable?**
`hash = SHA-256(index, timestamp, action, filename, file_hash, user, previous_hash)`, and
the next block stores this hash as its `previous_hash`. Edit any old block → its hash
changes → it no longer matches the stored value *and* every later `previous_hash` link
breaks → `is_valid()` reports exactly which block failed.

**Q13. Is this "the blockchain" from Bitcoin?**
No. It reuses only the core *idea* — a chain of hashed blocks — as a tamper-evident audit
log. No mining, consensus, tokens or network nodes; it runs inside one Flask process.

**Q14. What is the difference between "3 nodes" here and real blockchain nodes?**
Here a "node" is a storage location (a GCS bucket) providing copies for redundancy. Real
blockchain nodes are peers that each keep the full ledger and agree by consensus. We combine
both ideas: replicated storage + append-only hashed ledger.

**Q15. Which is better: one bucket or three? Why?**
One bucket = single point of failure. Three buckets give redundancy: lose/corrupt one copy
and the file is still available and verifiable — that's the project's whole point.

**Q16. Does replication make it fully "decentralized"?**
It is a step toward decentralization (no single copy/location). A production system would
add independent providers/regions and a distributed consensus ledger; this project
demonstrates the concept at diploma scale.

**Q17. Where are users/files stored? Why not keep dictionaries?**
SQLite (`decentracloud.db`) and `ledger.json` — original in-memory dicts lost everything on
restart; persistent storage also lets the UNIQUE constraint prevent duplicate-name problems.

**Q18. What security issues did you fix?**
Hard-coded secret key → env var/generated file; added CSRF tokens, input validation
(username/password/filename/size), session regeneration on login, HttpOnly + SameSite
cookies, permission checks on verify, graceful error handling (404/413/500), and credential
hygiene (`.env`, service-account JSON in `.gitignore`).

**Q19. How do you prevent someone from downloading others' files?**
Every download/verify route re-checks ownership/sharing server-side (tested: a third user
gets "Access denied"). CSRF protects the POST forms.

**Q20. What would you add next?**
Expiry/time-limited sharing links, real database for multi-instance deployment, chunked
uploads for large files, multi-cloud (GCS + S3) providers per node, and a distributed
consensus ledger instead of a single process's chain.

---

## M. 2–3 Minute Project Presentation Script

> **[0:00–0:30] Problem.** "Today we store files on central servers. If that server fails
> or is tampered with, data is lost or altered, and we have no way to *prove* the file we
> downloaded is the original one. DecentraCloud solves both: it replicates files across
> three independent cloud storage locations and keeps a tamper-evident record of every
> action."
>
> **[0:30–1:15] Architecture.** "A user logs into a Flask web app. On upload, the app reads
> the bytes, computes a SHA-256 hash, and stores the same file in **three Google Cloud
> Storage buckets — Node 1, Node 2 and Node 3** — in parallel. The hash itself is used as
> the object's name, so every bucket holds an identical, content-addressed copy, along with
> metadata like owner, original filename and upload time. Every event — register, upload,
> share, download, verify — is appended to a **blockchain-style ledger** where each block
> stores the hash of the block before it."
>
> **[1:15–2:00] Live demo.** "Here is the dashboard: file, owner, hash, and live status of
> all three nodes — 3 of 3 healthy. This is the verification page: expected hash at the top,
> and below, the actual hash of every node's copy — all ✓ Healthy — plus overall blockchain
> validity. Now I *corrupt* Node 1's copy with one command… reload: Node 1 shows
> **✗ Integrity Failed**, Nodes 2 and 3 are still healthy — and watch: the **download still
> works**, because the app skips the bad copy and serves a healthy one after re-checking its
> hash. Repair, and we're back to 3 of 3."
>
> **[2:00–2:40] Why it matters.** "This demonstrates the three ideas my course asked for:
> **replication** for availability, **hashing** for integrity, and a **hash-chain ledger**
> for accountability — no single point of failure, no blind trust. Authentication uses
> salted password hashes, authorization is enforced per file, and configuration lives in
> environment variables so no secrets enter the repository."
>
> **[2:40–3:00] Closing.** "All code is modular — `app.py` for routes, `storage.py` for the
> cloud layer, `blockchain.py` for the ledger, `database.py` for persistence — with a full
> setup guide and automated tests. Thank you — happy to take questions."

**Expected follow-up question:** *"Is this a real blockchain?"* → Answer with Q13 above.
