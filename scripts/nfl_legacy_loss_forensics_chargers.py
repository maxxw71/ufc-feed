from pathlib import Path
import os, json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'legacy_loss_forensics_chargers'; OUT.mkdir(parents=True,exist_ok=True)
ENR=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'

ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}
def num(x): return pd.to_numeric(x,errors='coerce')
def met(x):
    if not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit_units.mean())}

# Exact currently-live legacy bet sets.
o=pd.read_csv(REPO/'nfl/legacy_live_home_opener_exact/original_bets.csv',low_memory=False)
s=pd.read_csv(REPO/'nfl/legacy_live_home_opener_exact/stricter_bets.csv',low_memory=False)
sec=pd.read_csv(REPO/'nfl/legacy_live_secondary_exact/bets.csv',low_memory=False)

def norm_home(df,name):
    z=df.copy(); z['method']=name; z['team']=z.home_team.replace(ALIASES); z['opponent']=z.away_team.replace(ALIASES)
    z['moneyline']=num(z.home_moneyline); z['win']=num(z.win); z['profit_units']=num(z.profit)
    keep=['method','season','week','game_id','team','opponent','moneyline','win','profit_units']
    for c in ['prior_win_pct','opponent_prior_win_pct','edge','run_rank']: 
        if c in z: keep.append(c)
    return z[keep]
bets=pd.concat([norm_home(o,'original'),norm_home(s,'stricter'),sec],ignore_index=True,sort=False)
bets['team']=bets.team.replace(ALIASES); bets['opponent']=bets.opponent.replace(ALIASES)
bets=bets[num(bets.win).isin([0,1]) & num(bets.moneyline).notna()].copy()

# Rich pregame context for selected side and opponent.
d=pd.read_parquet(ENR)
d=d[d.season.between(2006,2025)].copy(); d['team']=d.team.replace(ALIASES); d['opponent']=d.opponent.replace(ALIASES)
sel=d.sort_values(['season','week']).drop_duplicates(['game_id','team'],keep='last')
base_cols=['game_id','team']
feature_candidates=[
 'hc_changed_season','oc_changed_season','dc_changed_season','both_coords_changed','staff_change_count','full_staff_stable','full_staff_overhaul',
 'hc_tenure_seasons','oc_tenure_seasons','dc_tenure_seasons','hc_prior_games','oc_prior_games','dc_prior_games','hc_prior_win_pct','oc_prior_win_pct','dc_prior_win_pct',
 'qb_changed','qb_changed_last_game','qb_prior_starts','returning_offense_share','returning_defense_share','returning_ol_share','returning_skill_share',
 'new_hc_early_season','new_oc_early_season','new_dc_early_season','new_hc_young_qb','new_oc_young_qb','new_oc_inexperienced_qb',
 'new_oc_low_off_continuity','new_dc_low_def_continuity','adv_skill_unavail_equiv','adv_ol_unavail_equiv','adv_qb_unavail_equiv'
]
feat=[c for c in feature_candidates if c in sel.columns]
teamctx=sel[base_cols+feat].copy().rename(columns={c:'team_'+c for c in feat})
oppctx=sel[base_cols+feat].copy().rename(columns={'team':'opponent',**{c:'opp_'+c for c in feat}})
bets=bets.merge(teamctx,on=['game_id','team'],how='left').merge(oppctx,on=['game_id','opponent'],how='left')

# Same-season preseason record for both teams. This is available before Week 1/2 and directly tests the Chargers 1-2 idea.
g=pd.read_parquet(SCHED).copy()
for c in ['home_team','away_team']: g[c]=g[c].replace(ALIASES)
gt='game_type' if 'game_type' in g.columns else 'season_type'
pre=g[g[gt].astype(str).str.upper().eq('PRE')].copy()
ps=[]
for _,r in pre.iterrows():
    if pd.isna(r.get('home_score')) or pd.isna(r.get('away_score')): continue
    hs=float(r.home_score); aas=float(r.away_score)
    ps += [
      {'season':int(r.season),'team':r.home_team,'pre_win':int(hs>aas),'pre_loss':int(hs<aas),'pre_pf':hs,'pre_pa':aas},
      {'season':int(r.season),'team':r.away_team,'pre_win':int(aas>hs),'pre_loss':int(aas<hs),'pre_pf':aas,'pre_pa':hs},
    ]
ps=pd.DataFrame(ps)
if len(ps):
    pr=ps.groupby(['season','team']).agg(pre_wins=('pre_win','sum'),pre_losses=('pre_loss','sum'),pre_games=('pre_win','size'),pre_pf=('pre_pf','sum'),pre_pa=('pre_pa','sum')).reset_index()
    pr['pre_win_pct']=pr.pre_wins/pr.pre_games; pr['pre_margin_pg']=(pr.pre_pf-pr.pre_pa)/pr.pre_games
    bets=bets.merge(pr.rename(columns={c:'team_'+c for c in pr.columns if c not in ['season','team']}),on=['season','team'],how='left')
    bets=bets.merge(pr.rename(columns={'team':'opponent',**{c:'opp_'+c for c in pr.columns if c not in ['season','team']}}),on=['season','opponent'],how='left')

def bcol(name): return num(bets[name]) if name in bets else pd.Series(np.nan,index=bets.index)
flags={}
def add(name,series): flags[name]=series.fillna(False).astype(bool)
for prefix in ['team','opp']:
    for c in ['hc_changed_season','oc_changed_season','dc_changed_season','both_coords_changed','full_staff_overhaul','new_oc_low_off_continuity','new_dc_low_def_continuity']:
        col=f'{prefix}_{c}'
        if col in bets:add(col,bcol(col)>=1)
    col=f'{prefix}_staff_change_count'
    if col in bets:
        add(f'{prefix}_staff_changes_ge2',bcol(col)>=2); add(f'{prefix}_staff_changes_ge3',bcol(col)>=3)
    col=f'{prefix}_qb_prior_starts'
    if col in bets:add(f'{prefix}_qb_prior_starts_le16',bcol(col)<=16)
for prefix in ['team','opp']:
    if f'{prefix}_pre_win_pct' in bets:
        add(f'{prefix}_preseason_losing',bcol(f'{prefix}_pre_win_pct')<.5)
        add(f'{prefix}_preseason_winless',bcol(f'{prefix}_pre_wins')==0)
        add(f'{prefix}_preseason_one_win_or_less',bcol(f'{prefix}_pre_wins')<=1)
        add(f'{prefix}_preseason_1_2_exact',(bcol(f'{prefix}_pre_games')==3)&(bcol(f'{prefix}_pre_wins')==1)&(bcol(f'{prefix}_pre_losses')==2))
        add(f'{prefix}_preseason_negative_margin',bcol(f'{prefix}_pre_margin_pg')<0)
if 'team_pre_win_pct' in bets and 'opp_pre_win_pct' in bets:
    add('preseason_record_disadvantage',bcol('team_pre_win_pct')<bcol('opp_pre_win_pct'))
    add('preseason_record_edge_le_minus_0_25',(bcol('team_pre_win_pct')-bcol('opp_pre_win_pct'))<=-.25)
for c in ['returning_offense_share','returning_defense_share','returning_ol_share','returning_skill_share']:
    col='team_'+c
    if col in bets:
        for th in [.50,.60,.70]: add(f'{col}_le_{th:.2f}',bcol(col)<=th)

def F(n): return flags.get(n,pd.Series(False,index=bets.index))
add('team_both_coords_changed_and_losing_preseason',F('team_both_coords_changed')&F('team_preseason_losing'))
add('team_oc_dc_changed_and_losing_preseason',F('team_oc_changed_season')&F('team_dc_changed_season')&F('team_preseason_losing'))
add('team_any_coord_changed_and_losing_preseason',(F('team_oc_changed_season')|F('team_dc_changed_season'))&F('team_preseason_losing'))
add('team_both_coords_changed_and_preseason_1_2',F('team_both_coords_changed')&F('team_preseason_1_2_exact'))
add('team_staff_ge2_and_losing_preseason',F('team_staff_changes_ge2')&F('team_preseason_losing'))
add('opponent_new_hc_and_team_staff_ge2',F('opp_hc_changed_season')&F('team_staff_changes_ge2'))
add('opponent_new_hc_and_team_both_coords_changed',F('opp_hc_changed_season')&F('team_both_coords_changed'))
add('scheme_reset_both_sides',(F('team_oc_changed_season')|F('team_dc_changed_season'))&(F('opp_hc_changed_season')|F('opp_oc_changed_season')|F('opp_dc_changed_season')))
if 'opponent_prior_win_pct' in bets:
    add('opponent_new_hc_after_poor_prior_year',F('opp_hc_changed_season')&(num(bets.opponent_prior_win_pct)<=.35))
    add('opponent_staff_change_after_poor_prior_year',(F('opp_hc_changed_season')|F('opp_oc_changed_season')|F('opp_dc_changed_season'))&(num(bets.opponent_prior_win_pct)<=.35))

rows=[]
for method,x0 in bets.groupby('method'):
    idx=x0.index; base_all=met(x0); old=x0.season<=2019; rec=x0.season>=2020
    b_old=met(x0[old]); b_rec=met(x0[rec])
    for name,series in flags.items():
        r=series.loc[idx]
        risk=x0[r]; safe=x0[~r]
        ro=met(x0[old & r]); so=met(x0[old & ~r]); rr=met(x0[rec & r]); sr=met(x0[rec & ~r])
        if ro['n']<3 or rr['n']<2 or safe.shape[0]<10: continue
        old_loss_lift=(1-ro['win_pct'])-(1-b_old['win_pct']) if b_old['n'] else np.nan
        rec_loss_lift=(1-rr['win_pct'])-(1-b_rec['win_pct']) if b_rec['n'] else np.nan
        confirmed=(old_loss_lift>=.08 and rec_loss_lift>=.08 and so['roi']>=b_old['roi']-.02 and sr['roi']>=b_rec['roi']-.02)
        rows.append({'method':method,'flag':name,'base_n':base_all['n'],'base_win_pct':base_all['win_pct'],'base_roi':base_all['roi'],
                     'risk_n':len(risk),'risk_win_pct':risk.win.mean() if len(risk) else np.nan,'risk_roi':risk.profit_units.mean() if len(risk) else np.nan,
                     'safe_n':len(safe),'safe_win_pct':safe.win.mean() if len(safe) else np.nan,'safe_roi':safe.profit_units.mean() if len(safe) else np.nan,
                     'old_risk_n':ro['n'],'old_risk_win_pct':ro['win_pct'],'old_safe_roi':so['roi'],'old_loss_lift':old_loss_lift,
                     'recent_risk_n':rr['n'],'recent_risk_win_pct':rr['win_pct'],'recent_safe_roi':sr['roi'],'recent_loss_lift':rec_loss_lift,
                     'confirmed':confirmed})
res=pd.DataFrame(rows)
if len(res):
    res['score']=res.confirmed.astype(int)*10 + res.old_loss_lift.fillna(0)+res.recent_loss_lift.fillna(0)+(res.safe_roi-res.base_roi).fillna(0)
    res=res.sort_values(['confirmed','score','risk_n'],ascending=[False,False,False])
    res.to_csv(OUT/'risk_filters.csv',index=False)
    res[res.confirmed].to_csv(OUT/'confirmed_risk_filters.csv',index=False)

charg={'team':'LAC','opponent':'ARI','season':2026,'known_public_context':{'new_oc':'Mike McDaniel','new_dc':'Chris OLeary','dc_regular_season_playcalling_debut':True,'preseason_record':'1-2'}}
if len(ps):
    for tm,label in [('LAC','team'),('ARI','opponent')]:
        q=pr[(pr.season==2026)&(pr.team==tm)]
        if len(q): charg[label+'_preseason']=q.iloc[0].to_dict()
staff=CTX/'raw/coaching_staff_2006_2026.csv'
if staff.exists():
    st=pd.read_csv(staff); st.team=st.team.replace(ALIASES)
    for tm,label in [('LAC','team'),('ARI','opponent')]:
        q=st[(st.season==2026)&(st.team==tm)]; p=st[(st.season==2025)&(st.team==tm)]
        if len(q):
            cur=q.iloc[0]; prev=p.iloc[0] if len(p) else None
            charg[label+'_staff']={k:(None if pd.isna(cur.get(k)) else str(cur.get(k))) for k in ['head_coach','offensive_coordinator','defensive_coordinator'] if k in q.columns}
            if prev is not None:
                for role in ['head_coach','offensive_coordinator','defensive_coordinator']:
                    if role in q.columns: charg[label+'_staff'][role+'_changed']=bool(str(cur.get(role))!=str(prev.get(role))) if pd.notna(cur.get(role)) and pd.notna(prev.get(role)) else None
(OUT/'chargers_2026_profile.json').write_text(json.dumps(charg,indent=2,default=str))
summary={'methods':sorted(bets.method.unique().tolist()),'bets':int(len(bets)),'candidate_flags':len(flags),'tested_rows':int(len(res)) if len(rows) else 0,'confirmed_filters':int(res.confirmed.sum()) if len(rows) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL LEGACY LOSS FORENSICS + CHARGERS-STYLE FILTERS','',json.dumps(summary,indent=2),'','TOP CONFIRMED FILTERS']
if len(res):
    for _,r in res[res.confirmed].head(30).iterrows():
        lines.append(f"{r.method} | {r.flag} | risk n={int(r.risk_n)} win={100*r.risk_win_pct:.1f}% ROI={100*r.risk_roi:+.1f}% | safe win={100*r.safe_win_pct:.1f}% ROI={100*r.safe_roi:+.1f}% | old loss lift={100*r.old_loss_lift:+.1f}pp recent={100*r.recent_loss_lift:+.1f}pp")
lines+=['','CHARGERS 2026 PROFILE',json.dumps(charg,indent=2,default=str)]
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:60]))
# workflow trigger: 2026-09-14
