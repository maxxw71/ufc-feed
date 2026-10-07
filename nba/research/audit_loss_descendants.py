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

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"descendants":{},
        "policy":"These are candidate descendants only. Parent live rules remain frozen. Filters are not promoted unless robustness is stable across phases, seasons and threshold neighborhoods."}
for name,sp in specs.items():
    rr=methods.get(sp["parent"],[]);total=(sp["parent"]=="NBA_STYLE_TOT_001")
    child=[r for r in rr if keep(r,sp)]
    parent={"overall":met(rr,total),"phases":{"discA":phase(rr,A,total),"discB":phase(rr,B,total),"validation":phase(rr,V,total),"holdout":phase(rr,H,total)}}
    x={"parent_method":sp["parent"],"feature":sp["feature"],"op":sp["op"],"cut":sp["cut"],
       "parent":parent,
       "child":{"overall":met(child,total),
                "phases":{"discA":phase(child,A,total),"discB":phase(child,B,total),"validation":phase(child,V,total),"holdout":phase(child,H,total)},
                "by_season":{s:met([r for r in child if r["season"]==s],total) for s in SEASONS},
                "leave_one_season_out":{s:met([r for r in child if r["season"]!=s],total) for s in SEASONS}}}
    neigh=[]
    for cut in sp["neighborhood"]:
        z=[r for r in rr if keep(r,sp,cut)]
        neigh.append({"cut":cut,**met(z,total),
                      "discA":phase(z,A,total),"discB":phase(z,B,total),"validation":phase(z,V,total),"holdout":phase(z,H,total)})
    x["threshold_neighborhood"]=neigh
    report["descendants"][name]=x

(OUT/"loss_descendant_robustness.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"parent":v["parent"]["overall"],"child":v["child"]["overall"],"holdout":v["child"]["phases"]["holdout"],"by_season":v["child"]["by_season"]} for k,v in report["descendants"].items()},indent=2))
