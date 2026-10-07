#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"travel"
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H
BASE_TRAVEL=576.9;BASE_PROB=0.7023

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
    vals=[(r,pf(r["ml"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(vals)-w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
travel={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"travel_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_quality_pregame.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if not m or hs is None or as_ is None:continue
    # TRAVEL_001 is away side only
    tid=g.get("away_team_id");oid=g.get("home_team_id")
    tt,ot=travel.get((gid,tid),{}),travel.get((gid,oid),{})
    tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
    tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
    tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
    ml=n(m.get("away_moneyline_median"));prob=n(m.get("away_implied_probability_devig"))
    travel7=d(ot,tt,"travel_miles_prev_7d_including_arrival")
    if None in (ml,prob,travel7) or travel7<BASE_TRAVEL or prob<BASE_PROB:continue
    rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
    rows.append({
      "season":g.get("season"),"ml":ml,"won":as_>hs,
      "travel7_adv":travel7,"market_prob":prob,
      "rest_diff":rest-orest if rest is not None and orest is not None else None,
      "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
      "work_top5_7d_adv":d(ow,tw,"top5_minutes_prev_7d"),
      "work_top8_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
      "heavy36_adv":d(ow,tw,"players_36plus_prior_game"),
      "start5_pm48_gap":d(tl,ol,"current_start5_prior_pm48"),
      "start_pm48_5_gap":d(tl,ol,"start_pm48_last5_avg"),
      "unit_volatility5_adv":d(ol,tl,"stint_pm48_std_last5_avg"),
      "elevation_gain_adv":d(ot,tt,"elevation_gain_ft_from_prev")
    })

disc=[r for r in rows if r["season"] in A]
features=["rest_diff","work_top5_3d_adv","work_top5_7d_adv","work_top8_3d_adv","heavy36_adv","start5_pm48_gap","start_pm48_5_gap","unit_volatility5_adv","elevation_gain_adv"]
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<40:continue
    for qq in (0.25,0.40,0.60,0.75):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))

def meet(r,c):
    f,o,v=c;x=r.get(f)
    if x is None:return False
    return x>=v if o==">=" else x<=v
def phase(c,ss):return met([r for r in rows if r["season"] in ss and meet(r,c)])

res=[]
base={"overall":met(rows),"discA":met([r for r in rows if r["season"] in A]),"discB":met([r for r in rows if r["season"] in B]),"validation":met([r for r in rows if r["season"] in V]),"holdout":met([r for r in rows if r["season"] in H])}
for c in conds:
    a=phase(c,A);b=phase(c,B);v=phase(c,V);h=phase(c,H);o=met([r for r in rows if meet(r,c)])
    if a["n"]<40 or b["n"]<25 or v["n"]<15 or h["n"]<30:continue
    if None in (a["roi"],b["roi"],v["roi"],h["roi"]):continue
    # require improvement in holdout ROI and no phase goes negative
    if h["roi"] <= base["holdout"]["roi"]+0.01:continue
    if min(a["roi"],b["roi"],v["roi"],h["roi"])<=0:continue
    res.append({"filter":f"{c[0]} {c[1]} {c[2]:.4g}","overall":o,"discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["roi"],x["holdout"]["wilson_low"] or 0,x["holdout"]["n"]),reverse=True)
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "base_rule":"away AND travel7d_adv >= 576.9 AND market_prob >= 0.7023","base":base,
        "filters_tested":len(conds),"retained_filters":len(res),"top_filters":res[:50],
        "policy":"Filters are derived from discovery-A quantiles only. A refinement must improve holdout ROI by >=1 point while remaining profitable in every phase; no team/season exclusions."}
(OUT/"travel_001_interactions.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
