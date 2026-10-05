#!/usr/bin/env python3
import csv,gzip,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"maturity"
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
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr):
    vals=[]
    for r in rr:
        p=pf(r["ml"],r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
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
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        net=d(tr,orr,"_net_rating_est_last5_avg");defadv=d(orr,tr,"_def_rating_est_last5_avg");ts=d(tr,orr,"_true_shooting_est_last5_avg")
        tp=n(tr.get("prior_games_available"));op=n(orr.get("prior_games_available"))
        if None in (med,rest,orest,net,defadv,ts,tp,op):continue
        rows.append({"season":g.get("season"),"game_id":gid,"team_id":tid,"ml":med,"won":hs>as_ if home else as_>hs,
                     "net5_gap":net,"def5_adv":defadv,"ts5_gap":ts,"rest_diff":rest-orest,
                     "team_prior_games":tp,"opp_prior_games":op})

def phase(rr,ss):return met([r for r in rr if r["season"] in ss])
def method_rows(kind,mature):
    rr=[]
    for r in rows:
        if r["net5_gap"]<7.759 or r["rest_diff"]<2 or r["def5_adv"]>5.5:continue
        if kind=="ELITE" and r["ts5_gap"]<0.05603199206897114:continue
        if mature and (r["team_prior_games"]<5 or r["opp_prior_games"]<5):continue
        rr.append(r)
    return rr

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{}}
for kind in ("DEF","ELITE"):
    allr=method_rows(kind,False);maturer=method_rows(kind,True)
    report["methods"][kind]={
      "original":{"overall":met(allr),"discA":phase(allr,A),"discB":phase(allr,B),"validation":phase(allr,V),"holdout":phase(allr,H)},
      "mature_5game":{"overall":met(maturer),"discA":phase(maturer,A),"discB":phase(maturer,B),"validation":phase(maturer,V),"holdout":phase(maturer,H)},
      "signals_removed_for_immaturity":len(allr)-len(maturer)
    }
(OUT/"maturity_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
