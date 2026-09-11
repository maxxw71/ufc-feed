from __future__ import annotations

import json
import re
from pathlib import Path

PATH = Path("upcoming.json")

BAD_EVENT_PATTERNS = (
    "road to ufc",
    "dwcs",
    "dana white's contender series",
    "contender series",
)

BAD_FIGHTER_PATTERNS = (
    "tbd",
    "tba",
    "to be determined",
    "to be announced",
    "unknown",
)


def bad_text(value: str, patterns) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return not text or any(p in text for p in patterns)


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    clean_events = []
    removed_events = 0
    removed_bouts = 0

    for event in data.get("events") or []:
        event_text = " ".join([
            str(event.get("name") or ""),
            str(event.get("event_id") or ""),
        ])
        if bad_text(event_text, BAD_EVENT_PATTERNS):
            removed_events += 1
            continue

        bouts = []
        seen = set()
        for bout in event.get("bouts") or []:
            a = str(bout.get("fighter_a") or "").strip()
            b = str(bout.get("fighter_b") or "").strip()
            if bad_text(a, BAD_FIGHTER_PATTERNS) or bad_text(b, BAD_FIGHTER_PATTERNS):
                removed_bouts += 1
                continue
            if a.casefold() == b.casefold():
                removed_bouts += 1
                continue
            key = tuple(sorted((a.casefold(), b.casefold())))
            if key in seen:
                removed_bouts += 1
                continue
            seen.add(key)
            bouts.append(bout)

        if not bouts:
            removed_events += 1
            continue

        event["bouts"] = bouts
        clean_events.append(event)

    data["events"] = clean_events
    data["filters"] = {
        "road_to_ufc_removed": True,
        "contender_series_removed": True,
        "tbd_tba_unknown_bouts_removed": True,
    }
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"filter complete: {len(clean_events)} events / "
        f"{sum(len(e.get('bouts') or []) for e in clean_events)} bouts; "
        f"removed {removed_events} events and {removed_bouts} bouts"
    )


if __name__ == "__main__":
    main()
