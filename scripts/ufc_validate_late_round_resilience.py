#!/usr/bin/env python3
from __future__ import annotations
import json,re,math
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path.home()/"ufc-predictor-v1"
D=ROOT/"feature_expansion"/"prefight_favorite_features_v6.csv"
def norm(s):return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def key(dt,a,b):
    try:return (str(pd.Timestamp(dt).date()),)+tuple(sorted((norm(a),norm(b))))
    except:return None
def n(d,c):return pd.to_numeric(d[c],errors='coerce') if c in d else pd.Series(np.nan,index=d.index)
def bcol(d,c):
    if c not in d:return pd.Series(False,index=d.index)
    return d[c].astype(str).str.lower().isin(['true','1','yes'])
def metrics(d,m):
    q=d[m].copy()
    if not len(q):return {'n':0}
    p=np.where(q.won,q.fav_decimal-1,-1.0)
    return {'n':len(q),'wins':int(q.won.sum()),'losses':int((~q.won).sum()),'win_rate':float(q.won.mean()),
            'roi':float(np.mean(p)),'avg_odds':float(q.fav_decimal.mean()),'avg_market':float(q.market_prob.mean())}
def existing(d):
    universe=set(d._key); sets={f'U{i}':set() for i in range(1,13)}
    sets['U1']=set(d.loc[(n(d,'market_prob')>=.70)&(n(d,'age_adv')>=3),'_key'])
    for mid,path,cols in [('U2','skill_veto_official_samples/reach_415_with_skill_veto.csv',('event_date','favorite','underdog')),('U3','structural_edge_failure_analysis/structural_edge_83_fights.csv',('event_date','favorite','underdog')),('U4','ufc_age_reach_overlap/age_plus_reach4.csv',('event_date','player1','player2'))]:
        p=ROOT/path
        if p.exists():
            x=pd.read_csv(p,low_memory=False);sets[mid]={key(getattr(r,cols[0]),getattr(r,cols[1]),getattr(r,cols[2])) for r in x.itertuples()}
    sets['U5']=set(d.loc[(n(d,'f_fights')>=2)&(n(d,'o_fights')>=2)&(n(d,'f_td_a15')>=5)&(n(d,'f_td_l15')>=1)&(n(d,'f_ctrl15')>=1)&(n(d,'o_td_a15')<=2)&(n(d,'o_ctrl15')<=.75),'_key'])
    sets['U6']=set(d.loc[(n(d,'market_prob')>=.65)&(n(d,'f_fights')>=3)&(n(d,'o_fights')>=3)&((n(d,'f_sig_l_pm')-n(d,'o_sig_l_pm'))>=.25)&((n(d,'f_sig_def')-n(d,'o_sig_def'))>=.15),'_key'])
    power=n(d,'o_kd15')*n(d,'f_kd_abs15')
    sets['U7']=set(d.loc[(n(d,'market_prob')>=.70)&(n(d,'f_fights')>=4)&(n(d,'o_fights')>=4)&((n(d,'f_sig_diff_pm')-n(d,'o_sig_diff_pm'))>=1)&((n(d,'f_td_def')-n(d,'o_td_def'))>=.10)&(n(d,'age_adv')>=-3)&(power<.12),'_key'])
    sets['U8']=set(d.loc[(n(d,'market_prob')>=.75)&(n(d,'f_fights')>=4)&(n(d,'o_fights')>=4)&((n(d,'f_sig_diff_pm')-n(d,'o_sig_diff_pm'))>=1)&(n(d,'f_sig_diff_pm')>=.5),'_key'])
    wg=n(d,'f_win_pct')-n(d,'o_win_pct');fg=n(d,'f_finish_loss_pct')-n(d,'o_finish_loss_pct');base=(n(d,'market_prob')>=.575)&(n(d,'market_prob')<=.75)&(n(d,'f_fights')>=8)&(n(d,'o_fights')<=3)&(wg>=.05)&(fg<=.60)
    sets['U9']=set(d.loc[base&~((n(d,'f_ctrl_allowed15')>=3)&(n(d,'f_finish_loss_pct')>=.40)),'_key'])
    sets['U10']=set(d.loc[n(d,'f2_diff_days_since_ko_loss')>=105,'_key'])
    risk11=(n(d,'age_adv')<=-2).astype(int)+(n(d,'f_td_def')<.60).astype(int)+(n(d,'f_ctrl_allowed15')>=3).astype(int)+(n(d,'f_sig_diff_pm')<0).astype(int)+(n(d,'f_finish_loss_pct')>=.40).astype(int)
    sets['U11']=set(d.loc[base&(risk11<4),'_key'])
    risk12=risk11+(n(d,'f_last5')<.50).astype(int)
    sets['U12']=set(d.loc[base&(risk12<4),'_key'])
    sets={k:v&universe for k,v in sets.items()}
    # For genuinely prospective rows, trust the method labels frozen before the fight.
    if 'official_methods_json' in d.columns:
        for _,r in d.iterrows():
            try:mids=json.loads(r.get('official_methods_json') or '[]')
            except Exception:mids=[]
            for mid in mids:
                mid=str(mid)
                if mid in sets:sets[mid].add(r['_key'])
    return sets,set().union(*sets.values())
def main():
    d=pd.read_csv(D,low_memory=False);d['_date']=pd.to_datetime(d.event_date,errors='coerce');d=d[d._date.notna()].sort_values('_date').reset_index(drop=True)
    raw=d.won.copy()
    if pd.api.types.is_bool_dtype(raw):d['won']=raw.fillna(False).astype(bool)
    elif pd.api.types.is_numeric_dtype(raw):d['won']=pd.to_numeric(raw,errors='coerce')>.5
    else:d['won']=raw.astype(str).str.lower().map({'true':True,'false':False,'1':True,'0':False,'w':True,'l':False,'win':True,'loss':False}).fillna(False).astype(bool)
    d['fav_decimal']=n(d,'fav_decimal');d['market_prob']=n(d,'market_prob');d['_key']=[key(r.event_date,r.favorite,r.opponent) for r in d.itertuples()]
    sets,union=existing(d);pros=bcol(d,'prospective_point_in_time')
    def rule(r3=1.0,cardio=.5,lo=.60,hi=.80):
        return (n(d,'market_prob')>=lo)&(n(d,'market_prob')<=hi)&(n(d,'f_fights')>=3)&(n(d,'o_fights')>=3)&(n(d,'f2_diff_r3_sig_diff_pm')>=r3)&(n(d,'f2_diff_cardio_sig_decay')>=cardio)
    top=rule(); unique=top & ~d._key.isin(union)
    print('FROZEN_TOP',json.dumps(metrics(d,top),sort_keys=True))
    print('UNIQUE_ONLY_NO_U1_U12',json.dumps(metrics(d,unique),sort_keys=True))
    print('OVERLAP_ANY_U',json.dumps(metrics(d,top&d._key.isin(union)),sort_keys=True))
    print('PROSPECTIVE_ALL',json.dumps(metrics(d,top&pros),sort_keys=True))
    print('PROSPECTIVE_UNIQUE_ONLY',json.dumps(metrics(d,unique&pros),sort_keys=True))
    for label,m in [('2010_2014',top&(d._date.dt.year<=2014)),('2015_2019',top&(d._date.dt.year.between(2015,2019))),('2020_2023',top&(d._date.dt.year.between(2020,2023))),('2024plus',top&(d._date.dt.year>=2024)),('2025plus',top&(d._date.dt.year>=2025))]:
        print('ERA',label,json.dumps(metrics(d,m),sort_keys=True))
    for lo,hi in [(.60,.65),(.65,.70),(.70,.75),(.75,.800001)]:
        print('MARKET_BAND',f'{lo:.2f}-{hi:.2f}',json.dumps(metrics(d,top&(n(d,'market_prob')>=lo)&(n(d,'market_prob')<hi)),sort_keys=True))
    print('THRESHOLD_STABILITY')
    for r3 in [.5,.75,1.0,1.25,1.5]:
        for ca in [.25,.5,.75,1.0]:
            m=rule(r3,ca)
            z=metrics(d,m)
            if z['n']>=100:print(json.dumps({'r3':r3,'cardio':ca,**z},sort_keys=True))
    print('YEARLY')
    for y in sorted(d.loc[top,'_date'].dt.year.unique()):
        z=metrics(d,top&(d._date.dt.year==y))
        if z['n']>=5:print(y,json.dumps(z,sort_keys=True))
    print('UNIQUE_ONLY_RECENT_FIGHTS')
    cols=[c for c in ['event_date','favorite','opponent','market_prob','fav_decimal','won','prospective_point_in_time','official_methods_json'] if c in d]
    for _,r in d.loc[unique,cols].tail(30).iterrows():print(json.dumps({k:(None if pd.isna(v) else v) for k,v in r.to_dict().items()},default=str))
    print('OVERLAP_BY_U',json.dumps({u:round(len(set(d.loc[top,'_key'])&s)/int(top.sum()),4) for u,s in sets.items()},sort_keys=True))
if __name__=='__main__':main()
