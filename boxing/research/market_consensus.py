"""Deterministic two-sided boxing market aggregation for research.

Archived quote timing/settlement remains unverified.  This module only removes
an arbitrary bookmaker-choice artifact from exploratory backtests by requiring
clean two-sided books and aggregating them symmetrically.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def _median(values):
    values=[float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(values) if values else None


def _fighter_win(row):
    outcome=(row.get('outcome') or {}).get('result')
    return outcome=='BOXER A' if outcome in ('BOXER A','BOXER B') else None


def load_master(path):
    path=Path(path)
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def canonical_market_rows(master_rows,min_books=2):
    """Return one consensus favorite row per canonical bout.

    Each bookmaker must expose exactly one quote for each fighter and both quotes
    must identify the same archived odds-bout id.  Ambiguous books are ignored.
    The consensus favorite is selected from the median no-vig probability across
    remaining books.  ROI fields use the median displayed decimal price; best
    displayed price is retained separately as an optimistic sensitivity bound.
    """
    groups=defaultdict(list)
    for row in master_rows:
        a=row.get('fighter') or {}; b=row.get('opponent') or {}
        if not row.get('canonical_verified_pair') or not a.get('id') or not b.get('id'):
            continue
        if not (a.get('record_totals_match') and b.get('record_totals_match')):
            continue
        if min((a.get('summary') or {}).get('observed_prior_bouts',0),(b.get('summary') or {}).get('observed_prior_bouts',0))<5:
            continue
        key=(row.get('bout_date'),*sorted([a['id'],b['id']]))
        groups[key].append(row)

    out=[]
    for key,rows in sorted(groups.items()):
        by_id=defaultdict(list)
        for row in rows:
            fid=(row.get('fighter') or {}).get('id')
            if fid:
                by_id[fid].append(row)
        if set(by_id)!=set(key[1:]) or any(len(v)!=1 for v in by_id.values()):
            continue
        side_rows={fid:vals[0] for fid,vals in by_id.items()}
        ids=sorted(side_rows)
        wins={fid:_fighter_win(side_rows[fid]) for fid in ids}
        if sorted(wins.values(),key=lambda x:(x is None,x))!=[False,True]:
            continue

        books=defaultdict(lambda:defaultdict(list))
        for fid,row in side_rows.items():
            for q in row.get('quotes') or []:
                if q.get('result') not in ('WIN','LOSS'):
                    continue
                try: price=float(q.get('decimal_price'))
                except (TypeError,ValueError): continue
                if not math.isfinite(price) or price<=1: continue
                book=str(q.get('bookmaker') or '').strip()
                if not book: continue
                books[book][fid].append(q)

        clean=[]
        for book,sides in sorted(books.items()):
            if set(sides)!=set(ids) or any(len(v)!=1 for v in sides.values()):
                continue
            q0,q1=sides[ids[0]][0],sides[ids[1]][0]
            if q0.get('odds_bout_id')!=q1.get('odds_bout_id'):
                continue
            d0,d1=float(q0['decimal_price']),float(q1['decimal_price'])
            raw0,raw1=1/d0,1/d1
            p0=raw0/(raw0+raw1)
            clean.append({'bookmaker':book,'p0':p0,'prices':{ids[0]:d0,ids[1]:d1},
                          'quote_rowids':{ids[0]:q0.get('quote_rowid'),ids[1]:q1.get('quote_rowid')},
                          'odds_bout_id':q0.get('odds_bout_id')})
        if len(clean)<min_books:
            continue

        p0=_median([b['p0'] for b in clean])
        if p0 is None or math.isclose(p0,.5,abs_tol=1e-12):
            continue
        fav=ids[0] if p0>.5 else ids[1]; dog=ids[1] if fav==ids[0] else ids[0]
        market_prob=p0 if fav==ids[0] else 1-p0
        fav_prices=[b['prices'][fav] for b in clean]
        med=_median(fav_prices); best=max(fav_prices); worst=min(fav_prices)
        fav_row, dog_row=side_rows[fav],side_rows[dog]
        win=bool(wins[fav])
        out.append({'key':key,'year':int(str(key[0])[:4]),'bout_date':key[0],
                    'favorite_id':fav,'opponent_id':dog,
                    'favorite':fav_row.get('fighter_name'),'opponent':dog_row.get('fighter_name'),
                    'market_prob':market_prob,'book_count':len(clean),
                    'median_price':med,'best_price':best,'worst_price':worst,
                    'price_range':best-worst,'win':win,
                    'profit_median':med-1 if win else -1.0,
                    'profit_best':best-1 if win else -1.0,
                    'books':[b['bookmaker'] for b in clean],
                    'favorite_row':fav_row,'opponent_row':dog_row})
    return out
