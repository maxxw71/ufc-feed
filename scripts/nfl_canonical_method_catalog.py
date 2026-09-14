from pathlib import Path
import pandas as pd, numpy as np, json
P=Path('nfl/final_method_expansion/unique_double_digit_methods.csv')
OUT=Path('nfl/final_method_expansion'); OUT.mkdir(parents=True,exist_ok=True)
d=pd.read_csv(P)
# Preserve strongest candidates while limiting threshold/market variants of the same concept.
d=d.sort_values(['research_tier','robust_score','full_n'],ascending=[True,False,False]).copy()
# tier lexical A/B/C already desired; robust score handles within tier.
selected=[]; sig_counts={}; bucket_counts={}
for _,r in d.iterrows():
    sig=str(r.semantic_signature)
    bucket=(sig,str(r.price_band),str(r.venue))
    # At most 2 variants of the same signal+market+venue; at most 6 variants of any semantic concept.
    if bucket_counts.get(bucket,0)>=2: continue
    if sig_counts.get(sig,0)>=6: continue
    selected.append(r)
    bucket_counts[bucket]=bucket_counts.get(bucket,0)+1
    sig_counts[sig]=sig_counts.get(sig,0)+1
c=pd.DataFrame(selected).reset_index(drop=True)
c.insert(0,'canonical_id',[f'NFL-C{i+1:03d}' for i in range(len(c))])
c.to_csv(OUT/'canonical_double_digit_methods.csv',index=False)

# One flagship per semantic concept for the shortest serious shortlist.
f=(c.sort_values(['research_tier','robust_score','full_n'],ascending=[True,False,False])
    .drop_duplicates('semantic_signature',keep='first').reset_index(drop=True))
f.insert(0,'flagship_id',[f'NFL-F{i+1:02d}' for i in range(len(f))])
f.to_csv(OUT/'flagship_unique_method_families.csv',index=False)

counts=(d.groupby(['semantic_signature']).agg(candidates=('method_id','count'),best_roi=('full_roi','max'),best_holdout_roi=('holdout_roi','max'),max_n=('full_n','max')).reset_index().sort_values(['candidates','best_roi'],ascending=[False,False]))
counts.to_csv(OUT/'method_family_counts.csv',index=False)
summary={'strict_unique_betset_methods':int(len(d)),'canonical_methods':int(len(c)),'flagship_semantic_families':int(len(f)),'semantic_signatures':int(d.semantic_signature.nunique()),'price_bands':sorted(d.price_band.dropna().unique().tolist()),'venues':sorted(d.venue.dropna().unique().tolist())}
(OUT/'canonical_summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL CANONICAL METHOD CATALOG','',json.dumps(summary,indent=2),'','FLAGSHIP UNIQUE FAMILIES']
for _,r in f.iterrows():
    f1=f"{r.feature1} {r.op1} {r.threshold1:.5g}"
    if pd.notna(r.feature2): f1+=f" AND {r.feature2} {r.op2} {r.threshold2:.5g}"
    lines.append(f"{r.flagship_id} -> {r.method_id} [{r.research_tier}] {r.semantic_signature} | {r.price_band} {r.venue} | {f1} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% | train {100*r.train_roi:+.1f}% val {100*r.validation_roi:+.1f}% holdout {100*r.holdout_roi:+.1f}% | positive seasons {int(r.full_positive_seasons)}/{int(r.full_active_seasons)}")
(OUT/'canonical_report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
