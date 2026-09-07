"""Genuine reverse-image-search backed by the Yandex Images engine.

Yandex is the only major free reverse-image engine that still server-renders
its "pages that include this image" results, which makes it scrapable with a
plain HTTP client. We search BY URL (the image must be hosted publicly, see
hosting.py) and parse the result items out of the page.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .hosting import random_ua


@dataclass
class Candidate:
    domain: str
    url: str
    title: str
    original_image: str
    rank: float = 0.0
    note: str = ""
    order: int = 0


# Social platforms we care about, in order of desirability for a "social post".
SOCIAL_DOMAINS = {
    "x.com": 100,
    "twitter.com": 100,
    "instagram.com": 100,
    "tiktok.com": 95,
    "facebook.com": 90,
    "reddit.com": 88,
    "threads.net": 88,
    "youtube.com": 80,
    "bsky.app": 80,
    "vk.com": 74,
    "deviantart.com": 70,
    "pinterest.com": 65,
    "flickr.com": 60,
    "linkedin.com": 60,
    "imgur.com": 58,
    "fotostrana.ru": 55,
    "coub.com": 55,
    "medium.com": 45,
    "tumblr.com": 72,
}

# subdomains that map to the same platform (pinterest locales, etc.)
_STRIP_SUBDOMAINS = {
    "pinterest.com": {"ar", "ru", "hu", "es", "fi", "ca", "pt", "ko", "tr", "in", "de", "fr", "id", "nz", "za", "row", "www"},
    "linkedin.com": {"www"},
    "flickr.com": {"www"},
}


def clean_domain(netloc: str) -> str:
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for base, subs in _STRIP_SUBDOMAINS.items():
        prefix = base.split(".")[0]
        if host.endswith(base):
            top = host[: -(len(base))]
            parts = [p for p in top.split(".") if p]
            if parts and parts[-1] in subs:
                host = base
    return host


def clean_url(url: str) -> str:
    """Strip tracking params added by the search engine."""
    url = html.unescape(url).replace("\\/", "/").strip()
    if not url.startswith(("http://", "https://")):
        return url
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    query = urlencode(keep)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _coerce_http(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


class YandexReverseSearch:
    """Reverse image search against Yandex by URL."""

    ENGINE = "yandex"

    def __init__(self, session: requests.Session | None = None, timeout: int = 60):
        self.timeout = timeout
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": random_ua()})
        else:
            session.headers.setdefault("User-Agent", random_ua())
        self.s = session
        session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self._warmed = False

    def _warm(self) -> None:
        if not self._warmed:
            self.s.get("https://yandex.com/images/", timeout=self.timeout)
            self._warmed = True

    def search(self, image_url: str, max_items: int = 400) -> list[Candidate]:
        """Run a reverse image search for the given public image URL."""
        self._warm()
        params = {"rpt": "imageview", "url": image_url, "format": "html"}
        r = self.s.get("https://yandex.com/images/search", params=params, timeout=self.timeout)
        r.raise_for_status()
        page = html.unescape(r.text)
        items = self._parse(page)
        return items[:max_items]

    @staticmethod
    def _parse(page: str) -> list[Candidate]:
        """Extract result items from the server-rendered Yandex results page."""
        items: list[Candidate] = []
        for i, m in enumerate(re.finditer(r'"domain"\s*:\s*"([^"]+)"', page)):
            dom = m.group(1)
            window = page[max(0, m.start() - 5000): m.end() + 600]

            def grab(key: str) -> str:
                mm = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, window)
                return mm.group(1).replace("\\/", "/") if mm else ""

            url = clean_url(grab("url"))
            title = html.unescape(grab("title")).strip()
            og = re.search(r'"originalImage"\s*:\s*\{[^}]*?"url"\s*:\s*"((?:[^"\\]|\\.)*)"', window)
            orig = og.group(1).replace("\\/", "/") if og else ""
            if not url.startswith(("http://", "https://")):
                continue
            items.append(Candidate(domain=dom, url=url, title=title,
                                   original_image=_coerce_http(orig), order=i))
        return items


def rank_candidates(items: list[Candidate]) -> list[Candidate]:
    """Score candidates; want real social posts of the detected face."""
    for c in items:
        c.domain = clean_domain(urlsplit(c.url).netloc)
        base = SOCIAL_DOMAINS.get(c.domain, 0)
        score = float(base)
        note = "social" if base >= 55 else "other"

        if c.domain in ("t.co",):
            score = 100.0  # resolves to twitter/x; try to resolve later

        # Prefer a direct post page over a bare profile/landing path.
        if base >= 55:
            path = urlsplit(c.url).path.strip("/")
            score += 5.0 if path else 0.0
            # loose preference: profile "@handle" URLs are fine for a *match*,
            # post URLs (/posts/, /status/, /p/, /video/, /watch?v=) are better.
            if re.search(r"/(status|posts|post|p|reel|video|watch|item|pin|gallery|thread|feed)/", c.url):
                score += 4.0

        # The engine orders results by its own relevance; respect it lightly.
        if c.order < 10:
            score += 25.0
        elif c.order < 25:
            score += 12.0
        elif c.order < 50:
            score += 5.0

        # Reduce noise: image-URL results ranked below page results on the same host.
        if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", urlsplit(c.url).path, re.I):
            score -= 30.0
        c.rank = score
        c.note = note
    return sorted(items, key=lambda c: c.rank, reverse=True)


def pick_match(candidates: list[Candidate]) -> Candidate | None:
    ranked = rank_candidates(candidates)
    for c in ranked:
        if c.rank >= 55:
            return c
    return None


def resolve_t_co(c: Candidate, session: requests.Session | None = None, timeout: int = 25) -> Candidate:
    if c.domain != "t.co":
        return c
    try:
        r = (session or requests.Session()).head(c.url, allow_redirects=True, timeout=timeout, headers={"User-Agent": random_ua()})
        final = clean_url(r.url)
        c.url = final
        c.domain = clean_domain(urlsplit(final).netloc)
        c.note = "resolved t.co"
    except Exception:  # noqa: BLE001
        pass
    return c