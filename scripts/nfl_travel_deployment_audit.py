from pathlib import Path
import json, os
import numpy as np
import pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'creative_travel_fatigue'/'creative_context_team_sides_2006_2025.parquet'
METHODS=REPO/'nfl'/'creative_travel_fatigue'/'creative_method_survivors.csv'
VETOES=REPO/'nfl'/'creative_travel_fatigue'/'confirmed_creative_vetoes.csv'
OUT=CTX/'travel_deployment_audit'; OUT.mkdir(parents=True,exist_ok=True)

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))

d=pd.read_parquet(DATA)
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['market_prob_calc']=implied(d.moneyline)
d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d else d.market_prob_calc
price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}

def mask_for(r):
    z=num(d[r.feature]); m=(z>=float(r.threshold)) if r.op=='>=' else (z<=float(r.threshold))
    lo,hi=price_bands[r.price_band]; m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='HOME':m &= num(d.is_home).eq(1)
    elif r.venue=='AWAY':m &= num(d.is_home).eq(0)
    return m

m=pd.read_csv(METHODS)
rows=[]
for i,r in m.iterrows():
    x=d[mask_for(r)].copy()
    if not len(x):continue
    team_counts=x.team.value_counts(); season_counts=x.season.value_counts()
    top_team_share=float(team_counts.iloc[0]/len(x)) if len(team_counts) else np.nan
    top3_team_share=float(team_counts.head(3).sum()/len(x)) if len(team_counts) else np.nan
    top5_season_share=float(season_counts.head(5).sum()/len(x)) if len(season_counts) else np.nan
    pos_ratio=float(r.full_positive_seasons/max(1,r.full_active_seasons))
    ready=(r.full_n>=120 and r.holdout_n>=30 and r.train_roi>=.05 and r.validation_roi>=.05 and r.holdout_roi>=.10 and r.full_roi>=.10 and pos_ratio>=.65 and top_team_share<=.15 and top3_team_share<=.35)
    watch=(not ready and r.full_n>=90 and r.holdout_n>=25 and r.holdout_roi>=.10 and r.full_roi>=.10 and pos_ratio>=.60 and top_team_share<=.20)
    rows.append({**r.to_dict(),'top_team':team_counts.index[0] if len(team_counts) else None,'top_team_share':top_team_share,'top3_team_share':top3_team_share,'top5_season_share':top5_season_share,'positive_season_ratio':pos_ratio,'deployment_status':'READY' if ready else ('WATCH' if watch else 'RESEARCH_ONLY')})
a=pd.DataFrame(rows).sort_values(['deployment_status','holdout_roi','full_n'],ascending=[True,False,False])
a.to_csv(OUT/'travel_method_deployment_audit.csv',index=False)
a[a.deployment_status.eq('READY')].to_csv(OUT/'travel_methods_ready.csv',index=False)
a[a.deployment_status.eq('WATCH')].to_csv(OUT/'travel_methods_watch.csv',index=False)

v=pd.read_csv(VETOES)
v=v[v.confirmed.eq(True)].copy()
v['retain_share']=v.holdout_safe_n/v.holdout_base_n
v['loss_win_ratio']=v.holdout_removed_losses/np.maximum(1,v.holdout_removed_wins)
v['deployment_grade']=(v.holdout_removed_n.ge(8)&v.holdout_removed_losses.ge(v.holdout_removed_wins+2)&v.holdout_safe_n.ge(20)&v.retain_share.ge(.50)&v.roi_change.ge(.08)&v.loss_rate_change.le(-.05))
# Collapse nearly duplicate vetoes on same method/feature by best ROI improvement, then one strongest veto per method for initial live readiness.
vg=(v[v.deployment_grade].sort_values(['method_id','roi_change','loss_rate_change'],ascending=[True,False,True]).drop_duplicates(['method_id','feature'],keep='first'))
primary=vg.sort_values(['method_id','roi_change','loss_rate_change'],ascending=[True,False,True]).drop_duplicates('method_id',keep='first')
vg.to_csv(OUT/'travel_vetoes_deployment_grade.csv',index=False)
primary.to_csv(OUT/'travel_primary_vetoes_ready.csv',index=False)

summary={
 'creative_methods_audited':int(len(a)),
 'travel_methods_ready':int((a.deployment_status=='READY').sum()),
 'travel_methods_watch':int((a.deployment_status=='WATCH').sum()),
 'travel_methods_research_only':int((a.deployment_status=='RESEARCH_ONLY').sum()),
 'confirmed_vetoes_audited':int(len(v)),
 'deployment_grade_vetoes':int(len(vg)),
 'methods_with_primary_travel_veto':int(primary.method_id.nunique()) if len(primary) else 0,
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL TRAVEL DEPLOYMENT AUDIT','',json.dumps(summary,indent=2),'','READY STANDALONE TRAVEL METHODS']
for _,r in a[a.deployment_status.eq('READY')].sort_values('holdout_roi',ascending=False).iterrows():
    lines.append(f"{r.track} {r.price_band} {r.venue} | {r.feature} {r.op} {r.threshold:.5g} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}% seasons={int(r.full_positive_seasons)}/{int(r.full_active_seasons)} | top-team share={100*r.top_team_share:.1f}% ({r.top_team})")
lines+=['','PRIMARY DEPLOYMENT-GRADE TRAVEL VETOES']
for _,r in primary.sort_values('roi_change',ascending=False).iterrows():
    lines.append(f"{r.method_id} | VETO {r.feature} {r.op} {r.threshold:.5g} | holdout ROI {100*r.holdout_base_roi:+.1f}% -> {100*r.holdout_safe_roi:+.1f}% | removed {int(r.holdout_removed_losses)}L/{int(r.holdout_removed_wins)}W | retain {100*r.retain_share:.0f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
