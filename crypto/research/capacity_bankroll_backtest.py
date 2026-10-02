#!/usr/bin/env python3
"""
Capacity-aware historical bankroll simulation for the strongest frozen
Hyperliquid primary-perp rebound rules.

Assumptions:
- 10,000 USDC starting bankroll
- 1,000 USDC notional per trade, no leverage
- max 10 simultaneous positions
- +5% take profit / -7.5% stop / 5-day timeout
- same-bar TP+SL conservatively counts as stop first
- 0.045% taker fee per side
- 0.10% slippage per side
- actual Hyperliquid hourly funding pulled from entry until exit
"""
from __future__ import annotations
import time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"
SRC="crypto/research/results_hyperliquid_all_pairs/events.csv"
OUT=Path("crypto/research/results_capacity_bankroll")
OUT.mkdir(parents=True,exist_ok=True)

START_BANKROLL=10000.0
NOTIONAL=1000.0
TP=0.05
SL=0.075
FEE_SIDE=0.00045
SLIP_SIDE=0.0010
MAX_POS=10

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

def fetch_candles(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":coin,"interval":"4h",
                 "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)}})
    out=[]
    for r in rows or []:
        out.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                    "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
    return pd.DataFrame(out).sort_values("time").drop_duplicates("time") if out else pd.DataFrame()

def funding_sum(s,coin,start,end):
    try:
        h=post(s,{"type":"fundingHistory","coin":coin,
                  "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000)})
        return float(sum(float(x.get("fundingRate",0)) for x in h or []))
    except Exception:
        return 0.0

def load_holdout():
    e=pd.read_csv(SRC)
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    key=["kind","dex","display_name","arm_time"]
    e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
    e=e[(e["kind"]=="perp")&(e["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(e)*.60))
    return e.iloc[cut:].copy()

def masks(h):
    a=(h["dist_ema9"]<=-0.03649)&(h["rsi_accel"]<=-7.63088)
    b=(h["rsi_pct1"]<=-0.21182)&(h["sma9_slope3"]<=0.02604)
    c=(h["rsi_pct1"]<=-0.21182)&(h["dist_sma9"]<=-0.03517)
    d=(h["rsi_sma5_slope1"]<=-0.05915)&(h["rsi_pct1"]<=-0.17635)
    ee=(h["dist_ema9"]<=-0.03649)&(h["close_location"]<=0.16592)
    votes=a.astype(int)+b.astype(int)+c.astype(int)+d.astype(int)+ee.astype(int)
    return {
        "A_EMA9_RSIacc":a,
        "E_EMA9_lowclose":ee,
        "F_ensemble_2of5":votes>=2,
        "G_ensemble_3of5":votes>=3,
    }

def replay_event(s,row):
    st=pd.Timestamp(row["entry_time"])
    end=st+pd.Timedelta(days=5,hours=4)
    c=fetch_candles(s,str(row["api_coin"]),st-pd.Timedelta(hours=1),end)
    if c.empty:return None
    c=c[c["time"]>=st].reset_index(drop=True)
    if c.empty:return None
    entry=float(row["entry"])
    exit_time=None;gross_ret=None;reason=None;exit_px=None
    for _,bar in c.iterrows():
        hit_tp=float(bar["h"])>=entry*(1+TP)
        hit_sl=float(bar["l"])<=entry*(1-SL)
        if hit_tp and hit_sl:
            exit_time=bar["time"];gross_ret=-SL;reason="same_bar_stop";exit_px=entry*(1-SL);break
        if hit_sl:
            exit_time=bar["time"];gross_ret=-SL;reason="stop";exit_px=entry*(1-SL);break
        if hit_tp:
            exit_time=bar["time"];gross_ret=TP;reason="target";exit_px=entry*(1+TP);break
    if exit_time is None:
        last=c[c["time"]<=st+pd.Timedelta(days=5)]
        if last.empty:last=c
        bar=last.iloc[-1]
        exit_time=bar["time"];exit_px=float(bar["c"]);gross_ret=exit_px/entry-1;reason="timeout"
    fund=funding_sum(s,str(row["api_coin"]),st,exit_time+pd.Timedelta(hours=1))
    # positive funding is paid by longs
    cost=2*(FEE_SIDE+SLIP_SIDE)+fund
    net_ret=gross_ret-cost
    return {"entry_time":st,"exit_time":exit_time,"entry":entry,"exit_px":exit_px,
            "gross_ret":gross_ret,"funding":fund,"cost_ret":cost,"net_ret":net_ret,
            "pnl":NOTIONAL*net_ret,"reason":reason}

def simulate(rule,signals,replays):
    bankroll=START_BANKROLL
    active=[];trades=[];skipped=0
    for idx,row in signals.sort_values("entry_time").iterrows():
        t=pd.Timestamp(row["entry_time"])
        # release positions exited by this entry time
        active=[x for x in active if x>t]
        if len(active)>=MAX_POS or bankroll<NOTIONAL:
            skipped+=1;continue
        rep=replays.get(idx)
        if rep is None:continue
        active.append(rep["exit_time"])
        bankroll+=rep["pnl"]
        trades.append({**rep,"rule":rule,"symbol":row["display_name"],"bankroll_after":bankroll})
    td=pd.DataFrame(trades)
    if td.empty:return td,{"rule":rule,"trades":0,"skipped_capacity":skipped,"ending_bankroll":bankroll}
    td["month"]=pd.to_datetime(td["entry_time"],utc=True).dt.to_period("M").astype(str)
    return td,{
        "rule":rule,"trades":len(td),"skipped_capacity":skipped,
        "wins":int((td["pnl"]>0).sum()),"win_rate":float((td["pnl"]>0).mean()),
        "target_exits":int((td["reason"]=="target").sum()),
        "stop_exits":int(td["reason"].isin(["stop","same_bar_stop"]).sum()),
        "timeout_exits":int((td["reason"]=="timeout").sum()),
        "net_profit":float(td["pnl"].sum()),"ending_bankroll":float(bankroll),
        "max_simultaneous_cap":MAX_POS,
    }

def main():
    h=load_holdout();mm=masks(h)
    # replay union once
    union=pd.Series(False,index=h.index)
    for m in mm.values():union|=m
    s=requests.Session();s.headers["User-Agent"]="appwiza-capacity-backtest/1.0"
    replays={}
    for n,(idx,row) in enumerate(h[union].sort_values("entry_time").iterrows(),1):
        try:
            replays[idx]=replay_event(s,row)
        except Exception as ex:
            print("ERR",row["display_name"],ex)
            replays[idx]=None
        print(n,"/",int(union.sum()),row["display_name"])
        time.sleep(.15)

    alltr=[];summ=[]
    for rn,m in mm.items():
        td,sm=simulate(rn,h[m],replays);summ.append(sm)
        if len(td):alltr.append(td)
    trades=pd.concat(alltr,ignore_index=True) if alltr else pd.DataFrame()
    trades.to_csv(OUT/"trades.csv",index=False)
    pd.DataFrame(summ).to_csv(OUT/"summary.csv",index=False)

    monthly=[]
    if len(trades):
        for rn,g in trades.groupby("rule"):
            q=g.groupby("month").agg(trades=("pnl","size"),wins=("pnl",lambda x:int((x>0).sum())),
                                      pnl=("pnl","sum")).reset_index()
            q["ending_bankroll_if_month_starts_10k"]=START_BANKROLL+q["pnl"]
            q["rule"]=rn;monthly.append(q)
    mdf=pd.concat(monthly,ignore_index=True) if monthly else pd.DataFrame()
    mdf.to_csv(OUT/"monthly.csv",index=False)

    lines=["CAPACITY-AWARE HYPERLIQUID BANKROLL BACKTEST","",
           "Start 10,000 USDC | 1,000 notional/trade | max 10 concurrent | no leverage",
           "+5% TP | -7.5% SL | 5d timeout | same-bar ambiguity -> stop first",
           "Costs: actual funding to exit + 0.045% taker/side + 0.10% slippage/side","",
           pd.DataFrame(summ).to_string(index=False),"","MONTHLY",mdf.to_string(index=False)]
    (OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((OUT/"REPORT.txt").read_text())

if __name__=="__main__":main()
