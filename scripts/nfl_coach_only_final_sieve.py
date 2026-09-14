from pathlib import Path
import os,json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=Path('/home/appwiza-runner/nfl-context-data/coach_only_final_sieve');OUT.mkdir(parents=True,exist_ok=True)
V=REPO/'nfl/coach_only_deep_dissection/confirmed_vetoes.csv'
D=REPO/'nfl/coach_only_deep_dissection/final_dissection.csv'
P=REPO/'nfl/coach_only_deep_dissection/coach_h_pair_overlap.csv'
ANY=REPO/'nfl/coach_only_deep_dissection/coach_any_h_consensus.csv'
W=REPO/'nfl/coach_only_deep_dissection/weekly_roi.csv'
S=REPO/'nfl/coach_only_deep_dissection/segment_roi.csv'

v=pd.read_csv(V);fd=pd.read_csv(D);pairs=pd.read_csv(P);anyh=pd.read_csv(ANY);weekly=pd.read_csv(W);segments=pd.read_csv(S)
# Strict veto sieve: substantial samples in all eras, keep most holdout bets, risk subset poor in all eras,
# safe subset must materially improve holdout and cannot hurt pre-holdout.
v['preholdout_risk_n']=v.train_risk_n+v.validation_risk_n
v['strict']=(v.preholdout_risk_n>=18)&(v.holdout_risk_n>=6)&(v.retained_share>=.65)&(v.train_risk_roi<0)&(v.validation_risk_roi<.05)&(v.holdout_risk_roi<0)&(v.train_safe_roi>=v.base_roi-.02)&(v.validation_safe_roi>=v.base_roi-.02)&(v.holdout_safe_roi>=v.base_roi+.08)
# ultra strict needs negative ROI all three eras and at least 8 holdout risk bets
v['ultra_strict']=v.strict&(v.holdout_risk_n>=8)&(v.validation_risk_roi<0)&(v.holdout_safe_roi>=v.base_roi+.12)
sv=v[v.strict].copy().sort_values(['ultra_strict','holdout_safe_roi','retained_share'],ascending=[False,False,False])
sv.to_csv(OUT/'strict_vetoes.csv',index=False)
# Pick one only, favor coach-only veto if statistically comparable; otherwise strongest context veto.
selected=[]
for mid,g in sv.groupby('method_id'):
    g=g.copy();cg=g[g.family.eq('COACH')]
    if len(cg):
        bestc=cg.sort_values(['ultra_strict','holdout_safe_roi','retained_share'],ascending=[False,False,False]).iloc[0]
        bestall=g.sort_values(['ultra_strict','holdout_safe_roi','retained_share'],ascending=[False,False,False]).iloc[0]
        # retain coach-only purity unless context safe ROI beats coach by >25 percentage points.
        pick=bestc if bestall.holdout_safe_roi-bestc.holdout_safe_roi<=.25 else bestall
    else:pick=g.sort_values(['ultra_strict','holdout_safe_roi','retained_share'],ascending=[False,False,False]).iloc[0]
    selected.append(pick)
sel=pd.DataFrame(selected) if selected else pd.DataFrame(columns=v.columns);sel.to_csv(OUT/'selected_single_veto.csv',index=False)

# Weekly caution: label full-sample weak windows, but explicitly do NOT veto unless deep audit confirmed timing.
rows=[]
for mid,g in segments[segments['split'].eq('ALL')].groupby('method_id'):
    base=fd[fd.method_id.eq(mid)].iloc[0]
    for _,r in g.iterrows():
        rows.append({'method_id':mid,'segment':r.segment,'n':r.n,'win_pct':r.win_pct,'roi':r.roi,'below_base_by':base.base_roi-r.roi,'descriptive_weak':bool(r.roi<0 or r.roi<base.base_roi-.20)})
pd.DataFrame(rows).to_csv(OUT/'descriptive_week_windows.csv',index=False)

# Consensus pair priority: require meaningful overall + holdout sample and positive holdout.
pairs['priority']=(pairs.n>=15)&(pairs.holdout_n>=7)&(pairs.roi>=.25)&(pairs.holdout_roi>=.25)
pairs['elite_overlap']=(pairs.n>=18)&(pairs.holdout_n>=8)&(pairs.win_pct>=.65)&(pairs.holdout_win_pct>=.65)&(pairs.roi>=.40)&(pairs.holdout_roi>=.40)
pairs.sort_values(['elite_overlap','priority','holdout_n','holdout_roi'],ascending=[False,False,False,False]).to_csv(OUT/'consensus_pair_sieve.csv',index=False)
anyh['priority']=(anyh.min_h_agreement==1)&(anyh.n>=30)&(anyh.holdout_n>=12)&(anyh.roi>=.25)&(anyh.holdout_roi>=.25)
anyh.sort_values(['priority','holdout_n','holdout_roi'],ascending=[False,False,False]).to_csv(OUT/'consensus_any_h_sieve.csv',index=False)

# Final status from strict sieve only. If no strict veto, keep core unfiltered rather than manufacture one.
final=[]
for _,r in fd.iterrows():
    q=sel[sel.method_id.eq(r.method_id)] if len(sel) else pd.DataFrame()
    status='CO_CORE_READY_UNFILTERED'
    fam=feat=op='';thr=np.nan
    if len(q):
        z=q.iloc[0];status='CO_CORE_READY_WITH_VETO';fam=z.family;feat=z.feature;op=z.op;thr=z.threshold
    # Caution if full-sample weak window exists but not validated as timing veto.
    weak=pd.DataFrame(rows); wg=weak[(weak.method_id==r.method_id)&weak.descriptive_weak]
    final.append({'method_id':r.method_id,'base_n':r.base_n,'base_win_pct':r.base_win_pct,'base_roi':r.base_roi,'holdout_n':r.holdout_n,'holdout_win_pct':r.holdout_win_pct,'holdout_roi':r.holdout_roi,
                  'status':status,'veto_family':fam,'veto_feature':feat,'veto_op':op,'veto_threshold':thr,'descriptive_weak_windows':'|'.join(wg.segment.tolist()) if len(wg) else '',
                  'timing_veto_applied':False})
final=pd.DataFrame(final);final.to_csv(OUT/'final_coach_only_arsenal_sieve.csv',index=False)
summary={'input_methods':len(fd),'strict_veto_count':int(len(sv)),'methods_with_strict_single_veto':int(sel.method_id.nunique()) if len(sel) else 0,'ultra_strict_vetoes':int(sv.ultra_strict.sum()) if len(sv) else 0,'elite_co_h_pairs':int(pairs.elite_overlap.sum()),'priority_co_h_pairs':int(pairs.priority.sum()),'priority_any_h':int(anyh.priority.sum()),'status_counts':final.status.value_counts().to_dict()}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACH-ONLY FINAL SIEVE','',json.dumps(summary,indent=2),'','SELECTED STRICT VETOES']
for _,r in sel.iterrows():lines.append(f"{r.method_id} {r.family} {r.feature} {r.op} {r.threshold:.6g} | risk hold n={int(r.holdout_risk_n)} win={100*r.holdout_risk_win_pct:.1f}% ROI={100*r.holdout_risk_roi:+.1f}% -> safe ROI={100*r.holdout_safe_roi:+.1f}% retain={100*r.retained_share:.0f}% ultra={bool(r.ultra_strict)}")
lines+=['','ELITE CO+H PAIRS']
for _,r in pairs[pairs.elite_overlap].iterrows():lines.append(f"{r.coach_method}+{r.h_method}: {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold {int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}%")
lines+=['','PRIORITY ANY-H CONSENSUS']
for _,r in anyh[anyh.priority].iterrows():lines.append(f"{r.coach_method}+any H: {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold {int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}%")
lines+=['','FINAL']
for _,r in final.iterrows():lines.append(f"{r.method_id}: {r.status} | {int(r.base_n)} bets win={100*r.base_win_pct:.1f}% ROI={100*r.base_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% | weak windows descriptive only={r.descriptive_weak_windows or '-'} | veto={r.veto_family+':'+r.veto_feature if r.veto_feature else '-'}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
