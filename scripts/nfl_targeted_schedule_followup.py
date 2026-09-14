from pathlib import Path
import os,json
import numpy as np
import pandas as pd
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1');R=ROOT/'research_v2';CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'targeted_schedule_followup';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet';SCHED=ROOT/'data/raw/schedules_2006_2026.parquet';ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}
def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def met(x):
    if not len(x):return {'n':0,'wins':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}
def sr(x):
    if not len(x):return np.nan
    y=x.groupby('season').profit.sum();return float((y>0).mean())
d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES);d['win']=num(d.win);d=d[d.win.isin([0,1])&num(d.moneyline).notna()].copy();d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy();s.home_team=s.home_team.replace(ALIASES);s.away_team=s.away_team.replace(ALIASES);s['date']=pd.to_datetime(s.gameday,errors='coerce');s['month']=s.date.dt.month
ss=[]
for home in [1,0]:
    z=s[['game_id','season','week','date','month','home_team','away_team']+[c for c in ['temp','roof'] if c in s.columns]].copy();z['team']=z.home_team if home else z.away_team;z['opp']=z.away_team if home else z.home_team;z['away']=1-home;ss.append(z)
ss=pd.concat(ss).sort_values(['season','team','week','date','game_id']);rows=[]
for (season,team),g in ss.groupby(['season','team']):
    prev=[]
    for _,r in g.iterrows():
        streak=0
        for a in reversed(prev):
            if a:streak+=1
            else:break
        rows.append({'game_id':r.game_id,'team':team,'road3_current':int(r.away and streak>=2),'road4_current':int(r.away and streak>=3),'road_after2_current':int(r.away and streak>=1),'home_after3road':int((not r.away) and streak>=3),'road_prev5_ge3':int(sum(prev[-5:])>=3),'sched_temp':r.get('temp',np.nan),'sched_roof':r.get('roof',None),'sched_month':r.month});prev.append(bool(r.away))
ctx=pd.DataFrame(rows);d=d.merge(ctx,on=['game_id','team'],how='left');roof=d.sched_roof.astype(str).str.lower();known=num(d.sched_temp).notna();outdoor=~roof.str.contains('dome|closed|indoor',regex=True)
d['late_cold40_known']=((num(d.sched_temp)<=40)&known&outdoor&num(d.sched_month).isin([11,12,1])).astype(int);d['late_freezing_known']=((num(d.sched_temp)<=32)&known&outdoor&num(d.sched_month).isin([11,12,1])).astype(int)
# run-rate strictly prior week
rr=[]
for season in range(2006,2026):
    p=next((x for x in [R/f'data/stats_team/stats_team_week_{season}.parquet',ROOT/f'data/stats_team/stats_team_week_{season}.parquet'] if x.exists()),None)
    if not p:continue
    try:t=pd.read_parquet(p,columns=['season','week','season_type','team','carries','attempts','sacks_suffered'])
    except:continue
    t=t[(t.season==season)&t.season_type.eq('REG')];t.team=t.team.replace(ALIASES);t['plays']=num(t.carries)+num(t.attempts)+num(t.sacks_suffered)
    for team,g in t.groupby('team'):
        g=g.groupby('week',as_index=False).agg(carries=('carries','sum'),plays=('plays','sum')).sort_values('week');hist=[];pc=pp=0.
        for _,r in g.iterrows():
            r3=hist[-3:];rc=sum(a for a,b in r3);rp=sum(b for a,b in r3);rr.append({'season':season,'week':int(r.week),'team':team,'pre_run_rate2':pc/pp if pp else np.nan,'recent3_run_rate2':rc/rp if rp else np.nan});pc+=float(r.carries);pp+=float(r.plays);hist.append((float(r.carries),float(r.plays)))
run=pd.DataFrame(rr);d=d.merge(run,on=['season','week','team'],how='left')
# Target concepts only; combine each with broad pregame football metrics.
concepts={'ROAD3':['road3_current','road4_current','home_after3road','road_prev5_ge3'],'COLD':['late_cold40_known','late_freezing_known'],'RUN':['pre_run_rate2','recent3_run_rate2']}
football=[c for c in d.columns if (c.startswith('rank_edge_') or c.startswith('recent_edge_')) and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=500][:100]
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))};bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)};venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}
def mask(x,c,op,t):v=num(x[c]);return v>=t if op=='>=' else v<=t
def fil(x,cs):
    m=pd.Series(True,index=x.index)
    for c,o,t in cs:m&=mask(x,c,o,t)
    return x[m]
res=[]
for track,(tr,va,ho) in tracks.items():
 for band,(lo,hi) in bands.items():
  for venue,vfn in venues.items():
   base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)];train=base[base.season.between(*tr)];val=base[base.season.between(*va)];hold=base[base.season.between(*ho)]
   if min(len(train),len(val),len(hold))<50:continue
   new=[]
   for fam,cols in concepts.items():
    for c in cols:
     if c not in d:continue
     if fam in ['ROAD3','COLD']:vals=[('>=',1.)]
     else:
      v=num(train[c]).dropna();vals=[('>=',float(v.quantile(q))) for q in [.5,.65,.75]]+[('<=',float(v.quantile(q))) for q in [.25,.35,.5]] if len(v)>=60 else []
     for op,t in vals:
      a,b=fil(train,[(c,op,t)]),fil(val,[(c,op,t)]);ma,mb=met(a),met(b)
      if ma['n']>=12 and mb['n']>=8 and ma['roi']>=0 and mb['roi']>=0:new.append((fam,c,op,t,ma['roi']+mb['roi']))
   fc=[]
   for c in football:
    v=num(train[c]).dropna()
    if len(v)<100:continue
    for q in [.25,.5,.75]:
     t=float(v.quantile(q))
     for op in ['>=','<=']:
      a,b=fil(train,[(c,op,t)]),fil(val,[(c,op,t)]);ma,mb=met(a),met(b)
      if ma['n']>=25 and mb['n']>=15 and ma['roi']>=.03 and mb['roi']>=.03:fc.append((c,op,t,ma['roi']+mb['roi']))
   fc=sorted(fc,key=lambda z:z[3],reverse=True)[:25];new=sorted(new,key=lambda z:z[4],reverse=True)[:35]
   cand=[]
   for fam,c,o,t,_ in new:cand.append((fam,[(c,o,t)]))
   for fam,c,o,t,_ in new:
    for f,fo,ft,_ in fc:
     if f!=c:cand.append((fam,[(c,o,t),(f,fo,ft)]))
   seen=set()
   for fam,cs in cand:
    k=json.dumps(cs)
    if k in seen:continue
    seen.add(k);a,b,h,full=fil(train,cs),fil(val,cs),fil(hold,cs),fil(base,cs);ma,mb,mh,mf=met(a),met(b),met(h),met(full)
    minfull=45 if fam in ['ROAD3','COLD'] else 70
    if ma['n']<10 or mb['n']<7 or mh['n']<10 or mf['n']<minfull:continue
    if min(ma['roi'],mb['roi'])<.05 or mh['roi']<.08 or mf['roi']<.08:continue
    pos=sr(full);recent=mh['roi']-(ma['roi']+mb['roi'])/2
    res.append({'track':track,'band':band,'venue':venue,'concept':fam,'conditions':json.dumps(cs),'n':mf['n'],'wins':mf['wins'],'win_pct':mf['win_pct'],'roi':mf['roi'],'train_n':ma['n'],'train_roi':ma['roi'],'val_n':mb['n'],'val_roi':mb['roi'],'hold_n':mh['n'],'hold_roi':mh['roi'],'positive_season_ratio':pos,'recent_delta':recent})
r=pd.DataFrame(res)
if len(r):
 r['preferred']=(r.n>=60)&(r.roi>=.10)&(r.hold_roi>=.10)&(r.positive_season_ratio>=.65)&(r.recent_delta>=-.02);r['score']=r.hold_roi*.32+r.roi*.25+r.positive_season_ratio*.2+r.recent_delta.clip(-.2,.2)*.13+r.win_pct*.1;r=r.sort_values(['preferred','score'],ascending=False).drop_duplicates(['track','band','venue','conditions']);r.to_csv(OUT/'methods.csv',index=False);r[r.preferred].to_csv(OUT/'preferred.csv',index=False)
summary={'road3_rows':int(d.road3_current.sum()),'road4_rows':int(d.road4_current.sum()),'late_cold40_rows':int(d.late_cold40_known.sum()),'late_freezing_rows':int(d.late_freezing_known.sum()),'survivors':int(len(r)) if len(res) else 0,'preferred':int(r.preferred.sum()) if len(res) else 0,'preferred_by_concept':r[r.preferred].concept.value_counts().to_dict() if len(res) else {}}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));lines=['TARGETED ROAD3 / COLD / RUN-HEAVY FOLLOW-UP','',json.dumps(summary,indent=2),'','TOP PREFERRED']
if len(r):
 for i,(_,x) in enumerate(r[r.preferred].head(25).iterrows(),1):lines.append(f"TF{i:03d} {x.concept} {x.track} {x.band} {x.venue} | {int(x.wins)}-{int(x.n-x.wins)} ({100*x.win_pct:.1f}%) n={int(x.n)} ROI={100*x.roi:+.1f}% train={100*x.train_roi:+.1f}% val={100*x.val_roi:+.1f}% hold={100*x.hold_roi:+.1f}% pos={100*x.positive_season_ratio:.0f}% recent={100*x.recent_delta:+.1f}pp | {x.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:60]))
