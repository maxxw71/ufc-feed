#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/new_category_discovery/prefight_favorite_features.csv"
OUT="$ROOT/striking_family_stability"
mkdir -p "$OUT"

[ -s "$IN" ] || { echo "ERROR: Missing $IN"; echo "Run the new-category discovery scan first."; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUT" <<'PY'
import sys
from pathlib import Path
import numpy as np
import pandas as pd

inp=Path(sys.argv[1]); out=Path(sys.argv[2])

def progress(p,msg): print(f"[{p:3d}%] {msg}",flush=True)

progress(5,"Loading cached 2006-2026 pre-fight feature table")
t=pd.read_csv(inp,low_memory=False)
t['event_date']=pd.to_datetime(t['event_date'],errors='coerce')
for c in t.columns:
    if c not in {'event_date','favorite','opponent'}:
        t[c]=pd.to_numeric(t[c],errors='coerce')
t=t.dropna(subset=['event_date','market_prob','profit100','won']).copy()
progress(12,f"Loaded {len(t):,} favorite-side fights")

def metrics(mask):
    g=t[mask.fillna(False)].copy(); n=len(g)
    if not n:return None
    old=g[g.event_date<pd.Timestamp('2020-01-01')]; rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    roi=lambda z: float(z.profit100.sum()/(100*len(z))) if len(z) else np.nan
    return {
        'n':n,'wins':int(g.won.sum()),'win_rate':float(g.won.mean()),
        'roi':roi(g),'pre_n':len(old),'pre_roi':roi(old),'recent_n':len(rec),'recent_roi':roi(rec)
    }

families={}

# 1) PACE + DEFENSE
rows=[]
for pm in [.55,.60,.65,.70,.75]:
  for prior in [2,3,4]:
    for landed in [.25,.50,.75,1.00,1.25]:
      for dgap in [.05,.10,.15,.20]:
        mask=(t.market_prob>=pm)&(t.f_fights>=prior)&(t.o_fights>=prior)&((t.f_sig_l_pm-t.o_sig_l_pm)>=landed)&((t.f_sig_def-t.o_sig_def)>=dgap)
        s=metrics(mask)
        if s: rows.append({'family':'PACE + DEFENSE','market_min':pm,'prior_min':prior,'landed_gap':landed,'sigdef_gap':dgap,**s})
families['PACE + DEFENSE']=pd.DataFrame(rows)
progress(28,"Pace + Defense grid complete")

# 2) STRIKING + TD DEFENSE
rows=[]
for pm in [.60,.65,.70,.75,.80]:
  for prior in [2,3,4]:
    for sdiff in [.50,.75,1.00,1.25,1.50]:
      for tdg in [.05,.10,.15,.20]:
        mask=(t.market_prob>=pm)&(t.f_fights>=prior)&(t.o_fights>=prior)&((t.f_sig_diff_pm-t.o_sig_diff_pm)>=sdiff)&((t.f_td_def-t.o_td_def)>=tdg)
        s=metrics(mask)
        if s: rows.append({'family':'STRIKING + TD DEFENSE','market_min':pm,'prior_min':prior,'sigdiff_gap':sdiff,'tddef_gap':tdg,**s})
families['STRIKING + TD DEFENSE']=pd.DataFrame(rows)
progress(45,"Striking + TD Defense grid complete")

# 3) STRIKING DEFENSE
rows=[]
for pm in [.65,.70,.75,.80]:
  for prior in [2,3,4]:
    for dgap in [.05,.10,.15,.20]:
      for absorbed in [.50,1.00,1.50,2.00]:
        mask=(t.market_prob>=pm)&(t.f_fights>=prior)&(t.o_fights>=prior)&((t.f_sig_def-t.o_sig_def)>=dgap)&((t.o_sig_abs_pm-t.f_sig_abs_pm)>=absorbed)
        s=metrics(mask)
        if s: rows.append({'family':'STRIKING DEFENSE','market_min':pm,'prior_min':prior,'sigdef_gap':dgap,'absorbed_gap':absorbed,**s})
families['STRIKING DEFENSE']=pd.DataFrame(rows)
progress(60,"Striking Defense grid complete")

# 4) STRIKING DIFFERENTIAL
rows=[]
for pm in [.65,.70,.75,.80]:
  for prior in [2,3,4]:
    for gap in [.50,.75,1.00,1.25,1.50]:
      for floor in [-.50,0.00,.50,1.00]:
        mask=(t.market_prob>=pm)&(t.f_fights>=prior)&(t.o_fights>=prior)&((t.f_sig_diff_pm-t.o_sig_diff_pm)>=gap)&(t.f_sig_diff_pm>=floor)
        s=metrics(mask)
        if s: rows.append({'family':'STRIKING DIFFERENTIAL','market_min':pm,'prior_min':prior,'sigdiff_gap':gap,'fav_sigdiff_floor':floor,**s})
families['STRIKING DIFFERENTIAL']=pd.DataFrame(rows)
progress(75,"Striking Differential grid complete")

# Save all grids and isolate robust 9%+ candidates.
all_frames=[]; robust_frames=[]
for fam,df in families.items():
    df.to_csv(out/(fam.lower().replace(' + ','_').replace(' ','_')+'_all.csv'),index=False)
    all_frames.append(df)
    r=df[(df.roi>=.09)&(df.pre_roi>0)&(df.recent_roi>0)&(df.n>=45)&(df.pre_n>=15)&(df.recent_n>=15)].copy()
    robust_frames.append(r)
allres=pd.concat(all_frames,ignore_index=True,sort=False)
robust=pd.concat(robust_frames,ignore_index=True,sort=False)
allres.to_csv(out/'all_striking_family_results.csv',index=False)
robust.to_csv(out/'all_9pct_positive_both_eras.csv',index=False)
progress(82,f"Retained {len(robust):,} rules at >=9% ROI, positive in both eras")

# Neighbor stability. A neighbor differs by exactly one grid step in one parameter, same family.
param_map={
 'PACE + DEFENSE':['market_min','prior_min','landed_gap','sigdef_gap'],
 'STRIKING + TD DEFENSE':['market_min','prior_min','sigdiff_gap','tddef_gap'],
 'STRIKING DEFENSE':['market_min','prior_min','sigdef_gap','absorbed_gap'],
 'STRIKING DIFFERENTIAL':['market_min','prior_min','sigdiff_gap','fav_sigdiff_floor'],
}
stable=[]
for fam,df in families.items():
    params=param_map[fam]
    vals={p:sorted(df[p].dropna().unique().tolist()) for p in params}
    lookup={tuple(row[p] for p in params):row for _,row in df.iterrows()}
    famrob=robust[robust.family==fam]
    for _,r in famrob.iterrows():
        base=[r[p] for p in params]; neighbors=[]
        for i,p in enumerate(params):
            arr=vals[p]
            try: idx=arr.index(r[p])
            except ValueError: continue
            for j in (idx-1,idx+1):
                if 0<=j<len(arr):
                    k=base.copy(); k[i]=arr[j]
                    rr=lookup.get(tuple(k))
                    if rr is not None and rr['n']>=35 and rr['pre_n']>=10 and rr['recent_n']>=10:
                        neighbors.append(rr)
        if not neighbors: continue
        nrois=np.array([float(x['roi']) for x in neighbors])
        npre=np.array([float(x['pre_roi']) for x in neighbors])
        nrec=np.array([float(x['recent_roi']) for x in neighbors])
        stable.append({**r.to_dict(),
            'neighbors':len(neighbors),
            'neighbor_mean_roi':float(np.mean(nrois)),
            'neighbor_min_roi':float(np.min(nrois)),
            'neighbors_positive_both_eras':bool((npre>0).all() and (nrec>0).all()),
            'neighbors_ge_7pct_share':float(np.mean(nrois>=.07)),
            'neighbors_ge_9pct_share':float(np.mean(nrois>=.09)),
        })
stab=pd.DataFrame(stable)
if not stab.empty:
    stab=stab[(stab.neighbors>=4)&(stab.neighbor_min_roi>0)&(stab.neighbors_positive_both_eras)].copy()
    stab=stab.sort_values(['neighbors_ge_9pct_share','neighbor_mean_roi','roi','n'],ascending=[False,False,False,False])
    stab.to_csv(out/'stable_striking_candidates.csv',index=False)
progress(90,f"Stability filter retained {len(stab):,} candidates")

print('\nTOP STABLE CANDIDATES BY FAMILY')
print('='*130)
for fam in param_map:
    print(f'\n{fam}')
    print('-'*130)
    z=stab[stab.family==fam].copy() if not stab.empty else pd.DataFrame()
    if z.empty:
        print('No candidate cleared the stability requirements.')
        continue
    for _,r in z.head(10).iterrows():
        if fam=='PACE + DEFENSE':
            rule=f"mkt>={r.market_min:.2f}, prior>={int(r.prior_min)}, landedGap>={r.landed_gap:.2f}/min, sigDefGap>={r.sigdef_gap:.2f}"
        elif fam=='STRIKING + TD DEFENSE':
            rule=f"mkt>={r.market_min:.2f}, prior>={int(r.prior_min)}, sigDiffGap>={r.sigdiff_gap:.2f}/min, TDdefGap>={r.tddef_gap:.2f}"
        elif fam=='STRIKING DEFENSE':
            rule=f"mkt>={r.market_min:.2f}, prior>={int(r.prior_min)}, sigDefGap>={r.sigdef_gap:.2f}, absorbedGap>={r.absorbed_gap:.2f}/min"
        else:
            rule=f"mkt>={r.market_min:.2f}, prior>={int(r.prior_min)}, sigDiffGap>={r.sigdiff_gap:.2f}/min, favSigDiff>={r.fav_sigdiff_floor:+.2f}"
        print(f"{rule} | n={int(r.n):3d} {int(r.wins)}-{int(r.n-r.wins)} win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}% | neighbors={int(r.neighbors)} mean={r.neighbor_mean_roi*100:+6.2f}% min={r.neighbor_min_roi*100:+6.2f}% | >=9% neighbors={r.neighbors_ge_9pct_share*100:4.0f}%")

print('\nWIDEST >=9% ROI CANDIDATE BY FAMILY')
print('='*130)
for fam in param_map:
    z=robust[robust.family==fam].sort_values(['n','roi'],ascending=[False,False])
    if z.empty:
        print(f'{fam}: none')
        continue
    r=z.iloc[0]
    print(f"{fam:<24} n={int(r.n):3d} win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}%")

print('\n[100%] Complete',flush=True)
print('Saved:',out/'stable_striking_candidates.csv')
print('Saved:',out/'all_9pct_positive_both_eras.csv')
PY
