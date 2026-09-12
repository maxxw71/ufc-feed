#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"

[ -f "$WATCHER" ] || { echo "ERROR: Missing $WATCHER"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: Missing $ENV_FILE"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-striking-methods.$(date +%Y%m%d-%H%M%S).bak"
echo "[ 10%] Backed up current watcher"

python - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if 'STRIKING_METHODS_RULE_V1' in s:
    print('Striking methods already present in watcher.')
    raise SystemExit(0)

marker='if __name__ == "__main__":'
if marker not in s:
    marker="if __name__ == '__main__':"
if marker not in s:
    raise RuntimeError('Could not find watcher main marker')

addon=r'''
# STRIKING_METHODS_RULE_V1
# Validated 2006-2026 favorite-side methods:
# Pace + Defense: 53 bets, 49-4, +20.41% ROI; pre +20.43%, 2020+ +20.38%.
# Striking + TD Defense: 54 bets, 51-3, +17.64% ROI; pre +16.28%, 2020+ +19.09%.
# Striking Differential: 68 bets, 66-2, +17.34% ROI; pre +16.23%, 2020+ +19.02%.
_SM_INDEX=None
_SM_CACHE=ROOT/'striking_method_index.json'
_SM_CACHE_MAX_AGE=86400

def _sm_parse_of(v):
    import re as _re
    if v is None: return 0.0,0.0
    z=_re.search(r'(\d+)\s+of\s+(\d+)',str(v),_re.I)
    return (float(z.group(1)),float(z.group(2))) if z else (0.0,0.0)

def _sm_minutes(row):
    import re as _re
    try: rnd=max(1,int(float(row.get('round') or 1)))
    except Exception: rnd=1
    z=_re.match(r'(\d+):(\d+)',str(row.get('time') or '0:00'))
    sec=int(z.group(1))*60+int(z.group(2)) if z else 0
    return max(1,(rnd-1)*300+sec)/60.0

def load_striking_method_index():
    global _SM_INDEX
    if _SM_INDEX is not None: return _SM_INDEX
    import json as _json, time as _time
    if _SM_CACHE.exists():
        try:
            if (_time.time()-_SM_CACHE.stat().st_mtime)<=_SM_CACHE_MAX_AGE:
                _SM_INDEX=_json.loads(_SM_CACHE.read_text())
                return _SM_INDEX
        except Exception: pass
    try:
        if 'load_skill_index' in globals():
            try: load_skill_index()
            except Exception: pass
        raw=globals().get('SKILL_RAW_CACHE_PATH',ROOT/'ufcstats_competitions_skill_cache.csv')
        if not raw.exists(): _SM_INDEX={}; return _SM_INDEX
        df=pd.read_csv(raw,low_memory=False)
    except Exception:
        _SM_INDEX={}; return _SM_INDEX
    agg={}
    for _,row in df.iterrows():
        mins=_sm_minutes(row)
        if mins<=0: continue
        for side in (1,2):
            opp=2 if side==1 else 1
            name=str(row.get(f'player{side}') or '').strip()
            if not name: continue
            sig_l=sig_a=sig_abs=sig_abs_a=td_allowed=td_faced=0.0
            for rd in range(1,6):
                a,b=_sm_parse_of(row.get(f'p{side}_rd{rd}_Sig_str')); sig_l+=a; sig_a+=b
                a,b=_sm_parse_of(row.get(f'p{opp}_rd{rd}_Sig_str')); sig_abs+=a; sig_abs_a+=b
                a,b=_sm_parse_of(row.get(f'p{opp}_rd{rd}_Td')); td_allowed+=a; td_faced+=b
            k=norm_name(name)
            q=agg.setdefault(k,{'m':0.0,'f':0,'sl':0.0,'sa':0.0,'abs':0.0,'absa':0.0,'tda':0.0,'tdf':0.0})
            q['m']+=mins; q['f']+=1; q['sl']+=sig_l; q['sa']+=sig_a; q['abs']+=sig_abs; q['absa']+=sig_abs_a; q['tda']+=td_allowed; q['tdf']+=td_faced
    out={}
    for k,q in agg.items():
        if q['m']<=0: continue
        out[k]={'fights':q['f'],'sig_l_pm':q['sl']/q['m'],'sig_abs_pm':q['abs']/q['m'],
                'sig_diff_pm':(q['sl']-q['abs'])/q['m'],
                'sig_def':1-q['abs']/q['absa'] if q['absa']>0 else None,
                'td_def':1-q['tda']/q['tdf'] if q['tdf']>0 else None}
    try: _SM_CACHE.write_text(_json.dumps(out))
    except Exception: pass
    _SM_INDEX=out
    return out

def _sm_resolve(name,idx):
    k=norm_name(name)
    if k in idx: return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_striking_methods(favorite,opponent,market_prob):
    idx=load_striking_method_index(); fp=_sm_resolve(favorite,idx or {}); op=_sm_resolve(opponent,idx or {})
    r={'available':bool(fp and op),'favorite':favorite,'opponent':opponent,'fav_profile':fp,'opp_profile':op,
       'pace_defense':False,'striking_td_defense':False,'striking_differential':False}
    if not fp or not op: return r
    try: mp=float(market_prob)
    except Exception: mp=0.0
    landed_gap=fp.get('sig_l_pm',0)-op.get('sig_l_pm',0)
    diff_gap=fp.get('sig_diff_pm',0)-op.get('sig_diff_pm',0)
    fdef,odef=fp.get('sig_def'),op.get('sig_def'); ftd,otd=fp.get('td_def'),op.get('td_def')
    def_gap=(fdef-odef) if fdef is not None and odef is not None else None
    td_gap=(ftd-otd) if ftd is not None and otd is not None else None
    r.update({'market_prob':mp,'landed_gap':landed_gap,'sig_diff_gap':diff_gap,'sig_def_gap':def_gap,'td_def_gap':td_gap})
    r['pace_defense']=bool(mp>=.65 and fp.get('fights',0)>=3 and op.get('fights',0)>=3 and landed_gap>=.25 and def_gap is not None and def_gap>=.15)
    r['striking_td_defense']=bool(mp>=.70 and fp.get('fights',0)>=4 and op.get('fights',0)>=4 and diff_gap>=1.0 and td_gap is not None and td_gap>=.10)
    r['striking_differential']=bool(mp>=.75 and fp.get('fights',0)>=4 and op.get('fights',0)>=4 and diff_gap>=1.0 and fp.get('sig_diff_pm',-999)>=.5)
    return r

_orig_predict_bout_sm=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_sm(*args,**kwargs)
    bout=args[0] if args else kwargs.get('bout',{})
    a=str((bout or {}).get('fighter_a') or out.get('fighter_a') or out.get('a') or '').strip()
    b=str((bout or {}).get('fighter_b') or out.get('fighter_b') or out.get('b') or '').strip()
    m=(bout or {}).get('market') or {}
    fav=str(out.get('favorite') or '').strip(); mp=out.get('market_prob')
    if not fav and a and b:
        try:
            pa=float(m.get('consensus_no_vig_a')); pb=float(m.get('consensus_no_vig_b')); fav=a if pa>=pb else b; mp=max(pa,pb)
        except Exception: fav=''
    if mp is None:
        try: mp=max(float(m.get('consensus_no_vig_a')),float(m.get('consensus_no_vig_b')))
        except Exception: mp=0.0
    opp=b if fav==a else (a if fav==b else str(out.get('underdog') or '').strip())
    out['striking_methods']=compute_striking_methods(fav,opp,mp) if fav and opp else {'available':False,'pace_defense':False,'striking_td_defense':False,'striking_differential':False}
    return out

_orig_event_block_sm=event_block
def event_block(e,preds):
    import html as _html
    base=_orig_event_block_sm(e,preds); cards=[]
    specs=[('pace_defense','PACE + DEFENSE',53,49,4,.925,.2041,.2043,.2038),
           ('striking_td_defense','STRIKING + TD DEFENSE',54,51,3,.944,.1764,.1628,.1909),
           ('striking_differential','STRIKING DIFFERENTIAL',68,66,2,.971,.1734,.1623,.1902)]
    for p in preds or []:
        sm=p.get('striking_methods') or {}
        if not any(sm.get(k) for k,*_ in specs): continue
        fp=sm.get('fav_profile') or {}; op=sm.get('opp_profile') or {}
        fav=_html.escape(str(sm.get('favorite') or '')); opp=_html.escape(str(sm.get('opponent') or ''))
        for key,title,n,w,l,wr,roi,pre,recent in specs:
            if not sm.get(key): continue
            cards.append('<div style="margin:14px 18px;padding:14px;border:1px solid #aaa;border-radius:10px;">'
                f'<div style="font-weight:700;font-size:18px;">{title}</div>'
                f'<div style="margin-top:6px;"><b>{fav}</b> vs. {opp}</div>'
                f'<div style="margin-top:6px;">Favorite: {fp.get("sig_l_pm",0):.2f} sig landed/min · {fp.get("sig_diff_pm",0):+.2f} sig diff/min · {(fp.get("sig_def") or 0)*100:.1f}% sig defense · {(fp.get("td_def") or 0)*100:.1f}% TD defense</div>'
                f'<div>Opponent: {op.get("sig_l_pm",0):.2f} sig landed/min · {op.get("sig_diff_pm",0):+.2f} sig diff/min · {(op.get("sig_def") or 0)*100:.1f}% sig defense · {(op.get("td_def") or 0)*100:.1f}% TD defense</div>'
                f'<div style="margin-top:6px;"><b>Historical:</b> {n} bets · {w}-{l} · {wr*100:.1f}% wins · <b>{roi*100:+.2f}% ROI</b></div>'
                f'<div>Pre-2020 {pre*100:+.2f}% · 2020+ {recent*100:+.2f}%</div></div>')
    if not cards: return base
    section='<div style="margin-top:18px;"><div style="font-weight:700;font-size:19px;margin:0 18px 8px;">Striking Signals</div>'+''.join(cards)+'</div>'
    pos=base.rfind('</div>')
    return base[:pos]+section+base[pos:] if pos>=0 else base+section

'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Patched live watcher with three striking methods.')
PY

echo "[ 35%] Patched watcher"
cd "$ROOT"
source "$ROOT/venv/bin/activate"
python -m py_compile "$WATCHER"
echo "[ 45%] Syntax check passed"

python -u - <<'PY'
import importlib.util
from pathlib import Path
import pandas as pd
root=Path.home()/"ufc-predictor-v1"; wp=root/"ufc_email_watcher.py"
spec=importlib.util.spec_from_file_location("w",wp); w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
print('[ 55%] Building/loading live striking profiles...',flush=True)
idx=w.load_striking_method_index(); print(f'[ 70%] Striking profiles loaded: {len(idx):,}',flush=True)
bundle=w.joblib.load(w.BUNDLE_PATH); reach=w.load_reach_index(); skills=w.load_skill_index(); feed=w.fetch_feed()
counts={'pace_defense':0,'striking_td_defense':0,'striking_differential':0}
for e in feed.get('events') or []:
    try: ed=pd.Timestamp(e['date']).date()
    except Exception: continue
    for b in e.get('bouts') or []:
        try: p=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError: p=w.predict_bout(b,ed,bundle,reach)
        sm=p.get('striking_methods') or {}
        for k in counts: counts[k]+=int(bool(sm.get(k)))
print('[ 80%] Current signals: Pace+Defense={pace_defense}, Striking+TD={striking_td_defense}, Striking Differential={striking_differential}'.format(**counts),flush=True)
PY

set -a
source "$ENV_FILE"
set +a
echo "[ 85%] Sending preview digest..."
python "$WATCHER" --run --force-initial

echo "[ 95%] Restarting scheduled watcher..."
sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer

echo "[100%] STRIKING METHODS ACTIVE"
echo "PACE + DEFENSE: 53 bets | 49-4 | +20.41% ROI"
echo "STRIKING + TD DEFENSE: 54 bets | 51-3 | +17.64% ROI"
echo "STRIKING DIFFERENTIAL: 68 bets | 66-2 | +17.34% ROI"
echo "Preview email sent; timer restarted."
