#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np

SRC=Path("crypto/research/results_hyperliquid_all_pairs/events.csv")
OUT=Path("crypto/research/results_monthly_profit_examples")
OUT.mkdir(parents=True,exist_ok=True)

e=pd.read_csv(SRC)
for c in ["arm_time","trigger_time","entry_time"]:
    e[c]=pd.to_datetime(e[c],utc=True,errors="coerce")

# Unique physical setups only, primary Hyperliquid perps.
key=["kind","dex","display_name","arm_time"]
e=e.sort_values(key+["flush_threshold","trigger_time"]).drop_duplicates(key,keep="first")
e=e[(e["kind"]=="perp")&(e["dex"]=="primary")].sort_values("entry_time").reset_index(drop=True)

# Same 60/40 chronological split used in the validation.
cut=max(1,int(len(e)*0.60))
hold=e.iloc[cut:].copy()

rules={
    "A_EMA9_RSIacc": (hold["dist_ema9"]<=-0.03649)&(hold["rsi_accel"]<=-7.63088),
    "B_RSIcrash_SMA9slope": (hold["rsi_pct1"]<=-0.21182)&(hold["sma9_slope3"]<=0.02604),
    "C_RSIcrash_SMA9stretch": (hold["rsi_pct1"]<=-0.21182)&(hold["dist_sma9"]<=-0.03517),
    "D_RSI_SMA5slope_RSIcrash": (hold["rsi_sma5_slope1"]<=-0.05915)&(hold["rsi_pct1"]<=-0.17635),
    "E_EMA9_lowclose": (hold["dist_ema9"]<=-0.03649)&(hold["close_location"]<=0.16592),
}
# Ensemble: at least 2 of 5 high-probability rules agree.
votes=pd.DataFrame({k:v.astype(int) for k,v in rules.items()},index=hold.index)
rules["F_ensemble_2of5"]=(votes.sum(axis=1)>=2)
rules["G_ensemble_3of5"]=(votes.sum(axis=1)>=3)

ENTRY=1000.0
TARGET=0.05
STOP=0.075

# Conservative all-in cost stress used earlier:
# 0.045% taker/side + 0.10% slippage/side + median 5d funding 0.1541%.
# Apply full median 5d funding to every trade (intentionally conservative).
round_trip_cost=2*(0.00045+0.0010)+0.001541
win_net=ENTRY*(TARGET-round_trip_cost)
loss_net=-ENTRY*(STOP+round_trip_cost)

rows=[]
summary=[]
for name,m in rules.items():
    g=hold[m].copy()
    if g.empty: continue
    g["month"]=g["entry_time"].dt.to_period("M").astype(str)
    # ex-ante historical outcome: +5% before -7.5%. For a simple fixed target/stop
    # illustration, every success is booked at target and every non-success is
    # conservatively treated as a full stop loss.
    g["success"]=g["t50_before_s75_5d"].fillna(False).astype(bool)
    g["gross_pnl"]=np.where(g["success"],ENTRY*TARGET,-ENTRY*STOP)
    g["net_pnl_stress"]=np.where(g["success"],win_net,loss_net)
    monthly=g.groupby("month").agg(trades=("success","size"),wins=("success","sum"),
                                   gross_pnl=("gross_pnl","sum"),
                                   net_pnl_stress=("net_pnl_stress","sum")).reset_index()
    monthly["losses"]=monthly["trades"]-monthly["wins"]
    monthly["win_rate"]=monthly["wins"]/monthly["trades"]
    monthly["ending_bankroll_if_month_starts_10k"]=10000+monthly["net_pnl_stress"]
    monthly["rule"]=name
    rows.append(monthly)
    summary.append({
        "rule":name,"holdout_trades":len(g),"wins":int(g["success"].sum()),
        "win_rate":g["success"].mean(),
        "months_with_signals":monthly["month"].nunique(),
        "avg_trades_per_active_month":monthly["trades"].mean(),
        "median_trades_per_active_month":monthly["trades"].median(),
        "avg_net_profit_per_active_month":monthly["net_pnl_stress"].mean(),
        "median_net_profit_per_active_month":monthly["net_pnl_stress"].median(),
        "best_month_net":monthly["net_pnl_stress"].max(),
        "worst_month_net":monthly["net_pnl_stress"].min(),
        "total_net_profit_holdout":monthly["net_pnl_stress"].sum(),
        "ending_bankroll_from_10k_fixed_1k":10000+monthly["net_pnl_stress"].sum(),
    })

allm=pd.concat(rows,ignore_index=True)
allm.to_csv(OUT/"monthly_by_rule.csv",index=False)
s=pd.DataFrame(summary).sort_values(["win_rate","holdout_trades"],ascending=[False,False])
s.to_csv(OUT/"summary.csv",index=False)

lines=[
"MONTHLY PROFIT EXAMPLES — HYPERLIQUID PRIMARY PERP HOLDOUT",
"",
"Assumptions:",
"- Starting bankroll: 10,000 USDC",
"- Fixed position: 1,000 USDC per trade; no compounding",
"- Take profit: +5%",
"- Stop: -7.5%",
"- Uses later 40% chronological holdout only",
"- Every non-win is conservatively charged as a full -7.5% stop",
"- Cost-stress per trade: 0.045% taker/side + 0.10% slippage/side + full median 5d funding 0.1541%",
f"- Net win used: {win_net:.2f} USDC; net loss used: {loss_net:.2f} USDC",
"",
"SUMMARY",
s.to_string(index=False),
"",
"MONTHLY DETAIL",
allm.sort_values(["rule","month"]).to_string(index=False),
"",
"Important: this is a fixed-payoff illustration, not an exact execution replay. It is intentionally conservative on non-winners and funding, but it does not yet model order-book depth, partial fills, simultaneous-position capacity, or exact time-to-exit."
]
(OUT/"REPORT.txt").write_text("\n".join(lines)+"\n")
print((OUT/"REPORT.txt").read_text())
