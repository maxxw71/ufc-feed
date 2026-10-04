#!/usr/bin/env python3
from __future__ import annotations
import json, math, re
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
DATA=ROOT/"feature_expansion"/"prefight_favorite_features_v6.csv"
OUT=ROOT/"unique_method_search_v2"/"lrr_loss_forensics"
OUT.mkdir(parents=True,exist_ok=True)

def num(d,c):
    return pd.to_numeric(d[c],errors='coerce') if c in d else pd.Series(np.nan,index=d.index)

def stats(d,m):
    q=d[m & d['valid']].copy()
    if q.empty:return {'n':0,'wins':0,'losses':0,'win_rate':None,'roi':None}
    p=np.where(q.won,q.fav_decimal-1,-1.0)
    return {'n':int(len(q)),'wins':int(q.won.sum()),'losses':int((~q.won).sum()),
            'win_rate':float(q.won.mean()),'roi':float(p.mean()),
            'avg_market':float(q.market_prob.mean()),'avg_odds':float(q.fav_decimal.mean())}

def norm_won(s):
    if pd.api.types.is_bool_dtype(s):return s.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(s):return pd.to_numeric(s,errors='coerce').fillna(0)>.5
    return s.astype(str).str.strip().str.lower().map({'true':True,'false':False,'1':True,'0':False,'w':True,'l':False,'win':True,'loss':False}).fillna(False).astype(bool)

def qmed(s,m):
    x=pd.to_numeric(s[m],errors='coerce').dropna()
    return None if x.empty else float(x.median())

def mean(s,m):
    x=pd.to_numeric(s[m],errors='coerce').dropna()
    return None if x.empty else float(x.mean())

def norm_name(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def rebuilt_age_adv(d):
    """Opponent age minus favorite age; positive means favorite is younger."""
    p=ROOT/'raw'/'individuals.csv'
    if not p.exists():return pd.Series(np.nan,index=d.index),0
    ids=pd.read_csv(p,low_memory=False)
    dob={}
    for _,r in ids.iterrows():
        name=norm_name(r.get('name'))
        dt=pd.to_datetime(r.get('dob'),errors='coerce')
        if name and pd.notna(dt):dob[name]=dt
    vals=[];covered=0
    for _,r in d.iterrows():
        ed=pd.to_datetime(r.get('event_date'),errors='coerce')
        fd=dob.get(norm_name(r.get('favorite')));od=dob.get(norm_name(r.get('opponent')))
        if pd.notna(ed) and fd is not None and od is not None:
            vals.append(((ed-od).days-(ed-fd).days)/365.2425)
            covered+=1
        else:vals.append(np.nan)
    return pd.Series(vals,index=d.index,dtype=float),covered

def main():
    d=pd.read_csv(DATA,low_memory=False)
    d['_date']=pd.to_datetime(d.event_date,errors='coerce')
    d['won']=norm_won(d['won'])
    d['fav_decimal']=num(d,'fav_decimal');d['market_prob']=num(d,'market_prob')
    d['valid']=d._date.notna()&d.fav_decimal.notna()&(d.fav_decimal>1)
    lrr=(d.market_prob>=.60)&(d.market_prob<=.80)&(num(d,'f_fights')>=3)&(num(d,'o_fights')>=3)&(num(d,'f2_diff_r3_sig_diff_pm')>=1.0)&(num(d,'f2_diff_cardio_sig_decay')>=.5)&d.valid
    base=stats(d,lrr)
    print('BASELINE',json.dumps(base,sort_keys=True))

    # AGE ROLE: rebuild from fighter DOBs because legacy age_adv coverage is sparse.
    # Positive = favorite younger by that many years.
    age,age_covered=rebuilt_age_adv(d)
    print('AGE_REBUILD_COVERAGE',json.dumps({'dataset_rows':len(d),'dob_matched_rows':age_covered,
          'lrr_rows':int(lrr.sum()),'lrr_age_covered':int((lrr&age.notna()).sum())},sort_keys=True))
    buckets=[
      ('fav_5plus_older',-99,-5),('fav_3to5_older',-5,-3),('fav_1to3_older',-3,-1),
      ('roughly_same_age',-1,1),('fav_1to3_younger',1,3),('fav_3to5_younger',3,5),('fav_5plus_younger',5,99)]
    print('AGE_BUCKETS')
    age_rows=[]
    for label,lo,hi in buckets:
        m=lrr&(age>=lo)&(age<hi)
        z=stats(d,m);z.update({'bucket':label,'age_min':lo,'age_max':hi})
        age_rows.append(z);print(json.dumps(z,sort_keys=True))
    pd.DataFrame(age_rows).to_csv(OUT/'age_buckets.csv',index=False)

    print('AGE_THRESHOLD_RETENTION')
    for t in [-5,-4,-3,-2,-1,0,1,2,3,4,5]:
        keep=lrr&(age>=t)
        removed=lrr&~(age>=t)
        z=stats(d,keep);rz=stats(d,removed)
        print(json.dumps({'keep_age_adv_gte':t,'retained':z,'removed':rz},sort_keys=True))

    # Feature forensics: compare medians/means for winners vs losses inside frozen LRR.
    derived={
      'age_adv':age,
      'market_prob':d.market_prob,
      'fav_fights':num(d,'f_fights'),
      'opp_fights':num(d,'o_fights'),
      'r3_edge':num(d,'f2_diff_r3_sig_diff_pm'),
      'cardio_decay_edge':num(d,'f2_diff_cardio_sig_decay'),
      'elo_edge':num(d,'f2_diff_elo'),
      'sos_elo_edge':num(d,'f2_diff_sos_opp_elo'),
      'quality_sig_edge':num(d,'f2_diff_quality_adj_sig_diff'),
      'strong_opp_sig_edge':num(d,'f2_diff_strong_opp_sig_diff'),
      'recent3_sig_edge':num(d,'f2_diff_recent3_sig_diff_pm'),
      'recent3_ctrl_edge':num(d,'f2_diff_recent3_ctrl_diff15'),
      'recent3_kdabs_edge':num(d,'f2_diff_recent3_kd_abs15'),
      'recent3_sigabs_edge':num(d,'f2_diff_recent3_sig_abs_pm'),
      'sig_trend_edge':num(d,'f2_diff_sig_diff_trend3'),
      'ctrl_trend_edge':num(d,'f2_diff_ctrl_diff_trend3'),
      'fav_recent3_kdabs':num(d,'f2_f_recent3_kd_abs15'),
      'fav_recent3_sigabs':num(d,'f2_f_recent3_sig_abs_pm'),
      'fav_ctrl_allowed15':num(d,'f_ctrl_allowed15'),
      'opp_ctrl15':num(d,'o_ctrl15'),
      'opp_td_a15':num(d,'o_td_a15'),
      'td_def_edge':num(d,'f_td_def')-num(d,'o_td_def'),
      'fav_finish_loss_pct':num(d,'f_finish_loss_pct'),
      'opp_power':num(d,'o_kd15'),
      'power_risk':num(d,'o_kd15')*num(d,'f_kd_abs15'),
      'fav_current_win_streak':num(d,'f5_f_current_win_streak'),
      'opp_current_win_streak':num(d,'f5_o_current_win_streak'),
      'decision_win_edge':num(d,'f6_diff_decision_win_pct'),
      'opp_split_losses':num(d,'f6_o_split_loss_count'),
    }
    wins=lrr&d.won;losses=lrr&~d.won
    comps=[]
    for name,s in derived.items():
        wm=qmed(s,wins);lm=qmed(s,losses);wa=mean(s,wins);la=mean(s,losses)
        if wm is None or lm is None:continue
        comps.append({'feature':name,'win_median':wm,'loss_median':lm,'median_gap_loss_minus_win':lm-wm,
                      'win_mean':wa,'loss_mean':la,'mean_gap_loss_minus_win':None if wa is None or la is None else la-wa,
                      'win_nonnull':int(pd.to_numeric(s[wins],errors='coerce').notna().sum()),
                      'loss_nonnull':int(pd.to_numeric(s[losses],errors='coerce').notna().sum())})
    pd.DataFrame(comps).to_csv(OUT/'win_loss_feature_comparison.csv',index=False)
    print('WIN_LOSS_FEATURE_COMPARISON')
    for x in comps:print(json.dumps(x,sort_keys=True))

    # Predefined interpretable vetoes. Veto=True means skip that LRR pick.
    vetoes={
      'AGE_favorite_3plus_older': age<=-3,
      'AGE_favorite_4plus_older': age<=-4,
      'AGE_favorite_5plus_older': age<=-5,
      'WEAK_MARKET_below_65': d.market_prob<.65,
      'WEAK_MARKET_below_70': d.market_prob<.70,
      'NEG_RECENT_STRIKING_fav_recent3_below0': num(d,'f2_f_recent3_sig_diff_pm')<0,
      'RECENT_SIG_EDGE_negative': num(d,'f2_diff_recent3_sig_diff_pm')<0,
      'SOS_DISADVANTAGE_50elo': num(d,'f2_diff_sos_opp_elo')<=-50,
      'SOS_DISADVANTAGE_100elo': num(d,'f2_diff_sos_opp_elo')<=-100,
      'HIGH_RECENT_DAMAGE_kdabs15_050': num(d,'f2_f_recent3_kd_abs15')>=.50,
      'HIGH_RECENT_DAMAGE_kdabs15_075': num(d,'f2_f_recent3_kd_abs15')>=.75,
      'HIGH_RECENT_SIGABS_4': num(d,'f2_f_recent3_sig_abs_pm')>=4.0,
      'HIGH_RECENT_SIGABS_5': num(d,'f2_f_recent3_sig_abs_pm')>=5.0,
      'WRESTLING_RISK_opp_td_attempts5': num(d,'o_td_a15')>=5,
      'CONTROL_RISK_opp_ctrl15_3': num(d,'o_ctrl15')>=3,
      'CONTROL_RISK_fav_ctrl_allowed3': num(d,'f_ctrl_allowed15')>=3,
      'TD_DEF_DISADVANTAGE_10pp': (num(d,'f_td_def')-num(d,'o_td_def'))<=-.10,
      'POWER_RISK_012': (num(d,'o_kd15')*num(d,'f_kd_abs15'))>=.12,
      'POWER_RISK_020': (num(d,'o_kd15')*num(d,'f_kd_abs15'))>=.20,
      'FINISH_VULN_40': num(d,'f_finish_loss_pct')>=.40,
      'OPP_WIN_STREAK_3': num(d,'f5_o_current_win_streak')>=3,
    }

    def audit_veto(name,v):
        v=(v.fillna(False) if hasattr(v,'fillna') else pd.Series(v,index=d.index).fillna(False))
        rem=lrr&v;keep=lrr&~v
        rz=stats(d,rem);kz=stats(d,keep)
        total_losses=base['losses']; total_wins=base['wins']
        return {'veto':name,'removed_n':rz['n'],'removed_wins':rz['wins'],'removed_losses':rz['losses'],
                'loss_capture':rz['losses']/total_losses if total_losses else None,
                'win_cost':rz['wins']/total_wins if total_wins else None,
                'retained':kz,
                'removed':rz}

    audits=[audit_veto(k,v) for k,v in vetoes.items()]
    # Same veto must not be a one-era mirage.
    for a in audits:
        v=vetoes[a['veto']].fillna(False)
        for label,era in [('pre2020',d._date.dt.year<=2019),('2020plus',d._date.dt.year>=2020),('2024plus',d._date.dt.year>=2024)]:
            a[label+'_baseline']=stats(d,lrr&era)
            a[label+'_retained']=stats(d,lrr&era&~v)
    audits=sorted(audits,key=lambda a:(
        (a['retained'].get('roi') or -9)-(base.get('roi') or -9),
        (a['loss_capture'] or 0)-(a['win_cost'] or 0)
    ),reverse=True)
    pd.DataFrame([{**{k:v for k,v in a.items() if not isinstance(v,dict)},
                   **{'retained_'+k:v for k,v in a['retained'].items()},
                   **{'removed_'+k:v for k,v in a['removed'].items()}} for a in audits]).to_csv(OUT/'simple_veto_audit.csv',index=False)
    print('SIMPLE_VETO_AUDIT')
    for a in audits:print(json.dumps(a,sort_keys=True))

    # Pairwise risk combinations, only from interpretable risk flags and requiring at least 6 removed.
    pairrows=[]
    keys=list(vetoes)
    for a,b in combinations(keys,2):
        v=vetoes[a].fillna(False)&vetoes[b].fillna(False)
        x=audit_veto(a+' + '+b,v)
        if x['removed_n']<6:continue
        # Robustness: retained ROI must improve or at least stay positive in both broad eras.
        ok=True
        for era in [d._date.dt.year<=2019,d._date.dt.year>=2020]:
            bz=stats(d,lrr&era);kz=stats(d,lrr&era&~v)
            if kz['n']<20 or kz['roi'] is None or kz['roi']<=0:ok=False
            x.setdefault('era_checks',[]).append({'baseline':bz,'retained':kz})
        if ok:pairrows.append(x)
    pairrows=sorted(pairrows,key=lambda x:((x['retained'].get('roi') or -9), (x['loss_capture'] or 0)-(x['win_cost'] or 0)),reverse=True)
    print('PAIRWISE_VETO_AUDIT_TOP')
    for x in pairrows[:30]:print(json.dumps(x,sort_keys=True))

    # Loss roster with all diagnostic variables.
    cols=['event_date','favorite','opponent','market_prob','fav_decimal','age_adv','won',
          'f2_diff_r3_sig_diff_pm','f2_diff_cardio_sig_decay','f2_diff_sos_opp_elo',
          'f2_diff_quality_adj_sig_diff','f2_diff_recent3_sig_diff_pm','f2_diff_recent3_ctrl_diff15',
          'f2_f_recent3_kd_abs15','f2_f_recent3_sig_abs_pm','f_ctrl_allowed15','o_ctrl15','o_td_a15',
          'f_td_def','o_td_def','f_finish_loss_pct','o_kd15','f_kd_abs15']
    cols=[c for c in cols if c in d]
    lossdf=d.loc[losses,cols].copy()
    lossdf.to_csv(OUT/'lrr_losses.csv',index=False)
    print('LOSSES')
    for _,r in lossdf.iterrows():print(json.dumps({k:(None if pd.isna(v) else v) for k,v in r.to_dict().items()},default=str))

if __name__=='__main__':main()
