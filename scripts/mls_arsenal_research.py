#!/usr/bin/env python3
from __future__ import annotations

import itertools, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_autoresearch as ar
import mls_player_manager_research as pm
import mls_context_research as cr

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA_CANDIDATES=[
    ROOT/'data/processed/mls_match_features_weather_enriched.parquet',
    ROOT/'data/processed/mls_match_features_confirmed_lineup_enriched.parquet',
    ROOT/'data/processed/mls_match_features_context_enriched.parquet',
]
OUT=ROOT/'research/arsenal'
OUT.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2013,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
    ('ALL',0.0,1.0),
    ('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),('P35_45',.35,.45),
    ('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70),
]
WX=[
 'weather_temperature_f','weather_humidity_pct','weather_apparent_temperature_f',
 'weather_precipitation_in','weather_wind_mph','weather_hot','weather_cold',
 'weather_high_heat_index','weather_high_humidity','weather_wet','weather_windy'
]
KEYS={'match_id','date','season','outcome','win','odds','market_prob','profit'}

def now():return datetime.now(timezone.utc).isoformat()
def pick():
    for p in DATA_CANDIDATES:
        if p.exists():return p
    raise RuntimeError('No enriched MLS warehouse found')

def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum'])
    active=by[by['count']>=5]
    return {
        'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
        'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
        'active_seasons':int(len(active)),'positive_seasons':int((active['sum']>0).sum()),
        'positive_season_ratio':float((active['sum']>0).mean()) if len(active) else 0.0,
    }
def period(df,k):
    lo,hi=SPLIT[k];return df.season.between(lo,hi)
def pmask(df,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return df.market_prob.between(lo,hi,inclusive='both')
def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)
def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=6:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))

def merged_selection_rows(d):
    # Start from context selection rows because they preserve all regular-season
    # priced matches and known outcome/price/profit semantics.
    s=cr.selection_rows(d).copy()

    # Base autoresearch features are explicitly pregame-engineered by the existing
    # MLS research pipeline. Prefix them to prevent accidental collisions.
    b=ar.selection_rows(d).copy()
    bcols=[c for c in b.columns if c not in KEYS and c not in {'odds','market_prob'}]
    b=b[['match_id','outcome']+bcols].drop_duplicates(['match_id','outcome'])
    b=b.rename(columns={c:'base__'+c for c in bcols})
    s=s.merge(b,on=['match_id','outcome'],how='left',validate='1:1')

    # Player/manager selection rows are safe prior-game features, but coverage
    # intentionally begins only after enough player history exists.
    p=pm.selection_rows(d).copy()
    pcols=[c for c in p.columns if c not in KEYS and c not in {'odds','market_prob'}]
    p=p[['match_id','outcome']+pcols].drop_duplicates(['match_id','outcome'])
    p=p.rename(columns={c:'pm__'+c for c in pcols})
    s=s.merge(p,on=['match_id','outcome'],how='left',validate='1:1')

    # Prefix context fields already produced by cr.selection_rows.
    context_cols=[
        c for c in s.columns
        if c not in KEYS and c not in {'odds','market_prob'}
        and not c.startswith(('base__','pm__'))
    ]
    rename={}
    for c in context_cols:
        if c.startswith(('sel_','opp_','edge_','balance_','combined_','referee_')):
            rename[c]='ctx__'+c
    s=s.rename(columns=rename)

    # Weather is match-level context, same for HOME/DRAW/AWAY selections.
    wxcols=[c for c in WX if c in d.columns]
    if wxcols:
        w=d[['match_id','weather_available']+wxcols].drop_duplicates('match_id')
        w=w[pd.to_numeric(w.weather_available,errors='coerce').eq(1)].drop(columns='weather_available')
        w=w.rename(columns={c:'wx__'+c for c in wxcols})
        s=s.merge(w,on='match_id',how='left',validate='m:1')
    return s

def family(f):
    if f.startswith('base__'):return 'base'
    if f.startswith('pm__'):return 'player_manager'
    if f.startswith('ctx__'):return 'context'
    if f.startswith('wx__'):return 'weather'
    return 'other'

def eligible_features(so):
    feats=[]
    train=so[period(so,'train')]
    for c in so.columns:
        if c in KEYS or c in {'odds','market_prob'}:continue
        if family(c)=='other':continue
        v=pd.to_numeric(train[c],errors='coerce')
        # Require broad pre-holdout coverage. Weather can be a little thinner,
        # but no sparse one-season confirmed-lineup/availability features enter.
        req=350 if family(c)!='weather' else 250
        if v.notna().sum()<req or v.nunique(dropna=True)<2:continue
        feats.append(c)
    return feats

def base_metrics(so,pb,era):
    m=pmask(so,pb)&period(so,era)
    return metrics(so[m])

def preeval(so,m,pb):
    out={}
    for era in ['train','validation']:
        z=metrics(so[m&period(so,era)])
        b=base_metrics(so,pb,era)
        if not z or not b:return None
        out[era]=z;out[era+'_baseline']=b;out[era+'_lift']=z['roi']-b['roi']
    pre_mask=period(so,'train')|period(so,'validation')
    z=metrics(so[m&pre_mask]);b=metrics(so[pmask(so,pb)&pre_mask])
    if not z or not b:return None
    out['preholdout']=z;out['preholdout_baseline']=b;out['preholdout_lift']=z['roi']-b['roi']
    return out

def gate_single(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return (
      tr['n']>=40 and va['n']>=30 and pre['n']>=100
      and tr['roi']>=.02 and va['roi']>=.01 and pre['roi']>=.025
      and e['train_lift']>=.015 and e['validation_lift']>=.005 and e['preholdout_lift']>=.015
      and pre['positive_season_ratio']>=.60
    )
def gate_pair(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return (
      tr['n']>=30 and va['n']>=24 and pre['n']>=80
      and tr['roi']>=.035 and va['roi']>=.02 and pre['roi']>=.04
      and e['train_lift']>=.025 and e['validation_lift']>=.01 and e['preholdout_lift']>=.025
      and pre['positive_season_ratio']>=.60
    )
def prescore(e):
    return min(e['train']['roi'],e['validation']['roi'])*math.sqrt(max(1,min(e['train']['n'],e['validation']['n'])))

def bootstrap(v,n=4000,seed=8421):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a))
    means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,size=len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]

def team_concentration(x,meta):
    z=x[['match_id','outcome']].merge(meta,on='match_id',how='left')
    if len(z)==0:return {'unique_teams':0,'top1_share':0,'top3_share':0,'top5':{}}
    if z.outcome.iloc[0]=='DRAW':
        counts=pd.concat([z.home_team,z.away_team]).value_counts()
    else:
        selected=np.where(z.outcome.eq('HOME'),z.home_team,z.away_team)
        counts=pd.Series(selected).value_counts()
    total=max(1,int(counts.sum()))
    return {
      'unique_teams':int(len(counts)),
      'top1_share':float(counts.iloc[0]/total) if len(counts) else 0,
      'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0,
      'top5':{str(k):int(v) for k,v in counts.head(5).items()}
    }

def yearly(x):
    out=[]
    for y,z in x.groupby('season'):
        m=metrics(z);out.append({'season':int(y),**m})
    return out

def robustness_single(so,r,meta):
    m=cond(so,r['feature'],r['op'],r['threshold'])&pmask(so,r['price_band'])
    x=so[m].copy()
    h=metrics(x[period(x,'holdout')]);full=metrics(x)
    hb=base_metrics(so,r['price_band'],'holdout')
    hold_lift=(h['roi']-hb['roi']) if h and hb else None
    vals=pd.to_numeric(so.loc[period(so,'train'),r['feature']],errors='coerce').dropna()
    sd=float(vals.std()) if len(vals)>1 else 0.0
    step=max(abs(float(r['threshold']))*.05,sd*.05,0.01)
    neigh=[]
    for t in [r['threshold']-2*step,r['threshold']-step,r['threshold'],r['threshold']+step,r['threshold']+2*step]:
        mm=cond(so,r['feature'],r['op'],t)&pmask(so,r['price_band'])
        eras={e:metrics(so[mm&period(so,e)]) for e in ['train','validation','holdout']}
        neigh.append({'threshold':float(t),**eras})
    stable=sum(1 for q in neigh if all(q[e] and q[e]['roi']>0 for e in ['train','validation','holdout']))
    ci=bootstrap(x.profit) if len(x) else [None,None]
    conc=team_concentration(x,meta)
    status='RESEARCH_ONLY'
    if (
      h and full and h['n']>=50 and full['n']>=180 and h['roi']>=.04
      and hold_lift is not None and hold_lift>=0
      and full['roi']>=.05 and full['positive_season_ratio']>=.70
      and ci[0] is not None and ci[0]>0 and stable>=4 and conc['top3_share']<.30
    ): status='PROSPECTIVE_PRIORITY_SHADOW'
    elif (
      h and full and h['n']>=35 and h['roi']>0 and full['positive_season_ratio']>=.65
      and stable>=3 and conc['top3_share']<.35
    ): status='SHADOW_WATCH'
    return {
      **{k:r[k] for k in ['outcome','price_band','feature','op','threshold','pre_score','family']},
      'status':status,'train':r['pre']['train'],'validation':r['pre']['validation'],
      'preholdout':r['pre']['preholdout'],'holdout':h,'holdout_baseline':hb,'holdout_lift':hold_lift,
      'full':full,'bootstrap95_roi':ci,'threshold_neighbors_positive_all_eras':stable,
      'threshold_neighbors':neigh,'team_concentration':conc,'yearly':yearly(x)
    }

def robustness_pair(so,r,meta):
    a,b=r['rule1'],r['rule2']
    m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,r['price_band'])
    x=so[m].copy();h=metrics(x[period(x,'holdout')]);full=metrics(x);hb=base_metrics(so,r['price_band'],'holdout')
    hold_lift=(h['roi']-hb['roi']) if h and hb else None
    ci=bootstrap(x.profit,seed=9107) if len(x) else [None,None]
    conc=team_concentration(x,meta)
    # Pairs are already much more multiple-tested; require stronger evidence.
    status='RESEARCH_ONLY'
    if (
      h and full and h['n']>=45 and full['n']>=150 and h['roi']>=.05
      and hold_lift is not None and hold_lift>=.01
      and full['roi']>=.07 and full['positive_season_ratio']>=.70
      and ci[0] is not None and ci[0]>0 and conc['top3_share']<.30
    ):status='PROSPECTIVE_PRIORITY_SHADOW'
    elif h and full and h['n']>=30 and h['roi']>0 and full['positive_season_ratio']>=.65 and conc['top3_share']<.35:
        status='SHADOW_WATCH'
    return {
      **{k:r[k] for k in ['outcome','price_band','rule1','rule2','pre_score','families']},
      'status':status,'train':r['pre']['train'],'validation':r['pre']['validation'],
      'preholdout':r['pre']['preholdout'],'holdout':h,'holdout_baseline':hb,'holdout_lift':hold_lift,
      'full':full,'bootstrap95_roi':ci,'team_concentration':conc,'yearly':yearly(x)
    }

def main():
    path=pick();d=pd.read_parquet(path)
    s=merged_selection_rows(d)
    meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id')

    singles=[];tested=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy()
        tr=so[period(so,'train')]
        for f in eligible_features(so):
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    cm=cond(so,f,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested+=1;m=cm&pmask(so,pb);e=preeval(so,m,pb)
                        if e and gate_single(e):
                            singles.append({
                              'outcome':outcome,'price_band':pb,'feature':f,'op':op,'threshold':t,
                              'family':family(f),'pre_score':prescore(e),'pre':e
                            })

    singles.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in singles:
        k=(r['outcome'],r['price_band'],r['feature'])
        if k in seen:continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=160:break

    single_results=[]
    for r in frozen:
        so=s[s.outcome.eq(r['outcome'])].copy()
        single_results.append(robustness_single(so,r,meta))

    # Pair only preholdout-frozen single rules, and require cross-family pairs to
    # reduce redundant threshold mining.
    groups=defaultdict(list)
    for r in frozen:groups[(r['outcome'],r['price_band'])].append(r)
    pairs=[];tested_pairs=0
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:35],2):
            if a['feature']==b['feature'] or a['family']==b['family']:continue
            tested_pairs+=1
            m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,pb)
            e=preeval(so,m,pb)
            if e and gate_pair(e):
                pairs.append({
                  'outcome':outcome,'price_band':pb,
                  'rule1':{k:a[k] for k in ['feature','op','threshold','family']},
                  'rule2':{k:b[k] for k in ['feature','op','threshold','family']},
                  'families':sorted({a['family'],b['family']}),'pre_score':prescore(e),'pre':e
                })
    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    fp=[];seen=set()
    for r in pairs:
        k=(r['outcome'],r['price_band'],tuple(sorted([r['rule1']['feature'],r['rule2']['feature']])))
        if k in seen:continue
        seen.add(k);fp.append(r)
        if len(fp)>=100:break
    pair_results=[]
    for r in fp:
        so=s[s.outcome.eq(r['outcome'])].copy()
        pair_results.append(robustness_pair(so,r,meta))

    priorities=[x for x in single_results+pair_results if x['status']=='PROSPECTIVE_PRIORITY_SHADOW']
    watches=[x for x in single_results+pair_results if x['status']=='SHADOW_WATCH']
    # Preserve preholdout ranking within type; do not rank by holdout ROI.
    payload={
      'built_at':now(),'dataset':str(path),'selection_rows':len(s),'splits':SPLIT,
      'tested_single_contexts':tested,'preholdout_single_survivors':len(singles),'frozen_singles':len(frozen),
      'tested_cross_family_pairs':tested_pairs,'preholdout_pair_survivors':len(pairs),'frozen_pairs':len(fp),
      'prospective_priority_count':len(priorities),'shadow_watch_count':len(watches),
      'status':'SHADOW_RESEARCH_ONLY',
      'design':'All thresholds and rankings are frozen using 2013-18 train + 2019-22 validation. 2023-25 is opened only afterward. Candidate ROI must beat the same outcome/price-band baseline pre-holdout. Sparse confirmed-XI and availability features are excluded from this long-history scan.',
      'prospective_priority_preholdout_order':priorities,
      'shadow_watch_preholdout_order':watches,
      'all_frozen_singles_preholdout_order':single_results,
      'all_frozen_pairs_preholdout_order':pair_results,
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({k:payload[k] for k in [
      'selection_rows','tested_single_contexts','preholdout_single_survivors','frozen_singles',
      'tested_cross_family_pairs','preholdout_pair_survivors','frozen_pairs',
      'prospective_priority_count','shadow_watch_count'
    ]},indent=2))
    for i,x in enumerate(priorities,1):
        if 'feature' in x:
            print('PRIORITY',i,x['outcome'],x['price_band'],x['feature'],x['op'],x['threshold'],
                  'HOLD',x['holdout'],'FULL',x['full'],'CI',x['bootstrap95_roi'])
        else:
            print('PRIORITY_PAIR',i,x['outcome'],x['price_band'],x['rule1'],x['rule2'],
                  'HOLD',x['holdout'],'FULL',x['full'],'CI',x['bootstrap95_roi'])

if __name__=='__main__':
    main()
