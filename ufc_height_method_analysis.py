from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path('ufc_height_method_analysis')
OUT.mkdir(exist_ok=True)

COMP_URL = 'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv'
IND_URL = 'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
ODDS_ZIP = Path('/tmp/odds.zip')

DISCOVERY_END = pd.Timestamp('2019-12-31')
VALIDATION_START = pd.Timestamp('2020-01-01')

DIVISIONS = {
    'Strawweight': 'Strawweight',
    "Women's Strawweight": "Women's Strawweight",
    'Flyweight': 'Flyweight',
    "Women's Flyweight": "Women's Flyweight",
    'Bantamweight': 'Bantamweight',
    "Women's Bantamweight": "Women's Bantamweight",
    'Featherweight': 'Featherweight',
    "Women's Featherweight": "Women's Featherweight",
    'Lightweight': 'Lightweight',
    'Welterweight': 'Welterweight',
    'Middleweight': 'Middleweight',
    'Light Heavyweight': 'Light Heavyweight',
    'Heavyweight': 'Heavyweight',
}


def parse_height_inches(v):
    s = str(v or '').strip()
    if not s or s == '--' or s.lower() == 'nan':
        return np.nan
    m = re.search(r"(\d+)\s*'\s*(\d+)", s)
    if m:
        return int(m.group(1))*12 + int(m.group(2))
    return np.nan


def clean_division(v):
    s = str(v or '').strip()
    s = re.sub(r'\s+Bout$', '', s, flags=re.I)
    s = re.sub(r'^UFC\s+', '', s, flags=re.I)
    for k in DIVISIONS:
        if s.lower() == k.lower():
            return DIVISIONS[k]
    return None


def implied_decimal(d):
    d = float(d)
    return 1.0/d if d > 1 else np.nan


def wilson(wins, n, z=1.96):
    if n <= 0:
        return np.nan, np.nan
    p = wins/n
    den = 1 + z*z/n
    center = (p + z*z/(2*n))/den
    half = z*np.sqrt((p*(1-p)/n) + z*z/(4*n*n))/den
    return center-half, center+half


def metric(g, win_col='taller_won'):
    n = len(g)
    if n == 0:
        return {'n':0,'wins':0,'win_rate':np.nan,'ci_low':np.nan,'ci_high':np.nan}
    wins = int(g[win_col].astype(bool).sum())
    lo, hi = wilson(wins,n)
    return {'n':n,'wins':wins,'win_rate':wins/n,'ci_low':lo,'ci_high':hi}


def load_fights():
    c = pd.read_csv(COMP_URL, low_memory=False)
    i = pd.read_csv(IND_URL, low_memory=False)

    i['height_in'] = i['height'].map(parse_height_inches)
    i['url_norm'] = i['url'].astype(str).str.strip()
    heights = i.dropna(subset=['height_in']).drop_duplicates('url_norm').set_index('url_norm')['height_in'].to_dict()

    c['event_date'] = pd.to_datetime(c['event_date'], errors='coerce')
    c = c[c['event_date'].notna() & (c['event_date'] >= pd.Timestamp('2006-01-01'))].copy()
    c['division'] = c['weightclass'].map(clean_division)
    c = c[c['division'].notna()].copy()
    c['result_norm'] = c['result'].astype(str).str.upper().str.strip()
    c = c[c['result_norm'].isin(['W','L'])].copy()

    c['h1'] = c['player1_url'].astype(str).str.strip().map(heights)
    c['h2'] = c['player2_url'].astype(str).str.strip().map(heights)
    c = c.dropna(subset=['h1','h2']).copy()
    c['height_diff'] = c['h1'] - c['h2']
    c = c[c['height_diff'] != 0].copy()
    c['height_gap'] = c['height_diff'].abs()
    c['taller_is_p1'] = c['height_diff'] > 0
    c['p1_won'] = c['result_norm'].eq('W')
    c['taller_won'] = np.where(c['taller_is_p1'], c['p1_won'], ~c['p1_won'])
    c['shorter_won'] = ~c['taller_won']
    c['pair_key'] = c.apply(lambda r: '|'.join(sorted([str(r['player1_url']).strip(), str(r['player2_url']).strip()])),axis=1)
    return c


def height_only_tables(df):
    rows=[]
    divisions=['ALL'] + sorted(df['division'].unique())
    for div in divisions:
        d = df if div == 'ALL' else df[df['division']==div]
        for gap in [1,2,3,4,5,6]:
            g=d[d['height_gap']>=gap]
            allm=metric(g)
            disc=metric(g[g['event_date']<=DISCOVERY_END])
            val=metric(g[g['event_date']>=VALIDATION_START])
            rows.append({
                'division':div,'min_height_gap_in':gap,
                **{f'all_{k}':v for k,v in allm.items()},
                **{f'discovery_{k}':v for k,v in disc.items()},
                **{f'validation_{k}':v for k,v in val.items()},
            })
    return pd.DataFrame(rows)


def load_odds_selected():
    if not ODDS_ZIP.exists():
        return pd.DataFrame()
    with zipfile.ZipFile(ODDS_ZIP) as z:
        names=[n for n in z.namelist() if n.endswith('UFC_betting_odds.csv') or n.endswith('.csv')]
        if not names:
            return pd.DataFrame()
        with z.open(names[0]) as f:
            x=pd.read_csv(f,low_memory=False)
    x['event_date']=pd.to_datetime(x['event_date'],errors='coerce').dt.normalize()
    x['adding_date']=pd.to_datetime(x['adding_date'],errors='coerce',utc=True)
    x['odds_1']=pd.to_numeric(x['odds_1'],errors='coerce')
    x['odds_2']=pd.to_numeric(x['odds_2'],errors='coerce')
    x=x.dropna(subset=['event_date','fighter_1_url','fighter_2_url','odds_1','odds_2'])
    x=x[(x['odds_1']>1)&(x['odds_2']>1)].copy()
    x['u1']=x['fighter_1_url'].astype(str).str.strip()
    x['u2']=x['fighter_2_url'].astype(str).str.strip()
    x['pair_key']=x.apply(lambda r:'|'.join(sorted([r['u1'],r['u2']])),axis=1)
    x['region_norm']=x['region'].fillna('').astype(str).str.lower()

    rows=[]
    for (date,pair),g in x.groupby(['event_date','pair_key'],sort=False):
        us=g[g['region_norm'].eq('us')]
        if not us.empty:
            g=us
        cutoff=pd.Timestamp(date,tz='UTC')+pd.Timedelta(hours=36)
        timely=g[g['adding_date'].notna() & (g['adding_date']<=cutoff)]
        if not timely.empty:
            latest=timely['adding_date'].max(); snap=timely[timely['adding_date']==latest].copy()
        else:
            latest=g['adding_date'].max() if g['adding_date'].notna().any() else pd.NaT
            snap=g[g['adding_date']==latest].copy() if pd.notna(latest) else g.copy()
        imp1=1/snap['odds_1'].astype(float); imp2=1/snap['odds_2'].astype(float); total=imp1+imp2
        nv1=float((imp1/total).median()); nv2=1-nv1
        r0=snap.iloc[0]
        rows.append({
            'odds_date':date,'pair_key':pair,'ou1':r0['u1'],'ou2':r0['u2'],
            'market_fav_side':1 if nv1>=nv2 else 2,
            'market_prob':max(nv1,nv2),
        })
    return pd.DataFrame(rows)


def attach_odds(df, odds):
    if odds.empty:
        return pd.DataFrame()
    # pair exact, date within +-1 day
    bypair={k:g for k,g in odds.groupby('pair_key')}
    rows=[]
    for _,r in df.iterrows():
        g=bypair.get(r['pair_key'])
        if g is None: continue
        deltas=(g['odds_date']-r['event_date'].normalize()).dt.days.abs()
        idx=deltas.idxmin()
        if deltas.loc[idx]>1: continue
        o=g.loc[idx]
        p1url=str(r['player1_url']).strip()
        if o['ou1']==p1url:
            fav_is_p1=int(o['market_fav_side'])==1
        else:
            fav_is_p1=int(o['market_fav_side'])==2
        rr=r.to_dict()
        rr['market_prob']=float(o['market_prob'])
        rr['market_fav_is_p1']=fav_is_p1
        rr['market_fav_taller']=(fav_is_p1 and r['taller_is_p1']) or ((not fav_is_p1) and (not r['taller_is_p1']))
        rr['market_fav_won']=r['p1_won'] if fav_is_p1 else (not r['p1_won'])
        rows.append(rr)
    return pd.DataFrame(rows)


def market_height_tables(df):
    rows=[]
    divisions=['ALL']+sorted(df['division'].unique())
    for div in divisions:
        d=df if div=='ALL' else df[df['division']==div]
        for pmin in [.55,.60,.65,.70,.75]:
            for gap in [1,2,3,4,5]:
                g=d[(d['market_prob']>=pmin)&(d['market_fav_taller'])&(d['height_gap']>=gap)]
                allm=metric(g,'market_fav_won')
                disc=metric(g[g['event_date']<=DISCOVERY_END],'market_fav_won')
                val=metric(g[g['event_date']>=VALIDATION_START],'market_fav_won')
                rows.append({
                    'division':div,'market_prob_min':pmin,'min_height_gap_in':gap,
                    **{f'all_{k}':v for k,v in allm.items()},
                    **{f'discovery_{k}':v for k,v in disc.items()},
                    **{f'validation_{k}':v for k,v in val.items()},
                })
    return pd.DataFrame(rows)


def report_height_role(df, ht, mh):
    lines=[]
    lines += ['UFC HEIGHT METHOD ANALYSIS','='*88,'']
    lines += [f'Fight sample with both heights, decisive W/L, recognized division: {len(df)}',
              f'Date range: {df.event_date.min().date()} to {df.event_date.max().date()}',
              f'Overall taller-fighter win rate: {df.taller_won.mean()*100:.2f}% ({int(df.taller_won.sum())}/{len(df)})','']

    lines += ['TALLER FIGHTER BY WEIGHT CLASS (ANY NONZERO HEIGHT ADVANTAGE)','-'*88]
    for div,g in df.groupby('division'):
        m=metric(g)
        lines.append(f'{div:<24} n={m["n"]:4d} taller wins={m["win_rate"]*100:6.2f}%')

    lines += ['', 'HEIGHT-ONLY RULES WITH >=70% DISCOVERY WIN RATE (min 50 discovery fights)','-'*88]
    cand=ht[(ht.discovery_n>=50)&(ht.discovery_win_rate>=.70)].copy()
    if cand.empty:
        lines.append('NONE')
    else:
        cand=cand.sort_values(['validation_win_rate','validation_n'],ascending=[False,False])
        for _,r in cand.iterrows():
            lines.append(
                f'{r.division:<24} taller by >={int(r.min_height_gap_in)}in | '
                f'discovery {int(r.discovery_n):3d} @ {r.discovery_win_rate*100:5.1f}% | '
                f'validation {int(r.validation_n):3d} @ {r.validation_win_rate*100:5.1f}% | '
                f'all {int(r.all_n):3d} @ {r.all_win_rate*100:5.1f}%'
            )

    robust=ht[(ht.discovery_n>=50)&(ht.validation_n>=30)&(ht.discovery_win_rate>=.70)&(ht.validation_win_rate>=.70)].copy()
    lines += ['', 'ROBUST HEIGHT-ONLY RULES: >=70% IN BOTH DISCOVERY AND 2020+ VALIDATION','-'*88]
    if robust.empty:
        lines.append('NONE')
    else:
        robust=robust.sort_values(['validation_win_rate','all_n'],ascending=[False,False])
        for _,r in robust.iterrows():
            lines.append(
                f'{r.division:<24} taller by >={int(r.min_height_gap_in)}in | '
                f'all n={int(r.all_n):3d} win={r.all_win_rate*100:5.1f}% | '
                f'2020+ n={int(r.validation_n):3d} win={r.validation_win_rate*100:5.1f}%'
            )

    if not mh.empty:
        lines += ['', 'MARKET FAVORITE + HEIGHT RULES >=70% IN BOTH PERIODS','-'*88]
        mc=mh[(mh.discovery_n>=50)&(mh.validation_n>=30)&(mh.discovery_win_rate>=.70)&(mh.validation_win_rate>=.70)].copy()
        if mc.empty:
            lines.append('NONE')
        else:
            # Keep parsimonious candidates: sort by validation n then win rate.
            mc=mc.sort_values(['validation_n','validation_win_rate'],ascending=[False,False])
            for _,r in mc.head(40).iterrows():
                lines.append(
                    f'{r.division:<24} market>={r.market_prob_min*100:2.0f}% + taller fav by >={int(r.min_height_gap_in)}in | '
                    f'all n={int(r.all_n):3d} win={r.all_win_rate*100:5.1f}% | '
                    f'disc={int(r.discovery_n):3d}/{r.discovery_win_rate*100:5.1f}% | '
                    f'2020+={int(r.validation_n):3d}/{r.validation_win_rate*100:5.1f}%'
                )

    lines += ['', 'NOTE','-'*88,
              'Rules were screened on 2006-2019 and then checked separately on 2020+ fights.',
              'A 70% full-sample win rate by itself is not treated as enough evidence if validation fails.',
              'This analysis evaluates hit rate first; profitability/ROI requires historical prices and is a separate test.']
    return '\n'.join(lines)+'\n'


def main():
    fights=load_fights()
    fights.to_csv(OUT/'height_fight_sample.csv',index=False)
    ht=height_only_tables(fights)
    ht.to_csv(OUT/'height_only_rules.csv',index=False)

    odds=load_odds_selected()
    mh=pd.DataFrame()
    if not odds.empty:
        odf=attach_odds(fights,odds)
        odf.to_csv(OUT/'height_market_sample.csv',index=False)
        mh=market_height_tables(odf)
        mh.to_csv(OUT/'market_plus_height_rules.csv',index=False)

    report=report_height_role(fights,ht,mh)
    (OUT/'report.txt').write_text(report,encoding='utf-8')
    print(report)

if __name__=='__main__':
    main()
