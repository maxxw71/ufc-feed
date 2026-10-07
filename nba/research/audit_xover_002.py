#!/usr/bin/env python3
import csv,gzip,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"cross_family"
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
OREB=0.4672;RANK=8.0;TS=1.113

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def pf(a,out):
    a=n(a)
    if a is None or abs(a)<100:return None
    if out==0.5:return 0.0
    if out==0:return -1.0
    return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr,pk="median"):
    vals=[(r,pf(r.get(pk),r.get("result"))) for r in rr];vals=[x for x in vals if x[1] is not None]
    dec=[r for r,_ in vals if r.get("result")!=0.5];w=sum(1 for r in dec if r.get("result")==1);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def sum2(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x+y if x is not None and y is not None else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
style={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_style_rolling.csv.gz")}
stand={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"standings_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

univ=[]
for gid,g in games.items():
    m=market.get(gid);h=g.get("home_team_id");a=g.get("away_team_id")
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"));line=n(m.get("closing_total_median"))
    if None in (hs,aas,line):continue
    hr,ar=roll.get((gid,h),{}),roll.get((gid,a),{});styh,stya=style.get((gid,h),{}),style.get((gid,a),{})
    sh,sa=stand.get((gid,h),{}),stand.get((gid,a),{})
    oreb=sum2(styh,stya,"oreb_rate_last5_avg");tss=sum2(hr,ar,"_true_shooting_est_last5_avg");rank=abs(d(sh,sa,"conference_rank")) if d(sh,sa,"conference_rank") is not None else None
    actual=hs+aas;res=1 if actual>line else (0 if actual<line else 0.5)
    univ.append({"season":g.get("season"),"game_id":gid,"oreb_sum":oreb,"stand_rank_abs_gap":rank,"ts_sum":tss,"total_line":line,"result":res,
                 "median":n(m.get("over_price_median")),"best":n(m.get("over_price_best")),"worst":n(m.get("over_price_worst"))})

def qual(oreb=OREB,rank=RANK,ts=TS):
    return [r for r in univ if None not in (r["oreb_sum"],r["stand_rank_abs_gap"],r["ts_sum"]) and r["oreb_sum"]>=oreb and r["stand_rank_abs_gap"]>=rank and r["ts_sum"]<=ts]
rows=qual()
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"method":{"id":"NBA_XOVER_002","status":"shadow_only","rule":f"oreb_sum >= {OREB} AND stand_rank_abs_gap >= {RANK} AND ts_sum <= {TS}"},
        "overall":met(rows),"phases":{"discA":met([r for r in rows if r["season"] in A]),"discB":met([r for r in rows if r["season"] in B]),"validation":met([r for r in rows if r["season"] in V]),"holdout":met([r for r in rows if r["season"] in H])},
        "price_stress":{p:met(rows,p) for p in ("best","median","worst")},
        "by_season":{s:met([r for r in rows if r["season"]==s]) for s in SEASONS},
        "leave_one_season_out":{s:met([r for r in rows if r["season"]!=s]) for s in SEASONS}}
neigh=[]
for o in (0.44,0.455,0.4672,0.48,0.495):
  for ra in (6,8,10):
    for ts in (1.10,1.113,1.125):
      rr=qual(o,ra,ts);neigh.append({"oreb_min":o,"rank_gap_min":ra,"ts_sum_max":ts,**met(rr)})
report["threshold_neighborhood"]=neigh
(OUT/"xover_002_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
