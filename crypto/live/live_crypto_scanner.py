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

def funding_at_or_before(s,coin,t,lookback_hours=12):
    """Last published Hyperliquid funding rate at or before timestamp t."""
    tt=pd.Timestamp(t)
    rows=post(s,{"type":"fundingHistory","coin":coin,
                 "startTime":int((tt-pd.Timedelta(hours=lookback_hours)).timestamp()*1000),
                 "endTime":int(tt.timestamp()*1000)})
    valid=[x for x in (rows or []) if int(x.get("time",0))<=int(tt.timestamp()*1000)]
    if not valid:
        return None
    x=max(valid,key=lambda z:int(z.get("time",0)))
    try:
        return float(x.get("fundingRate"))
    except Exception:
        return None


def fetch_candles(s,coin,interval,start,end):
    rows=post(s,{"type":"candleSnapshot","req":{
        "coin":coin,"interval":interval,
        "startTime":int(pd.Timestamp(start).timestamp()*1000),
        "endTime":int(pd.Timestamp(end).timestamp()*1000)
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

def fetch_4h(s,coin,start,end):
    return fetch_candles(s,coin,"4h",start,end)

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
    x["volume_med20"]=x["v"].rolling(20,min_periods=10).median()
    x["volume_ratio20"]=x["v"]/x["volume_med20"].replace(0,np.nan)
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

        # C1 uses the validated no-lookahead momentum-reset contract.
        # Peak RSI/top-shape remain research diagnostics, not live hard vetoes.
        if (float(row["rsi_pct1"])<=-0.18174
            and float(row["rsi_accel"])<=-11.72020):
            found.append({
                "trigger_idx":int(trigger),
                "trigger_time":row["time"],
                "trigger_price":float(row["c"]),
                "rsi":float(row["rsi"]),
                "rsi_pct1":float(row["rsi_pct1"]),
                "rsi_accel":float(row["rsi_accel"]),
            })
    return found

def method3_events(x):
    """C3 shadow: capitulation close + abnormal volume after the first 8% flush."""
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
        if pd.isna(row["close_location"]) or pd.isna(row["volume_ratio20"]):
            continue
        if float(row["close_location"])<=0.1527 and float(row["volume_ratio20"])>=1.8296:
            found.append({
                "trigger_idx":int(trigger),
                "trigger_time":row["time"],
                "trigger_price":float(row["c"]),
                "close_location":float(row["close_location"]),
                "volume_ratio20":float(row["volume_ratio20"]),
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

def resolve_same_4h_exit(s,coin,bar_time,target,stop):
    """Resolve target-vs-stop order inside one 4H candle using 1-minute candles."""
    if s is None or not coin:
        return None
    start=pd.Timestamp(bar_time)
    end=start+pd.Timedelta(hours=4)
    fine=fetch_candles(s,coin,"1m",start,end)
    if fine.empty:
        return None
    for _,bar in fine.iterrows():
        hit_target=float(bar["h"])>=target
        hit_stop=float(bar["l"])<=stop
        if not hit_target and not hit_stop:
            continue
        # If only one boundary is touched in this minute, order is known.
        if hit_target and not hit_stop:
            return ("target",bar["time"],"1m")
        if hit_stop and not hit_target:
            return ("stop",bar["time"],"1m")

        # Both boundaries inside the same 1m candle is extremely rare because
        # they are 12.5 percentage points apart. Use the minute open only when
        # it already lies beyond one boundary; otherwise order is unknowable
        # from OHLC alone and we leave it unresolved rather than inventing it.
        o=float(bar["o"])
        if o>=target:
            return ("target",bar["time"],"1m_open")
        if o<=stop:
            return ("stop",bar["time"],"1m_open")
        return None
    return None


def settle_signal(raw, signal, now, session=None, coin=None):
    """Update a signal from market data after its reference entry."""
    try:
        entry_time=pd.Timestamp(signal["entry_time"])
    except Exception:
        # Backward compatibility with early history rows that only stored trigger.
        entry_time=pd.Timestamp(signal["trigger_time"])+pd.Timedelta(hours=4)
    entry=float(signal["entry_reference"])
    target=float(signal["target_5pct"])
    stop=float(signal["risk_stop_reference"])
    w=raw[(raw["time"]>=entry_time)&(raw["time"]<=pd.Timestamp(now))].copy()
    if w.empty:
        signal["status"]="ACTIVE"
        return signal

    target_bar=None
    stop_bar=None
    for _,bar in w.iterrows():
        if target_bar is None and float(bar["h"])>=target:
            target_bar=bar["time"]
        if stop_bar is None and float(bar["l"])<=stop:
            stop_bar=bar["time"]

    # Position semantics: whichever exit is reached first ends the trade.
    # A same-4H target+stop candle is resolved using 1-minute candles so we do
    # not automatically call it stopped merely because both are in one 4H wick.
    signal["stop_touched"]=bool(stop_bar is not None)
    if stop_bar is not None:
        signal["stop_touched_at"]=pd.Timestamp(stop_bar).isoformat()

    if target_bar is not None and stop_bar is not None and pd.Timestamp(target_bar)==pd.Timestamp(stop_bar):
        resolved=resolve_same_4h_exit(session,coin or signal.get("coin"),target_bar,target,stop)
        if resolved is not None:
            side,ts,resolution=resolved
            signal["same_4h_resolution"]=resolution
            if side=="target":
                target_bar=ts
                stop_bar=None
            else:
                stop_bar=ts
                target_bar=None
        else:
            # Do not invent the ordering. Keep tracking state unresolved until
            # a finer-grained source is available rather than forcing STOPPED.
            signal["status"]="RECENT"
            signal["same_4h_resolution"]="unresolved"
            return signal

    if target_bar is not None and (stop_bar is None or pd.Timestamp(target_bar) < pd.Timestamp(stop_bar)):
        signal["status"]="SUCCESS"
        signal["settled_at"]=pd.Timestamp(target_bar).isoformat()
        signal["realized_target_pct"]=0.05
        signal["target_after_stop"]=False
    elif stop_bar is not None:
        signal["status"]="STOPPED"
        signal["settled_at"]=pd.Timestamp(stop_bar).isoformat()
        signal["target_after_stop"]=bool(target_bar is not None and pd.Timestamp(target_bar) >= pd.Timestamp(stop_bar))
    else:
        age=pd.Timestamp(now)-pd.Timestamp(signal["trigger_time"])
        signal["status"]="ACTIVE" if age<=pd.Timedelta(hours=8) else "RECENT"
    return signal


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
    shadow_signals=[]
    coverage=[]
    raw_cache={}
    for pos,item in enumerate(fetch_universe(s),1):
        coin=item["coin"]
        try:
            raw=fetch_4h(s,coin,start,now+timedelta(hours=4))
            raw_cache[coin]=raw
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
            shadow_candidates=[]
            c3_events=[]
            if "volume_capitulation_flush" in methods and methods["volume_capitulation_flush"].get("enabled"):
                c3_events=method3_events(x)
                if methods["volume_capitulation_flush"].get("status")=="LIVE":
                    candidates += [("volume_capitulation_flush",z) for z in c3_events]
                else:
                    shadow_candidates += [("volume_capitulation_flush",z) for z in c3_events]

            # Optional C3+Funding family. The funding threshold is frozen in
            # methods.json and checked against the last published funding rate
            # at/before the C3 trigger. This block is dormant unless registered.
            if "volume_capitulation_funding" in methods and methods["volume_capitulation_funding"].get("enabled"):
                fm=methods["volume_capitulation_funding"]
                fmin=float(fm.get("params",{}).get("funding_rate_min",0.000013))
                for z in c3_events:
                    try:
                        fr=funding_at_or_before(s,coin,z["trigger_time"])
                    except Exception:
                        fr=None
                    if fr is None or fr < fmin:
                        continue
                    zz=dict(z);zz["funding_rate_at_trigger"]=fr
                    if fm.get("status")=="LIVE":
                        candidates.append(("volume_capitulation_funding",zz))
                    else:
                        shadow_candidates.append(("volume_capitulation_funding",zz))
            for key,ev in candidates:
                age=pd.Timestamp(now)-pd.Timestamp(ev["trigger_time"])
                if age>pd.Timedelta(hours=args.recent_hours):
                    continue
                entry_ref=next_open_reference(raw,ev["trigger_time"],item.get("mark"))
                if entry_ref is None:
                    entry_ref=ev["trigger_price"]
                method=methods[key]
                sig={
                    "id":f"{method['id']}:{coin}:{pd.Timestamp(ev['trigger_time']).isoformat()}",
                    "coin":coin,
                    "method_id":method["id"],
                    "method_key":key,
                    "method_name":method["name"],
                    "trigger_time":pd.Timestamp(ev["trigger_time"]).isoformat(),
                    "entry_time":(pd.Timestamp(ev["trigger_time"])+pd.Timedelta(hours=4)).isoformat(),
                    "trigger_price":ev["trigger_price"],
                    "entry_reference":entry_ref,
                    "target_5pct":entry_ref*1.05,
                    "risk_stop_reference":entry_ref*(1-float(method["validation"]["stop_reference_pct"])),
                    "status":"ACTIVE" if age<=pd.Timedelta(hours=8) else "RECENT",
                    "metrics":{k:v for k,v in ev.items() if k not in {"trigger_idx","trigger_time","trigger_price"}},
                }
                signals.append(settle_signal(raw,sig,now,s,coin))
            for key,ev in shadow_candidates:
                age=pd.Timestamp(now)-pd.Timestamp(ev["trigger_time"])
                if age>pd.Timedelta(hours=args.recent_hours):
                    continue
                entry_ref=next_open_reference(raw,ev["trigger_time"],item.get("mark"))
                if entry_ref is None:
                    entry_ref=ev["trigger_price"]
                method=methods[key]
                ssig={
                    "id":f"{method['id']}:{coin}:{pd.Timestamp(ev['trigger_time']).isoformat()}",
                    "coin":coin,
                    "method_id":method["id"],
                    "method_key":key,
                    "method_name":method["name"],
                    "trigger_time":pd.Timestamp(ev["trigger_time"]).isoformat(),
                    "entry_time":(pd.Timestamp(ev["trigger_time"])+pd.Timedelta(hours=4)).isoformat(),
                    "trigger_price":ev["trigger_price"],
                    "entry_reference":entry_ref,
                    "target_5pct":entry_ref*1.05,
                    "risk_stop_reference":entry_ref*(1-float(method["validation"]["stop_reference_pct"])),
                    "status":"SHADOW_ACTIVE" if age<=pd.Timedelta(hours=8) else "SHADOW_RECENT",
                    "metrics":{k:v for k,v in ev.items() if k not in {"trigger_idx","trigger_time","trigger_price"}},
                }
                # Reuse settlement logic, then preserve shadow labeling.
                ssig=settle_signal(raw,ssig,now,s,coin)
                if ssig["status"]=="ACTIVE": ssig["status"]="SHADOW_ACTIVE"
                elif ssig["status"]=="RECENT": ssig["status"]="SHADOW_RECENT"
                elif ssig["status"]=="SUCCESS": ssig["status"]="SHADOW_SUCCESS"
                elif ssig["status"]=="STOPPED": ssig["status"]="SHADOW_STOPPED"
                shadow_signals.append(ssig)
            coverage.append({"coin":coin,"status":"ok","bars":len(x),"signals":len(candidates),"shadow_signals":len(shadow_candidates)})
        except Exception as ex:
            coverage.append({"coin":coin,"status":"error","error":str(ex)[:160]})
        time.sleep(.04)

    signals.sort(key=lambda x:x["trigger_time"],reverse=True)
    history=load_history(args.history)
    by_id={str(x.get("id")):x for x in history if isinstance(x,dict) and x.get("id")}
    for sig in reversed(signals):
        by_id[sig["id"]]=sig

    # Re-settle previously recorded signals on every scan so the website changes
    # automatically from ACTIVE/RECENT to SUCCESS/STOPPED when market data proves it.
    history=list(by_id.values())
    for hsig in history:
        coin=str(hsig.get("coin") or "")
        raw=raw_cache.get(coin)
        if raw is not None and len(raw):
            try:
                settle_signal(raw,hsig,now,s,coin)
            except Exception:
                pass
    history=sorted(history,key=lambda x:str(x.get("trigger_time") or ""))[-500:]
    atomic_json(args.history,history)

    # Keep recent live signals plus recently settled trades visible online.
    visible_by_id={x["id"]:x for x in signals}
    for hsig in history:
        if hsig.get("status") in {"SUCCESS","STOPPED"}:
            try:
                settled_age=pd.Timestamp(now)-pd.Timestamp(hsig.get("settled_at"))
            except Exception:
                settled_age=pd.Timedelta(days=999)
            if settled_age<=pd.Timedelta(days=7):
                visible_by_id[hsig["id"]]=hsig
    signals=sorted(visible_by_id.values(),key=lambda x:str(x.get("trigger_time") or ""),reverse=True)

    shadow_path=Path(args.history).with_name("shadow_history.json")
    shadow_history=load_history(shadow_path)
    shadow_by_id={str(x.get("id")):x for x in shadow_history if isinstance(x,dict) and x.get("id")}
    for sig in shadow_signals:
        shadow_by_id[sig["id"]]=sig
    shadow_history=sorted(shadow_by_id.values(),key=lambda x:str(x.get("trigger_time") or ""))[-500:]
    atomic_json(shadow_path,shadow_history)
    shadow_signals=sorted(shadow_signals,key=lambda x:str(x.get("trigger_time") or ""),reverse=True)

    payload={
        "generated_at":now.isoformat(),
        "venue":cfg["venue"],
        "timeframe":cfg["timeframe"],
        "target_pct":cfg["target_pct"],
        "methods":cfg["methods"],
        "signals":signals[:100],
        "shadow_signals":shadow_signals[:100],
        "history_count":len(history),
        "shadow_history_count":len(shadow_history),
        "coverage":{"ok":sum(1 for x in coverage if x.get("status")=="ok"),
                    "short":sum(1 for x in coverage if x.get("status")=="short"),
                    "errors":sum(1 for x in coverage if x.get("status")=="error")},
    }
    atomic_json(args.output,payload)
    print(json.dumps({"generated_at":payload["generated_at"],"signals":len(signals),"coverage":payload["coverage"]},indent=2))

if __name__=="__main__":
    main()
