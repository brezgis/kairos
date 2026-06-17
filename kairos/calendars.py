"""Calendar client — Google/iCloud secret iCal (.ics) feeds.

Fetches each calendar's secret URL and expands events (including recurring ones)
over a date window. Configured via KAIROS_CALENDARS in .env:

    KAIROS_CALENDARS=label|https://.../basic.ics;label2|https://.../basic.ics

Each event is tagged with its source-calendar label so work/personal/school
becomes an analytic dimension. (iCloud calendars will come via CalDAV later.)
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request

import icalendar
import recurring_ical_events

from .config import cfg


class CalendarError(Exception):
    pass


def configured() -> list:
    out = []
    for part in cfg("KAIROS_CALENDARS").split(";"):
        part = part.strip()
        if part and "|" in part:
            label, url = part.split("|", 1)
            out.append((label.strip(), url.strip()))
    return out


def fetch_events(start: dt.date, end: dt.date) -> list:
    events = []
    for label, url in configured():
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
        except urllib.error.URLError as e:
            raise CalendarError(f"fetch '{label}' failed: {e}")
        cal = icalendar.Calendar.from_ical(raw)
        for ev in recurring_ical_events.of(cal).between(start, end):
            s = ev.get("DTSTART").dt
            e = ev.get("DTEND").dt if ev.get("DTEND") else None
            all_day = not isinstance(s, dt.datetime)
            day = (s.date() if isinstance(s, dt.datetime) else s).isoformat()
            dur_min = None
            if isinstance(s, dt.datetime) and isinstance(e, dt.datetime):
                dur_min = round((e - s).total_seconds() / 60)
            events.append({
                "calendar": label,
                "uid": str(ev.get("UID") or ""),
                "summary": str(ev.get("SUMMARY") or ""),
                "location": str(ev.get("LOCATION") or ""),
                "day": day,
                "start": s.isoformat(),
                "end": e.isoformat() if e else None,
                "all_day": all_day,
                "duration_min": dur_min,
            })
    return events
