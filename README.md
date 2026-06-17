# Kairos

Comprehensive personal analytics — empirical, not astrological.

Kairos pulls data from personal sources (Oura Ring, calendar, music, weather,
and more) into one place and runs continuous, longitudinal analysis over it.
The aim is a daily, personalized read on the patterns across your sleep,
activity, schedule, and environment — grounded in your own data.

## Status

Early work in progress.

## Data sources (planned)

- Oura Ring — sleep, readiness, activity, heart rate, SpO2, stress, workouts
- Calendar
- Music listening history
- Weather
- More to come

## How it works (planned)

A backend ingests each source on a schedule (and via webhooks where available),
normalizes it into a shared store, and computes longitudinal metrics and
correlations that drive a personal dashboard.

## Running it

```bash
# 1. One-time Oura auth (writes a refresh token to .env)
python3 scripts/oura_auth.py

# 2. Ingestion (stdlib only — no dependencies)
python3 -m kairos.ingest_oura       # Oura history -> data/kairos.db
python3 -m kairos.ingest_weather    # ~92 days of local weather
python3 -m kairos.ingest_all        # incremental run, for cron

# 3. API backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn kairos.api:app --reload   # http://127.0.0.1:8000
```

Data lives in `data/kairos.db` (gitignored). Secrets/config live in `.env`
(gitignored) — copy `.env.example` to start.

## Privacy & terms

This is a personal, single-user project. See [PRIVACY.md](PRIVACY.md) and
[TERMS.md](TERMS.md).
