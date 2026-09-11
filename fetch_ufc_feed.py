from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

OUT = Path("upcoming.json")
UA = "ufc-feed/1.1"
EVENTS_URL = "https://www.ufc.com/events?language_content_entity=en"
JINA = "https://r.jina.ai/"

MONTHS = {
    m.lower(): i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"],
        start=1,
    )
}
MON3 = {m[:3].lower(): n for m, n in MONTHS.items()}

DIVISIONS = [
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight", "Light Heavyweight", "Heavyweight",
    "Middleweight", "Welterweight", "Lightweight", "Featherweight",
    "Bantamweight", "Flyweight", "Strawweight", "Catch Weight",
]

ATHLETE_RE = re.compile(
    r"(?<!!)\[([^\]]+)\]\(https://www\.ufc\.com/athlete/[^)]+\)", re.I
)
EVENT_URL_RE = re.compile(
    r"https://www\.ufc\.com/event/[A-Za-z0-9._~-]+", re.I
)
SLUG_DATE_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)-(\d{1,2})-(\d{4})", re.I,
)
DATE_LINE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
    re.I,
)


def get_text(url: str) -> str:
    r = requests.get(
        JINA + url,
        timeout=45,
        headers={"User-Agent": UA, "Accept": "text/plain"},
    )
    r.raise_for_status()
    return r.text


def clean(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s or "")
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[#*_`]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -|:")


def slug_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


def date_from_slug(slug: str):
    m = SLUG_DATE_RE.search(slug)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def nearest_year_date(month_name: str, day: int, today: date):
    mon = MON3.get(month_name[:3].lower()) or MONTHS.get(month_name.lower())
    if not mon:
        return None
    vals = []
    for yr in (today.year - 1, today.year, today.year + 1):
        try:
            vals.append(date(yr, mon, day))
        except ValueError:
            pass
    if not vals:
        return None
    future = [d for d in vals if d >= today]
    return min(future) if future else min(vals, key=lambda d: abs((d - today).days))


def date_from_match(m, today):
    if m.group(3):
        mon = MON3.get(m.group(1)[:3].lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(2)))
            except ValueError:
                return None
    return nearest_year_date(m.group(1), int(m.group(2)), today)


def simple(s: str) -> str:
    s = clean(s).lower().replace("vs.", "vs")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def listing_date_for_matchup(listing: str, matchup: str, today: date):
    target = simple(matchup)
    if not target:
        return None
    lines = listing.splitlines()
    for i, line in enumerate(lines):
        if simple(line) != target:
            continue
        for j in range(max(0, i - 12), min(len(lines), i + 13)):
            m = DATE_LINE_RE.search(lines[j])
            if m:
                d = date_from_match(m, today)
                if d:
                    return d
    return None


def extract_event_urls(listing: str):
    seen, out = set(), []
    for m in EVENT_URL_RE.finditer(listing):
        url = m.group(0).rstrip(".,)")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def heading_and_matchup(page: str, url: str):
    lines = [x.strip() for x in page.splitlines()]
    for i, line in enumerate(lines):
        if re.match(r"^#\s+.*\bUFC\b", line, flags=re.I):
            heading = clean(line)
            for nxt in lines[i + 1:i + 12]:
                t = clean(nxt)
                if t and re.search(r"\bvs\.?\b", t, flags=re.I):
                    matchup = re.sub(r"\s*vs\.?\s*", " vs ", t, flags=re.I)
                    return heading, matchup
            return heading, ""
    return slug_from_url(url).replace("-", " ").title(), ""


def event_name(heading: str, matchup: str):
    if matchup and simple(matchup) not in simple(heading):
        return f"{heading}: {matchup.replace(' vs ', ' vs. ')}"
    return heading


def extract_location(page: str):
    lines = [clean(x) for x in page.splitlines()]
    for i, line in enumerate(lines):
        if DATE_LINE_RE.search(line):
            for nxt in lines[i + 1:i + 8]:
                if not nxt:
                    continue
                low = nxt.lower()
                if low.startswith("sponsored") or low.startswith("how to watch"):
                    continue
                if "watch on" in low or low.startswith("main card") or low.startswith("prelims"):
                    continue
                if "vs." in low or " vs " in low:
                    continue
                if len(nxt) <= 140:
                    return nxt
    return ""


def division_from_line(line: str):
    low = line.lower()
    for d in DIVISIONS:
        if f"{d.lower()} bout" in low or d.lower() in low:
            return d
    if "catchweight" in low or "catch weight" in low:
        return "Catch Weight"
    return "Unknown"


def parse_bouts(page: str):
    bouts, seen = [], set()
    for raw in page.splitlines():
        if " vs " not in raw.lower() and " vs. " not in raw.lower():
            continue
        names = ATHLETE_RE.findall(raw)
        if len(names) < 2:
            continue
        a, b = clean(names[0]), clean(names[1])
        if not a or not b or a == b:
            continue
        key = tuple(sorted((a.casefold(), b.casefold())))
        if key in seen:
            continue
        seen.add(key)
        bouts.append({
            "fighter_a": a,
            "fighter_b": b,
            "division": division_from_line(raw),
            "status": "announced",
        })
    return bouts


def page_date_fallback(page: str, today: date):
    m = DATE_LINE_RE.search(page)
    return date_from_match(m, today) if m else None


def main():
    today = date.today()
    listing = get_text(EVENTS_URL)
    urls = extract_event_urls(listing)
    events = []

    for url in urls[:12]:
        slug = slug_from_url(url)
        try:
            page = get_text(url)
        except Exception as exc:
            print(f"warning: {url}: {exc}")
            continue

        heading, matchup = heading_and_matchup(page, url)
        event_date = date_from_slug(slug)
        if not event_date and matchup:
            event_date = listing_date_for_matchup(listing, matchup, today)
        if not event_date:
            event_date = page_date_fallback(page, today)

        if not event_date or event_date < today:
            continue

        bouts = parse_bouts(page)
        if not bouts:
            continue

        events.append({
            "event_id": slug,
            "name": event_name(heading, matchup),
            "date": event_date.isoformat(),
            "event_url": url,
            "venue_location": extract_location(page),
            "bouts": bouts,
        })

    uniq = {e["event_id"]: e for e in events}
    events = sorted(uniq.values(), key=lambda x: (x["date"], x["event_id"]))[:12]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "UFC.com via Jina Reader relay",
        "events": events,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(events)} events and {sum(len(e['bouts']) for e in events)} bouts")
    for e in events:
        print(e["date"], e["name"], len(e["bouts"]))


if __name__ == "__main__":
    main()
