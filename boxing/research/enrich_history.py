"""Source-specific, strictly earlier-bout statistics; never current profiles.

Counts describe observed histories, not certified complete careers. Terminal
rounds are rounds reached, not full rounds completed or minutes boxed.
"""
import collections
import bisect
import datetime as dt
import json
import re
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STOP = {'KO', 'TKO', 'RTD'}
DECISION = {'UD', 'SD', 'MD', 'PTS', 'TD'}


def rounds_value(text):
    text = re.sub(r'\[[^]]*\]', '', text or '').strip()
    m = re.fullmatch(r'(\d{1,2})(?:\s*\((\d{1,2})\))?(?:\s*,\s*\d{1,2}:\d{2})?', text)
    if not m:
        return None, None
    terminal, scheduled = int(m[1]), int(m[2]) if m[2] else None
    if not 1 <= terminal <= 20 or (scheduled is not None and not terminal <= scheduled <= 20):
        return None, None
    return terminal, scheduled


def stats(hist, date, opponent_id=None, links=None, histories=None, strength_by_id=None):
    rows = [r for r in hist if r['date'] < date]
    assert all(r['date'] < date for r in rows)
    day = dt.date.fromisoformat(date)
    ages = [(day - dt.date.fromisoformat(r['date'])).days for r in rows]
    out = {'observed_prior_bouts': len(rows), 'history_complete': False,
           'latest_input_bout_date': rows[-1]['date'] if rows else None,
           'days_since_first_observed_bout': max(ages) if ages else None,
           'prior_duplicate_date_opponent_rows': len(rows) - len({(r['date'], r['boxer_b']) for r in rows})}
    for days in (90, 180, 365, 730):
        out[f'bouts_last_{days}_days'] = sum(a <= days for a in ages)
    gaps = [(dt.date.fromisoformat(b['date']) - dt.date.fromisoformat(a['date'])).days
            for a, b in zip(rows, rows[1:])]
    out['median_gap_days_last_five_intervals'] = statistics.median(gaps[-5:]) if gaps else None
    out['maximum_prior_gap_days'] = max(gaps) if gaps else None
    for result, label in [('BOXER B', 'loss'), ('DRAW', 'draw')]:
        streak = 0
        for row in reversed(rows):
            if row['winner'] != result:
                break
            streak += 1
        out[label + '_streak'] = streak
    stops = [r for r in rows if r['winner'] == 'BOXER B' and r['method'].upper() in STOP]
    out['days_since_last_stoppage_loss'] = (day - dt.date.fromisoformat(stops[-1]['date'])).days if stops else None
    for n in (None, 3, 5, 10):
        group = rows if n is None else rows[-n:]
        prefix = 'career' if n is None else f'last{n}'
        out[prefix + '_sample_bouts'] = len(group)
        out[prefix + '_no_contests'] = sum(r['winner'] == 'NO CONTEST' for r in group)
        for methods, label in [(DECISION, 'decision'), ({'SD','MD'}, 'split_or_majority_decision'), (STOP, 'stoppage')]:
            for result, suffix in [('BOXER A', 'wins'), ('BOXER B', 'losses')]:
                out[f'{prefix}_{label}_{suffix}'] = sum(r['winner'] == result and r['method'].upper() in methods for r in group)
        parsed = [(r, *rounds_value(r['rounds'])) for r in group]
        known = [terminal for _, terminal, _ in parsed if terminal is not None]
        out[prefix + '_rounds_known_bouts'] = len(known)
        out[prefix + '_sum_terminal_rounds'] = sum(known) if known else None
        out[prefix + '_mean_terminal_round'] = statistics.mean(known) if known else None
        out[prefix + '_reached_round_9_bouts'] = sum(x >= 9 for x in known)
        out[prefix + '_scheduled_12plus_known_bouts'] = sum(s is not None and s >= 12 for _, _, s in parsed)
        out[prefix + '_early_stoppage_wins_rounds_1_3'] = sum(r['winner'] == 'BOXER A' and r['method'].upper() in STOP and t is not None and t <= 3 for r, t, _ in parsed)
    if links is not None:
        prior_pair = [r for r in rows if opponent_id and links.get(r['source_id']) == opponent_id]
        out['verified_prior_meetings'] = len(prior_pair)
        out['verified_prior_meeting_wins'] = sum(r['winner'] == 'BOXER A' for r in prior_pair)
        out['verified_prior_meeting_losses'] = sum(r['winner'] == 'BOXER B' for r in prior_pair)
        strengths = []
        for row in rows:
            if strength_by_id is not None:
                strength = strength_by_id.get(row['source_id'])
                if strength is not None:
                    strengths.append((strength, row['winner']))
                continue
            prior_opponent = links.get(row['source_id'])
            if prior_opponent not in histories:
                continue
            earlier = [r for r in histories[prior_opponent] if r['date'] < row['date']]
            wins = sum(r['winner'] == 'BOXER A' for r in earlier)
            losses = sum(r['winner'] == 'BOXER B' for r in earlier)
            if wins + losses >= 5:
                strengths.append((wins / (wins + losses), row['winner']))
        out['opponent_strength_known_bouts'] = len(strengths)
        out['mean_prior_opponent_observed_win_fraction'] = statistics.mean(x[0] for x in strengths) if strengths else None
        out['wins_over_opponents_with_observed_75pct_record'] = sum(p >= .75 and w == 'BOXER A' for p,w in strengths)
    return out


ALLOWED_CAREER_SOURCES=('wikipedia','champinon','wba_consensus')

def explicit_opponent_strength(row):
    try:data=json.loads(row.get('data') or '{}')
    except Exception:return None
    candidates=[
        data.get('opponent_record_at_bout'),
        data.get("opponent's pre-fight record"),
        data.get("opponent's record"),
        data.get('opp record'),
    ]
    for value in candidates:
        if isinstance(value,dict):
            try:w=int(value.get('wins',0));l=int(value.get('losses',0))
            except Exception:continue
        else:
            m=re.search(r'(\d+)\s*[–−-]\s*(\d+)(?:\s*[–−-]\s*(\d+))?',str(value or ''))
            if not m:continue
            w,l=int(m.group(1)),int(m.group(2))
        if w+l>=5:return w/(w+l)
    return None

def main():
    db = sqlite3.connect(ROOT / 'boxing.sqlite3', timeout=120)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    histories = collections.defaultdict(list)
    marks=','.join('?'*len(ALLOWED_CAREER_SOURCES))
    for row in db.execute(f"SELECT * FROM bouts WHERE source in ({marks}) AND status='FINISHED' ORDER BY date,source_id",ALLOWED_CAREER_SOURCES):
        histories[row['url']].append(dict(row))
    links = {r['bout_id']: r['opponent_id'] for r in db.execute("SELECT * FROM opponent_links WHERE evidence='reciprocal_result_confirmed'")}
    if db.execute("select 1 from sqlite_master where type='table' and name='opponent_links_v2'").fetchone():
        for r in db.execute("select bout_id,opponent_id from opponent_links_v2 where evidence='reciprocal_result_observed_alias'"):
            links.setdefault(r['bout_id'],r['opponent_id'])
    db.commit()
    # Prefix totals freeze each past opponent at the earlier meeting's date.
    # Reuse them rather than reconstructing that record for every later bout.
    indexes = {}
    for url, history in histories.items():
        wins, losses = [0], [0]
        for row in history:
            wins.append(wins[-1] + (row['winner'] == 'BOXER A'))
            losses.append(losses[-1] + (row['winner'] == 'BOXER B'))
        indexes[url] = ([r['date'] for r in history], wins, losses)
    strength_by_id = {}
    for history in histories.values():
        for row in history:
            index = indexes.get(links.get(row['source_id']))
            if index:
                dates, wins, losses = index
                pos = bisect.bisect_left(dates, row['date'])
                if wins[pos] + losses[pos] >= 5:
                    strength_by_id[row['source_id']] = wins[pos] / (wins[pos] + losses[pos])
                    continue
            x=explicit_opponent_strength(row)
            if x is not None:
                strength_by_id[row['source_id']]=x
    db.execute('''CREATE TABLE IF NOT EXISTS enriched_pre_bout(
        source_id TEXT PRIMARY KEY,bout_date TEXT,fighter_id TEXT,opponent_id TEXT,
        features_json TEXT,input_bout_ids_json TEXT,built_at TEXT)''')
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    years = collections.defaultdict(collections.Counter)
    # Stage a complete build before replacing the previous feature layer.
    db.execute('CREATE TEMP TABLE next_enriched AS SELECT * FROM enriched_pre_bout WHERE 0')
    for url, history in histories.items():
        for row in history:
            date = row['date']
            if date < '1990-01-01':
                continue
            opponent = links.get(row['source_id'])
            own = stats(history, date, opponent, links, histories, strength_by_id)
            other = stats(histories[opponent], date, url, links, histories, strength_by_id) if opponent in histories else None
            payload = {'fighter': own, 'opponent': other,
                       'quality': 'observed_history_only; reciprocal_opponent_links; not_complete_career_certification',
                       'rounds_definition': 'terminal round reached, not completed rounds or elapsed minutes'}
            inputs = {'fighter': [r['source_id'] for r in history if r['date'] < date],
                      'opponent': [r['source_id'] for r in histories.get(opponent, []) if r['date'] < date]}
            db.execute('INSERT INTO next_enriched VALUES(?,?,?,?,?,?,?)', (row['source_id'], date, url, opponent, json.dumps(payload), json.dumps(inputs), stamp))
            counts = years[date[:4]]
            counts['source_bout_rows'] += 1
            counts['verified_opponent_history'] += other is not None
            counts['both_have_5_prior_bouts'] += bool(other and min(own['observed_prior_bouts'],other['observed_prior_bouts']) >= 5)
            counts['has_prior_rounds'] += own['career_rounds_known_bouts'] > 0
            counts['has_prior_opponent_strength'] += own['opponent_strength_known_bouts'] > 0
        db.commit()
    db.execute('DELETE FROM enriched_pre_bout')
    db.execute('INSERT INTO enriched_pre_bout SELECT * FROM next_enriched')
    db.commit()
    report = {'built_at': stamp, 'per_fighter_fields': len(stats([], '2026-01-01', None, {}, {})),
              'career_sources': list(ALLOWED_CAREER_SOURCES), 'identity_links_used': len(links),
              'opponent_strength_rows': len(strength_by_id),
              'years': dict(sorted(years.items())), 'notes': ['Source observations overlap; not unique fights.', 'No current profile snapshots used.', 'Missing rounds stay unknown.', 'Both-five-prior is coverage, not research eligibility.', 'Opponent strength uses linked point-in-time histories first and source-stated pre-fight records second.']}
    (ROOT / 'ENRICHED_COVERAGE.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
