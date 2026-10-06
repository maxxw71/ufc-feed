#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"interaction_core"
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
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def met(rr):
    vals=[(r,pf(r["ml"],r["won"])) for r in rr]
    vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
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
core={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_core_pregame.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid);hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if not m or hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc,oc=core.get((gid,tid),{}),core.get((gid,oid),{})
        tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
        tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
        tx,ox=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        rest=n(tx.get("days_since_prev_game"));orest=n(ox.get("days_since_prev_game"))
        rows.append({
          "season":g.get("season"),"ml":ml,"won":hs>as_ if home else as_>hs,
          "market_prob":prob,"is_home":1.0 if home else 0.0,
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(ox.get("back_to_back")) else 0.0,
          "side_b2b":1.0 if t(tx.get("back_to_back")) else 0.0,
          "points5_gap":d(tc,oc,"starter_points5_sum"),
          "points5_topshare_adv":d(oc,tc,"starter_points5_top_share"),
          "assists5_gap":d(tc,oc,"starter_assists5_sum"),
          "assists5_topshare_adv":d(oc,tc,"starter_assists5_top_share"),
          "plusminus5_gap":d(tc,oc,"starter_plusminus5_avg"),
          "minutes5_stability_adv":d(oc,tc,"starter_minutes5_std"),
          "points10_gap":d(tc,oc,"starter_points10_sum"),
          "assists10_gap":d(tc,oc,"starter_assists10_sum"),
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),
          "close_overlap_gap":d(tl,ol,"starter_overlap_prev_closing_lineup"),
          "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
          "work_top5_7d_adv":d(ow,tw,"top5_minutes_prev_7d"),
          "work_top8_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
          "work_top8_7d_adv":d(ow,tw,"top8_minutes_prev_7d"),
          "heavy30_adv":d(ow,tw,"players_30plus_prior_game"),
          "heavy36_adv":d(ow,tw,"players_36plus_prior_game"),
          "ot_exposure_adv":d(ow,tw,"players_prev_game_ot_proxy")
        })

disc=[r for r in rows if r["season"] in A]
features=[k for k in rows[0] if k not in {"season","ml","won"}] if rows else []
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
    for qq in (0.20,0.35,0.65,0.80):
        cut=q(vals,qq)
        if cut is None:continue
        conds.append((f,"<=" if qq<0.5 else ">=",float(cut)))
conds += [("is_home","==",0.0),("is_home","==",1.0),("opp_b2b","==",1.0),("side_b2b","==",0.0)]

def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
        if o=="==" and x!=v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])

# Require cross-domain interactions: at least one starter-core feature and one lineup/workload/rest feature.
core_feats={"points5_gap","points5_topshare_adv","assists5_gap","assists5_topshare_adv","plusminus5_gap","minutes5_stability_adv","points10_gap","assists10_gap"}
context_feats=set(features)-core_feats-{"market_prob","is_home"}
rules=[]
for k in (2,3):
    for cs in itertools.combinations(conds,k):
        fs={x[0] for x in cs}
        if len(fs)!=k:continue
        if not (fs & core_feats):continue
        if not (fs & context_feats):continue
        rules.append(cs)

res=[];seen=set()
for cs in rules:
    key=tuple((f,o,round(v,8)) for f,o,v in cs)
    if key in seen:continue
    seen.add(key)
    a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
    if a["n"]<70 or b["n"]<45 or v["n"]<22 or h["n"]<45:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<0.72 or a["roi"]<0.035:continue
    if b["hit"]<0.70 or b["roi"]<=0:continue
    if v["hit"]<0.70 or v["roi"]<=0:continue
    if h["hit"]<0.72 or h["roi"]<0.04:continue
    res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),
                "status":"interaction_shadow_candidate","discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_INTERACT_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":len(seen),"candidates":len(res),"top_methods":res[:50],
        "policy":"Independent interaction lane combining starter-core with lineup/workload/rest/market context. Explicitly excludes ML001 team-efficiency and PHYSCOACH body-size inputs."}
(OUT/"interaction_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
