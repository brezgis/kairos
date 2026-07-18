# Kairos

Comprehensive personal analytics — empirical, not astrological.

Kairos pulls data from personal sources (Oura Ring, calendar, music, weather,
and more) into one place and runs continuous, longitudinal analysis over it.
The aim is a daily, personalized read on the patterns across your sleep,
activity, schedule, and environment — grounded in your own data.

## Status

Early work in progress.

## Data sources

- Oura Ring — sleep, readiness, activity, heart rate, SpO2, stress, workouts
- Calendar — Google (secret iCal URLs) and iCloud (CalDAV)
- Music listening history — Spotify
- Weather — Open-Meteo
- More to come

## How it works

A backend ingests each source on a schedule (webhooks are planned), normalizes
it into a shared SQLite store, and computes longitudinal metrics and
correlations that drive a personal dashboard — plus a daily "Oracle" reading
that interprets the day's data in plain language.

## Running it

```bash
# 1. One-time Oura auth (writes a refresh token to .env)
python3 scripts/oura_auth.py

# 2. Ingestion (stdlib only — no dependencies)
python3 -m kairos.ingest_oura       # Oura history -> data/kairos.db
python3 -m kairos.ingest_weather    # ~92 days of local weather
python3 -m kairos.ingest_all        # incremental run, for cron (+ Spotify if configured)

# 3. API backend + dashboard (serves web/ at /)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn kairos.api:app --reload   # http://127.0.0.1:8000

# Optional: calendar ingestion (needs the venv — icalendar/caldav)
.venv/bin/python -m kairos.ingest_calendar
```

Data lives in `data/kairos.db` (gitignored). Secrets/config live in `.env`
(gitignored) — copy `.env.example` to start. Oracle readings shell out to an
external `agent-generate` helper (headless Claude Code, or any LLM CLI you wire
in); without it the dashboard still works — readings just won't generate.

## Privacy & terms

This is a personal, single-user project. See [PRIVACY.md](PRIVACY.md) and
[TERMS.md](TERMS.md).
