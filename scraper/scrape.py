"""Daily/periodic scraper: reads the public Meta Ad Library for every tracked domain,
stores ads in Supabase, detects new / stopped ads and writes a daily snapshot.

Run:  python scrape.py            (all active domains)
      python scrape.py --domain example.com
Env:  SUPABASE_URL, SUPABASE_SERVICE_KEY
      AD_COUNTRY (default ALL), MAX_ADS_PER_PAGE (default 400), HEADFUL=1 to watch the browser
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests

import db
from parse import (domain_matches, extract_records, find_facebook_url, json_blobs_from_html,
                   json_blobs_from_text, normalize_domain, page_id_from_fb_url)

COUNTRY = os.environ.get("AD_COUNTRY", "ALL")
MAX_ADS = int(os.environ.get("MAX_ADS_PER_PAGE", "400"))
MAX_SCROLLS = int(os.environ.get("MAX_SCROLLS", "80"))
DEBUG_DIR = Path(__file__).parent / "debug"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- URL builders
def url_for_page(page_id: str) -> str:
    return ("https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
            f"&country={COUNTRY}&media_type=all&search_type=page&view_all_page_id={page_id}")


def url_for_keyword(q: str) -> str:
    return ("https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
            f"&country={COUNTRY}&media_type=all&search_type=keyword_unordered&q={quote(q)}")


# ------------------------------------------------------------- browser logic
def collect(page, url: str, max_ads: int, tag: str) -> tuple[dict[str, dict], bool]:
    """Open an Ad Library URL, scroll, return ({ad_id: record}, truncated)."""
    texts: list[str] = []

    def on_response(resp):
        try:
            if "/api/graphql" in resp.url and resp.request.method == "POST":
                texts.append(resp.text())
        except Exception:
            pass

    page.on("response", on_response)
    records: dict[str, dict] = {}
    truncated = False
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        try:  # cookie banner (decline optional cookies = most private choice)
            page.get_by_role("button", name=re.compile(r"decline optional cookies|allow all cookies", re.I)
                             ).first.click(timeout=3000)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        def harvest():
            for b in json_blobs_from_html(page.content()):
                records.update(extract_records([b]))
            while texts:
                for b in json_blobs_from_text(texts.pop(0)):
                    records.update(extract_records([b]))

        harvest()
        stalled = 0
        for i in range(MAX_SCROLLS):
            before = len(records)
            page.mouse.wheel(0, 6000)
            page.keyboard.press("End")
            page.wait_for_timeout(random.randint(1800, 3000))
            harvest()
            if len(records) >= max_ads:
                truncated = True
                break
            stalled = stalled + 1 if len(records) == before else 0
            if stalled >= 4:
                break
        else:
            truncated = True

        if not records:
            DEBUG_DIR.mkdir(exist_ok=True)
            safe = re.sub(r"[^a-z0-9]+", "_", tag.lower())[:50]
            try:
                page.screenshot(path=str(DEBUG_DIR / f"{safe}.png"), full_page=False)
                (DEBUG_DIR / f"{safe}.html").write_text(page.content()[:400000], encoding="utf-8")
            except Exception:
                pass
    finally:
        page.remove_listener("response", on_response)
    return records, truncated


# --------------------------------------------------------- domain processing
def detect_facebook(domain: str) -> str | None:
    for scheme in ("https://", "http://"):
        try:
            r = requests.get(scheme + domain, headers={"User-Agent": UA}, timeout=20)
            if r.ok:
                fb = find_facebook_url(r.text)
                if fb:
                    return fb
        except Exception:
            continue
    return None


def resolve_pages(page, domain: str) -> tuple[list[str], list[str]]:
    """Find which Facebook Page(s) run ads that send traffic to this domain."""
    recs, _ = collect(page, url_for_keyword(domain), 150, f"resolve_{domain}")
    hits = [r for r in recs.values() if domain_matches(r.get("link_url"), domain) and r.get("page_id")]
    counts = Counter(r["page_id"] for r in hits)
    names = {r["page_id"]: r.get("page_name") for r in hits}
    top = [pid for pid, _ in counts.most_common(3)]
    return top, [names[p] or "" for p in top]


def process_domain(page, d: dict) -> dict:
    domain = d["domain"]
    today = date.today().isoformat()
    log(f"== {domain}")

    fb_url = d.get("fb_page_url") or detect_facebook(domain)
    page_ids = list(dict.fromkeys((d.get("manual_page_ids") or []) + (d.get("page_ids") or [])))
    names = list(d.get("page_names") or [])

    fb_id = page_id_from_fb_url(fb_url)
    if fb_id and fb_id not in page_ids:
        page_ids.append(fb_id)

    if not page_ids:
        log("   no page id yet -> searching Ad Library for ads linking to the domain")
        ids, nms = resolve_pages(page, domain)
        page_ids, names = ids, nms
        if not page_ids:
            raise RuntimeError(
                "Could not find a Facebook Page for this domain. Add the Page ID manually in the Domains tab "
                "(Facebook page -> About -> Page transparency -> Page ID).")
        log(f"   detected page ids: {page_ids}")

    existing = {r["ad_archive_id"]: r for r in db.select(
        "ads", {"domain_id": f"eq.{d['id']}",
                "select": "ad_archive_id,status,first_seen,last_seen,ended_detected,start_date,end_date"})}

    seen: dict[str, dict] = {}
    any_truncated = False
    for pid in page_ids:
        recs, trunc = collect(page, url_for_page(pid), MAX_ADS, f"{domain}_{pid}")
        log(f"   page {pid}: {len(recs)} ads{' (truncated)' if trunc else ''}")
        seen.update(recs)
        any_truncated |= trunc
        time.sleep(random.uniform(3, 6))

    if not seen:
        raise RuntimeError("Ad Library returned 0 ads (page has no ads, or Facebook blocked this run - "
                           "see debug screenshot artifact).")

    new_rows, upd_rows = [], []
    for aid, rec in seen.items():
        active = rec["status"] == "active"
        row = dict(rec, domain_id=d["id"])
        old = existing.get(aid)
        if old is None:
            row.update(first_seen=today, last_seen=today if active else (rec["end_date"] or today),
                       ended_detected=None)
            new_rows.append(row)
        else:
            row.update(last_seen=today if active else old["last_seen"],
                       ended_detected=None if active else (old["ended_detected"] or
                                                          (today if old["status"] == "active" else None)))
            upd_rows.append(row)

    if new_rows:
        db.upsert("ads", new_rows, "ad_archive_id")
    if upd_rows:
        db.upsert("ads", upd_rows, "ad_archive_id")

    # ads we knew as active but no longer listed at all -> treat as stopped today
    vanished = 0
    if not any_truncated:
        for aid, old in existing.items():
            if aid not in seen and old["status"] == "active":
                db.patch("ads", {"ad_archive_id": aid},
                         {"status": "inactive", "ended_detected": today})
                vanished += 1

    # daily snapshot (re-read so numbers match the database exactly)
    rows = db.select("ads", {"domain_id": f"eq.{d['id']}",
                             "select": "status,start_date,first_seen,end_date,ended_detected"})
    active = sum(1 for r in rows if r["status"] == "active")
    new_today = sum(1 for r in rows if (r["start_date"] or r["first_seen"]) == today)
    stopped_today = sum(1 for r in rows if (r["end_date"] or r["ended_detected"]) == today)
    db.upsert("daily_snapshots", [{
        "domain_id": d["id"], "snap_date": today, "active_ads": active,
        "new_ads": new_today, "stopped_ads": stopped_today, "total_ads": len(rows)}],
        "domain_id,snap_date")

    db.patch("domains", {"id": d["id"]}, {
        "last_scraped_at": "now()" if False else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_error": None, "page_ids": [p for p in page_ids if p not in (d.get("manual_page_ids") or [])],
        "page_names": names, "fb_page_url": fb_url})
    db.insert("scrape_log", {"domain_id": d["id"], "ok": True, "ads_found": len(seen),
                             "message": f"{len(new_rows)} new, {vanished} vanished, {active} active"})
    log(f"   done: {len(seen)} ads, {len(new_rows)} new, {active} active, {vanished} vanished")
    return {"ads": len(seen)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", help="only this domain")
    args = ap.parse_args()

    if not db.URL or not db.KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.", file=sys.stderr)
        return 2

    params = {"is_active": "eq.true", "select": "*"}
    if args.domain:
        params["domain"] = f"eq.{normalize_domain(args.domain)}"
    domains = db.select("domains", params)
    if not domains:
        log("No active domains to track. Add some in the web app.")
        return 0

    from playwright.sync_api import sync_playwright

    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not os.environ.get("HEADFUL"))
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        for d in domains:
            try:
                process_domain(page, d)
            except Exception as e:  # keep going with the other domains
                failures += 1
                msg = str(e)[:500]
                log(f"   FAILED {d['domain']}: {msg}")
                try:
                    db.patch("domains", {"id": d["id"]}, {"last_error": msg})
                    db.insert("scrape_log", {"domain_id": d["id"], "ok": False, "message": msg})
                except Exception:
                    pass
        browser.close()
    return 1 if failures == len(domains) else 0


if __name__ == "__main__":
    sys.exit(main())
