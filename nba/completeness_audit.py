#!/usr/bin/env python3
import csv,gzip,json,statistics
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

COMPLETED_EXPECTATIONS={"2025-26":{"regular_games":1230,"teams":30,"games_per_team":82}}

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def present(v):
    return v not in (None,"","None","nan")

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":[],"ok":True}
for season_dir in sorted(DATA.iterdir()) if DATA.exists() else []:
    if not season_dir.is_dir(): continue
    season=season_dir.name.replace("_","-")
    reg=season_dir/"regular_season"
    if not reg.exists(): continue
    games=read(reg/"games.csv.gz")
    teams=read(reg/"team_boxscores.csv.gz")
    players=read(reg/"player_boxscores.csv.gz")
    pbp=read(reg/"playbyplay.csv.gz")

    team_counts=Counter()
    team_names={}
    for g in games:
        for side in ("home","away"):
            tid=g.get(f"{side}_team_id")
            if tid:
                team_counts[tid]+=1
                team_names[tid]=g.get(f"{side}_team") or g.get(f"{side}_tricode")
    pbp_counts=Counter(r.get("game_id") for r in pbp if r.get("game_id"))
    player_counts=Counter(r.get("game_id") for r in players if r.get("game_id"))

    core=["points","field_goals_attempted","three_pointers_attempted","free_throws_attempted",
          "rebounds_total","assists","turnovers"]
    core_cov={}
    for field in core:
        core_cov[field]=(sum(1 for r in teams if present(r.get(field)))/len(teams) if teams else 0)

    s={
      "season":season,"regular_games":len(games),"unique_teams":len(team_counts),
      "team_game_min":min(team_counts.values()) if team_counts else 0,
      "team_game_max":max(team_counts.values()) if team_counts else 0,
      "teams_not_82":[{"team_id":tid,"team":team_names.get(tid),"games":n} for tid,n in sorted(team_counts.items()) if n!=82],
      "team_boxscore_rows":len(teams),"player_rows":len(players),"playbyplay_rows":len(pbp),
      "player_rows_per_game_median":statistics.median(player_counts.values()) if player_counts else 0,
      "playbyplay_rows_per_game_median":statistics.median(pbp_counts.values()) if pbp_counts else 0,
      "core_team_stat_coverage":core_cov
    }
    exp=COMPLETED_EXPECTATIONS.get(season)
    if exp:
        checks={
          "regular_game_count":len(games)==exp["regular_games"],
          "team_count":len(team_counts)==exp["teams"],
          "each_team_game_count":all(n==exp["games_per_team"] for n in team_counts.values()) and len(team_counts)==exp["teams"],
          "team_rows_exactly_two_per_game":len(teams)==2*len(games),
          "player_rows_nonempty":len(players)>0,
          "playbyplay_rows_nonempty":len(pbp)>0
        }
        s["hard_checks"]=checks
        s["ok"]=all(checks.values())
        report["ok"]=report["ok"] and s["ok"]
    else:
        s["hard_checks"]="not enforced for an incomplete/current season"
        s["ok"]=True
    report["seasons"].append(s)

(ROOT/"completeness_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
raise SystemExit(0 if report["ok"] else 1)
