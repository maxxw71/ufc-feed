from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import requests

OUT = Path("upcoming.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
UA = "ufc-feed/1.0"


def get_json(url: str, params=None):
    r = requests.get(url, params=params, timeout=30, headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def division_from_comp(comp):
    raw = str((comp.get("type") or {}).get("abbreviation") or "").strip()
    mapping = {
        "W Strawweight": "Women's Strawweight",
        "W Flyweight": "Women's Flyweight",
        "W Bantamweight": "Women's Bantamweight",
        "W Featherweight": "Women's Featherweight",
        "Strawweight": "Strawweight",
        "Flyweight": "Flyweight",
        "Bantamweight": "Bantamweight",
        "Featherweight": "Featherweight",
        "Lightweight": "Lightweight",
        "Welterweight": "Welterweight",
        "Middleweight": "Middleweight",
        "Light Heavyweight": "Light Heavyweight",
        "Heavyweight": "Heavyweight",
    }
    return mapping.get(raw, raw or "Unknown")


def event_location(event):
    comps = event.get("competitions") or []
    venue = (comps[0].get("venue") or {}) if comps else {}
    name = venue.get("fullName") or ""
    addr = venue.get("address") or {}
    parts = [addr.get("city"), addr.get("state"), addr.get("country")]
    return name, ", ".join(str(x) for x in parts if x)


def parse_event(event):
    try:
        dt = datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00"))
    except Exception:
        return None

    venue, location = event_location(event)
    bouts = []

    for comp in event.get("competitions") or []:
        status = ((comp.get("status") or {}).get("type") or {})
        if status.get("completed"):
            continue

        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        competitors = sorted(competitors, key=lambda x: int(x.get("order") or 99))
        a = ((competitors[0].get("athlete") or {}).get("displayName") or "").strip()
        b = ((competitors[1].get("athlete") or {}).get("displayName") or "").strip()
        if not a or not b:
            continue

        bouts.append({
            "competition_id": str(comp.get("id") or ""),
            "fighter_a": a,
            "fighter_b": b,
            "division": division_from_comp(comp),
            "status": status.get("name") or status.get("description") or "scheduled",
        })

    return {
        "event_id": str(event.get("id") or ""),
        "name": str(event.get("name") or event.get("shortName") or "UFC Event"),
        "date": dt.date().isoformat(),
        "start_time_utc": dt.astimezone(timezone.utc).isoformat(),
        "venue": venue,
        "location": location,
        "bouts": bouts,
    }


def main():
    base = get_json(ESPN)
    today = date.today()
    calendar = ((base.get("leagues") or [{}])[0].get("calendar") or [])

    candidates = []
    for item in calendar:
        label = str(item.get("label") or "")
        if "Contender Series" in label:
            continue
        try:
            start = datetime.fromisoformat(str(item.get("startDate")).replace("Z", "+00:00"))
        except Exception:
            continue
        if start.date() < today:
            continue
        ref = (((item.get("event") or {}).get("$ref")) or "")
        event_id = ref.split("/events/")[-1].split("?")[0] if "/events/" in ref else ""
        candidates.append((start, event_id))

    candidates.sort(key=lambda x: x[0])
    events = []
    seen = set()

    for start, wanted_id in candidates[:10]:
        day = start.strftime("%Y%m%d")
        data = get_json(ESPN, params={"dates": day})
        for event in data.get("events") or []:
            eid = str(event.get("id") or "")
            if wanted_id and eid != wanted_id:
                continue
            if eid in seen:
                continue
            parsed = parse_event(event)
            if parsed and parsed["date"] >= today.isoformat() and parsed["bouts"]:
                events.append(parsed)
                seen.add(eid)

    events.sort(key=lambda e: e["date"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "ESPN public UFC scoreboard API",
        "events": events,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(events)} upcoming events to {OUT}")


if __name__ == "__main__":
    main()
