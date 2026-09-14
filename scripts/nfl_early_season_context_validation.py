from __future__ import annotations

from pathlib import Path
import json
import os
import numpy as np
import pandas as pd

SOURCE_ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
context=pd.read_parquet(CTX/'derived'/'team_game_pregame_context_2006_2026.parquet')
sched=pd.read_parquet(SOURCE_ROOT/'data'/'raw'/'schedules_2006_2026.parquet')
OUT=CTX/'validation'
OUT.mkdir(parents=True,exist_ok=True)

def implied(o):
    if pd.isna(o) or float(o)==0:return np.nan
    o=float(o)
    return 100/(o+100) if o>0 else abs(o)/(abs(o)+100)

def profit100(o,win):
    if pd.isna(o):return np.nan
    o=float(o)
    if not win:return -100.0
    return o if o>0 else 10000/abs(o)

# Result map, one row per team-game.
parts=[]
for side,opp in [('home','away'),('away','home')]:
    d=pd.DataFrame({
      'game_id':sched.game_id,'team':sched[f'{side}_team'],
      'pf':pd.to_numeric(sched[f'{side}_score'],errors='coerce'),
      'pa':pd.to_numeric(sched[f'{opp}_score'],errors='coerce')})
    parts.append(d)
r=pd.concat(parts,ignore_index=True)
r['win']=(r.pf>r.pa).astype(float)
b=context.merge(r[['game_id','team','win']],on=['game_id','team'],how='left')
b=b[(b.season.between(2014,2025)) & (b.week<=3) & b.moneyline.notna() & b.opp_moneyline.notna() & b.win.notna()].copy()
b['team_imp']=b.moneyline.map(implied); b['opp_imp']=b.opp_moneyline.map(implied)
b['novig_prob']=b.team_imp/(b.team_imp+b.opp_imp)
b=b[b.novig_prob>.5].copy()
b['profit100']=[profit100(o,w) for o,w in zip(b.moneyline,b.win)]

# Fixed football-motivated definitions. These are descriptive validation buckets,
# not an optimizer and not automatic production vetoes.
FACTORS={
 'QB_CHANGED': lambda d: d.qb_changed_from_prior_season.eq(1),
 'HC_CHANGED': lambda d: d.head_coach_changed.eq(1),
 'RETURN_OFF_LT_50': lambda d: pd.to_numeric(d.returning_offense_snap_share,errors='coerce')<.50,
 'RETURN_OFF_LT_60': lambda d: pd.to_numeric(d.returning_offense_snap_share,errors='coerce')<.60,
 'RETURN_OFF_LT_70': lambda d: pd.to_numeric(d.returning_offense_snap_share,errors='coerce')<.70,
 'RETURN_DEF_LT_50': lambda d: pd.to_numeric(d.returning_defense_snap_share,errors='coerce')<.50,
 'RETURN_DEF_LT_60': lambda d: pd.to_numeric(d.returning_defense_snap_share,errors='coerce')<.60,
 'RETURN_DEF_LT_70': lambda d: pd.to_numeric(d.returning_defense_snap_share,errors='coerce')<.70,
 'RETURN_OL_LT_60': lambda d: pd.to_numeric(d.returning_ol_snap_share,errors='coerce')<.60,
 'RETURN_OL_LT_70': lambda d: pd.to_numeric(d.returning_ol_snap_share,errors='coerce')<.70,
 'RETURN_SKILL_LT_60': lambda d: pd.to_numeric(d.returning_skill_snap_share,errors='coerce')<.60,
 'RETURN_SKILL_LT_70': lambda d: pd.to_numeric(d.returning_skill_snap_share,errors='coerce')<.70,
}
# Preseason hypotheses are prespecified from football logic and the 2026 Chargers audit.
# The audited source is record/win-rate only; do not invent scoring margin.
if 'preseason_win_pct' in b:
    FACTORS['PRESEASON_LOSING']=lambda d: pd.to_numeric(d.preseason_win_pct,errors='coerce')<.50
    FACTORS['PRESEASON_WINLESS']=lambda d: pd.to_numeric(d.preseason_win_pct,errors='coerce').eq(0)
if 'preseason_form_disadvantage' in b:
    FACTORS['PRESEASON_FORM_DISADVANTAGE']=lambda d: pd.to_numeric(d.preseason_form_disadvantage,errors='coerce').eq(1)
if 'both_teams_preseason_losing' in b:
    FACTORS['BOTH_PRESEASON_LOSING']=lambda d: pd.to_numeric(d.both_teams_preseason_losing,errors='coerce').eq(1)
# Coordinator factors are included only if meaningful coverage exists.
if 'offensive_coordinator_changed' in b and 'offensive_coordinator' in b and b.offensive_coordinator.notna().sum()>=100:
    FACTORS['OC_CHANGED']=lambda d:d.offensive_coordinator_changed.eq(1)
if 'defensive_coordinator_changed' in b and 'defensive_coordinator' in b and b.defensive_coordinator.notna().sum()>=100:
    FACTORS['DC_CHANGED']=lambda d:d.defensive_coordinator_changed.eq(1)


def stats(z):
    n=len(z)
    if not n:return None
    old=z[z.season<=2019]; new=z[z.season>=2020]
    yearly=[]
    for y,q in z.groupby('season'):
        yearly.append({'season':int(y),'n':len(q),'win_pct':100*q.win.mean(),'roi_pct':100*q.profit100.sum()/(100*len(q))})
    return {
      'n':n,'wins':int(z.win.sum()),'losses':int(n-z.win.sum()),'win_pct':100*z.win.mean(),
      'roi_pct':100*z.profit100.sum()/(100*n),'avg_moneyline':float(z.moneyline.mean()),'avg_novig_prob':float(z.novig_prob.mean()),
      'old_n':len(old),'old_win_pct':100*old.win.mean() if len(old) else None,'old_roi_pct':100*old.profit100.sum()/(100*len(old)) if len(old) else None,
      'new_n':len(new),'new_win_pct':100*new.win.mean() if len(new) else None,'new_roi_pct':100*new.profit100.sum()/(100*len(new)) if len(new) else None,
      'profitable_years':sum(y['roi_pct']>0 for y in yearly),'years_with_bets':len(yearly),'yearly':yearly,
    }

rows=[]; detail={}
for floor in [.60,.65,.70,.75,.80]:
    base=b[b.novig_prob>=floor].copy()
    bs=stats(base)
    if bs:
        rows.append({'market_floor':floor,'factor':'BASE','bucket':'ALL',**{k:v for k,v in bs.items() if k!='yearly'}})
        detail[f'{floor:.2f}_BASE']=bs
    for name,fn in FACTORS.items():
        raw=fn(base)
        known=raw.notna() if hasattr(raw,'notna') else pd.Series(True,index=base.index)
        risk=raw.fillna(False).astype(bool)
        # Unknown context must never silently become a no-risk observation.
        for bucket,m in [('RISK',known & risk),('NO_RISK',known & ~risk)]:
            z=base[m].copy(); st=stats(z)
            if st:
                rows.append({'market_floor':floor,'factor':name,'bucket':bucket,**{k:v for k,v in st.items() if k!='yearly'}})
                detail[f'{floor:.2f}_{name}_{bucket}']=st

# Simple prespecified risk counts at the Chargers-like big-favorite level.
z=b[b.novig_prob>=.75].copy()
component_names=['QB_CHANGED','HC_CHANGED','RETURN_OFF_LT_60','RETURN_DEF_LT_60','RETURN_OL_LT_60','RETURN_SKILL_LT_60','PRESEASON_LOSING']
components=[]
for name in component_names:
    if name in FACTORS:
        raw=FACTORS[name](z)
        components.append(raw.fillna(False).astype(int))
if components:
    z['risk_count']=sum(components)
    for k in [1,2,3]:
        st=stats(z[z.risk_count>=k])
        if st:
            rows.append({'market_floor':.75,'factor':f'RISK_COUNT_GE_{k}','bucket':'RISK',**{kk:vv for kk,vv in st.items() if kk!='yearly'}})
            detail[f'0.75_RISK_COUNT_GE_{k}']=st

out=pd.DataFrame(rows).sort_values(['market_floor','factor','bucket'])
out.to_csv(OUT/'early_season_context_summary.csv',index=False)
(OUT/'early_season_context_detail.json').write_text(json.dumps(detail,indent=2,default=str))

comp=[]
for floor in [.60,.65,.70,.75,.80]:
    for name in FACTORS:
        a=out[(out.market_floor==floor)&(out.factor==name)&(out.bucket=='RISK')]
        c=out[(out.market_floor==floor)&(out.factor==name)&(out.bucket=='NO_RISK')]
        if len(a) and len(c):
            A=a.iloc[0]; C=c.iloc[0]
            comp.append({'market_floor':floor,'factor':name,'risk_n':int(A.n),'risk_win_pct':A.win_pct,'risk_roi_pct':A.roi_pct,
                         'no_risk_n':int(C.n),'no_risk_win_pct':C.win_pct,'no_risk_roi_pct':C.roi_pct,
                         'win_pct_gap_risk_minus_safe':A.win_pct-C.win_pct,'roi_gap_risk_minus_safe':A.roi_pct-C.roi_pct})
compdf=pd.DataFrame(comp)
compdf.to_csv(OUT/'early_season_risk_comparison.csv',index=False)

# Compact preseason-only report for direct inspection of the hypothesis.
pre_names=[x for x in FACTORS if x.startswith('PRESEASON_') or x=='BOTH_PRESEASON_LOSING']
if len(compdf):
    compdf[compdf.factor.isin(pre_names)].to_csv(OUT/'early_season_preseason_comparison.csv',index=False)

print('=== FULL EARLY-SEASON SUMMARY ===')
print(out.to_string(index=False))
print('\n=== PRESEASON COMPARISONS ===')
if len(compdf):
    print(compdf[compdf.factor.isin(pre_names)].to_string(index=False))
