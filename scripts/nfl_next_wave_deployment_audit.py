from pathlib import Path
import json, os, math
import numpy as np
import pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
if not DATA.exists(): DATA=CTX/'creative_travel_fatigue'/'creative_context_team_sides_2006_2025.parquet'
STAFF=CTX/'raw'/'coaching_staff_2006_2026.csv'
NW=REPO/'nfl/next_wave_combinations'/'next_wave_survivors.csv'
OUT=CTX/'next_wave_deployment_audit'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win); return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if not len(x): return None
    by=x.groupby('season').profit_units.agg(['count','sum']); elig=by[by['count']>=3]
    return {'n':len(x),'roi':float(x.profit_units.mean()),'pos':int((elig['sum']>0).sum()),'active':len(elig)}
def amer(p):
    if p>=.5:return int(round(-100*p/(1-p)))
    return int(round(100*(1-p)/p))
def odds_range(lo,hi):
    a,b=amer(hi),amer(lo)
    return f'{a:+d} to {b:+d}'

d=pd.read_parquet(DATA)
d=d[d.season.between(2006,2025)].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_use']=num(d.market_prob) if 'market_prob' in d else pd.Series(implied(d.moneyline),index=d.index)
d['market_prob_use']=d.market_prob_use.fillna(pd.Series(implied(d.moneyline),index=d.index))

# Rebuild next-wave-only staff features that were derived in memory during discovery.
if STAFF.exists():
    st=pd.read_csv(STAFF)
    st['season']=num(st.season).astype('Int64'); st['team']=st.team.astype(str)
    for role,pfx in [('head_coach','hc'),('offensive_coordinator','oc'),('defensive_coordinator','dc')]:
        if role not in st.columns: continue
        z=st[['season','team',role]].dropna().sort_values(['team','season']).copy()
        vals=[]
        for team,g in z.groupby('team',sort=False):
            prev=None; run=0
            for _,r in g.iterrows():
                nm=str(r[role]).strip().lower(); run=run+1 if nm==prev else 1; prev=nm
                vals.append((r.season,team,run))
        zz=pd.DataFrame(vals,columns=['season','team',f'{pfx}_team_tenure_seasons'])
        if f'{pfx}_team_tenure_seasons' in d.columns:d=d.drop(columns=[f'{pfx}_team_tenure_seasons'])
        d=d.merge(zz.drop_duplicates(['season','team']),on=['season','team'],how='left')
if all(c in d.columns for c in ['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']):
    h=num(d.head_coach_changed); o=num(d.offensive_coordinator_changed); dc=num(d.defensive_coordinator_changed)
    d['staff_changes_count']=h.fillna(0)+o.fillna(0)+dc.fillna(0)
    d['full_staff_stable']=((h==0)&(o==0)&(dc==0)).astype(float)
    d['staff_overhaul_2plus']=(d.staff_changes_count>=2).astype(float)
if 'offensive_coordinator_changed' in d.columns and 'qb_prior_starts' in d.columns:
    d['new_oc_young_qb']=((num(d.offensive_coordinator_changed)==1)&(num(d.qb_prior_starts)<=16)).astype(float)
if 'defensive_coordinator_changed' in d.columns and 'returning_defense_snap_share' in d.columns:
    d['new_dc_low_def_continuity']=((num(d.defensive_coordinator_changed)==1)&(num(d.returning_defense_snap_share)<.65)).astype(float)
if 'staff_overhaul_2plus' in d.columns and 'fatigue_load_index' in d.columns:
    d['staff_overhaul_high_fatigue']=((num(d.staff_overhaul_2plus)==1)&(num(d.fatigue_load_index)>=1.5)).astype(float)

price={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)}
tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}
def era(track,p):lo,hi=tracks[track][p];return d.season.between(lo,hi)
def cond(c,op,q):
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def mask(r,plo=None,phi=None):
    lo,hi=price[r.price_band]; lo=lo if plo is None else plo; hi=hi if phi is None else phi
    m=num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='AWAY':m &= num(d.is_home).eq(0)
    elif r.venue=='HOME':m &= num(d.is_home).eq(1)
    for j in [1,2,3]:
        c=getattr(r,f'feature{j}',np.nan)
        if pd.notna(c):
            if c not in d.columns: return pd.Series(False,index=d.index)
            m &= cond(c,getattr(r,f'op{j}'),getattr(r,f'threshold{j}'))
    return m

nw=pd.read_csv(NW)
rows=[]
for _,r in nw.iterrows():
    track=r.track; base=mask(r); x=d[base]
    if not len(x):continue
    tc=x.team.value_counts(); top=float(tc.iloc[0]/len(x)); top3=float(tc.head(3).sum()/len(x))
    pos=float(r.full_positive_seasons/max(1,r.full_active_seasons))
    lo0,hi0=price[r.price_band]; best=None
    probs=num(d.loc[base&(era(track,'train')|era(track,'validation')),'market_prob_use']).dropna()
    cuts=sorted(set([lo0,hi0]+[float(v) for v in probs.quantile([.15,.25,.35,.65,.75,.85]).dropna()]))
    bt=metrics(d[base&era(track,'train')]); bv=metrics(d[base&era(track,'validation')])
    for lo in cuts:
      for hi in cuts:
        if lo<lo0 or hi>hi0 or hi<=lo:continue
        mm=mask(r,lo,hi); mt=metrics(d[mm&era(track,'train')]); mv=metrics(d[mm&era(track,'validation')])
        if not mt or not mv or mt['n']<max(20,int(.5*bt['n'])) or mv['n']<max(12,int(.5*bv['n'])):continue
        gain=.5*((mt['roi']-bt['roi'])+(mv['roi']-bv['roi'])); retain=.5*(mt['n']/bt['n']+mv['n']/bv['n'])
        score=gain+.03*retain
        if best is None or score>best[0]:best=(score,lo,hi,mt,mv)
    live_lo,live_hi=lo0,hi0; price_decision='KEEP_ORIGINAL_BAND'; hold_gain=0
    if best:
        _,lo,hi,mt,mv=best; mh0=metrics(d[base&era(track,'holdout')]); mw=mask(r,lo,hi); mh=metrics(d[mw&era(track,'holdout')])
        if mh and mh0 and mh['n']>=20 and mh['n']>=.5*mh0['n'] and mh['roi']>=mh0['roi']+.02:
            live_lo,live_hi=lo,hi; price_decision='TIGHTEN_LIVE_PRICE'; hold_gain=mh['roi']-mh0['roi']
    ready=(r.full_n>=100 and r.holdout_n>=25 and r.train_roi>=.15 and r.validation_roi>=.15 and r.holdout_roi>=.15 and r.full_roi>=.18 and pos>=.70 and top<=.12 and top3<=.30)
    watch=(not ready and r.full_n>=80 and r.holdout_n>=20 and r.holdout_roi>=.15 and r.full_roi>=.15 and pos>=.60 and top<=.16)
    rows.append({**r.to_dict(),'top_team':tc.index[0],'top_team_share':top,'top3_team_share':top3,'positive_season_ratio':pos,'live_p_lo':live_lo,'live_p_hi':live_hi,'live_odds_range':odds_range(live_lo,live_hi),'price_decision':price_decision,'price_holdout_gain':hold_gain,'pre_status':'READY' if ready else ('WATCH' if watch else 'RESEARCH_ONLY'),'mask_idx':set(d.index[base])})

a=pd.DataFrame(rows)
if not len(a): raise RuntimeError('No auditable next-wave methods after feature reconstruction')
a['robust_score']=a.full_roi+a.holdout_roi+.25*a.positive_season_ratio+.0005*a.full_n
order=a.sort_values('robust_score',ascending=False).index.tolist(); kept=[]; dup={}; overlap={}
for i in order:
    si=a.at[i,'mask_idx']; bestj=None; bestov=0
    for j in kept:
        sj=a.at[j,'mask_idx']; u=len(si|sj); ov=(len(si&sj)/u) if u else 0
        if ov>bestov:bestov=ov;bestj=j
    overlap[i]=bestov
    if bestov>=.75:dup[i]=a.at[bestj,'next_method_id']
    else:kept.append(i)
a['duplicate_of']=[dup.get(i,'') for i in a.index];a['max_jaccard_overlap']=[overlap.get(i,0) for i in a.index]
a['deployment_status']=np.where(a.duplicate_of.ne(''),'RESEARCH_ONLY',a.pre_status)
a.loc[(a.deployment_status=='READY') & (a.max_jaccard_overlap>=.60),'deployment_status']='WATCH'
a=a.drop(columns=['mask_idx']).sort_values(['deployment_status','holdout_roi','full_n'],ascending=[True,False,False])
a.to_csv(OUT/'next_wave_deployment_audit.csv',index=False)
a[a.deployment_status.eq('READY')].to_csv(OUT/'next_wave_ready.csv',index=False)
a[a.deployment_status.eq('WATCH')].to_csv(OUT/'next_wave_watch.csv',index=False)
summary={'audited':int(len(a)),'ready':int((a.deployment_status=='READY').sum()),'watch':int((a.deployment_status=='WATCH').sum()),'research_only':int((a.deployment_status=='RESEARCH_ONLY').sum()),'duplicates':int(a.duplicate_of.ne('').sum()),'tightened_prices':int((a.price_decision=='TIGHTEN_LIVE_PRICE').sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL NEXT-WAVE DEPLOYMENT AUDIT','',json.dumps(summary,indent=2),'','READY']
for _,r in a[a.deployment_status.eq('READY')].sort_values(['holdout_roi','full_n'],ascending=[False,False]).iterrows():
    rules=[]
    for j in [1,2,3]:
        if pd.notna(r.get(f'feature{j}')):rules.append(f"{r[f'feature{j}']} {r[f'op{j}']} {r[f'threshold{j}']:.5g}")
    lines.append(f"{r.next_method_id} {r.price_band} {r.venue} {r.live_odds_range} | {' AND '.join(rules)} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}% seasons={int(r.full_positive_seasons)}/{int(r.full_active_seasons)} top-team={100*r.top_team_share:.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
