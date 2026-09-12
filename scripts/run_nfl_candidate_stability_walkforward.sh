#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
IN="$ROOT/rolling_roi_discovery/pregame_team_sides_2006_2026.parquet"
OUT="$ROOT/nfl_candidate_validation"
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

def metrics(df):
    n=len(df)
    if not n: return dict(n=0,wins=0,losses=0,win_rate=np.nan,roi=np.nan,old_n=0,old_roi=np.nan,new_n=0,new_roi=np.nan,profit=0.0)
    roi=df.bet_profit100.sum()/(100*n)
    old=df[df.season<=2015]; new=df[df.season>=2016]
    old_roi=old.bet_profit100.sum()/(100*len(old)) if len(old) else np.nan
    new_roi=new.bet_profit100.sum()/(100*len(new)) if len(new) else np.nan
    return dict(n=n,wins=int(df.win.sum()),losses=int(n-df.win.sum()),win_rate=float(df.win.mean()),roi=float(roi),old_n=len(old),old_roi=float(old_roi) if pd.notna(old_roi) else np.nan,new_n=len(new),new_roi=float(new_roi) if pd.notna(new_roi) else np.nan,profit=float(df.bet_profit100.sum()))

prog(5,'Loading strict pre-game feature table')
g=pd.read_parquet(inp)
for c in ['season','week','market_prob','bet_profit100','win','rest_edge','edge_off_epa','edge_rush_epa','edge_def_rush_epa','edge_points_allowed','pre_point_diff','opp_pre_point_diff','pre_turnover_margin','opp_pre_turnover_margin','last3_point_diff','opp_last3_point_diff']:
    if c in g.columns: g[c]=pd.to_numeric(g[c],errors='coerce')
# completed/priced regular-season side rows with meaningful history
b=g[g.moneyline.notna() & g.market_prob.notna() & g.win.isin([0.0,1.0]) & (g.prior_games>=3)].copy()
b['last3_pd_edge']=b['last3_point_diff']-b['opp_last3_point_diff']
b['season_pd_edge']=b['pre_point_diff']-b['opp_pre_point_diff']
b['to_margin_edge']=b['pre_turnover_margin']-b['opp_pre_turnover_margin']
prog(12,f'Usable priced team-sides: {len(b):,}')

# Rule functions. Market tags mimic discovery scan.
def market_mask(df,tag):
    if tag=='ANY': return pd.Series(True,index=df.index)
    if tag=='FAV55+': return df.market_prob>=.55
    if tag=='FAV60+': return df.market_prob>=.60
    if tag=='DOG45-': return df.market_prob<=.45
    raise ValueError(tag)

def rush(df,w=7,market='ANY',oe=14,de=14):
    return (df.week>=w)&market_mask(df,market)&(df.edge_rush_epa>=oe)&(df.edge_def_rush_epa>=de)
def recent(df,w=8,market='DOG45-',pdedge=5,oe=8):
    return (df.week>=w)&market_mask(df,market)&(df.last3_pd_edge>=pdedge)&(df.edge_off_epa>=oe)
def turnover(df,w=5,market='DOG45-',toedge=1.0,oe=4):
    return (df.week>=w)&market_mask(df,market)&(df.to_margin_edge>=toedge)&(df.edge_off_epa>=oe)
def restoff(df,w=8,market='FAV55+',rest=7,oe=8):
    return (df.week>=w)&market_mask(df,market)&(df.rest_edge>=rest)&(df.edge_off_epa>=oe)
def scoringdef(df,w=5,market='DOG45-',pdedge=5,de=4):
    return (df.week>=w)&market_mask(df,market)&(df.season_pd_edge>=pdedge)&(df.edge_points_allowed>=de)

families={
 'RUSH MATCHUP':(rush,dict(w=7,market='ANY',oe=14,de=14)),
 'RECENT FORM + EPA':(recent,dict(w=8,market='DOG45-',pdedge=5,oe=8)),
 'TURNOVER + OFFENSE':(turnover,dict(w=5,market='DOG45-',toedge=1.0,oe=4)),
 'REST + OFFENSE':(restoff,dict(w=8,market='FAV55+',rest=7,oe=8)),
 'SCORING + DEFENSE':(scoringdef,dict(w=5,market='DOG45-',pdedge=5,de=4)),
}

print('\nEXACT DISCOVERY CANDIDATES — RECOMPUTED')
print('='*110)
exact_rows=[]
for name,(fn,kw) in families.items():
    x=b[fn(b,**kw).fillna(False)].copy(); m=metrics(x); exact_rows.append({'family':name,**kw,**m})
    print(f"{name:22s} n={m['n']:3d} {m['wins']}-{m['losses']} win={100*m['win_rate']:.1f}% ROI={pct(m['roi'])} | 2006-15={pct(m['old_roi'])} ({m['old_n']}) | 2016-26={pct(m['new_roi'])} ({m['new_n']}) | P/L=${m['profit']:+,.2f}")
pd.DataFrame(exact_rows).to_csv(outdir/'exact_candidates.csv',index=False)

prog(24,'Testing neighboring thresholds around each discovery rule')
# Deliberately modest neighborhoods: we want plateaus, not another giant optimization search.
grids={
 'RUSH MATCHUP': [dict(w=w,market=m,oe=oe,de=de) for w,m,oe,de in product([6,7,8,9],['ANY','FAV55+'],[10,12,14,16],[10,12,14,16])],
 'RECENT FORM + EPA': [dict(w=w,market='DOG45-',pdedge=p,oe=oe) for w,p,oe in product([7,8,9,10],[3,4,5,6,7],[4,6,8,10,12])],
 'TURNOVER + OFFENSE': [dict(w=w,market='DOG45-',toedge=t,oe=oe) for w,t,oe in product([5,6,7,8],[.5,.75,1.0,1.25],[2,4,6,8])],
 'REST + OFFENSE': [dict(w=w,market=m,rest=r,oe=oe) for w,m,r,oe in product([7,8,9,10],['FAV55+','FAV60+'],[6,7,8],[4,6,8,10,12])],
 'SCORING + DEFENSE': [dict(w=w,market='DOG45-',pdedge=p,de=de) for w,p,de in product([5,6,7,8],[3,4,5,6,7],[2,4,6,8])],
}
all_neighbors=[]
for fi,(name,(fn,_)) in enumerate(families.items(),1):
    rows=[]
    for kw in grids[name]:
        m=metrics(b[fn(b,**kw).fillna(False)])
        stable=(m['n']>=50 and m['roi']>=.10 and m['old_n']>=15 and m['new_n']>=15 and m['old_roi']>0 and m['new_roi']>0)
        rows.append({'family':name,**kw,**m,'stable10':stable})
    d=pd.DataFrame(rows); all_neighbors.append(d)
    eligible=d[d.n>=50]
    passn=int(eligible.stable10.sum()); denom=len(eligible)
    rate=passn/denom if denom else 0
    best=d[d.stable10].sort_values(['n','roi'],ascending=[False,False]).head(1)
    print(f"\n{name} NEIGHBORHOOD: {passn}/{denom} eligible neighbors pass >=10% + both-era test ({100*rate:.1f}%)")
    if len(best):
        z=best.iloc[0]
        params={k:z[k] for k in z.index if k in grids[name][0]}
        print(f"  Widest stable >=10% neighbor: n={int(z.n)} ROI={100*z.roi:+.2f}% old={100*z.old_roi:+.2f}% new={100*z.new_roi:+.2f}% params={params}")
    else: print('  No neighboring rule clears the full stability bar.')
    prog(24+int(26*fi/len(families)),f'Neighborhood {fi}/{len(families)} complete')
neighbor_df=pd.concat(all_neighbors,ignore_index=True); neighbor_df.to_csv(outdir/'neighbor_threshold_results.csv',index=False)

prog(55,'Calculating season-by-season and four-block consistency')
season_rows=[]; block_rows=[]
blocks=[(2006,2010),(2011,2015),(2016,2020),(2021,2026)]
for name,(fn,kw) in families.items():
    x=b[fn(b,**kw).fillna(False)].copy()
    for y in sorted(x.season.dropna().unique()):
        z=x[x.season==y]; m=metrics(z); season_rows.append({'family':name,'season':int(y),**m})
    for lo,hi in blocks:
        z=x[(x.season>=lo)&(x.season<=hi)]; m=metrics(z); block_rows.append({'family':name,'block':f'{lo}-{hi}',**m})
season_df=pd.DataFrame(season_rows); season_df.to_csv(outdir/'exact_rule_by_season.csv',index=False)
block_df=pd.DataFrame(block_rows); block_df.to_csv(outdir/'exact_rule_by_era_block.csv',index=False)
print('\nFOUR-BLOCK CONSISTENCY')
print('='*110)
for name in families:
    z=block_df[block_df.family==name]
    vals=' | '.join(f"{r.block}: n={int(r.n)} ROI={pct(r.roi)}" for _,r in z.iterrows())
    print(f'{name:22s} {vals}')

prog(68,'Running genuine expanding-season walk-forward selection')
# For each family and test season, choose a threshold variant using PRIOR seasons only,
# then freeze it and grade the next season. This is deliberately harsher than an era split.
wf_rows=[]
for name,(fn,_) in families.items():
    variants=grids[name]
    for y in range(2016,2027):
        train=b[b.season<y]; test=b[b.season==y]
        candidates=[]
        for kw in variants:
            tr=train[fn(train,**kw).fillna(False)]; m=metrics(tr)
            # enough historical evidence, positive early/recent training eras where possible
            if m['n']<40 or m['roi']<=0: continue
            # score favors ROI but penalizes tiny samples; cap ROI term to reduce jackpot chasing
            score=min(m['roi'],.25)*math.sqrt(m['n'])
            candidates.append((score,kw,m))
        if not candidates: continue
        candidates.sort(key=lambda q:q[0],reverse=True); _,kw,trm=candidates[0]
        te=test[fn(test,**kw).fillna(False)]; tm=metrics(te)
        wf_rows.append({'family':name,'test_season':y,'selected_params':str(kw),'train_n':trm['n'],'train_roi':trm['roi'],'test_n':tm['n'],'test_wins':tm['wins'],'test_profit':tm['profit'],'test_roi':tm['roi']})
    prog(68+int(20*(list(families).index(name)+1)/len(families)),f'Walk-forward {name} complete')
wf=pd.DataFrame(wf_rows); wf.to_csv(outdir/'walk_forward_by_season.csv',index=False)
print('\nEXPANDING-SEASON WALK-FORWARD — 2016-2026')
print('='*110)
for name in families:
    z=wf[(wf.family==name)&(wf.test_n>0)]
    n=int(z.test_n.sum()) if len(z) else 0; profit=float(z.test_profit.sum()) if len(z) else 0
    roi=profit/(100*n) if n else np.nan
    posyrs=int((z.test_profit>0).sum()) if len(z) else 0
    years=len(z)
    print(f"{name:22s} OOS bets={n:3d} P/L=${profit:+,.2f} OOS ROI={pct(roi)} | profitable test seasons {posyrs}/{years}")

prog(92,'Calculating flat-$100 drawdown for exact candidates')
dd_rows=[]
for name,(fn,kw) in families.items():
    x=b[fn(b,**kw).fillna(False)].copy()
    # one side per game/rule should qualify; chronological by season/week
    x=x.sort_values(['season','week','game_id']).copy(); x['cum']=x.bet_profit100.cumsum(); x['peak']=x['cum'].cummax().clip(lower=0); x['drawdown']=x['cum']-x['peak']
    maxdd=float(x.drawdown.min()) if len(x) else np.nan
    dd_rows.append({'family':name,'bets':len(x),'profit':x.bet_profit100.sum(),'max_drawdown_100_flat':maxdd})
    print(f"{name:22s} exact flat-$100 max historical drawdown: ${maxdd:,.2f}")
pd.DataFrame(dd_rows).to_csv(outdir/'exact_rule_drawdowns.csv',index=False)

prog(100,'NFL candidate stability + walk-forward validation complete')
print('\nSaved:')
for p in ['exact_candidates.csv','neighbor_threshold_results.csv','exact_rule_by_season.csv','exact_rule_by_era_block.csv','walk_forward_by_season.csv','exact_rule_drawdowns.csv']:
    print(outdir/p)
print('\nDo NOT promote a rule solely because discovery ROI was high. The walk-forward and neighborhood results are the decision tests.')
PY
