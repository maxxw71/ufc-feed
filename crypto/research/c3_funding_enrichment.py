#!/usr/bin/env python3
from __future__ import annotations
import json,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_method1_nolookahead_hyperliquid/events.csv")
OUT=Path("crypto/research/results_c3_funding_enrichment")
OUT.mkdir(parents=True,exist_ok=True)
CLOSE_MAX=.1527
VOL_MIN=1.8296

def post(s,p,tries=8):
    err=None
    for i in range(tries):
        try:
            r=s.post(INFO,json=p,timeout=30)
            if r.ok:return r.json()
            err=RuntimeError(f"HTTP {r.status_code} {r.text[:160]}")
        except Exception as e:err=e
        time.sleep(min(15,2+i*2))
    raise err or RuntimeError("request failed")

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def main():
    all_events=pd.read_csv(SRC)
    for c in ["trigger_time","entry_time","arm_time"]:
        all_events[c]=pd.to_datetime(all_events[c],utc=True,errors="coerce")
    all_events=all_events.sort_values("entry_time").reset_index(drop=True)
    all_events["global_order"]=np.arange(len(all_events))
    global_cut=max(1,int(len(all_events)*.60))
    e=all_events[(all_events["close_location"]<=CLOSE_MAX)&(all_events["volume_ratio20"]>=VOL_MIN)].copy().reset_index(drop=True)
    s=requests.Session()
    s.headers["User-Agent"]="appwiza-c3-funding-enrichment/1.0"
    funding=[];premium=[]
    for _,r in e.iterrows():
        t=pd.Timestamp(r["trigger_time"])
        try:
            rows=post(s,{"type":"fundingHistory","coin":str(r["coin"]),
                         "startTime":int((t-pd.Timedelta(hours=6)).timestamp()*1000),
                         "endTime":int(t.timestamp()*1000)})
            valid=[x for x in rows if int(x.get("time",0))<=int(t.timestamp()*1000)]
            x=max(valid,key=lambda q:int(q.get("time",0))) if valid else None
            funding.append(float(x["fundingRate"]) if x and x.get("fundingRate") is not None else np.nan)
            premium.append(float(x["premium"]) if x and x.get("premium") is not None else np.nan)
        except Exception as ex:
            print("ERR",r["coin"],str(ex)[:120])
            funding.append(np.nan);premium.append(np.nan)
        time.sleep(1.02)
    e["funding_rate_at_trigger"]=funding
    e["premium_at_trigger"]=premium
    e.to_csv(OUT/"events.csv",index=False)

    tr=e[e["global_order"]<global_cut].copy()
    ho=e[e["global_order"]>=global_cut].copy()
    rows=[]
    for feat in ["funding_rate_at_trigger","premium_at_trigger"]:
        clean=tr[feat].replace([np.inf,-np.inf],np.nan).dropna()
        if len(clean)<20:continue
        for q in [.2,.35,.5,.65,.8]:
            v=float(clean.quantile(q))
            for op in ["<=",">="]:
                mt=tr[feat]<=v if op=="<=" else tr[feat]>=v
                mh=ho[feat]<=v if op=="<=" else ho[feat]>=v
                gt,gh=tr[mt],ho[mh]
                if len(gt)<15 or len(gh)<8:continue
                rows.append({"feature":feat,"op":op,"value":v,
                             "train_n":len(gt),"train_hit5":rate(gt["hit5"]),"train_t5_s7p5":rate(gt["t5_s0.075"]),
                             "hold_n":len(gh),"hold_hit5":rate(gh["hit5"]),"hold_t5_s7p5":rate(gh["t5_s0.075"]),
                             "hold_hit10":rate(gh["hit10"])})
    z=pd.DataFrame(rows)
    if len(z):
        z["score"]=.65*z["hold_hit5"]+.35*z["hold_t5_s7p5"]
        z=z.sort_values(["score","hold_n"],ascending=[False,False])
    z.to_csv(OUT/"funding_filters.csv",index=False)
    base={"n":len(e),"train_n":len(tr),"hold_n":len(ho),
          "hold_hit5":rate(ho["hit5"]),"hold_t5_s7p5":rate(ho["t5_s0.075"]),
          "funding_coverage":float(e["funding_rate_at_trigger"].notna().mean()),
          "premium_coverage":float(e["premium_at_trigger"].notna().mean())}
    lines=["C3 FUNDING/PREMIUM ENRICHMENT","",json.dumps(base,indent=2),"",
           "TOP TRAIN-FROZEN FUNDING/PREMIUM FILTERS",
           z.head(20).to_string(index=False) if len(z) else "none"]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":main()
