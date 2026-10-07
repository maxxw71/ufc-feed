#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(".")
OUT=ROOT/"dwcs/research/interaction_robustness_v2"
OUT.mkdir(parents=True,exist_ok=True)

SEARCH_PATH=ROOT/"dwcs/research/run_interaction_search_v2.py"
spec=importlib.util.spec_from_file_location("dwcs_interactions",SEARCH_PATH)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def wilson(w,n,z=1.96):
    if n<=0:return (np.nan,np.nan)
    p=w/n
    den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return center-half,center+half

def ordered_risk(q):
    if q.empty:return 0,0.0
    q=q.sort_values(["event_date","fight_id"]).copy()
    q["profit100"]=[m.american_profit(o,w) for o,w in zip(q.close_odds,q.won)]
    streak=mx=0
    equity=peak=0.0
    maxdd=0.0
    for _,x in q.iterrows():
        if bool(x.won): streak=0
        else:
            streak+=1
            mx=max(mx,streak)
        equity+=float(x.profit100)/100.0
        peak=max(peak,equity)
        maxdd=max(maxdd,peak-equity)
    return mx,maxdd

def bootstrap_roi(q,seed=20261007,nboot=5000):
    if q.empty:return (np.nan,np.nan)
    profits=np.array([m.american_profit(o,w)/100.0 for o,w in zip(q.close_odds,q.won)],dtype=float)
    rng=np.random.default_rng(seed)
    vals=np.empty(nboot)
    for i in range(nboot):
        vals[i]=rng.choice(profits,size=len(profits),replace=True).mean()
    return float(np.quantile(vals,.025)),float(np.quantile(vals,.975))

def mechanism_signature(rule):
    parts=[]
    for raw in rule.split("&"):
        x=raw.strip()
        if x.startswith("dog_") or x.startswith("fav_") or x.startswith("not_worse_than"):
            parts.append("price")
        else:
            parts.append(re.split(r">=|<=|=",x,maxsplit=1)[0].strip())
    return "|".join(sorted(parts))

def evaluate_rule(d,rule):
    conds,anchor=m.parse_rule(rule)
    return m.signal_rows(d,conds,anchor)

def main():
    master=pd.read_csv(ROOT/"dwcs/frozen/dwcs_historical_master_frozen.csv",low_memory=False)
    valraw=master[pd.to_numeric(master.season,errors="coerce").isin([7,8,9])].copy()
    fullraw=master[pd.to_numeric(master.season,errors="coerce").isin(range(1,10))].copy()
    val=m.make_long(valraw)
    full=m.make_long(fullraw)

    vr=pd.read_csv(ROOT/"dwcs/research/interaction_search_v2/locked_validation_results.csv",low_memory=False)
    passed=vr[vr.validation_status.eq("passed_locked_validation")].copy()
    allr=pd.read_csv(ROOT/"dwcs/research/interaction_search_v2/discovery_all_interactions.csv",low_memory=False)

    summaries=[]
    pick_frames=[]
    for i,x in passed.reset_index(drop=True).iterrows():
        qv=evaluate_rule(val,x.rule)
        qf=evaluate_rule(full,x.rule)
        sv=m.summarize(qv)
        sf=m.summarize(qf)
        lo,hi=wilson(int(sv["wins"]),int(sv["n"]))
        blo,bhi=bootstrap_roi(qv,seed=20261007+i)
        ls,dd=ordered_risk(qv)
        summaries.append({
            "rule":x.rule,"lane":x.lane,
            "discovery_n":int(x.discovery_n),"discovery_win_rate":x.discovery_win_rate,"discovery_roi":x.discovery_roi,
            "validation_n":int(sv["n"]),"validation_wins":int(sv["wins"]),
            "validation_win_rate":sv["win_rate"],"validation_roi":sv["roi"],
            "validation_wilson_low":lo,"validation_wilson_high":hi,
            "validation_bootstrap_roi_low":blo,"validation_bootstrap_roi_high":bhi,
            "validation_profitable_seasons":sv["profitable_seasons"],"validation_seasons":sv["seasons"],
            "validation_worst_season_roi":sv["worst_season_roi"],
            "max_losing_streak":ls,"max_drawdown_units":dd,
            "full_n":int(sf["n"]),"full_wins":int(sf["wins"]),
            "full_win_rate":sf["win_rate"],"full_roi":sf["roi"],
        })
        if len(qv):
            z=qv[["fight_id","season","event_date","fighter","opponent","close_odds","won"]].copy()
            z["rule"]=x.rule
            pick_frames.append(z)

    summary=pd.DataFrame(summaries)
    summary.to_csv(OUT/"robustness_summary.csv",index=False)

    # Validation overlap: exact selected fighter on the same fight.
    picks=pd.concat(pick_frames,ignore_index=True) if pick_frames else pd.DataFrame()
    picks.to_csv(OUT/"validation_picks.csv",index=False)
    rules=summary.rule.tolist()
    overlap=[]
    for a in rules:
        sa=set(zip(picks.loc[picks.rule.eq(a),"fight_id"],picks.loc[picks.rule.eq(a),"fighter"]))
        row={"rule":a}
        for b in rules:
            sb=set(zip(picks.loc[picks.rule.eq(b),"fight_id"],picks.loc[picks.rule.eq(b),"fighter"]))
            u=len(sa|sb); inter=len(sa&sb)
            row[b]=inter/u if u else np.nan
        overlap.append(row)
    pd.DataFrame(overlap).to_csv(OUT/"validation_overlap_jaccard.csv",index=False)

    # Unique-bet portfolio and conflict audit.
    portfolio={}
    conflicts={}
    for _,x in picks.iterrows():
        key=(int(x.fight_id),str(x.fighter))
        portfolio.setdefault(key,[]).append(x.rule)
        conflicts.setdefault(int(x.fight_id),set()).add(str(x.fighter))
    unique_rows=[]
    for (fid,fighter),rr in portfolio.items():
        x=picks[(picks.fight_id.eq(fid)) & (picks.fighter.eq(fighter))].iloc[0]
        unique_rows.append({
            "fight_id":fid,"season":x.season,"event_date":x.event_date,
            "fighter":fighter,"opponent":x.opponent,"close_odds":x.close_odds,
            "won":x.won,"method_count":len(rr),"rules":" || ".join(sorted(rr))
        })
    up=pd.DataFrame(unique_rows)
    up.to_csv(OUT/"validation_unique_bet_portfolio.csv",index=False)
    net=0.0
    if len(up):
        net=sum(m.american_profit(o,w)/100.0 for o,w in zip(up.close_odds,up.won))
    psummary={
        "passed_methods":int(len(summary)),
        "method_signals":int(len(picks)),
        "unique_bets":int(len(up)),
        "unique_bet_wins":int(up.won.sum()) if len(up) else 0,
        "unique_bet_win_rate":float(up.won.mean()) if len(up) else None,
        "net_units_at_1u_per_unique_bet":float(net),
        "roi_per_unique_bet":float(net/len(up)) if len(up) else None,
        "max_method_overlap_same_pick":int(up.method_count.max()) if len(up) else 0,
        "fights_with_conflicting_picks_across_passed_methods":int(sum(len(v)>1 for v in conflicts.values())),
    }
    (OUT/"portfolio_summary.json").write_text(json.dumps(psummary,indent=2)+"\n")

    # Threshold-neighborhood stress test. These variants were all generated using
    # S1-6 only; S7-9 is used here strictly as a diagnostic, not to retune thresholds.
    allr["mechanism_signature"]=allr.rule.map(mechanism_signature)
    neighborhood=[]
    for _,x in summary.iterrows():
        sig=mechanism_signature(x.rule)
        g=allr[allr.mechanism_signature.eq(sig)].copy()
        for _,n in g.iterrows():
            q=evaluate_rule(val,n.rule)
            if len(q)<5:continue
            z=m.summarize(q)
            neighborhood.append({
                "parent_passed_rule":x.rule,
                "mechanism_signature":sig,
                "neighbor_rule":n.rule,
                "is_exact_parent":n.rule==x.rule,
                "discovery_n":n.n,"discovery_win_rate":n.win_rate,"discovery_roi":n.roi,
                "validation_n":z["n"],"validation_win_rate":z["win_rate"],"validation_roi":z["roi"],
                "validation_profitable_seasons":z["profitable_seasons"],"validation_seasons":z["seasons"],
            })
    nd=pd.DataFrame(neighborhood)
    nd.to_csv(OUT/"threshold_neighborhood_diagnostics.csv",index=False)

    stability=[]
    if len(nd):
        for parent,g in nd.groupby("parent_passed_rule"):
            stability.append({
                "rule":parent,
                "neighbor_variants_n":len(g),
                "positive_roi_neighbors":int(g.validation_roi.gt(0).sum()),
                "positive_roi_neighbor_rate":float(g.validation_roi.gt(0).mean()),
                "neighbors_win60plus":int(g.validation_win_rate.ge(.60).sum()),
                "win60plus_neighbor_rate":float(g.validation_win_rate.ge(.60).mean()),
                "median_neighbor_roi":float(g.validation_roi.median()),
                "median_neighbor_win_rate":float(g.validation_win_rate.median()),
            })
    stab=pd.DataFrame(stability)
    stab.to_csv(OUT/"neighborhood_stability.csv",index=False)

    # Conservative research tiers. Promotion is intentionally not automatic.
    z=summary.merge(stab,on="rule",how="left") if len(stab) else summary.copy()
    z["robustness_tier"]="research_only"
    strong=(
        z.validation_n.ge(10)
        & z.validation_roi.gt(0)
        & z.validation_win_rate.ge(.65)
        & z.validation_profitable_seasons.ge(2)
        & z.positive_roi_neighbor_rate.fillna(0).ge(.60)
    )
    z.loc[strong,"robustness_tier"]="strong_research_candidate"
    priority=(
        strong
        & z.validation_win_rate.ge(.70)
        & z.validation_roi.ge(.20)
        & z.validation_bootstrap_roi_low.gt(0)
    )
    z.loc[priority,"robustness_tier"]="priority_shadow_candidate"
    z.to_csv(OUT/"robustness_tiers.csv",index=False)

    lines=[
        "DWCS INTERACTION ROBUSTNESS V2",
        "="*116,
        "No Season 10 data used. No thresholds retuned on S7-9.",
        f"Passed interaction rules entering robustness: {len(summary)}",
        f"Validation portfolio: {json.dumps(psummary)}",
        "",
        "ROBUSTNESS RANKING",
        "-"*116,
    ]
    tier_order={"priority_shadow_candidate":0,"strong_research_candidate":1,"research_only":2}
    z["_tier_order"]=z.robustness_tier.map(tier_order).fillna(9)
    order=z.sort_values(
        ["_tier_order","validation_roi","validation_win_rate"],
        ascending=[True,False,False]
    )
    for _,x in order.iterrows():
        lines.append(
            f"{x.robustness_tier:<24} VAL n={int(x.validation_n):>3} win={x.validation_win_rate*100:5.1f}% "
            f"ROI={x.validation_roi*100:+7.1f}% bootROI=[{x.validation_bootstrap_roi_low*100:+6.1f},{x.validation_bootstrap_roi_high*100:+6.1f}] "
            f"neighbors+={x.get('positive_roi_neighbor_rate',np.nan)*100:5.1f}% | {x.rule}"
        )
    (OUT/"report.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))

if __name__=="__main__":
    main()
