#!/usr/bin/env python3
"""Audit the proposed Striking+TD risk gate against the current upcoming feed."""
from __future__ import annotations
import io,json,re,unicodedata,urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

COMP_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv'
IND_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
FEED_URL='https://raw.githubusercontent.com/maxxw71/ufc-feed/main/upcoming.json'
OUT=Path('ufc_striking_td_postmortem');OUT.mkdir(exist_ok=True)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Appwiza-UFC-Research/1.0'})
    with urllib.request.urlopen(req,timeout=120) as r:return r.read()

def norm(v):
    x=unicodedata.normalize('NFKD',str(v));x=''.join(c for c in x if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',x.lower()).split())

def parse_of(v):
    m=re.search(r'(\d+)\s+of\s+(\d+)',str(v),re.I);return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)

def parse_num(v):
    m=re.search(r'-?\d+(?:\.\d+)?',str(v));return float(m.group()) if m else 0.

def mins(r):
    try:rnd=max(1,int(float(r.get('round',1) or 1)))
    except:rnd=1
    m=re.match(r'(\d+):(\d+)',str(r.get('time','0:00')));sec=int(m.group(1))*60+int(m.group(2)) if m else 0
    return max(1,(rnd-1)*300+sec)/60

def profile(df,name,cutoff):
    nk=norm(name);q={'f':0,'m':0.,'sl':0.,'abs':0.,'absa':0.,'kdabs':0.,'tda':0.,'tdallowed':0.,'tdfaced':0.}
    for _,r in df[df.event_date<cutoff].iterrows():
        n1,n2=norm(r.get('player1','')),norm(r.get('player2',''))
        if nk==n1:s,o=1,2
        elif nk==n2:s,o=2,1
        else:continue
        fm=mins(r);q['f']+=1;q['m']+=fm
        for rd in range(1,6):
            a,_=parse_of(r.get(f'p{s}_rd{rd}_Sig_str'));q['sl']+=a
            a,c=parse_of(r.get(f'p{o}_rd{rd}_Sig_str'));q['abs']+=a;q['absa']+=c
            q['kdabs']+=parse_num(r.get(f'p{o}_rd{rd}_KD'))
            _,c=parse_of(r.get(f'p{s}_rd{rd}_Td'));q['tda']+=c
            a,c=parse_of(r.get(f'p{o}_rd{rd}_Td'));q['tdallowed']+=a;q['tdfaced']+=c
    if q['f']<1 or q['m']<=0:return None
    m=q['m'];return {'fights':q['f'],'sig_diff_pm':(q['sl']-q['abs'])/m,'td_def':1-q['tdallowed']/q['tdfaced'] if q['tdfaced'] else np.nan,'kd_abs15':q['kdabs']*15/m}

def opponent_kd15(df,name,cutoff):
    nk=norm(name);f=0;m=0.;kd=0.
    for _,r in df[df.event_date<cutoff].iterrows():
        n1,n2=norm(r.get('player1','')),norm(r.get('player2',''))
        if nk==n1:s=1
        elif nk==n2:s=2
        else:continue
        fm=mins(r);f+=1;m+=fm
        for rd in range(1,6):kd+=parse_num(r.get(f'p{s}_rd{rd}_KD'))
    return kd*15/m if f and m else np.nan

def american_from_prob(p):return -100*p/(1-p) if p>=.5 else 100*(1-p)/p

def main():
    comp=pd.read_csv(io.BytesIO(fetch(COMP_URL)),low_memory=False);comp['event_date']=pd.to_datetime(comp.event_date,errors='coerce').dt.normalize()
    ind=pd.read_csv(io.BytesIO(fetch(IND_URL)),low_memory=False);ind['_n']=ind['name'].map(norm) if 'name' in ind.columns else '' ;ind['dob_dt']=pd.to_datetime(ind.dob,errors='coerce')
    feed=json.loads(fetch(FEED_URL));rows=[]
    for e in feed.get('events',[]):
        ed=pd.Timestamp(e['date']).normalize()
        for b in e.get('bouts',[]):
            m=b.get('market') or {}
            if m.get('status')!='ok':continue
            a,bn=b.get('fighter_a'),b.get('fighter_b')
            try:pa=float(m.get('consensus_no_vig_a'));pb=float(m.get('consensus_no_vig_b'))
            except:continue
            fav,opp,p=(a,bn,pa) if pa>=pb else (bn,a,pb)
            fp=profile(comp,fav,ed);op=profile(comp,opp,ed)
            if not fp or not op:continue
            siggap=fp['sig_diff_pm']-op['sig_diff_pm'];tdgap=fp['td_def']-op['td_def'] if pd.notna(fp['td_def']) and pd.notna(op['td_def']) else np.nan
            baseline=bool(p>=.70 and fp['fights']>=4 and op['fights']>=4 and siggap>=1 and pd.notna(tdgap) and tdgap>=.10)
            if not baseline:continue
            def age(name):
                z=ind[ind['_n']==norm(name)]
                if not len(z) or pd.isna(z.iloc[0].dob_dt):return np.nan
                return (ed-z.iloc[0].dob_dt).days/365.2425
            fa,oa=age(fav),age(opp);ageadv=oa-fa if pd.notna(fa) and pd.notna(oa) else np.nan
            okd=opponent_kd15(comp,opp,ed);product=okd*fp['kd_abs15'] if pd.notna(okd) and pd.notna(fp['kd_abs15']) else np.nan
            gate=bool(pd.notna(ageadv) and ageadv>=-1 and pd.notna(product) and product<.12)
            rows.append({'event_date':ed.date(),'event':e.get('name'),'favorite':fav,'opponent':opp,'market_prob':p,'fair_american':american_from_prob(p),'sig_diff_gap':siggap,'td_def_gap':tdgap,'fav_age':fa,'opp_age':oa,'age_adv':ageadv,'opponent_kd15':okd,'favorite_kd_abs15':fp['kd_abs15'],'power_risk_product':product,'original_u7':True,'proposed_u7_gate':gate,'decision':'KEEP' if gate else 'VETO'})
    z=pd.DataFrame(rows);z.to_csv(OUT/'current_live_impact.csv',index=False)
    lines=['CURRENT UFC STRIKING + TD DEFENSE — PROPOSED RISK GATE IMPACT','='*104,'']
    if z.empty:lines.append('No current upcoming bouts qualify the original U7 rule.')
    else:
        for _,r in z.iterrows():lines.append(f"{r.event_date} | {r.favorite} vs {r.opponent} | market={r.market_prob:.3f} | ageAdv={r.age_adv:+.2f}y | power={r.power_risk_product:.3f} | {r.decision}")
        lines+=['',f"Original U7 current signals: {len(z)}",f"Kept by proposed gate: {int(z.proposed_u7_gate.sum())}",f"Vetoed by proposed gate: {int((~z.proposed_u7_gate).sum())}"]
    (OUT/'current_live_impact.txt').write_text('\n'.join(lines))
    print('\n'.join(lines))
if __name__=='__main__':main()
