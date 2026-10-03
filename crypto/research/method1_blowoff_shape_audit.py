#!/usr/bin/env python3
"""
Audit the *shape* of Method 1 blow-off tops.

Purpose:
Reject late-cycle distribution / repeated-top breakdowns that satisfy the old
numeric breakout+flush rule but are not the "fresh blow-off -> first washout"
pattern the method is intended to trade.

All rule discovery uses the earliest 60% of corrected Method-1 events.
The latest 40% remains holdout.
"""
from __future__ import annotations
import math,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_method1_nolookahead_hyperliquid/events.csv")
OUT=Path("crypto/research/results_method1_blowoff_shape")
OUT.mkdir(parents=True,exist_ok=True)

def post(s,p,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(INFO,json=p,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(8,1+k))
    raise RuntimeError(str(last))

def candles(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        try:
            a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                      "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),
                      "c":float(r["c"]),"v":float(r.get("v",0))})
        except Exception:pass
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time").reset_index(drop=True) if a else pd.DataFrame()

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["ret1"]=x["c"].pct_change()
    return x

def event_shape(x,arm,trigger):
    w=x[(x["time"]>=arm)&(x["time"]<=trigger)].copy()
    if len(w)<2:return None
    pi=int(w["h"].idxmax())
    peak=float(x.loc[pi,"h"])
    pt=x.loc[pi,"time"]
    loc=x.index.get_loc(pi)

    def past_return(bars):
        j=loc-bars
        if j<0:return np.nan
        base=float(x.iloc[j]["c"])
        return peak/base-1 if base>0 else np.nan

    # How long price loitered around the top. A clean blow-off should usually
    # spend little time within 95-97% of the peak before the first washout.
    near95=w[w["h"]>=peak*.95]
    near97=w[w["h"]>=peak*.97]
    first95=near95["time"].min() if len(near95) else pd.NaT
    first97=near97["time"].min() if len(near97) else pd.NaT

    # Count meaningful pullback/retest cycles before final trigger:
    # after reaching 95% of peak, count recoveries back above 95% following a
    # close >=5% below peak. Multiple cycles are distribution-like.
    post=w[w["time"]>=first95].copy() if pd.notna(first95) else w.iloc[0:0]
    cycles=0;in_pullback=False
    for _,r in post.iterrows():
        if float(r["c"])<=peak*.95:
            in_pullback=True
        elif in_pullback and float(r["h"])>=peak*.95:
            cycles+=1;in_pullback=False

    peak_rsi=float(x.loc[pi,"rsi"]) if pd.notna(x.loc[pi,"rsi"]) else np.nan
    peak_ds20=float(x.loc[pi,"dist_sma20"]) if pd.notna(x.loc[pi,"dist_sma20"]) else np.nan
    max4h=float(x.iloc[max(0,loc-12):loc+1]["ret1"].max()) if loc>=0 else np.nan

    return {
        "peak_time":pt,
        "peak_price":peak,
        "hours_peak_to_trigger":(trigger-pt).total_seconds()/3600,
        "bars_arm_to_peak":int((pt-arm).total_seconds()/14400) if pt>=arm else np.nan,
        "near95_bars":int(len(near95)),
        "near97_bars":int(len(near97)),
        "top95_span_hours":(trigger-first95).total_seconds()/3600 if pd.notna(first95) else np.nan,
        "top97_span_hours":(trigger-first97).total_seconds()/3600 if pd.notna(first97) else np.nan,
        "pullback_retest_cycles":cycles,
        "peak_rsi":peak_rsi,
        "peak_dist_sma20":peak_ds20,
        "ret24_to_peak":past_return(6),
        "ret48_to_peak":past_return(12),
        "ret72_to_peak":past_return(18),
        "max4h_gain_prepeak":max4h,
    }

def rate(s):
    x=s.dropna();return float(x.astype(bool).mean()) if len(x) else np.nan

def mask(df,spec):
    f,op,v=spec
    x=df[f].replace([np.inf,-np.inf],np.nan)
    return x<=v if op=="<=" else x>=v

def nm(s):
    return f"{s[0]} {s[1]} {s[2]:.4f}"

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:
    e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
e=e.sort_values("entry_time").reset_index(drop=True)

s=requests.Session();s.headers["User-Agent"]="appwiza-method1-blowoff-shape/1.0"
start=e["arm_time"].min()-pd.Timedelta(days=4)
end=e["trigger_time"].max()+pd.Timedelta(days=2)

cache={}
features=[]
for n,coin in enumerate(sorted(e["coin"].unique()),1):
    try:
        cache[coin]=prep(candles(s,coin,start.to_pydatetime(),end.to_pydatetime()))
        print(n,coin,len(cache[coin]))
    except Exception as ex:
        print("ERR",coin,ex);cache[coin]=pd.DataFrame()
    time.sleep(.05)

for i,r in e.iterrows():
    x=cache.get(r["coin"],pd.DataFrame())
    sh=event_shape(x,r["arm_time"],r["trigger_time"]) if len(x) else None
    features.append(sh or {})
f=pd.DataFrame(features)
z=pd.concat([e.reset_index(drop=True),f.reset_index(drop=True)],axis=1)
z.to_csv(OUT/"events_with_shape.csv",index=False)

cut=max(1,int(len(z)*.60));tr=z.iloc[:cut].copy();ho=z.iloc[cut:].copy()

# Explicitly evaluate VIRTUAL latest event if present.
v=z[z["coin"].astype(str).str.upper().eq("VIRTUAL")].sort_values("trigger_time")
v.to_csv(OUT/"virtual_events.csv",index=False)

# Search only intuitive blow-off-shape filters.
specs=[]
for feat,kind,vals in [
    ("hours_peak_to_trigger","low",[8,12,16,24,36,48]),
    ("near95_bars","low",[2,3,4,5,6,8]),
    ("near97_bars","low",[1,2,3,4,5]),
    ("top95_span_hours","low",[8,12,16,24,36,48]),
    ("pullback_retest_cycles","low",[0,1]),
    ("peak_rsi","high",[70,75,80,85]),
    ("peak_dist_sma20","high",[.08,.10,.12,.15,.20]),
    ("ret24_to_peak","high",[.08,.12,.15,.20,.25]),
    ("ret48_to_peak","high",[.12,.15,.20,.25,.30]),
    ("max4h_gain_prepeak","high",[.04,.06,.08,.10]),
]:
    if feat not in tr:continue
    for val in vals:
        specs.append((feat,"<=" if kind=="low" else ">=",float(val)))

singles=[]
for spec in specs:
    g=tr[mask(tr,spec)]
    if len(g)<30:continue
    singles.append((rate(g["hit5"]),rate(g["t5_s0.075"]),len(g),spec))
singles.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)

import itertools
combos=[(x[3],) for x in singles[:30]]
for a,b in itertools.combinations([x[3] for x in singles[:30]],2):
    if a[0]!=b[0]:combos.append((a,b))

rows=[];seen=set()
for combo in combos:
    name=" AND ".join(nm(s) for s in combo)
    if name in seen:continue
    seen.add(name)
    mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
    for sp in combo:
        mt&=mask(tr,sp);mh&=mask(ho,sp)
    gt,gh=tr[mt],ho[mh]
    if len(gt)<30 or len(gh)<12:continue
    rows.append({
        "rule":name,"train_n":len(gt),"train_hit5":rate(gt["hit5"]),
        "train_t5_s7p5":rate(gt["t5_s0.075"]),
        "hold_n":len(gh),"hold_hit5":rate(gh["hit5"]),
        "hold_hit10":rate(gh["hit10"]),
        "hold_t5_s7p5":rate(gh["t5_s0.075"]),
        "hold_median_mfe":float(gh["mfe5d"].median()),
        "hold_median_mae":float(gh["mae5d"].median()),
    })
rules=pd.DataFrame(rows)
if len(rules):
    rules["train_score"]=rules["train_hit5"]*.7+rules["train_t5_s7p5"]*.3
    rules=rules.sort_values(["train_score","train_n"],ascending=[False,False])
rules.to_csv(OUT/"shape_rules_train_holdout.csv",index=False)

# A direct candidate matching the user's visual definition:
# fresh peak, no long top distribution, no repeated recovery to peak zone.
direct={}
for label,m in {
    "fresh24_near95max4_no_retest":(z["hours_peak_to_trigger"]<=24)&(z["near95_bars"]<=4)&(z["pullback_retest_cycles"]<=0),
    "fresh36_near95max5_no_retest":(z["hours_peak_to_trigger"]<=36)&(z["near95_bars"]<=5)&(z["pullback_retest_cycles"]<=0),
    "fresh24_peakrsi75_no_retest":(z["hours_peak_to_trigger"]<=24)&(z["peak_rsi"]>=75)&(z["pullback_retest_cycles"]<=0),
}.items():
    q=z[m.fillna(False)].sort_values("entry_time")
    c=max(1,int(len(q)*.60));qh=q.iloc[c:]
    direct[label]={
        "n":len(q),"hold_n":len(qh),
        "hold_hit5":rate(qh["hit5"]) if len(qh) else np.nan,
        "hold_t5_s7p5":rate(qh["t5_s0.075"]) if len(qh) else np.nan,
    }

lines=[
"METHOD 1 BLOW-OFF SHAPE AUDIT",
"",
"Intent: separate a fresh blow-off / first washout from late-cycle repeated-top distribution.",
f"Events: {len(z)} train={len(tr)} holdout={len(ho)}",
"",
"DIRECT VISUAL-INTENT FILTERS",
str(direct),
"",
"LATEST VIRTUAL EVENTS",
v.tail(8).to_string(index=False) if len(v) else "none",
"",
"TOP TRAIN-SELECTED SHAPE FILTERS",
rules.head(35).to_string(index=False) if len(rules) else "none",
]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
