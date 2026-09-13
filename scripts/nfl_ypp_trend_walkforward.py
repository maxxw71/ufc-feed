import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "nfl-predictor-v1"
IN = ROOT / "rolling_roi_discovery" / "pregame_team_sides_2006_2026.parquet"
OUT = ROOT / "nfl_ypp_trend_walkforward"
OUT.mkdir(parents=True, exist_ok=True)


def metrics(x):
    n = len(x)
    if not n:
        return dict(n=0, wins=0, losses=0, win=np.nan, roi=np.nan, profit=0.0)
    p = float(x.bet_profit100.sum())
    w = int(x.win.sum())
    return dict(n=n, wins=w, losses=n-w, win=float(x.win.mean()), roi=p/(100*n), profit=p)


def base(df, p):
    z = (df.week >= p['w']) & (df.market_prob >= p['m'])
    if p['loc'] == 'HOME':
        z &= df.home_side.eq(1)
    elif p['loc'] == 'ROAD':
        z &= df.home_side.eq(0)
    return z


def ypp_mask(df, p):
    return (
        base(df, p)
        & (df.pre_yards_per_play >= p['oy'])
        & (df.opp_pre_def_ypp_allowed >= p['dy'])
    )


def trend_gate(df):
    # Fixed football-motivated gate: the opponent must still look weak recently,
    # not merely weak on its full-season average. No weather variables are used.
    needed = [
        'opp_last3_def_ypp_allowed', 'opp_pre_def_ypp_allowed',
        'opp_last3_point_diff', 'opp_pre_point_diff'
    ]
    ok = pd.Series(True, index=df.index)
    for c in needed:
        ok &= df[c].notna()
    ok &= df.opp_last3_def_ypp_allowed.ge(df.opp_pre_def_ypp_allowed)
    ok &= df.opp_last3_point_diff.le(df.opp_pre_point_diff)
    return ok


def select_original(train, grid):
    cand = []
    for p in grid:
        x = train[ypp_mask(train, p).fillna(False)]
        m = metrics(x)
        if m['n'] < 50 or m['win'] < .68 or m['roi'] < .03:
            continue
        recent = x[x.season >= max(2006, int(train.season.max()) - 4)]
        rm = metrics(recent)
        if rm['n'] >= 15 and rm['roi'] <= 0:
            continue
        score = min(m['roi'], .20)*math.sqrt(m['n']) + .25*max(0, m['win']-.68)*math.sqrt(m['n'])
        cand.append((score, p, m))
    if not cand:
        return None
    cand.sort(key=lambda q:q[0], reverse=True)
    return cand[0]


def select_trend(train, grid):
    # Same YPP grid, but selection quality is judged only on historically trend-confirmed bets.
    # Smaller minimum sample is necessary because the fixed gate intentionally removes many games.
    tg = trend_gate(train)
    cand = []
    for p in grid:
        x = train[(ypp_mask(train, p) & tg).fillna(False)]
        m = metrics(x)
        if m['n'] < 30 or m['win'] < .68 or m['roi'] < .03:
            continue
        recent = x[x.season >= max(2006, int(train.season.max()) - 4)]
        rm = metrics(recent)
        if rm['n'] >= 10 and rm['roi'] <= 0:
            continue
        score = min(m['roi'], .20)*math.sqrt(m['n']) + .25*max(0, m['win']-.68)*math.sqrt(m['n'])
        cand.append((score, p, m))
    if not cand:
        return None
    cand.sort(key=lambda q:q[0], reverse=True)
    return cand[0]


def summarize(label, bets):
    m = metrics(bets)
    if not len(bets):
        return {'strategy': label, **m, 'profitable_seasons': 0, 'seasons': 0, 'max_drawdown': np.nan}
    by = bets.groupby('test_season', as_index=False).agg(n=('win','size'), wins=('win','sum'), profit=('bet_profit100','sum'))
    by['roi'] = by.profit/(100*by.n)
    cum = bets.sort_values(['test_season','week','game_id','team']).bet_profit100.cumsum()
    peak = cum.cummax().clip(lower=0)
    dd = (cum-peak).min()
    return {'strategy': label, **m, 'profitable_seasons': int((by.profit>0).sum()), 'seasons': len(by), 'max_drawdown': float(dd)}


g = pd.read_parquet(IN)
for c in g.columns:
    if c not in {'game_id','team','opponent','opponent_team','roof','surface'}:
        try:
            g[c] = pd.to_numeric(g[c], errors='ignore')
        except Exception:
            pass

b = g[
    g.moneyline.notna() & g.market_prob.notna() & g.win.isin([0.0,1.0])
    & (g.prior_games >= 3) & (g.market_prob > .50)
].copy()
for c in ['season','week','market_prob','moneyline','home_side','div_game']:
    if c in b:
        b[c] = pd.to_numeric(b[c], errors='coerce')

required = ['opp_last3_def_ypp_allowed','opp_pre_def_ypp_allowed','opp_last3_point_diff','opp_pre_point_diff']
missing = [c for c in required if c not in b.columns]
if missing:
    raise RuntimeError(f'Missing required trend columns: {missing}')

grid=[]
for w in [4,5,6,7,8,9,10,11,12]:
    for m in [.52,.55,.60,.65]:
        for loc in ['ANY','HOME','ROAD']:
            for oy in [5.5,5.8,6.0,6.2]:
                for dy in [5.5,5.8,6.0,6.2]:
                    grid.append(dict(w=w,m=m,loc=loc,oy=oy,dy=dy))

season_rows=[]
bet_rows=[]
for year in range(2016, 2027):
    tr=b[b.season<year]
    te=b[b.season==year]

    orig=select_original(tr,grid)
    if orig:
        _, p, tm = orig
        raw=te[ypp_mask(te,p).fillna(False)].copy()
        gated=raw[trend_gate(raw)].copy()
        for strategy,z in [('ORIGINAL_YPP',raw),('ORIGINAL_SELECTION_PLUS_TREND',gated)]:
            mm=metrics(z)
            season_rows.append({'strategy':strategy,'test_season':year,'params':json.dumps(p,sort_keys=True),'train_n':tm['n'],'train_win':tm['win'],'train_roi':tm['roi'],'test_n':mm['n'],'test_wins':mm['wins'],'test_win':mm['win'],'test_roi':mm['roi'],'test_profit':mm['profit']})
            for _,r in z.iterrows():
                d=r.to_dict(); d['strategy']=strategy; d['test_season']=year; d['selected_params']=json.dumps(p,sort_keys=True); bet_rows.append(d)

    trend_sel=select_trend(tr,grid)
    if trend_sel:
        _, p, tm = trend_sel
        z=te[(ypp_mask(te,p) & trend_gate(te)).fillna(False)].copy()
        mm=metrics(z)
        season_rows.append({'strategy':'TREND_SELECTED_YPP','test_season':year,'params':json.dumps(p,sort_keys=True),'train_n':tm['n'],'train_win':tm['win'],'train_roi':tm['roi'],'test_n':mm['n'],'test_wins':mm['wins'],'test_win':mm['win'],'test_roi':mm['roi'],'test_profit':mm['profit']})
        for _,r in z.iterrows():
            d=r.to_dict(); d['strategy']='TREND_SELECTED_YPP'; d['test_season']=year; d['selected_params']=json.dumps(p,sort_keys=True); bet_rows.append(d)

seasons=pd.DataFrame(season_rows)
bets=pd.DataFrame(bet_rows)
seasons.to_csv(OUT/'ypp_trend_by_season.csv',index=False)
if len(bets):
    bets.to_csv(OUT/'ypp_trend_all_bets.csv',index=False)
    bets[bets.win==0].to_csv(OUT/'ypp_trend_losses.csv',index=False)

summaries=[]
for strategy in ['ORIGINAL_YPP','ORIGINAL_SELECTION_PLUS_TREND','TREND_SELECTED_YPP']:
    z=bets[bets.strategy==strategy].copy() if len(bets) else pd.DataFrame()
    summaries.append(summarize(strategy,z))
summary=pd.DataFrame(summaries)
summary.to_csv(OUT/'ypp_trend_summary.csv',index=False)

# Direct elimination accounting: only compare the exact original selections with the fixed gate.
orig=bets[bets.strategy=='ORIGINAL_YPP'].copy()
gated=bets[bets.strategy=='ORIGINAL_SELECTION_PLUS_TREND'].copy()
if len(orig):
    key=['test_season','game_id','team']
    keep=set(tuple(x) for x in gated[key].itertuples(index=False,name=None))
    orig['kept_by_trend']=orig[key].apply(tuple,axis=1).isin(keep)
    removed=orig[~orig.kept_by_trend].copy()
    removed.to_csv(OUT/'trend_removed_original_bets.csv',index=False)
    audit=pd.DataFrame([{
        'original_bets':len(orig),
        'original_wins':int(orig.win.sum()),
        'original_losses':int((orig.win==0).sum()),
        'removed_bets':len(removed),
        'removed_wins':int(removed.win.sum()),
        'removed_losses':int((removed.win==0).sum()),
        'kept_bets':len(gated),
        'kept_wins':int(gated.win.sum()),
        'kept_losses':int((gated.win==0).sum()),
    }])
    audit.to_csv(OUT/'trend_elimination_audit.csv',index=False)

print('\nNFL YPP TREND WALK-FORWARD')
print('='*100)
print(summary.to_string(index=False))
print('\nSeason detail:')
print(seasons[['strategy','test_season','test_n','test_wins','test_win','test_roi','test_profit']].to_string(index=False))
if (OUT/'trend_elimination_audit.csv').exists():
    print('\nElimination audit:')
    print(pd.read_csv(OUT/'trend_elimination_audit.csv').to_string(index=False))
print('\nNo weather filter is used anywhere in this validation.')
