from __future__ import annotations

from pathlib import Path
import json, os
import numpy as np
import pandas as pd

ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
IN=ROOT/'rolling_roi_discovery'/'pregame_team_sides_2006_2026.parquet'
CONTEXT=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
FROZEN_WF=ROOT/'nfl_top_family_walkforward'/'walkforward_by_season.csv'
OUT=CTX/'method_audit'; OUT.mkdir(parents=True,exist_ok=True)

def met(x):
    n=len(x)
    if not n:return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi_pct':np.nan,'profit':0.0}
    p=float(x.bet_profit100.sum()); w=int(x.win.sum())
    return {'n':n,'wins':w,'losses':n-w,'win_pct':100*w/n,'roi_pct':p/n,'profit':p}

def base(df,w,m,loc):
    z=(df.week>=w)&(df.market_prob>=m)
    if loc=='HOME': z &= df.home_side.eq(1)
    elif loc=='ROAD': z &= df.home_side.eq(0)
    return z

def ypp(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.pre_yards_per_play>=p['oy'])&(df.opp_pre_def_ypp_allowed>=p['dy'])
def passrush(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_sacks_made<=p['sr'])&(df.opp_pre_sacks_suffered>=p['osa'])
def passelite(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_pass_epa<=p['pr'])&(df.opp_rank_def_pass_epa<=p['dr'])
def epaypp(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.edge_off_epa>=p['oe'])&(df.edge_def_ypp>=p['de'])
def scoringdef(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_scoring<=p['sr'])&(df.rank_points_allowed<=p['dr'])
def offvdef(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['orank'])&(df.opp_rank_def_epa<=p['odrank'])
def balanced(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['n'])&(df.rank_def_epa<=p['n'])
def defweak(df,p): return base(df,p['w'],p['m'],p['loc'])&(df.rank_def_epa<=p['dr'])&(df.opp_rank_off_epa>=p['oor'])

FNS={
 'YPP OFF vs LEAKY DEF':ypp,
 'PASS RUSH vs WEAK PROTECTION':passrush,
 'PASS OFF vs ELITE PASS DEF':passelite,
 'EPA + YPP DEFENSE':epaypp,
 'SCORING + DEFENSE':scoringdef,
 'ELITE OFF vs ELITE DEF':offvdef,
 'BALANCED ELITE':balanced,
 'ELITE DEF vs WEAK OFF':defweak,
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

print('[1/5] Replaying already-frozen yearly walk-forward parameters',flush=True)
if not FROZEN_WF.exists(): raise FileNotFoundError(f'Frozen walk-forward selection missing: {FROZEN_WF}')
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

wf=pd.read_csv(FROZEN_WF)
need={'family','test_season','params'}
if not need.issubset(wf.columns): raise RuntimeError(f'Frozen walk-forward file missing {need-set(wf.columns)}')
bet_parts=[]
for _,r in wf.iterrows():
    family=str(r.family)
    if family not in FNS or pd.isna(r.params): continue
    y=int(r.test_season); p=json.loads(str(r.params)); te=b[b.season.eq(y)]
    z=te[FNS[family](te,p).fillna(False)].copy()
    if len(z):
        z['family']=family; z['selected_params']=json.dumps(p,sort_keys=True)
        for src,dst in [('train_n','train_n'),('train_roi','train_roi'),('train_win','train_win')]:
            if src in wf.columns:z[dst]=r[src]
        bet_parts.append(z)
bets=pd.concat(bet_parts,ignore_index=True) if bet_parts else pd.DataFrame()
wf.to_csv(OUT/'method_walkforward_by_season.csv',index=False)

baseline=[]
for family in FNS:
    z=bets[bets.family.eq(family)].copy(); m=met(z); exp=EXPECTED[family]
    if (m['n'],m['wins'])!=exp:
        raise RuntimeError(f'BASELINE MISMATCH {family}: got {(m["n"],m["wins"])} expected {exp}. Audit aborted.')
    yrs=z.groupby('season').bet_profit100.sum() if len(z) else pd.Series(dtype=float)
    m['profitable_seasons']=int((yrs>0).sum()); m['seasons_with_bets']=len(yrs); m['family']=family
    baseline.append(m)
baseline=pd.DataFrame(baseline)
baseline.to_csv(OUT/'method_baseline_summary.csv',index=False)
print(baseline[['family','n','wins','losses','win_pct','roi_pct','profit']].to_string(index=False),flush=True)

print('[2/5] Joining enriched pregame context',flush=True)
ctx=pd.read_parquet(CONTEXT)
keys=['game_id','team']
context_cols=[c for c in ctx.columns if c not in {'season','week','opponent','home_side','moneyline','opp_moneyline','rest','opp_rest','gameday','gameday_dt','win'}]
teamctx=ctx[context_cols].drop_duplicates(keys)
joined=bets.merge(teamctx,on=keys,how='left',suffixes=('','_ctx'))
oppctx=teamctx.rename(columns={c:f'oppctx_{c}' for c in teamctx.columns if c not in {'game_id','team'}}).rename(columns={'team':'opponent'})
joined=joined.merge(oppctx,on=['game_id','opponent'],how='left')

def series(col):
    if col not in joined:return pd.Series(np.nan,index=joined.index)
    return pd.to_numeric(joined[col],errors='coerce')
def lt(col,v):
    s=series(col); out=pd.Series(pd.NA,index=joined.index,dtype='boolean'); k=s.notna(); out.loc[k]=s.loc[k].lt(v); return out
def ge(col,v):
    s=series(col); out=pd.Series(pd.NA,index=joined.index,dtype='boolean'); k=s.notna(); out.loc[k]=s.loc[k].ge(v); return out
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
losses=joined[joined.win.eq(0)].copy(); losses.to_csv(OUT/'method_loss_forensics.csv',index=False)

print('[3/5] Comparing candidate risk buckets inside each method',flush=True)
rows=[]; split_rows=[]
for family in FNS:
    z=joined[joined.family.eq(family)].copy(); bm=met(z)
    for name in FACTORS:
        raw=z[f'risk_{name}']; known=raw.notna(); risk=raw.fillna(False).astype(bool)
        rz=z[known & risk]; sz=z[known & ~risk]; rm=met(rz); sm=met(sz)
        vz=z[~risk]; vm=met(vz)
        rows.append({'family':family,'factor':name,'baseline_n':bm['n'],'baseline_wins':bm['wins'],'baseline_losses':bm['losses'],'baseline_win_pct':bm['win_pct'],'baseline_roi_pct':bm['roi_pct'],'baseline_profit':bm['profit'],
                     'known_n':int(known.sum()),'coverage_pct':100*known.mean(),'risk_n':rm['n'],'risk_wins':rm['wins'],'risk_losses':rm['losses'],'risk_win_pct':rm['win_pct'],'risk_roi_pct':rm['roi_pct'],'risk_profit':rm['profit'],
                     'safe_n':sm['n'],'safe_wins':sm['wins'],'safe_losses':sm['losses'],'safe_win_pct':sm['win_pct'],'safe_roi_pct':sm['roi_pct'],'safe_profit':sm['profit'],
                     'veto_n':vm['n'],'veto_wins':vm['wins'],'veto_losses':vm['losses'],'veto_win_pct':vm['win_pct'],'veto_roi_pct':vm['roi_pct'],'veto_profit':vm['profit'],
                     'wins_removed':rm['wins'],'losses_removed':rm['losses'],'roi_change_if_veto':vm['roi_pct']-bm['roi_pct'] if vm['n'] else np.nan})
        for label,years in [('2016_2020',(2016,2020)),('2021_2025',(2021,2025))]:
            q=z[z.season.between(*years)]; qr=q[f'risk_{name}']; qknown=qr.notna(); qrisk=qr.fillna(False).astype(bool)
            qrm=met(q[qknown&qrisk]); qsm=met(q[qknown&~qrisk])
            split_rows.append({'family':family,'factor':name,'split':label,'risk_n':qrm['n'],'risk_win_pct':qrm['win_pct'],'risk_roi_pct':qrm['roi_pct'],'safe_n':qsm['n'],'safe_win_pct':qsm['win_pct'],'safe_roi_pct':qsm['roi_pct']})
comp=pd.DataFrame(rows); splits=pd.DataFrame(split_rows)
comp.to_csv(OUT/'method_context_factor_comparison.csv',index=False); splits.to_csv(OUT/'method_context_time_splits.csv',index=False)

print('[4/5] Ranking descriptive candidates with anti-cherry-pick checks',flush=True)
rank=comp[(comp.known_n>=20)&(comp.risk_n>=8)&(comp.coverage_pct>=50)].copy()
rank['loss_capture_pct']=100*rank.losses_removed/rank.baseline_losses.replace(0,np.nan)
rank['win_sacrifice_pct']=100*rank.wins_removed/rank.baseline_wins.replace(0,np.nan)
rank['net_capture_edge']=rank.loss_capture_pct-rank.win_sacrifice_pct
def split_ok(r):
    q=splits[(splits.family==r.family)&(splits.factor==r.factor)]; checks=[]
    for _,a in q.iterrows():
        if a.risk_n>=3 and a.safe_n>=3 and pd.notna(a.risk_roi_pct) and pd.notna(a.safe_roi_pct):checks.append(a.risk_roi_pct<a.safe_roi_pct)
    return bool(checks) and all(checks)
rank['both_time_splits_risk_worse']=rank.apply(split_ok,axis=1)
rank=rank.sort_values(['both_time_splits_risk_worse','roi_change_if_veto','net_capture_edge'],ascending=[False,False,False])
rank.to_csv(OUT/'method_context_candidate_ranking.csv',index=False)

# Loss-level count of known risk flags makes forensic review fast without treating correlation as causation.
risk_cols=[f'risk_{x}' for x in FACTORS]
loss_summary=[]
for _,r in losses.iterrows():
    active=[x.replace('risk_','') for x in risk_cols if x in losses.columns and pd.notna(r[x]) and bool(r[x])]
    loss_summary.append({'family':r.family,'season':r.season,'week':r.week,'game_id':r.game_id,'team':r.team,'opponent':r.opponent,'moneyline':r.moneyline,'market_prob':r.market_prob,'active_risk_count':len(active),'active_risks':' | '.join(active)})
pd.DataFrame(loss_summary).to_csv(OUT/'method_loss_risk_summary.csv',index=False)

print('[5/5] Writing concise report',flush=True)
report=[]
for family in FNS:
    b0=baseline[baseline.family.eq(family)].iloc[0]
    report.append(f"\n{family}: {int(b0.wins)}-{int(b0.losses)} | win {b0.win_pct:.2f}% | ROI {b0.roi_pct:+.2f}%")
    q=rank[rank.family.eq(family)].head(6)
    for _,r in q.iterrows():
        report.append(f"  {r.factor}: risk {int(r.risk_n)} ({int(r.risk_wins)}-{int(r.risk_losses)}) ROI {r.risk_roi_pct:+.2f}% | remove {int(r.losses_removed)} L / {int(r.wins_removed)} W -> ROI {r.veto_roi_pct:+.2f}% ({r.roi_change_if_veto:+.2f} pts) | splits={'YES' if r.both_time_splits_risk_worse else 'NO'}")
text='\n'.join(report)+'\n'; (OUT/'method_context_report.txt').write_text(text)
print(text); print('Loss forensic rows:',len(losses),flush=True)
