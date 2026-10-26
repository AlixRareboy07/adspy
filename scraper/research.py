"""Deep Research worker: searches the public Meta Ad Library for the keywords you typed in the
"Deep research" tab, in the country you picked, and lists every store (Facebook page) that
runs ads for them, with the number of ads per store and per keyword.

  python research.py --count    -> prints how many research requests are waiting
  python research.py            -> process all waiting requests
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, RESEARCH_MAX_ADS (default 300 per keyword)
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict

import db
import scrape
from parse import host_of

MAX_ADS = int(os.environ.get("RESEARCH_MAX_ADS", "300"))
SKIP_HOSTS = ("facebook.com", "fb.com", "fb.me", "instagram.com", "wa.me", "whatsapp.com",
              "messenger.com", "youtube.com", "youtu.be", "linktr.ee", "bit.ly", "tiktok.com",
              "play.google.com", "apps.apple.com", "google.com")


def waiting() -> list[dict]:
    return db.select("research_jobs", {"status": "eq.pending", "select": "*", "order": "created_at.asc"})


def usable_host(url: str | None) -> str | None:
    h = host_of(url)
    if not h or "." not in h:
        return None
    if any(h == s or h.endswith("." + s) for s in SKIP_HOSTS):
        return None
    return h


def run_job(page, job: dict) -> int:
    country = job.get("country") or "ALL"
    stores: dict[str, dict] = {}
    for kw in job["keywords"]:
        scrape.log(f"   keyword '{kw}' in {country}")
        recs, capped = scrape.collect(page, scrape.url_for_keyword(kw, country), MAX_ADS, f"research_{kw}")
        scrape.log(f"      {len(recs)} ads{' (capped)' if capped else ''}")
        for r in recs.values():
            pid = r.get("page_id")
            if not pid:
                continue
            s = stores.setdefault(pid, {"page_name": r.get("page_name"), "hosts": Counter(),
                                        "per_keyword": defaultdict(lambda: {"total": 0, "active": 0}),
                                        "ids": set(), "sample": None})
            pk = s["per_keyword"][kw]
            pk["capped"] = bool(capped)
            if r["ad_archive_id"] in s["ids"]:
                continue
            s["ids"].add(r["ad_archive_id"])
            pk["total"] += 1
            if r["status"] == "active":
                pk["active"] += 1
            h = usable_host(r.get("link_url"))
            if h:
                s["hosts"][h] += 1
            if s["sample"] is None or (r["status"] == "active" and s["sample"]["status"] != "active"):
                s["sample"] = r
        time.sleep(3)

    rows = []
    for pid, s in stores.items():
        pkw = {k: v for k, v in s["per_keyword"].items()}
        total = sum(v["total"] for v in pkw.values())
        active = sum(v["active"] for v in pkw.values())
        sm = s["sample"] or {}
        rows.append({
            "job_id": job["id"], "page_id": pid, "page_name": s["page_name"],
            "domain": s["hosts"].most_common(1)[0][0] if s["hosts"] else None,
            "total_ads": total, "active_ads": active, "per_keyword": pkw,
            "sample_url": sm.get("library_url"), "sample_copy": (sm.get("ad_copy") or sm.get("title") or "")[:300],
        })
    if rows:
        db.upsert("research_results", rows, "job_id,page_id")
    return len(rows)


def main() -> int:
    if not db.URL or not db.KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.", file=sys.stderr)
        return 2
    jobs = waiting()
    if "--count" in sys.argv:
        print(len(jobs))
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"research={len(jobs)}\n")
        return 0
    if not jobs:
        print("No research requests waiting.")
        return 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not os.environ.get("HEADFUL"))
        ctx = browser.new_context(user_agent=scrape.UA, locale="en-US", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        for job in jobs:
            scrape.log(f"== research job {job['id']} ({job['country']}): {job['keywords']}")
            db.patch("research_jobs", {"id": job["id"]}, {"status": "running"})
            try:
                n = run_job(page, job)
                if n == 0:
                    raise RuntimeError("Ad Library returned no ads for these keywords (nothing found, "
                                       "or Facebook blocked this run).")
                db.patch("research_jobs", {"id": job["id"]}, {
                    "status": "done", "error": None,
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                scrape.log(f"   done: {n} stores")
            except Exception as e:
                scrape.log(f"   FAILED: {e}")
                db.patch("research_jobs", {"id": job["id"]}, {"status": "failed", "error": str(e)[:400]})
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
