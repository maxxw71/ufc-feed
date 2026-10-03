#!/usr/bin/env python3
from __future__ import annotations

import json, math, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests

HL="https://api.hyperliquid.xyz/info"
BINANCE="https://data-api.binance.vision/api/v3/ticker/price"
ROOT=Path("/home/anestishkurti92/crypto-trading/context")
STATE=ROOT/"latest_state.json"
PUBLIC=Path("/srv/appwiza-sports/public/trading/crypto/context.json")

def f(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except Exception:return None

def post(session,payload,tries=5):
    err=None
    for i in range(tries):
        try:
            r=session.post(HL,json=payload,timeout=25)
            if r.ok:return r.json()
            err=RuntimeError(f"HTTP {r.status_code} {r.text[:120]}")
        except Exception as e:err=e
        time.sleep(1+i)
    raise err or RuntimeError("Hyperliquid request failed")

def binance_prices():
    try:
        r=requests.get(BINANCE,timeout=20)
        r.raise_for_status()
        out={}
        for x in r.json():
            s=str(x.get("symbol") or "")
            if s.endswith("USDT"):
                out[s[:-4]]=f(x.get("price"))
        return out
    except Exception:
        return {}

def l2_metrics(session,coin):
    try:
        d=post(session,{"type":"l2Book","coin":coin})
        levels=d.get("levels") or []
        bids=(levels[0] if len(levels)>0 else [])[:5]
        asks=(levels[1] if len(levels)>1 else [])[:5]
        if not bids or not asks:return {}
        bp=f(bids[0].get("px"));ap=f(asks[0].get("px"))
        mid=(bp+ap)/2 if bp and ap else None
        spread_bps=((ap-bp)/mid*10000) if mid else None
        bid_n=sum((f(x.get("px")) or 0)*(f(x.get("sz")) or 0) for x in bids)
        ask_n=sum((f(x.get("px")) or 0)*(f(x.get("sz")) or 0) for x in asks)
        den=bid_n+ask_n
        return {
            "spread_bps":spread_bps,
            "top5_bid_notional":bid_n,
            "top5_ask_notional":ask_n,
            "book_imbalance_top5":(bid_n-ask_n)/den if den else None,
        }
    except Exception:
        return {}

def load_state():
    try:return json.loads(STATE.read_text())
    except Exception:return {"coins":{}}

def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,indent=2,allow_nan=False))
    os.replace(tmp,path)

def main():
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    s=requests.Session();s.headers["User-Agent"]="appwiza-crypto-context/1.0"
    meta,ctx=post(s,{"type":"metaAndAssetCtxs"})
    prev=load_state().get("coins",{})
    bp=binance_prices()

    rows=[]
    universe=[]
    for i,u in enumerate(meta.get("universe") or []):
        if u.get("isDelisted") or not u.get("name"):continue
        c=ctx[i] if i<len(ctx) and isinstance(ctx[i],dict) else {}
        coin=str(u["name"])
        mark=f(c.get("markPx"));oracle=f(c.get("oraclePx"))
        oi=f(c.get("openInterest"));fund=f(c.get("funding"))
        dayv=f(c.get("dayNtlVlm"));prevday=f(c.get("prevDayPx"))
        ret24=(mark/prevday-1) if mark and prevday else None
        basis_bps=((mark/oracle)-1)*10000 if mark and oracle else None
        spot=bp.get(coin)
        xspread=((mark/spot)-1)*10000 if mark and spot else None
        p=prev.get(coin,{})
        poi=f(p.get("open_interest"));pf=f(p.get("funding"));pm=f(p.get("mark"))
        oi_chg=(oi/poi-1) if oi is not None and poi not in (None,0) else None
        fund_chg=(fund-pf) if fund is not None and pf is not None else None
        price_chg=(mark/pm-1) if mark and pm else None
        row={
            "coin":coin,"timestamp":now.isoformat(),"mark":mark,"oracle":oracle,
            "prev_day_px":prevday,"ret24":ret24,"day_notional_volume":dayv,
            "open_interest":oi,"funding":fund,"basis_bps":basis_bps,
            "oi_change_since_snapshot":oi_chg,"funding_change_since_snapshot":fund_chg,
            "price_change_since_snapshot":price_chg,
            "binance_spot_price":spot,"hl_vs_binance_bps":xspread,
            "deleveraging_proxy":((-oi_chg)*max(-(price_chg or 0),0)) if oi_chg is not None and price_chg is not None else None,
        }
        rows.append(row);universe.append((dayv or 0,coin,row))

    # L2 book is more expensive; collect it for the 60 most liquid primary perps.
    for _,coin,row in sorted(universe,reverse=True)[:60]:
        row.update(l2_metrics(s,coin))
        time.sleep(.04)

    valid=[r["ret24"] for r in rows if r["ret24"] is not None]
    summary={
        "timestamp":now.isoformat(),
        "coins":len(rows),
        "breadth_positive_24h":sum(x>0 for x in valid)/len(valid) if valid else None,
        "breadth_up5_24h":sum(x>=.05 for x in valid)/len(valid) if valid else None,
        "breadth_down5_24h":sum(x<=-.05 for x in valid)/len(valid) if valid else None,
        "btc_ret24":next((r["ret24"] for r in rows if r["coin"]=="BTC"),None),
        "eth_ret24":next((r["ret24"] for r in rows if r["coin"]=="ETH"),None),
    }

    ROOT.mkdir(parents=True,exist_ok=True)
    daily=ROOT/f"context_{now.date().isoformat()}.jsonl"
    with daily.open("a") as fh:
        fh.write(json.dumps({"summary":summary,"rows":rows},allow_nan=False)+"\n")
    atomic(STATE,{"timestamp":now.isoformat(),"coins":{r["coin"]:{
        "mark":r["mark"],"open_interest":r["open_interest"],"funding":r["funding"]
    } for r in rows}})
    atomic(PUBLIC,{"summary":summary,"rows":rows})
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
