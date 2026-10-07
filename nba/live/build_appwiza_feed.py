#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIVE=ROOT/"live"
arsenal=json.loads((LIVE/"arsenal.json").read_text())
scanner_path=ROOT/"scanner"/"active_picks.json"
try:
    scanner=json.loads(scanner_path.read_text())
    picks=scanner.get("picks") or []
except Exception:
    scanner={}
    picks=[]

feed={
  "sport":"nba",
  "tab":arsenal["tab"],
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "season":"2026-27",
  "season_type":"regular_season",
  "arsenal":arsenal["methods"],
  "picks":picks,
  "status":{
    "live_feed_ready":True,
    "regular_season_only":True,
    "prospective_tracking":True,
    "message":"NBA arsenal is live. Picks are populated by the active scanner using the latest point-in-time market, schedule, lineup and feature state.",
    "scanner_generated_at_utc":scanner.get("generated_at_utc"),
    "active_pick_count":len(picks)
  }
}
(LIVE/"appwiza_feed.json").write_text(json.dumps(feed,indent=2)+"\n")
print(json.dumps(feed["status"],indent=2))
