#!/usr/bin/env python3
"""
Third-venue validation for N02 and N06 on OKX USDT perpetual swaps.

The Hyperliquid thresholds are frozen. Nothing is tuned on OKX.

N02:
- completed daily breakout regime
- first >=8% post-arm 4H close drawdown
- asset 4H return minus BTC 4H return in [-5.3821%, -3.0504%]
- 3-bar normalized OBV delta in [-0.112934, -0.035454]

N06:
- completed daily breakout regime
- 5d daily breakout return in [31.3961%, 57.9532%]
- first >=8% post-arm 4H close drawdown
- trigger close drawdown from post-arm peak in [-13.9439%, -10.4190%]

Outcome contract:
+5% target / -7.5% stop / 5d horizon, next 4H open entry.
Same-4H target+stop is conservatively not target-first.
Costs for external comparison: 0.05% taker fee/side + 0.10% slippage/side.
Funding is not included in this initial third-venue screen.

Research only. No threshold tuning and no live promotion.
"""
from __future__ import annotations
import argparse, math, time, json
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd
import requests

BASE="https://www.okx.com"
OUT=Path("crypto/research/results_okx_novel_validation")
TP=.05
STOP=.075
RT_COST=2*(.0005+.0010)

N02_REL_MIN=-0.05382078204024199
N02_REL_MAX=-0.03050418191478533
N02_OBV_MIN=-0.11293352517433576
N02_OBV_MAX=-0.035453733606862416

N06_RET5_MIN=0.31396113303210554
N06_RET5_MAX=0.5795319768388887
N06_DD_MIN=-0.1394393733853019
N06_DD_MAX=-0.10419042571418308

def getj(s,path,params=None,tries=8):
    last=None
    for k in range(tries):
        try:
            r=s.get(BASE+path,params=params,timeout=35)
            if r.ok:
                j=r.json()
                if str(j.get("code","0"))=="0":
                    return j.get("data",[])
                last=RuntimeError("OKX code %s %s"%(j.get("code"),j.get("msg")))
            else:
                last=RuntimeError("HTTP %s %s"%(r.status_code,r.text[:180]))
        except Exception as e:last=e
        time.sleep(min(12,1.5*(k+1)))
    raise RuntimeError(str(last))

def universe(s):
    rows=getj(s,"/api/v5/public/instruments",{"instType":"SWAP"})
    out=[]
    for x in rows:
        if x.get("state")!="live":continue
        if x.get("settleCcy")!="USDT":continue
        if x.get("ctType") not in ("linear",""):continue
        iid=str(x.get("instId") or "")
        if not iid.endswith("-USDT-SWAP"):continue
        base=str(x.get("ctValCcy") or iid.split("-")[0])
        if base in {"USDT","USDC","DAI","FDUSD","TUSD"}:continue
        out.append(iid)
    return sorted(set(out))

def fetch_4h(s,inst,start,end):
    start_ms=int(pd.Timestamp(start).timestamp()*1000)
    end_ms=int(pd.Timestamp(end).timestamp()*1000)
    cursor=None;allrows=[];seen=set()
    for _ in range(30):
        params={"instId":inst,"bar":"4H","limit":"300"}
        if cursor is not None:params["after"]=str(cursor)
        rows=getj(s,"/api/v5/market/history-candles",params)
        if not rows:break
        oldest=None
        for z in rows:
            try:
                ts=int(z[0])
                if ts in seen:continue
                seen.add(ts)
                if ts<start_ms or ts>end_ms:continue
                allrows.append({
                    "time":pd.to_datetime(ts,unit="ms",utc=True),
                    "o":float(z[1]),"h":float(z[2]),"l":float(z[3]),"c":float(z[4]),
                    "v":float(z[5]) if len(z)>5 else np.nan,
                    "confirm":str(z[8]) if len(z)>8 else "1",
                })
                oldest=ts if oldest is None else min(oldest,ts)
            except Exception:pass
        raw_ts=[]
        for z in rows:
            try:raw_ts.append(int(z[0]))
            except Exception:pass
        if not raw_ts:break
        batch_oldest=min(raw_ts)
        if batch_oldest<=start_ms:break
        if cursor is not None and batch_oldest>=cursor:break
        cursor=batch_oldest
        time.sleep(.06)
    if not allrows:return pd.DataFrame()
    x=pd.DataFrame(allrows).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return x

def rsi(c,p=14):
    d=c.diff();g=d.clip(lower=0);l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def prep(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["price_pct1"]=x["c"].pct_change()
    direction=np.sign(x["c"].diff()).fillna(0)
    x["obv"]=(direction*x["v"].fillna(0)).cumsum()
    x["obv_delta3_norm"]=x["obv"].diff(3)/x["v"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    return x

def daily_from_4h(x):
    z=x.set_index("time")
    d=pd.DataFrame({
        "o":z["o"].resample("1D").first(),
        "h":z["h"].resample("1D").max(),
        "l":z["l"].resample("1D").min(),
        "c":z["c"].resample("1D").last(),
        "v":z["v"].resample("1D").sum(),
    }).dropna().reset_index()
    d["ret1"]=d["c"].pct_change()
    d["ret5"]=d["c"]/d["c"].shift(5)-1
    d["prior60"]=d["h"].shift(1).rolling(60,min_periods=60).max()
    d["rv20"]=d["ret1"].rolling(20,min_periods=20).std()
    d["setup"]=(d["ret5"]>=.15)&(d["h"]>=d["prior60"])&(d["rv20"]>=.025)
    d["arm_time"]=d["time"]+pd.Timedelta(days=1)
    return d

def first_flush(x,arm):
    w=x[(x["time"]>=arm)&(x["time"]<arm+pd.Timedelta(days=5))]
    if len(w)<4:return None
    peak=-math.inf;pi=None
    for idx,r in w.iterrows():
        hi=float(r["h"])
        if hi>=peak:peak=hi;pi=idx
        if pi is None or idx<=pi or peak<=0:continue
        if float(r["c"])/peak-1<=-.08:
            return idx,peak
    return None

def outcome(x,idx):
    loc=x.index.get_loc(idx)
    if loc+31>=len(x):return None
    entry=float(x.iloc[loc+1]["o"])
    f=x.iloc[loc+1:loc+31]
    if entry<=0 or len(f)<30:return None
    mfe=float(f["h"].max()/entry-1);mae=float(f["l"].min()/entry-1)
    tk=sk=None
    for k,(_,r) in enumerate(f.iterrows()):
        if tk is None and float(r["h"])>=entry*(1+TP):tk=k
        if sk is None and float(r["l"])<=entry*(1-STOP):sk=k
    win=tk is not None and (sk is None or tk<sk)
    stopped=(not win) and mae<=-STOP
    timeout=float(f.iloc[-1]["c"]/entry-1)
    gross=TP if win else (-STOP if stopped else float(np.clip(timeout,-STOP,TP)))
    return {
        "entry":entry,"entry_time":x.iloc[loc+1]["time"],
        "mfe_5d":mfe,"mae_5d":mae,"close_ret_5d":timeout,
        "hit5_5d":mfe>=.05,"hit10_5d":mfe>=.10,
        "t50_before_s75_5d":win,"modeled_net_return":gross-RT_COST
    }

def btc_context(btc,t):
    q=btc[btc["time"]<=pd.Timestamp(t)].tail(1)
    if q.empty:return None
    return float(q.iloc[0]["price_pct1"]) if pd.notna(q.iloc[0]["price_pct1"]) else None

def scan_coin(x,btc,inst):
    d=daily_from_4h(x);arms=[];last=None
    for _,r in d[d["setup"]].iterrows():
        t=r["arm_time"]
        if last is None or t-last>=pd.Timedelta(days=10):
            arms.append((t,r));last=t
    rows=[]
    for arm,dr in arms:
        ff=first_flush(x,arm)
        if ff is None:continue
        idx,peak=ff
        r=x.loc[idx]
        if pd.isna(r["price_pct1"]) or pd.isna(r["obv_delta3_norm"]):continue
        br=btc_context(btc,r["time"])
        if br is None:continue
        rel=float(r["price_pct1"])-br
        dd=float(r["c"])/peak-1
        o=outcome(x,idx)
        if o is None:continue
        base={
            "inst_id":inst,"coin":inst.split("-")[0],
            "arm_time":arm,"trigger_time":r["time"],
            "daily_ret5":float(dr["ret5"]),
            "price_dd":dd,"asset_ret4":float(r["price_pct1"]),
            "btc_ret4":br,"relative_ret4":rel,
            "obv_delta3_norm":float(r["obv_delta3_norm"]),**o
        }
        n02=(N02_REL_MIN<=rel<=N02_REL_MAX and N02_OBV_MIN<=base["obv_delta3_norm"]<=N02_OBV_MAX)
        n06=(N06_RET5_MIN<=base["daily_ret5"]<=N06_RET5_MAX and N06_DD_MIN<=dd<=N06_DD_MAX)
        if n02:rows.append({"method":"N02",**base})
        if n06:rows.append({"method":"N06",**base})
    return rows

def scan(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.headers["User-Agent"]="appwiza-okx-third-venue/1.0"
    end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(days=args.days)
    allinst=universe(s)
    selected=allinst[args.shard_index::args.shard_count]
    btc=prep(fetch_4h(s,"BTC-USDT-SWAP",start,end))
    rows=[];cov=[]
    for i,inst in enumerate(selected,1):
        try:
            x=prep(fetch_4h(s,inst,start,end))
            if len(x)<180:
                cov.append((inst,len(x),0,"short"));continue
            rr=scan_coin(x,btc,inst);rows.extend(rr)
            cov.append((inst,len(x),len(rr),"ok"))
            print(i,len(selected),inst,len(rr))
        except Exception as e:
            cov.append((inst,0,0,"error:"+str(e)[:120]));print("ERR",inst,e)
        time.sleep(.06)
    pd.DataFrame(rows).to_csv(out/f"events_{args.shard_index:02d}.csv",index=False)
    pd.DataFrame(cov,columns=["inst_id","bars","events","status"]).to_csv(out/f"coverage_{args.shard_index:02d}.csv",index=False)

def summary(g):
    if g.empty:return {"n":0}
    return {
        "n":len(g),"coins":int(g["coin"].nunique()),
        "hit5":float(g["hit5_5d"].map(bool).mean()),
        "hit10":float(g["hit10_5d"].map(bool).mean()),
        "target_before_stop":float(g["t50_before_s75_5d"].map(bool).mean()),
        "modeled_net_roi":float(pd.to_numeric(g["modeled_net_return"],errors="coerce").mean()),
        "median_mfe":float(pd.to_numeric(g["mfe_5d"],errors="coerce").median()),
        "median_mae":float(pd.to_numeric(g["mae_5d"],errors="coerce").median()),
    }

def aggregate(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=[];cs=[]
    for p in sorted(Path(args.input_dir).rglob("events_*.csv")):
        try:
            z=pd.read_csv(p)
            if len(z):fs.append(z)
        except Exception:pass
    for p in sorted(Path(args.input_dir).rglob("coverage_*.csv")):
        try:cs.append(pd.read_csv(p))
        except Exception:pass
    e=pd.concat(fs,ignore_index=True) if fs else pd.DataFrame()
    c=pd.concat(cs,ignore_index=True) if cs else pd.DataFrame()
    e.to_csv(out/"events.csv",index=False);c.to_csv(out/"coverage.csv",index=False)
    if len(e):
        for k in ["arm_time","trigger_time","entry_time"]:e[k]=pd.to_datetime(e[k],utc=True,errors="coerce")
        e=e.sort_values("entry_time").reset_index(drop=True)
    result={}
    rows=[]
    for m in ("N02","N06"):
        g=e[e["method"]==m].copy() if len(e) else pd.DataFrame()
        allx=summary(g)
        if len(g):
            cut=max(1,int(len(g)*.60));latest=summary(g.iloc[cut:])
        else:latest={"n":0}
        result[m]={"all":allx,"latest40":latest}
        rows.append({"method":m,**{"all_"+k:v for k,v in allx.items()},**{"latest40_"+k:v for k,v in latest.items()}})
    pd.DataFrame(rows).to_csv(out/"summary.csv",index=False)
    (out/"REPORT.json").write_text(json.dumps({
        "venue":"OKX USDT perpetual swaps",
        "policy":"Frozen Hyperliquid N02/N06 thresholds; no OKX tuning",
        "cost_contract":"+5/-7.5/5d; 0.05% taker + 0.10% slippage per side; funding excluded",
        "coverage":{"instruments":int(len(c)),"ok":int((c["status"]=="ok").sum()) if len(c) else 0},
        "methods":result
    },indent=2,default=str))
    lines=["OKX THIRD-VENUE VALIDATION — N02 / N06","",
           "Frozen Hyperliquid rules; no OKX threshold tuning.",
           "+5% target / -7.5% stop / 5d / next 4H open; 0.05% taker + 0.10% slippage per side; funding not yet included.","",
           f"Instruments attempted: {len(c)} | successfully scanned: {int((c['status']=='ok').sum()) if len(c) else 0}","",
           pd.DataFrame(rows).to_string(index=False) if rows else "no results"]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print((out/"REPORT.txt").read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--out",default="okx_out")
    ap.add_argument("--input-dir",default="okx_shards")
    args=ap.parse_args()
    scan(args) if args.mode=="scan" else aggregate(args)

if __name__=="__main__":
    main()
