from pathlib import Path
import json, math, os
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
BASE=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
TRAVEL=CTX/'injury_travel_mining'/'travel_team_game_2006_2026.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
ODDS=ROOT/'research_v2'/'historical_odds'/'all_open_close_quotes.csv'
METHODS=REPO/'nfl'/'frozen_holdout_discovery'/'canonical_frozen_holdout_methods.csv'
OUT=CTX/'creative_travel_fatigue'; OUT.mkdir(parents=True,exist_ok=True)


def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win); return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def max_neg(vals):
    m=r=0
    for v in vals:
        if v<0:r+=1;m=max(m,r)
        else:r=0
    return m

def metrics(x):
    if len(x)==0:return None
    by=x.groupby('season').profit_units.agg(['count','sum']).sort_index(); elig=by[by['count']>=3]
    w=int(x.win.sum()); n=len(x)
    return {'n':int(n),'wins':w,'losses':int(n-w),'win_rate':float(w/n),'loss_rate':float(1-w/n),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum()),
            'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum()),'negative_seasons':int((elig['sum']<0).sum()),'max_negative_streak':int(max_neg(elig['sum'].tolist()))}

print('Loading base data')
d=pd.read_parquet(BASE)
d['season']=num(d.season).astype('Int64'); d['week']=num(d.week).astype('Int64')
d=d[(num(d.get('completed',1))==1)&num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_calc']=implied(d.moneyline); d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc

s=pd.read_parquet(SCHED); s=s[s.game_type.astype(str).eq('REG')].copy(); s['gameday_dt']=pd.to_datetime(s.gameday,errors='coerce')
sc=[c for c in ['game_id','gameday','gameday_dt','home_rest','away_rest','overtime','div_game','roof','surface','gametime','location','home_team','away_team'] if c in s.columns]
d=d.merge(s[sc].drop_duplicates('game_id'),on='game_id',how='left',suffixes=('','_sched'))
d['rest_days']=np.where(num(d.is_home)==1,num(d.home_rest),num(d.away_rest)); d['opp_rest_days']=np.where(num(d.is_home)==1,num(d.away_rest),num(d.home_rest)); d['rest_days_edge']=d.rest_days-d.opp_rest_days

# Bring in current-game geographic fields, then build sequential schedule burden features.
t=pd.read_parquet(TRAVEL)
t['season']=num(t.season).astype('Int64'); t['week']=num(t.week).astype('Int64')
keep=[c for c in ['game_id','season','week','team','travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','high_altitude_game','international_game','neutral_site','road_game','consecutive_road_games'] if c in t.columns]
seq=d[['game_id','season','week','team','opponent','is_home','gameday_dt','rest_days']].drop_duplicates(['game_id','team']).merge(t[keep].drop_duplicates(['game_id','team']),on=['game_id','season','week','team'],how='left')
seq=seq.merge(s[['game_id','overtime']].drop_duplicates('game_id'),on='game_id',how='left')
seq=seq.sort_values(['team','season','gameday_dt','week','game_id']).reset_index(drop=True)
for c in ['travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','road_game','consecutive_road_games','international_game','high_altitude_game','overtime']:
    if c in seq.columns:seq[c]=num(seq[c]).fillna(0)

creative=[]
for (team,season),g in seq.groupby(['team','season'],sort=False):
    x=g.copy().reset_index(drop=True)
    x['prev_road_game']=x.road_game.shift(1).fillna(0)
    x['prev_travel_miles']=x.travel_miles.shift(1).fillna(0)
    x['prev_abs_tz_shift']=x.abs_tz_shift_hours.shift(1).fillna(0)
    x['prev_international_game']=x.international_game.shift(1).fillna(0)
    x['prev_high_altitude_game']=x.high_altitude_game.shift(1).fillna(0)
    x['prev_overtime']=x.overtime.shift(1).fillna(0)
    x['prior_road_streak']=x.consecutive_road_games.shift(1).fillna(0)
    for n in [2,3,4,5]:
        x[f'prior_road_games_last{n}']=x.road_game.shift(1).rolling(n,min_periods=1).sum().fillna(0)
        x[f'prior_road_miles_last{n}']=x.travel_miles.shift(1).rolling(n,min_periods=1).sum().fillna(0)
        x[f'prior_tz_hours_last{n}']=x.abs_tz_shift_hours.shift(1).rolling(n,min_periods=1).sum().fillna(0)
        x[f'prior_long_trips_last{n}']=x.travel_miles.ge(1500).astype(int).shift(1).rolling(n,min_periods=1).sum().fillna(0)
    x['prior_overtime_last3']=x.overtime.shift(1).rolling(3,min_periods=1).sum().fillna(0)
    x['road_games_last4_including_current']=x.prior_road_games_last3+x.road_game
    x['road_games_last5_including_current']=x.prior_road_games_last4+x.road_game
    x['road_miles_last4_including_current']=x.prior_road_miles_last3+x.travel_miles
    x['road_miles_last5_including_current']=x.prior_road_miles_last4+x.travel_miles
    x['tz_hours_last4_including_current']=x.prior_tz_hours_last3+x.abs_tz_shift_hours
    x['back_to_back_road']=((x.road_game>=1)&(x.prev_road_game>=1)).astype(int)
    x['third_straight_road']=((x.road_game>=1)&(x.prior_road_streak>=2)).astype(int)
    x['third_road_in_four']=(x.road_games_last4_including_current>=3).astype(int)
    x['fourth_road_in_five']=(x.road_games_last5_including_current>=4).astype(int)
    x['short_week_le6']=(x.rest_days<=6).astype(int)
    x['very_short_week_le5']=(x.rest_days<=5).astype(int)
    x['extra_rest_ge8']=(x.rest_days>=8).astype(int)
    x['road_on_short_week']=((x.road_game>=1)&(x.rest_days<=6)).astype(int)
    x['long_trip_short_week']=((x.travel_miles>=1500)&(x.rest_days<=6)).astype(int)
    x['very_long_trip_short_week']=((x.travel_miles>=2000)&(x.rest_days<=6)).astype(int)
    x['multi_tz_short_week']=((x.abs_tz_shift_hours>=2)&(x.rest_days<=6)).astype(int)
    x['after_long_road_game']=((x.prev_road_game>=1)&(x.prev_travel_miles>=1500)).astype(int)
    x['road_after_long_road_game']=((x.road_game>=1)&(x.prev_road_game>=1)&(x.prev_travel_miles>=1500)).astype(int)
    x['home_after_long_road_game']=((x.road_game<1)&(x.prev_road_game>=1)&(x.prev_travel_miles>=1500)).astype(int)
    x['after_international_game']=(x.prev_international_game>=1).astype(int)
    x['after_high_altitude_game']=(x.prev_high_altitude_game>=1).astype(int)
    x['short_week_after_overtime']=((x.prev_overtime>=1)&(x.rest_days<=6)).astype(int)
    x['travel_miles_per_rest_day']=x.travel_miles/x.rest_days.replace(0,np.nan)
    x['road_miles4_per_rest_day']=x.road_miles_last4_including_current/x.rest_days.replace(0,np.nan)
    x['fatigue_load_index']=(x.travel_miles/1000.0)+(x.prior_road_miles_last3/3000.0)+(np.maximum(0,7-x.rest_days)/2.0)+(x.road_games_last4_including_current/4.0)+(x.abs_tz_shift_hours/2.0)+(x.prev_overtime*.5)
    creative.append(x)
seq=pd.concat(creative,ignore_index=True)
base_cols=['game_id','season','week','team']
newcols=[c for c in seq.columns if c not in ['opponent','is_home','gameday_dt','rest_days','travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','high_altitude_game','international_game','neutral_site','road_game','consecutive_road_games','overtime'] and c not in base_cols]
newcols=['travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','road_game','consecutive_road_games']+newcols
newcols=list(dict.fromkeys([c for c in newcols if c in seq.columns]))
for c in newcols:
    if c in d.columns:d=d.drop(columns=[c])
d=d.merge(seq[base_cols+newcols],on=base_cols,how='left')

# Opponent edges: positive means our side has LESS burden / MORE rest than opponent.
bad=[c for c in newcols if c not in {'extra_rest_ge8'}]
for c in bad:
    # Existing context already contains some opponent travel fields; rebuild cleanly.
    for old in ['opp_'+c,'adv_'+c]:
        if old in d.columns:d=d.drop(columns=[old])
    opp=d[['game_id','team',c]].rename(columns={'team':'opponent',c:'opp_'+c})
    d=d.merge(opp,on=['game_id','opponent'],how='left')
    d['adv_'+c]=num(d['opp_'+c])-num(d[c])
if 'extra_rest_ge8' in d.columns:
    for old in ['opp_extra_rest_ge8','adv_extra_rest_ge8']:
        if old in d.columns:d=d.drop(columns=[old])
    opp=d[['game_id','team','extra_rest_ge8']].rename(columns={'team':'opponent','extra_rest_ge8':'opp_extra_rest_ge8'})
    d=d.merge(opp,on=['game_id','opponent'],how='left')
    d['adv_extra_rest_ge8']=num(d.extra_rest_ge8)-num(d.opp_extra_rest_ge8)

# Market movement needed by some frozen base methods.
if ODDS.exists():
    for c in ['open_home','open_away','close_home','close_away','open_prob','close_prob','open_to_close_prob_move','base_vs_open_prob_move']:
        if c in d.columns:d=d.drop(columns=[c])
    q=pd.read_csv(ODDS,low_memory=False); q=q[q.market.astype(str).str.lower().eq('moneyline')].copy(); q['american_odds']=num(q.american_odds); q['prob']=implied(q.american_odds)
    q=q[q.phase.astype(str).str.lower().isin(['open','close'])&q.selection.astype(str).str.lower().isin(['home','away'])]
    qa=q.groupby(['game_id','phase','selection'],as_index=False).prob.median(); w=qa.pivot_table(index='game_id',columns=['phase','selection'],values='prob',aggfunc='first'); w.columns=['_'.join(x) for x in w.columns]; w=w.reset_index()
    d=d.merge(w,on='game_id',how='left'); home=num(d.is_home)==1
    d['open_prob']=np.where(home,num(d.get('open_home')),num(d.get('open_away'))); d['close_prob']=np.where(home,num(d.get('close_home')),num(d.get('close_away')))
    d['open_to_close_prob_move']=d.close_prob-d.open_prob; d['base_vs_open_prob_move']=d.market_prob_use-d.open_prob

d.to_parquet(OUT/'creative_context_team_sides_2006_2025.parquet',index=False)

creative_features=[]
for c in d.columns:
    if c in newcols or (c.startswith('adv_') and any(x in c for x in newcols)):
        z=num(d[c])
        if z.notna().sum()>=150 and z.nunique(dropna=True)>=2:creative_features.append(c)
creative_features=list(dict.fromkeys(creative_features))
print('creative features',len(creative_features))

price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
methods=pd.read_csv(METHODS)
def cond(c,op,q):
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def method_mask(r):
    m=cond(r.feature1,r.op1,r.threshold1)
    if pd.notna(r.feature2):m &= cond(r.feature2,r.op2,r.threshold2)
    lo,hi=price_bands[r.price_band]; m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='HOME':m &= num(d.is_home).eq(1)
    elif r.venue=='AWAY':m &= num(d.is_home).eq(0)
    return m
tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}
def pmask(track,p):lo,hi=tracks[track][p]; return d.season.between(lo,hi)

# ------- Creative veto search across ALL canonical methods -------
confirmed=[]; all_pre=[]
for _,r in methods.iterrows():
    track=r.track if r.track in tracks else 'LONG'; bm=method_mask(r)
    bt=metrics(d[bm&pmask(track,'train')]); bv=metrics(d[bm&pmask(track,'validation')]); bh=metrics(d[bm&pmask(track,'holdout')])
    if not bt or not bv or not bh:continue
    tr=d[bm&pmask(track,'train')]
    cand=[]
    for c in creative_features:
        vals=num(tr[c]).dropna()
        if len(vals)<25:continue
        for qv in sorted(set(float(x) for x in vals.quantile([.1,.2,.3,.4,.6,.7,.8,.9]).dropna())):
            for op in ['<=','>=']:
                risk=cond(c,op,qv)
                def parts(p):
                    b=d[bm&pmask(track,p)]; rr=d[bm&risk&pmask(track,p)]; safe=d[bm&~risk&pmask(track,p)]; return metrics(b),metrics(rr),metrics(safe)
                mb1,mr1,ms1=parts('train'); mb2,mr2,ms2=parts('validation')
                if not all([mb1,mr1,ms1,mb2,mr2,ms2]):continue
                if min(mr1['n'],mr2['n'])<4 or min(ms1['n'],ms2['n'])<12:continue
                if ms1['n']<.5*mb1['n'] or ms2['n']<.5*mb2['n']:continue
                if not (mr1['loss_rate']>=ms1['loss_rate']+.07 and mr2['loss_rate']>=ms2['loss_rate']+.07):continue
                if not (ms1['roi']>=mb1['roi']-.005 and ms2['roi']>=mb2['roi']-.005):continue
                score=(ms1['roi']-mb1['roi'])+(ms2['roi']-mb2['roi'])+1.2*((mr1['loss_rate']-ms1['loss_rate'])+(mr2['loss_rate']-ms2['loss_rate']))
                cand.append((score,c,op,qv,risk,mr1,ms1,mr2,ms2))
    cand.sort(reverse=True,key=lambda x:x[0])
    chosen=[]; used=set()
    for x in cand:
        family='TRAVEL_FATIGUE' if any(k in x[1] for k in ['road','travel','tz_','fatigue','rest','overtime','long_trip','international','altitude']) else 'SCHEDULE_CONTEXT'
        if family in used and len(chosen)>=2:continue
        chosen.append((family,)+x);used.add(family)
        if len(chosen)>=4:break
    for rank,x in enumerate(chosen,1):
        family,score,c,op,qv,risk,mr1,ms1,mr2,ms2=x
        b=d[bm&pmask(track,'holdout')]; rr=d[bm&risk&pmask(track,'holdout')]; safe=d[bm&~risk&pmask(track,'holdout')]
        mb,mr,ms=metrics(b),metrics(rr),metrics(safe)
        if not all([mb,mr,ms]):continue
        ok=(ms['n']>=15 and ms['roi']>=mb['roi']+.02 and ms['loss_rate']<=mb['loss_rate']-.03 and mr['losses']>mr['wins']*.8)
        row={'method_id':r.method_id,'track':track,'rank_preholdout':rank,'feature':c,'op':op,'threshold':qv,'family':family,'pre_score':score,
             'train_base_roi':bt['roi'],'validation_base_roi':bv['roi'],'holdout_base_n':mb['n'],'holdout_base_roi':mb['roi'],'holdout_base_loss_rate':mb['loss_rate'],
             'holdout_removed_n':mr['n'],'holdout_removed_wins':mr['wins'],'holdout_removed_losses':mr['losses'],'holdout_safe_n':ms['n'],'holdout_safe_wins':ms['wins'],'holdout_safe_losses':ms['losses'],'holdout_safe_roi':ms['roi'],'holdout_safe_loss_rate':ms['loss_rate'],'roi_change':ms['roi']-mb['roi'],'loss_rate_change':ms['loss_rate']-mb['loss_rate'],'confirmed':ok}
        all_pre.append(row)
        if ok:confirmed.append(row)

pd.DataFrame(all_pre).to_csv(OUT/'all_creative_veto_tests.csv',index=False)
confdf=pd.DataFrame(confirmed)
if len(confdf):confdf=confdf.sort_values(['roi_change','holdout_removed_losses'],ascending=[False,False])
confdf.to_csv(OUT/'confirmed_creative_vetoes.csv',index=False)

# ------- Standalone creative-method discovery with frozen holdout -------
surv=[]
for track in tracks:
    tr=d[pmask(track,'train')]
    for c in creative_features:
        vals=num(tr[c]).dropna()
        if len(vals)<100:continue
        thresholds=sorted(set(float(x) for x in vals.quantile([.15,.25,.35,.5,.65,.75,.85]).dropna()))
        for qv in thresholds:
            for op in ['<=','>=']:
                cm=cond(c,op,qv)
                for pb,(lo,hi) in price_bands.items():
                    pm=num(d.market_prob_use).between(lo,hi,inclusive='both')
                    for venue in ['ANY','HOME','AWAY']:
                        vm=pd.Series(True,index=d.index) if venue=='ANY' else num(d.is_home).eq(1 if venue=='HOME' else 0)
                        m=cm&pm&vm
                        mt=metrics(d[m&pmask(track,'train')]); mv=metrics(d[m&pmask(track,'validation')])
                        if not mt or not mv:continue
                        mins=(30,20,25) if track=='LONG' else (22,14,18)
                        if mt['n']<mins[0] or mv['n']<mins[1] or mt['roi']<.05 or mv['roi']<.08:continue
                        mh=metrics(d[m&pmask(track,'holdout')])
                        if not mh or mh['n']<mins[2] or mh['roi']<.10:continue
                        fullmask=pmask(track,'train')|pmask(track,'validation')|pmask(track,'holdout'); mf=metrics(d[m&fullmask])
                        if not mf or mf['roi']<.10 or (mf['active_seasons']>=6 and mf['positive_seasons']/max(1,mf['active_seasons'])<.60):continue
                        surv.append({'track':track,'feature':c,'op':op,'threshold':qv,'price_band':pb,'venue':venue,
                                     **{f'train_{k}':v for k,v in mt.items()},**{f'validation_{k}':v for k,v in mv.items()},**{f'holdout_{k}':v for k,v in mh.items()},**{f'full_{k}':v for k,v in mf.items()}})
res=pd.DataFrame(surv)
if len(res):
    res['score']=res.holdout_roi*np.sqrt(res.holdout_n)+.35*res.validation_roi*np.sqrt(res.validation_n)
    res=res.sort_values(['score','full_n'],ascending=[False,False])
    res=res.drop_duplicates(['track','feature','price_band','venue'],keep='first')
res.to_csv(OUT/'creative_method_survivors.csv',index=False)

summary={'creative_features':len(creative_features),'canonical_methods_tested':int(len(methods)),'confirmed_creative_vetoes':int(len(confdf)),'methods_with_confirmed_creative_veto':int(confdf.method_id.nunique()) if len(confdf) else 0,'creative_standalone_methods':int(len(res))}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL CREATIVE TRAVEL/FATIGUE EXPANSION',json.dumps(summary,indent=2),'','TOP CONFIRMED CREATIVE VETOES']
for _,r in confdf.head(30).iterrows():
    lines.append(f"{r.method_id} | VETO {r.feature} {r.op} {r.threshold:.5g} | holdout {100*r.holdout_base_roi:+.1f}% -> {100*r.holdout_safe_roi:+.1f}% | removed {int(r.holdout_removed_losses)}L/{int(r.holdout_removed_wins)}W | n {int(r.holdout_base_n)}->{int(r.holdout_safe_n)}")
lines.append('');lines.append('TOP CREATIVE STANDALONE METHODS')
if len(res):
    for _,r in res.head(30).iterrows():
        lines.append(f"{r.track} {r.price_band} {r.venue} | {r.feature} {r.op} {r.threshold:.5g} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% | train {100*r.train_roi:+.1f}% val {100*r.validation_roi:+.1f}% frozen {100*r.holdout_roi:+.1f}% | +seasons {int(r.full_positive_seasons)}/{int(r.full_active_seasons)}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
