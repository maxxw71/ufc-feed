#!/usr/bin/env python3
import csv,gzip,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"intersections"
OUT.mkdir(parents=True,exist_ok=True)
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1
def met(rr):
    vals=[(r,pf(r["ml"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None}
    w=sum(1 for r,_ in vals if r["won"]);p=sum(x for _,x in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":p/len(vals),"profit":p}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        rows.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,"ml":ml,"won":hs>as_ if home else as_>hs,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg")
        })

def ml001(r):return r["net5_gap"] is not None and r["rest_diff"] is not None and r["net5_gap"]>=7.759 and r["rest_diff"]>=2
def ml001def(r):return ml001(r) and r["def5_adv"] is not None and r["def5_adv"]<=5.492107833295265
def v2001(r):return r["net5_gap"] is not None and r["starter_churn_adv"] is not None and r["net5_gap"]>=9.637235800525477 and r["starter_churn_adv"]>=2
def v2003(r):return r["def5_adv"] is not None and r["ts5_gap"] is not None and r["def5_adv"]>=10.430476353289166 and r["ts5_gap"]>=0.0362475832398097

RULES={
 "ML001_X_V2_001":lambda r:ml001(r) and v2001(r),
 "ML001DEF_X_V2_001":lambda r:ml001def(r) and v2001(r),
 "V2_001_X_V2_003":lambda r:v2001(r) and v2003(r),
 "ML001_X_V2_003":lambda r:ml001(r) and v2003(r),
}
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{}}
for name,fn in RULES.items():
    rr=[r for r in rows if fn(r)]
    report["methods"][name]={
      "overall":met(rr),"discA":met([r for r in rr if r["season"] in A]),"discB":met([r for r in rr if r["season"] in B]),
      "validation":met([r for r in rr if r["season"] in V]),"holdout":met([r for r in rr if r["season"] in H]),
      "by_season":{s:met([r for r in rr if r["season"]==s]) for s in sorted(ALL)}
    }
(OUT/"intersection_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
