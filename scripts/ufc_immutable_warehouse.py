#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path.home() / 'ufc-predictor-v1'
AR = ROOT / 'auto_research'
RAW = AR / 'raw'
STATE = AR / 'state'
WAREHOUSE = AR / 'warehouse'
DB = STATE / 'immutable_warehouse.sqlite3'
SOURCE = RAW / 'ufcstats_competitions_current.csv'
APPWIZA = Path('/srv/appwiza-sports/state/ufc.json')

for p in (STATE, WAREHOUSE):
    p.mkdir(parents=True, exist_ok=True)


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm(v):
    if pd.isna(v):
        return ''
    return str(v).strip()


def row_digest(row):
    payload = {str(k): norm(v) for k, v in row.items()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def detect_date_col(columns):
    low = {c.lower(): c for c in columns}
    for name in ('event_date', 'date', 'fight_date'):
        if name in low:
            return low[name]
    return None


def stable_key(row, date_col):
    low = {str(k).lower(): k for k in row.index}
    parts = []
    if date_col:
        parts.append(norm(row.get(date_col)))
    for group in (
        ('event', 'event_name', 'event_title'),
        ('fighter_1', 'fighter1', 'player1', 'red_fighter', 'fighter_a'),
        ('fighter_2', 'fighter2', 'player2', 'blue_fighter', 'fighter_b'),
        ('round', 'round_number'),
    ):
        for name in group:
            if name in low:
                parts.append(norm(row.get(low[name])))
                break
    if len(parts) >= 3:
        return '|'.join(parts).lower()
    # Safe fallback: first-seen full-row hash. This can never overwrite an old row.
    return 'rowhash:' + row_digest(row)


def db():
    c = sqlite3.connect(DB)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS canonical_rows(
      source TEXT NOT NULL,
      stable_key TEXT NOT NULL,
      first_seen TEXT NOT NULL,
      event_date TEXT,
      row_sha TEXT NOT NULL,
      row_json TEXT NOT NULL,
      PRIMARY KEY(source, stable_key)
    );
    CREATE TABLE IF NOT EXISTS revisions(
      id INTEGER PRIMARY KEY,
      source TEXT NOT NULL,
      stable_key TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      canonical_sha TEXT,
      proposed_sha TEXT NOT NULL,
      proposed_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending_review',
      UNIQUE(source, stable_key, proposed_sha)
    );
    CREATE TABLE IF NOT EXISTS ingest_runs(
      id INTEGER PRIMARY KEY,
      observed_at TEXT NOT NULL,
      source TEXT NOT NULL,
      new_rows INTEGER NOT NULL,
      unchanged_rows INTEGER NOT NULL,
      revisions INTEGER NOT NULL,
      rejected_old_unknown INTEGER NOT NULL,
      note TEXT
    );
    CREATE TABLE IF NOT EXISTS appwiza_snapshots(
      sha TEXT PRIMARY KEY,
      captured_at TEXT NOT NULL,
      updated_at TEXT,
      cards INTEGER,
      payload_json TEXT NOT NULL
    );
    ''')
    c.commit()
    return c


def ingest_ufcstats():
    if not SOURCE.exists():
        return {'status': 'missing_source'}
    frame = pd.read_csv(SOURCE, low_memory=False)
    date_col = detect_date_col(frame.columns)
    parsed_dates = pd.to_datetime(frame[date_col], errors='coerce') if date_col else None

    with db() as c:
        existing = {k: (sha, ed) for k, sha, ed in c.execute(
            "SELECT stable_key,row_sha,event_date FROM canonical_rows WHERE source='ufcstats'"
        )}
        max_date = None
        vals = [pd.to_datetime(v, errors='coerce') for _, v in existing.values() if v]
        vals = [v for v in vals if not pd.isna(v)]
        if vals:
            max_date = max(vals)

        # First run freezes the entire current source as baseline.
        bootstrap = not existing
        new_rows = unchanged = revisions = rejected = 0
        now = utcnow()
        for idx, row in frame.iterrows():
            key = stable_key(row, date_col)
            sha = row_digest(row)
            ed = norm(row.get(date_col)) if date_col else ''
            row_json = json.dumps({str(k): norm(v) for k, v in row.items()}, sort_keys=True)
            if key in existing:
                old_sha, _ = existing[key]
                if old_sha == sha:
                    unchanged += 1
                else:
                    c.execute('''INSERT OR IGNORE INTO revisions
                      (source,stable_key,observed_at,canonical_sha,proposed_sha,proposed_json,status)
                      VALUES(?,?,?,?,?,?,?)''',
                      ('ufcstats', key, now, old_sha, sha, row_json, 'pending_review'))
                    revisions += 1
                continue

            row_dt = parsed_dates.iloc[idx] if parsed_dates is not None else pd.NaT
            allow_append = bootstrap or max_date is None or (not pd.isna(row_dt) and row_dt > max_date)
            if allow_append:
                c.execute('''INSERT INTO canonical_rows
                  (source,stable_key,first_seen,event_date,row_sha,row_json)
                  VALUES(?,?,?,?,?,?)''', ('ufcstats', key, now, ed, sha, row_json))
                new_rows += 1
            else:
                c.execute('''INSERT OR IGNORE INTO revisions
                  (source,stable_key,observed_at,canonical_sha,proposed_sha,proposed_json,status)
                  VALUES(?,?,?,?,?,?,?)''',
                  ('ufcstats', key, now, None, sha, row_json, 'old_unknown_pending_review'))
                rejected += 1

        c.execute('''INSERT INTO ingest_runs
          (observed_at,source,new_rows,unchanged_rows,revisions,rejected_old_unknown,note)
          VALUES(?,?,?,?,?,?,?)''',
          (now, 'ufcstats', new_rows, unchanged, revisions, rejected,
           'Existing canonical rows are immutable; only strictly newer event dates auto-append.'))
        c.commit()

        pd.read_sql_query("SELECT * FROM canonical_rows WHERE source='ufcstats'", c).to_csv(WAREHOUSE/'ufcstats_canonical.csv', index=False)
        pd.read_sql_query("SELECT * FROM revisions WHERE source='ufcstats' ORDER BY id", c).to_csv(WAREHOUSE/'ufcstats_pending_revisions.csv', index=False)

    return {'status':'ok','bootstrap':bootstrap,'new_rows':new_rows,'unchanged':unchanged,'revisions':revisions,'rejected_old_unknown':rejected}


def snapshot_appwiza():
    if not APPWIZA.exists():
        return {'status':'missing_appwiza_state'}
    raw = APPWIZA.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    cards = payload.get('cards', []) if isinstance(payload, dict) else []
    with db() as c:
        c.execute('''INSERT OR IGNORE INTO appwiza_snapshots
          (sha,captured_at,updated_at,cards,payload_json) VALUES(?,?,?,?,?)''',
          (sha, utcnow(), payload.get('updated_at') if isinstance(payload,dict) else None,
           len(cards) if isinstance(cards,list) else None, raw.decode('utf-8')))
        c.commit()
    snap = WAREHOUSE / f'appwiza_ufc_{sha[:12]}.json'
    if not snap.exists():
        snap.write_bytes(raw)
    return {'status':'ok','sha':sha,'cards':len(cards) if isinstance(cards,list) else None}


def main():
    u = ingest_ufcstats()
    a = snapshot_appwiza()
    print(json.dumps({'ufcstats':u,'appwiza':a}, indent=2))

if __name__ == '__main__':
    main()
