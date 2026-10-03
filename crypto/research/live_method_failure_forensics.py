#!/usr/bin/env python3
"""
Failure forensics for live crypto methods C1/C2/C3/C3F.

Question: what is different about trades that fail to reach +5%, and can an
ex-ante filter reduce those failures without destroying sample size?

Methodology:
- reconstruct each method's exact historical qualifying set
- chronological 60/40 train/holdout
- inspect winner vs non-winner feature distributions
- choose veto thresholds on TRAIN ONLY
- apply unchanged threshold to HOLDOUT
- require meaningful retained sample; never promote automatically
"""
from __future__ import annotations

from pathlib import Path
import itertools,json
import numpy as np
import pandas as pd

ROOT=Path("crypto/research")
OUT=ROOT/"results_live_method_failure_forensics"
OUT.mkdir(parents=True,exist_ok=True)

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def qnum(s):
    x=pd.to_numeric(s,errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
    if not len(x):return {"n":0}
    return {
        "n":int(len(x)),
        "mean":float(x.mean()),
        "median":float(x.median()),
        "q25":float(x.quantile(.25)),
        "q75":float(x.quantile(.75)),
    }

def split60(e):
    e=e.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(e)*.60))
    return e.iloc[:cut].copy(),e.iloc[cut:].copy()

def feature_diff_table(method,ho,features):
    rows=[]
    wins=ho[ho["hit5"].astype(bool)]
    fails=ho[~ho["hit5"].astype(bool)]
    for f in features:
        if f not in ho.columns:continue
        a=qnum(wins[f]);b=qnum(fails[f])
        if a.get("n",0)<3 or b.get("n",0)<2:continue
        rows.append({
            "method":method,"feature":f,
            "winner_n":a["n"],"winner_median":a["median"],"winner_q25":a["q25"],"winner_q75":a["q75"],
            "failure_n":b["n"],"failure_median":b["median"],"failure_q25":b["q25"],"failure_q75":b["q75"],
            "median_gap_failure_minus_winner":b["median"]-a["median"],
        })
    return rows

def mask(df,spec):
    f,op,v=spec
    x=pd.to_numeric(df[f],errors="coerce").replace([np.inf,-np.inf],np.nan)
    return x>=v if op==">=" else x<=v

def name(spec):
    return f"{spec[0]} {spec[1]} {spec[2]:.6g}"

def discover_vetoes(method,tr,ho,features,min_train=20,min_hold=8,min_retain=.50):
    base_tr=rate(tr["hit5"]);base_ho=rate(ho["hit5"])
    base_tr_r=rate(tr["risk_ok"]);base_ho_r=rate(ho["risk_ok"])
    specs=[]
    for f in features:
        if f not in tr.columns or f not in ho.columns:continue
        s=pd.to_numeric(tr[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<max(30,min_train):continue
        for q in [.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85]:
            v=float(s.quantile(q))
            specs.append((f,">=",v))
            specs.append((f,"<=",v))

    singles=[]
    for sp in specs:
        gt=tr[mask(tr,sp)]
        if len(gt)<max(min_train,int(len(tr)*min_retain)):continue
        train_gain=rate(gt["hit5"])-base_tr
        train_risk_gain=rate(gt["risk_ok"])-base_tr_r
        if train_gain<.025 and train_risk_gain<.035:continue
        singles.append((.65*rate(gt["hit5"])+.35*rate(gt["risk_ok"]),len(gt),sp))
    singles.sort(key=lambda x:(x[0],x[1]),reverse=True)
    top=[x[2] for x in singles[:24]]

    combos=[(s,) for s in top]
    combos += [(a,b) for a,b in itertools.combinations(top,2) if a[0]!=b[0]]

    rows=[];seen=set()
    for combo in combos:
        nm=" AND ".join(name(x) for x in combo)
        if nm in seen:continue
        seen.add(nm)
        mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
        for sp in combo:
            mt&=mask(tr,sp);mh&=mask(ho,sp)
        gt,gh=tr[mt],ho[mh]
        if len(gt)<max(min_train,int(len(tr)*min_retain)) or len(gh)<min_hold:continue
        tr_hit=rate(gt["hit5"]);ho_hit=rate(gh["hit5"])
        tr_r=rate(gt["risk_ok"]);ho_r=rate(gh["risk_ok"])
        row={
            "method":method,"rule":nm,
            "train_n":len(gt),"train_retained":len(gt)/len(tr),
            "train_hit5":tr_hit,"train_hit5_gain":tr_hit-base_tr,
            "train_risk":tr_r,"train_risk_gain":tr_r-base_tr_r,
            "hold_n":len(gh),"hold_retained":len(gh)/len(ho),
            "hold_hit5":ho_hit,"hold_hit5_gain":ho_hit-base_ho,
            "hold_risk":ho_r,"hold_risk_gain":ho_r-base_ho_r,
            "hold_failures":int((~gh["hit5"].astype(bool)).sum()),
        }
        # Useful candidate: improves holdout hit rate, does not worsen risk,
        # and retains at least half of the holdout unless sample is tiny.
        row["passes_holdout"]=bool(
            row["hold_retained"]>=.45
            and row["hold_hit5_gain"]>=.02
            and row["hold_risk_gain"]>=-.01
        )
        row["score"]=.55*row["hold_hit5"]+.30*row["hold_risk"]+.15*row["hold_retained"]
        rows.append(row)
    return rows

def load_c1():
    e=pd.read_csv(ROOT/"results_method1_nolookahead_hyperliquid/events.csv")
    for c in ["entry_time","trigger_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    e=e[(e["rsi_accel"]<=-11.72020)&(e["rsi_pct1"]<=-0.18174)].copy()
    e["risk_ok"]=e["t5_s0.075"].astype(bool)
    feats=["daily_ret5","daily_rv20","daily_rsi","price_dd","trigger_rsi","rsi_pct1","rsi_pct3","rsi_accel",
           "dist_ema9","dist_sma9","dist_sma20","lower_wick","close_location","volume_ratio20","range_ratio20"]
    return e,feats

def load_c2():
    e=pd.read_csv(ROOT/"results_lower_high_hyperliquid_replication/events.csv")
    for c in ["entry_time","trigger_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    e=e[e["C_lowerhigh_bigpump"].astype(bool)].copy()
    e["risk_ok"]=e["t5_s0.075"].astype(bool)
    feats=["trigger_rsi","dist_sma50","dist_ema9","sma50_slope6","lower_wick","pump_pct","lower_high_pct","second_dump_pct"]
    return e,feats

def load_c3(funding=False):
    e=pd.read_csv(ROOT/"results_c3_funding_enrichment/events.csv")
    for c in ["entry_time","trigger_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    if funding:
        e=e[e["funding_rate_at_trigger"]>=0.000013].copy()
    e["risk_ok"]=e["t5_s0.075"].astype(bool)
    feats=["daily_ret5","daily_rv20","daily_rsi","price_dd","trigger_rsi","rsi_pct1","rsi_pct3","rsi_accel",
           "dist_ema9","dist_sma9","dist_sma20","lower_wick","close_location","volume_ratio20","range_ratio20",
           "funding_rate_at_trigger","premium_at_trigger"]
    return e,feats

def method_report(name,e,features):
    tr,ho=split60(e)
    # C3/C3F file carries original global order, so preserve original global
    # split when available rather than re-splitting the filtered subset.
    if "global_order" in e.columns:
        global_cut=int(np.floor((e["global_order"].max()+1)*.60))
        tr=e[e["global_order"]<global_cut].copy()
        ho=e[e["global_order"]>=global_cut].copy()
    base={
        "method":name,"all_n":len(e),"train_n":len(tr),"hold_n":len(ho),
        "hold_hit5":rate(ho["hit5"]),"hold_risk":rate(ho["risk_ok"]),
        "hold_failures":int((~ho["hit5"].astype(bool)).sum()),
    }
    diffs=feature_diff_table(name,ho,features)
    vetoes=discover_vetoes(name,tr,ho,features,min_train=max(12,int(len(tr)*.25)),min_hold=max(6,int(len(ho)*.15)),min_retain=.45)
    return base,diffs,vetoes,tr,ho

def main():
    loaders=[("C1",load_c1),("C2",load_c2),("C3",lambda:load_c3(False)),("C3F",lambda:load_c3(True))]
    summaries=[];all_diffs=[];all_vetoes=[];failure_rows=[]
    for name_,loader in loaders:
        e,features=loader()
        base,diffs,vetoes,tr,ho=method_report(name_,e,features)
        summaries.append(base);all_diffs.extend(diffs);all_vetoes.extend(vetoes)
        f=ho[~ho["hit5"].astype(bool)].copy()
        f.insert(0,"method",name_)
        cols=["method"]+[c for c in ["coin","trigger_time","entry_time","entry","mfe5d","mae5d","funding_rate_at_trigger","premium_at_trigger"] if c in f.columns]
        failure_rows.append(f[cols])

    s=pd.DataFrame(summaries)
    d=pd.DataFrame(all_diffs)
    v=pd.DataFrame(all_vetoes)
    if len(v):
        v=v.sort_values(["passes_holdout","score","hold_n"],ascending=[False,False,False])
    failures=pd.concat(failure_rows,ignore_index=True) if failure_rows else pd.DataFrame()
    s.to_csv(OUT/"method_failure_summary.csv",index=False)
    d.to_csv(OUT/"winner_vs_failure_features.csv",index=False)
    v.to_csv(OUT/"train_frozen_vetoes.csv",index=False)
    failures.to_csv(OUT/"holdout_failures.csv",index=False)

    lines=["LIVE CRYPTO METHOD FAILURE FORENSICS",""]
    for r in summaries:
        lines.append(f"{r['method']}: holdout n={r['hold_n']} +5={r['hold_hit5']*100:.2f}% risk={r['hold_risk']*100:.2f}% failures={r['hold_failures']}")
    lines += ["","BEST TRAIN-SELECTED VETOES THAT ALSO IMPROVED HOLDOUT"]
    good=v[v["passes_holdout"]] if len(v) else pd.DataFrame()
    lines.append(good.head(30).to_string(index=False) if len(good) else "No robust veto passed the holdout gate.")
    lines += ["","LARGEST WINNER/FAILURE MEDIAN DIFFERENCES BY METHOD"]
    if len(d):
        chunks=[]
        for m,g in d.groupby("method"):
            q=g.assign(abs_gap=g["median_gap_failure_minus_winner"].abs()).sort_values("abs_gap",ascending=False).head(8)
            chunks.append(m+"\n"+q.to_string(index=False))
        lines.append("\n\n".join(chunks))
    else:
        lines.append("none")
    lines += ["","Guardrail: these filters are research candidates only. No live method was changed by this audit."]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
