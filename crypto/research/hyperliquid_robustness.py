#!/usr/bin/env python3
"""
Robustness audit for the leading Hyperliquid rebound rules.
No threshold discovery on holdout: evaluates already-frozen candidate families
across time folds, market segments, episode clusters, and nearby thresholds.
"""
from pathlib import Path
import math
import numpy as np
import pandas as pd

SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT=Path("crypto/research/results_hyperliquid_robustness")
OUT.mkdir(parents=True,exist_ok=True)

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:
    e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
key=["kind","dex","display_name","arm_time"]
e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
e=e.sort_values("entry_time").reset_index(drop=True)

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return (np.nan,np.nan)
    p=w/n
    den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half,ctr+half

def ep_assign(df,hours=18):
    x=df.sort_values("trigger_time").copy()
    ids=[];eid=0;last=None
    for t in x["trigger_time"]:
        if last is None or t-last>pd.Timedelta(hours=hours):eid+=1
        ids.append(eid);last=t
    x["episode_id"]=ids
    return x

rules={
    "wick20_close_bottom24": lambda d:(d["lower_wick_pct_range"]>=0.19851)&(d["close_location"]<=0.24492),
    "wick_any_range3p15": lambda d:(d["lower_wick_pct_range"]>=0.04580)&(d["range_ratio20"]>=3.15345),
    "vol7_wick_any": lambda d:(d["volume_ratio20"]>=7.02245)&(d["lower_wick_pct_range"]>=0.04580),
    "wick7p35_close_bottom26": lambda d:(d["lower_wick_pct_range"]>=0.07354)&(d["close_location"]<=0.26377),
    "wick7p35_range2p287": lambda d:(d["lower_wick_pct_range"]>=0.07354)&(d["range_ratio20"]>=2.28744),
    "wick11p1_range2p287": lambda d:(d["lower_wick_pct_range"]>=0.11138)&(d["range_ratio20"]>=2.28744),
}

# Equal-count chronological folds expose regime dependence.
folds=np.array_split(np.arange(len(e)),5)
fold_rows=[]
for rn,fn in rules.items():
    mask=fn(e)
    for fi,idx in enumerate(folds,1):
        g=e.loc[idx][mask.loc[idx]]
        if not len(g):continue
        rec={"rule":rn,"fold":fi,"start":g["entry_time"].min(),"end":g["entry_time"].max(),"n":len(g),
             "hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"]),
             "median_mfe5d":float(g["mfe_5d"].median()),"median_mae5d":float(g["mae_5d"].median())}
        for stop,col in [(5,"t50_before_s50_5d"),(7.5,"t50_before_s75_5d"),(10,"t50_before_s100_5d")]:
            w=int(g[col].fillna(False).astype(bool).sum());n=len(g);lo,hi=wilson(w,n)
            rec[f"t5_before_s{stop}"]=w/n
            rec[f"wilson_lo_s{stop}"]=lo;rec[f"wilson_hi_s{stop}"]=hi
        fold_rows.append(rec)
pd.DataFrame(fold_rows).to_csv(OUT/"time_folds.csv",index=False)

# Segment stability.
seg=[]
for rn,fn in rules.items():
    g0=e[fn(e)]
    for cols in [["kind"],["dex"],["kind","dex"]]:
        for keys,g in g0.groupby(cols):
            if len(g)<8:continue
            if not isinstance(keys,tuple):keys=(keys,)
            rec={"rule":rn,"by":"+".join(cols),"segment":"|".join(map(str,keys)),"n":len(g),
                 "hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"])}
            for stop,col in [(5,"t50_before_s50_5d"),(7.5,"t50_before_s75_5d"),(10,"t50_before_s100_5d")]:
                rec[f"t5_before_s{stop}"]=rate(g[col])
            seg.append(rec)
pd.DataFrame(seg).to_csv(OUT/"market_segments.csv",index=False)

# Episode-level version: one result per market-wide 18h cluster. Report mean
# success fraction within the episode and whether >=50% of signals succeeded.
ep=[]
for rn,fn in rules.items():
    g=ep_assign(e[fn(e)])
    for stop,col in [(5,"t50_before_s50_5d"),(7.5,"t50_before_s75_5d"),(10,"t50_before_s100_5d")]:
        if not len(g):continue
        q=g.groupby("episode_id")[col].agg(["mean","count"]).reset_index()
        ep.append({"rule":rn,"stop":stop,"signals":len(g),"episodes":len(q),
                   "episode_mean_success":float(q["mean"].mean()),
                   "episode_majority_success":float((q["mean"]>=0.5).mean()),
                   "median_signals_per_episode":float(q["count"].median()),
                   "max_signals_one_episode":int(q["count"].max())})
pd.DataFrame(ep).to_csv(OUT/"episode_robustness.csv",index=False)

# Neighborhood diagnostics around the two leading candle families. This is NOT
# a new selection stage; it tests whether performance is smooth around the
# frozen thresholds rather than a one-number artifact.
grid=[]
# family A: wick + close location
for wick in [0.05,0.10,0.15,0.20,0.25,0.30]:
    for close in [0.15,0.20,0.25,0.30,0.35]:
        g=e[(e["lower_wick_pct_range"]>=wick)&(e["close_location"]<=close)]
        if len(g)<20:continue
        grid.append({"family":"wick_close","wick_min":wick,"second_threshold":close,"n":len(g),
                     "hit5":rate(g["hit5_5d"]),"hit10":rate(g["hit10_5d"]),
                     "t5_s5":rate(g["t50_before_s50_5d"]),
                     "t5_s7p5":rate(g["t50_before_s75_5d"]),
                     "t5_s10":rate(g["t50_before_s100_5d"])})
# family B: wick + abnormal range
for wick in [0.03,0.05,0.075,0.10,0.15,0.20]:
    for rr in [1.5,2.0,2.5,3.0,3.5,4.0]:
        g=e[(e["lower_wick_pct_range"]>=wick)&(e["range_ratio20"]>=rr)]
        if len(g)<20:continue
        grid.append({"family":"wick_range","wick_min":wick,"second_threshold":rr,"n":len(g),
                     "hit5":rate(g["hit5_5d"]),"hit10":rate(g["hit10_5d"]),
                     "t5_s5":rate(g["t50_before_s50_5d"]),
                     "t5_s7p5":rate(g["t50_before_s75_5d"]),
                     "t5_s10":rate(g["t50_before_s100_5d"])})
pd.DataFrame(grid).to_csv(OUT/"threshold_neighborhoods.csv",index=False)

# Compact all-time summaries with CIs.
summ=[]
for rn,fn in rules.items():
    g=e[fn(e)]
    if not len(g):continue
    rec={"rule":rn,"n":len(g),"hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"]),
         "median_mfe5d":float(g["mfe_5d"].median()),"median_mae5d":float(g["mae_5d"].median())}
    for stop,col in [(5,"t50_before_s50_5d"),(7.5,"t50_before_s75_5d"),(10,"t50_before_s100_5d")]:
        w=int(g[col].fillna(False).astype(bool).sum());lo,hi=wilson(w,len(g))
        rec[f"t5_s{stop}"]=w/len(g);rec[f"lo_s{stop}"]=lo;rec[f"hi_s{stop}"]=hi
    summ.append(rec)
s=pd.DataFrame(summ).sort_values("t5_s7.5",ascending=False)
s.to_csv(OUT/"summary.csv",index=False)

lines=["HYPERLIQUID LEADING-RULE ROBUSTNESS","","UNIQUE SETUPS: %d"%len(e),"",s.to_string(index=False),"",
       "Interpretation guardrail: Wilson intervals quantify sampling uncertainty; time folds and episode clustering expose regime/correlation dependence. Neighborhood grids are diagnostics only and are not used to select a new winner."]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
