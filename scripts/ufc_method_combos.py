#!/usr/bin/env python3
import json,re
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path.home()/"ufc-predictor-v1"
def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def fkey(dt,a,b):
    try:return (str(pd.Timestamp(dt).date()),)+tuple(sorted((norm(a),norm(b))))
    except:return None
def main():
    d=pd.read_csv(ROOT/'feature_expansion/prefight_favorite_features_v6.csv',low_memory=False)
    d['_key']=[fkey(r.event_date,r.favorite,r.opponent) for r in d.itertuples()]
    universe=set(d._key); sets={f'U{i}':set() for i in range(1,11)}
    u1=pd.read_csv(ROOT/'hybrid_70_agegap_robustness/all_rule_bets.csv',low_memory=False);sets['U1']={fkey(r.date,r.fighter_a,r.fighter_b) for r in u1.itertuples()}
    u2=pd.read_csv(ROOT/'skill_veto_official_samples/reach_415_with_skill_veto.csv',low_memory=False);sets['U2']={fkey(r.event_date,r.favorite,r.underdog) for r in u2.itertuples()}
    u3=pd.read_csv(ROOT/'structural_edge_failure_analysis/structural_edge_83_fights.csv',low_memory=False);sets['U3']={fkey(r.event_date,r.favorite,r.underdog) for r in u3.itertuples()}
    u4=pd.read_csv(ROOT/'ufc_age_reach_overlap/age_plus_reach4.csv',low_memory=False);sets['U4']={fkey(r.event_date,r.player1,r.player2) for r in u4.itertuples()}
    n=lambda c:pd.to_numeric(d[c],errors='coerce')
    sets['U5']=set(d.loc[(n('f_fights')>=2)&(n('o_fights')>=2)&(n('f_td_a15')>=5)&(n('f_td_l15')>=1)&(n('f_ctrl15')>=1)&(n('o_td_a15')<=2)&(n('o_ctrl15')<=.75),'_key'])
    sets['U6']=set(d.loc[(n('market_prob')>=.65)&(n('f_fights')>=3)&(n('o_fights')>=3)&((n('f_sig_l_pm')-n('o_sig_l_pm'))>=.25)&((n('f_sig_def')-n('o_sig_def'))>=.15),'_key'])
    raw7=(n('market_prob')>=.70)&(n('f_fights')>=4)&(n('o_fights')>=4)&((n('f_sig_diff_pm')-n('o_sig_diff_pm'))>=1)&((n('f_td_def')-n('o_td_def'))>=.10)
    ids=pd.read_csv(ROOT/'raw/individuals.csv',low_memory=False);dob={norm(r.get('name')):pd.to_datetime(r.get('dob'),errors='coerce') for _,r in ids.iterrows()}
    m7=[]
    for i,r in d.iterrows():
        if not raw7.iloc[i]:m7.append(False);continue
        ed=pd.Timestamp(r.event_date);fd=dob.get(norm(r.favorite));od=dob.get(norm(r.opponent))
        if pd.isna(fd) or pd.isna(od):m7.append(False);continue
        age_adv=((ed-od).days-(ed-fd).days)/365.2425
        power=float(r.o_kd15)*float(r.f_kd_abs15) if pd.notna(r.o_kd15) and pd.notna(r.f_kd_abs15) else np.nan
        m7.append(bool(age_adv>=-3 and np.isfinite(power) and power<.12))
    sets['U7']=set(d.loc[pd.Series(m7,index=d.index),'_key'])
    sets['U8']=set(d.loc[(n('market_prob')>=.75)&(n('f_fights')>=4)&(n('o_fights')>=4)&((n('f_sig_diff_pm')-n('o_sig_diff_pm'))>=1)&(n('f_sig_diff_pm')>=.5),'_key'])
    wg=n('f_win_pct')-n('o_win_pct');fg=n('f_finish_loss_pct')-n('o_finish_loss_pct');veto=(n('f_ctrl_allowed15')>=3)&(n('f_finish_loss_pct')>=.40)
    sets['U9']=set(d.loc[(n('market_prob')>=.575)&(n('market_prob')<=.75)&(n('f_fights')>=8)&(n('o_fights')<=3)&(wg>=.05)&(fg<=.60)&(~veto),'_key'])
    sets['U10']=set(d.loc[n('f2_diff_days_since_ko_loss')>=105,'_key'])
    sets={k:v&universe for k,v in sets.items()}
    d['methods']=[tuple(m for m in sets if k in sets[m]) for k in d._key];d['method_count']=d.methods.map(len);d['profit']=np.where(d.won.astype(bool),n('fav_decimal')-1,-1.0)
    def sm(q): return {'n':len(q),'wins':int(q.won.astype(bool).sum()),'losses':int((~q.won.astype(bool)).sum()),'win_pct':float(q.won.astype(bool).mean()),'roi':float(q.profit.mean())} if len(q) else None
    print('METHOD_COUNTS',json.dumps({k:len(v) for k,v in sets.items()},sort_keys=True))
    for z in range(1,8):
        q=d[d.method_count==z]
        if len(q):print('EXACT',z,json.dumps(sm(q),sort_keys=True))
    for z in range(2,7):
        q=d[d.method_count>=z]
        if len(q):print('AT_LEAST',z,json.dumps(sm(q),sort_keys=True))
    print('=== EXACT COMBOS N>=5 ===')
    arr=[]
    for combo,g in d[d.method_count>=2].groupby('methods'):
        if len(g)>=5:arr.append((len(g),float(g.profit.mean()),combo,sm(g)))
    for _,_,c,s in sorted(arr,key=lambda x:(x[0],x[1]),reverse=True):print('+'.join(c),json.dumps(s,sort_keys=True))
    print('=== PAIRS N>=5 ===')
    arr=[];names=list(sets)
    for i,a in enumerate(names):
        for b in names[i+1:]:
            q=d[d._key.isin(sets[a]&sets[b])]
            if len(q)>=5:arr.append((len(q),float(q.profit.mean()),a,b,sm(q)))
    for _,_,a,b,s in sorted(arr,reverse=True):print(a+'+'+b,json.dumps(s,sort_keys=True))
    print('=== 4+ EXAMPLES ===')
    for r in d[d.method_count>=4].sort_values(['method_count','event_date'],ascending=[False,False]).head(80).itertuples():print(r.event_date,'|',r.favorite,'vs',r.opponent,'|','+'.join(r.methods),'|',('W' if bool(r.won) else 'L'),'|',round(float(r.fav_decimal),3),'|',round(float(r.profit),3))
    out=ROOT/'method_deep_research';out.mkdir(exist_ok=True)
    d[['event_date','favorite','opponent','market_prob','fav_decimal','won','profit','methods','method_count']].to_csv(out/'method_combo_fights_final.csv',index=False)
    (out/'method_combo_summary_final.json').write_text(json.dumps({'method_counts':{k:len(v) for k,v in sets.items()},'at_least':{str(z):sm(d[d.method_count>=z]) for z in range(2,7)}},indent=2))
    print('U7_reconstructed_count',len(sets['U7']))
if __name__=='__main__':main()
