#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/method_expansion';OUT.mkdir(parents=True,exist_ok=True)

METHODS=[
 {'id':'MLS-R01','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__elo_edge','<=',.91)]},
 {'id':'MLS-R02','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__edge_last3_xgapg','>=',.0536)]},
 {'id':'MLS-R03','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113)]},
 {'id':'MLS-A02','outcome':'HOME','lo':.50,'hi':.60,'rules':[('ctx__edge_gk_save_pct5','<=',-.10148378191856453),('base__sel_last10_ppg','>',1.1)]},
 {'id':'MLS-A03','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('base__edge_last10_ppg','<=',-.4),('base__edge_last10_xgdpg','<',-.13458100000000023)]},
 {'id':'MLS-A04','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last5_xgdpg','<=',-.04208999999999996),('ctx__sel_roster_new_players3','<=',0),('pm__opp_player_weighted_age5','>',28.068592458747545)]},
 {'id':'MLS-A05','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('ctx__referee_prior_over25_rate','<=',.5348837209302325)]},
 {'id':'MLS-A05R','outcome':'AWAY','lo':.25,'hi':.35,'rules':[('ctx__referee_prior_over25_rate','<=',.5348837209302325),('ctx__opp_roster_new_players3','>',2)]},
 {'id':'MLS-A06','outcome':'AWAY','lo':.20,'hi':.30,'rules':[('ctx__opp_roster_new_players3','<=',0)]},
 {'id':'MLS-A06R','outcome':'AWAY','lo':.20,'hi':.30,'rules':[('ctx__opp_roster_new_players3','<=',0),('base__edge_last10_xgdpg','<',-.29336)]},
 {'id':'MLS-A07','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285)]},
 {'id':'MLS-A07R','outcome':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__opp_last10_gdpg','<',.6)]},
]

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return {'>=':v.ge(t),'<=':v.le(t),'>':v.gt(t),'<':v.lt(t)}[op]
def msk(df,sp,rules=None,lo=None,hi=None):
    m=df.market_prob.between(sp['lo'] if lo is None else lo,sp['hi'] if hi is None else hi,inclusive='both')
    for f,op,t in (rules or sp['rules']):m &= cond(df,f,op,t)
    return m
def met(x):return arx.metrics(x)
def boot(v,n=5000,seed=619):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a));z=np.empty(n)
    for i in range(n):z[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(z,[.025,.975])]
def ev(s,sp,rules=None,lo=None,hi=None):
    so=s[s.outcome.eq(sp['outcome'])].copy();x=so[msk(so,sp,rules,lo,hi)].copy()
    return {'rows':x,'train':met(x[arx.period(x,'train')]),'validation':met(x[arx.period(x,'validation')]),
            'holdout':met(x[arx.period(x,'holdout')]),'full':met(x),'ci':boot(x.profit) if len(x) else [None,None],
            'yearly':[{'season':int(y),**met(z)} for y,z in x.groupby('season')]}
def sensitivity(s,mid):
    sp=next(x for x in METHODS if x['id']==mid);tests=[]
    if mid=='MLS-A05R':
        for t in [1,2,3,4]:
            rr=[sp['rules'][0],('ctx__opp_roster_new_players3','>',t)]
            e=ev(s,sp,rr);tests.append({'axis':'opp_new_players_gt','threshold':t,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
        for t in [.50,.52,.5348837209302325,.55]:
            rr=[('ctx__referee_prior_over25_rate','<=',t),sp['rules'][1]]
            e=ev(s,sp,rr);tests.append({'axis':'ref_over25_le','threshold':t,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
        for lo,hi in [(.23,.37),(.25,.35),(.27,.33)]:
            e=ev(s,sp,lo=lo,hi=hi);tests.append({'axis':'price','lo':lo,'hi':hi,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
    elif mid=='MLS-A06R':
        for t in [-.45,-.35,-.29336,-.25,-.20]:
            rr=[sp['rules'][0],('base__edge_last10_xgdpg','<',t)]
            e=ev(s,sp,rr);tests.append({'axis':'xgd_lt','threshold':t,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
        for t in [0,1]:
            rr=[('ctx__opp_roster_new_players3','<=',t),sp['rules'][1]]
            e=ev(s,sp,rr);tests.append({'axis':'opp_new_players_le','threshold':t,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
        for lo,hi in [(.18,.32),(.20,.30),(.22,.28)]:
            e=ev(s,sp,lo=lo,hi=hi);tests.append({'axis':'price','lo':lo,'hi':hi,'train':e['train'],'validation':e['validation'],'holdout':e['holdout'],'full':e['full']})
    good=[x for x in tests if all(x.get(k) and x[k]['roi']>0 for k in ['train','validation','holdout'])]
    return {'id':mid,'positive_all_eras':len(good),'test_count':len(tests),'robust_ratio':len(good)/max(1,len(tests)),'tests':tests}

def main():
    src=json.loads(SRC.read_text());d=pd.read_parquet(Path(src['dataset']));s=arx.merged_selection_rows(d)
    results={}
    sets={}
    for sp in METHODS:
        e=ev(s,sp);results[sp['id']]={k:v for k,v in e.items() if k!='rows'}
        z=e['rows'];sets[sp['id']]={'match':set(z.match_id.astype(str)),'exact':set(zip(z.match_id.astype(str),z.outcome))}
    overlaps=[]
    ids=list(sets)
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            ma,mb=sets[a]['match'],sets[b]['match'];ea,eb=sets[a]['exact'],sets[b]['exact']
            overlaps.append({'a':a,'b':b,'a_n':len(ea),'b_n':len(eb),'shared_matches':len(ma&mb),
                             'shared_pct_smaller':len(ma&mb)/max(1,min(len(ma),len(mb))),
                             'exact_overlap':len(ea&eb),'exact_pct_smaller':len(ea&eb)/max(1,min(len(ea),len(eb)))})
    sens=[sensitivity(s,'MLS-A05R'),sensitivity(s,'MLS-A06R')]
    payload={'built_at':pd.Timestamp.now('UTC').isoformat(),'status':'SHADOW_RESEARCH_ONLY',
             'results':results,'sensitivity':sens,'overlaps':overlaps}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    keys=['MLS-A05','MLS-A05R','MLS-A06','MLS-A06R','MLS-A07','MLS-A07R']
    print(json.dumps({
      'methods':{k:results[k] for k in keys},
      'sensitivity':sens,
      'key_overlaps':[x for x in overlaps if (x['a'] in keys or x['b'] in keys) and
                      ({x['a'],x['b']}&{'MLS-R01','MLS-R02','MLS-R03','MLS-A02','MLS-A03','MLS-A04'})]
    },indent=2,default=str))

if __name__=='__main__':main()
