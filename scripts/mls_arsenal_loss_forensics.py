#!/usr/bin/env python3
from __future__ import annotations

import json, math
from pathlib import Path

import numpy as np
import pandas as pd

import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/arsenal_loss_forensics'
OUT.mkdir(parents=True,exist_ok=True)

def metrics(x):
    return arx.metrics(x)

def rule_mask(df,r):
    if 'feature' in r:
        return arx.cond(df,r['feature'],r['op'],r['threshold']) & arx.pmask(df,r['price_band'])
    a,b=r['rule1'],r['rule2']
    return (
        arx.cond(df,a['feature'],a['op'],a['threshold'])
        & arx.cond(df,b['feature'],b['op'],b['threshold'])
        & arx.pmask(df,r['price_band'])
    )

def zscore_diff(win,loss):
    w=pd.to_numeric(win,errors='coerce').dropna()
    l=pd.to_numeric(loss,errors='coerce').dropna()
    if len(w)<8 or len(l)<8:return None
    pooled=np.sqrt((w.var(ddof=1)+l.var(ddof=1))/2)
    if not np.isfinite(pooled) or pooled<=1e-12:return None
    return float((l.mean()-w.mean())/pooled)

def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=6:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.20,.35,.50,.65,.80]).dropna()))

def main():
    if not SRC.exists():
        raise RuntimeError('MLS arsenal research result missing')
    src=json.loads(SRC.read_text())
    data=Path(src['dataset'])
    d=pd.read_parquet(data)
    s=arx.merged_selection_rows(d)
    priorities=src.get('prospective_priority_preholdout_order') or []
    if not priorities:
        payload={'built_at':pd.Timestamp.utcnow().isoformat(),'priority_count':0,'methods':[],'status':'NO_PRIORITY_METHODS'}
        (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
        print(json.dumps(payload,indent=2))
        return

    results=[]
    for idx,r in enumerate(priorities,1):
        so=s[s.outcome.eq(r['outcome'])].copy()
        base=so[rule_mask(so,r)].copy()
        pre=base[arx.period(base,'train')|arx.period(base,'validation')].copy()
        hold=base[arx.period(base,'holdout')].copy()
        base_pre=metrics(pre);base_hold=metrics(hold)

        # Descriptive loss fingerprints across the frozen base method.
        used=set()
        if 'feature' in r:used.add(r['feature'])
        else:used.update([r['rule1']['feature'],r['rule2']['feature']])
        candidate_features=[]
        for c in so.columns:
            if c in arx.KEYS or c in {'odds','market_prob'} or c in used:continue
            if arx.family(c)=='other':continue
            v=pd.to_numeric(pre[c],errors='coerce')
            if v.notna().sum()<max(30,int(.6*len(pre))) or v.nunique(dropna=True)<2:continue
            candidate_features.append(c)

        fingerprints=[]
        wins=pre[pre.win.eq(1)]
        losses=pre[pre.win.eq(0)]
        for f in candidate_features:
            zd=zscore_diff(wins[f],losses[f])
            if zd is None:continue
            fingerprints.append({
                'feature':f,'family':arx.family(f),'loss_minus_win_z':zd,
                'win_mean':float(pd.to_numeric(wins[f],errors='coerce').mean()),
                'loss_mean':float(pd.to_numeric(losses[f],errors='coerce').mean()),
            })
        fingerprints.sort(key=lambda x:abs(x['loss_minus_win_z']),reverse=True)

        # Discover vetoes on pre-holdout only.
        vetoes=[]
        for f in [x['feature'] for x in fingerprints[:45]]:
            vals=pd.to_numeric(pre[f],errors='coerce')
            if vals.notna().sum()<40:continue
            for t in thresholds(vals):
                for op in ['>=','<=']:
                    risk=arx.cond(pre,f,op,t)
                    risk_m=metrics(pre[risk]);safe_m=metrics(pre[~risk])
                    if not risk_m or not safe_m:continue
                    if risk_m['n']<12 or safe_m['n']<50:continue
                    improvement=safe_m['roi']-base_pre['roi']
                    risk_drop=base_pre['roi']-risk_m['roi']
                    if improvement<.03 or risk_drop<.08:continue
                    score=improvement+.25*risk_drop+.005*math.sqrt(safe_m['n'])
                    vetoes.append({
                        'feature':f,'family':arx.family(f),'op':op,'threshold':t,'score':score,
                        'pre_base':base_pre,'pre_risk':risk_m,'pre_safe':safe_m
                    })
        vetoes.sort(key=lambda x:x['score'],reverse=True)

        frozen=[];seen=set()
        for v in vetoes:
            if v['feature'] in seen:continue
            seen.add(v['feature']);frozen.append(v)
            if len(frozen)>=12:break

        for v in frozen:
            risk=arx.cond(hold,v['feature'],v['op'],v['threshold'])
            risk_m=metrics(hold[risk]);safe_m=metrics(hold[~risk])
            v['hold_base']=base_hold;v['hold_risk']=risk_m;v['hold_safe']=safe_m
            v['validated']=bool(
                base_hold and safe_m and risk_m
                and safe_m['n']>=20
                and safe_m['roi']>0
                and safe_m['roi']>=base_hold['roi']+.015
                and risk_m['roi']<base_hold['roi']
            )

        # Team/season loss clusters are diagnostics only.
        meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id')
        z=base.merge(meta,on='match_id',how='left')
        z['selected_team']=np.where(z.outcome.eq('HOME'),z.home_team,np.where(z.outcome.eq('AWAY'),z.away_team,'DRAW'))
        team_diag=[]
        if r['outcome']!='DRAW':
            for team,g in z.groupby('selected_team'):
                if len(g)<8:continue
                m=metrics(g)
                team_diag.append({'team':str(team),**m})
            team_diag.sort(key=lambda x:(x['roi'], -x['n']))
        yearly=[{'season':int(y),**metrics(g)} for y,g in z.groupby('season')]

        results.append({
            'method_index':idx,
            'method':r,
            'base_preholdout':base_pre,
            'base_holdout':base_hold,
            'top_loss_fingerprints':fingerprints[:20],
            'frozen_vetoes':frozen,
            'validated_veto_count':sum(v['validated'] for v in frozen),
            'team_diagnostics_worst_first':team_diag[:15],
            'yearly':yearly,
        })

    payload={
        'built_at':pd.Timestamp.utcnow().isoformat(),
        'priority_count':len(priorities),
        'status':'SHADOW_RESEARCH_ONLY',
        'design':'Loss fingerprints and veto thresholds are discovered only on 2013-22. Frozen vetoes are then checked on untouched 2023-25. Team/season diagnostics are descriptive and never automatic vetoes.',
        'methods':results,
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({
        'priority_count':len(priorities),
        'validated_vetoes_total':sum(m['validated_veto_count'] for m in results)
    },indent=2))
    for m in results:
        for v in m['frozen_vetoes']:
            if v['validated']:
                print('VALIDATED_VETO',m['method_index'],v['feature'],v['op'],v['threshold'],
                      'PRE_SAFE',v['pre_safe'],'HOLD_SAFE',v['hold_safe'],'HOLD_RISK',v['hold_risk'])

if __name__=='__main__':
    main()
