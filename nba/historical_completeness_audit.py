#!/usr/bin/env python3
import csv,gzip,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
EXPECTED={
 "2023_24":{"games":1230,"teams":30,"team_games":82},
 "2024_25":{"games":1230,"teams":30,"team_games":82},
 "2025_26":{"games":1230,"teams":30,"team_games":82}
}

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":[],"ok":True}
for season,exp in EXPECTED.items():
    base=DATA/season/"regular_season"
    if not base.exists():
        report["seasons"].append({"season":season.replace("_","-"),"present":False,"ok":False})
        report["ok"]=False
        continue
    games=read(base/"games.csv.gz"); teams=read(base/"team_boxscores.csv.gz")
    players=read(base/"player_boxscores.csv.gz"); pbp=read(base/"playbyplay.csv.gz")
    counts=Counter()
    for g in games:
        for side in ("home","away"):
            tid=g.get(f"{side}_team_id")
            if tid: counts[tid]+=1
    gkeys=[g.get("game_id") for g in games]
    tkeys=[(r.get("game_id"),r.get("team_id")) for r in teams]
    pkeys=[(r.get("game_id"),r.get("person_id")) for r in players]
    bkeys=[(r.get("game_id"),r.get("action_number")) for r in pbp]
    checks={
      "regular_games_exact":len(games)==exp["games"],
      "unique_teams_exact":len(counts)==exp["teams"],
      "each_team_games_exact":len(counts)==exp["teams"] and all(v==exp["team_games"] for v in counts.values()),
      "team_rows_exact":len(teams)==2*len(games),
      "player_rows_nonempty":len(players)>0,
      "playbyplay_nonempty":len(pbp)>0,
      "duplicate_games":len(gkeys)==len(set(gkeys)),
      "duplicate_team_game_rows":len(tkeys)==len(set(tkeys)),
      "duplicate_player_game_rows":len(pkeys)==len(set(pkeys)),
      "duplicate_pbp_rows":len(bkeys)==len(set(bkeys))
    }
    item={
      "season":season.replace("_","-"),"present":True,"games":len(games),"teams":len(counts),
      "team_game_min":min(counts.values()) if counts else 0,"team_game_max":max(counts.values()) if counts else 0,
      "team_rows":len(teams),"player_rows":len(players),"playbyplay_rows":len(pbp),
      "checks":checks,"ok":all(checks.values())
    }
    report["seasons"].append(item); report["ok"]=report["ok"] and item["ok"]

(ROOT/"historical_completeness_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
raise SystemExit(0)
