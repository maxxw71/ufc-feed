from __future__ import annotations

from pathlib import Path
from itertools import product
import json, math, os
import numpy as np
import pandas as pd

ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
IN=ROOT/'rolling_roi_discovery'/'pregame_team_sides_2006_2026.parquet'
CONTEXT=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
OUT=CTX/'method_audit'; OUT.mkdir(parents=True,exist_ok=True)

def met(x):
    n=len(x)
    if not n:return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi_pct':np.nan,'profit':0.0}
    p=float(x.bet_profit100.sum()); w=int(x.win.sum())
    return {'n':n,'wins':w,'losses':n-w,'win_pct':100*w/n,'roi_pct':p/n,'profit':p}

def base(df,w,m,loc):
    z=(df.week>=w)&(df.market_prob>=m)
    if loc=='HOME':z &= df.home_side.eq(1)
    elif loc=='ROAD':z &= df.home_side.eq(0)
    return z

def ypp(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.pre_yards_per_play>=p['oy'])&(df.opp_pre_def_ypp_allowed>=p['dy'])
def passrush(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_sacks_made<=p['sr'])&(df.opp_pre_sacks_suffered>=p['osa'])
def passelite(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_pass_epa<=p['pr'])&(df.opp_rank_def_pass_epa<=p['dr'])
def epaypp(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.edge_off_epa>=p['oe'])&(df.edge_def_ypp>=p['de'])
def scoringdef(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_scoring<=p['sr'])&(df.rank_points_allowed<=p['dr'])
def offvdef(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['orank'])&(df.opp_rank_def_epa<=p['odrank'])
def balanced(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['n'])&(df.rank_def_epa<=p['n'])
def defweak(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_def_epa<=p['dr'])&(df.opp_rank_off_epa>=p['oor'])

FAMILIES={
 'YPP OFF vs LEAKY DEF':(ypp,[dict(w=w,m=m,loc=l,oy=oy,dy=dy) for w,m,l,oy,dy in product([4,5,6,7,8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5.5,5.8,6.0,6.2],[5.5,5.8,6.0,6.2])]),
 'PASS RUSH vs WEAK PROTECTION':(passrush,[dict(w=w,m=m,loc=l,sr=sr,osa=osa) for w,m,l,sr,osa in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[8,10,12],[2.5,3.0,3.5])]),
 'PASS OFF vs ELITE PASS DEF':(passelite,[dict(w=w,m=m,loc=l,pr=pr,dr=dr) for w,m,l,pr,dr in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10],[5,8,10])]),
 'EPA + YPP DEFENSE':(epaypp,[dict(w=w,m=m,loc=l,oe=oe,de=de) for w,m,l,oe,de in product([9,10,11,12],[.55,.60,.65,.70],['ANY','HOME','ROAD'],[8,10,12,14],[6,8,10,12])]),
 'SCORING + DEFENSE':(scoringdef,[dict(w=w,m=m,loc=l,sr=sr,dr=dr) for w,m,l,sr,dr in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10],[5,8,10])]),
 'ELITE OFF vs ELITE DEF':(offvdef,[dict(w=w,m=m,loc=l,orank=o,odrank=d) for w,m,l,o,d in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10,12],[5,8,10,12])]),
 'BALANCED ELITE':(balanced,[dict(w=w,m=m,loc=l,n=n) for w,m,l,n in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10,12])]),
 'ELITE DEF vs WEAK OFF':(defweak,[dict(w=w,m=m,loc=l,dr=d,oor=o) for w,m,l,d,o in product([5,6,7,8,9,10,11,12],[.55,.60,.65,.70],['ANY','HOME','ROAD'],[5,8,10,12],[22,25,28,30])]),
}
EXPECTED={
 'YPP OFF vs LEAKY DEF':(112,87),
 'PASS RUSH vs WEAK PROTECTION':(106,85),
 'PASS OFF vs ELITE PASS DEF':(100,68),
 'EPA + YPP DEFENSE':(67,58),
 'SCORING + DEFENSE':(70,55),
 'ELITE OFF vs ELITE DEF':(68,51),
 'BALANCED ELITE':(56,42),
 'ELITE DEF vs WEAK OFF':(93,73),
}

print('[1/5] Reproducing frozen existing walk-forward methods',flush=True)
g=pd.read_parquet(IN)
for c in g.columns:
    if c not in {'game_id','team','opponent','roof','surface'}:
        try:g[c]=pd.to_numeric(g[c],errors='ignore')
        except Exception:pass
b=g[g.moneyline.notna() & g.market_prob.notna() & g.win.isin([0.0,1.0]) & (g.prior_games>=3) & (g.market_prob>.50)].copy()
for c in ['season','week','market_prob','moneyline','home_side','div_game']:
    if c in b:b[c]=pd.to_numeric(b[c],errors='coerce')
for name in ['off_epa','def_epa','pass_epa','def_pass_epa','rush_epa','def_rush_epa','scoring','points_allowed','turnover','sacks_made','def_ypp']:
    a=f'rank_{name}'; o=f'opp_rank_{name}'
    if a in b and o in b:b[f'edge_{name}']=pd.to_numeric(b[o],errors='coerce')-pd.to_numeric(b[a],errors='coerce')

wf_rows=[]; bet_parts=[]
for family,(fn,grid) in FAMILIES.items():
    for y in range(2016,2027):
        tr=b[b.season<y]; te=b[b.season==y]; cand=[]
        for p in grid:
            x=tr[fn(tr,p).fillna(False)]; m=met(x)
            if m['n']<50 or m['win_pct']<68 or m['roi_pct']<3:continue
            recent=x[x.season>=max(2006,y-5)]; rm=met(recent)
            if rm['n']>=15 and rm['roi_pct']<=0:continue
            score=min(m['roi_pct']/100,.20)*math.sqrt(m['n']) + .25*max(0,m['win_pct']/100-.68)*math.sqrt(m['n'])
            cand.append((score,p,m))
        if not cand:continue
        cand.sort(key=lambda q:q[0],reverse=True); _,p,tm=cand[0]
        z=te[fn(te,p).fillna(False)].copy(); hm=met(z)
        wf_rows.append({'family':family,'test_season':y,'params':json.dumps(p,sort_keys=True),'train_n':tm['n'],'train_win_pct':tm['win_pct'],'train_roi_pct':tm['roi_pct'],**{f'test_{k}':v for k,v in hm.items()}})
        if len(z):
            z['family']=family; z['selected_params']=json.dumps(p,sort_keys=True); z['train_n']=tm['n']; z['train_roi_pct']=tm['roi_pct']; z['train_win_pct']=tm['win_pct']
            bet_parts.append(z)
wf=pd.DataFrame(wf_rows); bets=pd.concat(bet_parts,ignore_index=True) if bet_parts else pd.DataFrame()
wf.to_csv(OUT/'method_walkforward_by_season.csv',index=False)

baseline=[]
for family in FAMILIES:
    z=bets[bets.family.eq(family)].copy(); m=met(z)
    exp=EXPECTED[family]
    if (m['n'],m['wins'])!=exp:
        raise RuntimeError(f'BASELINE MISMATCH {family}: got {(m["n"],m["wins"])} expected {exp}. Audit aborted.')
    yrs=z.groupby('season').bet_profit100.sum() if len(z) else pd.Series(dtype=float)
    m['profitable_seasons']=int((yrs>0).sum()); m['seasons_with_bets']=len(yrs); m['family']=family
    baseline.append(m)
baseline=pd.DataFrame(baseline)
baseline.to_csv(OUT/'method_baseline_summary.csv',index=False)
print(baseline[['family','n','wins','losses','win_pct','roi_pct','profit']].to_string(index=False),flush=True)

print('[2/5] Joining richer pregame context',flush=True)
ctx=pd.read_parquet(CONTEXT)
keys=['game_id','team']
# Keep only context fields that are not already part of the original betting feature table.
context_cols=[c for c in ctx.columns if c not in {'season','week','opponent','home_side','moneyline','opp_moneyline','rest','opp_rest','gameday','gameday_dt','win'}]
teamctx=ctx[context_cols].drop_duplicates(keys)
joined=bets.merge(teamctx,on=keys,how='left',suffixes=('','_ctx'))
oppctx=teamctx.rename(columns={c:f'oppctx_{c}' for c in teamctx.columns if c not in {'game_id','team'}}).rename(columns={'team':'opponent'})
joined=joined.merge(oppctx,on=['game_id','opponent'],how='left')

# Fixed, football-motivated candidate risk definitions. Unknown data remains NA.
def lt(col,v):
    s=pd.to_numeric(joined.get(col),errors='coerce'); out=pd.Series(pd.NA,index=joined.index,dtype='boolean'); k=s.notna(); out.loc[k]=s.loc[k].lt(v); return out
def ge(col,v):
    s=pd.to_numeric(joined.get(col),errors='coerce'); out=pd.Series(pd.NA,index=joined.index,dtype='boolean'); k=s.notna(); out.loc[k]=s.loc[k].ge(v); return out
def eq1(col): return ge(col,1)
FACTORS={
 'QB_CHANGED':eq1('qb_changed_from_prior_season'),
 'QB_PRIOR_STARTS_LT10':lt('qb_prior_starts',10),
 'HC_CHANGED':eq1('head_coach_changed'),
 'OC_CHANGED':eq1('offensive_coordinator_changed'),
 'DC_CHANGED':eq1('defensive_coordinator_changed'),
 'STAFF_CHANGES_GE2':ge('major_staff_changes',2),
 'RETURN_OL_LT60':lt('returning_ol_snap_share',.60),
 'RETURN_OL_LT70':lt('returning_ol_snap_share',.70),
 'RETURN_OFF_LT60':lt('returning_offense_snap_share',.60),
 'RETURN_OFF_LT70':lt('returning_offense_snap_share',.70),
 'RETURN_DEF_LT60':lt('returning_defense_snap_share',.60),
 'RETURN_DEF_LT70':lt('returning_defense_snap_share',.70),
 'RETURN_SKILL_LT60':lt('returning_skill_snap_share',.60),
 'RETURN_SKILL_LT70':lt('returning_skill_snap_share',.70),
 'OUT_PLAYERS_GE2':ge('out_players',2),
 'OUT_OL_GE1':ge('out_ol',1),
 'OUT_QB_GE1':ge('out_qbs',1),
 'PRESEASON_LOSING':lt('preseason_win_pct',.50),
 'PRESEASON_FORM_DISADVANTAGE':eq1('preseason_form_disadvantage'),
 'OPP_QB_CHANGED':eq1('oppctx_qb_changed_from_prior_season'),
 'OPP_QB_PRIOR_STARTS_LT10':lt('oppctx_qb_prior_starts',10),
 'OPP_HC_CHANGED':eq1('oppctx_head_coach_changed'),
 'OPP_OC_CHANGED':eq1('oppctx_offensive_coordinator_changed'),
 'OPP_DC_CHANGED':eq1('oppctx_defensive_coordinator_changed'),
 'OPP_STAFF_CHANGES_GE2':ge('oppctx_major_staff_changes',2),
}
for name,s in FACTORS.items():joined[f'risk_{name}']=s
joined.to_csv(OUT/'method_all_bets_context.csv',index=False)
joined[joined.win.eq(0)].to_csv(OUT/'method_loss_forensics.csv',index=False)

print('[3/5] Comparing candidate risk buckets inside each existing method',flush=True)
rows=[]; split_rows=[]
for family in FAMILIES:
    z=joined[joined.family.eq(family)].copy(); bm=met(z)
    for name in FACTORS:
        raw=z[f'risk_{name}']; known=raw.notna(); risk=raw.fillna(False).astype(bool)
        rz=z[known & risk]; sz=z[known & ~risk]
        rm=met(rz); sm=met(sz)
        # A prospective veto removes only known-risk rows; unknown rows are retained, never silently called safe.
        vz=z[~risk]; vm=met(vz)
        rows.append({
          'family':family,'factor':name,'baseline_n':bm['n'],'baseline_wins':bm['wins'],'baseline_losses':bm['losses'],'baseline_win_pct':bm['win_pct'],'baseline_roi_pct':bm['roi_pct'],'baseline_profit':bm['profit'],
          'known_n':int(known.sum()),'coverage_pct':100*known.mean(),'risk_n':rm['n'],'risk_wins':rm['wins'],'risk_losses':rm['losses'],'risk_win_pct':rm['win_pct'],'risk_roi_pct':rm['roi_pct'],'risk_profit':rm['profit'],
          'safe_n':sm['n'],'safe_wins':sm['wins'],'safe_losses':sm['losses'],'safe_win_pct':sm['win_pct'],'safe_roi_pct':sm['roi_pct'],'safe_profit':sm['profit'],
          'veto_n':vm['n'],'veto_wins':vm['wins'],'veto_losses':vm['losses'],'veto_win_pct':vm['win_pct'],'veto_roi_pct':vm['roi_pct'],'veto_profit':vm['profit'],
          'wins_removed':rm['wins'],'losses_removed':rm['losses'],'roi_change_if_veto':vm['roi_pct']-bm['roi_pct'] if vm['n'] else np.nan,
        })
        for label,years in [('2016_2020',(2016,2020)),('2021_2025',(2021,2025))]:
            q=z[z.season.between(*years)]; qr=q[f'risk_{name}']; qknown=qr.notna(); qrisk=qr.fillna(False).astype(bool)
            qrm=met(q[qknown&qrisk]); qsm=met(q[qknown&~qrisk])
            split_rows.append({'family':family,'factor':name,'split':label,'risk_n':qrm['n'],'risk_win_pct':qrm['win_pct'],'risk_roi_pct':qrm['roi_pct'],'safe_n':qsm['n'],'safe_win_pct':qsm['win_pct'],'safe_roi_pct':qsm['roi_pct']})
comp=pd.DataFrame(rows); splits=pd.DataFrame(split_rows)
comp.to_csv(OUT/'method_context_factor_comparison.csv',index=False); splits.to_csv(OUT/'method_context_time_splits.csv',index=False)

print('[4/5] Ranking descriptive candidates with anti-cherry-pick checks',flush=True)
# This is a diagnostic ranking, NOT a production rule selector. Minimum sample/coverage and
# time-split direction checks prevent one-loss anecdotes from surfacing as recommendations.
rank=comp.copy()
rank=rank[(rank.known_n>=20)&(rank.risk_n>=8)&(rank.coverage_pct>=50)].copy()
rank['loss_capture_pct']=100*rank.losses_removed/rank.baseline_losses.replace(0,np.nan)
rank['win_sacrifice_pct']=100*rank.wins_removed/rank.baseline_wins.replace(0,np.nan)
rank['net_capture_edge']=rank.loss_capture_pct-rank.win_sacrifice_pct
# Confirm that risk ROI is worse than safe ROI in both broad time splits whenever both have >=3 bets.
def split_ok(r):
    q=splits[(splits.family==r.family)&(splits.factor==r.factor)]
    checks=[]
    for _,a in q.iterrows():
        if a.risk_n>=3 and a.safe_n>=3 and pd.notna(a.risk_roi_pct) and pd.notna(a.safe_roi_pct): checks.append(a.risk_roi_pct<a.safe_roi_pct)
    return bool(checks) and all(checks)
rank['both_time_splits_risk_worse']=rank.apply(split_ok,axis=1)
rank=rank.sort_values(['both_time_splits_risk_worse','roi_change_if_veto','net_capture_edge'],ascending=[False,False,False])
rank.to_csv(OUT/'method_context_candidate_ranking.csv',index=False)

print('[5/5] Writing concise report',flush=True)
report=[]
for family in FAMILIES:
    b0=baseline[baseline.family.eq(family)].iloc[0]
    report.append(f"\n{family}: {int(b0.wins)}-{int(b0.losses)} | win {b0.win_pct:.2f}% | ROI {b0.roi_pct:+.2f}%")
    q=rank[rank.family.eq(family)].head(6)
    for _,r in q.iterrows():
        report.append(f"  {r.factor}: risk {int(r.risk_n)} ({int(r.risk_wins)}-{int(r.risk_losses)}) ROI {r.risk_roi_pct:+.2f}% | remove {int(r.losses_removed)} L / {int(r.wins_removed)} W -> ROI {r.veto_roi_pct:+.2f}% ({r.roi_change_if_veto:+.2f} pts) | splits={'YES' if r.both_time_splits_risk_worse else 'NO'}")
text='\n'.join(report)+'\n'
(OUT/'method_context_report.txt').write_text(text)
print(text)
print('Loss forensic rows:',int((joined.win==0).sum()))
