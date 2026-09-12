#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
CACHE1="$ROOT/wrestling_stability_positive_roi/all_favorite_sides_with_prefight_wrestling.csv"
CACHE2="$ROOT/wrestling_stability_fast/all_favorite_sides_with_prefight_wrestling.csv"
OUTDIR="$ROOT/opponent_wrestling_threshold_expansion"
mkdir -p "$OUTDIR"

if [ -s "$CACHE1" ]; then IN="$CACHE1";
elif [ -s "$CACHE2" ]; then IN="$CACHE2";
else
  echo "ERROR: Missing cached pre-fight wrestling table."
  echo "Run the wrestling stability analysis first."
  exit 2
fi

source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUTDIR" <<'PY'
import sys
from pathlib import Path
import numpy as np
import pandas as pd

inp=Path(sys.argv[1]); outdir=Path(sys.argv[2])

def progress(p,msg): print(f"[{p:3d}%] {msg}", flush=True)
progress(5,"Loading cached pre-fight favorite wrestling table...")
df=pd.read_csv(inp,low_memory=False)
df['event_date']=pd.to_datetime(df['event_date'],errors='coerce')
for c in ['w_fights','o_fights','w_td_l15','w_td_a15','w_ctrl15','o_td_a15','o_ctrl15','profit100']:
    df[c]=pd.to_numeric(df[c],errors='coerce')
if 'won' in df:
    if not pd.api.types.is_bool_dtype(df['won']):
        df['won']=df['won'].astype(str).str.lower().isin(['true','1','yes','w','win'])

# Broad, fixed wrestler side. We vary ONLY opponent wrestling activity thresholds.
base=df[(df.w_fights>=2)&(df.o_fights>=2)&
        (df.w_td_a15>=5.0)&(df.w_td_l15>=1.0)&(df.w_ctrl15>=1.0)].copy()
progress(20,f"Broad wrestler-side pool fixed: {len(base):,} fights")

def stats(g):
    n=len(g)
    if not n:return None
    p=float(g.profit100.sum()); roi=p/(100*n)
    old=g[g.event_date<pd.Timestamp('2020-01-01')]
    rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    def rr(z): return float(z.profit100.sum()/(100*len(z))) if len(z) else np.nan
    return dict(n=n,wins=int(g.won.sum()),losses=n-int(g.won.sum()),win_rate=float(g.won.mean()),roi=roi,
                profit100=p,pre_n=len(old),pre_roi=rr(old),recent_n=len(rec),recent_roi=rr(rec))

# Baseline broad wrestler pool.
bs=stats(base)
print("\nBROAD WRESTLER-SIDE BASELINE")
print("="*108)
print("Favorite; both fighters >=2 prior UFC fights; wrestler TDatt>=5/15, TDland>=1/15, control>=1m/15")
print(f"n={bs['n']} | {bs['wins']}-{bs['losses']} | win={bs['win_rate']*100:.1f}% | ROI={bs['roi']*100:+.2f}% | pre={bs['pre_roi']*100:+.2f}% | 2020+={bs['recent_roi']*100:+.2f}%")

# Independent test A: opponent TD attempts only, with no opponent-control cap.
td_rows=[]
for tdmax in [0.5,1.0,1.5,2.0]:
    g=base[base.o_td_a15<=tdmax]
    s=stats(g)
    if s: td_rows.append({'opp_td_attempts_max':tdmax,**s})
td=pd.DataFrame(td_rows)
td.to_csv(outdir/'opponent_td_attempts_only.csv',index=False)
progress(35,"Completed opponent TD-attempt thresholds")

print("\nOPPONENT TD ATTEMPTS — INDEPENDENT TEST")
print("="*108)
for _,r in td.iterrows():
    flag='  <-- >=10% ROI' if r.roi>=.10 else ''
    print(f"opp TDatt <= {r.opp_td_attempts_max:.2f}/15 | n={int(r.n):3d} | {int(r.wins)}-{int(r.losses)} | win={r.win_rate*100:5.1f}% | ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% | 2020+={r.recent_roi*100:+6.2f}%{flag}")

# Independent test B: opponent control only, with no opponent-TD cap.
ctrl_rows=[]
for cmax in [0.50,0.75,1.00,1.25,1.50]:
    g=base[base.o_ctrl15<=cmax]
    s=stats(g)
    if s: ctrl_rows.append({'opp_control_max':cmax,**s})
ctrl=pd.DataFrame(ctrl_rows)
ctrl.to_csv(outdir/'opponent_control_only.csv',index=False)
progress(50,"Completed opponent control thresholds")

print("\nOPPONENT CONTROL — INDEPENDENT TEST")
print("="*108)
for _,r in ctrl.iterrows():
    flag='  <-- >=10% ROI' if r.roi>=.10 else ''
    print(f"opp ctrl <= {r.opp_control_max:.2f}m/15 | n={int(r.n):3d} | {int(r.wins)}-{int(r.losses)} | win={r.win_rate*100:5.1f}% | ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% | 2020+={r.recent_roi*100:+6.2f}%{flag}")

# Full 4x5 combination matrix.
combo=[]
tdvals=[0.5,1.0,1.5,2.0]
cvals=[0.50,0.75,1.00,1.25,1.50]
total=len(tdvals)*len(cvals); done=0
for tdmax in tdvals:
    for cmax in cvals:
        g=base[(base.o_td_a15<=tdmax)&(base.o_ctrl15<=cmax)]
        s=stats(g)
        if s: combo.append({'opp_td_attempts_max':tdmax,'opp_control_max':cmax,**s})
        done+=1
        if done in [5,10,15,20]: progress(50+int(35*done/total),f"Combination grid {done}/{total} complete")
comb=pd.DataFrame(combo)
comb.to_csv(outdir/'all_opponent_threshold_combinations.csv',index=False)

# User wants anything >=10% ROI. Keep all such rows; also identify stronger era-validated ones.
roi10=comb[comb.roi>=.10].sort_values(['n','roi'],ascending=[False,False]).copy()
roi10.to_csv(outdir/'all_combinations_roi_10plus.csv',index=False)
validated=roi10[(roi10.pre_roi>0)&(roi10.recent_roi>0)].copy()
validated=validated.sort_values(['n','roi'],ascending=[False,False])
validated.to_csv(outdir/'roi_10plus_positive_both_eras.csv',index=False)
progress(90,f">=10% ROI combinations found: {len(roi10)}; positive both eras: {len(validated)}")

print("\nALL COMBINATIONS WITH >=10% ROI")
print("="*108)
if roi10.empty:
    print("None.")
else:
    for _,r in roi10.iterrows():
        era='BOTH ERAS +' if (r.pre_roi>0 and r.recent_roi>0) else 'ERA WARNING'
        print(f"opp TDatt<={r.opp_td_attempts_max:.2f}/15 | opp ctrl<={r.opp_control_max:.2f}m/15 | n={int(r.n):3d} | {int(r.wins)}-{int(r.losses)} | win={r.win_rate*100:5.1f}% | ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% | 2020+={r.recent_roi*100:+6.2f}% | {era}")

print("\nLARGEST-SAMPLE >=10% ROI RULES WITH BOTH ERAS POSITIVE")
print("="*108)
if validated.empty:
    print("None.")
else:
    for _,r in validated.head(10).iterrows():
        print(f"opp TDatt<={r.opp_td_attempts_max:.2f}/15 | opp ctrl<={r.opp_control_max:.2f}m/15 | n={int(r.n):3d} | ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% | 2020+={r.recent_roi*100:+6.2f}%")

# Matrix for quick visual comparison.
mat=comb.pivot(index='opp_td_attempts_max',columns='opp_control_max',values='roi')*100
mat.to_csv(outdir/'roi_matrix_percent.csv')
print("\nROI MATRIX (%) — rows=opponent TD attempts max, columns=opponent control max")
print(mat.round(2).to_string())

progress(100,"Complete")
print(f"Saved: {outdir/'all_combinations_roi_10plus.csv'}")
print(f"Saved: {outdir/'roi_10plus_positive_both_eras.csv'}")
PY
