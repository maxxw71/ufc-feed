#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/active_portfolio';OUT.mkdir(parents=True,exist_ok=True)

METHODS=[
 {'id':'MLS-R01V2','side':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__elo_edge','<=',.91),('base__opp_last10_gdpg','<',.6)]},
 {'id':'MLS-R02V2','side':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__edge_last3_xgapg','>=',.0536)]},
 {'id':'MLS-R03','side':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113)]},
 {'id':'MLS-A02','side':'HOME','lo':.50,'hi':.60,'rules':[('ctx__edge_gk_save_pct5','<=',-.10148378191856453),('base__sel_last10_ppg','>',1.1)]},
 {'id':'MLS-A03','side':'AWAY','lo':.25,'hi':.35,'rules':[('base__edge_last10_ppg','<=',-.3999999999999999),('base__edge_last10_xgdpg','<',-.13458100000000023)]},
 {'id':'MLS-A05','side':'AWAY','lo':.25,'hi':.35,'rules':[('ctx__referee_prior_over25_rate','<=',.5348837209302325)]},
 {'id':'MLS-A06','side':'AWAY','lo':.20,'hi':.30,'rules':[('ctx__opp_roster_new_players3','<=',0),('base__edge_last10_xgdpg','<',-.2933600000000003)]},
]

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return {'>=':v.ge(t),'<=':v.le(t),'>':v.gt(t),'<':v.lt(t)}[op]

def method_rows(s,m):
    so=s[s.outcome.eq(m['side'])].copy()
    mask=so.market_prob.between(m['lo'],m['hi'],inclusive='both')
    for f,op,t in m['rules']: mask &= cond(so,f,op,t)
    z=so[mask][['match_id','date','season','outcome','odds','market_prob','profit']].copy()
    z['method_id']=m['id']
    return z

def metric(x):
    if x.empty:return None
    return {'n':int(len(x)),'wins':int((x.profit>0).sum()),'losses':int((x.profit<0).sum()),
            'roi':float(x.profit.mean()),'units':float(x.profit.sum())}

def main():
    src=json.loads(SRC.read_text());d=pd.read_parquet(Path(src['dataset']));s=arx.merged_selection_rows(d)
    allrows=pd.concat([method_rows(s,m) for m in METHODS],ignore_index=True)
    groups=[]
    for mid,g in allrows.groupby('match_id'):
        outs=sorted(set(g.outcome))
        methods=sorted(g.method_id)
        conflict=len(outs)>1
        groups.append({'match_id':str(mid),'season':int(g.season.iloc[0]),'signals':len(g),
                       'methods':methods,'outcomes':outs,'conflict':conflict,
                       'same_side_consensus':len(methods)>1 and not conflict})
    gdf=pd.DataFrame(groups)
    conflict_ids=set(gdf.loc[gdf.conflict,'match_id'])
    consensus_ids=set(gdf.loc[gdf.same_side_consensus,'match_id'])

    allrows['match_id']=allrows.match_id.astype(str)
    conflict_rows=allrows[allrows.match_id.isin(conflict_ids)].copy()
    clean=allrows[~allrows.match_id.isin(conflict_ids)].copy()
    consensus=allrows[allrows.match_id.isin(consensus_ids)].copy()

    # One unit per distinct method signal (normal portfolio accounting).
    def eras(x):
        out={'full':metric(x)}
        for label,lo,hi in [('preholdout',2013,2022),('holdout',2023,2025)]:
            out[label]=metric(x[x.season.between(lo,hi)])
        out['yearly']=[{'season':int(y),**metric(z)} for y,z in x.groupby('season')]
        return out

    # Per-method performance and conflict exposure.
    per={}
    for mid,z in allrows.groupby('method_id'):
        per[mid]={**eras(z),
                  'conflict_signals':int(z.match_id.isin(conflict_ids).sum()),
                  'conflict_share':float(z.match_id.isin(conflict_ids).mean())}

    # Exact same-side overlaps (method duplication).
    overlap=[]
    ids=[m['id'] for m in METHODS]
    for i,a in enumerate(ids):
        za=allrows[allrows.method_id.eq(a)]
        sa=set(zip(za.match_id,za.outcome))
        for b in ids[i+1:]:
            zb=allrows[allrows.method_id.eq(b)]
            sb=set(zip(zb.match_id,zb.outcome))
            exact=sa&sb
            overlap.append({'a':a,'b':b,'exact_overlap':len(exact),
                            'pct_smaller':len(exact)/max(1,min(len(sa),len(sb)))})

    payload={
      'built_at':pd.Timestamp.now('UTC').isoformat(),'status':'SHADOW_RESEARCH_ONLY',
      'methods':[m['id'] for m in METHODS],'watch_only_excluded':['MLS-A04'],
      'signal_count':len(allrows),'unique_matches':int(allrows.match_id.nunique()),
      'conflict_match_count':len(conflict_ids),'same_side_consensus_match_count':len(consensus_ids),
      'all_signals':eras(allrows),'exclude_opposite_side_conflicts':eras(clean),
      'conflict_only':eras(conflict_rows),'same_side_consensus_signals':eras(consensus),
      'per_method':per,'overlap':overlap,
      'conflicts':gdf[gdf.conflict].to_dict('records'),
      'note':'Conflict exclusion is a portfolio-policy stress test, not a retuned method. Methods remain independently tracked in the prospective ledger.'
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({
      'signal_count':payload['signal_count'],'unique_matches':payload['unique_matches'],
      'conflict_match_count':payload['conflict_match_count'],
      'all':payload['all_signals'],'exclude_conflicts':payload['exclude_opposite_side_conflicts'],
      'conflict_only':payload['conflict_only'],
      'top_overlaps':sorted(overlap,key=lambda x:x['pct_smaller'],reverse=True)[:12]
    },indent=2,default=str))

if __name__=='__main__':main()
