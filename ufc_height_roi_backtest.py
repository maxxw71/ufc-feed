from __future__ import annotations

import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import ufc_height_method_analysis as h

OUT=Path('ufc_height_roi_backtest'); OUT.mkdir(exist_ok=True)
STAKE=100.0

def load_odds_with_price():
    with zipfile.ZipFile('/tmp/odds.zip') as z:
        names=[n for n in z.namelist() if n.endswith('UFC_betting_odds.csv') or n.endswith('.csv')]
        with z.open(names[0]) as f:
            x=pd.read_csv(f,low_memory=False)
    x['event_date']=pd.to_datetime(x['event_date'],errors='coerce').dt.normalize()
    x['adding_date']=pd.to_datetime(x['adding_date'],errors='coerce',utc=True)
    x['odds_1']=pd.to_numeric(x['odds_1'],errors='coerce'); x['odds_2']=pd.to_numeric(x['odds_2'],errors='coerce')
    x=x.dropna(subset=['event_date','fighter_1_url','fighter_2_url','odds_1','odds_2'])
    x=x[(x.odds_1>1)&(x.odds_2>1)].copy()
    x['u1']=x['fighter_1_url'].astype(str).str.strip(); x['u2']=x['fighter_2_url'].astype(str).str.strip()
    x['pair_key']=x.apply(lambda r:'|'.join(sorted([r.u1,r.u2])),axis=1)
    x['region_norm']=x['region'].fillna('').astype(str).str.lower()
    rows=[]
    for (date,pair),g in x.groupby(['event_date','pair_key'],sort=False):
        us=g[g.region_norm.eq('us')]
        if not us.empty: g=us
        cutoff=pd.Timestamp(date,tz='UTC')+pd.Timedelta(hours=36)
        timely=g[g.adding_date.notna()&(g.adding_date<=cutoff)]
        if not timely.empty:
            latest=timely.adding_date.max(); snap=timely[timely.adding_date==latest].copy()
        else:
            latest=g.adding_date.max() if g.adding_date.notna().any() else pd.NaT
            snap=g[g.adding_date==latest].copy() if pd.notna(latest) else g.copy()
        i1=1/snap.odds_1.astype(float); i2=1/snap.odds_2.astype(float); t=i1+i2
        nv1=float((i1/t).median()); nv2=1-nv1
        r0=snap.iloc[0]
        if nv1>=nv2:
            side=1; mp=nv1; best=float(snap.odds_1.max())
        else:
            side=2; mp=nv2; best=float(snap.odds_2.max())
        rows.append({'odds_date':date,'pair_key':pair,'ou1':r0.u1,'ou2':r0.u2,'market_fav_side':side,'market_prob':mp,'favorite_decimal_odds':best})
    return pd.DataFrame(rows)

def attach(df,odds):
    by={k:g for k,g in odds.groupby('pair_key')}; rows=[]
    for _,r in df.iterrows():
        g=by.get(r.pair_key)
        if g is None: continue
        delta=(g.odds_date-r.event_date.normalize()).dt.days.abs(); idx=delta.idxmin()
        if delta.loc[idx]>1: continue
        o=g.loc[idx]; p1=str(r.player1_url).strip()
        favp1=(int(o.market_fav_side)==1) if o.ou1==p1 else (int(o.market_fav_side)==2)
        won=bool(r.p1_won) if favp1 else (not bool(r.p1_won))
        taller=(favp1 and bool(r.taller_is_p1)) or ((not favp1) and (not bool(r.taller_is_p1)))
        rr=r.to_dict(); rr.update({'market_prob':float(o.market_prob),'favorite_decimal_odds':float(o.favorite_decimal_odds),'market_fav_won':won,'market_fav_taller':taller})
        rows.append(rr)
    return pd.DataFrame(rows)

def metrics(g):
    if len(g)==0:return {'bets':0,'wins':0,'losses':0,'win_rate':np.nan,'profit':0.0,'roi':np.nan}
    prof=np.where(g.market_fav_won,(g.favorite_decimal_odds-1)*STAKE,-STAKE)
    return {'bets':len(g),'wins':int(g.market_fav_won.sum()),'losses':int((~g.market_fav_won).sum()),'win_rate':float(g.market_fav_won.mean()),'profit':float(prof.sum()),'roi':float(prof.sum()/(len(g)*STAKE))}

def american(d):
    d=float(d); return 100*(d-1) if d>=2 else -100/(d-1)

def main():
    fights=h.load_fights(); odds=load_odds_with_price(); df=attach(fights,odds)
    df['american_odds']=df.favorite_decimal_odds.map(american)
    rule=df[(df.market_prob>=.65)&(df.market_fav_taller)&(df.height_gap>=4)].copy()
    rule.to_csv(OUT/'height_rule_65pct_4in_bets.csv',index=False)
    lines=['UFC HEIGHT HYBRID ROI BACKTEST','='*80,'','Rule: no-vig favorite >=65% AND favorite >=4 inches taller','']
    for label,g in [('2006-current',rule),('2006-2019',rule[rule.event_date<pd.Timestamp('2020-01-01')]),('2020-current',rule[rule.event_date>=pd.Timestamp('2020-01-01')])]:
        m=metrics(g); lines.append(f"{label:<14} bets={m['bets']:3d} wins={m['wins']:3d} losses={m['losses']:3d} win={m['win_rate']*100:5.1f}% ROI={m['roi']*100:+6.2f}% P/L@100=${m['profit']:+,.2f}")
    lines += ['','PRICE BANDS','-'*80]
    bins=[-100000,-500,-400,-300,-250,-200,-150,0,100000]
    labels=['-500 or worse','-400 to -499','-300 to -399','-250 to -299','-200 to -249','-150 to -199','-149 to +0','underdog/plus']
    rule['line_band']=pd.cut(rule.american_odds,bins=bins,labels=labels,right=False)
    for b,g in rule.groupby('line_band',observed=True):
        m=metrics(g); lines.append(f"{str(b):<18} n={m['bets']:3d} win={m['win_rate']*100:5.1f}% ROI={m['roi']*100:+6.2f}% P/L=${m['profit']:+,.2f}")
    lines += ['','BY WEIGHT CLASS','-'*80]
    for div,g in rule.groupby('division'):
        m=metrics(g); lines.append(f"{div:<24} n={m['bets']:3d} win={m['win_rate']*100:5.1f}% ROI={m['roi']*100:+6.2f}% P/L=${m['profit']:+,.2f}")
    lines += ['','ALT HEIGHT THRESHOLDS @ MARKET>=65%','-'*80]
    for gap in [1,2,3,4,5]:
        g=df[(df.market_prob>=.65)&(df.market_fav_taller)&(df.height_gap>=gap)]
        m=metrics(g); lines.append(f">={gap} in taller  n={m['bets']:4d} win={m['win_rate']*100:5.1f}% ROI={m['roi']*100:+6.2f}% P/L=${m['profit']:+,.2f}")
    lines += ['','ALT MARKET THRESHOLDS @ HEIGHT>=4in','-'*80]
    for p in [.55,.60,.65,.70,.75]:
        g=df[(df.market_prob>=p)&(df.market_fav_taller)&(df.height_gap>=4)]
        m=metrics(g); lines.append(f">={int(p*100)}% market n={m['bets']:4d} win={m['win_rate']*100:5.1f}% ROI={m['roi']*100:+6.2f}% P/L=${m['profit']:+,.2f}")
    report='\n'.join(lines)+'\n'; (OUT/'report.txt').write_text(report); print(report)

if __name__=='__main__':main()
