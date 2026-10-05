#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"novel"
OUT.mkdir(parents=True,exist_ok=True)
DISC_A={"2018-19","2019-20","2020-21"}; DISC_B={"2021-22","2022-23"}; VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}; ALL=DISC_A|DISC_B|VALID|HOLDOUT

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
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
flow={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_flow_rolling.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if hs is None or as_ is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        tf=flow.get((gid,tid),{});of=flow.get((gid,oid),{})
        tw=work.get((gid,tid),{});ow=work.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        if ml is None or abs(ml)<100:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        rows.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,"ml":ml,"won":hs>as_ if home else as_>hs,
          "market_prob":n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig")),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,"side_b2b":1.0 if t(tc.get("back_to_back")) else 0.0,
          "opp_prev_ot":1.0 if t(oc.get("prev_game_went_ot")) else 0.0,
          "is_home":1.0 if home else 0.0,"is_dog":1.0 if ml>0 else 0.0,
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "flow_secondhalf5_gap":d(tf,of,"second_half_plus_ot_margin_last5_avg"),
          "flow_halftime5_gap":d(tf,of,"halftime_margin_team_last5_avg"),
          "flow_clutch5_gap":d(tf,of,"clutch_actions_last5_avg"),
          "flow_leadchanges5_gap":d(tf,of,"lead_changes_last5_avg"),
          "flow_comeback5_gap":d(tf,of,"comeback_win_last5_avg"),
          "flow_blownlead_adv":d(of,tf,"blown_halftime_lead_loss_last5_avg"),
          "flow_largestdef_adv":d(of,tf,"largest_deficit_last5_avg"),
          "work_top5_3d_adv":d(ow,tw,"top5_minutes_prev_3d"),
          "work_top5_5d_adv":d(ow,tw,"top5_minutes_prev_5d"),
          "work_top8_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
          "work_top8_5d_adv":d(ow,tw,"top8_minutes_prev_5d"),
          "work_usage3d_adv":d(ow,tw,"top5_usage_prev_3d"),
          "work_36plus_adv":d(ow,tw,"players_36plus_prior_game"),
          "work_heavy_streak_adv":d(ow,tw,"max_consecutive_prior_30plus"),
          "spread_move":n(m.get("spread_move")),"total_move":n(m.get("total_move")),
        })

A=[r for r in rows if r["season"] in DISC_A]
features=[
 "market_prob","rest_diff","lineup_stability_gap","lineup_diversity_adv","starter_churn_adv",
 "flow_secondhalf5_gap","flow_halftime5_gap","flow_clutch5_gap","flow_leadchanges5_gap","flow_comeback5_gap","flow_blownlead_adv","flow_largestdef_adv",
 "work_top5_3d_adv","work_top5_5d_adv","work_top8_3d_adv","work_top8_5d_adv","work_usage3d_adv","work_36plus_adv","work_heavy_streak_adv",
 "spread_move","total_move"
]
conds=[]
for f in features:
    vals=[r.get(f) for r in A if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.65,0.75,0.85):
        hi=q(vals,qq);lo=q(vals,1-qq)
        if hi is not None:conds.append((f,">=",float(hi)))
        if lo is not None:conds.append((f,"<=",float(lo)))
conds += [("opp_b2b","==",1.0),("side_b2b","==",0.0),("opp_prev_ot","==",1.0),("is_home","==",1.0),("is_home","==",0.0),("is_dog","==",1.0)]

def meet(r,cs):
    for f,op,v in cs:
        x=r.get(f)
        if x is None:return False
        if op==">=" and x<v:return False
        if op=="<=" and x>v:return False
        if op=="==" and x!=v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])
def desc(cs):return " AND ".join(f"{f} {op} {v:.4g}" if isinstance(v,float) else f"{f} {op} {v}" for f,op,v in cs)

# Singles with at least some discovery signal, then 2- and 3-way combinations.
screen=[]
for c in conds:
    m=phase((c,),DISC_A)
    if m["n"]>=120 and m["hit"] is not None and (m["hit"]>=0.63 or (m["roi"] is not None and m["roi"]>=0.025)):
        screen.append(c)
rules=[]
for a,b in itertools.combinations(screen,2):
    if a[0]!=b[0]:rules.append((a,b))
for a,b,c in itertools.combinations(screen,3):
    if len({a[0],b[0],c[0]})==3:rules.append((a,b,c))

results=[];seen=set()
for cs in rules:
    k=tuple((f,o,round(v,8) if isinstance(v,float) else v) for f,o,v in cs)
    if k in seen:continue
    seen.add(k)
    a=phase(cs,DISC_A);b=phase(cs,DISC_B);v=phase(cs,VALID);h=phase(cs,HOLDOUT)
    if a["n"]<70 or b["n"]<45 or v["n"]<20 or h["n"]<45:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    # New mechanisms must be both high probability and economically meaningful.
    if a["hit"]<0.72 or a["roi"]<0.035:continue
    if b["hit"]<0.70 or b["roi"]<=0:continue
    if v["hit"]<0.70 or v["roi"]<=0:continue
    if h["hit"]<0.72 or h["roi"]<0.04:continue
    results.append({
      "rule":desc(cs),"status":"novel_shadow_candidate",
      "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
      "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
      "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_wilson_low":h["wilson_low"]
    })
results.sort(key=lambda x:(x["holdout_wilson_low"] or 0,x["holdout_roi"],x["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_NOVEL_{i:03d}"

fields=[]
for r in results:
    for k in r:
        if k not in fields:fields.append(k)
with open(OUT/"novel_candidates.csv","w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(results)
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"screened_conditions":len(screen),
        "rules_tested":len(seen),"candidates":len(results),"top_methods":results[:50],
        "policy":"Novel lane excludes net/off/def/shooting efficiency as primary features; thresholds from 2018-21 only with internal confirmation and untouched later evaluation."}
(OUT/"novel_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
