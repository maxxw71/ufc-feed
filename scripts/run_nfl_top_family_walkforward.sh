#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/nfl-predictor-v1"
IN="$ROOT/rolling_roi_discovery/pregame_team_sides_2006_2026.parquet"
OUT="$ROOT/nfl_top_family_walkforward"
mkdir -p "$OUT"
[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$OUT" <<'PY'
import sys, math, json
from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd

inp,outdir=Path(sys.argv[1]),Path(sys.argv[2]); outdir.mkdir(parents=True,exist_ok=True)
def prog(p,msg): print(f"[{p:3d}%] {msg}",flush=True)
def pct(x): return 'NA' if pd.isna(x) else f'{100*x:+.2f}%'

def amer_profit(odds, stake=100.0):
    if pd.isna(odds) or odds==0:return np.nan
    o=float(odds)
    return stake*(o/100.0) if o>0 else stake*(100.0/abs(o))

def met(x):
    n=len(x)
    if not n:return dict(n=0,wins=0,losses=0,win=np.nan,roi=np.nan,profit=0.0)
    p=float(x.bet_profit100.sum()); w=int(x.win.sum())
    return dict(n=n,wins=w,losses=n-w,win=float(x.win.mean()),roi=p/(100*n),profit=p)

prog(3,'Loading strict pregame feature table')
g=pd.read_parquet(inp)
for c in g.columns:
    if c not in {'game_id','team','opponent','roof','surface'}:
        try:g[c]=pd.to_numeric(g[c],errors='ignore')
        except:pass

b=g[g.moneyline.notna() & g.market_prob.notna() & g.win.isin([0.0,1.0]) & (g.prior_games>=3) & (g.market_prob>.50)].copy()
for c in ['season','week','market_prob','moneyline','home_side','div_game']:
    if c in b:b[c]=pd.to_numeric(b[c],errors='coerce')

# Re-create matchup rank edges used by discovery.
for name in ['off_epa','def_epa','pass_epa','def_pass_epa','rush_epa','def_rush_epa','scoring','points_allowed','turnover','sacks_made','def_ypp']:
    a=f'rank_{name}'; o=f'opp_rank_{name}'
    if a in b and o in b:
        b[f'edge_{name}']=pd.to_numeric(b[o],errors='coerce')-pd.to_numeric(b[a],errors='coerce')

prog(8,f'Favorite-side pool: {len(b):,}')

def base(df,w,m,loc):
    z=(df.week>=w)&(df.market_prob>=m)
    if loc=='HOME':z &= df.home_side.eq(1)
    elif loc=='ROAD':z &= df.home_side.eq(0)
    return z

# Each family has a deliberately broad neighborhood around the discovery winners.
def ypp(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.pre_yards_per_play>=p['oy'])&(df.opp_pre_def_ypp_allowed>=p['dy'])
def passrush(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_sacks_made<=p['sr'])&(df.opp_pre_sacks_suffered>=p['osa'])
def passelite(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_pass_epa<=p['pr'])&(df.opp_rank_def_pass_epa<=p['dr'])
def epaypp(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.edge_off_epa>=p['oe'])&(df.edge_def_ypp>=p['de'])
def scoringdef(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_scoring<=p['sr'])&(df.rank_points_allowed<=p['dr'])
def offvdef(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['orank'])&(df.opp_rank_def_epa<=p['odrank'])
def balanced(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_off_epa<=p['n'])&(df.rank_def_epa<=p['n'])
def defweak(df,p):
    return base(df,p['w'],p['m'],p['loc'])&(df.rank_def_epa<=p['dr'])&(df.opp_rank_off_epa>=p['oor'])

families={
 'YPP OFF vs LEAKY DEF':(ypp,[dict(w=w,m=m,loc=l,oy=oy,dy=dy) for w,m,l,oy,dy in product([4,5,6,7,8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5.5,5.8,6.0,6.2],[5.5,5.8,6.0,6.2])]),
 'PASS RUSH vs WEAK PROTECTION':(passrush,[dict(w=w,m=m,loc=l,sr=sr,osa=osa) for w,m,l,sr,osa in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[8,10,12],[2.5,3.0,3.5])]),
 'PASS OFF vs ELITE PASS DEF':(passelite,[dict(w=w,m=m,loc=l,pr=pr,dr=dr) for w,m,l,pr,dr in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10],[5,8,10])]),
 'EPA + YPP DEFENSE':(epaypp,[dict(w=w,m=m,loc=l,oe=oe,de=de) for w,m,l,oe,de in product([9,10,11,12],[.55,.60,.65,.70],['ANY','HOME','ROAD'],[8,10,12,14],[6,8,10,12])]),
 'SCORING + DEFENSE':(scoringdef,[dict(w=w,m=m,loc=l,sr=sr,dr=dr) for w,m,l,sr,dr in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10],[5,8,10])]),
 'ELITE OFF vs ELITE DEF':(offvdef,[dict(w=w,m=m,loc=l,orank=o,odrank=d) for w,m,l,o,d in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10,12],[5,8,10,12])]),
 'BALANCED ELITE':(balanced,[dict(w=w,m=m,loc=l,n=n) for w,m,l,n in product([8,9,10,11,12],[.52,.55,.60,.65],['ANY','HOME','ROAD'],[5,8,10,12])]),
 'ELITE DEF vs WEAK OFF':(defweak,[dict(w=w,m=m,loc=l,dr=d,oor=o) for w,m,l,d,o in product([5,6,7,8,9,10,11,12],[.55,.60,.65,.70],['ANY','HOME','ROAD'],[5,8,10,12],[22,25,28,30])]),
}

# Discovery-exact rules from the completed broad scan, shown only descriptively.
exact={
 'YPP ROAD 6.2/6.0':(ypp,dict(w=4,m=.55,loc='ROAD',oy=6.2,dy=6.0)),
 'PASS RUSH HOME':(passrush,dict(w=11,m=.55,loc='HOME',sr=12,osa=3.0)),
 'PASS OFF vs ELITE PASS DEF':(passelite,dict(w=11,m=.55,loc='ANY',pr=5,dr=5)),
 'EPA + YPP DEF':(epaypp,dict(w=12,m=.60,loc='HOME',oe=12,de=8)),
 'SCORING + DEF':(scoringdef,dict(w=11,m=.52,loc='ROAD',sr=5,dr=8)),
 'ELITE OFF vs ELITE DEF':(offvdef,dict(w=11,m=.52,loc='HOME',orank=12,odrank=5)),
}
print('\nDISCOVERY-EXACT RULES — DESCRIPTIVE ONLY, NOT OOS')
print('='*120)
for name,(fn,p) in exact.items():
    z=b[fn(b,p).fillna(False)]; m=met(z)
    print(f"{name:32s} n={m['n']:3d} {m['wins']}-{m['losses']} win={100*m['win']:5.1f}% ROI={pct(m['roi'])}")

# Clean holdout: select within 2006-2015 only, then freeze and grade 2016-2026.
prog(18,'Running clean 2006-2015 selection -> 2016-2026 frozen holdout')
hold_rows=[]
for fi,(name,(fn,grid)) in enumerate(families.items(),1):
    tr=b[b.season<=2015]; te=b[b.season>=2016]
    cand=[]
    for p in grid:
        x=tr[fn(tr,p).fillna(False)]; m=met(x)
        if m['n']<35 or pd.isna(m['roi']):continue
        # Require actual train quality, but not 10% so we don't force a lucky optimum.
        if m['win']<.68 or m['roi']<.04:continue
        a=x[x.season<=2010]; c=x[(x.season>=2011)&(x.season<=2015)]
        ma,mc=met(a),met(c)
        if ma['n']>=10 and mc['n']>=10 and (ma['roi']<=0 or mc['roi']<=0):continue
        score=min(m['roi'],.22)*math.sqrt(m['n']) + .35*max(0,m['win']-.68)*math.sqrt(m['n'])
        cand.append((score,p,m))
    if not cand:
        hold_rows.append({'family':name,'selected_params':'NONE','train_n':0,'train_win':np.nan,'train_roi':np.nan,'test_n':0,'test_wins':0,'test_win':np.nan,'test_roi':np.nan,'test_profit':0.0});continue
    cand.sort(key=lambda q:q[0],reverse=True); _,p,tm=cand[0]
    z=te[fn(te,p).fillna(False)]; hm=met(z)
    hold_rows.append({'family':name,'selected_params':json.dumps(p,sort_keys=True),'train_n':tm['n'],'train_win':tm['win'],'train_roi':tm['roi'],'test_n':hm['n'],'test_wins':hm['wins'],'test_win':hm['win'],'test_roi':hm['roi'],'test_profit':hm['profit']})
    print(f"{name:32s} TRAIN n={tm['n']:3d} win={100*tm['win']:5.1f}% ROI={pct(tm['roi'])} -> HOLDOUT n={hm['n']:3d} win={100*hm['win']:5.1f}% ROI={pct(hm['roi'])} P/L=${hm['profit']:+,.2f} | {p}")
    prog(18+int(26*fi/len(families)),f'Holdout {fi}/{len(families)} complete')
hold=pd.DataFrame(hold_rows);hold.to_csv(outdir/'holdout_2006_15_to_2016_26.csv',index=False)

# Expanding walk-forward: one frozen choice per family per test season based only on previous seasons.
prog(48,'Running expanding-season walk-forward 2016-2026')
wf=[]
for fi,(name,(fn,grid)) in enumerate(families.items(),1):
    for y in range(2016,2027):
        tr=b[b.season<y]; te=b[b.season==y]
        cand=[]
        for p in grid:
            x=tr[fn(tr,p).fillna(False)]; m=met(x)
            if m['n']<50 or m['win']<.68 or m['roi']<.03:continue
            # Require at least modest recency support when sample exists.
            recent=x[x.season>=max(2006,y-5)]; rm=met(recent)
            if rm['n']>=15 and rm['roi']<=0:continue
            score=min(m['roi'],.20)*math.sqrt(m['n']) + .25*max(0,m['win']-.68)*math.sqrt(m['n'])
            cand.append((score,p,m))
        if not cand:continue
        cand.sort(key=lambda q:q[0],reverse=True); _,p,tm=cand[0]
        z=te[fn(te,p).fillna(False)]; hm=met(z)
        wf.append({'family':name,'test_season':y,'params':json.dumps(p,sort_keys=True),'train_n':tm['n'],'train_win':tm['win'],'train_roi':tm['roi'],'test_n':hm['n'],'test_wins':hm['wins'],'test_win':hm['win'],'test_roi':hm['roi'],'test_profit':hm['profit']})
    prog(48+int(30*fi/len(families)),f'Walk-forward {fi}/{len(families)} complete')
wf=pd.DataFrame(wf);wf.to_csv(outdir/'walkforward_by_season.csv',index=False)

print('\nEXPANDING WALK-FORWARD SUMMARY — TRUE OOS')
print('='*120)
summary=[]
for name in families:
    z=wf[(wf.family==name)&(wf.test_n>0)] if len(wf) else pd.DataFrame()
    if not len(z):continue
    n=int(z.test_n.sum()); wins=int(z.test_wins.sum()); profit=float(z.test_profit.sum()); roi=profit/(100*n) if n else np.nan; wr=wins/n if n else np.nan
    posyrs=int((z.test_profit>0).sum()); yrs=len(z)
    summary.append({'family':name,'oos_n':n,'oos_wins':wins,'oos_win':wr,'oos_roi':roi,'oos_profit':profit,'profitable_seasons':posyrs,'test_seasons':yrs})
    flag=' *** TARGET HIT ***' if n>=30 and wr>=.75 and roi>=.10 else ''
    print(f"{name:32s} OOS n={n:3d} {wins}-{n-wins} win={100*wr:5.1f}% ROI={pct(roi)} P/L=${profit:+,.2f} profitable years={posyrs}/{yrs}{flag}")
pd.DataFrame(summary).to_csv(outdir/'walkforward_summary.csv',index=False)

# Chronological drawdown for the holdout-selected frozen rule on 2016+.
prog(84,'Calculating frozen-holdout drawdowns')
dd=[]
for _,r in hold.iterrows():
    if r.selected_params=='NONE':continue
    name=r.family; fn=families[name][0]; p=json.loads(r.selected_params)
    z=b[(b.season>=2016) & fn(b,p).fillna(False)].sort_values(['season','week','game_id']).copy()
    if not len(z):continue
    z['cum']=z.bet_profit100.cumsum(); z['peak']=z['cum'].cummax().clip(lower=0); z['dd']=z['cum']-z['peak']
    dd.append({'family':name,'n':len(z),'profit':z.bet_profit100.sum(),'max_drawdown':z.dd.min()})
pd.DataFrame(dd).to_csv(outdir/'holdout_drawdowns.csv',index=False)

prog(90,'Testing strict target: >=75% win AND >=10% ROI')
if summary:
    sd=pd.DataFrame(summary)
    hits=sd[(sd.oos_n>=30)&(sd.oos_win>=.75)&(sd.oos_roi>=.10)].copy()
else:hits=pd.DataFrame()
hits.to_csv(outdir/'true_oos_target_hits.csv',index=False)
print('\nTRUE OOS TARGET HITS (>=30 bets, >=75% wins, >=10% ROI)')
print('='*120)
if len(hits):
    for _,r in hits.sort_values('oos_roi',ascending=False).iterrows():
        print(f"{r.family:32s} n={int(r.oos_n):3d} win={100*r.oos_win:.1f}% ROI={100*r.oos_roi:+.2f}% P/L=${r.oos_profit:+,.2f}")
else:print('None cleared the full target in expanding walk-forward.')

prog(100,'Hard NFL top-family validation complete')
print('\nSaved outputs in',outdir)
PY
