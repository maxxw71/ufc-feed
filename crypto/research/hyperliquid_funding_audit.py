#!/usr/bin/env python3
"""
Historical Hyperliquid funding audit for unique physical rebound setups.
Perpetuals only. Pulls actual hourly funding rates for each 5-day trade window.
"""
from __future__ import annotations
import argparse,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"

def post(s,payload,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=payload,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError("HTTP %s %s"%(r.status_code,r.text[:200]))
        except Exception as e:last=e
        time.sleep(min(10,1+k))
    raise RuntimeError(str(last))

def load_unique():
    e=pd.read_csv("crypto/research/results_hyperliquid_all_pairs/events.csv")
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    key=["kind","dex","display_name","arm_time"]
    e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
    return e[e["kind"]=="perp"].sort_values("entry_time").reset_index(drop=True)

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    e=load_unique()
    e=e.iloc[args.shard_index::args.shard_count].copy()
    s=requests.Session();s.headers["User-Agent"]="appwiza-hl-funding-audit/1.0"
    rows=[]
    for pos,(_,r) in enumerate(e.iterrows(),1):
        coin=str(r["api_coin"])
        st=pd.Timestamp(r["entry_time"])
        en=st+pd.Timedelta(days=5)
        try:
            hist=post(s,{"type":"fundingHistory","coin":coin,
                         "startTime":int(st.timestamp()*1000),
                         "endTime":int(en.timestamp()*1000)})
            rates=[]
            for x in hist or []:
                try:rates.append(float(x["fundingRate"]))
                except Exception:pass
            total=float(np.sum(rates)) if rates else 0.0
            rows.append({
                "kind":r["kind"],"dex":r["dex"],"display_name":r["display_name"],"api_coin":coin,
                "arm_time":r["arm_time"],"entry_time":r["entry_time"],
                "funding_points":len(rates),"funding_sum_5d":total,
                "funding_mean_hour":float(np.mean(rates)) if rates else np.nan,
                "funding_max_hour":float(np.max(rates)) if rates else np.nan,
                "funding_min_hour":float(np.min(rates)) if rates else np.nan,
                "status":"ok"
            })
        except Exception as exc:
            rows.append({
                "kind":r["kind"],"dex":r["dex"],"display_name":r["display_name"],"api_coin":coin,
                "arm_time":r["arm_time"],"entry_time":r["entry_time"],
                "funding_points":0,"funding_sum_5d":np.nan,
                "status":"error:"+str(exc)[:120]
            })
        print("%d/%d %s"%(pos,len(e),coin))
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"funding_shard_{args.shard_index:02d}.csv",index=False)

def aggregate(args):
    root=Path(args.input_dir);out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in sorted(root.rglob("funding_shard_*.csv")):
        try:fs.append(pd.read_csv(p))
        except Exception:pass
    f=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    e=load_unique()
    for c in ["arm_time","entry_time"]:
        if c in f.columns:f[c]=pd.to_datetime(f[c],utc=True,errors="coerce")
    keys=["kind","dex","display_name","arm_time"]
    m=e.merge(f[keys+["funding_points","funding_sum_5d","funding_mean_hour","funding_max_hour","funding_min_hour","status"]],
              on=keys,how="left")
    m.to_csv(out/"perp_events_with_funding.csv",index=False)
    ok=m[m["status"].eq("ok")].copy() if "status" in m else m.iloc[0:0]
    lines=[
        "HYPERLIQUID HISTORICAL FUNDING AUDIT",
        "",
        f"Unique perp setups: {len(e)}",
        f"Funding successfully retrieved: {len(ok)}",
    ]
    if len(ok):
        s=ok["funding_sum_5d"].dropna()
        lines += [
            f"Median 5d cumulative funding rate: {s.median()*100:.4f}%",
            f"Mean 5d cumulative funding rate: {s.mean()*100:.4f}%",
            f"90th percentile long funding drag: {s.quantile(.90)*100:.4f}%",
            f"95th percentile long funding drag: {s.quantile(.95)*100:.4f}%",
            f"99th percentile long funding drag: {s.quantile(.99)*100:.4f}%",
            f"Worst long funding drag: {s.max()*100:.4f}%",
            f"Best long funding benefit (negative funding): {s.min()*100:.4f}%",
            "",
        ]
        # Chronological later 40% only.
        ok=ok.sort_values("entry_time").reset_index(drop=True)
        cut=max(1,int(len(ok)*.60));h=ok.iloc[cut:]
        hs=h["funding_sum_5d"].dropna()
        lines += [
            "LATER 40% HOLDOUT FUNDING",
            f"N: {len(h)}",
            f"Median: {hs.median()*100:.4f}%",
            f"95th percentile drag: {hs.quantile(.95)*100:.4f}%",
            f"99th percentile drag: {hs.quantile(.99)*100:.4f}%",
        ]
        # Net target after base tier-0 taker fees and actual five-day funding.
        # Positive funding is paid by longs; negative funding helps longs.
        # This assumes position notional remains approximately constant for the funding approximation.
        fee_rt=2*0.00045
        for slip_side in (0.0005,0.0010,0.0025):
            net=0.05-fee_rt-2*slip_side-h["funding_sum_5d"].fillna(0)
            lines += [
                f"Gross +5% net after 0.045% taker/side + {slip_side*100:.2f}% slippage/side + actual funding:",
                f"  median net={net.median()*100:.3f}%  p05 net={net.quantile(.05)*100:.3f}%  min net={net.min()*100:.3f}%"
            ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.35)
    ap.add_argument("--out",default="funding_out")
    ap.add_argument("--input-dir",default="funding_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":main()
