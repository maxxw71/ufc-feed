#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"ml001_elite"
OUT.mkdir(parents=True,exist_ok=True)

A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
SEASONS=A|B|V|H
NET=7.759;REST=2.0;CAP=5.5

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
        if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
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
        net=d(tr,orr,"_net_rating_est_last5_avg");defadv=d(orr,tr,"_def_rating_est_last5_avg")
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net,defadv,rest,orest,med):continue
        if net<NET or rest-orest<REST or defadv>CAP:continue
        rows.append({
          "season":g.get("season"),"game_id":gid,"median":med,"worst":worst,"won":hs>as_ if home else as_>hs,
          "market_prob":n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig")),
          "net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
          "net_consistency":min(net,d(tr,orr,"_net_rating_est_last10_avg")) if d(tr,orr,"_net_rating_est_last10_avg") is not None else None,
          "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"efg5_gap":d(tr,orr,"_e_fg_last5_avg"),
          "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),"threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "rotation_size_adv":d(ol,tl,"prior5_rotation_players"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "opp_prev_ot":1.0 if t(oc.get("prev_game_went_ot")) else 0.0,
          "is_home":1.0 if home else 0.0,"is_away":0.0 if home else 1.0,"is_fav":1.0 if med<0 else 0.0,"is_dog":1.0 if med>0 else 0.0
        })

disc=[r for r in rows if r["season"] in A]
features=["market_prob","net10_gap","net_consistency","off5_gap","efg5_gap","ts5_gap","threepa5_gap","lineup_stability_gap","lineup_diversity_adv","rotation_size_adv","starter_churn_adv"]
conds=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<25:continue
    for qq in (0.35,0.50,0.65,0.75):
        lo=q(vals,qq);hi=q(vals,1-qq) if qq<0.5 else None
        if lo is not None:conds.append((f,">=",float(lo)))
        if hi is not None:conds.append((f,"<=",float(hi)))
conds += [("opp_b2b","==",1.0),("opp_prev_ot","==",1.0),("is_away","==",1.0),("is_home","==",1.0),("is_fav","==",1.0),("is_dog","==",1.0)]

def keep(r,c):
    f,op,v=c;x=r.get(f)
    if x is None:return False
    if op==">=":return x>=v
    if op=="<=":return x<=v
    return x==v
def phase(c,ss,price="median"):return met([r for r in rows if r["season"] in ss and keep(r,c)],price)
def desc(c):
    f,o,v=c
    return f"{f} {o} {v:.4g}" if isinstance(v,float) else f"{f} {o} {v}"

results=[]
baseA=met([r for r in rows if r["season"] in A])
for c in conds:
    a=phase(c,A);b=phase(c,B);v=phase(c,V);h=phase(c,H);hw=phase(c,H,"worst")
    if a["n"]<20 or b["n"]<15 or v["n"]<10 or h["n"]<10:continue
    # The extra filter must improve the early block materially, then survive forward.
    if a["hit"] is None or a["roi"] is None or a["hit"]<0.78 or a["roi"]<0.12:continue
    if b["hit"] is None or b["roi"] is None or b["hit"]<0.76 or b["roi"]<0.05:continue
    if v["hit"] is None or v["roi"] is None or v["hit"]<0.75 or v["roi"]<0.05:continue
    if h["hit"] is None or h["roi"] is None or h["hit"]<0.80 or h["roi"]<0.10:continue
    results.append({
      "extra_filter":desc(c),"condition":json.dumps(c),
      "discA_n":a["n"],"discA_hit":a["hit"],"discA_roi":a["roi"],
      "discB_n":b["n"],"discB_hit":b["hit"],"discB_roi":b["roi"],
      "validation_n":v["n"],"validation_hit":v["hit"],"validation_roi":v["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],"holdout_worst_roi":hw["roi"],"holdout_wilson_low":h["wilson_low"]
    })
results.sort(key=lambda x:(x["holdout_wilson_low"] or 0,x["holdout_roi"],x["holdout_n"]),reverse=True)
for i,r in enumerate(results,1):r["method_id"]=f"NBA_ML001DEF_ELITE_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"base_rule":f"net5_gap>={NET}, rest_diff>={REST}, def5_adv<={CAP}",
        "base_discA":baseA,"conditions_tested":len(conds),"retained":len(results),"candidates":results,
        "policy":"One-extra-filter search only. Thresholds formed from early discovery block; later phases used as forward pass/fail. Because later seasons have already been inspected in prior work, all descendants still require 2026-27 prospective confirmation."}
(OUT/"anchored_elite_search.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
