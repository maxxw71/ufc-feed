#!/usr/bin/env python3
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np,pandas as pd
import mls_master_method_discovery as md

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_master.parquet'
OUT=ROOT/'research/master_fast';OUT.mkdir(parents=True,exist_ok=True)

def met(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum']);a=by[by['count']>=5]
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
            'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
            'positive_season_ratio':float((a['sum']>0).mean()) if len(a) else 0.0}

def main():
    d=pd.read_parquet(DATA)
    if d.shape[0]!=9440 or d.shape[1]<1034:raise RuntimeError(f'stale master {d.shape}')
    s,fmap=md.build_selection_matrix(d)
    pbs=md.PRICE_BANDS
    out=[];tested=0
    for outcome in ['HOME','DRAW','AWAY']:
      so=s[s.outcome.eq(outcome)].copy()
      trmask=md.period(so,'train');vamask=md.period(so,'validation');homask=md.period(so,'holdout')
      for f,fam in fmap.items():
        if fam not in md.LONG_FAMILIES or f not in so:continue
        if outcome=='DRAW' and f.endswith(('__sel','__opp','__edge')):continue
        if outcome!='DRAW' and f.endswith(('__balance','__combined')):continue
        trv=pd.to_numeric(so.loc[trmask,f],errors='coerce');vav=pd.to_numeric(so.loc[vamask,f],errors='coerce')
        if trv.notna().sum()<300 or vav.notna().sum()<220 or trv.nunique(dropna=True)<2:continue
        qs=sorted(set(float(x) for x in trv.quantile([.25,.5,.75]).dropna()))
        v=pd.to_numeric(so[f],errors='coerce')
        for t in qs:
          for op in ['>=','<=']:
            cm=v.ge(t) if op=='>=' else v.le(t)
            for pb,lo,hi in pbs:
              tested+=1
              price=so.market_prob.between(lo,hi,inclusive='both')
              x=so[cm&price]
              tr=met(x[md.period(x,'train')]);va=met(x[md.period(x,'validation')])
              if not tr or not va or tr['n']<40 or va['n']<30 or tr['roi']<.02 or va['roi']<.01:continue
              base_tr=met(so[price&trmask]);base_va=met(so[price&vamask])
              if not base_tr or not base_va:continue
              if tr['roi']-base_tr['roi']<.01 or va['roi']-base_va['roi']<0:continue
              ho=met(x[md.period(x,'holdout')]);fu=met(x)
              if not ho or ho['n']<30:continue
              base_ho=met(so[price&homask])
              lift=ho['roi']-base_ho['roi'] if base_ho else None
              status='FAIL_HOLDOUT'
              if ho['roi']>0 and lift is not None and lift>=0 and fu['positive_season_ratio']>=.65:status='HOLDOUT_SURVIVOR'
              if ho['n']>=45 and ho['roi']>=.05 and lift is not None and lift>=0 and fu['n']>=180 and fu['roi']>=.05 and fu['positive_season_ratio']>=.70:status='STRONG'
              out.append({'outcome':outcome,'price_band':pb,'feature':f,'family':fam,'op':op,'threshold':t,
                          'train':tr,'validation':va,'holdout':ho,'holdout_baseline':base_ho,'holdout_lift':lift,'full':fu,'status':status})
    survivors=[r for r in out if r['status'] in {'HOLDOUT_SURVIVOR','STRONG'}]
    # Preholdout-based sort to avoid holdout ranking.
    survivors.sort(key=lambda r:(min(r['train']['roi'],r['validation']['roi'])*math.sqrt(min(r['train']['n'],r['validation']['n'])),r['train']['n']+r['validation']['n']),reverse=True)
    ded=[];seen=set()
    for r in survivors:
      k=(r['outcome'],r['price_band'],r['feature'])
      if k in seen:continue
      seen.add(k);ded.append(r)
      if len(ded)>=60:break
    payload={'built_at':pd.Timestamp.now('UTC').isoformat(),'master_shape':list(d.shape),'selection_rows':len(s),
             'tested':tested,'survivor_count':len(survivors),'deduped_count':len(ded),'results':ded,
             'design':'Fast single-feature screen only. Thresholds from 2013-18; 2019-22 validation required before 2023-25 holdout. Sorted by preholdout score, never holdout ROI.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({'tested':tested,'survivor_count':len(survivors),'deduped_count':len(ded)},indent=2))
    for i,r in enumerate(ded[:30],1):
      print('FINDING',i,r['status'],r['outcome'],r['price_band'],r['family'],r['feature'],r['op'],r['threshold'],
            'train',round(r['train']['roi'],4),r['train']['n'],'val',round(r['validation']['roi'],4),r['validation']['n'],
            'hold',round(r['holdout']['roi'],4),r['holdout']['n'],'lift',round(r['holdout_lift'],4) if r['holdout_lift'] is not None else None,
            'full',round(r['full']['roi'],4),r['full']['n'])
if __name__=='__main__':main()
