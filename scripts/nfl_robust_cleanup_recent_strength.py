from pathlib import Path
import json, os
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=REPO/'nfl'/'robust_cleanup_recent_strength'
OUT.mkdir(parents=True,exist_ok=True)

H=REPO/'nfl/final_methods/approved_research_methods.csv'
FINAL=REPO/'nfl/live_candidate_finalization/final_candidate_registry.csv'
TIMING=REPO/'nfl/season_timing_audit/method_season_timing.csv'
NEW=REPO/'nfl/new_method_reg_only_revalidation/survivors.csv'

h=pd.read_csv(H)
f=pd.read_csv(FINAL)
t=pd.read_csv(TIMING)
n=pd.read_csv(NEW)

# ---- clean the older H-series registry with the newest robust evidence ----
# Keep the H registry's own positive-season ratio; timing supplies REG-only reconstructed metrics.
hm=h.merge(f[['candidate_id','deployment_status','deployment_reason','duplicate_of','max_jaccard_overlap','live_odds_range','hard_veto_source','hard_veto_feature','hard_veto_op','hard_veto_threshold']],left_on='method_id',right_on='candidate_id',how='left')
tcols=t[['candidate_id','full_n','full_roi','trainval_n','trainval_roi','holdout_n','holdout_roi','recommended_start_week','timing_decision','min_week','median_week','max_week']].copy()
tcols=tcols.rename(columns={c:f'{c}_reg' for c in ['full_n','full_roi','trainval_n','trainval_roi','holdout_n','holdout_roi']})
hm=hm.merge(tcols,left_on='method_id',right_on='candidate_id',how='left',suffixes=('','_timing'))

hm['recent_delta']=hm['holdout_roi_reg']-hm['trainval_roi_reg']
hm['cleanup_status']='REVIEW'
for i,r in hm.iterrows():
    full_roi=r.get('full_roi_reg'); hold=r.get('holdout_roi_reg'); nfull=r.get('full_n_reg'); pos=r.get('positive_season_ratio')
    dep=str(r.get('deployment_status') or '')
    dup=str(r.get('duplicate_of') or '')
    if dup and dup!='nan':
        status='ARCHIVE_DUPLICATE'
    elif dep=='LIVE_READY' and pd.notna(hold) and hold>=.10 and pd.notna(pos) and pos>=.65:
        status='CORE_LIVE'
    elif pd.notna(full_roi) and pd.notna(hold) and full_roi>=.10 and hold>=.10 and pd.notna(nfull) and nfull>=80 and (pd.isna(pos) or pos>=.60):
        status='KEEP_RESEARCH'
    elif pd.notna(full_roi) and pd.notna(hold) and full_roi>=.08 and hold>=.05:
        status='WATCH_ONLY'
    else:
        status='ARCHIVE_WEAK'
    hm.at[i,'cleanup_status']=status

hm['cleanup_score']=(hm['holdout_roi_reg'].fillna(-1)*.40 + hm['full_roi_reg'].fillna(-1)*.25 + hm['positive_season_ratio'].fillna(0)*.15 + hm['recent_delta'].fillna(-1)*.15 + np.log10(hm['full_n_reg'].fillna(1).clip(lower=1))*.025)
hm=hm.sort_values(['cleanup_status','cleanup_score'],ascending=[True,False])
hm.to_csv(OUT/'h_series_cleaned_registry.csv',index=False)

# ---- user's preferred new-method ranking ----
# Join season-consistency / overlap information from final candidate registry.
nj=n.merge(f[['candidate_id','positive_season_ratio','duplicate_of','max_jaccard_overlap','rule_reproducible']],left_on='method_id',right_on='candidate_id',how='left')
nj['older_avg_roi']=(nj.train_roi_reg+nj.validation_roi_reg)/2
nj['recent_delta']=nj.holdout_roi_reg-nj.older_avg_roi
nj['era_floor']=nj[['train_roi_reg','validation_roi_reg','holdout_roi_reg']].min(axis=1)
nj['duplicate_of']=nj.duplicate_of.fillna('')
nj['positive_season_ratio']=nj.positive_season_ratio.fillna(0)

# Strong preference: double-digit ROI all eras, many positive seasons, and recent/holdout strengthening.
nj['preference_status']='RESEARCH'
for i,r in nj.iterrows():
    if r.duplicate_of:
        s='DUPLICATE'
    elif r.full_n_reg>=100 and r.full_roi_reg>=.15 and r.era_floor>=.10 and r.positive_season_ratio>=.70 and r.holdout_roi_reg>=r.older_avg_roi+.02:
        s='ELITE_RECENT_STRENGTH'
    elif r.full_n_reg>=80 and r.full_roi_reg>=.10 and r.era_floor>=.08 and r.positive_season_ratio>=.65 and r.holdout_roi_reg>=r.older_avg_roi-.01:
        s='STRONG_RECENT_STABLE'
    elif r.full_n_reg>=80 and r.full_roi_reg>=.10 and r.holdout_roi_reg>=.10 and r.positive_season_ratio>=.60:
        s='STRONG_STABLE'
    else:
        s='RESEARCH'
    nj.at[i,'preference_status']=s

nj['preference_score']=(nj.era_floor*.28 + nj.holdout_roi_reg*.28 + nj.full_roi_reg*.20 + nj.positive_season_ratio*.14 + nj.recent_delta.clip(-.25,.25)*.08 + np.log10(nj.full_n_reg.clip(lower=1))*.02)
nj=nj.sort_values(['preference_status','preference_score'],ascending=[True,False])
nj.to_csv(OUT/'new_methods_ranked_to_user_preference.csv',index=False)
preferred=nj[nj.preference_status.isin(['ELITE_RECENT_STRENGTH','STRONG_RECENT_STABLE'])].sort_values('preference_score',ascending=False)
preferred.to_csv(OUT/'preferred_recent_strength_methods.csv',index=False)

summary={
 'h_registry_total':int(len(hm)),
 'h_core_live':int((hm.cleanup_status=='CORE_LIVE').sum()),
 'h_keep_research':int((hm.cleanup_status=='KEEP_RESEARCH').sum()),
 'h_watch_only':int((hm.cleanup_status=='WATCH_ONLY').sum()),
 'h_archive_duplicate':int((hm.cleanup_status=='ARCHIVE_DUPLICATE').sum()),
 'h_archive_weak':int((hm.cleanup_status=='ARCHIVE_WEAK').sum()),
 'new_reg_survivors_ranked':int(len(nj)),
 'elite_recent_strength':int((nj.preference_status=='ELITE_RECENT_STRENGTH').sum()),
 'strong_recent_stable':int((nj.preference_status=='STRONG_RECENT_STABLE').sum()),
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))

lines=['NFL ROBUST CLEANUP + RECENT-STRENGTH RANKING','',json.dumps(summary,indent=2),'','TOP USER-PREFERENCE NEW METHODS']
for _,r in preferred.head(20).iterrows():
    lines.append(f"{r.method_id} [{r.source}] {r.preference_status} | n={int(r.full_n_reg)} full={100*r.full_roi_reg:+.1f}% train={100*r.train_roi_reg:+.1f}% val={100*r.validation_roi_reg:+.1f}% hold={100*r.holdout_roi_reg:+.1f}% recent_delta={100*r.recent_delta:+.1f}pp | positive-season ratio={100*r.positive_season_ratio:.0f}% | start W{int(r.recommended_start_week)}")
lines+=['','H-SERIES CLEANUP']
for status in ['CORE_LIVE','KEEP_RESEARCH','WATCH_ONLY','ARCHIVE_DUPLICATE','ARCHIVE_WEAK']:
    z=hm[hm.cleanup_status.eq(status)]
    lines.append(f'{status}: {len(z)}')
    for _,r in z.sort_values('cleanup_score',ascending=False).head(15).iterrows():
        nval=int(r.full_n_reg) if pd.notna(r.full_n_reg) else 0
        froi=100*r.full_roi_reg if pd.notna(r.full_roi_reg) else float('nan')
        hroi=100*r.holdout_roi_reg if pd.notna(r.holdout_roi_reg) else float('nan')
        dlt=100*r.recent_delta if pd.notna(r.recent_delta) else float('nan')
        sw=int(r.recommended_start_week) if pd.notna(r.recommended_start_week) else 0
        lines.append(f"  {r.method_id} | n={nval} full={froi:+.1f}% hold={hroi:+.1f}% recent_delta={dlt:+.1f}pp start=W{sw}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
