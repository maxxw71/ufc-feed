#!/usr/bin/env python3
"""
Deep economic audit for the frozen MA candidate:

SMA350 breakout -> price extends >=5% -> first MA111 support touch within
24 4H bars -> PRIOR completed 4H RSI has risen for two consecutive bars ->
buy the MA111 touch.

The rule comes from the corrected no-lookahead Golden Ratio research.
This audit does NOT retune thresholds.

Hyperliquid replay:
- reconstruct the MA111 limit fill inside the historical touch 4H candle
  with 1m data;
- touch-minute target is not credited because it may have occurred before the
  limit fill; a touch-minute stop is counted conservatively;
- all later same-4H target/stop conflicts are resolved with 1m candles;
- +5% target / -7.5% stop / 5d timeout;
- actual Hyperliquid funding from reconstructed fill through exit;
- 0.045% taker fee/side + 0.10% modeled slippage/side.

Capacity:
- current Hyperliquid L2 snapshots for historical holdout coins;
- $1k / $5k / $10k buy + immediate sell-back simulation.
This is only a present-day capacity proxy, never substituted for historical
books.

Research/scanner only. No orders are placed.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"
SRC=Path("crypto/research/results_golden_ratio_ma_rails/hyperliquid_events.csv")
OUT=Path("crypto/research/results_ma111_after_sma350_deep_audit")
TP=.05
STOP=.075
FEE=.00045
SLIP=.0010
RT=2*(FEE+SLIP)
SIZES=(1000,5000,10000)

BIN_N=24
BIN_HIT5=.875
BIN_RISK=.875
BIN_ROI=.030975

def post(s,payload,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=payload,timeout=45)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(12,1.3*(k+1)))
    raise RuntimeError(str(last))

def bval(x):
    if isinstance(x,bool):return x
    return str(x).strip().lower() in {"true","1","yes"}

def events():
    e=pd.read_csv(SRC)
    for c in ["breakout_time","touch_time","entry_time","rsi_context_time"]:
        if c in e.columns:e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    m=(
        (e["family"]=="ma111_after_rail")
        &(pd.to_numeric(e["rail_mult"],errors="coerce")==1)
        &(e["mode"]=="touch")
        &(pd.to_numeric(e["below_frac_24"],errors="coerce")>=.75)
        &(pd.to_numeric(e["extension_before_touch"],errors="coerce")>=.05)
        &(pd.to_numeric(e["delay_bars"],errors="coerce")<=24)
        &(e["rsi_up2"].map(bval))
    )
    x=e[m].copy().sort_values("entry_time").drop_duplicates(["coin","touch_time"]).reset_index(drop=True)
    cut=max(1,int(len(x)*.60))
    x["split"]="holdout"
    x.loc[:cut-1,"split"]="train"
    x["event_index"]=np.arange(len(x))
    return x

def candles(s,coin,interval,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":str(coin),"interval":interval,
        "startTime":int(pd.Timestamp(start).timestamp()*1000),
        "endTime":int(pd.Timestamp(end).timestamp()*1000)}})
    out=[]
    for r in rows or []:
        try:
            out.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                        "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
        except Exception:pass
    return pd.DataFrame(out).drop_duplicates("time").sort_values("time").reset_index(drop=True) if out else pd.DataFrame()

def minute_resolution(mins,target,stop,allow_target=True):
    for _,r in mins.iterrows():
        ht=allow_target and float(r["h"])>=target
        hs=float(r["l"])<=stop
        if not ht and not hs:continue
        if hs and not ht:return ("stop",r["time"],"1m")
        if ht and not hs:return ("target",r["time"],"1m")
        # Both touched inside same minute: use open only when already beyond a
        # boundary; otherwise stop conservatively.
        o=float(r["o"])
        if o<=stop:return ("stop",r["time"],"1m_open")
        if allow_target and o>=target:return ("target",r["time"],"1m_open")
        return ("stop",r["time"],"1m_both_conservative")
    return None

def find_fill_and_touch_exit(s,coin,touch_time,entry,target,stop):
    mins=candles(s,coin,"1m",touch_time,pd.Timestamp(touch_time)+pd.Timedelta(hours=4))
    if mins.empty:
        return pd.Timestamp(touch_time),None,"touch_1m_missing"
    fill_i=None
    for i,r in mins.iterrows():
        if float(r["l"])<=entry<=float(r["h"]):
            fill_i=i;break
    if fill_i is None:
        return pd.Timestamp(touch_time),None,"touch_fill_not_reconstructed"

    fr=mins.loc[fill_i]
    fill_time=pd.Timestamp(fr["time"])
    # On the fill minute, never credit a target because its high may precede
    # the downward touch. Count a stop conservatively if the minute reaches it.
    if float(fr["l"])<=stop:
        return fill_time,("stop",fill_time,"touch_minute_stop"),"touch_fill_1m"
    later=mins.loc[mins.index>fill_i]
    q=minute_resolution(later,target,stop,allow_target=True)
    return fill_time,q,"touch_fill_1m"

def resolve_4h_both(s,coin,bar_time,target,stop):
    mins=candles(s,coin,"1m",bar_time,pd.Timestamp(bar_time)+pd.Timedelta(hours=4))
    return minute_resolution(mins,target,stop,allow_target=True) if len(mins) else None

def funding(s,coin,start,end):
    rows=post(s,{"type":"fundingHistory","coin":str(coin),
                 "startTime":int(pd.Timestamp(start).timestamp()*1000),
                 "endTime":int((pd.Timestamp(end)+pd.Timedelta(hours=1)).timestamp()*1000)})
    vals=[]
    for x in rows or []:
        try:
            t=pd.to_datetime(int(x["time"]),unit="ms",utc=True)
            if t>=pd.Timestamp(start) and t<=pd.Timestamp(end):vals.append(float(x["fundingRate"]))
        except Exception:pass
    return float(np.sum(vals)),len(vals)

def replay(s,r):
    coin=str(r["coin"]);touch=pd.Timestamp(r["touch_time"]);entry=float(r["entry"])
    target=entry*(1+TP);stop=entry*(1-STOP)
    fill_time,touch_exit,fill_resolution=find_fill_and_touch_exit(s,coin,touch,entry,target,stop)
    if touch_exit:
        reason,exit_time,resolution=touch_exit
        gross=TP if reason=="target" else -STOP
    else:
        reason=None;exit_time=None;resolution=None;gross=None
        start4=touch+pd.Timedelta(hours=4)
        end=fill_time+pd.Timedelta(days=5)
        c=candles(s,coin,"4h",start4-pd.Timedelta(minutes=1),end+pd.Timedelta(hours=4))
        c=c[(c["time"]>=start4)&(c["time"]<end)].reset_index(drop=True)
        if c.empty:raise RuntimeError("no post-touch 4h candles")
        for _,bar in c.iterrows():
            ht=float(bar["h"])>=target;hs=float(bar["l"])<=stop
            if ht and hs:
                q=resolve_4h_both(s,coin,bar["time"],target,stop)
                if q is None:
                    reason="stop";exit_time=bar["time"];resolution="same4h_unresolved_stop";gross=-STOP
                else:
                    reason,exit_time,resolution=q;gross=TP if reason=="target" else -STOP
                break
            if hs:
                reason="stop";exit_time=bar["time"];resolution="4h";gross=-STOP;break
            if ht:
                reason="target";exit_time=bar["time"];resolution="4h";gross=TP;break
        if gross is None:
            last=c.iloc[-1]
            reason="timeout";exit_time=last["time"]+pd.Timedelta(hours=4);resolution="5d_timeout_close"
            gross=float(np.clip(float(last["c"])/entry-1,-STOP,TP))
    fund,n=funding(s,coin,fill_time,exit_time)
    return {
        "fill_time":fill_time,"fill_resolution":fill_resolution,
        "exit_time":exit_time,"exit_reason":reason,"exit_resolution":resolution,
        "gross_return":gross,"actual_funding_return":fund,"funding_points":n,
        "fixed_fee_slippage":RT,"net_return":gross-RT-fund
    }

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    e=events().iloc[args.shard_index::args.shard_count]
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-c6-deep-audit/1.0"
    rows=[]
    for pos,(_,r) in enumerate(e.iterrows(),1):
        try:q=replay(s,r);err=""
        except Exception as ex:
            q={"fill_time":pd.NaT,"fill_resolution":"error","exit_time":pd.NaT,"exit_reason":"error",
               "exit_resolution":"error","gross_return":np.nan,"actual_funding_return":np.nan,
               "funding_points":0,"fixed_fee_slippage":RT,"net_return":np.nan};err=str(ex)[:180]
        rows.append({**r.to_dict(),**q,"error":err})
        print(pos,len(e),r["coin"],r["touch_time"],r["split"])
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"audit_{args.shard_index:02d}.csv",index=False)

def meta(s):
    m,ctx=post(s,{"type":"metaAndAssetCtxs"});rows=[]
    for i,u in enumerate(m.get("universe",[])):
        if u.get("isDelisted") or not u.get("name"):continue
        c=ctx[i] if i<len(ctx) else {}
        rows.append({"coin":str(u["name"]),"day_volume":float(c.get("dayNtlVlm") or 0),
                     "mark":float(c.get("markPx") or 0)})
    return pd.DataFrame(rows)

def book(s,coin):
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
    mid=(bids[0][0]+asks[0][0])/2
    rem=float(notional);base=spent=0.0
    for px,sz in asks:
        take=min(rem,px*sz);spent+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    buy=rem<=max(1e-6,notional*1e-9);avg=spent/base if base else np.nan
    rb=base;proc=0.0
    for px,sz in bids:
        take=min(rb,sz);proc+=take*px;rb-=take
        if rb<=1e-12:break
    sell=rb<=max(1e-10,base*1e-9)
    return {"full_fill":bool(buy and sell),
            "entry_slippage":avg/mid-1 if buy and mid>0 else np.nan,
            "roundtrip_book_impact":1-proc/spent if buy and sell and spent>0 else np.nan}

def capacity(hold,out):
    s=requests.Session();s.headers["User-Agent"]="appwiza-ma-c6-capacity/1.0"
    mm=meta(s);wanted=pd.DataFrame({"coin":sorted(set(hold["coin"].astype(str)))})
    z=wanted.merge(mm,on="coin",how="left");rows=[]
    for _,r in z.iterrows():
        rec=r.to_dict()
        try:
            bids,asks=book(s,r["coin"]);rec["status"]="ok"
            for sz in SIZES:
                q=sim(bids,asks,sz)
                rec[f"full_fill_{sz}"]=q["full_fill"]
                rec[f"entry_slippage_{sz}"]=q.get("entry_slippage")
                rec[f"roundtrip_book_impact_{sz}"]=q.get("roundtrip_book_impact")
        except Exception as ex:
            rec["status"]="error";rec["error"]=str(ex)[:120]
        rows.append(rec);time.sleep(.08)
    q=pd.DataFrame(rows);q.to_csv(out/"current_l2_capacity.csv",index=False)
    return q

def summary(g):
    ok=g[pd.to_numeric(g["net_return"],errors="coerce").notna()].copy()
    nr=pd.to_numeric(ok["net_return"],errors="coerce")
    return {
        "n":len(g),"replayed_n":len(ok),
        "target_rate":float((ok["exit_reason"]=="target").mean()) if len(ok) else np.nan,
        "stop_rate":float((ok["exit_reason"]=="stop").mean()) if len(ok) else np.nan,
        "timeout_rate":float((ok["exit_reason"]=="timeout").mean()) if len(ok) else np.nan,
        "avg_funding":float(pd.to_numeric(ok["actual_funding_return"],errors="coerce").mean()) if len(ok) else np.nan,
        "net_roi":float(nr.mean()) if len(ok) else np.nan,
        "pnl_per_10000":float(nr.mean()*10000) if len(ok) else np.nan,
    }

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in sorted(Path(args.input_dir).rglob("audit_*.csv")):
        try:
            d=pd.read_csv(p)
            if len(d):fs.append(d)
        except Exception:pass
    t=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if t.empty:raise SystemExit("no audit rows")
    for c in ["touch_time","fill_time","exit_time","entry_time"]:
        if c in t:t[c]=pd.to_datetime(t[c],utc=True,errors="coerce")
    t=t.sort_values(["split","entry_time","coin"]).drop_duplicates(["coin","touch_time"]).reset_index(drop=True)
    t.to_csv(out/"trades.csv",index=False)
    tr=t[t["split"]=="train"];ho=t[t["split"]=="holdout"]
    st=summary(tr);sh=summary(ho)
    cap=capacity(ho,out)
    ff=pd.Series(cap.get("full_fill_10000",pd.Series(dtype=bool))).astype(str).str.lower().isin(["true","1"])
    rt=pd.to_numeric(cap.get("roundtrip_book_impact_10000",pd.Series(dtype=float)),errors="coerce")
    es=pd.to_numeric(cap.get("entry_slippage_10000",pd.Series(dtype=float)),errors="coerce")
    capacity_stats={
        "historical_holdout_coins":int(ho["coin"].nunique()),
        "current_books_ok":int((cap.get("status",pd.Series(dtype=str))=="ok").sum()) if len(cap) else 0,
        "full_fill_10k_fraction":float(ff.mean()) if len(ff) else np.nan,
        "median_entry_slippage_10k":float(es.median()) if es.notna().any() else np.nan,
        "p90_entry_slippage_10k":float(es.quantile(.90)) if es.notna().any() else np.nan,
        "median_roundtrip_book_impact_10k":float(rt.median()) if rt.notna().any() else np.nan,
        "p90_roundtrip_book_impact_10k":float(rt.quantile(.90)) if rt.notna().any() else np.nan,
    }
    promotion_ready=bool(
        sh["replayed_n"]>=25 and sh["target_rate"]>=.75 and sh["net_roi"]>=.015
        and BIN_N>=15 and BIN_HIT5>=.75 and BIN_RISK>=.67 and BIN_ROI>=.010
    )
    result={
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "rule":"SMA350 breakout -> >=5% extension -> first MA111 touch <=24 bars -> prior completed RSI rises 2 consecutive 4H bars -> touch entry",
        "target":TP,"stop":STOP,"horizon_days":5,
        "hyperliquid_train":st,"hyperliquid_holdout":sh,
        "binance_frozen":{"n":BIN_N,"hit5":BIN_HIT5,"target_before_stop":BIN_RISK,"modeled_net_roi":BIN_ROI},
        "current_l2_capacity":capacity_stats,
        "promotion_ready":promotion_ready,
        "promotion_note":"No live order execution. If promoted, this means scanner/site/email qualification only."
    }
    (out/"summary.json").write_text(json.dumps(result,indent=2,default=str))
    pd.DataFrame([{"split":"train",**st},{"split":"holdout",**sh}]).to_csv(out/"summary.csv",index=False)
    lines=[
        "MA111 AFTER SMA350 — DEEP ECONOMIC AUDIT","",
        result["rule"],"",
        "HYPERLIQUID TRAIN",str(st),"",
        "HYPERLIQUID HOLDOUT",str(sh),"",
        "FROZEN BINANCE",str(result["binance_frozen"]),"",
        "CURRENT $10K L2 CAPACITY",str(capacity_stats),"",
        f"PROMOTION_READY={promotion_ready}","",
        "Actual HL funding and reconstructed 1m touch fills are included. Current L2 is a present-day capacity proxy only.",
        "No orders are enabled."
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--shard-index",type=int,default=0);ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.10);ap.add_argument("--out",default="ma_c6_out")
    ap.add_argument("--input-dir",default="ma_c6_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)
if __name__=="__main__":main()
