from pathlib import Path
import os, json, itertools
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'coach_only_discovery'; OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'


def num(s): return pd.to_numeric(s,errors='coerce')
def metrics(x):
    if not len(x): return {'n':0,'wins':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}
def season_stats(x):
    if not len(x): return (np.nan,0,0,np.nan)
    y=x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit','sum')).reset_index()
    y['roi']=y.units/y.n; active=y[y.n>=2]
    pos=int((active.units>0).sum()); act=len(active)
    ratio=pos/act if act else np.nan
    worst=float(active.roi.min()) if act else np.nan
    return ratio,pos,act,worst

d=pd.read_parquet(BASE)
d=d[d.season.between(2006,2025)].copy()
d=d[num(d.win).isin([0,1]) & num(d.moneyline).notna()].copy()
d['win']=num(d.win); d['profit']=num(d.profit); d['market_prob']=num(d.market_prob)

# Coach-only numerical features. No team-performance or travel variables are allowed as conditions.
coach_features=[c for c in d.columns if c.startswith('coachq_') or c.startswith('adv_') and any(k in c for k in [
    'hc_prior_quality','oc_prior_quality','dc_prior_quality','hc_recent_quality','oc_recent_quality','dc_recent_quality',
    'hc_prior_win_pct','oc_prior_win_pct','dc_prior_win_pct','hc_quality_delta_vs_departed','oc_quality_delta_vs_departed','dc_quality_delta_vs_departed',
    'staff_quality_mean','staff_quality_min','staff_upgrade_count','staff_downgrade_count'])]
coach_features=[c for c in coach_features if pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=250]

# Add a few explicit purely-coaching interaction scores.
for roles in [('hc','oc'),('hc','dc'),('oc','dc')]:
    cols=[f'coachq_{r}_prior_quality' for r in roles]
    if all(c in d for c in cols):
        name='coachonly_'+roles[0]+'_'+roles[1]+'_quality_mean'; d[name]=d[cols].mean(axis=1); coach_features.append(name)
if all(c in d for c in ['coachq_hc_prior_quality','coachq_oc_prior_quality','coachq_dc_prior_quality']):
    d['coachonly_staff_prior_quality_spread']=d[['coachq_hc_prior_quality','coachq_oc_prior_quality','coachq_dc_prior_quality']].max(axis=1)-d[['coachq_hc_prior_quality','coachq_oc_prior_quality','coachq_dc_prior_quality']].min(axis=1)
    coach_features.append('coachonly_staff_prior_quality_spread')
if all(c in d for c in ['coachq_staff_upgrade_count','coachq_staff_downgrade_count']):
    d['coachonly_upgrade_minus_downgrade']=num(d.coachq_staff_upgrade_count)-num(d.coachq_staff_downgrade_count); coach_features.append('coachonly_upgrade_minus_downgrade')
coach_features=sorted(set(coach_features))

tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}
bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}

def apply(x,cond):
    c,op,t=cond; v=num(x[c]); return v>=t if op=='>=' else v<=t
def filt(x,conds):
    m=pd.Series(True,index=x.index)
    for c in conds:m &= apply(x,c)
    return x[m]
def era(x,a,b): return x[x.season.between(a,b)]

rows=[]
for track,(tr,va,ho) in tracks.items():
  for band,(lo,hi) in bands.items():
    for venue,vfn in venues.items():
      base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)].copy()
      train=era(base,*tr); val=era(base,*va); hold=era(base,*ho)
      if len(train)<100 or len(val)<50 or len(hold)<50: continue
      # Train-only threshold library.
      conds=[]
      for c in coach_features:
        v=num(train[c]).dropna()
        if len(v)<80: continue
        for q in [.15,.25,.35,.50,.65,.75,.85]:
          t=float(v.quantile(q))
          for op in ['>=','<=']:
            a=filt(train,[(c,op,t)]); b=filt(val,[(c,op,t)])
            ma,mb=metrics(a),metrics(b)
            if ma['n']>=25 and mb['n']>=15 and ma['roi']>=.04 and mb['roi']>=.04:
                conds.append((c,op,t,ma['roi']+mb['roi']))
      # Keep best condition per feature+direction and cap library.
      best={}
      for c,op,t,s in conds:
        key=(c,op)
        if key not in best or s>best[key][3]: best[key]=(c,op,t,s)
      lib=sorted(best.values(),key=lambda z:z[3],reverse=True)[:50]

      candidates=[]
      # univariates
      for z in lib: candidates.append([(z[0],z[1],z[2])])
      # pure coaching pairs
      for a,b in itertools.combinations(lib[:35],2):
        if a[0]==b[0]: continue
        candidates.append([(a[0],a[1],a[2]),(b[0],b[1],b[2])])
      # targeted triples from top coach conditions only
      for a,b,c in itertools.combinations(lib[:16],3):
        if len({a[0],b[0],c[0]})<3: continue
        candidates.append([(a[0],a[1],a[2]),(b[0],b[1],b[2]),(c[0],c[1],c[2])])

      seen=set()
      for cs in candidates:
        key=json.dumps(sorted(cs),sort_keys=True)
        if key in seen: continue
        seen.add(key)
        a,b,h,full=filt(train,cs),filt(val,cs),filt(hold,cs),filt(base,cs)
        ma,mb,mh,mf=metrics(a),metrics(b),metrics(h),metrics(full)
        minfull=70 if len(cs)<=2 else 60
        if ma['n']<18 or mb['n']<12 or mh['n']<15 or mf['n']<minfull: continue
        if ma['roi']<.06 or mb['roi']<.06 or mh['roi']<.08 or mf['roi']<.08: continue
        pos,posn,act,worst=season_stats(full)
        older=(ma['roi']+mb['roi'])/2
        rows.append({'track':track,'price_band':band,'venue':venue,'conditions':json.dumps(cs),'condition_count':len(cs),
                     'full_n':mf['n'],'full_wins':mf['wins'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],
                     'train_n':ma['n'],'train_win_pct':ma['win_pct'],'train_roi':ma['roi'],
                     'validation_n':mb['n'],'validation_win_pct':mb['win_pct'],'validation_roi':mb['roi'],
                     'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],
                     'positive_season_ratio':pos,'positive_seasons':posn,'active_seasons':act,'worst_active_season_roi':worst,
                     'recent_delta':mh['roi']-older})

res=pd.DataFrame(rows)
if len(res):
    # User preference: double-digit ROI all eras, season consistency, recent strengthening where possible.
    res['era_floor']=res[['train_roi','validation_roi','holdout_roi']].min(axis=1)
    res['preferred']=(res.full_n>=80)&(res.full_roi>=.10)&(res.era_floor>=.08)&(res.holdout_roi>=.10)&(res.positive_season_ratio>=.70)&(res.recent_delta>=-.01)
    res['elite_recent']=(res.full_n>=90)&(res.full_roi>=.15)&(res.era_floor>=.10)&(res.holdout_roi>=.15)&(res.positive_season_ratio>=.75)&(res.recent_delta>=.02)
    res['score']=res.era_floor*.25+res.holdout_roi*.25+res.full_roi*.18+res.positive_season_ratio*.16+res.recent_delta.clip(-.2,.2)*.10+res.full_win_pct*.04+np.log10(res.full_n)*.02
    res=res.sort_values(['elite_recent','preferred','score'],ascending=[False,False,False])
    # simple near-duplicate removal: same track/band/venue + identical condition feature set -> keep highest score
    res['feature_signature']=res.conditions.map(lambda s:'|'.join(sorted(c[0] for c in json.loads(s))))
    res=res.drop_duplicates(['track','price_band','venue','feature_signature'],keep='first')
    res.to_csv(OUT/'coach_only_methods.csv',index=False)
    res[res.preferred].to_csv(OUT/'preferred_coach_only_methods.csv',index=False)
summary={'coach_features':len(coach_features),'survivors':int(len(res)) if len(rows) else 0,'preferred':int(res.preferred.sum()) if len(rows) else 0,'elite_recent':int(res.elite_recent.sum()) if len(rows) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACH-ONLY METHOD DISCOVERY','',json.dumps(summary,indent=2),'','TOP COACH-ONLY METHODS']
if len(res):
    for i,(_,r) in enumerate(res.head(25).iterrows(),1):
        tag='ELITE_RECENT' if r.elite_recent else 'PREFERRED' if r.preferred else 'SURVIVOR'
        lines.append(f"CO{i:03d} {tag} {r.track} {r.price_band} {r.venue} | {int(r.full_wins)}-{int(r.full_n-r.full_wins)} ({100*r.full_win_pct:.1f}%) n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% | train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% | pos seasons={int(r.positive_seasons)}/{int(r.active_seasons)} ({100*r.positive_season_ratio:.0f}%) recent_delta={100*r.recent_delta:+.1f}pp | {r.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:60]))
