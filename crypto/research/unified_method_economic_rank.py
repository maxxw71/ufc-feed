#!/usr/bin/env python3
"""
Unified Hyperliquid economic ranking for all live + newly discovered crypto methods.

Historical ranking:
- untouched/later-40% method holdouts only
- +5% target / -7.5% stop / 5-day timeout
- same-4H target+stop order resolved with 1m candles when available
- 0.045% taker fee per side
- 0.10% modeled slippage per side
- actual Hyperliquid funding summed only until the realized exit

Capacity snapshot:
- current Hyperliquid primary-perp L2 book
- simulate $1k / $5k / $10k market buys and immediate sell-back
- report fill coverage, entry slippage and round-trip book impact
- this is a live capacity proxy, not historical order-book reconstruction

Research only. Does not place orders and does not promote methods live.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"
ROOT=Path("crypto/research")
BLOW=ROOT/"results_hyperliquid_all_pairs/events.csv"
LOWER=ROOT/"results_lower_high_hyperliquid_replication/events.csv"
C3F=ROOT/"results_c3_funding_enrichment/events.csv"
REG=ROOT/"auto_discovery/candidate_registry.json"
METHODS=Path("crypto/live/methods.json")

TP=.05
STOP=.075
FEE_SIDE=.00045
MODEL_SLIP_SIDE=.0010
ROUND_TRIP_FIXED=2*(FEE_SIDE+MODEL_SLIP_SIDE)
SIZES=(1000,5000,10000)

def post(s,payload,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=payload,timeout=45)
            if r.status_code==200:
                return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
        except Exception as exc:
            last=exc
        time.sleep(min(12,1.25*(k+1)))
    raise RuntimeError(str(last))

def bval(x):
    if isinstance(x,bool): return x
    return str(x).strip().lower() in {"true","1","yes"}

def fnum(x):
    try:
        v=float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan

def later40(df):
    x=df.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(x)*.60))
    return x.iloc[cut:].copy()

def apply_conditions(df,conds):
    m=pd.Series(True,index=df.index)
    for c in conds:
        f=c["feature"]
        if f not in df.columns:
            return pd.Series(False,index=df.index)
        x=pd.to_numeric(df[f],errors="coerce")
        op=c["op"];a=float(c["a"]);bb=c.get("b")
        if op=="<=": m &= x<=a
        elif op==">=": m &= x>=a
        elif op=="band": m &= x.between(a,float(bb),inclusive="both")
        else: m &= False
    return m

def live_config():
    j=json.loads(METHODS.read_text())
    return {m["id"]:m for m in j.get("methods",[])}

def build_memberships():
    cfg=live_config()
    rows=[]

    e=pd.read_csv(BLOW)
    for c in ["arm_time","trigger_time","entry_time"]:
        e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")
    e=e[(e["kind"]=="perp")&(e["dex"]=="primary")]
    e=e[np.isclose(pd.to_numeric(e["flush_threshold"],errors="coerce"),.08)]
    e=e.sort_values("entry_time").reset_index(drop=True)
    h=later40(e)

    p=cfg["C1"]["params"]
    m=(pd.to_numeric(h["rsi_pct1"],errors="coerce")<=float(p["rsi_pct1_max"])) & (
       pd.to_numeric(h["rsi_accel"],errors="coerce")<=float(p["rsi_accel_max"]))
    for _,r in h[m].iterrows():
        rows.append(member("C1",cfg["C1"]["name"],"LIVE",r["display_name"],r["api_coin"],r["trigger_time"],r["entry_time"],r["entry"],"blowoff_holdout"))

    p=cfg["C3"]["params"]
    m=(pd.to_numeric(h["close_location"],errors="coerce")<=float(p["close_location_max"])) & (
       pd.to_numeric(h["volume_ratio20"],errors="coerce")>=float(p["volume_ratio20_min"]))
    for _,r in h[m].iterrows():
        rows.append(member("C3",cfg["C3"]["name"],"LIVE",r["display_name"],r["api_coin"],r["trigger_time"],r["entry_time"],r["entry"],"blowoff_holdout"))

    p=cfg["C4"]["params"]
    m=(pd.to_numeric(h["rsi_accel"],errors="coerce")<=float(p["rsi_accel_max"])) & (
       pd.to_numeric(h["rsi_vs_sma3"],errors="coerce")<=float(p["rsi_vs_sma3_max"]))
    for _,r in h[m].iterrows():
        rows.append(member("C4",cfg["C4"]["name"],"LIVE",r["display_name"],r["api_coin"],r["trigger_time"],r["entry_time"],r["entry"],"blowoff_holdout"))

    # Current literal C3F contract. This intentionally uses the exact threshold
    # in methods.json so any historical rounding mismatch becomes visible.
    if C3F.exists():
        z=pd.read_csv(C3F)
        for c in ["trigger_time","entry_time"]:
            z[c]=pd.to_datetime(z[c],utc=True,errors="coerce")
        z=later40(z)
        th=float(cfg["C3F"]["params"]["funding_rate_min"])
        z=z[pd.to_numeric(z["funding_rate_at_trigger"],errors="coerce")>=th]
        for _,r in z.iterrows():
            rows.append(member("C3F",cfg["C3F"]["name"],"LIVE",r["coin"],r["coin"],r["trigger_time"],r["entry_time"],r["entry"],"c3_holdout_literal_funding"))

    q=pd.read_csv(LOWER)
    for c in ["trigger_time","entry_time"]:
        q[c]=pd.to_datetime(q[c],utc=True,errors="coerce")

    c2=q[q["C_lowerhigh_bigpump"].map(bval)].copy()
    for _,r in later40(c2).iterrows():
        rows.append(member("C2",cfg["C2"]["name"],"LIVE",r["coin"],r["coin"],r["trigger_time"],r["entry_time"],r["entry"],"lower_high_holdout"))

    dth=float(cfg["C2D"]["params"]["dist_ema9_max"])
    c2d=c2[pd.to_numeric(c2["dist_ema9"],errors="coerce")<=dth].copy()
    for _,r in later40(c2d).iterrows():
        rows.append(member("C2D",cfg["C2D"]["name"],"LIVE",r["coin"],r["coin"],r["trigger_time"],r["entry_time"],r["entry"],"lower_high_deepstretch_holdout"))

    # All newly discovered candidates, not just cross-validated survivors.
    if REG.exists():
        reg=json.loads(REG.read_text())
        for c in reg.get("candidates",[]):
            mm=apply_conditions(h,c.get("conditions",[]))
            mid=c["candidate_id"]
            name="AUTO: "+c["rule"]
            for _,r in h[mm].iterrows():
                rows.append(member(mid,name,c.get("status","SHADOW_ONLY"),r["display_name"],r["api_coin"],r["trigger_time"],r["entry_time"],r["entry"],"auto_discovery_holdout"))

    out=pd.DataFrame(rows)
    if out.empty:return out
    out=out.drop_duplicates(["method_id","api_coin","entry_time","entry"]).sort_values(["entry_time","method_id"]).reset_index(drop=True)
    return out

def member(mid,name,status,coin,api_coin,trigger_time,entry_time,entry,source):
    return {
        "method_id":mid,"method_name":name,"method_status":status,
        "coin":str(coin),"api_coin":str(api_coin),
        "trigger_time":pd.Timestamp(trigger_time),
        "entry_time":pd.Timestamp(entry_time),
        "entry":float(entry),"source":source,
    }

def candles(s,coin,interval,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":coin,"interval":interval,
        "startTime":int(pd.Timestamp(start).timestamp()*1000),
        "endTime":int(pd.Timestamp(end).timestamp()*1000)
    }})
    a=[]
    for r in rows or []:
        try:
            a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                      "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
        except Exception:pass
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time") if a else pd.DataFrame()

def resolve_same_bar(s,coin,bar_time,target,stop):
    try:
        fine=candles(s,coin,"1m",bar_time,pd.Timestamp(bar_time)+pd.Timedelta(hours=4))
    except Exception:
        return None
    if fine.empty:return None
    for _,r in fine.iterrows():
        ht=float(r["h"])>=target
        hs=float(r["l"])<=stop
        if not ht and not hs:continue
        if ht and not hs:return ("target",r["time"],"1m")
        if hs and not ht:return ("stop",r["time"],"1m")
        o=float(r["o"])
        if o>=target:return ("target",r["time"],"1m_open")
        if o<=stop:return ("stop",r["time"],"1m_open")
        return None
    return None

def funding_until(s,coin,start,end):
    rows=post(s,{"type":"fundingHistory","coin":coin,
                 "startTime":int(pd.Timestamp(start).timestamp()*1000),
                 "endTime":int((pd.Timestamp(end)+pd.Timedelta(hours=1)).timestamp()*1000)})
    vals=[]
    for x in rows or []:
        try:
            t=pd.to_datetime(int(x["time"]),unit="ms",utc=True)
            if t>=pd.Timestamp(start) and t<=pd.Timestamp(end):
                vals.append(float(x["fundingRate"]))
        except Exception:pass
    return float(np.sum(vals)),len(vals)

def replay_trade(s,r):
    st=pd.Timestamp(r["entry_time"])
    en=st+pd.Timedelta(days=5)
    coin=str(r["api_coin"]);entry=float(r["entry"])
    target=entry*(1+TP);stop=entry*(1-STOP)
    c=candles(s,coin,"4h",st-pd.Timedelta(minutes=1),en+pd.Timedelta(hours=4))
    c=c[(c["time"]>=st)&(c["time"]<en)].reset_index(drop=True)
    if c.empty:
        raise RuntimeError("no 4h candles in replay window")

    reason="timeout";gross=None;exit_time=None;resolution="4h"
    ambiguous=False
    for _,bar in c.iterrows():
        ht=float(bar["h"])>=target
        hs=float(bar["l"])<=stop
        if ht and hs:
            ambiguous=True
            rr=resolve_same_bar(s,coin,bar["time"],target,stop)
            if rr is not None:
                reason,exit_time,resolution=rr
                gross=TP if reason=="target" else -STOP
            else:
                reason="stop"
                gross=-STOP
                exit_time=bar["time"]
                resolution="same_1m_unresolved_conservative_stop"
            break
        if ht:
            reason="target";gross=TP;exit_time=bar["time"];break
        if hs:
            reason="stop";gross=-STOP;exit_time=bar["time"];break

    if gross is None:
        last=c.iloc[-1]
        gross=float(last["c"])/entry-1
        gross=float(np.clip(gross,-STOP,TP))
        exit_time=last["time"]+pd.Timedelta(hours=4)
        resolution="5d_timeout_close"

    fund,points=funding_until(s,coin,st,exit_time)
    net=gross-ROUND_TRIP_FIXED-fund
    return {
        "exit_time":exit_time,"exit_reason":reason,"resolution":resolution,
        "same_4h_ambiguous":ambiguous,"gross_return":gross,
        "funding_return":fund,"funding_points":points,
        "fixed_fee_slippage":ROUND_TRIP_FIXED,"net_return":net,
    }

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    m=build_memberships()
    if m.empty:
        pd.DataFrame().to_csv(out/f"econ_shard_{args.shard_index:02d}.csv",index=False);return
    keys=m[["api_coin","entry_time","entry"]].drop_duplicates().sort_values(["entry_time","api_coin"]).reset_index(drop=True)
    keys=keys.iloc[args.shard_index::args.shard_count]
    s=requests.Session();s.headers["User-Agent"]="appwiza-unified-econ/1.0"
    rows=[]
    for pos,(_,k) in enumerate(keys.iterrows(),1):
        members=m[(m["api_coin"]==k["api_coin"])&(m["entry_time"]==k["entry_time"])&(np.isclose(m["entry"],k["entry"]))]
        try:
            q=replay_trade(s,k)
            err=""
        except Exception as exc:
            q={"exit_time":pd.NaT,"exit_reason":"error","resolution":"error",
               "same_4h_ambiguous":False,"gross_return":np.nan,"funding_return":np.nan,
               "funding_points":0,"fixed_fee_slippage":ROUND_TRIP_FIXED,"net_return":np.nan}
            err=str(exc)[:180]
        for _,r in members.iterrows():
            rows.append({**r.to_dict(),**q,"error":err})
        print(f"{pos}/{len(keys)} {k['api_coin']} {k['entry_time']}")
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"econ_shard_{args.shard_index:02d}.csv",index=False)

def fetch_primary_meta(s):
    meta,ctx=post(s,{"type":"metaAndAssetCtxs"})
    out=[]
    for i,u in enumerate(meta.get("universe",[])):
        if u.get("isDelisted") or not u.get("name"):continue
        c=ctx[i] if i<len(ctx) else {}
        out.append({
            "coin":str(u["name"]),
            "day_volume":fnum(c.get("dayNtlVlm")),
            "open_interest_units":fnum(c.get("openInterest")),
            "mark_px":fnum(c.get("markPx")),
        })
    return pd.DataFrame(out)

def book_snapshot(s,coin):
    j=post(s,{"type":"l2Book","coin":coin})
    levels=j.get("levels",[]) if isinstance(j,dict) else []
    bids=levels[0] if len(levels)>0 else []
    asks=levels[1] if len(levels)>1 else []
    def conv(a):
        z=[]
        for x in a or []:
            try:z.append((float(x["px"]),float(x["sz"])))
            except Exception:pass
        return z
    return conv(bids),conv(asks)

def simulate_buy_sell(bids,asks,quote):
    if not bids or not asks:return None
    best_bid=bids[0][0];best_ask=asks[0][0];mid=(best_bid+best_ask)/2
    rem=float(quote);base=0.0;spent=0.0
    for px,sz in asks:
        cap=px*sz
        take=min(rem,cap)
        base+=take/px;spent+=take;rem-=take
        if rem<=1e-9:break
    buy_full=rem<=max(1e-6,quote*1e-9)
    avg_buy=spent/base if base>0 else np.nan
    rem_base=base;proceeds=0.0
    for px,sz in bids:
        take=min(rem_base,sz)
        proceeds+=take*px;rem_base-=take
        if rem_base<=1e-12:break
    sell_full=rem_base<=max(1e-10,base*1e-9)
    return {
        "full_fill":bool(buy_full and sell_full),
        "mid":mid,"best_bid":best_bid,"best_ask":best_ask,
        "entry_slippage":avg_buy/mid-1 if buy_full and mid>0 else np.nan,
        "roundtrip_book_impact":1-proceeds/spent if buy_full and sell_full and spent>0 else np.nan,
        "buy_book_coverage":spent/quote if quote>0 else np.nan,
    }

def liquidity_snapshot(members,outdir):
    s=requests.Session();s.headers["User-Agent"]="appwiza-liquidity-capacity/1.0"
    meta=fetch_primary_meta(s)
    wanted=set(members["coin"].astype(str))
    z=meta[meta["coin"].isin(wanted)].copy()
    rows=[]
    for pos,(_,r) in enumerate(z.iterrows(),1):
        try:
            bids,asks=book_snapshot(s,r["coin"])
            base={**r.to_dict(),"book_status":"ok","bid_levels":len(bids),"ask_levels":len(asks)}
            for size in SIZES:
                q=simulate_buy_sell(bids,asks,size)
                base[f"full_fill_{size}"]=q["full_fill"] if q else False
                base[f"entry_slippage_{size}"]=q["entry_slippage"] if q else np.nan
                base[f"roundtrip_book_impact_{size}"]=q["roundtrip_book_impact"] if q else np.nan
                base[f"buy_book_coverage_{size}"]=q["buy_book_coverage"] if q else np.nan
            rows.append(base)
        except Exception as exc:
            rows.append({**r.to_dict(),"book_status":"error:"+str(exc)[:120]})
        print(f"L2 {pos}/{len(z)} {r['coin']}")
        time.sleep(.12)
    q=pd.DataFrame(rows)
    q.to_csv(outdir/"current_l2_capacity.csv",index=False)
    return q

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in sorted(Path(args.input_dir).rglob("econ_shard_*.csv")):
        try:
            d=pd.read_csv(p)
            if len(d):fs.append(d)
        except Exception:pass
    t=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if t.empty:raise SystemExit("No economic replay rows")
    for c in ["trigger_time","entry_time","exit_time"]:
        t[c]=pd.to_datetime(t[c],utc=True,errors="coerce")
    t=t.sort_values(["method_id","entry_time","coin"]).reset_index(drop=True)
    t.to_csv(out/"trades.csv",index=False)

    summaries=[]
    for (mid,name,status),g in t.groupby(["method_id","method_name","method_status"],dropna=False):
        ok=g[pd.to_numeric(g["net_return"],errors="coerce").notna()].copy()
        if ok.empty:continue
        nr=pd.to_numeric(ok["net_return"],errors="coerce")
        summaries.append({
            "method_id":mid,"method_name":name,"status":status,
            "signals":len(g),"economically_replayed":len(ok),
            "target_rate":float((ok["exit_reason"]=="target").mean()),
            "stop_rate":float((ok["exit_reason"]=="stop").mean()),
            "timeout_rate":float((ok["exit_reason"]=="timeout").mean()),
            "same_4h_ambiguous":int(ok["same_4h_ambiguous"].map(bval).sum()),
            "avg_gross_return":float(pd.to_numeric(ok["gross_return"],errors="coerce").mean()),
            "avg_actual_funding":float(pd.to_numeric(ok["funding_return"],errors="coerce").mean()),
            "median_actual_funding":float(pd.to_numeric(ok["funding_return"],errors="coerce").median()),
            "avg_net_roi":float(nr.mean()),"median_net_roi":float(nr.median()),
            "pnl_per_1000":float(nr.mean()*1000),
            "pnl_per_5000":float(nr.mean()*5000),
            "pnl_per_10000":float(nr.mean()*10000),
        })
    s=pd.DataFrame(summaries).sort_values(["avg_net_roi","economically_replayed"],ascending=[False,False])
    s.to_csv(out/"economic_ranking.csv",index=False)

    liq=liquidity_snapshot(t,out)
    lm=[]
    for (mid,name,status),g in t.groupby(["method_id","method_name","method_status"],dropna=False):
        coins=pd.DataFrame({"coin":sorted(set(g["coin"].astype(str)))})
        j=coins.merge(liq,on="coin",how="left")
        rec={"method_id":mid,"method_name":name,"status":status,
             "historical_unique_coins":len(coins),
             "currently_active_with_book":int((j["book_status"]=="ok").sum()) if "book_status" in j else 0}
        for size in SIZES:
            ff=j.get(f"full_fill_{size}",pd.Series(dtype=bool)).map(bval) if len(j) else pd.Series(dtype=bool)
            es=pd.to_numeric(j.get(f"entry_slippage_{size}",pd.Series(dtype=float)),errors="coerce")
            rt=pd.to_numeric(j.get(f"roundtrip_book_impact_{size}",pd.Series(dtype=float)),errors="coerce")
            rec[f"full_fill_fraction_{size}"]=float(ff.mean()) if len(ff) else np.nan
            rec[f"median_entry_slippage_{size}"]=float(es.median()) if es.notna().any() else np.nan
            rec[f"p90_entry_slippage_{size}"]=float(es.quantile(.90)) if es.notna().any() else np.nan
            rec[f"median_roundtrip_book_impact_{size}"]=float(rt.median()) if rt.notna().any() else np.nan
            rec[f"p90_roundtrip_book_impact_{size}"]=float(rt.quantile(.90)) if rt.notna().any() else np.nan
        lm.append(rec)
    lms=pd.DataFrame(lm)
    lms.to_csv(out/"liquidity_by_method.csv",index=False)

    merged=s.merge(lms,on=["method_id","method_name","status"],how="left")
    # Replace the generic 0.20% modeled round-trip slippage with today's median
    # $10k book impact as a capacity proxy. Keep this explicitly separate from
    # the historical ranking because today's book is not the historical book.
    if "median_roundtrip_book_impact_10000" in merged:
        merged["current_10k_book_proxy_roi"]=merged["avg_net_roi"] + 2*MODEL_SLIP_SIDE - merged["median_roundtrip_book_impact_10000"]
        merged["current_10k_book_proxy_pnl"]=merged["current_10k_book_proxy_roi"]*10000
    merged.to_csv(out/"economic_plus_liquidity.csv",index=False)

    lines=[
        "UNIFIED HYPERLIQUID METHOD ECONOMICS + CAPACITY",
        "",
        "Historical contract: +5% TP / -7.5% SL / 5d timeout / 0.045% taker per side / 0.10% modeled slippage per side / ACTUAL funding to realized exit.",
        "Same-4H target+stop order is resolved with 1m candles when available; unresolved same-minute cases are stopped conservatively.",
        "Current L2 capacity is a live snapshot proxy only; it is not used to rewrite historical fills.",
        "",
        "ECONOMIC RANKING",
        s.to_string(index=False),
        "",
        "CURRENT L2 CAPACITY BY METHOD",
        lms.to_string(index=False),
        "",
        "COMBINED (includes today's $10k-book proxy)",
        merged.to_string(index=False),
        "",
        "Research only. No order placement and no automatic live-method promotion.",
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.20)
    ap.add_argument("--out",default="econ_out")
    ap.add_argument("--input-dir",default="econ_shards")
    args=ap.parse_args()
    if args.mode=="scan":scan(args)
    else:aggregate(args)

if __name__=="__main__":
    main()
