#!/usr/bin/env python3
"""
Research-only daily + 4H blow-off rebound study.

Daily chart: detects the violent breakout / blow-off setup.
4H chart: times the first hard flush after the peak.
No live orders and no production deployment.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://data-api.binance.vision"
STABLE = {"USDC","FDUSD","TUSD","USDP","DAI","BUSD","USD1","EUR","TRY","BRL","GBP","AUD","JPY"}


def get_json(session, path, params=None):
    last = None
    for attempt in range(6):
        try:
            r = session.get(API + path, params=params, timeout=25)
            if r.ok:
                return r.json()
            last = RuntimeError("HTTP %s %s" % (r.status_code, r.text[:180]))
            if r.status_code in (418, 429):
                time.sleep(min(8, 1.5 ** attempt))
        except Exception as exc:
            last = exc
            time.sleep(min(8, 1.5 ** attempt))
    raise RuntimeError(str(last))


def klines_to_df(rows):
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_base","taker_quote","ignore"]
    if not rows:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(rows, columns=cols)
    for c in ["open","high","low","close","volume","quote_volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["open_time"] = pd.to_datetime(d["open_time"], unit="ms", utc=True)
    d["close_time"] = pd.to_datetime(d["close_time"], unit="ms", utc=True)
    return d.dropna(subset=["open","high","low","close"]).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)


def fetch_klines(session, symbol, interval, start, end):
    step = {"1d":86400000, "4h":14400000}[interval]
    cur = int(start.timestamp() * 1000)
    stop = int(end.timestamp() * 1000)
    rows = []
    while cur < stop:
        batch = get_json(session, "/api/v3/klines", {
            "symbol": symbol, "interval": interval, "startTime": cur,
            "endTime": stop, "limit": 1000
        })
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 1000:
            break
        time.sleep(0.03)
    return klines_to_df(rows)


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(avg_loss.ne(0), 100.0)


def universe(session, n):
    info = get_json(session, "/api/v3/exchangeInfo")
    tick = get_json(session, "/api/v3/ticker/24hr")
    qv = {x["symbol"]: float(x.get("quoteVolume") or 0) for x in tick if isinstance(x, dict) and "symbol" in x}
    syms = []
    for x in info.get("symbols", []):
        if x.get("status") != "TRADING" or x.get("quoteAsset") != "USDT":
            continue
        base = x.get("baseAsset", "")
        if base in STABLE or base.startswith("USD") or base.startswith("1000"):
            continue
        if any(base.endswith(z) for z in ("UP","DOWN","BULL","BEAR")):
            continue
        syms.append(x["symbol"])
    syms.sort(key=lambda s: qv.get(s, 0), reverse=True)
    return syms[:n]


def daily_setups(d, ret5_min, rv_min):
    x = d.copy()
    x["ret1"] = x["close"].pct_change()
    x["ret5"] = x["close"] / x["close"].shift(5) - 1
    x["prior60_high"] = x["high"].shift(1).rolling(60, min_periods=60).max()
    x["rv20"] = x["ret1"].rolling(20, min_periods=20).std()
    x["rsi14"] = rsi(x["close"])
    x["setup"] = (x["ret5"] >= ret5_min) & (x["high"] >= x["prior60_high"]) & (x["rv20"] >= rv_min)
    events = []
    last = None
    for _, row in x.loc[x["setup"]].iterrows():
        t = row["open_time"]
        if last is None or t - last >= pd.Timedelta(days=10):
            events.append(t)
            last = t
    return x, events


def prep4h(h):
    x = h.copy()
    x["rsi14"] = rsi(x["close"])
    x["prev_low"] = x["low"].shift(1)
    return x


def first_flush(h, arm, dd_min, rsi_max, breakdown, window_days=5):
    w = h[(h["open_time"] >= arm) & (h["open_time"] < arm + pd.Timedelta(days=window_days))]
    if len(w) < 4:
        return None
    peak = -math.inf
    peak_rsi = -math.inf
    min_rsi = math.inf
    peak_idx = None
    for idx, row in w.iterrows():
        hi = float(row["high"])
        rr = float(row["rsi14"]) if pd.notna(row["rsi14"]) else np.nan
        if not np.isfinite(rr):
            continue
        if hi >= peak:
            peak = hi
            peak_idx = idx
        peak_rsi = max(peak_rsi, rr)
        min_rsi = min(min_rsi, rr)
        if peak_idx is None or idx <= peak_idx or peak_rsi < 60:
            continue
        dd = float(row["close"]) / peak - 1
        breakdown_ok = (not breakdown) or (pd.notna(row["prev_low"]) and float(row["close"]) < float(row["prev_low"]))
        if dd <= -dd_min and rr <= rsi_max and rr <= min_rsi + 1e-9 and breakdown_ok:
            return idx
    return None


def evaluate(h, idx, mode):
    loc = h.index.get_loc(idx)
    if mode == "flush_close":
        entry = float(h.iloc[loc]["close"])
        entry_time = h.iloc[loc]["close_time"]
        start = loc + 1
    elif mode == "next_open":
        if loc + 1 >= len(h):
            return None
        entry = float(h.iloc[loc+1]["open"])
        entry_time = h.iloc[loc+1]["open_time"]
        start = loc + 1
    else:
        return None
    if entry <= 0:
        return None
    out = {"entry":entry, "entry_time":entry_time}
    for name, bars in (("36h",9),("72h",18),("5d",30),("20d",120)):
        f = h.iloc[start:min(len(h), start+bars)]
        if f.empty:
            continue
        out["max_" + name] = float(f["high"].max()/entry - 1)
        out["min_" + name] = float(f["low"].min()/entry - 1)
        for tgt in (5, 7.5, 10):
            key = "hit" + str(tgt).replace(".","p") + "_" + name
            out[key] = out["max_" + name] >= tgt/100
    f = h.iloc[start:min(len(h), start+120)]
    tp = entry*1.05
    sl = entry*0.90
    tbar = None
    sbar = None
    for k, (_, row) in enumerate(f.iterrows()):
        if tbar is None and float(row["high"]) >= tp:
            tbar = k
        if sbar is None and float(row["low"]) <= sl:
            sbar = k
    out["hit5_before_m10"] = tbar is not None and (sbar is None or tbar < sbar)
    return out


def rate(s):
    s = s.dropna()
    return float(s.astype(bool).mean()) if len(s) else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=100)
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--out", default="crypto/research/results_1d4h")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    s = requests.Session()
    s.headers["User-Agent"] = "appwiza-crypto-research/1.0"

    syms = universe(s, args.symbols)
    variants = []
    for ret5 in (0.12,0.15,0.20,0.25):
        for rv in (0.020,0.025,0.030):
            for dd in (0.06,0.08,0.10,0.12,0.15):
                for rr in (30,35,40,45):
                    for bd in (False,True):
                        for mode in ("flush_close","next_open"):
                            variants.append((ret5,rv,dd,rr,bd,mode))

    trades = []
    coverage = []
    for pos, sym in enumerate(syms, 1):
        try:
            d = fetch_klines(s, sym, "1d", start-timedelta(days=100), end)
            if len(d) < 120:
                coverage.append((sym,"short",len(d),0))
                continue
            cache = {}
            arms_union = set()
            for ret5,rv,dd,rr,bd,mode in variants:
                k = (ret5,rv)
                if k not in cache:
                    dx, arms = daily_setups(d, ret5, rv)
                    cache[k] = (dx, [a for a in arms if a >= pd.Timestamp(start)])
                arms_union.update(cache[k][1])
            if not arms_union:
                coverage.append((sym,"no_setup",len(d),0))
                print("%d/%d %s no setups" % (pos,len(syms),sym))
                continue

            h = fetch_klines(s, sym, "4h", min(arms_union).to_pydatetime()-timedelta(days=2), end)
            h = prep4h(h)
            ntr = 0
            for ret5,rv,dd,rr,bd,mode in variants:
                dx, arms = cache[(ret5,rv)]
                for arm in arms:
                    idx = first_flush(h, arm, dd, rr, bd)
                    if idx is None:
                        continue
                    ev = evaluate(h, idx, mode)
                    if ev is None:
                        continue
                    row = dx.loc[dx["open_time"] == arm].iloc[0]
                    rec = {
                        "symbol":sym,
                        "variant":"ret%d_rv%dp_dd%d_rsi%d_%s_%s" % (
                            round(ret5*100),round(rv*1000),
                            round(dd*100),rr,
                            "bd" if bd else "nobd",mode
                        ),
                        "arm_time":arm,
                        "daily_ret5":float(row["ret5"]),
                        "daily_rv20":float(row["rv20"]),
                        "trigger_time":h.loc[idx,"close_time"],
                        "trigger_rsi14":float(h.loc[idx,"rsi14"]),
                    }
                    rec.update(ev)
                    trades.append(rec)
                    ntr += 1
            coverage.append((sym,"ok",len(d),ntr))
            print("%d/%d %s %d variant-trades" % (pos,len(syms),sym,ntr))
        except Exception as exc:
            coverage.append((sym,"error:" + str(exc)[:100],0,0))
            print("%s ERROR %s" % (sym,exc), file=sys.stderr)

    pd.DataFrame(coverage,columns=["symbol","status","daily_rows","variant_trades"]).to_csv(outdir/"coverage.csv",index=False)
    t = pd.DataFrame(trades)
    if t.empty:
        (outdir/"REPORT.txt").write_text("No trades produced. Check coverage.csv\n")
        raise SystemExit(2)
    t.to_csv(outdir/"trades.csv",index=False)

    rows = []
    for name,g in t.groupby("variant"):
        g = g.sort_values("entry_time")
        cut = max(1,int(len(g)*0.60))
        late = g.iloc[cut:]
        rows.append({
            "variant":name,"n":len(g),"symbols":g.symbol.nunique(),"late_n":len(late),
            "hit5_36h":rate(g.get("hit5_36h",pd.Series(dtype=float))),
            "hit5_72h":rate(g.get("hit5_72h",pd.Series(dtype=float))),
            "hit5_5d":rate(g.get("hit5_5d",pd.Series(dtype=float))),
            "hit7p5_5d":rate(g.get("hit7p5_5d",pd.Series(dtype=float))),
            "hit10_5d":rate(g.get("hit10_5d",pd.Series(dtype=float))),
            "hit5_20d":rate(g.get("hit5_20d",pd.Series(dtype=float))),
            "hit5_before_m10":rate(g["hit5_before_m10"]),
            "late_hit5_5d":rate(late.get("hit5_5d",pd.Series(dtype=float))),
            "late_hit10_5d":rate(late.get("hit10_5d",pd.Series(dtype=float))),
            "late_hit5_before_m10":rate(late["hit5_before_m10"]) if len(late) else np.nan,
        })
    z = pd.DataFrame(rows)
    z["score"] = z["late_hit5_5d"].fillna(0)*0.50 + z["late_hit10_5d"].fillna(0)*0.20 + z["late_hit5_before_m10"].fillna(0)*0.20 + z["hit5_36h"].fillna(0)*0.10
    z = z.sort_values(["score","late_n","n"],ascending=[False,False,False])
    z.to_csv(outdir/"variant_summary.csv",index=False)

    robust = z[(z["n"]>=30)&(z["late_n"]>=12)].head(20)
    if robust.empty:
        robust = z.head(20)
    lines = [
        "DAILY + 4H BLOW-OFF / FIRST-FLUSH REBOUND RESEARCH",
        "",
        "Daily chart is the setup detector; 4H is the entry clock.",
        "The entry is the first hard 4H flush after the daily breakout setup, not a later RSI recovery cross.",
        "Primary evaluation windows are 36h, 72h and 5d; 20d is secondary only.",
        "Latest 40 percent of each variant is reported separately as a simple chronological holdout.",
        "Current high-volume Binance USDT pairs are a fast refinement panel, not the final point-in-time top-500 validation.",
        "",
        robust.to_string(index=False),
        ""
    ]
    (outdir/"REPORT.txt").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
