#!/usr/bin/env python3
import csv,gzip,json,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";SRC=NBA/"officials"/"historical_game_officials.csv.gz";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)

def rgz(p):
    if not p.exists():return []
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
    except:return None
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None

officials=defaultdict(list)
for r in rgz(SRC):
    if r.get("game_id") and r.get("official_name"):officials[r["game_id"]].append(r["official_name"])

games={};team=defaultdict(list)
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in {"2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"}:games[g.get("game_id")]=g
for p in DATA.glob("*_*/regular_season/team_boxscores.csv.gz"):
    for r in rgz(p):team[r.get("game_id")].append(r)

events=[]
for gid,g in games.items():
    when=dt(g.get("game_date"));pair=team.get(gid)
    if not when or not pair or len(pair)!=2 or gid not in officials:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    tf=sum((n(r.get("fouls_personal")) or 0) for r in pair)
    fta=sum((n(r.get("free_throws_attempted")) or 0) for r in pair)
    events.append((when,g,{"total":hs+as_,"fouls":tf,"fta":fta,"home_margin":hs-as_,"home_win":1.0 if hs>as_ else 0.0}))
events.sort(key=lambda z:z[0])

state=defaultdict(lambda:{"n":0,"total":0.0,"fouls":0.0,"fta":0.0,"home_margin":0.0,"home_win":0.0})
rows=[]
for when,g,outcome in events:
    names=officials[g["game_id"]]
    prof=[]
    for name in names:
        s=state[name]
        if s["n"]>0:
            prof.append({"name":name,"n":s["n"],"total":s["total"]/s["n"],"fouls":s["fouls"]/s["n"],"fta":s["fta"]/s["n"],
                         "home_margin":s["home_margin"]/s["n"],"home_win":s["home_win"]/s["n"]})
    row={"season":g.get("season"),"game_id":g.get("game_id"),"game_date":g.get("game_date"),
         "officials":"|".join(names),"crew_size":len(names),"officials_with_prior_history":len(prof),
         "requires_pregame_official_assignment":True,"pregame_only_feature":True}
    if prof:
        row.update({
          "crew_prior_games_min":min(x["n"] for x in prof),"crew_prior_games_mean":statistics.mean(x["n"] for x in prof),
          "crew_total_avg":statistics.mean(x["total"] for x in prof),"crew_fouls_avg":statistics.mean(x["fouls"] for x in prof),
          "crew_fta_avg":statistics.mean(x["fta"] for x in prof),"crew_home_margin_avg":statistics.mean(x["home_margin"] for x in prof),
          "crew_home_win_pct":statistics.mean(x["home_win"] for x in prof)
        })
    rows.append(row)
    for name in names:
        s=state[name];s["n"]+=1
        for k in ("total","fouls","fta","home_margin","home_win"):s[k]+=outcome[k]

wgz(OUT/"official_crew_pregame.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"unique_officials":len(state),
         "games_with_three_officials":sum(1 for r in rows if r["crew_size"]==3),
         "policy":"Crew assignment is target-game information; all referee tendency features use only that official's earlier games. Prospective use requires officials to be known before bet placement."}
(OUT/"official_crew_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
