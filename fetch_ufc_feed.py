from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path("upcoming.json")
API = "https://en.wikipedia.org/w/api.php"
UA = "ufc-feed/1.0 (public UFC schedule relay)"

MONTHS = {
    m: i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"],
        start=1,
    )
}


def api_parse(page: str) -> tuple[str, str]:
    r = requests.get(
        API,
        params={
            "action": "parse",
            "page": page,
            "prop": "text|displaytitle",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        },
        timeout=30,
        headers={"User-Agent": UA},
    )
    r.raise_for_status()
    data = r.json()
    parsed = data.get("parse") or {}
    return parsed.get("text") or "", parsed.get("displaytitle") or page


def clean_text(s: str) -> str:
    s = re.sub(r"\[[^\]]+\]", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def parse_date_text(text: str):
    text = clean_text(text)
    for month, num in MONTHS.items():
        m = re.search(rf"\b{month}\s+(\d{{1,2}}),\s+(\d{{4}})\b", text)
        if m:
            try:
                return date(int(m.group(2)), num, int(m.group(1)))
            except ValueError:
                return None
    return None


def find_scheduled_events(html: str):
    soup = BeautifulSoup(html, "lxml")
    target = None

    # Find the Scheduled events heading, then the next wikitable.
    for h in soup.find_all(["h2", "h3", "h4"]):
        if clean_text(h.get_text(" ", strip=True)).lower() == "scheduled events":
            target = h.find_next("table")
            break

    if target is None:
        # Fallback: find a table whose headers include Event and Date.
        for table in soup.find_all("table"):
            headers = [clean_text(x.get_text(" ", strip=True)).lower() for x in table.find_all("th")]
            if "event" in headers and "date" in headers:
                target = table
                break

    if target is None:
        return []

    events = []
    today = date.today()
    for tr in target.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue

        texts = [clean_text(c.get_text(" ", strip=True)) for c in cells]
        if texts[0].lower() == "event":
            continue

        # Scheduled-event tables are normally Event | Date | Venue | Location.
        event_cell = cells[0]
        title = texts[0]
        if not title or "UFC" not in title:
            continue

        event_date = None
        for t in texts[1:3]:
            event_date = parse_date_text(t)
            if event_date:
                break
        if not event_date or event_date < today:
            continue

        a = event_cell.find("a", href=True)
        wiki_title = None
        if a:
            wiki_title = a.get("title") or a.get_text(" ", strip=True)

        venue = texts[2] if len(texts) >= 3 else ""
        location = texts[3] if len(texts) >= 4 else ""

        events.append({
            "name": title,
            "date": event_date,
            "wiki_title": wiki_title,
            "venue": venue,
            "location": location,
        })

    events.sort(key=lambda e: e["date"])
    return events


def normalize_division(raw: str) -> str:
    s = clean_text(raw)
    s = re.sub(r"\b(UFC|Interim|Title|Championship|bout)\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" :-")
    return s or "Unknown"


def parse_announced_bouts(page_html: str):
    soup = BeautifulSoup(page_html, "lxml")
    bouts = []
    seen = set()

    # Wikipedia upcoming-event pages commonly use an Announced bouts section.
    heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if clean_text(h.get_text(" ", strip=True)).lower() == "announced bouts":
            heading = h
            break

    nodes = []
    if heading is not None:
        cur = heading.find_next_sibling()
        while cur is not None and cur.name not in {"h2", "h3"}:
            nodes.append(cur)
            cur = cur.find_next_sibling()

    # Fallback: scan all list items because some pages omit a dedicated section.
    if not nodes:
        nodes = [soup]

    for node in nodes:
        for li in node.find_all("li"):
            text = clean_text(li.get_text(" ", strip=True))
            # Typical pattern: Heavyweight bout: Fighter A vs. Fighter B
            m = re.match(r"(.+?\bbout)\s*:\s*(.+?)\s+vs\.?\s+(.+)$", text, flags=re.I)
            if not m:
                continue
            division = normalize_division(m.group(1))
            fighter_a = clean_text(m.group(2))
            fighter_b = clean_text(m.group(3))
            if not fighter_a or not fighter_b:
                continue
            key = (fighter_a.casefold(), fighter_b.casefold(), division.casefold())
            if key in seen:
                continue
            seen.add(key)
            bouts.append({
                "fighter_a": fighter_a,
                "fighter_b": fighter_b,
                "division": division,
                "status": "announced",
            })

    # Also handle future pages that already use a results-style table.
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        texts = [clean_text(c.get_text(" ", strip=True)) for c in cells]
        vs_idx = None
        for i, t in enumerate(texts):
            if t.lower() in {"vs.", "vs"}:
                vs_idx = i
                break
        if vs_idx is None or vs_idx < 1 or vs_idx + 1 >= len(cells):
            continue
        a = texts[vs_idx - 1]
        b = texts[vs_idx + 1]
        division = texts[max(0, vs_idx - 2)] if vs_idx >= 2 else "Unknown"
        division = normalize_division(division)
        if not a or not b:
            continue
        key = (a.casefold(), b.casefold(), division.casefold())
        if key in seen:
            continue
        seen.add(key)
        bouts.append({
            "fighter_a": a,
            "fighter_b": b,
            "division": division,
            "status": "scheduled",
        })

    return bouts


def main():
    list_html, _ = api_parse("List of UFC events")
    scheduled = find_scheduled_events(list_html)

    events = []
    for ev in scheduled[:12]:
        bouts = []
        canonical_name = ev["name"]
        if ev.get("wiki_title"):
            try:
                event_html, display_title = api_parse(ev["wiki_title"])
                bouts = parse_announced_bouts(event_html)
                canonical_name = clean_text(
                    BeautifulSoup(display_title, "lxml").get_text(" ", strip=True)
                ) or canonical_name
            except Exception as exc:
                print(f"warning: failed {ev['wiki_title']}: {exc}")

        events.append({
            "event_id": ev.get("wiki_title") or canonical_name,
            "name": canonical_name,
            "date": ev["date"].isoformat(),
            "venue": ev.get("venue", ""),
            "location": ev.get("location", ""),
            "bouts": bouts,
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Wikipedia MediaWiki API",
        "events": events,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(events)} upcoming events and {sum(len(e['bouts']) for e in events)} bouts")


if __name__ == "__main__":
    main()
