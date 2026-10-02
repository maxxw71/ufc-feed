#!/usr/bin/env python3
"""
Deep filter search for the lower-high / second-dump / RSI-oversold method.

Rules are selected ONLY on the earliest 60% of each base variant.
The latest 40% is untouched holdout.

Goal: find simple, explainable trigger-time filters that lift +5% hit rate
toward or above 80% without collapsing sample size.
"""
from pathlib import Path
import itertools
import numpy as np
import pandas as pd

SRC=Path("crypto/research/results_lower_high_rsi30_binance/events.csv")
OUT=Path("crypto/research/results_lower_high_rsi30_filtered")
OUT.mkdir(parents=True,exist_ok=True)

e=pd.read_csv(SRC)
for c in ["trigger_time","entry_time"]:
    e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")

# Derived ex-ante structure metrics available at trigger.
e["undercut_depth"] = e["trigger_low"] / e["first_low"] - 1
e["pump_retrace_frac"] = (-e["second_dump_pct"]) / e["pump_pct"].replace(0,np.nan)
e["prior_high_gap"] = e["pump_peak"] / e["prior_high"] - 1
e["rsi_shock_abs1"] = -e["rsi_pct1"]
e["rsi_shock_abs3"] = -e["rsi_pct3"]

# Focus on the most promising broad variants first.
VARIANTS=[
    "rsi28_pump8_dump8_any",
    "rsi30_pump10_dump10_any",
    "rsi30_pump8_dump8_any",
]

FEATURES=[
    ("trigger_rsi","low"),
    ("rsi_pct1","low"),
    ("rsi_pct3","low"),
    ("rsi_accel","low"),
    ("rsi_vs_sma5","low"),
    ("dist_sma20","low"),
    ("dist_sma50","low"),
    ("dist_ema9","low"),
    ("sma50_slope6","low"),
    ("close_location","low"),
    ("lower_wick","high"),
    ("vol_ratio20","high"),
    ("pump_pct","high"),
    ("lower_high_pct","low"),
    ("second_dump_pct","low"),
    ("undercut_depth","low"),
    ("pump_retrace_frac","high"),
]

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def spec_name(s):
    f,op,v=s
    return f"{f} {op} {v:.5f}"

def mask(df,s):
    f,op,v=s
    x=df[f].replace([np.inf,-np.inf],np.nan)
    return x<=v if op=="<=" else x>=v

rows=[]
bucket_rows=[]
for variant in VARIANTS:
    g=e[e["variant"]==variant].sort_values("entry_time").reset_index(drop=True)
    if len(g)<100: continue
    cut=max(1,int(len(g)*.60))
    tr=g.iloc[:cut].copy();ho=g.iloc[cut:].copy()

    specs=[]
    for feat,direction in FEATURES:
        if feat not in tr: continue
        s=tr[feat].replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<80: continue
        for q in (.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85):
            v=float(s.quantile(q))
            specs.append((feat,"<=" if direction=="low" else ">=",v))

        # Quartile diagnostics for shape.
        try:
            qx=pd.qcut(g[feat].replace([np.inf,-np.inf],np.nan),4,duplicates="drop")
            tmp=g.copy();tmp["bucket"]=qx
            for b,bg in tmp.dropna(subset=["bucket"]).groupby("bucket",observed=True):
                bucket_rows.append({
                    "variant":variant,"feature":feat,"bucket":str(b),"n":len(bg),
                    "feature_median":float(bg[feat].median()),
                    "hit5":rate(bg["hit5"]),"hit10":rate(bg["hit10"]),
                    "t5_s7p5":rate(bg["t5_s0.075"]),
                    "median_mfe":float(bg["mfe5d"].median()),
                    "median_mae":float(bg["mae5d"].median()),
                })
        except Exception:
            pass

    # Rank single filters by TRAIN only.
    singles=[]
    for s in specs:
        gt=tr[mask(tr,s)]
        if len(gt)<35: continue
        singles.append((rate(gt["hit5"]),rate(gt["t5_s0.075"]),len(gt),s))
    singles.sort(key=lambda z:(z[0],z[1],z[2]),reverse=True)

    # Pair only top training singles, with distinct features.
    combos=[(x[3],) for x in singles[:35]]
    for a,b in itertools.combinations([x[3] for x in singles[:35]],2):
        if a[0]==b[0]: continue
        combos.append((a,b))

    seen=set()
    for combo in combos:
        name=" AND ".join(spec_name(s) for s in combo)
        if name in seen: continue
        seen.add(name)
        mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
        for s in combo:
            mt &= mask(tr,s);mh &= mask(ho,s)
        gt=tr[mt];gh=ho[mh]
        if len(gt)<35 or len(gh)<15: continue
        rows.append({
            "variant":variant,
            "rule":name,
            "features":"+".join(s[0] for s in combo),
            "train_n":len(gt),
            "train_hit5":rate(gt["hit5"]),
            "train_hit10":rate(gt["hit10"]),
            "train_t5_s7p5":rate(gt["t5_s0.075"]),
            "hold_n":len(gh),
            "hold_hit5":rate(gh["hit5"]),
            "hold_hit10":rate(gh["hit10"]),
            "hold_t5_s5":rate(gh["t5_s0.05"]),
            "hold_t5_s7p5":rate(gh["t5_s0.075"]),
            "hold_t5_s10":rate(gh["t5_s0.1"]),
            "hold_median_mfe":float(gh["mfe5d"].median()),
            "hold_median_mae":float(gh["mae5d"].median()),
        })

z=pd.DataFrame(rows)
if len(z):
    z["train_score"]=z["train_hit5"]*.7+z["train_t5_s7p5"]*.3
    z=z.sort_values(["variant","train_score","train_n"],ascending=[True,False,False])
z.to_csv(OUT/"rules_train_holdout.csv",index=False)
pd.DataFrame(bucket_rows).to_csv(OUT/"feature_buckets.csv",index=False)

# Shortlist: train-selected top 15 per variant, but clearly show untouched holdout.
short=[]
for v in VARIANTS:
    q=z[z["variant"]==v].head(15) if len(z) else pd.DataFrame()
    if len(q): short.append(q)
short=pd.concat(short,ignore_index=True) if short else pd.DataFrame()
short.to_csv(OUT/"shortlist.csv",index=False)

# Also report all rules that happened to clear 80% holdout, but DO NOT use this
# list to choose a rule; it is diagnostic only because it looks at holdout.
diag=z[(z["hold_n"]>=20)&(z["hold_hit5"]>=.80)].copy() if len(z) else pd.DataFrame()
if len(diag):
    diag=diag.sort_values(["hold_hit5","hold_n"],ascending=[False,False])
diag.to_csv(OUT/"diagnostic_holdout_80plus.csv",index=False)

lines=[
    "LOWER-HIGH / SECOND-DUMP DEEP FILTER RESEARCH",
    "",
    "Thresholds/rules below are ranked by TRAINING data only. Later 40% is untouched holdout.",
    "",
]
for v in VARIANTS:
    q=short[short["variant"]==v] if len(short) else pd.DataFrame()
    lines += [v, q.to_string(index=False) if len(q) else "none", ""]
lines += [
    "DIAGNOSTIC: rules with >=20 holdout signals and >=80% +5 hit rate",
    "(Do not treat this section as selection-valid; it is shown only to reveal whether 80%+ pockets exist.)",
    diag.head(40).to_string(index=False) if len(diag) else "none",
]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
