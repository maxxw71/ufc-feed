#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIVE=ROOT/"live"
arsenal=json.loads((LIVE/"arsenal.json").read_text())

feed={
  "sport":"nba",
  "tab":arsenal["tab"],
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "season":"2026-27",
  "season_type":"regular_season",
  "arsenal":arsenal["methods"],
  "picks":[],
  "status":{
    "live_feed_ready":True,
    "regular_season_only":True,
    "prospective_tracking":True,
    "message":"NBA arsenal is live. Picks publish only when a regular-season game has the required pregame market/features and, where required, confirmed starters."
  }
}
(LIVE/"appwiza_feed.json").write_text(json.dumps(feed,indent=2)+"\n")
print(json.dumps(feed["status"],indent=2))
