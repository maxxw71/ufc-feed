#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/new_category_discovery/prefight_favorite_features.csv"
OUT="$ROOT/striking_portfolio_100"
mkdir -p "$OUT"

[ -s "$IN" ] || { echo "ERROR: Missing $IN"; echo "Run the new-category discovery scan first."; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUT" <<'PY'
import sys
from pathlib import Path
import numpy as np
import pandas as pd

inp=Path(sys.argv[1]); out=Path(sys.argv[2])

def progress(p,msg):
    print(f"[{p:3d}%] {msg}", flush=True)

progress(5,"Loading historical pre-fight favorite table")
t=pd.read_csv(inp,low_memory=False)
t['event_date']=pd.to_datetime(t['event_date'],errors='coerce')
for c in t.columns:
    if c not in {'event_date','favorite','opponent'}:
        t[c]=pd.to_numeric(t[c],errors='coerce')
t=t.dropna(subset=['event_date','favorite','opponent','market_prob','fav_decimal','profit100']).copy()

# Stable rules chosen from the striking-family stability pass.
pace=(
    (t.market_prob>=0.65) &
    (t.f_fights>=3) & (t.o_fights>=3) &
    ((t.f_sig_l_pm-t.o_sig_l_pm)>=0.25) &
    ((t.f_sig_def-t.o_sig_def)>=0.15)
)

td=(
    (t.market_prob>=0.70) &
    (t.f_fights>=4) & (t.o_fights>=4) &
    ((t.f_sig_diff_pm-t.o_sig_diff_pm)>=1.00) &
    ((t.f_td_def-t.o_td_def)>=0.10)
)

diff=(
    (t.market_prob>=0.75) &
    (t.f_fights>=4) & (t.o_fights>=4) &
    ((t.f_sig_diff_pm-t.o_sig_diff_pm)>=1.00) &
    (t.f_sig_diff_pm>=0.50)
)

methods={
    'PACE + DEFENSE': pace,
    'STRIKING + TD DEFENSE': td,
    'STRIKING DIFFERENTIAL': diff,
}

progress(20,"Matching the three stable striking methods")

def american_from_decimal(d):
    if pd.isna(d) or d<=1: return np.nan
    return 100*(d-1) if d>=2 else -100/(d-1)

t['american_odds']=t['fav_decimal'].map(american_from_decimal)
t['american_odds_rounded']=t['american_odds'].round().astype('Int64')
t['stake']=100.0

t['pace_defense']=pace
t['striking_td_defense']=td
t['striking_differential']=diff

summary=[]
all_bets=[]
expected={'PACE + DEFENSE':53,'STRIKING + TD DEFENSE':54,'STRIKING DIFFERENTIAL':68}

for name,mask in methods.items():
    g=t[mask].copy().sort_values(['event_date','favorite','opponent']).reset_index(drop=True)
    g['method']=name
    g['bet_number']=np.arange(1,len(g)+1)
    g['cumulative_profit']=g['profit100'].cumsum()
    g['cumulative_staked']=100.0*g['bet_number']
    g['cumulative_roi']=g['cumulative_profit']/g['cumulative_staked']
    n=len(g); wins=int((g.profit100>0).sum()); losses=int((g.profit100<0).sum())
    profit=float(g.profit100.sum()); stake=100*n; roi=profit/stake if stake else np.nan
    summary.append({'method':name,'bets':n,'wins':wins,'losses':losses,'staked':stake,'profit':profit,'return':stake+profit,'roi':roi})
    all_bets.append(g)
    if n!=expected[name]:
        print(f"WARNING: {name} produced {n} bets; stability run previously showed {expected[name]}.")

progress(40,"Calculating strategy-by-strategy portfolio")
all_signals=pd.concat(all_bets,ignore_index=True).sort_values(['event_date','favorite','method']).reset_index(drop=True)
all_signals['portfolio_bet_number']=np.arange(1,len(all_signals)+1)
all_signals['portfolio_cumulative_profit']=all_signals['profit100'].cumsum()
all_signals['portfolio_cumulative_staked']=100.0*all_signals['portfolio_bet_number']
all_signals['portfolio_cumulative_roi']=all_signals['portfolio_cumulative_profit']/all_signals['portfolio_cumulative_staked']

sig_n=len(all_signals); sig_profit=float(all_signals.profit100.sum()); sig_stake=100*sig_n
sig_roi=sig_profit/sig_stake if sig_stake else np.nan

progress(55,"Calculating unique-fight $100-once portfolio")
anymask=pace|td|diff
u=t[anymask].copy().sort_values(['event_date','favorite','opponent']).reset_index(drop=True)

def labels(r):
    z=[]
    if r['pace_defense']: z.append('PACE + DEFENSE')
    if r['striking_td_defense']: z.append('STRIKING + TD DEFENSE')
    if r['striking_differential']: z.append('STRIKING DIFFERENTIAL')
    return ' + '.join(z)

u['methods']=u.apply(labels,axis=1)
u['num_methods']=u[['pace_defense','striking_td_defense','striking_differential']].sum(axis=1)
u['bet_number']=np.arange(1,len(u)+1)
u['cumulative_profit']=u['profit100'].cumsum()
u['cumulative_staked']=100.0*u['bet_number']
u['cumulative_roi']=u['cumulative_profit']/u['cumulative_staked']
uniq_n=len(u); uniq_profit=float(u.profit100.sum()); uniq_stake=100*uniq_n
uniq_roi=uniq_profit/uniq_stake if uniq_stake else np.nan

progress(68,"Calculating overlap groups")
over=[]
for label,g in u.groupby('methods'):
    n=len(g); p=float(g.profit100.sum()); s=100*n
    over.append({'methods':label,'unique_fights':n,'wins':int((g.profit100>0).sum()),'losses':int((g.profit100<0).sum()),'staked':s,'profit':p,'roi':p/s if s else np.nan})
over=pd.DataFrame(over).sort_values(['unique_fights','roi'],ascending=[False,False])

progress(78,"Calculating year-by-year portfolio results")
u['year']=u.event_date.dt.year
yearly=[]
for yr,g in u.groupby('year'):
    n=len(g); p=float(g.profit100.sum()); s=100*n
    yearly.append({'year':int(yr),'bets':n,'wins':int((g.profit100>0).sum()),'losses':int((g.profit100<0).sum()),'staked':s,'profit':p,'roi':p/s if s else np.nan})
yearly=pd.DataFrame(yearly)

# Save audit files.
cols=['event_date','method','favorite','opponent','market_prob','fav_decimal','american_odds','american_odds_rounded','stake','profit100','bet_number','cumulative_profit','cumulative_roi']
all_signals[cols+[c for c in ['portfolio_bet_number','portfolio_cumulative_profit','portfolio_cumulative_roi'] if c in all_signals.columns]].to_csv(out/'all_strategy_signal_bets.csv',index=False)
ucols=['event_date','favorite','opponent','methods','num_methods','market_prob','fav_decimal','american_odds','american_odds_rounded','stake','profit100','bet_number','cumulative_profit','cumulative_roi']
u[ucols].to_csv(out/'unique_fight_portfolio.csv',index=False)
pd.DataFrame(summary).to_csv(out/'strategy_summary.csv',index=False)
over.to_csv(out/'overlap_groups.csv',index=False)
yearly.to_csv(out/'unique_portfolio_by_year.csv',index=False)

progress(90,"Printing exact results")
print("\n$100 FLAT-BET BACKTEST — THREE STABLE STRIKING METHODS")
print("="*108)
for r in summary:
    print(f"{r['method']:<25} {r['bets']:3d} bets | {r['wins']}-{r['losses']} | staked ${r['staked']:,.0f} | profit ${r['profit']:+,.2f} | ROI {r['roi']*100:+.2f}% | returned ${r['return']:,.2f}")

print("\nSTRATEGY-BY-STRATEGY PORTFOLIO")
print("="*108)
print(f"Every method gets its own $100 bet, including multiple $100 bets on the same fight if methods overlap.")
print(f"Bet units: {sig_n} | staked ${sig_stake:,.0f} | profit ${sig_profit:+,.2f} | ROI {sig_roi*100:+.2f}% | returned ${sig_stake+sig_profit:,.2f}")

print("\nUNIQUE-FIGHT PORTFOLIO")
print("="*108)
print(f"Maximum $100 per fight regardless of how many methods agree.")
print(f"Unique fights: {uniq_n} | staked ${uniq_stake:,.0f} | profit ${uniq_profit:+,.2f} | ROI {uniq_roi*100:+.2f}% | returned ${uniq_stake+uniq_profit:,.2f}")
print(f"Overlapping signal units avoided: {sig_n-uniq_n}")

print("\nOVERLAP GROUPS")
print("="*108)
for _,r in over.iterrows():
    print(f"{r.methods:<72} n={int(r.unique_fights):3d} | {int(r.wins)}-{int(r.losses)} | profit ${r.profit:+,.2f} | ROI {r.roi*100:+.2f}%")

print("\nYEAR BY YEAR — UNIQUE-FIGHT PORTFOLIO")
print("="*108)
for _,r in yearly.iterrows():
    print(f"{int(r.year)} | {int(r.bets):3d} bets | {int(r.wins)}-{int(r.losses)} | profit ${r.profit:+,.2f} | ROI {r.roi*100:+.2f}%")

print("\nFILES")
print("="*108)
print(out/'all_strategy_signal_bets.csv')
print(out/'unique_fight_portfolio.csv')
print(out/'strategy_summary.csv')
print(out/'overlap_groups.csv')
print(out/'unique_portfolio_by_year.csv')
print("\nEach bet file includes the exact historical decimal price, derived American odds, $100 P/L, and cumulative P/L.")
progress(100,"Complete")
PY
