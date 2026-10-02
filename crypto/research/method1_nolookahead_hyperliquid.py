#!/usr/bin/env python3
"""
No-lookahead revalidation of Method 1 (daily blow-off -> first 4H flush).

Critical correction:
Daily setup features are computed from a COMPLETED UTC daily candle. The 4H
scanner is not armed until the next UTC day begins. No 4H bar from the signal
day can use that day's final daily high/close/volatility information.

Scans Hyperliquid primary perps only.
"""
from __future__ import annotations
import argparse, itertools, math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"

def post(s,p,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=p,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(8,1+k))
    raise RuntimeError(str(last))

def universe(s):
    meta,_=post(s,{"type":"metaAndAssetCtxs"})
    return [str(u["name"]) for u in meta.get("universe",[]) if not u.get("isDelisted") and u.get("name")]

def fetch(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                  "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),
                  "c":float(r["c"]),"v":float(r.get("v",0))})
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time").reset_index(drop=True) if a else pd.DataFrame()

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep4(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_pct3"]=x["rsi"]/x["rsi"].shift(3)-1
    x["rsi_delta"]=x["rsi"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["sma9"]=x["c"].rolling(9,min_periods=5).mean()
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["dist_sma9"]=x["c"]/x["sma9"]-1
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["range"]=(x["h"]-x["l"]).replace(0,np.nan)
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/x["range"]
    x["close_location"]=(x["c"]-x["l"])/x["range"]
    x["vol_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["volume_ratio20"]=x["v"]/x["vol_med20"].replace(0,np.nan)
    x["range_pct"]=x["range"]/x["c"].replace(0,np.nan)
    x["range_med20"]=x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"]=x["range_pct"]/x["range_med20"].replace(0,np.nan)
    return x

def daily(x):
    z=x.set_index("time")
    d=pd.DataFrame({
        "o":z["o"].resample("1D").first(),
        "h":z["h"].resample("1D").max(),
        "l":z["l"].resample("1D").min(),
        "c":z["c"].resample("1D").last(),
        "v":z["v"].resample("1D").sum(),
    }).dropna().reset_index()
    d["ret1"]=d["c"].pct_change()
    d["ret5"]=d["c"]/d["c"].shift(5)-1
    d["prior60"]=d["h"].shift(1).rolling(60,min_periods=60).max()
    d["rv20"]=d["ret1"].rolling(20,min_periods=20).std()
    d["rsi"]=rsi(d["c"])
    d["setup"]=(d["ret5"]>=.15)&(d["h"]>=d["prior60"])&(d["rv20"]>=.025)
    # CRITICAL: setup is only knowable AFTER the daily candle completes.
    d["arm_time"]=d["time"]+pd.Timedelta(days=1)
    return d

def setup_events(d,start):
    ev=[];last=None
    for _,r in d[d["setup"]].iterrows():
        t=r["arm_time"]
        if t<start:continue
        if last is None or t-last>=pd.Timedelta(days=10):
            ev.append((t,r));last=t
    return ev

def first_flush(x,arm,dd=.08):
    w=x[(x["time"]>=arm)&(x["time"]<arm+pd.Timedelta(days=5))]
    if len(w)<3:return None
    peak=-math.inf;pi=None
    for idx,r in w.iterrows():
        if float(r["h"])>=peak:
            peak=float(r["h"]);pi=idx
        if pi is None or idx<=pi:continue
        if float(r["c"])/peak-1<=-dd:
            return idx,peak
    return None

def outcome(x,idx):
    loc=x.index.get_loc(idx)
    if loc+1>=len(x):return None
    entry=float(x.iloc[loc+1]["o"])
    f=x.iloc[loc+1:min(len(x),loc+31)]
    if entry<=0 or f.empty:return None
    out={"entry":entry,"entry_time":x.iloc[loc+1]["time"],
         "mfe5d":float(f["h"].max()/entry-1),"mae5d":float(f["l"].min()/entry-1)}
    out["hit5"]=out["mfe5d"]>=.05;out["hit10"]=out["mfe5d"]>=.10
    for stop in (.05,.075,.10):
        tk=sk=None
        for k,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*1.05:tk=k
            if sk is None and float(r["l"])<=entry*(1-stop):sk=k
        out[f"t5_s{stop}"]=tk is not None and (sk is None or tk<sk)
    return out

def scan_coin(x,coin,start):
    d=daily(x)
    rows=[]
    for arm,dr in setup_events(d,pd.Timestamp(start)):
        ff=first_flush(x,arm,.08)
        if ff is None:continue
        idx,peak=ff
        r=x.loc[idx]
        o=outcome(x,idx)
        if o is None:continue
        rows.append({
            "coin":coin,"arm_time":arm,"trigger_time":r["time"],
            "daily_ret5":float(dr["ret5"]),"daily_rv20":float(dr["rv20"]),
            "daily_rsi":float(dr["rsi"]),
            "price_dd":float(r["c"])/peak-1,
            "trigger_rsi":float(r["rsi"]),
            "rsi_pct1":float(r["rsi_pct1"]) if pd.notna(r["rsi_pct1"]) else np.nan,
            "rsi_pct3":float(r["rsi_pct3"]) if pd.notna(r["rsi_pct3"]) else np.nan,
            "rsi_accel":float(r["rsi_accel"]) if pd.notna(r["rsi_accel"]) else np.nan,
            "dist_ema9":float(r["dist_ema9"]) if pd.notna(r["dist_ema9"]) else np.nan,
            "dist_sma9":float(r["dist_sma9"]) if pd.notna(r["dist_sma9"]) else np.nan,
            "dist_sma20":float(r["dist_sma20"]) if pd.notna(r["dist_sma20"]) else np.nan,
            "lower_wick":float(r["lower_wick"]) if pd.notna(r["lower_wick"]) else np.nan,
            "close_location":float(r["close_location"]) if pd.notna(r["close_location"]) else np.nan,
            "volume_ratio20":float(r["volume_ratio20"]) if pd.notna(r["volume_ratio20"]) else np.nan,
            "range_ratio20":float(r["range_ratio20"]) if pd.notna(r["range_ratio20"]) else np.nan,
            **o
        })
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="m1_nolook_out")
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-method1-nolook/1.0"
    coins=universe(s)[args.shard_index::args.shard_count]
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for i,coin in enumerate(coins,1):
        try:
            x=prep4(fetch(s,coin,start-timedelta(days=100),end))
            rr=scan_coin(x,coin,start);rows.extend(rr);cov.append((coin,len(x),len(rr)))
            print(i,coin,len(rr))
        except Exception as ex:
            cov.append((coin,0,0));print("ERR",coin,ex)
        time.sleep(.04)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

if __name__=="__main__":main()
