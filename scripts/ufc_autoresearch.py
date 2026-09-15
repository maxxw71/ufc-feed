#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, html, json, math, os, shutil, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

ROOT = Path.home() / "ufc-predictor-v1"
AR = ROOT / "auto_research"
RAW = AR / "raw"
SNAP = AR / "snapshots"
REPORTS = AR / "reports"
STATE = AR / "state"
DB = STATE / "research.sqlite3"
LOCK = STATE / "research.lock"
LOG = AR / "autoresearch.log"
PREFIGHT = ROOT / "new_category_discovery" / "prefight_favorite_features.csv"
UFCSTATS_URL = "https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv"
ENV_PATH = Path.home() / ".config" / "ufc-watcher.env"

for p in (AR, RAW, SNAP, REPORTS, STATE): p.mkdir(parents=True, exist_ok=True)

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def log(msg):
    line=f"{now()} {msg}"
    print(line, flush=True)
    with LOG.open('a',encoding='utf-8') as f: f.write(line+'\n')

def conn():
    c=sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript('''
    CREATE TABLE IF NOT EXISTS source_snapshots(
      id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, source TEXT NOT NULL,
      sha256 TEXT NOT NULL, path TEXT NOT NULL, rows INTEGER, bytes INTEGER,
      UNIQUE(source,sha256));
    CREATE TABLE IF NOT EXISTS research_runs(
      id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, mode TEXT,
      dataset_sha TEXT, dataset_rows INTEGER, candidates_tested INTEGER DEFAULT 0,
      survivors INTEGER DEFAULT 0, status TEXT, note TEXT);
    CREATE TABLE IF NOT EXISTS candidates(
      fingerprint TEXT PRIMARY KEY, name TEXT, rule_json TEXT, source_dataset TEXT,
      first_seen TEXT, last_seen TEXT, times_seen INTEGER DEFAULT 1,
      bets INTEGER, wins INTEGER, losses INTEGER, win_rate REAL, roi REAL,
      train_roi REAL, holdout_roi REAL, early_roi REAL, recent_roi REAL,
      yearly_positive_ratio REAL, status TEXT NOT NULL DEFAULT 'shadow_candidate',
      official INTEGER NOT NULL DEFAULT 0, note TEXT);
    CREATE TABLE IF NOT EXISTS candidate_observations(
      id INTEGER PRIMARY KEY, run_id INTEGER, fingerprint TEXT, observed_at TEXT,
      metrics_json TEXT, FOREIGN KEY(run_id) REFERENCES research_runs(id));
    CREATE TABLE IF NOT EXISTS data_events(
      id INTEGER PRIMARY KEY, observed_at TEXT, source TEXT, event_type TEXT,
      detail_json TEXT);
    ''')
    c.commit(); return c

def resources_ok(min_mem_mb=1100, max_load=1.6):
    try:
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' in line:
                k,v=line.split(':',1); mem[k]=float(v.strip().split()[0])/1024
        avail=mem.get('MemAvailable',99999)
        load=os.getloadavg()[0]
        return avail>=min_mem_mb and load<=max_load, avail, load
    except Exception: return True,99999,0

def acquire_lock():
    if LOCK.exists():
        try:
            age=time.time()-LOCK.stat().st_mtime
            if age < 4*3600: return False
        except Exception: pass
    LOCK.write_text(str(os.getpid()))
    return True

def release_lock():
    try: LOCK.unlink()
    except FileNotFoundError: pass

def fetch_bytes(url, timeout=60):
    req=Request(url,headers={'User-Agent':'appwiza-ufc-autoresearch/1.0'})
    with urlopen(req,timeout=timeout) as r: return r.read()

def snapshot_source(send_event=True):
    b=fetch_bytes(UFCSTATS_URL,90)
    h=sha_bytes(b); current=RAW/'ufcstats_competitions_current.csv'
    oldh=sha_file(current) if current.exists() else None
    changed=(h!=oldh)
    if changed:
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
        archive=RAW/f'ufcstats_competitions_{stamp}_{h[:10]}.csv'
        archive.write_bytes(b); current.write_bytes(b)
        try: rows=max(0,b.count(b'\n')-1)
        except Exception: rows=None
        with conn() as c:
            c.execute("INSERT OR IGNORE INTO source_snapshots(captured_at,source,sha256,path,rows,bytes) VALUES(?,?,?,?,?,?)",
                      (now(),'ufcstats_competitions',h,str(archive),rows,len(b)))
            c.execute("INSERT INTO data_events(observed_at,source,event_type,detail_json) VALUES(?,?,?,?)",
                      (now(),'ufcstats_competitions','source_changed',json.dumps({'previous_sha':oldh,'new_sha':h,'rows':rows,'archive':str(archive)})))
            c.commit()
        log(f"UFCStats source changed; archived {rows} rows as {archive.name}")
    else: log("UFCStats source unchanged")
    if PREFIGHT.exists():
        ph=sha_file(PREFIGHT); stamp=datetime.now().strftime('%Y%m%d')
        dst=SNAP/f'prefight_features_{stamp}_{ph[:10]}.csv'
        if not dst.exists():
            shutil.copy2(PREFIGHT,dst)
            with conn() as c:
                try: n=sum(1 for _ in open(dst,'rb'))-1
                except Exception: n=None
                c.execute("INSERT OR IGNORE INTO source_snapshots(captured_at,source,sha256,path,rows,bytes) VALUES(?,?,?,?,?,?)",
                          (now(),'prefight_favorite_features',ph,str(dst),n,dst.stat().st_size)); c.commit()
    return changed,h

def pick_col(cols, names):
    low={c.lower():c for c in cols}
    for n in names:
        if n.lower() in low: return low[n.lower()]
    return None

def schema(df):
    cols=list(df.columns)
    datec=pick_col(cols,['date','event_date','fight_date'])
    winc=pick_col(cols,['favorite_won','fav_won','favorite_win','fav_win','won'])
    probc=pick_col(cols,['market_prob','favorite_market_prob','fav_market_prob','no_vig_prob','favorite_prob'])
    oddsc=pick_col(cols,['favorite_american_odds','favorite_moneyline','fav_moneyline','favorite_ml','fav_ml','favorite_odds','fav_odds','american_odds'])
    return datec,winc,probc,oddsc

def normalize_win(s):
    if pd.api.types.is_bool_dtype(s): return s.astype(float)
    if pd.api.types.is_numeric_dtype(s):
        x=pd.to_numeric(s,errors='coerce'); return (x>0.5).astype(float)
    m={'w':1,'win':1,'true':1,'1':1,'l':0,'loss':0,'false':0,'0':0}
    return s.astype(str).str.strip().str.lower().map(m)

def decimal_odds(df, probc, oddsc):
    if oddsc:
        x=pd.to_numeric(df[oddsc],errors='coerce')
        med=float(x.dropna().abs().median()) if x.notna().any() else 0
        if med>20:
            return pd.Series(np.where(x>0,1+x/100,1+100/(-x)),index=df.index,dtype=float)
        if med>1: return x.astype(float)
    if probc:
        p=pd.to_numeric(df[probc],errors='coerce')
        return 1/p.clip(lower=.02,upper=.98)
    return pd.Series(np.nan,index=df.index)

def roi_of(mask,wins,dec):
    m=mask & wins.notna() & dec.notna() & (dec>1)
    n=int(m.sum())
    if n==0:return None
    profit=np.where(wins[m].values>0.5,dec[m].values-1,-1.0)
    return float(np.mean(profit)),n,int((wins[m]>0.5).sum())

def yearly_positive(mask, dates, wins, dec):
    if dates is None:return None
    years=pd.to_datetime(dates,errors='coerce').dt.year
    vals=[]
    for y in sorted(years[mask].dropna().unique()):
        r=roi_of(mask&(years==y),wins,dec)
        if r and r[1]>=5: vals.append(r[0]>0)
    return float(np.mean(vals)) if vals else None

def candidate_metrics(mask,df,wins,dec,datec,split):
    full=roi_of(mask,wins,dec)
    if not full:return None
    train=roi_of(mask & (np.arange(len(df))<split),wins,dec)
    test=roi_of(mask & (np.arange(len(df))>=split),wins,dec)
    half=len(df)//2
    early=roi_of(mask & (np.arange(len(df))<half),wins,dec)
    recent=roi_of(mask & (np.arange(len(df))>=half),wins,dec)
    if not train or not test:return None
    return {'bets':full[1],'wins':full[2],'losses':full[1]-full[2],'win_rate':full[2]/full[1],
            'roi':full[0],'train_roi':train[0],'train_bets':train[1],'holdout_roi':test[0],'holdout_bets':test[1],
            'early_roi':early[0] if early else None,'recent_roi':recent[0] if recent else None,
            'yearly_positive_ratio':yearly_positive(mask,df[datec] if datec else None,wins,dec)}

def eligible_features(df, excluded):
    out=[]
    for c in df.columns:
        if c in excluded: continue
        if not pd.api.types.is_numeric_dtype(df[c]): continue
        lc=c.lower()
        if any(x in lc for x in ['won','result','profit','roi','odds','moneyline','prob','year','id']): continue
        s=pd.to_numeric(df[c],errors='coerce')
        if s.notna().sum()<200 or s.nunique(dropna=True)<5: continue
        out.append(c)
    priority=[c for c in out if c.startswith(('f_','o_')) or 'diff' in c.lower() or 'gap' in c.lower()]
    rest=[c for c in out if c not in priority]
    return (priority+rest)[:80]

def rule_fingerprint(rule): return hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest()[:24]

def passes(m):
    return (m['bets']>=40 and m['train_bets']>=25 and m['holdout_bets']>=12 and
            m['train_roi']>=0.05 and m['holdout_roi']>0 and m['roi']>=0.05 and
            (m['early_roi'] is None or m['early_roi']>-0.02) and
            (m['recent_roi'] is None or m['recent_roi']>0) and
            (m['yearly_positive_ratio'] is None or m['yearly_positive_ratio']>=0.55))

def evaluate_rule(df,wins,dec,datec,split,conds):
    mask=pd.Series(True,index=df.index)
    for c,op,t in conds:
        x=pd.to_numeric(df[c],errors='coerce')
        mask &= (x>=t) if op=='>=' else (x<=t)
    return candidate_metrics(mask,df,wins,dec,datec,split)

def save_candidate(c,run_id,source):
    fp=rule_fingerprint(c['rule']); m=c['metrics']; ts=now()
    with conn() as db:
        old=db.execute("SELECT times_seen,official,status FROM candidates WHERE fingerprint=?",(fp,)).fetchone()
        if old:
            db.execute('''UPDATE candidates SET last_seen=?,times_seen=?,bets=?,wins=?,losses=?,win_rate=?,roi=?,train_roi=?,holdout_roi=?,early_roi=?,recent_roi=?,yearly_positive_ratio=? WHERE fingerprint=?''',
                       (ts,old[0]+1,m['bets'],m['wins'],m['losses'],m['win_rate'],m['roi'],m['train_roi'],m['holdout_roi'],m['early_roi'],m['recent_roi'],m['yearly_positive_ratio'],fp))
        else:
            db.execute('''INSERT INTO candidates(fingerprint,name,rule_json,source_dataset,first_seen,last_seen,bets,wins,losses,win_rate,roi,train_roi,holdout_roi,early_roi,recent_roi,yearly_positive_ratio,status,official,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (fp,c['name'],json.dumps(c['rule'],sort_keys=True),source,ts,ts,m['bets'],m['wins'],m['losses'],m['win_rate'],m['roi'],m['train_roi'],m['holdout_roi'],m['early_roi'],m['recent_roi'],m['yearly_positive_ratio'],'shadow_candidate',0,'Never auto-promote; explicit user approval required.'))
        db.execute("INSERT INTO candidate_observations(run_id,fingerprint,observed_at,metrics_json) VALUES(?,?,?,?)",(run_id,fp,ts,json.dumps(m,sort_keys=True)))
        db.commit()
    return fp

def research(mode):
    ok,mem,load=resources_ok(1200 if mode!='monthly' else 1500,1.5)
    if not ok:
        log(f"Research skipped by resource guard: MemAvailable={mem:.0f}MB load1={load:.2f}")
        return {'status':'skipped_resource_guard','mem_mb':mem,'load':load,'survivors':0,'tested':0}
    if not PREFIGHT.exists(): return {'status':'missing_prefight_dataset','survivors':0,'tested':0}
    df=pd.read_csv(PREFIGHT,low_memory=False)
    datec,winc,probc,oddsc=schema(df)
    if datec:
        d=pd.to_datetime(df[datec],errors='coerce'); df=df.assign(_sortdate=d).sort_values('_sortdate').drop(columns='_sortdate').reset_index(drop=True)
    if not winc: return {'status':'missing_outcome_column','columns':list(df.columns),'survivors':0,'tested':0}
    wins=normalize_win(df[winc]); dec=decimal_odds(df,probc,oddsc)
    valid=wins.notna()&dec.notna()&(dec>1)
    df=df.loc[valid].reset_index(drop=True); wins=wins.loc[valid].reset_index(drop=True); dec=dec.loc[valid].reset_index(drop=True)
    split=max(1,int(len(df)*0.70)); dh=sha_file(PREFIGHT)
    with conn() as db:
        cur=db.execute("INSERT INTO research_runs(started_at,mode,dataset_sha,dataset_rows,status) VALUES(?,?,?,?,?)",(now(),mode,dh,len(df),'running')); run_id=cur.lastrowid; db.commit()
    excluded={x for x in [datec,winc,probc,oddsc] if x}
    feats=eligible_features(df,excluded)
    tested=0; survivors=[]; singles=[]
    qset=[.25,.40,.60,.75] if mode=='daily' else [.20,.30,.40,.50,.60,.70,.80]
    for c in feats:
        tr=pd.to_numeric(df.loc[:split-1,c],errors='coerce').dropna()
        if len(tr)<100: continue
        vals=sorted(set(float(tr.quantile(q)) for q in qset if np.isfinite(tr.quantile(q))))
        for t in vals:
            for op in ('>=','<='):
                rule=[(c,op,round(t,8))]; m=evaluate_rule(df,wins,dec,datec,split,rule); tested+=1
                if m and m['train_bets']>=25 and m['train_roi']>=.04: singles.append((m['train_roi'],rule,m))
                if m and passes(m): survivors.append({'name':f"{c} {op} {t:.4g}",'rule':rule,'metrics':m})
    if mode in ('weekly','monthly'):
        top=sorted(singles,key=lambda z:(z[0],z[2]['train_bets']),reverse=True)[:24 if mode=='weekly' else 36]
        seen=set()
        for i in range(len(top)):
            for j in range(i+1,len(top)):
                r1,r2=top[i][1],top[j][1]
                if r1[0][0]==r2[0][0]: continue
                rule=r1+r2; key=json.dumps(rule,sort_keys=True)
                if key in seen: continue
                seen.add(key); m=evaluate_rule(df,wins,dec,datec,split,rule); tested+=1
                if m and passes(m):
                    survivors.append({'name':f"{rule[0][0]} {rule[0][1]} {rule[0][2]:.4g} + {rule[1][0]} {rule[1][1]} {rule[1][2]:.4g}",'rule':rule,'metrics':m})
    # De-duplicate and rank without auto-promoting.
    uniq={rule_fingerprint(c['rule']):c for c in survivors}
    survivors=list(uniq.values())
    survivors.sort(key=lambda c:(c['metrics']['holdout_roi'],c['metrics']['bets']),reverse=True)
    survivors=survivors[:50]
    for c in survivors: save_candidate(c,run_id,str(PREFIGHT))
    with conn() as db:
        db.execute("UPDATE research_runs SET finished_at=?,candidates_tested=?,survivors=?,status=?,note=? WHERE id=?",(now(),tested,len(survivors),'completed',f"{len(feats)} numeric features; 70/30 chronological holdout; no automatic promotion",run_id)); db.commit()
    log(f"{mode} research completed: tested={tested}, survivors={len(survivors)}, rows={len(df)}")
    return {'status':'completed','run_id':run_id,'tested':tested,'survivors':len(survivors),'rows':len(df),'top':survivors[:8]}

def envvals():
    vals={}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
    vals.update({k:v for k,v in os.environ.items() if k in ('RESEND_API_KEY','UFC_ALERT_EMAIL','UFC_ALERT_FROM')})
    return vals

def top_candidates(limit=5):
    with conn() as db:
        rows=db.execute('''SELECT fingerprint,name,bets,wins,losses,win_rate,roi,train_roi,holdout_roi,recent_roi,yearly_positive_ratio,times_seen FROM candidates WHERE official=0 AND status='shadow_candidate' ORDER BY holdout_roi DESC,bets DESC LIMIT ?''',(limit,)).fetchall()
    return rows

def counts():
    with conn() as db:
        runs=db.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
        shadows=db.execute("SELECT COUNT(*) FROM candidates WHERE status='shadow_candidate' AND official=0").fetchone()[0]
        snaps=db.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0]
        last=db.execute("SELECT mode,finished_at,dataset_rows,candidates_tested,survivors,status FROM research_runs ORDER BY id DESC LIMIT 1").fetchone()
    return runs,shadows,snaps,last

def send_digest(extra=None):
    vals=envvals(); key=vals.get('RESEND_API_KEY'); to=vals.get('UFC_ALERT_EMAIL'); sender=vals.get('UFC_ALERT_FROM','UFC Model <onboarding@resend.dev>')
    if not key or not to:
        log('Daily research email skipped: Resend configuration unavailable'); return False
    runs,shadows,snaps,last=counts(); tops=top_candidates(5)
    tr=''.join(f"<tr><td>{html.escape(r[1])}</td><td>{r[2]}</td><td>{r[3]}-{r[4]}</td><td>{100*r[6]:+.1f}%</td><td>{100*r[8]:+.1f}%</td><td>{r[11]}</td></tr>" for r in tops)
    if not tr: tr='<tr><td colspan="6">No shadow candidate currently clears the robustness gates.</td></tr>'
    lasttxt='None yet' if not last else f"{last[0]} · {last[5]} · {last[2] or 0} rows · {last[3] or 0:,} tested · {last[4] or 0} survivors"
    body=f'''<div style="font-family:Arial,sans-serif;max-width:900px;margin:auto;color:#172033">
    <h2>UFC Research Daily</h2>
    <p><b>Research is autonomous; promotion is not.</b> No candidate can enter the official/live arsenal without explicit approval.</p>
    <p><b>Last research:</b> {html.escape(lasttxt)}<br><b>Research runs retained:</b> {runs}<br><b>Shadow candidates retained:</b> {shadows}<br><b>Immutable source snapshots retained:</b> {snaps}</p>
    <h3>Best current shadow candidates</h3>
    <table style="border-collapse:collapse;width:100%"><tr><th align="left">Rule</th><th>Bets</th><th>W-L</th><th>ROI</th><th>Holdout ROI</th><th>Seen</th></tr>{tr}</table>
    <p style="font-size:13px;color:#596273">Candidates are screened with a chronological 70/30 train/holdout split, minimum sample gates, era consistency checks and yearly consistency checks. These are research findings, not official selections.</p>
    </div>'''
    payload=json.dumps({'from':sender,'to':[to],'subject':f"UFC Research Daily — {datetime.now().strftime('%Y-%m-%d')}",'html':body}).encode()
    req=Request('https://api.resend.com/emails',data=payload,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=30) as r: ok=200<=r.status<300
        log(f"Daily research email {'sent' if ok else 'failed'}"); return ok
    except Exception as e:
        log(f"Daily research email error: {e}"); return False

def write_report(result):
    p=REPORTS/f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{result.get('mode','run')}.json"
    p.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8'); return p

def run_mode(mode):
    if not acquire_lock():
        log('Another UFC autoresearch process owns the lock; skipping'); return 0
    try:
        changed,h=snapshot_source()
        result={'mode':mode,'timestamp':now(),'source_changed':changed,'source_sha':h}
        if mode=='snapshot': pass
        elif mode=='daily': result['research']=research('daily'); send_digest(result)
        elif mode=='weekly': result['research']=research('weekly')
        elif mode=='monthly': result['research']=research('monthly')
        elif mode=='digest': send_digest(result)
        write_report(result)
        return 0
    finally: release_lock()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('mode',choices=['snapshot','daily','weekly','monthly','digest']); a=ap.parse_args(); raise SystemExit(run_mode(a.mode))
if __name__=='__main__': main()
