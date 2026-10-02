#!/usr/bin/env python3
from pathlib import Path
import itertools
import numpy as np
import pandas as pd

ROOT=Path("m1_nolook_shards")
OUT=Path("crypto/research/results_method1_nolookahead_hyperliquid")
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
    (OUT/"REPORT.txt").write_text("No corrected no-lookahead events\n")
    raise SystemExit(2)

for col in ["arm_time","trigger_time","entry_time"]:
    e[col]=pd.to_datetime(e[col],utc=True,errors="coerce")
e=e.sort_values("entry_time").reset_index(drop=True)
cut=max(1,int(len(e)*.60));tr=e.iloc[:cut].copy();ho=e.iloc[cut:].copy()

def rate(s):
    x=s.dropna();return float(x.astype(bool).mean()) if len(x) else np.nan
def mask(df,s):
    f,op,v=s;x=df[f].replace([np.inf,-np.inf],np.nan)
    return x<=v if op=="<=" else x>=v
def name(s):
    return f"{s[0]} {s[1]} {s[2]:.5f}"

features=[
("rsi_pct1","low"),("rsi_pct3","low"),("rsi_accel","low"),
("dist_ema9","low"),("dist_sma9","low"),("dist_sma20","low"),
("lower_wick","high"),("close_location","low"),
("volume_ratio20","high"),("range_ratio20","high"),
("price_dd","low"),("daily_ret5","high"),("daily_rv20","high")
]
features=[x for x in features if x[0] in tr]

specs=[]
for f,k in features:
    s=tr[f].replace([np.inf,-np.inf],np.nan).dropna()
    if len(s)<50:continue
    for q in (.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85):
        specs.append((f,"<=" if k=="low" else ">=",float(s.quantile(q))))

singles=[]
for s in specs:
    g=tr[mask(tr,s)]
    if len(g)<25:continue
    singles.append((rate(g["hit5"]),rate(g["t5_s0.075"]),len(g),s))
singles.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)

combos=[(x[3],) for x in singles[:35]]
for a,b in itertools.combinations([x[3] for x in singles[:35]],2):
    if a[0]!=b[0]:combos.append((a,b))

rows=[];seen=set()
for combo in combos:
    nm=" AND ".join(name(s) for s in combo)
    if nm in seen:continue
    seen.add(nm)
    mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
    for s in combo:
        mt&=mask(tr,s);mh&=mask(ho,s)
    gt,gh=tr[mt],ho[mh]
    if len(gt)<25 or len(gh)<10:continue
    rows.append({
        "rule":nm,"features":"+".join(s[0] for s in combo),
        "train_n":len(gt),"train_hit5":rate(gt["hit5"]),
        "train_t5_s7p5":rate(gt["t5_s0.075"]),
        "hold_n":len(gh),"hold_hit5":rate(gh["hit5"]),
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
    z=z.sort_values(["train_score","train_n"],ascending=[False,False])
z.to_csv(OUT/"rules_train_holdout.csv",index=False)

# Evaluate the previously frozen rule ONLY as a diagnostic, because its thresholds
# were discovered before the look-ahead correction.
old=ho[(ho["dist_ema9"]<=-0.03649)&(ho["rsi_accel"]<=-7.63088)]
old_stat={
    "n":len(old),
    "hit5":rate(old["hit5"]) if len(old) else np.nan,
    "hit10":rate(old["hit10"]) if len(old) else np.nan,
    "t5_s7p5":rate(old["t5_s0.075"]) if len(old) else np.nan,
}

lines=[
"METHOD 1 — NO-LOOKAHEAD HYPERLIQUID REVALIDATION",
"",
f"Corrected unique events: {len(e)}",
f"Train: {len(tr)} | untouched holdout: {len(ho)}",
f"Raw holdout +5%: {rate(ho['hit5'])*100:.2f}%",
f"Raw holdout +5 before -7.5%: {rate(ho['t5_s0.075'])*100:.2f}%",
"",
"PREVIOUS RULE DIAGNOSTIC AFTER LOOKAHEAD FIX",
str(old_stat),
"",
"TOP TRAIN-SELECTED RULES / CORRECTED HOLDOUT",
z.head(30).to_string(index=False) if len(z) else "none",
"",
"Guardrail: daily setup arms at the next UTC day only after the complete daily candle is known."
]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
