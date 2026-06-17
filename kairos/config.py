"""Shared configuration.

Loads `.env` from the repo root, with real environment variables taking
precedence over file values, and provides a helper to persist values (e.g.
refreshed tokens) back to `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> dict:
    env: dict = {}
    if path.exists():
        for line in path.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_FILE_ENV = load_env()


def cfg(key: str, default: str = "") -> str:
    """Environment variable wins; otherwise fall back to the .env file."""
    val = os.environ.get(key)
    return val if val else _FILE_ENV.get(key, default)


def set_env_vars(updates: dict, path: Path = ENV_PATH) -> None:
    """Update or append KEY=VALUE lines in .env, preserving everything else."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n")
    _FILE_ENV.update(updates)  # keep in-process cache fresh
