"""The Oracle-Lab — scheduled longitudinal analysis (Sonnet 4.6, agentic).

Each run: snapshot the DB read-only, run a headless Sonnet agent in lab/ that
writes/runs analysis scripts and keeps a notebook (NOTES.md) to mine for durable,
evidence-backed patterns, then ingest its vetted findings.json into the insights
table (candidate→graduate→archive). Scheduled Mon/Wed/Fri 02:30 via systemd timer.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from . import db, insights
from .config import ROOT

LAB = ROOT / "lab"
SNAPSHOT = LAB / "snapshot.db"
FINDINGS = LAB / "findings.json"
SNAP_FILES = (SNAPSHOT, Path(f"{SNAPSHOT}-wal"), Path(f"{SNAPSHOT}-shm"))
CLAUDE = Path(os.path.expanduser("~/.local/bin/claude"))
VENV_PY = ROOT / ".venv" / "bin" / "python"
MODEL = "claude-sonnet-4-6"
LAB_PATH = "/home/user/.local/bin:/home/user/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/usr/bin:/bin"


def _prompt() -> str:
    return (
        "You are the Oracle-Lab — the analytical engine behind Kairos. Each run, mine the user's "
        "personal data for DURABLE, evidence-backed patterns ('insights') the Oracle and the user can "
        "trust. Be a skeptical scientist: far better to report nothing than a spurious correlation.\n\n"
        f"Your scratch folder (you are here): {LAB}\n"
        f"READ-ONLY data snapshot: {SNAPSHOT}  (never write to it)\n"
        f"Run Python with: {VENV_PY}  (has pandas, numpy, scipy)\n\n"
        "Files here:\n"
        "- NOTES.md — your running notebook. READ it first; UPDATE it at the end.\n"
        "- methods.md — your rigor checklist; follow it.\n"
        "- scripts/ — analysis. Seeds: _data.py (tidy per-day frame), correlations.py, trends.py. "
        f"Run e.g. `{VENV_PY} scripts/correlations.py`; write new scripts as needed.\n\n"
        "This run:\n"
        "1. Read NOTES.md and methods.md.\n"
        "2. Load the data; check how much there is. The ring + check-ins are new — if there's too "
        "little (e.g. < ~14 days with overlapping check-ins AND biometrics), DO NOT force patterns: "
        "note that in NOTES.md, write findings.json as [], and stop.\n"
        "3. If there's enough: explore across streams (sleep, readiness, HRV, resting HR, activity, "
        "temperature deviation, weather, mood, energy, focus, cycle phase, caffeine, alcohol, "
        "exercise, calendar load). CONTROL for cycle phase and season. Use effect size + "
        "significance, not raw correlation. Mind multiple comparisons. Favor patterns that recur.\n"
        "4. Write findings.json — a JSON array (use [] if nothing clears the bar). Each item:\n"
        '   {"id": "stable-slug (reuse the SAME slug for the same pattern across runs)",\n'
        '    "title": "short, human",\n'
        "    \"stat\": \"evidence in a phrase, e.g. '-38 min sleep, 4 of 5 weeks'\",\n"
        '    "detail": "one plain, useful sentence",\n'
        '    "confidence": 0.0-1.0,\n'
        "    \"since\": \"how long it has held, e.g. '6 weeks'\",\n"
        '    "evidence": {"n": int, "effect": num, "p": num, "method": "e.g. partial corr controlling for cycle", "controls": ["cycle","season"]}}\n'
        "5. Update NOTES.md: what you tested, what held/didn't, what to revisit next run.\n\n"
        "Rules: READ-ONLY on the snapshot DB; write only inside this folder; no web or other tools. "
        "The lifecycle (graduating a finding to a shown card, archiving stale ones) is handled "
        "downstream — just report what's genuinely real THIS run."
    )


def _snapshot() -> None:
    """Write a consistent, WAL-safe read-only snapshot of the live DB.

    shutil.copyfile of a WAL-mode DB can miss pages still in the -wal and leaves
    orphan sidecars; the sqlite backup API produces a clean single-file copy.
    """
    _cleanup_snapshot()
    src = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(str(SNAPSHOT))
    try:
        with dst:
            src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")   # no -wal/-shm when the agent reads it
    finally:
        src.close()
        dst.close()


def _cleanup_snapshot() -> None:
    for p in SNAP_FILES:
        try:
            p.unlink()
        except OSError:
            pass


def run() -> dict:
    LAB.mkdir(exist_ok=True)
    (LAB / "scripts").mkdir(exist_ok=True)
    _snapshot()
    if FINDINGS.exists():
        FINDINGS.unlink()
    env = {**os.environ, "PATH": LAB_PATH}
    proc = subprocess.run(
        [str(CLAUDE), "-p", _prompt(), "--model", MODEL,
         "--permission-mode", "bypassPermissions",
         "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep"],
        cwd=str(LAB), env=env, capture_output=True, text=True, timeout=2400,
    )
    findings = []
    if FINDINGS.exists():
        try:
            findings = json.loads(FINDINGS.read_text())
        except Exception:
            findings = []
    if not isinstance(findings, list):
        findings = []
    conn = db.connect()
    try:
        res = insights.record_findings(conn, findings, dt.date.today().isoformat())
        n_active = len(insights.active(conn))
    finally:
        conn.close()
    _cleanup_snapshot()   # don't leave a copy of the health DB lying around
    return {"rc": proc.returncode, "findings": len(findings),
            "reported": res["reported"], "active": n_active}


def main() -> None:
    print("Oracle-Lab run:", run())


if __name__ == "__main__":
    main()
