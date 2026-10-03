#!/usr/bin/env python3
"""
Automatic crypto method discovery.

Consumes the latest Hyperliquid all-pairs research event table and searches for
new, explainable rebound rules. It does NOT promote rules live.

Design:
- chronological 60/40 train/holdout
- thresholds chosen on train only
- single, pair, and limited triple rules
- +5% within 5d is primary target
- +5% before -7.5% is the execution-quality target
- episode clustering penalizes market-wide correlated flushes
- Wilson lower bound and time-fold stability penalize tiny/fragile samples
- new candidates are written to a registry with DISCOVERED / SHADOW_ONLY status
"""
from __future__ import annotations

from pathlib import Path
from math import sqrt
import itertools
import json
import numpy as np
import pandas as pd

SRC = Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT = Path("crypto/research/auto_discovery")
OUT.mkdir(parents=True, exist_ok=True)

PRIMARY = "hit5_5d"
RISK = "t50_before_s75_5d"

FEATURES = [
    ("rsi_pct1","low"),("rsi_pct3","low"),("rsi_accel","low"),
    ("rsi_drop_pct","low"),("trigger_rsi","low"),
    ("rsi_price_shock_ratio","band"),
    ("daily_4h_rsi_gap","high"),("rsi_to_daily_ratio","low"),
    ("volume_ratio20","high"),("range_ratio20","high"),
    ("lower_wick_pct_range","high"),("close_location","low"),
    ("dist_ema20","low"),("dist_ema9","low"),("dist_sma9","low"),
    ("dist_sma20","low"),("dist_sma50","low"),
    ("sma9_slope3","low"),("sma20_slope3","low"),
    ("rsi_vs_sma3","low"),("rsi_vs_sma5","low"),("rsi_vs_sma9","low"),
    ("rsi_vs_ema5","low"),("rsi_sma5_slope1","low"),
    ("rsi_sma5_slope3","low"),("dual_stretch_9","low"),
    ("daily_ret5","high"),("daily_rv20","high"),("daily_rsi","high"),
    ("price_pct1","low"),("price_pct3","low"),("price_dd","low"),
]

KNOWN_METHOD_FEATURE_SETS = [
    frozenset(["rsi_pct1","rsi_accel"]),  # C1 core
]

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def wilson_lower(w,n,z=1.96):
    if n<=0: return np.nan
    p=w/n
    den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def episodes(df,hours=18):
    x=df.sort_values("trigger_time").copy()
    ids=[];eid=0;last=None
    for t in pd.to_datetime(x["trigger_time"],utc=True):
        if last is None or (t-last)>pd.Timedelta(hours=hours):
            eid+=1
        ids.append(eid);last=t
    x["episode_id"]=ids
    return x

def episode_success(df,col):
    if df.empty:return (0,np.nan)
    x=episodes(df)
    q=x.groupby("episode_id")[col].mean()
    return int(len(q)),float(q.mean()) if len(q) else np.nan

def rule_name(spec):
    if spec[1]=="band":
        return f"{spec[2]:.6g} <= {spec[0]} <= {spec[3]:.6g}"
    return f"{spec[0]} {spec[1]} {spec[2]:.6g}"

def mask(df,spec):
    f,op,a,b=spec
    x=df[f].replace([np.inf,-np.inf],np.nan)
    if op=="<=":return x<=a
    if op==">=":return x>=a
    return x.between(a,b,inclusive="both")

def make_specs(train):
    specs=[]
    for feat,direction in FEATURES:
        if feat not in train.columns:continue
        s=train[feat].replace([np.inf,-np.inf],np.nan).dropna()
        if len(s)<80:continue
        if direction=="band":
            q=s.quantile([.1,.2,.3,.4,.5,.6,.7,.8,.9])
            for lo,hi in [(.1,.5),(.1,.6),(.2,.6),(.2,.7),(.3,.7),(.3,.8),(.4,.8),(.4,.9)]:
                specs.append((feat,"band",float(q.loc[lo]),float(q.loc[hi])))
        else:
            for q in (.10,.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85,.90):
                specs.append((feat,"<=" if direction=="low" else ">=",float(s.quantile(q)),None))
    return specs

def apply_combo(df,combo):
    m=pd.Series(True,index=df.index)
    for s in combo:m &= mask(df,s)
    return m

def time_fold_stability(train,combo):
    # Four chronological blocks; rule is already frozen from the full training
    # threshold grid, this just checks that performance is not one-period-only.
    idx=np.array_split(np.arange(len(train)),4)
    vals=[]
    ns=[]
    for ii in idx:
        g=train.loc[ii]
        q=g[apply_combo(g,combo)]
        if len(q)>=8:
            vals.append(rate(q[PRIMARY]))
            ns.append(len(q))
    if not vals:return (0,np.nan,np.nan)
    return len(vals),float(np.mean(vals)),float(np.min(vals))

def classify_novelty(combo):
    fs=frozenset(s[0] for s in combo)
    for known in KNOWN_METHOD_FEATURE_SETS:
        if fs.issubset(known):
            return "KNOWN_FAMILY_REFINEMENT"
    return "NEW_CANDIDATE"

def discover_segment(base,segment_name):
    base=base.sort_values("entry_time").reset_index(drop=True)
    if len(base)<140:return pd.DataFrame()
    cut=max(1,int(len(base)*.60))
    train=base.iloc[:cut].copy();hold=base.iloc[cut:].copy()

    specs=make_specs(train)
    single_scores=[]
    for s in specs:
        g=train[mask(train,s)]
        if len(g)<35:continue
        score=.70*rate(g[PRIMARY])+.30*rate(g[RISK])
        single_scores.append((score,len(g),s))
    single_scores.sort(key=lambda x:(x[0],x[1]),reverse=True)
    top=[x[2] for x in single_scores[:45]]

    combos=[(s,) for s in top]
    combos += [(a,b) for a,b in itertools.combinations(top,2) if a[0]!=b[0]]
    # Limited triples from only the strongest 14 single mechanisms.
    top3=[x[2] for x in single_scores[:14]]
    combos += [(a,b,c) for a,b,c in itertools.combinations(top3,3)
               if len({a[0],b[0],c[0]})==3]

    rows=[];seen=set()
    for combo in combos:
        nm=" AND ".join(rule_name(s) for s in combo)
        if nm in seen:continue
        seen.add(nm)
        gt=train[apply_combo(train,combo)]
        gh=hold[apply_combo(hold,combo)]
        if len(gt)<35 or len(gh)<20:continue

        tr_hit=rate(gt[PRIMARY]);tr_risk=rate(gt[RISK])
        ho_hit=rate(gh[PRIMARY]);ho_risk=rate(gh[RISK])
        wins=int(gh[PRIMARY].fillna(False).astype(bool).sum())
        lo=wilson_lower(wins,len(gh))
        ep_n,ep_rate=episode_success(gh,PRIMARY)
        folds,fold_mean,fold_min=time_fold_stability(train,combo)

        # Discovery gate: strong +5% probability, reasonable stop-order
        # performance, enough independent episodes, and no train collapse.
        eligible=(
            ho_hit>=.80
            and ho_risk>=.72
            and len(gh)>=20
            and ep_n>=12
            and ep_rate>=.76
            and lo>=.62
            and tr_hit>=.78
            and folds>=3
            and fold_mean>=.75
        )

        rows.append({
            "segment":segment_name,
            "rule":nm,
            "features":"+".join(s[0] for s in combo),
            "conditions":json.dumps([{"feature":s[0],"op":s[1],"a":s[2],"b":s[3]} for s in combo]),
            "novelty":classify_novelty(combo),
            "train_n":len(gt),"train_hit5":tr_hit,"train_t5_s7p5":tr_risk,
            "hold_n":len(gh),"hold_hit5":ho_hit,"hold_hit10":rate(gh["hit10_5d"]),
            "hold_t5_s7p5":ho_risk,
            "hold_wilson_lower":lo,
            "hold_episodes":ep_n,"episode_hit5":ep_rate,
            "train_folds":folds,"train_fold_mean_hit5":fold_mean,"train_fold_min_hit5":fold_min,
            "hold_median_mfe5d":float(gh["mfe_5d"].median()),
            "hold_median_mae5d":float(gh["mae_5d"].median()),
            "eligible":eligible,
            "research_score":(.45*ho_hit+.25*ho_risk+.15*ep_rate+.15*lo),
        })
    return pd.DataFrame(rows)

def main():
    e=pd.read_csv(SRC)
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")

    # Use 8% trigger as the first observable member so 10/12% threshold
    # duplicates do not inflate method discovery.
    e=e[e["flush_threshold"]==0.08].copy()

    segments={
        "primary_perps":e[(e["kind"]=="perp")&(e["dex"]=="primary")].copy(),
        "all_perps":e[e["kind"]=="perp"].copy(),
        "spot":e[e["kind"]=="spot"].copy(),
        "all_hyperliquid":e.copy(),
    }

    frames=[]
    for name,g in segments.items():
        z=discover_segment(g,name)
        if len(z):frames.append(z)
    allr=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    if len(allr):
        allr=allr.sort_values(["eligible","research_score","hold_n"],ascending=[False,False,False])
    allr.to_csv(OUT/"all_rules.csv",index=False)

    cand=allr[(allr["eligible"]==True)&(allr["novelty"]=="NEW_CANDIDATE")].copy() if len(allr) else pd.DataFrame()
    if len(cand):
        # Deduplicate near-identical feature sets: retain the strongest score per
        # segment + feature family.
        cand["family_key"]=cand["segment"]+"|"+cand["features"]
        cand=cand.sort_values(["research_score","hold_n"],ascending=[False,False]).drop_duplicates("family_key")
        cand=cand.head(30).copy()
    cand.to_csv(OUT/"candidate_shortlist.csv",index=False)

    registry=[]
    for i,(_,r) in enumerate(cand.iterrows(),1):
        registry.append({
            "candidate_id":f"AUTO-{pd.Timestamp.utcnow().strftime('%Y%m%d')}-{i:02d}",
            "status":"SHADOW_ONLY",
            "segment":r["segment"],
            "rule":r["rule"],
            "features":r["features"].split("+"),
            "conditions":json.loads(r["conditions"]),
            "validation":{
                "train_n":int(r["train_n"]),
                "train_hit5":float(r["train_hit5"]),
                "hold_n":int(r["hold_n"]),
                "hold_hit5":float(r["hold_hit5"]),
                "hold_hit10":float(r["hold_hit10"]),
                "hold_t5_before_s7p5":float(r["hold_t5_s7p5"]),
                "hold_wilson_lower":float(r["hold_wilson_lower"]),
                "episodes":int(r["hold_episodes"]),
                "episode_hit5":float(r["episode_hit5"]),
            },
            "promotion_requirements":[
                "independent cross-venue validation",
                "fees/slippage/funding replay",
                "forward shadow signals",
                "manual review before assignment of C-number"
            ]
        })
    (OUT/"candidate_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "source":"Hyperliquid all-pairs 4H research",
        "candidates":registry,
    },indent=2))

    lines=[
        "AUTOMATIC CRYPTO METHOD DISCOVERY",
        "",
        f"Source events: {len(e)}",
        f"Eligible new candidates: {len(cand)}",
        "",
        "Promotion policy: AUTO discoveries are SHADOW_ONLY. They never become C3/C4/etc automatically.",
        "",
        "TOP NEW CANDIDATES",
        cand.head(20).to_string(index=False) if len(cand) else "No candidate passed today's full gate.",
        "",
        "GATES",
        "+5% holdout >= 80%",
        "+5% before -7.5% holdout >= 72%",
        "holdout N >= 20",
        ">=12 market episodes",
        "episode-average +5% >= 76%",
        "Wilson lower bound >= 62%",
        "training +5% >= 78%",
        "stable across >=3 chronological training folds",
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
    main()
