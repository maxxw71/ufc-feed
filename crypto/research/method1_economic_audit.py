#!/usr/bin/env python3
"""
Economic holdout replay for Method 1: Blow-off First Flush.

Frozen rule:
- Hyperliquid primary perp
- daily 5d return >= 15%
- daily high at/above prior 60d high
- daily 20d realized vol >= 2.5%
- first 4H drawdown from post-setup peak >= 8%
- trigger close at least 3.649% below 4H EMA9
- RSI acceleration <= -7.63088
- enter next 4H open

Evaluates latest 40% chronological holdout with actual funding, fees, slippage.
"""
from __future__ import annotations
from pathlib import Path
import time
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT=Path("crypto/research/results_method1_economics")
OUT.mkdir(parents=True,exist_ok=True)

TP=.05
FEE=.00045
SLIP=.0010
NOTIONAL=1000.0

def post(s,p,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(INFO,json=p,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(8,1+k))
    raise RuntimeError(str(last))

def candles(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    a=[]
    for r in rows or []:
        a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                  "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time") if a else pd.DataFrame()

def funding(s,coin,start,end):
    try:
        h=post(s,{"type":"fundingHistory","coin":coin,"startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)})
        return float(sum(float(x.get("fundingRate",0)) for x in h or []))
    except Exception:return 0.0

def replay(s,row,stop):
    st=pd.Timestamp(row["entry_time"])
    c=candles(s,str(row["api_coin"]),st-pd.Timedelta(hours=1),st+pd.Timedelta(days=5,hours=4))
    c=c[c["time"]>=st].reset_index(drop=True)
    if c.empty:return None
    entry=float(row["entry"])
    gross=None;reason="timeout";et=None
    for _,b in c.iterrows():
        ht=float(b["h"])>=entry*(1+TP)
        hs=(stop is not None) and float(b["l"])<=entry*(1-stop)
        if ht and hs:
            gross=-stop;reason="same_bar_stop";et=b["time"];break
        if hs:
            gross=-stop;reason="stop";et=b["time"];break
        if ht:
            gross=TP;reason="target";et=b["time"];break
    if gross is None:
        q=c[c["time"]<=st+pd.Timedelta(days=5)]
        if q.empty:q=c
        b=q.iloc[-1];gross=float(b["c"])/entry-1;et=b["time"];reason="timeout"
    fund=funding(s,str(row["api_coin"]),st,et+pd.Timedelta(hours=1))
    net=gross-2*(FEE+SLIP)-fund
    return {"exit_time":et,"gross_ret":gross,"funding":fund,"net_ret":net,"pnl":NOTIONAL*net,"reason":reason}

def main():
    e=pd.read_csv(SRC)
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    key=["kind","dex","display_name","arm_time"]
    e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
    e=e[(e["kind"]=="perp")&(e["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)
    # exact frozen method 1
    g=e[(e["dist_ema9"]<=-0.03649)&(e["rsi_accel"]<=-7.63088)].sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(g)*.60))
    h=g.iloc[cut:].copy()

    s=requests.Session();s.headers["User-Agent"]="appwiza-method1-econ/1.0"
    rows=[];summ=[]
    for stop in [None,.05,.075,.10]:
        rr=[]
        for _,r in h.iterrows():
            q=replay(s,r,stop)
            if q:
                rows.append({"stop":"none" if stop is None else stop,"coin":r["display_name"],"entry_time":r["entry_time"],**q})
                rr.append(q)
            time.sleep(.08)
        d=pd.DataFrame(rr)
        summ.append({
            "stop":"none" if stop is None else stop,
            "trades":len(d),
            "target_rate":float((d["reason"]=="target").mean()) if len(d) else np.nan,
            "positive_net_rate":float((d["pnl"]>0).mean()) if len(d) else np.nan,
            "median_net_return":float(d["net_ret"].median()) if len(d) else np.nan,
            "total_pnl_1000_each":float(d["pnl"].sum()) if len(d) else np.nan,
            "avg_pnl_per_trade":float(d["pnl"].mean()) if len(d) else np.nan,
        })
    pd.DataFrame(rows).to_csv(OUT/"trades.csv",index=False)
    z=pd.DataFrame(summ);z.to_csv(OUT/"summary.csv",index=False)
    lines=["METHOD 1 BLOW-OFF FIRST-FLUSH — ECONOMIC HOLDOUT REPLAY","",
           f"Holdout signals: {len(h)}","",z.to_string(index=False)]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":main()
