#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, sqlite3, subprocess, sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
STATE=ROOT/"auto_research"/"state"
WARE=ROOT/"auto_research"/"warehouse"
DB=STATE/"immutable_warehouse.sqlite3"
PDB=STATE/"prospective_ufc.sqlite3"
BASE=ROOT/"new_category_discovery"/"prefight_favorite_features.csv"
RAW=ROOT/"raw"/"competitions.csv"
IND=ROOT/"raw"/"individuals.csv"
WATCHER=ROOT/"ufc_email_watcher.py"
for p in (STATE,WARE,BASE.parent,RAW.parent):p.mkdir(parents=True,exist_ok=True)

def utcnow(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def num(v,default=np.nan):
    try:return float(v)
    except:return default

def parse_of(v):
    if pd.isna(v):return 0.0,0.0
    m=re.search(r'(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)',str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.0,0.0)
def parse_ctrl(v):
    if pd.isna(v):return 0.0
    m=re.match(r'\s*(\d+):(\d+)\s*$',str(v))
    return (60*int(m.group(1))+int(m.group(2)))/60.0 if m else 0.0
def parse_single(v):
    if pd.isna(v):return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v));return float(m.group()) if m else 0.0
def fight_minutes(r):
    try:rnd=max(1,int(float(r.get('round',1) or 1)))
    except:rnd=1
    m=re.match(r'(\d+):(\d+)',str(r.get('time','0:00')))
    sec=(int(m.group(1))*60+int(m.group(2))) if m else 0
    return max(1,((rnd-1)*300+sec))/60.0

def connect():
    c=sqlite3.connect(PDB)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS feed_snapshots(sha TEXT PRIMARY KEY,captured_at TEXT NOT NULL,payload_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bout_quotes(
      id INTEGER PRIMARY KEY,event_date TEXT,event_start TEXT,fighter_a TEXT,fighter_b TEXT,
      p_a REAL,p_b REAL,dec_a REAL,dec_b REAL,book TEXT,quote_at TEXT,captured_at TEXT NOT NULL,
      snapshot_sha TEXT NOT NULL,market_json TEXT,UNIQUE(snapshot_sha,event_date,fighter_a,fighter_b));
    CREATE TABLE IF NOT EXISTS finalized_bouts(
      fight_key TEXT PRIMARY KEY,finalized_at TEXT NOT NULL,quote_id INTEGER,snapshot_sha TEXT,base_row_json TEXT NOT NULL);
    ''');c.commit();return c

def load_watcher():
    spec=importlib.util.spec_from_file_location('ufcw',WATCHER);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);return w

def to_decimal(v):
    x=num(v)
    if not np.isfinite(x):return np.nan
    if x>1.0 and x<20:return x
    if x<=-100:return 1+100/abs(x)
    if x>=100:return 1+x/100
    return np.nan

def market_values(m):
    if not isinstance(m,dict):m={}
    def first(keys):
        for k in keys:
            if k in m and m.get(k) not in (None,''):return m.get(k)
        return None
    pa=num(first(['consensus_no_vig_a','no_vig_a','prob_a','market_prob_a']))
    pb=num(first(['consensus_no_vig_b','no_vig_b','prob_b','market_prob_b']))
    da=to_decimal(first(['best_decimal_a','decimal_a','price_a','best_price_a','odds_a','american_a']))
    db=to_decimal(first(['best_decimal_b','decimal_b','price_b','best_price_b','odds_b','american_b']))
    if not np.isfinite(da) and np.isfinite(pa) and pa>0:da=1/pa
    if not np.isfinite(db) and np.isfinite(pb) and pb>0:db=1/pb
    book=str(first(['book','sportsbook','best_book']) or '')
    qa=str(first(['quote_at','updated_at','timestamp']) or '')
    return pa,pb,da,db,book,qa

def capture():
    w=load_watcher();feed=w.fetch_feed();raw=json.dumps(feed,sort_keys=True,default=str);sha=hashlib.sha256(raw.encode()).hexdigest();cap=utcnow()
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO feed_snapshots(sha,captured_at,payload_json) VALUES(?,?,?)',(sha,cap,raw))
        added=0
        for e in (feed.get('events') or []):
            ed=str(e.get('date') or e.get('event_date') or '')[:10]
            estart=str(e.get('start') or e.get('start_time') or e.get('date_time') or '')
            for b in (e.get('bouts') or []):
                a=str(b.get('fighter_a') or b.get('a') or b.get('player1') or '').strip();bb=str(b.get('fighter_b') or b.get('b') or b.get('player2') or '').strip()
                if not a or not bb:continue
                m=b.get('market') or {};pa,pb,da,db,book,qa=market_values(m)
                cur=c.execute('''INSERT OR IGNORE INTO bout_quotes(event_date,event_start,fighter_a,fighter_b,p_a,p_b,dec_a,dec_b,book,quote_at,captured_at,snapshot_sha,market_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(ed,estart,a,bb,pa,pb,da,db,book,qa,cap,sha,json.dumps(m,sort_keys=True,default=str)))
                added+=cur.rowcount
        c.commit()
    print(json.dumps({'capture':'ok','snapshot_sha':sha,'quotes_added':added,'captured_at':cap}))

def canonical_frame():
    if not DB.exists():raise RuntimeError('immutable warehouse DB missing')
    c=sqlite3.connect(DB)
    rows=c.execute("SELECT row_json FROM canonical_rows WHERE source='ufcstats'").fetchall();c.close()
    arr=[json.loads(x[0]) for x in rows];d=pd.DataFrame(arr)
    if d.empty:raise RuntimeError('no canonical UFCStats rows')
    d['event_date']=pd.to_datetime(d['event_date'],errors='coerce').dt.normalize();d=d[d.event_date.notna()].sort_values(['event_date','event_url','player1','player2']).reset_index(drop=True)
    return d

def export_canonical_raw(d):
    x=d.copy();x['event_date']=x.event_date.dt.strftime('%Y-%m-%d');x.to_csv(RAW,index=False)

def new_state():return {'fights':0,'wins':0,'losses':0,'mins':0.0,'sig_l':0.0,'sig_a':0.0,'sig_abs':0.0,'sig_abs_a':0.0,'kd':0.0,'kd_abs':0.0,'td_l':0.0,'td_a':0.0,'td_allowed':0.0,'td_faced':0.0,'sub':0.0,'ctrl':0.0,'ctrl_allowed':0.0,'ground_l':0.0,'finish_wins':0,'finish_losses':0,'last_date':None,'recent':deque(maxlen=5)}
def prof(q,target_date):
    if not q or q['fights']<=0 or q['mins']<=0:return None
    sc15=15.0/q['mins'];m=q['mins'];last3=list(q['recent'])[-3:];last5=list(q['recent'])[-5:]
    return {'fights':q['fights'],'wins':q['wins'],'losses':q['losses'],'win_pct':q['wins']/max(1,q['wins']+q['losses']),'last3':float(np.mean(last3)) if last3 else np.nan,'last5':float(np.mean(last5)) if last5 else np.nan,'layoff':(target_date-q['last_date']).days if q['last_date'] is not None else np.nan,'sig_l_pm':q['sig_l']/m,'sig_abs_pm':q['sig_abs']/m,'sig_diff_pm':(q['sig_l']-q['sig_abs'])/m,'sig_acc':q['sig_l']/q['sig_a'] if q['sig_a']>0 else np.nan,'sig_def':1-q['sig_abs']/q['sig_abs_a'] if q['sig_abs_a']>0 else np.nan,'kd15':q['kd']*sc15,'kd_abs15':q['kd_abs']*sc15,'td_l15':q['td_l']*sc15,'td_a15':q['td_a']*sc15,'td_acc':q['td_l']/q['td_a'] if q['td_a']>0 else np.nan,'td_def':1-q['td_allowed']/q['td_faced'] if q['td_faced']>0 else np.nan,'sub15':q['sub']*sc15,'ctrl15':q['ctrl']*sc15,'ctrl_allowed15':q['ctrl_allowed']*sc15,'ground_l15':q['ground_l']*sc15,'finish_win_pct':q['finish_wins']/q['wins'] if q['wins']>0 else np.nan,'finish_loss_pct':q['finish_losses']/q['losses'] if q['losses']>0 else np.nan}
def update_state(r,states):
    d=r.event_date;res=str(r.get('result','')).upper();p1win=res.startswith('W');p2win=res.startswith('L');method=str(r.get('method','')).upper();finish=('KO' in method or 'TKO' in method or 'SUB' in method);mins=fight_minutes(r)
    for side,name,opp,won,lost in [(1,norm(r.get('player1')),2,p1win,p2win),(2,norm(r.get('player2')),1,p2win,p1win)]:
        if not name:continue
        q=states.setdefault(name,new_state());sl=sa=sabs=sabsa=kd=kda=tdl=tda=tdal=tdf=sub=ct=cta=gr=0.0
        for rd in range(1,6):
            a,b=parse_of(r.get(f'p{side}_rd{rd}_Sig_str'));sl+=a;sa+=b;a,b=parse_of(r.get(f'p{opp}_rd{rd}_Sig_str'));sabs+=a;sabsa+=b
            kd+=parse_single(r.get(f'p{side}_rd{rd}_KD'));kda+=parse_single(r.get(f'p{opp}_rd{rd}_KD'));a,b=parse_of(r.get(f'p{side}_rd{rd}_Td'));tdl+=a;tda+=b;a,b=parse_of(r.get(f'p{opp}_rd{rd}_Td'));tdal+=a;tdf+=b
            sub+=parse_single(r.get(f'p{side}_rd{rd}_Sub_att'));ct+=parse_ctrl(r.get(f'p{side}_rd{rd}_Ctrl'));cta+=parse_ctrl(r.get(f'p{opp}_rd{rd}_Ctrl'));a,_=parse_of(r.get(f'p{side}_rd{rd}_Ground'));gr+=a
        q['fights']+=1;q['mins']+=mins;q['sig_l']+=sl;q['sig_a']+=sa;q['sig_abs']+=sabs;q['sig_abs_a']+=sabsa;q['kd']+=kd;q['kd_abs']+=kda;q['td_l']+=tdl;q['td_a']+=tda;q['td_allowed']+=tdal;q['td_faced']+=tdf;q['sub']+=sub;q['ctrl']+=ct;q['ctrl_allowed']+=cta;q['ground_l']+=gr;q['last_date']=d
        if won:q['wins']+=1;q['recent'].append(1);q['finish_wins']+=int(finish)
        elif lost:q['losses']+=1;q['recent'].append(0);q['finish_losses']+=int(finish)

def individual_map():
    if not IND.exists():return {}
    d=pd.read_csv(IND,low_memory=False);return {norm(r.get('name')):r for _,r in d.iterrows()}
def parse_reach(v):
    m=re.search(r'\d+(?:\.\d+)?',str(v or ''));return float(m.group()) if m else np.nan
def age_on(rec,dt):
    try:return (dt-pd.Timestamp(rec.get('dob'))).days/365.2425
    except:return np.nan

def quote_map():
    with connect() as c:q=pd.read_sql_query('SELECT * FROM bout_quotes ORDER BY captured_at',c)
    if q.empty:return {}
    q['_ka']=q.fighter_a.map(norm);q['_kb']=q.fighter_b.map(norm);q['_key']=q.apply(lambda r:(str(r.event_date),)+tuple(sorted((r._ka,r._kb))),axis=1)
    out={}
    now=pd.Timestamp.now(tz='UTC')
    for k,g in q.groupby('_key'):
        valid=[]
        for _,r in g.iterrows():
            cap=pd.to_datetime(r.captured_at,utc=True,errors='coerce');st=pd.to_datetime(r.event_start,utc=True,errors='coerce')
            if pd.notna(st) and pd.notna(cap) and cap<st:valid.append(r)
        if valid:out[k]=valid[-1]
    return out

def finalize():
    d=canonical_frame();export_canonical_raw(d);quotes=quote_map();base=pd.read_csv(BASE,low_memory=False) if BASE.exists() else pd.DataFrame();existing=set()
    if len(base):
        for r in base.itertuples():existing.add((str(pd.Timestamp(r.event_date).date()),)+tuple(sorted((norm(r.favorite),norm(r.opponent)))))
    im=individual_map();states={};new=[];seen_fights=set()
    with connect() as pc:
        fin={x[0] for x in pc.execute('SELECT fight_key FROM finalized_bouts')}
        for dt,grp in d.groupby('event_date',sort=True):
            snaps={}
            for _,r in grp.iterrows():
                a,b=norm(r.get('player1')),norm(r.get('player2'));k=(str(dt.date()),)+tuple(sorted((a,b)));snaps[k]=(prof(states.get(a),dt),prof(states.get(b),dt),r)
            for k,(p1,p2,r) in snaps.items():
                if k in existing or '|'.join(k) in fin:continue
                q=quotes.get(k)
                if q is None or p1 is None or p2 is None:continue
                pa,pb=num(q.p_a),num(q.p_b);da,db=num(q.dec_a),num(q.dec_b)
                if not all(np.isfinite(x) for x in [pa,pb,da,db]) or max(pa,pb)<=0:continue
                fav1=pa>=pb;fp,op=(p1,p2) if fav1 else (p2,p1);fav=str(r.get('player1') if fav1 else r.get('player2'));opp=str(r.get('player2') if fav1 else r.get('player1'));dec=da if fav1 else db;mkt=max(pa,pb)
                res=str(r.get('result','')).upper();won=res.startswith('W') if fav1 else res.startswith('L')
                fr=im.get(norm(fav));orr=im.get(norm(opp));fa=age_on(fr,dt) if fr is not None else np.nan;oa=age_on(orr,dt) if orr is not None else np.nan;fre=parse_reach(fr.get('reach')) if fr is not None else np.nan;ore=parse_reach(orr.get('reach')) if orr is not None else np.nan
                row={'event_date':str(dt.date()),'favorite':fav,'opponent':opp,'market_prob':mkt,'fav_decimal':dec,'profit100':100*(dec-1) if won else -100.0,'won':bool(won),'age_adv':oa-fa if pd.notna(fa) and pd.notna(oa) else np.nan,'reach_adv':fre-ore if pd.notna(fre) and pd.notna(ore) else np.nan,'prospective_quote_at':q.quote_at or q.captured_at,'prospective_captured_at':q.captured_at,'prospective_snapshot_sha':q.snapshot_sha,'prospective_book':q.book,'prospective_event_start':q.event_start,'prospective_point_in_time':True}
                row.update({f'f_{x}':v for x,v in fp.items()});row.update({f'o_{x}':v for x,v in op.items()});new.append(row)
                fk='|'.join(k);pc.execute('INSERT OR REPLACE INTO finalized_bouts(fight_key,finalized_at,quote_id,snapshot_sha,base_row_json) VALUES(?,?,?,?,?)',(fk,utcnow(),int(q.id),q.snapshot_sha,json.dumps(row,default=str)))
            for _,r in grp.iterrows():update_state(r,states)
        pc.commit()
    if not new:
        print(json.dumps({'finalize':'ok','new_rows':0,'canonical_rows':len(d),'base_rows':len(base)}));return 0
    nd=pd.DataFrame(new);combined=pd.concat([base,nd],ignore_index=True,sort=False);combined.to_csv(BASE,index=False)
    scripts=['ufc_feature_expansion.py','ufc_metadata_features.py','ufc_weight_history_features.py','ufc_context_crosscheck_features.py','ufc_career_context_features.py']
    for s in scripts:
        p=ROOT/s
        if not p.exists():p=ROOT/'scripts'/s
        subprocess.run([str(ROOT/'venv/bin/python'),str(p)],cwd=ROOT,check=True)
    print(json.dumps({'finalize':'ok','new_rows':len(nd),'base_rows':len(combined),'v6':str(ROOT/'feature_expansion/prefight_favorite_features_v6.csv')}));return len(nd)

def status():
    with connect() as c:
        print(json.dumps({'snapshots':c.execute('select count(*) from feed_snapshots').fetchone()[0],'quotes':c.execute('select count(*) from bout_quotes').fetchone()[0],'finalized':c.execute('select count(*) from finalized_bouts').fetchone()[0],'base_rows':len(pd.read_csv(BASE,low_memory=False)) if BASE.exists() else 0,'v6_rows':len(pd.read_csv(ROOT/'feature_expansion/prefight_favorite_features_v6.csv',low_memory=False)) if (ROOT/'feature_expansion/prefight_favorite_features_v6.csv').exists() else 0},indent=2))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['capture','finalize','status']);a=ap.parse_args();{'capture':capture,'finalize':finalize,'status':status}[a.mode]()
if __name__=='__main__':main()
