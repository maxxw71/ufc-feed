from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path('ufc_reach_method_analysis')
OUT.mkdir(exist_ok=True)
COMP_URL = 'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv'
IND_URL = 'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
ODDS_ZIP = Path('/tmp/odds.zip')
DISCOVERY_END = pd.Timestamp('2019-12-31')
VALIDATION_START = pd.Timestamp('2020-01-01')
STAKE = 100.0

DIVISIONS = {
    'Strawweight':'Strawweight',"Women's Strawweight":"Women's Strawweight",
    'Flyweight':'Flyweight',"Women's Flyweight":"Women's Flyweight",
    'Bantamweight':'Bantamweight',"Women's Bantamweight":"Women's Bantamweight",
    'Featherweight':'Featherweight',"Women's Featherweight":"Women's Featherweight",
    'Lightweight':'Lightweight','Welterweight':'Welterweight','Middleweight':'Middleweight',
    'Light Heavyweight':'Light Heavyweight','Heavyweight':'Heavyweight'
}

def norm_name(s):
    s=str(s or '').lower().replace('’',"'").replace('-',' ')
    s=re.sub(r'\b(jr|sr|ii|iii|iv)\b',' ',s)
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def parse_reach(v):
    s=str(v or '').strip()
    if not s or s=='--' or s.lower()=='nan': return np.nan
    m=re.search(r'(\d+(?:\.\d+)?)',s)
    return float(m.group(1)) if m else np.nan

def clean_division(v):
    s=re.sub(r'\s+Bout$','',str(v or '').strip(),flags=re.I)
    for k in DIVISIONS:
        if s.lower()==k.lower(): return DIVISIONS[k]
    return None

def implied(d):
    d=float(d); return 1.0/d if d>1 else np.nan

def american(d):
    d=float(d)
    if d<=1:return np.nan
    return 100*(d-1) if d>=2 else -100/(d-1)

def profit100(d,won):
    return STAKE*(float(d)-1) if bool(won) else -STAKE

def wilson(w,n,z=1.96):
    if n<=0:return np.nan,np.nan
    p=w/n; den=1+z*z/n
    c=(p+z*z/(2*n))/den; h=z*np.sqrt((p*(1-p)/n)+z*z/(4*n*n))/den
    return c-h,c+h

def metric(g,win_col='longer_won'):
    n=len(g)
    if not n:return {'n':0,'wins':0,'win_rate':np.nan,'ci_low':np.nan,'ci_high':np.nan}
    w=int(g[win_col].astype(bool).sum()); lo,hi=wilson(w,n)
    return {'n':n,'wins':w,'win_rate':w/n,'ci_low':lo,'ci_high':hi}

def roi_metric(g):
    n=len(g)
    if not n:return {'n':0,'wins':0,'win_rate':np.nan,'roi':np.nan,'profit':0.0}
    return {'n':n,'wins':int(g.market_fav_won.sum()),'win_rate':float(g.market_fav_won.mean()),'roi':float(g.profit_100.sum()/(n*STAKE)),'profit':float(g.profit_100.sum())}

def load_fights():
    c=pd.read_csv(COMP_URL,low_memory=False); i=pd.read_csv(IND_URL,low_memory=False)
    i['reach_in']=i['reach'].map(parse_reach); i['url_norm']=i.url.astype(str).str.strip()
    reach=i.dropna(subset=['reach_in']).drop_duplicates('url_norm').set_index('url_norm').reach_in.to_dict()
    c['event_date']=pd.to_datetime(c.event_date,errors='coerce')
    c=c[c.event_date.notna()&(c.event_date>=pd.Timestamp('2006-01-01'))].copy()
    c['division']=c.weightclass.map(clean_division); c=c[c.division.notna()].copy()
    c['result_norm']=c.result.astype(str).str.upper().str.strip(); c=c[c.result_norm.isin(['W','L'])].copy()
    c['u1']=c.player1_url.astype(str).str.strip(); c['u2']=c.player2_url.astype(str).str.strip()
    c['r1']=c.u1.map(reach); c['r2']=c.u2.map(reach); c=c.dropna(subset=['r1','r2']).copy()
    c['reach_diff']=c.r1-c.r2; c=c[c.reach_diff!=0].copy(); c['reach_gap']=c.reach_diff.abs()
    c['longer_is_p1']=c.reach_diff>0; c['p1_won']=c.result_norm.eq('W')
    c['longer_won']=np.where(c.longer_is_p1,c.p1_won,~c.p1_won)
    c['p1_norm']=c['player1'].map(norm_name); c['p2_norm']=c['player2'].map(norm_name)
    c['pair_key']=c.apply(lambda r:'|'.join(sorted([r.u1,r.u2])),axis=1)
    return c

def load_odds():
    with zipfile.ZipFile(ODDS_ZIP) as z:
        names=[n for n in z.namelist() if n.endswith('UFC_betting_odds.csv') or n.endswith('.csv')]
        with z.open(names[0]) as f:x=pd.read_csv(f,low_memory=False)
    x['event_date']=pd.to_datetime(x.event_date,errors='coerce').dt.normalize()
    x['adding_date']=pd.to_datetime(x.adding_date,errors='coerce',utc=True)
    x['odds_1']=pd.to_numeric(x.odds_1,errors='coerce'); x['odds_2']=pd.to_numeric(x.odds_2,errors='coerce')
    x=x.dropna(subset=['event_date','fighter_1_url','fighter_2_url','odds_1','odds_2'])
    x=x[(x.odds_1>1)&(x.odds_2>1)].copy(); x['u1']=x.fighter_1_url.astype(str).str.strip(); x['u2']=x.fighter_2_url.astype(str).str.strip()
    x['n1']=x.fighter_1.map(norm_name); x['n2']=x.fighter_2.map(norm_name)
    x['pair_key']=x.apply(lambda r:'|'.join(sorted([r.u1,r.u2])),axis=1); x['region_norm']=x.region.fillna('').astype(str).str.lower()
    rows=[]
    for (date,pair),g in x.groupby(['event_date','pair_key'],sort=False):
        us=g[g.region_norm.eq('us')]
        if not us.empty:g=us
        cutoff=pd.Timestamp(date,tz='UTC')
        timely=g[g.adding_date.notna()&(g.adding_date<cutoff)]
        if not timely.empty:
            latest=timely.adding_date.max(); snap=timely[timely.adding_date==latest].copy()
        else:
            late=g.adding_date.notna()&(g.adding_date>=cutoff+pd.Timedelta(days=3))
            if late.all() and g.adding_date.notna().any():
                latest=g.adding_date.max(); snap=g[g.adding_date==latest].copy()
            else:
                continue
        r0=snap.iloc[0]; ref1,ref2=r0.n1,r0.n2
        direct=snap.n1.eq(ref1)&snap.n2.eq(ref2); reverse=snap.n1.eq(ref2)&snap.n2.eq(ref1)
        snap=snap[direct|reverse].copy()
        if snap.empty: continue
        direct=snap.n1.eq(ref1)&snap.n2.eq(ref2)
        snap['a1']=np.where(direct,snap.odds_1,snap.odds_2).astype(float)
        snap['a2']=np.where(direct,snap.odds_2,snap.odds_1).astype(float)
        i1=1/snap.a1; i2=1/snap.a2; t=i1+i2
        nv1=float((i1/t).median()); nv2=float((i2/t).median()); z=nv1+nv2; nv1/=z; nv2/=z
        if nv1>=nv2: side=1; prob=nv1; best=float(snap.a1.max())
        else: side=2; prob=nv2; best=float(snap.a2.max())
        rows.append({'odds_date':date,'pair_key':pair,'on1':ref1,'on2':ref2,'fav_side':side,'market_prob':prob,'fav_decimal':best})
    return pd.DataFrame(rows)

def attach(df,odds):
    bypair={k:g for k,g in odds.groupby('pair_key')}; rows=[]
    for _,r in df.iterrows():
        g=bypair.get(r.pair_key)
        if g is None:continue
        deltas=(g.odds_date-r.event_date.normalize()).dt.days.abs(); idx=deltas.idxmin()
        if deltas.loc[idx]>1:continue
        o=g.loc[idx]
        if o.on1==r.p1_norm and o.on2==r.p2_norm:
            fav_is_p1=(int(o.fav_side)==1)
        elif o.on1==r.p2_norm and o.on2==r.p1_norm:
            fav_is_p1=(int(o.fav_side)==2)
        else:
            continue
        rr=r.to_dict(); rr['market_prob']=float(o.market_prob); rr['market_fav_is_p1']=fav_is_p1
        rr['market_fav_longer']=(fav_is_p1 and r.longer_is_p1) or ((not fav_is_p1) and (not r.longer_is_p1))
        rr['market_fav_won']=bool(r.p1_won if fav_is_p1 else (not r.p1_won)); rr['fav_decimal']=float(o.fav_decimal)
        rr['american_odds']=american(o.fav_decimal); rr['profit_100']=profit100(o.fav_decimal,rr['market_fav_won'])
        rows.append(rr)
    return pd.DataFrame(rows)

def main():
    f=load_fights(); o=load_odds(); d=attach(f,o)
    f.to_csv(OUT/'reach_fight_sample.csv',index=False); d.to_csv(OUT/'reach_market_sample.csv',index=False)
    lines=['UFC REACH METHOD ANALYSIS','='*92,'',f'Fight sample with both reaches: {len(f)}',f'Date range: {f.event_date.min().date()} to {f.event_date.max().date()}',f'Overall longer-reach fighter win rate: {f.longer_won.mean()*100:.2f}% ({int(f.longer_won.sum())}/{len(f)})','']
    lines+=['LONGER REACH BY WEIGHT CLASS','-'*92]
    for div,g in f.groupby('division'):
        m=metric(g); lines.append(f'{div:<24} n={m["n"]:4d} longer-reach wins={m["win_rate"]*100:6.2f}%')
    lines+=['','REACH-ONLY THRESHOLDS (ALL / DISCOVERY / 2020+)','-'*92]
    for gap in [1,2,3,4,5,6,7,8]:
        g=f[f.reach_gap>=gap]; a=metric(g); ds=metric(g[g.event_date<=DISCOVERY_END]); v=metric(g[g.event_date>=VALIDATION_START])
        lines.append(f'>={gap}in reach | all n={a["n"]:4d} {a["win_rate"]*100:5.1f}% | disc n={ds["n"]:4d} {ds["win_rate"]*100:5.1f}% | 2020+ n={v["n"]:4d} {v["win_rate"]*100:5.1f}%')
    lines+=['','MARKET FAVORITE + REACH: WIN RATE, LIFT, ROI','-'*92]
    candidates=[]
    for p in [.55,.60,.65,.70,.75,.80]:
        base=d[d.market_prob>=p]; bm=roi_metric(base)
        lines.append(f'MARKET >= {p*100:.0f}% baseline: n={bm["n"]:4d} win={bm["win_rate"]*100:5.1f}% ROI={bm["roi"]*100:+6.2f}%')
        for gap in [1,2,3,4,5,6]:
            g=d[(d.market_prob>=p)&d.market_fav_longer&(d.reach_gap>=gap)]
            m=roi_metric(g); disc=roi_metric(g[g.event_date<=DISCOVERY_END]); val=roi_metric(g[g.event_date>=VALIDATION_START])
            lift=(m['win_rate']-bm['win_rate'])*100 if m['n'] else np.nan
            lines.append(f'  reach >={gap}in: n={m["n"]:4d} win={m["win_rate"]*100:5.1f}% lift={lift:+5.2f}pp ROI={m["roi"]*100:+6.2f}% | disc {disc["n"]}/{disc["roi"]*100:+.2f}% | 2020+ {val["n"]}/{val["roi"]*100:+.2f}%')
            if m['n']>=75 and m['win_rate']>=.70 and m['roi']>0 and disc['n']>=40 and val['n']>=25 and disc['roi']>0 and val['roi']>0:
                candidates.append((p,gap,m,disc,val,lift))
    lines+=['','ROBUST POSITIVE-ROI CANDIDATES','-'*92]
    if not candidates:lines.append('NONE')
    else:
        candidates=sorted(candidates,key=lambda x:(x[2]['roi'],x[2]['n']),reverse=True)
        for p,gap,m,disc,val,lift in candidates:
            lines.append(f'market>={p*100:.0f}% + favorite reach >={gap}in | n={m["n"]} win={m["win_rate"]*100:.1f}% ROI={m["roi"]*100:+.2f}% lift={lift:+.2f}pp | disc ROI={disc["roi"]*100:+.2f}% | 2020+ ROI={val["roi"]*100:+.2f}%')
    # by division for a practical 65% / 2in and 65% / 4in view
    for p,gap in [(.65,2),(.65,4),(.70,2),(.70,4)]:
        lines+=['',f'BY DIVISION — market>={p*100:.0f}% + favorite reach >={gap}in','-'*92]
        for div,g0 in d.groupby('division'):
            g=g0[(g0.market_prob>=p)&g0.market_fav_longer&(g0.reach_gap>=gap)]
            if len(g)>=8:
                m=roi_metric(g); lines.append(f'{div:<24} n={m["n"]:3d} win={m["win_rate"]*100:5.1f}% ROI={m["roi"]*100:+6.2f}% P/L=${m["profit"]:+,.2f}')
    report='\n'.join(lines)+'\n'; (OUT/'report.txt').write_text(report); print(report)

if __name__=='__main__':main()
