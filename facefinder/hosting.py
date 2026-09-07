"""Anonymous temporary image hosting so local photos can be reverse-searched.

Reverse image search by URL (the only scrapable entry point on Yandex's engine)
needs the query image to be reachable at a public URL. We push the *face crop*
to a throwaway anonymous host and never the full source photo.
"""

from __future__ import annotations

import requests

DEFAULT_TIMEOUT = 60


def upload_image(data: bytes, filename: str = "query.jpg", content_type: str = "image/jpeg",
                 session: requests.Session | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Upload bytes to one of several anonymous hosts; returns the public URL."""
    s = session or requests.Session()
    last_err: Exception | None = None
    for name, fn in (("uguu", _uguu), ("catbox", _catbox)):
        try:
            url = fn(s, data, filename, content_type, timeout)
            print(f"  [host] {name}: {url}")
            return url
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  [host] {name} failed: {exc}")
    raise RuntimeError(f"all anonymous image hosts failed ({last_err})")


def _uguu(s, data: bytes, filename: str, content_type: str, timeout: int) -> str:
    r = s.post(
        "https://uguu.se/upload",
        files={"files[]": (filename, data, content_type)},
        headers={"User-Agent": random_ua()},
        timeout=timeout,
    )
    r.raise_for_status()
    d = r.json()
    try:
        url = d["files"][0]["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"uguu: unexpected response: {d}") from exc
    if not url:
        raise RuntimeError("uguu: empty url")
    return url


def _catbox(s, data: bytes, filename: str, content_type: str, timeout: int) -> str:
    r = s.post(
        "https://catbox.moe/user/api.php",
        data={"reqtype": "fileupload"},
        files={"fileToUpload": (filename, data, content_type)},
        headers={"User-Agent": random_ua()},
        timeout=timeout,
    )
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox: upload rejected: {url[:120]!r}")
    return url


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]


def random_ua() -> str:
    import random

    return random.choice(_USER_AGENTS)