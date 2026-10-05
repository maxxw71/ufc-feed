#!/usr/bin/env python3
import bisect,csv,gzip,itertools,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"value"
OUT.mkdir(parents=True,exist_ok=True)

A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
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
def met(rr,price="median"):
    vals=[]
    for r in rr:
        p=pf(r.get(price),r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None,"profit":0,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
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
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{});tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        if None in (med,prob):continue
        rows.append({
          "season":g.get("season"),"game_id":gid,"median":med,"worst":worst,"market_prob":prob,"won":hs>as_ if home else as_>hs,
          "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),
          "net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
          "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "efg5_gap":d(tr,orr,"_e_fg_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0
        })

FEATURES=["net5_gap","net10_gap","ts5_gap","efg5_gap","rest_diff","starter_churn_adv","lineup_stability_gap"]
dist={}
for f in FEATURES:
    vals=sorted(r[f] for r in rows if r["season"] in A and r.get(f) is not None)
    dist[f]=vals

def pct(f,x):
    vals=dist[f]
    if x is None or not vals:return None
    return bisect.bisect_right(vals,x)/len(vals)

for r in rows:
    ps=[pct(f,r.get(f)) for f in FEATURES]
    if any(x is None for x in ps):
        r["strength_score"]=None;r["value_gap"]=None
    else:
        # equal-weight empirical-percentile composite learned only from DISC_A distributions
        r["strength_score"]=sum(ps)/len(ps)
        r["value_gap"]=r["strength_score"]-r["market_prob"]

def phase(fn,ss,price="median"):return met([r for r in rows if r["season"] in ss and fn(r)],price)

rules=[]
for score_min in (0.62,0.68,0.72,0.76,0.80):
    for gap_min in (0.00,0.05,0.10,0.15,0.20):
        for prob_lo,prob_hi in ((0.45,0.75),(0.50,0.75),(0.55,0.80),(0.60,0.85),(0.45,0.85)):
            def fn(r,sm=score_min,gm=gap_min,lo=prob_lo,hi=prob_hi):
                return r.get("strength_score") is not None and r.get("value_gap") is not None and r["strength_score"]>=sm and r["value_gap"]>=gm and lo<=r["market_prob"]<=hi
            rules.append((score_min,gap_min,prob_lo,prob_hi,fn))

results=[]
for sm,gm,lo,hi,fn in rules:
    a=phase(fn,A);b=phase(fn,B);v=phase(fn,V);h=phase(fn,H);hw=phase(fn,H,"worst")
    if a["n"]<70 or b["n"]<45 or v["n"]<20 or h["n"]<45:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<0.70 or a["roi"]<0.04:continue
    if b["hit"]<0.70 or b["roi"]<=0:continue
    if v["hit"]<0.70 or v["roi"]<=0:continue
    if h["hit"]<0.74 or h["roi"]<0.04:continue
    results.append({
      "strength_score_min":sm,"value_gap_min":gm,"market_prob_min":lo,"market_prob_max":hi,
      "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
      "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
      "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_worst_roi":hw["roi"],"holdout_wilson_low":h["wilson_low"]
    })
results.sort(key=lambda r:(r["holdout_wilson_low"] or 0,r["holdout_roi"],r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_VALUE_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":len(rules),"retained":len(results),"top_methods":results[:50],
        "policy":"Strength-score percentiles are calibrated exclusively on 2018-21. Later phases are forward evaluation only."}
(OUT/"value_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
