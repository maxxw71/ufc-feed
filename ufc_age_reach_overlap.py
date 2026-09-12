from __future__ import annotations

import re, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT=Path('ufc_age_reach_overlap'); OUT.mkdir(exist_ok=True)
COMP_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv'
IND_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
ODDS_ZIP=Path('/tmp/odds.zip')
DISC_END=pd.Timestamp('2019-12-31'); VAL_START=pd.Timestamp('2020-01-01')

def parse_reach(v):
    s=str(v or '').strip()
    m=re.search(r'(\d+(?:\.\d+)?)',s)
    return float(m.group(1)) if m else np.nan

def american_from_decimal(d):
    if pd.isna(d) or d<=1: return np.nan
    return 100*(d-1) if d>=2 else -100/(d-1)

def profit100_from_decimal(d, won):
    return (100*(d-1)) if won else -100.0

def load_fights():
    c=pd.read_csv(COMP_URL,low_memory=False)
    i=pd.read_csv(IND_URL,low_memory=False)
    i['url_norm']=i['url'].astype(str).str.strip()
    i['dob_dt']=pd.to_datetime(i['dob'],errors='coerce')
    i['reach_in']=i['reach'].map(parse_reach)
    meta=i.drop_duplicates('url_norm').set_index('url_norm')[['dob_dt','reach_in']].to_dict('index')
    c['event_date']=pd.to_datetime(c['event_date'],errors='coerce').dt.normalize()
    c=c[c['event_date'].notna() & (c['event_date']>=pd.Timestamp('2006-01-01'))].copy()
    c['result_norm']=c['result'].astype(str).str.upper().str.strip()
    c=c[c['result_norm'].isin(['W','L'])].copy()
    def g(url,k):
        return meta.get(str(url).strip(),{}).get(k,np.nan)
    c['dob1']=c['player1_url'].map(lambda x:g(x,'dob_dt')); c['dob2']=c['player2_url'].map(lambda x:g(x,'dob_dt'))
    c['reach1']=c['player1_url'].map(lambda x:g(x,'reach_in')); c['reach2']=c['player2_url'].map(lambda x:g(x,'reach_in'))
    c=c.dropna(subset=['dob1','dob2','reach1','reach2']).copy()
    c['age1']=(c['event_date']-c['dob1']).dt.days/365.2425; c['age2']=(c['event_date']-c['dob2']).dt.days/365.2425
    c['p1_won']=c['result_norm'].eq('W')
    c['pair_key']=c.apply(lambda r:'|'.join(sorted([str(r['player1_url']).strip(),str(r['player2_url']).strip()])),axis=1)
    return c

def load_odds():
    with zipfile.ZipFile(ODDS_ZIP) as z:
        names=[n for n in z.namelist() if n.endswith('UFC_betting_odds.csv') or n.endswith('.csv')]
        with z.open(names[0]) as f: x=pd.read_csv(f,low_memory=False)
    x['event_date']=pd.to_datetime(x['event_date'],errors='coerce').dt.normalize(); x['adding_date']=pd.to_datetime(x['adding_date'],errors='coerce',utc=True)
    x['odds_1']=pd.to_numeric(x['odds_1'],errors='coerce'); x['odds_2']=pd.to_numeric(x['odds_2'],errors='coerce')
    x=x.dropna(subset=['event_date','fighter_1_url','fighter_2_url','odds_1','odds_2'])
    x=x[(x.odds_1>1)&(x.odds_2>1)].copy()
    x['u1']=x['fighter_1_url'].astype(str).str.strip(); x['u2']=x['fighter_2_url'].astype(str).str.strip(); x['pair_key']=x.apply(lambda r:'|'.join(sorted([r.u1,r.u2])),axis=1)
    x['region_norm']=x['region'].fillna('').astype(str).str.lower()
    rows=[]
    for (date,pair),g in x.groupby(['event_date','pair_key'],sort=False):
        us=g[g.region_norm.eq('us')]; g=us if not us.empty else g
        cutoff=pd.Timestamp(date,tz='UTC')+pd.Timedelta(hours=36)
        timely=g[g.adding_date.notna() & (g.adding_date<=cutoff)]
        if not timely.empty: g=timely[timely.adding_date==timely.adding_date.max()]
        elif g.adding_date.notna().any(): g=g[g.adding_date==g.adding_date.max()]
        imp1=1/g.odds_1.astype(float); imp2=1/g.odds_2.astype(float); total=imp1+imp2
        nv1=float((imp1/total).median()); nv2=1-nv1
        # Median decimal for each side at selected snapshot, to price bet.
        d1=float(g.odds_1.astype(float).median()); d2=float(g.odds_2.astype(float).median())
        r0=g.iloc[0]
        rows.append({'odds_date':date,'pair_key':pair,'ou1':r0.u1,'ou2':r0.u2,'nv1':nv1,'nv2':nv2,'d1':d1,'d2':d2})
    return pd.DataFrame(rows)

def attach(f,od):
    by={k:g for k,g in od.groupby('pair_key')}; rows=[]
    for _,r in f.iterrows():
        g=by.get(r.pair_key)
        if g is None: continue
        delta=(g.odds_date-r.event_date).dt.days.abs(); idx=delta.idxmin()
        if delta.loc[idx]>1: continue
        o=g.loc[idx]; p1url=str(r.player1_url).strip()
        if o.ou1==p1url:
            p1prob,p2prob,d1,d2=o.nv1,o.nv2,o.d1,o.d2
        else:
            p1prob,p2prob,d1,d2=o.nv2,o.nv1,o.d2,o.d1
        fav_is_p1=p1prob>=p2prob
        fav_prob=max(p1prob,p2prob)
        fav_dec=d1 if fav_is_p1 else d2
        fav_won=bool(r.p1_won) if fav_is_p1 else (not bool(r.p1_won))
        fav_age=float(r.age1 if fav_is_p1 else r.age2); dog_age=float(r.age2 if fav_is_p1 else r.age1)
        fav_reach=float(r.reach1 if fav_is_p1 else r.reach2); dog_reach=float(r.reach2 if fav_is_p1 else r.reach1)
        rr=r.to_dict(); rr.update({'fav_prob':float(fav_prob),'fav_is_p1':fav_is_p1,'fav_won':fav_won,'fav_dec':float(fav_dec),'fav_ml':american_from_decimal(float(fav_dec)),'age_adv':dog_age-fav_age,'reach_adv':fav_reach-dog_reach,'profit100':profit100_from_decimal(float(fav_dec),fav_won)})
        rows.append(rr)
    return pd.DataFrame(rows)

def stats(g):
    n=len(g)
    if not n:return {'n':0,'wins':0,'losses':0,'win':np.nan,'roi':np.nan,'pl':0.0}
    w=int(g.fav_won.sum()); pl=float(g.profit100.sum())
    return {'n':n,'wins':w,'losses':n-w,'win':w/n,'roi':pl/(100*n),'pl':pl}

def line(label,s):
    return f'{label:<34} n={s["n"]:4d} W-L={s["wins"]}-{s["losses"]} win={s["win"]*100:5.1f}% ROI={s["roi"]*100:+6.2f}% P/L@100=${s["pl"]:+,.2f}'

def main():
    df=attach(load_fights(),load_odds())
    df.to_csv(OUT/'age_reach_market_sample.csv',index=False)
    age=df[(df.fav_prob>=.70)&(df.age_adv>=3)].copy()
    r4=df[(df.fav_prob>=.65)&(df.reach_adv>=4)].copy()
    r6=df[(df.fav_prob>=.65)&(df.reach_adv>=6)].copy()
    a_r4=age[age.reach_adv>=4].copy(); a_r6=age[age.reach_adv>=6].copy()
    lines=['UFC AGE + REACH OVERLAP BACKTEST','='*96,'',
           'Age hybrid: market >=70% and favorite >=3 years younger',
           'Reach signal: favorite reach advantage >=4in or >=6in','']
    for label,g in [('AGE HYBRID',age),('REACH >=4in @ market>=65%',r4),('REACH >=6in @ market>=65%',r6),('AGE + REACH >=4in',a_r4),('AGE + REACH >=6in',a_r6)]:
        lines.append(line(label,stats(g)))
        lines.append('  '+line('2006-2019',stats(g[g.event_date<=DISC_END])))
        lines.append('  '+line('2020-current',stats(g[g.event_date>=VAL_START])))
        lines.append('')
    # Reach sweep within age hybrid.
    lines += ['REACH THRESHOLD INSIDE AGE HYBRID','-'*96]
    for rg in [1,2,3,4,5,6,7,8]:
        g=age[age.reach_adv>=rg]
        if len(g): lines.append(line(f'Age + reach >={rg}in',stats(g)))
    # Price bands for the two main overlaps.
    def pb(g,title):
        out=['',title,'-'*96]
        defs=[('-500 or worse',-1e9,-500),('-400 to -499',-499.999,-400),('-300 to -399',-399.999,-300),('-250 to -299',-299.999,-250),('-200 to -249',-249.999,-200),('-150 to -199',-199.999,-150)]
        for name,lo,hi in defs:
            q=g[(g.fav_ml>=lo)&(g.fav_ml<=hi)]
            if len(q): out.append(line(name,stats(q)))
        return out
    lines += pb(a_r4,'PRICE BANDS — AGE + REACH >=4in')
    lines += pb(a_r6,'PRICE BANDS — AGE + REACH >=6in')
    # Yearly for overlaps.
    for g,title in [(a_r4,'YEARLY — AGE + REACH >=4in'),(a_r6,'YEARLY — AGE + REACH >=6in')]:
        lines += ['',title,'-'*96]
        for y,yg in g.groupby(g.event_date.dt.year): lines.append(line(str(y),stats(yg)))
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    a_r4.to_csv(OUT/'age_plus_reach4.csv',index=False); a_r6.to_csv(OUT/'age_plus_reach6.csv',index=False)
    print('\n'.join(lines))

if __name__=='__main__': main()
