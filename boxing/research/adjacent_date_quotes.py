"""Conservative recovery of already-matched adjacent-date archival quotes."""
import datetime as dt
import json
from market_consensus import quote_signature
from features import namekey

def archive_times(rows):
    index = {}
    for row in rows:
        sig = quote_signature({**row, 'decimal_price': row.get('stored_decimal_price'),
                               'odds_bout_id': row.get('bout_id')})
        if sig is None:
            continue
        evidence = [row] + list(row.get('strict_validation_evidence') or [])
        for item in evidence:
            try:
                stamp = dt.datetime.strptime(item['snapshot_timestamp'], '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            index.setdefault(sig, []).append(stamp)
    return index

def matches_bout(quote, bout, verified_times):
    if quote.get('event_date') == bout['date']:
        return True  # Existing exact-date join remains unchanged.
    if quote.get('feature_bout_id') != bout['source_id']:
        return False
    if quote.get('match_method') != 'exact_pair_adjacent_date':
        return False
    if namekey(quote.get('selection')) != namekey(bout.get('boxer_a')):
        return False
    expected = 'WIN' if bout.get('winner') == 'BOXER A' else 'LOSS' if bout.get('winner') == 'BOXER B' else None
    if expected is None or quote.get('result') != expected:
        return False
    try:
        event = dt.date.fromisoformat(quote['event_date'])
        actual = dt.date.fromisoformat(bout['date'])
        offset = (actual - event).days
        if abs(offset) != 1 or quote.get('date_offset_days') != offset:
            return False
        sources = json.loads(quote.get('result_sources_json') or '[]')
        if not any(s.get('id') == bout['source_id'] and s.get('result_date') == bout['date'] for s in sources):
            return False
        # With no start time/timezone, use the earliest possible start of either
        # calendar day (UTC+14). Never move the original archive quote date.
        cutoff = dt.datetime.combine(min(event, actual), dt.time(), tzinfo=dt.timezone.utc) - dt.timedelta(hours=14)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return any(stamp < cutoff for stamp in verified_times.get(quote_signature(quote), []))
