#!/usr/bin/env python3
"""
External Hyperliquid replication of Binance-discovered lower-high / second-dump
/ RSI-oversold rebound rules.

Frozen before Hyperliquid evaluation:
A) RSI<=28, pump>=8%, second dump>=8%, lower-high gap <= -18.411%,
   lower wick >=43.790% of trigger candle range.
B) RSI<=28, pump>=8%, second dump>=8%, price <= -19.126% vs SMA50,
   lower wick >=43.790%.
C) RSI<=30, pump>=10%, second dump>=10%, lower-high gap <= -19.915%,
   first pump >=21.337%.
D) RSI<=30, pump>=10%, second dump>=10%, SMA50 6-bar slope <= -3.026%,
   price <= -8.346% vs EMA9.

No threshold is tuned on Hyperliquid.
"""
from __future__ import annotations
import argparse, math, time
from datetime import datetime, timedelta, timezone
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
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(10,1+k))
    raise RuntimeError(str(last))

def universe(s):
    meta,ctx=post(s,{"type":"metaAndAssetCtxs"})
    out=[]
    for i,u in enumerate(meta.get("universe",[])):
        if u.get("isDelisted"):continue
        name=str(u.get("name",""))
        if not name:continue
        out.append(name)
    return out

def fetch(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),
        "endTime":int(end.timestamp()*1000)
    }})
    rec=[]
    for r in rows or []:
        rec.append({
            "time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
            "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),
            "c":float(r["c"]),"v":float(r.get("v",0))
        })
    if not rec:return pd.DataFrame()
    return pd.DataFrame(rec).drop_duplicates("time").sort_values("time").reset_index(drop=True)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_pct3"]=x["rsi"]/x["rsi"].shift(3)-1
    x["rsi_delta"]=x["rsi"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_vs_sma5"]=x["rsi"]/x["rsi_sma5"]-1
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["sma50"]=x["c"].rolling(50,min_periods=25).mean()
    x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["sma50_slope6"]=x["sma50"]/x["sma50"].shift(6)-1
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["dist_sma50"]=x["c"]/x["sma50"]-1
    x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["range"]=(x["h"]-x["l"]).replace(0,np.nan)
    x["close_location"]=(x["c"]-x["l"])/x["range"]
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/x["range"]
    return x

def local_low_idx(x,start,end):
    if end<start:return None
    s=x.loc[start:end,"l"];return int(s.idxmin()) if len(s) else None

def local_peak_idx(x,start,end):
    if end<start:return None
    s=x.loc[start:end,"h"];return int(s.idxmax()) if len(s) else None

def find_event(x,t,rsi_max,min_pump,min_second_dump):
    if t<90 or pd.isna(x.loc[t,"rsi"]) or float(x.loc[t,"rsi"])>rsi_max:return None
    lo_start=max(55,t-36);lo_end=t-6
    if lo_end<=lo_start:return None
    l1=local_low_idx(x,lo_start,lo_end)
    if l1 is None or l1+2>=t:return None
    if pd.isna(x.loc[l1,"sma50"]) or pd.isna(x.loc[l1,"sma50_slope6"]):return None
    if not (float(x.loc[l1,"c"])<float(x.loc[l1,"sma50"])
            and float(x.loc[l1,"sma20"])<float(x.loc[l1,"sma50"])
            and float(x.loc[l1,"sma50_slope6"])<0):
        return None
    p1=local_peak_idx(x,l1+1,t-2)
    if p1 is None:return None
    low1=float(x.loc[l1,"l"]);peak=float(x.loc[p1,"h"])
    pump=peak/low1-1
    if pump<min_pump:return None
    ph_start=max(0,l1-36);ph_end=l1-2
    if ph_end<=ph_start:return None
    prior_high=float(x.loc[ph_start:ph_end,"h"].max())
    if prior_high<=0 or peak>prior_high:return None
    if t<=p1:return None
    second_dump=float(x.loc[t,"c"])/peak-1
    if second_dump>-min_second_dump:return None
    return {
        "first_low":low1,"pump_peak":peak,"prior_high":prior_high,
        "pump_pct":pump,"lower_high_pct":peak/prior_high-1,
        "second_dump_pct":second_dump,
    }

def outcome(x,t):
    if t+1>=len(x):return None
    entry=float(x.loc[t+1,"o"])
    if entry<=0:return None
    f=x.iloc[t+1:min(len(x),t+1+30)]
    if f.empty:return None
    out={"entry":entry,"entry_time":x.loc[t+1,"time"],
         "mfe5d":float(f["h"].max()/entry-1),
         "mae5d":float(f["l"].min()/entry-1)}
    out["hit5"]=out["mfe5d"]>=.05
    out["hit10"]=out["mfe5d"]>=.10
    for stop in (.05,.075,.10):
        tk=sk=None
        for k,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*1.05:tk=k
            if sk is None and float(r["l"])<=entry*(1-stop):sk=k
        out[f"t5_s{stop}"]=tk is not None and (sk is None or tk<sk)
    return out

FROZEN={
    "A_lowerhigh_wick": lambda r: r["base28"] and r["lower_high_pct"]<=-0.18411 and r["lower_wick"]>=0.43790,
    "B_sma50_wick": lambda r: r["base28"] and r["dist_sma50"]<=-0.19126 and r["lower_wick"]>=0.43790,
    "C_lowerhigh_bigpump": lambda r: r["base30_10"] and r["lower_high_pct"]<=-0.19915 and r["pump_pct"]>=0.21337,
    "D_sma50slope_ema9": lambda r: r["base30_10"] and r["sma50_slope6"]<=-0.03026 and r["dist_ema9"]<=-0.08346,
}

def scan_coin(x,coin):
    recs=[]
    last_by_base={"base28":None,"base30_10":None}
    for t in range(90,len(x)-31):
        ev28=find_event(x,t,28,.08,.08)
        ev30=find_event(x,t,30,.10,.10)
        if ev28 is None and ev30 is None:continue

        base28=False;base30=False;ev=None
        if ev28 is not None:
            if last_by_base["base28"] is None or x.loc[t,"time"]-last_by_base["base28"]>=pd.Timedelta(days=5):
                base28=True;last_by_base["base28"]=x.loc[t,"time"];ev=ev28
        if ev30 is not None:
            if last_by_base["base30_10"] is None or x.loc[t,"time"]-last_by_base["base30_10"]>=pd.Timedelta(days=5):
                base30=True;last_by_base["base30_10"]=x.loc[t,"time"]
                if ev is None:ev=ev30
        if not base28 and not base30:continue
        o=outcome(x,t)
        if o is None:continue
        r={
            "coin":coin,"trigger_time":x.loc[t,"time"],
            "trigger_rsi":float(x.loc[t,"rsi"]),
            "dist_sma50":float(x.loc[t,"dist_sma50"]) if pd.notna(x.loc[t,"dist_sma50"]) else np.nan,
            "dist_ema9":float(x.loc[t,"dist_ema9"]) if pd.notna(x.loc[t,"dist_ema9"]) else np.nan,
            "sma50_slope6":float(x.loc[t,"sma50_slope6"]) if pd.notna(x.loc[t,"sma50_slope6"]) else np.nan,
            "lower_wick":float(x.loc[t,"lower_wick"]) if pd.notna(x.loc[t,"lower_wick"]) else np.nan,
            "base28":base28,"base30_10":base30,
            **ev,**o
        }
        for name,fn in FROZEN.items():r[name]=bool(fn(r))
        recs.append(r)
    return recs

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="hl_lower_high_out")
    args=ap.parse_args()

    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-hl-lower-high-replication/1.0"
    coins=universe(s)
    coins=coins[args.shard_index::args.shard_count]
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for i,coin in enumerate(coins,1):
        try:
            x=prep(fetch(s,coin,start,end))
            if len(x)<150:
                cov.append((coin,len(x),0));continue
            rr=scan_coin(x,coin);rows.extend(rr);cov.append((coin,len(x),len(rr)))
            print(i,coin,len(rr))
        except Exception as ex:
            cov.append((coin,0,0));print("ERR",coin,ex)
        time.sleep(.05)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["coin","bars","events"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

if __name__=="__main__":main()
