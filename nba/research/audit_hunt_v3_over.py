#!/usr/bin/env python3
import csv,gzip,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"hunt_v3";OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
SPECS={
 "NBA_H3_OVER_001":{"forced_tov_max":25.8,"rank_min":9.0,"net_min":12.958},
 "NBA_H3_OVER_002":{"clutch_min":1.7,"rank_min":8.0,"starterpm_min":7.16}
}
def rgz(p):
  if not p.exists(): return []
  with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
  try:return float(v)
  except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def d(a,b,k):
  x=n((a or {}).get(k));y=n((b or {}).get(k));return x-y if x is not None and y is not None else None
def absd(a,b,k):
  z=d(a,b,k);return abs(z) if z is not None else None
def sum2(a,b,k):
  x=n((a or {}).get(k));y=n((b or {}).get(k));return x+y if x is not None and y is not None else None
def pf(a,out):
  a=n(a)
  if a is None or abs(a)<100:return None
  if out==.5:return 0.0
  if out==0:return -1.0
  return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
  if nn<=0:return None
  p=w/nn;den=1+z*z/nn
  return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr,price="median"):
  vals=[(r,pf(r.get(price),r["out"])) for r in rr];vals=[x for x in vals if x[1] is not None]
  dec=[r for r,_ in vals if r["out"]!=.5];w=sum(1 for r in dec if r["out"]==1);pr=sum(x for _,x in vals)
  return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def idx(name):return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
  for g in rgz(p):
    if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
roll=idx("team_rolling.csv.gz");style=idx("team_style_rolling.csv.gz");stand=idx("standings_pregame.csv.gz");shape=idx("scoring_shape_rolling.csv.gz");core=idx("starter_core_pregame.csv.gz")

rows=[]
for gid,g in games.items():
  m=market.get(gid);h=str(g.get("home_team_id") or "");a=str(g.get("away_team_id") or "")
  if not m:continue
  hs=n(g.get("home_score"));aa=n(g.get("away_score"));line=n(m.get("closing_total_median"))
  if None in (hs,aa,line):continue
  hr,ar=roll.get((gid,h),{}),roll.get((gid,a),{});styh,stya=style.get((gid,h),{}),style.get((gid,a),{})
  sh,sa=stand.get((gid,h),{}),stand.get((gid,a),{});ch,ca=shape.get((gid,h),{}),shape.get((gid,a),{});coh,coa=core.get((gid,h),{}),core.get((gid,a),{})
  actual=hs+aa;out=1 if actual>line else (0 if actual<line else .5)
  rows.append({"season":g.get("season"),"game_id":gid,"out":out,
    "median":n(m.get("over_price_median")),"best":n(m.get("over_price_best")),"worst":n(m.get("over_price_worst")),
    "forced_tov_sum":sum2(styh,stya,"forced_turnovers_last5_avg"),"rank_gap":absd(sh,sa,"conference_rank"),"net_gap":absd(hr,ar,"_net_rating_est_last5_avg"),
    "clutch_gap":absd(ch,ca,"clutch_margin_last10_avg"),"starterpm_gap":absd(coh,coa,"starter_plusminus5_avg")})

def qual(mid,spec=None):
  s=spec or SPECS[mid]
  if mid=="NBA_H3_OVER_001":
    return [r for r in rows if None not in (r["forced_tov_sum"],r["rank_gap"],r["net_gap"]) and r["forced_tov_sum"]<=s["forced_tov_max"] and r["rank_gap"]>=s["rank_min"] and r["net_gap"]>=s["net_min"]]
  return [r for r in rows if None not in (r["clutch_gap"],r["rank_gap"],r["starterpm_gap"]) and r["clutch_gap"]>=s["clutch_min"] and r["rank_gap"]>=s["rank_min"] and r["starterpm_gap"]>=s["starterpm_min"]]

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{}}
for mid,spec in SPECS.items():
  rr=qual(mid)
  x={"rule":spec,"overall":met(rr),"price_stress":{p:met(rr,p) for p in ("best","median","worst")},
     "phases":{k:met([r for r in rr if r["season"] in ss]) for k,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))},
     "by_season":{s:met([r for r in rr if r["season"]==s]) for s in SEASONS},
     "leave_one_season_out":{s:met([r for r in rr if r["season"]!=s]) for s in SEASONS}}
  neigh=[]
  if mid=="NBA_H3_OVER_001":
    for ft in (24.5,25.8,27.0):
      for rk in (7,9,11):
        for ng in (10,12.958,15):
          z=qual(mid,{"forced_tov_max":ft,"rank_min":rk,"net_min":ng});neigh.append({"forced_tov_max":ft,"rank_min":rk,"net_min":ng,**met(z),"holdout":met([r for r in z if r["season"] in H])})
  else:
    for cl in (1.2,1.7,2.2):
      for rk in (6,8,10):
        for sp in (5.5,7.16,9):
          z=qual(mid,{"clutch_min":cl,"rank_min":rk,"starterpm_min":sp});neigh.append({"clutch_min":cl,"rank_min":rk,"starterpm_min":sp,**met(z),"holdout":met([r for r in z if r["season"] in H])})
  x["threshold_neighborhood"]=neigh;report["methods"][mid]=x
(OUT/"hunt_v3_over_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
