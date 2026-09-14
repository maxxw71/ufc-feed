from __future__ import annotations
from pathlib import Path
import json, math, re
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
SRC=CTX/'injury_travel_mining'
OUT=CTX/'final_method_expansion'
OUT.mkdir(parents=True,exist_ok=True)
BASE=SRC/'complete_pregame_team_sides.parquet'
CANDS=SRC/'positive_roi_method_candidates.csv'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
ODDS=ROOT/'research_v2'/'historical_odds'/'all_open_close_quotes.csv'

TEAM_MAP={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}
def canon(x):
    if pd.isna(x): return x
    return TEAM_MAP.get(str(x),str(x))
def num(x): return pd.to_numeric(x,errors='coerce')
def implied_from_american(a):
    a=num(a)
    return np.where(a>0,100/(a+100),np.where(a<0,a.abs()/(a.abs()+100),np.nan))
def profit_units(odds,win):
    o=num(odds); w=num(win)
    return np.where(w.eq(1),np.where(o>0,o/100,100/o.abs()),-1.0)

def semantic(c):
    s=str(c).lower()
    if any(k in s for k in ['injur','unavail','out_player','star_out','questionable','doubtful','dnp_','limited_']): return 'INJURY_AVAILABILITY'
    if any(k in s for k in ['travel','tz_','timezone','altitude','international','road_game','road_trip']): return 'TRAVEL_CIRCADIAN'
    if any(k in s for k in ['returning_','continuity']): return 'ROSTER_CONTINUITY'
    if any(k in s for k in ['market_move','open_market','open_close','line_move']): return 'MARKET_MOVEMENT'
    if any(k in s for k in ['qb_','quarterback']): return 'QB_CONTEXT'
    if any(k in s for k in ['coach','coordinator','staff_']): return 'COACHING_STAFF'
    if any(k in s for k in ['punt','kick_return','fg_accuracy','special']): return 'SPECIAL_TEAMS'
    if any(k in s for k in ['sack','hit_rate','pressure']): return 'PASS_PROTECTION_RUSH'
    if 'third_down' in s: return 'THIRD_DOWN'
    if 'redzone' in s or 'red_zone' in s: return 'RED_ZONE'
    if 'explosive' in s: return 'EXPLOSIVENESS'
    if any(k in s for k in ['giveaway','turnover','takeaway']): return 'TURNOVERS'
    if any(k in s for k in ['pass_epa','passing_cpoe','passing_']): return 'PASSING'
    if any(k in s for k in ['rush_epa','rushing_']): return 'RUSHING'
    if any(k in s for k in ['yards_per_play','off_epa','success_rate','early_down']): return 'EFFICIENCY'
    if 'elo' in s: return 'ELO_POWER'
    if 'rest' in s or 'short_week' in s or 'long_rest' in s: return 'REST_SCHEDULING'
    if any(k in s for k in ['div_game','weekday','thursday','monday','primetime','roof','surface']): return 'GAME_CONTEXT'
    return 'OTHER'

def split_for(track):
    if track=='MODERN': return [(2012,2018),(2019,2022),(2023,2025)]
    return [(2006,2014),(2015,2019),(2020,2025)]

def band_mask(mp,name):
    m=num(mp)
    bands={
      'DOG20_34':(.20,.349999),'DOG35_44':(.35,.449999),'DOG45_49':(.45,.499999),'DOG35_49':(.35,.499999),
      'PK_55':(.50,.55),'FAV50_59':(.50,.599999),'FAV55_65':(.55,.65),'FAV60_69':(.60,.699999),
      'FAV65_75':(.65,.75),'FAV70_79':(.70,.799999),'FAV75_85':(.75,.85),'FAV80_89':(.80,.899999),'FAV85_95':(.85,.95),'FAV90_97':(.90,.97),
      'ALL':(0,1)}
    lo,hi=bands.get(name,(0,1)); return m.between(lo,hi,inclusive='both')

def venue_mask(d,name):
    if name=='HOME': return num(d.is_home).eq(1)
    if name=='AWAY': return num(d.is_home).eq(0)
    return pd.Series(True,index=d.index)

def mstats(x):
    if len(x)==0: return None
    pu=num(x.profit_units); wins=int(num(x.win).sum())
    by=x.assign(_p=pu).groupby('season')._p.agg(['count','sum'])
    elig=by[by['count']>=3]
    pos=int((elig['sum']>0).sum()); neg=int((elig['sum']<0).sum())
    # Longest negative-season streak among seasons with >=3 bets.
    streak=mx=0
    for z in (elig['sum']<0).astype(int).tolist():
        streak=streak+1 if z else 0; mx=max(mx,streak)
    arr=pu.dropna().to_numpy(dtype=float)
    se=float(np.std(arr,ddof=1)/np.sqrt(len(arr))) if len(arr)>1 else np.nan
    return {'n':int(len(x)),'wins':wins,'win_rate':float(num(x.win).mean()),'roi':float(pu.mean()),'units':float(pu.sum()),
            'active_seasons':int(len(elig)),'positive_seasons':pos,'negative_seasons':neg,'max_negative_streak':int(mx),'roi_se':se}

def evaluate(d,mask,track,meta):
    sub=d[mask].copy(); full=mstats(sub)
    if full is None: return None
    vals=[]
    for lo,hi in split_for(track): vals.append(mstats(sub[sub.season.between(lo,hi)]))
    if any(v is None for v in vals): return None
    out={**meta,**{f'full_{k}':v for k,v in full.items()}}
    for lab,v in zip(['train','validation','holdout'],vals): out.update({f'{lab}_{k}':vv for k,vv in v.items()})
    return out

def threshold_mask(series,op,q):
    z=num(series)
    return z.ge(q) if op=='>=' else z.le(q)

print('Loading complete pregame table...',flush=True)
if not BASE.exists(): raise FileNotFoundError(BASE)
d=pd.read_parquet(BASE)
d['team']=d.team.map(canon); d['opponent']=d.opponent.map(canon)
d['season']=num(d.season).astype(int); d['week']=num(d.week).astype(int)
if 'game_type' in d.columns: d=d[d.game_type.astype(str).eq('REG')].copy()
if 'completed' in d.columns: d=d[num(d.completed).eq(1)].copy()
d=d[d.win.notna() & d.moneyline.notna()].copy()
if 'prior_games' in d.columns: d=d[num(d.prior_games)>=3].copy()
d['profit_units']=profit_units(d.moneyline,d.win)
d['market_prob_use']=num(d.market_prob) if 'market_prob' in d.columns else implied_from_american(d.moneyline)

# ---------- Merge missing schedule context ----------
print('Adding schedule context...',flush=True)
s=pd.read_parquet(SCHED)
s['home_team']=s.home_team.map(canon); s['away_team']=s.away_team.map(canon)
ctxcols=[c for c in ['game_id','weekday','gametime','div_game','roof','surface','temp','wind','location','stadium','home_rest','away_rest'] if c in s.columns]
sc=s[ctxcols].drop_duplicates('game_id')
d=d.merge(sc,on='game_id',how='left',suffixes=('','_sched'))
if 'home_rest' in d.columns and 'away_rest' in d.columns:
    d['rest_days']=np.where(num(d.is_home).eq(1),num(d.home_rest),num(d.away_rest))
else: d['rest_days']=np.nan
d['short_week']=num(d.rest_days).le(6).astype(float)
d['long_rest']=num(d.rest_days).ge(9).astype(float)
d['divisional']=num(d.get('div_game',0)).fillna(0)
wd=d.get('weekday',pd.Series('',index=d.index)).astype(str).str.lower()
d['is_thursday']=wd.str.startswith('thu').astype(float); d['is_monday']=wd.str.startswith('mon').astype(float)
# Game time strings like 20:15 or 8:15PM.
def hourval(x):
    try:
        t=pd.to_datetime(str(x),errors='coerce')
        return t.hour+t.minute/60 if pd.notna(t) else np.nan
    except Exception: return np.nan
d['kickoff_hour']=d.get('gametime',pd.Series(np.nan,index=d.index)).map(hourval)
d['primetime']=num(d.kickoff_hour).ge(19).astype(float)
roof=d.get('roof',pd.Series('',index=d.index)).astype(str).str.lower(); surf=d.get('surface',pd.Series('',index=d.index)).astype(str).str.lower()
d['roof_closed_or_dome']=roof.str.contains('dome|closed|indoor',regex=True,na=False).astype(float)
d['surface_turf']=surf.str.contains('turf|artificial',regex=True,na=False).astype(float)
d['short_week_long_trip']=(num(d.short_week).eq(1)&num(d.get('travel_miles',np.nan)).ge(1000)).astype(float)
d['long_trip_1500']=num(d.get('travel_miles',np.nan)).ge(1500).astype(float)
d['eastward_2plus']=num(d.get('eastward_tz_hours',np.nan)).ge(2).astype(float)
d['westward_2plus']=num(d.get('westward_tz_hours',np.nan)).ge(2).astype(float)
d['high_altitude_away']=(num(d.get('high_altitude_game',0)).eq(1)&num(d.is_home).eq(0)).astype(float)
d['international_away']=(num(d.get('international_game',0)).eq(1)&num(d.is_home).eq(0)).astype(float)

# ---------- Open/close market movement ----------
print('Adding opening/closing market movement...',flush=True)
movement_cov=0.0
if ODDS.exists():
    q=pd.read_csv(ODDS,low_memory=False)
    q=q[q.market.astype(str).str.lower().eq('moneyline')].copy()
    q['american_odds']=num(q.american_odds)
    q=q[q.selection.astype(str).str.lower().isin(['home','away']) & q.phase.astype(str).str.lower().isin(['open','close'])]
    # Median across providers prevents one book from dominating and is robust to outliers.
    med=q.groupby(['game_id','selection','phase'],as_index=False).american_odds.median()
    wide=med.pivot_table(index='game_id',columns=['selection','phase'],values='american_odds',aggfunc='first')
    wide.columns=[f'{a}_{b}_ml' for a,b in wide.columns]; wide=wide.reset_index()
    counts=q.groupby('game_id').provider.nunique().rename('odds_provider_count').reset_index()
    wide=wide.merge(counts,on='game_id',how='left')
    d=d.merge(wide,on='game_id',how='left')
    for phase in ['open','close']:
        hc=f'home_{phase}_ml'; ac=f'away_{phase}_ml'
        d[f'{phase}_side_ml']=np.where(num(d.is_home).eq(1),num(d.get(hc,np.nan)),num(d.get(ac,np.nan)))
        d[f'{phase}_market_prob']=implied_from_american(d[f'{phase}_side_ml'])
    d['market_move_prob']=num(d.close_market_prob)-num(d.open_market_prob)
    d['market_move_abs']=num(d.market_move_prob).abs()
    d['base_vs_open_prob_move']=num(d.market_prob_use)-num(d.open_market_prob)
    movement_cov=float(d.market_move_prob.notna().mean())
else:
    for c in ['open_market_prob','close_market_prob','market_move_prob','market_move_abs','base_vs_open_prob_move','odds_provider_count']: d[c]=np.nan

# ---------- Data completeness ----------
coverage={
 'rows_for_mining':int(len(d)),
 'travel_pct':float(100*d.get('travel_miles',pd.Series(np.nan,index=d.index)).notna().mean()),
 'injury_pct':float(100*d.get('unavailable_equiv',pd.Series(np.nan,index=d.index)).notna().mean()),
 'continuity_pct':float(100*d.get('returning_ol_snap_share',pd.Series(np.nan,index=d.index)).notna().mean()),
 'market_movement_pct':100*movement_cov,
 'schedule_context_pct':float(100*d.get('weekday',pd.Series(np.nan,index=d.index)).notna().mean()),
 'columns_after_enrichment':int(len(d.columns))}

# ---------- Re-evaluate 1,859 existing discoveries under stricter double-digit gates ----------
print('Re-evaluating existing candidates...',flush=True)
all_rules=[]; rule_masks={}
if CANDS.exists():
    src=pd.read_csv(CANDS,low_memory=False)
    for i,r in src.iterrows():
        track='MODERN' if str(r.get('track'))=='COMPLETE' else 'LONG'
        if r.feature1 not in d.columns: continue
        m=threshold_mask(d[r.feature1],str(r.op1),float(r.threshold1))
        f2=r.get('feature2',np.nan)
        if pd.notna(f2) and str(f2) in d.columns:
            m &= threshold_mask(d[str(f2)],str(r.op2),float(r.threshold2))
        m &= band_mask(d.market_prob_use,str(r.price_band)) & venue_mask(d,str(r.venue))
        # Respect injury-era missingness if rule uses injury fields.
        feats=[str(r.feature1)]+([str(f2)] if pd.notna(f2) else [])
        if any(semantic(x)=='INJURY_AVAILABILITY' for x in feats) and 'injury_data_available' in d.columns:
            m &= num(d.injury_data_available).eq(1)
        meta={'origin':'EXISTING_1859','track':track,'family':'PAIR' if pd.notna(f2) else 'UNIVARIATE',
              'feature1':str(r.feature1),'op1':str(r.op1),'threshold1':float(r.threshold1),'feature2':None if pd.isna(f2) else str(f2),
              'op2':None if pd.isna(f2) else str(r.op2),'threshold2':None if pd.isna(f2) else float(r.threshold2),
              'price_band':str(r.price_band),'venue':str(r.venue)}
        z=evaluate(d,m,track,meta)
        if z:
            mid=f'E{i:04d}'; z['raw_id']=mid; all_rules.append(z); rule_masks[mid]=m.to_numpy(bool)

# ---------- Target newly completed context families directly ----------
context_features=[c for c in [
 'adv_unavailable_equiv','adv_out_equiv','adv_star_outs','adv_qb_unavail_equiv','adv_ol_unavail_equiv','adv_skill_unavail_equiv','adv_front7_unavail_equiv','adv_secondary_unavail_equiv',
 'travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','consecutive_road_games','short_week_long_trip','long_trip_1500','eastward_2plus','westward_2plus','high_altitude_away','international_away',
 'adv_returning_offense_snap_share','adv_returning_defense_snap_share','adv_returning_ol_snap_share','adv_returning_skill_snap_share',
 'qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed',
 'rest_edge','rest_days','short_week','long_rest','divisional','is_thursday','is_monday','primetime','roof_closed_or_dome','surface_turf',
 'open_market_prob','market_move_prob','market_move_abs','base_vs_open_prob_move','odds_provider_count'] if c in d.columns]

price_bands=['DOG20_34','DOG35_44','DOG45_49','DOG35_49','PK_55','FAV50_59','FAV55_65','FAV60_69','FAV65_75','FAV70_79','FAV75_85','FAV80_89','FAV85_95','FAV90_97']
venues=['ANY','HOME','AWAY']

print(f'Mining {len(context_features)} newly completed context features...',flush=True)
context_candidates=[]
for c in context_features:
    cat=semantic(c)
    track='MODERN' if cat in {'INJURY_AVAILABILITY','ROSTER_CONTINUITY','COACHING_STAFF'} else 'LONG'
    sp=split_for(track); tr=d[d.season.between(*sp[0])]
    vals=num(tr[c]).dropna()
    if len(vals)<120 or vals.nunique()<2: continue
    if vals.nunique()<=4:
        qs=sorted(set(float(x) for x in vals.unique() if pd.notna(x)))
        qs=[x for x in qs if x not in [0]] or qs
    else:
        qs=sorted(set(float(x) for x in vals.quantile([.20,.35,.50,.65,.80]).dropna()))
    for qv in qs:
      for op in ['>=','<=']:
       fm=threshold_mask(d[c],op,qv)
       # injury features only where reports exist
       if cat=='INJURY_AVAILABILITY' and 'injury_data_available' in d.columns: fm &= num(d.injury_data_available).eq(1)
       for pb in price_bands:
        pm=band_mask(d.market_prob_use,pb)
        for vb in venues:
         m=fm&pm&venue_mask(d,vb)
         meta={'origin':'NEW_CONTEXT','track':track,'family':'UNIVARIATE','feature1':c,'op1':op,'threshold1':qv,'feature2':None,'op2':None,'threshold2':None,'price_band':pb,'venue':vb}
         z=evaluate(d,m,track,meta)
         if not z: continue
         # Selection for pair generation uses train + validation only, never holdout.
         mintr=25 if track=='MODERN' else 35; minva=15 if track=='MODERN' else 20
         if z['train_n']>=mintr and z['validation_n']>=minva and z['train_roi']>.02 and z['validation_roi']>.02:
             mid=f'C{len(context_candidates):05d}'; z['raw_id']=mid; context_candidates.append(z); rule_masks[mid]=m.to_numpy(bool); all_rules.append(z)

# Pair top context candidates with strong existing core rules, chosen only by train+validation score.
def prehold_score(r):
    return r.get('train_roi',-9)*math.sqrt(max(1,r.get('train_n',0))) + .7*r.get('validation_roi',-9)*math.sqrt(max(1,r.get('validation_n',0)))
core=[r for r in all_rules if r['origin']=='EXISTING_1859' and semantic(r['feature1']) not in {'INJURY_AVAILABILITY','TRAVEL_CIRCADIAN','ROSTER_CONTINUITY','MARKET_MOVEMENT','COACHING_STAFF','GAME_CONTEXT','REST_SCHEDULING'}]
core=sorted(core,key=prehold_score,reverse=True)[:100]
contexts=sorted(context_candidates,key=prehold_score,reverse=True)[:140]
print(f'Pairing {len(contexts)} context candidates with {len(core)} core candidates...',flush=True)
pair_count=0
for a in contexts:
    am=rule_masks[a['raw_id']]
    for b in core:
        if a['track']!=b['track'] or a['price_band']!=b['price_band'] or a['venue']!=b['venue']: continue
        # Require different semantic source to create a genuinely new interaction.
        if semantic(a['feature1'])==semantic(b['feature1']): continue
        bm=rule_masks[b['raw_id']]; m=pd.Series(am & bm,index=d.index)
        meta={'origin':'CONTEXT_CORE_PAIR','track':a['track'],'family':'PAIR','feature1':a['feature1'],'op1':a['op1'],'threshold1':a['threshold1'],
              'feature2':b['feature1'],'op2':b['op1'],'threshold2':b['threshold1'],'price_band':a['price_band'],'venue':a['venue']}
        z=evaluate(d,m,a['track'],meta)
        if not z: continue
        mintr=22 if a['track']=='MODERN' else 30; minva=12 if a['track']=='MODERN' else 16
        if z['train_n']<mintr or z['validation_n']<minva or z['train_roi']<=.03 or z['validation_roi']<=.05: continue
        mid=f'P{pair_count:05d}'; pair_count+=1; z['raw_id']=mid; all_rules.append(z); rule_masks[mid]=m.to_numpy(bool)

res=pd.DataFrame(all_rules)
if res.empty: raise RuntimeError('No candidate rules generated')
# Semantic labels and robustness.
res['semantic1']=res.feature1.map(semantic)
res['semantic2']=res.feature2.map(lambda x: semantic(x) if pd.notna(x) and x else '')
res['semantic_signature']=res.apply(lambda r:'+'.join(sorted(set([x for x in [r.semantic1,r.semantic2] if x]))),axis=1)
res['positive_season_ratio']=res.full_positive_seasons/np.maximum(1,res.full_active_seasons)
res['robust_score']=(res.holdout_roi*np.sqrt(res.holdout_n)+.65*res.validation_roi*np.sqrt(res.validation_n)+.25*res.train_roi*np.sqrt(res.train_n))

# Strict final screen: every method must be double-digit overall AND in validation AND holdout.
# Train is allowed at +5% minimum because thresholds are selected there; demanding +10% train would favor overfit training peaks.
strict=res[(res.full_roi>=.10)&(res.validation_roi>=.10)&(res.holdout_roi>=.10)&(res.train_roi>=.05)].copy()
strict=strict[(strict.full_n>=60)&(strict.validation_n>=15)&(strict.holdout_n>=20)]
strict=strict[(strict.positive_season_ratio>=.60)&(strict.full_max_negative_streak<=3)]
strict=strict.sort_values(['robust_score','full_n'],ascending=[False,False])

# Deduplicate: reject near-identical bet sets, and be stricter for the same semantic signature/market bucket.
selected=[]; selected_masks=[]
for _,r in strict.iterrows():
    m=rule_masks.get(r.raw_id)
    if m is None: continue
    keep=True
    for sr,sm in zip(selected,selected_masks):
        inter=int(np.logical_and(m,sm).sum()); union=int(np.logical_or(m,sm).sum()); jac=inter/union if union else 0
        same_bucket=(r.price_band==sr.price_band and r.venue==sr.venue and r.semantic_signature==sr.semantic_signature)
        if jac>=.86 or (same_bucket and jac>=.67): keep=False; break
    if keep:
        selected.append(r); selected_masks.append(m)

sel=pd.DataFrame(selected).reset_index(drop=True)
sel.insert(0,'method_id',[f'NFL-U{i+1:03d}' for i in range(len(sel))])
# Confidence tiers are descriptive, not guarantees.
def tier(r):
    if r.full_n>=120 and r.validation_roi>=.15 and r.holdout_roi>=.15 and r.positive_season_ratio>=.70 and r.full_max_negative_streak<=2: return 'A'
    if r.full_n>=80 and r.validation_roi>=.10 and r.holdout_roi>=.10 and r.positive_season_ratio>=.65: return 'B'
    return 'C'
if len(sel): sel['research_tier']=sel.apply(tier,axis=1)

# Year-by-year for selected unique methods.
years=[]; betrows=[]
for idx,r in sel.iterrows():
    m=selected_masks[idx]
    z=d[m].copy()
    for yr,g in z.groupby('season'):
        st=mstats(g); years.append({'method_id':r.method_id,'season':int(yr),**st})
    for _,g in z[['game_id','season','week','date','team','opponent','moneyline','win','profit_units'] if 'date' in z.columns else ['game_id','season','week','team','opponent','moneyline','win','profit_units']].iterrows():
        rec=g.to_dict(); rec['method_id']=r.method_id; betrows.append(rec)

# Context-specific survivors summary
ctxsurv=sel[sel.semantic_signature.str.contains('INJURY|TRAVEL|CONTINUITY|MARKET_MOVEMENT|COACHING|REST_SCHEDULING|GAME_CONTEXT',regex=True,na=False)].copy() if len(sel) else pd.DataFrame()

# Save outputs.
res.sort_values('robust_score',ascending=False).to_csv(OUT/'all_retested_candidates.csv',index=False)
strict.to_csv(OUT/'strict_double_digit_candidates_pre_dedupe.csv',index=False)
sel.to_csv(OUT/'unique_double_digit_methods.csv',index=False)
pd.DataFrame(years).to_csv(OUT/'unique_methods_year_by_year.csv',index=False)
pd.DataFrame(betrows).to_csv(OUT/'unique_method_bets.csv',index=False)
ctxsurv.to_csv(OUT/'context_enriched_survivors.csv',index=False)
(OUT/'final_coverage.json').write_text(json.dumps({**coverage,'all_candidates_retested':int(len(res)),'strict_pre_dedupe':int(len(strict)),'unique_methods':int(len(sel)),'context_unique_methods':int(len(ctxsurv))},indent=2))

# Human-readable report.
report=['NFL UNIQUE DOUBLE-DIGIT ROI METHOD EXPANSION','',json.dumps({**coverage,'all_candidates_retested':int(len(res)),'strict_pre_dedupe':int(len(strict)),'unique_methods':int(len(sel)),'context_unique_methods':int(len(ctxsurv))},indent=2),'']
report += ['STRICT RULE: overall ROI >= +10%, validation ROI >= +10%, holdout ROI >= +10%, train ROI >= +5%, minimum samples, >=60% positive active seasons, no >3-season losing streak.','Near-duplicate methods are removed using actual bet-set Jaccard overlap; same semantic/market bucket receives a stricter overlap cutoff.','Pair generation uses train + validation ranking before holdout grading; holdout is not used to choose which pairs are generated.','']
if len(sel):
    report.append('UNIQUE METHODS')
    for _,r in sel.iterrows():
        f=f"{r.feature1} {r.op1} {r.threshold1:.5g}"
        if pd.notna(r.feature2) and r.feature2: f+=f" AND {r.feature2} {r.op2} {r.threshold2:.5g}"
        report.append(f"{r.method_id} [{r.research_tier}] {r.track} {r.price_band} {r.venue} | {r.semantic_signature} | {f} | n={int(r.full_n)} W={int(r.full_wins)} WR={100*r.full_win_rate:.1f}% ROI={100*r.full_roi:+.1f}% | train {100*r.train_roi:+.1f}% n={int(r.train_n)} | val {100*r.validation_roi:+.1f}% n={int(r.validation_n)} | holdout {100*r.holdout_roi:+.1f}% n={int(r.holdout_n)} | +seasons {int(r.full_positive_seasons)}/{int(r.full_active_seasons)}, max losing streak {int(r.full_max_negative_streak)}")
else: report.append('No methods survived the strict double-digit + uniqueness gates.')
report += ['','CONTEXT-ENRICHED UNIQUE SURVIVORS']
if len(ctxsurv):
    for _,r in ctxsurv.iterrows(): report.append(f"{r.method_id}: {r.semantic_signature} | ROI {100*r.full_roi:+.1f}% | holdout {100*r.holdout_roi:+.1f}% | n={int(r.full_n)}")
else: report.append('None survived strict gates; context features remain descriptive/research-only.')
(OUT/'unique_method_report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report[:80]),flush=True)
