from __future__ import annotations

import io, json, re, zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path('ufc_recent_tidytuesday_backfill')
OUT.mkdir(exist_ok=True)
STAKE = 50.0
MARKET_MIN = 0.70
GAP_MIN = 3.0
STRONG_GAP = 4.0

FIGHTS_URL = 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2026/2026-07-07/ufc_fights.csv'
ATHLETES_URL = 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2026/2026-07-07/ufc_athletes.csv'
ODDS_KAGGLE = 'https://www.kaggle.com/api/v1/datasets/download/binduvr/ufc-betting-odds'


def norm_name(s):
    s = str(s or '').lower().replace('’', "'").replace('-', ' ')
    s = re.sub(r'\b(jr|sr|ii|iii|iv)\b', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def implied_decimal(d):
    d = float(d)
    return 1.0 / d if d > 1 else np.nan


def exact_age(dob, event_date):
    return (pd.Timestamp(event_date) - pd.Timestamp(dob)).days / 365.2425


def american_profit(stake, decimal_odds):
    return stake * (float(decimal_odds) - 1.0)


def bootstrap_ci(profits, n_boot=10000, seed=42):
    arr = np.asarray(profits, dtype=float)
    if len(arr) < 10:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    vals = np.empty(n_boot)
    n = len(arr)
    for i in range(n_boot):
        s = rng.choice(arr, size=n, replace=True)
        vals[i] = s.sum() / (n * STAKE)
    return float(np.quantile(vals, .025)), float(np.quantile(vals, .975))


def metrics(df):
    if df.empty:
        return {'bets':0,'wins':0,'win_rate':np.nan,'profit':0.0,'roi':np.nan,'ci_low':np.nan,'ci_high':np.nan}
    profit = float(df['profit'].sum())
    lo,hi = bootstrap_ci(df['profit'].to_numpy())
    return {'bets':len(df),'wins':int(df['won'].sum()),'win_rate':float(df['won'].mean()),'profit':profit,'roi':profit/(len(df)*STAKE),'ci_low':lo,'ci_high':hi}


def pick_col(cols, options):
    cmap = {c.lower(): c for c in cols}
    for x in options:
        if x.lower() in cmap:
            return cmap[x.lower()]
    return None


def load_tidy():
    f = pd.read_csv(FIGHTS_URL, low_memory=False)
    a = pd.read_csv(ATHLETES_URL, low_memory=False)
    print('FIGHT_COLS', list(f.columns), flush=True)
    print('ATHLETE_COLS', list(a.columns), flush=True)

    date_col = pick_col(f.columns,['date','event_date'])
    f1_col = pick_col(f.columns,['f1_name','fighter_1','r_fighter','fighter1'])
    f2_col = pick_col(f.columns,['f2_name','fighter_2','b_fighter','fighter2'])
    r1_col = pick_col(f.columns,['f1_result','fighter_1_result','r_result','result_1'])
    r2_col = pick_col(f.columns,['f2_result','fighter_2_result','b_result','result_2'])
    url_col = pick_col(f.columns,['fight_url'])
    event_col = pick_col(f.columns,['event_name','event'])
    if not all([date_col,f1_col,f2_col,r1_col,r2_col]):
        raise RuntimeError('Could not identify required TidyTuesday fight columns')

    out = pd.DataFrame({
        'event_date': pd.to_datetime(f[date_col], errors='coerce').dt.normalize(),
        'fighter_1': f[f1_col].astype(str),
        'fighter_2': f[f2_col].astype(str),
        'result_1': f[r1_col].astype(str),
        'result_2': f[r2_col].astype(str),
        'fight_url': f[url_col].astype(str) if url_col else '',
        'event_name': f[event_col].astype(str) if event_col else '',
    })
    out = out[(out['event_date'] >= pd.Timestamp('2024-01-01')) & (out['event_date'] <= pd.Timestamp('2026-09-11'))].copy()
    out = out[~out['event_name'].str.contains('Road to UFC', case=False, na=False)].copy()
    out = out[(out['result_1'].isin(['W','L'])) & (out['result_2'].isin(['W','L']))].copy()
    out['f1_norm'] = out['fighter_1'].map(norm_name)
    out['f2_norm'] = out['fighter_2'].map(norm_name)
    out['pair'] = out.apply(lambda r: tuple(sorted((r.f1_norm,r.f2_norm))),axis=1)

    # Athlete DOB schema varies slightly across TidyTuesday sources; inspect and adapt.
    name_col = pick_col(a.columns,['name','fighter_name','fighter','athlete_name'])
    dob_col = pick_col(a.columns,['dob','date_of_birth','birth_date'])
    if not name_col or not dob_col:
        raise RuntimeError('Could not identify athlete name/DOB columns')
    a['name_norm'] = a[name_col].map(norm_name)
    a['dob_parsed'] = pd.to_datetime(a[dob_col], errors='coerce')
    dob = {}
    for _,r in a.dropna(subset=['dob_parsed']).iterrows():
        dob.setdefault(r['name_norm'], pd.Timestamp(r['dob_parsed']))
    return out, dob


def load_odds():
    r = requests.get(ODDS_KAGGLE, timeout=180)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    csv = next(n for n in z.namelist() if n.lower().endswith('.csv'))
    with z.open(csv) as fh:
        x = pd.read_csv(fh, low_memory=False)
    x['event_date'] = pd.to_datetime(x['event_date'], errors='coerce').dt.normalize()
    x['adding_date'] = pd.to_datetime(x['adding_date'], errors='coerce', utc=True)
    x['odds_1'] = pd.to_numeric(x['odds_1'], errors='coerce')
    x['odds_2'] = pd.to_numeric(x['odds_2'], errors='coerce')
    x = x.dropna(subset=['event_date','fighter_1','fighter_2','odds_1','odds_2'])
    x = x[(x['event_date'] >= pd.Timestamp('2024-01-01')) & (x['event_date'] <= pd.Timestamp('2026-09-11'))]
    x = x[(x['odds_1']>1)&(x['odds_2']>1)].copy()
    x['f1_norm'] = x['fighter_1'].map(norm_name)
    x['f2_norm'] = x['fighter_2'].map(norm_name)
    x['pair'] = x.apply(lambda r: tuple(sorted((r.f1_norm,r.f2_norm))),axis=1)

    selected=[]
    for (pair, evdate),g in x.groupby(['pair','event_date'],sort=False):
        us=g[g['region'].fillna('').astype(str).str.lower().eq('us')]
        if not us.empty: g=us
        cutoff=pd.Timestamp(evdate,tz='UTC')+pd.Timedelta(hours=36)
        timely=g[g['adding_date'].notna() & (g['adding_date']<=cutoff)]
        if not timely.empty:
            latest=timely['adding_date'].max(); snap=timely[timely['adding_date']==latest].copy(); mode='pre_or_event_snapshot'
        else:
            latest=g['adding_date'].max() if g['adding_date'].notna().any() else pd.NaT
            snap=g[g['adding_date']==latest].copy() if pd.notna(latest) else g.copy(); mode='historical_import_fallback'
        i1=1/snap['odds_1'].astype(float); i2=1/snap['odds_2'].astype(float); tot=i1+i2
        snap['nv1']=i1/tot; snap['nv2']=i2/tot
        nv1=float(snap['nv1'].median()); nv2=1-nv1
        if nv1>=nv2:
            fav_side=1; mp=nv1; best=float(snap['odds_1'].max())
        else:
            fav_side=2; mp=nv2; best=float(snap['odds_2'].max())
        r0=snap.iloc[0]
        selected.append({'event_date':evdate,'pair':pair,'fighter_1':r0['fighter_1'],'fighter_2':r0['fighter_2'],'f1_norm':r0['f1_norm'],'f2_norm':r0['f2_norm'],'favorite_side_odds':fav_side,'market_prob':mp,'favorite_decimal_odds':best,'snapshot_mode':mode,'fight_url':r0.get('fight_url')})
    return pd.DataFrame(selected)


def main():
    fights,dob=load_tidy(); odds=load_odds()
    print('TIDY_COMPLETED_RECENT_FIGHTS',len(fights),'ODDS_FIGHTS',len(odds),flush=True)
    pair_index={}
    for _,r in fights.iterrows(): pair_index.setdefault(r['pair'],[]).append(r)
    rows=[]; unmatched=[]
    for _,o in odds.iterrows():
        cands=pair_index.get(o['pair'],[])
        if not cands:
            unmatched.append({**o.to_dict(),'reason':'pair_not_found'}); continue
        od=pd.Timestamp(o['event_date']).normalize()
        r=min(cands,key=lambda rr:abs((pd.Timestamp(rr['event_date']).normalize()-od).days))
        delta=int((pd.Timestamp(r['event_date']).normalize()-od).days)
        if abs(delta)>2:
            unmatched.append({**o.to_dict(),'reason':'date_gt_2','delta':delta}); continue
        d1=dob.get(r['f1_norm']); d2=dob.get(r['f2_norm'])
        if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
            unmatched.append({**o.to_dict(),'reason':'missing_dob'}); continue
        direct=(o['f1_norm']==r['f1_norm'] and o['f2_norm']==r['f2_norm'])
        reverse=(o['f1_norm']==r['f2_norm'] and o['f2_norm']==r['f1_norm'])
        if not direct and not reverse:
            unmatched.append({**o.to_dict(),'reason':'orientation_failed'}); continue
        fav_is_f1=(int(o['favorite_side_odds'])==1) if direct else (int(o['favorite_side_odds'])==2)
        age1=exact_age(d1,r['event_date']); age2=exact_age(d2,r['event_date'])
        fav_age=age1 if fav_is_f1 else age2; dog_age=age2 if fav_is_f1 else age1
        younger_adv=dog_age-fav_age
        if fav_is_f1: won=(r['result_1']=='W')
        else: won=(r['result_2']=='W')
        profit=american_profit(STAKE,o['favorite_decimal_odds']) if won else -STAKE
        rows.append({'event_date':pd.Timestamp(r['event_date']),'year':int(pd.Timestamp(r['event_date']).year),'event_name':r['event_name'],'fighter_1':r['fighter_1'],'fighter_2':r['fighter_2'],'favorite':r['fighter_1'] if fav_is_f1 else r['fighter_2'],'underdog':r['fighter_2'] if fav_is_f1 else r['fighter_1'],'favorite_age':fav_age,'underdog_age':dog_age,'younger_advantage':younger_adv,'market_prob':float(o['market_prob']),'favorite_decimal_odds':float(o['favorite_decimal_odds']),'won':bool(won),'profit':float(profit),'snapshot_mode':o['snapshot_mode'],'date_offset_days':delta,'fight_url_tidy':r['fight_url'],'fight_url_odds':o.get('fight_url')})
    df=pd.DataFrame(rows).drop_duplicates(subset=['event_date','fighter_1','fighter_2']).sort_values('event_date').reset_index(drop=True)
    df.to_csv(OUT/'matched_recent_fights.csv',index=False)
    pd.DataFrame(unmatched).to_csv(OUT/'unmatched_odds.csv',index=False)
    hybrid=df[(df.market_prob>=MARKET_MIN)&(df.younger_advantage>=GAP_MIN)].copy()
    strong=df[(df.market_prob>=MARKET_MIN)&(df.younger_advantage>=STRONG_GAP)].copy()
    hybrid.to_csv(OUT/'hybrid_3yr_bets.csv',index=False); strong.to_csv(OUT/'hybrid_4yr_bets.csv',index=False)

    def summ(d):
        rows=[]
        for y in sorted(d.year.unique()): rows.append({'segment':str(int(y)),**metrics(d[d.year==y])})
        rows.append({'segment':'2024-current',**metrics(d)})
        return pd.DataFrame(rows)
    s3=summ(hybrid); s4=summ(strong)
    s3.to_csv(OUT/'hybrid_3yr_summary.csv',index=False); s4.to_csv(OUT/'hybrid_4yr_summary.csv',index=False)

    lines=['UFC RECENT TIDYTUESDAY RECOVERY','= '*38,'',f'TidyTuesday completed UFC fights 2024-current: {len(fights)}',f'Odds fights: {len(odds)}',f'Matched odds+result+DOB fights: {len(df)}',f'Coverage vs TidyTuesday completed fights: {len(df)/len(fights)*100:.1f}%','','3+ YEAR HYBRID RULE','-'*76]
    for _,r in s3.iterrows():
        ci='n/a' if pd.isna(r.ci_low) else f'[{r.ci_low*100:+.2f}%, {r.ci_high*100:+.2f}%]'
        lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f} 95%CI={ci}')
    lines += ['','4+ YEAR STRONG','-'*76]
    for _,r in s4.iterrows():
        lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f}')
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n')
    print((OUT/'report.txt').read_text(),flush=True)

if __name__=='__main__': main()
