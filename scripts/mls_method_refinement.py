#!/usr/bin/env python3
from __future__ import annotations

import json, math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/method_refinement';OUT.mkdir(parents=True,exist_ok=True)

CURRENT=[
 {'id':'MLS-A02','name':'GK rebound home favorite','outcome':'HOME','lo':.50,'hi':.60,
  'rules':[('ctx__edge_gk_save_pct5','<=',-0.10148378191856453),('base__sel_last10_ppg','>',1.1)],
  'grids':{
    'ctx__edge_gk_save_pct5':[-.14,-.12,-.10148378191856453,-.08,-.06],
    'base__sel_last10_ppg':[.9,1.0,1.1,1.2,1.3],
  },'prices':[(.48,.62),(.50,.60),(.52,.58)]},
 {'id':'MLS-A03','name':'Contrarian away xG underdog','outcome':'AWAY','lo':.25,'hi':.35,
  'rules':[('base__edge_last10_ppg','<=',-.4),('base__edge_last10_xgdpg','<',-.13458100000000023)],
  'grids':{
    'base__edge_last10_ppg':[-.60,-.50,-.40,-.30,-.20],
    'base__edge_last10_xgdpg':[-.25,-.20,-.13458100000000023,-.10,-.05],
  },'prices':[(.23,.37),(.25,.35),(.27,.33)]},
 {'id':'MLS-A04','name':'Stable-roster home xGD','outcome':'HOME','lo':.40,'hi':.50,
  'rules':[('base__edge_last5_xgdpg','<=',-.04208999999999996),('ctx__sel_roster_new_players3','<=',0.0),
           ('pm__opp_player_weighted_age5','>',28.068592458747545)],
  'grids':{
    'base__edge_last5_xgdpg':[-.15,-.10,-.04208999999999996,0,.05],
    'ctx__sel_roster_new_players3':[0.0,1.0],
    'pm__opp_player_weighted_age5':[27.0,27.5,28.068592458747545,28.5,29.0],
  },'prices':[(.38,.52),(.40,.50),(.42,.48)]},
]

NEW=[
 {'id':'MLS-A05','name':'Referee low-scoring away underdog','outcome':'AWAY','lo':.25,'hi':.35,
  'rules':[('ctx__referee_prior_over25_rate','<=',0.5348837209302325)]},
 {'id':'MLS-A06','name':'Away longshot vs stable opponent roster','outcome':'AWAY','lo':.20,'hi':.30,
  'rules':[('ctx__opp_roster_new_players3','<=',0.0)]},
 {'id':'MLS-A07','name':'Home xG surge','outcome':'HOME','lo':.40,'hi':.50,
  'rules':[('base__edge_last3_xgfpg','>=',0.5782850000000002)]},
]

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return {'>=':v.ge(t),'<=':v.le(t),'>':v.gt(t),'<':v.lt(t)}[op]

def mask(df,spec,rules=None,price=None):
    lo,hi=price or (spec['lo'],spec['hi'])
    m=df.market_prob.between(lo,hi,inclusive='both')
    for f,op,t in (rules or spec['rules']):m &= cond(df,f,op,t)
    return m

def met(x):return arx.metrics(x)
def period(x,k):return arx.period(x,k)

def boot(v,n=5000,seed=3407):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a))
    means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]

def yearly(x):
    return [{'season':int(y),**met(z)} for y,z in x.groupby('season')]

def eval_spec(s,spec,rules=None,price=None):
    so=s[s.outcome.eq(spec['outcome'])].copy()
    x=so[mask(so,spec,rules,price)].copy()
    return {
      'n':len(x),'train':met(x[period(x,'train')]),'validation':met(x[period(x,'validation')]),
      'holdout':met(x[period(x,'holdout')]),'full':met(x),'bootstrap95_roi':boot(x.profit) if len(x) else [None,None],
      'yearly':yearly(x),'rows':x
    }

def sensitivity(s,spec):
    base=eval_spec(s,spec)
    tests=[]
    # One-axis threshold perturbations: all other rules frozen.
    for fi,(f,op,t0) in enumerate(spec['rules']):
        for t in spec['grids'].get(f,[t0]):
            rr=list(spec['rules']);rr[fi]=(f,op,t)
            e=eval_spec(s,spec,rr)
            tests.append({'axis':f,'threshold':t,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
    for lo,hi in spec['prices']:
        e=eval_spec(s,spec,price=(lo,hi))
        tests.append({'axis':'price_band','lo':lo,'hi':hi,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
    good=[q for q in tests if all(q.get(k) and q[k]['roi']>0 for k in ['train','validation','holdout'])]
    return {
      'id':spec['id'],'name':spec['name'],'base':{k:v for k,v in base.items() if k!='rows'},
      'tests':tests,'positive_all_eras':len(good),'test_count':len(tests),
      'robust_ratio':len(good)/max(1,len(tests))
    }

def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=7:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))

def refine_new(s,spec):
    so=s[s.outcome.eq(spec['outcome'])].copy()
    base=eval_spec(s,spec)
    x=base['rows'];pre=x[period(x,'train')|period(x,'validation')].copy();hold=x[period(x,'holdout')].copy()
    bpre=met(pre);bhold=met(hold)
    used={r[0] for r in spec['rules']}
    candidates=[]
    for f in so.columns:
        if f in arx.KEYS or f in {'odds','market_prob'} or f in used or arx.family(f)=='other':continue
        v=pd.to_numeric(pre[f],errors='coerce')
        if v.notna().sum()<max(40,int(.7*len(pre))) or v.nunique(dropna=True)<2:continue
        for t in thresholds(v):
            for op in ['>=','<=']:
                risk=cond(pre,f,op,t)
                rm=met(pre[risk]);sm=met(pre[~risk])
                if not rm or not sm or rm['n']<15 or sm['n']<50:continue
                improve=sm['roi']-bpre['roi'];riskdrop=bpre['roi']-rm['roi']
                if improve<.025 or riskdrop<.06:continue
                score=improve+.25*riskdrop+.004*math.sqrt(sm['n'])
                candidates.append({'feature':f,'family':arx.family(f),'op':op,'threshold':t,'score':score,
                                   'pre_risk':rm,'pre_safe':sm})
    candidates.sort(key=lambda x:x['score'],reverse=True)
    frozen=[];seen=set()
    for v in candidates:
        if v['feature'] in seen:continue
        seen.add(v['feature']);frozen.append(v)
        if len(frozen)>=15:break
    for v in frozen:
        risk=cond(hold,v['feature'],v['op'],v['threshold'])
        v['hold_risk']=met(hold[risk]);v['hold_safe']=met(hold[~risk])
        v['validated']=bool(bhold and v['hold_risk'] and v['hold_safe'] and v['hold_safe']['n']>=25
                            and v['hold_safe']['roi']>0 and v['hold_safe']['roi']>=bhold['roi']+.015
                            and v['hold_risk']['roi']<bhold['roi'])
    valid=[v for v in frozen if v['validated']]
    # Characterize base signal; do not auto-add a veto.
    status='RESEARCH_ONLY'
    ci=base['bootstrap95_roi']
    if bhold and bhold['n']>=50 and bhold['roi']>=.08 and base['full'] and base['full']['positive_season_ratio']>=.75 and ci[0] is not None and ci[0]>0:
        status='PROSPECTIVE_PRIORITY_SHADOW'
    elif bhold and bhold['n']>=50 and bhold['roi']>0 and base['full'] and base['full']['positive_season_ratio']>=.65:
        status='SHADOW_WATCH'
    # A strong validated simple veto can upgrade a watch, but report only; no auto-promotion in this script.
    return {
      'id':spec['id'],'name':spec['name'],'rules':spec['rules'],'base':{k:v for k,v in base.items() if k!='rows'},
      'status':status,'frozen_vetoes':frozen,'validated_veto_count':len(valid)
    }

def overlap(s,specs):
    sets={}
    for sp in specs:
        e=eval_spec(s,sp);z=e['rows']
        sets[sp['id']]={'matches':set(z.match_id.astype(str)),'exact':set(zip(z.match_id.astype(str),z.outcome))}
    out=[]
    ids=list(sets)
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            ma,mb=sets[a]['matches'],sets[b]['matches'];ea,eb=sets[a]['exact'],sets[b]['exact']
            out.append({'a':a,'b':b,'shared_matches':len(ma&mb),'shared_pct_smaller':len(ma&mb)/max(1,min(len(ma),len(mb))),
                        'exact_selection_overlap':len(ea&eb)})
    return out

def main():
    src=json.loads(SRC.read_text());d=pd.read_parquet(Path(src['dataset']));s=arx.merged_selection_rows(d)
    sens=[sensitivity(s,x) for x in CURRENT]
    new=[refine_new(s,x) for x in NEW]
    payload={
      'built_at':pd.Timestamp.now('UTC').isoformat(),'status':'SHADOW_RESEARCH_ONLY',
      'current_sensitivity':sens,'new_candidate_refinement':new,
      'overlap':overlap(s,CURRENT+NEW),
      'rules':'Sensitivity is characterization only; no threshold is retuned from holdout. New-candidate vetoes are discovered on 2013-22 and validated on untouched 2023-25.'
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({
      'current':[{'id':x['id'],'robust_ratio':x['robust_ratio'],'base_holdout':x['base']['holdout'],'base_full':x['base']['full']} for x in sens],
      'new':[{'id':x['id'],'status':x['status'],'base_holdout':x['base']['holdout'],'base_full':x['base']['full'],
              'ci':x['base']['bootstrap95_roi'],'validated_veto_count':x['validated_veto_count']} for x in new],
      'validated_vetoes':{x['id']:[{k:v[k] for k in ['feature','family','op','threshold','pre_safe','pre_risk','hold_safe','hold_risk']}
                                   for v in x['frozen_vetoes'] if v['validated']] for x in new}
    },indent=2,default=str))

if __name__=='__main__':main()
