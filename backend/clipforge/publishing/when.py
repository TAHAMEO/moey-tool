"""Publish times in plain language.

"tomorrow at 6pm", "Friday 9:30", "tonight", "in 2 hours", "on 25 November at 18:00",
"2026-12-01 08:15", "every day at 6pm starting tomorrow", "one every 3 hours".

Deterministic and offline. Times are read in the owner's time zone and returned
in UTC. :func:`parse` returns ``None`` when the text names no time, so callers
publish immediately (or ask) instead of guessing. Bare hours without am/pm are
read the way people schedule posts: 1–7 → afternoon/evening, 8–12 → morning/noon.
The Brain always repeats the exact time it understood, so a misreading is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
WEEKDAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
          "september": 9, "october": 10, "november": 11, "december": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
          "nov": 11, "dec": 12}
DAY_PARTS = {"morning": time(9, 0), "noon": time(12, 0), "midday": time(12, 0), "afternoon": time(14, 0),
             "evening": time(18, 0), "tonight": time(20, 0), "night": time(20, 0), "midnight": time(0, 0)}
SMALL_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "half an": 0.5}
UNIT_MINUTES = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440, "week": 10080}

_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_NUM = r"(\d{1,3}|half an|an|a|one|two|three|four|five|six|seven|eight|nine|ten|twelve)"
_UNIT = r"(minutes?|mins?|hours?|hrs?|days?|weeks?)"
RELATIVE_RE = re.compile(rf"\bin\s+{_NUM}\s+{_UNIT}\b")
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2}):(\d{2}))?\b")
MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({MONTH_NAMES})\.?(?:\s+(\d{{4}}))?\b")
MONTH_DAY_RE = re.compile(rf"\b({MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
WEEKDAY_RE = re.compile(r"\b(?:(next|this|on|coming)\s+)?(" + "|".join(WEEKDAYS) + r")\b")
WEEKDAY_ABBR_RE = re.compile(r"\b(next|this|on|coming)\s+(" + "|".join(sorted(WEEKDAY_ABBR, key=len, reverse=True)) + r")\b\.?")
# A clock time must be unmistakable: "at 6", "@ 18", "6pm", "6:30", "18:00" — never a bare "3" (as in "3 shorts")
# and never a duration ("at 30 seconds").
_NOT_UNIT = r"(?!\s*(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|%|x)\b)"
CLOCK_RE = re.compile(r"(?:\b(?:at|@)\s*(?P<h1>\d{1,2})(?:[:.](?P<m1>\d{2}))?\s*(?P<ap1>a\.?m\.?|p\.?m\.?)?(?![\w:])" + _NOT_UNIT +
                      r"|\b(?P<h2>\d{1,2})(?:[:.](?P<m2>\d{2}))?\s*(?P<ap2>a\.?m\.?|p\.?m\.?)(?!\w)"
                      r"|\b(?P<h3>\d{1,2})[:](?P<m3>\d{2})\b" + _NOT_UNIT + ")")
ASPECT_RE = re.compile(r"\b(?:9:16|16:9|4:5|1:1)\b")
DAY_PART_RE = re.compile(r"\b(?:in the\s+|this\s+|tomorrow\s+)?(morning|noon|midday|afternoon|evening|tonight|night|midnight)\b")
EVERY_RE = re.compile(rf"\b(?:one\s+)?every\s+(other\s+day|{_NUM}\s+{_UNIT}|day|hour|week)\b|\b(daily|hourly|weekly|everyday)\b"
                      r"|\bone\s+(?:a|per)\s+(day|hour|week)\b|\b(\d{1,3})\s+(hours?|days?)\s+apart\b")


@dataclass
class When:
    at: datetime  # UTC
    every_minutes: int | None = None
    assumed_time: bool = False  # a date without a clock time: 09:00 was assumed
    phrase: str = ""

    def to_dict(self) -> dict:
        return {"at": self.at.isoformat(), "every_minutes": self.every_minutes, "assumed_time": self.assumed_time,
                "phrase": self.phrase}


def _number(raw: str) -> float:
    return float(raw) if raw[0].isdigit() else float(SMALL_NUMBERS[raw])


def _unit_minutes(unit: str) -> int:
    unit = unit.rstrip("s")
    return UNIT_MINUTES.get(unit) or UNIT_MINUTES[{"hr": "hour", "min": "minute"}.get(unit, unit)]


def _clock(match: re.Match) -> time | None:
    hour = match.group("h1") or match.group("h2") or match.group("h3")
    minute = match.group("m1") or match.group("m2") or match.group("m3") or "0"
    ampm = (match.group("ap1") or match.group("ap2") or "").replace(".", "")
    h, m = int(hour), int(minute)
    if m > 59:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ampm == "pm" else 0)
    elif match.group("h1") and not match.group("m1") and 1 <= h <= 7:
        h += 12  # "at 6" → 18:00
    if h > 23:
        return None
    return time(h, m)


def _every(text: str) -> int | None:
    match = EVERY_RE.search(text)
    if not match:
        return None
    phrase = match.group(0)
    if "other day" in phrase:
        return 2880
    if m := re.search(rf"{_NUM}\s+{_UNIT}", phrase):
        return max(1, int(_number(m.group(1)) * _unit_minutes(m.group(2))))
    for word, minutes in (("day", 1440), ("daily", 1440), ("everyday", 1440), ("hour", 60), ("hourly", 60), ("week", 10080), ("weekly", 10080)):
        if re.search(rf"\b{word}\b", phrase):
            return minutes
    return None


def _first_clock(text: str) -> tuple[time, str] | None:
    for match in CLOCK_RE.finditer(text):
        if (value := _clock(match)) is not None:
            return value, match.group(0).strip()
    return None


def parse(text: str, *, now: datetime, zone: ZoneInfo | str) -> When | None:
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    lower = ASPECT_RE.sub(" ", _URL.sub(" ", (text or "").lower()))
    local_now = now.astimezone(zone)
    every = _every(lower)

    if m := RELATIVE_RE.search(lower):
        minutes = _number(m.group(1)) * _unit_minutes(m.group(2))
        at = now + timedelta(minutes=minutes)
        if minutes >= 1440 and (clock := _first_clock(lower)):
            day = (local_now + timedelta(minutes=minutes)).date()
            at = datetime.combine(day, clock[0], zone).astimezone(UTC)
        return When(at=at.astimezone(UTC).replace(second=0, microsecond=0), every_minutes=every, phrase=m.group(0))

    day: date | None = None
    phrases: list[str] = []
    if m := ISO_RE.search(lower):
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        phrases.append(m.group(0))
        if m.group(4):
            t = time(int(m.group(4)), int(m.group(5))) if int(m.group(4)) < 24 and int(m.group(5)) < 60 else None
            if t is None:
                return None
            return When(at=datetime.combine(day, t, zone).astimezone(UTC), every_minutes=every, phrase=m.group(0))
    elif re.search(r"\bday after tomorrow\b", lower):
        day, phrases = local_now.date() + timedelta(days=2), ["day after tomorrow"]
    elif m := re.search(r"\b(tomorrow|tmrw|tmr)\b", lower):
        day, phrases = local_now.date() + timedelta(days=1), [m.group(0)]
    elif m := re.search(r"\b(today|tonight|this (?:morning|afternoon|evening))\b", lower):
        day, phrases = local_now.date(), [m.group(0)]
    elif (m := DAY_MONTH_RE.search(lower)) or (m2 := MONTH_DAY_RE.search(lower)):
        if m:
            d, month, year = int(m.group(1)), MONTHS[m.group(2)], m.group(3)
            phrase = m.group(0)
        else:
            month, d, year = MONTHS[m2.group(1)], int(m2.group(2)), m2.group(3)
            phrase = m2.group(0)
        try:
            day = date(int(year) if year else local_now.year, month, d)
        except ValueError:
            return None
        if not year and day < local_now.date():
            day = day.replace(year=day.year + 1)
        phrases.append(phrase)
    elif (m := WEEKDAY_RE.search(lower)) or (m := WEEKDAY_ABBR_RE.search(lower)):
        target = WEEKDAYS.get(m.group(2), WEEKDAY_ABBR.get(m.group(2)))
        ahead = (target - local_now.weekday()) % 7
        if ahead == 0 and m.group(1) == "next":
            ahead = 7
        day = local_now.date() + timedelta(days=ahead)
        phrases.append(m.group(0))

    clock_time: time | None = None
    if (clock := _first_clock(lower)) is not None:
        clock_time = clock[0]
        phrases.append(clock[1])
    elif (part := DAY_PART_RE.search(lower)) is not None and (
            day is not None or part.group(1) in ("tonight", "noon", "midday", "midnight")
            or part.group(0).startswith("in the ")):
        # Without a date only unambiguous parts count ("tonight", "at noon", "in the evening") —
        # never a greeting such as "good morning".
        clock_time = DAY_PARTS[part.group(1)]
        if part.group(0) not in phrases[-1:]:
            phrases.append(part.group(0))
        if day is None and part.group(1) == "tonight":
            day = local_now.date()

    if day is None and clock_time is None:
        if every:  # "post one every day" with no start: the first one now
            start = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            return When(at=start.astimezone(UTC), every_minutes=every, assumed_time=True, phrase="")
        return None
    assumed = clock_time is None
    if clock_time is None:
        clock_time = time(20, 0) if phrases and "tonight" in phrases[0] else time(9, 0)
    if day is None:
        day = local_now.date()
        if datetime.combine(day, clock_time, zone) <= local_now:
            day += timedelta(days=1)
    elif day == local_now.date() and datetime.combine(day, clock_time, zone) <= local_now and \
            any(p.startswith(tuple(WEEKDAYS) + tuple(WEEKDAY_ABBR) + ("on ", "this ", "coming ")) for p in phrases):
        day += timedelta(days=7)  # "on Friday at 9" said on Friday at 10 → next Friday
    at = datetime.combine(day, clock_time, zone).astimezone(UTC)
    return When(at=at, every_minutes=every, assumed_time=assumed, phrase=_join(phrases))


def _join(phrases: list[str]) -> str:
    kept = [p for p in phrases if not any(p != other and p in other for other in phrases)]
    return " ".join(dict.fromkeys(kept))


def describe(at: datetime, zone: ZoneInfo | str, *, now: datetime | None = None) -> str:
    """'tomorrow (Sat 22 Nov) at 18:00 Europe/London' style wording for replies."""
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    local = at.astimezone(zone)
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    delta = (local.date() - today).days
    label = {0: "today", 1: "tomorrow"}.get(delta)
    day = f"{local.strftime('%a')} {local.day} {local.strftime('%b')}" + ("" if local.year == today.year else f" {local.year}")
    when = f"{label} ({day})" if label else day
    return f"{when} at {local.strftime('%H:%M')} {zone.key}"
