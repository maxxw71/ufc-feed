#!/usr/bin/env python3
from pathlib import Path
import math
import pandas as pd
import numpy as np

HL=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
BN=Path("crypto/research/results_binance_external_replication/events.csv")
OUT=Path("crypto/research/results_crossvenue_confirmation")
OUT.mkdir(parents=True,exist_ok=True)

rules={
    "EMA9 stretch + RSI acceleration": lambda d:(d["dist_ema9"]<=-0.03649)&(d["rsi_accel"]<=-7.63088),
    "RSI 1-bar crash + SMA9 slope": lambda d:(d["rsi_pct1"]<=-0.21182)&(d["sma9_slope3"]<=0.02604),
    "RSI 1-bar crash + SMA9 stretch": lambda d:(d["rsi_pct1"]<=-0.21182)&(d["dist_sma9"]<=-0.03517),
    "RSI-SMA5 slope + RSI 1-bar crash": lambda d:(d["rsi_sma5_slope1"]<=-0.05915)&(d["rsi_pct1"]<=-0.17635),
    "EMA9 stretch + low candle close": lambda d:(d["dist_ema9"]<=-0.03649)&(d["close_location"]<=0.16592),
}

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return (np.nan,np.nan)
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half,ctr+half

hl=pd.read_csv(HL)
for c in ["arm_time","trigger_time","entry_time"]:
    hl[c]=pd.to_datetime(hl[c],utc=True,errors="coerce")
key=["kind","dex","display_name","arm_time"]
hl=hl.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
hl=hl[(hl["kind"]=="perp")&(hl["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)
hc=max(1,int(len(hl)*.60));hh=hl.iloc[hc:].copy()

bn=pd.read_csv(BN)
for c in ["arm_time","trigger_time","entry_time"]:
    bn[c]=pd.to_datetime(bn[c],utc=True,errors="coerce")
bn=bn.sort_values("entry_time").reset_index(drop=True)
bc=max(1,int(len(bn)*.60));bh=bn.iloc[bc:].copy()

rows=[]
for name,fn in rules.items():
    gh=hh[fn(hh)]
    gb=bh[fn(bh)]
    for venue,g in [("Hyperliquid primary perps",gh),("Binance USDT spot",gb)]:
        if not len(g):continue
        w5=int(g["hit5_5d"].fillna(False).astype(bool).sum()) if "hit5_5d" in g else int(g["hit5"].fillna(False).astype(bool).sum())
        n=len(g);lo,hi=wilson(w5,n)
        # target before -7.5%
        col="t50_before_s75_5d" if "t50_before_s75_5d" in g else "t5_s0.075"
        wts=int(g[col].fillna(False).astype(bool).sum())
        l2,h2=wilson(wts,n)
        h10=rate(g["hit10_5d"]) if "hit10_5d" in g else rate(g["hit10"])
        rows.append({
            "rule":name,"venue":venue,"n":n,
            "hit5_rate":w5/n,"hit5_wilson_lo":lo,"hit5_wilson_hi":hi,
            "hit10_rate":h10,
            "t5_before_7p5stop":wts/n,"t5s7p5_wilson_lo":l2,"t5s7p5_wilson_hi":h2,
        })
    # pooled untouched holdouts from separate venues
    parts=[]
    for venue,g in [("HL",gh),("BN",gb)]:
        if len(g):
            gg=g.copy()
            gg["venue_pool"]=venue
            if "hit5_5d" not in gg and "hit5" in gg:gg["hit5_5d"]=gg["hit5"]
            if "hit10_5d" not in gg and "hit10" in gg:gg["hit10_5d"]=gg["hit10"]
            if "t50_before_s75_5d" not in gg and "t5_s0.075" in gg:gg["t50_before_s75_5d"]=gg["t5_s0.075"]
            parts.append(gg)
    if parts:
        gp=pd.concat(parts,ignore_index=True)
        n=len(gp);w=int(gp["hit5_5d"].fillna(False).astype(bool).sum());lo,hi=wilson(w,n)
        wt=int(gp["t50_before_s75_5d"].fillna(False).astype(bool).sum());l2,h2=wilson(wt,n)
        rows.append({
            "rule":name,"venue":"POOLED independent holdouts","n":n,
            "hit5_rate":w/n,"hit5_wilson_lo":lo,"hit5_wilson_hi":hi,
            "hit10_rate":rate(gp["hit10_5d"]),
            "t5_before_7p5stop":wt/n,"t5s7p5_wilson_lo":l2,"t5s7p5_wilson_hi":h2,
        })

z=pd.DataFrame(rows)
z.to_csv(OUT/"crossvenue_holdout_confirmation.csv",index=False)

lines=["CROSS-VENUE CONFIRMATION — FROZEN HYPERLIQUID RULES","","No Binance threshold tuning was performed.",""]
for name in rules:
    q=z[z["rule"]==name]
    lines.append(name)
    lines.append(q.to_string(index=False))
    lines.append("")
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
