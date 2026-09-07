# Face → Web → Blockchain: tamper-evident social-match pipeline

`facefinder` is a command-line pipeline that takes a photo as input, detects and
encodes the face in it, performs a **genuine reverse-image search** to find a
real matching social-media post on the web, and then writes a fingerprint of
that discovery to a blockchain as a tamper-evident record.

```
photo ──► face detect + encode (512-d embedding)
        ──► anonymous hosting of the face crop
        ──► reverse-image search (Yandex) → candidate social posts
        ──► face-verify each candidate (embedding similarity)
        ──► fetch post metadata → build canonical record → keccak256 fingerprint
        ──► Notary smart contract on an EVM chain (local / ganache / testnet)
        ──► re-verify on-chain, write records/record-*.json
```

No website is included on purpose — everything is the pipeline itself.

## Requirements checklist (what this repo delivers)

| Requirement | Where it lives |
|---|---|
| Detect and encode a face from an input image | `facefinder/face.py` — InsightFace `buffalo_l` (SCRFD detector + ArcFace `w600k_r50`), 512-d embedding |
| Genuine reverse-image search, real matching social post, no hardcoding | `facefinder/searcher.py` — live Yandex Images by-URL queries at run time; every URL/title/rank in `records/` was fetched live |
| Upload the match to a blockchain, tamper-evident + verifiable | `contracts/Notary.sol` + `facefinder/chain.py` — keccak256 fingerprint stored on-chain, read back and compared |
| No website | None built — CLI only (`facefinder/cli.py`) |
| README with functionality, run instructions, blockchain, limitations | This file |
| Unedited end-to-end screen recording | Record `run` + `verify` as described in “What success looks like” below |

## Prerequisites

| Need | Version / note | Why |
|---|---|---|
| Python | **3.11** recommended (best wheel coverage for the ML stack) | InsightFace + onnxruntime + py-evm |
| `uv` or `pip` | any recent version (`uv` is much faster) | installing `requirements.txt` |
| Internet access | required at **run time**, not just install | live reverse-image search + anonymous image hosting |
| Disk space | ~500 MB free (native) / ~3 GB (Docker image) | face models ~300 MB, solc binary, deps |
| Node.js + npm | only for the persistent-local-chain path | `ganache` EVM node |
| Docker Desktop | only for the container path | `Dockerfile` / `docker-compose.yml` |

Check yours first:

```powershell
python --version   # want 3.11.x (3.10–3.12 also work; 3.13+ lose some wheels)
node --version     # only needed for ganache
docker --version   # only needed for containers
```

## Setup (Windows PowerShell)

```powershell
cd C:\path\to\hhg-task-3

# 1. create the environment (uv is fastest, plain pip works too)
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# …or without uv:
# py -3.11 -m venv .venv
# .venv\Scripts\python -m pip install -r requirements.txt

# 2. confirm the CLI loads
.venv\Scripts\python -m facefinder run --help
```

On the first real run, the InsightFace models (~300 MB, auto-downloaded from
GitHub) and the solc 0.8.24 compiler (auto-downloaded) are fetched. Subsequent
runs reuse them — models live in `~/.insightface`, the compiler in `~/.solcx`.

## Run the pipeline

```powershell
# Full pipeline on the sample photo. Default chain backend is `local`
# (in-process EVM, zero setup). Takes ~2–4 minutes, mostly the live search.
.venv\Scripts\python -m facefinder run --input samples\obama.jpg
```

### What success looks like

You should see each stage report in order (values vary run to run — the search
is live, so candidates/URLs change):

```
==> reading samples\obama.jpg
==> detected 1 face(s); using largest (score=0.903)
    bbox=(1002, 223, 1633, 1150) | embedding dim=512 norm=19.771
    face crop: 1135x1520px, 355320 bytes
==> hosting face crop on an anonymous image host
  [host] uguu: https://….uguu.se/….jpg
==> reverse-image search (engine=yandex) for face crop
    engine returned ~260 candidate items
    23 are social-platform candidates:
      [ 97.0 social] youtube.com  https://www.youtube.com/shorts/…
      …
    probe [youtube.com] HTTP 200 face-sim=0.964 -> ACCEPT: https://…
==> selected match: [youtube.com] https://www.youtube.com/shorts/…
    face verification: PASS (sim=0.964)
==> fingerprint = 0x…
==> connecting chain backend=local
    Notary deployed at 0x… (chain_id=1337)
    record #0 tx=0x… block=2
==> on-chain verification: fingerprint match = True
==> record written to records\record-20260907T….json
    fingerprint: 0x…
    tamper check (file digest vs fingerprint): PASS
```

Two artifacts prove the run: the console transcript above and
`records/record-<timestamp>.json` (full record: source-image hash, face
embedding, engine candidates, match + probes, post metadata, chain receipt).

### Run on your own photo

```powershell
.venv\Scripts\python -m facefinder run --input C:\photos\someone.jpg --out records
```

Use a clear, front-facing, well-lit photo. Public figures match best — reverse
engines can only find what is already posted publicly, so a private photo of
an unknown person will very likely end with `error: reverse-image search found
no social-media post` (exit code 3). That is the search telling the truth, not
a bug.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | full pipeline succeeded, record written and verified |
| `2` | usage/input problem — e.g. `error: no face detected in the input image` |
| `3` | genuine search completed but found no social-media post |
| `4` | verification failed — fingerprint mismatch (tampered record or chain entry) |

## Verify a record (and prove tamper-evidence)

```powershell
# re-verify a saved record: recomputes the fingerprint from the file and,
# when the chain is reachable, compares it with the on-chain entry
.venv\Scripts\python -m facefinder verify --record records\<name>.json
# expect: matches stored fingerprint? YES  (+ fingerprint match: YES for rpc records)
```

Tamper demo — exact commands, no manual editing needed (the pattern below
targets the sample run's match URL; for any other record just change any single
character inside its `content` object — the effect is identical):

```powershell
Copy-Item records\<name>.json records\tampered.json
(Get-Content records\tampered.json -Raw) -replace 'youtube\.com/shorts/o144x9fTln0','evil.example/post' | Set-Content records\tampered.json
.venv\Scripts\python -m facefinder verify --record records\tampered.json
# expect: matches stored fingerprint? NO - RECORD WAS MODIFIED   (exit code 4)
Remove-Item records\tampered.json
```

Any single changed byte anywhere in `content` produces a different keccak256,
so the modified file can never match the stored/on-chain fingerprint again.

## Blockchain used

Any EVM-compatible chain, via two backends:

| backend | chain | durability |
|---|---|---|
| `local` (default) | in-process Ethereum EVM (`eth-tester` + `py-evm`), chain id 1337 | lives only for the run; on-chain verification happens in-process right after recording |
| `rpc` | any JSON-RPC endpoint — persistent local node (ganache, below) or a public testnet (e.g. Base Sepolia, Polygon Amoy) | records persist; `verify` re-checks the chain in a later process, even after restarts |

The `Notary` contract (`contracts/Notary.sol`, compiled with solc 0.8.24,
deployed fresh per run) exposes `record(source, url, fingerprint) → id` and
`get(id) → (fingerprint, source, url, timestamp)`. Each run prints the contract
address, transaction hash and block number; `records/*.json` stores them under
`chain` so anyone can re-verify later.

### Persistent local chain (ganache)

```powershell
npm install   # installs ganache (see package.json)

# generate a throwaway key and start a funded, persistent node with it:
.venv\Scripts\python -c "from eth_account import Account; a=Account.create(); print(a.key.hex())"
node node_modules/ganache/dist/node/cli.js --server.host 127.0.0.1 --server.port 8545 `
  --chain.chainId 1337 --database.dbPath ./chaindata --wallet.accounts "0x<KEY>,1000000000000000000000"

# run the rpc backend against it (separate terminal):
.venv\Scripts\python -m facefinder run --input samples\obama.jpg --chain rpc `
  --rpc-url http://127.0.0.1:8545 --private-key 0x<KEY> --chain-id 1337

# later — even after stopping/restarting the node — re-verify:
.venv\Scripts\python -m facefinder verify --record records\<new>.json
# expect: file integrity YES + on-chain fingerprint match YES
```

### Public testnet

```powershell
Copy-Item .env.example .env
# edit .env: FACEFINDER_CHAIN=rpc, RPC_URL, CHAIN_ID, and a *funded* PRIVATE_KEY
# e.g. Base Sepolia: RPC_URL=https://sepolia.base.org  CHAIN_ID=84532
.venv\Scripts\python -m facefinder run --input samples\obama.jpg --chain rpc
.venv\Scripts\python -m facefinder verify --record records\<new>.json
```

Never commit a real private key — `.env` is git-ignored.

## Docker

The repo ships a `Dockerfile` (pipeline image, ~2.8 GB — face models and solc
are pre-fetched at build time) and `docker-compose.yml` (pipeline + a
persistent ganache EVM with its data in the `ganache-data` volume; the compose
file uses a documented throwaway dev key — dev only).

```powershell
# build once (takes a while: pip deps + ~300MB of face models)
docker build -t facefinder:latest .

# 1) self-contained run: in-process chain, record lands in ./records
docker run --rm -v "${PWD}/records:/app/records" facefinder:latest run --input samples/obama.jpg

# 2) verify a record (file integrity; local-chain note for `local` records)
docker run --rm -v "${PWD}/records:/app/records" facefinder:latest verify --record records/<name>.json

# 3) tamper demo: edit a copy, verify flags it
Copy-Item records/<name>.json records/tampered.json
# ... change content.match.url inside tampered.json ...
docker run --rm -v "${PWD}/records:/app/records" facefinder:latest verify --record records/tampered.json
# => matches stored fingerprint? NO - RECORD WAS MODIFIED

# 4) persistent chain + rpc backend (durable on-chain records)
docker compose up -d chain
docker compose run --rm facefinder run --input samples/obama.jpg --chain rpc
docker compose run --rm facefinder verify --record records/<new>.json
# => file integrity YES + on-chain fingerprint match YES
docker compose down   # chain data persists in the ganache-data volume
```

How to test, in order: `run --help` → full `run` (expect `face verification:
PASS` and `on-chain verification: fingerprint match = True`) → `verify` on
the produced record (expect `YES`) → tampered copy (expect `NO - RECORD WAS
MODIFIED`) → compose `chain` + rpc `run` + cross-container `verify` (expect
`YES`/`YES`). The pipeline needs internet from inside the containers for the
live reverse-image search; if your network blocks container egress, the search
step will fail while everything else still works.

## Useful options

```
facefinder run --input photo.jpg [--out records]
  --chain local|rpc            chain backend (default: local)
  --rpc-url / --private-key / --chain-id   for rpc backend
  --image-url URL              skip anonymous hosting, search this URL directly
  --engine yandex               reverse-image engine
  --margin 0.4                  margin around the face crop
  --probe 8                     top-N social candidates to validate
  --face-threshold 0.40         min cosine similarity to accept the match
  --search-timeout 60

facefinder verify --record records/x.json [--rpc-url …] [--contract …] [--record-id N]
```

Set `FACEFINDER_FACE_MODEL` to a different InsightFace pack (e.g. `antelopev2`)
if you want a lighter download.

## The sample photo

`samples/obama.jpg` is the official 2013 portrait of Barack Obama from
Wikimedia Commons, chosen because it is front-facing, high-resolution, and
widely reposted (so reverse-image engines genuinely find social matches):

`https://upload.wikimedia.org/wikipedia/commons/8/8d/President_Barack_Obama.jpg`

For this input the pipeline currently matches a real YouTube Short whose
poster frame is literally this portrait (face similarity 0.96 — the uploader's
title text is spam, but the visual face match is verified). The shipped demo
records show this exact outcome.

## Record layout

```jsonc
{
  "schema_version": "1.0.0",
  "fingerprint_algorithm": "keccak256(canonical-json)",
  "fingerprint": "0x…",
  "content": {
    "pipeline_version": "1.0.0",
    "created_at": "…",
    "source_image":   { "file": "…", "sha256": "…", "size_px": [w, h] },
    "face":           { "bbox": […], "det_score": 0.9,
                        "embedding_model": "insightface/buffalo_l/w600k_r50",
                        "embedding_dim": 512, "embedding": "<hex>" },
    "reverse_search": { "engine": "yandex", "query_image_url": "…",
                        "candidate_count": N, "top_candidates": […] },
    "match":          { "domain": "youtube.com", "url": "…",
                        "face_similarity": 0.9639, "face_verified": true,
                        "probed": […] },
    "post_meta":      { "status_code": 200, "page_title": "…", "og": {…} }
  },
  "chain": { "backend": "local|rpc", "chain_id": 1337, "contract_address": "0x…",
             "record_id": 0, "tx_hash": "…", "block_number": 2 }
}
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: no face detected` (exit 2) | no clear frontal face in the input | use a front-facing, well-lit photo |
| `…found no social-media post` (exit 3) | nobody publicly posted this face | expected for private/unknown people; try a public figure |
| Search step hangs then errors | Yandex rate-limiting or blocking your network | wait and retry; try another network |
| `cannot reach JSON-RPC …` | `rpc` backend but no node running | start ganache / check `RPC_URL` (the CLI retries ~60 s, then gives up) |
| `Unexpected private key length` | malformed `PRIVATE_KEY` | must be 64 hex chars (`0x…`); generate a fresh one as shown above |
| First run is very slow | one-time downloads (models ~300 MB, solc, pip packages) | wait it out; later runs reuse everything |
| `docker build` fails on `py-solc-x` | stale `requirements.txt` pin | ensure the line reads `py-solc-x>=2.0` (2.0.5 is latest) |
| Container search fails but native works | container has no internet egress | allow Docker through the firewall/VPN, then retry |

## Known limitations

- **Search-engine brittleness.** Yandex is the only free engine that currently
  server-renders reverse-image results. It rate-limits / captchas aggressively
  from some networks; the run will then fail loudly in the search step. Google
  Lens and Yandex push results behind client-side JS; Bing's upload endpoint
  returns 400 to plain HTTP clients — all were evaluated and rejected as
  backends.
- **Match quality depends on the engine.** Reverse-image results can include
  lookalikes, memes with mismatched titles, or low-resolution reposts. The
  embedding-similarity gate (`--face-threshold`) filters most junk, but there
  is no identity guarantee — treat this as a *demonstration* pipeline, not a
  forensic tool. Famous/public photos match best; private photos of unknown
  people often have no social results at all.
- **Face scope.** Only the largest detected face is processed. No liveness,
  age/gender or "official account" reasoning.
- **Anonymous hosting.** The face *crop* is uploaded to uguu.se/catbox.moe so
  the engine can fetch it. If you are uncomfortable with that,
  pass `--image-url` for an image you already host.
- **Chain ephemerality.** The default `local` backend is an in-memory EVM —
  perfect for the demo, but its state disappears when the process exits. Use
  the `rpc` backend (ganache or a testnet) for records you want to re-verify
  later.
- **Social login walls.** Instagram/X/TikTok often return login walls or sparse
  OpenGraph tags to scrapers; metadata is always best-effort. The visual
  (face) check still applies via the CDN image when `og:image` is present.
- **Heavy first run.** InsightFace models (~300 MB), solc binary and npm
  packages are downloaded on first use; subsequent runs are fast.

## Layout

```
facefinder/        pipeline package (face, hosting, searcher, postmeta, record, chain, cli)
contracts/Notary.sol   on-chain notary
samples/           demo input photo
records/           tamper-evident record JSONs produced by runs
Dockerfile / docker-compose.yml / .dockerignore   container setup
package.json       local EVM helper (ganache)
requirements.txt   python dependencies
.env.example       chain/search configuration template
```