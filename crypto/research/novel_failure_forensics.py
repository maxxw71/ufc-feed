#!/usr/bin/env python3
"""
Failure forensics for the novel crypto candidates that did NOT pass deep validation.

Purpose:
- explain what is different about losing/stopped trades,
- search simple train-only vetoes that might remove those failure states,
- freeze each veto before evaluating Hyperliquid holdout and Binance,
- never promote anything live.

Hyperliquid holdout filtered economics use the already-computed ACTUAL-funding
trade replay whenever available. Training selection uses the same modeled
+5/-7.5/5d contract used during discovery because the historical funding replay
was deliberately kept out of fitting.
"""
from __future__ import annotations
from pathlib import Path
from math import sqrt
import json
import numpy as np
import pandas as pd

ROOT=Path("crypto/research")
CTX=ROOT/"novel_context_discovery/hyperliquid_context_events.csv"
BIN=ROOT/"novel_context_discovery/binance_context_events.csv"
REG=ROOT/"novel_context_discovery/candidate_registry.json"
DEEP=ROOT/"results_novel_candidate_deep_validation/deep_validation.csv"
TRADES=ROOT/"results_novel_candidate_deep_validation/hyperliquid_actual_funding_trades.csv"
OUT=ROOT/"results_novel_failure_forensics"
OUT.mkdir(parents=True,exist_ok=True)

TP=.05
STOP=.075
HL_COST=2*(.00045+.0010)
BIN_COST=2*(.00070+.0010)

FEATURES=[
    "trigger_rsi","rsi_drop_pct","rsi_pct1","rsi_pct3","rsi_accel",
    "rsi_price_shock_ratio","daily_4h_rsi_gap","rsi_to_daily_ratio",
    "volume_ratio5","volume_ratio20","volume_z20","trades_ratio20",
    "dist_vwap20","obv_delta3_norm","cmf20","atr14_pct","range_ratio20",
    "lower_wick_pct_range","close_location",
    "dist_ema20","dist_ema9","dist_sma9","dist_sma20","dist_sma50",
    "sma9_slope3","sma20_slope3","rsi_vs_sma3","rsi_vs_sma5",
    "rsi_vs_sma9","rsi_vs_ema5","rsi_sma5_slope1","rsi_sma5_slope3",
    "dual_stretch_9","daily_ret5","daily_rv20","daily_rsi",
    "price_pct1","price_pct3","price_dd",
    "btc_ret4","btc_ret12","btc_ret24","btc_ret72","btc_rsi14",
    "btc_dist_ema20","btc_atr14_pct","market_flush_count_8h",
    "market_flush_count_24h","relative_ret4","relative_ret12"
]
CORE_LIVE_FEATURES={"rsi_pct1","rsi_accel","rsi_vs_sma3","close_location","volume_ratio20"}

def bval(x):
    if isinstance(x,bool):return x
    return str(x).strip().lower() in {"true","1","yes"}

def rate(s):
    q=s.dropna()
    return float(q.astype(bool).mean()) if len(q) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def apply_condition(df,c):
    f=c["feature"]
    if f not in df.columns:return pd.Series(False,index=df.index)
    x=pd.to_numeric(df[f],errors="coerce")
    a=float(c["a"]);bb=c.get("b")
    if c["op"]=="<=":return x<=a
    if c["op"]==">=":return x>=a
    if c["op"]=="band":return x.between(a,float(bb),inclusive="both")
    return pd.Series(False,index=df.index)

def apply_rule(df,conds):
    m=pd.Series(True,index=df.index)
    for c in conds:m &= apply_condition(df,c)
    return m

def modeled_roi(g,cost):
    if g.empty:return np.nan
    win=g["t50_before_s75_5d"].map(bval)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(g["close_ret_5d"],errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,TP)
    return float((gross-cost).mean())

def stats(g,cost):
    if g.empty:return {"n":0,"hit5":np.nan,"risk":np.nan,"roi":np.nan,"wilson":np.nan}
    hit=g["hit5_5d"].map(bval);risk=g["t50_before_s75_5d"].map(bval)
    return {"n":len(g),"hit5":rate(hit),"risk":rate(risk),"roi":modeled_roi(g,cost),
            "wilson":wilson(int(hit.sum()),len(g))}

def actual_holdout_stats(cid,g,trades):
    if g.empty:return {"n":0,"target_rate":np.nan,"net_roi":np.nan}
    keys=g[["api_coin","entry_time"]].copy()
    z=trades[trades["candidate_id"]==cid].copy()
    z["entry_time"]=pd.to_datetime(z["entry_time"],utc=True,errors="coerce")
    m=keys.merge(z[["api_coin","entry_time","exit_reason","net_return"]],on=["api_coin","entry_time"],how="left")
    ok=m[pd.to_numeric(m["net_return"],errors="coerce").notna()].copy()
    if ok.empty:return {"n":0,"target_rate":np.nan,"net_roi":np.nan}
    return {"n":len(ok),"target_rate":float((ok["exit_reason"]=="target").mean()),
            "net_roi":float(pd.to_numeric(ok["net_return"],errors="coerce").mean())}

def signature_rows(cid,label,g,condition_features):
    if g.empty:return []
    win=g[g["t50_before_s75_5d"].map(bval)]
    fail=g[~g["t50_before_s75_5d"].map(bval)]
    if len(win)<4 or len(fail)<2:return []
    rows=[]
    for f in FEATURES:
        if f not in g.columns or f in condition_features:continue
        a=pd.to_numeric(win[f],errors="coerce").dropna()
        b=pd.to_numeric(fail[f],errors="coerce").dropna()
        allv=pd.to_numeric(g[f],errors="coerce").dropna()
        if len(a)<3 or len(b)<2 or len(allv)<8:continue
        q25,q75=allv.quantile(.25),allv.quantile(.75)
        scale=float(q75-q25)
        if not np.isfinite(scale) or abs(scale)<1e-12:scale=float(allv.std())
        if not np.isfinite(scale) or abs(scale)<1e-12:continue
        diff=float((b.median()-a.median())/scale)
        rows.append({"candidate_id":cid,"split":label,"feature":f,
                     "winner_median":float(a.median()),"failure_median":float(b.median()),
                     "failure_minus_winner_iqr":diff,"winner_n":len(a),"failure_n":len(b),
                     "uses_current_live_core_feature":f in CORE_LIVE_FEATURES})
    return sorted(rows,key=lambda x:abs(x["failure_minus_winner_iqr"]),reverse=True)

def make_veto_specs(train,condition_features):
    out=[]
    for f in FEATURES:
        if f not in train.columns or f in condition_features:continue
        s=pd.to_numeric(train[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<18:continue
        for q in (.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80):
            v=float(s.quantile(q))
            out.append((f,"<=",v))
            out.append((f,">=",v))
    return out

def apply_veto(df,spec):
    f,op,v=spec
    x=pd.to_numeric(df[f],errors="coerce")
    return x<=v if op=="<=" else x>=v

def veto_search(cid,train,hold,binfull,trades,condition_features):
    base_tr=stats(train,HL_COST);base_ho=stats(hold,HL_COST);base_bin=stats(binfull,BIN_COST)
    rows=[]
    for spec in make_veto_specs(train,condition_features):
        gt=train[apply_veto(train,spec)]
        if len(gt)<max(15,int(len(train)*.60)):continue
        st=stats(gt,HL_COST)
        # Select only on Hyperliquid training. Require a meaningful improvement
        # in target-before-stop and no collapse in modeled ROI.
        if st["risk"] < base_tr["risk"]+.05:continue
        if st["roi"] < max(base_tr["roi"],.010):continue

        gh=hold[apply_veto(hold,spec)]
        f,op,v=spec
        binance_supported=f in binfull.columns
        gb=binfull[apply_veto(binfull,spec)] if binance_supported else binfull.iloc[0:0].copy()
        sh=stats(gh,HL_COST);sb=stats(gb,BIN_COST)
        ah=actual_holdout_stats(cid,gh,trades)
        rescued=(
            binance_supported
            and
            ah["n"]>=15 and ah["target_rate"]>=.72 and ah["net_roi"]>=.015
            and sb["n"]>=15 and sb["hit5"]>=.78 and sb["risk"]>=.68
            and sb["roi"]>=.010 and sb["wilson"]>=.58
        )
        rows.append({
            "candidate_id":cid,"feature":f,"op":op,"threshold":v,
            "uses_current_live_core_feature":f in CORE_LIVE_FEATURES,
            "binance_feature_supported":binance_supported,
            "train_base_n":base_tr["n"],"train_base_risk":base_tr["risk"],"train_base_roi":base_tr["roi"],
            "train_keep_n":st["n"],"train_keep_fraction":st["n"]/max(base_tr["n"],1),
            "train_risk":st["risk"],"train_roi":st["roi"],
            "hl_hold_base_n":base_ho["n"],"hl_hold_base_hit5":base_ho["hit5"],"hl_hold_base_risk":base_ho["risk"],
            "hl_hold_filtered_n":sh["n"],"hl_hold_filtered_hit5":sh["hit5"],"hl_hold_filtered_risk":sh["risk"],
            "hl_hold_actual_funding_n":ah["n"],"hl_hold_actual_target_rate":ah["target_rate"],
            "hl_hold_actual_funding_net_roi":ah["net_roi"],
            "binance_base_n":base_bin["n"],"binance_base_hit5":base_bin["hit5"],"binance_base_risk":base_bin["risk"],"binance_base_roi":base_bin["roi"],
            "binance_filtered_n":sb["n"],"binance_filtered_hit5":sb["hit5"],"binance_filtered_risk":sb["risk"],
            "binance_filtered_roi":sb["roi"],"binance_filtered_wilson":sb["wilson"],
            "rescued_full_gate":rescued,
            "research_score":(
                .30*(st["risk"]-base_tr["risk"])
                +.15*max(st["roi"]-base_tr["roi"],0)
                +.20*(0 if np.isnan(ah["target_rate"]) else ah["target_rate"])
                +.20*(0 if np.isnan(sb["risk"]) else sb["risk"])
                +.15*(0 if np.isnan(sb["roi"]) else min(max(sb["roi"]/.03,-1),1))
            )
        })
    z=pd.DataFrame(rows)
    return z.sort_values(["rescued_full_gate","research_score","binance_filtered_n"],ascending=[False,False,False]) if len(z) else z

def main():
    h=pd.read_csv(CTX);b=pd.read_csv(BIN)
    for df in (h,b):
        for c in ["trigger_time","entry_time"]:
            df[c]=pd.to_datetime(df[c],utc=True,errors="coerce")
    h=h.sort_values("entry_time").reset_index(drop=True)
    b=b.sort_values("entry_time").reset_index(drop=True)
    hcut=max(1,int(len(h)*.60))
    htr=h.iloc[:hcut].copy();hho=h.iloc[hcut:].copy()

    reg=json.loads(REG.read_text())
    deep=pd.read_csv(DEEP)
    deep_status=dict(zip(deep["candidate_id"],deep["status"]))
    trades=pd.read_csv(TRADES)
    trades["entry_time"]=pd.to_datetime(trades["entry_time"],utc=True,errors="coerce")

    signatures=[];vetos=[];summary=[]
    for cand in reg.get("candidates",[]):
        cid=cand["candidate_id"]
        if deep_status.get(cid)=="DEEP_VALIDATED_SHADOW":
            continue
        conds=cand["conditions"];cf={x["feature"] for x in conds}
        tr=htr[apply_rule(htr,conds)].copy()
        ho=hho[apply_rule(hho,conds)].copy()
        bf=b[apply_rule(b,conds)].copy()
        signatures += signature_rows(cid,"train",tr,cf)[:12]
        signatures += signature_rows(cid,"holdout",ho,cf)[:12]
        vz=veto_search(cid,tr,ho,bf,trades,cf)
        if len(vz):vetos.append(vz.head(12))
        base_actual=actual_holdout_stats(cid,ho,trades)
        sb=stats(bf,BIN_COST)
        summary.append({
            "candidate_id":cid,"rule":cand["rule"],"deep_status":deep_status.get(cid,"UNKNOWN"),
            "hl_train_n":len(tr),"hl_hold_n":len(ho),
            "hl_hold_actual_target_rate":base_actual["target_rate"],
            "hl_hold_actual_funding_net_roi":base_actual["net_roi"],
            "binance_full_n":sb["n"],"binance_full_hit5":sb["hit5"],
            "binance_full_risk":sb["risk"],"binance_full_roi":sb["roi"],
        })

    sig=pd.DataFrame(signatures)
    veto=pd.concat(vetos,ignore_index=True) if vetos else pd.DataFrame()
    summ=pd.DataFrame(summary)
    sig.to_csv(OUT/"winner_failure_feature_diffs.csv",index=False)
    veto.to_csv(OUT/"train_frozen_vetoes.csv",index=False)
    summ.to_csv(OUT/"failed_candidate_summary.csv",index=False)

    lines=["NOVEL CRYPTO FAILED-CANDIDATE FORENSICS","",
           "All veto thresholds below are selected on the earliest 60% Hyperliquid training rows only.",
           "Holdout and Binance are read only after the veto is frozen.",
           "A veto is not a new live rule even if it improves results; any rescue still needs fresh deep validation and forward shadowing.","",
           "FAILED CANDIDATE BASELINES",
           summ.to_string(index=False) if len(summ) else "none","",
           "TOP FAILURE SIGNATURES (positive = failure median is higher than winner median, IQR-normalized)",
           sig.groupby("candidate_id").head(6).to_string(index=False) if len(sig) else "none","",
           "TOP TRAIN-FROZEN VETOES",
           veto.groupby("candidate_id").head(5).to_string(index=False) if len(veto) else "none","",
           "FULL-GATE RESCUES",
           veto[veto["rescued_full_gate"]==True].to_string(index=False) if len(veto) and "rescued_full_gate" in veto else "none"]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
