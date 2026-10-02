#!/usr/bin/env python3
"""
External Binance replication of no-lookahead Hyperliquid Method 1 candidates.

Frozen from corrected Hyperliquid TRAINING data:
A) RSI acceleration <= -11.72020 AND 1-bar RSI % change <= -18.174%
B) RSI acceleration <= -8.99335 AND 1-bar RSI % change <= -18.174%

Base setup:
- completed daily 5d return >=15%
- daily high >= prior 60d high
- daily RV20 >=2.5%
- arm only at next UTC day
- first 4H close drawdown >=8% from post-arm peak
- enter next 4H open

No Binance threshold tuning.
"""
from __future__ import annotations
import argparse,math,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://data-api.binance.vision"
STABLE={"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}
RULES={
    "M1A_accel11p72_rsi1_18p174":lambda r:r["rsi_accel"]<=-11.72020 and r["rsi_pct1"]<=-0.18174,
    "M1B_accel8p993_rsi1_18p174":lambda r:r["rsi_accel"]<=-8.99335 and r["rsi_pct1"]<=-0.18174,
}

def gj(s,path,params=None):
    last=None
    for k in range(6):
        try:
            r=s.get(API+path,params=params,timeout=25)
            if r.ok:return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:160]}")
        except Exception as e:last=e
        time.sleep(min(8,1.5**k))
    raise RuntimeError(str(last))

def universe(s,n):
    info=gj(s,"/api/v3/exchangeInfo");tick=gj(s,"/api/v3/ticker/24hr")
    qv={x["symbol"]:float(x.get("quoteVolume") or 0) for x in tick if isinstance(x,dict) and "symbol" in x}
    syms=[]
    for x in info.get("symbols",[]):
        if x.get("status")!="TRADING" or x.get("quoteAsset")!="USDT":continue
        b=x.get("baseAsset","")
        if b in STABLE or b.startswith("USD") or b.startswith("1000") or any(b.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):continue
        syms.append(x["symbol"])
    syms.sort(key=lambda z:qv.get(z,0),reverse=True)
    return syms[:n]

def fetch(s,sym,interval,start,end):
    step={"1d":86400000,"4h":14400000}[interval]
    cur=int(start.timestamp()*1000);stop=int(end.timestamp()*1000);rows=[]
    while cur<stop:
        b=gj(s,"/api/v3/klines",{"symbol":sym,"interval":interval,"startTime":cur,"endTime":stop,"limit":1000})
        if not b:break
        rows.extend(b);nxt=int(b[-1][0])+step
        if nxt<=cur:break
        cur=nxt
        if len(b)<1000:break
        time.sleep(.02)
    cols=["t","o","h","l","c","v","ct","qv","n","tb","tq","x"]
    d=pd.DataFrame(rows,columns=cols)
    if d.empty:return d
    for c in ["o","h","l","c","v"]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d["time"]=pd.to_datetime(d["t"],unit="ms",utc=True)
    return d.dropna(subset=["o","h","l","c"]).drop_duplicates("time").sort_values("time").reset_index(drop=True)

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean();al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan);return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep4(x):
    x=x.copy();x["rsi"]=rsi(x["c"]);x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_delta"]=x["rsi"].diff();x["rsi_accel"]=x["rsi_delta"].diff()
    return x

def daily(d):
    x=d.copy();x["ret1"]=x["c"].pct_change();x["ret5"]=x["c"]/x["c"].shift(5)-1
    x["prior60"]=x["h"].shift(1).rolling(60,min_periods=60).max()
    x["rv20"]=x["ret1"].rolling(20,min_periods=20).std()
    x["setup"]=(x["ret5"]>=.15)&(x["h"]>=x["prior60"])&(x["rv20"]>=.025)
    x["arm_time"]=x["time"]+pd.Timedelta(days=1)
    return x

def first_flush(h,arm):
    w=h[(h["time"]>=arm)&(h["time"]<arm+pd.Timedelta(days=5))]
    peak=-math.inf;pi=None
    for i,r in w.iterrows():
        if float(r["h"])>=peak:peak=float(r["h"]);pi=i
        if pi is not None and i>pi and float(r["c"])/peak-1<=-.08:return i
    return None

def outcome(h,idx):
    loc=h.index.get_loc(idx)
    if loc+1>=len(h):return None
    entry=float(h.iloc[loc+1]["o"]);f=h.iloc[loc+1:min(len(h),loc+31)]
    if entry<=0 or f.empty:return None
    out={"entry":entry,"entry_time":h.iloc[loc+1]["time"],
         "mfe5d":float(f["h"].max()/entry-1),"mae5d":float(f["l"].min()/entry-1)}
    out["hit5"]=out["mfe5d"]>=.05;out["hit10"]=out["mfe5d"]>=.10
    for stop in (.05,.075,.10):
        tk=sk=None
        for k,(_,r) in enumerate(f.iterrows()):
            if tk is None and float(r["h"])>=entry*1.05:tk=k
            if sk is None and float(r["l"])<=entry*(1-stop):sk=k
        out[f"t5_s{stop}"]=tk is not None and (sk is None or tk<sk)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--symbols",type=int,default=120);ap.add_argument("--days",type=int,default=900)
    args=ap.parse_args()
    out=Path("crypto/research/results_method1_nolookahead_binance_replication");out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-m1-binance-replication/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);start=end-timedelta(days=args.days)
    rows=[];cov=[]
    for pos,sym in enumerate(universe(s,args.symbols),1):
        try:
            d=daily(fetch(s,sym,"1d",start-timedelta(days=100),end))
            arms=[];last=None
            for _,r in d[d["setup"]].iterrows():
                t=r["arm_time"]
                if t<pd.Timestamp(start):continue
                if last is None or t-last>=pd.Timedelta(days=10):arms.append(t);last=t
            if not arms:cov.append((sym,0,0));continue
            h=prep4(fetch(s,sym,"4h",min(arms).to_pydatetime()-timedelta(days=2),end))
            n=0
            for arm in arms:
                idx=first_flush(h,arm)
                if idx is None:continue
                r=h.loc[idx]
                o=outcome(h,idx)
                if o is None:continue
                rec={"symbol":sym,"arm_time":arm,"trigger_time":r["time"],
                     "rsi_pct1":float(r["rsi_pct1"]),"rsi_accel":float(r["rsi_accel"]),**o}
                for name,fn in RULES.items():rec[name]=bool(fn(rec))
                rows.append(rec);n+=1
            cov.append((sym,len(arms),n));print(pos,sym,n)
        except Exception as ex:
            cov.append((sym,0,0));print("ERR",sym,ex)
    e=pd.DataFrame(rows);e.to_csv(out/"events.csv",index=False)
    pd.DataFrame(cov,columns=["symbol","arms","events"]).to_csv(out/"coverage.csv",index=False)
    if e.empty:raise SystemExit(2)
    for c in ["arm_time","trigger_time","entry_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    e=e.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(e)*.60));tr=e.iloc[:cut];ho=e.iloc[cut:]
    summary=[]
    for name in RULES:
        for label,g in [("all",e[e[name]]),("train",tr[tr[name]]),("holdout",ho[ho[name]])]:
            if not len(g):continue
            summary.append({"rule":name,"split":label,"n":len(g),
                "hit5":float(g["hit5"].mean()),"hit10":float(g["hit10"].mean()),
                "t5_s7p5":float(g["t5_s0.075"].mean()),
                "median_mfe":float(g["mfe5d"].median()),"median_mae":float(g["mae5d"].median())})
    z=pd.DataFrame(summary);z.to_csv(out/"summary.csv",index=False)
    lines=["METHOD 1 NO-LOOKAHEAD — BINANCE EXTERNAL REPLICATION","",
           "No Binance threshold tuning.","",z.to_string(index=False)]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n");print((out/"REPORT.txt").read_text())

if __name__=="__main__":main()
