#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent
F=NBA/"features"; SRC=F/"player_workload.csv.gz"; OUT=F

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return 0.0
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

rows=rgz(SRC)
by=defaultdict(list)
for r in rows:
    if r.get("game_id") and r.get("team_id"):
        by[(r.get("season"),r.get("game_id"),r.get("team_id"))].append(r)

out=[]
for (season,gid,tid),arr in by.items():
    arr=sorted(arr,key=lambda r:(n(r.get("minutes_prev_7d")),n(r.get("prior_game_minutes"))),reverse=True)
    active=[r for r in arr if n(r.get("minutes_prev_7d"))>0 or n(r.get("prior_game_minutes"))>0]
    top5=active[:5];top8=active[:8]
    def sumk(rr,k):return sum(n(r.get(k)) for r in rr)
    feat={
      "season":season,"game_id":gid,"team_id":tid,"pregame_only_feature":True,
      "requires_game_roster_presence":True,
      "rotation_players_with_recent_minutes":len(active),
      "top5_minutes_prev_3d":sumk(top5,"minutes_prev_3d"),
      "top5_minutes_prev_5d":sumk(top5,"minutes_prev_5d"),
      "top5_minutes_prev_7d":sumk(top5,"minutes_prev_7d"),
      "top8_minutes_prev_3d":sumk(top8,"minutes_prev_3d"),
      "top8_minutes_prev_5d":sumk(top8,"minutes_prev_5d"),
      "top8_minutes_prev_7d":sumk(top8,"minutes_prev_7d"),
      "top5_usage_prev_3d":sumk(top5,"usage_proxy_prev_3d"),
      "top5_usage_prev_5d":sumk(top5,"usage_proxy_prev_5d"),
      "top5_usage_prev_7d":sumk(top5,"usage_proxy_prev_7d"),
      "players_30plus_prior_game":sum(1 for r in active if n(r.get("prior_game_minutes"))>=30),
      "players_36plus_prior_game":sum(1 for r in active if n(r.get("prior_game_minutes"))>=36),
      "max_consecutive_prior_30plus":max([n(r.get("consecutive_prior_30plus_min_games")) for r in active] or [0]),
      "players_prev_game_ot_proxy":sum(1 for r in active if str(r.get("prior_game_overtime_exposure_proxy")).lower() in ("true","1")),
    }
    out.append(feat)

wgz(OUT/"team_workload_pregame.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_games":len(by),
         "policy":"Aggregates only prior-game/window player workload. Player inclusion requires presence in the target game's roster/boxscore record, so this lane is tagged as game-roster dependent."}
(OUT/"team_workload_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
