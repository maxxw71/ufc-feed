#!/usr/bin/env python3
"""
Economic replay of the strongest externally replicated lower-high candidate
on Hyperliquid.

Candidate C (frozen from Binance):
- 4H established downtrend
- RSI <= 30
- relief pump >= 21.337%
- relief pump remains >= 19.915% below prior swing high
- second dump >= 10%

Evaluates latest 40% chronological holdout only with:
- +5% take-profit
- stop variants: none, -5%, -7.5%, -10%
- 5-day timeout
- actual Hyperliquid funding
- 0.045% taker fee per side
- 0.10% slippage per side
"""
from __future__ import annotations
from pathlib import Path
import time
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_lower_high_hyperliquid_replication/events.csv")
OUT=Path("crypto/research/results_lower_high_candidate_economics")
OUT.mkdir(parents=True,exist_ok=True)

NOTIONAL=1000.0
TP=.05
FEE=.00045
SLIP=.0010

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
    c=candles(s,str(row["coin"]),st-pd.Timedelta(hours=1),st+pd.Timedelta(days=5,hours=4))
    c=c[c["time"]>=st].reset_index(drop=True)
    if c.empty:return None
    entry=float(row["entry"]);reason="timeout";gross=None;et=None
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
    fund=funding(s,str(row["coin"]),st,et+pd.Timedelta(hours=1))
    net=gross-2*(FEE+SLIP)-fund
    return {"exit_time":et,"gross_ret":gross,"funding":fund,"net_ret":net,
            "pnl":NOTIONAL*net,"reason":reason}

def main():
    e=pd.read_csv(SRC)
    for c in ["trigger_time","entry_time"]:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    g=e[e["C_lowerhigh_bigpump"].fillna(False).astype(bool)].sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(g)*.60));h=g.iloc[cut:].copy()

    s=requests.Session();s.headers["User-Agent"]="appwiza-lower-high-econ/1.0"
    rows=[];summ=[]
    for stop in [None,.05,.075,.10]:
        pnl=[]
        for i,(_,r) in enumerate(h.iterrows(),1):
            q=replay(s,r,stop)
            if q is None:continue
            rows.append({"stop":"none" if stop is None else stop,"coin":r["coin"],"entry_time":r["entry_time"],**q})
            pnl.append(q)
            time.sleep(.08)
        d=pd.DataFrame(pnl)
        summ.append({
            "stop":"none" if stop is None else stop,
            "trades":len(d),
            "target_rate":float((d["reason"]=="target").mean()) if len(d) else np.nan,
            "positive_net_rate":float((d["pnl"]>0).mean()) if len(d) else np.nan,
            "median_net_return":float(d["net_ret"].median()) if len(d) else np.nan,
            "total_pnl_1000_each":float(d["pnl"].sum()) if len(d) else np.nan,
            "avg_pnl_per_trade":float(d["pnl"].mean()) if len(d) else np.nan,
        })
    td=pd.DataFrame(rows);td.to_csv(OUT/"trades.csv",index=False)
    z=pd.DataFrame(summ);z.to_csv(OUT/"summary.csv",index=False)
    lines=["LOWER-HIGH CANDIDATE C — HYPERLIQUID ECONOMIC HOLDOUT REPLAY","",
           f"Holdout signals: {len(h)}","",z.to_string(index=False)]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":main()
