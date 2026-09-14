from pathlib import Path
import os,json
import numpy as np,pandas as pd
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1');CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'unique_situational_discovery';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet';SCHED=ROOT/'data/raw/schedules_2006_2026.parquet';ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}
def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(w,ml):ml=num(ml);return np.where(num(w).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.)
def met(x):
 if not len(x):return {'n':0,'wins':0,'win_pct':np.nan,'roi':np.nan}
 return {'n':len(x),'wins':int(x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean())}
def sr(x):
 if not len(x):return np.nan
 y=x.groupby('season').profit.sum();return float((y>0).mean()) if len(y) else np.nan
def cmask(x,c,o,t):
 v=num(x[c]);return v>=t if o=='>=' else v<=t
def filt(x,cs):
 m=pd.Series(True,index=x.index)
 for c,o,t in cs:m&=cmask(x,c,o,t)
 return x[m]
d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES);d['win']=num(d.win);d=d[d.win.isin([0,1])&num(d.moneyline).notna()].copy();d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy();s.home_team=s.home_team.replace(ALIASES);s.away_team=s.away_team.replace(ALIASES);s['date']=pd.to_datetime(s.gameday,errors='coerce')
# Build pregame-safe schedule context: rest, rematch, revenge, home/away sequence.
ss=[]
for home in [1,0]:
 z=s[['game_id','season','week','date','home_team','away_team']+[c for c in ['home_score','away_score','div_game'] if c in s.columns]].copy();z['team']=z.home_team if home else z.away_team;z['opponent']=z.away_team if home else z.home_team;z['is_home2']=home
 if 'home_score' in z:
  z['team_score']=num(z.home_score) if home else num(z.away_score);z['opp_score']=num(z.away_score) if home else num(z.home_score)
 ss.append(z)
ss=pd.concat(ss).sort_values(['season','team','date','week','game_id']);rows=[]
for (season,team),g in ss.groupby(['season','team'],sort=False):
 prev_date=None;opp_hist={};loc=[]
 for _,r in g.iterrows():
  rest=(r.date-prev_date).days if prev_date is not None and pd.notna(r.date) else np.nan;hist=opp_hist.get(r.opponent,[]);prior_meet=len(hist);prior_lost=bool(hist and hist[-1][0]<hist[-1][1]);prior_won=bool(hist and hist[-1][0]>hist[-1][1])
  rows.append({'game_id':r.game_id,'team':team,'rest_days_derived':rest,'short_rest6':int(pd.notna(rest) and rest<=6),'short_rest5':int(pd.notna(rest) and rest<=5),'long_rest9':int(pd.notna(rest) and rest>=9),'post_bye12':int(pd.notna(rest) and rest>=12),'same_opp_prior_meetings':prior_meet,'second_meeting':int(prior_meet>=1),'revenge_after_loss':int(prior_lost),'repeat_after_win':int(prior_won),'prev2_home_games':sum(loc[-2:]),'prev3_home_games':sum(loc[-3:])})
  if pd.notna(r.get('team_score',np.nan)):opp_hist.setdefault(r.opponent,[]).append((float(r.team_score),float(r.opp_score)))
  prev_date=r.date;loc.append(int(r.is_home2))
ctx=pd.DataFrame(rows);d=d.merge(ctx,on=['game_id','team'],how='left')
# opponent context to get rest disadvantage/advantage
oppctx=ctx.rename(columns={'team':'opponent',**{c:'opp_'+c for c in ctx.columns if c not in ['game_id','team']}});d=d.merge(oppctx,on=['game_id','opponent'],how='left');d['rest_edge_days']=num(d.rest_days_derived)-num(d.opp_rest_days_derived);d['rest_disadv3']=(d.rest_edge_days<=-3).astype(int);d['rest_adv3']=(d.rest_edge_days>=3).astype(int)
# Unique feature families. Use only columns actually present and with coverage.
families={
 'REST':['short_rest6','short_rest5','long_rest9','post_bye12','rest_edge_days','rest_disadv3','rest_adv3','opp_post_bye12'],
 'REMATCH':['second_meeting','same_opp_prior_meetings','revenge_after_loss','repeat_after_win'],
 'QB_CONTINUITY':['qb_prior_starts','qb_changed','qb_changed_last_game','returning_offense_share','returning_ol_share','returning_skill_share'],
 'MARKET':['base_vs_open_prob_move','open_to_current_prob_move','market_prob_move','line_move_prob'],
 'COACH_TRANSITION':['coachq_staff_upgrade_count','coachq_staff_downgrade_count','coachq_hc_quality_delta_vs_departed','coachq_oc_quality_delta_vs_departed','coachq_dc_quality_delta_vs_departed','adv_staff_quality_mean']
}
features=[];family={}
for fam,cols in families.items():
 for c in cols:
  if c in d and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=300:features.append(c);family[c]=fam
# matchup interaction library excludes coaching to keep discovery distinct unless concept itself is coaching transition.
football=[c for c in d.columns if (c.startswith('rank_edge_') or c.startswith('recent_edge_') or c in ['fatigue_load_index','adv_prior_road_miles_last5']) and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=600][:100]
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))};bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)};venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}
res=[]
for track,(tr,va,ho) in tracks.items():
 for band,(lo,hi) in bands.items():
  for venue,vfn in venues.items():
   base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)].copy();train=base[base.season.between(*tr)];val=base[base.season.between(*va)];hold=base[base.season.between(*ho)]
   if min(len(train),len(val),len(hold))<60:continue
   new=[]
   for c in features:
    v=num(train[c]).dropna();
    if len(v)<60:continue
    uniq=set(v.unique());vals=[]
    if uniq.issubset({0,1}):vals=[('>=',1.)]
    elif c in ['same_opp_prior_meetings']:vals=[('>=',1.)]
    elif c=='rest_edge_days':vals=[('>=',3.),('<=',-3.),('>=',5.),('<=',-5.)]
    elif 'prior_starts' in c:vals=[('>=',16.),('>=',32.),('<=',8.),('<=',16.)]
    else:vals=[(o,float(v.quantile(q))) for q in [.25,.5,.75] for o in ['>=','<=']]
    for o,t in vals:
     a,b=filt(train,[(c,o,t)]),filt(val,[(c,o,t)]);ma,mb=met(a),met(b)
     if ma['n']>=20 and mb['n']>=12 and ma['roi']>=.02 and mb['roi']>=.02:new.append((c,o,t,ma['roi']+mb['roi']))
   new=sorted(new,key=lambda z:z[3],reverse=True)[:45]
   fc=[]
   for c in football:
    v=num(train[c]).dropna();
    if len(v)<100:continue
    for q in [.25,.5,.75]:
     t=float(v.quantile(q))
     for o in ['>=','<=']:
      a,b=filt(train,[(c,o,t)]),filt(val,[(c,o,t)]);ma,mb=met(a),met(b)
      if ma['n']>=30 and mb['n']>=18 and ma['roi']>=.03 and mb['roi']>=.03:fc.append((c,o,t,ma['roi']+mb['roi']))
   fc=sorted(fc,key=lambda z:z[3],reverse=True)[:30]
   cand=[]
   for c,o,t,_ in new:cand.append([[c,o,t]])
   for c,o,t,_ in new[:30]:
    for f,fo,ft,_ in fc[:25]:
     if c!=f:cand.append([[c,o,t],[f,fo,ft]])
   seen=set()
   for cs in cand:
    k=json.dumps(cs)
    if k in seen:continue
    seen.add(k);a,b,h,full=filt(train,cs),filt(val,cs),filt(hold,cs),filt(base,cs);ma,mb,mh,mf=met(a),met(b),met(h),met(full)
    if ma['n']<18 or mb['n']<12 or mh['n']<15 or mf['n']<70:continue
    if min(ma['roi'],mb['roi'])<.06 or mh['roi']<.08 or mf['roi']<.08:continue
    pos=sr(full);recent=mh['roi']-(ma['roi']+mb['roi'])/2;fam=family.get(cs[0][0],'OTHER')
    res.append({'track':track,'band':band,'venue':venue,'concept':fam,'conditions':json.dumps(cs),'n':mf['n'],'wins':mf['wins'],'win_pct':mf['win_pct'],'roi':mf['roi'],'train_n':ma['n'],'train_roi':ma['roi'],'val_n':mb['n'],'val_roi':mb['roi'],'hold_n':mh['n'],'hold_roi':mh['roi'],'positive_season_ratio':pos,'recent_delta':recent})
r=pd.DataFrame(res)
if len(r):
 r['preferred']=(r.n>=90)&(r.roi>=.10)&(r.train_roi>=.08)&(r.val_roi>=.08)&(r.hold_roi>=.10)&(r.positive_season_ratio>=.70)&(r.recent_delta>=-.01);r['score']=r.hold_roi*.30+r.roi*.23+r.positive_season_ratio*.18+r.recent_delta.clip(-.2,.2)*.13+r.win_pct*.1+np.log10(r.n)*.03;r=r.sort_values(['preferred','score'],ascending=False);r['signature']=r.conditions.map(lambda s:'|'.join(sorted(x[0] for x in json.loads(s))));r=r.drop_duplicates(['track','band','venue','signature']);r.to_csv(OUT/'methods.csv',index=False);r[r.preferred].to_csv(OUT/'preferred.csv',index=False)
summary={'feature_families':{k:[c for c in v if c in features] for k,v in families.items()},'survivors':int(len(r)) if len(res) else 0,'preferred':int(r.preferred.sum()) if len(res) else 0,'preferred_by_concept':r[r.preferred].concept.value_counts().to_dict() if len(res) else {}};(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL UNIQUE SITUATIONAL DISCOVERY','',json.dumps(summary,indent=2),'','TOP PREFERRED']
if len(r):
 for i,(_,x) in enumerate(r[r.preferred].head(30).iterrows(),1):lines.append(f"US{i:03d} {x.concept} {x.track} {x.band} {x.venue} | {int(x.wins)}-{int(x.n-x.wins)} ({100*x.win_pct:.1f}%) n={int(x.n)} ROI={100*x.roi:+.1f}% train={100*x.train_roi:+.1f}% val={100*x.val_roi:+.1f}% hold={100*x.hold_roi:+.1f}% pos={100*x.positive_season_ratio:.0f}% recent={100*x.recent_delta:+.1f}pp | {x.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:70]))
