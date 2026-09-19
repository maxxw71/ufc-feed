#!/usr/bin/env python3
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np,pandas as pd
import mls_context_research as cr

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_context_enriched.parquet'
SRC=ROOT/'research/context/latest.json'
OUT=ROOT/'research/context_robustness';OUT.mkdir(parents=True,exist_ok=True)

def met(x):return cr.metrics(x)
def boot(v,n=5000,seed=2719):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed)
    means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,size=len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]
def yearly(x):
    out=[]
    for y,z in x.groupby('season'):
        m=met(z);out.append({'season':int(y),**m})
    return out
def thirds(x):
    x=x.sort_values('date').reset_index(drop=True);out=[]
    for i,idx in enumerate(np.array_split(np.arange(len(x)),3),1):
        z=x.iloc[idx];m=met(z);out.append({'third':i,**m} if m else {'third':i,'n':0})
    return out
def loo(x):
    out=[]
    for y in sorted(x.season.unique()):
        m=met(x[~x.season.eq(y)])
        out.append({'left_out':int(y),**m} if m else {'left_out':int(y),'n':0})
    return out
def exact(so,r,t=None,pb=None):
    return so[cr.cond(so,r['feature'],r['op'],r['threshold'] if t is None else t)&cr.pmask(so,r['price_band'] if pb is None else pb)].copy()

def main():
    d=pd.read_parquet(DATA);s=cr.selection_rows(d);src=json.loads(SRC.read_text())
    meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id')
    candidates=src.get('all_holdout_surviving_singles_preholdout_order') or []
    results=[]
    for r in candidates:
        so=s[s.outcome.eq(r['outcome'])].copy();x=exact(so,r)
        full=met(x);hold=met(x[cr.period(x,'holdout')]);train=met(x[cr.period(x,'train')]);val=met(x[cr.period(x,'validation')])
        vals=pd.to_numeric(so.loc[cr.period(so,'train'),r['feature']],errors='coerce').dropna()
        sd=float(vals.std()) if len(vals)>1 else 0
        step=max(abs(float(r['threshold']))*.05,sd*.05,0.01)
        neigh=[]
        for t in [r['threshold']-2*step,r['threshold']-step,r['threshold'],r['threshold']+step,r['threshold']+2*step]:
            z=exact(so,r,t=t)
            neigh.append({'threshold':float(t),'train':met(z[cr.period(z,'train')]),'validation':met(z[cr.period(z,'validation')]),
                          'holdout':met(z[cr.period(z,'holdout')]),'full':met(z)})
        pos_neighbors=sum(1 for q in neigh if q['train'] and q['validation'] and q['holdout'] and
                          q['train']['roi']>0 and q['validation']['roi']>0 and q['holdout']['roi']>0)
        prices=[]
        for pb,_,__ in cr.PRICE_BANDS:
            z=exact(so,r,pb=pb)
            if len(z):prices.append({'price_band':pb,'train':met(z[cr.period(z,'train')]),'validation':met(z[cr.period(z,'validation')]),
                                     'holdout':met(z[cr.period(z,'holdout')]),'full':met(z)})
        zm=x[['match_id','outcome']].merge(meta,on='match_id',how='left')
        if r['outcome']=='DRAW':
            counts=pd.concat([zm.home_team,zm.away_team]).value_counts()
        else:
            selected=np.where(zm.outcome.eq('HOME'),zm.home_team,zm.away_team)
            counts=pd.Series(selected).value_counts()
        total=max(1,int(counts.sum()))
        conc={'unique_teams':int(len(counts)),'top1_share':float(counts.iloc[0]/total) if len(counts) else 0,
              'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0,
              'top5':{str(k):int(v) for k,v in counts.head(5).items()}}
        ci=boot(x.profit)
        status='RESEARCH_ONLY'
        if (hold and hold['n']>=50 and hold['roi']>=.03 and full and full['positive_season_ratio']>=.70 and
            ci[0] is not None and ci[0]>0 and pos_neighbors>=3 and conc['top3_share']<.35):
            status='PROSPECTIVE_PRIORITY_SHADOW'
        elif (hold and hold['n']>=30 and hold['roi']>0 and full and full['positive_season_ratio']>=.65 and pos_neighbors>=2):
            status='SHADOW_WATCH'
        results.append({k:r[k] for k in ['outcome','price_band','feature','op','threshold','pre_score'] if k in r}|
                       {'status':status,'train':train,'validation':val,'holdout':hold,'full':full,'bootstrap95_roi':ci,
                        'threshold_neighbors_positive_all_eras':pos_neighbors,'threshold_neighbors':neigh,'price_neighbors':prices,
                        'yearly':yearly(x),'thirds':thirds(x),'leave_one_season_out':loo(x),'team_concentration':conc})
    payload={'built_at':pd.Timestamp.utcnow().isoformat(),'status':'SHADOW_RESEARCH_ONLY',
             'candidate_count':len(results),'prospective_priority':sum(x['status']=='PROSPECTIVE_PRIORITY_SHADOW' for x in results),
             'shadow_watch':sum(x['status']=='SHADOW_WATCH' for x in results),'results':results,
             'note':'Robustness characterization only. No thresholds were retuned after holdout and nothing is promoted to official selections.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({'candidate_count':payload['candidate_count'],'prospective_priority':payload['prospective_priority'],'shadow_watch':payload['shadow_watch']},indent=2))
    for x in results:
        if x['status']!='RESEARCH_ONLY':
            print(x['status'],x['outcome'],x['price_band'],x['feature'],x['op'],x['threshold'],
                  'full',x['full'],'hold',x['holdout'],'ci',x['bootstrap95_roi'],'neighbors',x['threshold_neighbors_positive_all_eras'])

if __name__=='__main__':main()
