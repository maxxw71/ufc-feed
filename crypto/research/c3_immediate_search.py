#!/usr/bin/env python3
from pathlib import Path
from math import sqrt
import itertools,json
import numpy as np
import pandas as pd

SRC=Path("crypto/research/results_method1_nolookahead_hyperliquid/events.csv")
OUT=Path("crypto/research/results_c3_search")
OUT.mkdir(parents=True,exist_ok=True)

FEATURES=[
 ("volume_ratio20","high"),
 ("range_ratio20","high"),
 ("lower_wick","high"),
 ("close_location","low"),
 ("trigger_rsi","low"),
 ("rsi_pct3","low"),
 ("dist_ema9","low"),
 ("dist_sma9","low"),
 ("dist_sma20","low"),
 ("daily_ret5","high"),
 ("daily_rv20","high"),
 ("daily_rsi","high"),
 ("price_dd","low"),
]
PRIMARY="hit5"
RISK="t5_s0.075"

def rate(s):
 x=s.dropna()
 return float(x.astype(bool).mean()) if len(x) else np.nan

def wilson(w,n,z=1.96):
 if n<=0:return np.nan
 p=w/n;den=1+z*z/n
 ctr=(p+z*z/(2*n))/den
 half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
 return ctr-half

def episodes(df):
 x=df.sort_values("trigger_time").copy();eid=0;last=None;ids=[]
 for t in x["trigger_time"]:
  if last is None or t-last>pd.Timedelta(hours=18):eid+=1
  ids.append(eid);last=t
 x["episode_id"]=ids
 return x

def spec_name(s):
 return f"{s[0]} {s[1]} {s[2]:.5g}"

def mask(df,s):
 f,op,v=s
 x=pd.to_numeric(df[f],errors="coerce").replace([np.inf,-np.inf],np.nan)
 return x>=v if op==">=" else x<=v

def main():
 e=pd.read_csv(SRC)
 for c in ["arm_time","trigger_time","entry_time"]:
  e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
 e=e.sort_values("entry_time").reset_index(drop=True)
 cut=max(1,int(len(e)*.60))
 tr=e.iloc[:cut].copy();ho=e.iloc[cut:].copy()

 specs=[]
 for f,d in FEATURES:
  s=pd.to_numeric(tr[f],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
  if len(s)<100:continue
  for q in [.15,.20,.25,.30,.35,.40,.50,.60,.65,.70,.75,.80,.85]:
   specs.append((f,">=" if d=="high" else "<=",float(s.quantile(q))))

 singles=[]
 for s in specs:
  g=tr[mask(tr,s)]
  if len(g)<45:continue
  score=.65*rate(g[PRIMARY])+.35*rate(g[RISK])
  singles.append((score,len(g),s))
 singles.sort(reverse=True,key=lambda x:(x[0],x[1]))
 top=[x[2] for x in singles[:35]]

 combos=[(x,) for x in top]
 combos += [(a,b) for a,b in itertools.combinations(top,2) if a[0]!=b[0]]
 top3=top[:12]
 combos += [(a,b,c) for a,b,c in itertools.combinations(top3,3) if len({a[0],b[0],c[0]})==3]

 rows=[];seen=set()
 for combo in combos:
  name=" AND ".join(spec_name(s) for s in combo)
  if name in seen:continue
  seen.add(name)
  mt=pd.Series(True,index=tr.index);mh=pd.Series(True,index=ho.index)
  for s in combo:
   mt&=mask(tr,s);mh&=mask(ho,s)
  gt,gh=tr[mt],ho[mh]
  if len(gt)<40 or len(gh)<20:continue
  hit=rate(gh[PRIMARY]);risk=rate(gh[RISK]);hit10=rate(gh["hit10"])
  ep=episodes(gh);ep_rates=ep.groupby("episode_id")[PRIMARY].mean()
  w=int(gh[PRIMARY].fillna(False).astype(bool).sum())
  rows.append({
   "rule":name,
   "features":"+".join(s[0] for s in combo),
   "train_n":len(gt),"train_hit5":rate(gt[PRIMARY]),"train_t5_s7p5":rate(gt[RISK]),
   "hold_n":len(gh),"hold_hit5":hit,"hold_hit10":hit10,"hold_t5_s7p5":risk,
   "hold_wilson_lower":wilson(w,len(gh)),
   "hold_episodes":int(ep["episode_id"].nunique()),
   "episode_hit5":float(ep_rates.mean()) if len(ep_rates) else np.nan,
   "median_mfe":float(gh["mfe5d"].median()),"median_mae":float(gh["mae5d"].median()),
  })
 z=pd.DataFrame(rows)
 if len(z):
  z["score"]=.45*z["hold_hit5"]+.30*z["hold_t5_s7p5"]+.15*z["episode_hit5"]+.10*z["hold_wilson_lower"]
  z["passes"]=(z["hold_hit5"]>=.85)&(z["hold_t5_s7p5"]>=.75)&(z["hold_n"]>=20)&(z["hold_episodes"]>=12)&(z["train_hit5"]>=.80)
  z=z.sort_values(["passes","score","hold_n"],ascending=[False,False,False])
 z.to_csv(OUT/"candidates.csv",index=False)
 passing=z[z["passes"]] if len(z) else pd.DataFrame()
 best=passing.iloc[0].to_dict() if len(passing) else None
 (OUT/"best_candidate.json").write_text(json.dumps(best,indent=2,default=str))
 lines=[
  "C3 IMMEDIATE SEARCH — CORRECTED NO-LOOKAHEAD HYPERLIQUID",
  "",
  f"events={len(e)} train={len(tr)} holdout={len(ho)}",
  f"passing_candidates={len(passing)}",
  "",
  "BEST",
  json.dumps(best,indent=2,default=str) if best else "none",
  "",
  "TOP 20",
  passing.head(20).to_string(index=False) if len(passing) else "none",
  "",
  "NOTE: This is a new SHADOW candidate only until independent Binance validation and forward tracking."
 ]
 (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
 print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":
 main()
