#!/usr/bin/env python3
import contextlib,io,json,runpy
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"research"/"live_loss_lab";OUT.mkdir(parents=True,exist_ok=True)
buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    ns=runpy.run_path(str(NBA/"research"/"audit_live_arsenal_losses.py"))
methods=ns["methods"];met=ns["met"];A=ns["A"];B=ns["B"];V=ns["V"];H=ns["H"];SEASONS=ns["SEASONS"]
market=ns["market"];pf=ns["pf"];pftotal=ns["pftotal"];n=ns["n"]

specs={
 "PHYSCOACH_OREB":{"parent":"NBA_PHYSCOACH_001","feature":"oreb5_gap","op":"<=","cut":-0.00869616350911806,"neighborhood":[-0.02,-0.012,-0.00869616350911806,-0.005,0.0]},
 "INTERACT_LQVOL":{"parent":"NBA_INTERACT_003","feature":"lq_volatility_adv","op":"<=","cut":-5.4103434372065635,"neighborhood":[-10,-7.5,-5.4103434372065635,-3,0]},
 "STAND_STARTPM":{"parent":"NBA_STAND_002","feature":"starter_pm5_gap","op":">=","cut":0.92,"neighborhood":[0,0.5,0.92,1.5,3]},
 "LQ_FOUL":{"parent":"NBA_LQ_003","feature":"foulrate5_adv","op":"<=","cut":-1.2742150116302042,"neighborhood":[-2,-1.5,-1.2742150116302042,-0.75,0]},
 "ROTSHAPE_FTOV":{"parent":"NBA_ROTSHAPE_001","feature":"forced_tov5_gap","op":">=","cut":1.6,"neighborhood":[0.5,1,1.6,2,2.5]},
 "TRAVEL_CLUTCH":{"parent":"NBA_TRAVEL_001","feature":"clutch10_gap","op":">=","cut":1.3,"neighborhood":[0.5,1,1.3,1.5,2]},
 "STYLE_TRAVEL":{"parent":"NBA_STYLE_TOT_001","feature":"travel7d_sum","op":">=","cut":5392.399386990663,"neighborhood":[4000,5000,5392.399386990663,6000,7000]}
}
def keep(r,sp,cut=None):
    c=sp["cut"] if cut is None else cut
    x=r.get(sp["feature"])
    if x is None:return False
    try:x=float(x)
    except:return False
    return x<=c if sp["op"]=="<=" else x>=c
def phase(rr,ss,total):return met([r for r in rr if r["season"] in ss],total)

def quoted_price(r,total,which):
    m=market.get(r.get("game_id"),{})
    if total:
        return n(m.get(f"over_price_{which}"))
    home=float(r.get("home") or 0)>=0.5
    return n(m.get(("home_moneyline_" if home else "away_moneyline_")+which))

def met_price(rr,total,which):
    vals=[]
    for r in rr:
        price=quoted_price(r,total,which)
        p=pftotal(price,r.get("result")) if total else pf(price,r.get("won"))
        if p is not None:vals.append((r,p))
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"profit":0}
    if total:
        dec=[r for r,_ in vals if r.get("result")!=0.5]
        wins=sum(1 for r in dec if r.get("result")==1)
    else:
        dec=[r for r,_ in vals]
        wins=sum(1 for r in dec if r.get("won"))
    profit=sum(p for _,p in vals)
    return {"n":len(vals),"wins":wins,"losses":len(dec)-wins,
            "hit":wins/len(dec) if dec else None,
            "roi":profit/len(vals) if vals else None,"profit":profit}

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"descendants":{},
        "policy":"These are candidate descendants only. Parent live rules remain frozen. Filters are not promoted unless robustness is stable across phases, seasons, threshold neighborhoods and executable best/median/worst historical price stress."}
for name,sp in specs.items():
    rr=methods.get(sp["parent"],[]);total=(sp["parent"]=="NBA_STYLE_TOT_001")
    child=[r for r in rr if keep(r,sp)]
    parent={"overall":met(rr,total),"phases":{"discA":phase(rr,A,total),"discB":phase(rr,B,total),"validation":phase(rr,V,total),"holdout":phase(rr,H,total)}}
    child_by_season={s:met([r for r in child if r["season"]==s],total) for s in SEASONS}
    child_hold=[r for r in child if r["season"] in H]
    x={"parent_method":sp["parent"],"feature":sp["feature"],"op":sp["op"],"cut":sp["cut"],
       "parent":parent,
       "child":{"overall":met(child,total),
                "phases":{"discA":phase(child,A,total),"discB":phase(child,B,total),"validation":phase(child,V,total),"holdout":phase(child,H,total)},
                "by_season":child_by_season,
                "leave_one_season_out":{s:met([r for r in child if r["season"]!=s],total) for s in SEASONS},
                "price_stress":{
                  "best":met_price(child,total,"best"),
                  "median":met(child,total),
                  "worst":met_price(child,total,"worst"),
                  "holdout_best":met_price(child_hold,total,"best"),
                  "holdout_median":met(child_hold,total),
                  "holdout_worst":met_price(child_hold,total,"worst")
                }}}
    neigh=[]
    for cut in sp["neighborhood"]:
        z=[r for r in rr if keep(r,sp,cut)]
        neigh.append({"cut":cut,**met(z,total),
                      "discA":phase(z,A,total),"discB":phase(z,B,total),"validation":phase(z,V,total),"holdout":phase(z,H,total)})
    x["threshold_neighborhood"]=neigh
    robust=[z for z in neigh if (z.get("roi") or -999)>0 and ((z.get("holdout") or {}).get("roi") or -999)>0]
    season_eval=[v for v in child_by_season.values() if (v.get("n") or 0)>=5]
    worst=x["child"]["price_stress"]["worst"]
    hworst=x["child"]["price_stress"]["holdout_worst"]
    x["promotion_checks"]={
      "overall_n_at_least_100":(x["child"]["overall"].get("n") or 0)>=100,
      "holdout_n_at_least_25":(x["child"]["phases"]["holdout"].get("n") or 0)>=25,
      "all_seasons_with_5plus_signals_positive_roi":bool(season_eval) and all((v.get("roi") or -999)>0 for v in season_eval),
      "worst_price_positive_overall":(worst.get("roi") or -999)>0,
      "worst_price_positive_holdout":(hworst.get("roi") or -999)>0,
      "positive_threshold_neighborhood_fraction":len(robust)/len(neigh) if neigh else None,
      "threshold_neighborhood_at_least_80pct_positive":bool(neigh) and len(robust)/len(neigh)>=0.8
    }
    report["descendants"][name]=x

(OUT/"loss_descendant_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"parent":v["parent"]["overall"],"child":v["child"]["overall"],"holdout":v["child"]["phases"]["holdout"],"by_season":v["child"]["by_season"]} for k,v in report["descendants"].items()},indent=2))
