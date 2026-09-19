#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_player_enriched.parquet'
OUT=ROOT/'research/player_manager'
OUT.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2013,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
    ('ALL',0.0,1.0),
    ('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),('P35_45',.35,.45),
    ('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70),
]
BASE_FEATURES=[
    'player_active_players5',
    'player_top11_minutes_share5',
    'player_top3_xgi_share5',
    'player_top3_gplus_share5',
    'player_weighted_age5',
    'player_starter_proxy_continuity',
    'player_gk_continuity3',
    'player_high_load_players3',
    'player_minutes_entropy5',
    'manager_prior_games',
    'manager_prior_ppg',
    'manager_prior_gdpg',
    'manager_new3',
]

def now():
    return datetime.now(timezone.utc).isoformat()

def metrics(x):
    if x.empty:
        return None
    by=x.groupby('season').profit.agg(['count','sum'])
    active=by[by['count']>=5]
    return {
        'n':int(len(x)),
        'wins':int(x.win.sum()),
        'losses':int(len(x)-x.win.sum()),
        'win_rate':float(x.win.mean()),
        'roi':float(x.profit.mean()),
        'units':float(x.profit.sum()),
        'active_seasons':int(len(active)),
        'positive_seasons':int((active['sum']>0).sum()),
        'positive_season_ratio':float((active['sum']>0).mean()) if len(active) else 0.0,
    }

def period(df,name):
    lo,hi=SPLIT[name]
    return df.season.between(lo,hi)

def price_mask(df,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return df.market_prob.between(lo,hi,inclusive='both')

def add_side_features(z,base,sel_side,opp_side,outcome):
    for name in BASE_FEATURES:
        hs='home_'+name
        aw='away_'+name
        if hs not in base.columns or aw not in base.columns:
            continue
        if outcome in {'HOME','AWAY'}:
            sel=base[hs] if sel_side=='home' else base[aw]
            opp=base[aw] if opp_side=='away' else base[hs]
            z['sel_'+name]=sel
            z['opp_'+name]=opp
            z['edge_'+name]=pd.to_numeric(sel,errors='coerce')-pd.to_numeric(opp,errors='coerce')
        else:
            h=pd.to_numeric(base[hs],errors='coerce')
            a=pd.to_numeric(base[aw],errors='coerce')
            z['balance_'+name]=(h-a).abs()
            z['combined_'+name]=h+a

def selection_rows(d):
    required={'odds_matched','asa_game_available','asa_knockout_game','home_player_recent_games','away_player_recent_games'}
    missing=required-set(d.columns)
    if missing:
        raise RuntimeError('Missing player research columns: '+','.join(sorted(missing)))

    base=d[
        d.odds_matched.eq(True)
        & d.season.between(2013,2025)
        & d.asa_game_available.eq(True)
        & d.asa_knockout_game.eq(False)
        & pd.to_numeric(d.home_player_recent_games,errors='coerce').ge(5)
        & pd.to_numeric(d.away_player_recent_games,errors='coerce').ge(5)
    ].copy()

    rows=[]
    for outcome in ['HOME','DRAW','AWAY']:
        z=pd.DataFrame(index=base.index)
        z['match_id']=base.match_id
        z['date']=pd.to_datetime(base.date,errors='coerce')
        z['season']=base.season
        z['outcome']=outcome
        if outcome=='HOME':
            z['win']=base.home_win
            z['odds']=base.home_odds
            z['market_prob']=base.home_novig_prob
            add_side_features(z,base,'home','away',outcome)
        elif outcome=='AWAY':
            z['win']=base.away_win
            z['odds']=base.away_odds
            z['market_prob']=base.away_novig_prob
            add_side_features(z,base,'away','home',outcome)
        else:
            z['win']=base.draw
            z['odds']=base.draw_odds
            z['market_prob']=base.draw_novig_prob
            add_side_features(z,base,'home','away',outcome)
        z['profit']=np.where(z.win.eq(1),z.odds-1.0,-1.0)
        rows.append(z.reset_index(drop=True))
    return pd.concat(rows,ignore_index=True)

def eligible_features(df,outcome):
    exclude={'match_id','date','season','outcome','win','odds','market_prob','profit'}
    feats=[]
    for c in df.columns:
        if c in exclude:
            continue
        if outcome=='DRAW' and not c.startswith(('balance_','combined_')):
            continue
        if outcome!='DRAW' and c.startswith(('balance_','combined_')):
            continue
        v=pd.to_numeric(df[c],errors='coerce')
        if v.notna().sum()<300 or v.nunique(dropna=True)<2:
            continue
        feats.append(c)
    return feats

def thresholds(series):
    v=pd.to_numeric(series,errors='coerce').dropna()
    if v.nunique()<2:
        return []
    if v.nunique()<=5:
        return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))

def cond(df,feature,op,t):
    v=pd.to_numeric(df[feature],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)

def preholdout_eval(df,mask):
    tr=metrics(df[mask&period(df,'train')])
    va=metrics(df[mask&period(df,'validation')])
    if not tr or not va:
        return None
    pre=metrics(df[mask&(period(df,'train')|period(df,'validation'))])
    if not pre:
        return None
    return {'train':tr,'validation':va,'preholdout':pre}

def single_gate(ev):
    tr,va,pre=ev['train'],ev['validation'],ev['preholdout']
    return (
        tr['n']>=30 and va['n']>=22 and pre['n']>=75
        and tr['roi']>=.025 and va['roi']>=.015 and pre['roi']>=.025
        and pre['positive_season_ratio']>=.55
    )

def pair_gate(ev):
    tr,va,pre=ev['train'],ev['validation'],ev['preholdout']
    return (
        tr['n']>=25 and va['n']>=18 and pre['n']>=60
        and tr['roi']>=.04 and va['roi']>=.03 and pre['roi']>=.045
        and pre['positive_season_ratio']>=.60
    )

def pre_score(ev):
    tr,va=ev['train'],ev['validation']
    return min(tr['roi'],va['roi'])*math.sqrt(max(1,min(tr['n'],va['n'])))

def holdout_eval(df,mask):
    return metrics(df[mask&period(df,'holdout')])

def full_eval(df,mask):
    return metrics(df[mask])

def main():
    if not DATA.exists():
        raise RuntimeError('Player-enriched MLS warehouse missing')
    d=pd.read_parquet(DATA)
    s=selection_rows(d)

    singles=[]
    tested_singles=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy()
        train=so[period(so,'train')]
        for feature in eligible_features(so,outcome):
            for t in thresholds(train[feature]):
                for op in ['>=','<=']:
                    cm=cond(so,feature,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested_singles+=1
                        m=cm&price_mask(so,pb)
                        ev=preholdout_eval(so,m)
                        if ev and single_gate(ev):
                            singles.append({
                                'outcome':outcome,'price_band':pb,'feature':feature,'op':op,'threshold':t,
                                'pre_score':pre_score(ev),'pre':ev,
                            })

    singles.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen_singles=[]
    seen=set()
    for r in singles:
        key=(r['outcome'],r['price_band'],r['feature'])
        if key in seen:
            continue
        seen.add(key)
        frozen_singles.append(r)
        if len(frozen_singles)>=90:
            break

    # Pair construction and ranking remain entirely pre-holdout.
    pairs=[]
    tested_pairs=0
    grouped={}
    for r in frozen_singles:
        grouped.setdefault((r['outcome'],r['price_band']),[]).append(r)
    for (outcome,pb),rules in grouped.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:28],2):
            if a['feature']==b['feature']:
                continue
            tested_pairs+=1
            m=(
                cond(so,a['feature'],a['op'],a['threshold'])
                & cond(so,b['feature'],b['op'],b['threshold'])
                & price_mask(so,pb)
            )
            ev=preholdout_eval(so,m)
            if ev and pair_gate(ev):
                pairs.append({
                    'outcome':outcome,'price_band':pb,
                    'rule1':{k:a[k] for k in ['feature','op','threshold']},
                    'rule2':{k:b[k] for k in ['feature','op','threshold']},
                    'pre_score':pre_score(ev),'pre':ev,
                })

    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen_pairs=[]
    seen_pairs=set()
    for r in pairs:
        names=tuple(sorted([r['rule1']['feature'],r['rule2']['feature']]))
        key=(r['outcome'],r['price_band'],names)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        frozen_pairs.append(r)
        if len(frozen_pairs)>=60:
            break

    # Only now open the 2023-25 holdout.
    def open_single(r):
        so=s[s.outcome.eq(r['outcome'])].copy()
        m=cond(so,r['feature'],r['op'],r['threshold'])&price_mask(so,r['price_band'])
        h=holdout_eval(so,m)
        f=full_eval(so,m)
        return {**r,'holdout':h,'full':f,
                'holdout_survived':bool(h and h['n']>=20 and h['roi']>0 and h['units']>0)}

    def open_pair(r):
        so=s[s.outcome.eq(r['outcome'])].copy()
        a,b=r['rule1'],r['rule2']
        m=(cond(so,a['feature'],a['op'],a['threshold'])
           & cond(so,b['feature'],b['op'],b['threshold'])
           & price_mask(so,r['price_band']))
        h=holdout_eval(so,m)
        f=full_eval(so,m)
        return {**r,'holdout':h,'full':f,
                'holdout_survived':bool(h and h['n']>=18 and h['roi']>0 and h['units']>0)}

    opened_singles=[open_single(r) for r in frozen_singles]
    opened_pairs=[open_pair(r) for r in frozen_pairs]

    def compact(r):
        out={k:v for k,v in r.items() if k not in {'pre'}}
        out['train']=r['pre']['train']
        out['validation']=r['pre']['validation']
        out['preholdout']=r['pre']['preholdout']
        return out

    # Preserve frozen pre-holdout order. Do NOT re-rank by holdout.
    singles_json=[compact(r) for r in opened_singles]
    pairs_json=[compact(r) for r in opened_pairs]

    payload={
        'built_at':now(),
        'dataset':str(DATA),
        'selection_rows':len(s),
        'feature_columns_tested':sorted(set(r['feature'] for r in frozen_singles)),
        'tested_single_contexts':tested_singles,
        'preholdout_single_survivors':len(singles),
        'frozen_single_candidates':len(frozen_singles),
        'single_holdout_survivors':sum(r['holdout_survived'] for r in opened_singles),
        'tested_pair_contexts':tested_pairs,
        'preholdout_pair_survivors':len(pairs),
        'frozen_pair_candidates':len(frozen_pairs),
        'pair_holdout_survivors':sum(r['holdout_survived'] for r in opened_pairs),
        'splits':SPLIT,
        'selection_rule':'Thresholds/rankings frozen using train+validation only; 2023-25 holdout opened afterward and never used to retune.',
        'status':'SHADOW_RESEARCH_ONLY',
        'top_singles_preholdout_order':singles_json[:30],
        'top_pairs_preholdout_order':pairs_json[:30],
        'all_holdout_surviving_singles_preholdout_order':[x for x in singles_json if x['holdout_survived']],
        'all_holdout_surviving_pairs_preholdout_order':[x for x in pairs_json if x['holdout_survived']],
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=[
        'MLS PLAYER/MANAGER SHADOW RESEARCH',
        '='*100,
        f"selection_rows={len(s):,} tested_singles={tested_singles:,} frozen_singles={len(frozen_singles)} holdout_survivors={payload['single_holdout_survivors']}",
        f"tested_pairs={tested_pairs:,} frozen_pairs={len(frozen_pairs)} holdout_survivors={payload['pair_holdout_survivors']}",
        'Splits: 2013-18 train | 2019-22 validation | 2023-25 holdout.',
        'Frozen order is based only on train+validation. Holdout is reported, never used to tune thresholds.',
        'SHADOW ONLY — no promotion to live/email/website.',
        '',
        'TOP FROZEN SINGLES',
    ]
    for i,r in enumerate(opened_singles[:20],1):
        h=r['holdout'] or {}
        pre=r['pre']['preholdout']
        lines.append(
            f"{i:02d}. {r['outcome']} {r['price_band']} | {r['feature']} {r['op']} {r['threshold']:.6g} "
            f"| PRE n={pre['n']} ROI={pre['roi']:+.1%} train={r['pre']['train']['roi']:+.1%} val={r['pre']['validation']['roi']:+.1%} "
            f"| HOLD n={h.get('n',0)} ROI={h.get('roi',float('nan')):+.1%} survived={r['holdout_survived']}"
        )
    lines+=['','TOP FROZEN PAIRS']
    for i,r in enumerate(opened_pairs[:20],1):
        h=r['holdout'] or {}
        pre=r['pre']['preholdout']
        a,b=r['rule1'],r['rule2']
        lines.append(
            f"{i:02d}. {r['outcome']} {r['price_band']} | {a['feature']} {a['op']} {a['threshold']:.6g} AND "
            f"{b['feature']} {b['op']} {b['threshold']:.6g} "
            f"| PRE n={pre['n']} ROI={pre['roi']:+.1%} train={r['pre']['train']['roi']:+.1%} val={r['pre']['validation']['roi']:+.1%} "
            f"| HOLD n={h.get('n',0)} ROI={h.get('roi',float('nan')):+.1%} survived={r['holdout_survived']}"
        )
    report='\n'.join(lines)+'\n'
    (OUT/'report.txt').write_text(report)
    print(report)

if __name__=='__main__':
    main()
