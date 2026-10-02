#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np

ROOT=Path("hl_lower_high_shards")
OUT=Path("crypto/research/results_lower_high_hyperliquid_replication")
OUT.mkdir(parents=True,exist_ok=True)

ev=[];cv=[]
for p in sorted(ROOT.rglob("events_*.csv")):
    try:
        x=pd.read_csv(p)
        if len(x):ev.append(x)
    except Exception:pass
for p in sorted(ROOT.rglob("coverage_*.csv")):
    try:cv.append(pd.read_csv(p))
    except Exception:pass
e=pd.concat(ev,ignore_index=True) if ev else pd.DataFrame()
c=pd.concat(cv,ignore_index=True) if cv else pd.DataFrame()
c.to_csv(OUT/"coverage.csv",index=False)
e.to_csv(OUT/"events.csv",index=False)
if e.empty:
    (OUT/"REPORT.txt").write_text("No events\n")
    raise SystemExit(2)
for col in ["trigger_time","entry_time"]:
    e[col]=pd.to_datetime(e[col],utc=True,errors="coerce")

rules=["A_lowerhigh_wick","B_sma50_wick","C_lowerhigh_bigpump","D_sma50slope_ema9"]
rows=[]
for r in rules:
    if r not in e:continue
    g=e[e[r].fillna(False).astype(bool)].sort_values("entry_time").reset_index(drop=True)
    if not len(g):continue
    cut=max(1,int(len(g)*.60));tr=g.iloc[:cut];ho=g.iloc[cut:]
    rows.append({
        "rule":r,"n":len(g),"coins":g["coin"].nunique(),
        "hold_n":len(ho),
        "hold_hit5":float(ho["hit5"].mean()) if len(ho) else np.nan,
        "hold_hit10":float(ho["hit10"].mean()) if len(ho) else np.nan,
        "hold_t5_s5":float(ho["t5_s0.05"].mean()) if len(ho) else np.nan,
        "hold_t5_s7p5":float(ho["t5_s0.075"].mean()) if len(ho) else np.nan,
        "hold_t5_s10":float(ho["t5_s0.1"].mean()) if len(ho) else np.nan,
        "hold_median_mfe":float(ho["mfe5d"].median()) if len(ho) else np.nan,
        "hold_median_mae":float(ho["mae5d"].median()) if len(ho) else np.nan,
    })
z=pd.DataFrame(rows)
z.to_csv(OUT/"summary.csv",index=False)
lines=["HYPERLIQUID EXTERNAL REPLICATION — FROZEN BINANCE LOWER-HIGH RULES","",
       "No thresholds were tuned on Hyperliquid.","",
       z.to_string(index=False)]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
