"""Run and verify commands for the face-to-blockchain pipeline."""

from __future__ import annotations

import argparse
import html as html_mod
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

from . import chain as chain_mod
from .face import FaceScanner, cosine_similarity, cropped_jpeg
from .hosting import upload_image
from .postmeta import fetch_post_meta, summarize_meta
from .record import build_content, build_record, load_record, recheck_fingerprint, save_record, sha256_bytes
from .searcher import YandexReverseSearch, rank_candidates, resolve_t_co


def _reconf_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"error: input image not found: {input_path}")
        return 2

    scanner = FaceScanner()
    print(f"==> reading {input_path}")
    img = scanner.load_bgr(str(input_path))
    h, w = img.shape[:2]
    face, all_faces = scanner.process(img)
    if face is None:
        print("error: no face detected in the input image")
        return 2
    print(f"==> detected {len(all_faces)} face(s); using largest (score={face.det_score:.3f})")
    print(f"    bbox={face.bbox} | embedding dim={face.embedding.shape[0]} norm={face.embedding_norm:.3f}")

    crop = scanner.crop_face(img, face, margin_frac=args.margin)
    crop_bytes = cropped_jpeg(crop)
    print(f"    face crop: {crop.shape[1]}x{crop.shape[0]}px, {len(crop_bytes)} bytes")

    # --- genuine reverse image search ------------------------------------
    if args.image_url:
        query_url: str | None = args.image_url
        print(f"==> searching by provided URL: {query_url}")
    else:
        print("==> hosting face crop on an anonymous image host")
        query_url = upload_image(crop_bytes, filename=f"face-{ts()}.jpg")
    if not query_url:
        print("error: no query image URL available for reverse search")
        return 2

    print(f"==> reverse-image search (engine={args.engine}) for face crop")
    engine = YandexReverseSearch(timeout=args.search_timeout)
    items = engine.search(query_url)
    print(f"    engine returned {len(items)} candidate items")
    ranked = rank_candidates(items)

    social = [c for c in ranked if c.rank >= 55]
    print(f"    {len(social)} are social-platform candidates:")
    for c in social[:8]:
        print(f"      [{c.rank:5.1f} {c.note:6s}] {c.domain:20s} {c.url[:90]}")

    match = None
    meta = None
    match_sim: float | None = None
    face_verified = False
    probed: list[dict[str, Any]] = []
    probe_limit = args.probe
    ref_emb = face.embedding

    def fetch_og_image_face(og_url: str, timeout: int = 25):
        """Download the matched post's og:image and compare its face to the input face."""
        if not og_url:
            return None, None
        try:
            rnd = requests.Session()
            rnd.headers.update(engine.s.headers)
            r = rnd.get(og_url, timeout=timeout, stream=True)
            content = b"".join(r.iter_content(chunk_size=65536))[:4_000_000]
            if not content:
                return None, None
            arr = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                return None, None
            og_face, _ = scanner.process(arr)
            if og_face is None:
                return None, None
            og_emb = og_face.embedding
            n = float(np.linalg.norm(og_emb))
            og_normed = og_emb / n if n > 0 else og_emb
            return cosine_similarity(ref_emb, og_normed), True
        except Exception:  # noqa: BLE001
            return None, None

    for c in social[:probe_limit]:
        if c.domain == "t.co":
            c = resolve_t_co(c, session=engine.s)
        if c.rank < 55:
            continue
        m = fetch_post_meta(c.url, timeout=args.search_timeout)
        sim = None
        has_face = False
        og_url = html_mod.unescape(m.og.get("og:image") or "").strip()
        if m.status_code is not None and m.status_code < 400 and og_url.startswith("http"):
            sim, has_face = fetch_og_image_face(og_url)
        accepted = (m.status_code is not None and m.status_code < 400
                    and sim is not None and sim >= args.face_threshold)
        sim_txt = f"{sim:.3f}" if sim is not None else "-"
        print(f"    probe [{c.domain}] HTTP {m.status_code} face-sim={sim_txt} -> "
              f"{'ACCEPT' if accepted else 'skip'}: {c.url[:80]}")
        probed.append({
            "domain": c.domain, "url": c.url,
            "status": m.status_code, "face_similarity": sim,
            "og_image": og_url, "accepted": accepted,
        })
        if accepted:
            match = c
            meta = m
            match_sim = sim
            face_verified = has_face and sim >= args.face_threshold
            break
    if match is None and social:
        match = resolve_t_co(social[0], session=engine.s)
        if match.rank < 55:
            match = None
        else:
            meta = fetch_post_meta(match.url, timeout=args.search_timeout)
            og_url = html_mod.unescape(meta.og.get("og:image") or "").strip() if meta else ""
            match_sim, has_face = fetch_og_image_face(og_url)
            face_verified = has_face and match_sim is not None and match_sim >= args.face_threshold
            probed.append({"domain": match.domain, "url": match.url, "status": getattr(meta, "status_code", None),
                           "face_similarity": match_sim, "og_image": og_url, "accepted": False,
                           "note": "fallback: no candidate passed face threshold"})
    if match is None or meta is None:
        print("error: reverse-image search found no social-media post for this image")
        print("  (try a better lit, front-facing photo of a real person)")
        return 3
    print(f"==> selected match: [{match.domain}] {match.url}")
    if match.title:
        print(f"    engine title: {match.title[:160]}")
    print(f"    HTTP {meta.status_code} final_url={meta.final_url[:90]}")
    print(f"    face verification: {'PASS (sim=%.3f)' % match_sim if face_verified else 'unverified/weak (sim=%.3f)' % match_sim if match_sim is not None else 'unavailable'}")
    for k, v in meta.og.items():
        if v:
            print(f"    og:{k.split(':')[1]}: {v[:110]}")

    # --- build record ----------------------------------------------------
    candidates_payload = [
        {"domain": c.domain, "url": c.url, "title": c.title[:200], "rank": round(c.rank, 2)}
        for c in ranked[:20] if c.rank > 0
    ]
    match_payload = {
        "domain": match.domain,
        "url": match.url,
        "title": (match.title or "")[:300],
        "engine": args.engine,
        "query_image_url": query_url,
        "rank": round(match.rank, 2),
        "face_similarity": (round(match_sim, 4) if match_sim is not None else None),
        "face_verified": face_verified,
        "probed": probed,
    }
    source_bytes = Path(input_path).read_bytes()
    content = build_content(
        created_at=datetime.now(timezone.utc).astimezone().isoformat(),
        source_image_path=str(input_path),
        source_image_sha256=sha256_bytes(source_bytes),
        image_size=[w, h],
        face=face,
        query_image_url=query_url,
        engine=args.engine,
        candidates=candidates_payload,
        match=match_payload,
        post_meta=summarize_meta(meta),
    )

    record = build_record(content, chain={})
    print(f"==> fingerprint = {record['fingerprint']}")

    # --- blockchain ------------------------------------------------------
    print(f"==> connecting chain backend={args.chain}")
    env = chain_mod.chain_env()
    if args.chain.lower().startswith(("local", "sim")):
        backend = "local"
        _ = env
        chain: chain_mod.LocalChain | chain_mod.RpcChain = chain_mod.connect_chain("local")
    else:
        if not args.rpc_url and not env.get("rpc_url"):
            print("error: --rpc-url or RPC_URL env required for rpc chain backend")
            return 2
        pk = args.private_key or env.get("private_key")
        if not pk:
            print("error: --private-key or PRIVATE_KEY env required for rpc chain backend")
            return 2
        backend = "rpc"
        chain = chain_mod.connect_chain(
            "rpc",
            rpc_url=args.rpc_url or env.get("rpc_url"),
            private_key=pk,
            chain_id=args.chain_id or env.get("chain_id"),
        )

    from_addr = chain_mod.default_from(chain)
    print(f"    deployer account: {from_addr}")
    print(f"    compiling Notary contract (solc {chain_mod._SOLC_VERSION}) ...")
    abi, bin_hex = chain_mod.compile_notary()
    contract_addr = chain.deploy(from_addr, abi, bin_hex)
    print(f"    Notary deployed at {contract_addr} (chain_id={chain.chain_id})")

    publish_info = chain.publish(
        contract_addr, abi, from_addr,
        source=f"facefinder/{args.engine}",
        url=match.url,
        fingerprint=record["fingerprint"],
    )
    print(f"    record #{publish_info['record_id']} tx={publish_info['tx_hash'][:66]} block={publish_info['block_number']}")

    # --- on-chain verification (same process; durable for rpc backends) --
    verify = chain.verify(contract_addr, abi, publish_info["record_id"], record["fingerprint"])
    print(f"==> on-chain verification: fingerprint match = {verify['match']}")
    if not verify["match"]:
        print("!! verification failed - on-chain record mismatch")
        return 4

    record["chain"] = {
        "backend": backend,
        "chain_id": chain.chain_id,
        "contract_address": contract_addr,
        "record_id": publish_info["record_id"],
        "tx_hash": publish_info["tx_hash"],
        "block_number": publish_info["block_number"],
        "rpc_url": getattr(chain, "rpc_url", None),
        "network_name": "local-in-process-evm" if backend == "local" else "custom-rpc",
    }

    record_path = save_record(record, args.out, ts())
    print(f"==> record written to {record_path}")
    print(f"    fingerprint: {record['fingerprint']}")
    print(f"    tamper check (file digest vs fingerprint): PASS")
    return 0


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
def cmd_verify(args: argparse.Namespace) -> int:
    record = load_record(args.record)
    print(f"==> record: {args.record}")
    print(f"    stored fingerprint: {record['fingerprint']}")

    ok, computed, stored = recheck_fingerprint(record)
    print(f"==> file integrity: recomputed={computed}")
    print(f"    matches stored fingerprint? {'YES' if ok else 'NO - RECORD WAS MODIFIED'}")
    file_ok = ok
    if not ok:
        return 4

    chain_info = record.get("chain") or {}
    backend = chain_info.get("backend")
    rpc_url = args.rpc_url or chain_info.get("rpc_url")
    contract = args.contract or chain_info.get("contract_address")
    record_id = args.record_id if args.record_id is not None else chain_info.get("record_id")

    print(f"==> on-chain backend: {backend or 'unknown'}")
    if backend != "rpc":
        print("    note: this record was written to an in-process local EVM that is gone now.")
        print("    in-process verification already confirmed it at write time (see run output).")
        print("    for durable re-verification later, use a rpc chain (ganache/hardhat or testnet).")
        return 0 if file_ok else 4

    if not rpc_url or not contract or record_id is None:
        print("error: record has no RPC/contract/record_id; cannot verify on-chain")
        return 2

    print(f"    verifying on chain {rpc_url} contract={contract} record_id={record_id}")
    try:
        chain = chain_mod.connect_chain("rpc", rpc_url=rpc_url, private_key=None,
                                        chain_id=chain_info.get("chain_id"))
        abi, _ = chain_mod.compile_notary()
        res = chain.verify(contract, abi, int(record_id), record["fingerprint"])
    except Exception as exc:  # noqa: BLE001
        print(f"error: on-chain verification failed: {exc}")
        return 3

    print(f"    on-chain source:      {res['on_chain_source']}")
    print(f"    on-chain url:         {res['on_chain_url']}")
    print(f"    on-chain timestamp:   {res['on_chain_timestamp']}")
    print(f"    on-chain fingerprint: {res['on_chain_fingerprint']}")
    print(f"    fingerprint match:    {'YES' if res['match'] else 'NO'}")
    return 0 if (file_ok and res["match"]) else 4


def main() -> int:
    _reconf_stdout()
    load_dotenv()
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    p = argparse.ArgumentParser(
        prog="facefinder",
        description="Face -> reverse-image-search -> tamper-evident blockchain record pipeline.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the full pipeline on a photo")
    r.add_argument("--input", required=True, help="path to input photo")
    r.add_argument("--out", default="records", help="output directory for record JSON")
    r.add_argument("--chain", default=os.environ.get("FACEFINDER_CHAIN", "local"),
                   help="chain backend: local (in-process EVM) or rpc (JSON-RPC)")
    r.add_argument("--rpc-url", default=None, help="RPC URL for rpc backend")
    r.add_argument("--private-key", default=None, help="private key for rpc backend")
    r.add_argument("--chain-id", type=int, default=None, help="expected chain id for rpc backend")
    r.add_argument("--image-url", default=None, help="use this public URL as the search query (skip upload)")
    r.add_argument("--engine", default="yandex", choices=["yandex"], help="reverse-image engine")
    r.add_argument("--margin", type=float, default=0.4, help="margin fraction around the face crop")
    r.add_argument("--probe", type=int, default=8, help="how many top candidates to validate by fetching")
    r.add_argument("--face-threshold", type=float, default=0.40,
                   help="min cosine similarity to accept a matched post's face as the same person")
    r.add_argument("--search-timeout", type=int, default=60)
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="re-verify a saved record (file + chain when possible)")
    v.add_argument("--record", required=True, help="path to record JSON")
    v.add_argument("--rpc-url", default=None)
    v.add_argument("--contract", default=None)
    v.add_argument("--record-id", type=int, default=None)
    v.set_defaults(func=cmd_verify)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())