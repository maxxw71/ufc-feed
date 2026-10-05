#!/usr/bin/env python3
"""
Convergence / overlap analysis for novel crypto methods versus the current live arsenal.

Focus:
- N02 and N06 deep-validated shadow methods.
- R01/R03 secondary refined shadows.
- Current live methods C1/C2/C2D/C3/C3F/C4.

The analysis uses the untouched later-40% Hyperliquid first-flush panel for
N02/N06/R01/R03 and exact same-coin + same-entry-time matches to C2/C2D.
Actual-funding net returns and exact exit ordering come from the completed
novel deep replay, so the convergence ROI is not a synthetic target-rate proxy.

No threshold fitting and no live promotion.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT=Path("crypto/research")
CTX=ROOT/"novel_context_discovery/hyperliquid_context_events.csv"
LOW=ROOT/"results_lower_high_hyperliquid_replication/events.csv"
C3F=ROOT/"results_c3_funding_enrichment/events.csv"
NOVEL_TRADES=ROOT/"results_novel_candidate_deep_validation/hyperliquid_actual_funding_trades.csv"
OUT=ROOT/"results_novel_convergence"
OUT.mkdir(parents=True,exist_ok=True)

LIVE=["C1","C2","C2D","C3","C3F","C4"]
NOVELS=["N02","N06","R01","R03"]
PARENT={"N02":"NOVEL-20261005-02","N06":"NOVEL-20261005-06","R01":"NOVEL-20261005-01","R03":"NOVEL-20261005-03"}

def bval(x):
    if isinstance(x,bool):return x
    return str(x).strip().lower() in {"true","1","yes"}

def rate(s):
    q=s.dropna()
    return float(q.astype(bool).mean()) if len(q) else np.nan

def prep():
    h=pd.read_csv(CTX)
    for c in ["trigger_time","entry_time"]:
        h[c]=pd.to_datetime(h[c],utc=True,errors="coerce")
    h=h.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(h)*.60))
    h=h.iloc[cut:].copy().reset_index(drop=True)

    # Current live first-flush methods.
    h["C1"]=(pd.to_numeric(h["rsi_pct1"],errors="coerce")<=-0.18174)&(pd.to_numeric(h["rsi_accel"],errors="coerce")<=-11.7202)
    h["C3"]=(pd.to_numeric(h["close_location"],errors="coerce")<=0.1527)&(pd.to_numeric(h["volume_ratio20"],errors="coerce")>=1.8296)
    h["C4"]=(pd.to_numeric(h["rsi_accel"],errors="coerce")<=-9.01822)&(pd.to_numeric(h["rsi_vs_sma3"],errors="coerce")<=-0.120961)

    # Deep-validated novel shadows.
    h["N02"]=(
        pd.to_numeric(h["relative_ret4"],errors="coerce").between(-0.05382078204024199,-0.03050418191478533,inclusive="both")
        &pd.to_numeric(h["obv_delta3_norm"],errors="coerce").between(-0.11293352517433576,-0.035453733606862416,inclusive="both")
    )
    h["N06"]=(
        pd.to_numeric(h["price_dd"],errors="coerce").between(-0.1394393733853019,-0.10419042571418308,inclusive="both")
        &pd.to_numeric(h["daily_ret5"],errors="coerce").between(0.31396113303210554,0.5795319768388887,inclusive="both")
    )

    # Secondary train-frozen research refinements.
    h["R01"]=(
        pd.to_numeric(h["daily_4h_rsi_gap"],errors="coerce").between(14.564335396607099,22.840074974386972,inclusive="both")
        &pd.to_numeric(h["relative_ret4"],errors="coerce").between(-0.048556911108327216,-0.02494468062678926,inclusive="both")
        &(pd.to_numeric(h["btc_rsi14"],errors="coerce")>=45.84034715519137)
    )
    h["R03"]=(
        pd.to_numeric(h["lower_wick_pct_range"],errors="coerce").between(0.04160912056190943,0.11986433193646617,inclusive="both")
        &pd.to_numeric(h["obv_delta3_norm"],errors="coerce").between(-0.1342119207492753,-0.045244761407937324,inclusive="both")
        &(pd.to_numeric(h["btc_atr14_pct"],errors="coerce")>=0.01032640865130135)
    )

    # C3F literal live threshold.
    f=pd.read_csv(C3F)
    for c in ["trigger_time","entry_time"]:
        f[c]=pd.to_datetime(f[c],utc=True,errors="coerce")
    fk=set(
        (str(r.coin),pd.Timestamp(r.trigger_time))
        for r in f.itertuples()
        if pd.notna(r.funding_rate_at_trigger) and float(r.funding_rate_at_trigger)>=0.000013
    )
    h["C3F"]=[(str(c),pd.Timestamp(t)) in fk for c,t in zip(h["display_name"],h["trigger_time"])]

    # C2/C2D exact coincidences with the first-flush event entry.
    q=pd.read_csv(LOW)
    q["entry_time"]=pd.to_datetime(q["entry_time"],utc=True,errors="coerce")
    c2=q[q["C_lowerhigh_bigpump"].map(bval)].sort_values("entry_time").reset_index(drop=True)
    c2h=c2.iloc[max(1,int(len(c2)*.60)):].copy()
    c2keys=set((str(r.coin),pd.Timestamp(r.entry_time)) for r in c2h.itertuples())
    c2d=c2[pd.to_numeric(c2["dist_ema9"],errors="coerce")<=-0.069254].sort_values("entry_time").reset_index(drop=True)
    c2dh=c2d.iloc[max(1,int(len(c2d)*.60)):].copy()
    c2dkeys=set((str(r.coin),pd.Timestamp(r.entry_time)) for r in c2dh.itertuples())
    h["C2"]=[(str(c),pd.Timestamp(t)) in c2keys for c,t in zip(h["display_name"],h["entry_time"])]
    h["C2D"]=[(str(c),pd.Timestamp(t)) in c2dkeys for c,t in zip(h["display_name"],h["entry_time"])]

    # Actual-funding replay for the four novel definitions. R01/R03 inherit the
    # parent candidate replay because they are strict subsets of those parents.
    tr=pd.read_csv(NOVEL_TRADES)
    tr["entry_time"]=pd.to_datetime(tr["entry_time"],utc=True,errors="coerce")
    tr["trigger_time"]=pd.to_datetime(tr["trigger_time"],utc=True,errors="coerce")
    maps={}
    for n,parent in PARENT.items():
        g=tr[tr["candidate_id"]==parent].copy()
        maps[n]={
            (str(r.api_coin),pd.Timestamp(r.entry_time)):{
                "net_return":float(r.net_return) if pd.notna(r.net_return) else np.nan,
                "target":str(r.exit_reason)=="target",
                "stop":str(r.exit_reason)=="stop",
            } for r in g.itertuples()
        }

    return h,maps

def subset_stats(h,n,mask,maps):
    g=h[mask].copy()
    mp=maps[n]
    vals=[];tgt=[];stp=[]
    for r in g.itertuples():
        q=mp.get((str(r.api_coin),pd.Timestamp(r.entry_time)))
        if q is None:continue
        if np.isfinite(q["net_return"]):vals.append(q["net_return"])
        tgt.append(q["target"]);stp.append(q["stop"])
    return {
        "n":len(g),
        "actual_replayed_n":len(vals),
        "target_rate":float(np.mean(tgt)) if tgt else np.nan,
        "stop_rate":float(np.mean(stp)) if stp else np.nan,
        "actual_funding_net_roi":float(np.mean(vals)) if vals else np.nan,
        "pnl_per_10000":float(np.mean(vals)*10000) if vals else np.nan,
        "hit5_5d":rate(g["hit5_5d"].map(bval)) if len(g) else np.nan,
        "target_before_stop_raw":rate(g["t50_before_s75_5d"].map(bval)) if len(g) else np.nan,
    }

def main():
    h,maps=prep()
    rows=[];coverage=[]
    for n in NOVELS:
        nm=h[n].astype(bool)
        base=subset_stats(h,n,nm,maps)
        any_live=h[LIVE].any(axis=1)
        unique=subset_stats(h,n,nm&(~any_live),maps)
        coverage.append({
            "novel_method":n,
            "n":base["n"],
            "unique_vs_all_live_n":unique["n"],
            "unique_fraction":unique["n"]/base["n"] if base["n"] else np.nan,
            "unique_target_rate":unique["target_rate"],
            "unique_actual_funding_net_roi":unique["actual_funding_net_roi"],
            "overlap_any_live_n":int((nm&any_live).sum()),
            "overlap_any_live_fraction":float((nm&any_live).sum()/base["n"]) if base["n"] else np.nan,
        })
        for live in LIVE:
            om=nm & h[live].astype(bool)
            om_stats=subset_stats(h,n,om,maps)
            no_stats=subset_stats(h,n,nm & (~h[live].astype(bool)),maps)
            rows.append({
                "novel_method":n,"live_method":live,
                "novel_n":base["n"],
                "novel_target_rate":base["target_rate"],
                "novel_actual_funding_net_roi":base["actual_funding_net_roi"],
                "overlap_n":om_stats["n"],
                "overlap_fraction_of_novel":om_stats["n"]/base["n"] if base["n"] else np.nan,
                "overlap_target_rate":om_stats["target_rate"],
                "overlap_actual_funding_net_roi":om_stats["actual_funding_net_roi"],
                "overlap_pnl_per_10000":om_stats["pnl_per_10000"],
                "nonoverlap_n":no_stats["n"],
                "nonoverlap_target_rate":no_stats["target_rate"],
                "nonoverlap_actual_funding_net_roi":no_stats["actual_funding_net_roi"],
                "target_rate_lift":om_stats["target_rate"]-base["target_rate"] if np.isfinite(om_stats["target_rate"]) and np.isfinite(base["target_rate"]) else np.nan,
                "roi_lift":om_stats["actual_funding_net_roi"]-base["actual_funding_net_roi"] if np.isfinite(om_stats["actual_funding_net_roi"]) and np.isfinite(base["actual_funding_net_roi"]) else np.nan,
            })

    z=pd.DataFrame(rows)
    # A convergence boost remains research-only and requires at least 8 exact
    # overlaps so a 2/2 or 3/3 coincidence cannot look important.
    z["boost_candidate"]=(
        (z["overlap_n"]>=8)
        &(z["overlap_target_rate"]>=0.80)
        &(z["overlap_actual_funding_net_roi"]>=0.02)
        &((z["target_rate_lift"]>=0.05)|(z["roi_lift"]>=0.005))
    )
    z=z.sort_values(["boost_candidate","overlap_actual_funding_net_roi","overlap_n"],ascending=[False,False,False])
    z.to_csv(OUT/"novel_live_overlap.csv",index=False)
    pd.DataFrame(coverage).to_csv(OUT/"incremental_coverage.csv",index=False)

    # N02/N06 same-event convergence.
    both=h["N02"].astype(bool)&h["N06"].astype(bool)
    n02n06=subset_stats(h,"N02",both,maps)
    pair=pd.DataFrame([{"pair":"N02+N06",**n02n06}])
    pair.to_csv(OUT/"novel_pair_convergence.csv",index=False)

    boosts=z[z["boost_candidate"]==True]
    lines=[
        "NOVEL METHOD CONVERGENCE / INCREMENTAL-COVERAGE ANALYSIS","",
        "Uses later-40% Hyperliquid holdout only. No fitting is performed here.",
        "Actual-funding net ROI comes from the completed deep replay for each novel parent candidate.",
        "C2/C2D convergence requires exact same coin + same next-4H entry time; first-flush live methods use exact same event row.","",
        "INCREMENTAL COVERAGE",
        pd.DataFrame(coverage).to_string(index=False),"",
        "N02/N06 SAME-EVENT CONVERGENCE",
        pair.to_string(index=False),"",
        "NOVEL x LIVE OVERLAPS",
        z.to_string(index=False),"",
        "CONVERGENCE BOOST CANDIDATES (N>=8, target>=80%, ROI>=2%, plus meaningful lift)",
        boosts.to_string(index=False) if len(boosts) else "none","",
        "Research only. No position-size boost has been activated."
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
