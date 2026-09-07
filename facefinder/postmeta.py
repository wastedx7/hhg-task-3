"""Best-effort metadata fetch (OpenGraph) for the matched social post."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

from .hosting import random_ua

_MAX_BYTES = 3_000_000
_OG_KEYS = ("og:title", "og:description", "og:image", "og:url", "og:site_name", "og:type")


@dataclass
class PostMeta:
    url: str
    final_url: str
    status_code: int | None
    content_type: str | None
    page_title: str | None
    og: dict[str, str]
    notes: list[str]


def fetch_post_meta(url: str, session: requests.Session | None = None, timeout: int = 30) -> PostMeta:
    meta = PostMeta(url=url, final_url=url, status_code=None, content_type=None,
                    page_title=None, og={}, notes=[])
    s = session or requests.Session()
    try:
        r = s.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            stream=True,
        )
        meta.status_code = r.status_code
        meta.content_type = r.headers.get("content-type", "")[:80]
        meta.final_url = r.url
        if r.status_code >= 400:
            meta.notes.append(f"HTTP {r.status_code}")
            return meta

        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype:
            meta.notes.append(f"non-HTML response: {ctype}")
            return meta

        chunks: list[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_BYTES:
                meta.notes.append("body truncated")
                break
        body = b"".join(chunks)
        text = _decode(body)
        meta.page_title = _title(text)
        meta.og = _open_graph(text)
    except Exception as exc:  # noqa: BLE001
        meta.notes.append(f"fetch error: {exc}")
    return meta


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "windows-1252", "iso-8859-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def _title(text: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if not m:
        return None
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t[:300] or None


def _open_graph(text: str) -> dict[str, str]:
    og: dict[str, str] = {}
    for key in _OG_KEYS:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)["\']' % key, text)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*?(?:property|name)=["\']%s["\']' % key, text)
        if m:
            og[key] = m.group(1).strip()[:1000]
    return og


def summarize_meta(meta: PostMeta) -> dict[str, Any]:
    return {
        "url": meta.url,
        "final_url": meta.final_url,
        "status_code": meta.status_code,
        "content_type": meta.content_type,
        "page_title": meta.page_title,
        "og": meta.og,
        "notes": meta.notes,
    }