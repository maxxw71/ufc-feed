#!/usr/bin/env python3
"""
Frozen external validation of C3 and C3+Funding on Binance USD-M perpetuals.

No Binance threshold tuning:
C3:
- completed daily breakout regime (5d >=15%, new 60d high, RV20 >=2.5%)
- arm only after daily candle closes
- first >=8% 4H close drawdown from post-arm peak
- trigger candle close location <= 0.1527
- 4H volume >= 1.8296x its 20-bar median
C3+Funding:
- all C3 conditions
- last published Binance funding rate at/before trigger >= 0.000013

Primary endpoint: +5% within 5d.
Risk endpoint: +5% before -7.5%, same-bar target+stop conservatively counts as not target-first.
"""
from __future__ import annotations

import argparse, json, math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE="https://fapi.binance.com"
CLOSE_MAX=0.1527
VOL_MIN=1.8296
FUNDING_MIN=0.000013
STABLE={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}

def getj(s,path,params=None,tries=8):
    err=None
    for k in range(tries):
        try:
            r=s.get(BASE+path,params=params,timeout=30)
            if r.ok:
                return r.json()
            err=RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code in (418,429,451):
                time.sleep(min(20,2+k*2))
            else:
                time.sleep(min(8,1+k))
        except Exception as e:
            err=e
            time.sleep(min(8,1+k))
    raise err or RuntimeError("request failed")

def universe(s,n):
    info=getj(s,"/fapi/v1/exchangeInfo")
    tick=getj(s,"/fapi/v1/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    out=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT" or x.get("contractType")!="PERPETUAL":
            continue
        b=str(x.get("baseAsset") or "")
        if b in STABLE or b.startswith("USD"):
            continue
        out.append(x["symbol"])
    out.sort(key=lambda z:qv.get(z,0),reverse=True)
    return out[:n]

def klines(s,symbol,interval,start,end):
    step={"1d":86400000,"4h":14400000}[interval]
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[]
    while cur<stop:
        batch=getj(s,"/fapi/v1/klines",{
            "symbol":symbol,"interval":interval,"startTime":cur,"endTime":stop,"limit":1500
        })
        if not batch: break
        rows.extend(batch)
        nxt=int(batch[-1][0])+step
        if nxt<=cur: break
        cur=nxt
        if len(batch)<1500: break
        time.sleep(.04)
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
    if not rows: return pd.DataFrame(columns=cols)
    d=pd.DataFrame(rows,columns=cols)
    for c in ["open","high","low","close","volume","quote_volume"]:
        d[c]=pd.to_numeric(d[c],errors="coerce")
    d["open_time"]=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    d["close_time"]=pd.to_datetime(d["close_time"],unit="ms",utc=True)
    return d.dropna(subset=["open","high","low","close"]).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def daily_arms(d):
    x=d.copy()
    x["ret1"]=x["close"].pct_change()
    x["ret5"]=x["close"]/x["close"].shift(5)-1
    x["prior60"]=x["high"].shift(1).rolling(60,min_periods=60).max()
    x["rv20"]=x["ret1"].rolling(20,min_periods=20).std()
    x["setup"]=(x["ret5"]>=.15)&(x["high"]>=x["prior60"])&(x["rv20"]>=.025)
    out=[];last=None
    for _,r in x[x["setup"]].iterrows():
        # close_time is ex-ante arm timestamp.
        t=pd.Timestamp(r["close_time"])
        if last is None or t-last>=pd.Timedelta(days=10):
            out.append(t);last=t
    return out

def prep4(h):
    x=h.copy()
    rng=(x["high"]-x["low"]).replace(0,np.nan)
    x["close_location"]=(x["close"]-x["low"])/rng
    x["volume_med20"]=x["volume"].rolling(20,min_periods=10).median()
    x["volume_ratio20"]=x["volume"]/x["volume_med20"].replace(0,np.nan)
    return x

def first_c3(h,arm):
    w=h[(h["open_time"]>=arm)&(h["open_time"]<arm+pd.Timedelta(days=5))]
    if len(w)<4:return None
    peak=-math.inf;pi=None
    for idx,r in w.iterrows():
        hi=float(r["high"])
        if hi>=peak:
            peak=hi;pi=idx
        if pi is None or idx<=pi or peak<=0:continue
        if float(r["close"])/peak-1<=-.08:
            loc=h.index.get_loc(idx)
            if loc+1>=len(h):return None
            if pd.isna(r["close_location"]) or pd.isna(r["volume_ratio20"]):return None
            if float(r["close_location"])>CLOSE_MAX or float(r["volume_ratio20"])<VOL_MIN:return None
            entry=float(h.iloc[loc+1]["open"])
            fut=h.iloc[loc+1:min(len(h),loc+31)]
            if fut.empty or entry<=0:return None
            tk=sk=None
            for k,(_,b) in enumerate(fut.iterrows()):
                if tk is None and float(b["high"])>=entry*1.05:tk=k
                if sk is None and float(b["low"])<=entry*.925:sk=k
            return {
                "trigger_time":pd.Timestamp(r["open_time"]),
                "entry_time":pd.Timestamp(h.iloc[loc+1]["open_time"]),
                "entry":entry,
                "close_location":float(r["close_location"]),
                "volume_ratio20":float(r["volume_ratio20"]),
                "mfe5d":float(fut["high"].max()/entry-1),
                "mae5d":float(fut["low"].min()/entry-1),
                "hit5":bool(float(fut["high"].max())>=entry*1.05),
                "hit10":bool(float(fut["high"].max())>=entry*1.10),
                "t5_s7p5":bool(tk is not None and (sk is None or tk<sk)),
            }
    return None

def funding_at(s,symbol,t):
    end=int(pd.Timestamp(t).timestamp()*1000)
    start=int((pd.Timestamp(t)-pd.Timedelta(hours=24)).timestamp()*1000)
    rows=getj(s,"/fapi/v1/fundingRate",{"symbol":symbol,"startTime":start,"endTime":end,"limit":100})
    valid=[x for x in rows if int(x.get("fundingTime",0))<=end]
    if not valid:return np.nan
    x=max(valid,key=lambda z:int(z.get("fundingTime",0)))
    return float(x["fundingRate"])

def rate(s):
    x=s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan

def summary(g):
    if g is None or len(g)==0:
        return {"n":0,"hit5":None,"hit10":None,"t5_s7p5":None,"median_mfe":None,"median_mae":None}
    return {
        "n":len(g),"hit5":rate(g["hit5"]),"hit10":rate(g["hit10"]),
        "t5_s7p5":rate(g["t5_s7p5"]),
        "median_mfe":float(g["mfe5d"].median()),"median_mae":float(g["mae5d"].median())
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbols",type=int,default=120)
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--out",default="crypto/research/results_c3_funding_binance_perps")
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)

    now=datetime.now(timezone.utc);start=now-timedelta(days=args.days)
    s=requests.Session();s.headers["User-Agent"]="appwiza-c3-funding-binance-perps/1.0"
    syms=universe(s,args.symbols)
    rec=[]
    for i,sym in enumerate(syms,1):
        try:
            d=klines(s,sym,"1d",start,now)
            h=prep4(klines(s,sym,"4h",start,now))
            for arm in daily_arms(d):
                ev=first_c3(h,arm)
                if ev:
                    try:ev["funding_rate_at_trigger"]=funding_at(s,sym,ev["trigger_time"])
                    except Exception as ex:
                        print("FUNDING_ERR",sym,ev["trigger_time"],str(ex)[:100])
                        ev["funding_rate_at_trigger"]=np.nan
                    rec.append({"symbol":sym,**ev})
            print(i,len(syms),sym,"events",len(rec))
        except Exception as ex:
            print("ERR",sym,str(ex)[:160])
        time.sleep(.05)

    z=pd.DataFrame(rec)
    if len(z):
        z=z.sort_values("entry_time").reset_index(drop=True)
    z.to_csv(out/"events.csv",index=False)

    cut=max(1,int(len(z)*.60)) if len(z) else 0
    tr=z.iloc[:cut] if len(z) else z
    ho=z.iloc[cut:] if len(z) else z
    trf=tr[tr["funding_rate_at_trigger"]>=FUNDING_MIN] if len(tr) else tr
    hof=ho[ho["funding_rate_at_trigger"]>=FUNDING_MIN] if len(ho) else ho

    result={
        "venue":"Binance USD-M perpetuals",
        "symbols_requested":args.symbols,
        "days":args.days,
        "c3_rule":{"close_location_max":CLOSE_MAX,"volume_ratio20_min":VOL_MIN},
        "funding_rule":{"funding_rate_at_trigger_min":FUNDING_MIN},
        "funding_coverage":float(z["funding_rate_at_trigger"].notna().mean()) if len(z) else 0,
        "c3_all":summary(z),
        "c3_train":summary(tr),
        "c3_holdout":summary(ho),
        "c3_funding_all":summary(z[z["funding_rate_at_trigger"]>=FUNDING_MIN]) if len(z) else summary(z),
        "c3_funding_train":summary(trf),
        "c3_funding_holdout":summary(hof),
    }
    h=result["c3_funding_holdout"]
    result["similar_to_hyperliquid"]=bool(
        h["n"] is not None and h["n"]>=15 and
        h["hit5"] is not None and h["hit5"]>=.85 and
        h["t5_s7p5"] is not None and h["t5_s7p5"]>=.78
    )
    (out/"REPORT.json").write_text(json.dumps(result,indent=2,default=str))
    (out/"REPORT.txt").write_text(json.dumps(result,indent=2,default=str)+"\n")
    print(json.dumps(result,indent=2,default=str))

if __name__=="__main__":
    main()
