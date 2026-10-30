"""Parsing helpers: turn Meta Ad Library JSON blobs into flat ad records.

Meta changes the exact JSON layout from time to time, so instead of relying on
one fixed path we walk the whole JSON tree and pick out every dict that looks
like an ad (it has an ``ad_archive_id`` and a ``snapshot``). If Meta renames a
field, this file is the only place you need to touch.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

LIBRARY_AD_URL = "https://www.facebook.com/ads/library/?id={id}"


# ----------------------------------------------------------------- JSON walk
def iter_ad_dicts(node: Any) -> Iterator[dict]:
    """Yield every dict in a JSON tree that looks like an Ad Library ad."""
    if isinstance(node, dict):
        if "ad_archive_id" in node and isinstance(node.get("snapshot"), dict):
            yield node
        else:
            for v in node.values():
                yield from iter_ad_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_ad_dicts(v)


def json_blobs_from_text(text: str) -> Iterator[Any]:
    """Facebook responses may be 'for (;;);{...}' or several JSON objects, one per line."""
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    try:
        yield json.loads(text)
        return
    except Exception:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def json_blobs_from_html(html: str) -> Iterator[Any]:
    """Pull embedded JSON out of <script type="application/json"> tags in the page."""
    for m in re.finditer(r"<script[^>]*type=\"application/json\"[^>]*>(.*?)</script>", html, re.S):
        raw = m.group(1).strip()
        if "ad_archive_id" not in raw:
            continue
        try:
            yield json.loads(raw)
        except Exception:
            continue


# --------------------------------------------------------------- field utils
def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def epoch_to_date(v: Any) -> str | None:
    if v in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _text(v: Any) -> str | None:
    """Ad text may be a plain string or {'text': '...'}."""
    if isinstance(v, dict):
        v = v.get("text")
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        h = urlparse(url if "//" in url else "//" + url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return None


def normalize_domain(d: str) -> str:
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


def domain_matches(url: str | None, domain: str) -> bool:
    h = host_of(url)
    domain = normalize_domain(domain)
    return bool(h) and (h == domain or h.endswith("." + domain))


# ------------------------------------------------------------ ad -> record
def to_record(ad: dict) -> dict:
    """Flatten one Ad Library ad into the columns of our `ads` table."""
    snap = ad.get("snapshot") or {}
    cards = snap.get("cards") or []
    videos = snap.get("videos") or []
    images = snap.get("images") or []

    # --- ad copy: main body, else first card bodies
    body = _text(snap.get("body"))
    if not body and cards:
        parts = [t for t in (_text(c.get("body")) for c in cards) if t]
        body = "\n---\n".join(dict.fromkeys(parts)) or None

    title = _first(_text(snap.get("title")), *(_text(c.get("title")) for c in cards))
    link_url = _first(snap.get("link_url"), *(c.get("link_url") for c in cards))
    cta = _first(snap.get("cta_text"), *(c.get("cta_text") for c in cards))

    # --- media
    video_url = _first(
        *(v.get("video_hd_url") for v in videos),
        *(v.get("video_sd_url") for v in videos),
        *(c.get("video_hd_url") for c in cards),
        *(c.get("video_sd_url") for c in cards),
    )
    video_sd_url = _first(
        *(v.get("video_sd_url") for v in videos),
        *(c.get("video_sd_url") for c in cards),
    )
    image_url = _first(
        *(i.get("resized_image_url") or i.get("original_image_url") for i in images),
        *(v.get("video_preview_image_url") for v in videos),
        *(c.get("resized_image_url") or c.get("original_image_url") for c in cards),
        *(c.get("video_preview_image_url") for c in cards),
    )

    fmt = (snap.get("display_format") or "").upper() or ("VIDEO" if video_url else "IMAGE" if image_url else None)
    plat = ad.get("publisher_platform") or []
    if isinstance(plat, str):
        plat = [plat]

    ad_id = str(ad["ad_archive_id"])
    return {
        "ad_archive_id": ad_id,
        "page_id": str(_first(ad.get("page_id"), snap.get("page_id")) or "") or None,
        "page_name": _first(ad.get("page_name"), snap.get("page_name")),
        "status": "active" if ad.get("is_active") else "inactive",
        "start_date": epoch_to_date(ad.get("start_date")),
        "end_date": epoch_to_date(ad.get("end_date")),
        "ad_copy": body,
        "title": title,
        "cta_text": cta,
        "link_url": link_url,
        "display_format": fmt,
        "video_url": video_url,
        "video_sd_url": video_sd_url,
        "image_url": image_url,
        "platforms": [str(p) for p in plat],
        "collation_count": ad.get("collation_count"),
        "library_url": LIBRARY_AD_URL.format(id=ad_id),
    }


def extract_records(blobs: Iterable[Any]) -> dict[str, dict]:
    """All unique ads (by id) from a list of JSON blobs."""
    out: dict[str, dict] = {}
    for blob in blobs:
        for ad in iter_ad_dicts(blob):
            try:
                rec = to_record(ad)
            except Exception:
                continue
            out[rec["ad_archive_id"]] = rec
    return out


# ------------------------------------------------- finding a page from a site
FB_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9.\-_/?=&%]+)", re.I
)
FB_IGNORE = {"sharer", "sharer.php", "share", "tr", "dialog", "plugins", "login", "policies", "help", "groups", "events", "watch"}


def find_facebook_url(html: str) -> str | None:
    """Look for the brand's Facebook page link on its website (footer/social icons)."""
    for m in FB_LINK_RE.finditer(html):
        path = m.group(1).split("?")[0].strip("/")
        first = path.split("/")[0].lower() if path else ""
        if not first or first in FB_IGNORE:
            continue
        if first == "profile.php":
            idm = re.search(r"id=(\d+)", m.group(0))
            if idm:
                return f"https://www.facebook.com/profile.php?id={idm.group(1)}"
            continue
        return f"https://www.facebook.com/{first}"
    return None


def page_id_from_fb_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"profile\.php\?id=(\d+)", url) or re.search(r"facebook\.com/[^/?]+-(\d{8,})", url)
    return m.group(1) if m else None
