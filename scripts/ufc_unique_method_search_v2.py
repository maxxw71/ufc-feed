#!/usr/bin/env python3
from __future__ import annotations
import json, math, re
from itertools import product
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
DATA=ROOT/"feature_expansion"/"prefight_favorite_features_v6.csv"
OUT=ROOT/"unique_method_search_v2"
OUT.mkdir(exist_ok=True)

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def key(dt,a,b):
    try:return (str(pd.Timestamp(dt).date()),)+tuple(sorted((norm(a),norm(b))))
    except:return None
def n(df,c):
    return pd.to_numeric(df[c],errors='coerce') if c in df else pd.Series(np.nan,index=df.index)
def bcol(df,c):
    if c not in df:return pd.Series(False,index=df.index)
    s=df[c]
    if pd.api.types.is_bool_dtype(s):return s.fillna(False)
    return s.astype(str).str.lower().isin(['true','1','yes'])
def sm(df,mask):
    q=df[mask & df.won.notna() & df.fav_decimal.notna() & (df.fav_decimal>1)].copy()
    if q.empty:return {'n':0}
    p=np.where(q.won, q.fav_decimal-1, -1.0)
    return {'n':int(len(q)),'wins':int(q.won.sum()),'losses':int((~q.won).sum()),
            'win_rate':float(q.won.mean()),'roi':float(np.mean(p)),
            'avg_odds':float(q.fav_decimal.mean()),'avg_market':float(q.market_prob.mean())}
def wilson(w,n,z=1.96):
    if n<=0:return (None,None)
    p=w/n;d=1+z*z/n
    cen=(p+z*z/(2*n))/d
    rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (cen-rad,cen+rad)

def existing_union(d):
    universe=set(d._key)
    sets={f'U{i}':set() for i in range(1,13)}
    # Exact/simple live mechanics where available.
    sets['U1']=set(d.loc[(n(d,'market_prob')>=.70)&(n(d,'age_adv')>=3),'_key'])
    for mid,path,cols in [
      ('U2','skill_veto_official_samples/reach_415_with_skill_veto.csv',('event_date','favorite','underdog')),
      ('U3','structural_edge_failure_analysis/structural_edge_83_fights.csv',('event_date','favorite','underdog')),
      ('U4','ufc_age_reach_overlap/age_plus_reach4.csv',('event_date','player1','player2'))]:
        p=ROOT/path
        if p.exists():
            x=pd.read_csv(p,low_memory=False)
            sets[mid]={key(getattr(r,cols[0]),getattr(r,cols[1]),getattr(r,cols[2])) for r in x.itertuples()}
    sets['U5']=set(d.loc[(n(d,'f_fights')>=2)&(n(d,'o_fights')>=2)&(n(d,'f_td_a15')>=5)&(n(d,'f_td_l15')>=1)&(n(d,'f_ctrl15')>=1)&(n(d,'o_td_a15')<=2)&(n(d,'o_ctrl15')<=.75),'_key'])
    sets['U6']=set(d.loc[(n(d,'market_prob')>=.65)&(n(d,'f_fights')>=3)&(n(d,'o_fights')>=3)&((n(d,'f_sig_l_pm')-n(d,'o_sig_l_pm'))>=.25)&((n(d,'f_sig_def')-n(d,'o_sig_def'))>=.15),'_key'])
    power=n(d,'o_kd15')*n(d,'f_kd_abs15')
    sets['U7']=set(d.loc[(n(d,'market_prob')>=.70)&(n(d,'f_fights')>=4)&(n(d,'o_fights')>=4)&((n(d,'f_sig_diff_pm')-n(d,'o_sig_diff_pm'))>=1)&((n(d,'f_td_def')-n(d,'o_td_def'))>=.10)&(n(d,'age_adv')>=-3)&(power<.12),'_key'])
    sets['U8']=set(d.loc[(n(d,'market_prob')>=.75)&(n(d,'f_fights')>=4)&(n(d,'o_fights')>=4)&((n(d,'f_sig_diff_pm')-n(d,'o_sig_diff_pm'))>=1)&(n(d,'f_sig_diff_pm')>=.5),'_key'])
    wg=n(d,'f_win_pct')-n(d,'o_win_pct'); fg=n(d,'f_finish_loss_pct')-n(d,'o_finish_loss_pct')
    veto=(n(d,'f_ctrl_allowed15')>=3)&(n(d,'f_finish_loss_pct')>=.40)
    base=(n(d,'market_prob')>=.575)&(n(d,'market_prob')<=.75)&(n(d,'f_fights')>=8)&(n(d,'o_fights')<=3)&(wg>=.05)&(fg<=.60)
    sets['U9']=set(d.loc[base & ~veto,'_key'])
    sets['U10']=set(d.loc[n(d,'f2_diff_days_since_ko_loss')>=105,'_key'])
    older=(n(d,'age_adv')<=-2)
    common=[older,n(d,'f_td_def')<.60,n(d,'f_ctrl_allowed15')>=3,n(d,'f_sig_diff_pm')<0,n(d,'f_finish_loss_pct')>=.40]
    risk11=sum(x.fillna(False).astype(int) for x in common)
    sets['U11']=set(d.loc[base & (risk11<4),'_key'])
    recent5=n(d,'f_last5')
    risk12=risk11+(recent5<.50).fillna(False).astype(int)
    sets['U12']=set(d.loc[base & (risk12<4),'_key'])
    sets={k:(v&universe) for k,v in sets.items()}
    union=set().union(*sets.values())
    return sets,union

def cond(df,c,op,v):
    x=n(df,c)
    if op=='>=':return x>=v
    if op=='<=':return x<=v
    if op=='==':return x==v
    raise ValueError(op)

def mask_rule(df,conds,market=(.55,.82),min_fights=3):
    m=(n(df,'market_prob')>=market[0])&(n(df,'market_prob')<=market[1])&(n(df,'f_fights')>=min_fights)&(n(df,'o_fights')>=min_fights)
    for c,op,v in conds:
        if c not in df.columns:return pd.Series(False,index=df.index)
        m &= cond(df,c,op,v)
    return m.fillna(False)

def candidate_rules():
    rules=[]
    markets=[(.55,.75),(.575,.775),(.60,.80)]
    # A: late-round/cardio advantage.
    for mk,a,b in product(markets,[.25,.5,1.0],[.25,.5,1.0]):
        rules.append(('LATE_ROUND_RESILIENCE',
            [('f2_diff_r3_sig_diff_pm','>=',a),('f2_diff_cardio_sig_decay','>=',b)],mk))
    for mk,a,b in product(markets,[.5,1.0],[0,.25]):
        rules.append(('LATE_ROUND_RESILIENCE',
            [('f2_diff_r3_sig_diff_pm','>=',a),('f2_f_recent3_sig_diff_pm','>=',b),('f2_f_cardio_sig_decay','>=',-.5)],mk))
    # B: damage avoidance / durability asymmetry.
    for mk,kd,sig in product(markets,[-.10,-.25,-.50],[-.20,-.40,-.60]):
        rules.append(('DAMAGE_AVOIDANCE_EDGE',
            [('f2_diff_recent3_kd_abs15','<=',kd),('f2_diff_recent3_sig_abs_pm','<=',sig)],mk))
    for mk,kd,head in product(markets,[-.10,-.25],[-.15,-.30,-.50]):
        rules.append(('DAMAGE_AVOIDANCE_EDGE',
            [('f2_diff_recent3_kd_abs15','<=',kd),('f2_diff_head_abs_pm_career','<=',head)],mk))
    # C: quality-tested momentum.
    for mk,sos,qd in product(markets,[0,25,50],[.25,.50,1.0]):
        rules.append(('QUALITY_TESTED_MOMENTUM',
            [('f2_diff_sos_opp_elo','>=',sos),('f2_diff_quality_adj_sig_diff','>=',qd),('f2_f_strong_opp_count','>=',2)],mk))
    # D: control trend, not raw wrestling mismatch.
    for mk,tr,rc in product(markets,[.5,1.0,2.0],[0,.5,1.0]):
        rules.append(('CONTROL_MOMENTUM',
            [('f2_diff_ctrl_diff_trend3','>=',tr),('f2_f_recent3_ctrl_diff15','>=',rc),('f2_o_ctrl_diff_trend3','<=',0)],mk))
    # E: opponent deterioration + favorite stable/improving.
    for mk,ot,fr in product(markets,[-.25,-.50,-1.0],[0,.25,.50]):
        rules.append(('OPPONENT_DECLINE_SIGNAL',
            [('f2_o_sig_diff_trend3','<=',ot),('f2_f_sig_diff_trend3','>=',fr),('f2_diff_sos_opp_elo','>=',0)],mk))
    # F: decision reliability / opponent close-decision vulnerability.
    for mk,dw,loss in product(markets,[.10,.20,.30],[1,2]):
        rules.append(('DECISION_RELIABILITY',
            [('f6_f_decision_fights','>=',3),('f6_diff_decision_win_pct','>=',dw),('f6_o_split_loss_count','>=',loss)],mk))
    # G: division stability against a mover.
    for mk,chg in product(markets,[1,2]):
        rules.append(('DIVISION_STABILITY',
            [('f6_f_changed_division','==',0),('f6_o_changed_division','==',1),('f6_o_division_changes','>=',chg)],mk))
        rules.append(('DIVISION_STABILITY',
            [('f6_f_changed_division','==',0),('f6_o_changed_division','==',1),('f6_diff_decision_win_pct','>=',0)],mk))
    # H: weight-discipline asymmetry (coverage is expected to be smaller).
    for mk in markets:
        rules.append(('WEIGHT_DISCIPLINE',
            [('f4_f_misses','==',0),('f4_o_miss_last_730','>=',1)],mk))
        rules.append(('WEIGHT_DISCIPLINE',
            [('f4_f_repeat_miss_flag','==',0),('f4_o_repeat_miss_flag','>=',1)],mk))
    return rules

def main():
    d=pd.read_csv(DATA,low_memory=False)
    d['_date']=pd.to_datetime(d['event_date'],errors='coerce')
    d=d[d._date.notna()].sort_values('_date').reset_index(drop=True)
    d['_key']=[key(r.event_date,r.favorite,r.opponent) for r in d.itertuples()]
    d['won']=d['won'].astype(str).str.lower().map({'true':True,'false':False,'1':True,'0':False,'w':True,'l':False}).where(~pd.api.types.is_bool_dtype(d['won']),d['won'])
    if d['won'].isna().all():
        d['won']=pd.to_numeric(d['won'],errors='coerce')>.5
    else:
        # Recover numeric/bool variants safely.
        raw=pd.read_csv(DATA,usecols=['won'])['won']
        if pd.api.types.is_bool_dtype(raw):d['won']=raw.astype(bool).values
        elif pd.api.types.is_numeric_dtype(raw):d['won']=(pd.to_numeric(raw,errors='coerce')>.5).values
    d['fav_decimal']=n(d,'fav_decimal')
    d['market_prob']=n(d,'market_prob')
    d['profit']=np.where(d.won,d.fav_decimal-1,-1.0)
    methods,existing=existing_union(d)
    years=d._date.dt.year
    prospect=bcol(d,'prospective_point_in_time')

    rows=[]
    for fam,conds,mkt in candidate_rules():
        m=mask_rule(d,conds,mkt)
        full=sm(d,m); old=sm(d,m&(years<=2019)); mid=sm(d,m&(years>=2020)&(years<=2023)); recent=sm(d,m&(years>=2024))
        pro=sm(d,m&prospect)
        keys=set(d.loc[m,'_key']); ov=len(keys&existing)/len(keys) if keys else 1.0
        unique=len(keys-existing)
        if full.get('n',0)<35 or recent.get('n',0)<8 or old.get('n',0)<8 or mid.get('n',0)<8:continue
        if min(old.get('roi',-9),mid.get('roi',-9),recent.get('roi',-9))<=0:continue
        if full.get('roi',-9)<.06 or full.get('win_rate',0)<.74:continue
        if ov>.45 or unique<20:continue
        lo,hi=wilson(full['wins'],full['n'])
        score=(full['roi']*1.2+recent['roi']*1.4+full['win_rate']*.4+(1-ov)*.25+min(full['n'],150)/150*.15)
        rows.append({'family':fam,'conditions':json.dumps(conds),'market_min':mkt[0],'market_max':mkt[1],
                     **{f'full_{k}':v for k,v in full.items()},
                     **{f'pre2020_{k}':v for k,v in old.items()},
                     **{f'y2020_23_{k}':v for k,v in mid.items()},
                     **{f'y2024plus_{k}':v for k,v in recent.items()},
                     **{f'prospective_{k}':v for k,v in pro.items()},
                     'existing_overlap':ov,'unique_fights':unique,'wilson_low':lo,'wilson_high':hi,'score':score})
    out=pd.DataFrame(rows)
    if out.empty:
        print('NO_CANDIDATE_PASSED_GATES');return
    out=out.sort_values(['score','full_n'],ascending=False).drop_duplicates(subset=['family','conditions','market_min','market_max'])
    out.to_csv(OUT/'unique_candidates.csv',index=False)
    print('DATASET_ROWS',len(d),'PROSPECTIVE_ROWS',int(prospect.sum()),'EXISTING_METHOD_UNION',len(existing))
    print('TOP_UNIQUE_CANDIDATES')
    for _,r in out.head(20).iterrows():
        print(json.dumps({k:(None if pd.isna(v) else v) for k,v in r.to_dict().items()},sort_keys=True,default=str))
    # Persist matched fights for the top candidate.
    best=out.iloc[0]
    conds=json.loads(best.conditions)
    m=mask_rule(d,conds,(float(best.market_min),float(best.market_max)))
    cols=[c for c in ['event_date','favorite','opponent','market_prob','fav_decimal','won','profit','prospective_point_in_time','official_methods_json'] if c in d]
    d.loc[m,cols].to_csv(OUT/'top_candidate_fights.csv',index=False)
    print('TOP_FAMILY',best.family)
    print('TOP_RULE',best.conditions,'MARKET',best.market_min,best.market_max)
    print('TOP_RECENT_FIGHTS')
    for _,x in d.loc[m,cols].tail(20).iterrows():
        print(json.dumps({k:(None if pd.isna(v) else v) for k,v in x.to_dict().items()},default=str))
    # Check overlap against each U method individually.
    keys=set(d.loc[m,'_key'])
    print('TOP_OVERLAP_BY_METHOD',json.dumps({u:round(len(keys&s)/len(keys),4) if keys else None for u,s in methods.items()},sort_keys=True))

if __name__=='__main__':main()
