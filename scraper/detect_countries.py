"""Fill in the country of every tracked store that has none yet (no browser needed).

Runs at the start of the "Scrape new domains" workflow, so a freshly added store gets its
country within a few minutes. Countries you set by hand (country_source = 'manual') are never touched.
"""
from __future__ import annotations

import sys

import db
from country import detect, name_of


def main() -> int:
    if not db.URL or not db.KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.", file=sys.stderr)
        return 2
    rows = db.select("domains", {"country": "is.null", "select": "id,domain"})
    for d in rows:
        try:
            code, why = detect(d["domain"])
        except Exception as e:  # never let one site break the run
            print(f"{d['domain']}: error {e}")
            continue
        print(f"{d['domain']}: {name_of(code)} ({why})")
        if code:
            db.patch("domains", {"id": d["id"]}, {"country": code, "country_source": "auto"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
