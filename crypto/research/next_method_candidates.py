#!/usr/bin/env python3
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT=Path("crypto/research")
OUT=ROOT/"results_next_method_candidates"
OUT.mkdir(parents=True,exist_ok=True)

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def summarize(g):
    if len(g)==0:return {"n":0}
    out={"n":int(len(g))}
    for c in ["hit5","hit10","t5_s0.075","t5_s7p5","hit5_5d","hit10_5d","t50_before_s75_5d"]:
        if c in g.columns:out[c]=rate(g[c])
    for c in ["mfe5d","mae5d","mfe_5d","mae_5d"]:
        if c in g.columns:out["median_"+c]=float(pd.to_numeric(g[c],errors="coerce").median())
    return out

def c2_deepstretch_binance():
    p=ROOT/"results_lower_high_rsi30_binance/events.csv"
    e=pd.read_csv(p)
    if e.empty:return {"error":"empty Binance lower-high event file"}
    for c in ["entry_time","trigger_time"]:
        if c in e.columns:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    # Exact frozen C2 base structure + new train-selected HL failure-veto.
    g=e[
        (e["variant"]=="rsi30_pump10_dump10_any")&
        (pd.to_numeric(e["lower_high_pct"],errors="coerce")<=-0.19915)&
        (pd.to_numeric(e["pump_pct"],errors="coerce")>=0.21337)&
        (pd.to_numeric(e["dist_ema9"],errors="coerce")<=-0.069254)
    ].copy().sort_values("entry_time")
    cut=max(1,int(len(g)*.60))
    return {"all":summarize(g),"later40":summarize(g.iloc[cut:]),"train60":summarize(g.iloc[:cut])}

def c2_deepstretch_hl():
    e=pd.read_csv(ROOT/"results_lower_high_hyperliquid_replication/events.csv")
    for c in ["entry_time","trigger_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    g=e[e["C_lowerhigh_bigpump"].astype(bool)&(pd.to_numeric(e["dist_ema9"],errors="coerce")<=-0.069254)].copy().sort_values("entry_time")
    cut=max(1,int(len(g)*.60))
    return {"all":summarize(g),"later40":summarize(g.iloc[cut:]),"train60":summarize(g.iloc[:cut])}

def c4_auto():
    reg=json.loads((ROOT/"auto_discovery/candidate_registry.json").read_text())
    c=next(x for x in reg["candidates"] if x["candidate_id"]=="AUTO-20261003-01")
    return c

def main():
    result={
      "C4_candidate":{
        "name":"RSI Shock vs 3-Bar RSI Mean",
        "mechanism":"first post-breakout flush where RSI acceleration is sharply negative and RSI is deeply below its own 3-bar mean",
        "frozen_rule":"rsi_accel <= -9.01822 AND rsi_vs_sma3 <= -0.120961",
        "validation":c4_auto(),
      },
      "C2_deepstretch_candidate":{
        "name":"Lower-High Second Dump — Deep EMA Stretch",
        "mechanism":"existing lower-high/second-dump structure plus price materially below 4H EMA9 at trigger",
        "frozen_additional_filter":"dist_ema9 <= -0.069254",
        "hyperliquid":c2_deepstretch_hl(),
        "binance":c2_deepstretch_binance(),
      }
    }
    (OUT/"REPORT.json").write_text(json.dumps(result,indent=2,default=str))
    lines=["NEXT CRYPTO METHOD CANDIDATES","",json.dumps(result,indent=2,default=str)]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
