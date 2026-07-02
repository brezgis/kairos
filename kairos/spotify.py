"""Spotify Web API client.

Reads SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REFRESH_TOKEN from
.env and refreshes access tokens automatically (persisting them back).

NOTE: the audio-features and audio-analysis endpoints were deprecated for new
apps on 2024-11-27 — they only work if your Spotify app predates that cutoff.
Listening history (recently-played), top tracks, and library are unaffected.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import cfg, set_env_vars

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"


class SpotifyError(Exception):
    pass


def _access_token() -> str:
    token = cfg("SPOTIFY_ACCESS_TOKEN")
    exp = cfg("SPOTIFY_ACCESS_TOKEN_EXPIRES_AT")
    if token and exp.isdigit() and int(time.time()) < int(exp) - 60:
        return token
    return _refresh()


def _refresh() -> str:
    refresh = cfg("SPOTIFY_REFRESH_TOKEN")
    if not refresh:
        raise SpotifyError("No SPOTIFY_REFRESH_TOKEN in .env.")
    cid, csec = cfg("SPOTIFY_CLIENT_ID"), cfg("SPOTIFY_CLIENT_SECRET")
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode()
    auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            tok = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SpotifyError(f"Token refresh failed (HTTP {e.code}): {e.read().decode(errors='replace')[:200]}")
    updates = {
        "SPOTIFY_ACCESS_TOKEN": tok["access_token"],
        "SPOTIFY_ACCESS_TOKEN_EXPIRES_AT": str(int(time.time()) + int(tok.get("expires_in", 3600))),
    }
    if tok.get("refresh_token"):  # Spotify may rotate it
        updates["SPOTIFY_REFRESH_TOKEN"] = tok["refresh_token"]
    set_env_vars(updates)
    return tok["access_token"]


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token()}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SpotifyError(f"GET {path} failed (HTTP {e.code}): {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        raise SpotifyError(f"GET {path} failed (network: {e.reason})")


def recently_played(limit: int = 50) -> list:
    """Last <=50 plays (Spotify's cap). Poll regularly to accumulate history."""
    return _get("me/player/recently-played", {"limit": limit}).get("items", [])


def top_tracks(limit: int = 50, time_range: str = "short_term") -> list:
    return _get("me/top/tracks", {"limit": limit, "time_range": time_range}).get("items", [])
