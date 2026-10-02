#!/usr/bin/env python3
"""
Capacity-aware bankroll replay using the frozen Hyperliquid signal ranker.

Rules:
- 10,000 USDC starting bankroll
- 1,000 USDC fixed notional per trade
- max 10 concurrent positions
- do NOT fill empty slots with low-quality signals
- only consider model scores above a frozen threshold
- when more qualifying signals arrive than free slots, take highest score first
- +5% TP / -7.5% SL / 5d timeout
- actual historical funding + 0.045% taker/side + 0.10% slippage/side
"""
from __future__ import annotations
import time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_signal_ranking/holdout_scored.csv")
OUT=Path("crypto/research/results_ranked_capacity_bankroll")
OUT.mkdir(parents=True,exist_ok=True)

START=10000.0
NOTIONAL=1000.0
MAX_POS=10
TP=.05
SL=.075
FEE_SIDE=.00045
SLIP_SIDE=.0010

def post(s,payload,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(INFO,json=payload,timeout=40)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(8,1+k))
    raise RuntimeError(str(last))

def candles(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
               "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    out=[]
    for r in rows or []:
        out.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                    "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
    return pd.DataFrame(out).drop_duplicates("time").sort_values("time") if out else pd.DataFrame()

def funding(s,coin,start,end):
    try:
        h=post(s,{"type":"fundingHistory","coin":coin,
                  "startTime":int(start.timestamp()*1000),
                  "endTime":int(end.timestamp()*1000)})
        return float(sum(float(x.get("fundingRate",0)) for x in h or []))
    except Exception:
        return 0.0

def replay(s,row):
    st=pd.Timestamp(row["entry_time"])
    end=st+pd.Timedelta(days=5,hours=4)
    c=candles(s,str(row["api_coin"]),st-pd.Timedelta(hours=1),end)
    if c.empty:return None
    c=c[c["time"]>=st].reset_index(drop=True)
    if c.empty:return None
    entry=float(row["entry"])
    exitt=None;ret=None;reason=None
    for _,b in c.iterrows():
        h=float(b["h"]);l=float(b["l"])
        ht=h>=entry*(1+TP);hs=l<=entry*(1-SL)
        if ht and hs:
            exitt=b["time"];ret=-SL;reason="same_bar_stop";break
        if hs:
            exitt=b["time"];ret=-SL;reason="stop";break
        if ht:
            exitt=b["time"];ret=TP;reason="target";break
    if exitt is None:
        q=c[c["time"]<=st+pd.Timedelta(days=5)]
        if q.empty:q=c
        b=q.iloc[-1];exitt=b["time"];ret=float(b["c"])/entry-1;reason="timeout"
    fund=funding(s,str(row["api_coin"]),st,exitt+pd.Timedelta(hours=1))
    cost=2*(FEE_SIDE+SLIP_SIDE)+fund
    net=ret-cost
    return {"exit_time":exitt,"gross_ret":ret,"funding":fund,"net_ret":net,
            "pnl":NOTIONAL*net,"reason":reason}

def simulate(df,replays,score_col,threshold):
    sig=df[df[score_col]>=threshold].sort_values(["entry_time",score_col],ascending=[True,False]).copy()
    bankroll=START
    active=[] # list of dicts exit_time
    trades=[]
    skipped_capacity=0
    i=0
    while i<len(sig):
        t=sig.iloc[i]["entry_time"]
        # batch same 4h entry time, because simultaneous candidates should be ranked together
        j=i
        while j<len(sig) and sig.iloc[j]["entry_time"]==t:j+=1
        batch=sig.iloc[i:j].sort_values(score_col,ascending=False)
        active=[a for a in active if a["exit_time"]>t]
        free=max(0,MAX_POS-len(active))
        chosen=batch.head(free)
        skipped_capacity += max(0,len(batch)-free)
        for idx,row in chosen.iterrows():
            rep=replays.get(idx)
            if rep is None:continue
            bankroll+=rep["pnl"]
            active.append({"exit_time":rep["exit_time"]})
            trades.append({
                "score_type":score_col,"threshold":threshold,"symbol":row["display_name"],
                "entry_time":row["entry_time"],"score":row[score_col],"rule_votes":row.get("rule_votes",0),
                **rep,"bankroll_after":bankroll
            })
        i=j
    td=pd.DataFrame(trades)
    if td.empty:
        return td,{"score_type":score_col,"threshold":threshold,"trades":0}
    td["month"]=pd.to_datetime(td["entry_time"],utc=True).dt.to_period("M").astype(str)
    return td,{
        "score_type":score_col,"threshold":threshold,"eligible_signals":len(sig),
        "trades":len(td),"skipped_capacity":skipped_capacity,
        "wins":int((td["pnl"]>0).sum()),"win_rate":float((td["pnl"]>0).mean()),
        "target_exits":int((td["reason"]=="target").sum()),
        "stop_exits":int(td["reason"].isin(["stop","same_bar_stop"]).sum()),
        "timeout_exits":int((td["reason"]=="timeout").sum()),
        "net_profit":float(td["pnl"].sum()),
        "ending_bankroll":float(START+td["pnl"].sum()),
    }

def main():
    d=pd.read_csv(SRC)
    for c in ["arm_time","trigger_time","entry_time"]:
        d[c]=pd.to_datetime(d[c],utc=True,errors="coerce")
    # Only need to replay events that could enter at the lowest tested threshold.
    union=d[(d["score"]>=.55)|(d["rank_score"]>=.55)].copy()
    s=requests.Session();s.headers["User-Agent"]="appwiza-ranked-capacity/1.0"
    reps={}
    for n,(idx,row) in enumerate(union.sort_values("entry_time").iterrows(),1):
        try:reps[idx]=replay(s,row)
        except Exception as ex:
            print("ERR",row["display_name"],ex);reps[idx]=None
        print(n,"/",len(union),row["display_name"])
        time.sleep(.12)

    alltd=[];summ=[]
    for col in ["score","rank_score"]:
        for th in [.55,.60,.65]:
            td,sm=simulate(d,reps,col,th);summ.append(sm)
            if len(td):alltd.append(td)
    trades=pd.concat(alltd,ignore_index=True) if alltd else pd.DataFrame()
    trades.to_csv(OUT/"trades.csv",index=False)
    ssum=pd.DataFrame(summ)
    ssum.to_csv(OUT/"summary.csv",index=False)

    monthly=[]
    if len(trades):
        for (col,th),g in trades.groupby(["score_type","threshold"]):
            q=g.groupby("month").agg(trades=("pnl","size"),wins=("pnl",lambda x:int((x>0).sum())),
                                      pnl=("pnl","sum")).reset_index()
            q["win_rate"]=q["wins"]/q["trades"]
            q["score_type"]=col;q["threshold"]=th
            q["ending_bankroll_if_month_starts_10k"]=START+q["pnl"]
            monthly.append(q)
    m=pd.concat(monthly,ignore_index=True) if monthly else pd.DataFrame()
    m.to_csv(OUT/"monthly.csv",index=False)

    lines=[
        "RANKED CAPACITY BANKROLL REPLAY",
        "",
        "10,000 USDC | 1,000/trade | max 10 concurrent | no leverage",
        "+5% target | -7.5% stop | 5d timeout",
        "Actual funding + 0.045% taker/side + 0.10% slippage/side",
        "Signals below the score threshold are skipped even when capital is idle.",
        "When signals share an entry timestamp and slots are limited, highest score is taken first.",
        "",
        ssum.to_string(index=False),
        "",
        "MONTHLY",
        m.to_string(index=False),
    ]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":main()
