#!/usr/bin/env python3
from __future__ import annotations

import json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_master_method_discovery as md
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
MASTER=ROOT/'data/processed/mls_match_features_master.parquet'
RAW_XLSX=ROOT/'data/raw/mls_betting_xlsx.xlsx'
MOVEMENT=ROOT/'data/processed/mls_historical_multibook_movement_long.parquet'
OUT=ROOT/'research/draw_candidate_validation'
OUT.mkdir(parents=True,exist_ok=True)

CANDIDATES=[
 {
  'id':'MLS-D01','name':'Balanced Starter Continuity Draw','band':'ALL',
  'feature':'new__player_manager__player_starter_proxy_continuity__balance','op':'<=','threshold':0.07965686274509803
 },
 {
  'id':'MLS-D02','name':'Weak Shooting-Momentum Draw','band':'P25_35',
  'feature':'new__style__style_delta3_prev5_gplus_shooting__combined','op':'<=','threshold':-0.0830052365333333
 },
 {
  'id':'MLS-D03','name':'Similar GK Concession Draw','band':'P25_35',
  'feature':'new__goalkeeper_roster__gk_goals_conceded_p96_5__balance','op':'<=','threshold':0.2094130089899524
 },
]
BANDS={'ALL':(0,1),'P25_35':(.25,.35)}
QUOTE_COLS={
 'pinnacle_close':'PSCD',
 'max_close':'MaxCD',
 'average_close':'AvgCD',
 'betfair_exchange_close':'BFECD',
 'bet365_close':'B365CD',
}

def now(): return datetime.now(timezone.utc).isoformat()

def metrics_profit(p, wins=None):
    p=pd.to_numeric(pd.Series(p),errors='coerce').dropna()
    if p.empty:return None
    out={'n':int(len(p)),'roi':float(p.mean()),'units':float(p.sum())}
    if wins is not None:
        w=pd.to_numeric(pd.Series(wins),errors='coerce').loc[p.index]
        out['wins']=int(w.sum());out['losses']=int(len(w)-w.sum());out['win_rate']=float(w.mean())
    return out

def boot(p,n=5000,seed=20261002):
    a=np.asarray(pd.to_numeric(pd.Series(p),errors='coerce').dropna(),float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a))
    means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]

def period_mask(df,label):
    return {
      'train':df.season.between(2013,2018),
      'validation':df.season.between(2019,2022),
      'holdout':df.season.between(2023,2025),
    }[label]

def band_mask(df,band):
    lo,hi=BANDS[band]
    return pd.to_numeric(df.market_prob,errors='coerce').between(lo,hi,inclusive='both')

def rule_mask(df,c,t=None):
    t=c['threshold'] if t is None else t
    v=pd.to_numeric(df[c['feature']],errors='coerce')
    m=v.le(t) if c['op']=='<=' else v.ge(t)
    return m&band_mask(df,c['band'])

def eval_main(z):
    out={}
    for label in ['train','validation','holdout']:
        q=z[period_mask(z,label)]
        out[label]=metrics_profit(q.profit,q.win)
    out['full']=metrics_profit(z.profit,z.win)
    out['bootstrap95_roi']=boot(z.profit)
    return out

def yearly(z):
    out=[]
    for y,g in z.groupby('season'):
        m=metrics_profit(g.profit,g.win)
        out.append({'season':int(y),**m})
    return out

def team_concentration(z,meta):
    q=z[['match_id']].merge(meta,on='match_id',how='left')
    counts=pd.concat([q.home_team,q.away_team]).value_counts()
    total=max(1,int(counts.sum()))
    return {
      'unique_teams':int(len(counts)),
      'top1_share':float(counts.iloc[0]/total) if len(counts) else 0,
      'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0,
      'top5':{str(k):int(v) for k,v in counts.head(5).items()}
    }

def threshold_neighbors(draws,c):
    tr=pd.to_numeric(draws.loc[period_mask(draws,'train'),c['feature']],errors='coerce').dropna()
    sd=float(tr.std()) if len(tr)>1 else 0.0
    step=max(abs(float(c['threshold']))*.05,sd*.05,.005)
    rows=[]
    for t in [c['threshold']-2*step,c['threshold']-step,c['threshold'],c['threshold']+step,c['threshold']+2*step]:
        q=draws[rule_mask(draws,c,t)]
        rows.append({'threshold':float(t),**eval_main(q)})
    positive_all=sum(
      1 for r in rows
      if all(r[x] and r[x]['roi']>0 for x in ['train','validation','holdout'])
    )
    return {'step':step,'positive_all_eras':positive_all,'neighbors':rows}

def norm_date(x):
    return pd.to_datetime(x,errors='coerce').dt.normalize()

def load_workbook_quotes(master_meta):
    if not RAW_XLSX.exists():return pd.DataFrame(),{'status':'MISSING_XLSX'}
    book=pd.read_excel(RAW_XLSX,sheet_name=None)
    raw=pd.concat([x.assign(_sheet=str(k)) for k,x in book.items()],ignore_index=True)
    need=['Date','Home','Away']
    if not all(c in raw for c in need):return pd.DataFrame(),{'status':'MISSING_KEYS','columns':list(raw.columns)}
    q=pd.DataFrame({
      'qdate':norm_date(raw['Date']),
      'qhome':raw['Home'].map(canon_team),
      'qaway':raw['Away'].map(canon_team),
    })
    for name,col in QUOTE_COLS.items():
        q[name]=pd.to_numeric(raw[col],errors='coerce') if col in raw else np.nan
    q=q[q.qdate.notna()].drop_duplicates(['qdate','qhome','qaway'],keep='last')
    m=master_meta.copy()
    m['qdate']=norm_date(m.date)
    exact=m.merge(q,on=['qdate'],how='left')
    exact=exact[(exact.home_team==exact.qhome)&(exact.away_team==exact.qaway)]
    exact=exact.drop_duplicates('match_id')
    return exact[['match_id']+list(QUOTE_COLS)],{
      'status':'OK','raw_rows':int(len(raw)),'mapped_matches':int(exact.match_id.nunique()),
      'available_quote_columns':[k for k in QUOTE_COLS if exact[k].notna().any()]
    }

def reprice(z,quote_map):
    q=z[['match_id','win']].merge(quote_map,on='match_id',how='left')
    out={}
    for name in QUOTE_COLS:
        if name not in q:continue
        odd=pd.to_numeric(q[name],errors='coerce')
        good=odd.gt(1)&odd.notna()
        if not good.any():continue
        p=np.where(q.loc[good,'win'].eq(1),odd[good]-1,-1)
        p=pd.Series(p,index=q.index[good])
        out[name]={**metrics_profit(p,q.loc[good,'win']),'bootstrap95_roi':boot(p,seed=20261020)}
    return out

def movement_reprice(z):
    if not MOVEMENT.exists():return {'status':'MISSING'}
    ids=set(z.match_id.astype(str))
    mv=pd.read_parquet(MOVEMENT)
    mv['match_id']=mv.match_id.astype(str)
    mv=mv[mv.match_id.isin(ids)&pd.to_numeric(mv.hour_before,errors='coerce').eq(0)].copy()
    if mv.empty:return {'status':'NO_MATCHES'}
    winners=z[['match_id','win']].copy();winners.match_id=winners.match_id.astype(str)
    out={'status':'OK','matches':int(mv.match_id.nunique()),'book_rows':int(len(mv))}
    pivot=mv.groupby('match_id').draw_odds.agg(['mean','median','max']).reset_index()
    pivot=pivot.merge(winners,on='match_id',how='inner')
    for col in ['mean','median','max']:
        odd=pd.to_numeric(pivot[col],errors='coerce');good=odd.gt(1)&odd.notna()
        p=pd.Series(np.where(pivot.loc[good,'win'].eq(1),odd[good]-1,-1),index=pivot.index[good])
        out['all_books_'+col]={**metrics_profit(p,pivot.loc[good,'win']),'bootstrap95_roi':boot(p,seed=20261030)}
    per={}
    for book in ['Pinnacle Sports','bet365','William Hill','Betfair Sports','Unibet','SBOBET']:
        b=mv[mv.bookmaker.eq(book)][['match_id','draw_odds']].drop_duplicates('match_id').merge(winners,on='match_id',how='inner')
        odd=pd.to_numeric(b.draw_odds,errors='coerce');good=odd.gt(1)&odd.notna()
        if not good.any():continue
        p=pd.Series(np.where(b.loc[good,'win'].eq(1),odd[good]-1,-1),index=b.index[good])
        per[book]={**metrics_profit(p,b.loc[good,'win']),'bootstrap95_roi':boot(p,seed=20261040)}
    out['books']=per
    return out

def external_pinnacle_reprice(z,master):
    col='external_pinnacle_close_draw_odds'
    if col not in master:return None
    q=z[['match_id','win']].merge(master[['match_id',col]],on='match_id',how='left')
    odd=pd.to_numeric(q[col],errors='coerce');good=odd.gt(1)&odd.notna()
    if not good.any():return None
    p=pd.Series(np.where(q.loc[good,'win'].eq(1),odd[good]-1,-1),index=q.index[good])
    return {**metrics_profit(p,q.loc[good,'win']),'bootstrap95_roi':boot(p,seed=20261050)}

def overlap_analysis(draws,sets):
    ids=sorted(set().union(*sets.values()))
    base=draws[['match_id','season','win','profit']].drop_duplicates('match_id').set_index('match_id')
    rows=[]
    membership={}
    for mid in ids:
        methods=[k for k,s in sets.items() if mid in s]
        membership[mid]=methods
    for count in [1,2,3]:
        mids=[m for m,ms in membership.items() if len(ms)==count]
        q=base.loc[base.index.intersection(mids)]
        rows.append({'candidate_count':count,'matches':len(q),'metrics':metrics_profit(q.profit,q.win) if len(q) else None})
    pair=[]
    names=list(sets)
    for i,a in enumerate(names):
        for b in names[i+1:]:
            inter=sets[a]&sets[b]
            union=sets[a]|sets[b]
            pair.append({'a':a,'b':b,'intersection':len(inter),'jaccard':len(inter)/max(1,len(union)),
                         'pct_smaller':len(inter)/max(1,min(len(sets[a]),len(sets[b])))})
    triple=set.intersection(*sets.values()) if sets else set()
    q=base.loc[base.index.intersection(triple)]
    return {'pairwise':pair,'triple_intersection':len(triple),
            'triple_metrics':metrics_profit(q.profit,q.win) if len(q) else None,
            'by_candidate_count':rows}

def main():
    if not MASTER.exists():raise RuntimeError('Missing MLS master warehouse')
    d=pd.read_parquet(MASTER).copy();d['match_id']=d.match_id.astype(str)
    s,fmap=md.build_selection_matrix(d)
    draws=s[s.outcome.eq('DRAW')].copy();draws['match_id']=draws.match_id.astype(str)
    meta=d[['match_id','date','home_team','away_team']].drop_duplicates('match_id').copy()
    meta['match_id']=meta.match_id.astype(str)
    quote_map,quote_meta=load_workbook_quotes(meta)
    if len(quote_map):quote_map['match_id']=quote_map.match_id.astype(str)

    results=[];sets={}
    for c in CANDIDATES:
        if c['feature'] not in draws:raise RuntimeError(f"Missing candidate feature {c['feature']}")
        z=draws[rule_mask(draws,c)].copy()
        sets[c['id']]=set(z.match_id)
        main=eval_main(z)
        res={**c,'main_pricing':main,'yearly':yearly(z),'team_concentration':team_concentration(z,meta),
             'threshold_stability':threshold_neighbors(draws,c),'workbook_repricing':reprice(z,quote_map) if len(quote_map) else {},
             'external_pinnacle_repricing':external_pinnacle_reprice(z,d),'movement_close_repricing':movement_reprice(z)}
        res['robustness_flags']={
          'bootstrap_lower_gt_0':bool(main['bootstrap95_roi'][0] is not None and main['bootstrap95_roi'][0]>0),
          'neighbors_positive_all_eras_ge_4':bool(res['threshold_stability']['positive_all_eras']>=4),
          'team_top3_share_lt_30pct':bool(res['team_concentration']['top3_share']<.30),
          'holdout_positive':bool(main['holdout'] and main['holdout']['roi']>0),
        }
        results.append(res)

    overlap=overlap_analysis(draws,sets)
    payload={'built_at':now(),'status':'SHADOW_VALIDATION_ONLY','master_shape':list(d.shape),
             'fixed_candidates':[x['id'] for x in CANDIDATES],
             'quote_mapping':quote_meta,'results':results,'overlap':overlap,
             'policy':'Rules are fixed from the prior frozen master screen. This job does not retune thresholds using holdout or 2026 and cannot promote a method.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=['MLS FIXED DRAW CANDIDATE VALIDATION','='*100,
           f"master={len(d):,} x {len(d.columns):,} | quote mapping={quote_meta}",'']
    for r in results:
        m=r['main_pricing']
        lines.append(f"{r['id']} {r['name']}")
        lines.append(f"  full n={m['full']['n']} ROI={m['full']['roi']:+.1%} | train={m['train']['roi']:+.1%} | validation={m['validation']['roi']:+.1%} | holdout={m['holdout']['roi']:+.1%}")
        lines.append(f"  bootstrap95={m['bootstrap95_roi']} | neighbor-positive-all-eras={r['threshold_stability']['positive_all_eras']}/5 | top3-team-share={r['team_concentration']['top3_share']:.1%}")
        lines.append(f"  flags={r['robustness_flags']}")
        for src,v in r['workbook_repricing'].items():
            lines.append(f"  reprice {src}: n={v['n']} ROI={v['roi']:+.1%} CI={v['bootstrap95_roi']}")
        if r['external_pinnacle_repricing']:
            v=r['external_pinnacle_repricing'];lines.append(f"  external_pinnacle: n={v['n']} ROI={v['roi']:+.1%} CI={v['bootstrap95_roi']}")
        mv=r['movement_close_repricing']
        if mv.get('status')=='OK':
            lines.append(f"  movement subset: matches={mv['matches']} all-books-mean ROI={mv['all_books_mean']['roi']:+.1%}")
        lines.append('')
    lines.append('OVERLAP')
    for x in overlap['pairwise']:
        lines.append(f"  {x['a']} vs {x['b']}: intersection={x['intersection']} pct_smaller={x['pct_smaller']:.1%} jaccard={x['jaccard']:.1%}")
    lines.append(f"  triple intersection={overlap['triple_intersection']} metrics={overlap['triple_metrics']}")
    lines.append('')
    lines.append('SHADOW VALIDATION ONLY — NO LIVE PROMOTION.')
    (OUT/'latest.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':main()
