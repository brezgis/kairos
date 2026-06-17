"""Calendar client — Google secret iCal (.ics) feeds + iCloud via CalDAV.

- Google: KAIROS_CALENDARS=label|url;...  (secret "iCal format" URLs)
- iCloud: ICLOUD_USERNAME + ICLOUD_APP_PASSWORD (an Apple app-specific password)
  → all of the account's calendars, auto-discovered via CalDAV.

Every event is tagged with its source-calendar label, and recurring events are
expanded over the requested window.
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


def _ical_urls() -> list:
    out = []
    for part in cfg("KAIROS_CALENDARS").split(";"):
        part = part.strip()
        if part and "|" in part:
            label, url = part.split("|", 1)
            out.append((label.strip(), url.strip()))
    return out


def _event_from_component(comp, label: str):
    dtstart = comp.get("DTSTART")
    if dtstart is None:
        return None
    s = dtstart.dt
    e = comp.get("DTEND").dt if comp.get("DTEND") else None
    all_day = not isinstance(s, dt.datetime)
    day = (s.date() if isinstance(s, dt.datetime) else s).isoformat()
    dur_min = None
    if isinstance(s, dt.datetime) and isinstance(e, dt.datetime):
        dur_min = round((e - s).total_seconds() / 60)
    return {
        "calendar": label,
        "uid": str(comp.get("UID") or ""),
        "summary": str(comp.get("SUMMARY") or ""),
        "location": str(comp.get("LOCATION") or ""),
        "day": day,
        "start": s.isoformat(),
        "end": e.isoformat() if e else None,
        "all_day": all_day,
        "duration_min": dur_min,
    }


def _fetch_ical(start: dt.date, end: dt.date) -> list:
    events = []
    for label, url in _ical_urls():
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
        except urllib.error.URLError as ex:
            raise CalendarError(f"fetch '{label}' failed: {ex}")
        cal = icalendar.Calendar.from_ical(raw)
        for ev in recurring_ical_events.of(cal).between(start, end):
            rec = _event_from_component(ev, label)
            if rec:
                events.append(rec)
    return events


def _fetch_icloud(start: dt.date, end: dt.date) -> list:
    user, pw = cfg("ICLOUD_USERNAME"), cfg("ICLOUD_APP_PASSWORD")
    if not (user and pw):
        return []
    import caldav  # lazy import: only needed when iCloud is configured

    s = dt.datetime(start.year, start.month, start.day)
    e = dt.datetime(end.year, end.month, end.day)
    try:
        client = caldav.DAVClient(url="https://caldav.icloud.com", username=user, password=pw)
        calendars = client.principal().calendars()
    except Exception as ex:  # noqa: BLE001
        raise CalendarError(f"iCloud CalDAV connect failed: {ex}")

    events = []
    for cal in calendars:
        try:
            label = "icloud:" + str(cal.name or "calendar")
        except Exception:  # noqa: BLE001
            label = "icloud:calendar"
        try:
            results = cal.search(start=s, end=e, event=True, expand=True)
        except Exception:  # calendar doesn't support event search (e.g. reminders)
            continue
        for item in results:
            try:
                inst = item.icalendar_instance
            except Exception:  # noqa: BLE001
                continue
            for comp in inst.walk("vevent"):
                rec = _event_from_component(comp, label)
                if rec:
                    events.append(rec)
    return events


def fetch_events(start: dt.date, end: dt.date) -> list:
    return _fetch_ical(start, end) + _fetch_icloud(start, end)
