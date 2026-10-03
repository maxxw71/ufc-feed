#!/usr/bin/env python3
from __future__ import annotations
import math,time,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://data-api.binance.vision"
OUT=Path("crypto/research/results_c3_binance_validation");OUT.mkdir(parents=True,exist_ok=True)
STABLE={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}
CLOSE_MAX=0.1527
VOL_MIN=1.8296

def get(s,path,params=None,tries=6):
    err=None
    for i in range(tries):
        try:
            r=s.get(API+path,params=params,timeout=25)
            if r.ok:return r.json()
            err=RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        except Exception as e:err=e
        time.sleep(min(8,1.5**i))
    raise err or RuntimeError("request failed")

def universe(s,n=160):
    info=get(s,"/api/v3/exchangeInfo");tick=get(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and x.get("symbol")}
    out=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=x.get("baseAsset","")
        if b in STABLE or b.startswith("USD") or b.startswith("1000"):continue
        if any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        out.append(x["symbol"])
    out.sort(key=lambda x:qv.get(x,0),reverse=True)
    return out[:n]

def klines(s,symbol,interval,start,end):
    step={"1d":86400000,"4h":14400000}[interval]
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[]
    while cur<stop:
        b=get(s,"/api/v3/klines",{"symbol":symbol,"interval":interval,"startTime":cur,"endTime":stop,"limit":1000})
        if not b:break
        rows.extend(b);nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1000:break
        time.sleep(.02)
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
    if not rows:return pd.DataFrame(columns=cols)
    d=pd.DataFrame(rows,columns=cols)
    for c in ["open","high","low","close","volume"]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d["open_time"]=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    d["close_time"]=pd.to_datetime(d["close_time"],unit="ms",utc=True)
    return d.dropna(subset=["open","high","low","close"]).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def prep_daily(d):
    x=d.copy();x["ret1"]=x["close"].pct_change();x["ret5"]=x["close"]/x["close"].shift(5)-1
    x["prior60"]=x["high"].shift(1).rolling(60,min_periods=60).max()
    x["rv20"]=x["ret1"].rolling(20,min_periods=20).std()
    x["setup"]=(x["ret5"]>=.15)&(x["high"]>=x["prior60"])&(x["rv20"]>=.025)
    arms=[];last=None
    for _,r in x[x["setup"]].iterrows():
        t=r["close_time"]
        if last is None or t-last>=pd.Timedelta(days=10):
            arms.append(t);last=t
    return arms

def prep4(h):
    x=h.copy()
    den=(x["high"]-x["low"]).replace(0,np.nan)
    x["close_location"]=(x["close"]-x["low"])/den
    x["volume_med20"]=x["volume"].rolling(20,min_periods=10).median()
    x["volume_ratio20"]=x["volume"]/x["volume_med20"].replace(0,np.nan)
    return x

def event(h,arm):
    w=h[(h["open_time"]>=arm)&(h["open_time"]<arm+pd.Timedelta(days=5))]
    if len(w)<4:return None
    peak=-math.inf;pi=None
    for idx,r in w.iterrows():
        if float(r["high"])>=peak:peak=float(r["high"]);pi=idx
        if pi is not None and idx>pi and float(r["close"])/peak-1<=-.08:
            loc=h.index.get_loc(idx)
            if loc+1>=len(h):return None
            row=h.loc[idx]
            if pd.isna(row["close_location"]) or pd.isna(row["volume_ratio20"]):return None
            if float(row["close_location"])>CLOSE_MAX or float(row["volume_ratio20"])<VOL_MIN:return None
            entry=float(h.iloc[loc+1]["open"])
            fut=h.iloc[loc+1:min(len(h),loc+31)]
            if fut.empty:return None
            tk=sk=None
            for k,(_,b) in enumerate(fut.iterrows()):
                if tk is None and float(b["high"])>=entry*1.05:tk=k
                if sk is None and float(b["low"])<=entry*.925:sk=k
            return {
                "trigger_time":row["open_time"],"entry_time":h.iloc[loc+1]["open_time"],"entry":entry,
                "close_location":float(row["close_location"]),"volume_ratio20":float(row["volume_ratio20"]),
                "mfe5d":float(fut["high"].max()/entry-1),"mae5d":float(fut["low"].min()/entry-1),
                "hit5":bool(float(fut["high"].max())>=entry*1.05),
                "hit10":bool(float(fut["high"].max())>=entry*1.10),
                "t5_s7p5":bool(tk is not None and (sk is None or tk<sk)),
            }
    return None

def rate(s):
    return float(s.astype(bool).mean()) if len(s) else np.nan

def main():
    end=datetime.now(timezone.utc);start=end-timedelta(days=900)
    s=requests.Session();s.headers["User-Agent"]="appwiza-c3-binance-validation/1.0"
    rows=[];syms=universe(s,160)
    for i,sym in enumerate(syms,1):
        try:
            d=klines(s,sym,"1d",start,end);h=prep4(klines(s,sym,"4h",start,end))
            for arm in prep_daily(d):
                ev=event(h,arm)
                if ev:rows.append({"symbol":sym,**ev})
            print(i,sym,len(rows))
        except Exception as e:print("ERR",sym,str(e)[:120])
        time.sleep(.02)
    z=pd.DataFrame(rows)
    if len(z):z=z.sort_values("entry_time").reset_index(drop=True)
    z.to_csv(OUT/"events.csv",index=False)
    cut=max(1,int(len(z)*.60)) if len(z) else 0
    tr=z.iloc[:cut] if len(z) else z;ho=z.iloc[cut:] if len(z) else z
    report={
        "rule":f"close_location <= {CLOSE_MAX} AND volume_ratio20 >= {VOL_MIN}",
        "all_n":len(z),"all_hit5":rate(z["hit5"]) if len(z) else None,
        "hold_n":len(ho),"hold_hit5":rate(ho["hit5"]) if len(ho) else None,
        "hold_hit10":rate(ho["hit10"]) if len(ho) else None,
        "hold_t5_s7p5":rate(ho["t5_s7p5"]) if len(ho) else None,
        "hold_median_mfe":float(ho["mfe5d"].median()) if len(ho) else None,
        "hold_median_mae":float(ho["mae5d"].median()) if len(ho) else None,
        "passed":bool(len(ho)>=15 and rate(ho["hit5"])>=.78 and rate(ho["t5_s7p5"])>=.68) if len(ho) else False,
    }
    (OUT/"REPORT.json").write_text(json.dumps(report,indent=2))
    (OUT/"REPORT.txt").write_text("\n".join(f"{k}={v}" for k,v in report.items())+"\n")
    print(json.dumps(report,indent=2))

if __name__=="__main__":main()
