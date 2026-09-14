from pathlib import Path
import json, math, os
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CREATIVE=CTX/'creative_travel_fatigue'/'creative_context_team_sides_2006_2025.parquet'
BASE=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
STAFF=CTX/'raw'/'coaching_staff_2006_2026.csv'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
METHODS=REPO/'nfl'/'frozen_holdout_discovery'/'canonical_frozen_holdout_methods.csv'
OUT=CTX/'coaching_everything'; OUT.mkdir(parents=True,exist_ok=True)


def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win); return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if len(x)==0:return None
    by=x.groupby('season').profit_units.agg(['count','sum']).sort_index(); elig=by[by['count']>=3]
    w=int(x.win.sum()); n=len(x)
    return {'n':int(n),'wins':w,'losses':int(n-w),'win_rate':float(w/n),'loss_rate':float(1-w/n),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum()),'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum()),'negative_seasons':int((elig['sum']<0).sum())}
def norm(s):
    if pd.isna(s):return None
    z=' '.join(str(s).strip().lower().split())
    return z or None

def cond(d,c,op,q):
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)

print('Loading richest available pregame table')
d=pd.read_parquet(CREATIVE if CREATIVE.exists() else BASE)
d['season']=num(d.season).astype('Int64'); d['week']=num(d.week).astype('Int64')
d=d[(num(d.get('completed',1))==1)&num(d.win).isin([0,1])&num(d.moneyline).notna()&d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_calc']=implied(d.moneyline); d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc

# Authoritative game-by-game head coach from schedule.
s=pd.read_parquet(SCHED); s=s[s.game_type.astype(str).eq('REG')].copy(); s['gameday_dt']=pd.to_datetime(s.gameday,errors='coerce')
side=[]
for sd in ['home','away']:
    side.append(pd.DataFrame({'game_id':s.game_id,'team':s[f'{sd}_team'],'gameday_dt':s.gameday_dt,'head_coach':s.get(f'{sd}_coach')}))
sg=pd.concat(side,ignore_index=True).drop_duplicates(['game_id','team'])
for c in ['gameday_dt','head_coach']:
    if c in d.columns:d=d.drop(columns=[c])
d=d.merge(sg,on=['game_id','team'],how='left')

# Audited team-season OC/DC history.
st=pd.read_csv(STAFF)
st['season']=num(st.season).astype('Int64')
st['team']=st.team.astype(str)
for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
    if role in st.columns:st[role]=st[role].map(norm)
# Prefer game-specific HC; season table supplies OC/DC.
keep=['season','team']+[c for c in ['offensive_coordinator','defensive_coordinator','offensive_coordinator_changed','defensive_coordinator_changed','coordinator_changes','major_staff_changes'] if c in st.columns]
for c in keep:
    if c not in ['season','team'] and c in d.columns:d=d.drop(columns=[c])
d=d.merge(st[keep].drop_duplicates(['season','team']),on=['season','team'],how='left')
d['head_coach']=d.head_coach.map(norm)

# Team-season tenure / continuity features. Missing roles remain missing, never imputed as stable.
season_staff=st[['season','team','head_coach','offensive_coordinator','defensive_coordinator']].copy().sort_values(['team','season'])
for role,pfx in [('head_coach','hc'),('offensive_coordinator','oc'),('defensive_coordinator','dc')]:
    prev=season_staff.groupby('team')[role].shift(1)
    known=season_staff[role].notna()&prev.notna()
    season_staff[f'{pfx}_changed_season']=np.where(known,season_staff[role].ne(prev).astype(float),np.nan)
    ten=np.full(len(season_staff),np.nan)
    for team,idx in season_staff.groupby('team').groups.items():
        last=None; run=0
        for i in idx:
            cur=season_staff.at[i,role]
            if pd.isna(cur):run=0;last=None;continue
            run=run+1 if cur==last else 1
            ten[i]=run; last=cur
    season_staff[f'{pfx}_tenure_seasons']=ten
# Cross-role continuity.
season_staff['both_coords_known']=season_staff.offensive_coordinator.notna()&season_staff.defensive_coordinator.notna()
season_staff['both_coords_changed']=np.where(season_staff[['oc_changed_season','dc_changed_season']].notna().all(axis=1),(season_staff.oc_changed_season+season_staff.dc_changed_season>=2).astype(float),np.nan)
season_staff['staff_change_count']=season_staff[['hc_changed_season','oc_changed_season','dc_changed_season']].sum(axis=1,min_count=3)
season_staff['full_staff_stable']=np.where(season_staff.staff_change_count.notna(),(season_staff.staff_change_count==0).astype(float),np.nan)
season_staff['full_staff_overhaul']=np.where(season_staff.staff_change_count.notna(),(season_staff.staff_change_count>=2).astype(float),np.nan)
mergecols=['season','team','hc_changed_season','oc_changed_season','dc_changed_season','hc_tenure_seasons','oc_tenure_seasons','dc_tenure_seasons','both_coords_changed','staff_change_count','full_staff_stable','full_staff_overhaul']
for c in mergecols[2:]:
    if c in d.columns:d=d.drop(columns=[c])
d=d.merge(season_staff[mergecols],on=['season','team'],how='left')

# Pregame career experience and historical team win rate for each staff role.
d=d.sort_values(['gameday_dt','game_id','team']).reset_index(drop=True)
for role,pfx in [('head_coach','hc'),('offensive_coordinator','oc'),('defensive_coordinator','dc')]:
    valid=d[role].notna()
    sub=d.loc[valid,[role,'gameday_dt','game_id','team','win']].copy().sort_values(['gameday_dt','game_id','team'])
    sub[f'{pfx}_prior_games']=sub.groupby(role).cumcount()
    sub[f'{pfx}_prior_wins']=sub.groupby(role).win.cumsum()-sub.win
    sub[f'{pfx}_prior_win_pct']=np.where(sub[f'{pfx}_prior_games']>0,sub[f'{pfx}_prior_wins']/sub[f'{pfx}_prior_games'],np.nan)
    d.loc[sub.index,f'{pfx}_prior_games']=sub[f'{pfx}_prior_games']
    d.loc[sub.index,f'{pfx}_prior_win_pct']=sub[f'{pfx}_prior_win_pct']

# Current-team HC stint games, including midseason changes.
d=d.sort_values(['team','gameday_dt','game_id']).reset_index(drop=True)
stint=np.full(len(d),np.nan)
for team,idx in d.groupby('team').groups.items():
    last=None; run=0
    for i in idx:
        cur=d.at[i,'head_coach']
        if cur is None or pd.isna(cur):run=0;last=None;continue
        if cur!=last:run=0
        stint[i]=run; run+=1; last=cur
d['hc_prior_games_current_stint']=stint

# Role experience across prior team-seasons (useful for new coordinators who are not new to the NFL role).
for role,pfx in [('offensive_coordinator','oc'),('defensive_coordinator','dc')]:
    temp=season_staff[season_staff[role].notna()][['season','team',role]].sort_values(['season','team']).copy()
    temp[f'{pfx}_prior_role_seasons']=temp.groupby(role).cumcount()
    d=d.merge(temp[['season','team',f'{pfx}_prior_role_seasons']],on=['season','team'],how='left')

# Interactions that capture implementation/continuity difficulty.
rest=num(d.get('rest_days',d.get('rest',np.nan)))
qb=num(d.get('qb_prior_starts',np.nan))
travel=num(d.get('travel_miles',0)).fillna(0)
week=num(d.week)
d['early_season_le4']=(week<=4).astype(int)
d['new_hc_early_season']=num(d.hc_changed_season).fillna(0)*d.early_season_le4
d['new_oc_early_season']=num(d.oc_changed_season).fillna(0)*d.early_season_le4
d['new_dc_early_season']=num(d.dc_changed_season).fillna(0)*d.early_season_le4
d['new_hc_young_qb']=((num(d.hc_changed_season)==1)&(qb<=16)).astype(int)
d['new_oc_young_qb']=((num(d.oc_changed_season)==1)&(qb<=16)).astype(int)
d['new_oc_inexperienced_qb']=((num(d.oc_changed_season)==1)&(qb<=32)).astype(int)
d['new_hc_short_week']=((num(d.hc_changed_season)==1)&(rest<=6)).astype(int)
d['new_oc_short_week']=((num(d.oc_changed_season)==1)&(rest<=6)).astype(int)
d['new_dc_short_week']=((num(d.dc_changed_season)==1)&(rest<=6)).astype(int)
d['staff_overhaul_short_week']=((num(d.full_staff_overhaul)==1)&(rest<=6)).astype(int)
d['staff_overhaul_long_trip']=((num(d.full_staff_overhaul)==1)&(travel>=1500)).astype(int)
d['new_oc_long_trip']=((num(d.oc_changed_season)==1)&(travel>=1500)).astype(int)
d['new_dc_long_trip']=((num(d.dc_changed_season)==1)&(travel>=1500)).astype(int)
if 'returning_offense_snap_share' in d:
    d['new_oc_low_off_continuity']=((num(d.oc_changed_season)==1)&(num(d.returning_offense_snap_share)<.60)).astype(int)
if 'returning_defense_snap_share' in d:
    d['new_dc_low_def_continuity']=((num(d.dc_changed_season)==1)&(num(d.returning_defense_snap_share)<.60)).astype(int)

staff_features=[c for c in d.columns if any(k in c for k in ['hc_','oc_','dc_','staff_','both_coords']) and c not in ['head_coach']]
staff_features=[c for c in staff_features if num(d[c]).notna().sum()>=150 and num(d[c]).nunique(dropna=True)>=2]
# Opponent-relative versions: positive = our side has the stronger/more stable coaching context.
for c in list(staff_features):
    opp=d[['game_id','team',c]].rename(columns={'team':'opponent',c:'opp_'+c})
    if 'opp_'+c in d.columns:d=d.drop(columns=['opp_'+c])
    d=d.merge(opp,on=['game_id','opponent'],how='left')
    if any(x in c for x in ['changed','overhaul','new_']):d['adv_'+c]=num(d['opp_'+c])-num(d[c])
    else:d['adv_'+c]=num(d[c])-num(d['opp_'+c])
staff_features += [c for c in d.columns if c.startswith('adv_') and c[4:] in staff_features]
staff_features=list(dict.fromkeys(staff_features))
print('staff features',len(staff_features))
d.to_parquet(OUT/'coaching_enriched_team_sides_2006_2025.parquet',index=False)

methods=pd.read_csv(METHODS)
price={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}
def pmask(track,p):lo,hi=tracks[track][p]; return d.season.between(lo,hi)
def method_mask(r):
    m=cond(d,r.feature1,r.op1,r.threshold1)
    if pd.notna(r.feature2):m &= cond(d,r.feature2,r.op2,r.threshold2)
    lo,hi=price[r.price_band]; m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='AWAY':m &= num(d.is_home).eq(0)
    elif r.venue=='HOME':m &= num(d.is_home).eq(1)
    return m

# Coaching veto search across every canonical method. Holdout is opened only after train+validation selection.
veto=[]
for _,r in methods.iterrows():
    track=r.track if r.track in tracks else 'LONG'; bm=method_mask(r)
    base_tr=metrics(d[bm&pmask(track,'train')]); base_va=metrics(d[bm&pmask(track,'validation')]); base_ho=metrics(d[bm&pmask(track,'holdout')])
    if not all([base_tr,base_va,base_ho]):continue
    pre=[]; tr=d[bm&pmask(track,'train')]
    for c in staff_features:
        vals=num(tr[c]).dropna()
        if len(vals)<25:continue
        for q in sorted(set(float(x) for x in vals.quantile([.15,.25,.35,.5,.65,.75,.85]).dropna())):
            for op in ['<=','>=']:
                risk=cond(d,c,op,q)
                def split(p):
                    b=d[bm&pmask(track,p)]; rr=d[bm&risk&pmask(track,p)]; ss=d[bm&~risk&pmask(track,p)]; return metrics(b),metrics(rr),metrics(ss)
                b1,x1,s1=split('train'); b2,x2,s2=split('validation')
                if not all([x1,s1,x2,s2]) or min(x1['n'],x2['n'])<4 or min(s1['n'],s2['n'])<12:continue
                if s1['n']<.5*b1['n'] or s2['n']<.5*b2['n']:continue
                if x1['loss_rate']<s1['loss_rate']+.07 or x2['loss_rate']<s2['loss_rate']+.07:continue
                if s1['roi']<b1['roi']-.005 or s2['roi']<b2['roi']-.005:continue
                score=(s1['roi']-b1['roi'])+(s2['roi']-b2['roi'])+1.2*((x1['loss_rate']-s1['loss_rate'])+(x2['loss_rate']-s2['loss_rate']))
                pre.append((score,c,op,q,risk))
    pre=sorted(pre,reverse=True)[:6]
    for score,c,op,q,risk in pre:
        h=d[bm&pmask(track,'holdout')]; rh=d[bm&risk&pmask(track,'holdout')]; sh=d[bm&~risk&pmask(track,'holdout')]
        bh,mr,ms=metrics(h),metrics(rh),metrics(sh)
        if not all([bh,mr,ms]) or ms['n']<15:continue
        confirmed=ms['roi']>=bh['roi']+.02 and ms['loss_rate']<=bh['loss_rate']-.02 and mr['losses']>mr['wins']
        veto.append({'method_id':r.method_id,'feature':c,'op':op,'threshold':q,'pre_score':score,'holdout_base_n':bh['n'],'holdout_base_roi':bh['roi'],'removed_wins':mr['wins'],'removed_losses':mr['losses'],'safe_n':ms['n'],'safe_roi':ms['roi'],'roi_change':ms['roi']-bh['roi'],'loss_rate_change':ms['loss_rate']-bh['loss_rate'],'confirmed':confirmed})
vd=pd.DataFrame(veto)
if len(vd):vd=vd.sort_values(['confirmed','roi_change'],ascending=[False,False])
vd.to_csv(OUT/'coaching_veto_candidates.csv',index=False)

# New coaching-driven methods. Thresholds from train; train+validation select; holdout only confirms.
rows=[]
core=[c for c in d.columns if c.startswith(('rank_edge_','recent_edge_','adv_')) and c not in staff_features]
core=[c for c in core if num(d[c]).notna().sum()>=500 and num(d[c]).nunique(dropna=True)>=5]
for track in tracks:
    train=d[pmask(track,'train')]
    # Staff rules first.
    staff_rules=[]
    for c in staff_features:
        vals=num(train[c]).dropna()
        if len(vals)<100:continue
        for q in sorted(set(float(x) for x in vals.quantile([.2,.35,.5,.65,.8]).dropna())):
            for op in ['<=','>=']:
                cm=cond(d,c,op,q)
                for pb,(lo,hi) in price.items():
                    pm=num(d.market_prob_use).between(lo,hi,inclusive='both')
                    for venue in ['ANY','AWAY','HOME']:
                        vm=pd.Series(True,index=d.index) if venue=='ANY' else num(d.is_home).eq(0 if venue=='AWAY' else 1)
                        m=cm&pm&vm
                        a=metrics(d[m&pmask(track,'train')]); b=metrics(d[m&pmask(track,'validation')])
                        if not a or not b or min(a['n'],b['n'])<20 or a['roi']<.08 or b['roi']<.08:continue
                        staff_rules.append((min(a['roi'],b['roi'])*math.sqrt(min(a['n'],b['n'])),c,op,q,pb,venue,m,a,b))
    staff_rules=sorted(staff_rules,reverse=True)[:140]
    # Staff standalone + staff x football signal pairs.
    frozen=[]
    for item in staff_rules:
        sc,c,op,q,pb,venue,m,a,b=item
        frozen.append(('STAFF',c,op,q,None,None,np.nan,pb,venue,m,a,b,sc))
    # Build a modest core pool from train-only thresholds, validated pre-holdout.
    core_rules=[]
    for c in core[:120]:
        vals=num(train[c]).dropna()
        if len(vals)<200:continue
        for q in sorted(set(float(x) for x in vals.quantile([.25,.5,.75]).dropna())):
            for op in ['<=','>=']:
                cm=cond(d,c,op,q)
                for pb,(lo,hi) in price.items():
                    pm=num(d.market_prob_use).between(lo,hi,inclusive='both')
                    for venue in ['ANY','AWAY','HOME']:
                        vm=pd.Series(True,index=d.index) if venue=='ANY' else num(d.is_home).eq(0 if venue=='AWAY' else 1)
                        m=cm&pm&vm; a=metrics(d[m&pmask(track,'train')]); b=metrics(d[m&pmask(track,'validation')])
                        if a and b and min(a['n'],b['n'])>=25 and a['roi']>.04 and b['roi']>.04:
                            core_rules.append((min(a['roi'],b['roi'])*math.sqrt(min(a['n'],b['n'])),c,op,q,pb,venue,m))
    core_rules=sorted(core_rules,reverse=True)[:160]
    for sr in staff_rules[:90]:
        _,c1,o1,q1,pb,v,m1,a1,b1=sr
        for cr in core_rules[:100]:
            _,c2,o2,q2,pb2,v2,m2=cr
            if pb!=pb2 or v!=v2:continue
            m=m1&m2; a=metrics(d[m&pmask(track,'train')]); b=metrics(d[m&pmask(track,'validation')])
            if not a or not b or min(a['n'],b['n'])<18 or a['roi']<.10 or b['roi']<.10:continue
            frozen.append(('STAFF+FOOTBALL',c1,o1,q1,c2,o2,q2,pb,v,m,a,b,min(a['roi'],b['roi'])*math.sqrt(min(a['n'],b['n']))))
    # Dedup before holdout by pre-holdout bet sets.
    frozen=sorted(frozen,key=lambda z:z[-1],reverse=True); kept=[]
    for z in frozen:
        trackmask=pmask(track,'train')|pmask(track,'validation'); ids=set(d.loc[z[9]&trackmask,'game_id'].astype(str)+'|'+d.loc[z[9]&trackmask,'team'].astype(str))
        if len(ids)<20:continue
        if any(len(ids&k[-1])/max(1,len(ids|k[-1]))>=.82 for k in kept):continue
        kept.append((*z,ids))
        if len(kept)>=180:break
    # Open holdout once.
    for z in kept:
        family,c1,o1,q1,c2,o2,q2,pb,v,m,a,b,pre,ids=z
        h=metrics(d[m&pmask(track,'holdout')]); full=metrics(d[m&(pmask(track,'train')|pmask(track,'validation')|pmask(track,'holdout'))])
        if not h or not full or h['n']<18 or h['roi']<.10 or full['roi']<.10:continue
        if full['active_seasons']>=6 and full['positive_seasons']/max(1,full['active_seasons'])<.60:continue
        rows.append({'track':track,'family':family,'feature1':c1,'op1':o1,'threshold1':q1,'feature2':c2,'op2':o2,'threshold2':q2,'price_band':pb,'venue':v,'train_n':a['n'],'train_roi':a['roi'],'validation_n':b['n'],'validation_roi':b['roi'],'holdout_n':h['n'],'holdout_roi':h['roi'],'full_n':full['n'],'full_wins':full['wins'],'full_roi':full['roi'],'positive_seasons':full['positive_seasons'],'active_seasons':full['active_seasons']})
res=pd.DataFrame(rows)
if len(res):
    res['score']=res.holdout_roi*np.sqrt(res.holdout_n)+.35*res.validation_roi*np.sqrt(res.validation_n)
    res=res.sort_values(['score','full_n'],ascending=[False,False])
res.to_csv(OUT/'new_coaching_methods.csv',index=False)

summary={'source_table':'creative' if CREATIVE.exists() else 'base','staff_features':len(staff_features),'canonical_methods_tested':int(len(methods)),'coaching_veto_candidates':int(len(vd)),'confirmed_coaching_vetoes':int(vd.confirmed.sum()) if len(vd) else 0,'new_coaching_methods_double_digit_holdout':int(len(res))}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACHING + EVERYTHING EXPANSION','',json.dumps(summary,indent=2),'','CONFIRMED COACHING VETOES']
if len(vd):
    for _,r in vd[vd.confirmed].head(30).iterrows():lines.append(f"{r.method_id}: veto {r.feature} {r.op} {r.threshold:.5g} | holdout ROI +{100*r.holdout_base_roi:.1f}% -> +{100*r.safe_roi:.1f}% | removed L/W {int(r.removed_losses)}/{int(r.removed_wins)}")
lines+=['','TOP NEW COACHING METHODS']
if len(res):
    for _,r in res.head(40).iterrows():
        rule=f"{r.feature1} {r.op1} {r.threshold1:.5g}"+(f" AND {r.feature2} {r.op2} {r.threshold2:.5g}" if pd.notna(r.feature2) else '')
        lines.append(f"{r.family} | {r.price_band} {r.venue} | {rule} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% | train {100*r.train_roi:+.1f}% val {100*r.validation_roi:+.1f}% frozen holdout {100*r.holdout_roi:+.1f}% | seasons {int(r.positive_seasons)}/{int(r.active_seasons)}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n'); print('\n'.join(lines))
