"""Open-Meteo weather client — free, no API key required.

Uses the forecast endpoint with `past_days` (up to 92) so a single call covers
recent history through today. Deeper history can be backfilled later via the
archive API.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_hours",
    "sunrise",
    "sunset",
    "daylight_duration",
    "sunshine_duration",
    "uv_index_max",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
]


class WeatherError(Exception):
    pass


def fetch_daily(lat: float, lon: float, tz: str, past_days: int = 92, forecast_days: int = 1) -> list:
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": tz,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "daily": ",".join(DAILY_VARS),
    }
    url = FORECAST_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise WeatherError(f"Open-Meteo failed (HTTP {e.code}): {e.read().decode(errors='replace')[:200]}")
    daily = payload.get("daily", {})
    times = daily.get("time", [])
    records = []
    for i, day in enumerate(times):
        rec = {"day": day}
        for k, arr in daily.items():
            if k == "time":
                continue
            rec[k] = arr[i] if i < len(arr) else None
        records.append(rec)
    return records
