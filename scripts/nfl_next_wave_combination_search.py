from pathlib import Path
import os, json, math
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
BASE=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
CREATIVE=CTX/'creative_travel_fatigue'/'creative_context_team_sides_2006_2025.parquet'
PCTX=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
OUT=CTX/'next_wave_combinations'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win); return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def sem(c):
    c=c.lower()
    if 'coach' in c or 'coordinator' in c or 'staff_' in c:return 'COACHING'
    if 'travel' in c or 'road_' in c or 'tz_' in c or 'altitude' in c or 'fatigue' in c:return 'TRAVEL_FATIGUE'
    if 'injur' in c or 'unavail' in c or 'out_' in c or 'questionable' in c:return 'INJURY'
    if 'returning_' in c or 'continuity' in c:return 'CONTINUITY'
    if 'qb_' in c:return 'QB'
    if 'open_prob' in c or 'market_move' in c:return 'MARKET'
    if 'third_down' in c:return 'THIRD_DOWN'
    if 'redzone' in c:return 'RED_ZONE'
    if 'explosive' in c:return 'EXPLOSIVE'
    if 'sack' in c or 'hit_rate' in c:return 'PRESSURE'
    if 'punt' in c or 'kick_return' in c or 'fg_accuracy' in c:return 'SPECIAL_TEAMS'
    if 'giveaway' in c or 'turnover' in c:return 'TURNOVERS'
    if 'pass' in c or 'cpoe' in c:return 'PASSING'
    if 'rush' in c:return 'RUSHING'
    if 'rest' in c:return 'REST'
    if 'epa' in c or 'success_rate' in c or 'yards_per_play' in c:return 'EFFICIENCY'
    if 'elo' in c:return 'ELO'
    if 'penalty' in c:return 'PENALTY'
    return 'OTHER'
def metrics(x):
    if len(x)==0:return None
    n=len(x); w=int(x.win.sum()); by=x.groupby('season').profit_units.agg(['count','sum']); elig=by[by['count']>=3]
    return {'n':n,'wins':w,'losses':n-w,'roi':float(x.profit_units.mean()),'win_rate':w/n,'active_seasons':len(elig),'positive_seasons':int((elig['sum']>0).sum())}

print('Loading next-wave dataset')
d=pd.read_parquet(CREATIVE if CREATIVE.exists() else BASE)
d['season']=num(d.season).astype('Int64'); d['week']=num(d.week).astype('Int64')
d=d[(d.season.between(2006,2025)) & num(d.win).isin([0,1]) & num(d.moneyline).notna()].copy()
if 'prior_games' in d:d=d[num(d.prior_games)>=3].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_use']=num(d.market_prob) if 'market_prob' in d else pd.Series(implied(d.moneyline),index=d.index)
d['market_prob_use']=d.market_prob_use.fillna(pd.Series(implied(d.moneyline),index=d.index))

# Refresh audited pregame coaching/QB/continuity fields.
if PCTX.exists():
    cx=pd.read_parquet(PCTX)
    keys=['game_id','season','week','team']
    want=[c for c in cx.columns if any(t in c.lower() for t in ['coach','coordinator','staff_','qb_prior_starts','qb_changed','returning_'])]
    want=[c for c in want if c not in keys]
    for c in want:
        if c in d:d=d.drop(columns=[c])
    d=d.merge(cx[keys+want].drop_duplicates(keys),on=keys,how='left')

# Derive staff tenure / experience from audited names without leaking future seasons.
def tenure_by_name(df,namecol,outcol):
    if namecol not in df:return
    seasons=df[['season','team',namecol]].dropna().drop_duplicates().sort_values(['team','season'])
    out=[]
    for team,g in seasons.groupby('team'):
        run=0; prev=None
        for _,r in g.iterrows():
            nm=str(r[namecol]); run=run+1 if nm==prev else 1; prev=nm
            out.append((r.season,team,nm,run))
    z=pd.DataFrame(out,columns=['season','team',namecol,outcol])
    nonlocal_keys=['season','team',namecol]
    df2=df.merge(z,on=nonlocal_keys,how='left')
    for c in df2.columns:
        df[c]=df2[c]

def career_prior_by_name(df,namecol,outcol):
    if namecol not in df:return
    seasons=df[['season','team',namecol]].dropna().drop_duplicates().sort_values(['season','team'])
    seen={}; rows=[]
    for _,r in seasons.iterrows():
        nm=str(r[namecol]); rows.append((r.season,r.team,nm,seen.get(nm,0))); seen[nm]=seen.get(nm,0)+1
    z=pd.DataFrame(rows,columns=['season','team',namecol,outcol]); df2=df.merge(z,on=['season','team',namecol],how='left')
    for c in df2.columns: df[c]=df2[c]

for name,prefix in [('head_coach','hc'),('offensive_coordinator','oc'),('defensive_coordinator','dc')]:
    if name in d.columns:
        tenure_by_name(d,name,prefix+'_team_tenure_seasons')
        career_prior_by_name(d,name,prefix+'_prior_role_seasons')
if all(c in d for c in ['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed']):
    h=num(d.head_coach_changed); o=num(d.offensive_coordinator_changed); dc=num(d.defensive_coordinator_changed)
    d['staff_changes_count']=h.fillna(0)+o.fillna(0)+dc.fillna(0)
    d['full_staff_stable']=((h==0)&(o==0)&(dc==0)).astype(float)
    d['staff_overhaul_2plus']=(d.staff_changes_count>=2).astype(float)
if 'offensive_coordinator_changed' in d and 'qb_prior_starts' in d:
    d['new_oc_young_qb']=((num(d.offensive_coordinator_changed)==1)&(num(d.qb_prior_starts)<=16)).astype(float)
if 'defensive_coordinator_changed' in d and 'returning_defense_snap_share' in d:
    d['new_dc_low_def_continuity']=((num(d.defensive_coordinator_changed)==1)&(num(d.returning_defense_snap_share)<.65)).astype(float)
if 'staff_overhaul_2plus' in d and 'fatigue_load_index' in d:
    d['staff_overhaul_high_fatigue']=((num(d.staff_overhaul_2plus)==1)&(num(d.fatigue_load_index)>=1.5)).astype(float)

# Candidate pregame features only.
features=[]
explicit={'elo_edge','rest_edge','rest_days','rest_days_edge','qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','coordinator_changes','major_staff_changes','staff_changes_count','full_staff_stable','staff_overhaul_2plus','new_oc_young_qb','new_dc_low_def_continuity','staff_overhaul_high_fatigue','hc_team_tenure_seasons','oc_team_tenure_seasons','dc_team_tenure_seasons','hc_prior_role_seasons','oc_prior_role_seasons','dc_prior_role_seasons','market_prob_use'}
for c in d.columns:
    lc=c.lower()
    if c.startswith(('rank_edge_','recent_edge_','adv_')) or c in explicit or any(k in lc for k in ['travel_miles','road_games','road_miles','tz_hours','fatigue_load','short_week','long_trip','extra_rest']):
        if any(x in lc for x in ['profit','result','final_score','points_for','points_against','completed','win','push']):continue
        try:z=num(d[c])
        except:continue
        if z.notna().sum()>=180 and z.nunique(dropna=True)>=2:features.append(c)
features=list(dict.fromkeys(features))
print('features',len(features))

tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}
price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)}
venues=['ANY','AWAY','HOME']
def era(track,name):lo,hi=tracks[track][name];return d.season.between(lo,hi)
def apply_rule(feature,op,q):
    z=num(d[feature]); return z>=q if op=='>=' else z<=q

def base_context(pb,venue):
    lo,hi=price_bands[pb]; m=num(d.market_prob_use).between(lo,hi,inclusive='both')
    if venue=='AWAY':m &= num(d.is_home).eq(0)
    if venue=='HOME':m &= num(d.is_home).eq(1)
    return m

survivors=[]
for track in tracks:
  for pb in price_bands:
    for venue in venues:
      base=base_context(pb,venue)
      # stage 1: pre-holdout univariates
      uni=[]
      train=d[base&era(track,'train')]
      for c in features:
        vals=num(train[c]).dropna()
        if len(vals)<50:continue
        for q in sorted(set(float(x) for x in vals.quantile([.15,.25,.35,.65,.75,.85]).dropna())):
          for op in ['<=','>=']:
            m=base&apply_rule(c,op,q)
            mt=metrics(d[m&era(track,'train')]); mv=metrics(d[m&era(track,'validation')])
            if not mt or not mv or mt['n']<35 or mv['n']<22:continue
            if mt['roi']<.06 or mv['roi']<.06:continue
            if mt['positive_seasons']<max(2,math.ceil(mt['active_seasons']*.55)):continue
            if mv['positive_seasons']<max(2,math.ceil(mv['active_seasons']*.55)):continue
            score=mt['roi']+mv['roi']+.15*min(mt['n'],mv['n'])/100
            uni.append((score,c,op,q,sem(c),m,mt,mv))
      uni=sorted(uni,reverse=True,key=lambda x:x[0])
      # de-duplicate per semantic; keep up to 4 each / 24 total
      kept=[]; counts={}
      for u in uni:
        if counts.get(u[4],0)>=4:continue
        kept.append(u); counts[u[4]]=counts.get(u[4],0)+1
        if len(kept)>=24:break
      # stage 2 pairs across distinct domains
      pairs=[]
      for i,a in enumerate(kept):
        for b in kept[i+1:]:
          if a[4]==b[4]:continue
          m=a[5]&b[5]
          mt=metrics(d[m&era(track,'train')]); mv=metrics(d[m&era(track,'validation')])
          if not mt or not mv or mt['n']<30 or mv['n']<18:continue
          if mt['roi']<.10 or mv['roi']<.10:continue
          score=mt['roi']+mv['roi']+.20*min(mt['n'],mv['n'])/100
          pairs.append((score,a,b,m,mt,mv))
      pairs=sorted(pairs,reverse=True,key=lambda x:x[0])[:80]
      # stage 3 selected triples using a third distinct domain
      frozen=[]
      for p in pairs:
        frozen.append(('PAIR',p[0],[p[1],p[2]],p[3],p[4],p[5]))
      for p in pairs[:25]:
        used={p[1][4],p[2][4]}
        for c in kept:
          if c[4] in used:continue
          m=p[3]&c[5]
          mt=metrics(d[m&era(track,'train')]); mv=metrics(d[m&era(track,'validation')])
          if not mt or not mv or mt['n']<24 or mv['n']<15:continue
          if mt['roi']<.12 or mv['roi']<.12:continue
          score=mt['roi']+mv['roi']+.18*min(mt['n'],mv['n'])/100
          frozen.append(('TRIPLE',score,[p[1],p[2],c],m,mt,mv))
      frozen=sorted(frozen,reverse=True,key=lambda x:x[1])[:100]
      # open holdout only after freeze
      for typ,score,parts,m,mt,mv in frozen:
        mh=metrics(d[m&era(track,'holdout')]); mf=metrics(d[m])
        if not mh or not mf or mh['n']<15 or mh['roi']<.10:continue
        if mf['n']<70:continue
        sig='+'.join(sorted(set(x[4] for x in parts)))
        row={'track':track,'type':typ,'price_band':pb,'venue':venue,'semantic_signature':sig,'pre_score':score,
             'train_n':mt['n'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_roi':mv['roi'],
             'holdout_n':mh['n'],'holdout_roi':mh['roi'],'full_n':mf['n'],'full_roi':mf['roi'],
             'full_positive_seasons':mf['positive_seasons'],'full_active_seasons':mf['active_seasons']}
        for j,x in enumerate(parts,1):
            row[f'feature{j}']=x[1]; row[f'op{j}']=x[2]; row[f'threshold{j}']=x[3]
        survivors.append(row)

res=pd.DataFrame(survivors)
if len(res):
    res['season_ratio']=res.full_positive_seasons/res.full_active_seasons.replace(0,np.nan)
    res=res.sort_values(['holdout_roi','full_n'],ascending=[False,False])
    # canonicalize near-duplicate concepts: keep strongest 3 per semantic+market+venue+type
    res['bucket']=res.type+'|'+res.semantic_signature+'|'+res.price_band+'|'+res.venue
    res=res.groupby('bucket',group_keys=False).head(3).drop(columns='bucket').reset_index(drop=True)
    res.insert(0,'next_method_id',[f'NFL-NW{i+1:03d}' for i in range(len(res))])
res.to_csv(OUT/'next_wave_survivors.csv',index=False)
summary={'features_tested':len(features),'survivors':int(len(res)),'pair_survivors':int((res.type=='PAIR').sum()) if len(res) else 0,'triple_survivors':int((res.type=='TRIPLE').sum()) if len(res) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL NEXT-WAVE CROSS-DOMAIN COMBINATION SEARCH',json.dumps(summary,indent=2),'']
if len(res):
  for _,r in res.head(50).iterrows():
    parts=[]
    for j in [1,2,3]:
      if pd.notna(r.get(f'feature{j}')):parts.append(f"{r[f'feature{j}']} {r[f'op{j}']} {r[f'threshold{j}']:.5g}")
    lines.append(f"{r.next_method_id} {r.type} {r.price_band} {r.venue} [{r.semantic_signature}] | {' AND '.join(parts)} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% frozen={100*r.holdout_roi:+.1f}% seasons={int(r.full_positive_seasons)}/{int(r.full_active_seasons)}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
