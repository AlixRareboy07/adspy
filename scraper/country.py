"""Guess which country a store sells/ships to (ISO-2 code, e.g. PK, IN, AE).

Signals, strongest first:
  1. Shopify /meta.json  ("country" and "currency" of the store)
  2. domain ending (.pk, .in, .co.uk ...)
  3. currency found on the homepage (PKR, INR, AED ...)
  4. phone prefix (+92, +91 ...) and country names mentioned on the page
Nothing needs an API key. Returns None when there is no clear answer
(you can then set the country by hand in the Domains tab).

  python country.py example.com      -> prints the guess
"""
from __future__ import annotations

import re
import sys
from collections import Counter

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

NAMES = {
    "PK": "Pakistan", "IN": "India", "AE": "United Arab Emirates", "BD": "Bangladesh",
    "LK": "Sri Lanka", "NP": "Nepal", "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait",
    "OM": "Oman", "BH": "Bahrain", "GB": "United Kingdom", "US": "United States",
    "CA": "Canada", "AU": "Australia", "NZ": "New Zealand", "MY": "Malaysia",
    "SG": "Singapore", "ID": "Indonesia", "PH": "Philippines", "TR": "Turkey",
    "EG": "Egypt", "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya",
    "DE": "Germany", "FR": "France", "IT": "Italy", "ES": "Spain", "NL": "Netherlands",
}

TLD = {c.lower(): c for c in NAMES}
TLD["uk"] = "GB"

CURRENCY = {
    "PKR": "PK", "INR": "IN", "AED": "AE", "BDT": "BD", "LKR": "LK", "NPR": "NP",
    "SAR": "SA", "QAR": "QA", "KWD": "KW", "OMR": "OM", "BHD": "BH", "GBP": "GB",
    "CAD": "CA", "AUD": "AU", "NZD": "NZ", "MYR": "MY", "SGD": "SG", "IDR": "ID",
    "PHP": "PH", "TRY": "TR", "EGP": "EG", "ZAR": "ZA", "NGN": "NG", "KES": "KE",
    "EUR": None, "USD": "US",
}

PHONE = {"+92": "PK", "+91": "IN", "+971": "AE", "+880": "BD", "+94": "LK", "+977": "NP",
         "+966": "SA", "+974": "QA", "+965": "KW", "+968": "OM", "+973": "BH", "+44": "GB"}

WORDS = {NAMES[c].lower(): c for c in ("PK", "IN", "AE", "BD", "LK", "SA", "GB", "CA", "AU")}


def name_of(code: str | None) -> str:
    return NAMES.get(code or "", code or "Unknown")


def _get(url: str, timeout: int = 20):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        return r if r.ok else None
    except Exception:
        return None


def detect(domain: str) -> tuple[str | None, str]:
    """Return (country_code_or_None, short_reason)."""
    score: Counter = Counter()
    why: dict[str, list[str]] = {}

    def add(code, pts, reason):
        if code:
            score[code] += pts
            why.setdefault(code, []).append(reason)

    # 1) Shopify meta.json
    r = _get(f"https://{domain}/meta.json")
    if r is not None:
        try:
            j = r.json()
            c = (j.get("country") or "").upper()
            if c in NAMES:
                add(c, 6, "store settings")
            cur = (j.get("currency") or "").upper()
            if cur != "USD":
                add(CURRENCY.get(cur), 3, f"currency {cur}")
        except Exception:
            pass

    # 2) domain ending
    parts = domain.lower().split(".")
    if parts[-1] in TLD:
        add(TLD[parts[-1]], 5, f".{parts[-1]} domain")

    # 3+4) homepage text
    r = _get(f"https://{domain}") or _get(f"http://{domain}")
    if r is not None:
        html = r.text[:400000]
        low = html.lower()
        m = re.search(r"Shopify\.currency\s*=\s*\{[^}]*?\"active\"\s*:\s*\"([A-Z]{3})\"", html)
        cur = m.group(1) if m else None
        if not cur:
            m = re.search(r"\"currency\"\s*:\s*\"([A-Z]{3})\"", html)
            cur = m.group(1) if m else None
        if cur and cur != "USD":
            add(CURRENCY.get(cur), 3, f"currency {cur}")
        for code, pts in ((k, 1) for k in ("PKR", "INR", "AED")):
            if re.search(rf"\b{code}\b", html):
                add(CURRENCY[code], pts, f"mentions {code}")
        if re.search(r"\bRs\.?\s?\d", html):
            add("PK", 1, "prices in Rs")  # weak: also used in India, phone/text decide
        for pre, code in PHONE.items():
            if re.search(re.escape(pre) + r"[\s\-]?\(?\d", html):
                add(code, 2, f"phone {pre}")
        for word, code in WORDS.items():
            n = low.count(word)
            if n >= 2:
                add(code, 2, f"mentions {word}")

    if not score:
        return None, "no signal"
    code, pts = score.most_common(1)[0]
    # ties or very weak evidence -> don't guess
    top = score.most_common(2)
    if pts < 3 or (len(top) > 1 and top[1][1] == pts):
        return None, "unclear: " + ", ".join(f"{c}={p}" for c, p in top)
    return code, ", ".join(why[code])


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(d, detect(d))
