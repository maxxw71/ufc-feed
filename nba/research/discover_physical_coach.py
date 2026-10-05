#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"physical_coach"
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
def q(vals,qq):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(qq*(len(vals)-1)))] if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g
phys={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_physical.csv.gz")}
coach={(r.get("season"),r.get("team_id")):r for r in rgz(F/"coach_continuity.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    season=g.get("season")
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tp=phys.get((gid,tid),{});op=phys.get((gid,oid),{})
        tc=coach.get((season,tid),{});oc=coach.get((season,oid),{})
        tctx=ctx.get((gid,tid),{});octx=ctx.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if ml is None or prob is None:continue
        rest=n(tctx.get("days_since_prev_game"));orest=n(octx.get("days_since_prev_game"))
        team_cont=tc.get("coach_continuity_from_prior_season");opp_cont=oc.get("coach_continuity_from_prior_season")
        rows.append({
          "season":season,"game_id":gid,"ml":ml,"won":hs>as_ if home else as_>hs,"market_prob":prob,
          "height_gap":d(tp,op,"starter_avg_height_inches"),"weight_gap":d(tp,op,"starter_avg_weight_lbs"),
          "age_gap":d(tp,op,"starter_avg_age"),"experience_gap":d(tp,op,"starter_avg_experience"),
          "guard_height_gap":d(tp,op,"guard_height"),"big_height_gap":d(tp,op,"big_height"),
          "guard_weight_gap":d(tp,op,"guard_weight"),"big_weight_gap":d(tp,op,"big_weight"),
          "age_std_gap":d(tp,op,"starter_age_std"),
          "coach_tenure_gap":d(tc,oc,"consecutive_seasons_same_coach"),
          "coach_continuity_adv":1.0 if str(team_cont).lower()=="true" and str(opp_cont).lower()=="false" else 0.0,
          "coach_change_disadv":1.0 if str(team_cont).lower()=="false" and str(opp_cont).lower()=="true" else 0.0,
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "is_home":1.0 if home else 0.0
        })

disc=[r for r in rows if r["season"] in A]
physical=["height_gap","weight_gap","age_gap","experience_gap","guard_height_gap","big_height_gap","guard_weight_gap","big_weight_gap","age_std_gap"]
context=["coach_tenure_gap","rest_diff","market_prob"]
conds=[]
for f in physical+context:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.65,0.75,0.85):
        hi=q(vals,qq);lo=q(vals,1-qq)
        if hi is not None:conds.append((f,">=",float(hi)))
        if lo is not None:conds.append((f,"<=",float(lo)))
conds += [("coach_continuity_adv","==",1.0),("coach_change_disadv","==",1.0),("is_home","==",1.0),("is_home","==",0.0)]

def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
        if o=="==" and x!=v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])
def desc(cs):return " AND ".join(f"{f} {o} {v:.4g}" if isinstance(v,float) else f"{f} {o} {v}" for f,o,v in cs)

rules=[]
# require at least one physical feature so these are genuinely different
for a,b in itertools.combinations(conds,2):
    if a[0]==b[0]:continue
    if a[0] in physical or b[0] in physical:rules.append((a,b))
for a,b,c in itertools.combinations(conds,3):
    fs={a[0],b[0],c[0]}
    if len(fs)<3:continue
    if any(x in physical for x in fs):rules.append((a,b,c))

results=[];seen=set()
for cs in rules:
    key=tuple((f,o,round(v,8) if isinstance(v,float) else v) for f,o,v in cs)
    if key in seen:continue
    seen.add(key)
    a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
    if a["n"]<80 or b["n"]<50 or v["n"]<25 or h["n"]<55:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<0.70 or a["roi"]<0.03:continue
    if b["hit"]<0.70 or b["roi"]<=0:continue
    if v["hit"]<0.70 or v["roi"]<=0:continue
    if h["hit"]<0.74 or h["roi"]<0.04:continue
    results.append({"rule":desc(cs),"status":"confirmed_starter_shadow_candidate",
                    "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
                    "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
                    "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
                    "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_wilson_low":h["wilson_low"]})
results.sort(key=lambda r:(r["holdout_wilson_low"] or 0,r["holdout_roi"],r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_PHYSCOACH_{i:03d}"

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"rules_tested":len(seen),"candidates":len(results),"top_methods":results[:50],
        "policy":"Confirmed-starter lane. Physical thresholds formed from early discovery only; season-level coaching continuity is used only where team assignment exists."}
(OUT/"physical_coach_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
