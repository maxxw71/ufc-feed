#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"rotation_shape"
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
role={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_bench_rolling.csv.gz")}
shape={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"scoring_shape_rolling.csv.gz")}
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
        tr,orr=role.get((gid,tid),{}),role.get((gid,oid),{})
        ts,os=shape.get((gid,tid),{}),shape.get((gid,oid),{})
        tc,oc=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        rows.append({
          "season":g.get("season"),"ml":ml,"won":hs>as_ if home else as_>hs,
          "market_prob":prob,"is_home":1.0 if home else 0.0,
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "role_bench_pts5_gap":d(tr,orr,"bench_points_last5_avg"),
          "role_bench_share5_gap":d(tr,orr,"bench_points_share_last5_avg"),
          "role_bench_pm5_gap":d(tr,orr,"bench_plus_minus_last5_avg"),
          "role_starter_pm5_gap":d(tr,orr,"starter_plus_minus_last5_avg"),
          "role_starter_share5_gap":d(tr,orr,"starter_points_share_last5_avg"),
          "role_bench_ast5_gap":d(tr,orr,"bench_assists_last5_avg"),
          "role_bench_reb5_gap":d(tr,orr,"bench_rebounds_last5_avg"),
          "role_bench_used5_gap":d(tr,orr,"bench_used_last5_avg"),
          "role_starter_usage_share5_gap":d(tr,orr,"starter_usage_proxy_share_last5_avg"),
          "shape_q1m5_gap":d(ts,os,"q1_margin_last5_avg"),
          "shape_q3m5_gap":d(ts,os,"q3_margin_last5_avg"),
          "shape_q4m5_gap":d(ts,os,"q4_margin_last5_avg"),
          "shape_h2m5_gap":d(ts,os,"second_half_margin_last5_avg"),
          "shape_clutchm5_gap":d(ts,os,"clutch_margin_last5_avg"),
          "shape_clutchtov_adv":d(os,ts,"clutch_turnovers_last5_avg"),
          "shape_q3m10_gap":d(ts,os,"q3_margin_last10_avg"),
          "shape_q4m10_gap":d(ts,os,"q4_margin_last10_avg"),
          "shape_clutchm10_gap":d(ts,os,"clutch_margin_last10_avg")
        })

disc=[r for r in rows if r["season"] in A]
role_feats=[k for k in rows[0] if k.startswith("role_")] if rows else []
shape_feats=[k for k in rows[0] if k.startswith("shape_")] if rows else []
context_feats=["market_prob","rest_diff"]
conds=[]
for f in role_feats+shape_feats+context_feats:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<500:continue
    for qq in (0.20,0.35,0.65,0.80):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<0.5 else ">=",float(cut)))
conds += [("is_home","==",0.0),("is_home","==",1.0),("opp_b2b","==",1.0)]

def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
        if o=="==" and x!=v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])

rules=[]
for k in (2,3):
    for cs in itertools.combinations(conds,k):
        fs={x[0] for x in cs}
        if len(fs)!=k:continue
        # Force a true interaction: at least one role feature and one scoring-shape feature.
        if not (fs & set(role_feats)):continue
        if not (fs & set(shape_feats)):continue
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
                "status":"rotation_shape_shadow_candidate","discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_ROTSHAPE_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":len(seen),"candidates":len(res),"top_methods":res[:50],
        "policy":"Independent role+scoring-shape lane. Uses only prior-game starter/bench structure, quarter/clutch history and basic market/rest context; excludes ML001 efficiency and PHYSCOACH body-size inputs."}
(OUT/"rotation_shape_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
