#!/usr/bin/env python3
from __future__ import annotations

import itertools, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mls_master_method_discovery as md
import mls_active_portfolio as active
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
MASTER=ROOT/'data/processed/mls_match_features_master.parquet'
IP=ROOT/'data/processed/mls_individual_player_pregame_features.parquet'
OUT=ROOT/'research/omni_discovery'
OUT.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2013,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
 ('ALL',0,1),('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),
 ('P35_45',.35,.45),('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70)
]
KEYS={'match_id','date','season','outcome','win','odds','market_prob','profit'}

def now():return datetime.now(timezone.utc).isoformat()

def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum'])
    a=by[by['count']>=5]
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
            'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
            'active_seasons':int(len(a)),'positive_seasons':int((a['sum']>0).sum()),
            'positive_season_ratio':float((a['sum']>0).mean()) if len(a) else 0.0}

def period(df,k):
    lo,hi=SPLIT[k];return df.season.between(lo,hi)

def pmask(df,pb):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==pb)
    return pd.to_numeric(df.market_prob,errors='coerce').between(lo,hi,inclusive='both')

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)

def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=7:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.20,.35,.50,.65,.80]).dropna()))

def boot(v,n=3500,seed=20261002):
    a=np.asarray(v,float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed+len(a));means=np.empty(n)
    for i in range(n):means[i]=rng.choice(a,len(a),replace=True).mean()
    return [float(x) for x in np.quantile(means,[.025,.975])]

def yearly(x):
    return [{'season':int(y),**metrics(z)} for y,z in x.groupby('season')]

def family_of(f):
    if f.startswith('ip__'):return 'individual_player'
    if f.startswith('market__'):return 'market'
    if f.startswith('base__'):return 'base'
    if f.startswith('pm__'):return 'player_manager'
    if f.startswith('ctx__'):return 'context'
    if f.startswith('wx__'):return 'weather'
    if f.startswith('new__'):
        p=f.split('__',2)
        return p[1] if len(p)>1 else 'other'
    return 'other'

def team_concentration(x,meta):
    z=x[['match_id','outcome']].merge(meta,on='match_id',how='left')
    if z.empty:return {'unique_teams':0,'top3_share':0}
    if str(z.outcome.iloc[0])=='DRAW':
        counts=pd.concat([z.home_team,z.away_team]).value_counts()
    else:
        selected=np.where(z.outcome.eq('HOME'),z.home_team,z.away_team)
        counts=pd.Series(selected).value_counts()
    total=max(1,int(counts.sum()))
    return {'unique_teams':int(len(counts)),'top1_share':float(counts.iloc[0]/total) if len(counts) else 0,
            'top3_share':float(counts.iloc[:3].sum()/total) if len(counts) else 0,
            'top5':{str(k):int(v) for k,v in counts.head(5).items()}}

def add_market_context(s):
    p=s.pivot_table(index='match_id',columns='outcome',values='market_prob',aggfunc='first').reset_index()
    for c in ['HOME','DRAW','AWAY']:
        if c not in p:p[c]=np.nan
    ph=pd.to_numeric(p.HOME,errors='coerce');pdw=pd.to_numeric(p.DRAW,errors='coerce');pa=pd.to_numeric(p.AWAY,errors='coerce')
    p['market__home_prob']=ph;p['market__draw_prob']=pdw;p['market__away_prob']=pa
    p['market__home_away_gap_abs']=(ph-pa).abs()
    p['market__favorite_prob']=pd.concat([ph,pa],axis=1).max(axis=1)
    p['market__underdog_prob']=pd.concat([ph,pa],axis=1).min(axis=1)
    p['market__draw_vs_favorite_gap']=p['market__favorite_prob']-pdw
    probs=np.vstack([ph.fillna(0),pdw.fillna(0),pa.fillna(0)]).T
    p['market__entropy']=-np.sum(np.where(probs>0,probs*np.log(probs+1e-12),0),axis=1)
    return s.merge(p.drop(columns=['HOME','DRAW','AWAY']),on='match_id',how='left',validate='m:1')

def add_individual_players(s,d):
    if not IP.exists():raise RuntimeError(f'Missing individual player features: {IP}')
    p=pd.read_parquet(IP).copy()
    p['asa_game_id']=p.asa_game_id.astype(str)
    m=d[['match_id','asa_game_id']].drop_duplicates('match_id').copy()
    m['match_id']=m.match_id.astype(str);m['asa_game_id']=m.asa_game_id.astype(str)
    p=m.merge(p,on='asa_game_id',how='left',validate='m:1')

    paired={}
    for c in p.columns:
        if c.startswith('home_indplayer_'):
            rest=c[len('home_indplayer_'):];ac='away_indplayer_'+rest
            if ac in p.columns:paired[rest]=(c,ac)
    base=p[['match_id']].copy()
    for rest,(hc,ac) in paired.items():
        h=pd.to_numeric(p[hc],errors='coerce');a=pd.to_numeric(p[ac],errors='coerce')
        base[f'ip__home__{rest}']=h;base[f'ip__away__{rest}']=a
    s=s.merge(base,on='match_id',how='left',validate='m:1')
    added={}
    for rest in paired:
        h=pd.to_numeric(s[f'ip__home__{rest}'],errors='coerce');a=pd.to_numeric(s[f'ip__away__{rest}'],errors='coerce')
        home=s.outcome.eq('HOME');away=s.outcome.eq('AWAY');draw=s.outcome.eq('DRAW')
        added[f'ip__sel__{rest}']=np.where(home,h,np.where(away,a,np.nan))
        added[f'ip__opp__{rest}']=np.where(home,a,np.where(away,h,np.nan))
        added[f'ip__edge__{rest}']=np.where(home,h-a,np.where(away,a-h,np.nan))
        added[f'ip__balance__{rest}']=np.where(draw,np.abs(h-a),np.nan)
        added[f'ip__combined__{rest}']=np.where(draw,h+a,np.nan)
    s=pd.concat([s,pd.DataFrame(added,index=s.index)],axis=1)
    s=s.drop(columns=[c for c in s.columns if c.startswith(('ip__home__','ip__away__'))],errors='ignore')
    return s,len(added)

def eligible_features(so,outcome):
    tr=so[period(so,'train')];va=so[period(so,'validation')]
    out=[]
    allowed={'base','player_manager','context','weather','salary','style','cross_comp',
             'goalkeeper_roster','venue','referee','individual_player','market'}
    for c in so.columns:
        fam=family_of(c)
        if fam not in allowed:continue
        if outcome=='DRAW' and c.startswith('ip__') and not c.startswith(('ip__balance__','ip__combined__')):continue
        if outcome!='DRAW' and c.startswith('ip__') and c.startswith(('ip__balance__','ip__combined__')):continue
        # Never long-history mine known recent-only new families here.
        if fam in {'availability','confirmed_lineup','transactions'}:continue
        tv=pd.to_numeric(tr[c],errors='coerce');vv=pd.to_numeric(va[c],errors='coerce')
        if tv.notna().sum()<320 or vv.notna().sum()<220 or tv.nunique(dropna=True)<2:continue
        out.append(c)
    return out

def baseline(so,pb,era):
    return metrics(so[pmask(so,pb)&period(so,era)])

def preeval(so,m,pb):
    out={}
    for era in ['train','validation']:
        z=metrics(so[m&period(so,era)]);b=baseline(so,pb,era)
        if not z or not b:return None
        out[era]=z;out[era+'_baseline']=b;out[era+'_lift']=z['roi']-b['roi']
    pre=period(so,'train')|period(so,'validation')
    z=metrics(so[m&pre]);b=metrics(so[pmask(so,pb)&pre])
    if not z or not b:return None
    out['preholdout']=z;out['preholdout_baseline']=b;out['preholdout_lift']=z['roi']-b['roi']
    return out

def single_gate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return tr['n']>=45 and va['n']>=32 and pre['n']>=120 and tr['roi']>=.02 and va['roi']>=.012 and pre['roi']>=.025 and e['train_lift']>=.012 and e['validation_lift']>=.004 and e['preholdout_lift']>=.015 and pre['positive_season_ratio']>=.60

def pair_gate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return tr['n']>=32 and va['n']>=25 and pre['n']>=90 and tr['roi']>=.04 and va['roi']>=.025 and pre['roi']>=.045 and e['train_lift']>=.025 and e['validation_lift']>=.012 and e['preholdout_lift']>=.03 and pre['positive_season_ratio']>=.62

def triple_gate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return tr['n']>=27 and va['n']>=21 and pre['n']>=75 and tr['roi']>=.055 and va['roi']>=.04 and pre['roi']>=.06 and e['train_lift']>=.04 and e['validation_lift']>=.025 and e['preholdout_lift']>=.045 and pre['positive_season_ratio']>=.65

def score(e):
    return min(e['train']['roi'],e['validation']['roi'])*math.sqrt(max(1,min(e['train']['n'],e['validation']['n'])))

def open_candidate(so,r,meta,kind):
    m=pmask(so,r['price_band'])
    rules=[r] if 'feature' in r else [r[k] for k in ['rule1','rule2','rule3'] if k in r]
    for q in rules:m &= cond(so,q['feature'],q['op'],q['threshold'])
    x=so[m].copy();hold=metrics(x[period(x,'holdout')]);full=metrics(x);hb=baseline(so,r['price_band'],'holdout')
    lift=hold['roi']-hb['roi'] if hold and hb else None
    ci=boot(x.profit,seed={'single':20261101,'pair':20261102,'triple':20261103}[kind]) if len(x) else [None,None]
    conc=team_concentration(x,meta)
    stable=None;neighbors=None
    if kind=='single':
        trv=pd.to_numeric(so.loc[period(so,'train'),r['feature']],errors='coerce').dropna()
        sd=float(trv.std()) if len(trv)>1 else 0.0
        step=max(abs(float(r['threshold']))*.05,sd*.05,.005)
        neighbors=[];stable=0
        for t in [r['threshold']-2*step,r['threshold']-step,r['threshold'],r['threshold']+step,r['threshold']+2*step]:
            mm=pmask(so,r['price_band'])&cond(so,r['feature'],r['op'],t)
            eras={e:metrics(so[mm&period(so,e)]) for e in ['train','validation','holdout']}
            ok=all(eras[e] and eras[e]['roi']>0 for e in eras)
            stable+=int(ok);neighbors.append({'threshold':float(t),**eras})
    status='RESEARCH_ONLY'
    if kind=='single':
        strong=hold and full and hold['n']>=50 and hold['roi']>=.05 and lift is not None and lift>=.01 and full['n']>=180 and full['roi']>=.06 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and stable>=4 and conc['top3_share']<.30
        watch=hold and full and hold['n']>=32 and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.65 and stable>=3 and conc['top3_share']<.35
    elif kind=='pair':
        strong=hold and full and hold['n']>=40 and hold['roi']>=.06 and lift is not None and lift>=.015 and full['n']>=140 and full['roi']>=.08 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and conc['top3_share']<.30
        watch=hold and full and hold['n']>=28 and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.65 and conc['top3_share']<.35
    else:
        strong=hold and full and hold['n']>=32 and hold['roi']>=.08 and lift is not None and lift>=.02 and full['n']>=110 and full['roi']>=.10 and full['positive_season_ratio']>=.70 and ci[0] is not None and ci[0]>0 and conc['top3_share']<.30
        watch=hold and full and hold['n']>=24 and hold['roi']>0 and lift is not None and lift>=0 and full['positive_season_ratio']>=.65 and conc['top3_share']<.35
    if strong:status='OMNI_STRONG_SHADOW'
    elif watch:status='OMNI_WATCH'
    return {**r,'kind':kind,'holdout':hold,'holdout_baseline':hb,'holdout_lift':lift,'full':full,
            'bootstrap95_roi':ci,'team_concentration':conc,'yearly':yearly(x),
            'threshold_neighbors_positive_all_eras':stable,'threshold_neighbors':neighbors,'status':status}

def candidate_set(so,r):
    m=pmask(so,r['price_band'])
    rules=[r] if 'feature' in r else [r[k] for k in ['rule1','rule2','rule3'] if k in r]
    for q in rules:m &= cond(so,q['feature'],q['op'],q['threshold'])
    z=so[m]
    return set(zip(z.match_id.astype(str),z.outcome.astype(str)))

def active_overlap(d,s,cands):
    old=arx.merged_selection_rows(d)
    aset={}
    for m in active.METHODS:
        z=active.method_rows(old,m);aset[m['id']]=set(zip(z.match_id.astype(str),z.outcome.astype(str)))
    out=[]
    for i,c in enumerate(cands):
        so=s[s.outcome.eq(c['outcome'])];cs=candidate_set(so,c)
        ovs=[]
        for mid,a in aset.items():
            n=len(cs&a);ovs.append({'method_id':mid,'intersection':n,'pct_candidate':n/max(1,len(cs))})
        out.append({'candidate_index':i,'candidate_n':len(cs),'active_overlap':sorted(ovs,key=lambda x:x['pct_candidate'],reverse=True)})
    return out

def main():
    if not MASTER.exists():raise RuntimeError('Missing MLS master')
    d=pd.read_parquet(MASTER).copy();d['match_id']=d.match_id.astype(str)
    s,fmap=md.build_selection_matrix(d)
    s['match_id']=s.match_id.astype(str)
    s,ip_added=add_individual_players(s,d)
    s=add_market_context(s)
    meta=d[['match_id','home_team','away_team']].drop_duplicates('match_id').copy();meta.match_id=meta.match_id.astype(str)

    singles=[];tested=0
    for outcome in ['HOME','AWAY']:
        so=s[s.outcome.eq(outcome)].copy();tr=so[period(so,'train')]
        feats=eligible_features(so,outcome)
        for f in feats:
            fam=family_of(f)
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    cm=cond(so,f,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested+=1;e=preeval(so,cm&pmask(so,pb),pb)
                        if e and single_gate(e):
                            singles.append({'outcome':outcome,'price_band':pb,'feature':f,'family':fam,'op':op,'threshold':float(t),'pre_score':score(e),'pre':e})
    singles.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in singles:
        k=(r['outcome'],r['price_band'],r['feature'])
        if k in seen:continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=360:break
    opened_s=[open_candidate(s[s.outcome.eq(r['outcome'])].copy(),r,meta,'single') for r in frozen]

    groups=defaultdict(list)
    for r in frozen:groups[(r['outcome'],r['price_band'])].append(r)
    pairs=[];tested_pairs=0
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:42],2):
            if a['family']==b['family']:continue
            tested_pairs+=1
            m=pmask(so,pb)&cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])
            e=preeval(so,m,pb)
            if e and pair_gate(e):
                pairs.append({'outcome':outcome,'price_band':pb,
                  'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                  'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                  'pre_score':score(e),'pre':e})
    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    fp=[];seen=set()
    for r in pairs:
        k=(r['outcome'],r['price_band'],tuple(sorted([r['rule1']['feature'],r['rule2']['feature']])))
        if k in seen:continue
        seen.add(k);fp.append(r)
        if len(fp)>=180:break
    opened_p=[open_candidate(s[s.outcome.eq(r['outcome'])].copy(),r,meta,'pair') for r in fp]

    # Triple confluence: only preholdout-frozen single rules, three distinct families.
    triples=[];tested_triples=0
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b,c in itertools.combinations(rules[:18],3):
            if len({a['family'],b['family'],c['family']})<3:continue
            tested_triples+=1
            m=pmask(so,pb)
            for q in [a,b,c]:m &= cond(so,q['feature'],q['op'],q['threshold'])
            e=preeval(so,m,pb)
            if e and triple_gate(e):
                triples.append({'outcome':outcome,'price_band':pb,
                  'rule1':{k:a[k] for k in ['feature','family','op','threshold']},
                  'rule2':{k:b[k] for k in ['feature','family','op','threshold']},
                  'rule3':{k:c[k] for k in ['feature','family','op','threshold']},
                  'pre_score':score(e),'pre':e})
    triples.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    ft=triples[:120]
    opened_t=[open_candidate(s[s.outcome.eq(r['outcome'])].copy(),r,meta,'triple') for r in ft]

    all_open=opened_s+opened_p+opened_t
    strong=[x for x in all_open if x['status']=='OMNI_STRONG_SHADOW']
    watch=[x for x in all_open if x['status']=='OMNI_WATCH']
    # Preserve preholdout ranking; holdout never reorders candidates.
    strong.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    watch.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    overlaps=active_overlap(d,s,strong+watch[:40])

    def compact(x):
        z={k:v for k,v in x.items() if k!='pre'}
        z['train']=x['pre']['train'];z['validation']=x['pre']['validation'];z['preholdout']=x['pre']['preholdout']
        z['preholdout_baseline']=x['pre']['preholdout_baseline'];z['preholdout_lift']=x['pre']['preholdout_lift']
        return z

    payload={'built_at':now(),'status':'SHADOW_RESEARCH_ONLY','master_shape':list(d.shape),'selection_rows':len(s),
      'individual_player_engineered_columns':ip_added,'families':sorted(set(family_of(c) for c in s.columns if family_of(c)!='other')),
      'splits':SPLIT,'tested_single_contexts':tested,'preholdout_single_survivors':len(singles),'frozen_singles':len(frozen),
      'tested_pair_contexts':tested_pairs,'preholdout_pair_survivors':len(pairs),'frozen_pairs':len(fp),
      'tested_triple_contexts':tested_triples,'preholdout_triple_survivors':len(triples),'frozen_triples':len(ft),
      'strong_count':len(strong),'watch_count':len(watch),
      'strong_preholdout_order':[compact(x) for x in strong],
      'watch_preholdout_order':[compact(x) for x in watch],
      'active_method_overlap':overlaps,
      'design':'All long-history thresholds and rankings are frozen on 2013-18 train plus 2019-22 validation before opening untouched 2023-25 holdout. 2026 is excluded. Cross-family pairs/triples require distinct feature families. Individual-player features use prior games only. No automatic promotion.'}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))

    lines=['MLS OMNI METHOD DISCOVERY','='*110,
      f"master={d.shape[0]:,} x {d.shape[1]:,} | selections={len(s):,} | individual-player engineered={ip_added}",
      f"families={', '.join(payload['families'])}",
      'Splits: 2013-18 train | 2019-22 validation | 2023-25 untouched holdout | 2026 excluded','',
      f"tested singles={tested:,} pairs={tested_pairs:,} triples={tested_triples:,}",
      f"preholdout survivors singles={len(singles):,} pairs={len(pairs):,} triples={len(triples):,}",
      f"OMNI strong={len(strong)} watch={len(watch)}",'']
    for i,x in enumerate(strong[:25],1):
        if x['kind']=='single':rules=f"{x['feature']} {x['op']} {x['threshold']:.6g}"
        else:
            rr=[]
            for k in ['rule1','rule2','rule3']:
                if k in x:
                    q=x[k];rr.append(f"{q['feature']} {q['op']} {q['threshold']:.6g}")
            rules=' + '.join(rr)
        lines.append(f"{i}. {x['kind'].upper()} {x['outcome']} {x['price_band']} | {rules}")
        lines.append(f"   train {x['pre']['train']['roi']:+.1%} n={x['pre']['train']['n']} | val {x['pre']['validation']['roi']:+.1%} n={x['pre']['validation']['n']} | hold {x['holdout']['roi']:+.1%} n={x['holdout']['n']} | full {x['full']['roi']:+.1%} n={x['full']['n']} | CI={x['bootstrap95_roi']}")
    lines+=['','SHADOW RESEARCH ONLY — NO AUTOMATIC PROMOTION.']
    (OUT/'latest.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':main()

# omni queue refresh 2026-10-02
