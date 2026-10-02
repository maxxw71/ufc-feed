#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, math, os, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

INFO="https://api.hyperliquid.xyz/info"

def post(s,payload,tries=7):
    last=None
    for k in range(tries):
        try:
            r=s.post(INFO,json=payload,timeout=40)
            if r.status_code==200:
                return r.json()
            last=RuntimeError(f"HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:
            last=e
        time.sleep(min(8,1+k))
    raise RuntimeError(str(last))

def rsi(c,p=14):
    d=c.diff()
    g=d.clip(lower=0)
    l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    al=l.ewm(alpha=1/p,adjust=False,min_periods=p).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).where(al.ne(0),100.0)

def fetch_universe(s):
    meta,ctx=post(s,{"type":"metaAndAssetCtxs"})
    out=[]
    for i,u in enumerate(meta.get("universe",[])):
        if u.get("isDelisted") or not u.get("name"):
            continue
        c=ctx[i] if i<len(ctx) and isinstance(ctx[i],dict) else {}
        out.append({
            "coin":str(u["name"]),
            "mark":float(c.get("markPx")) if c.get("markPx") not in (None,"") else None,
            "day_volume":float(c.get("dayNtlVlm")) if c.get("dayNtlVlm") not in (None,"") else None,
        })
    return out

def fetch_4h(s,coin,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":coin,"interval":"4h",
        "startTime":int(start.timestamp()*1000),
        "endTime":int(end.timestamp()*1000)
    }})
    rec=[]
    for r in rows or []:
        try:
            rec.append({
                "time":pd.to_datetime(int(r["t"]),unit="ms",utc=True),
                "o":float(r["o"]),"h":float(r["h"]),"l":float(r["l"]),
                "c":float(r["c"]),"v":float(r.get("v",0))
            })
        except Exception:
            pass
    if not rec:
        return pd.DataFrame()
    return pd.DataFrame(rec).drop_duplicates("time").sort_values("time").reset_index(drop=True)

def split_closed(raw,now):
    if raw.empty:
        return raw,raw
    closed=raw[(raw["time"]+pd.Timedelta(hours=4))<=pd.Timestamp(now)].copy().reset_index(drop=True)
    current=raw[(raw["time"]+pd.Timedelta(hours=4))>pd.Timestamp(now)].copy().reset_index(drop=True)
    return closed,current

def prep4(x):
    x=x.copy()
    x["rsi"]=rsi(x["c"])
    x["rsi_pct1"]=x["rsi"].pct_change()
    x["rsi_pct3"]=x["rsi"]/x["rsi"].shift(3)-1
    x["rsi_delta"]=x["rsi"].diff()
    x["rsi_accel"]=x["rsi_delta"].diff()
    x["rsi_sma5"]=x["rsi"].rolling(5,min_periods=3).mean()
    x["rsi_vs_sma5"]=x["rsi"]/x["rsi_sma5"]-1
    x["ema9"]=x["c"].ewm(span=9,adjust=False).mean()
    x["sma20"]=x["c"].rolling(20,min_periods=10).mean()
    x["sma50"]=x["c"].rolling(50,min_periods=25).mean()
    x["sma50_slope6"]=x["sma50"]/x["sma50"].shift(6)-1
    x["dist_ema9"]=x["c"]/x["ema9"]-1
    x["dist_sma20"]=x["c"]/x["sma20"]-1
    x["dist_sma50"]=x["c"]/x["sma50"]-1
    x["range"]=(x["h"]-x["l"]).replace(0,np.nan)
    x["close_location"]=(x["c"]-x["l"])/x["range"]
    x["lower_wick"]=(np.minimum(x["o"],x["c"])-x["l"])/x["range"]
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

def method1_events(x):
    if len(x)<120:
        return []
    d=daily_from_4h(x)
    arms=[]
    last=None
    for _,r in d[d["setup"]].iterrows():
        t=r["arm_time"]
        if last is None or t-last>=pd.Timedelta(days=10):
            arms.append(t)
            last=t

    found=[]
    for arm in arms:
        if arm < x["time"].max()-pd.Timedelta(days=7):
            continue
        w=x[(x["time"]>=arm)&(x["time"]<arm+pd.Timedelta(days=5))]
        if len(w)<3:
            continue
        peak=-math.inf
        pi=None
        trigger=None
        for idx,row in w.iterrows():
            hi=float(row["h"])
            if hi>=peak:
                peak=hi
                pi=idx
            if pi is not None and idx>pi and float(row["c"])/peak-1<=-.08:
                trigger=idx
                break
        if trigger is None:
            continue
        row=x.loc[trigger]
        if pd.isna(row["rsi_pct1"]) or pd.isna(row["rsi_accel"]):
            continue
        if float(row["rsi_pct1"])<=-0.18174 and float(row["rsi_accel"])<=-11.72020:
            found.append({
                "trigger_idx":int(trigger),
                "trigger_time":row["time"],
                "trigger_price":float(row["c"]),
                "rsi":float(row["rsi"]),
                "rsi_pct1":float(row["rsi_pct1"]),
                "rsi_accel":float(row["rsi_accel"]),
            })
    return found

def lower_low_idx(x,start,end):
    if end<start:
        return None
    s=x.loc[start:end,"l"]
    return int(s.idxmin()) if len(s) else None

def upper_high_idx(x,start,end):
    if end<start:
        return None
    s=x.loc[start:end,"h"]
    return int(s.idxmax()) if len(s) else None

def method2_events(x):
    if len(x)<100:
        return []
    events=[]
    last=None
    start=max(90,len(x)-80)
    for t in range(start,len(x)):
        if pd.isna(x.loc[t,"rsi"]) or float(x.loc[t,"rsi"])>30:
            continue
        lo_start=max(55,t-36)
        lo_end=t-6
        l1=lower_low_idx(x,lo_start,lo_end)
        if l1 is None or l1+2>=t:
            continue
        if any(pd.isna(x.loc[l1,c]) for c in ["sma20","sma50","sma50_slope6"]):
            continue
        if not (float(x.loc[l1,"c"])<float(x.loc[l1,"sma50"])
                and float(x.loc[l1,"sma20"])<float(x.loc[l1,"sma50"])
                and float(x.loc[l1,"sma50_slope6"])<0):
            continue
        p1=upper_high_idx(x,l1+1,t-2)
        if p1 is None:
            continue
        low1=float(x.loc[l1,"l"])
        pump_peak=float(x.loc[p1,"h"])
        pump=pump_peak/low1-1
        if pump<0.21337:
            continue
        ph_start=max(0,l1-36)
        ph_end=l1-2
        if ph_end<=ph_start:
            continue
        prior_high=float(x.loc[ph_start:ph_end,"h"].max())
        if prior_high<=0:
            continue
        lower_high_pct=pump_peak/prior_high-1
        if lower_high_pct>-0.19915:
            continue
        second_dump=float(x.loc[t,"c"])/pump_peak-1
        if second_dump>-.10:
            continue
        tt=x.loc[t,"time"]
        if last is not None and tt-last<pd.Timedelta(days=5):
            continue
        last=tt
        events.append({
            "trigger_idx":int(t),
            "trigger_time":tt,
            "trigger_price":float(x.loc[t,"c"]),
            "rsi":float(x.loc[t,"rsi"]),
            "pump_pct":pump,
            "lower_high_pct":lower_high_pct,
            "second_dump_pct":second_dump,
        })
    return events

def next_open_reference(raw,trigger_time,mark):
    nt=pd.Timestamp(trigger_time)+pd.Timedelta(hours=4)
    q=raw[raw["time"]==nt]
    if len(q):
        return float(q.iloc[0]["o"])
    return mark

def load_history(path):
    try:
        data=json.loads(Path(path).read_text())
        return data if isinstance(data,list) else []
    except Exception:
        return []

def atomic_json(path,obj):
    p=Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+".tmp")
    tmp.write_text(json.dumps(obj,indent=2,allow_nan=False,default=str))
    os.replace(tmp,p)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--methods",default="methods.json")
    ap.add_argument("--output",default="signals.json")
    ap.add_argument("--history",default="history.json")
    ap.add_argument("--days",type=int,default=125)
    ap.add_argument("--recent-hours",type=int,default=72)
    args=ap.parse_args()

    cfg=json.loads(Path(args.methods).read_text())
    methods={m["key"]:m for m in cfg["methods"]}
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    start=now-timedelta(days=args.days)
    s=requests.Session()
    s.headers["User-Agent"]="appwiza-live-crypto-method-scanner/1.0"

    signals=[]
    coverage=[]
    for pos,item in enumerate(fetch_universe(s),1):
        coin=item["coin"]
        try:
            raw=fetch_4h(s,coin,start,now+timedelta(hours=4))
            closed,current=split_closed(raw,now)
            x=prep4(closed)
            if len(x)<100:
                coverage.append({"coin":coin,"status":"short","bars":len(x)})
                continue
            candidates=[]
            if methods["blowoff_first_flush"]["enabled"]:
                candidates += [("blowoff_first_flush",z) for z in method1_events(x)]
            if methods["lower_high_second_dump"]["enabled"]:
                candidates += [("lower_high_second_dump",z) for z in method2_events(x)]
            for key,ev in candidates:
                age=pd.Timestamp(now)-pd.Timestamp(ev["trigger_time"])
                if age>pd.Timedelta(hours=args.recent_hours):
                    continue
                entry_ref=next_open_reference(raw,ev["trigger_time"],item.get("mark"))
                if entry_ref is None:
                    entry_ref=ev["trigger_price"]
                method=methods[key]
                signals.append({
                    "id":f"{method['id']}:{coin}:{pd.Timestamp(ev['trigger_time']).isoformat()}",
                    "coin":coin,
                    "method_id":method["id"],
                    "method_key":key,
                    "method_name":method["name"],
                    "trigger_time":pd.Timestamp(ev["trigger_time"]).isoformat(),
                    "trigger_price":ev["trigger_price"],
                    "entry_reference":entry_ref,
                    "target_5pct":entry_ref*1.05,
                    "risk_stop_reference":entry_ref*(1-float(method["validation"]["stop_reference_pct"])),
                    "status":"ACTIVE" if age<=pd.Timedelta(hours=8) else "RECENT",
                    "metrics":{k:v for k,v in ev.items() if k not in {"trigger_idx","trigger_time","trigger_price"}},
                })
            coverage.append({"coin":coin,"status":"ok","bars":len(x),"signals":len(candidates)})
        except Exception as ex:
            coverage.append({"coin":coin,"status":"error","error":str(ex)[:160]})
        time.sleep(.04)

    signals.sort(key=lambda x:x["trigger_time"],reverse=True)
    history=load_history(args.history)
    known={str(x.get("id")) for x in history if isinstance(x,dict)}
    for sig in reversed(signals):
        if sig["id"] not in known:
            history.append(sig)
            known.add(sig["id"])
    history=history[-500:]
    atomic_json(args.history,history)

    payload={
        "generated_at":now.isoformat(),
        "venue":cfg["venue"],
        "timeframe":cfg["timeframe"],
        "target_pct":cfg["target_pct"],
        "methods":cfg["methods"],
        "signals":signals[:100],
        "history_count":len(history),
        "coverage":{"ok":sum(1 for x in coverage if x.get("status")=="ok"),
                    "short":sum(1 for x in coverage if x.get("status")=="short"),
                    "errors":sum(1 for x in coverage if x.get("status")=="error")},
    }
    atomic_json(args.output,payload)
    print(json.dumps({"generated_at":payload["generated_at"],"signals":len(signals),"coverage":payload["coverage"]},indent=2))

if __name__=="__main__":
    main()
