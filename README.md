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
        ──►Notary smart contract on an EVM chain (local / ganache / testnet)
        ──► re-verify on-chain, write records/record-*.json
```

No website is included on purpose — everything is the pipeline itself.

## What each stage does

1. **Face identification** (`facefinder/face.py`)
   InsightFace (`buffalo_l`: SCRFD detector + ArcFace `w600k_r50`) detects the
   largest face, and produces a 512-dimensional biometric encoding
   (L2-normalised float32, also stored as hex). The face crop (with margin) is
   what gets searched.
2. **Reverse-image search** (`facefinder/searcher.py`)
   The crop is pushed to an anonymous image host (uguu.se, catbox.moe fallback)
   so the image is URL-addressable, then queried against the Yandex Images
   engine by URL. This is a **real search performed at run time** — scratch the
   `records/*.json` files, every URL, title and engine rank inside them was
   fetched live. Candidates from social platforms (X/Twitter, Instagram,
   TikTok, YouTube, Reddit, …) are ranked by platform desirability + the
   engine's own result order.
3. **Match validation** — the top candidates are probed in order: the post page
   is fetched (OpenGraph metadata), its `og:image` is downloaded, a face is
   detected in it, and its embedding is compared to the input face by cosine
   similarity. The first candidate above `--face-threshold` (default 0.40) is
   accepted as the match; the similarity and every probe are stored in the
   record under `content.match`.
4. **Blockchain record** (`contracts/Notary.sol` via `facefinder/chain.py`)
   The canonical record (source-image hash, face embedding, engine, candidates,
   match, post metadata) is fingerprinted with **keccak256** and stored in the
   `Notary` smart contract together with the post URL:
   `record(source, url, fingerprint) → record_id`. The on-chain tuple is then
   read back and compared — `on-chain verification: fingerprint match = True`.
5. **Verify any time** (`facefinder verify`) — recompute the fingerprint from a
   saved record file and (when the chain is reachable) read the on-chain
   record and compare. Any modification of the file, or of the chain entry,
   shows up as a mismatch.

## Blockchain used

Any EVM-compatible chain, via two backends:

| backend | chain | durability |
|---|---|---|
| `local` (default) | in-process Ethereum EVM (`eth-tester` + `py-evm`), chain id 1337 | lives only for the run; on-chain verification happens in-process right after recording |
| `rpc` | any JSON-RPC endpoint — persistent local node (`npm run node`, ganache) or a public testnet (e.g. Base Sepolia, Polygon Amoy) | records persist; `verify` re-checks the chain in a later process |

A `Notary` contract is compiled with solc 0.8.24 and deployed fresh per run.
Public-testnet usage just needs `RPC_URL` + a funded `PRIVATE_KEY`
(see `.env.example`).

## Quick start (Windows PowerShell)

Python 3.11 is recommended (best wheel coverage for the ML stack).

```powershell
# 1. environment (uv is fastest, plain pip works too)
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# or: .venv\Scripts\python -m pip install -r requirements.txt

# 2. run the whole pipeline on the sample photo (default: local chain)
.venv\Scripts\python -m facefinder run --input samples\obama.jpg
```

On first run the InsightFace models (~300 MB) and the solc 0.8.24 compiler are
downloaded automatically. Expect the run to take 1–4 minutes (face models load
once, then search + metadata fetches + chain deploy).

Outputs:

- a console transcript of every stage (faces, engine candidates, probes with
  `face-sim`, fingerprint, contract address, tx hash, block number),
- `records/record-<timestamp>.json` — the full tamper-evident record.

Re-verify a saved record later:

```powershell
.venv\Scripts\python -m facefinder verify --record records\<name>.json
```

Tamper demo — change one byte of a record file and re-verify: the recomputed
fingerprint no longer matches the stored/on-chain one.

```powershell
# copy a record, edit content.match.url inside, then verify
.venv\Scripts\python -m facefinder verify --record records\record-TAMPERED.json
# => matches stored fingerprint? NO - RECORD WAS MODIFIED
```

## Persistent local chain (ganache)

```powershell
npm install            # installs ganache (see package.json)
npm run node           # persistent EVM at http://127.0.0.1:8545, data in ./chaindata

# generate a key, start the node with it funded, then run the rpc backend:
.venv\Scripts\python -c "from eth_account import Account; a=Account.create(); print(a.key.hex())"
node node_modules/ganache/dist/node/cli.js --server.host 127.0.0.1 --server.port 8545 `
  --chain.chainId 1337 --database.dbPath ./chaindata --wallet.accounts "0x<KEY>,1000000000000000000000"
.venv\Scripts\python -m facefinder run --input samples\obama.jpg --chain rpc `
  --rpc-url http://127.0.0.1:8545 --private-key 0x<KEY> --chain-id 1337
.venv\Scripts\python -m facefinder verify --record records\<new>.json   # works after restarts
```

Public testnet instead: copy `.env.example` to `.env`, set `FACEFINDER_CHAIN=rpc`,
`RPC_URL`, `CHAIN_ID` and a funded `PRIVATE_KEY`, then run with `--chain rpc`.

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

## Docker

The repo ships a `Dockerfile` (pipeline image, ~2.8 GB — face models and solc
are pre-fetched at build time) and `docker-compose.yml` (pipeline + a
persistent ganache EVM with its data in the `ganache-data` volume).

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
package.json       local EVM helper (ganache)
requirements.txt   python dependencies
.env.example       chain/search configuration template
```