from pathlib import Path
import json, os
import numpy as np
import pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
METHODS=REPO/'nfl'/'coaching_everything'/'new_coaching_methods.csv'
VETOES=REPO/'nfl'/'coaching_everything'/'coaching_veto_candidates.csv'
OUT=CTX/'coaching_deployment_audit'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))

d=pd.read_parquet(DATA)
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['market_prob_calc']=implied(d.moneyline)
d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc
price={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}

def cond(c,op,q):
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def mask_for(r):
    m=cond(r.feature1,r.op1,r.threshold1) & cond(r.feature2,r.op2,r.threshold2)
    lo,hi=price[r.price_band]; m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='HOME':m &= num(d.is_home).eq(1)
    elif r.venue=='AWAY':m &= num(d.is_home).eq(0)
    return m

m=pd.read_csv(METHODS)
rows=[]
for _,r in m.iterrows():
    x=d[mask_for(r)].copy()
    if not len(x):continue
    tc=x.team.value_counts(); sc=x.season.value_counts()
    top_team_share=float(tc.iloc[0]/len(x)) if len(tc) else np.nan
    top3_team_share=float(tc.head(3).sum()/len(x)) if len(tc) else np.nan
    top5_season_share=float(sc.head(5).sum()/len(x)) if len(sc) else np.nan
    pos_ratio=float(r.positive_seasons/max(1,r.active_seasons))
    # Deployment is intentionally stricter than research survival.
    ready=(r.full_n>=120 and r.holdout_n>=30 and r.train_roi>=.08 and r.validation_roi>=.08 and r.holdout_roi>=.12 and r.full_roi>=.15 and pos_ratio>=.65 and top_team_share<=.15 and top3_team_share<=.35 and top5_season_share<=.50)
    watch=(not ready and r.full_n>=90 and r.holdout_n>=25 and r.train_roi>=.05 and r.validation_roi>=.05 and r.holdout_roi>=.10 and r.full_roi>=.10 and pos_ratio>=.60 and top_team_share<=.20 and top3_team_share<=.42)
    rows.append({**r.to_dict(),'top_team':tc.index[0] if len(tc) else None,'top_team_share':top_team_share,'top3_team_share':top3_team_share,'top5_season_share':top5_season_share,'positive_season_ratio':pos_ratio,'deployment_status':'READY' if ready else ('WATCH' if watch else 'RESEARCH_ONLY')})
a=pd.DataFrame(rows).sort_values(['deployment_status','holdout_roi','full_n'],ascending=[True,False,False])
a.to_csv(OUT/'coaching_method_deployment_audit.csv',index=False)
a[a.deployment_status.eq('READY')].to_csv(OUT/'coaching_methods_ready.csv',index=False)
a[a.deployment_status.eq('WATCH')].to_csv(OUT/'coaching_methods_watch.csv',index=False)

v=pd.read_csv(VETOES)
# coaching_veto_candidates contains only preselected and holdout-tested rows; use confirmed when present.
if 'confirmed' in v.columns:v=v[v.confirmed.eq(True)].copy()
elif 'confirmed_veto' in v.columns:v=v[v.confirmed_veto.eq(True)].copy()
# Normalize known column names from expansion output.
for target,choices in {
    'removed_losses':['holdout_removed_losses','removed_losses'],
    'removed_wins':['holdout_removed_wins','removed_wins'],
    'safe_n':['holdout_safe_n','safe_n'],
    'base_n':['holdout_base_n','base_n'],
    'roi_change':['holdout_roi_change','roi_change'],
    'safe_roi':['holdout_safe_roi','safe_roi'],
    'base_roi':['holdout_base_roi','base_roi'],
}.items():
    if target not in v.columns:
        for c in choices:
            if c in v.columns:v[target]=v[c];break
v['retain_share']=num(v.safe_n)/np.maximum(1,num(v.base_n))
v['loss_win_ratio']=num(v.removed_losses)/np.maximum(1,num(v.removed_wins))
v['deployment_grade']=(num(v.removed_losses).ge(num(v.removed_wins)+2)&num(v.removed_losses).ge(5)&num(v.safe_n).ge(18)&v.retain_share.ge(.50)&num(v.roi_change).ge(.08))
vg=(v[v.deployment_grade].sort_values(['method_id','roi_change'],ascending=[True,False]).drop_duplicates(['method_id','feature'],keep='first'))
primary=vg.sort_values(['method_id','roi_change'],ascending=[True,False]).drop_duplicates('method_id',keep='first')
vg.to_csv(OUT/'coaching_vetoes_deployment_grade.csv',index=False)
primary.to_csv(OUT/'coaching_primary_vetoes_ready.csv',index=False)

summary={
 'coaching_methods_audited':int(len(a)),
 'coaching_methods_ready':int((a.deployment_status=='READY').sum()),
 'coaching_methods_watch':int((a.deployment_status=='WATCH').sum()),
 'coaching_methods_research_only':int((a.deployment_status=='RESEARCH_ONLY').sum()),
 'confirmed_coaching_vetoes_audited':int(len(v)),
 'deployment_grade_coaching_vetoes':int(len(vg)),
 'methods_with_primary_coaching_veto':int(primary.method_id.nunique()) if len(primary) else 0,
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACHING DEPLOYMENT AUDIT','',json.dumps(summary,indent=2),'','READY COACHING+FOOTBALL METHODS']
for _,r in a[a.deployment_status.eq('READY')].sort_values(['holdout_roi','full_n'],ascending=[False,False]).iterrows():
    lines.append(f"{r.track} {r.price_band} {r.venue} | {r.feature1} {r.op1} {r.threshold1:.5g} AND {r.feature2} {r.op2} {r.threshold2:.5g} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}% seasons={int(r.positive_seasons)}/{int(r.active_seasons)} | top-team share={100*r.top_team_share:.1f}% ({r.top_team})")
lines+=['','PRIMARY DEPLOYMENT-GRADE COACHING VETOES']
for _,r in primary.sort_values('roi_change',ascending=False).iterrows():
    lines.append(f"{r.method_id} | VETO {r.feature} {r.op} {r.threshold:.5g} | holdout ROI {100*r.base_roi:+.1f}% -> {100*r.safe_roi:+.1f}% | removed {int(r.removed_losses)}L/{int(r.removed_wins)}W | retain {100*r.retain_share:.0f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
