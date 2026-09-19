#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_player_manager_research as pmr

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_player_enriched.parquet'
OUT=ROOT/'research/player_manager_robustness'
OUT.mkdir(parents=True,exist_ok=True)

CANDIDATES=[
    {
        'id':'MLS-P01',
        'name':'Draw — Near-Equal Starting XI Continuity',
        'outcome':'DRAW','price_band':'ALL',
        'feature':'balance_player_starter_proxy_continuity','op':'<=','threshold':0.049450549450549386,
    },
    {
        'id':'MLS-P02',
        'name':'Home — Opponent Core Minutes Concentration',
        'outcome':'HOME','price_band':'P25_35',
        'feature':'opp_player_top11_minutes_share5','op':'>=','threshold':0.8378727511411662,
    },
    {
        'id':'MLS-P03',
        'name':'Home — Lower Recent High-Load Count',
        'outcome':'HOME','price_band':'P20_30',
        'feature':'edge_player_high_load_players3','op':'<=','threshold':-1.0,
    },
    {
        'id':'MLS-P04',
        'name':'Home — Opponent Minutes Concentration',
        'outcome':'HOME','price_band':'P25_35',
        'feature':'opp_player_minutes_entropy5','op':'<=','threshold':2.7330179254029505,
    },
    {
        'id':'MLS-P05',
        'name':'Home — Less-Tenured Manager Edge',
        'outcome':'HOME','price_band':'P20_30',
        'feature':'edge_manager_prior_games','op':'<=','threshold':-7.100000000000023,
    },
]

def now():
    return datetime.now(timezone.utc).isoformat()

def met(x):
    m=pmr.metrics(x)
    if not m:
        return None
    return m

def condition(df,feature,op,t):
    v=pd.to_numeric(df[feature],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)

def exact(df,c,pb=None,t=None):
    z=df[df.outcome.eq(c['outcome'])].copy()
    m=condition(z,c['feature'],c['op'],c['threshold'] if t is None else t)
    m &= pmr.price_mask(z,pb or c['price_band'])
    return z[m].copy()

def bootstrap_roi(x,n=6000,seed=719):
    if len(x)<2:
        return [None,None]
    rng=np.random.default_rng(seed)
    v=x.profit.to_numpy(float)
    means=np.empty(n)
    for i in range(n):
        means[i]=rng.choice(v,size=len(v),replace=True).mean()
    return [float(q) for q in np.quantile(means,[.025,.975])]

def yearly(x):
    rows=[]
    for year,z in x.groupby('season'):
        m=met(z)
        rows.append({'season':int(year),**m})
    return rows

def thirds(x):
    z=x.sort_values('date').reset_index(drop=True)
    rows=[]
    for i,idx in enumerate(np.array_split(np.arange(len(z)),3),1):
        q=z.iloc[idx]
        m=met(q)
        rows.append({'third':i,**m} if m else {'third':i,'n':0})
    return rows

def loo(x):
    rows=[]
    for year in sorted(x.season.unique()):
        z=x[~x.season.eq(year)]
        m=met(z)
        rows.append({'left_out':int(year),**m} if m else {'left_out':int(year),'n':0})
    return rows

def team_concentration(x,meta):
    if x.empty or x.outcome.iloc[0]=='DRAW':
        # Draw rules have no selected side; report matchup-team concentration instead.
        ids=set(x.match_id)
        z=meta[meta.match_id.isin(ids)]
        counts=pd.concat([z.home_team,z.away_team]).value_counts()
    else:
        z=x[['match_id','outcome']].merge(meta[['match_id','home_team','away_team']],on='match_id',how='left')
        sel=np.where(z.outcome.eq('HOME'),z.home_team,z.away_team)
        counts=pd.Series(sel).value_counts()
    total=max(1,int(counts.sum()))
    return {
        'unique_teams':int(len(counts)),
        'top1_share':float(counts.iloc[0]/total) if len(counts) else 0.0,
        'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0.0,
        'top5':{str(k):int(v) for k,v in counts.head(5).items()},
    }

def threshold_neighbors(base,c):
    train=base[(base.outcome.eq(c['outcome'])) & base.season.between(*pmr.SPLIT['train'])]
    vals=pd.to_numeric(train[c['feature']],errors='coerce').dropna()
    if not len(vals):
        return []
    center=float(c['threshold'])
    if vals.nunique()<=8:
        unique=sorted(float(v) for v in vals.unique())
        unique.sort(key=lambda v:abs(v-center))
        ts=sorted(set(unique[:5]+[center]))
    else:
        sd=float(vals.std()) if len(vals)>1 else 0.0
        scale=max(abs(center)*.08,sd*.08,0.01)
        ts=sorted(set([center-2*scale,center-scale,center,center+scale,center+2*scale]))
    rows=[]
    for t in ts:
        x=exact(base,c,t=t)
        rows.append({
            'threshold':float(t),'full':met(x),
            'train':met(x[x.season.between(*pmr.SPLIT['train'])]),
            'validation':met(x[x.season.between(*pmr.SPLIT['validation'])]),
            'holdout':met(x[x.season.between(*pmr.SPLIT['holdout'])]),
        })
    return rows

def price_neighbors(base,c):
    rows=[]
    for pb,_,__ in pmr.PRICE_BANDS:
        x=exact(base,c,pb=pb)
        if not len(x):
            continue
        rows.append({
            'price_band':pb,'full':met(x),
            'train':met(x[x.season.between(*pmr.SPLIT['train'])]),
            'validation':met(x[x.season.between(*pmr.SPLIT['validation'])]),
            'holdout':met(x[x.season.between(*pmr.SPLIT['holdout'])]),
        })
    return rows

def status(rec):
    full=rec['full'];hold=rec['holdout'];ci=rec['bootstrap95'];conc=rec['team_concentration']
    if not full or not hold:
        return 'REJECT'
    # Still shadow: this only prioritizes prospective tracking.
    if hold['n']>=50 and hold['roi']>0 and ci[0] is not None and ci[0]>0 and conc['top3_share']<.35:
        return 'PROSPECTIVE_PRIORITY'
    if hold['n']>=25 and hold['roi']>0 and full['positive_season_ratio']>=.65:
        return 'SHADOW_WATCH'
    return 'REJECT'

def main():
    d=pd.read_parquet(DATA)
    s=pmr.selection_rows(d)
    meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id')

    registry=[]
    report=[
        'MLS PLAYER/MANAGER ROBUSTNESS VALIDATION',
        '='*100,
        'Candidate centers are frozen from the prior pre-holdout search. These tests characterize stability; they do not retune centers.',
        'All statuses remain shadow-only. PROSPECTIVE_PRIORITY means track in 2026, not publish as an official method.',
        '',
    ]

    for c in CANDIDATES:
        x=exact(s,c)
        rec={
            **c,
            'full':met(x),
            'train':met(x[x.season.between(*pmr.SPLIT['train'])]),
            'validation':met(x[x.season.between(*pmr.SPLIT['validation'])]),
            'holdout':met(x[x.season.between(*pmr.SPLIT['holdout'])]),
            'bootstrap95':bootstrap_roi(x),
            'yearly':yearly(x),
            'thirds':thirds(x),
            'leave_one_season_out':loo(x),
            'team_concentration':team_concentration(x,meta),
            'threshold_neighbors':threshold_neighbors(s,c),
            'price_neighbors':price_neighbors(s,c),
        }
        rec['status']=status(rec)
        registry.append(rec)

        f=rec['full'] or {};h=rec['holdout'] or {}
        loo_vals=[r.get('roi') for r in rec['leave_one_season_out'] if r.get('roi') is not None]
        th=rec['threshold_neighbors']
        th_positive=sum(
            1 for r in th if r.get('train') and r.get('validation') and r.get('holdout')
            and r['train']['roi']>0 and r['validation']['roi']>0 and r['holdout']['roi']>0
        )
        report += [
            f"{c['id']} — {c['name']}",
            f"RULE: {c['outcome']} {c['price_band']} | {c['feature']} {c['op']} {c['threshold']:.6g}",
            f"FULL n={f.get('n',0)} {f.get('wins',0)}-{f.get('losses',0)} ROI={f.get('roi',float('nan')):+.1%} units={f.get('units',0):+.2f}",
            f"TRAIN ROI={rec['train']['roi']:+.1%} n={rec['train']['n']} | VAL ROI={rec['validation']['roi']:+.1%} n={rec['validation']['n']} | HOLD ROI={h.get('roi',float('nan')):+.1%} n={h.get('n',0)}",
            f"bootstrap95={rec['bootstrap95'][0]:+.1%}..{rec['bootstrap95'][1]:+.1%} | LOO={min(loo_vals):+.1%}..{max(loo_vals):+.1%}",
            f"threshold neighbors positive all eras={th_positive}/{len(th)} | teams={rec['team_concentration']['unique_teams']} top3 share={rec['team_concentration']['top3_share']:.1%}",
            f"STATUS: {rec['status']}",
            '',
        ]

    payload={
        'built_at':now(),
        'dataset':str(DATA),
        'design':'Frozen centers from player-manager preholdout discovery; bootstrap, yearly, chronological thirds, leave-one-season-out, team concentration, threshold and price neighbors.',
        'official_promotions':0,
        'candidates':registry,
    }
    (OUT/'registry.json').write_text(json.dumps(payload,indent=2,default=str))
    (OUT/'report.txt').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))

if __name__=='__main__':
    main()
