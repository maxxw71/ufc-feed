#!/usr/bin/env python3
"""Frozen prospective UFC shadow tracker.

This tracker is deliberately isolated from the official U1-U12 selection path.
It freezes the first pre-event market favorite that qualifies, records every
qualifier prospectively, deduplicates overlapping rules to one underlying pick,
and settles only from completed UFCStats history.

Rule thresholds are frozen as of 2026-09-24 and must not be tuned from future
results. Any later rule change requires a new rule id/version.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/ufc-predictor-v1')
STATE=ROOT/'auto_research'/'state'
PDB=STATE/'prospective_ufc.sqlite3'
DB=STATE/'shadow_tracker.sqlite3'
RAW=ROOT/'raw'/'competitions.csv'
FEATURE=ROOT/'ufc_feature_expansion.py'
PUBLIC=Path('/srv/appwiza-sports/public/ufc/shadow')
VERSION='UFC_SHADOW_V1_20260924'
FROZEN_AT='2026-09-24T00:00:00-04:00'

RULES=[
  {
    'id':'S1','role':'primary',
    'name':'Strong-opponent striking + KO-loss gap ≥ -16.5d',
    'conditions':[
      ['f2_diff_strong_opp_sig_diff','>=',2.20154346],
      ['f2_diff_days_since_ko_loss','>=',-16.5],
    ],
    'historical':{'bets':59,'wins':45,'losses':14,'roi':0.1465319134308074,'holdout_roi':0.2608845483034144},
  },
  {
    'id':'S2','role':'primary',
    'name':'Strong-opponent striking + KO-loss gap ≥ 105d',
    'conditions':[
      ['f2_diff_strong_opp_sig_diff','>=',2.20154346],
      ['f2_diff_days_since_ko_loss','>=',105.0],
    ],
    'historical':{'bets':49,'wins':38,'losses':11,'roi':0.15176688264261085,'holdout_roi':0.2608845483034144},
  },
  {
    'id':'C1','role':'control',
    'name':'Strong-opponent striking ≥ 2.623 control',
    'conditions':[['f2_diff_strong_opp_sig_diff','>=',2.62273719]],
    'historical':{'bets':177,'wins':132,'losses':45,'roi':0.08575930084344707,'holdout_roi':0.11772473338982879},
  },
  {
    'id':'C2','role':'control',
    'name':'Strong-opponent striking ≥ 2.657 control',
    'conditions':[['f2_diff_strong_opp_sig_diff','>=',2.65722537]],
    'historical':{'bets':176,'wins':132,'losses':44,'roi':0.08101929687096666,'holdout_roi':0.10229636287809472},
  },
]
RULE_BY_ID={r['id']:r for r in RULES}

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def finite(v):
    try:return math.isfinite(float(v))
    except:return False

def american(dec):
    try:x=float(dec)
    except:return None
    if not math.isfinite(x) or x<=1:return None
    return 100*(x-1) if x>=2 else -100/(x-1)

def connect():
    STATE.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS shadow_rules(
      rule_id TEXT PRIMARY KEY,version TEXT NOT NULL,role TEXT NOT NULL,name TEXT NOT NULL,
      frozen_at TEXT NOT NULL,conditions_json TEXT NOT NULL,historical_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS shadow_scans(
      id INTEGER PRIMARY KEY,scanned_at TEXT NOT NULL,event_date TEXT NOT NULL,event_start TEXT,
      fighter_a TEXT NOT NULL,fighter_b TEXT NOT NULL,market_favorite TEXT,market_opponent TEXT,
      quote_decimal REAL,quote_book TEXT,quote_at TEXT,quote_captured_at TEXT,
      strong_opp_sig_diff REAL,days_since_ko_loss_diff REAL,qualified_rule_ids TEXT NOT NULL,
      qualified INTEGER NOT NULL,reason TEXT
    );
    CREATE TABLE IF NOT EXISTS shadow_picks(
      fight_key TEXT PRIMARY KEY,version TEXT NOT NULL,event_date TEXT NOT NULL,event_start TEXT,
      fighter_a TEXT NOT NULL,fighter_b TEXT NOT NULL,selection TEXT NOT NULL,opponent TEXT NOT NULL,
      quote_decimal REAL,quote_american REAL,quote_book TEXT,quote_at TEXT,quote_captured_at TEXT,
      first_recorded_at TEXT NOT NULL,strong_opp_sig_diff REAL,days_since_ko_loss_diff REAL,
      rule_ids_json TEXT NOT NULL,primary_rule_ids_json TEXT NOT NULL,control_rule_ids_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',result_settled_at TEXT,profit_units REAL,result_source TEXT,
      market_flip_observed INTEGER NOT NULL DEFAULT 0,last_scan_at TEXT
    );
    ''')
    for r in RULES:
        c.execute('''INSERT OR REPLACE INTO shadow_rules
          (rule_id,version,role,name,frozen_at,conditions_json,historical_json)
          VALUES(?,?,?,?,?,?,?)''',
          (r['id'],VERSION,r['role'],r['name'],FROZEN_AT,json.dumps(r['conditions'],sort_keys=True),json.dumps(r['historical'],sort_keys=True)))
    c.commit()
    return c

def load_feature_module():
    if not FEATURE.exists():
        raise RuntimeError(f'missing feature engine: {FEATURE}')
    spec=importlib.util.spec_from_file_location('shadow_feature_engine',FEATURE)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for n in ('State','update_fight','norm'):
        if not hasattr(m,n):raise RuntimeError(f'feature engine missing {n}')
    return m

def load_raw():
    if not RAW.exists():raise RuntimeError(f'missing canonical raw history: {RAW}')
    d=pd.read_csv(RAW,low_memory=False)
    d['_date']=pd.to_datetime(d['event_date'],errors='coerce').dt.normalize()
    d=d[d['_date'].notna()].sort_values(['_date','event_url','player1','player2'],kind='stable').reset_index(drop=True)
    return d

def latest_quotes():
    if not PDB.exists():raise RuntimeError(f'missing prospective market DB: {PDB}')
    now=pd.Timestamp.now(tz='UTC')
    with sqlite3.connect(PDB) as c:
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute('SELECT * FROM bout_quotes ORDER BY captured_at,id')]
    groups={}
    for r in rows:
        a,b=norm(r.get('fighter_a')),norm(r.get('fighter_b'))
        date=str(r.get('event_date') or '')[:10]
        if not a or not b or not date:continue
        cap=pd.to_datetime(r.get('captured_at'),utc=True,errors='coerce')
        start=pd.to_datetime(r.get('event_start'),utc=True,errors='coerce')
        if pd.isna(cap):continue
        if pd.notna(start):
            if cap>=start or start<=now:continue
        else:
            ed=pd.to_datetime(date,utc=True,errors='coerce')
            if pd.isna(ed) or ed.normalize()<now.normalize():continue
        if not str(r.get('book') or '').strip():continue
        key=(date,)+tuple(sorted((a,b)))
        groups[key]=r
    return groups

def favorite_from_quote(r):
    pa=float(r['p_a']) if finite(r.get('p_a')) else np.nan
    pb=float(r['p_b']) if finite(r.get('p_b')) else np.nan
    da=float(r['dec_a']) if finite(r.get('dec_a')) else np.nan
    db=float(r['dec_b']) if finite(r.get('dec_b')) else np.nan
    if np.isfinite(pa) and np.isfinite(pb) and pa!=pb:
        fav_a=pa>pb
    elif np.isfinite(da) and np.isfinite(db) and da!=db:
        fav_a=da<db
    else:
        return None
    fav=str(r['fighter_a'] if fav_a else r['fighter_b']).strip()
    opp=str(r['fighter_b'] if fav_a else r['fighter_a']).strip()
    dec=da if fav_a else db
    if not np.isfinite(dec) or dec<=1:return None
    return fav,opp,float(dec)

def states_before(raw,target_date,fe):
    target=pd.Timestamp(target_date)
    states=defaultdict(fe.State)
    past=raw[raw['_date']<target]
    for (_,event_url),grp in past.groupby(['_date','event_url'],sort=True,dropna=False):
        for _,r in grp.iterrows():
            fe.update_fight(r,states)
    return states

def feature_pair(states,fav,opp,target_date):
    fs=states.get(norm(fav));os=states.get(norm(opp))
    if fs is None or os is None:return None
    dt=pd.Timestamp(target_date)
    a=fs.snap(dt);b=os.snap(dt)
    sa=a.get('strong_opp_sig_diff');sb=b.get('strong_opp_sig_diff')
    ka=a.get('days_since_ko_loss');kb=b.get('days_since_ko_loss')
    strong=float(sa)-float(sb) if finite(sa) and finite(sb) else np.nan
    ko=float(ka)-float(kb) if finite(ka) and finite(kb) else np.nan
    return {'f2_diff_strong_opp_sig_diff':strong,'f2_diff_days_since_ko_loss':ko}

def qualifies(rule,features):
    for col,op,threshold in rule['conditions']:
        v=features.get(col)
        if not finite(v):return False
        if op=='>=' and not float(v)>=float(threshold):return False
        if op=='<=' and not float(v)<=float(threshold):return False
    return True

def fight_key(date,a,b):
    return date+'|'+ '|'.join(sorted((norm(a),norm(b))))

def scan():
    fe=load_feature_module();raw=load_raw();quotes=latest_quotes();stamp=utcnow()
    by_date={}
    for key,r in quotes.items():by_date.setdefault(key[0],[]).append((key,r))
    inserted=0;scanned=0
    with connect() as c:
        for date in sorted(by_date):
            states=states_before(raw,date,fe)
            for key,r in by_date[date]:
                favinfo=favorite_from_quote(r)
                if not favinfo:continue
                fav,opp,dec=favinfo
                features=feature_pair(states,fav,opp,date)
                if not features:
                    reason='missing fighter history'
                    tags=[]
                else:
                    tags=[rule['id'] for rule in RULES if qualifies(rule,features)]
                    reason='qualified' if tags else 'no frozen rule matched'
                scanned+=1
                c.execute('''INSERT INTO shadow_scans
                  (scanned_at,event_date,event_start,fighter_a,fighter_b,market_favorite,market_opponent,
                   quote_decimal,quote_book,quote_at,quote_captured_at,strong_opp_sig_diff,days_since_ko_loss_diff,
                   qualified_rule_ids,qualified,reason)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (stamp,date,r.get('event_start'),r.get('fighter_a'),r.get('fighter_b'),fav,opp,dec,r.get('book'),
                   r.get('quote_at'),r.get('captured_at'),
                   features.get('f2_diff_strong_opp_sig_diff') if features else None,
                   features.get('f2_diff_days_since_ko_loss') if features else None,
                   json.dumps(tags),int(bool(tags)),reason))
                if not tags:continue
                fk=fight_key(date,r.get('fighter_a'),r.get('fighter_b'))
                existing=c.execute('SELECT selection,rule_ids_json FROM shadow_picks WHERE fight_key=?',(fk,)).fetchone()
                if existing:
                    flip=int(norm(existing['selection'])!=norm(fav))
                    if flip:
                        c.execute('UPDATE shadow_picks SET market_flip_observed=1,last_scan_at=? WHERE fight_key=?',(stamp,fk))
                    else:
                        old=set(json.loads(existing['rule_ids_json'] or '[]'));new=sorted(old|set(tags))
                        prim=[x for x in new if RULE_BY_ID[x]['role']=='primary'];ctrl=[x for x in new if RULE_BY_ID[x]['role']=='control']
                        c.execute('''UPDATE shadow_picks SET rule_ids_json=?,primary_rule_ids_json=?,control_rule_ids_json=?,last_scan_at=?
                                     WHERE fight_key=?''',(json.dumps(new),json.dumps(prim),json.dumps(ctrl),stamp,fk))
                    continue
                prim=[x for x in tags if RULE_BY_ID[x]['role']=='primary'];ctrl=[x for x in tags if RULE_BY_ID[x]['role']=='control']
                c.execute('''INSERT INTO shadow_picks
                  (fight_key,version,event_date,event_start,fighter_a,fighter_b,selection,opponent,
                   quote_decimal,quote_american,quote_book,quote_at,quote_captured_at,first_recorded_at,
                   strong_opp_sig_diff,days_since_ko_loss_diff,rule_ids_json,primary_rule_ids_json,control_rule_ids_json,
                   status,last_scan_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (fk,VERSION,date,r.get('event_start'),r.get('fighter_a'),r.get('fighter_b'),fav,opp,dec,american(dec),
                   r.get('book'),r.get('quote_at'),r.get('captured_at'),stamp,
                   features.get('f2_diff_strong_opp_sig_diff'),features.get('f2_diff_days_since_ko_loss'),
                   json.dumps(tags),json.dumps(prim),json.dumps(ctrl),'pending',stamp))
                inserted+=1
        c.commit()
    print(json.dumps({'scan':'ok','scanned_bouts':scanned,'new_shadow_picks':inserted,'quote_bouts':len(quotes),'at':stamp}))
    return inserted

def settle():
    raw=load_raw();settled=0;stamp=utcnow()
    with connect() as c:
        pending=[dict(r) for r in c.execute("SELECT * FROM shadow_picks WHERE status='pending' ORDER BY event_date")]
        for p in pending:
            date=pd.Timestamp(p['event_date'])
            pair={norm(p['fighter_a']),norm(p['fighter_b'])}
            candidates=[]
            for _,r in raw[raw['_date']==date].iterrows():
                if {norm(r.get('player1')),norm(r.get('player2'))}==pair:
                    candidates.append(r)
            if len(candidates)!=1:continue
            r=candidates[0];sel=norm(p['selection']);p1=norm(r.get('player1'));p2=norm(r.get('player2'));res=str(r.get('result') or '').strip().upper()
            if sel==p1:
                win=res.startswith('W');loss=res.startswith('L')
            elif sel==p2:
                win=res.startswith('L');loss=res.startswith('W')
            else:continue
            if not (win or loss):continue
            status='win' if win else 'loss';dec=float(p['quote_decimal']) if finite(p.get('quote_decimal')) else np.nan
            profit=(dec-1.0) if win and np.isfinite(dec) else (-1.0 if loss else None)
            c.execute('''UPDATE shadow_picks SET status=?,result_settled_at=?,profit_units=?,result_source=?
                         WHERE fight_key=?''',(status,stamp,profit,'UFCStats canonical completed result',p['fight_key']))
            settled+=1
        c.commit()
    print(json.dumps({'settle':'ok','newly_settled':settled,'at':stamp}))
    return settled

def summary():
    with connect() as c:
        picks=[dict(r) for r in c.execute('SELECT * FROM shadow_picks ORDER BY event_date,first_recorded_at')]
    def stats(rows):
        done=[x for x in rows if x['status'] in ('win','loss')]
        w=sum(x['status']=='win' for x in done);l=sum(x['status']=='loss' for x in done)
        priced=[x for x in done if x.get('profit_units') is not None]
        units=sum(float(x['profit_units']) for x in priced)
        return {'picks':len(rows),'settled':len(done),'wins':w,'losses':l,'win_rate':w/(w+l) if w+l else None,
                'profit_units':units,'roi':units/len(priced) if priced else None}
    rules=[]
    for rule in RULES:
        rr=[p for p in picks if rule['id'] in json.loads(p['rule_ids_json'] or '[]')]
        rules.append({**rule,'prospective':stats(rr)})
    primary=[p for p in picks if json.loads(p['primary_rule_ids_json'] or '[]')]
    control_only=[p for p in picks if not json.loads(p['primary_rule_ids_json'] or '[]') and json.loads(p['control_rule_ids_json'] or '[]')]
    return {
      'generated_at':utcnow(),'version':VERSION,'frozen_at':FROZEN_AT,
      'policy':'Research/shadow only. Every qualifying fight is recorded before the event. First qualifying market favorite and price are frozen. Overlapping rules are one underlying pick with multiple tags. No official U1-U12 selection, official record, or official alert is created.',
      'overall_unique':stats(picks),'primary_unique':stats(primary),'control_only_unique':stats(control_only),
      'rules':rules,'picks':picks,
    }

def publish():
    out=summary();PUBLIC.mkdir(parents=True,exist_ok=True)
    (PUBLIC/'state.json').write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    pending=[p for p in out['picks'] if p['status']=='pending'];done=[p for p in out['picks'] if p['status']!='pending']
    def pct(v):return '—' if v is None else f'{100*float(v):+.1f}%'
    def wl(s):return f"{s['wins']}-{s['losses']}" if s['settled'] else '0-0'
    rules=''.join(
      f"<tr><td><b>{escape(r['id'])}</b> · {escape(r['name'])}</td><td>{escape(r['role'])}</td>"
      f"<td>{r['historical']['bets']}</td><td>{r['historical']['wins']}-{r['historical']['losses']}</td>"
      f"<td>{pct(r['historical']['roi'])}</td><td>{pct(r['historical']['holdout_roi'])}</td>"
      f"<td>{r['prospective']['picks']}</td><td>{wl(r['prospective'])}</td><td>{pct(r['prospective']['roi'])}</td></tr>"
      for r in out['rules'])
    def pickrow(p):
        tags=', '.join(json.loads(p['rule_ids_json'] or '[]'))
        price='—' if p.get('quote_american') is None else f"{float(p['quote_american']):+.0f}"
        ko='—' if p.get('days_since_ko_loss_diff') is None else f"{float(p['days_since_ko_loss_diff']):.1f}"
        strong='—' if p.get('strong_opp_sig_diff') is None else f"{float(p['strong_opp_sig_diff']):.3f}"
        pl='—' if p.get('profit_units') is None else f"{float(p['profit_units']):+.2f}u"
        return f"<tr><td>{escape(p['event_date'])}</td><td><b>{escape(p['selection'])}</b><br><small>vs {escape(p['opponent'])}</small></td><td>{escape(tags)}</td><td>{price}</td><td>{strong}</td><td>{ko}</td><td>{escape(p['status'].upper())}</td><td>{pl}</td></tr>"
    pending_html=''.join(pickrow(p) for p in pending) or '<tr><td colspan="8">No current frozen shadow qualifiers.</td></tr>'
    done_html=''.join(pickrow(p) for p in reversed(done)) or '<tr><td colspan="8">No prospective shadow results yet.</td></tr>'
    css='''body{font-family:Arial,sans-serif;color:#172033;background:#f5f7fa;margin:0}main{max-width:1180px;margin:auto;padding:32px 22px 60px}h1{margin-bottom:8px}.note{background:#fff4d6;border:1px solid #ead393;padding:14px 16px;border-radius:12px;margin:18px 0 26px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0 28px}.card{background:#fff;border:1px solid #dce3ec;border-radius:12px;padding:16px}.card small{display:block;color:#66758a;text-transform:uppercase}.card b{font-size:24px}table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dce3ec;margin:10px 0 30px}th,td{padding:10px 11px;border-bottom:1px solid #e7edf3;text-align:left;font-size:13px}th{background:#eef3f8;font-size:11px;text-transform:uppercase}small{color:#66758a}@media(max-width:800px){.cards{grid-template-columns:1fr}table{display:block;overflow:auto;white-space:nowrap}}'''
    h=f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UFC Shadow Research | Appwiza</title><style>{css}</style></head><body><main>
    <h1>UFC Frozen Shadow Tracker</h1><p>Prospective validation of research candidates. This page is separate from the official UFC arsenal.</p>
    <div class="note"><b>Research only — not official selections.</b><br>Rules were frozen on September 24, 2026. Every future qualifier is recorded automatically before the fight; overlapping rules are one underlying pick, not multiple bets.</div>
    <div class="cards"><div class="card"><small>Unique shadow picks</small><b>{out['overall_unique']['picks']}</b></div><div class="card"><small>Prospective record</small><b>{wl(out['overall_unique'])}</b></div><div class="card"><small>Prospective ROI</small><b>{pct(out['overall_unique']['roi'])}</b></div></div>
    <h2>Frozen rules</h2><table><thead><tr><th>Rule</th><th>Role</th><th>Hist bets</th><th>Hist W-L</th><th>Hist ROI</th><th>Holdout ROI</th><th>Pros picks</th><th>Pros W-L</th><th>Pros ROI</th></tr></thead><tbody>{rules}</tbody></table>
    <h2>Upcoming shadow qualifiers</h2><table><thead><tr><th>Date</th><th>Selection</th><th>Tags</th><th>Frozen price</th><th>Strong-opp diff</th><th>KO-loss day diff</th><th>Status</th><th>P/L</th></tr></thead><tbody>{pending_html}</tbody></table>
    <h2>Completed prospective shadow picks</h2><table><thead><tr><th>Date</th><th>Selection</th><th>Tags</th><th>Frozen price</th><th>Strong-opp diff</th><th>KO-loss day diff</th><th>Status</th><th>P/L</th></tr></thead><tbody>{done_html}</tbody></table>
    <p><small>Generated {escape(out['generated_at'])}. Frozen version {VERSION}.</small></p></main></body></html>'''
    (PUBLIC/'index.html').write_text(h,encoding='utf-8')
    print(json.dumps({'publish':'ok','path':str(PUBLIC),'pending':len(pending),'completed':len(done),'overall':out['overall_unique']}))

def run():
    scan();settle();publish()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['init','scan','settle','publish','run'],nargs='?',default='run');a=ap.parse_args()
    if a.mode=='init':connect().close();publish()
    elif a.mode=='scan':scan();publish()
    elif a.mode=='settle':settle();publish()
    elif a.mode=='publish':publish()
    else:run()

if __name__=='__main__':main()
