"""Record assembly: the tamper-evident payload + persistence."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .face import FaceInfo

PIPELINE_VERSION = "1.0.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """Deterministic serialisation so fingerprints are stable across runs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def keccak256(data: bytes) -> str:
    from eth_hash.auto import keccak  # sparse & fast

    return keccak(data).hex()


def build_content(
    *,
    created_at: str,
    source_image_path: str,
    source_image_sha256: str,
    image_size: list[int],
    face: FaceInfo,
    query_image_url: str,
    engine: str,
    candidates: list[dict[str, Any]],
    match: dict[str, Any],
    post_meta: dict[str, Any],
) -> dict[str, Any]:
    face_payload = {
        "count": 1,
        "bbox": list(face.bbox),
        "det_score": round(face.det_score, 4),
        "embedding_model": "insightface/buffalo_l/w600k_r50",
        "embedding_dim": int(face.embedding.shape[0]),
        "embedding_norm": round(face.embedding_norm, 4),
        "embedding": face.embedding_hex,  # L2-normalised float32, little-endian hex
    }
    return {
        "pipeline_version": PIPELINE_VERSION,
        "created_at": created_at,
        "source_image": {
            "file": os.path.basename(source_image_path),
            "sha256": source_image_sha256,
            "size_px": image_size,
        },
        "face": face_payload,
        "reverse_search": {
            "engine": engine,
            "query_image_url": query_image_url,
            "candidate_count": len(candidates),
            "top_candidates": candidates[:5],
        },
        "match": match,
        "post_meta": post_meta,
    }


def fingerprint(content: dict[str, Any]) -> str:
    return keccak256(canonical_json(content).encode("utf-8"))


def build_record(
    content: dict[str, Any],
    chain: dict[str, Any],
) -> dict[str, Any]:
    fp = fingerprint(content)
    return {
        "schema_version": PIPELINE_VERSION,
        "fingerprint_algorithm": "keccak256(canonical-json)",
        "fingerprint": "0x" + fp,
        "content": content,
        "chain": chain,
    }


def save_record(record: dict[str, Any], out_dir: str, token: str | int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"record-{token}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    return path


def load_record(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    return record


def recheck_fingerprint(record: dict[str, Any]) -> tuple[bool, str, str]:
    """Recompute the fingerprint from a record's content; compare to stored one."""
    content = record.get("content")
    stored = str(record.get("fingerprint", "")).lower()
    if not isinstance(content, dict):
        return False, "?", "missing content"
    computed = "0x" + fingerprint(content)
    ok = computed.lower() == stored
    return ok, computed, stored