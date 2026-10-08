#!/usr/bin/env python3
"""Independent frozen-candidate H5 audit. No threshold retuning or live writes."""
import csv,gzip,json,itertools,math,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
NBA=Path(__file__).resolve().parents[1]
F=NBA/"features";DATA=NBA/"data";OUT=NBA/"research"/"hunt_v5"
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"}
V={"2023-24"};H={"2024-25","2025-26"};YEARS=A|B|V|H
REVIEW=("NBA_H5_ML_001","NBA_H5_ML_002","NBA_H5_ML_003","NBA_H5_ATS_001")
def read(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as h:return list(csv.DictReader(h))
def n(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None
def sub(a,b):
    return a-b if a is not None and b is not None else None
def delta(a,b,k):
    return sub(n(a.get(k)),n(b.get(k)))
def metrics(rows,price="median"):
    evaluated=[]
    for r in rows:
        p=n(r.get(price));w=r.get("out")
        if p is None or abs(p)<100 or w is None:continue
        ret=0 if w==0.5 else -1.0 if w==0 else p/100 if p>0 else 100/abs(p)
        evaluated.append((r,ret))
    decided=[r for r,z in evaluated if r["out"]!=0.5]
    win=sum(r["out"]==1 for r in decided);pr=sum(z for _,z in evaluated)
    return {"n":len(evaluated),"wins":win,"losses":len(decided)-win,
            "pushes":len(evaluated)-len(decided),
            "hit":win/len(decided) if decided else None,
            "roi":pr/len(evaluated) if evaluated else None,"profit_units":pr}
def idx(path):
    return {(r.get("game_id"),r.get("team_id")):r for r in read(path)}
def matches(r,cs):
    for field,op,cut in cs:
        x=r.get(field)
        if x is None:return False
        if op=="==" and not x==cut:return False
        if op==">=" and not x>=cut:return False
        if op=="<=" and not x<=cut:return False
    return True
def cs_for(name,report):
    for lane in ("moneyline","ats"):
        for x in report[lane]["candidates"]:
            if x["method_id"]==name:
                return [(f,op,float(v)) for f,op,v in x["conditions"]]
    raise KeyError(name)
games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read(p):
        if g.get("season") in YEARS and str(g.get("completed")).lower() in ("true","1"):
            games[g["game_id"]]=g
market={r["game_id"]:r for r in read(F/"historical_market_features.csv.gz")}
strength={r["game_id"]:r for r in read(F/"pregame_strength.csv.gz")}
ctx=idx(NBA/"team_game_context.csv.gz");shape=idx(F/"scoring_shape_rolling.csv.gz")
impact=idx(F/"starter_individual_impact_pregame.csv.gz")
continuity=idx(F/"starter_prior_season_continuity.csv.gz")
stand=idx(F/"standings_pregame.csv.gz");transaction=idx(F/"transaction_value_pregame.csv.gz")

dataset=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aw=n(g.get("away_score"))
    hspread=n(m.get("home_spread_signed_median"))
    if None in (hs,aw):continue
    h=str(g.get("home_team_id") or "");a=str(g.get("away_team_id") or "")
    for home in (True,False):
        tid,oid=(h,a) if home else (a,h)
        sh,so=shape.get((gid,tid),{}),shape.get((gid,oid),{})
        im,io=impact.get((gid,tid),{}),impact.get((gid,oid),{})
        cont,co=continuity.get((gid,tid),{}),continuity.get((gid,oid),{})
        st,sto=stand.get((gid,tid),{}),stand.get((gid,oid),{})
        tx,txo=transaction.get((gid,tid),{}),transaction.get((gid,oid),{})
        context,cop=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
        strengthrow=strength.get(gid,{})
        pointdiff=n(strengthrow.get("home_prior_point_diff_avg" if home else "away_prior_point_diff_avg"))
        otherdiff=n(strengthrow.get("away_prior_point_diff_avg" if home else "home_prior_point_diff_avg"))
        score,other=(hs,aw) if home else (aw,hs)
        spread=hspread if home else (-hspread if hspread is not None else None)
        ats=None if spread is None else (1.0 if score+spread>other else 0.0 if score+spread<other else 0.5)
        prefix="home_" if home else "away_"
        feat={
          "starter_negative_adv":delta(io,im,"starter_individual_negative_count"),
          "prior_minutes_share_gap":delta(cont,co,"prior_team_starter_minutes_share"),
          "clutch10_gap":delta(sh,so,"clutch_margin_last10_avg"),
          "strength_pointdiff_gap":sub(pointdiff,otherdiff),
          "rank_gap":delta(sto,st,"conference_rank"),
          "tx_minutes14_gap":delta(tx,txo,"net_minutes_value_14d"),
          "is_home":1.0 if home else 0.0,
          "rest_diff":delta(context,cop,"days_since_prev_game")
        }
        for typ,outcome,pricepref in (("moneyline",1.0 if score>other else 0.0,prefix+"moneyline_"),("ats",ats,prefix+"spread_price_")):
            if outcome is None:continue
            prices={key:n(m.get(pricepref+key)) for key in ("median","best","worst")}
            if prices["median"] is None:continue
            dataset.append({
              "season":g["season"],"game_id":gid,"team_id":tid,
              "selection":g.get("home_team" if home else "away_team"),
              "market":typ,"out":outcome,
              "price_prob":n(m.get(prefix+"implied_probability_devig")),
              "book_count":n(m.get("pregame_provider_count")),
              **prices,**feat
            })
report=json.loads((OUT/"hunt_v5_report.json").read_text())
ledger_path=NBA/"research"/"bankroll"/"live_arsenal_50k_1pct_ledger.csv"
live_methods=defaultdict(set)
if ledger_path.exists():
    with ledger_path.open(newline="",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for mid in str(r.get("methods") or "").split("|"):
                if mid:live_methods[(str(r["game_id"]),str(r["market"]),str(r["selection"]))].add(mid)

def band(rows,key,buckets):
    return {lab:metrics([r for r in rows if r.get(key) is not None and (lo is None or r[key]>=lo) and (hi is None or r[key]<hi)])
            for lab,lo,hi in buckets}
def stress(rr):
    return {pk:metrics(rr,pk) for pk in ("median","best","worst")}
out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"dataset_side_rows":len(dataset),"methods":{}}
selection_sets={}
for name in REVIEW:
    conds=cs_for(name,report)
    market_name="ats" if "_ATS_" in name else "moneyline"
    rr=[r for r in dataset if r["market"]==market_name and matches(r,conds)]
    ss={(r["game_id"],r["team_id"]) for r in rr}
    selection_sets[name]=ss
    independent=[r for r in rr if not live_methods.get((str(r["game_id"]),market_name,str(r["selection"])))]
    overlap=[r for r in rr if live_methods.get((str(r["game_id"]),market_name,str(r["selection"]))]
    phase={key:metrics([r for r in rr if r["season"] in seasons])
           for key,seasons in (("discA",A),("discB",B),("validation",V),("holdout",H))}
    valid=[(f,op,cut) for f,op,cut in conds if op!="=="]
    variations=[]
    # Each numeric cutoff shifts +-15% of magnitude or a small measurement floor;
    # do not select a neighborhood winner or update the frozen original.
    for multipliers in itertools.product((-1,0,1),repeat=len(valid)):
        replacements=[]
        for (f,op,cut),move in zip(valid,multipliers):
            floor=0.1 if abs(cut)<1 else 0.5
            newcut=cut+move*max(abs(cut)*.15,floor)
            replacements.append((f,op,newcut))
        allconds=[(f,op,cut) for f,op,cut in conds if op=="=="]+replacements
        z=[r for r in dataset if r["market"]==market_name and matches(r,allconds)]
        hold=[r for r in z if r["season"] in H]
        variations.append({"moves":multipliers,"overall":metrics(z),"holdout":metrics(hold)})
    eligible=[z for z in variations if z["overall"]["n"]>=max(60,len(rr)//2)
              and z["holdout"]["n"]>=max(20,phase["holdout"]["n"]//2)]
    stable=[z for z in eligible if (z["overall"]["roi"] or -999)>0 and (z["holdout"]["roi"] or -999)>0]
    no_cross_overs=band(rr,"price_prob",[
       ("under_60pct",None,.6),("60_69pct",.6,.7),("70_79pct",.7,.8),("80plus_pct",.8,None)])
    out["methods"][name]={
      "market":market_name,"rule":report[market_name]["candidates"][next(i for i,x in enumerate(report[market_name]["candidates"]) if x["method_id"]==name)]["rule"],
      "frozen_conditions":conds,"overall":metrics(rr),"phases":phase,
      "by_season":{s:metrics([r for r in rr if r["season"]==s]) for s in sorted(YEARS)},
      "price_stress":stress(rr),"holdout_price_stress":stress([r for r in rr if r["season"] in H]),
      "book_coverage":{"1_or_fewer":sum(1 for r in rr if r["book_count"] is None or r["book_count"]<=1),
                       "two_or_more":sum(1 for r in rr if r["book_count"] is not None and r["book_count"]>=2)},
      "price_probability_buckets":no_cross_overs,
      "live_arsenal_same_selection_overlap":{"shared":len(overlap),"independent":len(independent),
        "shared_performance":metrics(overlap),"independent_performance":metrics(independent),
        "live_method_counts":dict(sorted(__import__("collections").Counter(k for r in overlap for k in live_methods[(str(r["game_id"]),market_name,str(r["selection"]))]).items()))},
      "threshold_neighborhood":{"tested":len(variations),"eligible":len(eligible),"positive_overall_and_holdout":len(stable),
             "positive_fraction":len(stable)/len(eligible) if eligible else None,
             "worst_eligible_holdout_roi":min((z["holdout"]["roi"] for z in eligible if z["holdout"]["roi"] is not None),default=None)},
      "warning":"Post-selection audit: no new holdout created. These were mined from the same historical years; statistical winner's curse remains."
    }
out["pairwise"]={}
for a,b in itertools.combinations(REVIEW,2):
    inter=selection_sets[a]&selection_sets[b]
    union=selection_sets[a]|selection_sets[b]
    out["pairwise"][a+"|"+b]={"shared_game_selections":len(inter),"jaccard":len(inter)/len(union) if union else None,
                               "a_shared_fraction":len(inter)/len(selection_sets[a]) if selection_sets[a] else None,
                               "b_shared_fraction":len(inter)/len(selection_sets[b]) if selection_sets[b] else None}
out["policy"]="Frozen H5 rule cuts are never changed. All metrics are retrospective. Threshold neighborhood, per-season stability, bookmaker depth, same-selection live overlap, and inter-candidate overlap are measured without promoting or retuning any candidate."
(OUT/"hunt_v5_candidate_audit.json").write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({m:{"overall":v["overall"],"holdout":v["phases"]["holdout"],
                        "overlap":v["live_arsenal_same_selection_overlap"]["shared"],
                        "neighborhood":v["threshold_neighborhood"]}
                 for m,v in out["methods"].items()},indent=2))
