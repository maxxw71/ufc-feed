from pathlib import Path
import os, json, math
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'coach_quality_expansion';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
STAFF=CTX/'raw/coaching_staff_2006_2026.csv'
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS'}

def num(s):return pd.to_numeric(s,errors='coerce')
def implied(ml):
    ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):
    ml=num(ml);win=num(win);return np.where(win.eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def metrics(x):
    if not len(x):return {'n':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}

d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES)
# authoritative REG only
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')].copy();reg=set(s.game_id.astype(str));d=d[d.game_id.astype(str).isin(reg)].copy()
d['win']=num(d.win);d['moneyline']=num(d.moneyline);d=d[d.win.isin([0,1])&d.moneyline.notna()].copy();d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)

# Build team-season performance table using only season-complete information, which becomes pregame-safe for the next season.
# Use final available pregame rolling snapshot + actual season win rate.
last=d.sort_values(['season','week','game_id']).groupby(['season','team']).tail(1).copy()
season_win=d.groupby(['season','team']).win.mean().rename('team_season_win_pct').reset_index()
last=last.merge(season_win,on=['season','team'],how='left')
off_col=next((c for c in ['pre_off_epa_per_play','recent_off_epa_per_play','pre_pass_epa_per_dropback'] if c in last.columns),None)
def_col=next((c for c in ['pre_def_allowed_off_epa_per_play','recent_def_allowed_off_epa_per_play','pre_def_allowed_pass_epa_per_dropback'] if c in last.columns),None)
if off_col is None or def_col is None: raise RuntimeError('Required offense/defense quality columns unavailable')
last['off_value']=num(last[off_col]);last['def_allowed_value']=num(last[def_col])
last['off_quality_pct']=last.groupby('season').off_value.rank(pct=True,method='average')
last['def_quality_pct']=1-last.groupby('season').def_allowed_value.rank(pct=True,method='average')+1/last.groupby('season').def_allowed_value.transform('count')
teamperf=last[['season','team','team_season_win_pct','off_quality_pct','def_quality_pct','off_value','def_allowed_value']].copy()

st=pd.read_csv(STAFF);st.team=st.team.replace(ALIASES);st=st[st.season.between(2006,2026)].copy()
for c in ['head_coach','offensive_coordinator','defensive_coordinator']:
    if c not in st:st[c]=np.nan
# attach team performance for that coach-season
st=st.merge(teamperf,on=['season','team'],how='left')

# prior-role quality for each coach, strictly prior seasons only.
def prior_role_features(frame,name_col,role_prefix,quality_col):
    rows=[]
    for coach,g in frame[frame[name_col].notna()].groupby(name_col):
        g=g.sort_values('season')
        hist=[]
        for _,r in g.iterrows():
            vals=[h for h in hist if pd.notna(h['quality'])]
            winvals=[h for h in hist if pd.notna(h['win'])]
            rows.append({'season':int(r.season),'team':r.team,
                         f'{role_prefix}_prior_role_seasons':len(set(h['season'] for h in hist)),
                         f'{role_prefix}_prior_quality':float(np.mean([h['quality'] for h in vals])) if vals else np.nan,
                         f'{role_prefix}_recent_quality':float(vals[-1]['quality']) if vals else np.nan,
                         f'{role_prefix}_prior_win_pct':float(np.mean([h['win'] for h in winvals])) if winvals else np.nan})
            hist.append({'season':int(r.season),'quality':r.get(quality_col),'win':r.get('team_season_win_pct')})
    return pd.DataFrame(rows)
hc=prior_role_features(st,'head_coach','hc','team_season_win_pct')
oc=prior_role_features(st,'offensive_coordinator','oc','off_quality_pct')
dc=prior_role_features(st,'defensive_coordinator','dc','def_quality_pct')
q=st[['season','team','head_coach','offensive_coordinator','defensive_coordinator']].copy()
for z in [hc,oc,dc]:q=q.merge(z,on=['season','team'],how='left')

# Compare current staff prior quality with previous season's incumbent staff prior quality.
prev=q.copy();prev['season']=prev.season+1
prev=prev.rename(columns={c:'prev_'+c for c in prev.columns if c not in ['season','team']})
q=q.merge(prev,on=['season','team'],how='left')
for role in ['hc','oc','dc']:
    name={'hc':'head_coach','oc':'offensive_coordinator','dc':'defensive_coordinator'}[role]
    q[f'{role}_changed_quality']=np.where(q[name].notna()&q['prev_'+name].notna()&(q[name]!=q['prev_'+name]),1.0,0.0)
    q[f'{role}_quality_delta_vs_departed']=q[f'{role}_prior_quality']-q[f'prev_{role}_prior_quality']
# overall staff quality uses available components only, not missing-as-zero.
for role in ['hc','oc','dc']:
    # HC prior quality is win pct; OC/DC prior quality are percentiles, all 0-1 scale.
    pass
q['staff_quality_mean']=q[['hc_prior_quality','oc_prior_quality','dc_prior_quality']].mean(axis=1,skipna=True)
q['staff_quality_min']=q[['hc_prior_quality','oc_prior_quality','dc_prior_quality']].min(axis=1,skipna=True)
q['staff_upgrade_count']=((q.hc_quality_delta_vs_departed>0).fillna(False).astype(int)+(q.oc_quality_delta_vs_departed>0).fillna(False).astype(int)+(q.dc_quality_delta_vs_departed>0).fillna(False).astype(int))
q['staff_downgrade_count']=((q.hc_quality_delta_vs_departed<0).fillna(False).astype(int)+(q.oc_quality_delta_vs_departed<0).fillna(False).astype(int)+(q.dc_quality_delta_vs_departed<0).fillna(False).astype(int))

# map team + opponent quality onto every game row
tcols=[c for c in q.columns if c not in ['head_coach','offensive_coordinator','defensive_coordinator']]
teamq=q[tcols].copy().rename(columns={c:'coachq_'+c for c in tcols if c not in ['season','team']})
oppq=q[tcols].copy().rename(columns={'team':'opponent',**{c:'opp_coachq_'+c for c in tcols if c not in ['season','team']}})
d=d.merge(teamq,on=['season','team'],how='left').merge(oppq,on=['season','opponent'],how='left')
base_quality=['hc_prior_quality','oc_prior_quality','dc_prior_quality','hc_recent_quality','oc_recent_quality','dc_recent_quality','hc_prior_win_pct','oc_prior_win_pct','dc_prior_win_pct','hc_quality_delta_vs_departed','oc_quality_delta_vs_departed','dc_quality_delta_vs_departed','staff_quality_mean','staff_quality_min','staff_upgrade_count','staff_downgrade_count']
features=[]
for b in base_quality:
    a='coachq_'+b;o='opp_coachq_'+b
    if a in d:features.append(a)
    if a in d and o in d:
        d['adv_'+b]=num(d[a])-num(d[o]);features.append('adv_'+b)
# save enriched layer for future searches/live builder
d.to_parquet(OUT/'coach_quality_enriched_team_sides.parquet',index=False)
q.to_csv(OUT/'coach_quality_team_seasons.csv',index=False)

# Search coaching quality + football combinations. Train thresholds only, validation screen, holdout confirmation.
football=[c for c in d.columns if any(k in c.lower() for k in ['rank_edge_','recent_edge_','adv_travel','adv_prior_road','fatigue','continuity','qb_prior_starts']) and c not in features]
football=[c for c in football if pd.api.types.is_numeric_dtype(d[c])][:80]
features=[c for c in features if c in d and num(d[c]).notna().sum()>=250]
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}
bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)}
venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:~num(x.is_home).eq(1),'HOME':lambda x:num(x.is_home).eq(1)}
rows=[]
def apply_cond(x,c,op,t):
    v=num(x[c]);return v>=t if op=='>=' else v<=t
def era(x,a,b):return x[x.season.between(a,b)]
def season_ratio(x):
    if not len(x):return np.nan
    y=x.groupby('season').profit.sum();return float((y>0).mean()) if len(y) else np.nan
for track,(tr,va,ho) in tracks.items():
  for band,(plo,phi) in bands.items():
    for venue,vfun in venues.items():
      base=d[(d.market_prob>=plo)&(d.market_prob<phi)&vfun(d)&(num(d.prior_games)>=3)].copy()
      train=era(base,*tr);valid=era(base,*va);hold=era(base,*ho)
      if len(train)<100:continue
      # coach quality univariates and coach+football pairs
      coachconds=[]
      for c in features:
        vv=num(train[c]).dropna()
        if len(vv)<80:continue
        for qtile in [.20,.35,.50,.65,.80]:
          t=float(vv.quantile(qtile))
          for op in ['>=','<=']:
            a=train[apply_cond(train,c,op,t)];b=valid[apply_cond(valid,c,op,t)]
            ma,mb=metrics(a),metrics(b)
            if ma['n']>=25 and mb['n']>=15 and ma['roi']>=.06 and mb['roi']>=.06:coachconds.append((c,op,t,ma['roi']+mb['roi']))
      coachconds=sorted(coachconds,key=lambda z:z[3],reverse=True)[:35]
      footconds=[]
      for c in football:
        vv=num(train[c]).dropna()
        if len(vv)<100:continue
        for qtile in [.25,.50,.75]:
          t=float(vv.quantile(qtile))
          for op in ['>=','<=']:
            a=train[apply_cond(train,c,op,t)];b=valid[apply_cond(valid,c,op,t)]
            ma,mb=metrics(a),metrics(b)
            if ma['n']>=35 and mb['n']>=20 and ma['roi']>=.04 and mb['roi']>=.04:footconds.append((c,op,t,ma['roi']+mb['roi']))
      footconds=sorted(footconds,key=lambda z:z[3],reverse=True)[:35]
      candidates=[]
      for c,op,t,_ in coachconds:candidates.append([(c,op,t)])
      for cc in coachconds[:20]:
        for fc in footconds[:25]:
          if cc[0]==fc[0]:continue
          candidates.append([(cc[0],cc[1],cc[2]),(fc[0],fc[1],fc[2])])
      for conds in candidates:
        def filt(x):
          m=pd.Series(True,index=x.index)
          for c,op,t in conds:m&=apply_cond(x,c,op,t)
          return x[m]
        a,b,h=filt(train),filt(valid),filt(hold);full=filt(base)
        ma,mb,mh,mf=metrics(a),metrics(b),metrics(h),metrics(full)
        if ma['n']<20 or mb['n']<15 or mh['n']<15 or mf['n']<70:continue
        if ma['roi']<.08 or mb['roi']<.08 or mh['roi']<.10 or mf['roi']<.10:continue
        older=(ma['roi']+mb['roi'])/2;pos=season_ratio(full)
        rows.append({'track':track,'price_band':band,'venue':venue,'conditions':json.dumps(conds),'full_n':mf['n'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],'train_n':ma['n'],'train_win_pct':ma['win_pct'],'train_roi':ma['roi'],'validation_n':mb['n'],'validation_win_pct':mb['win_pct'],'validation_roi':mb['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'positive_season_ratio':pos,'recent_delta':mh['roi']-older})
res=pd.DataFrame(rows)
if len(res):
    res['user_preferred']=(res.full_n>=90)&(res.full_roi>=.12)&(res.train_roi>=.10)&(res.validation_roi>=.10)&(res.holdout_roi>=.12)&(res.positive_season_ratio>=.70)&(res.recent_delta>=0)
    res['score']=res.holdout_roi*.30+res.full_roi*.22+res.positive_season_ratio*.18+res.recent_delta.clip(-.2,.2)*.15+res.full_win_pct*.10+np.log10(res.full_n)*.025
    res=res.sort_values(['user_preferred','score'],ascending=[False,False]).drop_duplicates(['track','price_band','venue','conditions'])
    res.to_csv(OUT/'coach_quality_methods.csv',index=False)
    res[res.user_preferred].to_csv(OUT/'user_preferred_methods.csv',index=False)
summary={'coach_quality_features':len(features),'football_features':len(football),'survivors':int(len(res)) if len(rows) else 0,'user_preferred':int(res.user_preferred.sum()) if len(rows) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACH QUALITY EXPANSION','',json.dumps(summary,indent=2),'','TOP USER-PREFERRED']
if len(res):
  for i,(_,r) in enumerate(res[res.user_preferred].head(20).iterrows(),1):
    lines.append(f"CQ{i:03d} {r.track} {r.price_band} {r.venue} | n={int(r.full_n)} win={100*r.full_win_pct:.1f}% ROI={100*r.full_roi:+.1f}% | train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% recent_delta={100*r.recent_delta:+.1f}pp pos_seasons={100*r.positive_season_ratio:.0f}% | {r.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:60]))
