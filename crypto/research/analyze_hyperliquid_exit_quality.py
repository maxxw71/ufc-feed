#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np

SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT=Path("crypto/research/results_hyperliquid_all_pairs/exit_quality_deduped.txt")

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:
    if c in e.columns:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")

# One physical setup per instrument + daily arm. Keep the earliest/smallest flush trigger
# so 8/10/12 threshold variants do not inflate N.
key=["kind","dex","display_name","arm_time"]
e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first").copy()
e=e.sort_values("entry_time").reset_index(drop=True)
cut=max(1,int(len(e)*0.60))
train=e.iloc[:cut].copy()
hold=e.iloc[cut:].copy()

def pct(x):
    return "n/a" if pd.isna(x) else f"{100*x:.2f}%"

def stat(df,key):
    found=df[key+"_found"].fillna(False).astype(bool)
    g=df[found]
    ret=g[key+"_return"] if key+"_return" in g.columns else pd.Series(dtype=float)
    return {
        "all_n":len(df),
        "found_n":len(g),
        "coverage":len(g)/len(df) if len(df) else np.nan,
        "positive":float((ret>0).mean()) if len(ret) else np.nan,
        "ge5":float((ret>=0.05).mean()) if len(ret) else np.nan,
        "ge10":float((ret>=0.10).mean()) if len(ret) else np.nan,
        "unconditional_found_ge5":float((found & (df[key+"_return"].fillna(-999)>=0.05)).mean()) if len(df) else np.nan,
        "median":float(ret.median()) if len(ret) else np.nan,
    }

lines=[]
lines.append(f"UNIQUE PHYSICAL SETUPS: {len(e)}")
lines.append(f"TRAIN: {len(train)}  HOLDOUT: {len(hold)}")
lines.append("")
for label,df in [("ALL",e),("TRAIN",train),("HOLDOUT",hold)]:
    lines.append(label)
    if "hit5_5d" in df:
        lines.append(f"baseline_hit5_5d={pct(df['hit5_5d'].fillna(False).astype(bool).mean())}")
    if "hit10_5d" in df:
        lines.append(f"baseline_hit10_5d={pct(df['hit10_5d'].fillna(False).astype(bool).mean())}")
    for key in ["rsi_recover_50","rsi_recover_75","rsi_recover_100"]:
        s=stat(df,key)
        lines.append(
            f"{key}: found={s['found_n']}/{s['all_n']} ({pct(s['coverage'])}), "
            f"positive_when_found={pct(s['positive'])}, >=5_when_found={pct(s['ge5'])}, "
            f">=10_when_found={pct(s['ge10'])}, median_return={pct(s['median'])}, "
            f"all_setups_that_both_reach_and_are_>=5={pct(s['unconditional_found_ge5'])}"
        )
    # Ex-ante target-before-stop stats already calculated from entry OHLC paths.
    for col in ["t50_before_s50_5d","t50_before_s75_5d","t50_before_s100_5d"]:
        if col in df:
            lines.append(f"{col}={pct(df[col].fillna(False).astype(bool).mean())}")
    lines.append("")

OUT.write_text("\n".join(lines)+"\n")
print(OUT.read_text())
