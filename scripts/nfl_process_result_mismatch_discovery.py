from pathlib import Path
import os,json
import numpy as np,pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'process_result_mismatch_discovery';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(w,ml):ml=num(ml);w=num(w);return np.where(w.eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.)
def met(x):
 if not len(x):return dict(n=0,wins=0,win_pct=np.nan,roi=np.nan)
 return dict(n=len(x),wins=int(x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()))
def posratio(x):
 if not len(x):return np.nan
 y=x.groupby('season').profit.sum();return float((y>0).mean())
def cmask(x,c,o,t):
 v=num(x[c]);return v>=t if o=='>=' else v<=t
def filt(x,conds):
 m=pd.Series(True,index=x.index)
 for c,o,t in conds:m &= cmask(x,c,o,t)
 return x[m]

d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d['moneyline']=num(d.moneyline);d['win']=num(d.win);d=d[d.moneyline.notna()&d.win.isin([0,1])].copy();d['market_prob']=implied(d.moneyline);d['profit']=profit(d.win,d.moneyline)
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))};bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75)};venues={'ANY':lambda x:pd.Series(True,index=x.index),'AWAY':lambda x:num(x.is_home).ne(1),'HOME':lambda x:num(x.is_home).eq(1)}
# 'Drag' = visible result/luck factor that can depress market perception. 'Process' = underlying football quality.
drag=[c for c in ['rank_edge_point_margin','recent_edge_point_margin','rank_edge_giveaway_rate','recent_edge_giveaway_rate','rank_edge_fg_accuracy','recent_edge_penalty_yards','rank_edge_penalty_yards','base_vs_open_prob_move'] if c in d]
process_good_hi=[c for c in ['rank_edge_off_epa','rank_edge_off_epa_per_play','rank_edge_pass_epa_per_dropback','rank_edge_third_down_success','rank_edge_sack_or_hit_rate','recent_edge_off_epa','recent_edge_off_epa_per_play','recent_edge_third_down_success','recent_edge_sack_or_hit_rate','rank_edge_success_rate','recent_edge_success_rate'] if c in d]
process_good_lo=[c for c in ['rank_edge_def_allowed_off_epa_per_play','rank_edge_def_allowed_pass_epa_per_dropback','rank_edge_def_allowed_yards_per_play','rank_edge_def_allowed_explosive_rate','recent_edge_def_allowed_off_epa_per_play','recent_edge_def_allowed_pass_epa_per_dropback','recent_edge_def_allowed_yards_per_play','recent_edge_def_allowed_explosive_rate'] if c in d]
# Also test coaching quality as an underlying stabilizer for buy-low teams.
coach_hi=[c for c in ['adv_staff_quality_mean','coachq_staff_quality_mean','coachq_dc_recent_quality','coachq_oc_recent_quality','coachq_hc_recent_quality'] if c in d]
rows=[]
for track,(tr,va,ho) in tracks.items():
 for band,(lo,hi) in bands.items():
  for venue,vfn in venues.items():
   base=d[(d.market_prob>=lo)&(d.market_prob<hi)&vfn(d)&(num(d.prior_games)>=3)].copy();a0=base[base.season.between(*tr)];b0=base[base.season.between(*va)];h0=base[base.season.between(*ho)]
   if min(len(a0),len(b0),len(h0))<45:continue
   candidates=[]
   for dc in drag:
    dv=num(a0[dc]).dropna()
    if len(dv)<100:continue
    # low visible-result/luck signal = buy-low setup
    dths=[float(dv.quantile(q)) for q in [.2,.35,.5]]
    for dt in dths:
     for pc in process_good_hi+coach_hi:
      pv=num(a0[pc]).dropna()
      if len(pv)<100:continue
      for q in [.5,.65,.75]:candidates.append(('BUY_LOW',[(dc,'<=',dt),(pc,'>=',float(pv.quantile(q))) ]))
     for pc in process_good_lo:
      pv=num(a0[pc]).dropna()
      if len(pv)<100:continue
      for q in [.25,.35,.5]:candidates.append(('BUY_LOW_DEF',[(dc,'<=',dt),(pc,'<=',float(pv.quantile(q))) ]))
    # High result/luck but still strong process can identify durable favorites rather than fragile overachievers.
    for dt in [float(dv.quantile(q)) for q in [.5,.65,.8]]:
     for pc in process_good_hi:
      pv=num(a0[pc]).dropna()
      if len(pv)>=100:candidates.append(('CONFIRMED_STRENGTH',[(dc,'>=',dt),(pc,'>=',float(pv.quantile(.65))) ]))
   seen=set()
   for fam,conds in candidates:
    key=json.dumps(conds)
    if key in seen:continue
    seen.add(key);a=filt(a0,conds);b=filt(b0,conds);h=filt(h0,conds);full=filt(base,conds);ma,mb,mh,mf=met(a),met(b),met(h),met(full)
    if ma['n']<16 or mb['n']<10 or mh['n']<14 or mf['n']<70:continue
    if ma['roi']<.06 or mb['roi']<.06 or mh['roi']<.09 or mf['roi']<.09:continue
    pr=posratio(full);recent=mh['roi']-(ma['roi']+mb['roi'])/2
    rows.append({'track':track,'band':band,'venue':venue,'concept':fam,'conditions':json.dumps(conds),'n':mf['n'],'wins':mf['wins'],'win_pct':mf['win_pct'],'roi':mf['roi'],'train_n':ma['n'],'train_roi':ma['roi'],'val_n':mb['n'],'val_roi':mb['roi'],'hold_n':mh['n'],'hold_roi':mh['roi'],'positive_season_ratio':pr,'recent_delta':recent})
r=pd.DataFrame(rows)
if len(r):
 r['preferred']=(r.n>=85)&(r.roi>=.12)&(r.hold_roi>=.12)&(r.positive_season_ratio>=.70)&(r.recent_delta>=-.03);r['score']=r.hold_roi*.32+r.roi*.25+r.positive_season_ratio*.18+r.win_pct*.1+r.recent_delta.clip(-.2,.2)*.1+np.log10(r.n)*.05;r=r.sort_values(['preferred','score'],ascending=False).drop_duplicates(['track','band','venue','conditions']);r.to_csv(OUT/'methods.csv',index=False);r[r.preferred].to_csv(OUT/'preferred.csv',index=False)
summary={'drag_features':drag,'process_hi':process_good_hi,'process_lo':process_good_lo,'coach_hi':coach_hi,'survivors':int(len(r)) if len(rows) else 0,'preferred':int(r.preferred.sum()) if len(rows) else 0,'preferred_by_concept':r[r.preferred].concept.value_counts().to_dict() if len(rows) else {}};(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL PROCESS VS RESULTS / BUY-LOW DISCOVERY','',json.dumps(summary,indent=2),'','TOP PREFERRED']
if len(r):
 for i,(_,x) in enumerate(r[r.preferred].head(30).iterrows(),1):lines.append(f"PR{i:03d} {x.concept} {x.track} {x.band} {x.venue} | {int(x.wins)}-{int(x.n-x.wins)} ({100*x.win_pct:.1f}%) n={int(x.n)} ROI={100*x.roi:+.1f}% train={100*x.train_roi:+.1f}% val={100*x.val_roi:+.1f}% hold={100*x.hold_roi:+.1f}% pos={100*x.positive_season_ratio:.0f}% recent={100*x.recent_delta:+.1f}pp | {x.conditions}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:80]))
