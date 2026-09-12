#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
AGE="$ROOT/historical_backtest/master_2006_2026/hybrid_master_2006_2026.csv"
REACH="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
STATS="$ROOT/skill_veto_all_methods/all_original_signals_with_skill_veto.csv"
OUTDIR="$ROOT/skill_veto_official_samples"
mkdir -p "$OUTDIR"

for f in "$AGE" "$REACH" "$STATS"; do
  if [ ! -f "$f" ]; then
    echo "ERROR: Missing $f"
    exit 2
  fi
done

source "$ROOT/venv/bin/activate"

python - "$AGE" "$REACH" "$STATS" "$OUTDIR" <<'PY'
import sys,re,unicodedata
from pathlib import Path
import numpy as np
import pandas as pd

age_path=Path(sys.argv[1])
reach_path=Path(sys.argv[2])
stats_path=Path(sys.argv[3])
outdir=Path(sys.argv[4])

def norm(v):
    v=unicodedata.normalize("NFKD",str(v))
    v="".join(ch for ch in v if not unicodedata.combining(ch))
    v=re.sub(r"[^a-z0-9 ]+"," ",v.lower())
    return " ".join(v.split())

def boolify(s):
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False)
    return s.astype(str).str.lower().map(
        {"true":True,"1":True,"yes":True,"false":False,"0":False,"no":False}
    ).fillna(False).astype(bool)

stats=pd.read_csv(stats_path,low_memory=False)
stats["event_date"]=pd.to_datetime(stats["event_date"],errors="coerce").dt.normalize()
stats["favorite_won"]=boolify(stats["favorite_won"])
for c in ["diff_sig_diff_pm","diff_td_def","market_prob","profit_100","fav_younger_by","fav_reach_adv"]:
    if c in stats:
        stats[c]=pd.to_numeric(stats[c],errors="coerce")
stats["favn"]=stats["favorite"].map(norm)
stats["dogn"]=stats["underdog"].map(norm)
stats["skill_veto"]=(stats["diff_sig_diff_pm"]<=-1.0)|(stats["diff_td_def"]<=-0.20)

skill=stats[[
    "event_date","favn","dogn","favorite","underdog",
    "diff_sig_diff_pm","diff_td_def","skill_veto"
]].drop_duplicates(["event_date","favn","dogn"])

def metric(g, profit_col):
    n=len(g)
    if not n:
        return dict(n=0,w=0,l=0,roi=np.nan,p=0)
    if "favorite_won" in g.columns:
        w=int(g["favorite_won"].sum())
    elif "won" in g.columns:
        w=int(g["won"].sum())
    else:
        w=np.nan
    p=float(pd.to_numeric(g[profit_col],errors="coerce").fillna(0).sum())
    return dict(n=n,w=w,l=n-w if not pd.isna(w) else np.nan,roi=p/(100*n),p=p)

def merge_skill(df,date_col,fav_col,dog_col):
    z=df.copy()
    z["event_date"]=pd.to_datetime(z[date_col],errors="coerce").dt.normalize()
    z["favn"]=z[fav_col].map(norm)
    z["dogn"]=z[dog_col].map(norm)
    z=z.merge(skill,on=["event_date","favn","dogn"],how="left",suffixes=("","_skill"))
    return z

lines=[]
summary=[]

age=pd.read_csv(age_path,low_memory=False)
cols=set(age.columns)
date_col=next(c for c in ["event_date","date","fight_date"] if c in cols)
fav_col=next(c for c in ["favorite","fav_name","fighter","fighter_name"] if c in cols)
dog_col=next(c for c in ["underdog","dog_name","opponent","opponent_name"] if c in cols)
profit_col=next(c for c in ["profit_100","profit","pl_100"] if c in cols)

if "favorite_won" in age.columns:
    age["favorite_won"]=boolify(age["favorite_won"])
elif "won" in age.columns:
    age["favorite_won"]=boolify(age["won"])
elif "result" in age.columns:
    age["favorite_won"]=age["result"].astype(str).str.lower().isin(["win","w","1","true"])
else:
    raise RuntimeError("Cannot find win column in Age master.")

age=merge_skill(age,date_col,fav_col,dog_col)
base=metric(age,profit_col)
kept=age[~age.skill_veto.fillna(False)].copy()
vetoed=age[age.skill_veto.fillna(False)].copy()
km=metric(kept,profit_col); vm=metric(vetoed,profit_col)

reach=pd.read_csv(reach_path,low_memory=False)
rcols=set(reach.columns)
rdate=next(c for c in ["event_date","date","fight_date"] if c in rcols)
rfav=next(c for c in ["favorite","fav_name","favorite_name"] if c in rcols)
rdog=next(c for c in ["underdog","dog_name","underdog_name"] if c in rcols)

for c in ["market_prob","fav_reach_adv","profit_100"]:
    if c in reach.columns:
        reach[c]=pd.to_numeric(reach[c],errors="coerce")
if "favorite_won" in reach.columns:
    reach["favorite_won"]=boolify(reach["favorite_won"])

reach_off=reach[(reach["market_prob"]>=.65)&(reach["fav_reach_adv"]>=4)].copy()
if len(reach_off)!=415:
    print(f"WARNING: Reach official filter produced {len(reach_off)} rows, expected 415.")

reach_off=merge_skill(reach_off,rdate,rfav,rdog)
rb=metric(reach_off,"profit_100")
rk=metric(reach_off[~reach_off.skill_veto.fillna(False)],"profit_100")
rv=metric(reach_off[reach_off.skill_veto.fillna(False)],"profit_100")

if "fav_younger_by" not in reach.columns:
    raise RuntimeError("Reach sample lacks fav_younger_by needed for Premium.")
premium=reach[
    (reach["market_prob"]>=.70)&
    (reach["fav_younger_by"]>=3)&
    (reach["fav_reach_adv"]>=4)
].copy()
if len(premium)!=133:
    print(f"WARNING: Premium official filter produced {len(premium)} rows, expected 133.")

premium=merge_skill(premium,rdate,rfav,rdog)
pb=metric(premium,"profit_100")
pk=metric(premium[~premium.skill_veto.fillna(False)],"profit_100")
pv=metric(premium[premium.skill_veto.fillna(False)],"profit_100")

def add(name,b,k,v):
    removed=int(v["n"])
    removed_losses=int(v["l"]) if not pd.isna(v["l"]) else np.nan
    removed_wins=int(v["w"]) if not pd.isna(v["w"]) else np.nan
    lines.append(name)
    lines.append("-"*108)
    lines.append(
        f"ORIGINAL: {b['n']} bets | {int(b['w'])}-{int(b['l'])} | "
        f"ROI={b['roi']*100:+.2f}% | P/L=${b['p']:+,.2f}"
    )
    lines.append(
        f"WITH SKILL VETO: {k['n']} bets | {int(k['w'])}-{int(k['l'])} | "
        f"ROI={k['roi']*100:+.2f}% | P/L=${k['p']:+,.2f}"
    )
    lines.append(f"ROI CHANGE: {(k['roi']-b['roi'])*100:+.2f}pp")
    lines.append(
        f"REMOVED: {removed} signals = {removed_wins} winners + {removed_losses} losses "
        f"| removed-group ROI={v['roi']*100:+.2f}%"
    )
    lines.append("")
    summary.append({
        "method":name,
        "baseline_bets":b["n"],"baseline_wins":b["w"],"baseline_losses":b["l"],
        "baseline_roi":b["roi"],"baseline_profit":b["p"],
        "filtered_bets":k["n"],"filtered_wins":k["w"],"filtered_losses":k["l"],
        "filtered_roi":k["roi"],"filtered_profit":k["p"],
        "roi_change_pp":(k["roi"]-b["roi"])*100,
        "removed_bets":v["n"],"removed_wins":v["w"],"removed_losses":v["l"],
        "removed_roi":v["roi"]
    })

lines.append("SKILL VETO — OFFICIAL HISTORICAL SAMPLES")
lines.append("="*108)
lines.append("Veto if favorite is either:")
lines.append("  >=1.0 sig strike/min worse than opponent, OR")
lines.append("  >=20 percentage points worse in takedown defense")
lines.append("")

add("AGE HYBRID — OFFICIAL MASTER",base,km,vm)
add("REACH HYBRID — OFFICIAL 415",rb,rk,rv)
add("AGE + REACH PREMIUM — OFFICIAL 133",pb,pk,pv)

pd.DataFrame(summary).to_csv(outdir/"official_skill_veto_summary.csv",index=False)
age.to_csv(outdir/"age_889_with_skill_veto.csv",index=False)
reach_off.to_csv(outdir/"reach_415_with_skill_veto.csv",index=False)
premium.to_csv(outdir/"premium_133_with_skill_veto.csv",index=False)

report="\n".join(lines)+"\n"
(outdir/"report.txt").write_text(report)
print(report)
print(f"Saved: {outdir/'report.txt'}")
print(f"Saved: {outdir/'official_skill_veto_summary.csv'}")
PY
