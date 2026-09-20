#!/usr/bin/env python3
from __future__ import annotations

import itertools,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_context_research as cr
import mls_arsenal_research as arx
import mls_active_portfolio as active

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_master.parquet'
OUT=ROOT/'research/new_families';OUT.mkdir(parents=True,exist_ok=True)

LAYER_FILES={
 'salary':ROOT/'data/processed/mls_salary_features.parquet',
 'cross_comp':ROOT/'data/processed/mls_cross_comp_features.parquet',
 'transactions':ROOT/'data/processed/mls_transaction_features.parquet',
 'style':ROOT/'data/processed/mls_team_style_features.parquet',
 'referee_discipline':ROOT/'data/processed/mls_referee_discipline_features.parquet',
 'venue':ROOT/'data/processed/mls_venue_features.parquet',
 'availability':ROOT/'data/processed/mls_availability_pregame_features.parquet',
 'confirmed_lineup':ROOT/'data/processed/mls_confirmed_lineup_card_pregame_features.parquet',
}
LONG_FAMILIES={'salary','cross_comp','style','venue'}
SPARSE_FAMILIES={'transactions','referee_discipline','availability','confirmed_lineup'}
PRICE_BANDS=[
 ('ALL',0,1),('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),
 ('P35_45',.35,.45),('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70)
]
KEYS={'match_id','date','season','outcome','win','odds','market_prob','profit'}

def now():return datetime.now(timezone.utc).isoformat()
def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum'])
    active_seasons=by[by['count']>=5]
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
            'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
            'active_seasons':int(len(active_seasons)),'positive_seasons':int((active_seasons['sum']>0).sum()),
            'positive_season_ratio':float((active_seasons['sum']>0).mean()) if len(active_seasons) else 0.0}
def pmask(df,pb):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==pb)
    return df.market_prob.between(lo,hi,inclusive='both')
def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)
def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=7:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))
def bootstrap(v,n=4000,seed=20260920):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a))
    means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]
def yearly(x):
    return [{'season':int(y),**metrics(z)} for y,z in x.groupby('season')]
def team_concentration(x,meta):
    z=x[['match_id','outcome']].merge(meta,on='match_id',how='left')
    if z.empty:return {'unique_teams':0,'top3_share':0.0}
    if str(z.outcome.iloc[0])=='DRAW':
        counts=pd.concat([z.home_team,z.away_team]).value_counts()
    else:
        selected=np.where(z.outcome.eq('HOME'),z.home_team,z.away_team)
        counts=pd.Series(selected).value_counts()
    total=max(1,int(counts.sum()))
    return {'unique_teams':int(len(counts)),'top1_share':float(counts.iloc[0]/total) if len(counts) else 0,
            'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0,
            'top5':{str(k):int(v) for k,v in counts.head(5).items()}}

def layer_columns():
    out={}
    for fam,p in LAYER_FILES.items():
        if not p.exists():continue
        z=pd.read_parquet(p)
        out[fam]=[c for c in z.columns if c not in {'match_id','asa_game_id','season','asa_matchday'}]
    return out

def numeric_series(d,c):
    return pd.to_numeric(d[c],errors='coerce') if c in d else pd.Series(np.nan,index=d.index)

def transform_family(d,fam,cols):
    cols=[c for c in cols if c in d.columns]
    # Only numeric data is researchable here. IDs, dates, labels, competition strings are excluded.
    numeric=[]
    for c in cols:
        v=pd.to_numeric(d[c],errors='coerce')
        if v.notna().sum()>=20 and v.nunique(dropna=True)>=2:numeric.append(c)
    pairs={}
    for c in numeric:
        if c.startswith('home_'):
            suffix=c[5:];a='away_'+suffix
            if a in numeric:pairs[suffix]=(c,a)
    used={x for p in pairs.values() for x in p}
    out=pd.DataFrame({'match_id':d.match_id.astype(str)})
    # Paired side fields become selection-relative edges for HOME/AWAY and balance/combined for DRAW.
    for suffix,(hc,ac) in pairs.items():
        h=numeric_series(d,hc);a=numeric_series(d,ac)
        out[f'{fam}__home__{suffix}']=h
        out[f'{fam}__away__{suffix}']=a
        out[f'{fam}__edgehome__{suffix}']=h-a
        out[f'{fam}__balance__{suffix}']=(h-a).abs()
        out[f'{fam}__combined__{suffix}']=(h+a)/2.0
    # Existing edge fields are assumed home-oriented by construction in our feature scripts.
    for c in numeric:
        if c in used:continue
        v=numeric_series(d,c)
        if c.startswith('edge_'):
            out[f'{fam}__rawedge__{c[5:]}']=v
        elif not c.startswith(('home_','away_')):
            out[f'{fam}__raw__{c}']=v
    return out

def make_selection_rows(d):
    base=cr.selection_rows(d).copy()
    keep=[c for c in ['match_id','date','season','outcome','win','odds','market_prob','profit'] if c in base]
    s=base[keep].copy();s['match_id']=s.match_id.astype(str)
    layers=layer_columns()
    for fam,cols in layers.items():
        mf=transform_family(d,fam,cols)
        s=s.merge(mf,on='match_id',how='left',validate='m:1')

    # Re-orient paired/edge features by selected side. Draw uses only balance/combined/raw.
    for fam in layers:
        hcols=[c for c in s.columns if c.startswith(f'{fam}__home__')]
        for hc in hcols:
            suffix=hc.split('__home__',1)[1]
            ac=f'{fam}__away__{suffix}'
            if ac not in s:continue
            h=pd.to_numeric(s[hc],errors='coerce');a=pd.to_numeric(s[ac],errors='coerce')
            sel=np.where(s.outcome.eq('HOME'),h,np.where(s.outcome.eq('AWAY'),a,np.nan))
            opp=np.where(s.outcome.eq('HOME'),a,np.where(s.outcome.eq('AWAY'),h,np.nan))
            s[f'{fam}__sel__{suffix}']=sel
            s[f'{fam}__opp__{suffix}']=opp
            s[f'{fam}__seledge__{suffix}']=np.where(s.outcome.eq('HOME'),h-a,np.where(s.outcome.eq('AWAY'),a-h,np.nan))
        for c in [x for x in s.columns if x.startswith(f'{fam}__rawedge__')]:
            v=pd.to_numeric(s[c],errors='coerce')
            suffix=c.split('__rawedge__',1)[1]
            s[f'{fam}__seledge_raw__{suffix}']=np.where(s.outcome.eq('HOME'),v,np.where(s.outcome.eq('AWAY'),-v,v.abs()))
    # Drop raw home/away orientation to avoid duplicate mining.
    drop=[c for c in s.columns if '__home__' in c or '__away__' in c or '__edgehome__' in c or '__rawedge__' in c]
    return s.drop(columns=drop,errors='ignore'),layers

def family_of(f):
    return f.split('__',1)[0] if '__' in f else 'other'

def features_for(so,families,train_years,val_years,min_train,min_val):
    feats=[]
    tr=so[so.season.between(*train_years)];va=so[so.season.between(*val_years)]
    for c in so.columns:
        fam=family_of(c)
        if fam not in families:continue
        vtr=pd.to_numeric(tr[c],errors='coerce');vva=pd.to_numeric(va[c],errors='coerce')
        if vtr.notna().sum()<min_train or vva.notna().sum()<min_val:continue
        if vtr.nunique(dropna=True)<2:continue
        feats.append(c)
    return feats

def period_metrics(so,m,years):
    return metrics(so[m&so.season.between(*years)])

def baseline(so,pb,years):
    return metrics(so[pmask(so,pb)&so.season.between(*years)])

def scan_horizon(s,meta,name,families,train_years,val_years,hold_years,
                 min_train,min_val,min_hold,allow_priority):
    single_survivors=[];tested=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy()
        feats=features_for(so,families,train_years,val_years,min_train,min_val)
        tr=so[so.season.between(*train_years)]
        for f in feats:
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    cm=cond(so,f,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested+=1;m=cm&pmask(so,pb)
                        a=period_metrics(so,m,train_years);b=period_metrics(so,m,val_years)
                        ba=baseline(so,pb,train_years);bb=baseline(so,pb,val_years)
                        if not all([a,b,ba,bb]):continue
                        if a['n']<min_train or b['n']<min_val:continue
                        if a['roi']<.02 or b['roi']<.01:continue
                        if a['roi']-ba['roi']<.015 or b['roi']-bb['roi']<.005:continue
                        pre=metrics(so[m&so.season.between(train_years[0],val_years[1])])
                        bp=baseline(so,pb,(train_years[0],val_years[1]))
                        if not pre or not bp or pre['roi']-bp['roi']<.015 or pre['positive_season_ratio']<.60:continue
                        score=min(a['roi'],b['roi'])*math.sqrt(min(a['n'],b['n']))
                        single_survivors.append({'outcome':outcome,'price_band':pb,'feature':f,'family':family_of(f),
                            'op':op,'threshold':float(t),'pre_score':score,'train':a,'validation':b,
                            'preholdout':pre,'preholdout_baseline':bp,'preholdout_lift':pre['roi']-bp['roi']})
    single_survivors.sort(key=lambda x:(x['pre_score'],x['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in single_survivors:
        k=(r['outcome'],r['price_band'],r['feature'])
        if k in seen:continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=180:break

    results=[]
    for r in frozen:
        so=s[s.outcome.eq(r['outcome'])].copy()
        m=cond(so,r['feature'],r['op'],r['threshold'])&pmask(so,r['price_band'])
        hold=period_metrics(so,m,hold_years);hb=baseline(so,r['price_band'],hold_years)
        full=metrics(so[m&so.season.between(train_years[0],hold_years[1])])
        x=so[m&so.season.between(train_years[0],hold_years[1])]
        ci=bootstrap(x.profit) if len(x) else [None,None]
        conc=team_concentration(x,meta)
        # one-axis threshold stability
        vals=pd.to_numeric(so.loc[so.season.between(*train_years),r['feature']],errors='coerce').dropna()
        sd=float(vals.std()) if len(vals)>1 else 0.0
        step=max(abs(r['threshold'])*.05,sd*.05,.01)
        neigh=[]
        for t in [r['threshold']-2*step,r['threshold']-step,r['threshold'],r['threshold']+step,r['threshold']+2*step]:
            mm=cond(so,r['feature'],r['op'],t)&pmask(so,r['price_band'])
            eras=[period_metrics(so,mm,y) for y in [train_years,val_years,hold_years]]
            neigh.append({'threshold':float(t),'train':eras[0],'validation':eras[1],'holdout':eras[2]})
        stable=sum(1 for q in neigh if all(q[e] and q[e]['roi']>0 for e in ['train','validation','holdout']))
        lift=(hold['roi']-hb['roi']) if hold and hb else None
        status='RESEARCH_ONLY'
        if allow_priority and hold and full and hold['n']>=min_hold and full['n']>=160 and hold['roi']>=.04 and lift is not None and lift>=0 and full['roi']>=.05 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and stable>=4 and conc['top3_share']<.30:
            status='PROSPECTIVE_PRIORITY_SHADOW'
        elif hold and full and hold['n']>=max(20,min_hold//2) and hold['roi']>0 and full['positive_season_ratio']>=.60 and stable>=3 and conc['top3_share']<.35:
            status='SHADOW_WATCH' if allow_priority else 'EXPLORATORY_WATCH'
        results.append({**r,'horizon':name,'status':status,'holdout':hold,'holdout_baseline':hb,'holdout_lift':lift,
                        'full':full,'bootstrap95_roi':ci,'threshold_neighbors_positive_all_eras':stable,
                        'threshold_neighbors':neigh,'team_concentration':conc,'yearly':yearly(x)})

    # Pair only strongest frozen rules from different NEW families.
    groups=defaultdict(list)
    for r in frozen:groups[(r['outcome'],r['price_band'])].append(r)
    pairs=[];tested_pairs=0
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:30],2):
            if a['family']==b['family']:continue
            tested_pairs+=1
            m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,pb)
            tr=period_metrics(so,m,train_years);va=period_metrics(so,m,val_years)
            bt=baseline(so,pb,train_years);bv=baseline(so,pb,val_years)
            if not all([tr,va,bt,bv]):continue
            if tr['n']<max(25,min_train-10) or va['n']<max(20,min_val-10):continue
            if tr['roi']<.035 or va['roi']<.02 or tr['roi']-bt['roi']<.025 or va['roi']-bv['roi']<.01:continue
            pre=metrics(so[m&so.season.between(train_years[0],val_years[1])]);bp=baseline(so,pb,(train_years[0],val_years[1]))
            if not pre or not bp or pre['roi']-bp['roi']<.025 or pre['positive_season_ratio']<.60:continue
            pairs.append({'outcome':outcome,'price_band':pb,
                          'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                          'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                          'pre_score':min(tr['roi'],va['roi'])*math.sqrt(min(tr['n'],va['n'])),
                          'train':tr,'validation':va,'preholdout':pre,'preholdout_baseline':bp})
    pairs.sort(key=lambda x:(x['pre_score'],x['preholdout']['n']),reverse=True)
    pair_results=[]
    for r in pairs[:80]:
        so=s[s.outcome.eq(r['outcome'])].copy();a=r['rule1'];b=r['rule2']
        m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,r['price_band'])
        hold=period_metrics(so,m,hold_years);hb=baseline(so,r['price_band'],hold_years)
        full=metrics(so[m&so.season.between(train_years[0],hold_years[1])]);x=so[m&so.season.between(train_years[0],hold_years[1])]
        ci=bootstrap(x.profit,seed=20260921) if len(x) else [None,None];conc=team_concentration(x,meta)
        lift=(hold['roi']-hb['roi']) if hold and hb else None
        status='RESEARCH_ONLY'
        if allow_priority and hold and full and hold['n']>=max(35,min_hold-10) and full['n']>=130 and hold['roi']>=.05 and lift is not None and lift>=.01 and full['roi']>=.07 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and conc['top3_share']<.30:
            status='PROSPECTIVE_PRIORITY_SHADOW'
        elif hold and full and hold['n']>=25 and hold['roi']>0 and full['positive_season_ratio']>=.60 and conc['top3_share']<.35:
            status='SHADOW_WATCH' if allow_priority else 'EXPLORATORY_WATCH'
        pair_results.append({**r,'horizon':name,'status':status,'holdout':hold,'holdout_baseline':hb,
                             'holdout_lift':lift,'full':full,'bootstrap95_roi':ci,
                             'team_concentration':conc,'yearly':yearly(x)})
    return {'name':name,'families':sorted(families),'train_years':train_years,'validation_years':val_years,'holdout_years':hold_years,
            'tested_single_contexts':tested,'preholdout_single_survivors':len(single_survivors),'frozen_singles':len(frozen),
            'tested_pairs':tested_pairs,'pair_survivors':len(pairs),
            'single_results':results,'pair_results':pair_results}

def candidate_rows(s,c):
    so=s[s.outcome.eq(c['outcome'])].copy();m=pmask(so,c['price_band'])
    if 'feature' in c:m &= cond(so,c['feature'],c['op'],c['threshold'])
    else:
        for k in ['rule1','rule2']:
            r=c[k];m &= cond(so,r['feature'],r['op'],r['threshold'])
    return so[m].copy()

def active_overlap(master,s,candidates):
    # Existing active methods use the prior 323-column semantics; build those selections independently.
    old=arx.merged_selection_rows(master)
    active_sets={}
    for m in active.METHODS:
        z=active.method_rows(old,m)
        active_sets[m['id']]=set(zip(z.match_id.astype(str),z.outcome.astype(str)))
    out=[]
    for c in candidates:
        z=candidate_rows(s,c)
        cs=set(zip(z.match_id.astype(str),z.outcome.astype(str)))
        ovs=[]
        for mid,aset in active_sets.items():
            n=len(cs&aset);ovs.append({'active_method':mid,'exact_overlap':n,'pct_candidate':n/max(1,len(cs))})
        out.append({'candidate_key':candidate_key(c),'candidate_n':len(cs),'active_overlap':sorted(ovs,key=lambda x:x['pct_candidate'],reverse=True)})
    return out

def candidate_key(c):
    if 'feature' in c:return f"{c['horizon']}|{c['outcome']}|{c['price_band']}|{c['feature']}|{c['op']}|{c['threshold']}"
    return f"{c['horizon']}|{c['outcome']}|{c['price_band']}|{c['rule1']['feature']}|{c['rule2']['feature']}"

def main():
    if not DATA.exists():raise RuntimeError(f'Missing master warehouse: {DATA}')
    d=pd.read_parquet(DATA).copy();d['match_id']=d.match_id.astype(str)
    s,layers=make_selection_rows(d)
    meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id')

    long=scan_horizon(s,meta,'LONG_2013_2025',LONG_FAMILIES,(2013,2018),(2019,2022),(2023,2025),40,30,50,True)
    tx=scan_horizon(s,meta,'TRANSACTIONS_2021_2024',{'transactions'},(2021,2022),(2023,2023),(2024,2024),30,20,25,False)
    lineup=scan_horizon(s,meta,'LINEUP_REF_2023_2025',{'confirmed_lineup','referee_discipline'},(2023,2023),(2024,2024),(2025,2025),25,25,25,False)
    avail=scan_horizon(s,meta,'AVAILABILITY_2024_2025',{'availability'},(2024,2024),(2025,2025),(2025,2025),25,25,25,False)

    allres=[]
    for h in [long,tx,lineup,avail]:allres.extend(h['single_results']+h['pair_results'])
    priority=[x for x in allres if x['status']=='PROSPECTIVE_PRIORITY_SHADOW']
    watch=[x for x in allres if x['status'] in {'SHADOW_WATCH','EXPLORATORY_WATCH'}]
    overlaps=active_overlap(d,s,priority+[x for x in watch if x['horizon']=='LONG_2013_2025'])

    payload={'built_at':now(),'dataset':str(DATA),'rows':len(d),'columns':len(d.columns),'selection_rows':len(s),
             'new_feature_columns':int(sum(1 for c in s.columns if family_of(c) in set(LAYER_FILES))),
             'status':'SHADOW_RESEARCH_ONLY','horizons':[long,tx,lineup,avail],
             'prospective_priority_count':len(priority),'watch_count':len(watch),
             'prospective_priority_preholdout_order':priority,
             'watch_candidates':watch,'active_method_overlap':overlaps,
             'design':'Only new feature families are mined. Long-history families may qualify for priority using 2013-18 train, 2019-22 validation, and untouched 2023-25 holdout. Transactions/lineup/referee/availability remain exploratory because their historical windows are shorter. Existing active methods are never changed by this scan.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({'rows':len(d),'columns':len(d.columns),'selection_rows':len(s),'new_feature_columns':payload['new_feature_columns'],
                      'long':{k:long[k] for k in ['tested_single_contexts','preholdout_single_survivors','frozen_singles','tested_pairs','pair_survivors']},
                      'transactions':{k:tx[k] for k in ['tested_single_contexts','preholdout_single_survivors','frozen_singles']},
                      'lineup':{k:lineup[k] for k in ['tested_single_contexts','preholdout_single_survivors','frozen_singles']},
                      'availability':{k:avail[k] for k in ['tested_single_contexts','preholdout_single_survivors','frozen_singles']},
                      'priority_count':len(priority),'watch_count':len(watch)},indent=2))
    for i,c in enumerate(priority,1):
        print('PRIORITY',i,json.dumps({k:c.get(k) for k in ['horizon','outcome','price_band','feature','op','threshold','rule1','rule2','holdout','full','bootstrap95_roi','team_concentration']},default=str))
    for i,c in enumerate([x for x in watch if x['horizon']=='LONG_2013_2025'][:12],1):
        print('LONG_WATCH',i,json.dumps({k:c.get(k) for k in ['outcome','price_band','feature','op','threshold','rule1','rule2','holdout','full','bootstrap95_roi']},default=str))

if __name__=='__main__':main()
