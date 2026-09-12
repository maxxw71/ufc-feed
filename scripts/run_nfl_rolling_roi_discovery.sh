#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
RAW="$ROOT/data/raw"
OUT="$ROOT/rolling_roi_discovery"
SCHED="$RAW/schedules_2006_2026.parquet"
TEAM="$RAW/team_weekly_2006_2026.parquet"
mkdir -p "$OUT"

[ -s "$SCHED" ] || { echo "ERROR: Missing $SCHED"; exit 2; }
[ -s "$TEAM" ] || { echo "ERROR: Missing $TEAM"; exit 3; }
source "$ROOT/venv/bin/activate"

python -u - "$SCHED" "$TEAM" "$OUT" <<'PY'
import sys, math
from pathlib import Path
import numpy as np
import pandas as pd

sched_path, team_path, outdir = map(Path, sys.argv[1:4])
outdir.mkdir(parents=True, exist_ok=True)

def prog(p,msg): print(f"[{p:3d}%] {msg}", flush=True)

def amer_profit(odds, stake=100.0):
    if pd.isna(odds) or odds == 0: return np.nan
    odds=float(odds)
    return stake*(odds/100.0) if odds>0 else stake*(100.0/abs(odds))

def implied(odds):
    if pd.isna(odds) or odds==0: return np.nan
    odds=float(odds)
    return 100/(odds+100) if odds>0 else abs(odds)/(abs(odds)+100)

def col(df,*names):
    for n in names:
        if n in df.columns: return n
    return None

def ssum(df, names):
    xs=[pd.to_numeric(df[n],errors='coerce').fillna(0) for n in names if n in df.columns]
    if not xs: return pd.Series(np.nan,index=df.index)
    z=xs[0].copy()
    for x in xs[1:]: z=z+x
    return z

prog(2,"Loading 2006-2026 schedules and weekly team stats")
sched=pd.read_parquet(sched_path)
team=pd.read_parquet(team_path)
prog(8,f"Loaded {len(sched):,} games and {len(team):,} team-week rows")

# Regular-season completed games only for this first discovery pass.
s=sched.copy()
s=s[(s['game_type'].astype(str)=='REG') & s['home_score'].notna() & s['away_score'].notna()].copy()
s['season']=pd.to_numeric(s['season'],errors='coerce').astype('Int64')
s['week']=pd.to_numeric(s['week'],errors='coerce').astype('Int64')

# Keep only required schedule fields if present.
keep=['game_id','season','week','gameday','home_team','away_team','home_score','away_score','home_moneyline','away_moneyline','home_rest','away_rest','spread_line','home_spread_odds','away_spread_odds','total_line','over_odds','under_odds','roof','surface','temp','wind','div_game']
keep=[c for c in keep if c in s.columns]
s=s[keep].copy()

prog(12,"Building one row per team-game and deriving offense/defense results")
t=team.copy()
if 'season_type' in t.columns:
    t=t[t['season_type'].astype(str)=='REG'].copy()
for c in ['season','week']:
    t[c]=pd.to_numeric(t[c],errors='coerce').astype('Int64')

# Core weekly offense metrics. The team stats dataset is offense-oriented;
# defensive performance is derived from what the opponent produced in the same game.
t['pass_yards']=pd.to_numeric(t[col(t,'passing_yards')],errors='coerce') if col(t,'passing_yards') else np.nan
t['rush_yards']=pd.to_numeric(t[col(t,'rushing_yards')],errors='coerce') if col(t,'rushing_yards') else np.nan
t['total_yards']=t['pass_yards'].fillna(0)+t['rush_yards'].fillna(0)
t['pass_epa']=pd.to_numeric(t[col(t,'passing_epa')],errors='coerce') if col(t,'passing_epa') else np.nan
t['rush_epa']=pd.to_numeric(t[col(t,'rushing_epa')],errors='coerce') if col(t,'rushing_epa') else np.nan
t['off_epa']=t['pass_epa'].fillna(0)+t['rush_epa'].fillna(0)
t['pass_att']=pd.to_numeric(t[col(t,'attempts')],errors='coerce') if col(t,'attempts') else np.nan
t['rush_att']=pd.to_numeric(t[col(t,'carries')],errors='coerce') if col(t,'carries') else np.nan
t['sacks_suffered']=pd.to_numeric(t[col(t,'sacks_suffered')],errors='coerce') if col(t,'sacks_suffered') else 0
plays=t['pass_att'].fillna(0)+t['rush_att'].fillna(0)+t['sacks_suffered'].fillna(0)
t['yards_per_play']=np.where(plays>0,t['total_yards']/plays,np.nan)
t['interceptions_thrown']=pd.to_numeric(t[col(t,'passing_interceptions')],errors='coerce').fillna(0) if col(t,'passing_interceptions') else 0
fumble_loss_cols=[c for c in ['sack_fumbles_lost','rushing_fumbles_lost','receiving_fumbles_lost'] if c in t.columns]
t['fumbles_lost']=ssum(t,fumble_loss_cols).fillna(0)
t['giveaways']=t['interceptions_thrown']+t['fumbles_lost']
# Explosive play counts when available.
pass_ex=col(t,'passing_20','passing_16')
rush_ex=col(t,'rushing_10','rushing_20')
t['explosive_passes']=pd.to_numeric(t[pass_ex],errors='coerce') if pass_ex else np.nan
t['explosive_runs']=pd.to_numeric(t[rush_ex],errors='coerce') if rush_ex else np.nan

# Join score/location info.
home=s[['game_id','home_team','away_team','home_score','away_score']].copy()
home=home.rename(columns={'home_team':'team','away_team':'opp_sched','home_score':'points_for','away_score':'points_against'})
home['is_home']=1
away=s[['game_id','home_team','away_team','home_score','away_score']].copy()
away=away.rename(columns={'away_team':'team','home_team':'opp_sched','away_score':'points_for','home_score':'points_against'})
away['is_home']=0
score_rows=pd.concat([home,away],ignore_index=True)
t=t.merge(score_rows[['game_id','team','points_for','points_against','is_home']],on=['game_id','team'],how='inner')
t['point_diff']=t['points_for']-t['points_against']
t['win']=(t['point_diff']>0).astype(float)

# Self-merge opponent output into defensive metrics.
base_cols=['game_id','team','opponent_team','season','week','points_for','points_against','is_home','win','point_diff','pass_yards','rush_yards','total_yards','pass_epa','rush_epa','off_epa','yards_per_play','giveaways','sacks_suffered','explosive_passes','explosive_runs']
base_cols=[c for c in base_cols if c in t.columns]
a=t[base_cols].copy()
b=a.copy().rename(columns={
    'team':'opponent_team_join','pass_yards':'def_pass_yards_allowed','rush_yards':'def_rush_yards_allowed','total_yards':'def_total_yards_allowed',
    'pass_epa':'def_pass_epa_allowed','rush_epa':'def_rush_epa_allowed','off_epa':'def_epa_allowed','yards_per_play':'def_ypp_allowed',
    'giveaways':'takeaways','sacks_suffered':'sacks_made','explosive_passes':'def_explosive_passes_allowed','explosive_runs':'def_explosive_runs_allowed'
})
# opponent_team is already present on a; join opponent's team row by game id.
g=a.merge(b[['game_id','opponent_team_join','def_pass_yards_allowed','def_rush_yards_allowed','def_total_yards_allowed','def_pass_epa_allowed','def_rush_epa_allowed','def_epa_allowed','def_ypp_allowed','takeaways','sacks_made','def_explosive_passes_allowed','def_explosive_runs_allowed']],left_on=['game_id','opponent_team'],right_on=['game_id','opponent_team_join'],how='left')
g['turnover_margin']=g['takeaways']-g['giveaways']

prog(22,"Calculating strict pre-game rolling season and recent-form metrics")
metrics=['points_for','points_against','point_diff','win','pass_yards','rush_yards','total_yards','pass_epa','rush_epa','off_epa','yards_per_play','giveaways','takeaways','turnover_margin','sacks_suffered','sacks_made','def_pass_yards_allowed','def_rush_yards_allowed','def_total_yards_allowed','def_pass_epa_allowed','def_rush_epa_allowed','def_epa_allowed','def_ypp_allowed','explosive_passes','explosive_runs','def_explosive_passes_allowed','def_explosive_runs_allowed']
metrics=[c for c in metrics if c in g.columns]
g=g.sort_values(['season','team','week','game_id']).reset_index(drop=True)

# All rolling stats are shifted one game: target game never contributes to its own features.
for i,m in enumerate(metrics):
    grp=g.groupby(['season','team'],sort=False)[m]
    g[f'pre_{m}']=grp.transform(lambda x:x.expanding().mean().shift(1))
    g[f'last3_{m}']=grp.transform(lambda x:x.rolling(3,min_periods=2).mean().shift(1))
    if i%7==0: prog(24+int(16*i/max(1,len(metrics))),f"Rolling metrics {i+1}/{len(metrics)}")
g['prior_games']=g.groupby(['season','team']).cumcount()

# Attach schedule market/context to each side.
market_home=s.copy(); market_home['team']=market_home['home_team']; market_home['opponent']=market_home['away_team']; market_home['moneyline']=market_home.get('home_moneyline'); market_home['opp_moneyline']=market_home.get('away_moneyline'); market_home['rest']=market_home.get('home_rest'); market_home['opp_rest']=market_home.get('away_rest'); market_home['home_side']=1
market_away=s.copy(); market_away['team']=market_away['away_team']; market_away['opponent']=market_away['home_team']; market_away['moneyline']=market_away.get('away_moneyline'); market_away['opp_moneyline']=market_away.get('home_moneyline'); market_away['rest']=market_away.get('away_rest'); market_away['opp_rest']=market_away.get('home_rest'); market_away['home_side']=0
ms=pd.concat([market_home,market_away],ignore_index=True)
context=['game_id','team','opponent','moneyline','opp_moneyline','rest','opp_rest','home_side']
for c in ['roof','surface','temp','wind','div_game','spread_line','home_spread_odds','away_spread_odds','total_line']:
    if c in ms.columns: context.append(c)
g=g.merge(ms[context],on=['game_id','team'],how='left')
g['market_prob']=g['moneyline'].map(implied)
g['rest_edge']=pd.to_numeric(g['rest'],errors='coerce')-pd.to_numeric(g['opp_rest'],errors='coerce')
g['bet_profit100']=np.where(g['win']==1,g['moneyline'].map(amer_profit),-100.0)

# Merge opponent pregame features onto each row so every rule is matchup-relative.
precols=['prior_games']+[c for c in g.columns if c.startswith('pre_') or c.startswith('last3_')]
opp=g[['game_id','team']+precols].copy()
opp=opp.rename(columns={'team':'opponent', **{c:'opp_'+c for c in precols}})
g=g.merge(opp,on=['game_id','opponent'],how='left')

# Rank teams within each season/week using only pregame information.
prog(44,"Creating pre-game offense, defense and turnover rankings")
rank_specs={
    'off_epa':'pre_off_epa','pass_epa':'pre_pass_epa','rush_epa':'pre_rush_epa','scoring':'pre_points_for','point_diff':'pre_point_diff','turnover':'pre_turnover_margin','sacks_made':'pre_sacks_made',
    'def_epa':'pre_def_epa_allowed','def_pass_epa':'pre_def_pass_epa_allowed','def_rush_epa':'pre_def_rush_epa_allowed','points_allowed':'pre_points_against','def_ypp':'pre_def_ypp_allowed'
}
for name,c in list(rank_specs.items()):
    if c not in g.columns: rank_specs.pop(name,None); continue
    asc=name.startswith('def_') or name=='points_allowed'
    g[f'rank_{name}']=g.groupby(['season','week'])[c].rank(method='average',ascending=asc)
    # opponent rank already exists on its row; merge cheaply via game/team after ranks later
rankcols=[c for c in g.columns if c.startswith('rank_')]
opp_rank=g[['game_id','team']+rankcols].copy().rename(columns={'team':'opponent',**{c:'opp_'+c for c in rankcols}})
g=g.merge(opp_rank,on=['game_id','opponent'],how='left')
for c in rankcols:
    g[f'edge_{c[5:]}']=g['opp_'+c]-g[c]  # positive = our team has the better rank

# Save reusable feature table.
feature_path=outdir/'pregame_team_sides_2006_2026.parquet'
g.to_parquet(feature_path,index=False)
prog(52,f"Saved {len(g):,} strict pre-game team-side rows")

# Completed, priced, non-tie regular-season sides.
bets=g[g['moneyline'].notna() & g['market_prob'].notna() & g['win'].isin([0.0,1.0]) & (g['prior_games']>=3)].copy()

# Helper metrics and robust-era checks.
def calc(mask):
    x=bets[mask.fillna(False)].copy(); n=len(x)
    if n==0:return None
    roi=x.bet_profit100.sum()/(100*n)
    old=x[x.season<=2015]; new=x[x.season>=2016]
    roi_old=old.bet_profit100.sum()/(100*len(old)) if len(old) else np.nan
    roi_new=new.bet_profit100.sum()/(100*len(new)) if len(new) else np.nan
    return dict(n=n,wins=int(x.win.sum()),win_rate=float(x.win.mean()),roi=float(roi),old_n=len(old),old_roi=float(roi_old) if pd.notna(roi_old) else np.nan,new_n=len(new),new_roi=float(roi_new) if pd.notna(roi_new) else np.nan,avg_odds=float(x.moneyline.mean()),avg_market=float(x.market_prob.mean()))

rules=[]
def add(family,desc,mask):
    z=calc(mask)
    if z: rules.append({'family':family,'rule':desc,**z})

prog(58,"Scanning late-season moneyline categories")
week_floors=[5,6,7,8,9,10,11,12]
markets=[('ANY',pd.Series(True,index=bets.index)),('FAV55+',bets.market_prob>=.55),('FAV60+',bets.market_prob>=.60),('FAV65+',bets.market_prob>=.65),('DOG45-',bets.market_prob<=.45)]

for wi,wf in enumerate(week_floors):
    late=bets.week>=wf
    for mname,mm in markets:
        base=late & mm
        # Balanced offense + defense rank superiority.
        if 'edge_off_epa' in bets and 'edge_def_epa' in bets:
            for oe in [4,8,12]:
                for de in [4,8,12]:
                    add('BALANCED EPA',f'week>={wf}, {mname}, offEPA rank edge>={oe}, defEPA rank edge>={de}',base&(bets.edge_off_epa>=oe)&(bets.edge_def_epa>=de))
        # Passing mismatch.
        if 'edge_pass_epa' in bets and 'edge_def_pass_epa' in bets:
            for oe in [6,10,14]:
                for de in [6,10,14]:
                    add('PASS MATCHUP',f'week>={wf}, {mname}, passEPA rank edge>={oe}, passDef rank edge>={de}',base&(bets.edge_pass_epa>=oe)&(bets.edge_def_pass_epa>=de))
        # Rushing mismatch.
        if 'edge_rush_epa' in bets and 'edge_def_rush_epa' in bets:
            for oe in [6,10,14]:
                for de in [6,10,14]:
                    add('RUSH MATCHUP',f'week>={wf}, {mname}, rushEPA rank edge>={oe}, rushDef rank edge>={de}',base&(bets.edge_rush_epa>=oe)&(bets.edge_def_rush_epa>=de))
        # Turnover edge plus overall efficiency.
        if 'pre_turnover_margin' in bets and 'opp_pre_turnover_margin' in bets:
            tod=(bets.pre_turnover_margin-bets.opp_pre_turnover_margin)
            for td in [.5,1.0,1.5]:
                if 'edge_off_epa' in bets:
                    for oe in [4,8]:
                        add('TURNOVER + OFFENSE',f'week>={wf}, {mname}, TO margin edge>={td:.1f}/g, offEPA rank edge>={oe}',base&(tod>=td)&(bets.edge_off_epa>=oe))
                else:
                    add('TURNOVER EDGE',f'week>={wf}, {mname}, TO margin edge>={td:.1f}/g',base&(tod>=td))
        # Scoring margin + defense.
        if 'pre_point_diff' in bets and 'opp_pre_point_diff' in bets and 'edge_points_allowed' in bets:
            pdiff=bets.pre_point_diff-bets.opp_pre_point_diff
            for pe in [5,8,12]:
                for de in [4,8]:
                    add('SCORING + DEFENSE',f'week>={wf}, {mname}, pointDiff edge>={pe:.0f}/g, pointsAllowed rank edge>={de}',base&(pdiff>=pe)&(bets.edge_points_allowed>=de))
        # Recent 3-game form + season quality.
        if 'last3_point_diff' in bets and 'opp_last3_point_diff' in bets:
            recent=bets.last3_point_diff-bets.opp_last3_point_diff
            for re in [5,8,12]:
                if 'edge_off_epa' in bets:
                    for oe in [0,4,8]:
                        add('RECENT FORM + EPA',f'week>={wf}, {mname}, last3 pointDiff edge>={re:.0f}, offEPA rank edge>={oe}',base&(recent>=re)&(bets.edge_off_epa>=oe))
        # Pass rush / protection mismatch.
        if all(c in bets for c in ['pre_sacks_made','opp_pre_sacks_made','pre_sacks_suffered','opp_pre_sacks_suffered']):
            rush_edge=(bets.pre_sacks_made-bets.opp_pre_sacks_made)+(bets.opp_pre_sacks_suffered-bets.pre_sacks_suffered)
            for se in [.5,1.0,1.5]:
                add('SACK / PROTECTION',f'week>={wf}, {mname}, sack+protection composite edge>={se:.1f}',base&(rush_edge>=se))
        # Rest edge paired with team quality.
        if 'edge_off_epa' in bets:
            for rd in [2,4,7]:
                for oe in [4,8]:
                    add('REST + OFFENSE',f'week>={wf}, {mname}, rest edge>={rd}d, offEPA rank edge>={oe}',base&(bets.rest_edge>=rd)&(bets.edge_off_epa>=oe))
    prog(60+int(22*(wi+1)/len(week_floors)),f"Completed week-floor {wf}+ scan")

r=pd.DataFrame(rules)
if r.empty:
    print('No rules generated; inspect feature table.'); raise SystemExit(0)
r=r.sort_values(['roi','n'],ascending=[False,False])
r.to_csv(outdir/'all_moneyline_rules.csv',index=False)

# Discovery threshold: user target 10%+, but require sample and both historical eras positive.
rob=r[(r.n>=50)&(r.roi>=.10)&(r.old_n>=15)&(r.new_n>=15)&(r.old_roi>0)&(r.new_roi>0)].copy()
rob=rob.sort_values(['roi','n'],ascending=[False,False])
rob.to_csv(outdir/'roi_10plus_both_eras.csv',index=False)

prog(86,"Ranking stable 10%+ ROI candidates")
print('\nNFL MONEYLINE DISCOVERY — 10%+ ROI, BOTH ERAS POSITIVE, N>=50')
print('='*118)
if rob.empty:
    print('No candidate cleared every robustness filter in this first pass.')
else:
    for _,x in rob.head(40).iterrows():
        print(f"{x.family:22s} | n={int(x.n):4d} {int(x.wins):3d}-{int(x.n-x.wins):3d} | win={x.win_rate*100:5.1f}% | ROI={x.roi*100:+6.2f}% | 2006-15={x.old_roi*100:+6.2f}% ({int(x.old_n)}) | 2016+={x.new_roi*100:+6.2f}% ({int(x.new_n)}) | avg line={x.avg_odds:+.0f}")
        print('  '+x.rule)

print('\nBEST ROBUST CANDIDATE BY FAMILY')
print('='*118)
if not rob.empty:
    for fam,gg in rob.groupby('family',sort=False):
        x=gg.iloc[0]
        print(f"{fam:22s} n={int(x.n):4d} ROI={x.roi*100:+6.2f}% old={x.old_roi*100:+6.2f}% new={x.new_roi*100:+6.2f}% | {x.rule}")

# Week-floor summary: how much signal availability exists later in season.
summary=[]
for wf in week_floors:
    x=bets[bets.week>=wf]
    summary.append({'week_floor':wf,'priced_team_sides':len(x),'unique_games':x.game_id.nunique()})
pd.DataFrame(summary).to_csv(outdir/'week_floor_sample_sizes.csv',index=False)

prog(94,"Saving feature and discovery outputs")
print(f"\nFeature table: {feature_path}")
print(f"All rules: {outdir/'all_moneyline_rules.csv'}")
print(f"10%+ stable rules: {outdir/'roi_10plus_both_eras.csv'}")
print(f"Week-floor samples: {outdir/'week_floor_sample_sizes.csv'}")
print('\nImportant: this is discovery, not a live betting model. Any winners still need neighboring-threshold stability and walk-forward season validation before promotion.')
prog(100,"NFL rolling ROI discovery complete")
PY
