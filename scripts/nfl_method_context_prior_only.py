from __future__ import annotations

from pathlib import Path
import json, math, os
import numpy as np
import pandas as pd

ROOT=Path(os.environ.get('NFL_SOURCE_ROOT','/home/anestishkurti92/nfl-predictor-v1'))
CTX=Path(os.environ.get('NFL_CONTEXT_ROOT','/home/appwiza-runner/nfl-context-data'))
RAW=ROOT/'rolling_roi_discovery'/'pregame_team_sides_2006_2026.parquet'
CONTEXT=CTX/'derived'/'team_game_pregame_context_2006_2026.parquet'
AUDIT=CTX/'method_audit'
OUT=CTX/'method_prior_only'; OUT.mkdir(parents=True,exist_ok=True)

# Menu frozen after descriptive football-based audit. This is intentionally small per family.
# Historical results below are a retrospective prior-only simulation; 2026+ remains the clean prospective test.
MENUS={
 'YPP OFF vs LEAKY DEF':['NONE','OC_CHANGED','RETURN_SKILL_LT60','OUT_OL_GE1'],
 'PASS RUSH vs WEAK PROTECTION':['NONE','OPP_STAFF_CHANGES_GE2','OPP_OC_CHANGED','OPP_DC_CHANGED','OPP_HC_CHANGED'],
 'PASS OFF vs ELITE PASS DEF':['NONE','OPP_OC_CHANGED','OPP_STAFF_CHANGES_GE2','PRESEASON_FORM_DISADVANTAGE'],
 'EPA + YPP DEFENSE':['NONE','DC_CHANGED','PRESEASON_FORM_DISADVANTAGE','OUT_OL_GE1','RETURN_SKILL_LT60'],
 'SCORING + DEFENSE':['NONE','RETURN_SKILL_LT70','OPP_HC_CHANGED','STAFF_CHANGES_GE2'],
 'ELITE OFF vs ELITE DEF':['NONE','OPP_OC_CHANGED','OPP_STAFF_CHANGES_GE2','QB_CHANGED','PRESEASON_LOSING'],
 'BALANCED ELITE':['NONE','OPP_OC_CHANGED','RETURN_OL_LT70'],
 'ELITE DEF vs WEAK OFF':['NONE','OPP_DC_CHANGED','OPP_OC_CHANGED','RETURN_SKILL_LT70'],
}

def stats(z):
    n=len(z)
    if not n:return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi_pct':np.nan,'profit':0.0}
    w=int(z.win.sum()); p=float(z.bet_profit100.sum())
    return {'n':n,'wins':w,'losses':n-w,'win_pct':100*w/n,'roi_pct':p/n,'profit':p}

def base(df,p):
    z=(df.week>=p['w'])&(df.market_prob>=p['m'])
    if p['loc']=='HOME':z &= df.home_side.eq(1)
    elif p['loc']=='ROAD':z &= df.home_side.eq(0)
    return z

def apply_family(name,df,p):
    z=base(df,p)
    if name=='YPP OFF vs LEAKY DEF':return z&(df.pre_yards_per_play>=p['oy'])&(df.opp_pre_def_ypp_allowed>=p['dy'])
    if name=='PASS RUSH vs WEAK PROTECTION':return z&(df.rank_sacks_made<=p['sr'])&(df.opp_pre_sacks_suffered>=p['osa'])
    if name=='PASS OFF vs ELITE PASS DEF':return z&(df.rank_pass_epa<=p['pr'])&(df.opp_rank_def_pass_epa<=p['dr'])
    if name=='EPA + YPP DEFENSE':return z&(df.edge_off_epa>=p['oe'])&(df.edge_def_ypp>=p['de'])
    if name=='SCORING + DEFENSE':return z&(df.rank_scoring<=p['sr'])&(df.rank_points_allowed<=p['dr'])
    if name=='ELITE OFF vs ELITE DEF':return z&(df.rank_off_epa<=p['orank'])&(df.opp_rank_def_epa<=p['odrank'])
    if name=='BALANCED ELITE':return z&(df.rank_off_epa<=p['n'])&(df.rank_def_epa<=p['n'])
    if name=='ELITE DEF vs WEAK OFF':return z&(df.rank_def_epa<=p['dr'])&(df.opp_rank_off_epa>=p['oor'])
    raise KeyError(name)

def risk_series(d,name):
    if name=='NONE':return pd.Series(False,index=d.index,dtype='boolean')
    col={
      'OC_CHANGED':'offensive_coordinator_changed','DC_CHANGED':'defensive_coordinator_changed',
      'OPP_OC_CHANGED':'oppctx_offensive_coordinator_changed','OPP_DC_CHANGED':'oppctx_defensive_coordinator_changed',
      'OPP_HC_CHANGED':'oppctx_head_coach_changed','OPP_STAFF_CHANGES_GE2':'oppctx_major_staff_changes',
      'STAFF_CHANGES_GE2':'major_staff_changes','QB_CHANGED':'qb_changed_from_prior_season',
      'RETURN_SKILL_LT60':'returning_skill_snap_share','RETURN_SKILL_LT70':'returning_skill_snap_share',
      'RETURN_OL_LT70':'returning_ol_snap_share','OUT_OL_GE1':'out_ol',
      'PRESEASON_FORM_DISADVANTAGE':'preseason_form_disadvantage','PRESEASON_LOSING':'preseason_win_pct',
    }[name]
    s=pd.to_numeric(d[col],errors='coerce')
    out=pd.Series(pd.NA,index=d.index,dtype='boolean'); k=s.notna()
    if name.endswith('LT60'):out.loc[k]=s.loc[k].lt(.60)
    elif name.endswith('LT70'):out.loc[k]=s.loc[k].lt(.70)
    elif name=='PRESEASON_LOSING':out.loc[k]=s.loc[k].lt(.50)
    elif name in {'OPP_STAFF_CHANGES_GE2','STAFF_CHANGES_GE2'}:out.loc[k]=s.loc[k].ge(2)
    elif name=='OUT_OL_GE1':out.loc[k]=s.loc[k].ge(1)
    else:out.loc[k]=s.loc[k].eq(1)
    return out

# Original feature universe.
df=pd.read_parquet(RAW)
for name in ['off_epa','def_epa','pass_epa','def_pass_epa','rush_epa','def_rush_epa','scoring','points_allowed','turnover','sacks_made','def_ypp']:
    a=f'rank_{name}'; o=f'opp_rank_{name}'
    if a in df and o in df:df[f'edge_{name}']=pd.to_numeric(df[o],errors='coerce')-pd.to_numeric(df[a],errors='coerce')
b=df[df.moneyline.notna() & df.market_prob.notna() & df.win.isin([0.0,1.0]) & (df.prior_games>=3) & (df.market_prob>.50)].copy()

# Join context once to the full training universe.
ctx=pd.read_parquet(CONTEXT)
ctxcols=[c for c in ctx.columns if c not in {'season','week','opponent','home_side','moneyline','opp_moneyline','rest','opp_rest','gameday','gameday_dt','win'}]
t=ctx[ctxcols].drop_duplicates(['game_id','team'])
b=b.merge(t,on=['game_id','team'],how='left',suffixes=('','_ctx'))
o=t.rename(columns={c:f'oppctx_{c}' for c in t.columns if c not in {'game_id','team'}}).rename(columns={'team':'opponent'})
b=b.merge(o,on=['game_id','opponent'],how='left')

wf=pd.read_csv(AUDIT/'method_walkforward_by_season.csv')
oos=pd.read_csv(AUDIT/'method_all_bets_context.csv')
rows=[]; betrows=[]
for family,menu in MENUS.items():
    famwf=wf[wf.family.eq(family)].copy()
    for _,sel in famwf.iterrows():
        y=int(sel.test_season); p=json.loads(sel.params)
        train=b[(b.season<y) & apply_family(family,b,p).fillna(False)].copy()
        test=oos[(oos.family==family)&(oos.season==y)].copy()
        bm=stats(train)
        # NONE is always admissible and has a small complexity advantage.
        candidates=[{'factor':'NONE','score':0.0,'train_base':bm,'train_veto':bm,'risk':stats(train.iloc[0:0]),'coverage_pct':100.0,'recent_improve':0.0,'overall_improve':0.0}]
        for factor in menu:
            if factor=='NONE':continue
            r=risk_series(train,factor); known=r.notna(); risk=r.fillna(False).astype(bool)
            rz=train[known & risk]; sz=train[known & ~risk]; vz=train[~risk]
            rm,sm,vm=stats(rz),stats(sz),stats(vz)
            coverage=100*known.mean() if len(train) else 0
            if bm['n']<50 or coverage<55 or rm['n']<8 or rm['losses']<2 or vm['n']<max(35,int(.55*bm['n'])):continue
            improve=vm['roi_pct']-bm['roi_pct']
            # Recent prior-only confirmation using the five seasons before test year.
            rec=train[train.season>=max(int(train.season.min()) if len(train) else y-5,y-5)]
            rr=risk_series(rec,factor); rrisk=rr.fillna(False).astype(bool)
            rb=stats(rec); rv=stats(rec[~rrisk]); rsub=stats(rec[rr.notna()&rrisk])
            recent_improve=rv['roi_pct']-rb['roi_pct'] if rb['n'] and rv['n'] else -999
            loss_capture=100*rm['losses']/bm['losses'] if bm['losses'] else 0
            win_sac=100*rm['wins']/bm['wins'] if bm['wins'] else 0
            # Require a real football-risk separation rather than just price luck.
            if improve<2.0 or rm['roi_pct']>=sm['roi_pct'] or loss_capture<=win_sac:continue
            if rb['n']>=15 and rsub['n']>=3 and recent_improve<=0:continue
            # Favor robust ROI gain, recent agreement and efficient loss capture; penalize complexity.
            score=improve + .35*max(-10,min(10,recent_improve)) + .05*(loss_capture-win_sac) - 1.5
            if score<=1.5:continue
            candidates.append({'factor':factor,'score':score,'train_base':bm,'train_veto':vm,'risk':rm,'coverage_pct':coverage,'recent_improve':recent_improve,'overall_improve':improve})
        candidates.sort(key=lambda x:x['score'],reverse=True); chosen=candidates[0]
        factor=chosen['factor']
        trisk=risk_series(test,factor).fillna(False).astype(bool) if factor!='NONE' else pd.Series(False,index=test.index)
        kept=test[~trisk].copy(); removed=test[trisk].copy(); km=stats(kept); rem=stats(removed); tm=stats(test)
        rows.append({
          'family':family,'test_season':y,'selected_factor':factor,'selector_score':chosen['score'],
          'train_n':bm['n'],'train_roi_pct':bm['roi_pct'],'train_veto_roi_pct':chosen['train_veto']['roi_pct'],
          'train_risk_n':chosen['risk']['n'],'train_risk_roi_pct':chosen['risk']['roi_pct'],'coverage_pct':chosen['coverage_pct'],
          'recent_roi_improvement':chosen['recent_improve'],'test_base_n':tm['n'],'test_base_wins':tm['wins'],'test_base_losses':tm['losses'],'test_base_roi_pct':tm['roi_pct'],
          'test_kept_n':km['n'],'test_kept_wins':km['wins'],'test_kept_losses':km['losses'],'test_kept_roi_pct':km['roi_pct'],
          'test_removed_n':rem['n'],'test_removed_wins':rem['wins'],'test_removed_losses':rem['losses'],'test_removed_roi_pct':rem['roi_pct'],
        })
        if len(test):
            q=test.copy(); q['selected_context_factor']=factor; q['context_vetoed']=trisk.astype(int); betrows.append(q)
res=pd.DataFrame(rows); allbets=pd.concat(betrows,ignore_index=True)
res.to_csv(OUT/'prior_only_by_season.csv',index=False); allbets.to_csv(OUT/'prior_only_all_bets.csv',index=False)

summ=[]
for family in MENUS:
    z=allbets[allbets.family.eq(family)]; base=stats(z); kept=stats(z[z.context_vetoed.eq(0)]); removed=stats(z[z.context_vetoed.eq(1)])
    sel=res[res.family.eq(family)].selected_factor.value_counts().to_dict()
    summ.append({'family':family,'base_n':base['n'],'base_wins':base['wins'],'base_losses':base['losses'],'base_win_pct':base['win_pct'],'base_roi_pct':base['roi_pct'],
                 'kept_n':kept['n'],'kept_wins':kept['wins'],'kept_losses':kept['losses'],'kept_win_pct':kept['win_pct'],'kept_roi_pct':kept['roi_pct'],
                 'removed_n':removed['n'],'removed_wins':removed['wins'],'removed_losses':removed['losses'],'removed_roi_pct':removed['roi_pct'],
                 'roi_change':kept['roi_pct']-base['roi_pct'] if kept['n'] else np.nan,'selection_counts':json.dumps(sel,sort_keys=True)})
summary=pd.DataFrame(summ); summary.to_csv(OUT/'prior_only_summary.csv',index=False)
print('=== PRIOR-ONLY CONTEXT VETO SUMMARY ===')
print(summary.to_string(index=False))
print('\n=== SELECTIONS BY SEASON ===')
print(res[['family','test_season','selected_factor','train_n','train_roi_pct','train_veto_roi_pct','test_base_n','test_base_roi_pct','test_kept_n','test_kept_roi_pct','test_removed_n','test_removed_wins','test_removed_losses']].to_string(index=False))
