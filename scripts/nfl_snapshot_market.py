from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import os

import nflreadpy as nfl

CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
OUT = CTX / "market_snapshots"
OUT.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc)
season = int(os.environ.get("NFL_SEASON", now.year))

df = nfl.load_schedules([season]).to_pandas()
keep = [c for c in [
    "game_id","season","game_type","week","gameday","gametime",
    "away_team","home_team","away_moneyline","home_moneyline",
    "spread_line","away_spread_odds","home_spread_odds",
    "total_line","under_odds","over_odds","away_rest","home_rest",
    "away_qb_id","away_qb_name","home_qb_id","home_qb_name",
    "away_coach","home_coach","stadium","roof","surface","temp","wind",
] if c in df.columns]

df = df[keep].copy()
if "game_type" in df.columns:
    df = df[df.game_type.eq("REG")]
if "gameday" in df.columns:
    gd = __import__("pandas").to_datetime(df.gameday, errors="coerce")
    today = now.date()
    df = df[(gd.dt.date >= today - timedelta(days=1)) & (gd.dt.date <= today + timedelta(days=14))]

stamp = now.isoformat()
rows=[]
for rec in df.astype(object).where(df.notna(), None).to_dict("records"):
    rec["snapshot_at_utc"] = stamp
    rows.append(rec)

path = OUT / f"{now.date().isoformat()}.jsonl"
with path.open("a", encoding="utf-8") as f:
    for row in rows:
        f.write(json.dumps(row, default=str, sort_keys=True) + "\n")

latest = OUT / "latest.json"
latest.write_text(json.dumps({"snapshot_at_utc":stamp,"season":season,"games":rows}, indent=2, default=str))
print(json.dumps({"snapshot_at_utc":stamp,"season":season,"games_saved":len(rows),"path":str(path)}, indent=2))
