#!/usr/bin/env python3
"""
Research-only RSI-dynamics study for blow-off-top rebound entries/exits.

Core idea:
- Daily chart identifies a violent breakout regime.
- 4H chart supplies the first post-peak flush.
- RSI is treated as a moving variable, not a static level:
  * percentage loss from its own post-breakout peak
  * 1-bar / 3-bar percentage change
  * acceleration of RSI losses
  * RSI shock relative to price shock
  * daily-vs-4H RSI dislocation
  * percentage recovery from the RSI trough
  * recovery of the peak-to-trough RSI loss as a dynamic exit signal

No live trading or production deployment.
"""
from __future__ import annotations

import argparse
import itertools
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
        time.sleep(0.02)
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


def prep_daily(d):
    x = d.copy()
    x["ret1"] = x["close"].pct_change()
    x["ret5"] = x["close"] / x["close"].shift(5) - 1
    x["prior60_high"] = x["high"].shift(1).rolling(60, min_periods=60).max()
    x["rv20"] = x["ret1"].rolling(20, min_periods=20).std()
    x["rsi14"] = rsi(x["close"])
    x["setup"] = (x["ret5"] >= 0.15) & (x["high"] >= x["prior60_high"]) & (x["rv20"] >= 0.025)
    events = []
    last = None
    for _, row in x.loc[x["setup"]].iterrows():
        t = row["close_time"]
        if last is None or t - last >= pd.Timedelta(days=10):
            events.append(t)
            last = t
    return x, events


def prep4h(h):
    x = h.copy()
    x["rsi14"] = rsi(x["close"])
    x["rsi_pct1"] = x["rsi14"].pct_change()
    x["rsi_pct3"] = x["rsi14"] / x["rsi14"].shift(3) - 1
    x["rsi_delta1"] = x["rsi14"].diff()
    x["rsi_accel"] = x["rsi_delta1"].diff()
    x["price_pct1"] = x["close"].pct_change()
    x["price_pct3"] = x["close"] / x["close"].shift(3) - 1

    # Price trend context.
    x["sma9"] = x["close"].rolling(9, min_periods=5).mean()
    x["sma20"] = x["close"].rolling(20, min_periods=10).mean()
    x["sma50"] = x["close"].rolling(50, min_periods=25).mean()
    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["dist_sma9"] = x["close"] / x["sma9"] - 1
    x["dist_sma20"] = x["close"] / x["sma20"] - 1
    x["dist_ema9"] = x["close"] / x["ema9"] - 1
    x["sma9_slope3"] = x["sma9"] / x["sma9"].shift(3) - 1
    x["sma20_slope3"] = x["sma20"] / x["sma20"].shift(3) - 1
    x["dist_sma50"] = x["close"] / x["sma50"] - 1
    x["range_pct"] = (x["high"] - x["low"]) / x["close"].replace(0, np.nan)
    x["lower_wick_pct_range"] = (np.minimum(x["open"],x["close"]) - x["low"]) / (x["high"]-x["low"]).replace(0,np.nan)
    x["close_location"] = (x["close"] - x["low"]) / (x["high"]-x["low"]).replace(0,np.nan)
    x["volume_med20"] = x["volume"].rolling(20,min_periods=10).median()
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0,np.nan)
    x["trades"] = pd.to_numeric(x["trades"],errors="coerce")
    x["trades_med20"] = x["trades"].rolling(20,min_periods=10).median()
    x["trades_ratio20"] = x["trades"] / x["trades_med20"].replace(0,np.nan)
    typical=(x["high"]+x["low"]+x["close"])/3.0
    x["vwap20"]=(typical*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["dist_vwap20"]=x["close"]/x["vwap20"]-1
    direction=np.sign(x["close"].diff()).fillna(0)
    x["obv"]=(direction*x["volume"].fillna(0)).cumsum()
    x["obv_delta3_norm"]=x["obv"].diff(3)/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    mfm=((x["close"]-x["low"])-(x["high"]-x["close"]))/(x["high"]-x["low"]).replace(0,np.nan)
    x["cmf20"]=(mfm*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["atr14_pct"]=(x["high"]-x["low"]).rolling(14,min_periods=8).mean()/x["close"].replace(0,np.nan)
    x["range_med20"] = x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0,np.nan)

    # RSI is treated as a price-like series with its own moving averages.
    x["rsi_sma3"] = x["rsi14"].rolling(3, min_periods=2).mean()
    x["rsi_sma5"] = x["rsi14"].rolling(5, min_periods=3).mean()
    x["rsi_sma9"] = x["rsi14"].rolling(9, min_periods=5).mean()
    x["rsi_ema5"] = x["rsi14"].ewm(span=5, adjust=False).mean()
    x["rsi_vs_sma3"] = x["rsi14"] / x["rsi_sma3"] - 1
    x["rsi_vs_sma5"] = x["rsi14"] / x["rsi_sma5"] - 1
    x["rsi_vs_sma9"] = x["rsi14"] / x["rsi_sma9"] - 1
    x["rsi_vs_ema5"] = x["rsi14"] / x["rsi_ema5"] - 1
    x["rsi_sma5_slope1"] = x["rsi_sma5"].pct_change()
    x["rsi_sma5_slope3"] = x["rsi_sma5"] / x["rsi_sma5"].shift(3) - 1
    x["rsi_cross_up_sma5"] = (x["rsi14"] > x["rsi_sma5"]) & (x["rsi14"].shift(1) <= x["rsi_sma5"].shift(1))
    x["rsi_cross_down_sma5"] = (x["rsi14"] < x["rsi_sma5"]) & (x["rsi14"].shift(1) >= x["rsi_sma5"].shift(1))
    x["price_cross_up_ema9"] = (x["close"] > x["ema9"]) & (x["close"].shift(1) <= x["ema9"].shift(1))
    x["price_cross_down_ema9"] = (x["close"] < x["ema9"]) & (x["close"].shift(1) >= x["ema9"].shift(1))
    x["dual_stretch_9"] = x["dist_sma9"].fillna(0) + x["rsi_vs_sma9"].fillna(0)
    return x


def first_price_flush(h, arm, dd_threshold, window_days=5):
    w = h[(h["open_time"] >= arm) & (h["open_time"] < arm + pd.Timedelta(days=window_days))]
    if len(w) < 5:
        return None
    peak_price = -math.inf
    peak_idx = None
    for idx, row in w.iterrows():
        hi = float(row["high"])
        if hi >= peak_price:
            peak_price = hi
            peak_idx = idx
        if peak_idx is None or idx <= peak_idx:
            continue
        dd = float(row["close"]) / peak_price - 1
        if dd <= -dd_threshold:
            return idx
    return None


def event_features(h, idx, arm, daily_rsi, dd_threshold):
    loc = h.index.get_loc(idx)
    arm_rows = h[(h["open_time"] >= arm) & (h.index <= idx)]
    if arm_rows.empty:
        return None
    rr = float(h.iloc[loc]["rsi14"])
    if not np.isfinite(rr) or rr <= 0:
        return None
    peak_rsi = float(arm_rows["rsi14"].max())
    peak_price = float(arm_rows["high"].max())
    trough_price = float(arm_rows["low"].min())
    rsi_drop_pct = rr / peak_rsi - 1 if peak_rsi > 0 else np.nan
    rsi_drop_pts = rr - peak_rsi
    price_dd = float(h.iloc[loc]["close"]) / peak_price - 1 if peak_price > 0 else np.nan
    price3 = float(h.iloc[loc]["price_pct3"]) if pd.notna(h.iloc[loc]["price_pct3"]) else np.nan
    rsi3 = float(h.iloc[loc]["rsi_pct3"]) if pd.notna(h.iloc[loc]["rsi_pct3"]) else np.nan
    shock_ratio = abs(rsi3) / max(abs(price3), 0.0025) if np.isfinite(rsi3) and np.isfinite(price3) else np.nan
    daily_gap = float(daily_rsi) - rr if np.isfinite(daily_rsi) else np.nan
    daily_ratio = rr / float(daily_rsi) if np.isfinite(daily_rsi) and daily_rsi > 0 else np.nan
    return {
        "flush_dd_threshold": dd_threshold,
        "trigger_rsi": rr,
        "rsi_peak": peak_rsi,
        "rsi_drop_pct": rsi_drop_pct,
        "rsi_drop_pts": rsi_drop_pts,
        "rsi_pct1": float(h.iloc[loc]["rsi_pct1"]) if pd.notna(h.iloc[loc]["rsi_pct1"]) else np.nan,
        "rsi_pct3": rsi3,
        "rsi_accel": float(h.iloc[loc]["rsi_accel"]) if pd.notna(h.iloc[loc]["rsi_accel"]) else np.nan,
        "price_pct1": float(h.iloc[loc]["price_pct1"]) if pd.notna(h.iloc[loc]["price_pct1"]) else np.nan,
        "price_pct3": price3,
        "price_dd": price_dd,
        "rsi_price_shock_ratio": shock_ratio,
        "daily_rsi": float(daily_rsi),
        "daily_4h_rsi_gap": daily_gap,
        "rsi_to_daily_ratio": daily_ratio,
        "event_low_to_peak": trough_price / peak_price - 1 if peak_price > 0 else np.nan,
        "dist_sma9": float(h.iloc[loc]["dist_sma9"]) if pd.notna(h.iloc[loc]["dist_sma9"]) else np.nan,
        "dist_sma20": float(h.iloc[loc]["dist_sma20"]) if pd.notna(h.iloc[loc]["dist_sma20"]) else np.nan,
        "dist_ema9": float(h.iloc[loc]["dist_ema9"]) if pd.notna(h.iloc[loc]["dist_ema9"]) else np.nan,
        "sma9_slope3": float(h.iloc[loc]["sma9_slope3"]) if pd.notna(h.iloc[loc]["sma9_slope3"]) else np.nan,
        "rsi_vs_sma3": float(h.iloc[loc]["rsi_vs_sma3"]) if pd.notna(h.iloc[loc]["rsi_vs_sma3"]) else np.nan,
        "rsi_vs_sma5": float(h.iloc[loc]["rsi_vs_sma5"]) if pd.notna(h.iloc[loc]["rsi_vs_sma5"]) else np.nan,
        "rsi_vs_sma9": float(h.iloc[loc]["rsi_vs_sma9"]) if pd.notna(h.iloc[loc]["rsi_vs_sma9"]) else np.nan,
        "rsi_sma5_slope1": float(h.iloc[loc]["rsi_sma5_slope1"]) if pd.notna(h.iloc[loc]["rsi_sma5_slope1"]) else np.nan,
        "rsi_sma5_slope3": float(h.iloc[loc]["rsi_sma5_slope3"]) if pd.notna(h.iloc[loc]["rsi_sma5_slope3"]) else np.nan,
        "dual_stretch_9": float(h.iloc[loc]["dual_stretch_9"]) if pd.notna(h.iloc[loc]["dual_stretch_9"]) else np.nan,
        "dist_sma50": float(h.iloc[loc]["dist_sma50"]) if pd.notna(h.iloc[loc]["dist_sma50"]) else np.nan,
        "sma20_slope3": float(h.iloc[loc]["sma20_slope3"]) if pd.notna(h.iloc[loc]["sma20_slope3"]) else np.nan,
        "rsi_vs_ema5": float(h.iloc[loc]["rsi_vs_ema5"]) if pd.notna(h.iloc[loc]["rsi_vs_ema5"]) else np.nan,
        "volume_ratio20": float(h.iloc[loc]["volume_ratio20"]) if pd.notna(h.iloc[loc]["volume_ratio20"]) else np.nan,
        "trades_ratio20": float(h.iloc[loc]["trades_ratio20"]) if pd.notna(h.iloc[loc]["trades_ratio20"]) else np.nan,
        "dist_vwap20": float(h.iloc[loc]["dist_vwap20"]) if pd.notna(h.iloc[loc]["dist_vwap20"]) else np.nan,
        "obv_delta3_norm": float(h.iloc[loc]["obv_delta3_norm"]) if pd.notna(h.iloc[loc]["obv_delta3_norm"]) else np.nan,
        "cmf20": float(h.iloc[loc]["cmf20"]) if pd.notna(h.iloc[loc]["cmf20"]) else np.nan,
        "atr14_pct": float(h.iloc[loc]["atr14_pct"]) if pd.notna(h.iloc[loc]["atr14_pct"]) else np.nan,
        "range_ratio20": float(h.iloc[loc]["range_ratio20"]) if pd.notna(h.iloc[loc]["range_ratio20"]) else np.nan,
        "lower_wick_pct_range": float(h.iloc[loc]["lower_wick_pct_range"]) if pd.notna(h.iloc[loc]["lower_wick_pct_range"]) else np.nan,
        "close_location": float(h.iloc[loc]["close_location"]) if pd.notna(h.iloc[loc]["close_location"]) else np.nan,
    }


def future_labels(h, idx, entry_mode="next_open"):
    loc = h.index.get_loc(idx)
    if entry_mode == "flush_close":
        entry = float(h.iloc[loc]["close"])
        entry_time = h.iloc[loc]["close_time"]
        start = loc + 1
    else:
        if loc + 1 >= len(h):
            return None
        entry = float(h.iloc[loc+1]["open"])
        entry_time = h.iloc[loc+1]["open_time"]
        start = loc + 1
    if entry <= 0:
        return None
    out = {"entry":entry, "entry_time":entry_time}
    for name,bars in (("36h",9),("72h",18),("5d",30)):
        f = h.iloc[start:min(len(h), start+bars)]
        if f.empty:
            continue
        mfe = float(f["high"].max()/entry - 1)
        mae = float(f["low"].min()/entry - 1)
        out["mfe_"+name] = mfe
        out["mae_"+name] = mae
        out["hit5_"+name] = mfe >= 0.05
        out["hit7p5_"+name] = mfe >= 0.075
        out["hit10_"+name] = mfe >= 0.10

    # Ex-ante target-before-stop outcomes for cross-venue validation.
    f5 = h.iloc[start:min(len(h), start+30)]
    for stop in (0.05,0.075,0.10):
        tk = sk = None
        for k, (_, row) in enumerate(f5.iterrows()):
            if tk is None and float(row["high"]) >= entry*1.05:
                tk = k
            if sk is None and float(row["low"]) <= entry*(1-stop):
                sk = k
        out["t50_before_s%d_5d" % round(stop*1000)] = (
            tk is not None and (sk is None or tk < sk)
        )

    # RSI-based entry confirmation: first 4H close after the flush where RSI
    # rises by 10/20/30% from the trigger RSI, but price has not already bounced >4%.
    trigger_rsi = float(h.iloc[loc]["rsi14"])
    trigger_px = float(h.iloc[loc]["close"])
    for pct in (0.10,0.20,0.30):
        key = "confirm%d" % round(pct*100)
        out[key+"_found"] = False
        for j in range(loc+1, min(len(h), loc+5)):
            rj = float(h.iloc[j]["rsi14"])
            pj = float(h.iloc[j]["close"])
            if rj >= trigger_rsi*(1+pct) and pj <= trigger_px*1.04:
                out[key+"_found"] = True
                out[key+"_entry_time"] = h.iloc[j]["close_time"]
                out[key+"_entry"] = pj
                f = h.iloc[j+1:min(len(h), j+1+30)]
                if not f.empty:
                    out[key+"_mfe5d"] = float(f["high"].max()/pj - 1)
                    out[key+"_hit5_5d"] = out[key+"_mfe5d"] >= 0.05
                    out[key+"_hit10_5d"] = out[key+"_mfe5d"] >= 0.10
                break

    # Moving-average entry confirmations. These are deliberately evaluated
    # after the flush trigger so we can measure whether confirmation helps or
    # simply gives away too much of the rebound.
    trigger_px = float(h.iloc[loc]["close"])
    for key, pred in (
        ("ma_rsi_cross", lambda row: bool(row["rsi_cross_up_sma5"])),
        ("ma_price_reclaim", lambda row: bool(row["price_cross_up_ema9"])),
        ("ma_dual_reclaim", lambda row: bool(row["rsi_cross_up_sma5"]) and float(row["close"]) > float(row["ema9"])),
        ("ma_rsi_lead", lambda row: bool(row["rsi_cross_up_sma5"]) and float(row["close"]) < float(row["ema9"])),
    ):
        out[key+"_found"] = False
        for j in range(loc+1, min(len(h), loc+7)):
            row = h.iloc[j]
            if pred(row) and float(row["close"]) <= trigger_px*1.08:
                px = float(row["close"])
                out[key+"_found"] = True
                out[key+"_bars"] = j-loc
                out[key+"_entry"] = px
                out[key+"_move_before_entry"] = px/entry - 1
                fut = h.iloc[j+1:min(len(h),j+1+30)]
                if not fut.empty:
                    mfe=float(fut["high"].max()/px-1)
                    mae=float(fut["low"].min()/px-1)
                    out[key+"_mfe5d"]=mfe
                    out[key+"_mae5d"]=mae
                    out[key+"_hit5_5d"]=mfe>=0.05
                    out[key+"_hit10_5d"]=mfe>=0.10
                break

    # RSI-based exits from the ordinary next-open entry. Treat RSI as a
    # percentage-recovery path, not an absolute 30/70 oscillator.
    peak_rsi = float(h.iloc[max(0,loc-12):loc+1]["rsi14"].max())
    trough_rsi = trigger_rsi
    loss = max(peak_rsi - trough_rsi, 1e-9)
    f = h.iloc[start:min(len(h), start+31)]
    for frac in (0.50,0.75,1.00):
        key = "rsi_recover_%d" % round(frac*100)
        out[key+"_found"] = False
        target_rsi = trough_rsi + loss*frac
        for _, row in f.iterrows():
            if float(row["rsi14"]) >= target_rsi:
                out[key+"_found"] = True
                out[key+"_return"] = float(row["close"])/entry - 1
                out[key+"_time"] = row["close_time"]
                break
    for pct in (0.20,0.35,0.50):
        key = "rsi_gain_%d" % round(pct*100)
        out[key+"_found"] = False
        target_rsi = trough_rsi*(1+pct)
        for _, row in f.iterrows():
            if float(row["rsi14"]) >= target_rsi:
                out[key+"_found"] = True
                out[key+"_return"] = float(row["close"])/entry - 1
                out[key+"_time"] = row["close_time"]
                break

    # Rebound-exhaustion exits using RSI's own SMA and price EMA9.
    recovered50 = False
    px_reclaimed = False
    rsi_turn = px_loss = None
    for j in range(start, min(len(h), start+31)):
        row=h.iloc[j]
        if float(row["rsi14"]) >= trough_rsi + loss*0.50:
            recovered50=True
        if float(row["close"]) > float(row["ema9"]):
            px_reclaimed=True
        if recovered50 and rsi_turn is None and bool(row["rsi_cross_down_sma5"]):
            rsi_turn=j
        if px_reclaimed and px_loss is None and bool(row["price_cross_down_ema9"]):
            px_loss=j
    out["ma_exit_rsi_turn_found"]=rsi_turn is not None
    if rsi_turn is not None:
        out["ma_exit_rsi_turn_return"]=float(h.iloc[rsi_turn]["close"])/entry-1
    out["ma_exit_price_loss_found"]=px_loss is not None
    if px_loss is not None:
        out["ma_exit_price_loss_return"]=float(h.iloc[px_loss]["close"])/entry-1
    out["ma_exit_dual_found"]=rsi_turn is not None and px_loss is not None and abs(rsi_turn-px_loss)<=1
    if out["ma_exit_dual_found"]:
        j=max(rsi_turn,px_loss)
        out["ma_exit_dual_return"]=float(h.iloc[j]["close"])/entry-1
    return out


def rate(s):
    x = s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan


def train_holdout_split(df):
    d = df.sort_values("entry_time").reset_index(drop=True)
    cut = max(1, int(len(d)*0.60))
    return d.iloc[:cut].copy(), d.iloc[cut:].copy()


def feature_rule_search(events):
    features = [
        ("rsi_drop_pct","low"),
        ("rsi_pct1","low"),
        ("rsi_pct3","low"),
        ("rsi_accel","low"),
        ("rsi_price_shock_ratio","high"),
        ("daily_4h_rsi_gap","high"),
        ("rsi_to_daily_ratio","low"),
        ("dist_sma9","low"),("dist_sma20","low"),("dist_ema9","low"),
        ("sma9_slope3","low"),("rsi_vs_sma3","low"),("rsi_vs_sma5","low"),
        ("rsi_vs_sma9","low"),("rsi_sma5_slope1","low"),
        ("rsi_sma5_slope3","low"),("dual_stretch_9","low"),
    ]
    train, hold = train_holdout_split(events)
    candidates = []
    for feat, direction in features:
        clean = train[feat].replace([np.inf,-np.inf],np.nan).dropna()
        if len(clean) < 20:
            continue
        qs = (0.20,0.33,0.50,0.67,0.80)
        for q in qs:
            th = float(clean.quantile(q))
            if direction == "low":
                mt = train[feat] <= th
                mh = hold[feat] <= th
                op = "<="
            else:
                mt = train[feat] >= th
                mh = hold[feat] >= th
                op = ">="
            gt, gh = train[mt], hold[mh]
            if len(gt) < 15 or len(gh) < 6:
                continue
            candidates.append({
                "rule":"%s %s %.5f" % (feat,op,th),
                "features":feat,
                "train_n":len(gt),
                "train_hit5_5d":rate(gt["hit5_5d"]),
                "train_hit10_5d":rate(gt["hit10_5d"]),
                "hold_n":len(gh),
                "hold_hit5_5d":rate(gh["hit5_5d"]),
                "hold_hit10_5d":rate(gh["hit10_5d"]),
                "hold_mfe5d_median":float(gh["mfe_5d"].median()),
                "hold_mae5d_median":float(gh["mae_5d"].median()),
            })

    # Pairwise rules are selected using TRAIN only. Holdout is reported but
    # never used to pick thresholds.
    base_rules = sorted(candidates, key=lambda x:(x["train_hit5_5d"],x["train_hit10_5d"],x["train_n"]), reverse=True)[:18]
    pairs = []
    for a,b in itertools.combinations(base_rules,2):
        if a["features"] == b["features"]:
            continue
        def parse(rule):
            p = rule.split()
            return p[0],p[1],float(p[2])
        f1,o1,t1 = parse(a["rule"])
        f2,o2,t2 = parse(b["rule"])
        mt = (train[f1] <= t1 if o1=="<=" else train[f1] >= t1) & (train[f2] <= t2 if o2=="<=" else train[f2] >= t2)
        mh = (hold[f1] <= t1 if o1=="<=" else hold[f1] >= t1) & (hold[f2] <= t2 if o2=="<=" else hold[f2] >= t2)
        gt,gh = train[mt],hold[mh]
        if len(gt) < 15 or len(gh) < 6:
            continue
        pairs.append({
            "rule":a["rule"]+" AND "+b["rule"],
            "features":f1+"+"+f2,
            "train_n":len(gt),
            "train_hit5_5d":rate(gt["hit5_5d"]),
            "train_hit10_5d":rate(gt["hit10_5d"]),
            "hold_n":len(gh),
            "hold_hit5_5d":rate(gh["hit5_5d"]),
            "hold_hit10_5d":rate(gh["hit10_5d"]),
            "hold_mfe5d_median":float(gh["mfe_5d"].median()),
            "hold_mae5d_median":float(gh["mae_5d"].median()),
        })
    all_rules = pd.DataFrame(candidates + pairs)
    if all_rules.empty:
        return all_rules
    all_rules["train_score"] = all_rules["train_hit5_5d"]*0.7 + all_rules["train_hit10_5d"]*0.3
    return all_rules.sort_values(["train_score","train_n"],ascending=[False,False]).reset_index(drop=True)


def summarize_confirmations(events):
    rows = []
    for pct in (10,20,30):
        found = events["confirm%d_found" % pct] == True
        g = events[found]
        rows.append({
            "entry":"RSI +%d%% from flush trough" % pct,
            "n":len(g),
            "coverage":len(g)/len(events) if len(events) else np.nan,
            "hit5_5d":rate(g["confirm%d_hit5_5d" % pct]) if len(g) else np.nan,
            "hit10_5d":rate(g["confirm%d_hit10_5d" % pct]) if len(g) else np.nan,
            "median_mfe5d":float(g["confirm%d_mfe5d" % pct].median()) if len(g) else np.nan,
        })
    return pd.DataFrame(rows)


def summarize_exits(events):
    rows = []
    for key,label in [
        ("rsi_recover_50","Recover 50% of RSI peak-to-trough loss"),
        ("rsi_recover_75","Recover 75% of RSI peak-to-trough loss"),
        ("rsi_recover_100","Recover 100% of RSI peak-to-trough loss"),
        ("rsi_gain_20","RSI gains 20% from trough"),
        ("rsi_gain_35","RSI gains 35% from trough"),
        ("rsi_gain_50","RSI gains 50% from trough"),
    ]:
        g = events[events[key+"_found"] == True]
        ret = g[key+"_return"] if len(g) else pd.Series(dtype=float)
        rows.append({
            "exit":label,
            "n":len(g),
            "coverage":len(g)/len(events) if len(events) else np.nan,
            "positive_rate":float((ret>0).mean()) if len(ret) else np.nan,
            "return_ge_5":float((ret>=0.05).mean()) if len(ret) else np.nan,
            "median_return":float(ret.median()) if len(ret) else np.nan,
            "mean_return":float(ret.mean()) if len(ret) else np.nan,
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--days", type=int, default=600)
    ap.add_argument("--out", default="crypto/research/results_rsi_dynamics")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    start = end - timedelta(days=args.days)
    session = requests.Session()
    session.headers["User-Agent"] = "appwiza-rsi-dynamics-research/1.0"

    syms = universe(session,args.symbols)
    records = []
    coverage = []
    for pos,sym in enumerate(syms,1):
        try:
            d = fetch_klines(session,sym,"1d",start-timedelta(days=100),end)
            if len(d) < 120:
                coverage.append((sym,"short",len(d),0))
                continue
            dx,arms = prep_daily(d)
            arms = [a for a in arms if a >= pd.Timestamp(start)]
            if not arms:
                coverage.append((sym,"no_setup",len(d),0))
                print("%d/%d %s no setups" % (pos,len(syms),sym))
                continue
            h = prep4h(fetch_klines(session,sym,"4h",min(arms).to_pydatetime()-timedelta(days=2),end))
            n = 0
            for arm in arms:
                dr = dx.loc[dx["close_time"]==arm]
                if dr.empty:
                    continue
                daily_rsi = float(dr.iloc[0]["rsi14"])
                for dd in (0.08,0.10,0.12):
                    idx = first_price_flush(h,arm,dd)
                    if idx is None:
                        continue
                    feat = event_features(h,idx,arm,daily_rsi,dd)
                    lab = future_labels(h,idx,"next_open")
                    if feat is None or lab is None:
                        continue
                    rec = {
                        "symbol":sym,
                        "arm_time":arm,
                        "trigger_time":h.loc[idx,"close_time"],
                        "daily_ret5":float(dr.iloc[0]["ret5"]),
                        "daily_rv20":float(dr.iloc[0]["rv20"]),
                    }
                    rec.update(feat)
                    rec.update(lab)
                    records.append(rec)
                    n += 1
            coverage.append((sym,"ok",len(d),n))
            print("%d/%d %s %d events" % (pos,len(syms),sym,n))
        except Exception as exc:
            coverage.append((sym,"error:"+str(exc)[:100],0,0))
            print("%s ERROR %s" % (sym,exc),file=sys.stderr)

    pd.DataFrame(coverage,columns=["symbol","status","daily_rows","events"]).to_csv(outdir/"coverage.csv",index=False)
    e = pd.DataFrame(records)
    if e.empty:
        (outdir/"REPORT.txt").write_text("No events. Check coverage.csv\n")
        raise SystemExit(2)
    e.to_csv(outdir/"events.csv",index=False)

    rules = feature_rule_search(e)
    rules.to_csv(outdir/"rsi_feature_rules.csv",index=False)
    confirms = summarize_confirmations(e)
    confirms.to_csv(outdir/"rsi_entry_confirmations.csv",index=False)
    exits = summarize_exits(e)
    exits.to_csv(outdir/"rsi_exit_rules.csv",index=False)

    # Feature-bucket diagnostics: shows shape instead of assuming linearity.
    diag_rows = []
    for feat in ["rsi_drop_pct","rsi_pct1","rsi_pct3","rsi_accel","rsi_price_shock_ratio","daily_4h_rsi_gap","rsi_to_daily_ratio",
                 "dist_sma9","dist_sma20","dist_ema9","sma9_slope3","rsi_vs_sma3","rsi_vs_sma5","rsi_vs_sma9",
                 "rsi_sma5_slope1","rsi_sma5_slope3","dual_stretch_9"]:
        x = e[[feat,"hit5_5d","hit10_5d","mfe_5d","mae_5d"]].replace([np.inf,-np.inf],np.nan).dropna()
        if len(x) < 20:
            continue
        try:
            x["bucket"] = pd.qcut(x[feat],4,duplicates="drop")
        except Exception:
            continue
        for b,g in x.groupby("bucket",observed=True):
            diag_rows.append({
                "feature":feat,"bucket":str(b),"n":len(g),
                "feature_median":float(g[feat].median()),
                "hit5_5d":rate(g["hit5_5d"]),
                "hit10_5d":rate(g["hit10_5d"]),
                "median_mfe5d":float(g["mfe_5d"].median()),
                "median_mae5d":float(g["mae_5d"].median()),
            })
    diag = pd.DataFrame(diag_rows)
    diag.to_csv(outdir/"rsi_feature_buckets.csv",index=False)

    base = {
        "events":len(e),
        "symbols":e["symbol"].nunique(),
        "hit5_36h":rate(e["hit5_36h"]),
        "hit5_72h":rate(e["hit5_72h"]),
        "hit5_5d":rate(e["hit5_5d"]),
        "hit10_5d":rate(e["hit10_5d"]),
        "median_mfe5d":float(e["mfe_5d"].median()),
        "median_mae5d":float(e["mae_5d"].median()),
    }
    top = rules.head(15) if not rules.empty else pd.DataFrame()
    lines = [
        "RSI DYNAMICS RESEARCH — DAILY BLOW-OFF + 4H FIRST FLUSH",
        "",
        "BASELINE",
        str(base),
        "",
        "This study does not use RSI as a one-dimensional absolute threshold.",
        "It measures RSI percentage loss, RSI acceleration, RSI-vs-price shock, daily/4H RSI dislocation, RSI percentage rebound entries, and RSI-recovery exits.",
        "",
        "TOP TRAIN-SELECTED RULES WITH LATER 40% HOLDOUT",
        top.to_string(index=False) if not top.empty else "No rule met minimum sample sizes.",
        "",
        "RSI PERCENT-RECOVERY ENTRY TIMING",
        confirms.to_string(index=False),
        "",
        "RSI-DERIVED EXIT TIMING",
        exits.to_string(index=False),
        "",
        "Guardrail: rules are ranked only by the first 60% chronological training sample; the later 40% is shown as holdout and is not used to choose thresholds.",
    ]
    (outdir/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
