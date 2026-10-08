#!/usr/bin/env python3
import contextlib,io,json,runpy
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"research"/"hunt_v3";OUT.mkdir(parents=True,exist_ok=True)

buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    ns=runpy.run_path(str(NBA/"research"/"audit_h3_over_losses.py"))
rows=ns["extra"];met=ns["met"];A=ns["A"];B=ns["B"];V=ns["V"];H=ns["H"]

SPECS={
 "NBA_H3_OVER_002_TS":{
   "feature":"ts_sum","op":">=","cut":1.1490513453597666,
   "neighborhood":[1.135,1.142,1.1490513453597666,1.156,1.163]
 },
 "NBA_H3_OVER_002_TRAVEL":{
   "feature":"travel7d_abs_gap","op":">=","cut":1450.7229856559238,
   "neighborhood":[900,1200,1450.7229856559238,1700,2000]
 }
}
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]

def keep(r,sp,cut=None):
    x=r.get(sp["feature"]);c=sp["cut"] if cut is None else cut
    if x is None:return False
    return x>=c if sp["op"]==">=" else x<=c

def phase(rr,ss,price="median"):
    return met([r for r in rr if r["season"] in ss],price)

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"descendants":{},
        "policy":"Descendant thresholds originate only from the H3_OVER_002 2018-21 discovery loss-forensics pass. This audit does not retune from validation or holdout. Promotion requires season, threshold-neighborhood and worst-price robustness."}
for mid,sp in SPECS.items():
    child=[r for r in rows if keep(r,sp)]
    by_season={s:met([r for r in child if r["season"]==s]) for s in SEASONS}
    hold=[r for r in child if r["season"] in H]
    x={
      "parent_method":"NBA_H3_OVER_002","feature":sp["feature"],"op":sp["op"],"cut":sp["cut"],
      "overall":met(child),
      "phases":{k:phase(child,ss) for k,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))},
      "by_season":by_season,
      "leave_one_season_out":{s:met([r for r in child if r["season"]!=s]) for s in SEASONS},
      "price_stress":{
        "best":met(child,"best"),"median":met(child),"worst":met(child,"worst"),
        "holdout_best":met(hold,"best"),"holdout_median":met(hold),"holdout_worst":met(hold,"worst")
      }
    }
    neigh=[]
    for cut in sp["neighborhood"]:
        z=[r for r in rows if keep(r,sp,cut)]
        neigh.append({"cut":cut,**met(z),
          "discA":phase(z,A),"discB":phase(z,B),"validation":phase(z,V),"holdout":phase(z,H),
          "worst":met(z,"worst"),"holdout_worst":met([r for r in z if r["season"] in H],"worst")})
    x["threshold_neighborhood"]=neigh
    robust=[z for z in neigh if all((z[k].get("roi") if isinstance(z[k],dict) else None) is not None and z[k]["roi"]>0 for k in ("discB","validation","holdout","worst","holdout_worst")) and (z.get("roi") or -999)>0]
    season_eval=[v for v in by_season.values() if (v.get("n") or 0)>=5]
    x["promotion_checks"]={
      "overall_n_at_least_150":(x["overall"].get("n") or 0)>=150,
      "holdout_n_at_least_40":(x["phases"]["holdout"].get("n") or 0)>=40,
      "all_seasons_with_5plus_signals_positive_roi":bool(season_eval) and all((v.get("roi") or -999)>0 for v in season_eval),
      "worst_price_positive_overall":(x["price_stress"]["worst"].get("roi") or -999)>0,
      "worst_price_positive_holdout":(x["price_stress"]["holdout_worst"].get("roi") or -999)>0,
      "robust_threshold_neighborhood_fraction":len(robust)/len(neigh) if neigh else None,
      "threshold_neighborhood_at_least_80pct_robust":bool(neigh) and len(robust)/len(neigh)>=0.8
    }
    report["descendants"][mid]=x

(OUT/"h3_over_descendant_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
