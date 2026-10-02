#!/usr/bin/env python3
"""Primary Hyperliquid perp-specific ex-ante method discovery/validation."""
from pathlib import Path
import itertools,math
import numpy as np
import pandas as pd

SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT=Path("crypto/research/results_hyperliquid_primary_perps");OUT.mkdir(parents=True,exist_ok=True)
e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
key=["kind","dex","display_name","arm_time"]
e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
e=e[(e["kind"]=="perp")&(e["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)

features=[
("rsi_pct1","low"),("rsi_pct3","low"),("rsi_accel","low"),("rsi_drop_pct","low"),
("rsi_price_shock_ratio","band"),("daily_4h_rsi_gap","high"),("rsi_to_daily_ratio","low"),
("volume_ratio20","high"),("range_ratio20","high"),("lower_wick_pct_range","high"),
("close_location","low"),("dist_ema9","low"),("dist_sma9","low"),("dist_sma20","low"),
("sma9_slope3","low"),("rsi_vs_sma3","low"),("rsi_vs_sma5","low"),("rsi_vs_sma9","low"),
("rsi_sma5_slope1","low"),("rsi_sma5_slope3","low"),("dual_stretch_9","low")]
features=[x for x in features if x[0] in e]

cut=max(1,int(len(e)*.60));tr=e.iloc[:cut].copy();ho=e.iloc[cut:].copy()

def rate(s):
    x=s.dropna();return float(x.astype(bool).mean()) if len(x) else np.nan
def tcol(stop):return {5:"t50_before_s50_5d",7.5:"t50_before_s75_5d",10:"t50_before_s100_5d"}[stop]
def mask(df,s):
    f,op,a,b=s;x=df[f].replace([np.inf,-np.inf],np.nan)
    if op=="<=":return x<=a
    if op==">=":return x>=a
    return x.between(a,b,inclusive="both")
def name(s):
    f,op,a,b=s
    return f"{a:.5f} <= {f} <= {b:.5f}" if op=="band" else f"{f} {op} {a:.5f}"

specs=[]
for f,k in features:
    s=tr[f].replace([np.inf,-np.inf],np.nan).dropna()
    if len(s)<60:continue
    if k=="band":
        q=s.quantile([.1,.2,.3,.4,.5,.6,.7,.8,.9])
        for lo,hi in [(.1,.5),(.2,.6),(.3,.7),(.4,.8),(.5,.9)]:
            specs.append((f,"band",float(q.loc[lo]),float(q.loc[hi])))
    else:
        for q in (.1,.2,.3,.4,.5,.6,.7,.8,.9):
            specs.append((f,"<=" if k=="low" else ">=",float(s.quantile(q)),None))

rows=[]
for stop in (5,7.5,10):
    col=tcol(stop)
    one=[]
    for s in specs:
        g=tr[mask(tr,s)]
        if len(g)>=20:one.append((rate(g[col]),len(g),s))
    one.sort(key=lambda x:(x[0],x[1]),reverse=True)
    tops=[x[2] for x in one[:30]]
    combos=[(s,) for s in tops]+[(a,b) for a,b in itertools.combinations(tops,2) if a[0]!=b[0]]
    seen=set()
    for combo in combos:
        nm=" AND ".join(name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
        for s in combo:mt&=mask(tr,s);mh&=mask(ho,s)
        gt,gh=tr[mt],ho[mh]
        if len(gt)<20 or len(gh)<10:continue
        rows.append({"stop":stop,"rule":nm,"train_n":len(gt),"train_t5_before_stop":rate(gt[col]),
                     "train_hit5":rate(gt["hit5_5d"]),"hold_n":len(gh),
                     "hold_t5_before_stop":rate(gh[col]),"hold_hit5":rate(gh["hit5_5d"]),
                     "hold_hit10":rate(gh["hit10_5d"]),"hold_mfe_med":float(gh["mfe_5d"].median()),
                     "hold_mae_med":float(gh["mae_5d"].median())})
z=pd.DataFrame(rows)
if len(z):
    z["train_score"]=z["train_t5_before_stop"]*.8+z["train_hit5"]*.2
    z=z.sort_values(["stop","train_score","train_n"],ascending=[True,False,False])
z.to_csv(OUT/"rules.csv",index=False)

lines=["HYPERLIQUID PRIMARY PERP VALIDATION","",f"Unique setups={len(e)} train={len(tr)} holdout={len(ho)}",
       f"Holdout hit +5%={rate(ho['hit5_5d'])*100:.2f}%  +10%={rate(ho['hit10_5d'])*100:.2f}%",
       f"Holdout +5 before -5={rate(ho[tcol(5)])*100:.2f}%",
       f"Holdout +5 before -7.5={rate(ho[tcol(7.5)])*100:.2f}%",
       f"Holdout +5 before -10={rate(ho[tcol(10)])*100:.2f}%",""]
for stop in (5,7.5,10):
    lines += [f"TOP TRAIN-SELECTED / HOLDOUT STOP -{stop}%",z[z["stop"]==stop].head(15).to_string(index=False) if len(z) else "none",""]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
