#!/usr/bin/env python3
"""
Deep economic audit of the strongest broad point-in-time MA candidate.

Frozen rule:
- SMA350 breakout after >=75% of prior 24 closes below SMA350
- first point-in-time MA111 touch within the family search window
- PRIOR completed 4H close remains >=1.65551% above SMA350
- PRIOR completed BTC 4H RSI >=59.4615
- buy exact point-in-time MA111 touch
- +5% target / -7.5% stop / 5d timeout

No threshold tuning here.

Audit:
- reconstruct MA111 touch fill with 1m Hyperliquid candles
- same-fill-minute target is not credited; stop is conservative
- later same-4H target/stop conflicts resolved with 1m
- actual Hyperliquid funding through realized exit
- 0.045% taker + 0.10% modeled slippage per side
- current $1k/$5k/$10k L2 capacity snapshot
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_ma_multimechanic/hyperliquid_events.csv")
OUT=Path("crypto/research/results_ma_top_candidate_deep_audit")
TP=.05; STOP=.075; RT=2*(.00045+.0010); SIZES=(1000,5000,10000)

RULE_DIST350=0.0165551
RULE_BTC_RSI=59.4615

def post(s,p,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=p,timeout=45)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(12,1.3*(k+1)))
    raise RuntimeError(str(last))

def source_events():
    e=pd.read_csv(SRC)
    for c in ["anchor_time","touch_time","context_time","entry_time"]:
        if c in e:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    m=(
        (e["family"]=="sma350_break_ma111_touch")
        &(e["mode"]=="touch")
        &(pd.to_numeric(e["dist350"],errors="coerce")>=RULE_DIST350)
        &(pd.to_numeric(e["btc_rsi"],errors="coerce")>=RULE_BTC_RSI)
    )
    x=e[m].copy().sort_values("entry_time").drop_duplicates(["coin","touch_time"]).reset_index(drop=True)
    cut=max(1,int(len(x)*.60))
    x["split"]="holdout";x.loc[:cut-1,"split"]="train"
    return x

def candles(s,coin,interval,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{"coin":str(coin),"interval":interval,
      "startTime":int(pd.Timestamp(start).timestamp()*1000),"endTime":int(pd.Timestamp(end).timestamp()*1000)}})
    out=[]
    for r in rows or []:
        try:out.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),"o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
        except Exception:pass
    return pd.DataFrame(out).drop_duplicates("time").sort_values("time").reset_index(drop=True) if out else pd.DataFrame()

def resolve_minutes(mins,target,stop,credit_target=True):
    for _,r in mins.iterrows():
        ht=credit_target and float(r["h"])>=target
        hs=float(r["l"])<=stop
        if not ht and not hs:continue
        if hs and not ht:return ("stop",r["time"],"1m")
        if ht and not hs:return ("target",r["time"],"1m")
        o=float(r["o"])
        if o<=stop:return ("stop",r["time"],"1m_open")
        if credit_target and o>=target:return ("target",r["time"],"1m_open")
        return ("stop",r["time"],"1m_both_conservative")
    return None

def replay(s,r):
    coin=str(r["coin"]);touch=pd.Timestamp(r["touch_time"]);entry=float(r["touch_level"])
    target=entry*(1+TP);stop=entry*(1-STOP)
    mins=candles(s,coin,"1m",touch,touch+pd.Timedelta(hours=4))
    if mins.empty:raise RuntimeError("no 1m touch candles")
    fill_idx=None
    for i,m in mins.iterrows():
        if float(m["l"])<=entry<=float(m["h"]):fill_idx=i;break
    if fill_idx is None:raise RuntimeError("MA111 fill not reconstructed")
    fill_time=pd.Timestamp(mins.loc[fill_idx,"time"])
    fm=mins.loc[fill_idx]
    if float(fm["l"])<=stop:
        reason="stop";exit_time=fill_time;gross=-STOP;resolution="fill_minute_stop"
    else:
        q=resolve_minutes(mins.loc[mins.index>fill_idx],target,stop,True)
        if q:
            reason,exit_time,resolution=q;gross=TP if reason=="target" else -STOP
        else:
            reason=None;gross=None;exit_time=None;resolution=None

    if gross is None:
        end=fill_time+pd.Timedelta(days=5)
        c=candles(s,coin,"4h",touch+pd.Timedelta(hours=4)-pd.Timedelta(minutes=1),end+pd.Timedelta(hours=4))
        c=c[(c["time"]>=touch+pd.Timedelta(hours=4))&(c["time"]<end)].reset_index(drop=True)
        if c.empty:raise RuntimeError("no post-touch 4h candles")
        for _,b in c.iterrows():
            ht=float(b["h"])>=target;hs=float(b["l"])<=stop
            if ht and hs:
                mm=candles(s,coin,"1m",b["time"],pd.Timestamp(b["time"])+pd.Timedelta(hours=4))
                q=resolve_minutes(mm,target,stop,True) if len(mm) else None
                if q:
                    reason,exit_time,resolution=q;gross=TP if reason=="target" else -STOP
                else:
                    reason="stop";exit_time=b["time"];resolution="same4h_unresolved_stop";gross=-STOP
                break
            if hs:reason="stop";exit_time=b["time"];resolution="4h";gross=-STOP;break
            if ht:reason="target";exit_time=b["time"];resolution="4h";gross=TP;break
        if gross is None:
            last=c.iloc[-1];reason="timeout";exit_time=last["time"]+pd.Timedelta(hours=4);resolution="timeout_close"
            gross=float(np.clip(float(last["c"])/entry-1,-STOP,TP))

    fr=post(s,{"type":"fundingHistory","coin":coin,
      "startTime":int(fill_time.timestamp()*1000),"endTime":int((pd.Timestamp(exit_time)+pd.Timedelta(hours=1)).timestamp()*1000)})
    vals=[]
    for x in fr or []:
        try:
            t=pd.to_datetime(int(x["time"]),unit="ms",utc=True)
            if fill_time<=t<=pd.Timestamp(exit_time):vals.append(float(x["fundingRate"]))
        except Exception:pass
    fund=float(np.sum(vals))
    return {"fill_time":fill_time,"exit_time":exit_time,"exit_reason":reason,"exit_resolution":resolution,
            "gross_return":gross,"actual_funding_return":fund,"funding_points":len(vals),"net_return":gross-RT-fund}

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    e=source_events().iloc[args.shard_index::args.shard_count]
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-top-deep/1.0"
    rows=[]
    for pos,(_,r) in enumerate(e.iterrows(),1):
        try:q=replay(s,r);err=""
        except Exception as ex:q={"fill_time":pd.NaT,"exit_time":pd.NaT,"exit_reason":"error","exit_resolution":"error","gross_return":np.nan,"actual_funding_return":np.nan,"funding_points":0,"net_return":np.nan};err=str(ex)[:180]
        rows.append({**r.to_dict(),**q,"error":err});print(pos,len(e),r["coin"],r["touch_time"])
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"audit_{args.shard_index:02d}.csv",index=False)

def meta(s):
    m,ctx=post(s,{"type":"metaAndAssetCtxs"});out=[]
    for i,u in enumerate(m.get("universe",[])):
        if u.get("isDelisted") or not u.get("name"):continue
        c=ctx[i] if i<len(ctx) else {}
        out.append({"coin":str(u["name"]),"day_volume":float(c.get("dayNtlVlm") or 0),"mark":float(c.get("markPx") or 0)})
    return pd.DataFrame(out)

def l2(s,coin):
    j=post(s,{"type":"l2Book","coin":str(coin)});lv=j.get("levels",[]) if isinstance(j,dict) else []
    def cv(a):
        z=[]
        for x in a or []:
            try:z.append((float(x["px"]),float(x["sz"])))
            except Exception:pass
        return z
    return cv(lv[0] if len(lv)>0 else []),cv(lv[1] if len(lv)>1 else [])

def sim(bids,asks,notional):
    if not bids or not asks:return {"full_fill":False}
    mid=(bids[0][0]+asks[0][0])/2;rem=float(notional);base=spent=0.0
    for px,sz in asks:
        take=min(rem,px*sz);spent+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    buy=rem<=max(1e-6,notional*1e-9);avg=spent/base if base else np.nan
    rb=base;proc=0.0
    for px,sz in bids:
        take=min(rb,sz);proc+=take*px;rb-=take
        if rb<=1e-12:break
    sell=rb<=max(1e-10,base*1e-9)
    return {"full_fill":bool(buy and sell),"entry_slippage":avg/mid-1 if buy else np.nan,
            "roundtrip_book_impact":1-proc/spent if buy and sell and spent>0 else np.nan}

def capacity(hold,out):
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-top-cap/1.0"
    m=meta(s);z=pd.DataFrame({"coin":sorted(set(hold["coin"].astype(str)))}).merge(m,on="coin",how="left");rows=[]
    for _,r in z.iterrows():
        rec=r.to_dict()
        try:
            bids,asks=l2(s,r["coin"]);rec["status"]="ok"
            for size in SIZES:
                q=sim(bids,asks,size);rec[f"full_fill_{size}"]=q["full_fill"];rec[f"entry_slippage_{size}"]=q.get("entry_slippage");rec[f"roundtrip_book_impact_{size}"]=q.get("roundtrip_book_impact")
        except Exception as ex:rec["status"]="error";rec["error"]=str(ex)[:120]
        rows.append(rec);time.sleep(.08)
    q=pd.DataFrame(rows);q.to_csv(out/"current_l2_capacity.csv",index=False);return q

def summarize(g):
    ok=g[pd.to_numeric(g["net_return"],errors="coerce").notna()]
    nr=pd.to_numeric(ok["net_return"],errors="coerce")
    return {"n":len(g),"replayed_n":len(ok),"target_rate":float((ok["exit_reason"]=="target").mean()) if len(ok) else np.nan,
            "stop_rate":float((ok["exit_reason"]=="stop").mean()) if len(ok) else np.nan,
            "timeout_rate":float((ok["exit_reason"]=="timeout").mean()) if len(ok) else np.nan,
            "avg_funding":float(pd.to_numeric(ok["actual_funding_return"],errors="coerce").mean()) if len(ok) else np.nan,
            "net_roi":float(nr.mean()) if len(ok) else np.nan,"pnl_per_10000":float(nr.mean()*10000) if len(ok) else np.nan}

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in Path(args.input_dir).rglob("audit_*.csv"):
        try:
            z=pd.read_csv(p)
            if len(z):fs.append(z)
        except Exception:pass
    t=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if t.empty:raise SystemExit("no audit rows")
    for c in ["touch_time","fill_time","exit_time","entry_time"]:
        if c in t:t[c]=pd.to_datetime(t[c],utc=True,errors="coerce")
    t=t.drop_duplicates(["coin","touch_time"]).sort_values(["split","entry_time"]).reset_index(drop=True)
    t.to_csv(out/"trades.csv",index=False)
    tr=t[t["split"]=="train"];ho=t[t["split"]=="holdout"];st=summarize(tr);sh=summarize(ho)
    cap=capacity(ho,out)
    ff=cap.get("full_fill_10000",pd.Series(dtype=bool)).astype(str).str.lower().isin(["true","1"])
    es=pd.to_numeric(cap.get("entry_slippage_10000",pd.Series(dtype=float)),errors="coerce")
    rt=pd.to_numeric(cap.get("roundtrip_book_impact_10000",pd.Series(dtype=float)),errors="coerce")
    cs={"coins":int(ho["coin"].nunique()),"full_fill_10k_fraction":float(ff.mean()) if len(ff) else np.nan,
        "median_entry_slippage_10k":float(es.median()) if es.notna().any() else np.nan,
        "p90_entry_slippage_10k":float(es.quantile(.9)) if es.notna().any() else np.nan,
        "median_roundtrip_book_impact_10k":float(rt.median()) if rt.notna().any() else np.nan,
        "p90_roundtrip_book_impact_10k":float(rt.quantile(.9)) if rt.notna().any() else np.nan}
    result={"rule":f"sma350_break_ma111_touch + dist350>={RULE_DIST350} + btc_rsi>={RULE_BTC_RSI}",
            "train":st,"holdout":sh,"binance_frozen":{"n":27,"hit5":.888889,"risk":.851852,"modeled_roi":.031086},
            "capacity":cs,"promotion_gate":bool(sh["replayed_n"]>=25 and sh["target_rate"]>=.75 and sh["net_roi"]>=.015)}
    (out/"summary.json").write_text(json.dumps(result,indent=2,default=str))
    (out/"REPORT.txt").write_text("MA TOP CANDIDATE DEEP AUDIT\n\n"+json.dumps(result,indent=2,default=str)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1);ap.add_argument("--sleep",type=float,default=.10)
    ap.add_argument("--out",default="ma_top_out");ap.add_argument("--input-dir",default="ma_top_shards");args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
