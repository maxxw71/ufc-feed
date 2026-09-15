from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import numpy as np

REPO=Path(__file__).resolve().parents[1]
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=REPO/'nfl'/'legacy_coaching_currentseason_audit'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def metrics(x):
    if x is None or len(x)==0:return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan}
    w=num(x.win); p=num(x.profit_units)
    return {'n':int(len(x)),'wins':int(w.sum()),'losses':int(len(x)-w.sum()),'win_pct':float(w.mean()),'roi':float(p.mean())}

def add_result_sides(s):
    rows=[]
    s=s[(s.game_type.astype(str)=='REG')].copy()
    for _,r in s.iterrows():
        hs=num(pd.Series([r.home_score])).iloc[0]; aws=num(pd.Series([r.away_score])).iloc[0]
        if pd.isna(hs) or pd.isna(aws):continue
        for tm,opp,pf,pa in [(r.home_team,r.away_team,hs,aws),(r.away_team,r.home_team,aws,hs)]:
            rows.append({'season':int(r.season),'week':int(r.week),'team':tm,'opponent':opp,'prior_game_win':int(pf>pa),'prior_game_margin':float(pf-pa)})
    return pd.DataFrame(rows)

parts=[]
for mid,fn in [('original','original_bets.csv'),('stricter','stricter_bets.csv')]:
    z=pd.read_csv(REPO/'nfl'/'legacy_live_home_opener_exact'/fn)
    z['method']=mid; z['team']=z.home_team; z['opponent']=z.away_team; z['profit_units']=num(z.profit)
    parts.append(z)
bets=pd.concat(parts,ignore_index=True)
bets=bets[(bets.game_type.astype(str)=='REG') & (num(bets.season)<=2025)].copy()

# Coaching/staff context already engineered prospectively from each season's staff state.
rich=pd.read_parquet(CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet')
rich=rich.drop_duplicates(['game_id','team'])
coach_features=[c for c in [
 'hc_changed_season','oc_changed_season','dc_changed_season','both_coords_changed','staff_change_count','full_staff_stable','full_staff_overhaul','major_staff_changes',
 'coachq_hc_prior_quality','coachq_oc_prior_quality','coachq_dc_prior_quality','coachq_staff_quality_mean','coachq_staff_quality_min','coachq_staff_upgrade_count','coachq_staff_downgrade_count',
 'opp_hc_changed_season','opp_oc_changed_season','opp_dc_changed_season','opp_both_coords_changed','opp_staff_change_count','opp_full_staff_stable','opp_full_staff_overhaul','opp_major_staff_changes',
 'opp_coachq_hc_prior_quality','opp_coachq_oc_prior_quality','opp_coachq_dc_prior_quality','opp_coachq_staff_quality_mean','opp_coachq_staff_quality_min','opp_coachq_staff_upgrade_count','opp_coachq_staff_downgrade_count'
] if c in rich.columns]
b=bets.merge(rich[['game_id','team']+coach_features],on=['game_id','team'],how='left')

# Reconstruct only games that occurred BEFORE each target game in that same season.
sched=pd.read_parquet(REPO/'relay'/'nfl'/'schedules_2006_2026.parquet')
sides=add_result_sides(sched)
teamw=pd.read_parquet(REPO/'relay'/'nfl'/'team_weekly_2006_2026.parquet')
stat_candidates=['passing_yards','rushing_yards','passing_epa','rushing_epa','passing_cpoe','interceptions','fumbles_lost','sacks_suffered','attempts','carries']
stat_cols=[c for c in stat_candidates if c in teamw.columns]

prior_rows=[]
for _,r in b.iterrows():
    s=int(r.season); w=int(r.week); tm=r.team; opp=r.opponent
    tp=sides[(sides.season==s)&(sides.team==tm)&(sides.week<w)]
    op=sides[(sides.season==s)&(sides.team==opp)&(sides.week<w)]
    rec={'game_id':r.game_id,'team':tm,'current_prior_games':len(tp),'current_prior_win_pct':tp.prior_game_win.mean() if len(tp) else np.nan,'current_prior_margin':tp.prior_game_margin.mean() if len(tp) else np.nan,
         'opp_current_prior_games':len(op),'opp_current_prior_win_pct':op.prior_game_win.mean() if len(op) else np.nan,'opp_current_prior_margin':op.prior_game_margin.mean() if len(op) else np.nan}
    if len(teamw) and {'season','week','team'}.issubset(teamw.columns):
        tw=teamw[(num(teamw.season)==s)&(teamw.team.astype(str)==str(tm))&(num(teamw.week)<w)]
        ow=teamw[(num(teamw.season)==s)&(teamw.team.astype(str)==str(opp))&(num(teamw.week)<w)]
        for c in stat_cols:
            rec['current_'+c]=num(tw[c]).mean() if len(tw) else np.nan
            rec['opp_current_'+c]=num(ow[c]).mean() if len(ow) else np.nan
    prior_rows.append(rec)
pr=pd.DataFrame(prior_rows).drop_duplicates(['game_id','team'])
b=b.merge(pr,on=['game_id','team'],how='left')

# Explicit interpretable risk/confirmation conditions. No threshold mining on holdout.
conditions={
 'team_hc_changed': lambda x:num(x.get('hc_changed_season')).eq(1),
 'team_oc_changed': lambda x:num(x.get('oc_changed_season')).eq(1),
 'team_dc_changed': lambda x:num(x.get('dc_changed_season')).eq(1),
 'team_both_coords_changed': lambda x:num(x.get('both_coords_changed')).eq(1),
 'team_full_staff_overhaul': lambda x:num(x.get('full_staff_overhaul')).eq(1),
 'team_2plus_staff_changes': lambda x:num(x.get('staff_change_count')).ge(2),
 'opponent_full_staff_overhaul': lambda x:num(x.get('opp_full_staff_overhaul')).eq(1),
 'opponent_2plus_staff_changes': lambda x:num(x.get('opp_staff_change_count')).ge(2),
 'prior_current_loss': lambda x:num(x.current_prior_games).ge(1)&num(x.current_prior_win_pct).lt(.5),
 'prior_current_win': lambda x:num(x.current_prior_games).ge(1)&num(x.current_prior_win_pct).gt(.5),
 'prior_current_negative_margin': lambda x:num(x.current_prior_games).ge(1)&num(x.current_prior_margin).lt(0),
 'prior_current_positive_margin': lambda x:num(x.current_prior_games).ge(1)&num(x.current_prior_margin).gt(0),
 'prior_current_loss_and_2plus_staff_changes': lambda x:num(x.current_prior_games).ge(1)&num(x.current_prior_win_pct).lt(.5)&num(x.get('staff_change_count')).ge(2),
}
# Add zero-crossing football form flags when fields exist.
for c in ['passing_epa','rushing_epa','passing_cpoe']:
    cc='current_'+c
    if cc in b.columns:
        conditions[f'prior_current_{c}_negative']=lambda x,cc=cc:num(x.current_prior_games).ge(1)&num(x[cc]).lt(0)
        conditions[f'prior_current_{c}_positive']=lambda x,cc=cc:num(x.current_prior_games).ge(1)&num(x[cc]).gt(0)

rows=[]
for mid,g in b.groupby('method'):
    for name,fn in conditions.items():
        try: flag=fn(g).fillna(False)
        except Exception: continue
        for era,label in [((2007,2013),'train'),((2014,2019),'validation'),((2020,2025),'holdout'),((2007,2025),'full')]:
            q=g[num(g.season).between(*era)]
            f=flag.loc[q.index]
            risk=metrics(q[f]); safe=metrics(q[~f])
            rows.append({'method':mid,'condition':name,'era':label,'risk_n':risk['n'],'risk_wins':risk['wins'],'risk_losses':risk['losses'],'risk_win_pct':risk['win_pct'],'risk_roi':risk['roi'],'safe_n':safe['n'],'safe_win_pct':safe['win_pct'],'safe_roi':safe['roi'],'roi_gap_safe_minus_risk':safe['roi']-risk['roi'] if pd.notna(safe['roi']) and pd.notna(risk['roi']) else np.nan})
res=pd.DataFrame(rows); res.to_csv(OUT/'condition_era_audit.csv',index=False)

# Require direction to repeat in validation and holdout; this is a stability screen, not automatic production deployment.
stable=[]
for (mid,cond),g in res.groupby(['method','condition']):
    d={r.era:r for _,r in g.iterrows()}
    if not all(k in d for k in ['train','validation','holdout','full']):continue
    va,ho,fu=d['validation'],d['holdout'],d['full']
    if va.risk_n>=3 and ho.risk_n>=3 and va.safe_n>=5 and ho.safe_n>=5 and va.roi_gap_safe_minus_risk>0 and ho.roi_gap_safe_minus_risk>0:
        stable.append({'method':mid,'condition':cond,'full_risk_n':fu.risk_n,'full_risk_win_pct':fu.risk_win_pct,'full_risk_roi':fu.risk_roi,'full_safe_n':fu.safe_n,'full_safe_win_pct':fu.safe_win_pct,'full_safe_roi':fu.safe_roi,'validation_gap':va.roi_gap_safe_minus_risk,'holdout_gap':ho.roi_gap_safe_minus_risk,'holdout_risk_n':ho.risk_n,'holdout_risk_roi':ho.risk_roi,'holdout_safe_roi':ho.safe_roi})
st=pd.DataFrame(stable).sort_values(['method','holdout_gap'],ascending=[True,False]) if stable else pd.DataFrame()
st.to_csv(OUT/'stable_context_signals.csv',index=False)

# Current-board facts from the fresh schedule-authoritative audit.
curp=REPO/'nfl'/'live_2026_current_snapshot'/'current_board_fresh_context.csv'
cur=pd.read_csv(curp) if curp.exists() else pd.DataFrame()
cur.to_csv(OUT/'current_board_copy.csv',index=False)

lines=['NFL LEGACY METHODS — COACHING + PRIOR CURRENT-SEASON FORM AUDIT','',
       'Exact historical home-opener bets only. Current-season features use games strictly before the target game.','Stable = risk direction repeated in validation and 2020-25 holdout with minimum subset sizes; not an automatic veto.','']
for mid in ['original','stricter']:
    base=metrics(b[b.method==mid]); lines.append(f"BASE {mid}: {base['wins']}-{base['losses']} win={base['win_pct']:.1%} ROI={base['roi']:+.1%}")
    if len(st):
        for _,r in st[st.method==mid].head(12).iterrows():
            lines.append(f"  {r.condition}: risk n={int(r.full_risk_n)} win={r.full_risk_win_pct:.1%} ROI={r.full_risk_roi:+.1%} | safe n={int(r.full_safe_n)} win={r.full_safe_win_pct:.1%} ROI={r.full_safe_roi:+.1%} | hold risk={r.holdout_risk_roi:+.1%} safe={r.holdout_safe_roi:+.1%}")
lines+=['','CURRENT BOARD (fresh identity + Week 1 evidence)']
if len(cur):
    for _,r in cur.iterrows():
        lines.append(f"{r.team} {r.selection}: status={r.target_status}; staff HC/OC/DC={r.get('head_coach_changed')}/{r.get('offensive_coordinator_changed')}/{r.get('defensive_coordinator_changed')}; prior2026={int(r.current_wins)}-{int(r.current_losses)} margin={r.current_point_margin_avg if pd.notna(r.current_point_margin_avg) else 'NA'}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
(OUT/'summary.json').write_text(json.dumps({'historical_bets':len(b),'coach_features':len(coach_features),'current_form_stat_fields':stat_cols,'stable_context_signals':len(st),'generated_from':'exact legacy bets + prospective staff context + prior current-season weekly stats'},indent=2,default=str))
print('\n'.join(lines))
