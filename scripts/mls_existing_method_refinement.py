#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/existing_method_refinement';OUT.mkdir(parents=True,exist_ok=True)

SPECS=[
 {'id':'R01','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__elo_edge','<=',.91)]},
 {'id':'R01_GD','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__elo_edge','<=',.91),('base__opp_last10_gdpg','<',.6)]},
 {'id':'R01_XG','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__elo_edge','<=',.91)]},
 {'id':'R01_XG_GD','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__elo_edge','<=',.91),('base__opp_last10_gdpg','<',.6)]},

 {'id':'R02','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__edge_last3_xgapg','>=',.0536)]},
 {'id':'R02_GD','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__edge_last3_xgapg','>=',.0536),('base__opp_last10_gdpg','<',.6)]},
 {'id':'R02_XG','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__edge_last3_xgapg','>=',.0536)]},
 {'id':'R02_XG_GD','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__edge_last3_xgapg','>=',.0536),('base__opp_last10_gdpg','<',.6)]},

 {'id':'R03','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113)]},
 {'id':'R03_REF','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113),('ctx__referee_prior_over25_rate','<=',.5348837209302325)]},
 {'id':'R03_XGD','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113),('base__edge_last10_xgdpg','<',-.134581)]},
 {'id':'R03_REF_XGD','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113),('ctx__referee_prior_over25_rate','<=',.5348837209302325),('base__edge_last10_xgdpg','<',-.134581)]},
]

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return {'>=':v.ge(t),'<=':v.le(t),'>':v.gt(t),'<':v.lt(t)}[op]
def met(x):return arx.metrics(x)
def boot(v,n=5000,seed=922):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a));z=np.empty(n)
    for i in range(n):z[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(z,[.025,.975])]
def eval_one(s,sp):
    so=s[s.outcome.eq(sp['outcome'])].copy()
    m=so.market_prob.between(sp['lo'],sp['hi'],inclusive='both')
    for f,op,t in sp['rules']:m &= cond(so,f,op,t)
    x=so[m].copy()
    return {'n':len(x),'train':met(x[arx.period(x,'train')]),'validation':met(x[arx.period(x,'validation')]),
            'holdout':met(x[arx.period(x,'holdout')]),'full':met(x),'ci':boot(x.profit) if len(x) else [None,None],
            'yearly':[{'season':int(y),**met(z)} for y,z in x.groupby('season')]}

def choose(base,variants):
    bpre=(base['train']['units']+base['validation']['units'])/(base['train']['n']+base['validation']['n'])
    out=[]
    for v in variants:
        if not all(v.get(k) for k in ['train','validation','holdout','full']):continue
        pre=(v['train']['units']+v['validation']['units'])/(v['train']['n']+v['validation']['n'])
        pre_lift=pre-bpre
        qualifies=(v['train']['n']>=25 and v['validation']['n']>=20 and v['holdout']['n']>=25 and
                   v['train']['roi']>0 and v['validation']['roi']>0 and v['holdout']['roi']>0 and
                   pre_lift>=.04 and v['full']['positive_season_ratio']>=.70)
        out.append({'id':v['_id'],'preholdout_roi':pre,'preholdout_lift':pre_lift,'qualifies':qualifies,
                    'holdout':v['holdout'],'full':v['full'],'ci':v['ci']})
    return out

def main():
    src=json.loads(SRC.read_text());d=pd.read_parquet(Path(src['dataset']));s=arx.merged_selection_rows(d)
    res={}
    for sp in SPECS:
        e=eval_one(s,sp);e['_id']=sp['id'];e['rules']=sp['rules'];res[sp['id']]=e
    decisions={
      'R01':choose(res['R01'],[res['R01_GD'],res['R01_XG'],res['R01_XG_GD']]),
      'R02':choose(res['R02'],[res['R02_GD'],res['R02_XG'],res['R02_XG_GD']]),
      'R03':choose(res['R03'],[res['R03_REF'],res['R03_XGD'],res['R03_REF_XGD']]),
    }
    payload={'built_at':pd.Timestamp.now('UTC').isoformat(),'status':'SHADOW_RESEARCH_ONLY',
             'results':res,'decisions':decisions,
             'note':'Candidate refinements use independently discovered pre-holdout features. Selection requires positive train/validation/holdout and >=4pp pre-holdout ROI lift; existing method IDs are never silently rewritten.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({
      'summary':{k:{x:{m:res[x][m] for m in ['train','validation','holdout','full','ci']} for x in [k]+[z['id'] for z in decisions[k]]} for k in ['R01','R02','R03']},
      'decisions':decisions
    },indent=2,default=str))

if __name__=='__main__':main()
