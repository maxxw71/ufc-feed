#!/usr/bin/env python3
import csv,gzip,json,statistics,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"; F=NBA/"features"; OUT=NBA/"research"/"loss_forensics"
OUT.mkdir(parents=True,exist_ok=True)
DISC_A={"2018-19","2019-20","2020-21"}
DISC_B={"2021-22","2022-23"}
VALID={"2023-24"}; HOLDOUT={"2024-25","2025-26"}; ALL=DISC_A|DISC_B|VALID|HOLDOUT

PARENTS={
 "ML001":lambda r:r["net5_gap"] is not None and r["rest_diff"] is not None and r["net5_gap"]>=7.759 and r["rest_diff"]>=2,
 "V2_001":lambda r:r["net5_gap"] is not None and r["starter_churn_adv"] is not None and r["net5_gap"]>=9.637235800525477 and r["starter_churn_adv"]>=2,
 "V2_003":lambda r:r["def5_adv"] is not None and r["ts5_gap"] is not None and r["def5_adv"]>=10.430476353289166 and r["ts5_gap"]>=0.0362475832398097,
}

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
def met(rr):
    vals=[]
    for r in rr:
        p=pf(r["ml"],r["won"])
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"hit":None,"roi":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def quant(vals,q):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(q*(len(vals)-1)))] if vals else None

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
        ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        if ml is None or abs(ml)<100:continue
        rest=n(tc.get("days_since_prev_game"));orest=n(oc.get("days_since_prev_game"))
        net5=d(tr,orr,"_net_rating_est_last5_avg");net10=d(tr,orr,"_net_rating_est_last10_avg")
        rows.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,"won":hs>as_ if home else as_>hs,"ml":ml,
          "market_prob":n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig")),
          "net5_gap":net5,"net10_gap":net10,
          "net_consistency":min(net5,net10) if net5 is not None and net10 is not None else None,
          "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
          "efg5_gap":d(tr,orr,"_e_fg_last5_avg"),"ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
          "threepa5_gap":d(tr,orr,"_three_pa_rate_last5_avg"),"points5_gap":d(tr,orr,"points_last5_avg"),
          "rest_diff":rest-orest if rest is not None and orest is not None else None,
          "fatigue4_adv":(n(oc.get("games_prev_4_days"))-n(tc.get("games_prev_4_days")))
             if n(oc.get("games_prev_4_days")) is not None and n(tc.get("games_prev_4_days")) is not None else None,
          "opp_b2b":1.0 if t(oc.get("back_to_back")) else 0.0,
          "opp_prev_ot":1.0 if t(oc.get("prev_game_went_ot")) else 0.0,
          "side_prev_ot":1.0 if t(tc.get("prev_game_went_ot")) else 0.0,
          "lineup_stability_gap":d(tl,ol,"prior5_top_lineup_share"),
          "lineup_diversity_adv":d(ol,tl,"prior5_distinct_lineups"),
          "rotation_size_adv":d(ol,tl,"prior5_rotation_players"),
          "starter_churn_adv":d(ol,tl,"prior5_distinct_starters"),
          "is_home":1.0 if home else 0.0,"is_dog":1.0 if ml>0 else 0.0
        })

features=["market_prob","net5_gap","net10_gap","net_consistency","off5_gap","def5_adv","efg5_gap","ts5_gap","threepa5_gap","points5_gap",
          "rest_diff","fatigue4_adv","lineup_stability_gap","lineup_diversity_adv","rotation_size_adv","starter_churn_adv"]
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"parents":{}}
filter_rows=[]

for name,parent in PARENTS.items():
    base=[r for r in rows if parent(r)]
    A=[r for r in base if r["season"] in DISC_A]
    wins=[r for r in A if r["won"]]; losses=[r for r in A if not r["won"]]
    contrasts={}
    candidate_filters=[]
    for f in features:
        wv=[r[f] for r in wins if r.get(f) is not None];lv=[r[f] for r in losses if r.get(f) is not None]
        if len(wv)<20 or len(lv)<10:continue
        contrasts[f]={
          "win_median":statistics.median(wv),"loss_median":statistics.median(lv),
          "median_gap":statistics.median(wv)-statistics.median(lv),
          "win_mean":statistics.mean(wv),"loss_mean":statistics.mean(lv)
        }
        # Candidate filters learned only from DISC_A parent population.
        vals=[r[f] for r in A if r.get(f) is not None]
        for qq in (0.25,0.40,0.60,0.75):
            cut=quant(vals,qq)
            if cut is None:continue
            for op in (">=","<="):
                def keep(r,ff=f,oo=op,cc=cut):
                    x=r.get(ff)
                    return x is not None and (x>=cc if oo==">=" else x<=cc)
                kept=[r for r in A if keep(r)]
                m=met(kept)
                base_m=met(A)
                if m["n"]<35 or m["hit"] is None or base_m["hit"] is None:continue
                # Must improve both hit rate and ROI in discovery A.
                if m["hit"]>=base_m["hit"]+0.04 and m["roi"] is not None and base_m["roi"] is not None and m["roi"]>=base_m["roi"]+0.03:
                    candidate_filters.append((f,op,float(cut),m))
    # Test candidates forward, no retuning.
    tested=[]
    for f,op,cut,am in candidate_filters:
        def keep(r,ff=f,oo=op,cc=cut):
            x=r.get(ff)
            return x is not None and (x>=cc if oo==">=" else x<=cc)
        B=met([r for r in base if r["season"] in DISC_B and keep(r)])
        V=met([r for r in base if r["season"] in VALID and keep(r)])
        H=met([r for r in base if r["season"] in HOLDOUT and keep(r)])
        row={"parent":name,"feature":f,"op":op,"cut":cut,
             "discA_n":am["n"],"discA_hit":am["hit"],"discA_roi":am["roi"],
             "discB_n":B["n"],"discB_hit":B["hit"],"discB_roi":B["roi"],
             "validation_n":V["n"],"validation_hit":V["hit"],"validation_roi":V["roi"],
             "holdout_n":H["n"],"holdout_hit":H["hit"],"holdout_roi":H["roi"]}
        tested.append(row);filter_rows.append(row)
    tested.sort(key=lambda x:((x["holdout_hit"] or 0),(x["holdout_roi"] or -9),(x["holdout_n"] or 0)),reverse=True)
    report["parents"][name]={
      "base":{"discA":met(A),"discB":met([r for r in base if r["season"] in DISC_B]),"validation":met([r for r in base if r["season"] in VALID]),"holdout":met([r for r in base if r["season"] in HOLDOUT])},
      "win_loss_feature_contrasts":contrasts,
      "forward_tested_loss_filters":tested[:30]
    }

with open(OUT/"loss_filters.csv","w",encoding="utf-8",newline="") as f:
    fields=[]
    for r in filter_rows:
        for k in r:
            if k not in fields:fields.append(k)
    w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(filter_rows)
(OUT/"loss_forensics_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"parents":{k:{"base":v["base"],"top_filters":v["forward_tested_loss_filters"][:10]} for k,v in report["parents"].items()}},indent=2))
