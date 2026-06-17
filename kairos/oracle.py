"""The Daily Oracle — turns the curated brief into a reading.

Reuses the user's `~/.local/bin/agent-generate` wrapper (headless Claude Code, with
local Qwopus-via-Hermes as fallback), so the oracle inherits the same
Claude-routine-with-fallback pipeline as daily-briefing. The model is given the
*real* curated brief (Oura/weather/check-in features) and asked to write a short
grounded reading; the result is cached per day in the `oracle` table.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import db, features
from .config import cfg

AGENT_GENERATE = Path(os.path.expanduser("~/.local/bin/agent-generate"))

TONES = {
    "plain": "plain, grounded, kind but factual",
    "mystic": "mysterious, oracular, lightly poetic",
    "warm": "warm, intimate, a little mysterious",
}


def _morning(conn, day: str) -> dict:
    row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (day,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def _voice(conn) -> str:
    row = conn.execute("SELECT value FROM app_state WHERE key = 'kairos:prefs'").fetchone()
    if row:
        try:
            return json.loads(row[0]).get("voice", "warm")
        except Exception:
            pass
    return "warm"


def _prompt(brief: dict, morning: dict, voice: str, out_path: str) -> str:
    tone = TONES.get(voice, TONES["warm"])
    data = {
        "day": brief.get("day"),
        "notable_vs_baseline": brief.get("notable"),
        "features": brief.get("features"),
        "active_insights": brief.get("active_insights"),
        "morning_checkin": morning,
    }
    return (
        "You are Kairos — an empirical oracle. You read a person's real data for the day "
        "(sleep, readiness, weather, listening, their morning check-in) and write a short daily "
        "reading: an 'empirical horoscope' grounded entirely in the numbers, never invented.\n\n"
        f"Voice: {tone}.\n\n"
        "Rules:\n"
        "- Write 3-5 sentences. No preamble, no heading, no lists, no markdown.\n"
        "- Use ONLY the data below. Reference real figures/patterns (a low readiness, a notable "
        "deviation, the weather, what they logged). If a field is missing or null, don't mention it "
        "and never invent a number.\n"
        "- Address 'you'. End with one small, concrete suggestion that follows from the data.\n"
        "- Do NOT use any tools and do NOT search the web. Just write the reading.\n\n"
        f"DATA (JSON):\n{json.dumps(data, indent=2, default=str)}\n\n"
        f"Write the finished reading — only the reading text — to this exact file path:\n  {out_path}\n"
    )


def _generate_text(brief: dict, morning: dict, voice: str) -> str:
    if not AGENT_GENERATE.exists():
        raise RuntimeError(f"agent-generate not found at {AGENT_GENERATE}")
    with tempfile.TemporaryDirectory() as d:
        prompt_file = Path(d) / "prompt.txt"
        out_file = Path(d) / "reading.txt"
        prompt_file.write_text(_prompt(brief, morning, voice, str(out_file)))
        env = {
            **os.environ,
            "AGENT_MIN_BYTES": "80",
            "AGENT_CLAUDE_TIMEOUT": cfg("ORACLE_CLAUDE_TIMEOUT", "240"),
        }
        subprocess.run(
            [str(AGENT_GENERATE), str(prompt_file), str(out_file)],
            env=env, capture_output=True, text=True, timeout=1800,
        )
        if not out_file.exists():
            raise RuntimeError("agent-generate produced no oracle output")
        return out_file.read_text().strip()


def _split(text: str):
    """Derive a short LINE (first sentence) + the full LETTER (the reading)."""
    text = text.strip()
    first = text.split(". ")[0].strip().rstrip(".")
    line = (first[:90].rstrip() + "…") if len(first) > 90 else first
    return (line or "Today asks for less than you think"), text


def generate(day: str, conn, force: bool = False, morning: dict | None = None) -> dict:
    if not force:
        row = conn.execute("SELECT line, letter FROM oracle WHERE day = ?", (day,)).fetchone()
        if row and row[1]:
            return {"line": row[0], "letter": row[1], "cached": True}
    brief = features.daily_brief(conn, day)
    if morning is None:
        morning = _morning(conn, day)
    text = _generate_text(brief, morning, _voice(conn))
    line, letter = _split(text)
    conn.execute(
        "INSERT OR REPLACE INTO oracle(day, line, letter, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (day, line, letter, "agent-generate", dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    conn.commit()
    return {"line": line, "letter": letter, "cached": False}
