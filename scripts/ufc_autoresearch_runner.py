#!/usr/bin/env python3
import hashlib, html, json, os, shutil
from datetime import datetime
from pathlib import Path
import ufc_autoresearch as ar

ROOT=Path.home()/"ufc-predictor-v1"
for name in ["prefight_favorite_features_v6.csv","prefight_favorite_features_v5.csv","prefight_favorite_features_v4.csv","prefight_favorite_features_v3.csv","prefight_favorite_features_v2.csv"]:
    p=ROOT/"feature_expansion"/name
    if p.exists():
        ar.PREFIGHT=p
        break

OFFICIAL_ARCHIVE=ROOT/"auto_research"/"official_history"
OFFICIAL_ARCHIVE.mkdir(parents=True,exist_ok=True)
PUBLIC_STATE=Path('/srv/appwiza-sports/state/ufc.json')
LEDGER_DB=Path.home()/"betting-ledger"/"assumed_bets.sqlite3"

_base_schema=ar.schema
_base_snapshot=ar.snapshot_source
_base_eligible=ar.eligible_features

def schema(df):
    datec,winc,probc,oddsc=_base_schema(df)
    if 'fav_decimal' in df.columns: oddsc='fav_decimal'
    elif 'favorite_decimal_odds' in df.columns: oddsc='favorite_decimal_odds'
    return datec,winc,probc,oddsc
ar.schema=schema

def eligible_features(df,excluded):
    base=_base_eligible(df,excluded); extra=[]
    prefixes=('f2_','f3_','f4_','f5_','f6_')
    for c in df.columns:
        if not c.startswith(prefixes) or c in excluded: continue
        try:
            s=df[c]
            if s.notna().sum()>=120 and s.nunique(dropna=True)>=2 and str(s.dtype)!='object': extra.append(c)
        except Exception: pass
    extra=sorted(set(extra),key=lambda c:(0 if ('_diff_' in c or c.endswith('_adv')) else 1,c))
    out=[];seen=set()
    for c in extra+base:
        if c not in seen: seen.add(c);out.append(c)
    return out[:220]
ar.eligible_features=eligible_features

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def archive_one(path,label,suffix):
    if not path.exists() or not os.access(path,os.R_OK): return None
    h=digest(path);current=OFFICIAL_ARCHIVE/f'{label}_current{suffix}';old=digest(current) if current.exists() else None
    if h==old:return None
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S');dst=OFFICIAL_ARCHIVE/f'{label}_{stamp}_{h[:10]}{suffix}'
    shutil.copy2(path,dst);shutil.copy2(path,current)
    with ar.conn() as db:
        try:
            db.execute("INSERT OR IGNORE INTO source_snapshots(captured_at,source,sha256,path,rows,bytes) VALUES(?,?,?,?,?,?)",(ar.now(),label,h,str(dst),None,dst.stat().st_size))
            db.execute("INSERT INTO data_events(observed_at,source,event_type,detail_json) VALUES(?,?,?,?)",(ar.now(),label,'official_history_changed',json.dumps({'previous_sha':old,'new_sha':h,'archive':str(dst)})));db.commit()
        except Exception:pass
    ar.log(f'Archived Appwiza {label} history: {dst.name}');return dst

def snapshot_source(send_event=True):
    result=_base_snapshot(send_event);archive_one(PUBLIC_STATE,'appwiza_ufc_state','.json');archive_one(LEDGER_DB,'appwiza_betting_ledger','.sqlite3');return result
ar.snapshot_source=snapshot_source

def official_history_summary():
    cards=settled=active=0
    try:
        d=json.loads(PUBLIC_STATE.read_text());cards=len(d.get('cards',[]))
        for x in d.get('cards',[]):
            txt=json.dumps((x.get('tracking') if isinstance(x,dict) else None) or {}).lower()
            if any(k in txt for k in ('won','lost','settled','win','loss')):settled+=1
            if not x.get('withdrawn'):active+=1
    except Exception:pass
    return cards,active,settled

def send_digest(extra=None):
    vals=ar.envvals()
    for k in ('RESEND_API_KEY','UFC_ALERT_EMAIL','UFC_ALERT_FROM'):
        if vals.get(k):os.environ[k]=vals[k]
    runs,shadows,snaps,last=ar.counts();cards,active,settled=official_history_summary();tops=ar.top_candidates(5)
    rows=''.join(f"<tr><td>{html.escape(r[1])}</td><td>{r[2]}</td><td>{r[3]}-{r[4]}</td><td>{100*r[6]:+.1f}%</td><td>{100*r[8]:+.1f}%</td><td>{r[11]}</td></tr>" for r in tops) or '<tr><td colspan="6">No shadow candidate currently clears the robustness gates.</td></tr>'
    lasttxt='None yet' if not last else f"{last[0]} · {last[5]} · {last[2] or 0} rows · {last[3] or 0:,} tested · {last[4] or 0} survivors"
    body=f'''<div style="font-family:Arial,sans-serif;max-width:900px;margin:auto;color:#172033"><h2>UFC Research Daily</h2><p><b>Research is autonomous; promotion is not.</b> No candidate can enter the official/live arsenal without explicit approval.</p><p><b>Research dataset:</b> {html.escape(ar.PREFIGHT.name)}<br><b>Last research:</b> {html.escape(lasttxt)}<br><b>Research runs retained:</b> {runs}<br><b>Shadow candidates retained:</b> {shadows}<br><b>Immutable source/official snapshots retained:</b> {snaps}</p><p><b>Appwiza UFC tracker:</b> {cards} stored cards · {active} not withdrawn · {settled} with settlement-like tracking state.</p><h3>Best current shadow candidates</h3><table style="border-collapse:collapse;width:100%"><tr><th align="left">Rule</th><th>Bets</th><th>W-L</th><th>ROI</th><th>Holdout ROI</th><th>Seen</th></tr>{rows}</table><p style="font-size:13px;color:#596273">Research now includes opponent strength, damage/decline, cardio/round trends, style, rankings, stance/height, documented weight-cut history, win/loss streaks, title experience, actual weigh-in weights, division movement and decision volatility. These are research findings, not official selections.</p></div>'''
    try:
        import ufc_email_watcher as watcher;watcher.send_email(f"UFC Research Daily — {datetime.now().strftime('%Y-%m-%d')}",body);ar.log('Daily research email sent through official watcher transport');return True
    except Exception as e:ar.log(f'Daily research email through watcher failed: {e}');return False
ar.send_digest=send_digest

if __name__=='__main__':ar.main()
