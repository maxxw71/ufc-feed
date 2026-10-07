#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"style_totals"
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
PAINT=0.8907;FOUL=38.5;REST=3.0

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def profit(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr,pk="median"):
    vals=[(r,profit(r.get(pk),r.get("result"))) for r in rr];vals=[x for x in vals if x[1] is not None]
    dec=[r for r,_ in vals if r.get("result")!=0.5];w=sum(1 for r in dec if r.get("result")==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def sumv(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x+y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
style={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_style_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

universe=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));line=n(m.get("closing_total_median"))
    if None in (hs,aas,line):continue
    st,sa=style.get((gid,h),{}),style.get((gid,a),{})
    hc,ac=ctx.get((gid,h),{}),ctx.get((gid,a),{})
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    paint=sumv(st,sa,"paint_share_last5_avg");foul=sumv(st,sa,"fouls_per100_last5_avg")
    rest=rh+ra if rh is not None and ra is not None else None
    actual=hs+aas;result=1 if actual>line else (0 if actual<line else 0.5)
    universe.append({"season":g.get("season"),"game_id":gid,"paint":paint,"foul":foul,"rest":rest,"result":result,
                     "median":n(m.get("over_price_median")),"best":n(m.get("over_price_best")),"worst":n(m.get("over_price_worst"))})
def qual(p=PAINT,f=FOUL,r=REST):
    return [x for x in universe if x["paint"] is not None and x["foul"] is not None and x["rest"] is not None and x["paint"]>=p and x["foul"]<=f and x["rest"]<=r]
rows=qual()
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"method_id":"NBA_STYLE_TOT_001","status":"shadow_only",
        "rule":f"paint_share_sum >= {PAINT} AND foulrate_sum <= {FOUL} AND rest_sum <= {REST}",
        "overall":met(rows),"price_stress":{k:met(rows,k) for k in ("best","median","worst")},
        "by_season":{s:met([x for x in rows if x["season"]==s]) for s in SEASONS},
        "phases":{"discA":met([x for x in rows if x["season"] in A]),"discB":met([x for x in rows if x["season"] in B]),"validation":met([x for x in rows if x["season"] in V]),"holdout":met([x for x in rows if x["season"] in H])},
        "leave_one_season_out":{s:met([x for x in rows if x["season"]!=s]) for s in SEASONS}}
neigh=[]
for p in (0.86,0.89,0.92):
  for f in (36.0,38.5,41.0):
    for rr in (2.0,3.0,4.0):
      x=qual(p,f,rr);neigh.append({"paint_min":p,"foul_max":f,"rest_max":rr,**met(x)})
report["threshold_neighborhood"]=neigh
(OUT/"style_totals_001_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
