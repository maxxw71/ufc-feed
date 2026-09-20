from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OUT = Path('ufc_recent_daily_backfill')
OUT.mkdir(exist_ok=True)
DB_PATH = Path('/tmp/mma_extract/dataset_global_v3.duckdb')
ODDS_PATH = Path('/tmp/odds_extract/UFC_betting_odds.csv')
STAKE = 50.0
MARKET_MIN = 0.70
GAP_MIN = 3.0
STRONG_GAP = 4.0


def norm_name(s):
    s = str(s or '').lower().replace('’', "'").replace('-', ' ')
    s = re.sub(r'\b(jr|sr|ii|iii|iv)\b', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def implied_decimal(d):
    d = float(d)
    return 1.0 / d if d > 1 else np.nan


def american_profit(stake, decimal_odds):
    return stake * (float(decimal_odds) - 1.0)


def exact_age(dob, event_date):
    return (pd.Timestamp(event_date) - pd.Timestamp(dob)).days / 365.2425


def bootstrap_ci(profits, n_boot=10000, seed=42):
    arr = np.asarray(profits, dtype=float)
    if len(arr) < 10:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    vals = np.empty(n_boot)
    n = len(arr)
    for i in range(n_boot):
        sample = rng.choice(arr, size=n, replace=True)
        vals[i] = sample.sum() / (n * STAKE)
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def metrics(df):
    if df.empty:
        return {
            'bets':0,'wins':0,'win_rate':np.nan,'profit':0.0,'roi':np.nan,
            'ci_low':np.nan,'ci_high':np.nan,'avg_market_prob':np.nan,
            'avg_decimal_odds':np.nan,
        }
    profit = float(df['profit'].sum())
    lo, hi = bootstrap_ci(df['profit'].to_numpy())
    return {
        'bets': int(len(df)),
        'wins': int(df['won'].sum()),
        'win_rate': float(df['won'].mean()),
        'profit': profit,
        'roi': profit / (len(df) * STAKE),
        'ci_low': lo,
        'ci_high': hi,
        'avg_market_prob': float(df['market_prob'].mean()),
        'avg_decimal_odds': float(df['favorite_decimal_odds'].mean()),
    }


def load_results_and_dobs():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    fights = con.execute("""
        select event_name, event_date, fighter_1, fighter_2, winner_side, weight_class
        from fights_career_longitudinal
        where lower(organization) = 'ufc'
          and event_date >= DATE '2024-01-01'
          and event_date <= CURRENT_DATE
          and winner_side is not null
          and fighter_1 is not null and fighter_2 is not null
    """).fetchdf()
    fighters = con.execute("select fighter_name, dob from fighters_master where dob is not null").fetchdf()
    con.close()

    fights['event_date'] = pd.to_datetime(fights['event_date'], errors='coerce').dt.normalize()
    fights = fights[~fights['event_name'].astype(str).str.contains('Road to UFC', case=False, na=False)].copy()
    fights['f1_norm'] = fights['fighter_1'].map(norm_name)
    fights['f2_norm'] = fights['fighter_2'].map(norm_name)
    fights['pair'] = fights.apply(lambda r: tuple(sorted((r['f1_norm'], r['f2_norm']))), axis=1)

    fighters['dob'] = pd.to_datetime(fighters['dob'], errors='coerce')
    dob = {}
    for _, r in fighters.dropna(subset=['dob']).iterrows():
        dob.setdefault(norm_name(r['fighter_name']), pd.Timestamp(r['dob']))
    return fights, dob


def choose_odds_snapshot(odds):
    x = odds.copy()
    x['event_date'] = pd.to_datetime(x['event_date'], errors='coerce').dt.normalize()
    x['adding_date'] = pd.to_datetime(x['adding_date'], errors='coerce', utc=True)
    x['odds_1'] = pd.to_numeric(x['odds_1'], errors='coerce')
    x['odds_2'] = pd.to_numeric(x['odds_2'], errors='coerce')
    x = x.dropna(subset=['event_date','fighter_1','fighter_2','odds_1','odds_2'])
    x = x[(x['odds_1'] > 1.0) & (x['odds_2'] > 1.0)].copy()
    x = x[x['event_date'] >= pd.Timestamp('2024-01-01')].copy()

    # Prefer US-region quotes. If a fight has no US quote, keep all regions as fallback.
    x['region_norm'] = x['region'].fillna('').astype(str).str.lower()
    x['f1_norm'] = x['fighter_1'].map(norm_name)
    x['f2_norm'] = x['fighter_2'].map(norm_name)
    x['pair'] = x.apply(lambda r: tuple(sorted((r['f1_norm'], r['f2_norm']))), axis=1)
    x['fight_key'] = x['event_date'].astype(str) + '|' + x['pair'].astype(str)

    selected = []
    for key, g in x.groupby('fight_key', sort=False):
        us = g[g['region_norm'].eq('us')]
        if not us.empty:
            g = us

        # Strict point-in-time rule: recent daily snapshots must be captured
        # BEFORE the UTC start of the listed event date. Event-day snapshots can
        # already be in-play for Asia/Europe cards, so they are not research-safe.
        # Old rows imported long after the event are static historical imports and
        # may use the legacy fallback.
        event_date = g['event_date'].iloc[0]
        cutoff = pd.Timestamp(event_date, tz='UTC')
        timely = g[g['adding_date'].notna() & (g['adding_date'] < cutoff)]
        if not timely.empty:
            latest_time = timely['adding_date'].max()
            snap = timely[timely['adding_date'] == latest_time].copy()
            snapshot_mode = 'strict_pre_event_snapshot'
        else:
            late_import = g['adding_date'].notna() & (g['adding_date'] >= cutoff + pd.Timedelta(days=3))
            if late_import.all() and g['adding_date'].notna().any():
                latest_time = g['adding_date'].max()
                snap = g[g['adding_date'] == latest_time].copy()
                snapshot_mode = 'historical_import_fallback'
            else:
                # No safely pre-event quote. Missing is preferable to in-play leakage.
                continue

        # IMPORTANT: rows for the same fight may list the fighters in opposite
        # order across books/sources. Align every quote to one canonical orientation
        # BEFORE aggregating probabilities or prices. Mixing raw odds_1/odds_2 from
        # reversed rows can manufacture a fake extreme favorite.
        r0 = snap.iloc[0]
        ref1, ref2 = r0['f1_norm'], r0['f2_norm']
        direct = snap['f1_norm'].eq(ref1) & snap['f2_norm'].eq(ref2)
        reverse = snap['f1_norm'].eq(ref2) & snap['f2_norm'].eq(ref1)
        invalid = ~(direct | reverse)
        if invalid.any():
            snap = snap.loc[~invalid].copy()
            direct = snap['f1_norm'].eq(ref1) & snap['f2_norm'].eq(ref2)
            reverse = snap['f1_norm'].eq(ref2) & snap['f2_norm'].eq(ref1)
        if snap.empty:
            continue

        snap['aligned_odds_1'] = np.where(direct, snap['odds_1'], snap['odds_2']).astype(float)
        snap['aligned_odds_2'] = np.where(direct, snap['odds_2'], snap['odds_1']).astype(float)
        i1 = 1.0 / snap['aligned_odds_1']
        i2 = 1.0 / snap['aligned_odds_2']
        total = i1 + i2
        snap['nv1'] = i1 / total
        snap['nv2'] = i2 / total

        nv1 = float(snap['nv1'].median())
        nv2 = float(snap['nv2'].median())
        normtot = nv1 + nv2
        nv1, nv2 = nv1 / normtot, nv2 / normtot
        if nv1 >= nv2:
            favorite_side = 1
            market_prob = nv1
            best_decimal = float(snap['aligned_odds_1'].max())
        else:
            favorite_side = 2
            market_prob = nv2
            best_decimal = float(snap['aligned_odds_2'].max())
        selected.append({
            'fight_url': r0.get('fight_url'),
            'event_date': event_date,
            'fighter_1': r0['fighter_1'],
            'fighter_2': r0['fighter_2'],
            'f1_norm': r0['f1_norm'],
            'f2_norm': r0['f2_norm'],
            'pair': r0['pair'],
            'favorite_side_odds': favorite_side,
            'market_prob': market_prob,
            'favorite_decimal_odds': best_decimal,
            'snapshot_mode': snapshot_mode,
            'snapshot_rows': int(len(snap)),
            'orientation_reversed_rows': int(reverse.sum()),
            'orientation_invalid_rows': int(invalid.sum()) if 'invalid' in locals() else 0,
            'source_values': ';'.join(sorted(set(snap['source'].dropna().astype(str)))),
            'region_values': ';'.join(sorted(set(snap['region'].dropna().astype(str)))),
            'adding_date_selected': str(snap['adding_date'].max()),
        })

    return pd.DataFrame(selected)


def main():
    fights, dob = load_results_and_dobs()
    odds_raw = pd.read_csv(ODDS_PATH, low_memory=False)
    odds = choose_odds_snapshot(odds_raw)

    result_index = {}
    for _, r in fights.iterrows():
        result_index.setdefault((r['event_date'], r['pair']), []).append(r)

    rows = []
    unmatched = []
    for _, o in odds.iterrows():
        key = (pd.Timestamp(o['event_date']), o['pair'])
        candidates = result_index.get(key, [])
        if not candidates:
            unmatched.append(o.to_dict())
            continue
        r = candidates[0]
        d1 = dob.get(r['f1_norm'])
        d2 = dob.get(r['f2_norm'])
        if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
            continue

        # Orient odds fighter_1/2 to results fighter_1/2 by normalized names.
        direct = (o['f1_norm'] == r['f1_norm'] and o['f2_norm'] == r['f2_norm'])
        reverse = (o['f1_norm'] == r['f2_norm'] and o['f2_norm'] == r['f1_norm'])
        if not direct and not reverse:
            continue

        if direct:
            fav_is_result_f1 = int(o['favorite_side_odds']) == 1
        else:
            fav_is_result_f1 = int(o['favorite_side_odds']) == 2

        age1 = exact_age(d1, r['event_date'])
        age2 = exact_age(d2, r['event_date'])
        fav_age = age1 if fav_is_result_f1 else age2
        dog_age = age2 if fav_is_result_f1 else age1
        younger_adv = dog_age - fav_age
        won = (int(r['winner_side']) == 1) if fav_is_result_f1 else (int(r['winner_side']) == 2)
        profit = american_profit(STAKE, o['favorite_decimal_odds']) if won else -STAKE

        rows.append({
            'event_date': pd.Timestamp(r['event_date']),
            'year': int(pd.Timestamp(r['event_date']).year),
            'event_name': r['event_name'],
            'weight_class': r['weight_class'],
            'fighter_1': r['fighter_1'],
            'fighter_2': r['fighter_2'],
            'favorite': r['fighter_1'] if fav_is_result_f1 else r['fighter_2'],
            'underdog': r['fighter_2'] if fav_is_result_f1 else r['fighter_1'],
            'favorite_age': fav_age,
            'underdog_age': dog_age,
            'younger_advantage': younger_adv,
            'market_prob': float(o['market_prob']),
            'favorite_decimal_odds': float(o['favorite_decimal_odds']),
            'won': bool(won),
            'profit': float(profit),
            'snapshot_mode': o['snapshot_mode'],
            'snapshot_rows': o['snapshot_rows'],
            'source_values': o['source_values'],
            'region_values': o['region_values'],
            'adding_date_selected': o['adding_date_selected'],
            'fight_url': o['fight_url'],
        })

    df = pd.DataFrame(rows).drop_duplicates(subset=['event_date','fighter_1','fighter_2']).sort_values('event_date').reset_index(drop=True)
    df.to_csv(OUT/'matched_recent_fights.csv', index=False)
    pd.DataFrame(unmatched).to_csv(OUT/'unmatched_odds.csv', index=False)

    hybrid = df[(df['market_prob'] >= MARKET_MIN) & (df['younger_advantage'] >= GAP_MIN)].copy()
    strong = df[(df['market_prob'] >= MARKET_MIN) & (df['younger_advantage'] >= STRONG_GAP)].copy()
    hybrid.to_csv(OUT/'hybrid_3yr_bets.csv', index=False)
    strong.to_csv(OUT/'hybrid_4yr_bets.csv', index=False)

    def segment_summary(d):
        rows = []
        for yr in sorted(d['year'].unique()):
            rows.append({'segment':str(int(yr)), **metrics(d[d['year']==yr])})
        rows.append({'segment':'2024-current', **metrics(d)})
        return pd.DataFrame(rows)

    s3 = segment_summary(hybrid)
    s4 = segment_summary(strong)
    s3.to_csv(OUT/'hybrid_3yr_summary.csv', index=False)
    s4.to_csv(OUT/'hybrid_4yr_summary.csv', index=False)

    # Market probability bands for recent 3yr rule.
    bands = []
    b = hybrid.copy()
    b['market_band'] = pd.cut(b['market_prob'], [.70,.75,.80,.85,.90,.95,1.001], right=False,
                              labels=['70-74.9%','75-79.9%','80-84.9%','85-89.9%','90-94.9%','95%+'])
    for band, g in b.groupby('market_band', observed=True):
        bands.append({'market_band':str(band), **metrics(g)})
    pd.DataFrame(bands).to_csv(OUT/'market_band_summary.csv', index=False)

    # Snapshot mode diagnostic.
    modes = df.groupby('snapshot_mode').size().reset_index(name='fights')
    modes.to_csv(OUT/'snapshot_mode_counts.csv', index=False)

    lines = [
        'UFC 2024-CURRENT DAILY-ODDS HYBRID BACKFILL',
        '='*76,
        '',
        'Rule unchanged:',
        '  no-vig market favorite >=70%',
        '  AND favorite >=3 years younger',
        '  >=4 years younger = strong tier',
        '',
        f'Completed UFC fights in result source (ex Road to UFC): {len(fights)}',
        f'Unique 2024-current odds fights in daily dataset: {len(odds)}',
        f'Fight-level odds+result+DOB matches: {len(df)}',
        f'Coverage of completed result fights: {len(df)/len(fights)*100:.1f}%',
        f'Matched date range: {df.event_date.min().date()} to {df.event_date.max().date()}',
        '',
        '3+ YEAR HYBRID RULE',
        '-'*76,
    ]
    for _, r in s3.iterrows():
        wr = 'n/a' if pd.isna(r.win_rate) else f'{r.win_rate*100:.2f}%'
        roi = 'n/a' if pd.isna(r.roi) else f'{r.roi*100:+.2f}%'
        ci = 'n/a' if pd.isna(r.ci_low) else f'[{r.ci_low*100:+.2f}%, {r.ci_high*100:+.2f}%]'
        lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={wr:>8} ROI={roi:>9} P/L=${r.profit:+,.2f} 95%CI={ci}')

    lines += ['', '4+ YEAR STRONG TIER', '-'*76]
    for _, r in s4.iterrows():
        wr = 'n/a' if pd.isna(r.win_rate) else f'{r.win_rate*100:.2f}%'
        roi = 'n/a' if pd.isna(r.roi) else f'{r.roi*100:+.2f}%'
        ci = 'n/a' if pd.isna(r.ci_low) else f'[{r.ci_low*100:+.2f}%, {r.ci_high*100:+.2f}%]'
        lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={wr:>8} ROI={roi:>9} P/L=${r.profit:+,.2f} 95%CI={ci}')

    lines += ['', 'ODDS SNAPSHOT MODES', '-'*76]
    for _, r in modes.iterrows():
        lines.append(f'{r.snapshot_mode}: {int(r.fights)} fights')

    lines += [
        '',
        'DATA NOTE',
        '-'*76,
        'For recent daily snapshots, only quotes captured before 00:00 UTC on the',
        'listed event date are eligible. Event-day/in-play snapshots are excluded.',
        'Rows imported >=3 days after the event may use the historical-import fallback.',
        'No-vig favorite probability is'
        'computed from the selected odds pair; if multiple same-time rows exist,',
        'median no-vig probability is used and the best decimal favorite price is used',
        'for ROI.',
    ]

    report = OUT/'report.txt'
    report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(report.read_text(), flush=True)

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'completed_fights': len(fights),
        'odds_fights': len(odds),
        'matched_fights': len(df),
        'coverage_pct': len(df)/len(fights)*100,
        'date_start': str(df.event_date.min().date()),
        'date_end': str(df.event_date.max().date()),
        'hybrid_3yr': s3.to_dict(orient='records'),
        'hybrid_4yr': s4.to_dict(orient='records'),
        'snapshot_modes': modes.to_dict(orient='records'),
    }
    (OUT/'summary.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
