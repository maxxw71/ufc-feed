#!/usr/bin/env python3
from pathlib import Path

def replace_once(path, old, new):
    p=Path(path)
    text=p.read_text()
    if new in text:
        print(path, "already patched")
        return False
    if old not in text:
        raise SystemExit(f"anchor missing in {path}")
    p.write_text(text.replace(old,new,1))
    print(path, "patched")
    return True

HL="crypto/research/hyperliquid_all_pairs_rsi.py"
BIN="crypto/research/rsi_dynamics_rebound.py"
AUTO="crypto/research/auto_method_discovery.py"

old_hl='''    x["volume_med20"] = x["volume"].rolling(20, min_periods=10).median()
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0, np.nan)
    x["range_med20"] = x["range_pct"].rolling(20, min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0, np.nan)
'''
new_hl='''    # Participation / volume / flow features.
    x["volume_med5"] = x["volume"].rolling(5, min_periods=3).median()
    x["volume_med20"] = x["volume"].rolling(20, min_periods=10).median()
    x["volume_mean20"] = x["volume"].rolling(20, min_periods=10).mean()
    x["volume_std20"] = x["volume"].rolling(20, min_periods=10).std()
    x["volume_ratio5"] = x["volume"] / x["volume_med5"].replace(0, np.nan)
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0, np.nan)
    x["volume_z20"] = (x["volume"] - x["volume_mean20"]) / x["volume_std20"].replace(0, np.nan)
    x["volume_pct1"] = x["volume"].pct_change().replace([np.inf,-np.inf], np.nan)
    x["volume_pct3"] = (x["volume"] / x["volume"].shift(3) - 1).replace([np.inf,-np.inf], np.nan)
    typical = (x["high"] + x["low"] + x["close"]) / 3.0
    x["quote_volume_est"] = x["volume"] * typical
    x["quote_volume_med20"] = x["quote_volume_est"].rolling(20, min_periods=10).median()
    x["quote_volume_ratio20"] = x["quote_volume_est"] / x["quote_volume_med20"].replace(0, np.nan)
    x["trades"] = pd.to_numeric(x["trades"], errors="coerce")
    x["trades_med20"] = x["trades"].rolling(20, min_periods=10).median()
    x["trades_ratio20"] = x["trades"] / x["trades_med20"].replace(0, np.nan)
    x["avg_trade_quote"] = x["quote_volume_est"] / x["trades"].replace(0, np.nan)
    x["avg_trade_quote_med20"] = x["avg_trade_quote"].rolling(20, min_periods=10).median()
    x["avg_trade_size_ratio20"] = x["avg_trade_quote"] / x["avg_trade_quote_med20"].replace(0, np.nan)
    direction = np.sign(x["close"].diff()).fillna(0)
    x["obv"] = (direction * x["volume"].fillna(0)).cumsum()
    x["obv_delta3_norm"] = x["obv"].diff(3) / x["volume"].rolling(20, min_periods=10).sum().replace(0, np.nan)
    mfm = ((x["close"]-x["low"]) - (x["high"]-x["close"])) / (x["high"]-x["low"]).replace(0,np.nan)
    x["cmf20"] = (mfm*x["volume"]).rolling(20,min_periods=10).sum() / x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["vwap20"] = (typical*x["volume"]).rolling(20,min_periods=10).sum() / x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["dist_vwap20"] = x["close"] / x["vwap20"] - 1
    x["rv6_4h"] = x["price_pct1"].rolling(6,min_periods=4).std()
    x["rv20_4h"] = x["price_pct1"].rolling(20,min_periods=10).std()
    x["rv_ratio_6_20"] = x["rv6_4h"] / x["rv20_4h"].replace(0,np.nan)
    x["atr14_pct"] = (x["high"]-x["low"]).rolling(14,min_periods=8).mean() / x["close"].replace(0,np.nan)
    x["range_med20"] = x["range_pct"].rolling(20, min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0, np.nan)
'''
replace_once(HL,old_hl,new_hl)

old_hl_rec='''        "volume_ratio20": fnum(h.iloc[loc]["volume_ratio20"]),
        "range_ratio20": fnum(h.iloc[loc]["range_ratio20"]),
'''
new_hl_rec='''        "volume_ratio5": fnum(h.iloc[loc]["volume_ratio5"]),
        "volume_ratio20": fnum(h.iloc[loc]["volume_ratio20"]),
        "volume_z20": fnum(h.iloc[loc]["volume_z20"]),
        "volume_pct1": fnum(h.iloc[loc]["volume_pct1"]),
        "volume_pct3": fnum(h.iloc[loc]["volume_pct3"]),
        "quote_volume_ratio20": fnum(h.iloc[loc]["quote_volume_ratio20"]),
        "trades_ratio20": fnum(h.iloc[loc]["trades_ratio20"]),
        "avg_trade_size_ratio20": fnum(h.iloc[loc]["avg_trade_size_ratio20"]),
        "obv_delta3_norm": fnum(h.iloc[loc]["obv_delta3_norm"]),
        "cmf20": fnum(h.iloc[loc]["cmf20"]),
        "dist_vwap20": fnum(h.iloc[loc]["dist_vwap20"]),
        "rv6_4h": fnum(h.iloc[loc]["rv6_4h"]),
        "rv20_4h": fnum(h.iloc[loc]["rv20_4h"]),
        "rv_ratio_6_20": fnum(h.iloc[loc]["rv_ratio_6_20"]),
        "atr14_pct": fnum(h.iloc[loc]["atr14_pct"]),
        "range_ratio20": fnum(h.iloc[loc]["range_ratio20"]),
'''
replace_once(HL,old_hl_rec,new_hl_rec)

old_bin='''    x["volume_med20"] = x["volume"].rolling(20,min_periods=10).median()
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0,np.nan)
    x["range_med20"] = x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0,np.nan)
'''
new_bin='''    # Participation / volume / order-flow features.
    for col in ["trades","taker_base","taker_quote"]:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x["volume_med5"] = x["volume"].rolling(5,min_periods=3).median()
    x["volume_med20"] = x["volume"].rolling(20,min_periods=10).median()
    x["volume_mean20"] = x["volume"].rolling(20,min_periods=10).mean()
    x["volume_std20"] = x["volume"].rolling(20,min_periods=10).std()
    x["volume_ratio5"] = x["volume"] / x["volume_med5"].replace(0,np.nan)
    x["volume_ratio20"] = x["volume"] / x["volume_med20"].replace(0,np.nan)
    x["volume_z20"] = (x["volume"]-x["volume_mean20"]) / x["volume_std20"].replace(0,np.nan)
    x["volume_pct1"] = x["volume"].pct_change().replace([np.inf,-np.inf],np.nan)
    x["volume_pct3"] = (x["volume"]/x["volume"].shift(3)-1).replace([np.inf,-np.inf],np.nan)
    x["quote_volume_med20"] = x["quote_volume"].rolling(20,min_periods=10).median()
    x["quote_volume_ratio20"] = x["quote_volume"] / x["quote_volume_med20"].replace(0,np.nan)
    x["trades_med20"] = x["trades"].rolling(20,min_periods=10).median()
    x["trades_ratio20"] = x["trades"] / x["trades_med20"].replace(0,np.nan)
    x["avg_trade_quote"] = x["quote_volume"] / x["trades"].replace(0,np.nan)
    x["avg_trade_quote_med20"] = x["avg_trade_quote"].rolling(20,min_periods=10).median()
    x["avg_trade_size_ratio20"] = x["avg_trade_quote"] / x["avg_trade_quote_med20"].replace(0,np.nan)
    x["taker_buy_ratio"] = x["taker_quote"] / x["quote_volume"].replace(0,np.nan)
    x["taker_buy_imbalance"] = 2*x["taker_buy_ratio"] - 1
    direction = np.sign(x["close"].diff()).fillna(0)
    x["obv"] = (direction*x["volume"].fillna(0)).cumsum()
    x["obv_delta3_norm"] = x["obv"].diff(3) / x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    mfm=((x["close"]-x["low"])-(x["high"]-x["close"]))/(x["high"]-x["low"]).replace(0,np.nan)
    x["cmf20"]=(mfm*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    typical=(x["high"]+x["low"]+x["close"])/3.0
    x["vwap20"]=(typical*x["volume"]).rolling(20,min_periods=10).sum()/x["volume"].rolling(20,min_periods=10).sum().replace(0,np.nan)
    x["dist_vwap20"]=x["close"]/x["vwap20"]-1
    x["rv6_4h"]=x["price_pct1"].rolling(6,min_periods=4).std()
    x["rv20_4h"]=x["price_pct1"].rolling(20,min_periods=10).std()
    x["rv_ratio_6_20"]=x["rv6_4h"]/x["rv20_4h"].replace(0,np.nan)
    x["atr14_pct"]=(x["high"]-x["low"]).rolling(14,min_periods=8).mean()/x["close"].replace(0,np.nan)
    x["range_med20"] = x["range_pct"].rolling(20,min_periods=10).median()
    x["range_ratio20"] = x["range_pct"] / x["range_med20"].replace(0,np.nan)
'''
replace_once(BIN,old_bin,new_bin)

old_bin_rec='''        "volume_ratio20": float(h.iloc[loc]["volume_ratio20"]) if pd.notna(h.iloc[loc]["volume_ratio20"]) else np.nan,
        "range_ratio20": float(h.iloc[loc]["range_ratio20"]) if pd.notna(h.iloc[loc]["range_ratio20"]) else np.nan,
'''
new_bin_rec='''        "volume_ratio5": float(h.iloc[loc]["volume_ratio5"]) if pd.notna(h.iloc[loc]["volume_ratio5"]) else np.nan,
        "volume_ratio20": float(h.iloc[loc]["volume_ratio20"]) if pd.notna(h.iloc[loc]["volume_ratio20"]) else np.nan,
        "volume_z20": float(h.iloc[loc]["volume_z20"]) if pd.notna(h.iloc[loc]["volume_z20"]) else np.nan,
        "volume_pct1": float(h.iloc[loc]["volume_pct1"]) if pd.notna(h.iloc[loc]["volume_pct1"]) else np.nan,
        "volume_pct3": float(h.iloc[loc]["volume_pct3"]) if pd.notna(h.iloc[loc]["volume_pct3"]) else np.nan,
        "quote_volume_ratio20": float(h.iloc[loc]["quote_volume_ratio20"]) if pd.notna(h.iloc[loc]["quote_volume_ratio20"]) else np.nan,
        "trades_ratio20": float(h.iloc[loc]["trades_ratio20"]) if pd.notna(h.iloc[loc]["trades_ratio20"]) else np.nan,
        "avg_trade_size_ratio20": float(h.iloc[loc]["avg_trade_size_ratio20"]) if pd.notna(h.iloc[loc]["avg_trade_size_ratio20"]) else np.nan,
        "taker_buy_ratio": float(h.iloc[loc]["taker_buy_ratio"]) if pd.notna(h.iloc[loc]["taker_buy_ratio"]) else np.nan,
        "taker_buy_imbalance": float(h.iloc[loc]["taker_buy_imbalance"]) if pd.notna(h.iloc[loc]["taker_buy_imbalance"]) else np.nan,
        "obv_delta3_norm": float(h.iloc[loc]["obv_delta3_norm"]) if pd.notna(h.iloc[loc]["obv_delta3_norm"]) else np.nan,
        "cmf20": float(h.iloc[loc]["cmf20"]) if pd.notna(h.iloc[loc]["cmf20"]) else np.nan,
        "dist_vwap20": float(h.iloc[loc]["dist_vwap20"]) if pd.notna(h.iloc[loc]["dist_vwap20"]) else np.nan,
        "rv6_4h": float(h.iloc[loc]["rv6_4h"]) if pd.notna(h.iloc[loc]["rv6_4h"]) else np.nan,
        "rv20_4h": float(h.iloc[loc]["rv20_4h"]) if pd.notna(h.iloc[loc]["rv20_4h"]) else np.nan,
        "rv_ratio_6_20": float(h.iloc[loc]["rv_ratio_6_20"]) if pd.notna(h.iloc[loc]["rv_ratio_6_20"]) else np.nan,
        "atr14_pct": float(h.iloc[loc]["atr14_pct"]) if pd.notna(h.iloc[loc]["atr14_pct"]) else np.nan,
        "range_ratio20": float(h.iloc[loc]["range_ratio20"]) if pd.notna(h.iloc[loc]["range_ratio20"]) else np.nan,
'''
replace_once(BIN,old_bin_rec,new_bin_rec)

old_auto='''    ("volume_ratio20","high"),("range_ratio20","high"),
    ("lower_wick_pct_range","high"),("close_location","low"),
'''
new_auto='''    ("volume_ratio5","high"),("volume_ratio20","high"),("volume_z20","high"),
    ("volume_pct1","high"),("volume_pct3","high"),("quote_volume_ratio20","high"),
    ("trades_ratio20","high"),("avg_trade_size_ratio20","high"),
    ("obv_delta3_norm","low"),("cmf20","low"),("dist_vwap20","low"),
    ("rv_ratio_6_20","high"),("atr14_pct","high"),("range_ratio20","high"),
    ("lower_wick_pct_range","high"),("close_location","low"),
'''
replace_once(AUTO,old_auto,new_auto)

print("volume feature expansion complete")
