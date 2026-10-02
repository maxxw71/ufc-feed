#!/usr/bin/env python3
"""
Deep ex-ante validation for Hyperliquid blow-off / first-flush methods.

Uses only trigger-time features to select rules. Searches on the earliest 60%
of unique physical setups and reports untouched later-40% holdout performance.

Primary outcome: +5% target BEFORE a stop (-5%, -7.5%, -10%) within 5 days.
Also reports correlation-aware episode stats and all-in cost stress.
"""
from pathlib import Path
import itertools
import math
import numpy as np
import pandas as pd

SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUTDIR=Path("crypto/research/results_hyperliquid_deep_validation")
OUTDIR.mkdir(parents=True,exist_ok=True)

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:
    if c in e.columns:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")

# Collapse 8/10/12 threshold duplicates to one physical event. The 8% trigger
# is the earliest observable member when a setup later proceeds to 10/12%.
key=["kind","dex","display_name","arm_time"]
e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first").copy()
e=e.sort_values("entry_time").reset_index(drop=True)

# No future-derived columns are allowed in filters.
features=[
    ("rsi_pct1","low"),("rsi_pct3","low"),("rsi_accel","low"),
    ("rsi_drop_pct","low"),("rsi_price_shock_ratio","band"),
    ("daily_4h_rsi_gap","high"),("rsi_to_daily_ratio","low"),
    ("volume_ratio20","high"),("range_ratio20","high"),
    ("lower_wick_pct_range","high"),("close_location","low"),
    ("dist_ema20","low"),("dist_ema9","low"),("dist_sma9","low"),
    ("dist_sma20","low"),("sma9_slope3","low"),
    ("rsi_vs_sma3","low"),("rsi_vs_sma5","low"),("rsi_vs_sma9","low"),
    ("rsi_sma5_slope1","low"),("rsi_sma5_slope3","low"),
    ("dual_stretch_9","low"),
]
features=[x for x in features if x[0] in e.columns]

cut=max(1,int(len(e)*0.60))
train=e.iloc[:cut].copy()
hold=e.iloc[cut:].copy()

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def episode_ids(df,hours=18):
    x=df.sort_values("trigger_time").copy()
    ids=[];eid=0;last=None
    for t in x["trigger_time"]:
        if last is None or (t-last)>pd.Timedelta(hours=hours):
            eid+=1
        ids.append(eid);last=t
    x["episode_id"]=ids
    return x

def episode_stats(g,col):
    if g.empty:return (0,np.nan,np.nan)
    x=episode_ids(g)
    s=x.groupby("episode_id")[col].mean()
    # mean fraction of successful signals per episode, and episode-majority success
    return (int(len(s)),float(s.mean()),float((s>=0.5).mean()))

def eval_mask(df, spec):
    f,op,a,b=spec
    x=df[f].replace([np.inf,-np.inf],np.nan)
    if op=="<=":return x<=a
    if op==">=":return x>=a
    if op=="band":return x.between(a,b,inclusive="both")
    raise ValueError(op)

def mk_specs(df):
    out=[]
    for f,kind in features:
        s=df[f].replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<100:continue
        if kind=="band":
            q=s.quantile([.1,.2,.3,.4,.5,.6,.7,.8,.9]).to_dict()
            for loq,hiq in [(.1,.5),(.1,.6),(.2,.6),(.2,.7),(.3,.7),(.3,.8),(.4,.8),(.4,.9)]:
                lo=float(q[loq]);hi=float(q[hiq])
                out.append((f,"band",lo,hi))
        else:
            for q in (.1,.2,.3,.4,.5,.6,.7,.8,.9):
                v=float(s.quantile(q))
                out.append((f,"<=" if kind=="low" else ">=",v,None))
    return out

def spec_name(s):
    f,op,a,b=s
    if op=="band":return f"{a:.5f} <= {f} <= {b:.5f}"
    return f"{f} {op} {a:.5f}"

def target_col(stop):
    return {5:"t50_before_s50_5d",7.5:"t50_before_s75_5d",10:"t50_before_s100_5d"}[stop]

specs=mk_specs(train)
rows=[]
for stop in (5,7.5,10):
    col=target_col(stop)
    if col not in e.columns:continue
    candidates=[]
    for s in specs:
        mt=eval_mask(train,s)
        gt=train[mt]
        if len(gt)<35:continue
        wr=rate(gt[col])
        candidates.append((wr,len(gt),s))
    candidates.sort(key=lambda z:(z[0],z[1]),reverse=True)
    # Pair ingredients selected only on training data.
    top_specs=[z[2] for z in candidates[:35]]
    combos=[(s,) for s in top_specs]
    for a,b in itertools.combinations(top_specs,2):
        if a[0]==b[0]:continue
        combos.append((a,b))

    seen=set()
    for combo in combos:
        nm=" AND ".join(spec_name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        mt=pd.Series(True,index=train.index)
        mh=pd.Series(True,index=hold.index)
        for s in combo:
            mt &= eval_mask(train,s)
            mh &= eval_mask(hold,s)
        gt=train[mt];gh=hold[mh]
        if len(gt)<35 or len(gh)<15:continue
        ep_n,ep_mean,ep_major=episode_stats(gh,col)
        rows.append({
            "stop_pct":stop,
            "rule":nm,
            "features":"+".join(s[0] for s in combo),
            "train_n":len(gt),
            "train_target_before_stop":rate(gt[col]),
            "train_hit5_5d":rate(gt["hit5_5d"]),
            "hold_n":len(gh),
            "hold_target_before_stop":rate(gh[col]),
            "hold_hit5_5d":rate(gh["hit5_5d"]),
            "hold_hit10_5d":rate(gh["hit10_5d"]),
            "hold_median_mfe5d":float(gh["mfe_5d"].median()),
            "hold_median_mae5d":float(gh["mae_5d"].median()),
            "hold_episodes":ep_n,
            "episode_mean_target_before_stop":ep_mean,
            "episode_majority_success_rate":ep_major,
        })

z=pd.DataFrame(rows)
if len(z):
    z["train_score"]=z["train_target_before_stop"]*0.8+z["train_hit5_5d"]*0.2
    z=z.sort_values(["stop_pct","train_score","train_n"],ascending=[True,False,False])
z.to_csv(OUTDIR/"target_stop_rules_train_holdout.csv",index=False)

# Frozen existing research candidates, evaluated on unique holdout only.
frozen=[
    ("wick20_range3.15", lambda d:(d["lower_wick_pct_range"]>=0.19851)&(d["range_ratio20"]>=3.15345)),
    ("wick20_close_bottom24", lambda d:(d["lower_wick_pct_range"]>=0.19851)&(d["close_location"]<=0.24492)),
    ("wick_any_range3.15", lambda d:(d["lower_wick_pct_range"]>=0.04580)&(d["range_ratio20"]>=3.15345)),
    ("vol7_wick_any", lambda d:(d["volume_ratio20"]>=7.02245)&(d["lower_wick_pct_range"]>=0.04580)),
    ("rsi1_19p7_ema9_4p27", lambda d:(d["rsi_pct1"]<=-0.19679)&(d["dist_ema9"]<=-0.04274)),
    ("rsi1_19p7_rsi3_23p6", lambda d:(d["rsi_pct1"]<=-0.19679)&(d["rsi_pct3"]<=-0.23562)),
    ("rsi1_19p7_rsi_vs_sma5_16p6", lambda d:(d["rsi_pct1"]<=-0.19679)&(d["rsi_vs_sma5"]<=-0.16622)),
]
fr=[]
for name,fn in frozen:
    try:
        g=hold[fn(hold)]
    except Exception:
        continue
    if not len(g):continue
    rec={"rule":name,"n":len(g),"hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"]),
         "median_mfe5d":float(g["mfe_5d"].median()),"median_mae5d":float(g["mae_5d"].median())}
    for stop in (5,7.5,10):
        col=target_col(stop)
        epn,epmean,epmaj=episode_stats(g,col)
        rec[f"t5_before_s{stop}"]=rate(g[col])
        rec[f"episodes_s{stop}"]=epn
        rec[f"episode_mean_s{stop}"]=epmean
    fr.append(rec)
pd.DataFrame(fr).to_csv(OUTDIR/"frozen_rules_unique_holdout.csv",index=False)

# Market-segment stability on later holdout.
seg=[]
for group_cols in [["kind"],["dex"],["kind","dex"]]:
    for keys,g in hold.groupby(group_cols):
        if len(g)<15:continue
        if not isinstance(keys,tuple):keys=(keys,)
        r={"segment_by":"+".join(group_cols),"segment":"|".join(map(str,keys)),"n":len(g),
           "hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"])}
        for stop in (5,7.5,10):r[f"t5_before_s{stop}"]=rate(g[target_col(stop)])
        seg.append(r)
pd.DataFrame(seg).to_csv(OUTDIR/"segment_stability_holdout.csv",index=False)

# Cost stress: round-trip all-in cost scenarios. This does not alter whether
# target is touched first; it shows how much of a gross +5% target remains net.
# Official base tier-0 taker fees: perps 0.045%/side, spot 0.070%/side.
# Slippage stress is intentionally conservative and modelled per side.
cost_rows=[]
for market,fee_side in [("perp",0.00045),("spot",0.00070)]:
    for slip_side in (0.0005,0.0010,0.0025,0.0050):
        rt=2*(fee_side+slip_side)
        cost_rows.append({
            "market":market,
            "fee_per_side_pct":fee_side*100,
            "slippage_per_side_pct":slip_side*100,
            "round_trip_cost_pct":rt*100,
            "net_if_gross_target_5_pct":(0.05-rt)*100,
            "net_if_gross_target_7p5_pct":(0.075-rt)*100,
            "net_if_gross_target_10_pct":(0.10-rt)*100,
            "breakeven_winrate_5target_5stop":(0.05+rt)/(0.10),
            "breakeven_winrate_5target_7p5stop":(0.075+rt)/(0.125),
            "breakeven_winrate_5target_10stop":(0.10+rt)/(0.15),
        })
pd.DataFrame(cost_rows).to_csv(OUTDIR/"fee_slippage_stress.csv",index=False)

# Compact report.
lines=[]
lines.append("HYPERLIQUID DEEP EX-ANTE VALIDATION")
lines.append("")
lines.append(f"Unique physical setups: {len(e)}")
lines.append(f"Train: {len(train)}  Holdout: {len(hold)}")
lines.append(f"Holdout raw +5% within 5d: {rate(hold['hit5_5d'])*100:.2f}%")
lines.append(f"Holdout raw +10% within 5d: {rate(hold['hit10_5d'])*100:.2f}%")
for stop in (5,7.5,10):
    lines.append(f"Holdout +5% before -{stop}%: {rate(hold[target_col(stop)])*100:.2f}%")
lines.append("")
lines.append("BEST TARGET-BEFORE-STOP RULES (selected on TRAIN, shown on untouched HOLDOUT)")
for stop in (5,7.5,10):
    if len(z):
        q=z[z["stop_pct"]==stop].head(12)
        lines.append("")
        lines.append(f"STOP -{stop}%")
        lines.append(q.to_string(index=False))
lines.append("")
lines.append("FROZEN PRIOR RULES ON UNIQUE HOLDOUT")
ff=pd.DataFrame(fr)
lines.append(ff.to_string(index=False) if len(ff) else "none")
lines.append("")
lines.append("Notes:")
lines.append("- +5-before-stop is an ex-ante outcome known only after entry; all rule inputs are trigger-time features.")
lines.append("- Same-bar target/stop touches are conservatively not counted as target-first.")
lines.append("- Episode stats cluster signals within 18h to expose correlated market-wide flushes.")
lines.append("- Fee/slippage stress uses current documented base tier-0 taker fees; HIP-3 deployer fees and funding are not yet included.")
(OUTDIR/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUTDIR/"REPORT.txt").read_text())
