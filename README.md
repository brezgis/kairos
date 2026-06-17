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

## Privacy & terms

This is a personal, single-user project. See [PRIVACY.md](PRIVACY.md) and
[TERMS.md](TERMS.md).
