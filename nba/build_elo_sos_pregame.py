#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)
K=20.0; HOME_ADV=65.0; REGRESS=0.50
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26","2026-27"]

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
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def n(v):
    try:return float(v)
    except:return None
def exp(r1,r2):return 1.0/(1.0+10**((r2-r1)/400.0))

games_by_season=defaultdict(list)
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and dt(g.get("game_date")):
            games_by_season[g["season"]].append(g)

ratings={}
hist_opp=defaultdict(list);hist_res=defaultdict(list);rows=[]
for si,season in enumerate(SEASONS):
    arr=sorted(games_by_season.get(season,[]),key=lambda g:dt(g.get("game_date")))
    teams=sorted({g.get("home_team_id") for g in arr if g.get("home_team_id")}|{g.get("away_team_id") for g in arr if g.get("away_team_id")})
    if si==0:
        for tid in teams:ratings[tid]=1500.0
    else:
        for tid in teams:ratings[tid]=1500.0+REGRESS*(ratings.get(tid,1500.0)-1500.0)
    for g in arr:
        hid=g.get("home_team_id");aid=g.get("away_team_id")
        if not hid or not aid:continue
        rh=ratings.get(hid,1500.0);ra=ratings.get(aid,1500.0)
        hprob=exp(rh+HOME_ADV,ra);aprob=1-hprob
        for home in (True,False):
            tid=hid if home else aid;oid=aid if home else hid
            rt=rh if home else ra;ro=ra if home else rh
            opphist=hist_opp[(season,tid)]
            resh=hist_res[(season,tid)]
            row={"season":season,"game_id":g.get("game_id"),"game_date":g.get("game_date"),"team_id":tid,
                 "elo":rt,"opp_elo":ro,"elo_gap":rt-ro,
                 "elo_win_prob":hprob if home else aprob,
                 "is_home":home,"pregame_only_feature":True}
            for w in (3,5,10,20):
                oo=opphist[-w:]
                row[f"opp_elo_last{w}_avg"]=sum(oo)/len(oo) if oo else None
                rr=resh[-w:]
                row[f"quality_win_last{w}_rate"]=sum(1 for x in rr if x["won"] and x["opp_elo"]>=1525)/len(rr) if rr else None
                row[f"bad_loss_last{w}_rate"]=sum(1 for x in rr if (not x["won"]) and x["opp_elo"]<=1475)/len(rr) if rr else None
            row["season_sos_elo_avg"]=sum(opphist)/len(opphist) if opphist else None
            rows.append(row)
        completed=str(g.get("completed")).lower() in ("true","1")
        hs=n(g.get("home_score"));as_=n(g.get("away_score"))
        if not completed or hs is None or as_ is None:continue
        sh=1.0 if hs>as_ else 0.0
        ratings[hid]=rh+K*(sh-hprob)
        ratings[aid]=ra+K*((1-sh)-aprob)
        hist_opp[(season,hid)].append(ra);hist_opp[(season,aid)].append(rh)
        hist_res[(season,hid)].append({"won":bool(sh),"opp_elo":ra})
        hist_res[(season,aid)].append({"won":not bool(sh),"opp_elo":rh})

wgz(OUT/"elo_sos_pregame.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),
         "k":K,"home_advantage_elo":HOME_ADV,"offseason_regression_to_1500":REGRESS,
         "policy":"Ratings and SOS use only completed games before the target game. No market prices are used to update Elo."}
(OUT/"elo_sos_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
