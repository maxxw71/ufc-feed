from pathlib import Path
import os,json,itertools
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
R=ROOT/'research_v2'
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'calendar_schedule_runheavy_discovery';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def met(x):
    if not len(x):return {'n':0,'wins':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}
def seas_ratio(x):
    if not len(x):return np.nan
    y=x.groupby('season').profit.sum();return float((y>0).mean()) if len(y) else np.nan

d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES)
d['win']=num(d.win);d['moneyline']=num(d.moneyline);d=d[d.win.isin([0,1])&d.moneyline.notna()].copy();d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)

s=pd.read_parquet(SCHED).copy();gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy()
for c in ['home_team','away_team']:
    s[c]=s[c].replace(ALIASES)
s['date']=pd.to_datetime(s.gameday,errors='coerce') if 'gameday' in s else pd.NaT
if 'weekday' not in s:s['weekday']=s.date.dt.day_name()
s['month']=s.date.dt.month
if 'gametime' in s:
    s['hour']=pd.to_numeric(s.gametime.astype(str).str.split(':').str[0],errors='coerce')
else:s['hour']=np.nan

# side schedule context and road streaks
sides=[]
for side in ['home','away']:
    z=s[['game_id','season','week','date','weekday','month','hour','home_team','away_team']+[c for c in ['temp','roof','surface'] if c in s.columns]].copy()
    if side=='home':z['team']=z.home_team;z['opponent']=z.away_team;z['side_is_home']=1
    else:z['team']=z.away_team;z['opponent']=z.home_team;z['side_is_home']=0
    sides.append(z)
ss=pd.concat(sides,ignore_index=True).sort_values(['season','team','week','date','game_id'])
ctx=[]
for (season,team),g in ss.groupby(['season','team'],sort=False):
    prev=[]
    for _,r in g.iterrows():
        away=1-int(r.side_is_home);prior_streak=0
        for v in reversed(prev):
            if v==1:prior_streak+=1
            else:break
        ctx.append({'game_id':r.game_id,'team':team,'cal_month':r.month,'cal_monday':int(str(r.weekday).lower().startswith('mon')),'cal_thursday':int(str(r.weekday).lower().startswith('thu')),'cal_saturday':int(str(r.weekday).lower().startswith('sat')),'cal_primetime':int(pd.notna(r.hour) and r.hour>=19),'cal_nov_or_later':int(pd.notna(r.month) and r.month in [11,12,1]),'cal_dec_or_jan':int(pd.notna(r.month) and r.month in [12,1]),'road_prior_streak':prior_streak,'road_streak_including_current':prior_streak+1 if away else 0,'third_straight_road':int(away and prior_streak>=2),'fourth_straight_road':int(away and prior_streak>=3),'road_games_prev3':sum(prev[-3:]),'road_games_prev5':sum(prev[-5:]),'cal_temp':r.get('temp',np.nan),'cal_roof':r.get('roof',None),'cal_surface':r.get('surface',None)})
        prev.append(away)
ctx=pd.DataFrame(ctx);d=d.merge(ctx,on=['game_id','team'],how='left')
# use existing temp/roof if schedule copy did not have them
if d.cal_temp.isna().all() and 'temp' in d:d['cal_temp']=num(d.temp)
roof=d.cal_roof.astype(str).str.lower() if 'cal_roof' in d else pd.Series('',index=d.index)
outdoor=~roof.str.contains('dome|closed|indoor',regex=True)
d['cold40_outdoor']=((num(d.cal_temp)<=40)&outdoor).astype(int);d['freezing32_outdoor']=((num(d.cal_temp)<=32)&outdoor).astype(int)
d['late_cold40']=((num(d.cal_temp)<=40)&outdoor&num(d.cal_nov_or_later).eq(1)).astype(int);d['late_freezing32']=((num(d.cal_temp)<=32)&outdoor&num(d.cal_nov_or_later).eq(1)).astype(int)

# Pregame run-rate from weekly team stats. Strictly prior weeks only.
rr=[]
for season in range(2006,2026):
    paths=[R/f'data/stats_team/stats_team_week_{season}.parquet',ROOT/f'data/stats_team/stats_team_week_{season}.parquet']
    p=next((x for x in paths if x.exists()),None)
    if p is None:continue
    try:t=pd.read_parquet(p,columns=['season','week','season_type','team','carries','attempts','sacks_suffered'])
    except Exception:continue
    t=t[(t.season==season)&t.season_type.eq('REG')].copy();t.team=t.team.replace(ALIASES);t['plays']=num(t.carries)+num(t.attempts)+num(t.sacks_suffered)
    for team,g in t.sort_values('week').groupby('team'):
        g=g.groupby('week',as_index=False).agg(carries=('carries','sum'),plays=('plays','sum')).sort_values('week');pc=0.;pp=0.;hist=[]
        for _,r in g.iterrows():
            r3=hist[-3:];rc=sum(v[0] for v in r3);rp=sum(v[1] for v in r3)
            rr.append({'season':season,'week':int(r.week),'team':team,'pre_run_rate':pc/pp if pp>0 else np.nan,'recent3_run_rate':rc/rp if rp>0 else np.nan})
            pc+=float(r.carries);pp+=float(r.plays);hist.append((float(r.carries),float(r.plays)))
run=pd.DataFrame(rr)
if len(run):
    d=d.merge(run,on=['season','week','team'],how='left')
    opp=run.rename(columns={'team':'opponent','pre_run_rate':'opp_pre_run_rate','recent3_run_rate':'opp_recent3_run_rate'})
    d=d.merge(opp,on=['season','week','opponent'],how='left');d['run_rate_edge']=num(d.pre_run_rate)-num(d.opp_pre_run_rate);d['recent3_run_rate_edge']=num(d.recent3_run_rate)-num(d.opp_recent3_run_rate)

# Concept feature catalog. Weather remains research-only even if it survives.
features={
 'CALENDAR':['cal_monday','cal_thursday','cal_saturday','cal_primetime','cal_nov_or_later','cal_dec_or_jan'],
 'ROAD_STREAK':['road_prior_streak','road_streak_including_current','third_straight_road','fourth_straight_road','road_games_prev3','road_games_prev5'],
 'COLD_WEATHER':['cold40_outdoor','freezing32_outdoor','late_cold40','late_freezing32','cal_temp'],
 'RUN_HEAVY':['pre_run_rate','recent3_run_rate','run_rate_edge','recent3_run_rate_edge','opp_pre_run_rate','opp_recent3_run_rate']}
new_features=[c for v in features.values() for c in v if c in d.columns and num(d[c]).notna().sum()>=150]
concept={c:k for k,v in features.items() for c in v}
# normal football metrics for interactions; no weather/temp duplicated
football=[c for c in d.columns if (c.startswith('rank_edge_') or c.startswith('recent_edge_') or c in ['qb_prior_starts','adv_prior_road_miles_last5','fatigue_load_index']) and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=500]
football=football[:100]
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}
bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)}
venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}
def cmask(x,c,op,t):
    v=num(x[c]);return v>=t if op=='>=' else v<=t
def filt(x,conds):
    m=pd.Series(True,index=x.index)
    for c,op,t in conds:m&=cmask(x,c,op,t)
    return x[m]
def era(x,a,b):return x[x.season.between(a,b)]

rows=[]
for track,(tr,va,ho) in tracks.items():
  for band,(lo,hi) in bands.items():
    for venue,vfn in venues.items():
      base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)].copy();train=era(base,*tr);val=era(base,*va);hold=era(base,*ho)
      if len(train)<100 or len(val)<50 or len(hold)<50:continue
      nc=[]
      for c in new_features:
        v=num(train[c]).dropna();
        if len(v)<60:continue
        vals=[]
        if set(v.dropna().unique()).issubset({0,1}): vals=[(1.0,'>=')]
        elif c=='cal_temp': vals=[(32.,'<='),(40.,'<='),(50.,'<=')]
        elif 'road_streak' in c: vals=[(2.,'>='),(3.,'>=')]
        elif c.startswith('road_games'): vals=[(2.,'>='),(3.,'>=')]
        else:
            vals=[(float(v.quantile(q)),op) for q in [.25,.5,.75] for op in ['>=','<=']]
        for t,op in vals:
            a=filt(train,[(c,op,t)]);b=filt(val,[(c,op,t)]);ma,mb=met(a),met(b)
            if ma['n']>=20 and mb['n']>=12 and ma['roi']>=0 and mb['roi']>=0:nc.append((c,op,t,ma['roi']+mb['roi']))
      nc=sorted(nc,key=lambda z:z[3],reverse=True)[:45]
      fc=[]
      for c in football:
        v=num(train[c]).dropna();
        if len(v)<100:continue
        for q in [.25,.5,.75]:
          t=float(v.quantile(q))
          for op in ['>=','<=']:
            a=filt(train,[(c,op,t)]);b=filt(val,[(c,op,t)]);ma,mb=met(a),met(b)
            if ma['n']>=30 and mb['n']>=18 and ma['roi']>=.03 and mb['roi']>=.03:fc.append((c,op,t,ma['roi']+mb['roi']))
      fc=sorted(fc,key=lambda z:z[3],reverse=True)[:30]
      cand=[[(c,op,t)] for c,op,t,_ in nc]
      for a in nc[:30]:
        for b in fc[:25]:
            if a[0]!=b[0]:cand.append([(a[0],a[1],a[2]),(b[0],b[1],b[2])])
      seen=set()
      for cs in cand:
        key=json.dumps(cs,sort_keys=True)
        if key in seen:continue
        seen.add(key);a,b,h,full=filt(train,cs),filt(val,cs),filt(hold,cs),filt(base,cs);ma,mb,mh,mf=met(a),met(b),met(h),met(full)
        if ma['n']<18 or mb['n']<12 or mh['n']<15 or mf['n']<70:continue
        if ma['roi']<.06 or mb['roi']<.06 or mh['roi']<.08 or mf['roi']<.08:continue
        pos=seas_ratio(full);older=(ma['roi']+mb['roi'])/2;newc=cs[0][0];family=concept.get(newc,'OTHER')
        rows.append({'track':track,'price_band':band,'venue':venue,'concept':family,'conditions':json.dumps(cs),'full_n':mf['n'],'full_wins':mf['wins'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],'train_n':ma['n'],'train_roi':ma['roi'],'validation_n':mb['n'],'validation_roi':mb['roi'],'holdout_n':mh['n'],'holdout_roi':mh['roi'],'positive_season_ratio':pos,'recent_delta':mh['roi']-older,'weather_research_only':family=='COLD_WEATHER'})
res=pd.DataFrame(rows)
if len(res):
    res['preferred']=(res.full_n>=90)&(res.full_roi>=.10)&(res.train_roi>=.08)&(res.validation_roi>=.08)&(res.holdout_roi>=.10)&(res.positive_season_ratio>=.70)&(res.recent_delta>=0)
    res['score']=res.holdout_roi*.30+res.full_roi*.22+res.positive_season_ratio*.18+res.recent_delta.clip(-.2,.2)*.15+res.full_win_pct*.10+np.log10(res.full_n)*.025
    res=res.sort_values(['preferred','score'],ascending=[False,False]).drop_duplicates(['track','price_band','venue','conditions'])
    res.to_csv(OUT/'methods.csv',index=False);res[res.preferred].to_csv(OUT/'preferred.csv',index=False)
summary={'rows':int(len(d)),'new_feature_coverage':{c:int(num(d[c]).notna().sum()) for c in new_features},'run_rate_rows':int(num(d.get('pre_run_rate',pd.Series(dtype=float))).notna().sum()) if 'pre_run_rate' in d else 0,'temperature_rows':int(num(d.cal_temp).notna().sum()),'survivors':int(len(res)) if len(rows) else 0,'preferred':int(res.preferred.sum()) if len(rows) else 0,'preferred_by_concept':res[res.preferred].concept.value_counts().to_dict() if len(rows) else {}}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL CALENDAR / SCHEDULE / ROAD-STREAK / COLD / RUN-HEAVY DISCOVERY','',json.dumps(summary,indent=2),'','TOP PREFERRED']
if len(res):
    for i,(_,r) in enumerate(res[res.preferred].head(30).iterrows(),1):lines.append(f"NS{i:03d} {r.concept} {r.track} {r.price_band} {r.venue} | {int(r.full_wins)}-{int(r.full_n-r.full_wins)} ({100*r.full_win_pct:.1f}%) n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% recent_delta={100*r.recent_delta:+.1f}pp weather_only={r.weather_research_only} | {r.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:80]))
