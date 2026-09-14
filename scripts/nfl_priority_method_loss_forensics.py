from pathlib import Path
import math, os, json
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
IN=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
CTXFILE=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
ODDS=ROOT/'research_v2'/'historical_odds'/'all_open_close_quotes.csv'
METHODS=REPO/'nfl'/'frozen_holdout_discovery'/'canonical_frozen_holdout_methods.csv'
OUT=CTX/'priority_method_loss_forensics'; OUT.mkdir(parents=True,exist_ok=True)
TARGETS=['NFL-H003','NFL-H002','NFL-H010','NFL-H016','NFL-H006','NFL-H029','NFL-H033']


def num(s): return pd.to_numeric(s,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def max_neg(vals):
    m=r=0
    for v in vals:
        if v<0:r+=1;m=max(m,r)
        else:r=0
    return m

def metrics(x):
    if len(x)==0:return {'n':0,'wins':0,'losses':0,'win_rate':np.nan,'loss_rate':np.nan,'roi':np.nan,'units':0.0,'active_seasons':0,'positive_seasons':0,'negative_seasons':0,'max_negative_streak':0}
    by=x.groupby('season').profit_units.agg(['count','sum']).sort_index(); elig=by[by['count']>=3]
    w=int(x.win.sum()); n=len(x)
    return {'n':int(n),'wins':w,'losses':int(n-w),'win_rate':float(w/n),'loss_rate':float(1-w/n),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum()),
            'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum()),'negative_seasons':int((elig['sum']<0).sum()),'max_negative_streak':int(max_neg(elig['sum'].tolist()))}

def sem(c):
    c=str(c).lower()
    if 'open_prob' in c or 'close_prob' in c or 'market_move' in c:return 'MARKET_MOVEMENT'
    if 'injur' in c or 'unavail' in c or 'star_out' in c or 'out_player' in c:return 'INJURY_AVAILABILITY'
    if 'returning_' in c or 'continuity' in c:return 'ROSTER_CONTINUITY'
    if 'travel' in c or 'tz_' in c or 'altitude' in c or 'road_games' in c:return 'TRAVEL_CIRCADIAN'
    if 'qb_' in c:return 'QB_CONTEXT'
    if 'rest' in c:return 'REST_SCHEDULING'
    if 'coach' in c or 'coordinator' in c:return 'COACHING'
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
    if 'penalty' in c:return 'PENALTIES'
    return 'OTHER'

print('Loading data...')
d=pd.read_parquet(IN)
d['season']=num(d.season).astype('Int64'); d['week']=num(d.week).astype('Int64')
d=d[(num(d.get('completed',1))==1)&num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_calc']=implied(d.moneyline); d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc

# Refresh QB/coach/coordinator fields from latest context backfill.
if CTXFILE.exists():
    cx=pd.read_parquet(CTXFILE); keys=['game_id','season','week','team']
    refresh=[c for c in cx.columns if any(t in c.lower() for t in ['coordinator','head_coach','qb_prior_starts','qb_changed_from_prior_season'])]
    refresh=[c for c in refresh if c not in keys]
    for c in refresh:
        if c in d.columns:d=d.drop(columns=[c])
    if refresh:d=d.merge(cx[keys+refresh].drop_duplicates(keys),on=keys,how='left')

# Schedule context (weather deliberately excluded from candidate search).
s=pd.read_parquet(SCHED); s=s[s.game_type.astype(str).eq('REG')].copy()
sc=[c for c in ['game_id','home_rest','away_rest','spread_line','total_line','div_game','roof','surface','gametime','stadium','location'] if c in s.columns]
d=d.merge(s[sc].drop_duplicates('game_id'),on='game_id',how='left',suffixes=('','_sched'))
d['rest_days']=np.where(num(d.is_home)==1,num(d.home_rest),num(d.away_rest)); d['opp_rest_days']=np.where(num(d.is_home)==1,num(d.away_rest),num(d.home_rest)); d['rest_days_edge']=d.rest_days-d.opp_rest_days
if 'roof' in d.columns:d['dome_game']=d.roof.fillna('').astype(str).str.lower().str.contains('dome|closed').astype(int)
if 'surface' in d.columns:d['grass_surface']=d.surface.fillna('').astype(str).str.lower().str.contains('grass').astype(int)

# Archived opening/closing moneyline movement.
if ODDS.exists():
    q=pd.read_csv(ODDS,low_memory=False); q=q[q.market.astype(str).str.lower().eq('moneyline')].copy(); q['american_odds']=num(q.american_odds); q['prob']=implied(q.american_odds)
    q=q[q.phase.astype(str).str.lower().isin(['open','close'])&q.selection.astype(str).str.lower().isin(['home','away'])]
    qa=q.groupby(['game_id','phase','selection'],as_index=False).prob.median(); w=qa.pivot_table(index='game_id',columns=['phase','selection'],values='prob',aggfunc='first'); w.columns=['_'.join(x) for x in w.columns]; w=w.reset_index()
    d=d.merge(w,on='game_id',how='left'); home=num(d.is_home)==1
    d['open_prob']=np.where(home,num(d.get('open_home')),num(d.get('open_away'))); d['close_prob']=np.where(home,num(d.get('close_home')),num(d.get('close_away')))
    d['open_to_close_prob_move']=d.close_prob-d.open_prob; d['base_vs_open_prob_move']=d.market_prob_use-d.open_prob

methods=pd.read_csv(METHODS); methods=methods[methods.method_id.isin(TARGETS)].copy()
missing=set(TARGETS)-set(methods.method_id)
if missing: raise RuntimeError(f'Missing target methods: {sorted(missing)}')

price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
def cond(c,op,q):
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def base_mask(r):
    m=cond(r.feature1,r.op1,r.threshold1)
    if pd.notna(r.feature2):m &= cond(r.feature2,r.op2,r.threshold2)
    lo,hi=price_bands[r.price_band];m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='HOME':m &= num(d.is_home).eq(1)
    elif r.venue=='AWAY':m &= num(d.is_home).eq(0)
    return m

# Only pregame features. Outcome-derived/postgame fields are excluded.
explicit=['market_prob_use','moneyline','spread_line','total_line','week','elo_edge','rest_edge','rest_days','rest_days_edge','qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','neutral_site','international_game','high_altitude_game','dome_game','grass_surface','div_game','travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','consecutive_road_games','returning_offense_snap_share','returning_defense_snap_share','returning_ol_snap_share','returning_skill_snap_share','adv_returning_offense_snap_share','adv_returning_defense_snap_share','adv_returning_ol_snap_share','adv_returning_skill_snap_share','adv_unavailable_equiv','adv_out_equiv','adv_qb_unavail_equiv','adv_ol_unavail_equiv','adv_skill_unavail_equiv','adv_front7_unavail_equiv','adv_secondary_unavail_equiv','open_prob','open_to_close_prob_move','base_vs_open_prob_move']
features=[]
for c in d.columns:
    if c.startswith(('rank_edge_','recent_edge_','adv_')) or c in explicit:
        lc=c.lower()
        if any(x in lc for x in ['win','profit','result','score','points_for','points_against','completed','push']):continue
        z=num(d[c])
        if z.notna().sum()>=150 and z.nunique(dropna=True)>=2:features.append(c)
features=list(dict.fromkeys(features)); print('candidate features',len(features))

PER={'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)}
def pmask(name):lo,hi=PER[name];return d.season.between(lo,hi)

def veto_stats(base,risk,p):
    b=d[base&pmask(p)]; rr=d[base&risk&pmask(p)]; safe=d[base&~risk&pmask(p)]
    mb,mr,ms=metrics(b),metrics(rr),metrics(safe)
    return mb,mr,ms

selected_rows=[]; holdout_loss_rows=[]; method_rows=[]
for _,mrule in methods.iterrows():
    mid=mrule.method_id; bm=base_mask(mrule)
    base_by={p:metrics(d[bm&pmask(p)]) for p in PER}
    full=metrics(d[bm])
    method_rows.append({'method_id':mid,'base_n':full['n'],'base_wins':full['wins'],'base_losses':full['losses'],'base_roi':full['roi'],**{f'{p}_n':base_by[p]['n'] for p in PER},**{f'{p}_roi':base_by[p]['roi'] for p in PER}})
    print(mid,'base',full)
    candidates=[]
    tr=d[bm&pmask('train')]
    for c in features:
        vals=num(tr[c]).dropna()
        if len(vals)<25:continue
        qs=sorted(set(float(x) for x in vals.quantile([.10,.20,.30,.40,.60,.70,.80,.90]).dropna()))
        for qv in qs:
            for op in ['<=','>=']:
                risk=cond(c,op,qv)
                bt,rt,st=veto_stats(bm,risk,'train'); bv,rv,sv=veto_stats(bm,risk,'validation')
                if min(rt['n'],rv['n'])<4 or min(st['n'],sv['n'])<12:continue
                if st['n']<.50*bt['n'] or sv['n']<.50*bv['n']:continue
                # The proposed veto profile must be loss-heavier in BOTH pre-holdout eras.
                if not (rt['loss_rate']>=st['loss_rate']+.07 and rv['loss_rate']>=sv['loss_rate']+.07):continue
                # Removing it must not reduce ROI in either pre-holdout era.
                if not (st['roi']>=bt['roi']-.005 and sv['roi']>=bv['roi']-.005):continue
                # Reward loss concentration, ROI improvement, and preserving volume.
                imp=(st['roi']-bt['roi'])+(sv['roi']-bv['roi'])
                sep=(rt['loss_rate']-st['loss_rate'])+(rv['loss_rate']-sv['loss_rate'])
                retain=.5*(st['n']/max(1,bt['n'])+sv['n']/max(1,bv['n']))
                score=imp+1.2*sep+.15*retain
                candidates.append({'method_id':mid,'feature':c,'op':op,'threshold':qv,'semantic':sem(c),'pre_score':score,'risk_mask':risk,'train_base':bt,'train_risk':rt,'train_safe':st,'validation_base':bv,'validation_risk':rv,'validation_safe':sv})
    candidates.sort(key=lambda x:x['pre_score'],reverse=True)
    chosen=[]; used_sem=set()
    for x in candidates:
        if x['semantic'] in used_sem:continue
        chosen.append(x);used_sem.add(x['semantic'])
        if len(chosen)>=6:break
    # Pair/consensus risk flags are frozen from the top pre-holdout univariate flags only.
    if len(chosen)>=2:
        top=chosen[:4]
        combos=[]
        for i in range(len(top)):
            for j in range(i+1,len(top)):
                for mode in ['OR','AND']:
                    risk=(top[i]['risk_mask']|top[j]['risk_mask']) if mode=='OR' else (top[i]['risk_mask']&top[j]['risk_mask'])
                    bt,rt,st=veto_stats(bm,risk,'train');bv,rv,sv=veto_stats(bm,risk,'validation')
                    if min(rt['n'],rv['n'])<4 or min(st['n'],sv['n'])<12:continue
                    if st['n']<.45*bt['n'] or sv['n']<.45*bv['n']:continue
                    if not (rt['loss_rate']>=st['loss_rate']+.07 and rv['loss_rate']>=sv['loss_rate']+.07):continue
                    if not (st['roi']>=bt['roi']-.005 and sv['roi']>=bv['roi']-.005):continue
                    score=(st['roi']-bt['roi'])+(sv['roi']-bv['roi'])+1.2*((rt['loss_rate']-st['loss_rate'])+(rv['loss_rate']-sv['loss_rate']))
                    combos.append({'method_id':mid,'feature':f"{top[i]['feature']} {top[i]['op']} {top[i]['threshold']:.6g} {mode} {top[j]['feature']} {top[j]['op']} {top[j]['threshold']:.6g}",'op':'COMBO','threshold':np.nan,'semantic':top[i]['semantic']+'+'+top[j]['semantic'],'pre_score':score,'risk_mask':risk,'train_base':bt,'train_risk':rt,'train_safe':st,'validation_base':bv,'validation_risk':rv,'validation_safe':sv})
        combos.sort(key=lambda x:x['pre_score'],reverse=True)
        if combos:chosen.append(combos[0])
    # Open holdout only now for the pre-selected flags.
    flags=[]
    for rank,x in enumerate(chosen,1):
        bh,rh,sh=veto_stats(bm,x['risk_mask'],'holdout')
        removed_losses=rh['losses'];removed_wins=rh['wins']
        confirmed=(sh['n']>=15 and sh['roi']>=bh['roi']+.02 and sh['loss_rate']<=bh['loss_rate']-.03 and removed_losses>removed_wins)
        row={'method_id':mid,'rank_preholdout':rank,'feature_or_combo':x['feature'],'op':x['op'],'threshold':x['threshold'],'semantic':x['semantic'],'pre_score':x['pre_score'],
             'train_base_n':x['train_base']['n'],'train_base_roi':x['train_base']['roi'],'train_risk_n':x['train_risk']['n'],'train_risk_loss_rate':x['train_risk']['loss_rate'],'train_safe_n':x['train_safe']['n'],'train_safe_roi':x['train_safe']['roi'],'train_safe_loss_rate':x['train_safe']['loss_rate'],
             'validation_base_n':x['validation_base']['n'],'validation_base_roi':x['validation_base']['roi'],'validation_risk_n':x['validation_risk']['n'],'validation_risk_loss_rate':x['validation_risk']['loss_rate'],'validation_safe_n':x['validation_safe']['n'],'validation_safe_roi':x['validation_safe']['roi'],'validation_safe_loss_rate':x['validation_safe']['loss_rate'],
             'holdout_base_n':bh['n'],'holdout_base_wins':bh['wins'],'holdout_base_losses':bh['losses'],'holdout_base_roi':bh['roi'],'holdout_risk_n':rh['n'],'holdout_removed_wins':removed_wins,'holdout_removed_losses':removed_losses,'holdout_risk_loss_rate':rh['loss_rate'],'holdout_safe_n':sh['n'],'holdout_safe_wins':sh['wins'],'holdout_safe_losses':sh['losses'],'holdout_safe_roi':sh['roi'],'holdout_safe_loss_rate':sh['loss_rate'],'holdout_roi_change':sh['roi']-bh['roi'],'holdout_loss_rate_change':sh['loss_rate']-bh['loss_rate'],'confirmed_veto':confirmed}
        selected_rows.append(row);flags.append((f'flag_{rank}',x['risk_mask'],x['feature']))
    # Detailed holdout losses with frozen flags and contextual columns.
    hz=d[bm&pmask('holdout')&d.win.eq(0)].copy()
    keep=['game_id','season','week','gameday','team','opponent','moneyline','market_prob_use','is_home','spread_line','rest_days','rest_days_edge','qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','travel_miles','abs_tz_shift_hours','westward_tz_hours','eastward_tz_hours','altitude_change_ft','adv_unavailable_equiv','adv_qb_unavail_equiv','adv_ol_unavail_equiv','adv_secondary_unavail_equiv','adv_returning_ol_snap_share','open_to_close_prob_move','base_vs_open_prob_move']
    keep=[c for c in keep if c in hz.columns]
    det=hz[keep].copy();det.insert(0,'method_id',mid)
    for name,mask,desc in flags:
        det[name]=mask.loc[hz.index].astype(int).values;det[name+'_rule']=desc
    holdout_loss_rows.append(det)

sel=pd.DataFrame(selected_rows); sel.to_csv(OUT/'veto_candidates_selected_preholdout.csv',index=False)
conf=sel[sel.confirmed_veto==True].copy() if len(sel) else pd.DataFrame(); conf.to_csv(OUT/'confirmed_holdout_veto_filters.csv',index=False)
pd.DataFrame(method_rows).to_csv(OUT/'priority_method_baselines.csv',index=False)
if holdout_loss_rows:pd.concat(holdout_loss_rows,ignore_index=True).to_csv(OUT/'holdout_losses_with_flags.csv',index=False)

# Compact report.
lines=['NFL PRIORITY METHOD LOSS FORENSICS','','Method thresholds and veto filters were selected WITHOUT using 2020-2025. Holdout only confirms/rejects the pre-selected risk flags.','Weather excluded by project decision. In-game events are not used because they are not knowable pregame.','']
for mid in TARGETS:
    b=pd.DataFrame(method_rows);br=b[b.method_id==mid].iloc[0]
    lines.append(f"{mid}: base full n={int(br.base_n)} ROI={100*br.base_roi:+.1f}% | frozen holdout n={int(br.holdout_n)} ROI={100*br.holdout_roi:+.1f}%")
    z=sel[sel.method_id==mid].sort_values('rank_preholdout')
    if not len(z): lines.append('  No stable pre-holdout veto flags survived selection gates.');continue
    for _,r in z.iterrows():
        tag='CONFIRMED' if r.confirmed_veto else 'REJECT/WEAK'
        lines.append(f"  {tag} #{int(r.rank_preholdout)} {r.semantic}: {r.feature_or_combo} {'' if r.op=='COMBO' else str(r.op)+' '+format(r.threshold,'.6g')} | holdout removed {int(r.holdout_removed_losses)} L / {int(r.holdout_removed_wins)} W; n {int(r.holdout_base_n)}->{int(r.holdout_safe_n)}; ROI {100*r.holdout_base_roi:+.1f}%->{100*r.holdout_safe_roi:+.1f}% ({100*r.holdout_roi_change:+.1f} pts); loss rate {100*r.holdout_base_losses/r.holdout_base_n:.1f}%->{100*r.holdout_safe_loss_rate:.1f}%")
    lines.append('')
summary={'methods':TARGETS,'candidate_features':len(features),'preselected_veto_rows':int(len(sel)),'confirmed_veto_rows':int(len(conf)),'methods_with_confirmed_veto':int(conf.method_id.nunique()) if len(conf) else 0}
lines.insert(1,json.dumps(summary,indent=2))
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print('\n'.join(lines))
