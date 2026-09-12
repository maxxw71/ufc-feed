#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
OUTDIR="$ROOT/wrestling_stability_positive_roi"
mkdir -p "$OUTDIR"

[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python - "$IN" "$OUTDIR" <<'PY'
import sys,re
from pathlib import Path
import numpy as np
import pandas as pd

inp=Path(sys.argv[1]); outdir=Path(sys.argv[2])
df=pd.read_csv(inp,low_memory=False)
df['event_date']=pd.to_datetime(df['event_date'],errors='coerce').dt.normalize()
df=df.dropna(subset=['event_date','player1','player2']).sort_values('event_date').reset_index(drop=True)

def b(v):
    if isinstance(v,bool): return v
    return str(v).strip().lower() in {'true','1','yes','w','win'}

def parse_of(v):
    if pd.isna(v): return 0.0,0.0
    m=re.search(r'(\d+)\s+of\s+(\d+)',str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.0,0.0)

def parse_num(v):
    if pd.isna(v): return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v))
    return float(m.group()) if m else 0.0

def parse_ctrl(v):
    if pd.isna(v): return 0.0
    m=re.match(r'\s*(\d+):(\d+)\s*$',str(v))
    return 60*int(m.group(1))+int(m.group(2)) if m else 0.0

def fight_seconds(r):
    rnd=max(1,int(parse_num(r.get('round',1)) or 1))
    m=re.match(r'(\d+):(\d+)',str(r.get('time','0:00')))
    sec=int(m.group(1))*60+int(m.group(2)) if m else 0
    return max(1,(rnd-1)*300+sec)

# Build fighter-perspective career history from completed prior fights only.
records=[]
for _,r in df.iterrows():
    dur=fight_seconds(r); mins=dur/60.0
    p1won=b(r.get('p1_won',False))
    for side in (1,2):
        opp=2 if side==1 else 1
        td_l=td_a=opp_td_l=opp_td_a=0.0
        sub=rev=ctrl=opp_ctrl=ground_l=0.0
        for rd in range(1,6):
            a,c=parse_of(r.get(f'p{side}_rd{rd}_Td')); td_l+=a; td_a+=c
            a,c=parse_of(r.get(f'p{opp}_rd{rd}_Td')); opp_td_l+=a; opp_td_a+=c
            sub+=parse_num(r.get(f'p{side}_rd{rd}_Sub_att'))
            rev+=parse_num(r.get(f'p{side}_rd{rd}_Rev'))
            ctrl+=parse_ctrl(r.get(f'p{side}_rd{rd}_Ctrl'))
            opp_ctrl+=parse_ctrl(r.get(f'p{opp}_rd{rd}_Ctrl'))
            a,_=parse_of(r.get(f'p{side}_rd{rd}_Ground')); ground_l+=a
        records.append({
            'date':r.event_date,'fighter_url':r[f'player{side}_url'],
            'won':p1won if side==1 else (not p1won),'mins':mins,
            'td_l':td_l,'td_a':td_a,'td_allowed':opp_td_l,'td_faced':opp_td_a,
            'sub':sub,'rev':rev,'ctrl_min':ctrl/60.0,'ctrl_allowed_min':opp_ctrl/60.0,
            'ground_l':ground_l,
        })
hist=pd.DataFrame(records).sort_values(['fighter_url','date']).reset_index(drop=True)
groups={k:g.copy() for k,g in hist.groupby('fighter_url')}

def profile(url,date):
    g=groups.get(url)
    if g is None:return None
    g=g[g.date<date]
    if g.empty:return None
    mins=float(g.mins.sum())
    if mins<=0:return None
    sc=15.0/mins
    td_l=float(g.td_l.sum()); td_a=float(g.td_a.sum())
    td_allowed=float(g.td_allowed.sum()); td_faced=float(g.td_faced.sum())
    return {
        'fights':len(g),'win_pct':float(g.won.mean()),
        'td_l15':td_l*sc,'td_a15':td_a*sc,
        'td_acc':td_l/td_a if td_a>0 else np.nan,
        'td_def':1-td_allowed/td_faced if td_faced>0 else np.nan,
        'sub15':float(g['sub'].sum())*sc,
        'rev15':float(g.rev.sum())*sc,
        'ctrl15':float(g.ctrl_min.sum())*sc,
        'ctrl_allowed15':float(g.ctrl_allowed_min.sum())*sc,
        'ground_l15':float(g.ground_l.sum())*sc,
    }

# One row per fighter-side per target fight, using only pre-fight history.
rows=[]
for _,r in df.iterrows():
    p1=profile(r.player1_url,r.event_date); p2=profile(r.player2_url,r.event_date)
    if p1 is None or p2 is None: continue
    fav_is_p1=b(r.get('market_fav_is_p1',False))
    fav_prob=pd.to_numeric(pd.Series([r.get('market_prob')]),errors='coerce').iloc[0]
    fav_dec=pd.to_numeric(pd.Series([r.get('fav_decimal')]),errors='coerce').iloc[0]
    if pd.isna(fav_prob) or pd.isna(fav_dec): continue
    p1won=b(r.get('p1_won',False))
    for side,(me,opp) in enumerate(((p1,p2),(p2,p1)),start=1):
        is_fav=(side==1 and fav_is_p1) or (side==2 and not fav_is_p1)
        won=p1won if side==1 else (not p1won)
        side_prob=float(fav_prob if is_fav else 1-fav_prob)
        rows.append({
            'event_date':r.event_date,
            'fighter':r[f'player{side}'],'opponent':r[f'player{2 if side==1 else 1}'],
            'division':r.get('division',r.get('weightclass')),
            'won':won,'is_favorite':is_fav,'market_prob':side_prob,
            'fav_decimal':float(fav_dec) if is_fav else np.nan,
            **{f'w_{k}':v for k,v in me.items()},
            **{f'o_{k}':v for k,v in opp.items()},
        })
t=pd.DataFrame(rows)
t=t[t.is_favorite].copy()  # ROI analysis only where wrestler side was favorite.
t['profit100']=np.where(t.won,100*(t.fav_decimal-1),-100.0)
t.to_csv(outdir/'all_favorite_sides_with_prefight_wrestling.csv',index=False)

# Threshold grid centered around the profitable style-contrast zone.
w_td_l=[1.0,1.25,1.5,1.75,2.0,2.25,2.5]
w_td_a=[3.0,4.0,5.0,6.0]
w_ctrl=[1.5,2.0,2.5,3.0,3.5,4.0]
o_td_a=[0.5,1.0,1.5,2.0,2.5]
o_ctrl=[0.5,0.75,1.0,1.5,2.0]
min_fights=[2,3,4]
prob_mins=[0.50,0.55,0.60,0.65,0.70,0.75,0.80]


def stats(g):
    n=len(g)
    if not n:return None
    p=float(g.profit100.sum()); roi=p/(100*n)
    old=g[g.event_date<pd.Timestamp('2020-01-01')]
    rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    def rr(z): return float(z.profit100.sum()/(100*len(z))) if len(z) else np.nan
    return {
        'n':n,'wins':int(g.won.sum()),'win_rate':float(g.won.mean()),
        'mean_market_prob':float(g.market_prob.mean()),'profit100':p,'roi':roi,
        'pre_n':len(old),'pre_roi':rr(old),'recent_n':len(rec),'recent_roi':rr(rec)
    }

res=[]
for tl in w_td_l:
  for ta in w_td_a:
    for wc in w_ctrl:
      for ota in o_td_a:
        for oc in o_ctrl:
          for mf in min_fights:
            base=t[(t.w_fights>=mf)&(t.o_fights>=mf)&
                   (t.w_td_l15>=tl)&(t.w_td_a15>=ta)&(t.w_ctrl15>=wc)&
                   (t.o_td_a15<=ota)&(t.o_ctrl15<=oc)]
            if len(base)<30: continue
            for pm in prob_mins:
                g=base[base.market_prob>=pm]
                s=stats(g)
                if not s: continue
                res.append({
                    'w_td_l15_min':tl,'w_td_a15_min':ta,'w_ctrl15_min':wc,
                    'opp_td_a15_max':ota,'opp_ctrl15_max':oc,'min_prior_fights':mf,
                    'favorite_prob_min':pm,**s
                })
res=pd.DataFrame(res)
res.to_csv(outdir/'all_threshold_probability_results.csv',index=False)

# Only positive-ROI candidates survive. Negative ROI is deliberately excluded.
pos=res[(res.roi>0)&(res.pre_roi>0)&(res.recent_roi>0)&
        (res.n>=50)&(res.pre_n>=20)&(res.recent_n>=20)].copy()
pos=pos.sort_values(['roi','n'],ascending=[False,False])
pos.to_csv(outdir/'positive_roi_candidates.csv',index=False)

# Stability scoring: for each rule, inspect one-step nearby thresholds at same probability floor.
param_cols=['w_td_l15_min','w_td_a15_min','w_ctrl15_min','opp_td_a15_max','opp_ctrl15_max','min_prior_fights']
values={c:sorted(res[c].dropna().unique()) for c in param_cols}
lookup={tuple(r[c] for c in param_cols+['favorite_prob_min']):r for _,r in res.iterrows()}

stable=[]
for _,r in pos.iterrows():
    neigh=[]
    basekey=[r[c] for c in param_cols]
    for i,c in enumerate(param_cols):
        vals=values[c]; cur=r[c]; idx=vals.index(cur)
        for j in [idx-1,idx+1]:
            if 0<=j<len(vals):
                key=basekey.copy(); key[i]=vals[j]
                rr=lookup.get(tuple(key+[r.favorite_prob_min]))
                if rr is not None and rr['n']>=40:
                    neigh.append(float(rr['roi']))
    if not neigh: continue
    stable.append({**r.to_dict(),
                   'neighbors':len(neigh),
                   'neighbor_mean_roi':float(np.mean(neigh)),
                   'neighbor_min_roi':float(np.min(neigh)),
                   'all_neighbors_positive':bool(np.min(neigh)>0)})
stab=pd.DataFrame(stable)
if not stab.empty:
    stab=stab[(stab.all_neighbors_positive)&(stab.neighbor_min_roi>0)].copy()
    stab=stab.sort_values(['neighbor_mean_roi','roi','n'],ascending=[False,False,False])
    stab.to_csv(outdir/'stable_positive_roi_candidates.csv',index=False)

# Probability bands for a clean anchor around the first strong profile from the initial study.
anchor=t[(t.w_fights>=2)&(t.o_fights>=2)&
         (t.w_td_l15>=1.5)&(t.w_td_a15>=4.0)&(t.w_ctrl15>=2.5)&
         (t.o_td_a15<=1.0)&(t.o_ctrl15<=0.75)].copy()

bands=[]
for lo,hi,label in [(0.50,.55,'50-54.9%'),(.55,.60,'55-59.9%'),(.60,.65,'60-64.9%'),(.65,.70,'65-69.9%'),(.70,.75,'70-74.9%'),(.75,.80,'75-79.9%'),(.80,1.01,'80%+')]:
    g=anchor[(anchor.market_prob>=lo)&(anchor.market_prob<hi)]
    s=stats(g)
    if s and s['roi']>0:  # skip negative ROI bands completely
        bands.append({'market_band':label,**s})
bands=pd.DataFrame(bands)
bands.to_csv(outdir/'positive_probability_bands.csv',index=False)

# Opponent TDD and control-allowed modifiers, positive ROI only.
mods=[]
for lo,hi,label in [(0,.50,'TDD <50%'),(.50,.60,'TDD 50-59.9%'),(.60,.70,'TDD 60-69.9%'),(.70,.80,'TDD 70-79.9%'),(.80,1.01,'TDD 80%+')]:
    g=anchor[(anchor.o_td_def>=lo)&(anchor.o_td_def<hi)]
    s=stats(g)
    if s and s['roi']>0: mods.append({'modifier':label,**s})
for lo,hi,label in [(0,1,'opp ctrl allowed <1m/15'),(1,2,'opp ctrl allowed 1-1.9m/15'),(2,3,'opp ctrl allowed 2-2.9m/15'),(3,99,'opp ctrl allowed 3m+/15')]:
    g=anchor[(anchor.o_ctrl_allowed15>=lo)&(anchor.o_ctrl_allowed15<hi)]
    s=stats(g)
    if s and s['roi']>0: mods.append({'modifier':label,**s})
mods=pd.DataFrame(mods)
mods.to_csv(outdir/'positive_modifiers_only.csv',index=False)

lines=[]
lines.append('WRESTLING MISMATCH — POSITIVE ROI STABILITY ANALYSIS')
lines.append('='*124)
lines.append('Negative-ROI rules and negative-ROI probability/modifier bands are intentionally omitted.')
lines.append('All wrestling metrics are calculated only from UFC fights BEFORE the target fight.')
lines.append('')
lines.append('TOP STABLE POSITIVE-ROI RULES')
lines.append('='*124)
if stab.empty:
    lines.append('No candidate met the stability + positive ROI requirements.')
else:
    for _,r in stab.head(35).iterrows():
        lines.append(
            f"fav>={r.favorite_prob_min*100:4.0f}% | TDland>={r.w_td_l15_min:.2f}/15 | TDatt>={r.w_td_a15_min:.1f}/15 | "
            f"ctrl>={r.w_ctrl15_min:.2f}m/15 | oppTDatt<={r.opp_td_a15_max:.2f}/15 | oppctrl<={r.opp_ctrl15_max:.2f}m/15 | prior>={int(r.min_prior_fights)} | "
            f"n={int(r.n):3d} {int(r.wins)}-{int(r.n-r.wins)} win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | "
            f"pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}% | neighbors={int(r.neighbors)} mean={r.neighbor_mean_roi*100:+6.2f}% min={r.neighbor_min_roi*100:+6.2f}%"
        )
lines.append('')
lines.append('ANCHOR RULE — POSITIVE FAVORITE-PROBABILITY BANDS ONLY')
lines.append('='*124)
lines.append('Anchor: TDland>=1.5/15, TDatt>=4/15, control>=2.5m/15; opponent TDatt<=1/15, control<=0.75m/15; prior>=2.')
if bands.empty:
    lines.append('No positive-ROI market bands.')
else:
    for _,r in bands.iterrows():
        lines.append(f"{r.market_band:<12} n={int(r.n):3d} win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}%")
lines.append('')
lines.append('POSITIVE MODIFIERS ONLY')
lines.append('='*124)
if mods.empty:
    lines.append('No positive modifiers.')
else:
    for _,r in mods.iterrows():
        lines.append(f"{r.modifier:<30} n={int(r.n):3d} win={r.win_rate*100:5.1f}% ROI={r.roi*100:+6.2f}% | pre={r.pre_roi*100:+6.2f}% recent={r.recent_roi*100:+6.2f}%")
lines.append('')
lines.append('Interpretation: only profitable historical favorite subsets are displayed. This is a research filter, not a guarantee of future returns.')

report='\n'.join(lines)+'\n'
(outdir/'report.txt').write_text(report)
print(report)
print('Saved:',outdir/'report.txt')
print('Saved:',outdir/'positive_roi_candidates.csv')
print('Saved:',outdir/'stable_positive_roi_candidates.csv')
print('Saved:',outdir/'positive_probability_bands.csv')
print('Saved:',outdir/'positive_modifiers_only.csv')
PY
