#!/usr/bin/env python3
"""
Hyperliquid exhaustive daily + 4H blow-off / RSI-dynamics research.

Scans every currently active Hyperliquid instrument exposed by the public API:
- primary perpetuals
- builder-deployed (HIP-3) perpetuals
- spot pairs

Research only. No orders. No deployment.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://api.hyperliquid.xyz/info"


def now_utc():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def post(session, payload, tries=8):
    last = None
    for k in range(tries):
        try:
            r = session.post(API, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError("HTTP %s: %s" % (r.status_code, r.text[:300]))
            if r.status_code in (418, 429, 503):
                time.sleep(min(30, 2.0 * (k + 1)))
            else:
                time.sleep(min(10, 1.0 + k))
        except Exception as exc:
            last = exc
            time.sleep(min(10, 1.0 + k))
    raise RuntimeError(str(last))


def fnum(x, default=np.nan):
    try:
        return float(x)
    except Exception:
        return default


def enumerate_universe(session):
    instruments = []

    # Perpetual DEXes: first item is null = primary dex.
    dexs = post(session, {"type": "perpDexs"})
    dex_names = [""]
    for item in dexs or []:
        if isinstance(item, dict) and item.get("name"):
            dex_names.append(str(item["name"]))

    seen = set()
    for dex in dex_names:
        payload = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        try:
            resp = post(session, payload)
            meta, ctxs = resp[0], resp[1]
        except Exception as exc:
            print("DEX_ENUM_ERROR %s %s" % (dex or "primary", exc), file=sys.stderr)
            continue
        universe = meta.get("universe", [])
        for i, u in enumerate(universe):
            if u.get("isDelisted"):
                continue
            raw = str(u.get("name", ""))
            if not raw:
                continue
            api_coin = raw if (not dex or ":" in raw) else dex + ":" + raw
            key = ("perp", api_coin)
            if key in seen:
                continue
            seen.add(key)
            ctx = ctxs[i] if i < len(ctxs) and isinstance(ctxs[i], dict) else {}
            instruments.append({
                "kind": "perp",
                "dex": dex or "primary",
                "display_name": api_coin,
                "api_coin": api_coin,
                "index": i,
                "day_ntl_vlm": fnum(ctx.get("dayNtlVlm")),
                "open_interest": fnum(ctx.get("openInterest")),
                "funding": fnum(ctx.get("funding")),
                "mark_px": fnum(ctx.get("markPx")),
                "oracle_px": fnum(ctx.get("oraclePx")),
                "max_leverage": fnum(u.get("maxLeverage")),
            })

    # Spot pairs.
    try:
        resp = post(session, {"type": "spotMetaAndAssetCtxs"})
        meta, ctxs = resp[0], resp[1]
        for i, u in enumerate(meta.get("universe", [])):
            name = str(u.get("name", ""))
            idx = int(u.get("index", i))
            if not name:
                continue
            api_coin = name if name == "PURR/USDC" else "@" + str(idx)
            key = ("spot", api_coin)
            if key in seen:
                continue
            seen.add(key)
            ctx = ctxs[i] if i < len(ctxs) and isinstance(ctxs[i], dict) else {}
            instruments.append({
                "kind": "spot",
                "dex": "spot",
                "display_name": name,
                "api_coin": api_coin,
                "index": idx,
                "day_ntl_vlm": fnum(ctx.get("dayNtlVlm")),
                "open_interest": np.nan,
                "funding": np.nan,
                "mark_px": fnum(ctx.get("midPx", ctx.get("markPx"))),
                "oracle_px": np.nan,
                "max_leverage": np.nan,
            })
    except Exception as exc:
        print("SPOT_ENUM_ERROR %s" % exc, file=sys.stderr)

    instruments.sort(key=lambda x: (x["kind"], x["dex"], x["display_name"]))
    return instruments


def candle_df(rows):
    if not rows:
        return pd.DataFrame(columns=["time","open","high","low","close","volume","trades"])
    recs = []
    for r in rows:
        recs.append({
            "time": pd.to_datetime(int(r["t"]), unit="ms", utc=True),
            "open": fnum(r["o"]),
            "high": fnum(r["h"]),
            "low": fnum(r["l"]),
            "close": fnum(r["c"]),
            "volume": fnum(r.get("v")),
            "trades": fnum(r.get("n")),
        })
    d = pd.DataFrame(recs)
    return d.dropna(subset=["open","high","low","close"]).drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_4h(session, coin, start, end):
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "4h",
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
        },
    }
    return candle_df(post(session, payload))


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(al.ne(0), 100.0)


def prep4h(h):
    x = h.copy()
    x["rsi14"] = rsi(x["close"])
    x["rsi_pct1"] = x["rsi14"].pct_change()
    x["rsi_pct3"] = x["rsi14"] / x["rsi14"].shift(3) - 1
    x["rsi_delta1"] = x["rsi14"].diff()
    x["rsi_accel"] = x["rsi_delta1"].diff()
    x["price_pct1"] = x["close"].pct_change()
    x["price_pct3"] = x["close"] / x["close"].shift(3) - 1
    x["range_pct"] = (x["high"] - x["low"]) / x["close"].replace(0, np.nan)
    x["lower_wick_pct_range"] = (np.minimum(x["open"], x["close"]) - x["low"]) / (x["high"] - x["low"]).replace(0, np.nan)
    x["close_location"] = (x["close"] - x["low"]) / (x["high"] - x["low"]).replace(0, np.nan)
    x["volume_med5"] = x["volume"].rolling(5, min_periods=3).median()
    x["volume_med20"] = x["volume"].rolling(20, min_periods=10).median()
    x["volume_mean20"] = x["volume"].rolling(20, min_periods=10).mean()
    x["volume_std20"] = x["volume"].rolling(20, min_periods=10).std()
    x["volume_ratio5"] = x["volume"] / x["volume_med5"].replace(0, np.nan)
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0, np.nan)
    x["volume_z20"] = (x["volume"] - x["volume_mean20"]) / x["volume_std20"].replace(0, np.nan)
    x["trades"] = pd.to_numeric(x["trades"], errors="coerce")
    x["trades_med20"] = x["trades"].rolling(20, min_periods=10).median()
    x["trades_ratio20"] = x["trades"] / x["trades_med20"].replace(0, np.nan)
    typical = (x["high"] + x["low"] + x["close"]) / 3.0
    x["vwap20"] = (typical*x["volume"]).rolling(20,min_periods=10).sum() / x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["dist_vwap20"] = x["close"] / x["vwap20"] - 1
    direction = np.sign(x["close"].diff()).fillna(0)
    x["obv"] = (direction*x["volume"].fillna(0)).cumsum()
    x["obv_delta3_norm"] = x["obv"].diff(3) / x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    mfm=((x["close"]-x["low"])-(x["high"]-x["close"]))/(x["high"]-x["low"]).replace(0,np.nan)
    x["cmf20"]=(mfm*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["atr14_pct"]=(x["high"]-x["low"]).rolling(14,min_periods=8).mean()/x["close"].replace(0,np.nan)
    x["range_med20"] = x["range_pct"].rolling(20, min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0, np.nan)

    # Price trend / mean-reversion structure. We deliberately keep several
    # horizons so the search can learn whether the rebound begins while price
    # is still stretched below trend, on a fast reclaim, or only after a
    # slower trend reset.
    x["sma9"] = x["close"].rolling(9, min_periods=5).mean()
    x["sma20"] = x["close"].rolling(20, min_periods=10).mean()
    x["sma50"] = x["close"].rolling(50, min_periods=25).mean()
    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["dist_sma9"] = x["close"] / x["sma9"] - 1
    x["dist_sma20"] = x["close"] / x["sma20"] - 1
    x["dist_sma50"] = x["close"] / x["sma50"] - 1
    x["dist_ema9"] = x["close"] / x["ema9"] - 1
    x["dist_ema20"] = x["close"] / x["ema20"] - 1
    x["sma9_slope3"] = x["sma9"] / x["sma9"].shift(3) - 1
    x["sma20_slope3"] = x["sma20"] / x["sma20"].shift(3) - 1
    x["price_cross_up_ema9"] = (x["close"] > x["ema9"]) & (x["close"].shift(1) <= x["ema9"].shift(1))
    x["price_cross_down_ema9"] = (x["close"] < x["ema9"]) & (x["close"].shift(1) >= x["ema9"].shift(1))
    x["price_cross_up_sma9"] = (x["close"] > x["sma9"]) & (x["close"].shift(1) <= x["sma9"].shift(1))
    x["price_cross_down_sma9"] = (x["close"] < x["sma9"]) & (x["close"].shift(1) >= x["sma9"].shift(1))

    # RSI gets its own moving-average structure. This lets us test RSI as a
    # price-like series rather than an absolute 30/70 oscillator.
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

    # Joint stretch: large negative means both price and RSI are below their
    # own local means. Lead/lag between the two reclaims may identify the
    # earliest high-quality rebound entry.
    x["dual_stretch_9"] = x["dist_sma9"].fillna(0) + x["rsi_vs_sma9"].fillna(0)
    return x


def daily_from_4h(h):
    z = h.set_index("time")
    d = pd.DataFrame({
        "open": z["open"].resample("1D").first(),
        "high": z["high"].resample("1D").max(),
        "low": z["low"].resample("1D").min(),
        "close": z["close"].resample("1D").last(),
        "volume": z["volume"].resample("1D").sum(),
    }).dropna(subset=["open","high","low","close"]).reset_index()
    d["ret1"] = d["close"].pct_change()
    d["ret5"] = d["close"] / d["close"].shift(5) - 1
    d["prior60_high"] = d["high"].shift(1).rolling(60, min_periods=60).max()
    d["rv20"] = d["ret1"].rolling(20, min_periods=20).std()
    d["rsi14"] = rsi(d["close"])
    d["setup"] = (d["ret5"] >= 0.15) & (d["high"] >= d["prior60_high"]) & (d["rv20"] >= 0.025)
    # No-lookahead: the completed UTC daily candle is not knowable until the
    # next UTC day begins. All 4H event detection must arm after that close.
    d["arm_time"] = d["time"] + pd.Timedelta(days=1)
    return d


def setup_events(d):
    out, last = [], None
    for _, row in d.loc[d["setup"]].iterrows():
        t = row["arm_time"]
        if last is None or t - last >= pd.Timedelta(days=10):
            out.append(t)
            last = t
    return out


def first_flush(h, arm, threshold, window_days=5):
    w = h[(h["time"] >= arm) & (h["time"] < arm + pd.Timedelta(days=window_days))]
    if len(w) < 4:
        return None
    peak, peak_idx = -math.inf, None
    for idx, row in w.iterrows():
        if float(row["high"]) >= peak:
            peak = float(row["high"])
            peak_idx = idx
        if peak_idx is None or idx <= peak_idx or peak <= 0:
            continue
        if float(row["close"]) / peak - 1 <= -threshold:
            return idx
    return None


def build_event(h, d, arm, idx, threshold, meta):
    loc = h.index.get_loc(idx)
    if loc + 1 >= len(h):
        return None
    arm_rows = h[(h["time"] >= arm) & (h.index <= idx)]
    if arm_rows.empty:
        return None
    rr = fnum(h.iloc[loc]["rsi14"])
    if not np.isfinite(rr) or rr <= 0:
        return None
    peak_rsi = fnum(arm_rows["rsi14"].max())
    peak_price = fnum(arm_rows["high"].max())
    dr = d[d["arm_time"] == arm]
    if dr.empty:
        return None
    daily_rsi = fnum(dr.iloc[0]["rsi14"])
    price3 = fnum(h.iloc[loc]["price_pct3"])
    rsi3 = fnum(h.iloc[loc]["rsi_pct3"])
    shock = abs(rsi3) / max(abs(price3), 0.0025) if np.isfinite(rsi3) and np.isfinite(price3) else np.nan

    entry = fnum(h.iloc[loc + 1]["open"])
    if not np.isfinite(entry) or entry <= 0:
        return None
    start = loc + 1

    rec = {
        **meta,
        "arm_time": arm,
        "trigger_time": h.iloc[loc]["time"],
        "entry_time": h.iloc[loc + 1]["time"],
        "entry": entry,
        "flush_threshold": threshold,
        "daily_ret5": fnum(dr.iloc[0]["ret5"]),
        "daily_rv20": fnum(dr.iloc[0]["rv20"]),
        "daily_rsi": daily_rsi,
        "trigger_rsi": rr,
        "rsi_peak": peak_rsi,
        "rsi_drop_pct": rr / peak_rsi - 1 if peak_rsi > 0 else np.nan,
        "rsi_pct1": fnum(h.iloc[loc]["rsi_pct1"]),
        "rsi_pct3": rsi3,
        "rsi_accel": fnum(h.iloc[loc]["rsi_accel"]),
        "price_pct1": fnum(h.iloc[loc]["price_pct1"]),
        "price_pct3": price3,
        "price_dd": fnum(h.iloc[loc]["close"]) / peak_price - 1 if peak_price > 0 else np.nan,
        "rsi_price_shock_ratio": shock,
        "daily_4h_rsi_gap": daily_rsi - rr if np.isfinite(daily_rsi) else np.nan,
        "rsi_to_daily_ratio": rr / daily_rsi if np.isfinite(daily_rsi) and daily_rsi > 0 else np.nan,
        "volume_ratio5": fnum(h.iloc[loc]["volume_ratio5"]),
        "volume_ratio20": fnum(h.iloc[loc]["volume_ratio20"]),
        "volume_z20": fnum(h.iloc[loc]["volume_z20"]),
        "trades_ratio20": fnum(h.iloc[loc]["trades_ratio20"]),
        "dist_vwap20": fnum(h.iloc[loc]["dist_vwap20"]),
        "obv_delta3_norm": fnum(h.iloc[loc]["obv_delta3_norm"]),
        "cmf20": fnum(h.iloc[loc]["cmf20"]),
        "atr14_pct": fnum(h.iloc[loc]["atr14_pct"]),
        "range_ratio20": fnum(h.iloc[loc]["range_ratio20"]),
        "lower_wick_pct_range": fnum(h.iloc[loc]["lower_wick_pct_range"]),
        "close_location": fnum(h.iloc[loc]["close_location"]),
        "dist_ema20": fnum(h.iloc[loc]["dist_ema20"]),
        "dist_ema9": fnum(h.iloc[loc]["dist_ema9"]),
        "dist_sma9": fnum(h.iloc[loc]["dist_sma9"]),
        "dist_sma20": fnum(h.iloc[loc]["dist_sma20"]),
        "dist_sma50": fnum(h.iloc[loc]["dist_sma50"]),
        "sma9_slope3": fnum(h.iloc[loc]["sma9_slope3"]),
        "sma20_slope3": fnum(h.iloc[loc]["sma20_slope3"]),
        "rsi_vs_sma3": fnum(h.iloc[loc]["rsi_vs_sma3"]),
        "rsi_vs_sma5": fnum(h.iloc[loc]["rsi_vs_sma5"]),
        "rsi_vs_sma9": fnum(h.iloc[loc]["rsi_vs_sma9"]),
        "rsi_vs_ema5": fnum(h.iloc[loc]["rsi_vs_ema5"]),
        "rsi_sma5_slope1": fnum(h.iloc[loc]["rsi_sma5_slope1"]),
        "rsi_sma5_slope3": fnum(h.iloc[loc]["rsi_sma5_slope3"]),
        "dual_stretch_9": fnum(h.iloc[loc]["dual_stretch_9"]),
    }

    for name, bars in (("36h",9),("72h",18),("5d",30)):
        f = h.iloc[start:min(len(h), start + bars)]
        if f.empty:
            continue
        mfe = fnum(f["high"].max() / entry - 1)
        mae = fnum(f["low"].min() / entry - 1)
        rec["mfe_" + name] = mfe
        rec["mae_" + name] = mae
        # Exact end-of-window return lets downstream research compute a
        # target/stop/timeout P&L without inventing the timeout outcome.
        rec["close_ret_" + name] = fnum(f.iloc[-1]["close"] / entry - 1)
        for tgt in (5, 7.5, 10):
            rec["hit%s_%s" % (str(tgt).replace(".","p"), name)] = mfe >= tgt/100

    f = h.iloc[start:min(len(h), start + 30)]
    for target in (0.05,0.075,0.10):
        for stop in (0.05,0.075,0.10):
            tk = sk = None
            for k, (_, row) in enumerate(f.iterrows()):
                if tk is None and fnum(row["high"]) >= entry * (1 + target):
                    tk = k
                if sk is None and fnum(row["low"]) <= entry * (1 - stop):
                    sk = k
            # same-bar target/stop is conservatively treated as not target-first
            rec["t%d_before_s%d_5d" % (round(target*1000),round(stop*1000))] = (
                tk is not None and (sk is None or tk < sk)
            )

    # RSI recovery exits from the trigger trough, measured dynamically.
    loss = max(peak_rsi - rr, 1e-9)
    f = h.iloc[start:min(len(h), start + 31)]
    for frac in (0.50,0.75,1.00):
        target_rsi = rr + loss * frac
        key = "rsi_recover_%d" % round(frac*100)
        rec[key+"_found"] = False
        for _, row in f.iterrows():
            if fnum(row["rsi14"]) >= target_rsi:
                rec[key+"_found"] = True
                rec[key+"_return"] = fnum(row["close"]) / entry - 1
                break

    # MA-aware entry timing. We test whether RSI leads price out of the hole,
    # whether price reclaim alone is enough, and whether requiring both is
    # worth the delay. These are evaluated after the original flush signal so
    # they cannot leak future information into the trigger.
    trigger_px = fnum(h.iloc[loc]["close"])
    for key, predicate in (
        ("entry_rsi_sma5_cross", lambda row: bool(row["rsi_cross_up_sma5"])),
        ("entry_price_ema9_reclaim", lambda row: bool(row["price_cross_up_ema9"])),
        ("entry_dual_reclaim", lambda row: bool(row["rsi_cross_up_sma5"]) and fnum(row["close"]) > fnum(row["ema9"])),
        ("entry_rsi_leads_price", lambda row: bool(row["rsi_cross_up_sma5"]) and fnum(row["close"]) < fnum(row["ema9"])),
    ):
        rec[key+"_found"] = False
        for j in range(loc+1, min(len(h), loc+7)):
            row = h.iloc[j]
            if predicate(row) and fnum(row["close"]) <= trigger_px * 1.08:
                px = fnum(row["close"])
                rec[key+"_found"] = True
                rec[key+"_bars"] = j-loc
                rec[key+"_return_at_entry"] = px/entry - 1
                fut = h.iloc[j+1:min(len(h), j+1+30)]
                if not fut.empty and px > 0:
                    mfe = fnum(fut["high"].max()/px - 1)
                    mae = fnum(fut["low"].min()/px - 1)
                    rec[key+"_mfe5d"] = mfe
                    rec[key+"_mae5d"] = mae
                    rec[key+"_hit5_5d"] = mfe >= 0.05
                    rec[key+"_hit10_5d"] = mfe >= 0.10
                break

    # MA-aware exits. Exit only after the rebound has actually developed:
    # 1) RSI turns back under its own SMA5 after having recovered >=50% of the
    #    original RSI loss; 2) price loses EMA9 after first reclaiming it;
    # 3) both happen on the same/adjacent bar.
    recovered50 = False
    price_reclaimed = False
    rsi_down_j = None
    price_down_j = None
    for j in range(start, min(len(h), start+31)):
        row = h.iloc[j]
        if fnum(row["rsi14"]) >= rr + loss*0.50:
            recovered50 = True
        if fnum(row["close"]) > fnum(row["ema9"]):
            price_reclaimed = True
        if recovered50 and rsi_down_j is None and bool(row["rsi_cross_down_sma5"]):
            rsi_down_j = j
        if price_reclaimed and price_down_j is None and bool(row["price_cross_down_ema9"]):
            price_down_j = j

    rec["exit_rsi_sma5_turn_found"] = rsi_down_j is not None
    if rsi_down_j is not None:
        rec["exit_rsi_sma5_turn_return"] = fnum(h.iloc[rsi_down_j]["close"])/entry - 1
        rec["exit_rsi_sma5_turn_bars"] = rsi_down_j-start
    rec["exit_price_ema9_loss_found"] = price_down_j is not None
    if price_down_j is not None:
        rec["exit_price_ema9_loss_return"] = fnum(h.iloc[price_down_j]["close"])/entry - 1
        rec["exit_price_ema9_loss_bars"] = price_down_j-start
    dual_candidates = [j for j in (rsi_down_j, price_down_j) if j is not None]
    rec["exit_dual_turn_found"] = len(dual_candidates)==2 and abs(rsi_down_j-price_down_j) <= 1
    if rec["exit_dual_turn_found"]:
        j = max(rsi_down_j, price_down_j)
        rec["exit_dual_turn_return"] = fnum(h.iloc[j]["close"])/entry - 1
        rec["exit_dual_turn_bars"] = j-start
    return rec


def rate(s):
    x = s.dropna()
    return float(x.astype(bool).mean()) if len(x) else np.nan


def cluster_episodes(df, hours=18):
    x = df.sort_values("trigger_time").copy()
    episode = 0
    last = None
    ids = []
    for t in pd.to_datetime(x["trigger_time"], utc=True):
        if last is None or (t-last) > pd.Timedelta(hours=hours):
            episode += 1
        ids.append(episode)
        last = t
    x["episode_id"] = ids
    return x


def summary_rows(events):
    rows = []
    for th, g in events.groupby("flush_threshold"):
        g = g.sort_values("entry_time")
        cut = max(1, int(len(g)*0.60))
        tr, ho = g.iloc[:cut], g.iloc[cut:]
        ep = cluster_episodes(ho) if len(ho) else ho
        ep_rates = ep.groupby("episode_id")["hit5_5d"].mean() if len(ep) else pd.Series(dtype=float)
        rows.append({
            "flush_threshold": th,
            "n": len(g),
            "symbols": g["display_name"].nunique(),
            "train_n": len(tr),
            "hold_n": len(ho),
            "hold_hit5_36h": rate(ho["hit5_36h"]) if len(ho) else np.nan,
            "hold_hit5_5d": rate(ho["hit5_5d"]) if len(ho) else np.nan,
            "hold_hit10_5d": rate(ho["hit10_5d"]) if len(ho) else np.nan,
            "hold_median_mfe5d": fnum(ho["mfe_5d"].median()) if len(ho) else np.nan,
            "hold_median_mae5d": fnum(ho["mae_5d"].median()) if len(ho) else np.nan,
            "hold_episodes": int(ep["episode_id"].nunique()) if len(ep) else 0,
            "episode_mean_hit5_fraction": fnum(ep_rates.mean()) if len(ep_rates) else np.nan,
        })
    return pd.DataFrame(rows)


def rule_search(events):
    feats = [
        ("rsi_pct1","low"),("rsi_pct3","low"),("rsi_accel","low"),
        ("rsi_drop_pct","low"),("rsi_price_shock_ratio","mid"),
        ("daily_4h_rsi_gap","high"),("rsi_to_daily_ratio","low"),
        ("volume_ratio20","high"),("range_ratio20","high"),
        ("lower_wick_pct_range","high"),("close_location","low"),
        ("dist_ema20","low"),("dist_ema9","low"),("dist_sma9","low"),
        ("dist_sma20","low"),("sma9_slope3","low"),
        ("rsi_vs_sma3","low"),("rsi_vs_sma5","low"),("rsi_vs_sma9","low"),
        ("rsi_sma5_slope1","low"),("rsi_sma5_slope3","low"),("dual_stretch_9","low"),
    ]
    all_rows = []
    for th, base in events.groupby("flush_threshold"):
        base = base.sort_values("entry_time").reset_index(drop=True)
        cut = max(1, int(len(base)*0.60))
        train, hold = base.iloc[:cut], base.iloc[cut:]
        singles = []
        for feat,direction in feats:
            clean = train[feat].replace([np.inf,-np.inf],np.nan).dropna()
            if len(clean) < 30:
                continue
            if direction == "mid":
                # band rules to catch the non-linear RSI/price shock sweet spot.
                qs = clean.quantile([0.2,0.4,0.6,0.8]).to_dict()
                bands = [(qs[0.2],qs[0.6]),(qs[0.2],qs[0.8]),(qs[0.4],qs[0.8])]
                for lo,hi in bands:
                    mt = train[feat].between(lo,hi,inclusive="both")
                    mh = hold[feat].between(lo,hi,inclusive="both")
                    gt,gh = train[mt],hold[mh]
                    if len(gt) < 20 or len(gh) < 8:
                        continue
                    singles.append((feat,"band",lo,hi,{
                        "flush_threshold":th,"rule":"%.5f <= %s <= %.5f"%(lo,feat,hi),
                        "features":feat,"train_n":len(gt),"hold_n":len(gh),
                        "train_hit5_5d":rate(gt["hit5_5d"]),"train_hit10_5d":rate(gt["hit10_5d"]),
                        "hold_hit5_5d":rate(gh["hit5_5d"]),"hold_hit10_5d":rate(gh["hit10_5d"]),
                        "hold_median_mfe5d":fnum(gh["mfe_5d"].median()),
                        "hold_median_mae5d":fnum(gh["mae_5d"].median()),
                    }))
            else:
                for q in (0.20,0.33,0.50,0.67,0.80):
                    v = fnum(clean.quantile(q))
                    if direction == "low":
                        mt, mh, op = train[feat] <= v, hold[feat] <= v, "<="
                    else:
                        mt, mh, op = train[feat] >= v, hold[feat] >= v, ">="
                    gt,gh = train[mt],hold[mh]
                    if len(gt) < 20 or len(gh) < 8:
                        continue
                    singles.append((feat,op,v,None,{
                        "flush_threshold":th,"rule":"%s %s %.5f"%(feat,op,v),
                        "features":feat,"train_n":len(gt),"hold_n":len(gh),
                        "train_hit5_5d":rate(gt["hit5_5d"]),"train_hit10_5d":rate(gt["hit10_5d"]),
                        "hold_hit5_5d":rate(gh["hit5_5d"]),"hold_hit10_5d":rate(gh["hit10_5d"]),
                        "hold_median_mfe5d":fnum(gh["mfe_5d"].median()),
                        "hold_median_mae5d":fnum(gh["mae_5d"].median()),
                    }))
        # select pairwise ingredients by TRAIN only
        singles_sorted = sorted(singles,key=lambda x:(x[4]["train_hit5_5d"],x[4]["train_hit10_5d"],x[4]["train_n"]),reverse=True)
        for s in singles:
            all_rows.append(s[4])
        top = singles_sorted[:20]
        for a,b in itertools.combinations(top,2):
            if a[0] == b[0]:
                continue
            def mask(df,s):
                feat,op,v1,v2,_ = s
                if op == "band":
                    return df[feat].between(v1,v2,inclusive="both")
                return df[feat] <= v1 if op == "<=" else df[feat] >= v1
            mt, mh = mask(train,a)&mask(train,b), mask(hold,a)&mask(hold,b)
            gt,gh = train[mt],hold[mh]
            if len(gt) < 20 or len(gh) < 8:
                continue
            all_rows.append({
                "flush_threshold":th,"rule":a[4]["rule"]+" AND "+b[4]["rule"],
                "features":a[0]+"+"+b[0],"train_n":len(gt),"hold_n":len(gh),
                "train_hit5_5d":rate(gt["hit5_5d"]),"train_hit10_5d":rate(gt["hit10_5d"]),
                "hold_hit5_5d":rate(gh["hit5_5d"]),"hold_hit10_5d":rate(gh["hit10_5d"]),
                "hold_median_mfe5d":fnum(gh["mfe_5d"].median()),
                "hold_median_mae5d":fnum(gh["mae_5d"].median()),
            })
    z = pd.DataFrame(all_rows)
    if z.empty:
        return z
    z["train_score"] = z["train_hit5_5d"]*0.7 + z["train_hit10_5d"]*0.3
    return z.sort_values(["train_score","train_n"],ascending=[False,False]).reset_index(drop=True)


def bucket_report(events):
    rows = []
    feats = ["rsi_pct1","rsi_pct3","rsi_accel","rsi_drop_pct","rsi_price_shock_ratio",
             "daily_4h_rsi_gap","rsi_to_daily_ratio","volume_ratio20","range_ratio20",
             "lower_wick_pct_range","close_location","dist_ema20","dist_ema9",
             "dist_sma9","dist_sma20","sma9_slope3","rsi_vs_sma3","rsi_vs_sma5",
             "rsi_vs_sma9","rsi_sma5_slope1","rsi_sma5_slope3","dual_stretch_9"]
    for feat in feats:
        x = events[[feat,"hit5_5d","hit10_5d","mfe_5d","mae_5d"]].replace([np.inf,-np.inf],np.nan).dropna()
        if len(x) < 40:
            continue
        try:
            x["bucket"] = pd.qcut(x[feat],4,duplicates="drop")
        except Exception:
            continue
        for b,g in x.groupby("bucket",observed=True):
            rows.append({
                "feature":feat,"bucket":str(b),"n":len(g),"median":fnum(g[feat].median()),
                "hit5_5d":rate(g["hit5_5d"]),"hit10_5d":rate(g["hit10_5d"]),
                "median_mfe5d":fnum(g["mfe_5d"].median()),"median_mae5d":fnum(g["mae_5d"].median()),
            })
    return pd.DataFrame(rows)


def best_per_episode(events):
    # Cross-sectional portfolio test: among simultaneous signals, select the
    # strongest RSI velocity shock rather than counting every correlated coin.
    x = cluster_episodes(events)
    rows = []
    for eid,g in x.groupby("episode_id"):
        g = g.dropna(subset=["rsi_pct1"])
        if g.empty:
            continue
        pick = g.sort_values("rsi_pct1").iloc[0]
        rows.append(pick)
    return pd.DataFrame(rows)


def exit_report(events):
    rows = []
    for key,label in [
        ("rsi_recover_50","Recover 50% RSI loss"),
        ("rsi_recover_75","Recover 75% RSI loss"),
        ("rsi_recover_100","Recover 100% RSI loss"),
        ("exit_rsi_sma5_turn","RSI crosses below RSI-SMA5 after >=50% recovery"),
        ("exit_price_ema9_loss","Price loses EMA9 after reclaim"),
        ("exit_dual_turn","RSI-SMA5 turn + price EMA9 loss within 1 bar"),
    ]:
        found_col = key+"_found"
        ret_col = key+"_return"
        if found_col not in events.columns:
            continue
        g = events[events[found_col] == True]
        ret = g[ret_col] if len(g) and ret_col in g.columns else pd.Series(dtype=float)
        rows.append({
            "exit":label,"n":len(g),"coverage":len(g)/len(events) if len(events) else np.nan,
            "positive_rate":float((ret>0).mean()) if len(ret) else np.nan,
            "return_ge_5":float((ret>=0.05).mean()) if len(ret) else np.nan,
            "return_ge_10":float((ret>=0.10).mean()) if len(ret) else np.nan,
            "median_return":fnum(ret.median()) if len(ret) else np.nan,
            "mean_return":fnum(ret.mean()) if len(ret) else np.nan,
        })
    return pd.DataFrame(rows)


def entry_report(events):
    rows = []
    for key,label in [
        ("entry_rsi_sma5_cross","RSI crosses above RSI-SMA5"),
        ("entry_price_ema9_reclaim","Price reclaims EMA9"),
        ("entry_dual_reclaim","RSI-SMA5 cross + price above EMA9"),
        ("entry_rsi_leads_price","RSI-SMA5 cross while price still below EMA9"),
    ]:
        found = key+"_found"
        if found not in events.columns:
            continue
        g = events[events[found] == True]
        rows.append({
            "entry":label,
            "n":len(g),
            "coverage":len(g)/len(events) if len(events) else np.nan,
            "median_delay_bars":fnum(g[key+"_bars"].median()) if len(g) and key+"_bars" in g else np.nan,
            "median_price_move_before_entry":fnum(g[key+"_return_at_entry"].median()) if len(g) and key+"_return_at_entry" in g else np.nan,
            "hit5_5d":rate(g[key+"_hit5_5d"]) if len(g) and key+"_hit5_5d" in g else np.nan,
            "hit10_5d":rate(g[key+"_hit10_5d"]) if len(g) and key+"_hit10_5d" in g else np.nan,
            "median_mfe5d":fnum(g[key+"_mfe5d"].median()) if len(g) and key+"_mfe5d" in g else np.nan,
            "median_mae5d":fnum(g[key+"_mae5d"].median()) if len(g) and key+"_mae5d" in g else np.nan,
        })
    return pd.DataFrame(rows)


def scan(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "appwiza-hyperliquid-research/1.0"
    universe = enumerate_universe(session)
    selected = [x for i,x in enumerate(universe) if i % args.shard_count == args.shard_index]
    end = now_utc()
    start = end - timedelta(days=args.days)

    pd.DataFrame(selected).to_csv(out / ("universe_shard_%02d.csv" % args.shard_index), index=False)
    events, coverage = [], []
    for j, meta in enumerate(selected,1):
        coin = meta["api_coin"]
        try:
            h = fetch_4h(session, coin, start, end)
            if len(h) < 180:
                coverage.append({**meta,"status":"short","candles":len(h),"events":0})
                print("%d/%d %s short %d" % (j,len(selected),meta["display_name"],len(h)))
                time.sleep(args.sleep_seconds)
                continue
            h = prep4h(h)
            d = daily_from_4h(h)
            arms = setup_events(d)
            n = 0
            for arm in arms:
                for th in (0.08,0.10,0.12):
                    idx = first_flush(h,arm,th)
                    if idx is None:
                        continue
                    rec = build_event(h,d,arm,idx,th,meta)
                    if rec:
                        events.append(rec)
                        n += 1
            coverage.append({**meta,"status":"ok","candles":len(h),"events":n})
            print("%d/%d %s candles=%d setups=%d" % (j,len(selected),meta["display_name"],len(h),n))
        except Exception as exc:
            coverage.append({**meta,"status":"error","error":str(exc)[:180],"candles":0,"events":0})
            print("ERROR %s %s" % (meta["display_name"],exc),file=sys.stderr)
        time.sleep(args.sleep_seconds)

    pd.DataFrame(coverage).to_csv(out / ("coverage_shard_%02d.csv" % args.shard_index), index=False)
    pd.DataFrame(events).to_csv(out / ("events_shard_%02d.csv" % args.shard_index), index=False)


def aggregate(args):
    root = Path(args.input_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def concat(pattern):
        frames = []
        for p in sorted(root.rglob(pattern)):
            try:
                frames.append(pd.read_csv(p))
            except Exception:
                pass
        return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()

    universe = concat("universe_shard_*.csv")
    coverage = concat("coverage_shard_*.csv")
    events = concat("events_shard_*.csv")
    for col in ("arm_time","trigger_time","entry_time"):
        if col in events:
            events[col] = pd.to_datetime(events[col],utc=True,errors="coerce")

    universe.to_csv(out/"universe.csv",index=False)
    coverage.to_csv(out/"coverage.csv",index=False)
    events.to_csv(out/"events.csv",index=False)
    if events.empty:
        (out/"REPORT.txt").write_text("No Hyperliquid events produced.\n")
        raise SystemExit(2)

    base = summary_rows(events)
    base.to_csv(out/"baseline_by_trigger.csv",index=False)
    rules = rule_search(events)
    rules.to_csv(out/"rules_train_holdout.csv",index=False)
    buckets = bucket_report(events)
    buckets.to_csv(out/"feature_buckets.csv",index=False)
    exits = exit_report(events)
    exits.to_csv(out/"rsi_exit_rules.csv",index=False)
    entries = entry_report(events)
    entries.to_csv(out/"ma_entry_rules.csv",index=False)

    # Correlation-aware portfolio-style view on 8% trigger, which fires first.
    e8 = events[events["flush_threshold"]==0.08].copy()
    picks = best_per_episode(e8)
    picks.to_csv(out/"best_rsi_velocity_signal_per_episode.csv",index=False)
    pick_summary = {
        "n":len(picks),
        "hit5_5d":rate(picks["hit5_5d"]) if len(picks) else np.nan,
        "hit10_5d":rate(picks["hit10_5d"]) if len(picks) else np.nan,
        "median_mfe5d":fnum(picks["mfe_5d"].median()) if len(picks) else np.nan,
        "median_mae5d":fnum(picks["mae_5d"].median()) if len(picks) else np.nan,
    }

    kinds = universe.groupby(["kind","dex"]).size().reset_index(name="pairs") if len(universe) else pd.DataFrame()
    ok = coverage[coverage["status"]=="ok"] if len(coverage) else coverage
    top = rules.head(20) if len(rules) else rules
    lines = [
        "HYPERLIQUID ALL-PAIR DAILY + 4H RSI-DYNAMICS RESEARCH",
        "",
        "Generated: "+datetime.now(timezone.utc).isoformat(),
        "Universe instruments enumerated: %d" % len(universe),
        "Successfully scanned: %d" % len(ok),
        "Research events: %d" % len(events),
        "",
        "UNIVERSE BREAKDOWN",
        kinds.to_string(index=False) if len(kinds) else "none",
        "",
        "BASELINE BY FIRST-FLUSH TRIGGER",
        base.to_string(index=False),
        "",
        "TOP TRAIN-SELECTED RULES / LATER 40% HOLDOUT",
        top.to_string(index=False) if len(top) else "none",
        "",
        "CORRELATION-AWARE: strongest 1-bar RSI collapse per 18h market episode (8% trigger)",
        str(pick_summary),
        "",
        "MA / RSI-SMA ENTRY TIMING",
        entries.to_string(index=False),
        "",
        "RSI + PRICE-MA EXITS",
        exits.to_string(index=False),
        "",
        "Guardrails:",
        "- Each trigger threshold is evaluated separately so an 8/10/12% threshold does not inflate its own N with duplicates.",
        "- Rules are selected on the earliest 60% only; the later 40% is holdout.",
        "- Episode clustering reduces the illusion of independence when many pairs flush in the same market event.",
        "- Current day volume/open interest/funding are inventory metadata only, not used as historical predictors.",
        "- Candle history comes from Hyperliquid's public 4H candleSnapshot and daily bars are aggregated from those same 4H candles.",
    ]
    (out/"REPORT.txt").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["scan","aggregate"],default="scan")
    ap.add_argument("--days",type=int,default=600)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--sleep-seconds",type=float,default=4.2)
    ap.add_argument("--out",default="hyperliquid_out")
    ap.add_argument("--input-dir",default="hyperliquid_shards")
    args = ap.parse_args()
    if args.mode == "scan":
        scan(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
