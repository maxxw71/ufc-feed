#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone
from collections import defaultdict

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"; F=NBA/"features"; OUT=NBA/"research"/"ml001"
OUT.mkdir(parents=True,exist_ok=True)
TRAIN={"2018-19","2019-20","2020-21","2021-22","2022-23"}
VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}
BASE_NET=7.759; BASE_REST=2.0

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def pfit(a,win):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if win else -1.0
def implied(a):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (-a)/((-a)+100) if a<0 else 100/(a+100)
def metrics(rr,price_key="ml_median"):
    vals=[]
    for r in rr:
        p=pfit(r.get(price_key),r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None,"profit":0}
    w=sum(1 for r,_ in vals if r["won"]); prof=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":prof/len(vals),"profit":prof}
def q(vals,q):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(q*(len(vals)-1)))] if vals else None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in TRAIN|VALID|HOLDOUT and t(g.get("completed")):games[g["game_id"]]=g
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    for side in ("home","away"):
        home=side=="home";tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{})
        tc=ctx.get((gid,tid),{});oc=ctx.get((gid,oid),{})
        tl=line.get((gid,tid),{});ol=line.get((gid,oid),{})
        net=n(tr.get("_net_rating_est_last5_avg"));onet=n(orr.get("_net_rating_est_last5_avg"))
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        med=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        best=n(m.get("home_moneyline_best") if home else m.get("away_moneyline_best"))
        worst=n(m.get("home_moneyline_worst") if home else m.get("away_moneyline_worst"))
        if None in (net,onet,rest,orest,med):continue
        if net-onet<BASE_NET or rest-orest<BASE_REST:continue
        won=(hs>aas) if home else (aas>hs)
        def d(a,b,k):
            x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
            return x-y if x is not None and y is not None else None
        rows.append({
          "season":g.get("season"),"game_id":gid,"side":side,"team_id":tid,"won":won,
          "ml_median":med,"ml_best":best,"ml_worst":worst,"implied":implied(med),
          "is_home":1.0 if home else 0.0,"is_dog":1.0 if med>0 else 0.0,
          "is_fav":1.0 if med<0 else 0.0,"opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "side_b2b":1.0 if t(tc.get("back_to_back")) else 0.0,
          "net5_gap":net-onet,"rest_diff":rest-orest,
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "rotation_size_adv":d(ol,tl,"prior5_rotation_players"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters")
        })

tr=[r for r in rows if r["season"] in TRAIN]

# Candidate refinements are defined exclusively from discovery seasons.
conds=[
 ("base",lambda r:True),
 ("away",lambda r:r["is_home"]==0),
 ("home",lambda r:r["is_home"]==1),
 ("underdog",lambda r:r["is_dog"]==1),
 ("favorite",lambda r:r["is_fav"]==1),
 ("away_underdog",lambda r:r["is_home"]==0 and r["is_dog"]==1),
 ("away_favorite",lambda r:r["is_home"]==0 and r["is_fav"]==1),
 ("opp_b2b",lambda r:r["opp_b2b"]==1),
 ("side_not_b2b",lambda r:r["side_b2b"]==0),
]
# Training-derived implied probability cuts.
for cut in [0.45,0.50,0.55,0.60,0.65,0.70,0.75,0.80]:
    conds.append((f"implied_le_{cut:.2f}",lambda r,c=cut:r["implied"] is not None and r["implied"]<=c))
    conds.append((f"implied_ge_{cut:.2f}",lambda r,c=cut:r["implied"] is not None and r["implied"]>=c))
# Training-derived lineup quantiles, only prior-game history fields.
for feat in ["lineup_stability_gap","lineup_diversity_adv","rotation_size_adv","starter_churn_adv"]:
    vals=[r.get(feat) for r in tr if r.get(feat) is not None]
    for qq in (0.60,0.70,0.80):
        cut=q(vals,qq)
        if cut is not None:
            conds.append((f"{feat}_ge_q{int(qq*100)}",lambda r,f=feat,c=cut:r.get(f) is not None and r[f]>=c))

def phase(fn,seasons,price="ml_median"):
    return metrics([r for r in rows if r["season"] in seasons and fn(r)],price)

cand=[]
for name,fn in conds:
    a=phase(fn,TRAIN);b=phase(fn,VALID);h=phase(fn,HOLDOUT)
    if a["n"]<40:continue
    # Eligibility chosen on discovery only. Later phases are reporting only.
    if a["roi"] is None or a["roi"]<=0.03:continue
    cand.append({
      "variant":name,
      "train_n":a["n"],"train_hit":a["hit"],"train_roi":a["roi"],
      "validation_n":b["n"],"validation_hit":b["hit"],"validation_roi":b["roi"],
      "holdout_n":h["n"],"holdout_hit":h["hit"],"holdout_roi":h["roi"],
    })
cand.sort(key=lambda x:(x["train_roi"],x["train_n"]),reverse=True)

# Price execution stress on frozen base rule.
price_stress={}
for price in ("ml_best","ml_median","ml_worst"):
    price_stress[price]={
      "overall":metrics(rows,price),
      "discovery":metrics([r for r in rows if r["season"] in TRAIN],price),
      "validation":metrics([r for r in rows if r["season"] in VALID],price),
      "holdout":metrics([r for r in rows if r["season"] in HOLDOUT],price),
    }

report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "policy":"All refinement eligibility is based only on 2018-19 through 2022-23. 2023-24 and 2024-26 are evaluation-only.",
 "base_rule":{"net5_gap":BASE_NET,"rest_diff":BASE_REST},
 "base_price_stress":price_stress,
 "training_selected_variants":cand,
}
(OUT/"ml001_refinement_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
