#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np

SRC=Path("crypto/research/results_method1_blowoff_shape/events_with_shape.csv")
OUT=Path("crypto/research/results_method1_peak_rsi_gate")
OUT.mkdir(parents=True,exist_ok=True)

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time","peak_time"]:
    if c in e.columns:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
e=e.sort_values("entry_time").reset_index(drop=True)

# Corrected C1 base rule selected before this audit.
base=e[(e["rsi_pct1"]<=-0.18174)&(e["rsi_accel"]<=-11.72020)].copy()
base["rsi_peak_to_trigger_drop_pct"]=base["trigger_rsi"]/base["peak_rsi"]-1
base["rsi_peak_to_trigger_drop_pts"]=base["trigger_rsi"]-base["peak_rsi"]

cut=max(1,int(len(base)*.60))
tr=base.iloc[:cut].copy();ho=base.iloc[cut:].copy()

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

rows=[]
for thr in [55,60,65,68,70,72,75,78,80,82,85]:
    for fresh in [False,True]:
        mt=tr["peak_rsi"]>=thr;mh=ho["peak_rsi"]>=thr
        if fresh:
            mt &= (tr["hours_peak_to_trigger"]<=24)&(tr["near95_bars"]<=4)&(tr["pullback_retest_cycles"]<=0)
            mh &= (ho["hours_peak_to_trigger"]<=24)&(ho["near95_bars"]<=4)&(ho["pullback_retest_cycles"]<=0)
        gt,gh=tr[mt],ho[mh]
        if len(gt)<8 or len(gh)<5:continue
        rows.append({
            "peak_rsi_min":thr,"fresh_shape":fresh,
            "train_n":len(gt),"train_hit5":rate(gt["hit5"]),"train_hit10":rate(gt["hit10"]),
            "train_t5_s7p5":rate(gt["t5_s0.075"]),
            "hold_n":len(gh),"hold_hit5":rate(gh["hit5"]),"hold_hit10":rate(gh["hit10"]),
            "hold_t5_s7p5":rate(gh["t5_s0.075"]),
            "hold_median_mfe":float(gh["mfe5d"].median()),"hold_median_mae":float(gh["mae5d"].median()),
        })

# Also test percentage RSI collapse from peak; this is important because a "top"
# should show both high starting RSI and a meaningful momentum unwind.
for peak_thr in [65,70,75]:
    for drop in [.10,.15,.20,.25]:
        mt=(tr["peak_rsi"]>=peak_thr)&(tr["rsi_peak_to_trigger_drop_pct"]<=-drop)
        mh=(ho["peak_rsi"]>=peak_thr)&(ho["rsi_peak_to_trigger_drop_pct"]<=-drop)
        gt,gh=tr[mt],ho[mh]
        if len(gt)<8 or len(gh)<5:continue
        rows.append({
            "peak_rsi_min":peak_thr,"fresh_shape":f"rsi_drop>={drop:.0%}",
            "train_n":len(gt),"train_hit5":rate(gt["hit5"]),"train_hit10":rate(gt["hit10"]),
            "train_t5_s7p5":rate(gt["t5_s0.075"]),
            "hold_n":len(gh),"hold_hit5":rate(gh["hit5"]),"hold_hit10":rate(gh["hit10"]),
            "hold_t5_s7p5":rate(gh["t5_s0.075"]),
            "hold_median_mfe":float(gh["mfe5d"].median()),"hold_median_mae":float(gh["mae5d"].median()),
        })

z=pd.DataFrame(rows)
z.to_csv(OUT/"peak_rsi_gate_results.csv",index=False)

# Current/latest VIRTUAL events under C1 base, so we can see exactly why they pass/fail.
v=base[base["coin"].astype(str).str.upper().eq("VIRTUAL")].sort_values("trigger_time")
v.to_csv(OUT/"virtual_c1_events.csv",index=False)

lines=[
"METHOD 1 — PEAK RSI GATE AUDIT",
"",
f"Corrected C1 base events: {len(base)} train={len(tr)} holdout={len(ho)}",
f"Base holdout +5%: {rate(ho['hit5'])*100:.2f}%",
f"Base holdout +5 before -7.5%: {rate(ho['t5_s0.075'])*100:.2f}%",
"",
"PEAK RSI THRESHOLD RESULTS",
z.to_string(index=False),
"",
"VIRTUAL C1 EVENTS",
v.tail(12).to_string(index=False) if len(v) else "none"
]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
