#!/usr/bin/env python3
import argparse,csv,gzip,json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

EXPECTED={
 "2015-16":{"games":1230,"teams":30,"uniform_team_games":82},
 "2016-17":{"games":1230,"teams":30,"uniform_team_games":82},
 "2017-18":{"games":1230,"teams":30,"uniform_team_games":82},
 "2018-19":{"games":1230,"teams":30,"uniform_team_games":82},
 "2019-20":{"games":1059,"teams":30,"uniform_team_games":None},
 "2020-21":{"games":1080,"teams":30,"uniform_team_games":72},
 "2021-22":{"games":1230,"teams":30,"uniform_team_games":82},
 "2022-23":{"games":1230,"teams":30,"uniform_team_games":82},
 "2023-24":{"games":1230,"teams":30,"uniform_team_games":82},
 "2024-25":{"games":1230,"teams":30,"uniform_team_games":82},
 "2025-26":{"games":1230,"teams":30,"uniform_team_games":82},
}

CORE_TEAM_FIELDS=[
 "points","field_goals_attempted","three_pointers_attempted","free_throws_attempted",
 "rebounds_total","assists","turnovers"
]

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def present(v):
    return v not in (None,"","None","nan")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--season",required=True,choices=sorted(EXPECTED))
    args=ap.parse_args()
    season=args.season
    exp=EXPECTED[season]
    base=DATA/season.replace("-","_")/"regular_season"
    games=read(base/"games.csv.gz")
    teams=read(base/"team_boxscores.csv.gz")
    players=read(base/"player_boxscores.csv.gz")
    pbp=read(base/"playbyplay.csv.gz")

    counts=Counter()
    for g in games:
        for side in ("home","away"):
            tid=g.get(f"{side}_team_id")
            if tid: counts[tid]+=1

    core_cov={
      f:(sum(1 for r in teams if present(r.get(f)))/len(teams) if teams else 0.0)
      for f in CORE_TEAM_FIELDS
    }
    gkeys=[r.get("game_id") for r in games]
    tkeys=[(r.get("game_id"),r.get("team_id")) for r in teams]
    pkeys=[(r.get("game_id"),r.get("person_id")) for r in players]
    bkeys=[(r.get("game_id"),r.get("action_number")) for r in pbp]

    checks={
      "regular_games_exact":len(games)==exp["games"],
      "unique_teams_exact":len(counts)==exp["teams"],
      "team_rows_exact":len(teams)==2*len(games),
      "player_rows_nonempty":len(players)>0,
      "playbyplay_nonempty":len(pbp)>0,
      "duplicate_games_zero":len(gkeys)==len(set(gkeys)),
      "duplicate_team_game_rows_zero":len(tkeys)==len(set(tkeys)),
      "duplicate_player_game_rows_zero":len(pkeys)==len(set(pkeys)),
      "duplicate_pbp_rows_zero":len(bkeys)==len(set(bkeys)),
      "core_team_stats_95pct_plus":all(v>=0.95 for v in core_cov.values()),
    }
    if exp["uniform_team_games"] is not None:
        checks["each_team_game_count_exact"]=(
          len(counts)==exp["teams"] and all(v==exp["uniform_team_games"] for v in counts.values())
        )

    report={
      "season":season,"games":len(games),"teams":len(counts),
      "team_game_min":min(counts.values()) if counts else 0,
      "team_game_max":max(counts.values()) if counts else 0,
      "team_rows":len(teams),"player_rows":len(players),"playbyplay_rows":len(pbp),
      "core_team_stat_coverage":core_cov,"checks":checks,"ok":all(checks.values())
    }
    out=base/"validation.json"
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["ok"] else 1)

if __name__=="__main__":
    main()
