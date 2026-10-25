"""Tiny Supabase REST client (only needs `requests`)."""
from __future__ import annotations

import os
from typing import Any

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _h(extra: dict | None = None) -> dict:
    h = {"apikey": KEY, "Content-Type": "application/json"}
    if KEY.startswith("eyJ"):  # legacy JWT keys also go in Authorization; new sb_secret_ keys do not
        h["Authorization"] = f"Bearer {KEY}"
    if extra:
        h.update(extra)
    return h


def _check(r: requests.Response) -> requests.Response:
    if not r.ok:
        raise RuntimeError(f"Supabase {r.request.method} {r.url.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
    return r


def select(table: str, params: dict | None = None) -> list[dict]:
    out: list[dict] = []
    start, step = 0, 1000
    while True:
        r = _check(requests.get(
            f"{URL}/rest/v1/{table}", params=params or {},
            headers=_h({"Range-Unit": "items", "Range": f"{start}-{start + step - 1}"}), timeout=60))
        rows = r.json()
        out.extend(rows)
        if len(rows) < step:
            return out
        start += step


def upsert(table: str, rows: list[dict], on_conflict: str) -> None:
    for i in range(0, len(rows), 200):
        _check(requests.post(
            f"{URL}/rest/v1/{table}", params={"on_conflict": on_conflict},
            headers=_h({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            json=rows[i:i + 200], timeout=60))


def insert(table: str, row: dict) -> None:
    _check(requests.post(f"{URL}/rest/v1/{table}", headers=_h({"Prefer": "return=minimal"}), json=row, timeout=60))


def patch(table: str, match: dict[str, Any], values: dict) -> None:
    params = {k: f"eq.{v}" for k, v in match.items()}
    _check(requests.patch(f"{URL}/rest/v1/{table}", params=params,
                          headers=_h({"Prefer": "return=minimal"}), json=values, timeout=60))
