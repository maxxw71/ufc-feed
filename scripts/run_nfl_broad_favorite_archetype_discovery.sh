#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
IN="$ROOT/rolling_roi_discovery/pregame_team_sides_2006_2026.parquet"
OUT="$ROOT/nfl_broad_favorite_discovery"
mkdir -p "$OUT"
[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUT" <<'PY'
import sys, math
from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd

inp,outdir=Path(sys.argv[1]),Path(sys.argv[2]); outdir.mkdir(parents=True,exist_ok=True)

def prog(p,msg): print(f"[{p:3d}%] {msg}",flush=True)
def pct(x): return 'NA' if pd.isna(x) else f'{100*x:+.2f}%'

def metrics(df):
    n=len(df)
    if not n:
        return dict(n=0,wins=0,losses=0,win_rate=np.nan,roi=np.nan,profit=0.0,old_n=0,old_roi=np.nan,new_n=0,new_roi=np.nan,b1=np.nan,b2=np.nan,b3=np.nan,b4=np.nan,pos_blocks=0,min_block=np.nan,avg_line=np.nan)
    roi=df.bet_profit100.sum()/(100*n)
    old=df[df.season<=2015]; new=df[df.season>=2016]
    def r(z): return z.bet_profit100.sum()/(100*len(z)) if len(z) else np.nan
    blocks=[]
    for lo,hi in [(2006,2010),(2011,2015),(2016,2020),(2021,2026)]:
        blocks.append(r(df[(df.season>=lo)&(df.season<=hi)]))
    vals=[x for x in blocks if pd.notna(x)]
    return dict(
        n=n,wins=int(df.win.sum()),losses=int(n-df.win.sum()),win_rate=float(df.win.mean()),roi=float(roi),profit=float(df.bet_profit100.sum()),
        old_n=len(old),old_roi=float(r(old)) if len(old) else np.nan,new_n=len(new),new_roi=float(r(new)) if len(new) else np.nan,
        b1=blocks[0],b2=blocks[1],b3=blocks[2],b4=blocks[3],pos_blocks=sum(x>0 for x in vals),min_block=min(vals) if vals else np.nan,
        avg_line=float(df.moneyline.mean()) if 'moneyline' in df else np.nan)

prog(3,'Loading strict pre-game NFL feature table')
g=pd.read_parquet(inp)
numcols=[c for c in g.columns if c not in {'game_id','team','opponent','roof','surface'}]
for c in numcols:
    try: g[c]=pd.to_numeric(g[c],errors='ignore')
    except Exception: pass

# One favorite side per priced game. Ignore ties and sides without enough prior games.
b=g[g.moneyline.notna() & g.market_prob.notna() & g.win.isin([0.0,1.0]) & (g.prior_games>=3) & (g.market_prob>.50)].copy()
b['week']=pd.to_numeric(b.week,errors='coerce')
b['season']=pd.to_numeric(b.season,errors='coerce')
b['market_prob']=pd.to_numeric(b.market_prob,errors='coerce')
b['moneyline']=pd.to_numeric(b.moneyline,errors='coerce')
if 'home_side' in b: b['home_side']=pd.to_numeric(b.home_side,errors='coerce')
if 'div_game' in b: b['div_game']=pd.to_numeric(b.div_game,errors='coerce')

# Convenient matchup edges from ranks and rolling stats.
for name in ['off_epa','def_epa','pass_epa','def_pass_epa','rush_epa','def_rush_epa','scoring','points_allowed','turnover','sacks_made','point_diff','def_ypp']:
    a=f'rank_{name}'; o=f'opp_rank_{name}'
    if a in b.columns and o in b.columns:
        b[f'edge_{name}']=pd.to_numeric(b[o],errors='coerce')-pd.to_numeric(b[a],errors='coerce')

for a,o,new in [
    ('pre_point_diff','opp_pre_point_diff','season_pd_edge'),
    ('last3_point_diff','opp_last3_point_diff','last3_pd_edge'),
    ('pre_turnover_margin','opp_pre_turnover_margin','to_margin_edge'),
    ('pre_off_epa','opp_pre_off_epa','off_epa_raw_edge'),
    ('pre_def_epa_allowed','opp_pre_def_epa_allowed','def_epa_raw_edge'),
    ('pre_points_for','opp_pre_points_for','scoring_raw_edge'),
    ('pre_points_against','opp_pre_points_against','points_allowed_raw_edge'),
    ('pre_yards_per_play','opp_pre_yards_per_play','ypp_raw_edge'),
    ('pre_def_ypp_allowed','opp_pre_def_ypp_allowed','def_ypp_raw_edge'),
    ('pre_sacks_made','opp_pre_sacks_made','sack_raw_edge'),
    ('pre_sacks_suffered','opp_pre_sacks_suffered','sacks_suffered_edge')]:
    if a in b.columns and o in b.columns:
        b[new]=pd.to_numeric(b[a],errors='coerce')-pd.to_numeric(b[o],errors='coerce')

prog(8,f'Favorite-side pool: {len(b):,} priced team-games')

# Base filters deliberately cover early-to-late season, favorite strength, and location.
week_floors=[4,5,6,7,8,9,10,11,12]
market_floors=[.52,.55,.60,.65,.70,.75,.80]
locations=['ANY','HOME','ROAD']

def base_mask(df,w,m,loc='ANY',div='ANY'):
    z=(df.week>=w)&(df.market_prob>=m)
    if loc=='HOME' and 'home_side' in df: z &= df.home_side.eq(1)
    elif loc=='ROAD' and 'home_side' in df: z &= df.home_side.eq(0)
    if div=='DIV' and 'div_game' in df: z &= df.div_game.eq(1)
    elif div=='NON' and 'div_game' in df: z &= df.div_game.eq(0)
    return z

rows=[]
def add(family,desc,mask):
    x=b[mask.fillna(False)]
    m=metrics(x)
    if m['n']:
        rows.append({'family':family,'rule':desc,**m})

# Keep combinations broad enough to avoid millions of nearly duplicate rules.
def loop_base():
    for w in week_floors:
      for m in market_floors:
        for loc in locations:
          yield w,m,loc,base_mask(b,w,m,loc)

prog(12,'1/12 — Strong offense vs weak/strong defense archetypes')
for w,m,loc,base in loop_base():
    for offN in [5,8,10,12]:
      for oppD in [5,8,10]:
        if 'rank_off_epa' in b and 'opp_rank_def_epa' in b:
          add('ELITE OFF vs ELITE DEF',f'w>={w} mkt>={m:.2f} {loc} offRank<={offN} oppDefRank<={oppD}',base&(b.rank_off_epa<=offN)&(b.opp_rank_def_epa<=oppD))
      for oppD in [18,22,25,28]:
        if 'rank_off_epa' in b and 'opp_rank_def_epa' in b:
          add('ELITE OFF vs WEAK DEF',f'w>={w} mkt>={m:.2f} {loc} offRank<={offN} oppDefRank>={oppD}',base&(b.rank_off_epa<=offN)&(b.opp_rank_def_epa>=oppD))

prog(19,'2/12 — Strong defense vs weak/strong offense archetypes')
for w,m,loc,base in loop_base():
    for defN in [5,8,10,12]:
      for oppO in [5,8,10]:
        if 'rank_def_epa' in b and 'opp_rank_off_epa' in b:
          add('ELITE DEF vs ELITE OFF',f'w>={w} mkt>={m:.2f} {loc} defRank<={defN} oppOffRank<={oppO}',base&(b.rank_def_epa<=defN)&(b.opp_rank_off_epa<=oppO))
      for oppO in [18,22,25,28]:
        if 'rank_def_epa' in b and 'opp_rank_off_epa' in b:
          add('ELITE DEF vs WEAK OFF',f'w>={w} mkt>={m:.2f} {loc} defRank<={defN} oppOffRank>={oppO}',base&(b.rank_def_epa<=defN)&(b.opp_rank_off_epa>=oppO))

prog(26,'3/12 — Balanced favorites and dual superiority')
for w,m,loc,base in loop_base():
    for n in [5,8,10,12,15]:
      if 'rank_off_epa' in b and 'rank_def_epa' in b:
        add('BALANCED ELITE',f'w>={w} mkt>={m:.2f} {loc} offRank<={n} defRank<={n}',base&(b.rank_off_epa<=n)&(b.rank_def_epa<=n))
    for oe in [4,8,12,16]:
      for de in [4,8,12,16]:
        if 'edge_off_epa' in b and 'edge_def_epa' in b:
          add('DUAL EPA EDGE',f'w>={w} mkt>={m:.2f} {loc} offEdge>={oe} defEdge>={de}',base&(b.edge_off_epa>=oe)&(b.edge_def_epa>=de))

prog(33,'4/12 — Passing offense/defense matchup families')
for w,m,loc,base in loop_base():
    if not {'rank_pass_epa','opp_rank_def_pass_epa'}.issubset(b.columns): continue
    for o in [5,8,10,12]:
      for d in [18,22,25,28]:
        add('PASS OFF vs WEAK PASS DEF',f'w>={w} mkt>={m:.2f} {loc} passRank<={o} oppPassDefRank>={d}',base&(b.rank_pass_epa<=o)&(b.opp_rank_def_pass_epa>=d))
      for d in [5,8,10]:
        add('PASS OFF vs ELITE PASS DEF',f'w>={w} mkt>={m:.2f} {loc} passRank<={o} oppPassDefRank<={d}',base&(b.rank_pass_epa<=o)&(b.opp_rank_def_pass_epa<=d))
    if {'edge_pass_epa','edge_def_pass_epa'}.issubset(b.columns):
      for oe in [6,10,14,18]:
        for de in [6,10,14,18]:
          add('PASS DOUBLE EDGE',f'w>={w} mkt>={m:.2f} {loc} passEdge>={oe} passDefEdge>={de}',base&(b.edge_pass_epa>=oe)&(b.edge_def_pass_epa>=de))

prog(40,'5/12 — Rushing offense/defense matchup families')
for w,m,loc,base in loop_base():
    if not {'rank_rush_epa','opp_rank_def_rush_epa'}.issubset(b.columns): continue
    for o in [5,8,10,12]:
      for d in [18,22,25,28]:
        add('RUSH OFF vs WEAK RUSH DEF',f'w>={w} mkt>={m:.2f} {loc} rushRank<={o} oppRushDefRank>={d}',base&(b.rank_rush_epa<=o)&(b.opp_rank_def_rush_epa>=d))
      for d in [5,8,10]:
        add('RUSH OFF vs ELITE RUSH DEF',f'w>={w} mkt>={m:.2f} {loc} rushRank<={o} oppRushDefRank<={d}',base&(b.rank_rush_epa<=o)&(b.opp_rank_def_rush_epa<=d))
    if {'edge_rush_epa','edge_def_rush_epa'}.issubset(b.columns):
      for oe in [6,10,14,18]:
        for de in [6,10,14,18]:
          add('RUSH DOUBLE EDGE',f'w>={w} mkt>={m:.2f} {loc} rushEdge>={oe} rushDefEdge>={de}',base&(b.edge_rush_epa>=oe)&(b.edge_def_rush_epa>=de))

prog(48,'6/12 — Scoring, point-differential and defense combinations')
for w,m,loc,base in loop_base():
    if {'rank_scoring','rank_points_allowed'}.issubset(b.columns):
      for sr in [5,8,10,12]:
        for dr in [5,8,10,12]:
          add('SCORING + DEFENSE',f'w>={w} mkt>={m:.2f} {loc} scoreRank<={sr} ptsAllowedRank<={dr}',base&(b.rank_scoring<=sr)&(b.rank_points_allowed<=dr))
    if {'season_pd_edge','edge_points_allowed'}.issubset(b.columns):
      for pe in [3,5,7,10]:
        for de in [4,8,12]:
          add('POINT DIFF + DEFENSE',f'w>={w} mkt>={m:.2f} {loc} seasonPDEdge>={pe} ptsAllowedEdge>={de}',base&(b.season_pd_edge>=pe)&(b.edge_points_allowed>=de))

prog(56,'7/12 — Turnover, pass-rush and protection combinations')
for w,m,loc,base in loop_base():
    if 'to_margin_edge' in b:
      for te in [.5,1.0,1.5,2.0]:
        if 'edge_def_epa' in b:
          for de in [4,8,12]: add('TURNOVER + DEFENSE',f'w>={w} mkt>={m:.2f} {loc} TOedge>={te} defEdge>={de}',base&(b.to_margin_edge>=te)&(b.edge_def_epa>=de))
        if 'edge_off_epa' in b:
          for oe in [4,8,12]: add('TURNOVER + OFFENSE',f'w>={w} mkt>={m:.2f} {loc} TOedge>={te} offEdge>={oe}',base&(b.to_margin_edge>=te)&(b.edge_off_epa>=oe))
    if 'rank_sacks_made' in b and 'opp_pre_sacks_suffered' in b:
      for sr in [5,8,10,12]:
        for osa in [2.0,2.5,3.0]:
          add('PASS RUSH vs WEAK PROTECTION',f'w>={w} mkt>={m:.2f} {loc} sackRank<={sr} oppSacksSuffered>={osa:.1f}/g',base&(b.rank_sacks_made<=sr)&(b.opp_pre_sacks_suffered>=osa))

prog(64,'8/12 — Yards/play efficiency matchup families')
for w,m,loc,base in loop_base():
    if {'pre_yards_per_play','opp_pre_def_ypp_allowed'}.issubset(b.columns):
      for oy in [5.5,5.8,6.0,6.2]:
        for dy in [5.5,5.8,6.0,6.2]:
          add('YPP OFFENSE vs LEAKY DEFENSE',f'w>={w} mkt>={m:.2f} {loc} offYPP>={oy:.1f} oppDefYPP>={dy:.1f}',base&(b.pre_yards_per_play>=oy)&(b.opp_pre_def_ypp_allowed>=dy))
    if {'edge_off_epa','edge_def_ypp'}.issubset(b.columns):
      for oe in [4,8,12]:
        for ye in [4,8,12]:
          add('EPA + YPP DEFENSE',f'w>={w} mkt>={m:.2f} {loc} offEdge>={oe} defYPPEdge>={ye}',base&(b.edge_off_epa>=oe)&(b.edge_def_ypp>=ye))

prog(71,'9/12 — Recent form layered onto established team strength')
for w,m,loc,base in loop_base():
    if 'last3_pd_edge' not in b: continue
    for re in [3,5,7,10,14]:
      if 'edge_off_epa' in b:
        for oe in [4,8,12]: add('RECENT FORM + OFFENSE',f'w>={w} mkt>={m:.2f} {loc} last3PDEdge>={re} offEdge>={oe}',base&(b.last3_pd_edge>=re)&(b.edge_off_epa>=oe))
      if 'edge_def_epa' in b:
        for de in [4,8,12]: add('RECENT FORM + DEFENSE',f'w>={w} mkt>={m:.2f} {loc} last3PDEdge>={re} defEdge>={de}',base&(b.last3_pd_edge>=re)&(b.edge_def_epa>=de))

prog(78,'10/12 — Multi-category superiority counts')
edge_cols=[c for c in ['edge_off_epa','edge_def_epa','edge_pass_epa','edge_def_pass_epa','edge_rush_epa','edge_def_rush_epa','edge_turnover','edge_scoring','edge_points_allowed','edge_sacks_made'] if c in b.columns]
for thresh in [4,8,12]:
    if not edge_cols: break
    cnt=sum((pd.to_numeric(b[c],errors='coerce')>=thresh).astype(int) for c in edge_cols)
    for w,m,loc,base in loop_base():
      for k in [3,4,5,6]:
        add('MULTI-EDGE FAVORITE',f'w>={w} mkt>={m:.2f} {loc} >= {k} rank edges of {len(edge_cols)} at threshold {thresh}',base&(cnt>=k))

prog(84,'11/12 — Opponent quality and strength-vs-strength favorites')
if {'opp_rank_off_epa','opp_rank_def_epa','rank_off_epa','rank_def_epa'}.issubset(b.columns):
    b['opp_quality_avg']=(b.opp_rank_off_epa+b.opp_rank_def_epa)/2
    b['fav_quality_avg']=(b.rank_off_epa+b.rank_def_epa)/2
    for w,m,loc,base in loop_base():
      for fq in [6,8,10,12]:
        for oq in [6,8,10,12]:
          add('ELITE FAVORITE vs ELITE OPP',f'w>={w} mkt>={m:.2f} {loc} favAvgRank<={fq} oppAvgRank<={oq}',base&(b.fav_quality_avg<=fq)&(b.opp_quality_avg<=oq))
        for oq in [18,20,22,24]:
          add('ELITE FAVORITE vs WEAK OPP',f'w>={w} mkt>={m:.2f} {loc} favAvgRank<={fq} oppAvgRank>={oq}',base&(b.fav_quality_avg<=fq)&(b.opp_quality_avg>=oq))

prog(89,'12/12 — Divisional/non-divisional and moneyline-band slices')
# These are context slices on top of balanced/dual-edge strength, not standalone line mining.
for w in week_floors:
  for m in [.55,.60,.65,.70,.75]:
    for div in ['DIV','NON']:
      base=base_mask(b,w,m,'ANY',div)
      if {'edge_off_epa','edge_def_epa'}.issubset(b.columns):
        for e in [4,8,12]: add('DUAL EDGE BY DIVISION',f'w>={w} mkt>={m:.2f} {div} offEdge>={e} defEdge>={e}',base&(b.edge_off_epa>=e)&(b.edge_def_epa>=e))

# Save everything, then enforce minimum sample + ROI + time stability.
allr=pd.DataFrame(rows)
allr.to_csv(outdir/'all_favorite_archetype_rules.csv',index=False)
prog(93,f'Generated {len(allr):,} historical favorite rules')

eligible=allr[allr.n>=75].copy()
positive=eligible[(eligible.roi>=.10)&(eligible.old_roi>0)&(eligible.new_roi>0)].copy()
# Robust = 3/4 era blocks profitable and worst block no worse than -3%.
robust=positive[(positive.pos_blocks>=3)&(positive.min_block>=-.03)].copy()
robust=robust.sort_values(['roi','n'],ascending=[False,False])
positive=positive.sort_values(['roi','n'],ascending=[False,False])
positive.to_csv(outdir/'roi10_both_eras_n75.csv',index=False)
robust.to_csv(outdir/'robust_roi10_n75.csv',index=False)

print('\nTOP >=10% ROI FAVORITE CATEGORIES — n>=75, BOTH MAJOR ERAS POSITIVE')
print('='*135)
for _,r in positive.head(60).iterrows():
    print(f"{r.family:32s} n={int(r.n):4d} {int(r.wins)}-{int(r.losses)} win={100*r.win_rate:5.1f}% ROI={100*r.roi:+6.2f}% | old={100*r.old_roi:+6.2f}% new={100*r.new_roi:+6.2f}% | blocks+={int(r.pos_blocks)}/4 min={100*r.min_block:+6.2f}% | {r.rule}")

print('\nROBUST SHORTLIST — >=10% ROI, n>=75, both eras positive, >=3/4 blocks positive, worst block >= -3%')
print('='*135)
for _,r in robust.head(40).iterrows():
    print(f"{r.family:32s} n={int(r.n):4d} win={100*r.win_rate:5.1f}% ROI={100*r.roi:+6.2f}% | old={100*r.old_roi:+6.2f}% new={100*r.new_roi:+6.2f}% | blocks+={int(r.pos_blocks)}/4 min={100*r.min_block:+6.2f}% | {r.rule}")

# Best broad candidate by family: maximize sample among robust rules, then ROI.
print('\nWIDEST ROBUST >=10% RULE BY FAMILY')
print('='*135)
wide=[]
for fam,z in robust.groupby('family'):
    q=z.sort_values(['n','roi'],ascending=[False,False]).iloc[0]
    wide.append(q)
    print(f"{fam:32s} n={int(q.n):4d} ROI={100*q.roi:+6.2f}% old={100*q.old_roi:+6.2f}% new={100*q.new_roi:+6.2f}% minBlock={100*q.min_block:+6.2f}% | {q.rule}")
if wide: pd.DataFrame(wide).to_csv(outdir/'widest_robust_by_family.csv',index=False)

prog(100,'Broad NFL favorite archetype discovery complete')
print('\nSaved:')
for fn in ['all_favorite_archetype_rules.csv','roi10_both_eras_n75.csv','robust_roi10_n75.csv','widest_robust_by_family.csv']:
    p=outdir/fn
    if p.exists(): print(p)
print('\nThis is still discovery. Any shortlisted family should receive neighboring-threshold and walk-forward validation before live use.')
PY
