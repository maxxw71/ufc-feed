#!/usr/bin/env python3
from __future__ import annotations

import itertools,json,math
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data/processed/mls_match_features_context_enriched.parquet'
OUT=ROOT/'research/context';OUT.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2013,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
 ('ALL',0,1),('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),
 ('P35_45',.35,.45),('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70)
]
SIDE_METRICS=[
 'roster_jaccard_recent5_prev5','roster_new_players3','roster_departed_players3',
 'roster_new_minutes_share3','roster_departed_minutes_share_prev5','roster_churn_index',
 'gk_save_pct5','gk_goals_minus_xg_p96_5','gk_xg_faced_p96_5','gk_goals_conceded_p96_5',
 'gk_current_keeper_prior_team_games','gk_same_keeper_last3',
]
REF_METRICS=[
 'referee_prior_games','referee_prior_home_win_rate','referee_prior_draw_rate',
 'referee_prior_total_goals','referee_prior_home_gd','referee_prior_over25_rate',
]

def now():return datetime.now(timezone.utc).isoformat()
def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum']);a=by[by['count']>=5]
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
            'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
            'active_seasons':int(len(a)),'positive_seasons':int((a['sum']>0).sum()),
            'positive_season_ratio':float((a['sum']>0).mean()) if len(a) else 0.0}
def period(df,k):lo,hi=SPLIT[k];return df.season.between(lo,hi)
def pmask(df,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return df.market_prob.between(lo,hi,inclusive='both')
def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return v.ge(t) if op=='>=' else v.le(t)
def thresholds(v):
    v=pd.to_numeric(v,errors='coerce').dropna()
    if v.nunique()<2:return []
    if v.nunique()<=6:return sorted(float(x) for x in v.unique())
    return sorted(set(float(x) for x in v.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))

def selection_rows(d):
    req={'odds_matched','asa_game_available','asa_knockout_game','referee_prior_games'}
    miss=req-set(d.columns)
    if miss:raise RuntimeError('Context warehouse missing: '+','.join(sorted(miss)))
    base=d[d.odds_matched.eq(True)&d.season.between(2013,2025)&
           d.asa_game_available.eq(True)&d.asa_knockout_game.eq(False)].copy()
    rows=[]
    for outcome in ['HOME','DRAW','AWAY']:
        z=pd.DataFrame(index=base.index)
        z['match_id']=base.match_id;z['date']=pd.to_datetime(base.date,errors='coerce')
        z['season']=base.season;z['outcome']=outcome
        if outcome=='HOME':
            z['win']=base.home_win;z['odds']=base.home_odds;z['market_prob']=base.home_novig_prob
        elif outcome=='AWAY':
            z['win']=base.away_win;z['odds']=base.away_odds;z['market_prob']=base.away_novig_prob
        else:
            z['win']=base.draw;z['odds']=base.draw_odds;z['market_prob']=base.draw_novig_prob

        for m in SIDE_METRICS:
            h='home_'+m;a='away_'+m
            if h not in base or a not in base:continue
            hv=pd.to_numeric(base[h],errors='coerce');av=pd.to_numeric(base[a],errors='coerce')
            if outcome=='HOME':
                z['sel_'+m]=hv;z['opp_'+m]=av;z['edge_'+m]=hv-av
            elif outcome=='AWAY':
                z['sel_'+m]=av;z['opp_'+m]=hv;z['edge_'+m]=av-hv
            else:
                z['balance_'+m]=(hv-av).abs();z['combined_'+m]=hv+av
        for m in REF_METRICS:
            if m in base:z[m]=pd.to_numeric(base[m],errors='coerce')
        z['profit']=np.where(z.win.eq(1),z.odds-1,-1)
        rows.append(z.reset_index(drop=True))
    return pd.concat(rows,ignore_index=True)

def eligible(df,outcome):
    exc={'match_id','date','season','outcome','win','odds','market_prob','profit'}
    out=[]
    for c in df.columns:
        if c in exc:continue
        if outcome=='DRAW':
            if not (c.startswith(('balance_','combined_')) or c.startswith('referee_')):continue
        else:
            if c.startswith(('balance_','combined_')):continue
        v=pd.to_numeric(df[c],errors='coerce')
        if v.notna().sum()<300 or v.nunique(dropna=True)<2:continue
        out.append(c)
    return out

def preeval(df,m):
    tr=metrics(df[m&period(df,'train')]);va=metrics(df[m&period(df,'validation')])
    pre=metrics(df[m&(period(df,'train')|period(df,'validation'))])
    return {'train':tr,'validation':va,'preholdout':pre} if tr and va and pre else None
def singlegate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return tr['n']>=30 and va['n']>=22 and pre['n']>=75 and tr['roi']>=.025 and va['roi']>=.015 and pre['roi']>=.025 and pre['positive_season_ratio']>=.55
def pairgate(e):
    tr,va,pre=e['train'],e['validation'],e['preholdout']
    return tr['n']>=25 and va['n']>=18 and pre['n']>=60 and tr['roi']>=.04 and va['roi']>=.03 and pre['roi']>=.045 and pre['positive_season_ratio']>=.60
def score(e):
    a,b=e['train'],e['validation'];return min(a['roi'],b['roi'])*math.sqrt(max(1,min(a['n'],b['n'])))

def main():
    if not DATA.exists():raise RuntimeError('MLS context-enriched warehouse missing')
    d=pd.read_parquet(DATA);s=selection_rows(d)
    singles=[];tested=0
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy();tr=so[period(so,'train')]
        for f in eligible(so,outcome):
            for t in thresholds(tr[f]):
                for op in ['>=','<=']:
                    cm=cond(so,f,op,t)
                    for pb,_,__ in PRICE_BANDS:
                        tested+=1;m=cm&pmask(so,pb);e=preeval(so,m)
                        if e and singlegate(e):
                            singles.append({'outcome':outcome,'price_band':pb,'feature':f,'op':op,'threshold':t,'pre_score':score(e),'pre':e})
    singles.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    frozen=[];seen=set()
    for r in singles:
        k=(r['outcome'],r['price_band'],r['feature'])
        if k in seen:continue
        seen.add(k);frozen.append(r)
        if len(frozen)>=100:break

    pairs=[];tested_pairs=0;groups={}
    for r in frozen:groups.setdefault((r['outcome'],r['price_band']),[]).append(r)
    for (outcome,pb),rules in groups.items():
        so=s[s.outcome.eq(outcome)].copy()
        for a,b in itertools.combinations(rules[:30],2):
            if a['feature']==b['feature']:continue
            tested_pairs+=1
            m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,pb)
            e=preeval(so,m)
            if e and pairgate(e):
                pairs.append({'outcome':outcome,'price_band':pb,'rule1':{k:a[k] for k in ['feature','op','threshold']},
                              'rule2':{k:b[k] for k in ['feature','op','threshold']},'pre_score':score(e),'pre':e})
    pairs.sort(key=lambda x:(x['pre_score'],x['pre']['preholdout']['n']),reverse=True)
    fp=[];seen=set()
    for r in pairs:
        k=(r['outcome'],r['price_band'],tuple(sorted([r['rule1']['feature'],r['rule2']['feature']])))
        if k in seen:continue
        seen.add(k);fp.append(r)
        if len(fp)>=70:break

    def opensingle(r):
        so=s[s.outcome.eq(r['outcome'])];m=cond(so,r['feature'],r['op'],r['threshold'])&pmask(so,r['price_band'])
        h=metrics(so[m&period(so,'holdout')]);f=metrics(so[m])
        return {**r,'holdout':h,'full':f,'holdout_survived':bool(h and h['n']>=20 and h['roi']>0 and h['units']>0)}
    def openpair(r):
        so=s[s.outcome.eq(r['outcome'])];a,b=r['rule1'],r['rule2']
        m=cond(so,a['feature'],a['op'],a['threshold'])&cond(so,b['feature'],b['op'],b['threshold'])&pmask(so,r['price_band'])
        h=metrics(so[m&period(so,'holdout')]);f=metrics(so[m])
        return {**r,'holdout':h,'full':f,'holdout_survived':bool(h and h['n']>=18 and h['roi']>0 and h['units']>0)}
    os=[opensingle(r) for r in frozen];op=[openpair(r) for r in fp]
    def compact(r):
        z={k:v for k,v in r.items() if k!='pre'}
        z['train']=r['pre']['train'];z['validation']=r['pre']['validation'];z['preholdout']=r['pre']['preholdout'];return z
    sj=[compact(r) for r in os];pj=[compact(r) for r in op]
    payload={'built_at':now(),'dataset':str(DATA),'selection_rows':len(s),'features_tested':sorted(set(r['feature'] for r in frozen)),
             'tested_single_contexts':tested,'preholdout_single_survivors':len(singles),'frozen_single_candidates':len(frozen),
             'single_holdout_survivors':sum(r['holdout_survived'] for r in os),
             'tested_pair_contexts':tested_pairs,'preholdout_pair_survivors':len(pairs),'frozen_pair_candidates':len(fp),
             'pair_holdout_survivors':sum(r['holdout_survived'] for r in op),'splits':SPLIT,
             'selection_rule':'Thresholds and rankings frozen on 2013-18 train + 2019-22 validation before opening 2023-25 holdout.',
             'status':'SHADOW_RESEARCH_ONLY','top_singles_preholdout_order':sj[:30],'top_pairs_preholdout_order':pj[:30],
             'all_holdout_surviving_singles_preholdout_order':[x for x in sj if x['holdout_survived']],
             'all_holdout_surviving_pairs_preholdout_order':[x for x in pj if x['holdout_survived']]}
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps({k:payload[k] for k in ['selection_rows','tested_single_contexts','frozen_single_candidates','single_holdout_survivors','tested_pair_contexts','frozen_pair_candidates','pair_holdout_survivors']},indent=2))
    for r in payload['all_holdout_surviving_singles_preholdout_order'][:20]:
        print('SURVIVOR',r['outcome'],r['price_band'],r['feature'],r['op'],r['threshold'],'hold',r['holdout'],'full',r['full'])

if __name__=='__main__':main()
