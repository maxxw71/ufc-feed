#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
CACHE="$ROOT/wrestling_stability_positive_roi/all_favorite_sides_with_prefight_wrestling.csv"

[ -s "$CACHE" ] || { echo "ERROR: Missing cached wrestling table: $CACHE"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$CACHE" <<'PY'
import sys
import pandas as pd
import numpy as np
from pathlib import Path

p=Path(sys.argv[1])
df=pd.read_csv(p,low_memory=False)
df['event_date']=pd.to_datetime(df['event_date'],errors='coerce')
if 'profit100' not in df.columns:
    df['profit100']=np.where(df.won.astype(bool),100*(pd.to_numeric(df.fav_decimal,errors='coerce')-1),-100.0)

def stats(g):
    n=len(g)
    w=int(g.won.astype(bool).sum()) if n else 0
    roi=float(g.profit100.sum()/(100*n)) if n else float('nan')
    return n,w,roi

stages=[]
cur=df.copy()
stages.append(("All favorite-side fights with two-sided pre-fight wrestling history",cur.copy()))

cur=cur[(cur.w_fights>=2)&(cur.o_fights>=2)].copy()
stages.append(("Both fighters have >=2 prior UFC fights",cur.copy()))

cur=cur[cur.w_td_a15>=5.0].copy()
stages.append(("Favorite attempts >=5.0 takedowns / 15",cur.copy()))

cur=cur[cur.w_td_l15>=1.25].copy()
stages.append(("Favorite lands >=1.25 takedowns / 15",cur.copy()))

cur=cur[cur.w_ctrl15>=1.5].copy()
stages.append(("Favorite control >=1.5 minutes / 15",cur.copy()))

cur=cur[cur.o_td_a15<=1.0].copy()
stages.append(("Opponent attempts <=1.0 takedown / 15",cur.copy()))

cur=cur[cur.o_ctrl15<=0.75].copy()
stages.append(("Opponent control <=0.75 minutes / 15",cur.copy()))

print("WRESTLING MISMATCH — EXACT FUNNEL")
print("="*118)
print("Final rule: favorite | prior>=2 | TDatt>=5/15 | TDland>=1.25/15 | ctrl>=1.5m/15 | opp TDatt<=1/15 | opp ctrl<=0.75m/15")
print()

base=len(stages[0][1])
prev=base
for i,(label,g) in enumerate(stages):
    n,w,roi=stats(g)
    removed=(prev-n) if i else 0
    retained=(100*n/base) if base else 0
    wr=(100*w/n) if n else 0
    print(f"{i}. {label}")
    if i==0:
        print(f"   {n:,} fights | {wr:.1f}% wins | ROI {roi*100:+.2f}% | 100.0% of starting pool")
    else:
        print(f"   {n:,} fights | removed {removed:,} at this step | {retained:.1f}% of start | {wr:.1f}% wins | ROI {roi*100:+.2f}%")
    prev=n

print()
print("COMPACT FUNNEL")
print(" -> ".join(f"{len(g):,}" for _,g in stages))

print()
print("ELIMINATION SHARE BY STEP")
print("-"*118)
prev=base
for i,(label,g) in enumerate(stages[1:],1):
    n=len(g); removed=prev-n
    share=(100*removed/prev) if prev else 0
    print(f"{label:<58} removed {removed:4d} ({share:5.1f}% of previous stage)")
    prev=n
PY
