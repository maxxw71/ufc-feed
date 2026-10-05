#!/usr/bin/env python3
"""
Deep validation for novel Hyperliquid-first context candidates.

Uses the already-frozen rules in novel_context_discovery/candidate_registry.json.
No thresholds are changed here.

For each candidate:
1) Replay the original untouched Hyperliquid later-40% holdout with
   +5% TP / -7.5% SL / 5d timeout, 1m same-bar resolution, actual funding,
   0.045% taker fee/side and 0.10% modeled slippage/side.
2) Apply the exact frozen rule to the FULL independent Binance event panel
   (and separately retain latest-40% results). No Binance fitting.
3) Label only candidates that clear both economic and external-confirmation
   gates as DEEP_VALIDATED_SHADOW. Never promotes live.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
from math import sqrt
import numpy as np
import pandas as pd
import requests

API="https://api.hyperliquid.xyz/info"
ROOT=Path("crypto/research")
REG=ROOT/"novel_context_discovery/candidate_registry.json"
HL=ROOT/"novel_context_discovery/hyperliquid_context_events.csv"
BIN=ROOT/"novel_context_discovery/binance_context_events.csv"
OUT=ROOT/"results_novel_candidate_deep_validation"

TP=.05
STOP=.075
HL_FEE=.00045
HL_SLIP=.0010
HL_RT=2*(HL_FEE+HL_SLIP)
BIN_RT=2*(.00070+.0010)

def post(s,p,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.post(API,json=p,timeout=45)
            if r.status_code==200:return r.json()
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
        except Exception as e:last=e
        time.sleep(min(12,1.2*(k+1)))
    raise RuntimeError(str(last))

def bval(x):
    if isinstance(x,bool):return x
    return str(x).strip().lower() in {"true","1","yes"}

def num(x):
    try:
        v=float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:return np.nan

def apply_condition(df,c):
    f=c["feature"]
    if f not in df.columns:return pd.Series(False,index=df.index)
    x=pd.to_numeric(df[f],errors="coerce")
    a=float(c["a"]);b=c.get("b")
    if c["op"]=="<=":return x<=a
    if c["op"]==">=":return x>=a
    if c["op"]=="band":return x.between(a,float(b),inclusive="both")
    return pd.Series(False,index=df.index)

def apply_rule(df,conds):
    m=pd.Series(True,index=df.index)
    for c in conds:m &= apply_condition(df,c)
    return m

def rate(s):
    q=s.dropna()
    return float(q.astype(bool).mean()) if len(q) else np.nan

def wilson(w,n,z=1.96):
    if n<=0:return np.nan
    p=w/n;den=1+z*z/n
    ctr=(p+z*z/(2*n))/den
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return ctr-half

def modeled_roi(g,cost):
    if g.empty:return np.nan
    win=g["t50_before_s75_5d"].map(bval)
    mae=pd.to_numeric(g["mae_5d"],errors="coerce")
    timeout=pd.to_numeric(g["close_ret_5d"],errors="coerce").fillna(0)
    stopped=(~win)&(mae<=-STOP)
    gross=pd.Series(np.where(win,TP,np.where(stopped,-STOP,timeout)),index=g.index).clip(-STOP,TP)
    return float((gross-cost).mean())

def candles(s,coin,interval,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":coin,"interval":interval,
        "startTime":int(pd.Timestamp(start).timestamp()*1000),
        "endTime":int(pd.Timestamp(end).timestamp()*1000)}})
    a=[]
    for r in rows or []:
        try:
            a.append({"time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                      "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),"c":float(r["c"])})
        except Exception:pass
    return pd.DataFrame(a).drop_duplicates("time").sort_values("time") if a else pd.DataFrame()

def resolve_same(s,coin,t,target,stop):
    fine=candles(s,coin,"1m",t,pd.Timestamp(t)+pd.Timedelta(hours=4))
    if fine.empty:return None
    for _,b in fine.iterrows():
        ht=float(b["h"])>=target;hs=float(b["l"])<=stop
        if not ht and not hs:continue
        if ht and not hs:return ("target",b["time"],"1m")
        if hs and not ht:return ("stop",b["time"],"1m")
        o=float(b["o"])
        if o>=target:return ("target",b["time"],"1m_open")
        if o<=stop:return ("stop",b["time"],"1m_open")
        return None
    return None

def funding(s,coin,start,end):
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

def replay(s,r):
    st=pd.Timestamp(r["entry_time"]);coin=str(r["api_coin"]);entry=float(r["entry"])
    en=st+pd.Timedelta(days=5);target=entry*(1+TP);stop=entry*(1-STOP)
    c=candles(s,coin,"4h",st-pd.Timedelta(minutes=1),en+pd.Timedelta(hours=4))
    c=c[(c["time"]>=st)&(c["time"]<en)].reset_index(drop=True)
    if c.empty:raise RuntimeError("no replay candles")
    gross=None;reason="timeout";et=None;resolution="4h";amb=False
    for _,b in c.iterrows():
        ht=float(b["h"])>=target;hs=float(b["l"])<=stop
        if ht and hs:
            amb=True
            rr=resolve_same(s,coin,b["time"],target,stop)
            if rr:
                reason,et,resolution=rr;gross=TP if reason=="target" else -STOP
            else:
                reason="stop";et=b["time"];resolution="unresolved_conservative_stop";gross=-STOP
            break
        if ht:reason="target";et=b["time"];gross=TP;break
        if hs:reason="stop";et=b["time"];gross=-STOP;break
    if gross is None:
        last=c.iloc[-1];gross=float(last["c"])/entry-1;gross=float(np.clip(gross,-STOP,TP))
        et=last["time"]+pd.Timedelta(hours=4);resolution="timeout_close"
    fund,n=funding(s,coin,st,et)
    net=gross-HL_RT-fund
    return {"exit_time":et,"exit_reason":reason,"resolution":resolution,
            "same_bar_ambiguous":amb,"gross_return":gross,"funding_return":fund,
            "funding_points":n,"net_return":net}

def memberships():
    reg=json.loads(REG.read_text())
    h=pd.read_csv(HL)
    for c in ["trigger_time","entry_time"]:h[c]=pd.to_datetime(h[c],utc=True,errors="coerce")
    h=h.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(h)*.60));hold=h.iloc[cut:].copy()
    rows=[]
    for cand in reg.get("candidates",[]):
        g=hold[apply_rule(hold,cand["conditions"])]
        for _,r in g.iterrows():
            rows.append({"candidate_id":cand["candidate_id"],"rule":cand["rule"],
                         "coin":r["display_name"],"api_coin":r["api_coin"],
                         "trigger_time":r["trigger_time"],"entry_time":r["entry_time"],"entry":r["entry"]})
    return pd.DataFrame(rows)

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    m=memberships()
    if m.empty:
        pd.DataFrame().to_csv(out/f"shard_{args.shard_index:02d}.csv",index=False);return
    keys=m[["api_coin","entry_time","entry"]].drop_duplicates().sort_values(["entry_time","api_coin"]).reset_index(drop=True)
    keys=keys.iloc[args.shard_index::args.shard_count]
    s=requests.Session();s.headers["User-Agent"]="appwiza-novel-deep-validation/1.0"
    rows=[]
    for pos,(_,k) in enumerate(keys.iterrows(),1):
        mem=m[(m["api_coin"]==k["api_coin"])&(m["entry_time"]==k["entry_time"])&(np.isclose(pd.to_numeric(m["entry"],errors="coerce"),float(k["entry"])))]
        try:q=replay(s,k);err=""
        except Exception as ex:
            q={"exit_time":pd.NaT,"exit_reason":"error","resolution":"error","same_bar_ambiguous":False,
               "gross_return":np.nan,"funding_return":np.nan,"funding_points":0,"net_return":np.nan};err=str(ex)[:180]
        for _,r in mem.iterrows():rows.append({**r.to_dict(),**q,"error":err})
        print(pos,len(keys),k["api_coin"],k["entry_time"])
        time.sleep(args.sleep)
    pd.DataFrame(rows).to_csv(out/f"shard_{args.shard_index:02d}.csv",index=False)

def binance_stats(cand,b):
    g=b[apply_rule(b,cand["conditions"])].copy()
    n=len(g);h5=int(g["hit5_5d"].map(bval).sum()) if n else 0
    return {
        "n":n,"hit5":rate(g["hit5_5d"].map(bval)) if n else np.nan,
        "hit10":rate(g["hit10_5d"].map(bval)) if n else np.nan,
        "risk":rate(g["t50_before_s75_5d"].map(bval)) if n else np.nan,
        "net_roi":modeled_roi(g,BIN_RT) if n else np.nan,
        "wilson_lower":wilson(h5,n) if n else np.nan,
    }

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[]
    for p in sorted(Path(args.input_dir).rglob("shard_*.csv")):
        try:
            d=pd.read_csv(p)
            if len(d):fs.append(d)
        except Exception:pass
    t=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    if t.empty:raise SystemExit("no deep replay rows")
    for c in ["trigger_time","entry_time","exit_time"]:
        t[c]=pd.to_datetime(t[c],utc=True,errors="coerce")
    t=t.drop_duplicates(["candidate_id","api_coin","entry_time"]).sort_values(["candidate_id","entry_time"])
    t.to_csv(out/"hyperliquid_actual_funding_trades.csv",index=False)

    reg=json.loads(REG.read_text())
    b=pd.read_csv(BIN)
    for c in ["trigger_time","entry_time"]:
        if c in b.columns:b[c]=pd.to_datetime(b[c],utc=True,errors="coerce")
    b=b.sort_values("entry_time").reset_index(drop=True)
    cut=max(1,int(len(b)*.60));b40=b.iloc[cut:].copy()

    rows=[];deep=[]
    byid={x["candidate_id"]:x for x in reg.get("candidates",[])}
    for cid,cand in byid.items():
        g=t[t["candidate_id"]==cid]
        ok=g[pd.to_numeric(g["net_return"],errors="coerce").notna()].copy()
        nr=pd.to_numeric(ok["net_return"],errors="coerce")
        full=binance_stats(cand,b);latest=binance_stats(cand,b40)
        hl_n=len(ok);hl_target=float((ok["exit_reason"]=="target").mean()) if hl_n else np.nan
        hl_roi=float(nr.mean()) if hl_n else np.nan
        passed=(
            hl_n>=20 and hl_target>=.72 and hl_roi>=.015
            and full["n"]>=15 and full["hit5"]>=.78 and full["risk"]>=.68
            and full["net_roi"]>=.010 and full["wilson_lower"]>=.58
        )
        status="DEEP_VALIDATED_SHADOW" if passed else "SHADOW_ONLY"
        row={"candidate_id":cid,"status":status,"rule":cand["rule"],
             "hl_n":hl_n,"hl_target_rate":hl_target,
             "hl_stop_rate":float((ok["exit_reason"]=="stop").mean()) if hl_n else np.nan,
             "hl_avg_actual_funding":float(pd.to_numeric(ok["funding_return"],errors="coerce").mean()) if hl_n else np.nan,
             "hl_actual_funding_net_roi":hl_roi,
             "hl_pnl_per_10000":hl_roi*10000 if np.isfinite(hl_roi) else np.nan,
             "binance_full_n":full["n"],"binance_full_hit5":full["hit5"],"binance_full_hit10":full["hit10"],
             "binance_full_risk":full["risk"],"binance_full_net_roi":full["net_roi"],"binance_full_wilson":full["wilson_lower"],
             "binance_latest40_n":latest["n"],"binance_latest40_hit5":latest["hit5"],
             "binance_latest40_risk":latest["risk"],"binance_latest40_net_roi":latest["net_roi"]}
        rows.append(row)
        if passed:
            deep.append({"candidate_id":cid,"status":status,"rule":cand["rule"],
                         "hyperliquid_actual_funding":{"n":hl_n,"target_rate":hl_target,"net_roi":hl_roi,
                             "pnl_per_10000":hl_roi*10000},
                         "binance_full_external":full,"binance_latest40":latest,
                         "next_stage":["forward shadow tracking","live L2 sizing at signal time","manual review before any C-number"]})
    z=pd.DataFrame(rows).sort_values(["status","hl_actual_funding_net_roi"],ascending=[True,False])
    z.to_csv(out/"deep_validation.csv",index=False)
    (out/"deep_validated_registry.json").write_text(json.dumps({
        "generated_at":pd.Timestamp.utcnow().isoformat(),
        "policy":"Frozen Hyperliquid rules; actual-funding HL holdout + full independent Binance external panel; no retuning",
        "candidates":deep},indent=2,default=str))
    lines=["NOVEL CRYPTO CANDIDATE — DEEP VALIDATION","",
           "Hyperliquid replay includes ACTUAL funding to realized exit, fees, slippage, and 1m same-bar ordering.",
           "Binance full panel is used only as independent external confirmation; no thresholds are fitted there.",
           "Gate: HL N>=20, target>=72%, actual-funding ROI>=1.5%; Binance full N>=15, +5>=78%, risk>=68%, ROI>=1%, Wilson>=.58.","",
           f"Deep-validated shadows: {len(deep)}","",z.to_string(index=False),"",
           "No live promotion."]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep",type=float,default=.10)
    ap.add_argument("--out",default="novel_deep_out")
    ap.add_argument("--input-dir",default="novel_deep_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":
    main()
