#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
OUT=ROOT/"features"
OUT.mkdir(parents=True,exist_ok=True)

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def num(v):
    try:return float(v)
    except:return 0.0

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None

game_meta={}
for p in DATA.glob("*/*/games.csv.gz"):
    for g in read(p):
        game_meta[g.get("game_id")]={
          "date":g.get("game_date"),"completed":str(g.get("completed")).lower() in ("true","1"),
          "season":g.get("season"),"season_type":g.get("season_type")
        }

players=[]
for p in DATA.glob("*/*/player_boxscores.csv.gz"):
    players.extend(read(p))

by_player=defaultdict(list)
for r in players:
    meta=game_meta.get(r.get("game_id")) or {}
    when=dt(meta.get("date"))
    if r.get("person_id") and when:
        by_player[(meta.get("season"),r["person_id"])].append((when,r,meta))

out=[]
for (season_key,pid),arr in by_player.items():
    arr.sort(key=lambda x:x[0])
    hist=[]
    for when,r,meta in arr:
        prior=[x for x in hist if x[0]<when]
        def window(days):
            cutoff=when-timedelta(days=days)
            return [x for x in prior if x[0]>=cutoff]
        prev=prior[-1] if prior else None
        fga=num(r.get("field_goals_attempted")); fta=num(r.get("free_throws_attempted")); tov=num(r.get("turnovers"))
        feat={
          "season":season_key or meta.get("season") or r.get("season"),"season_type":meta.get("season_type") or r.get("season_type"),
          "game_id":r.get("game_id"),"game_date":meta.get("date"),"person_id":pid,
          "player_name":r.get("player_name"),"team_id":r.get("team_id"),"team_tricode":r.get("team_tricode"),
          "days_since_prev_appearance":(when.date()-prev[0].date()).days if prev else None,
          "usage_proxy_current_game":fga+0.44*fta+tov,
          "pregame_only_feature":True
        }
        for d in (2,3,5,7):
            ww=window(d)
            feat[f"appearances_prev_{d}d"]=len(ww)
            feat[f"minutes_prev_{d}d"]=sum(num(x[1].get("minutes")) for x in ww)
            feat[f"fga_prev_{d}d"]=sum(num(x[1].get("field_goals_attempted")) for x in ww)
            feat[f"fta_prev_{d}d"]=sum(num(x[1].get("free_throws_attempted")) for x in ww)
            feat[f"usage_proxy_prev_{d}d"]=sum(
                num(x[1].get("field_goals_attempted"))+0.44*num(x[1].get("free_throws_attempted"))+num(x[1].get("turnovers"))
                for x in ww
            )
        heavy=0
        for x in reversed(prior):
            if num(x[1].get("minutes"))>=30: heavy+=1
            else: break
        feat["consecutive_prior_30plus_min_games"]=heavy
        feat["prior_game_minutes"]=num(prev[1].get("minutes")) if prev else None
        feat["prior_game_overtime_exposure_proxy"]=bool(prev and num(prev[1].get("minutes"))>42)
        out.append(feat)

        played=str(r.get("played")).lower() not in ("false","0","dnp","none","")
        if meta.get("completed") and played:
            hist.append((when,r,meta))

write(OUT/"player_workload.csv.gz",out)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "rows":len(out),"players":len(set(k[1] for k in by_player)),
 "windows_days":[2,3,5,7],"point_in_time":True
}
(OUT/"player_workload_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
