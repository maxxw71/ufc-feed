#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ml001"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
TRAIN=set(SEASONS[:5]);VALID={"2023-24"};HOLDOUT={"2024-25","2025-26"}

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def imp(a):
    a=n(a)
    if a is None or abs(a)<100:return None
    return -a/(-a+100) if a<0 else 100/(a+100)
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,key="median"):
    vals=[(r,pf(r.get(key),r["won"])) for r in rr]
    vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None}
    w=sum(1 for r,_ in vals if r["won"]);p=sum(x for _,x in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":p/len(vals),"profit":p,"wilson_low":wilson(w,len(vals))}
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
        net=d(tr,orr,"_net_rating_est_last5_avg");rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"));worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net,rest,orest,med,hs,as_) or net<7.759 or rest-orest<2:continue
        rows.append({"season":g.get("season"),"game_id":gid,"team_id":tid,"home":home,"median":med,"best":best,"worst":worst,
                     "implied":imp(med),"opp_b2b":t(oc.get("back_to_back")),"won":hs>as_ if home else as_>hs})

variants={
 "ML001_Base":lambda r:True,
 "ML001_Away":lambda r:not r["home"],
 "ML001_OppB2B":lambda r:r["opp_b2b"],
 "ML001_ImpliedLE75":lambda r:r["implied"] is not None and r["implied"]<=0.75,
}
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"variants":{}}
for name,fn in variants.items():
    rr=[r for r in rows if fn(r)]
    item={
      "overall":met(rr),"discovery":met([r for r in rr if r["season"] in TRAIN]),
      "validation":met([r for r in rr if r["season"] in VALID]),"holdout":met([r for r in rr if r["season"] in HOLDOUT]),
      "worst_price":{"overall":met(rr,"worst"),"discovery":met([r for r in rr if r["season"] in TRAIN],"worst"),
                     "validation":met([r for r in rr if r["season"] in VALID],"worst"),"holdout":met([r for r in rr if r["season"] in HOLDOUT],"worst")},
      "by_season":{s:met([r for r in rr if r["season"]==s]) for s in SEASONS}
    }
    teams=defaultdict(int)
    for r in rr:teams[r["team_id"]]+=1
    item["max_team_share"]=max(teams.values())/len(rr) if rr else 0
    report["variants"][name]=item

(OUT/"ml001_descendant_audit.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
