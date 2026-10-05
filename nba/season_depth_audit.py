#!/usr/bin/env python3
import csv,gzip,json,statistics
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

TEAM_FIELDS=[
 "points","field_goals_attempted","three_pointers_attempted","free_throws_attempted",
 "rebounds_total","assists","steals","blocks","turnovers","fouls_personal"
]
PLAYER_FIELDS=[
 "minutes","points","assists","rebounds_total","steals","blocks","turnovers",
 "field_goals_attempted","three_pointers_attempted","free_throws_attempted","plus_minus"
]

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def present(v):
    return v not in (None,"","None","nan")

def cov(rows,fields):
    return {f:(sum(1 for r in rows if present(r.get(f)))/len(rows) if rows else 0.0) for f in fields}

def median_per_game(rows):
    c=Counter(r.get("game_id") for r in rows if r.get("game_id"))
    return statistics.median(c.values()) if c else 0.0

def mean_per_game(rows):
    c=Counter(r.get("game_id") for r in rows if r.get("game_id"))
    return statistics.mean(c.values()) if c else 0.0

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":[]}
for season_dir in sorted(DATA.iterdir()) if DATA.exists() else []:
    reg=season_dir/"regular_season"
    if not reg.exists(): continue
    games=read(reg/"games.csv.gz")
    teams=read(reg/"team_boxscores.csv.gz")
    players=read(reg/"player_boxscores.csv.gz")
    pbp=read(reg/"playbyplay.csv.gz")
    shot_rows=[r for r in pbp if str(r.get("is_field_goal")).lower() in ("true","1")]
    coords=[r for r in shot_rows if present(r.get("x")) and present(r.get("y"))]
    item={
      "season":season_dir.name.replace("_","-"),
      "games":len(games),"team_rows":len(teams),"player_rows":len(players),"playbyplay_rows":len(pbp),
      "player_rows_per_game_median":median_per_game(players),
      "player_rows_per_game_mean":mean_per_game(players),
      "playbyplay_rows_per_game_median":median_per_game(pbp),
      "playbyplay_rows_per_game_mean":mean_per_game(pbp),
      "team_core_coverage":cov(teams,TEAM_FIELDS),
      "player_core_coverage":cov(players,PLAYER_FIELDS),
      "shot_rows":len(shot_rows),
      "shot_coordinate_coverage":len(coords)/len(shot_rows) if shot_rows else 0.0
    }
    report["seasons"].append(item)

baseline=next((x for x in report["seasons"] if x["season"]=="2025-26"),None)
if baseline:
    for x in report["seasons"]:
        ratios=[]
        if baseline["player_rows_per_game_median"]:
            ratios.append(min(1.0,x["player_rows_per_game_median"]/baseline["player_rows_per_game_median"]))
        if baseline["playbyplay_rows_per_game_median"]:
            ratios.append(min(1.0,x["playbyplay_rows_per_game_median"]/baseline["playbyplay_rows_per_game_median"]))
        ratios.append(statistics.mean(x["team_core_coverage"].values()) if x["team_core_coverage"] else 0)
        ratios.append(statistics.mean(x["player_core_coverage"].values()) if x["player_core_coverage"] else 0)
        x["modern_depth_score"]=sum(ratios)/len(ratios) if ratios else 0
        s=x["modern_depth_score"]
        x["depth_grade"]="A" if s>=0.95 else "B" if s>=0.85 else "C" if s>=0.70 else "D"

(ROOT/"season_depth_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
