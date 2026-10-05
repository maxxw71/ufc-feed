#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ml001"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}

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
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def met(rr,price="median"):
    vals=[(r,pf(r.get(price),r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None}
    w=sum(1 for r,_ in vals if r["won"]);p=sum(x for _,x in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":p/len(vals),"profit":p}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        net=d(tr,orr,"_net_rating_est_last5_avg");defadv=d(orr,tr,"_def_rating_est_last5_avg")
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net,defadv,rest,orest,med,hs,as_) or net<7.759 or rest-orest<2:continue
        rows.append({"season":g.get("season"),"game_id":gid,"team_id":tid,"def5_adv":defadv,"median":med,"worst":worst,
                     "won":hs>as_ if home else as_>hs,"home":home,"dog":med>0})

thresholds=[2.0,3.5,4.5,5.0,5.492107833295265,6.0,7.0,8.0]
sens=[]
for cut in thresholds:
    rr=[r for r in rows if r["def5_adv"]<=cut]
    item={"cut":cut,"overall":met(rr),"discA":met([r for r in rr if r["season"] in A]),"discB":met([r for r in rr if r["season"] in B]),
          "validation":met([r for r in rr if r["season"] in V]),"holdout":met([r for r in rr if r["season"] in H]),
          "holdout_worst":met([r for r in rr if r["season"] in H],"worst")}
    sens.append(item)
base=[r for r in rows if r["def5_adv"]<=5.492107833295265]
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"threshold_sensitivity":sens,
        "location":{"home":met([r for r in base if r["home"]]),"away":met([r for r in base if not r["home"]])},
        "market_side":{"favorite":met([r for r in base if not r["dog"]]),"underdog":met([r for r in base if r["dog"]])},
        "by_season":{s:met([r for r in base if r["season"]==s]) for s in SEASONS}}
(OUT/"ml001_defensive_filter_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
