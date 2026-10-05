#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"; F=NBA/"features"; OUT=NBA/"research"/"elite"
OUT.mkdir(parents=True,exist_ok=True)

DISC_A={"2018-19","2019-20","2020-21"}
DISC_B={"2021-22","2022-23"}
VALID={"2023-24"}
HOLDOUT={"2024-25","2025-26"}
ALL=DISC_A|DISC_B|VALID|HOLDOUT

def rgz(p):
    if not p.exists(): return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f: return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"): return None
    try:return float(v)
    except:return None
def t(v): return str(v).lower() in ("true","1","yes")
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;d=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/d
def metrics(rr,price="ml_median"):
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
def q(vals,qq):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(qq*(len(vals)-1)))] if vals else None

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
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{})
        tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if ml is None or abs(ml)<100:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        net5=d(tr,orr,"_net_rating_est_last5_avg");net10=d(tr,orr,"_net_rating_est_last10_avg")
        row={
          "season":g.get("season"),"game_id":gid,"team_id":tid,"side":"home" if home else "away",
          "ml_median":ml,"ml_worst":worst,"won":hs>as_ if home else as_>hs,
          "market_prob":n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig")),
          "net5_gap":net5,"net10_gap":net10,
          "net_consistency":min(net5,net10) if net5 is not None and net10 is not None else None,
          "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "efg5_gap":d(tr,orr,"_e_fg_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
          "points5_gap":d(tr,orr,"points_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "side_b2b":1.0 if t(tc.get("back_to_back")) else 0.0,
          "opp_prev_ot":1.0 if t(oc.get("prev_game_went_ot")) else 0.0,
          "side_prev_ot":1.0 if t(tc.get("prev_game_went_ot")) else 0.0,
          "fatigue4_adv":(n(oc.get("games_prev_4_days"))-n(tc.get("games_prev_4_days")))
             if n(oc.get("games_prev_4_days")) is not None and n(tc.get("games_prev_4_days")) is not None else None,
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "rotation_size_adv":d(ol,tl,"prior5_rotation_players"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "stint_complexity_adv":d(ol,tl,"prior5_avg_stints_per_game"),
          "spread_move":n(m.get("spread_move")),"total_move":n(m.get("total_move")),
          "is_home":1.0 if home else 0.0,"is_dog":1.0 if ml>0 else 0.0,
          "is_fav":1.0 if ml<0 else 0.0,
        }
        rows.append(row)

A=[r for r in rows if r["season"] in DISC_A]
features=["net5_gap","net10_gap","net_consistency","off5_gap","def5_adv","efg5_gap","ts5_gap","threepa5_gap","points5_gap",
          "rest_diff","fatigue4_adv","lineup_stability_gap","lineup_diversity_adv","rotation_size_adv","starter_churn_adv",
          "stint_complexity_adv","market_prob","spread_move","total_move"]
conds=[]
for f in features:
    vals=[r.get(f) for r in A if r.get(f) is not None]
    if len(vals)<300:continue
    for qq in (0.65,0.75,0.85,0.90):
        hi=q(vals,qq);lo=q(vals,1-qq)
        if hi is not None:conds.append((f,">=",float(hi)))
        if lo is not None:conds.append((f,"<=",float(lo)))
conds += [
 ("opp_b2b","==",1.0),("side_b2b","==",0.0),("opp_prev_ot","==",1.0),("side_prev_ot","==",0.0),
 ("is_home","==",1.0),("is_home","==",0.0),("is_dog","==",1.0),("is_fav","==",1.0)
]

def meet(r,cs):
    for f,op,v in cs:
        x=r.get(f)
        if x is None:return False
        if op==">=" and x<v:return False
        if op=="<=" and x>v:return False
        if op=="==" and x!=v:return False
    return True
def desc(cs):return " AND ".join(f"{f} {op} {v:.4g}" if isinstance(v,float) else f"{f} {op} {v}" for f,op,v in cs)
def phase(cs,seasons,price="ml_median"):
    return metrics([r for r in rows if r["season"] in seasons and meet(r,cs)],price)

# Pre-screen singles on DISC_A to shrink combinatorics toward useful signals.
single=[]
for c in conds:
    m=phase((c,),DISC_A)
    if m["n"]>=120 and m["hit"] is not None and (m["hit"]>=0.66 or (m["roi"] is not None and m["roi"]>=0.03)):
        single.append(c)

rules=[]
# pairs/triples, all thresholds selected only from DISC_A
for a,b in itertools.combinations(single,2):
    if a[0]==b[0]:continue
    rules.append((a,b))
for a,b,c in itertools.combinations(single,3):
    if len({a[0],b[0],c[0]})<3:continue
    rules.append((a,b,c))

# Focused descendants of existing mechanisms.
base_ml001=(("net5_gap",">=",7.759),("rest_diff",">=",2.0))
for c in single:
    if c[0] not in {"net5_gap","rest_diff"}:rules.append(base_ml001+(c,))
for a,b in itertools.combinations(single,2):
    if a[0] in {"net5_gap","rest_diff"} or b[0] in {"net5_gap","rest_diff"} or a[0]==b[0]:continue
    rules.append(base_ml001+(a,b))

seen=set();results=[];tested=0
for cs in rules:
    key=tuple((f,o,round(v,8) if isinstance(v,float) else v) for f,o,v in cs)
    if key in seen:continue
    seen.add(key);tested+=1
    a=phase(cs,DISC_A);b=phase(cs,DISC_B);v=phase(cs,VALID);h=phase(cs,HOLDOUT)
    # High-selectivity target: require internal confirmation before external validation.
    if a["n"]<70 or b["n"]<45 or v["n"]<20 or h["n"]<45:continue
    if None in (a["hit"],a["roi"],b["hit"],b["roi"],v["hit"],v["roi"],h["hit"],h["roi"]):continue
    if a["hit"]<0.76 or a["roi"]<0.04:continue
    if b["hit"]<0.74 or b["roi"]<=0:continue
    if v["hit"]<0.74 or v["roi"]<=0:continue
    if h["hit"]<0.74 or h["roi"]<0.04:continue
    worst=phase(cs,HOLDOUT,"ml_worst")
    status="elite_shadow_candidate"
    results.append({
      "rule":desc(cs),"conditions":json.dumps(cs),"status":status,
      "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
      "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
      "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_wilson_low":h["wilson_low"],
      "holdout_worst_roi":worst["roi"]
    })
results.sort(key=lambda r:(r["holdout_wilson_low"] or 0,r["holdout_roi"],r["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_ELITE_{i:03d}"

def wcsv(path,rr):
    fs=[]
    for r in rr:
        for k in r:
            if k not in fs:fs.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rr)

wcsv(OUT/"elite_candidates.csv",results)
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"single_conditions_screened":len(single),
        "rules_tested":tested,"elite_candidates":len(results),"top_methods":results[:50],
        "policy":"Thresholds from 2018-21 only. 2021-23 internal confirmation, 2023-24 validation, 2024-26 holdout. Every retained method must clear hit-rate and ROI floors in all four phases."}
(OUT/"elite_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
