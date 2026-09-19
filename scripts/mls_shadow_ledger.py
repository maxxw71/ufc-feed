#!/usr/bin/env python3
from __future__ import annotations
import json,sqlite3
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SH=ROOT/'live/shadow';SH.mkdir(parents=True,exist_ok=True)
BOARD=SH/'current_shadow_board.json'
DB=SH/'prospective.sqlite3'
REPORT=SH/'forward_record.json'

def now():return datetime.now(timezone.utc).isoformat()
def conn():
    db=sqlite3.connect(DB,timeout=60)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript("""
    CREATE TABLE IF NOT EXISTS signals(
      signal_key TEXT PRIMARY KEY,
      method_id TEXT NOT NULL,
      method_name TEXT,
      event_id TEXT NOT NULL,
      commence_time TEXT,
      home_team TEXT,
      away_team TEXT,
      selection TEXT NOT NULL,
      opponent TEXT,
      side TEXT,
      first_seen_at TEXT NOT NULL,
      first_price_american REAL,
      first_price_decimal REAL,
      first_book TEXT,
      first_market_prob REAL,
      status TEXT NOT NULL DEFAULT 'pending',
      settled_at TEXT,
      home_score REAL,
      away_score REAL,
      profit_units REAL,
      result_note TEXT
    );
    CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY,
      signal_key TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      american_price REAL,
      decimal_price REAL,
      book TEXT,
      market_prob REAL,
      UNIQUE(signal_key,observed_at)
    );
    """)
    db.commit();return db

def ingest():
    if not BOARD.exists():raise RuntimeError('MLS shadow board missing')
    d=json.loads(BOARD.read_text())
    qs=d.get('qualifiers') or []
    inserted=0;observed=0
    with conn() as db:
        for q in qs:
            key=f"{q['method_id']}:{q['event_id']}"
            row=db.execute('select signal_key from signals where signal_key=?',(key,)).fetchone()
            if row is None:
                db.execute("""insert into signals(signal_key,method_id,method_name,event_id,commence_time,home_team,away_team,selection,opponent,side,
                    first_seen_at,first_price_american,first_price_decimal,first_book,first_market_prob,status)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
                    (key,q['method_id'],q.get('method_name'),str(q['event_id']),q.get('commence_time'),
                     q.get('home_team'),q.get('away_team'),q.get('selection'),q.get('opponent'),q.get('side'),
                     q.get('captured_at') or d.get('market_captured_at') or now(),q.get('american_price'),q.get('decimal_price'),
                     q.get('book'),q.get('market_prob')))
                inserted+=1
            ts=q.get('captured_at') or d.get('market_captured_at') or now()
            cur=db.execute("""insert or ignore into observations(signal_key,observed_at,american_price,decimal_price,book,market_prob)
                              values(?,?,?,?,?,?)""",
                           (key,ts,q.get('american_price'),q.get('decimal_price'),q.get('book'),q.get('market_prob')))
            observed+=cur.rowcount
        db.commit()
    return inserted,observed

def asa_completed():
    asa=AmericanSoccerAnalysis()
    g=asa.get_games(leagues='mls',season_name='2026')
    t=asa.get_teams(leagues='mls')
    if not isinstance(g,pd.DataFrame):g=pd.DataFrame(g)
    if not isinstance(t,pd.DataFrame):t=pd.DataFrame(t)
    tid=next((c for c in ['team_id','id'] if c in t.columns),None)
    tname=next((c for c in ['team_name','name'] if c in t.columns),None)
    mp={str(r[tid]):canon_team(r[tname]) for _,r in t.iterrows()}
    g['home_team']=g.home_team_id.astype(str).map(mp);g['away_team']=g.away_team_id.astype(str).map(mp)
    g['dt']=pd.to_datetime(g.date_time_utc,errors='coerce',utc=True)
    for c in ['home_score','away_score']:g[c]=pd.to_numeric(g[c],errors='coerce')
    return g[g.status.astype(str).str.lower().eq('fulltime') & g.home_score.notna() & g.away_score.notna()].copy()

def settle():
    g=asa_completed();settled=0
    with conn() as db:
        pending=db.execute("select * from signals where status='pending' order by commence_time").fetchall()
        for s in pending:
            kick=pd.to_datetime(s['commence_time'],errors='coerce',utc=True)
            if pd.isna(kick):continue
            z=g[(g.home_team.eq(s['home_team']))&(g.away_team.eq(s['away_team']))]
            if len(z):
                z=z[(z.dt-kick).abs()<=pd.Timedelta(hours=24)]
            if len(z)!=1:continue
            r=z.iloc[0];hs=float(r.home_score);as_=float(r.away_score)
            if s['side']=='HOME':won=hs>as_
            elif s['side']=='AWAY':won=as_>hs
            elif s['side']=='DRAW':won=hs==as_
            else:continue
            status='win' if won else 'loss'
            dec=float(s['first_price_decimal']) if s['first_price_decimal'] is not None else None
            profit=(dec-1.0) if won and dec else (-1.0 if not won else None)
            db.execute("""update signals set status=?,settled_at=?,home_score=?,away_score=?,profit_units=?,result_note=?
                          where signal_key=? and status='pending'""",
                       (status,now(),hs,as_,profit,f"{s['home_team']} {hs:g}-{as_:g} {s['away_team']}",s['signal_key']))
            settled+=1
        db.commit()
    return settled

def build_report():
    with conn() as db:
        rows=[dict(r) for r in db.execute('select * from signals order by first_seen_at,signal_key')]
    methods={}
    for r in rows:
        m=methods.setdefault(r['method_id'],{'signals':0,'pending':0,'settled':0,'wins':0,'losses':0,'units':0.0})
        m['signals']+=1
        if r['status']=='pending':m['pending']+=1
        else:
            m['settled']+=1
            if r['status']=='win':m['wins']+=1
            if r['status']=='loss':m['losses']+=1
            if r['profit_units'] is not None:m['units']+=float(r['profit_units'])
    for m in methods.values():
        m['win_rate']=m['wins']/m['settled'] if m['settled'] else None
        m['roi']=m['units']/m['settled'] if m['settled'] else None
    payload={'updated_at':now(),'shadow_only':True,'official_autopromotions':0,'methods':methods,'signals':rows}
    REPORT.write_text(json.dumps(payload,indent=2,default=str))
    return payload

def main():
    ins,obs=ingest();settled=settle();d=build_report()
    print(json.dumps({'new_signals':ins,'new_observations':obs,'settled_now':settled,
                      'total_signals':len(d['signals']),'method_forward':d['methods'],
                      'official_autopromotions':0},indent=2))
if __name__=='__main__':main()
