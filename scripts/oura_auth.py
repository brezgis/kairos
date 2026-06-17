#!/usr/bin/env python3
"""Kairos - one-time Oura OAuth2 authorization.

Runs the Authorization Code flow once to obtain a long-lived refresh token for
your own Oura account, then saves the tokens to .env (which is gitignored).

Usage:
    1. cp .env.example .env
    2. Fill in OURA_CLIENT_ID and OURA_CLIENT_SECRET in .env
    3. python3 scripts/oura_auth.py
    4. Approve in the browser window that opens

After this, the backend uses OURA_REFRESH_TOKEN to mint access tokens forever.
No third-party dependencies - Python standard library only.

Note: run this on the same machine whose browser you'll click in, since the
redirect goes to localhost.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# --- Oura OAuth2 endpoints -------------------------------------------------
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
PERSONAL_INFO_URL = "https://api.ouraring.com/v2/usercollection/personal_info"

# Documented Oura scopes. Stress and heart-health (cardiovascular age) data are
# served under the "daily" scope, so this set covers the full Kairos dataset.
DEFAULT_SCOPES = "personal daily heartrate workout tag session spo2 heart_health stress"

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# --- tiny .env reader/writer (no python-dotenv dependency) -----------------
def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def set_env_vars(path: Path, updates: dict) -> None:
    """Update or append KEY=VALUE lines in .env, preserving everything else."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


# --- local callback server -------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.server.callback_path:
            self.send_response(404)
            self.end_headers()
            return
        self.server.query = urllib.parse.parse_qs(parsed.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;margin:3rem'>"
            b"<h2>Kairos: Oura authorization complete.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, *args):  # silence default request logging
        pass


def wait_for_callback(host: str, port: int, path: str) -> dict:
    httpd = HTTPServer((host, port), _CallbackHandler)
    httpd.callback_path = path
    httpd.query = None
    try:
        while httpd.query is None:
            httpd.handle_request()
    finally:
        httpd.server_close()
    return httpd.query


# --- HTTP helpers ----------------------------------------------------------
def post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fail(msg: str):
    print(f"\n[x] {msg}", file=sys.stderr)
    sys.exit(1)


# --- main ------------------------------------------------------------------
def main() -> None:
    # Flush prints (the auth URL especially) immediately, even when stdout is
    # redirected to a file or pipe.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    file_env = load_env(ENV_PATH)

    def cfg(key, default=""):
        # Real environment variables take precedence over the .env file.
        val = os.environ.get(key)
        return val if val else file_env.get(key, default)

    client_id = cfg("OURA_CLIENT_ID").strip()
    client_secret = cfg("OURA_CLIENT_SECRET").strip()
    redirect_uri = cfg("OURA_REDIRECT_URI", "http://localhost:8080/callback").strip()
    scopes = cfg("OURA_SCOPES", DEFAULT_SCOPES).strip()

    if not client_id or client_id.startswith("your_"):
        fail("OURA_CLIENT_ID is missing. Copy .env.example to .env and fill it in.")
    if not client_secret or client_secret.startswith("your_"):
        fail("OURA_CLIENT_SECRET is missing. Copy .env.example to .env and fill it in.")

    parsed = urllib.parse.urlparse(redirect_uri)
    path = parsed.path or "/callback"

    # The redirect_uri advertised to Oura is fixed (must match what you
    # registered). The local callback server can bind a *different* host:port via
    # OURA_CALLBACK_BIND - handy when that port is taken or reached via SSH tunnel.
    bind_spec = cfg("OURA_CALLBACK_BIND").strip()
    if bind_spec:
        if ":" in bind_spec:
            bhost, _, bport = bind_spec.rpartition(":")
            host = bhost or "127.0.0.1"
            port = int(bport)
        else:
            host, port = "127.0.0.1", int(bind_spec)
    else:
        host = parsed.hostname or "localhost"
        port = parsed.port or 8080

    state = secrets.token_urlsafe(24)
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
        }
    )

    print("Opening your browser to authorize Kairos with Oura...")
    print(f"If it doesn't open, paste this URL into a browser manually:\n\n{auth_url}\n")
    print(f"Listening for the redirect on {host}:{port}{path} ...")
    webbrowser.open(auth_url)

    try:
        query = wait_for_callback(host, port, path)
    except OSError as e:
        fail(
            f"Could not start the local server on {host}:{port} ({e}). "
            f"Is the port already in use? Check OURA_REDIRECT_URI."
        )

    if "error" in query:
        fail(
            f"Authorization denied: {query['error'][0]} "
            f"({query.get('error_description', [''])[0]})"
        )

    if query.get("state", [None])[0] != state:
        fail("State mismatch - possible CSRF. Aborting.")

    code = query.get("code", [None])[0]
    if not code:
        fail("No authorization code was returned.")

    print("Got authorization code. Exchanging for tokens...")
    try:
        tok = post_form(
            TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        fail(f"Token exchange failed (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        fail(f"Network error during token exchange: {e}")

    access_token = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    expires_in = tok.get("expires_in")
    if not refresh_token:
        fail(f"No refresh_token in response: {tok}")

    updates = {
        "OURA_ACCESS_TOKEN": access_token or "",
        "OURA_REFRESH_TOKEN": refresh_token,
    }
    if isinstance(expires_in, int):
        updates["OURA_ACCESS_TOKEN_EXPIRES_AT"] = str(int(time.time()) + expires_in)
    set_env_vars(ENV_PATH, updates)

    print(f"\n[ok] Tokens saved to {ENV_PATH}")
    print(f"     granted scope: {tok.get('scope', scopes)}")
    if isinstance(expires_in, int):
        print(
            f"     access token valid ~{expires_in // 3600}h; "
            f"refresh token persists for ongoing use."
        )

    # Quick sanity check that the token actually works end to end.
    if access_token:
        try:
            info = get_json(PERSONAL_INFO_URL, access_token)
            shown = {k: info[k] for k in ("age", "biological_sex", "height", "weight") if k in info}
            print(f"\n[ok] Verified against the API - personal_info: {shown}")
        except Exception as e:  # noqa: BLE001
            print(f"\n[!] Tokens saved, but the verification call failed: {e}")

    print(
        "\nDone. The backend can now use OURA_REFRESH_TOKEN to refresh access "
        "tokens automatically."
    )


if __name__ == "__main__":
    main()
