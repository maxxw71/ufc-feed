#!/usr/bin/env python3
"""Leakage-aware injury/rotation method discovery. All candidates remain research-only.

No missing official injury report is interpreted as 'healthy'.
Thresholds are learned in 2021-22 only; later years never select thresholds.
2024-26 must be populated before promotion-quality holdout is declared.
"""
import csv,gzip,itertools,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"injury_rotation";OUT.mkdir(parents=True,exist_ok=True)
A={"2021-22"};B={"2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
SEASONS=A|B|V|H
def read(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None
def diff(a,b,k):
    av=number((a or {}).get(k));bv=number((b or {}).get(k))
    return av-bv if av is not None and bv is not None else None
def truth(x):return str(x).lower() in ("true","1","yes")
def price_return(american,result):
    x=number(american)
    if x is None or abs(x)<100 or result is None:return None
    return 0.0 if result==0.5 else -1.0 if result==0 else x/100 if x>0 else 100/abs(x)
def quantile(xs,p):
    x=sorted(x for x in xs if x is not None)
    return x[int(p*(len(x)-1))] if x else None
def metrics(rows,price_key):
    pp=[(r,price_return(r.get(price_key),r.get("outcome"))) for r in rows]
    pp=[(r,v) for r,v in pp if v is not None]
    dec=[r for r,v in pp if r["outcome"]!=0.5];wins=sum(r["outcome"]==1 for r in dec)
    total=sum(v for r,v in pp)
    return {"n":len(pp),"wins":wins,"losses":len(dec)-wins,"pushes":len(pp)-len(dec),
            "hit":wins/len(dec) if dec else None,"roi":total/len(pp) if pp else None,"profit_units":total}
def index(p):return {(r.get("game_id"),r.get("team_id")):r for r in read(p)}
def meet(r,conditions):
    for key,op,t in conditions:
        x=r.get(key)
        if x is None or ((x<t) if op==">=" else (x>t)):return False
    return True

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read(p):
        if g.get("season") in SEASONS and truth(g.get("completed")):games[g["game_id"]]=g
market={r.get("game_id"):r for r in read(F/"historical_market_features.csv.gz")}
inj=index(F/"historical_injury_teams.csv.gz")
core=index(F/"starter_core_pregame.csv.gz")
line=index(F/"lineup_pregame.csv.gz")
roll=index(F/"team_rolling.csv.gz")
work=index(F/"team_workload_pregame.csv.gz")
stand=index(F/"standings_pregame.csv.gz")

FEATURE_GROUPS={
  "opponent_missing_minutes_edge":"injury",
  "opponent_missing_points_edge":"injury",
  "opponent_missing_assists_edge":"injury",
  "opponent_out_count_edge":"injury",
  "opponent_injury_count_edge":"injury",
  "opponent_questionable_edge":"injury",
  "starter_pm5_gap":"starter",
  "starter_overlap_gap":"rotation",
  "net5_gap":"efficiency",
  "workload_edge":"workload",
  "market_prob":"market",
}
side=[]
season_coverage=defaultdict(lambda:{"games_with_market":0,"games_with_both_official_injury_rows":0,"sides":0})
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    season=g["season"];cov=season_coverage[season];cov["games_with_market"]+=1
    home=str(g.get("home_team_id") or "");away=str(g.get("away_team_id") or "")
    if (gid,home) not in inj or (gid,away) not in inj:continue
    cov["games_with_both_official_injury_rows"]+=1
    hs=number(g.get("home_score"));av=number(g.get("away_score"));sp=number(m.get("home_spread_signed_median"))
    if hs is None or av is None:continue
    for is_home in (True,False):
        t,o=(home,away) if is_home else (away,home)
        ti,oi=inj[(gid,t)],inj[(gid,o)]
        c,oc=core.get((gid,t),{}),core.get((gid,o),{})
        l,ol=line.get((gid,t),{}),line.get((gid,o),{})
        r,orr=roll.get((gid,t),{}),roll.get((gid,o),{})
        w,ow=work.get((gid,t),{}),work.get((gid,o),{})
        score,opp=(hs,av) if is_home else (av,hs)
        ml_key=("home_" if is_home else "away_")+"moneyline_"
        ats_key=("home_" if is_home else "away_")+"spread_price_"
        ml={z:number(m.get(ml_key+z)) for z in ("median","best","worst")}
        at={z:number(m.get(ats_key+z)) for z in ("median","best","worst")}
        total_line=sp if is_home else (-sp if sp is not None else None)
        features={
          "opponent_missing_minutes_edge":diff(oi,ti,"weighted_missing_minutes_last5"),
          "opponent_missing_points_edge":diff(oi,ti,"weighted_missing_points_last5"),
          "opponent_missing_assists_edge":diff(oi,ti,"weighted_missing_assists_last5"),
          "opponent_out_count_edge":diff(oi,ti,"out_players"),
          "opponent_injury_count_edge":diff(oi,ti,"injury_illness_players"),
          "opponent_questionable_edge":diff(oi,ti,"questionable_players"),
          "starter_pm5_gap":diff(c,oc,"starter_plusminus5_avg"),
          "starter_overlap_gap":diff(l,ol,"starter_overlap_prev_game"),
          "net5_gap":diff(r,orr,"_net_rating_est_last5_avg"),
          "workload_edge":diff(ow,w,"top5_minutes_prev_3d"),
          "market_prob":number(m.get(("home_" if is_home else "away_")+"implied_probability_devig"))
        }
        ats_out=None if total_line is None else (1 if score+total_line>opp else 0 if score+total_line<opp else 0.5)
        for typ,out,pr in (("moneyline",float(score>opp),ml),("ats",ats_out,at)):
            if out is None or pr["median"] is None:continue
            side.append({"season":season,"game_id":gid,"team_id":t,"market":typ,"outcome":out,
                         "median":pr["median"],"best":pr["best"],"worst":pr["worst"],**features})
            cov["sides"]+=1

# Quantile directions are fixed in discovery, not chosen by later win/loss results.
# Each candidate contains at least one injury-domain variable and a distinct
# supporting domain, which improves interpretability over near-duplicate rules.
CANDIDATE_FEATURES={
  "opponent_missing_minutes_edge":("injury",">=",0.70),
  "opponent_missing_points_edge":("injury",">=",0.70),
  "opponent_missing_assists_edge":("injury",">=",0.70),
  "opponent_out_count_edge":("injury",">=",0.70),
  "opponent_injury_count_edge":("injury",">=",0.70),
  "opponent_questionable_edge":("injury",">=",0.70),
  "starter_pm5_gap":("starter",">=",0.60),
  "starter_overlap_gap":("rotation",">=",0.60),
  "net5_gap":("efficiency",">=",0.60),
  "workload_edge":("workload",">=",0.60),
  "market_prob":("market",">=",0.60)
}
candidates=[];screen_count=0
for market_name in ("moneyline","ats"):
    rows=[r for r in side if r["market"]==market_name]
    discovery=[r for r in rows if r["season"] in A]
    conds=[]
    for key,(domain,op,p) in CANDIDATE_FEATURES.items():
        values=[r[key] for r in discovery if r.get(key) is not None]
        if len(values)<150:continue
        cut=quantile(values,p)
        if cut is not None:conds.append((key,op,cut))
    screen_count+=len(conds)
    for size in (2,3):
        for cs in itertools.combinations(conds,size):
            domains={FEATURE_GROUPS[k] for k,op,x in cs}
            if "injury" not in domains or len(domains)<2:continue
            qualified=[r for r in rows if meet(r,cs)]
            result={x:metrics([r for r in qualified if r["season"] in years],"median")
                    for x,years in (("discovery",A),("confirmation",B),("validation",V),("holdout",H))}
            a,b,v,h=(result[z] for z in ("discovery","confirmation","validation","holdout"))
            if a["n"]<20 or b["n"]<20:continue
            if a["roi"] is None or b["roi"] is None or a["roi"]<=0 or b["roi"]<=0:continue
            if a["hit"] is None or b["hit"] is None or a["hit"]<(.67 if market_name=="moneyline" else .52) or b["hit"]<(.67 if market_name=="moneyline" else .52):continue
            allstress={z:metrics(qualified,z) for z in ("best","median","worst")}
            hold=[r for r in qualified if r["season"] in H]
            holdstress={z:metrics(hold,z) for z in ("best","median","worst")}
            eligible= v["n"]>=15 and h["n"]>=30 and all(z["roi"] is not None and z["roi"]>0 for z in (v,h,allstress["worst"],holdstress["worst"]))
            candidates.append({
              "market":market_name,"rule":" AND ".join(f"{k} {op} {v:.5g}" for k,op,v in cs),
              "domains":sorted(domains),"phases":result,"price_stress":allstress,"holdout_price_stress":holdstress,
              "status":"eligible_for_independent_review" if eligible else "research_only_awaiting_full_2023_26_injury_holdout"
            })

candidates.sort(key=lambda z:(z["status"]=="eligible_for_independent_review",
                              z["phases"]["confirmation"]["roi"] or -100,
                              z["phases"]["confirmation"]["n"]),reverse=True)
report={
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "seasons_covered":dict(season_coverage),
  "market_side_rows":len(side),"screened_conditions":screen_count,
  "research_candidates":len(candidates),"eligible_for_independent_review":sum(x["status"]=="eligible_for_independent_review" for x in candidates),
  "leaders":candidates[:40],
  "policy":"Official report mapping must precede target tip, both teams must have parsed rows. Status and player exposure come from pregame reports and prior player games. Thresholds frozen from 2021-22 discovery only. 2022-23 is confirmation, 2023-24 validation, 2024-26 untouched holdout. No promotion if 2024-26 coverage is missing; no hindsight injuries, no missing-as-healthy, and no tuning on validation or holdout."
}
(OUT/"injury_rotation_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:report[k] for k in ("seasons_covered","market_side_rows","screened_conditions","research_candidates","eligible_for_independent_review")},indent=2))
