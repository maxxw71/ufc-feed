from pathlib import Path
import json, math, re
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
IN=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
ODDS=ROOT/'research_v2'/'historical_odds'/'all_open_close_quotes.csv'
OUT=CTX/'frozen_holdout_discovery'; OUT.mkdir(parents=True,exist_ok=True)

def num(s): return pd.to_numeric(s,errors='coerce')
def implied(o):
    o=num(o)
    return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def max_losing_streak(vals):
    m=r=0
    for v in vals:
        if v<0:r+=1;m=max(m,r)
        else:r=0
    return m

def metrics(x):
    if len(x)==0:return None
    by=x.groupby('season').profit_units.agg(['count','sum']).sort_index()
    elig=by[by['count']>=3]
    return dict(n=int(len(x)),wins=int(x.win.sum()),win_rate=float(x.win.mean()),roi=float(x.profit_units.mean()),units=float(x.profit_units.sum()),
                active_seasons=int(len(elig)),positive_seasons=int((elig['sum']>0).sum()),negative_seasons=int((elig['sum']<0).sum()),
                max_negative_streak=int(max_losing_streak(elig['sum'].tolist())),roi_se=float(x.profit_units.std(ddof=1)/math.sqrt(len(x))) if len(x)>1 else np.nan)

def sem(c):
    c=str(c).lower()
    if 'open_prob' in c or 'close_prob' in c or 'market_move' in c:return 'MARKET_MOVEMENT'
    if 'injur' in c or 'unavail' in c or 'star_out' in c or 'out_player' in c:return 'INJURY_AVAILABILITY'
    if 'returning_' in c or 'continuity' in c:return 'ROSTER_CONTINUITY'
    if 'travel' in c or 'tz_' in c or 'altitude' in c or 'road_games' in c:return 'TRAVEL_CIRCADIAN'
    if 'qb_' in c:return 'QB_CONTEXT'
    if 'rest' in c:return 'REST_SCHEDULING'
    if 'third_down' in c:return 'THIRD_DOWN'
    if 'redzone' in c:return 'RED_ZONE'
    if 'explosive' in c:return 'EXPLOSIVENESS'
    if 'punt' in c or 'kick_return' in c or 'fg_accuracy' in c:return 'SPECIAL_TEAMS'
    if 'giveaway' in c or 'turnover' in c:return 'TURNOVERS'
    if 'sack' in c or 'hit_rate' in c:return 'PASS_PROTECTION_RUSH'
    if 'pass' in c or 'cpoe' in c:return 'PASSING'
    if 'rush' in c:return 'RUSHING'
    if 'epa' in c or 'yards_per_play' in c or 'success_rate' in c:return 'EFFICIENCY'
    if 'elo' in c:return 'ELO'
    if 'coach' in c or 'coordinator' in c:return 'COACHING'
    return 'OTHER'

def sig(r):
    s=[sem(r['feature1'])]
    if r.get('feature2'):s.append(sem(r['feature2']))
    return '+'.join(sorted(set(s)))

print('Loading complete pregame data')
d=pd.read_parquet(IN)
d=d[(num(d.get('completed',1))==1)&num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
d['season']=num(d.season).astype(int); d['week']=num(d.week).astype(int); d['win']=num(d.win); d['moneyline']=num(d.moneyline)
if 'prior_games' in d:d=d[num(d.prior_games)>=3].copy()
d['market_prob_calc']=implied(d.moneyline); d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc
d['profit_units']=profit(d.win,d.moneyline)

# Schedule context (strictly known pregame). Weather intentionally excluded from discovery per project decision.
s=pd.read_parquet(SCHED); s=s[s.game_type.astype(str).eq('REG')].copy()
sctx=[c for c in ['game_id','home_rest','away_rest','div_game','location','roof','surface','gametime','stadium'] if c in s.columns]
d=d.merge(s[sctx].drop_duplicates('game_id'),on='game_id',how='left',suffixes=('','_sched'))
d['rest_days']=np.where(num(d.is_home)==1,num(d.get('home_rest')),num(d.get('away_rest')))
d['opp_rest_days']=np.where(num(d.is_home)==1,num(d.get('away_rest')),num(d.get('home_rest')))
d['rest_days_edge']=d.rest_days-d.opp_rest_days
if 'roof' in d:
    d['dome_game']=d.roof.fillna('').astype(str).str.lower().str.contains('dome|closed').astype(int)
if 'surface' in d:
    d['grass_surface']=d.surface.fillna('').astype(str).str.lower().str.contains('grass').astype(int)

# Open/close moneyline movement. Threshold discovery uses only historical quotes already available pregame.
if ODDS.exists():
    q=pd.read_csv(ODDS,low_memory=False)
    q=q[q.market.astype(str).str.lower().eq('moneyline')].copy()
    q['american_odds']=num(q.american_odds); q['prob']=implied(q.american_odds)
    q=q[q.phase.astype(str).str.lower().isin(['open','close']) & q.selection.astype(str).str.lower().isin(['home','away'])]
    qa=q.groupby(['game_id','phase','selection'],as_index=False).prob.median()
    wide=qa.pivot_table(index='game_id',columns=['phase','selection'],values='prob',aggfunc='first')
    wide.columns=['_'.join(x) for x in wide.columns]; wide=wide.reset_index()
    d=d.merge(wide,on='game_id',how='left')
    home=num(d.is_home)==1
    d['open_prob']=np.where(home,num(d.get('open_home')),num(d.get('open_away')))
    d['close_prob']=np.where(home,num(d.get('close_home')),num(d.get('close_away')))
    d['open_to_close_prob_move']=d.close_prob-d.open_prob
    d['base_vs_open_prob_move']=d.market_prob_use-d.open_prob

# Candidate feature list. Outcome/postgame fields are excluded. Weather excluded deliberately.
explicit=['elo_edge','rest_edge','rest_days','rest_days_edge','qb_prior_starts','qb_changed_from_prior_season','head_coach_changed',
          'offensive_coordinator_changed','defensive_coordinator_changed','neutral_site','international_game','high_altitude_game','dome_game','grass_surface',
          'travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','consecutive_road_games',
          'returning_offense_snap_share','returning_defense_snap_share','returning_ol_snap_share','returning_skill_snap_share',
          'adv_returning_offense_snap_share','adv_returning_defense_snap_share','adv_returning_ol_snap_share','adv_returning_skill_snap_share',
          'adv_unavailable_equiv','adv_out_equiv','adv_qb_unavail_equiv','adv_ol_unavail_equiv','adv_skill_unavail_equiv','adv_front7_unavail_equiv','adv_secondary_unavail_equiv',
          'open_to_close_prob_move','base_vs_open_prob_move']
features=[]
for c in d.columns:
    if c.startswith(('rank_edge_','recent_edge_','adv_')) or c in explicit:
        lc=c.lower()
        if any(x in lc for x in ['win','profit','result','score','points_for','points_against','completed','push']):continue
        z=num(d[c])
        if z.notna().sum()>=150 and z.nunique(dropna=True)>=2:features.append(c)
features=list(dict.fromkeys(features))
print('features',len(features),'rows',len(d))

price_bands=[('DOG20_34',.20,.3499),('DOG35_44',.35,.4499),('DOG35_49',.35,.4999),('PK_55',.45,.55),('FAV55_65',.55,.65),('FAV65_75',.65,.75),('FAV75_85',.75,.85)]
venue_bands=[('ANY',None),('HOME',1),('AWAY',0)]
tracks={
 'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025),'mins':(30,20,25)},
 'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025),'mins':(22,14,18)}
}

def period_mask(track,name):
    lo,hi=tracks[track][name]; return d.season.between(lo,hi)
def apply_cond(c,op,q):
    z=num(d[c]); return z>=q if op=='>=' else z<=q
def market_mask(pb):
    _,lo,hi=next(x for x in price_bands if x[0]==pb); return num(d.market_prob_use).between(lo,hi,inclusive='both')
def venue_mask(v):
    if v=='ANY':return pd.Series(True,index=d.index)
    return num(d.is_home).eq(1 if v=='HOME' else 0)

def eval_pre(mask,track):
    a=d[mask&period_mask(track,'train')]; b=d[mask&period_mask(track,'validation')]
    ma,mb=metrics(a),metrics(b); mn=tracks[track]['mins']
    if not ma or not mb or ma['n']<mn[0] or mb['n']<mn[1]:return None
    if ma['roi']<.05 or mb['roi']<.08:return None
    if ma['active_seasons']>=4 and ma['positive_seasons']/max(1,ma['active_seasons'])<.5:return None
    if mb['active_seasons']>=3 and mb['positive_seasons']/max(1,mb['active_seasons'])<.5:return None
    return ma,mb

# 1) Thresholds are chosen using TRAIN ONLY. 2) Candidate survival/pair construction uses TRAIN+VALIDATION ONLY.
pre=[]
for track in tracks:
    train=d[period_mask(track,'train')]
    for c in features:
        # Injury/continuity features only on MODERN track.
        s=sem(c)
        if track=='LONG' and s in {'INJURY_AVAILABILITY','ROSTER_CONTINUITY','COACHING'}:continue
        vals=num(train[c]).dropna()
        if len(vals)<100:continue
        qs=sorted(set(float(x) for x in vals.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))
        for q in qs:
            for op in ['>=','<=']:
                cm=apply_cond(c,op,q)
                for pb,_,__ in price_bands:
                    pm=market_mask(pb)
                    for vv,_h in venue_bands:
                        m=cm&pm&venue_mask(vv)
                        ev=eval_pre(m,track)
                        if not ev:continue
                        ma,mb=ev
                        score=min(ma['roi'],mb['roi'])*math.sqrt(min(ma['n'],mb['n']))+.2*(ma['roi']+mb['roi'])
                        pre.append(dict(track=track,family='UNIVARIATE',feature1=c,op1=op,threshold1=q,feature2=None,op2=None,threshold2=np.nan,price_band=pb,venue=vv,pre_score=score,train=ma,validation=mb,mask=m))
print('pre univariate',len(pre))

# Keep broad pre-holdout pool and generate cross-signal pairs; no holdout values read here.
pre=sorted(pre,key=lambda r:r['pre_score'],reverse=True)
# Diversity cap before pairs.
pool=[]; caps={}
for r in pre:
    key=(r['track'],sem(r['feature1']),r['price_band'],r['venue'])
    if caps.get(key,0)>=8:continue
    pool.append(r); caps[key]=caps.get(key,0)+1
    if len(pool)>=320:break
pairs=[]
for i,a in enumerate(pool):
    for b in pool[i+1:min(len(pool),i+120)]:
        if a['track']!=b['track'] or a['price_band']!=b['price_band'] or a['venue']!=b['venue']:continue
        if a['feature1']==b['feature1'] or sem(a['feature1'])==sem(b['feature1']):continue
        m=a['mask']&b['mask']; ev=eval_pre(m,a['track'])
        if not ev:continue
        ma,mb=ev
        score=min(ma['roi'],mb['roi'])*math.sqrt(min(ma['n'],mb['n']))+.2*(ma['roi']+mb['roi'])
        pairs.append(dict(track=a['track'],family='PAIR',feature1=a['feature1'],op1=a['op1'],threshold1=a['threshold1'],feature2=b['feature1'],op2=b['op1'],threshold2=b['threshold1'],price_band=a['price_band'],venue=a['venue'],pre_score=score,train=ma,validation=mb,mask=m))
print('pre pairs',len(pairs))

frozen=sorted(pool+pairs,key=lambda r:r['pre_score'],reverse=True)
# Deduplicate using only train+validation bet sets, before opening holdout.
kept=[]
for r in frozen:
    preperiod=period_mask(r['track'],'train')|period_mask(r['track'],'validation')
    ids=set(d.loc[r['mask']&preperiod,'game_id'].astype(str)+'|'+d.loc[r['mask']&preperiod,'team'].astype(str))
    if len(ids)<20:continue
    duplicate=False
    rsig=sig(r)
    for k in kept:
        if r['track']!=k['track'] or r['price_band']!=k['price_band'] or r['venue']!=k['venue']:continue
        inter=len(ids&k['_ids']); union=len(ids|k['_ids']); jac=inter/union if union else 0
        cutoff=.78 if rsig==k['semantic_signature'] else .90
        if jac>=cutoff:duplicate=True;break
    if duplicate:continue
    r['_ids']=ids;r['semantic_signature']=rsig;kept.append(r)
    if len(kept)>=450:break
print('FROZEN CANDIDATES BEFORE HOLDOUT',len(kept))

# NOW open the holdout exactly once.
surv=[]
for r in kept:
    h=d[r['mask']&period_mask(r['track'],'holdout')]; mh=metrics(h); mn=tracks[r['track']]['mins'][2]
    if not mh or mh['n']<mn or mh['roi']<.10:continue
    allperiod=period_mask(r['track'],'train')|period_mask(r['track'],'validation')|period_mask(r['track'],'holdout')
    mf=metrics(d[r['mask']&allperiod])
    if not mf or mf['roi']<.10:continue
    if mf['active_seasons']>=6 and mf['positive_seasons']/max(1,mf['active_seasons'])<.60:continue
    if mf['max_negative_streak']>3:continue
    tier='A' if mh['n']>=30 and mh['roi']>=.15 and mf['positive_seasons']/max(1,mf['active_seasons'])>=.68 else 'B'
    row={k:v for k,v in r.items() if k not in ['mask','_ids','train','validation']}
    for p,m in [('train',r['train']),('validation',r['validation']),('holdout',mh),('full',mf)]:
        for k,v in m.items():row[f'{p}_{k}']=v
    row['tier']=tier;surv.append(row)

res=pd.DataFrame(surv)
if len(res):
    res['robust_score']=res.holdout_roi*np.sqrt(res.holdout_n)+.35*res.validation_roi*np.sqrt(res.validation_n)+.15*res.train_roi*np.sqrt(res.train_n)
    res=res.sort_values(['tier','robust_score','full_n'],ascending=[True,False,False]).reset_index(drop=True)
    res.insert(0,'method_id',[f'NFL-H{i+1:03d}' for i in range(len(res))])
res.to_csv(OUT/'frozen_holdout_survivors.csv',index=False)
# Canonical one/two per semantic signature.
canon=[]; counts={}
for _,r in res.iterrows():
    s=r.semantic_signature
    if counts.get(s,0)>=2:continue
    canon.append(r);counts[s]=counts.get(s,0)+1
can=pd.DataFrame(canon)
can.to_csv(OUT/'canonical_frozen_holdout_methods.csv',index=False)
flag=res.drop_duplicates('semantic_signature',keep='first') if len(res) else res
flag.to_csv(OUT/'flagship_frozen_holdout_families.csv',index=False)
summary={'candidate_features':len(features),'frozen_candidates_before_holdout':len(kept),'survivors_double_digit_holdout':len(res),'canonical_methods':len(can),'unique_signal_families':int(res.semantic_signature.nunique()) if len(res) else 0,'tier_A':int((res.tier=='A').sum()) if len(res) else 0,'tier_B':int((res.tier=='B').sum()) if len(res) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL FROZEN HOLDOUT DISCOVERY','',json.dumps(summary,indent=2),'','HOLDOUT WAS NOT USED FOR THRESHOLD, PAIR, OR DEDUP SELECTION.','','TOP SURVIVORS']
for _,r in res.head(60).iterrows():
    rule=f"{r.feature1} {r.op1} {r.threshold1:.5g}"+(f" AND {r.feature2} {r.op2} {r.threshold2:.5g}" if pd.notna(r.feature2) else '')
    lines.append(f"{r.method_id} [{r.tier}] {r.semantic_signature} | {r.price_band} {r.venue} | {rule} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% | train={100*r.train_roi:+.1f}% n={int(r.train_n)} val={100*r.validation_roi:+.1f}% n={int(r.validation_n)} FROZEN holdout={100*r.holdout_roi:+.1f}% n={int(r.holdout_n)} | +seasons {int(r.full_positive_seasons)}/{int(r.full_active_seasons)}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
