#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=(3,5,10)

def rgz(p):
    if not p.exists(): return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)
def n(v):
    try:return float(v)
    except:return 0.0
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def truth(v):return str(v).lower() in ("true","1","yes")

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g

by_team_game=defaultdict(list)
for p in DATA.glob("*_*/regular_season/player_boxscores.csv.gz"):
    for r in rgz(p):
        if r.get("game_id") and r.get("team_id"):
            by_team_game[(r["game_id"],r["team_id"])].append(r)

summaries=[]
for (gid,tid),arr in by_team_game.items():
    g=games.get(gid)
    if not g:continue
    when=dt(g.get("game_date"))
    if not when:continue
    played=[r for r in arr if str(r.get("played")).lower() not in ("false","0","dnp","none","")]
    starters=[r for r in played if truth(r.get("starter"))]
    bench=[r for r in played if not truth(r.get("starter"))]
    def agg(rr,key):return sum(n(r.get(key)) for r in rr)
    pts=agg(played,"points"); fga=agg(played,"field_goals_attempted"); fta=agg(played,"free_throws_attempted")
    spts=agg(starters,"points"); bpts=agg(bench,"points")
    summaries.append({
      "season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid,
      "starter_count":len(starters),"bench_used":len(bench),
      "starter_minutes":agg(starters,"minutes"),"bench_minutes":agg(bench,"minutes"),
      "starter_points":spts,"bench_points":bpts,
      "starter_assists":agg(starters,"assists"),"bench_assists":agg(bench,"assists"),
      "starter_rebounds":agg(starters,"rebounds_total"),"bench_rebounds":agg(bench,"rebounds_total"),
      "starter_plus_minus":agg(starters,"plus_minus"),"bench_plus_minus":agg(bench,"plus_minus"),
      "starter_fga":agg(starters,"field_goals_attempted"),"bench_fga":agg(bench,"field_goals_attempted"),
      "starter_3pa":agg(starters,"three_pointers_attempted"),"bench_3pa":agg(bench,"three_pointers_attempted"),
      "starter_fta":agg(starters,"free_throws_attempted"),"bench_fta":agg(bench,"free_throws_attempted"),
      "starter_turnovers":agg(starters,"turnovers"),"bench_turnovers":agg(bench,"turnovers"),
      "starter_points_share":spts/pts if pts>0 else None,
      "bench_points_share":bpts/pts if pts>0 else None,
      "starter_usage_proxy_share":(agg(starters,"field_goals_attempted")+0.44*agg(starters,"free_throws_attempted")+agg(starters,"turnovers"))/(fga+0.44*fta+agg(played,"turnovers")) if (fga+0.44*fta+agg(played,"turnovers"))>0 else None
    })

by=defaultdict(list)
for x in summaries:
    when=dt(x["game_date"])
    if when:by[(x["season"],x["team_id"])].append((when,x))

metrics=["bench_used","starter_minutes","bench_minutes","starter_points","bench_points","starter_assists","bench_assists",
         "starter_rebounds","bench_rebounds","starter_plus_minus","bench_plus_minus","starter_fga","bench_fga","starter_3pa",
         "bench_3pa","starter_fta","bench_fta","starter_turnovers","bench_turnovers","starter_points_share","bench_points_share",
         "starter_usage_proxy_share"]
out=[]
for (season,tid),arr in by.items():
    arr.sort(key=lambda z:z[0]);hist=[]
    for when,x in arr:
        row={"season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,"pregame_only_feature":True}
        for w in WINDOWS:
            prior=hist[-w:]
            for m in metrics:
                vals=[z.get(m) for z in prior if z.get(m) is not None]
                row[f"{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
        out.append(row)
        hist.append(x)

wgz(OUT/"starter_bench_rolling.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(by),
         "windows":list(WINDOWS),"metrics":metrics,"point_in_time":True,
         "policy":"Starter/bench aggregates use only completed prior regular-season player boxscores; target-game result is never included."}
(OUT/"starter_bench_rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
