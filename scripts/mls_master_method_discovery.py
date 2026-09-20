#!/usr/bin/env python3
from __future__ import annotations

import itertools, json, math, re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_arsenal_research as arx
import mls_active_portfolio as active

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_master.parquet'
OUT=ROOT/'research/master_discovery'
OUT.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2013,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
 ('ALL',0.0,1.0),('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),
 ('P35_45',.35,.45),('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70)
]
LONG_FAMILIES={'salary','style','cross_comp','player_manager','goalkeeper_roster','venue','weather','referee'}
RECENT_SPECS={
 'availability':{'train':('2024-02-01','2024-07-31'),'validation':('2024-08-01','2024-12-31'),'holdout':('2025-01-01','2025-12-31'),'label':'RECENT_2024_TO_2025'},
 'confirmed_lineup':{'train':('2022-01-01','2023-12-31'),'validation':('2024-01-01','2024-12-31'),'holdout':('2025-01-01','2025-12-31'),'label':'RECENT_2022_TO_2025'},
 'transactions':{'train':('2021-01-01','2023-12-31'),'validation':('2024-01-01','2024-12-31'),'holdout':('2025-01-01','2025-04-04'),'label':'RECENT_PARTIAL_2025_THROUGH_APR04'},
}
KEYS={'match_id','date','season','outcome','win','odds','market_prob','profit'}

def now():return datetime.now(timezone.utc).isoformat()

def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum'])
    active_seasons=by[by['count']>=5]
    return {
      'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
      'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
      'active_seasons':int(len(active_seasons)),
      'positive_seasons':int((active_seasons['sum']>0).sum()),
      'positive_season_ratio':float((active_seasons['sum']>0).mean()) if len(active_seasons) else 0.0,
    }

def period(df,k):
    lo,hi=SPLIT[k]
    return df.season.between(lo,hi)

def date_period(df,lo,hi):
    x=pd.to_datetime(df.date,errors='coerce')
    return x.between(pd.Timestamp(lo),pd.Timestamp(hi),inclusive='both')

def pmask(df,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return df.market_prob.between(lo,hi,inclusive='both')

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)

def thresholds(v,qs=(.20,.35,.50,.65,.80)):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=7:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile(list(qs)).dropna()))

def family_of(c):
    n=c.lower()
    if n.startswith('hist_mb_'):return 'market_movement'
    if 'availability_' in n:return 'availability'
    if 'confirmed_' in n or '_team_yellows' in n or '_team_reds' in n:return 'confirmed_lineup'
    if 'salary_' in n:return 'salary'
    if 'transaction_' in n or 'transactions_' in n:return 'transactions'
    if 'style_' in n:return 'style'
    if any(x in n for x in ['allcomp_','external_matches','external_minutes','days_since_external','us_open_cup_','leagues_cup_','concacaf_','canadian_championship_']):return 'cross_comp'
    if 'weather_' in n:return 'weather'
    if 'manager_' in n or 'player_' in n:return 'player_manager'
    if 'gk_' in n or 'roster_' in n:return 'goalkeeper_roster'
    if 'referee_' in n:return 'referee'
    if any(x in n for x in ['stadium_','venue_','travel_from_prev_match','surface_']):return 'venue'
    return None

def clean_rest(rest):
    return re.sub(r'[^a-z0-9_]+','_',rest.lower()).strip('_')

def build_selection_matrix(d):
    # Existing selection semantics remain authoritative for regular-season priced rows.
    s=arx.merged_selection_rows(d).copy()
    s['match_id']=s.match_id.astype(str)
    master=d.copy();master['match_id']=master.match_id.astype(str)
    idx=master.set_index('match_id',drop=False)

    numeric=[c for c in master.columns if c!='match_id' and pd.api.types.is_numeric_dtype(master[c])]
    safe={c:family_of(c) for c in numeric}
    safe={c:f for c,f in safe.items() if f}

    # Match-level raw safe fields used to derive selection-relative features.
    raw=master[['match_id']+list(safe)].drop_duplicates('match_id')
    s=s.merge(raw,on='match_id',how='left',validate='m:1',suffixes=('','__master'))

    added={};feature_family={}
    used=set()
    for hc,fam in list(safe.items()):
        if not hc.startswith('home_'):continue
        rest=hc[5:];ac='away_'+rest
        if ac not in safe or safe[ac]!=fam:continue
        hv=pd.to_numeric(s[hc],errors='coerce');av=pd.to_numeric(s[ac],errors='coerce')
        home=s.outcome.eq('HOME');away=s.outcome.eq('AWAY');draw=s.outcome.eq('DRAW')
        sel=np.where(home,hv,np.where(away,av,np.nan))
        opp=np.where(home,av,np.where(away,hv,np.nan))
        base='new__'+fam+'__'+clean_rest(rest)
        added[base+'__sel']=sel;feature_family[base+'__sel']=fam
        added[base+'__opp']=opp;feature_family[base+'__opp']=fam
        added[base+'__edge']=np.where(home,hv-av,np.where(away,av-hv,np.nan));feature_family[base+'__edge']=fam
        added[base+'__balance']=np.where(draw,np.abs(hv-av),np.nan);feature_family[base+'__balance']=fam
        added[base+'__combined']=np.where(draw,hv+av,np.nan);feature_family[base+'__combined']=fam
        used.update({hc,ac})

    # Explicit home-oriented edge fields get flipped for AWAY and absolute-valued for draws.
    for c,fam in safe.items():
        if c in used:continue
        n=c.lower()
        if n.startswith('edge_home_'):
            v=pd.to_numeric(s[c],errors='coerce')
            sign=np.where(s.outcome.eq('HOME'),1,np.where(s.outcome.eq('AWAY'),-1,np.nan))
            name='new__'+fam+'__'+clean_rest(c[10:])+'__edge'
            added[name]=np.where(s.outcome.eq('DRAW'),np.abs(v),v*sign);feature_family[name]=fam
            used.add(c)

    # Global fields are safe for every outcome. Skip ambiguous raw home/away/edge columns.
    for c,fam in safe.items():
        if c in used or c.startswith(('home_','away_','edge_')):continue
        name='new__'+fam+'__global__'+clean_rest(c)
        added[name]=pd.to_numeric(s[c],errors='coerce').to_numpy()
        feature_family[name]=fam

    if added:
        s=pd.concat([s,pd.DataFrame(added,index=s.index)],axis=1)
    # Drop duplicated raw master payload; keep only engineered new__ features + original arx fields.
    drop=[c for c in safe if c in s.columns and c not in KEYS]
    s=s.drop(columns=drop,errors='ignore')
    return s,feature_family

def baseline(so,pb,mask):
    return metrics(so[pmask(so,pb)&mask])

def eval_pre(so,m,pb):
    out={}
    for era in ['train','validation']:
        em=period(so,era);z=metrics(so[m&em]);b=baseline(so,pb,em)
        if not z or not b:return None
        out[era]=z;out[era+'_baseline']=b;out[era+'_lift']=z['roi']-b['roi']
    pre=period(so,'train')|period(so,'validation')
    z=metrics(so[m&pre]);b=baseline(so,pb,pre)
    if not z or not b:return None
    out['preholdout']=z;out['preholdout_baseline']=b;out['preholdout_lift']=z['roi']-b['roi']
    return out

def long_gate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return (
      tr['n']>=40 and va['n']>=30 and pre['n']>=110
      and tr['roi']>=.02 and va['roi']>=.01 and pre['roi']>=.025
      and e['train_lift']>=.01 and e['validation_lift']>=0 and e['preholdout_lift']>=.015
      and pre['positive_season_ratio']>=.60
    )

def pair_gate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return (
      tr['n']>=30 and va['n']>=24 and pre['n']>=85
      and tr['roi']>=.035 and va['roi']>=.02 and pre['roi']>=.04
      and e['train_lift']>=.02 and e['validation_lift']>=.005 and e['preholdout_lift']>=.025
      and pre['positive_season_ratio']>=.60
    )

def pre_score(e):
    return min(e['train']['roi'],e['validation']['roi'])*math.sqrt(max(1,min(e['train']['n'],e['validation']['n'])))

def boot(v,n=2500,seed=9251):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a));means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,size=len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]

def eligible_long_features(s,fmap,outcome):
    tr=s[period(s,'train')];va=s[period(s,'validation')]
    out=[]
    for f,fam in fmap.items():
        if fam not in LONG_FAMILIES or f not in s:continue
        if outcome=='DRAW' and f.endswith(('__sel','__opp','__edge')):continue
        if outcome!='DRAW' and f.endswith(('__balance','__combined')):continue
        tv=pd.to_numeric(tr[f],errors='coerce');vv=pd.to_numeric(va[f],errors='coerce')
        if tv.notna().sum()<300 or vv.notna().sum()<220:continue
        if tv.nunique(dropna=True)<2:continue
        out.append(f)
    return out

def candidate_status(so,r):
    m=cond(so,r['feature'],r['op'],r['threshold'])&pmask(so,r['price_band'])
    x=so[m].copy();hold=metrics(x[period(x,'holdout')]);full=metrics(x)
    hb=baseline(so,r['price_band'],period(so,'holdout'))
    lift=hold['roi']-hb['roi'] if hold and hb else None
    ci=boot(x.profit) if len(x) else [None,None]
    train_vals=pd.to_numeric(so.loc[period(so,'train'),r['feature']],errors='coerce').dropna()
    sd=float(train_vals.std()) if len(train_vals)>1 else 0.0
    step=max(abs(float(r['threshold']))*.04,sd*.04,0.005)
    stable=0;neighbors=[]
    for t in [r['threshold']-step,r['threshold'],r['threshold']+step]:
        mm=cond(so,r['feature'],r['op'],t)&pmask(so,r['price_band'])
        eras={e:metrics(so[mm&period(so,e)]) for e in ['train','validation','holdout']}
        ok=all(eras[e] and eras[e]['roi']>0 for e in eras)
        stable+=int(ok);neighbors.append({'threshold':float(t),**eras})
    status='RESEARCH_ONLY'
    if hold and full and hold['n']>=45 and hold['roi']>=.05 and lift is not None and lift>=0 and full['n']>=180 and full['roi']>=.05 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and stable>=2:
        status='STRONG_SHADOW_CANDIDATE'
    elif hold and full and hold['n']>=30 and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.65:
        status='SHADOW_WATCH'
    return {**r,'holdout':hold,'holdout_baseline':hb,'holdout_lift':lift,'full':full,'bootstrap95_roi':ci,'neighbor_stability':stable,'neighbors':neighbors,'status':status}

def pair_status(so,r):
    a,b=r['rule1'],r['rule2']
    m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,r['price_band'])
    x=so[m].copy();hold=metrics(x[period(x,'holdout')]);full=metrics(x);hb=baseline(so,r['price_band'],period(so,'holdout'))
    lift=hold['roi']-hb['roi'] if hold and hb else None;ci=boot(x.profit,seed=10391) if len(x) else [None,None]
    status='RESEARCH_ONLY'
    if hold and full and hold['n']>=40 and hold['roi']>=.06 and lift is not None and lift>=.01 and full['n']>=150 and full['roi']>=.07 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0:
        status='STRONG_SHADOW_CANDIDATE'
    elif hold and full and hold['n']>=28 and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.65:
        status='SHADOW_WATCH'
    return {**r,'holdout':hold,'holdout_baseline':hb,'holdout_lift':lift,'full':full,'bootstrap95_roi':ci,'status':status}

def long_discovery(s,fmap):
    survivors=[];tested=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy();tr=so[period(so,'train')]
        for f in eligible_long_features(so,fmap,outcome):
            fam=fmap[f]
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    cm=cond(so,f,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested+=1;e=eval_pre(so,cm&pmask(so,pb),pb)
                        if e and long_gate(e):
                            survivors.append({'outcome':outcome,'price_band':pb,'feature':f,'family':fam,'op':op,'threshold':t,'pre_score':pre_score(e),'pre':e})
    survivors.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in survivors:
        k=(r['outcome'],r['price_band'],r['feature'])
        if k in seen:continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=180:break
    opened=[candidate_status(s[s.outcome.eq(r['outcome'])].copy(),r) for r in frozen]

    groups=defaultdict(list)
    for r in frozen:groups[(r['outcome'],r['price_band'])].append(r)
    pairs=[];tested_pairs=0
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:24],2):
            if a['family']==b['family']:continue
            tested_pairs+=1
            m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,pb)
            e=eval_pre(so,m,pb)
            if e and pair_gate(e):
                pairs.append({'outcome':outcome,'price_band':pb,
                              'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                              'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                              'families':sorted({a['family'],b['family']}),'pre_score':pre_score(e),'pre':e})
    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    fp=[];seen=set()
    for r in pairs:
        k=(r['outcome'],r['price_band'],tuple(sorted([r['rule1']['feature'],r['rule2']['feature']])))
        if k in seen:continue
        seen.add(k);fp.append(r)
        if len(fp)>=100:break
    opened_pairs=[pair_status(s[s.outcome.eq(r['outcome'])].copy(),r) for r in fp]
    return {'tested_single_contexts':tested,'preholdout_single_survivors':len(survivors),'frozen_singles':len(frozen),'opened_singles':opened,
            'tested_cross_family_pairs':tested_pairs,'preholdout_pair_survivors':len(pairs),'frozen_pairs':len(fp),'opened_pairs':opened_pairs}

def recent_metrics(x,sp):
    out={}
    for k in ['train','validation','holdout']:
        lo,hi=sp[k];out[k]=metrics(x[date_period(x,lo,hi)])
    return out

def recent_discovery(s,fmap):
    results={}
    for fam,sp in RECENT_SPECS.items():
        candidates=[];tested=0
        for outcome in ['HOME','DRAW','AWAY']:
            so=s[s.outcome.eq(outcome)].copy()
            tr=so[date_period(so,*sp['train'])]
            va=so[date_period(so,*sp['validation'])]
            for f,ff in fmap.items():
                if ff!=fam or f not in so:continue
                if outcome=='DRAW' and f.endswith(('__sel','__opp','__edge')):continue
                if outcome!='DRAW' and f.endswith(('__balance','__combined')):continue
                tv=pd.to_numeric(tr[f],errors='coerce');vv=pd.to_numeric(va[f],errors='coerce')
                if tv.notna().sum()<80 or vv.notna().sum()<60 or tv.nunique(dropna=True)<2:continue
                for t in thresholds(tv,qs=(.25,.50,.75)):
                    for op in ['>=','<=']:
                        cm=cond(so,f,op,t)
                        for pb,_,__ in PRICE_BANDS:
                            tested+=1
                            x=so[cm&pmask(so,pb)].copy();em=recent_metrics(x,sp)
                            a,b=em['train'],em['validation']
                            if not a or not b or a['n']<18 or b['n']<15 or a['roi']<.03 or b['roi']<.02:continue
                            candidates.append({'outcome':outcome,'price_band':pb,'feature':f,'family':fam,'op':op,'threshold':t,'train':a,'validation':b,
                                               'pre_score':min(a['roi'],b['roi'])*math.sqrt(min(a['n'],b['n']))})
        candidates.sort(key=lambda x:(x['pre_score'],x['train']['n']+x['validation']['n']),reverse=True)
        frozen=[];seen=set()
        for r in candidates:
            k=(r['outcome'],r['price_band'],r['feature'])
            if k in seen:continue
            seen.add(k)
            so=s[s.outcome.eq(r['outcome'])].copy();x=so[cond(so,r['feature'],r['op'],r['threshold'])&pmask(so,r['price_band'])]
            h=recent_metrics(x,sp)['holdout']
            status='RECENT_RESEARCH_ONLY'
            if h and h['n']>=20 and h['roi']>0:status='RECENT_SHADOW_WATCH'
            r={**r,'holdout':h,'status':status,'coverage_design':sp['label']}
            frozen.append(r)
            if len(frozen)>=40:break
        results[fam]={'tested':tested,'preholdout_survivors':len(candidates),'frozen':frozen,'design':sp}
    return results

def zscore_diff(win,loss):
    w=pd.to_numeric(win,errors='coerce').dropna();l=pd.to_numeric(loss,errors='coerce').dropna()
    if len(w)<8 or len(l)<8:return None
    pooled=np.sqrt((w.var(ddof=1)+l.var(ddof=1))/2)
    if not np.isfinite(pooled) or pooled<=1e-12:return None
    return float((l.mean()-w.mean())/pooled)

def active_loss_forensics(s,fmap):
    out=[]
    long_feats=[f for f,fam in fmap.items() if fam in LONG_FAMILIES and f in s]
    for spec in active.METHODS:
        so=s[s.outcome.eq(spec['side'])].copy()
        m=so.market_prob.between(spec['lo'],spec['hi'],inclusive='both')
        for f,op,t in spec['rules']:m &= active.cond(so,f,op,t)
        base=so[m].copy();pre=base[period(base,'train')|period(base,'validation')].copy();hold=base[period(base,'holdout')].copy()
        bpre=metrics(pre);bhold=metrics(hold)
        wins=pre[pre.win.eq(1)];losses=pre[pre.win.eq(0)]
        fingerprints=[]
        for f in long_feats:
            fam=fmap[f];v=pd.to_numeric(pre[f],errors='coerce')
            if v.notna().sum()<max(40,int(.65*len(pre))) or v.nunique(dropna=True)<2:continue
            zd=zscore_diff(wins[f],losses[f])
            if zd is not None:fingerprints.append({'feature':f,'family':fam,'loss_minus_win_z':zd})
        fingerprints.sort(key=lambda x:abs(x['loss_minus_win_z']),reverse=True)
        vetoes=[]
        for item in fingerprints[:55]:
            f=item['feature']
            for t in thresholds(pre[f]):
                for op in ['>=','<=']:
                    risk=cond(pre,f,op,t);rm=metrics(pre[risk]);sm=metrics(pre[~risk])
                    if not rm or not sm or rm['n']<12 or sm['n']<max(35,int(.45*len(pre))):continue
                    improve=sm['roi']-bpre['roi'];drop=bpre['roi']-rm['roi']
                    if improve<.025 or drop<.06:continue
                    vetoes.append({'feature':f,'family':fmap[f],'op':op,'threshold':t,'score':improve+.25*drop+.004*math.sqrt(sm['n']),
                                   'pre_risk':rm,'pre_safe':sm})
        vetoes.sort(key=lambda x:x['score'],reverse=True)
        frozen=[];seen=set()
        for v in vetoes:
            if v['feature'] in seen:continue
            seen.add(v['feature'])
            risk=cond(hold,v['feature'],v['op'],v['threshold']);v['hold_risk']=metrics(hold[risk]);v['hold_safe']=metrics(hold[~risk])
            v['validated']=bool(bhold and v['hold_risk'] and v['hold_safe'] and v['hold_safe']['n']>=20 and v['hold_safe']['roi']>=bhold['roi']+.015 and v['hold_risk']['roi']<bhold['roi'])
            frozen.append(v)
            if len(frozen)>=12:break
        out.append({'method_id':spec['id'],'base_preholdout':bpre,'base_holdout':bhold,'top_loss_fingerprints':fingerprints[:20],
                    'frozen_vetoes':frozen,'validated_veto_count':sum(v['validated'] for v in frozen)})
    return out

def movement_diagnostics(d,s):
    if 'hist_mb_movement_available' not in d:return []
    movement_cols=[c for c in d.columns if c.startswith('hist_mb_move_') and c.endswith('_prob_mean')]
    if not movement_cols:return []
    mm=d[['match_id','hist_mb_movement_available']+movement_cols].copy();mm.match_id=mm.match_id.astype(str)
    out=[]
    for spec in active.METHODS:
        so=s[s.outcome.eq(spec['side'])].copy()
        m=so.market_prob.between(spec['lo'],spec['hi'],inclusive='both')
        for f,op,t in spec['rules']:m &= active.cond(so,f,op,t)
        x=so[m][['match_id','outcome','win','profit']].copy();x.match_id=x.match_id.astype(str);x=x.merge(mm,on='match_id',how='inner')
        x=x[pd.to_numeric(x.hist_mb_movement_available,errors='coerce').eq(1)]
        selected=[]
        for c in movement_cols:
            if '_home_prob_mean' in c:
                base=c.replace('_home_prob_mean','')
                dcol=base+'_draw_prob_mean';acol=base+'_away_prob_mean'
                if dcol not in x or acol not in x:continue
                v=np.where(x.outcome.eq('HOME'),x[c],np.where(x.outcome.eq('AWAY'),x[acol],x[dcol]))
                selected.append((base,v))
        diag=[]
        for base,v in selected:
            q=pd.DataFrame({'v':pd.to_numeric(v,errors='coerce'),'win':x.win}).dropna()
            if len(q)<10:continue
            diag.append({'interval':base.replace('hist_mb_move_',''),'n':len(q),
                         'winner_mean_move':float(q.loc[q.win.eq(1),'v'].mean()) if q.win.eq(1).any() else None,
                         'loser_mean_move':float(q.loc[q.win.eq(0),'v'].mean()) if q.win.eq(0).any() else None})
        out.append({'method_id':spec['id'],'movement_overlap_n':int(len(x)),'diagnostics':diag})
    return out

def compact_long(block):
    strong=[r for r in block['opened_singles']+block['opened_pairs'] if r['status']=='STRONG_SHADOW_CANDIDATE']
    watch=[r for r in block['opened_singles']+block['opened_pairs'] if r['status']=='SHADOW_WATCH']
    return strong,watch

def main():
    if not DATA.exists():raise RuntimeError('Final MLS master warehouse missing')
    d=pd.read_parquet(DATA)
    if len(d)!=9440 or len(d.columns)<1034:raise RuntimeError(f'Unexpected master shape {d.shape}; refusing stale dataset')
    s,fmap=build_selection_matrix(d)
    long=long_discovery(s,fmap);strong,watch=compact_long(long)
    recent=recent_discovery(s,fmap)
    loss=active_loss_forensics(s,fmap)
    movement=movement_diagnostics(d,s)

    # Preserve preholdout ordering; holdout is a pass/fail characterization, not a reranking key.
    payload={
      'built_at':now(),'status':'SHADOW_RESEARCH_ONLY','dataset':str(DATA),'master_rows':len(d),'master_columns':len(d.columns),
      'selection_rows':len(s),'engineered_candidate_features':len(fmap),'splits':SPLIT,
      'design':{
        'primary':'All thresholds/rankings frozen on 2013-18 train + 2019-22 validation. 2023-25 opened afterward. Candidate must beat same outcome/price-band baseline.',
        'recent':'Recent-only source families use explicit time-forward development/validation/holdout windows and can only become RECENT_SHADOW_WATCH.',
        'movement':'2015-16 continuous multi-book movement is diagnostic/confirmation evidence only; it is not used to originate long-history methods.',
        'existing_methods':'Active arsenal rules remain frozen. New-feature vetoes are discovered preholdout and validated on 2023-25.'
      },
      'long_history':{
        'tested_single_contexts':long['tested_single_contexts'],'preholdout_single_survivors':long['preholdout_single_survivors'],'frozen_singles':long['frozen_singles'],
        'tested_cross_family_pairs':long['tested_cross_family_pairs'],'preholdout_pair_survivors':long['preholdout_pair_survivors'],'frozen_pairs':long['frozen_pairs'],
        'strong_candidates_preholdout_order':strong,'watch_candidates_preholdout_order':watch,
        'all_opened_singles':long['opened_singles'],'all_opened_pairs':long['opened_pairs'],
      },
      'recent_only':recent,'active_method_loss_forensics':loss,'market_movement_diagnostics':movement,
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=[
      'MLS MASTER METHOD DISCOVERY',
      '='*100,
      f"master={len(d):,} x {len(d.columns):,} | selection_rows={len(s):,} | engineered_candidates={len(fmap):,}",
      f"long tested singles={long['tested_single_contexts']:,} pairs={long['tested_cross_family_pairs']:,}",
      f"strong new candidates={len(strong)} | watch candidates={len(watch)}",
      '',
      'STRONG LONG-HISTORY CANDIDATES (preholdout order)'
    ]
    for i,r in enumerate(strong[:20],1):
        if 'feature' in r:
            lines.append(f"{i}. {r['outcome']} {r['price_band']} | {r['feature']} {r['op']} {r['threshold']:.6g} | pre ROI {r['pre']['preholdout']['roi']:+.1%} n={r['pre']['preholdout']['n']} | hold {r['holdout']['roi']:+.1%} n={r['holdout']['n']} | full {r['full']['roi']:+.1%} n={r['full']['n']}")
        else:
            a,b=r['rule1'],r['rule2']
            lines.append(f"{i}. {r['outcome']} {r['price_band']} | {a['feature']} {a['op']} {a['threshold']:.5g} + {b['feature']} {b['op']} {b['threshold']:.5g} | hold {r['holdout']['roi']:+.1%} n={r['holdout']['n']} | full {r['full']['roi']:+.1%} n={r['full']['n']}")
    lines+=['','VALIDATED VETOES ON CURRENT ACTIVE METHODS']
    for m in loss:
        vals=[v for v in m['frozen_vetoes'] if v['validated']]
        if vals:
            for v in vals:
                lines.append(f"{m['method_id']} | veto risk when {v['feature']} {v['op']} {v['threshold']:.6g} | hold safe {v['hold_safe']['roi']:+.1%} n={v['hold_safe']['n']} vs base {m['base_holdout']['roi']:+.1%}")
        else:lines.append(f"{m['method_id']} | no new veto passed untouched holdout")
    lines+=['','RECENT-ONLY WATCH COUNTS']
    for fam,z in recent.items():
        lines.append(f"{fam}: {sum(1 for r in z['frozen'] if r['status']=='RECENT_SHADOW_WATCH')} watch / {len(z['frozen'])} frozen")
    (OUT/'latest.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:120]))

if __name__=='__main__':main()
