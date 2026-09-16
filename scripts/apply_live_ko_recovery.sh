#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
[ -f "$WATCHER" ] || { echo "missing watcher"; exit 2; }
cp "$WATCHER" "$WATCHER.pre-u10-ko-recovery.$(date +%Y%m%d-%H%M%S).bak"
python - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
if 'KO_RECOVERY_RULE_V1' in s:
    print('U10 already present'); raise SystemExit(0)
marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('main marker missing')
addon=r'''
# KO_RECOVERY_RULE_V1
# Official method U10 — KO/TKO Recovery Gap
# Historical point-in-time backtest: 333 bets, 238-95, 71.5% wins, +8.24% ROI.
# Pre-2020 +5.65%; 2020+ +10.85%.
# Rule: both fighters must have a prior UFC KO/TKO loss; favorite's most recent
# KO/TKO loss must be at least 105 days farther in the past than opponent's.
_KR_INDEX=None
_KR_CACHE=ROOT/'ko_recovery_index.json'
_KR_CACHE_MAX_AGE=86400
KO_RECOVERY_HISTORY={'bets':333,'wins':238,'losses':95,'win_rate':238/333,'roi':0.08244202474944022,'pre_roi':0.05652570342971997,'recent_roi':0.10851446848674913}

def load_ko_recovery_index():
    global _KR_INDEX
    if _KR_INDEX is not None:return _KR_INDEX
    import json as _json,time as _time
    if _KR_CACHE.exists():
        try:
            if (_time.time()-_KR_CACHE.stat().st_mtime)<=_KR_CACHE_MAX_AGE:
                z=_json.loads(_KR_CACHE.read_text())
                if isinstance(z,dict): _KR_INDEX=z; return z
        except Exception: pass
    try:
        if 'load_skill_index' in globals():
            try: load_skill_index()
            except Exception: pass
        raw=globals().get('SKILL_RAW_CACHE_PATH',ROOT/'ufcstats_competitions_skill_cache.csv')
        if not raw.exists(): _KR_INDEX={}; return _KR_INDEX
        df=pd.read_csv(raw,low_memory=False)
    except Exception:
        _KR_INDEX={}; return _KR_INDEX
    out={}
    for _,row in df.iterrows():
        method=str(row.get('method') or '').upper()
        if not ('KO' in method or 'TKO' in method): continue
        try: dt=pd.Timestamp(row.get('event_date')).normalize()
        except Exception: continue
        res=str(row.get('result') or '').strip().upper()
        loser=''
        if res.startswith('W'): loser=str(row.get('player2') or '').strip()
        elif res.startswith('L'): loser=str(row.get('player1') or '').strip()
        if not loser: continue
        k=norm_name(loser)
        out.setdefault(k,[]).append(dt.date().isoformat())
    for k,v in out.items(): out[k]=sorted(set(v))
    try:_KR_CACHE.write_text(_json.dumps(out))
    except Exception:pass
    _KR_INDEX=out;return out

def _kr_dates(name,idx):
    k=norm_name(name)
    if k in idx:return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_ko_recovery(favorite,opponent,event_date):
    idx=load_ko_recovery_index();fd=_kr_dates(favorite,idx or {});od=_kr_dates(opponent,idx or {})
    r={'available':False,'qualifies':False,'favorite':favorite,'opponent':opponent,'favorite_days':None,'opponent_days':None,'gap_days':None,'threshold_days':105}
    if not fd or not od:return r
    try: ed=pd.Timestamp(event_date).normalize()
    except Exception:return r
    fprior=[pd.Timestamp(x) for x in fd if pd.Timestamp(x)<ed]; oprior=[pd.Timestamp(x) for x in od if pd.Timestamp(x)<ed]
    if not fprior or not oprior:return r
    fl=max(fprior);ol=max(oprior); fdays=(ed-fl).days; odays=(ed-ol).days; gap=fdays-odays
    r.update({'available':True,'favorite_last_ko_loss':fl.date().isoformat(),'opponent_last_ko_loss':ol.date().isoformat(),'favorite_days':fdays,'opponent_days':odays,'gap_days':gap,'qualifies':bool(gap>=105)})
    return r

_orig_predict_bout_kr=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_kr(*args,**kwargs)
    bout=args[0] if args else kwargs.get('bout',{})
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    a=str((bout or {}).get('fighter_a') or out.get('fighter_a') or '').strip(); b=str((bout or {}).get('fighter_b') or out.get('fighter_b') or '').strip()
    m=(bout or {}).get('market') or {}; fav=str(out.get('favorite') or '').strip()
    if not fav and a and b:
        try:
            pa=float(out.get('market_a',m.get('consensus_no_vig_a')));pb=float(out.get('market_b',m.get('consensus_no_vig_b')));fav=a if pa>=pb else b
        except Exception:fav=''
    opp=b if fav==a else (a if fav==b else str(out.get('underdog') or '').strip())
    out['ko_recovery']=compute_ko_recovery(fav,opp,event_date) if fav and opp and event_date is not None else {'available':False,'qualifies':False}
    return out

_orig_method_cards_kr=ufc_email_design.method_cards
def _kr_method_cards(p,w):
    cards=list(_orig_method_cards_kr(p,w));k=p.get('ko_recovery') or {}
    if k.get('qualifies'):
        checks=[f"Favorite last KO/TKO loss: {k.get('favorite_last_ko_loss')} ({k.get('favorite_days')} days ago)",f"Opponent last KO/TKO loss: {k.get('opponent_last_ko_loss')} ({k.get('opponent_days')} days ago)",f"Recovery gap: {k.get('gap_days')} days (required ≥105)"]
        cards.append({'id':'U10','title':'KO/TKO Recovery Gap','favorite':k.get('favorite'),'history':dict(bets=333,wins=238,win_rate=238/333,roi=.08244202474944022),'checks':checks,'note':'Pre-2020 ROI +5.65%; 2020+ ROI +10.85%. Both fighters must have a prior UFC KO/TKO loss.'})
    return cards
ufc_email_design.method_cards=_kr_method_cards

_orig_ufc_picks_kr=bet_tracker.ufc_picks
def _kr_ufc_picks(event,preds):
    import copy as _copy
    base=list(_orig_ufc_picks_kr(event,preds))
    for p in preds or []:
        k=p.get('ko_recovery') or {}
        if not k.get('qualifies'):continue
        fav=k.get('favorite');opp=k.get('opponent')
        if any(x.get('selection')==fav and x.get('opponent')==opp for x in base):continue
        q=_copy.deepcopy(p);sm=q.setdefault('striking_methods',{});sm.update({'available':True,'favorite':fav,'opponent':opp,'striking_differential':True})
        generated=list(_orig_ufc_picks_kr(event,[q]));pick=next((x for x in generated if x.get('selection')==fav),None)
        if pick is not None:base.append(pick)
    return base
bet_tracker.ufc_picks=_kr_ufc_picks

_orig_event_block_kr=event_block
def event_block(e,preds):
    import html as _html
    base=_orig_event_block_kr(e,preds);cards=[]
    for p in preds or []:
        k=p.get('ko_recovery') or {}
        if not k.get('qualifies'):continue
        cards.append('<div style="margin:14px 18px;padding:14px;border:1px solid #aaa;border-radius:10px;">'
          '<div style="font-weight:700;font-size:18px;">U10 · KO/TKO RECOVERY GAP</div>'
          f'<div style="margin-top:6px;"><b>{_html.escape(str(k.get("favorite") or ""))}</b> vs. {_html.escape(str(k.get("opponent") or ""))}</div>'
          f'<div style="margin-top:6px;">Favorite: {k.get("favorite_days")} days since last KO/TKO loss · Opponent: {k.get("opponent_days")} days · Gap: <b>{k.get("gap_days")} days</b></div>'
          '<div style="margin-top:6px;"><b>Historical:</b> 333 bets · 238-95 · 71.5% wins · <b>+8.24% ROI</b></div>'
          '<div>Pre-2020 +5.65% ROI · 2020+ +10.85% ROI</div></div>')
    if not cards:return base
    section='<div style="margin-top:18px;"><div style="font-weight:700;font-size:19px;margin:0 18px 8px;">Recovery Signal</div>'+''.join(cards)+'</div>'
    pos=base.rfind('</div>');return base[:pos]+section+base[pos:] if pos>=0 else base+section
'''
s=s.replace(marker,addon+'\n'+marker,1);p.write_text(s);print('patched U10')
PY
cd "$ROOT"
venv/bin/python -m py_compile "$WATCHER"
venv/bin/python - <<'PY'
import importlib.util,sqlite3
from pathlib import Path
p=Path.home()/'ufc-predictor-v1'/'ufc_email_watcher.py';spec=importlib.util.spec_from_file_location('w',p);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
idx=w.load_ko_recovery_index();print('ko_recovery_profiles',len(idx));print('history',w.KO_RECOVERY_HISTORY)
db=sqlite3.connect(Path.home()/'ufc-predictor-v1'/'auto_research'/'state'/'research.sqlite3')
cur=db.execute("UPDATE candidates SET official=1,status='official',note='Manually approved by user for official U10 live arsenal; no auto-promotion.' WHERE name='f2_diff_days_since_ko_loss >= 105'")
db.commit();print('candidate_rows_promoted',cur.rowcount)
PY
