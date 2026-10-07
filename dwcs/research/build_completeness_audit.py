#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT=Path(".")
OUT=ROOT/"dwcs/research/completeness_audit"
OUT.mkdir(parents=True,exist_ok=True)

def jload(path):
    p=ROOT/path
    if not p.exists():
        return {}
    txt=p.read_text().strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        obj,_=json.JSONDecoder().raw_decode(txt)
        return obj

def pct(a,b):
    return float(a/b) if b else 0.0

def main():
    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    n=len(hist)

    age=jload("dwcs/research/age_enrichment/coverage.json")
    phys=jload("dwcs/research/enrichment_probe/coverage.json")
    static_phys=jload("dwcs/research/static_profile_merge/status.json")

    odds=(
        jload("dwcs/research/historical_odds/alias_recovery_status.json")
        or jload("dwcs/research/historical_odds/event_recovery_status.json")
        or jload("dwcs/research/historical_odds/retry_status.json")
        or jload("dwcs/research/historical_odds/status.json")
    )

    regional_base=jload("dwcs/research/regional_history/coverage.json")
    regional_v2=jload("dwcs/research/regional_history_v2/status.json")
    recovery_v1=jload("dwcs/research/regional_history_recovery/status.json")
    recovery_v2=jload("dwcs/research/regional_identity_recovery_v2/status.json")

    sos=jload("dwcs/research/point_in_time_sos/status.json")
    tech=jload("dwcs/research/historical_technical/status.json")
    offhist=jload("dwcs/research/official_ufc_history/coverage.json")
    ufc_verified=jload("dwcs/research/ufc_com_verified/status.json")

    unique_fighters=phys.get("unique_dwcs_fighters",702)

    reg_after=regional_v2.get(
        "rows_with_prior_history",
        recovery_v2.get(
            "estimated_matched_after",
            recovery_v1.get("matched_after_estimate",regional_base.get("rows_with_prior_history",0)),
        ),
    )
    reg_rows=regional_v2.get(
        "fighter_fight_rows",
        recovery_v2.get("base_rows",recovery_v1.get("rows",regional_base.get("dwcs_fighter_fight_rows",0))),
    )

    height_known=static_phys.get("height_known",phys.get("height_known",0))
    reach_known=static_phys.get("reach_known",phys.get("reach_known",0))
    priced=odds.get("total_priced_after",odds.get("unique_priced_fights",0))

    rows=[
      ("historical_fights","core",n,n,1.0,"complete historical S1-9 archive"),
      ("both_ages_known","identity",age.get("fights_both_ages_known",0),n,pct(age.get("fights_both_ages_known",0),n),"point-in-time age"),
      ("fighter_profile_match","identity",phys.get("matched_profiles",0),unique_fighters,phys.get("profile_match_rate",0),"identity/profile matching"),
      ("height_known","physical",height_known,unique_fighters,pct(height_known,unique_fighters),"height after verified UFC.com static fills"),
      ("reach_known","physical",reach_known,unique_fighters,pct(reach_known,unique_fighters),"reach after verified UFC.com static fills"),
      ("stance_known","physical",phys.get("stance_known",0),unique_fighters,pct(phys.get("stance_known",0),unique_fighters),"fighter-level stance"),
      ("historical_odds","market",priced,n,pct(priced,n),"priced historical fights"),
      ("regional_history","career",reg_after,reg_rows,pct(reg_after,reg_rows),"pre-DWCS regional history after event/opponent identity recovery"),
      ("global_elo_sos","career",sos.get("rows_with_global_history",0),sos.get("fighter_fight_rows",0),sos.get("coverage",0),"point-in-time global Elo/SOS"),
      ("dwcs_fight_technical_rows","technical",tech.get("fighter_fight_total_rows",0),2*n,pct(tech.get("fighter_fight_total_rows",0),2*n),"historical DWCS fight technical totals"),
      ("prior_dwcs_technical","technical",tech.get("snapshots_with_prior_technical_history",0),2*n,pct(tech.get("snapshots_with_prior_technical_history",0),2*n),"pre-fight technical history for repeat entrants"),
      ("prior_ufcstats_history","technical",offhist.get("rows_with_prior_ufcstats_history",0),2*n,pct(offhist.get("rows_with_prior_ufcstats_history",0),2*n),"strict-before-date UFCStats history"),
      ("ufc_com_profiles_verified","official_profile",ufc_verified.get("verified_profiles",0),633,pct(ufc_verified.get("verified_profiles",0),633),"verified UFC.com profiles; current dynamic metrics quarantined")
    ]

    cov=pd.DataFrame(rows,columns=["metric","category","covered","denominator","coverage","notes"])
    cov.to_csv(OUT/"coverage_matrix.csv",index=False)

    weights={
      "historical_fights":.15,
      "both_ages_known":.10,
      "fighter_profile_match":.10,
      "height_known":.04,
      "reach_known":.04,
      "stance_known":.02,
      "historical_odds":.18,
      "regional_history":.15,
      "global_elo_sos":.10,
      "dwcs_fight_technical_rows":.08,
      "prior_dwcs_technical":.02,
      "prior_ufcstats_history":.02,
    }
    weighted=0.0
    for k,w in weights.items():
        z=cov[cov.metric.eq(k)]
        if len(z):
            weighted += w*float(z.iloc[0].coverage)
    score=10*weighted/sum(weights.values())

    targets={
      "historical_odds":.90,
      "regional_history":.90,
      "global_elo_sos":.90,
      "reach_known":.90,
      "height_known":.95,
      "both_ages_known":.97,
      "fighter_profile_match":.98,
      "stance_known":.98,
      "dwcs_fight_technical_rows":.98,
    }
    gaps=[]
    for _,x in cov.iterrows():
        target=targets.get(x.metric)
        if target is not None and x.coverage<target:
            gaps.append({
              "metric":x.metric,
              "coverage":x.coverage,
              "target":target,
              "gap_pp":100*(target-x.coverage),
              "priority":"high" if x.metric in {"historical_odds","regional_history","global_elo_sos"} else "medium",
            })
    pd.DataFrame(gaps).to_csv(OUT/"remaining_gaps.csv",index=False)

    regional_cov=pct(reg_after,reg_rows)
    odds_cov=pct(priced,n)
    sos_cov=float(sos.get("coverage",0) or 0)

    freeze={
      "dataset_readiness_score_10":round(score,3),
      "historical_method_discovery_ready":bool(score>=8.5 and odds_cov>=.75 and regional_cov>=.75),
      "mature_9_of_10_target_met":bool(score>=9.0 and odds_cov>=.90 and regional_cov>=.90 and sos_cov>=.90),
      "quality_freeze_recommended":bool(score>=8.9 and odds_cov>=.95 and regional_cov>=.82 and sos_cov>=.82),
      "historical_data_leakage_guard":True,
      "current_ufc_com_aggregates_allowed_in_historical_backtests":False,
      "historical_technical_features_strict_before_fight_date":True,
      "frozen_split":"S1-6 discovery; S7-9 locked validation; S10 prospective_locked",
      "notes":"Current UFC.com dynamic aggregates are prospective/current only. Historical methods use only point-in-time or static-safe values."
    }
    (OUT/"readiness.json").write_text(json.dumps(freeze,indent=2)+"\n")

    lines=[
      "DWCS DATASET COMPLETENESS AUDIT",
      "="*96,
      f"Readiness score: {score:.2f}/10",
      f"Historical method discovery ready: {freeze['historical_method_discovery_ready']}",
      f"Mature 9/10 target met: {freeze['mature_9_of_10_target_met']}",
      f"Quality freeze recommended: {freeze['quality_freeze_recommended']}",
      "",
      "COVERAGE",
      "-"*96,
    ]
    for _,x in cov.iterrows():
        lines.append(f"{x.metric:<30} {int(x.covered):>5}/{int(x.denominator):<5} {x.coverage*100:6.1f}%  {x.notes}")

    lines += ["","REMAINING TARGET GAPS","-"*96]
    if gaps:
        for g in gaps:
            lines.append(f"{g['priority'].upper():<6} {g['metric']:<28} {g['coverage']*100:5.1f}% -> {g['target']*100:5.1f}%")
    else:
        lines.append("No configured target gaps.")

    (OUT/"report.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))

if __name__=="__main__":
    main()
