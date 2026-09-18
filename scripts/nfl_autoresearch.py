#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, os, shutil, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
AR=CTX/'nfl_auto_research'
RAW=AR/'raw'; SNAP=AR/'snapshots'; REPORTS=AR/'reports'; STATE=AR/'state'
DB=STATE/'research.sqlite3'; LOCK=STATE/'research.lock'; LOG=AR/'autoresearch.log'
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
SCHED=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
MARKET_DIR=CTX/'market_snapshots'
for p in (AR,RAW,SNAP,REPORTS,STATE): p.mkdir(parents=True,exist_ok=True)

PRICE_BANDS=[
 ('DOG20_34',.20,.3499),('DOG35_44',.35,.4499),('DOG35_49',.35,.4999),
 ('PK_55',.45,.55),('FAV55_65',.55,.65),('FAV65_75',.65,.75),('FAV75_85',.75,.85)
]
VENUES=['ANY','HOME','AWAY']
TRACKS={
 'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025),'mins':(30,20,25)},
 'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025),'mins':(22,14,18)}
}

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def log(msg):
    line=f"{now()} {msg}"; print(line,flush=True)
    with LOG.open('a',encoding='utf-8') as f:f.write(line+'\n')
def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def conn():
    db=sqlite3.connect(DB,timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript("""
    CREATE TABLE IF NOT EXISTS source_snapshots(
      id INTEGER PRIMARY KEY,captured_at TEXT,source TEXT,sha256 TEXT,path TEXT,bytes INTEGER,
      UNIQUE(source,sha256));
    CREATE TABLE IF NOT EXISTS research_runs(
      id INTEGER PRIMARY KEY,started_at TEXT,finished_at TEXT,mode TEXT,dataset_sha TEXT,
      dataset_rows INTEGER,features INTEGER,candidates_tested INTEGER,survivors INTEGER,status TEXT,note TEXT);
    CREATE TABLE IF NOT EXISTS candidates(
      fingerprint TEXT PRIMARY KEY,name TEXT,track TEXT,price_band TEXT,venue TEXT,rule_json TEXT,
      first_seen TEXT,last_seen TEXT,times_seen INTEGER DEFAULT 1,bets INTEGER,wins INTEGER,losses INTEGER,
      win_rate REAL,roi REAL,train_roi REAL,validation_roi REAL,holdout_roi REAL,holdout_n INTEGER,
      positive_season_ratio REAL,max_negative_streak INTEGER,min_week INTEGER,status TEXT NOT NULL DEFAULT 'shadow_candidate',
      official INTEGER NOT NULL DEFAULT 0,note TEXT);
    CREATE TABLE IF NOT EXISTS candidate_observations(
      id INTEGER PRIMARY KEY,run_id INTEGER,fingerprint TEXT,observed_at TEXT,metrics_json TEXT);
    CREATE TABLE IF NOT EXISTS data_events(
      id INTEGER PRIMARY KEY,observed_at TEXT,source TEXT,event_type TEXT,detail_json TEXT);
    """)
    db.commit();return db
def resources_ok(min_mem_mb=1100,max_load=1.8):
    try:
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' in line:
                k,v=line.split(':',1);mem[k]=float(v.strip().split()[0])/1024
        return mem.get('MemAvailable',99999)>=min_mem_mb and os.getloadavg()[0]<=max_load,mem.get('MemAvailable',99999),os.getloadavg()[0]
    except Exception:return True,99999,0
def acquire_lock():
    if LOCK.exists():
        try:
            if time.time()-LOCK.stat().st_mtime<4*3600:return False
        except Exception:pass
    LOCK.write_text(str(os.getpid()));return True
def release_lock():
    try:LOCK.unlink()
    except FileNotFoundError:pass

def snapshot_file(path,label,copy=False):
    if not path.exists():return None
    h=sha_file(path);dst=path
    if copy:
        dst=SNAP/f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{h[:10]}{path.suffix}"
        with conn() as db:
            old=db.execute('SELECT path FROM source_snapshots WHERE source=? AND sha256=?',(label,h)).fetchone()
        if not old:shutil.copy2(path,dst)
        elif old:dst=Path(old[0])
    with conn() as db:
        db.execute('INSERT OR IGNORE INTO source_snapshots(captured_at,source,sha256,path,bytes) VALUES(?,?,?,?,?)',
                   (now(),label,h,str(dst),path.stat().st_size))
        db.commit()
    return h

def snapshot_sources():
    dh=snapshot_file(DATA,'historical_feature_store',copy=True)
    sh=snapshot_file(SCHED,'schedule_source',copy=False)
    latest=None
    if MARKET_DIR.exists():
        files=sorted(MARKET_DIR.glob('*.jsonl'),key=lambda p:p.stat().st_mtime)
        if files:
            latest=files[-1];snapshot_file(latest,'prospective_market_'+latest.stem,copy=False)
    with conn() as db:
        db.execute('INSERT INTO data_events(observed_at,source,event_type,detail_json) VALUES(?,?,?,?)',
                   (now(),'nfl_auto_research','snapshot',json.dumps({'dataset_sha':dh,'schedule_sha':sh,'latest_market':str(latest) if latest else None})))
        db.commit()
    log(f"snapshot complete dataset={dh[:10] if dh else 'missing'} latest_market={latest.name if latest else 'none'}")
    return dh

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o);return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds);win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def max_losing_streak(vals):
    best=cur=0
    for v in vals:
        if v<0:cur+=1;best=max(best,cur)
        else:cur=0
    return best
def metrics(x):
    if x.empty:return None
    by=x.groupby('season').profit_units.agg(['count','sum']).sort_index()
    elig=by[by['count']>=3]
    return {
      'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),
      'win_rate':float(x.win.mean()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum()),
      'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum()),
      'negative_seasons':int((elig['sum']<0).sum()),'max_negative_streak':int(max_losing_streak(elig['sum'].tolist()))
    }
def sem(c):
    c=str(c).lower()
    if 'injur' in c or 'unavail' in c or 'star_out' in c:return 'INJURY'
    if 'returning_' in c or 'continuity' in c:return 'CONTINUITY'
    if 'coach' in c or 'coordinator' in c or c.startswith(('hc_','oc_','dc_')):return 'COACHING'
    if 'travel' in c or 'tz_' in c or 'altitude' in c or 'road_' in c:return 'TRAVEL'
    if 'qb_' in c:return 'QB'
    if 'rest' in c:return 'REST'
    if 'third_down' in c:return 'THIRD_DOWN'
    if 'redzone' in c:return 'RED_ZONE'
    if 'explosive' in c:return 'EXPLOSIVE'
    if 'punt' in c or 'kick_return' in c or 'fg_accuracy' in c:return 'SPECIAL_TEAMS'
    if 'giveaway' in c or 'turnover' in c:return 'TURNOVERS'
    if 'sack' in c or 'hit_rate' in c:return 'PASS_RUSH'
    if 'pass' in c or 'cpoe' in c:return 'PASSING'
    if 'rush' in c:return 'RUSHING'
    if 'epa' in c or 'yards_per_play' in c or 'success_rate' in c:return 'EFFICIENCY'
    if 'elo' in c:return 'ELO'
    return 'OTHER'

def load_data():
    if not DATA.exists():raise FileNotFoundError(DATA)
    d=pd.read_parquet(DATA)
    d=d[d.season.between(2006,2025)].copy()
    if SCHED.exists():
        s=pd.read_parquet(SCHED,columns=['game_id','game_type'])
        reg=set(s.loc[s.game_type.astype(str).eq('REG'),'game_id'].astype(str))
        d=d[d.game_id.astype(str).isin(reg)].copy()
    if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
    d=d[num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
    d['season']=num(d.season).astype(int);d['week']=num(d.week).astype(int)
    d['win']=num(d.win);d['moneyline']=num(d.moneyline)
    d['market_prob_use']=num(d.market_prob) if 'market_prob' in d.columns else pd.Series(implied(d.moneyline),index=d.index)
    d['market_prob_use']=d.market_prob_use.fillna(pd.Series(implied(d.moneyline),index=d.index))
    d['profit_units']=profit(d.win,d.moneyline)
    d=d.drop_duplicates(['game_id','team'],keep='last').reset_index(drop=True)
    return d

def eligible_features(d):
    explicit={'elo_edge','rest_edge','rest_days','rest_days_edge','qb_prior_starts','qb_changed_from_prior_season',
      'head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','neutral_site','international_game',
      'high_altitude_game','dome_game','grass_surface','travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours',
      'altitude_change_ft','consecutive_road_games','returning_offense_snap_share','returning_defense_snap_share',
      'returning_ol_snap_share','returning_skill_snap_share','adv_returning_offense_snap_share','adv_returning_defense_snap_share',
      'adv_returning_ol_snap_share','adv_returning_skill_snap_share','adv_unavailable_equiv','adv_out_equiv','adv_qb_unavail_equiv',
      'adv_ol_unavail_equiv','adv_skill_unavail_equiv','adv_front7_unavail_equiv','adv_secondary_unavail_equiv'}
    out=[]
    for c in d.columns:
        lc=c.lower()
        # Project decision: weather is excluded from automated discovery.
        if any(x in lc for x in ['weather','temperature','temp','wind']):continue
        if not (c.startswith(('rank_edge_','recent_edge_','adv_','coachq_')) or c in explicit):continue
        if any(x in lc for x in ['win','profit','result','score','points_for','points_against','completed','push']):continue
        z=num(d[c])
        if z.notna().sum()>=180 and z.nunique(dropna=True)>=2:out.append(c)
    return list(dict.fromkeys(out))[:180]

def pband_mask(d,name):
    _,lo,hi=next(x for x in PRICE_BANDS if x[0]==name)
    return num(d.market_prob_use).between(lo,hi,inclusive='both')
def venue_mask(d,v):
    if v=='ANY':return pd.Series(True,index=d.index)
    return num(d.is_home).eq(1 if v=='HOME' else 0)
def period_mask(d,track,name):
    lo,hi=TRACKS[track][name];return d.season.between(lo,hi)
def condition(d,c,op,q):
    z=num(d[c]);return z>=q if op=='>=' else z<=q
def fingerprint(track,pb,venue,rule):
    s=json.dumps({'track':track,'price_band':pb,'venue':venue,'rule':rule},sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:24]

def evaluate(d,mask,track):
    vals={}
    for period in ('train','validation','holdout'):
        vals[period]=metrics(d[mask&period_mask(d,track,period)])
        if not vals[period]:return None
    vals['full']=metrics(d[mask])
    if not vals['full']:return None
    f=vals['full']; pos=f['positive_seasons']/max(1,f['active_seasons'])
    vals['positive_ratio']=pos
    vals['min_week']=int(d.loc[mask,'week'].min()) if mask.any() else 99
    return vals

def passes(ev,track):
    mn=TRACKS[track]['mins']; tr=ev['train'];va=ev['validation'];ho=ev['holdout'];fu=ev['full']
    return (tr['n']>=mn[0] and va['n']>=mn[1] and ho['n']>=mn[2] and fu['n']>=80 and
            tr['roi']>=.05 and va['roi']>=.08 and ho['roi']>=.08 and fu['roi']>=.10 and
            ev['positive_ratio']>=.60 and fu['max_negative_streak']<=3)

def save_candidate(run_id,mode,track,pb,venue,rule,ev):
    fp=fingerprint(track,pb,venue,rule);ts=now();fu=ev['full']
    name=' AND '.join(f"{c} {op} {q:.5g}" for c,op,q in rule)
    with conn() as db:
        old=db.execute('SELECT times_seen FROM candidates WHERE fingerprint=?',(fp,)).fetchone()
        if old:
            db.execute("""UPDATE candidates SET last_seen=?,times_seen=?,bets=?,wins=?,losses=?,win_rate=?,roi=?,
                train_roi=?,validation_roi=?,holdout_roi=?,holdout_n=?,positive_season_ratio=?,max_negative_streak=?,min_week=?
                WHERE fingerprint=?""",
                (ts,old[0]+1,fu['n'],fu['wins'],fu['losses'],fu['win_rate'],fu['roi'],ev['train']['roi'],ev['validation']['roi'],
                 ev['holdout']['roi'],ev['holdout']['n'],ev['positive_ratio'],fu['max_negative_streak'],ev['min_week'],fp))
        else:
            db.execute("""INSERT INTO candidates(fingerprint,name,track,price_band,venue,rule_json,first_seen,last_seen,times_seen,
                bets,wins,losses,win_rate,roi,train_roi,validation_roi,holdout_roi,holdout_n,positive_season_ratio,
                max_negative_streak,min_week,status,official,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fp,name,track,pb,venue,json.dumps(rule),ts,ts,1,fu['n'],fu['wins'],fu['losses'],fu['win_rate'],fu['roi'],
                 ev['train']['roi'],ev['validation']['roi'],ev['holdout']['roi'],ev['holdout']['n'],ev['positive_ratio'],
                 fu['max_negative_streak'],ev['min_week'],'shadow_candidate',0,
                 'Autonomous research only. Never auto-promote to live/email/tracker. Explicit approval required.'))
        db.execute('INSERT INTO candidate_observations(run_id,fingerprint,observed_at,metrics_json) VALUES(?,?,?,?)',
                   (run_id,fp,ts,json.dumps({'mode':mode,**ev},sort_keys=True)))
        db.commit()
    return fp

def research(mode):
    ok,mem,load=resources_ok(1300 if mode=='weekly' else 1000,1.8)
    if not ok:
        log(f"research skipped by resource guard mem={mem:.0f}MB load={load:.2f}")
        return {'status':'skipped_resource_guard','tested':0,'survivors':0}
    d=load_data();features=eligible_features(d);dh=sha_file(DATA)
    with conn() as db:
        cur=db.execute('INSERT INTO research_runs(started_at,mode,dataset_sha,dataset_rows,features,status) VALUES(?,?,?,?,?,?)',
                       (now(),mode,dh,len(d),len(features),'running'));run_id=cur.lastrowid;db.commit()

    tested=0; survivors=[]; pre_pool=[]
    quantiles=[.25,.40,.60,.75] if mode=='daily' else [.15,.25,.35,.50,.65,.75,.85]
    for track in TRACKS:
        train=d[period_mask(d,track,'train')]
        for c in features:
            if track=='LONG' and sem(c) in {'INJURY','CONTINUITY','COACHING'}:continue
            vals=num(train[c]).dropna()
            if len(vals)<120:continue
            qs=sorted(set(float(x) for x in vals.quantile(quantiles).dropna()))
            for q in qs:
                for op in ('>=','<='):
                    cm=condition(d,c,op,q)
                    for pb,_,__ in PRICE_BANDS:
                        pm=pband_mask(d,pb)
                        for venue in VENUES:
                            m=cm&pm&venue_mask(d,venue);tested+=1
                            # Fail early before opening validation/holdout.
                            mn=TRACKS[track]['mins']
                            tr=metrics(d[m&period_mask(d,track,'train')])
                            if not tr or tr['n']<mn[0] or tr['roi']<.05:
                                continue
                            va=metrics(d[m&period_mask(d,track,'validation')])
                            if not va or va['n']<mn[1] or va['roi']<.08:
                                continue
                            score=min(tr['roi'],va['roi'])*math.sqrt(min(tr['n'],va['n']))
                            pre_pool.append((score,track,pb,venue,[(c,op,float(q))],m))
                            ev=evaluate(d,m,track)
                            if ev and passes(ev,track):
                                survivors.append((track,pb,venue,[(c,op,float(q))],ev))

    if mode=='weekly':
        # Pair thresholds are constructed only from train+validation survivors; holdout is opened only after pair is frozen.
        pre_pool=sorted(pre_pool,key=lambda x:x[0],reverse=True)
        diverse=[];caps={}
        for row in pre_pool:
            _,track,pb,venue,rule,_=row;c=rule[0][0];key=(track,pb,venue,sem(c))
            if caps.get(key,0)>=4:continue
            diverse.append(row);caps[key]=caps.get(key,0)+1
            if len(diverse)>=220:break
        for i,a in enumerate(diverse):
            for b in diverse[i+1:min(len(diverse),i+100)]:
                _,ta,pba,va,ra,ma=a;_,tb,pbb,vb,rb,mb=b
                if (ta,pba,va)!=(tb,pbb,vb):continue
                if ra[0][0]==rb[0][0] or sem(ra[0][0])==sem(rb[0][0]):continue
                rule=ra+rb;m=ma&mb;tested+=1
                ev=evaluate(d,m,ta)
                if ev and passes(ev,ta):survivors.append((ta,pba,va,rule,ev))

    # De-duplicate exact rules and rank by holdout floor + sample.
    uniq={}
    for track,pb,venue,rule,ev in survivors:
        fp=fingerprint(track,pb,venue,rule);uniq[fp]=(track,pb,venue,rule,ev)
    ranked=list(uniq.values())
    ranked.sort(key=lambda x:(x[4]['holdout']['roi'],x[4]['full']['n']),reverse=True)
    ranked=ranked[:75]
    for row in ranked:save_candidate(run_id,mode,*row)
    with conn() as db:
        db.execute('UPDATE research_runs SET finished_at=?,candidates_tested=?,survivors=?,status=?,note=? WHERE id=?',
                   (now(),tested,len(ranked),'completed',
                    'REG only; prior_games>=3; weather excluded; thresholds from train; validation before holdout; shadow only; no automatic promotion',run_id))
        db.commit()

    result={'status':'completed','mode':mode,'run_id':run_id,'rows':len(d),'features':len(features),'tested':tested,'survivors':len(ranked)}
    log(f"{mode} research completed rows={len(d)} features={len(features)} tested={tested} survivors={len(ranked)}")
    return result

def top_candidates(limit=12):
    with conn() as db:
        return db.execute("""SELECT fingerprint,name,track,price_band,venue,bets,wins,losses,roi,train_roi,validation_roi,
          holdout_roi,holdout_n,positive_season_ratio,times_seen,min_week
          FROM candidates WHERE official=0 AND status='shadow_candidate'
          ORDER BY times_seen DESC,holdout_roi DESC,bets DESC LIMIT ?""",(limit,)).fetchall()

def write_report(result):
    tops=top_candidates(20)
    lines=['NFL AUTONOMOUS RESEARCH','='*100,json.dumps(result,indent=2),'',
           'Shadow candidates only. This system never modifies the live scanner, email selector, or betting tracker.','',
           'TOP STABLE SHADOW CANDIDATES']
    for r in tops:
        lines.append(f"{r[1]} | {r[2]} {r[3]} {r[4]} | n={r[5]} {r[6]}-{r[7]} | ROI {r[8]*100:+.1f}% | train {r[9]*100:+.1f}% val {r[10]*100:+.1f}% hold {r[11]*100:+.1f}% (n={r[12]}) | +season {r[13]*100:.0f}% | seen {r[14]}x | earliest W{r[15]}")
    p=REPORTS/f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{result.get('mode','run')}.txt"
    p.write_text('\n'.join(lines)+'\n')
    (REPORTS/'latest.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return p

def status():
    with conn() as db:
        runs=db.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0]
        cand=db.execute("SELECT COUNT(*) FROM candidates WHERE status='shadow_candidate' AND official=0").fetchone()[0]
        off=db.execute('SELECT COUNT(*) FROM candidates WHERE official!=0').fetchone()[0]
        last=db.execute('SELECT id,mode,status,dataset_rows,features,candidates_tested,survivors,started_at,finished_at FROM research_runs ORDER BY id DESC LIMIT 1').fetchone()
        snaps=db.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0]
    out={'runs':runs,'shadow_candidates':cand,'official_autopromotions':off,'snapshots':snaps,'last_run':last}
    print(json.dumps(out,indent=2,default=str));return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['snapshot','daily','weekly','status']);a=ap.parse_args()
    if a.mode=='status':status();return
    if not acquire_lock():
        log('another NFL autoresearch process owns the lock; skipping');return
    try:
        snapshot_sources()
        if a.mode=='snapshot':return
        r=research(a.mode);write_report(r)
    finally:release_lock()
if __name__=='__main__':main()
