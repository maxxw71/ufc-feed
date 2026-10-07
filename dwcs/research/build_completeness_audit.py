#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd, numpy as np

ROOT=Path(".")
OUT=ROOT/"dwcs/research/completeness_audit"
OUT.mkdir(parents=True,exist_ok=True)

def jload(path):
    p=ROOT/path
    if not p.exists(): return {}
    txt=p.read_text().strip()\n    try: return json.loads(txt)\n    except json.JSONDecodeError:\n        obj,_=json.JSONDecoder().raw_decode(txt)\n        return obj

def pct(a,b):
    return float(a/b) if b else 0.0

def main():
    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    n=len(hist)

    age=jload("dwcs/research/age_enrichment/coverage.json")
    phys=jload("dwcs/research/enrichment_probe/coverage.json")
    odds=jload("dwcs/research/historical_odds/retry_status.json") or jload("dwcs/research/historical_odds/status.json")
    regional=jload("dwcs/research/regional_history/coverage.json")
    recovery=jload("dwcs/research/regional_history_recovery/status.json")
    sos=jload("dwcs/research/point_in_time_sos/status.json")
    tech=jload("dwcs/research/historical_technical/status.json")
    offhist=jload("dwcs/research/official_ufc_history/coverage.json")
    ufccom=jload("dwcs/research/ufc_com_search_enrichment/status.json")

    reg_after=recovery.get("matched_after_estimate",regional.get("rows_with_prior_history",0))
    reg_rows=recovery.get("rows",regional.get("dwcs_fighter_fight_rows",0))
    priced=odds.get("total_priced_after",odds.get("unique_priced_fights",0))

    rows=[
      ("historical_fights","core",n,n,1.0,"complete historical S1-9 archive"),
      ("both_ages_known","identity",age.get("fights_both_ages_known",0),n,pct(age.get("fights_both_ages_known",0),n),"point-in-time age"),
      ("fighter_profile_match","identity",phys.get("matched_profiles",0),phys.get("unique_dwcs_fighters",0),phys.get("profile_match_rate",0),"identity/profile matching"),
      ("height_known","physical",phys.get("height_known",0),phys.get("unique_dwcs_fighters",0),pct(phys.get("height_known",0),phys.get("unique_dwcs_fighters",0)),"fighter-level height"),
      ("reach_known","physical",phys.get("reach_known",0),phys.get("unique_dwcs_fighters",0),pct(phys.get("reach_known",0),phys.get("unique_dwcs_fighters",0)),"fighter-level reach"),
      ("stance_known","physical",phys.get("stance_known",0),phys.get("unique_dwcs_fighters",0),pct(phys.get("stance_known",0),phys.get("unique_dwcs_fighters",0)),"fighter-level stance"),
      ("historical_odds","market",priced,n,pct(priced,n),"priced historical fights"),
      ("regional_history","career",reg_after,reg_rows,pct(reg_after,reg_rows),"pre-DWCS regional history after conservative recovery"),
      ("global_elo_sos","career",sos.get("rows_with_global_history",0),sos.get("fighter_fight_rows",0),sos.get("coverage",0),"point-in-time global Elo/SOS"),
      ("dwcs_fight_technical_rows","technical",tech.get("fighter_fight_total_rows",0),2*n,pct(tech.get("fighter_fight_total_rows",0),2*n),"historical DWCS fight technical totals"),
      ("prior_dwcs_technical","technical",tech.get("snapshots_with_prior_technical_history",0),2*n,pct(tech.get("snapshots_with_prior_technical_history",0),2*n),"pre-fight technical history for repeat entrants"),
      ("prior_ufcstats_history","technical",offhist.get("rows_with_prior_ufcstats_history",0),2*n,pct(offhist.get("rows_with_prior_ufcstats_history",0),2*n),"strict-before-date UFCStats history"),
      ("ufc_com_profiles_resolved","official_profile",ufccom.get("ufc_search_resolved",0),ufccom.get("dwcs_unique_fighters",633),pct(ufccom.get("ufc_search_resolved",0),ufccom.get("dwcs_unique_fighters",633)),"official UFC.com search-resolved profiles; current-only metrics")
    ]
    cov=pd.DataFrame(rows,columns=["metric","category","covered","denominator","coverage","notes"])
    cov.to_csv(OUT/"coverage_matrix.csv",index=False)

    # Weighted readiness: structural categories only; current UFC.com profile coverage is not required
    # for historical validity because current aggregates are quarantined.
    weights={
      "historical_fights":.15,"both_ages_known":.10,"fighter_profile_match":.10,
      "height_known":.04,"reach_known":.04,"stance_known":.02,
      "historical_odds":.18,"regional_history":.15,"global_elo_sos":.10,
      "dwcs_fight_technical_rows":.08,"prior_dwcs_technical":.02,"prior_ufcstats_history":.02
    }
    weighted=0.0
    for k,w in weights.items():
        z=cov[cov.metric.eq(k)]
        if len(z): weighted += w*float(z.iloc[0].coverage)
    score=10*weighted/sum(weights.values())

    gaps=[]
    for _,x in cov.iterrows():
        target={
          "historical_odds":.90,"regional_history":.90,"global_elo_sos":.90,
          "reach_known":.90,"height_known":.95,"both_ages_known":.97,
          "fighter_profile_match":.98,"stance_known":.98,
          "dwcs_fight_technical_rows":.98
        }.get(x.metric)
        if target is not None and x.coverage<target:
            gaps.append({"metric":x.metric,"coverage":x.coverage,"target":target,
                         "gap_pp":100*(target-x.coverage),"priority":"high" if x.metric in {"historical_odds","regional_history","global_elo_sos"} else "medium"})
    gapsdf=pd.DataFrame(gaps)
    gapsdf.to_csv(OUT/"remaining_gaps.csv",index=False)

    freeze={
      "dataset_readiness_score_10":round(score,3),
      "historical_method_discovery_ready":bool(score>=8.5 and pct(priced,n)>=.75 and pct(reg_after,reg_rows)>=.75),
      "mature_freeze_target_met":bool(score>=9.0 and pct(priced,n)>=.90 and pct(reg_after,reg_rows)>=.90),
      "historical_data_leakage_guard":True,
      "current_ufc_com_aggregates_allowed_in_historical_backtests":False,
      "historical_technical_features_strict_before_fight_date":True,
      "notes":"Current UFC.com aggregates are prospective/current metadata only. Historical methods use only values available before each fight date."
    }
    (OUT/"readiness.json").write_text(json.dumps(freeze,indent=2)+"\n")

    lines=[
      "DWCS DATASET COMPLETENESS AUDIT",
      "="*96,
      f"Readiness score: {score:.2f}/10",
      f"Historical method discovery ready: {freeze['historical_method_discovery_ready']}",
      f"Mature 9/10-style freeze target met: {freeze['mature_freeze_target_met']}",
      "",
      "COVERAGE",
      "-"*96
    ]
    for _,x in cov.iterrows():
        lines.append(f"{x.metric:<30} {int(x.covered):>5}/{int(x.denominator):<5} {x.coverage*100:6.1f}%  {x.notes}")
    lines+=["","REMAINING TARGET GAPS","-"*96]
    if gaps:
        for g in gaps:
            lines.append(f"{g['priority'].upper():<6} {g['metric']:<28} {g['coverage']*100:5.1f}% -> {g['target']*100:5.1f}%")
    else:
        lines.append("No configured target gaps.")
    (OUT/"report.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))

if __name__=="__main__":main()
