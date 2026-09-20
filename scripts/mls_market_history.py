#!/usr/bin/env python3
from __future__ import annotations

import json,sqlite3
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SNAPS=ROOT/'live/market_snapshots'
LIVE=ROOT/'live/market_history';LIVE.mkdir(parents=True,exist_ok=True)
PROC=ROOT/'data/processed';REP=ROOT/'reports'
DB=LIVE/'market_history.sqlite3'
OBS=PROC/'mls_market_observations.parquet'
SUMMARY=PROC/'mls_market_event_history.parquet'
META=REP/'market_history_meta.json'

def now():return datetime.now(timezone.utc).isoformat()

def conn():
    db=sqlite3.connect(DB,timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute("""
    CREATE TABLE IF NOT EXISTS observations(
      event_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      captured_at TEXT NOT NULL,
      commence_time TEXT,
      home_team TEXT,
      away_team TEXT,
      home_american REAL,
      draw_american REAL,
      away_american REAL,
      home_odds REAL,
      draw_odds REAL,
      away_odds REAL,
      home_novig_prob REAL,
      draw_novig_prob REAL,
      away_novig_prob REAL,
      overround REAL,
      source_file TEXT,
      PRIMARY KEY(event_id,provider,captured_at)
    )""")
    db.commit();return db

def quote_rows(row,source_file):
    base={k:row.get(k) for k in ['event_id','captured_at','commence_time','home_team','away_team']}
    quotes=row.get('quotes')
    if not isinstance(quotes,list) or not quotes:
        quotes=[{k:row.get(k) for k in [
          'provider','home_american','draw_american','away_american',
          'home_odds','draw_odds','away_odds','home_novig_prob','draw_novig_prob','away_novig_prob','overround'
        ]}]
    out=[]
    for q in quotes:
        if not isinstance(q,dict):continue
        provider=str(q.get('provider') or row.get('provider') or 'Unknown')
        if q.get('home_odds') is None or q.get('draw_odds') is None or q.get('away_odds') is None:continue
        out.append({**base,'provider':provider,**{k:q.get(k) for k in [
          'home_american','draw_american','away_american','home_odds','draw_odds','away_odds',
          'home_novig_prob','draw_novig_prob','away_novig_prob','overround']},'source_file':source_file})
    return out

def ingest():
    files=sorted(p for p in SNAPS.glob('*.jsonl') if p.name!='latest.jsonl')
    ins=0
    with conn() as db:
        for p in files:
            for raw in p.read_text().splitlines():
                if not raw.strip():continue
                try:r=json.loads(raw)
                except Exception:continue
                for q in quote_rows(r,p.name):
                    cur=db.execute("""insert or ignore into observations(
                      event_id,provider,captured_at,commence_time,home_team,away_team,
                      home_american,draw_american,away_american,home_odds,draw_odds,away_odds,
                      home_novig_prob,draw_novig_prob,away_novig_prob,overround,source_file
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(q['event_id']),q['provider'],q['captured_at'],q['commence_time'],q['home_team'],q['away_team'],
                     q['home_american'],q['draw_american'],q['away_american'],q['home_odds'],q['draw_odds'],q['away_odds'],
                     q['home_novig_prob'],q['draw_novig_prob'],q['away_novig_prob'],q['overround'],q['source_file']))
                    ins+=cur.rowcount
        db.commit()
    return files,ins

def build():
    with conn() as db:
        d=pd.read_sql_query('select * from observations',db)
    if d.empty:
        pd.DataFrame().to_parquet(OBS,index=False)
        pd.DataFrame().to_parquet(SUMMARY,index=False)
        return d,pd.DataFrame()
    d['captured_at']=pd.to_datetime(d.captured_at,errors='coerce',utc=True)
    d['commence_time']=pd.to_datetime(d.commence_time,errors='coerce',utc=True)
    d=d[d.captured_at.notna()].sort_values(['event_id','provider','captured_at']).copy()
    d.to_parquet(OBS,index=False)
    rows=[]
    for (eid,provider),g in d.groupby(['event_id','provider']):
        g=g.sort_values('captured_at')
        kick=g.commence_time.dropna().iloc[0] if g.commence_time.notna().any() else pd.NaT
        pre=g[g.captured_at.le(kick)] if pd.notna(kick) else g
        first=g.iloc[0];last=g.iloc[-1];close=(pre.iloc[-1] if len(pre) else last)
        row={
          'event_id':str(eid),'provider':provider,'home_team':first.home_team,'away_team':first.away_team,
          'commence_time':None if pd.isna(kick) else kick,'observations':len(g),
          'opening_captured_at':first.captured_at,'latest_captured_at':last.captured_at,
          'closing_captured_at':close.captured_at,
        }
        for side in ['home','draw','away']:
            row[f'opening_{side}_american']=first[f'{side}_american']
            row[f'closing_{side}_american']=close[f'{side}_american']
            row[f'opening_{side}_odds']=first[f'{side}_odds']
            row[f'closing_{side}_odds']=close[f'{side}_odds']
            row[f'opening_{side}_novig_prob']=first[f'{side}_novig_prob']
            row[f'closing_{side}_novig_prob']=close[f'{side}_novig_prob']
            op=first[f'{side}_novig_prob'];cp=close[f'{side}_novig_prob']
            row[f'{side}_novig_prob_move']=float(cp-op) if pd.notna(op) and pd.notna(cp) else None
            oo=first[f'{side}_odds'];co=close[f'{side}_odds']
            row[f'{side}_odds_move']=float(co-oo) if pd.notna(oo) and pd.notna(co) else None
        row['opening_overround']=first.overround;row['closing_overround']=close.overround
        rows.append(row)
    s=pd.DataFrame(rows);s.to_parquet(SUMMARY,index=False)
    return d,s

def main():
    files,inserted=ingest();obs,s=build()
    providers=sorted(obs.provider.dropna().astype(str).unique()) if len(obs) else []
    meta={
      'built_at':now(),'snapshot_files_seen':len(files),'new_observations_inserted':inserted,
      'observation_rows':len(obs),'event_provider_series':len(s),
      'unique_events':int(obs.event_id.nunique()) if len(obs) else 0,'providers':providers,
      'observation_output':str(OBS),'event_history_output':str(SUMMARY),
      'definition':'Opening is first captured quote per event/provider. Closing is last captured quote at or before kickoff. Current/latest is retained in observations.',
      'clv_note':'For a recorded prospective signal, compare its first-bet decimal odds/no-vig probability to the matching provider closing quote. Positive CLV must be evaluated directionally by selection.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
