#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
BASE_DATA=ROOT/'data/processed/mls_match_features_1996_present.parquet'
ADV_DATA=ROOT/'data/processed/mls_match_features_advanced.parquet'
DATA=ADV_DATA if ADV_DATA.exists() else BASE_DATA
AR=ROOT/'auto_research';STATE=AR/'state';REPORTS=AR/'reports';DB=STATE/'research.sqlite3'
for p in [STATE,REPORTS]:p.mkdir(parents=True,exist_ok=True)

SPLIT={'train':(2012,2018),'validation':(2019,2022),'holdout':(2023,2025)}
PRICE_BANDS=[
 ('ALL',0.0,1.0),
 ('P20_30',.20,.30),('P25_35',.25,.35),('P30_40',.30,.40),('P35_45',.35,.45),
 ('P40_50',.40,.50),('P45_55',.45,.55),('P50_60',.50,.60),('P55_70',.55,.70)
]

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def conn():
    db=sqlite3.connect(DB,timeout=60);db.execute('PRAGMA journal_mode=WAL')
    db.executescript("""
    CREATE TABLE IF NOT EXISTS research_runs(
      id INTEGER PRIMARY KEY,started_at TEXT,finished_at TEXT,mode TEXT,rows INTEGER,features INTEGER,
      tested INTEGER,survivors INTEGER,status TEXT,note TEXT);
    CREATE TABLE IF NOT EXISTS candidates(
      fingerprint TEXT PRIMARY KEY,outcome TEXT,price_band TEXT,feature TEXT,op TEXT,threshold REAL,
      first_seen TEXT,last_seen TEXT,times_seen INTEGER,bets INTEGER,wins INTEGER,losses INTEGER,
      win_rate REAL,roi REAL,train_roi REAL,validation_roi REAL,holdout_roi REAL,holdout_n INTEGER,
      positive_season_ratio REAL,status TEXT,official INTEGER DEFAULT 0,note TEXT);
    CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY,run_id INTEGER,fingerprint TEXT,at TEXT,metrics_json TEXT);
    """);db.commit();return db
def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit.agg(['count','sum'])
    elig=by[by['count']>=5]
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
            'win_rate':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum()),
            'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum())}
def selection_rows(d):
    base=d[d.odds_matched.eq(True)&d.season.between(2012,2025)].copy()
    base=base[(base.home_prior_games>=5)&(base.away_prior_games>=5)].copy()
    if not {'asa_game_available','asa_knockout_game'}.issubset(base.columns):
        raise RuntimeError('MLS primary research requires ASA game/stage metadata; refusing untagged universe')
    base=base[base.asa_game_available.eq(True)&base.asa_knockout_game.eq(False)].copy()
    rows=[]
    for outcome in ['HOME','DRAW','AWAY']:
        z=pd.DataFrame(index=base.index)
        z['match_id']=base.match_id;z['date']=base.date;z['season']=base.season;z['outcome']=outcome
        if outcome=='HOME':
            z['win']=base.home_win;z['odds']=base.home_odds;z['market_prob']=base.home_novig_prob
            sign=1
            z['sel_prior_games']=base.home_prior_games;z['opp_prior_games']=base.away_prior_games
            for col in ['season_ppg','season_gdpg','days_rest','last3_ppg','last5_ppg','last10_ppg','last5_gdpg','last10_gdpg',
                        'venue5_ppg','travel_miles','altitude_change_ft','tz_shift_hours','prior_road_miles5','consecutive_road_pre','last3_xgfpg','last3_xgapg','last3_xgdpg','last5_xgfpg','last5_xgapg','last5_xgdpg','last10_xgfpg','last10_xgapg','last10_xgdpg','season_xgfpg','season_xgapg','season_xgdpg']:
                z['sel_'+col]=base['home_'+col];z['opp_'+col]=base['away_'+col]
            z['elo_edge']=base.elo_edge_home
        elif outcome=='AWAY':
            z['win']=base.away_win;z['odds']=base.away_odds;z['market_prob']=base.away_novig_prob
            sign=-1
            z['sel_prior_games']=base.away_prior_games;z['opp_prior_games']=base.home_prior_games
            for col in ['season_ppg','season_gdpg','days_rest','last3_ppg','last5_ppg','last10_ppg','last5_gdpg','last10_gdpg',
                        'venue5_ppg','travel_miles','altitude_change_ft','tz_shift_hours','prior_road_miles5','consecutive_road_pre','last3_xgfpg','last3_xgapg','last3_xgdpg','last5_xgfpg','last5_xgapg','last5_xgdpg','last10_xgfpg','last10_xgapg','last10_xgdpg','season_xgfpg','season_xgapg','season_xgdpg']:
                z['sel_'+col]=base['away_'+col];z['opp_'+col]=base['home_'+col]
            z['elo_edge']=-base.elo_edge_home
        else:
            z['win']=base.draw;z['odds']=base.draw_odds;z['market_prob']=base.draw_novig_prob
            z['sel_prior_games']=np.minimum(base.home_prior_games,base.away_prior_games)
            z['opp_prior_games']=np.maximum(base.home_prior_games,base.away_prior_games)
            z['elo_edge']=base.abs_elo_edge
            # Draw features emphasize matchup balance and combined fatigue/form.
            for col in ['season_ppg','season_gdpg','days_rest','last3_ppg','last5_ppg','last10_ppg','last5_gdpg','last10_gdpg','venue5_ppg','last3_xgfpg','last3_xgapg','last3_xgdpg','last5_xgfpg','last5_xgapg','last5_xgdpg','last10_xgdpg','season_xgfpg','season_xgapg','season_xgdpg']:
                z['balance_'+col]=(base['home_'+col]-base['away_'+col]).abs()
                z['combined_'+col]=(base['home_'+col]+base['away_'+col])
            for col in ['travel_miles','prior_road_miles5','consecutive_road_pre']:
                z['combined_'+col]=base['home_'+col]+base['away_'+col]
                z['balance_'+col]=(base['home_'+col]-base['away_'+col]).abs()
            z['balance_market_sides']=(base.home_novig_prob-base.away_novig_prob).abs()
        # selection-relative edges
        if outcome in ['HOME','AWAY']:
            for col in ['season_ppg','season_gdpg','days_rest','last3_ppg','last5_ppg','last10_ppg','last5_gdpg','last10_gdpg',
                        'venue5_ppg','travel_miles','altitude_change_ft','tz_shift_hours','prior_road_miles5','consecutive_road_pre','last3_xgfpg','last3_xgapg','last3_xgdpg','last5_xgfpg','last5_xgapg','last5_xgdpg','last10_xgfpg','last10_xgapg','last10_xgdpg','season_xgfpg','season_xgapg','season_xgdpg']:
                z['edge_'+col]=z['sel_'+col]-z['opp_'+col]
        z['profit']=np.where(z.win.eq(1),z.odds-1.0,-1.0)
        rows.append(z)
    return pd.concat(rows,ignore_index=True)
def eligible_features(s,outcome):
    exclude={'match_id','date','season','outcome','win','odds','market_prob','profit'}
    out=[]
    for c in s.columns:
        if c in exclude:continue
        v=pd.to_numeric(s[c],errors='coerce')
        if v.notna().sum()<250 or v.nunique(dropna=True)<8:continue
        if outcome=='DRAW' and not (c.startswith(('balance_','combined_')) or c in {'elo_edge','sel_prior_games','opp_prior_games'}):continue
        if outcome!='DRAW' and c.startswith(('balance_','combined_')):continue
        out.append(c)
    return out
def pmask(s,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return s.market_prob.between(lo,hi,inclusive='both')
def period(s,name):lo,hi=SPLIT[name];return s.season.between(lo,hi)
def evaluate(s,m):
    vals={k:metrics(s[m&period(s,k)]) for k in SPLIT}
    vals['full']=metrics(s[m])
    if any(vals[k] is None for k in ['train','validation','holdout','full']):return None
    f=vals['full'];vals['positive_ratio']=f['positive_seasons']/max(1,f['active_seasons'])
    return vals
def passes(ev):
    tr,va,ho,fu=ev['train'],ev['validation'],ev['holdout'],ev['full']
    return (tr['n']>=35 and va['n']>=25 and ho['n']>=30 and fu['n']>=120 and
            tr['roi']>=.04 and va['roi']>=.03 and ho['roi']>=.03 and fu['roi']>=.05 and
            ev['positive_ratio']>=.60 and ho['units']>=2.0)
def fingerprint(outcome,pb,feature,op,t):
    return hashlib.sha256(json.dumps([outcome,pb,feature,op,round(float(t),8)]).encode()).hexdigest()[:24]
def save(run_id,outcome,pb,feature,op,t,ev):
    fp=fingerprint(outcome,pb,feature,op,t);ts=now();fu=ev['full']
    with conn() as db:
        old=db.execute('select times_seen from candidates where fingerprint=?',(fp,)).fetchone()
        vals=(fu['n'],fu['wins'],fu['losses'],fu['win_rate'],fu['roi'],ev['train']['roi'],ev['validation']['roi'],ev['holdout']['roi'],ev['holdout']['n'],ev['positive_ratio'])
        if old:
            db.execute("""update candidates set last_seen=?,times_seen=?,bets=?,wins=?,losses=?,win_rate=?,roi=?,train_roi=?,validation_roi=?,holdout_roi=?,holdout_n=?,positive_season_ratio=? where fingerprint=?""",
                       (ts,old[0]+1,*vals,fp))
        else:
            db.execute("""insert into candidates(fingerprint,outcome,price_band,feature,op,threshold,first_seen,last_seen,times_seen,bets,wins,losses,win_rate,roi,train_roi,validation_roi,holdout_roi,holdout_n,positive_season_ratio,status,official,note)
                          values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (fp,outcome,pb,feature,op,float(t),ts,ts,1,*vals,'shadow_candidate',0,'Automated MLS research only; never auto-promote to website/email/tracker.'))
        db.execute('insert into observations(run_id,fingerprint,at,metrics_json) values(?,?,?,?)',(run_id,fp,ts,json.dumps(ev,sort_keys=True)))
        db.commit()
def research(mode='daily'):
    d=pd.read_parquet(DATA)
    s=selection_rows(d)
    with conn() as db:
        cur=db.execute('insert into research_runs(started_at,mode,rows,status) values(?,?,?,?)',(now(),mode,len(s),'running'));rid=cur.lastrowid;db.commit()
    qlist=[.33,.67] if mode=='daily' else [.15,.25,.35,.50,.65,.75,.85]
    tested=0;survivors=[]
    for outcome in ['HOME','DRAW','AWAY']:
        so=s[s.outcome.eq(outcome)].copy()
        tr=so[period(so,'train')]
        feats=eligible_features(so,outcome)
        for feat in feats:
            vals=pd.to_numeric(tr[feat],errors='coerce').dropna()
            if len(vals)<200:continue
            qs=sorted(set(float(x) for x in vals.quantile(qlist).dropna()))
            for t in qs:
                for op in ['>=','<=']:
                    cm=pd.to_numeric(so[feat],errors='coerce').ge(t) if op=='>=' else pd.to_numeric(so[feat],errors='coerce').le(t)
                    for pb,_,__ in PRICE_BANDS:
                        m=cm&pmask(so,pb);tested+=1
                        # Stage-gate before holdout.
                        mt=metrics(so[m&period(so,'train')])
                        if not mt or mt['n']<35 or mt['roi']<.04:continue
                        mv=metrics(so[m&period(so,'validation')])
                        if not mv or mv['n']<25 or mv['roi']<.03:continue
                        ev=evaluate(so,m)
                        if ev and passes(ev):survivors.append((outcome,pb,feat,op,t,ev))
    # Keep strongest diversified candidates.
    survivors.sort(key=lambda x:(x[5]['holdout']['roi'],x[5]['full']['n']),reverse=True)
    chosen=[];caps={}
    for row in survivors:
        key=(row[0],row[1],row[2])
        if caps.get(key,0)>=1:continue
        chosen.append(row);caps[key]=1
        if len(chosen)>=80:break
    for row in chosen:save(rid,*row)
    with conn() as db:
        db.execute('update research_runs set finished_at=?,features=?,tested=?,survivors=?,status=?,note=? where id=?',
                   (now(),len(set(x[2] for x in chosen)),tested,len(chosen),'completed',
                    'MLS regular season only; 2012-18 train; 2019-22 validation; 2023-25 holdout; 2026 excluded; shadow only',rid));db.commit()
    lines=['MLS AUTONOMOUS SHADOW RESEARCH','='*100,
           f'mode={mode} selection_rows={len(s):,} tested={tested:,} survivors={len(chosen)}',
           'Universe: MLS regular season only (ASA knockout_game=False)',
           'Splits: 2012-2018 train | 2019-2022 validation | 2023-2025 holdout | 2026 prospective only',
           'No automatic promotion to live/email/website.','',
           'TOP SURVIVORS']
    for outcome,pb,feat,op,t,ev in chosen[:30]:
        f=ev['full'];h=ev['holdout']
        lines.append(f"{outcome} {pb} | {feat} {op} {t:.4g} | n={f['n']} {f['wins']}-{f['losses']} win={f['win_rate']:.1%} ROI={f['roi']:+.1%} | train={ev['train']['roi']:+.1%} val={ev['validation']['roi']:+.1%} hold={h['roi']:+.1%} n={h['n']} | +season={ev['positive_ratio']:.0%}")
    report='\n'.join(lines)+'\n';(REPORTS/'latest.txt').write_text(report);(REPORTS/f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{mode}.txt').write_text(report)
    print(report)
def status():
    with conn() as db:
        print(json.dumps({'runs':db.execute('select count(*) from research_runs').fetchone()[0],
                          'shadow_candidates':db.execute("select count(*) from candidates where status='shadow_candidate' and official=0").fetchone()[0],
                          'official_autopromotions':db.execute('select count(*) from candidates where official!=0').fetchone()[0],
                          'last_run':db.execute('select id,mode,rows,tested,survivors,status,started_at,finished_at from research_runs order by id desc limit 1').fetchone()},indent=2,default=str))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['daily','weekly','status']);a=ap.parse_args()
    status() if a.mode=='status' else research(a.mode)
