#!/usr/bin/env python3
"""
Cross-venue validation for automatically discovered Hyperliquid candidates.

Applies frozen Hyperliquid candidate thresholds to Binance research events
without retuning. Updates candidate status but never promotes a rule live.
"""
from pathlib import Path
from math import sqrt
import json
import numpy as np
import pandas as pd

REG=Path("crypto/research/auto_discovery/candidate_registry.json")
BIN=Path("crypto/research/results_rsi_dynamics/events.csv")
OUT=Path("crypto/research/auto_discovery")
OUT.mkdir(parents=True,exist_ok=True)

TP=0.05
STOP=0.075
BINANCE_FEE_SIDE=0.00070
SLIP_SIDE=0.0010
BINANCE_RT_COST=2*(BINANCE_FEE_SIDE+SLIP_SIDE)
MIN_BINANCE_NET_ROI=0.010

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def wilson_lower(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def net_roi_target_stop_timeout(df):
    if df.empty:return np.nan
    win=df["t50_before_s75_5d"].fillna(False).astype(bool)
    mae=pd.to_numeric(df["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(
        df["close_ret_5d"] if "close_ret_5d" in df.columns else pd.Series(0.0,index=df.index),
        errors="coerce"
    ).fillna(0.0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,timeout)),index=df.index)
    gross=gross.clip(lower=-STOP,upper=TP)
    return float((gross-BINANCE_RT_COST).mean())

def apply_condition(df,c):
    f=c["feature"];op=c["op"];a=c["a"];b=c.get("b")
    if f not in df.columns:return pd.Series(False,index=df.index)
    x=pd.to_numeric(df[f],errors="coerce").replace([np.inf,-np.inf],np.nan)
    if op=="<=":return x<=float(a)
    if op==">=":return x>=float(a)
    if op=="band":return x.between(float(a),float(b),inclusive="both")
    return pd.Series(False,index=df.index)

def main():
    if not REG.exists() or not BIN.exists():
        raise SystemExit("Missing registry or Binance event table")

    reg=json.loads(REG.read_text())
    e=pd.read_csv(BIN)
    for c in ["arm_time","trigger_time","entry_time"]:
        if c in e.columns:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    if "flush_dd_threshold" in e.columns:
        e=e[np.isclose(pd.to_numeric(e["flush_dd_threshold"],errors="coerce"),0.08)].copy()
    e=e.sort_values("entry_time").reset_index(drop=True)
    # Keep latest 40% as the independent Binance confirmation set too.
    cut=max(1,int(len(e)*.60))
    hold=e.iloc[cut:].copy()

    rows=[]
    for cand in reg.get("candidates",[]):
        m=pd.Series(True,index=hold.index)
        unsupported=[]
        for cond in cand.get("conditions",[]):
            if cond["feature"] not in hold.columns:
                unsupported.append(cond["feature"])
                m &= False
            else:
                m &= apply_condition(hold,cond)
        g=hold[m]
        n=len(g)
        hit=rate(g["hit5_5d"]) if n and "hit5_5d" in g else np.nan
        risk=rate(g["t50_before_s75_5d"]) if n and "t50_before_s75_5d" in g else np.nan
        hit10=rate(g["hit10_5d"]) if n and "hit10_5d" in g else np.nan
        wins=int(g["hit5_5d"].fillna(False).astype(bool).sum()) if n else 0
        lo=wilson_lower(wins,n)
        roi=net_roi_target_stop_timeout(g) if n else np.nan

        passed=(
            not unsupported
            and n>=15
            and hit>=.78
            and risk>=.68
            and roi>=MIN_BINANCE_NET_ROI
            and lo>=.58
        )
        cand["cross_venue_validation"]={
            "venue":"Binance USDT spot",
            "split":"latest 40%",
            "n":n,
            "hit5":None if pd.isna(hit) else float(hit),
            "hit10":None if pd.isna(hit10) else float(hit10),
            "t5_before_s7p5":None if pd.isna(risk) else float(risk),
            "net_roi":None if pd.isna(roi) else float(roi),
            "wilson_lower":None if pd.isna(lo) else float(lo),
            "unsupported_features":unsupported,
            "passed":bool(passed),
        }
        cand["status"]="CROSS_VALIDATED_SHADOW" if passed else "SHADOW_ONLY"
        rows.append({
            "candidate_id":cand["candidate_id"],
            "status":cand["status"],
            "rule":cand["rule"],
            "binance_n":n,
            "binance_hit5":hit,
            "binance_hit10":hit10,
            "binance_t5_s7p5":risk,
            "binance_net_roi":roi,
            "binance_wilson_lower":lo,
            "unsupported_features":"+".join(unsupported),
        })

    reg["cross_venue_updated_at"]=pd.Timestamp.utcnow().isoformat()
    REG.write_text(json.dumps(reg,indent=2))
    z=pd.DataFrame(rows)
    z.to_csv(OUT/"crossvenue_validation.csv",index=False)

    passed=z[z["status"]=="CROSS_VALIDATED_SHADOW"] if len(z) else pd.DataFrame()
    lines=[
        "AUTO DISCOVERY — BINANCE CROSS-VENUE VALIDATION",
        "",
        f"Binance 8% first-flush events: {len(e)}",
        f"Independent Binance holdout events: {len(hold)}",
        f"Candidates checked: {len(z)}",
        f"Cross-validated shadow candidates: {len(passed)}",
        f"Binance net ROI gate: >= {MIN_BINANCE_NET_ROI*100:.2f}%/trade under the same +5/-7.5/5d contract",
        "",
        passed.to_string(index=False) if len(passed) else "No candidate passed cross-venue gates today.",
        "",
        "No candidate is auto-promoted live. CROSS_VALIDATED_SHADOW still requires forward shadow performance and manual review."
    ]
    (OUT/"CROSSVENUE_REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"CROSSVENUE_REPORT.txt").read_text())

if __name__=="__main__":
    main()
