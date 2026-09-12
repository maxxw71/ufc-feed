from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import ufc_recent_daily_backfill as base

OUT = Path('ufc_recent_daily_backfill_v2')
OUT.mkdir(exist_ok=True)
STAKE = base.STAKE


def metrics(df):
    return base.metrics(df)


def exact_age(dob, event_date):
    return base.exact_age(dob, event_date)


def main():
    fights, dob = base.load_results_and_dobs()
    odds_raw = pd.read_csv(base.ODDS_PATH, low_memory=False)
    odds = base.choose_odds_snapshot(odds_raw)

    # Index completed results by exact normalized fighter pair. Date is matched
    # within ±2 days to absorb UTC/local-card date differences.
    pair_index = {}
    for _, r in fights.iterrows():
        pair_index.setdefault(r['pair'], []).append(r)

    rows = []
    unmatched = []
    date_offsets = []

    for _, o in odds.iterrows():
        candidates = pair_index.get(o['pair'], [])
        if not candidates:
            unmatched.append({**o.to_dict(), 'reason':'pair_not_found'})
            continue

        od = pd.Timestamp(o['event_date']).normalize()
        ranked = sorted(
            candidates,
            key=lambda r: abs((pd.Timestamp(r['event_date']).normalize() - od).days)
        )
        r = ranked[0]
        delta = int((pd.Timestamp(r['event_date']).normalize() - od).days)
        if abs(delta) > 2:
            unmatched.append({**o.to_dict(), 'reason':'date_diff_gt_2', 'closest_date_diff':delta})
            continue

        d1 = dob.get(r['f1_norm'])
        d2 = dob.get(r['f2_norm'])
        if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
            unmatched.append({**o.to_dict(), 'reason':'missing_dob'})
            continue

        direct = (o['f1_norm'] == r['f1_norm'] and o['f2_norm'] == r['f2_norm'])
        reverse = (o['f1_norm'] == r['f2_norm'] and o['f2_norm'] == r['f1_norm'])
        if not direct and not reverse:
            unmatched.append({**o.to_dict(), 'reason':'orientation_failed'})
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
        profit = base.american_profit(STAKE, o['favorite_decimal_odds']) if won else -STAKE

        rows.append({
            'event_date': pd.Timestamp(r['event_date']),
            'odds_event_date': od,
            'date_offset_days': delta,
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
        date_offsets.append(delta)

    df = pd.DataFrame(rows).drop_duplicates(subset=['event_date','fighter_1','fighter_2']).sort_values('event_date').reset_index(drop=True)
    df.to_csv(OUT/'matched_recent_fights.csv', index=False)
    pd.DataFrame(unmatched).to_csv(OUT/'unmatched_odds.csv', index=False)

    hybrid = df[(df['market_prob'] >= base.MARKET_MIN) & (df['younger_advantage'] >= base.GAP_MIN)].copy()
    strong = df[(df['market_prob'] >= base.MARKET_MIN) & (df['younger_advantage'] >= base.STRONG_GAP)].copy()
    hybrid.to_csv(OUT/'hybrid_3yr_bets.csv', index=False)
    strong.to_csv(OUT/'hybrid_4yr_bets.csv', index=False)

    def segment_summary(d):
        out = []
        for yr in sorted(d['year'].unique()):
            out.append({'segment':str(int(yr)), **metrics(d[d['year']==yr])})
        out.append({'segment':'2024-current', **metrics(d)})
        return pd.DataFrame(out)

    s3 = segment_summary(hybrid)
    s4 = segment_summary(strong)
    s3.to_csv(OUT/'hybrid_3yr_summary.csv', index=False)
    s4.to_csv(OUT/'hybrid_4yr_summary.csv', index=False)

    offsets = pd.Series(date_offsets).value_counts().sort_index().reset_index()
    offsets.columns = ['date_offset_days','fights']
    offsets.to_csv(OUT/'date_offset_counts.csv', index=False)

    lines = [
        'UFC 2024-CURRENT DAILY-ODDS HYBRID BACKFILL V2',
        '='*76,
        '',
        'Matching improvement:',
        '  exact normalized fighter pair',
        '  event date allowed within ±2 days for UTC/local timing differences',
        '',
        f'Completed UFC fights in result source (ex Road to UFC): {len(fights)}',
        f'Unique 2024-current odds fights: {len(odds)}',
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

    lines += ['', 'DATE-OFFSET MATCHES', '-'*76]
    for _, r in offsets.iterrows():
        lines.append(f"{int(r.date_offset_days):+d} day: {int(r.fights)} fights")

    report = OUT/'report.txt'
    report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(report.read_text(), flush=True)

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'completed_fights': len(fights),
        'odds_fights': len(odds),
        'matched_fights': len(df),
        'coverage_pct': len(df)/len(fights)*100,
        'hybrid_3yr': s3.to_dict(orient='records'),
        'hybrid_4yr': s4.to_dict(orient='records'),
        'date_offsets': offsets.to_dict(orient='records'),
    }
    (OUT/'summary.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
