from pathlib import Path
import json
import pandas as pd

ROOT=Path('.')
FLAG=ROOT/'nfl/frozen_holdout_discovery/flagship_frozen_holdout_families.csv'
CANON=ROOT/'nfl/frozen_holdout_discovery/canonical_frozen_holdout_methods.csv'
VETO=ROOT/'nfl/priority_method_loss_forensics/confirmed_holdout_veto_filters.csv'
OUT=ROOT/'nfl/final_methods'; OUT.mkdir(parents=True,exist_ok=True)

m=pd.read_csv(FLAG)
# Keep high-value refined methods even when semantic-family dedup selected a different flagship.
priority_extra_ids={'NFL-H006'}
if CANON.exists():
    c=pd.read_csv(CANON)
    extra=c[c.method_id.isin(priority_extra_ids) & ~c.method_id.isin(m.method_id)].copy()
    if len(extra):m=pd.concat([m,extra],ignore_index=True,sort=False)
v=pd.read_csv(VETO) if VETO.exists() else pd.DataFrame()

m['positive_season_ratio']=m.full_positive_seasons/m.full_active_seasons
m['registry_status']='FINAL_RESEARCH'
m.loc[m.method_id.eq('NFL-H001'),'registry_status']='REVIEW_EXTREME_ROI'
m.loc[m.positive_season_ratio.lt(.65),'registry_status']='WATCHLIST'

if len(v):
    vv=(v[v.confirmed_veto.eq(True)].sort_values(['method_id','holdout_roi_change'],ascending=[True,False])
        .groupby('method_id').apply(lambda z:' || '.join(f"VETO: {r.feature_or_combo} {r.op} {r.threshold if pd.notna(r.threshold) else ''}".strip() for _,r in z.iterrows()),include_groups=False)
        .rename('validated_vetoes').reset_index())
    m=m.merge(vv,on='method_id',how='left')
else:m['validated_vetoes']=''
m['validated_vetoes']=m.validated_vetoes.fillna('')

cols=['method_id','registry_status','semantic_signature','price_band','venue','feature1','op1','threshold1','feature2','op2','threshold2','full_n','full_wins','full_win_rate','full_roi','train_roi','validation_roi','holdout_roi','full_active_seasons','full_positive_seasons','full_negative_seasons','positive_season_ratio','tier','validated_vetoes']
cols=[c for c in cols if c in m.columns]
m[cols].to_csv(OUT/'approved_research_methods.csv',index=False)
summary={'registry_methods':int(len(m)),'final_research':int((m.registry_status=='FINAL_RESEARCH').sum()),'watchlist':int((m.registry_status=='WATCHLIST').sum()),'review_extreme_roi':int((m.registry_status=='REVIEW_EXTREME_ROI').sum()),'methods_with_validated_vetoes':int((m.validated_vetoes!='').sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL FINAL RESEARCH METHOD REGISTRY','',json.dumps(summary,indent=2),'','Methods are research-approved, not automatically deployed to the live email/Appwiza scanner.','']
for _,r in m.sort_values(['registry_status','method_id']).iterrows():
    rule=f"{r.feature1} {r.op1} {r.threshold1:.5g}"
    if pd.notna(r.get('feature2')):rule+=f" AND {r.feature2} {r.op2} {r.threshold2:.5g}"
    lines.append(f"{r.method_id} [{r.registry_status}] {r.semantic_signature} | {r.price_band} {r.venue} | {rule} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}% seasons={int(r.full_positive_seasons)}/{int(r.full_active_seasons)}"+(f" | {r.validated_vetoes}" if r.validated_vetoes else ''))
(OUT/'registry_report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
