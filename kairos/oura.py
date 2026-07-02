"""Oura API v2 client with automatic OAuth2 token refresh.

Reads credentials/tokens from `.env` (via config). When the access token is
missing or near expiry, it refreshes using OURA_REFRESH_TOKEN and persists the
new access token (and any rotated refresh token) back to `.env`.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import cfg, set_env_vars

API_BASE = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"


class OuraError(Exception):
    pass


def _access_token() -> str:
    token = cfg("OURA_ACCESS_TOKEN")
    expires_at = cfg("OURA_ACCESS_TOKEN_EXPIRES_AT")
    if token and expires_at.isdigit() and int(time.time()) < int(expires_at) - 120:
        return token
    return _refresh()


def _refresh() -> str:
    refresh_token = cfg("OURA_REFRESH_TOKEN")
    if not refresh_token:
        raise OuraError("No OURA_REFRESH_TOKEN in .env — run scripts/oura_auth.py first.")
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cfg("OURA_CLIENT_ID"),
        "client_secret": cfg("OURA_CLIENT_SECRET"),
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            tok = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise OuraError(f"Token refresh failed (HTTP {e.code}): {e.read().decode(errors='replace')[:300]}")
    updates = {"OURA_ACCESS_TOKEN": tok["access_token"]}
    if "expires_in" in tok:
        updates["OURA_ACCESS_TOKEN_EXPIRES_AT"] = str(int(time.time()) + int(tok["expires_in"]))
    if tok.get("refresh_token"):  # Oura may rotate the refresh token
        updates["OURA_REFRESH_TOKEN"] = tok["refresh_token"]
    set_env_vars(updates)
    return tok["access_token"]


def _get(path: str, params: dict) -> dict:
    url = f"{API_BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token()}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise OuraError(f"GET {path} failed (HTTP {e.code}): {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise OuraError(f"GET {path} failed (network: {e.reason})")


def _paginate(path: str, params: dict):
    while True:
        page = _get(path, params)
        for rec in page.get("data", []):
            yield rec
        nxt = page.get("next_token")
        if not nxt:
            break
        params = {**params, "next_token": nxt}


def fetch_range(path: str, start_date: str, end_date: str, datetime_range: bool = False, chunk_days: int = 28):
    """Yield every record for a date(time)-range endpoint, following pagination.

    Datetime endpoints (e.g. heartrate) cap the query window, so we walk the
    range in chunk_days-sized segments.
    """
    if not datetime_range:
        yield from _paginate(path, {"start_date": start_date, "end_date": end_date})
        return
    cur = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    while cur < end:
        seg_end = min(cur + dt.timedelta(days=chunk_days), end)
        yield from _paginate(path, {
            "start_datetime": f"{cur}T00:00:00+00:00",
            "end_datetime": f"{seg_end}T00:00:00+00:00",
        })
        cur = seg_end


def fetch_single(path: str) -> dict:
    """For non-collection endpoints like personal_info that return one object."""
    return _get(path, {})
