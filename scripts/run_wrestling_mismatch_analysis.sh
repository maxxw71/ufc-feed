#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
OUTDIR="$ROOT/wrestling_mismatch_analysis"
mkdir -p "$OUTDIR"

if [ ! -s "$IN" ]; then
  echo "ERROR: Missing $IN"
  exit 2
fi

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
    s=str(v).strip().lower()
    return s in {'true','1','yes','w','win'}

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

# Build fighter-perspective history rows from each completed fight.
records=[]
for _,r in df.iterrows():
    dur=fight_seconds(r); mins=dur/60.0
    p1won=b(r.get('p1_won',False))
    for side in (1,2):
        opp=2 if side==1 else 1
        td_l=td_a=opp_td_l=opp_td_a=0.0
        sub=rev=ctrl=ground_l=ground_a=0.0
        opp_ctrl=0.0
        for rd in range(1,6):
            a,c=parse_of(r.get(f'p{side}_rd{rd}_Td')); td_l+=a; td_a+=c
            a,c=parse_of(r.get(f'p{opp}_rd{rd}_Td')); opp_td_l+=a; opp_td_a+=c
            sub+=parse_num(r.get(f'p{side}_rd{rd}_Sub_att'))
            rev+=parse_num(r.get(f'p{side}_rd{rd}_Rev'))
            ctrl+=parse_ctrl(r.get(f'p{side}_rd{rd}_Ctrl'))
            opp_ctrl+=parse_ctrl(r.get(f'p{opp}_rd{rd}_Ctrl'))
            a,c=parse_of(r.get(f'p{side}_rd{rd}_Ground')); ground_l+=a; ground_a+=c
        records.append({
            'date':r.event_date,
            'fighter':r[f'player{side}'],
            'fighter_url':r[f'player{side}_url'],
            'won':p1won if side==1 else (not p1won),
            'mins':mins,
            'td_l':td_l,'td_a':td_a,
            'td_allowed':opp_td_l,'td_faced':opp_td_a,
            'sub':sub,'rev':rev,
            'ctrl_min':ctrl/60.0,
            'ctrl_allowed_min':opp_ctrl/60.0,
            'ground_l':ground_l,'ground_a':ground_a,
        })
hist=pd.DataFrame(records).sort_values(['fighter_url','date']).reset_index(drop=True)
groups={k:g.copy() for k,g in hist.groupby('fighter_url')}

def profile(url,date):
    g=groups.get(url)
    if g is None: return None
    g=g[g.date<date]
    if g.empty: return None
    mins=float(g.mins.sum())
    if mins<=0: return None
    scale=15.0/mins
    td_l=float(g.td_l.sum()); td_a=float(g.td_a.sum())
    td_allowed=float(g.td_allowed.sum()); td_faced=float(g.td_faced.sum())
    return {
        'fights':len(g),
        'win_pct':float(g.won.mean()),
        'td_l15':td_l*scale,
        'td_a15':td_a*scale,
        'td_acc':td_l/td_a if td_a>0 else np.nan,
        'td_def':1-td_allowed/td_faced if td_faced>0 else np.nan,
        'sub15':float(g['sub'].sum())*scale,
        'rev15':float(g.rev.sum())*scale,
        'ctrl15':float(g.ctrl_min.sum())*scale,
        'ctrl_allowed15':float(g.ctrl_allowed_min.sum())*scale,
        'ground_l15':float(g.ground_l.sum())*scale,
    }

# Build target-fight rows from each side, using only prior-fight profiles.
rows=[]
for _,r in df.iterrows():
    p1=profile(r.player1_url,r.event_date); p2=profile(r.player2_url,r.event_date)
    if p1 is None or p2 is None: continue
    fav_is_p1=b(r.get('market_fav_is_p1',False))
    fav_prob=pd.to_numeric(pd.Series([r.get('market_prob')]),errors='coerce').iloc[0]
    fav_dec=pd.to_numeric(pd.Series([r.get('fav_decimal')]),errors='coerce').iloc[0]
    if pd.isna(fav_prob): continue
    p1won=b(r.get('p1_won',False))
    for side,(me,opp) in enumerate(((p1,p2),(p2,p1)),start=1):
        is_fav=(side==1 and fav_is_p1) or (side==2 and not fav_is_p1)
        my_prob=float(fav_prob if is_fav else 1-fav_prob)
        won=p1won if side==1 else (not p1won)
        rows.append({
            'event_date':r.event_date,
            'fighter':r[f'player{side}'],'opponent':r[f'player{2 if side==1 else 1}'],
            'division':r.get('division',r.get('weightclass')),
            'won':won,'is_market_favorite':is_fav,'market_prob_side':my_prob,
            'fav_decimal':fav_dec if is_fav else np.nan,
            **{f'w_{k}':v for k,v in me.items()},
            **{f'o_{k}':v for k,v in opp.items()},
        })

t=pd.DataFrame(rows)
if t.empty: raise RuntimeError('No fights with two-sided pre-fight wrestling history.')

# Exact $100 profit is available when the wrestler side is the market favorite.
t['favorite_profit_100']=np.where(
    t.is_market_favorite,
    np.where(t.won,100*(t.fav_decimal-1),-100.0),
    np.nan
)

def summarize(g):
    n=len(g)
    if not n:return None
    wr=float(g.won.mean()); mp=float(g.market_prob_side.mean()); edge=wr-mp
    fav=g[g.is_market_favorite & g.favorite_profit_100.notna()]
    fav_roi=float(fav.favorite_profit_100.sum()/(100*len(fav))) if len(fav) else np.nan
    pre=g[g.event_date<pd.Timestamp('2020-01-01')]
    rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    def e(z): return float(z.won.mean()-z.market_prob_side.mean()) if len(z) else np.nan
    return {
        'n':n,'wins':int(g.won.sum()),'win_rate':wr,'mean_market_prob':mp,'edge_pp':edge*100,
        'fav_n':len(fav),'fav_roi':fav_roi,
        'pre_n':len(pre),'pre_edge_pp':e(pre)*100 if len(pre) else np.nan,
        'recent_n':len(rec),'recent_edge_pp':e(rec)*100 if len(rec) else np.nan,
    }

# Main threshold grid: strong wrestler vs opponent who rarely wrestles.
w_td_l=[1.0,1.5,2.0,2.5,3.0]
w_td_a=[3.0,4.0,5.0,6.0]
w_ctrl=[1.5,2.5,3.5,4.5]
o_td_a=[1.0,2.0,3.0]
o_ctrl=[0.75,1.5,2.25]
min_fights=[2,3,4]
res=[]
for tl in w_td_l:
  for ta in w_td_a:
    for wc in w_ctrl:
      for ota in o_td_a:
        for oc in o_ctrl:
          for mf in min_fights:
            g=t[(t.w_fights>=mf)&(t.o_fights>=mf)&
                (t.w_td_l15>=tl)&(t.w_td_a15>=ta)&(t.w_ctrl15>=wc)&
                (t.o_td_a15<=ota)&(t.o_ctrl15<=oc)].copy()
            s=summarize(g)
            if not s: continue
            res.append({'w_td_l15_min':tl,'w_td_a15_min':ta,'w_ctrl15_min':wc,
                        'opp_td_a15_max':ota,'opp_ctrl15_max':oc,'min_prior_fights':mf,**s})
res=pd.DataFrame(res)
res.to_csv(outdir/'all_wrestling_thresholds.csv',index=False)

robust=res[(res.n>=80)&(res.pre_n>=30)&(res.recent_n>=25)&
           (res.edge_pp>2)&(res.pre_edge_pp>0)&(res.recent_edge_pp>0)].copy()
robust=robust.sort_values(['fav_roi','edge_pp','n'],ascending=[False,False,False])
robust.to_csv(outdir/'robust_wrestling_candidates.csv',index=False)

# A simple interpretable anchor rule for modifier analysis.
anchor=t[(t.w_fights>=3)&(t.o_fights>=3)&
         (t.w_td_l15>=1.5)&(t.w_td_a15>=4.0)&(t.w_ctrl15>=2.5)&
         (t.o_td_a15<=2.0)&(t.o_ctrl15<=1.5)].copy()

# Opponent TDD: does strong defensive wrestling cancel the mismatch?
tdd_rows=[]
for lo,hi,label in [(0,.50,'<50%'),(.50,.60,'50-59.9%'),(.60,.70,'60-69.9%'),(.70,.80,'70-79.9%'),(.80,1.01,'80%+')]:
    g=anchor[(anchor.o_td_def>=lo)&(anchor.o_td_def<hi)]
    s=summarize(g)
    if s:tdd_rows.append({'opp_tdd_band':label,**s})
pd.DataFrame(tdd_rows).to_csv(outdir/'opponent_tdd_modifier.csv',index=False)

# Control vulnerability: how often opponent has historically been controlled.
ctrl_rows=[]
for lo,hi,label in [(0,1,'<1m/15'),(1,2,'1-1.9m/15'),(2,3,'2-2.9m/15'),(3,99,'3m+/15')]:
    g=anchor[(anchor.o_ctrl_allowed15>=lo)&(anchor.o_ctrl_allowed15<hi)]
    s=summarize(g)
    if s:ctrl_rows.append({'opp_control_allowed_band':label,**s})
pd.DataFrame(ctrl_rows).to_csv(outdir/'opponent_control_vulnerability.csv',index=False)

# Grappling-intensity variants beyond takedowns/control.
extra=[]
for submin in [0,.25,.5,1.0]:
    for groundmin in [0,1,2,3]:
        g=anchor[(anchor.w_sub15>=submin)&(anchor.w_ground_l15>=groundmin)]
        s=summarize(g)
        if s:extra.append({'w_sub15_min':submin,'w_ground_l15_min':groundmin,**s})
pd.DataFrame(extra).to_csv(outdir/'submission_ground_modifiers.csv',index=False)

# Examples from anchor rule.
anchor.sort_values(['event_date'],ascending=False).head(100).to_csv(outdir/'anchor_examples.csv',index=False)

lines=[]
lines.append('WRESTLING MISMATCH ANALYSIS')
lines.append('='*118)
lines.append(f'Historical target-fight sides with two-sided prior UFC history: {len(t)}')
lines.append('All wrestler/opponent stats are calculated ONLY from UFC fights before the target fight.')
lines.append('')
lines.append('ANCHOR RULE')
lines.append('-'*118)
lines.append('Wrestler: >=3 prior UFC fights, >=1.5 TD landed/15, >=4 TD attempts/15, >=2.5 min control/15')
lines.append('Opponent: >=3 prior UFC fights, <=2 TD attempts/15, <=1.5 min control/15')
a=summarize(anchor)
if a:
    lines.append(f"n={a['n']} | wins={a['wins']} | win={a['win_rate']*100:.1f}% | market expected={a['mean_market_prob']*100:.1f}% | lift={a['edge_pp']:+.2f}pp")
    lines.append(f"Favorite subset: n={a['fav_n']} | exact flat-$100 ROI={a['fav_roi']*100:+.2f}%")
    lines.append(f"Pre-2020 edge={a['pre_edge_pp']:+.2f}pp ({a['pre_n']}) | 2020+ edge={a['recent_edge_pp']:+.2f}pp ({a['recent_n']})")
lines.append('')
lines.append('TOP ROBUST THRESHOLD CANDIDATES')
lines.append('='*118)
if robust.empty:
    lines.append('No rule cleared the conservative robustness criteria.')
else:
    for _,r in robust.head(35).iterrows():
        lines.append(
            f"TDland>={r.w_td_l15_min:.1f}/15 | TDatt>={r.w_td_a15_min:.1f}/15 | ctrl>={r.w_ctrl15_min:.1f}m/15 | "
            f"opp TDatt<={r.opp_td_a15_max:.1f}/15 | opp ctrl<={r.opp_ctrl15_max:.2f}m/15 | prior>={int(r.min_prior_fights)} | "
            f"n={int(r.n):3d} win={r.win_rate*100:5.1f}% mkt={r.mean_market_prob*100:5.1f}% lift={r.edge_pp:+5.2f}pp | "
            f"fav n={int(r.fav_n):3d} ROI={r.fav_roi*100:+6.2f}% | pre={r.pre_edge_pp:+5.2f}pp recent={r.recent_edge_pp:+5.2f}pp"
        )
lines.append('')
lines.append('OPPONENT TAKEDOWN DEFENSE — DOES IT CANCEL THE EDGE?')
lines.append('='*118)
for r in tdd_rows:
    lines.append(f"{r['opp_tdd_band']:<12} n={r['n']:3d} win={r['win_rate']*100:5.1f}% mkt={r['mean_market_prob']*100:5.1f}% lift={r['edge_pp']:+5.2f}pp | favROI={r['fav_roi']*100:+6.2f}%")
lines.append('')
lines.append('OPPONENT CONTROL VULNERABILITY')
lines.append('='*118)
for r in ctrl_rows:
    lines.append(f"{r['opp_control_allowed_band']:<12} n={r['n']:3d} win={r['win_rate']*100:5.1f}% mkt={r['mean_market_prob']*100:5.1f}% lift={r['edge_pp']:+5.2f}pp | favROI={r['fav_roi']*100:+6.2f}%")
lines.append('')
lines.append('Interpretation: positive lift means the wrestler won more often than the market probability implied. Favorite ROI uses the exact historical favorite price; underdog ROI is not inferred.')
report='\n'.join(lines)+'\n'
(outdir/'report.txt').write_text(report)
print(report)
print('Saved:',outdir/'report.txt')
print('Saved:',outdir/'robust_wrestling_candidates.csv')
print('Saved:',outdir/'opponent_tdd_modifier.csv')
print('Saved:',outdir/'opponent_control_vulnerability.csv')
PY
