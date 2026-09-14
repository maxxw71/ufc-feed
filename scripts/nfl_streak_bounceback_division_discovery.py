from pathlib import Path
import os,json
import numpy as np,pandas as pd
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1');CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'streak_bounceback_division_discovery';OUT.mkdir(parents=True,exist_ok=True)
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet';SCHED=ROOT/'data/raw/schedules_2006_2026.parquet';ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}
def num(x):return pd.to_numeric(x,errors='coerce')
def imp(ml):ml=num(ml);return np.where(ml<0,-ml/(-ml+100),100/(ml+100))
def prof(w,ml):ml=num(ml);return np.where(num(w).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.)
def met(x):
 if not len(x):return dict(n=0,wins=0,win_pct=np.nan,roi=np.nan)
 return dict(n=len(x),wins=int(x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()))
def sr(x):
 if not len(x):return np.nan
 y=x.groupby('season').profit.sum();return float((y>0).mean())
d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES);d['win']=num(d.win);d=d[d.win.isin([0,1])&num(d.moneyline).notna()].copy();d['profit']=prof(d.win,d.moneyline);d['market_prob']=imp(d.moneyline)
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy();s.home_team=s.home_team.replace(ALIASES);s.away_team=s.away_team.replace(ALIASES);s['date']=pd.to_datetime(s.gameday,errors='coerce')
# pregame sequence context strictly from completed prior games
ss=[]
for home in [1,0]:
 cols=['game_id','season','week','date','home_team','away_team','home_score','away_score']+[c for c in ['div_game'] if c in s.columns]
 z=s[cols].copy();z['team']=z.home_team if home else z.away_team;z['opp']=z.away_team if home else z.home_team;z['pf']=num(z.home_score) if home else num(z.away_score);z['pa']=num(z.away_score) if home else num(z.home_score);z['margin']=z.pf-z.pa;z['home_now']=home;ss.append(z)
ss=pd.concat(ss).sort_values(['season','team','date','week','game_id']);rows=[]
for (season,team),g in ss.groupby(['season','team'],sort=False):
 hist=[]
 for _,r in g.iterrows():
  wins=[1 if h>0 else 0 for h in hist];ws=ls=0
  for h in reversed(hist):
   if h>0:ws+=1
   else:break
  for h in reversed(hist):
   if h<0:ls+=1
   else:break
  p=hist[-1] if hist else np.nan;l3=hist[-3:];l5=hist[-5:]
  rows.append({'game_id':r.game_id,'team':team,'seq_prev_margin':p,'seq_prev_win':int(p>0) if pd.notna(p) else np.nan,'seq_prev_loss':int(p<0) if pd.notna(p) else np.nan,'seq_win_streak':ws,'seq_loss_streak':ls,'seq_last3_wins':sum(h>0 for h in l3),'seq_last5_wins':sum(h>0 for h in l5),'seq_last3_margin':np.mean(l3) if l3 else np.nan,'seq_last5_margin':np.mean(l5) if l5 else np.nan,'seq_blowout_loss14':int(pd.notna(p) and p<=-14),'seq_blowout_loss21':int(pd.notna(p) and p<=-21),'seq_blowout_win14':int(pd.notna(p) and p>=14),'seq_blowout_win21':int(pd.notna(p) and p>=21),'seq_close_loss3':int(pd.notna(p) and -3<=p<0),'seq_close_win3':int(pd.notna(p) and 0<p<=3),'seq_div_game':int(r.get('div_game',0)==1)})
  if pd.notna(r.margin):hist.append(float(r.margin))
ctx=pd.DataFrame(rows);d=d.merge(ctx,on=['game_id','team'],how='left')
# opponent sequence context
opp=ctx.rename(columns={'team':'opponent',**{c:'opp_'+c for c in ctx.columns if c not in ['game_id','team']}});d=d.merge(opp,on=['game_id','opponent'],how='left')
features=['seq_win_streak','seq_loss_streak','seq_last3_wins','seq_last5_wins','seq_last3_margin','seq_last5_margin','seq_blowout_loss14','seq_blowout_loss21','seq_blowout_win14','seq_blowout_win21','seq_close_loss3','seq_close_win3','seq_div_game','opp_seq_win_streak','opp_seq_loss_streak','opp_seq_last3_wins','opp_seq_last5_wins','opp_seq_last3_margin','opp_seq_last5_margin','opp_seq_blowout_loss14','opp_seq_blowout_win14']
concept={c:('DIVISION' if 'div_game' in c else 'BOUNCEBACK' if 'blowout' in c or 'close_' in c else 'STREAK') for c in features}
football=[c for c in d.columns if (c.startswith('rank_edge_') or c.startswith('recent_edge_') or c.startswith('coachq_') or c.startswith('adv_staff_') or c=='base_vs_open_prob_move') and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=500][:130]
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))};bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)};venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}
def mask(x,c,o,t):v=num(x[c]);return v>=t if o=='>=' else v<=t
def fil(x,cs):
 m=pd.Series(True,index=x.index)
 for c,o,t in cs:m&=mask(x,c,o,t)
 return x[m]
rows=[]
for track,(tr,va,ho) in tracks.items():
 for band,(lo,hi) in bands.items():
  for venue,vfn in venues.items():
   base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)].copy();train=base[base.season.between(*tr)];val=base[base.season.between(*va)];hold=base[base.season.between(*ho)]
   if min(len(train),len(val),len(hold))<50:continue
   nc=[]
   for c in features:
    if c not in d:continue
    v=num(train[c]).dropna();
    if len(v)<60:continue
    uniq=set(v.unique())
    if uniq.issubset({0,1}):vals=[('>=',1.)]
    elif 'streak' in c or 'wins' in c:vals=[('>=',1.),('>=',2.),('>=',3.),('<=',1.)]
    else:vals=[(o,float(v.quantile(q))) for q in [.25,.5,.75] for o in ['>=','<=']]
    for o,t in vals:
     a,b=fil(train,[(c,o,t)]),fil(val,[(c,o,t)]);ma,mb=met(a),met(b)
     if ma['n']>=18 and mb['n']>=10 and ma['roi']>=0 and mb['roi']>=0:nc.append((c,o,t,ma['roi']+mb['roi']))
   nc=sorted(nc,key=lambda z:z[3],reverse=True)[:45];fc=[]
   for c in football:
    v=num(train[c]).dropna();
    if len(v)<100:continue
    for q in [.25,.5,.75]:
     t=float(v.quantile(q))
     for o in ['>=','<=']:
      a,b=fil(train,[(c,o,t)]),fil(val,[(c,o,t)]);ma,mb=met(a),met(b)
      if ma['n']>=30 and mb['n']>=15 and ma['roi']>=.03 and mb['roi']>=.03:fc.append((c,o,t,ma['roi']+mb['roi']))
   fc=sorted(fc,key=lambda z:z[3],reverse=True)[:30];cand=[]
   for a in nc:cand.append([(a[0],a[1],a[2])])
   for a in nc[:35]:
    for b in fc[:25]:
     if a[0]!=b[0]:cand.append([(a[0],a[1],a[2]),(b[0],b[1],b[2])])
   seen=set()
   for cs in cand:
    k=json.dumps(cs)
    if k in seen:continue
    seen.add(k);a,b,h,f=fil(train,cs),fil(val,cs),fil(hold,cs),fil(base,cs);ma,mb,mh,mf=met(a),met(b),met(h),met(f)
    if ma['n']<18 or mb['n']<10 or mh['n']<15 or mf['n']<70:continue
    if min(ma['roi'],mb['roi'])<.06 or mh['roi']<.08 or mf['roi']<.08:continue
    pos=sr(f);recent=mh['roi']-(ma['roi']+mb['roi'])/2;fam=concept.get(cs[0][0],'OTHER')
    rows.append({'track':track,'band':band,'venue':venue,'concept':fam,'conditions':json.dumps(cs),'n':mf['n'],'wins':mf['wins'],'win_pct':mf['win_pct'],'roi':mf['roi'],'train_n':ma['n'],'train_roi':ma['roi'],'val_n':mb['n'],'val_roi':mb['roi'],'hold_n':mh['n'],'hold_roi':mh['roi'],'positive_season_ratio':pos,'recent_delta':recent})
r=pd.DataFrame(rows)
if len(r):
 r['preferred']=(r.n>=85)&(r.roi>=.10)&(r.train_roi>=.08)&(r.val_roi>=.08)&(r.hold_roi>=.10)&(r.positive_season_ratio>=.70)&(r.recent_delta>=-.02);r['score']=r.hold_roi*.3+r.roi*.25+r.positive_season_ratio*.2+r.recent_delta.clip(-.2,.2)*.12+r.win_pct*.1+np.log10(r.n)*.03;r=r.sort_values(['preferred','score'],ascending=False).drop_duplicates(['track','band','venue','conditions']);r.to_csv(OUT/'methods.csv',index=False);r[r.preferred].to_csv(OUT/'preferred.csv',index=False)
summary={'survivors':int(len(r)) if len(rows) else 0,'preferred':int(r.preferred.sum()) if len(rows) else 0,'by_concept':r[r.preferred].concept.value_counts().to_dict() if len(rows) else {}};(OUT/'summary.json').write_text(json.dumps(summary,indent=2));lines=['NFL STREAK / BOUNCEBACK / DIVISION DISCOVERY','',json.dumps(summary,indent=2),'','TOP PREFERRED']
if len(r):
 for i,(_,x) in enumerate(r[r.preferred].head(25).iterrows(),1):lines.append(f"SB{i:03d} {x.concept} {x.track} {x.band} {x.venue} | {int(x.wins)}-{int(x.n-x.wins)} ({100*x.win_pct:.1f}%) n={int(x.n)} ROI={100*x.roi:+.1f}% train={100*x.train_roi:+.1f}% val={100*x.val_roi:+.1f}% hold={100*x.hold_roi:+.1f}% pos={100*x.positive_season_ratio:.0f}% recent={100*x.recent_delta:+.1f}pp | {x.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:70]))
