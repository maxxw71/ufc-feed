#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
IN="$ROOT/rolling_roi_discovery/pregame_team_sides_2006_2026.parquet"
OUT="$ROOT/nfl_rush_matchup_deep_dive"
mkdir -p "$OUT"
[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUT" <<'PY'
import sys, math
from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd

inp,outdir=Path(sys.argv[1]),Path(sys.argv[2]); outdir.mkdir(parents=True,exist_ok=True)
def prog(p,msg): print(f"[{p:3d}%] {msg}",flush=True)
def pct(x): return 'NA' if pd.isna(x) else f'{100*x:+.2f}%'

def maxdd(df):
    if df.empty: return np.nan
    x=df.sort_values(['season','week','game_id']).copy()
    cum=x.bet_profit100.cumsum(); peak=cum.cummax().clip(lower=0)
    return float((cum-peak).min())

def metrics(df):
    n=len(df)
    if n==0:
        return dict(n=0,wins=0,losses=0,win_rate=np.nan,roi=np.nan,profit=0.0,old_n=0,old_roi=np.nan,new_n=0,new_roi=np.nan,avg_line=np.nan,med_line=np.nan,max_dd=np.nan)
    old=df[df.season<=2015]; new=df[df.season>=2016]
    roi=lambda z: float(z.bet_profit100.sum()/(100*len(z))) if len(z) else np.nan
    return dict(n=n,wins=int(df.win.sum()),losses=int(n-df.win.sum()),win_rate=float(df.win.mean()),roi=roi(df),profit=float(df.bet_profit100.sum()),old_n=len(old),old_roi=roi(old),new_n=len(new),new_roi=roi(new),avg_line=float(df.moneyline.mean()),med_line=float(df.moneyline.median()),max_dd=maxdd(df))

prog(5,'Loading strict pre-game NFL feature table')
b=pd.read_parquet(inp)
for c in ['season','week','market_prob','moneyline','bet_profit100','win','prior_games','edge_rush_epa','edge_def_rush_epa']:
    b[c]=pd.to_numeric(b[c],errors='coerce')
b=b[b.moneyline.notna() & b.market_prob.notna() & b.win.isin([0.0,1.0]) & (b.prior_games>=3)].copy()
prog(12,f'Usable priced team-sides: {len(b):,}')

def market_mask(df,tag):
    if tag=='ANY': return pd.Series(True,index=df.index)
    if tag=='FAVORITE': return df.market_prob>.50
    if tag=='FAV55+': return df.market_prob>=.55
    if tag=='FAV60+': return df.market_prob>=.60
    if tag=='UNDERDOG': return df.market_prob<.50
    if tag=='DOG45-': return df.market_prob<=.45
    raise ValueError(tag)

def mask(df,w=7,market='ANY',oe=14,de=14):
    return (df.week>=w)&market_mask(df,market)&(df.edge_rush_epa>=oe)&(df.edge_def_rush_epa>=de)

base_kw=dict(w=7,market='ANY',oe=14,de=14)
base=b[mask(b,**base_kw)].copy(); bm=metrics(base)
print('\nBASE RUSH MATCHUP')
print('='*105)
print(f"week>=7 | ANY market | rushEPA rank edge>=14 | rush-defense rank edge>=14")
print(f"n={bm['n']} | {bm['wins']}-{bm['losses']} | win={100*bm['win_rate']:.1f}% | ROI={pct(bm['roi'])} | P/L=${bm['profit']:+,.2f} | avg line={bm['avg_line']:+.0f} | max DD=${bm['max_dd']:,.2f}")

prog(20,'Breaking base rule down by favorite/underdog status')
market_groups=[
 ('Underdog',lambda x:x.market_prob<.50),
 ('Favorite',lambda x:x.market_prob>.50),
 ('Dog <=45%',lambda x:x.market_prob<=.45),
 ('45-55%',lambda x:(x.market_prob>.45)&(x.market_prob<.55)),
 ('Fav >=55%',lambda x:x.market_prob>=.55),
 ('Fav >=60%',lambda x:x.market_prob>=.60),
]
mg=[]
for name,fn in market_groups:
    x=base[fn(base)]; m=metrics(x); mg.append({'group':name,**m})
    if m['n']:
        print(f"{name:12s} n={m['n']:3d} {m['wins']}-{m['losses']} win={100*m['win_rate']:.1f}% ROI={pct(m['roi'])} old={pct(m['old_roi'])} new={pct(m['new_roi'])} avg line={m['avg_line']:+.0f}")
pd.DataFrame(mg).to_csv(outdir/'base_market_groups.csv',index=False)

prog(28,'Breaking base rule down by exact historical moneyline bands')
bands=[('<=-200',-99999,-200),('-199 to -150',-199,-150),('-149 to -110',-149,-110),('-109 to +109',-109,109),('+110 to +149',110,149),('+150 to +199',150,199),('+200+',200,99999)]
br=[]
print('\nBASE RULE — ODDS BANDS')
print('='*105)
for name,lo,hi in bands:
    x=base[(base.moneyline>=lo)&(base.moneyline<=hi)]; m=metrics(x); br.append({'band':name,**m})
    if m['n']:
        print(f"{name:14s} n={m['n']:3d} {m['wins']}-{m['losses']} win={100*m['win_rate']:.1f}% ROI={pct(m['roi'])} P/L=${m['profit']:+,.2f}")
pd.DataFrame(br).to_csv(outdir/'base_odds_bands.csv',index=False)

prog(36,'Scanning expanded Rush Matchup threshold grid')
weeks=list(range(5,13)); markets=['ANY','FAVORITE','FAV55+','FAV60+','UNDERDOG','DOG45-']; edges=[8,10,12,14,16,18]
rows=[]
combos=list(product(weeks,markets,edges,edges))
for i,(w,market,oe,de) in enumerate(combos,1):
    x=b[mask(b,w,market,oe,de).fillna(False)]; m=metrics(x)
    stable=(m['n']>=50 and m['roi']>=.10 and m['old_n']>=15 and m['new_n']>=15 and m['old_roi']>0 and m['new_roi']>0)
    recent10=(stable and m['new_roi']>=.10)
    rows.append({'w':w,'market':market,'oe':oe,'de':de,**m,'stable10':stable,'recent10':recent10})
    if i%300==0: prog(36+int(24*i/len(combos)),f'Tested {i:,}/{len(combos):,} threshold variants')
r=pd.DataFrame(rows); r.to_csv(outdir/'rush_threshold_grid.csv',index=False)

stable=r[r.stable10].copy().sort_values(['n','roi'],ascending=[False,False])
recent=r[r.recent10].copy().sort_values(['n','roi'],ascending=[False,False])
print('\nWIDEST RULES >=10% ROI + BOTH ERAS POSITIVE')
print('='*105)
for _,z in stable.head(15).iterrows():
    print(f"n={int(z.n):3d} ROI={pct(z.roi)} old={pct(z.old_roi)} new={pct(z.new_roi)} win={100*z.win_rate:.1f}% | week>={int(z.w)} {z.market} rushEPA>={int(z.oe)} rushDef>={int(z.de)} avg line={z.avg_line:+.0f}")
print('\nWIDEST RULES ALSO >=10% IN 2016-2026')
print('='*105)
for _,z in recent.head(15).iterrows():
    print(f"n={int(z.n):3d} ROI={pct(z.roi)} old={pct(z.old_roi)} new={pct(z.new_roi)} win={100*z.win_rate:.1f}% | week>={int(z.w)} {z.market} rushEPA>={int(z.oe)} rushDef>={int(z.de)} avg line={z.avg_line:+.0f}")
stable.head(100).to_csv(outdir/'widest_stable10.csv',index=False)
recent.head(100).to_csv(outdir/'widest_recent10.csv',index=False)

prog(64,'Testing week floors for the exact 14/14 matchup')
wf=[]
print('\nBASE 14/14 RULE BY WEEK FLOOR')
print('='*105)
for w in weeks:
    x=b[mask(b,w,'ANY',14,14)]; m=metrics(x); wf.append({'week_floor':w,**m})
    print(f"week>={w:2d} n={m['n']:3d} ROI={pct(m['roi'])} old={pct(m['old_roi'])} new={pct(m['new_roi'])} win={100*m['win_rate']:.1f}% maxDD=${m['max_dd']:,.0f}")
pd.DataFrame(wf).to_csv(outdir/'base_by_week_floor.csv',index=False)

prog(70,'Calculating season-by-season results for base rule')
yr=[]
print('\nBASE RULE — YEAR BY YEAR')
print('='*105)
for y in sorted(base.season.dropna().unique()):
    x=base[base.season==y]; m=metrics(x); yr.append({'season':int(y),**m})
    print(f"{int(y)} | n={m['n']:2d} {m['wins']}-{m['losses']} | ROI={pct(m['roi'])} | P/L=${m['profit']:+,.2f}")
pd.DataFrame(yr).to_csv(outdir/'base_by_year.csv',index=False)

prog(76,'Saving exact historical base-rule bets')
cols=[c for c in ['season','week','game_id','team','opponent','moneyline','market_prob','edge_rush_epa','edge_def_rush_epa','win','bet_profit100'] if c in base.columns]
exact=base[cols].sort_values(['season','week','game_id']).copy(); exact['cumulative_profit100']=exact.bet_profit100.cumsum(); exact.to_csv(outdir/'base_exact_bets.csv',index=False)

prog(82,'Running expanded-grid walk-forward selection for 2016-2026')
# Each test season selects one Rush Matchup variant from PRIOR seasons only.
# This asks whether widening the rule family can increase OOS activity while preserving ROI.
variants=[dict(w=w,market=m,oe=oe,de=de) for w,m,oe,de in combos]
wfr=[]
for y in range(2016,2027):
    train=b[b.season<y]; test=b[b.season==y]; candidates=[]
    for kw in variants:
        tr=train[mask(train,**kw).fillna(False)]; m=metrics(tr)
        if m['n']<40 or m['roi']<=0: continue
        score=min(m['roi'],.25)*math.sqrt(m['n'])
        candidates.append((score,kw,m))
    if not candidates: continue
    candidates.sort(key=lambda q:q[0],reverse=True); _,kw,trm=candidates[0]
    te=test[mask(test,**kw).fillna(False)]; tm=metrics(te)
    wfr.append({'test_season':y,**kw,'train_n':trm['n'],'train_roi':trm['roi'],'test_n':tm['n'],'test_wins':tm['wins'],'test_losses':tm['losses'],'test_profit':tm['profit'],'test_roi':tm['roi']})
    print(f"{y}: selected week>={kw['w']} {kw['market']} {kw['oe']}/{kw['de']} | test n={tm['n']} ROI={pct(tm['roi'])} P/L=${tm['profit']:+,.2f}")
wfdf=pd.DataFrame(wfr); wfdf.to_csv(outdir/'expanded_walk_forward_by_year.csv',index=False)
if len(wfdf):
    n=int(wfdf.test_n.sum()); p=float(wfdf.test_profit.sum()); roi=p/(100*n) if n else np.nan; pos=int((wfdf.test_profit>0).sum())
    print('\nEXPANDED GRID WALK-FORWARD TOTAL')
    print('='*105)
    print(f"OOS bets={n} | P/L=${p:+,.2f} | OOS ROI={pct(roi)} | profitable seasons={pos}/{len(wfdf)}")

prog(94,'Selecting practical broad candidates')
print('\nPRACTICAL CANDIDATES')
print('='*105)
if len(stable):
    z=stable.iloc[0]
    print(f"WIDEST BOTH-ERA >=10%: n={int(z.n)} ROI={pct(z.roi)} old={pct(z.old_roi)} new={pct(z.new_roi)} | week>={int(z.w)} {z.market} rushEPA>={int(z.oe)} rushDef>={int(z.de)}")
else: print('No expanded rule cleared overall >=10% + both-era-positive with n>=50.')
if len(recent):
    z=recent.iloc[0]
    print(f"WIDEST WITH 2016+ >=10% TOO: n={int(z.n)} ROI={pct(z.roi)} old={pct(z.old_roi)} new={pct(z.new_roi)} | week>={int(z.w)} {z.market} rushEPA>={int(z.oe)} rushDef>={int(z.de)}")
else: print('No expanded rule also maintained >=10% ROI in 2016-2026.')

prog(100,'Rush Matchup deep dive complete')
print('\nSaved under:',outdir)
print('Key files: rush_threshold_grid.csv, widest_stable10.csv, widest_recent10.csv, base_exact_bets.csv, expanded_walk_forward_by_year.csv')
PY
