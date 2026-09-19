#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/arsenal_portfolio';OUT.mkdir(parents=True,exist_ok=True)

def metric(x):return arx.metrics(x)
def c(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    if op=='>=':return v.ge(t)
    if op=='<=':return v.le(t)
    if op=='>':return v.gt(t)
    if op=='<':return v.lt(t)
    raise ValueError(op)
def pm(df,p):return arx.pmask(df,p)

def main():
    src=json.loads(SRC.read_text())
    d=pd.read_parquet(Path(src['dataset']))
    s=arx.merged_selection_rows(d)
    # Four distinct methods; #4/#5 from arsenal are equivalent, keep one.
    specs=[
      {'id':'MLS-A01','name':'Balanced continuity draw','outcome':'DRAW','pb':'ALL',
       'rules':[('pm__balance_player_starter_proxy_continuity','<=',0.049450549450549386),
                ('base__combined_season_gdpg','>',-0.5361654135338345)]},
      {'id':'MLS-A02','name':'GK rebound home favorite','outcome':'HOME','pb':'P50_60',
       'rules':[('ctx__edge_gk_save_pct5','<=',-0.10148378191856453),
                ('base__sel_last10_ppg','>',1.1)]},
      {'id':'MLS-A03','name':'Contrarian away xG underdog','outcome':'AWAY','pb':'P25_35',
       'rules':[('base__edge_last10_ppg','<=',-0.3999999999999999),
                ('base__edge_last10_xgdpg','<',-0.13458100000000023)]},
      {'id':'MLS-A04','name':'Stable-roster home xGD','outcome':'HOME','pb':'P40_50',
       'rules':[('base__edge_last5_xgdpg','<=',-0.04208999999999996),
                ('ctx__sel_roster_new_players3','<=',0.0),
                ('pm__opp_player_weighted_age5','>',28.068592458747545)]},
    ]
    picks={}
    results=[]
    for sp in specs:
        so=s[s.outcome.eq(sp['outcome'])].copy()
        m=pm(so,sp['pb'])
        for f,op,t in sp['rules']:m &= c(so,f,op,t)
        x=so[m].copy()
        picks[sp['id']]=x
        res={**sp,'full':metric(x),'train':metric(x[arx.period(x,'train')]),
             'validation':metric(x[arx.period(x,'validation')]),
             'holdout':metric(x[arx.period(x,'holdout')]),
             'yearly':[{'season':int(y),**metric(z)} for y,z in x.groupby('season')]}
        results.append(res)

    overlaps=[]
    ids=[x['id'] for x in specs]
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            xa=picks[a];xb=picks[b]
            ma=set(xa.match_id.astype(str));mb=set(xb.match_id.astype(str))
            # Same match may be opposite/outcome selections, so report match overlap and exact selection overlap.
            exact_a=set(zip(xa.match_id.astype(str),xa.outcome))
            exact_b=set(zip(xb.match_id.astype(str),xb.outcome))
            inter=ma&mb
            overlaps.append({
              'a':a,'b':b,'a_n':len(xa),'b_n':len(xb),'shared_matches':len(inter),
              'shared_pct_smaller':len(inter)/max(1,min(len(ma),len(mb))),
              'exact_selection_overlap':len(exact_a&exact_b)
            })

    # Portfolio: 1 unit per method qualification. If multiple methods qualify the
    # same match, each remains an independent selection; report duplication.
    allx=[]
    for sp in specs:
        z=picks[sp['id']][['match_id','date','season','outcome','profit']].copy()
        z['method_id']=sp['id'];allx.append(z)
    port=pd.concat(allx,ignore_index=True)
    by_year=port.groupby('season').profit.agg(['count','sum']).reset_index()
    portfolio={
      'selections':len(port),'unique_matches':int(port.match_id.nunique()),
      'duplicate_method_selections':int(len(port)-port.match_id.nunique()),
      'units':float(port.profit.sum()),'roi':float(port.profit.mean()),
      'positive_seasons':int((by_year['sum']>0).sum()),'seasons':int(len(by_year)),
      'yearly':[{'season':int(r.season),'n':int(r['count']),'units':float(r['sum']),
                 'roi':float(r['sum']/r['count'])} for _,r in by_year.iterrows()]
    }
    hold=port[port.season.between(2023,2025)]
    hy=hold.groupby('season').profit.agg(['count','sum']).reset_index()
    portfolio['holdout']={
      'selections':len(hold),'unique_matches':int(hold.match_id.nunique()),
      'units':float(hold.profit.sum()),'roi':float(hold.profit.mean()),
      'yearly':[{'season':int(r.season),'n':int(r['count']),'units':float(r['sum']),
                 'roi':float(r['sum']/r['count'])} for _,r in hy.iterrows()]
    }
    payload={'built_at':pd.Timestamp.now('UTC').isoformat(),'status':'SHADOW_ONLY',
             'methods':results,'overlaps':overlaps,'portfolio':portfolio,
             'note':'Each refinement is exactly one simple preholdout-discovered loss filter except A04, where opponent age is the single added filter on the original two-rule pair. Weather vetoes remain diagnostic-only.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps(payload,indent=2,default=str))

if __name__=='__main__':main()
