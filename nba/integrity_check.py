#!/usr/bin/env python3
import csv, gzip, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

def rows(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

report={"slices":[],"ok":True}
for summary_path in sorted(DATA.glob("*/*/summary.json")):
    base=summary_path.parent
    summary=json.loads(summary_path.read_text())
    g=rows(base/"games.csv.gz")
    t=rows(base/"team_boxscores.csv.gz")
    p=rows(base/"player_boxscores.csv.gz")
    pb=rows(base/"playbyplay.csv.gz")
    gids=[x.get("game_id") for x in g]
    dup_games=len(gids)-len(set(gids))
    team_counts={}
    for x in t: team_counts[x.get("game_id")]=team_counts.get(x.get("game_id"),0)+1
    bad_team_games=sum(1 for gid in set(gids) if team_counts.get(gid)!=2)
    player_keys=[(x.get("game_id"),x.get("person_id")) for x in p]
    dup_players=len(player_keys)-len(set(player_keys))
    pb_keys=[(x.get("game_id"),x.get("action_number")) for x in pb]
    dup_pbp=len(pb_keys)-len(set(pb_keys))
    checks={
      "duplicate_games":dup_games,
      "games_without_exactly_two_team_rows":bad_team_games,
      "duplicate_player_game_rows":dup_players,
      "duplicate_playbyplay_action_rows":dup_pbp,
      "game_count_matches_summary":len(g)==summary.get("games_written")
    }
    ok=(dup_games==0 and bad_team_games==0 and dup_players==0 and dup_pbp==0 and checks["game_count_matches_summary"])
    report["slices"].append({"path":str(base.relative_to(ROOT)),"summary":summary,"checks":checks,"ok":ok})
    report["ok"]=report["ok"] and ok

(ROOT/"integrity_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
raise SystemExit(0 if report["ok"] else 1)
