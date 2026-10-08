#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"hunt_v3";OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
SPECS={
 "NBA_H3_OVER_001":{"forced_tov_max":25.8,"rank_min":9.0,"net_min":12.958},
 "NBA_H3_OVER_002":{"clutch_min":1.7,"rank_min":8.0,"starterpm_min":7.16}
}
def rgz(p):
  if not p.exists(): return []
  with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
  try:return float(v)
  except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def d(a,b,k):
  x=n((a or {}).get(k));y=n((b or {}).get(k));return x-y if x is not None and y is not None else None
def absd(a,b,k):
  z=d(a,b,k);return abs(z) if z is not None else None
def sum2(a,b,k):
  x=n((a or {}).get(k));y=n((b or {}).get(k));return x+y if x is not None and y is not None else None
def pf(a,out):
  a=n(a)
  if a is None or abs(a)<100:return None
  if out==.5:return 0.0
  if out==0:return -1.0
  return a/100 if a>0 else 100/abs(a)
def wilson(w,nn,z=1.96):
  if nn<=0:return None
  p=w/nn;den=1+z*z/nn
  return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr,price="median"):
  vals=[(r,pf(r.get(price),r["out"])) for r in rr];vals=[x for x in vals if x[1] is not None]
  dec=[r for r,_ in vals if r["out"]!=.5];w=sum(1 for r in dec if r["out"]==1);pr=sum(x for _,x in vals)
  return {"n":len(vals),"wins":w,"losses":len(dec)-w,"hit":w/len(dec) if dec else None,"roi":pr/len(vals) if vals else None,"profit":pr,"wilson_low":wilson(w,len(dec))}
def idx(name):return {(r.get("game_id"),r.get("team_id")):r for r in rgz(F/name)}

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
  for g in rgz(p):
    if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}
roll=idx("team_rolling.csv.gz");style=idx("team_style_rolling.csv.gz");stand=idx("standings_pregame.csv.gz");shape=idx("scoring_shape_rolling.csv.gz");core=idx("starter_core_pregame.csv.gz")

rows=[]
for gid,g in games.items():
  m=market.get(gid);h=str(g.get("home_team_id") or "");a=str(g.get("away_team_id") or "")
  if not m:continue
  hs=n(g.get("home_score"));aa=n(g.get("away_score"));line=n(m.get("closing_total_median"))
  if None in (hs,aa,line):continue
  hr,ar=roll.get((gid,h),{}),roll.get((gid,a),{});styh,stya=style.get((gid,h),{}),style.get((gid,a),{})
  sh,sa=stand.get((gid,h),{}),stand.get((gid,a),{});ch,ca=shape.get((gid,h),{}),shape.get((gid,a),{});coh,coa=core.get((gid,h),{}),core.get((gid,a),{})
  actual=hs+aa;out=1 if actual>line else (0 if actual<line else .5)
  hp=n(sh.get("prior_games"));ap=n(sa.get("prior_games"))
  rows.append({"season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"out":out,
    "median":n(m.get("over_price_median")),"best":n(m.get("over_price_best")),"worst":n(m.get("over_price_worst")),
    "total_line":line,"actual_total":actual,"total_margin":actual-line,
    "prior_games_min":min(hp,ap) if hp is not None and ap is not None else None,
    "forced_tov_sum":sum2(styh,stya,"forced_turnovers_last5_avg"),"rank_gap":absd(sh,sa,"conference_rank"),"net_gap":absd(hr,ar,"_net_rating_est_last5_avg"),
    "clutch_gap":absd(ch,ca,"clutch_margin_last10_avg"),"starterpm_gap":absd(coh,coa,"starter_plusminus5_avg")})

def qual(mid,spec=None):
  s=spec or SPECS[mid]
  if mid=="NBA_H3_OVER_001":
    return [r for r in rows if None not in (r["forced_tov_sum"],r["rank_gap"],r["net_gap"]) and r["forced_tov_sum"]<=s["forced_tov_max"] and r["rank_gap"]>=s["rank_min"] and r["net_gap"]>=s["net_min"]]
  return [r for r in rows if None not in (r["clutch_gap"],r["rank_gap"],r["starterpm_gap"]) and r["clutch_gap"]>=s["clutch_min"] and r["rank_gap"]>=s["rank_min"] and r["starterpm_gap"]>=s["starterpm_min"]]

# Existing live-arsenal ledger is used only to measure exposure overlap.
# It does not participate in H3 rule selection or threshold tuning.
live_game_methods={}
ledger=NBA/"research"/"bankroll"/"live_arsenal_50k_1pct_ledger.csv"
if ledger.exists():
  with open(ledger,"rt",encoding="utf-8",newline="") as f:
    for z in csv.DictReader(f):
      gid=str(z.get("game_id") or "")
      if not gid:continue
      s=live_game_methods.setdefault(gid,set())
      for mid in str(z.get("methods") or "").split("|"):
        if mid:s.add(mid)

def slice_metrics(rr,field,bands):
  out={}
  for label,lo,hi in bands:
    z=[r for r in rr if r.get(field) is not None and (lo is None or r[field]>=lo) and (hi is None or r[field]<hi)]
    out[label]=met(z)
  return out

def contrast(rr,field):
  w=[r.get(field) for r in rr if r["out"]==1 and r.get(field) is not None]
  l=[r.get(field) for r in rr if r["out"]==0 and r.get(field) is not None]
  return {
    "wins_n":len(w),"losses_n":len(l),
    "wins_mean":statistics.mean(w) if w else None,
    "losses_mean":statistics.mean(l) if l else None,
    "wins_median":statistics.median(w) if w else None,
    "losses_median":statistics.median(l) if l else None
  }

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"methods":{}}
for mid,spec in SPECS.items():
  rr=qual(mid)
  x={"rule":spec,"overall":met(rr),"price_stress":{p:met(rr,p) for p in ("best","median","worst")},
     "phases":{k:met([r for r in rr if r["season"] in ss]) for k,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))},
     "by_season":{s:met([r for r in rr if r["season"]==s]) for s in SEASONS},
     "leave_one_season_out":{s:met([r for r in rr if r["season"]!=s]) for s in SEASONS}}
  neigh=[]
  if mid=="NBA_H3_OVER_001":
    for ft in (24.5,25.8,27.0):
      for rk in (7,9,11):
        for ng in (10,12.958,15):
          z=qual(mid,{"forced_tov_max":ft,"rank_min":rk,"net_min":ng});neigh.append({"forced_tov_max":ft,"rank_min":rk,"net_min":ng,**met(z),"holdout":met([r for r in z if r["season"] in H])})
  else:
    for cl in (1.2,1.7,2.2):
      for rk in (6,8,10):
        for sp in (5.5,7.16,9):
          z=qual(mid,{"clutch_min":cl,"rank_min":rk,"starterpm_min":sp});neigh.append({"clutch_min":cl,"rank_min":rk,"starterpm_min":sp,**met(z),"holdout":met([r for r in z if r["season"] in H])})
  x["threshold_neighborhood"]=neigh
  if mid=="NBA_H3_OVER_002":
    gids={str(r["game_id"]) for r in rr}
    all_live_methods=sorted({m for ms in live_game_methods.values() for m in ms})
    per_method={}
    for lm in all_live_methods:
      lg={gid for gid,ms in live_game_methods.items() if lm in ms}
      inter=gids&lg
      union=gids|lg
      per_method[lm]={
        "shared_games":len(inter),
        "h3_share":len(inter)/len(gids) if gids else None,
        "jaccard":len(inter)/len(union) if union else None,
        "h3_performance_on_shared_games":met([r for r in rr if str(r["game_id"]) in inter])
      }
    overlap_counts={}
    for label,pred in [
      ("0",lambda k:k==0),("1",lambda k:k==1),("2",lambda k:k==2),("3_plus",lambda k:k>=3)
    ]:
      z=[r for r in rr if pred(len(live_game_methods.get(str(r["game_id"]),set())))]
      overlap_counts[label]=met(z)
    no_overlap=[r for r in rr if not live_game_methods.get(str(r["game_id"]),set())]
    any_overlap=[r for r in rr if live_game_methods.get(str(r["game_id"]),set())]
    style_overlap=[r for r in rr if "NBA_STYLE_TOT_001" in live_game_methods.get(str(r["game_id"]),set())]
    x["live_arsenal_overlap"]={
      "h3_games":len(gids),
      "games_with_any_live_method":len({str(r["game_id"]) for r in any_overlap}),
      "games_with_no_live_method":len({str(r["game_id"]) for r in no_overlap}),
      "independent_game_fraction":len(no_overlap)/len(rr) if rr else None,
      "performance_no_live_overlap":met(no_overlap),
      "performance_any_live_overlap":met(any_overlap),
      "same_market_style_tot_overlap":{"games":len(style_overlap),"performance":met(style_overlap)},
      "by_live_method":per_method,
      "by_live_method_count":overlap_counts
    }
    x["mechanism_audit"]={
      "primary_feature_win_loss_contrasts":{
        f:contrast(rr,f) for f in ("clutch_gap","rank_gap","starterpm_gap","prior_games_min","total_line")
      },
      "season_maturity":{
        f"min_prior_games_{k}":met([r for r in rr if r.get("prior_games_min") is not None and r["prior_games_min"]>=k])
        for k in (5,10,15,20,30)
      },
      "prior_game_buckets":slice_metrics(rr,"prior_games_min",[
        ("0_9",0,10),("10_19",10,20),("20_39",20,40),("40_plus",40,None)
      ]),
      "closing_total_buckets":slice_metrics(rr,"total_line",[
        ("under_215",None,215),("215_224_5",215,225),("225_234_5",225,235),("235_plus",235,None)
      ]),
      "clutch_strength":slice_metrics(rr,"clutch_gap",[
        ("1_7_2_49",1.7,2.5),("2_5_3_99",2.5,4.0),("4_plus",4.0,None)
      ]),
      "rank_gap_strength":slice_metrics(rr,"rank_gap",[
        ("8_9",8,10),("10_12",10,13),("13_plus",13,None)
      ]),
      "starter_pm_strength":slice_metrics(rr,"starterpm_gap",[
        ("7_16_9_99",7.16,10),("10_14_99",10,15),("15_plus",15,None)
      ])
    }
  x["threshold_neighborhood"]=neigh
  report["methods"][mid]=x
report["policy"]="Rules remain frozen. New H3_OVER_002 diagnostics measure overlap, data maturity, price stress, season stability and mechanism slices only; no 2024-26 thresholds are retuned."
(OUT/"hunt_v3_over_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
