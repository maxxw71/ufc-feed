#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
[ -f "$WATCHER" ] || { echo "missing watcher"; exit 2; }

cp "$WATCHER" "$WATCHER.pre-u8-opponent-context.$(date +%Y%m%d-%H%M%S).bak"

"$ROOT/venv/bin/python" - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
if 'U8_OPPONENT_CONTEXT_GATE_V1' in s:
    print('U8 opponent-context gate already installed')
    raise SystemExit(0)
marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('main marker missing')

addon=r'''
# U8_OPPONENT_CONTEXT_GATE_V1
# Striking Differential opponent-context gate.
# Historical U8 parent sample: 69 bets, 67-2, +17.39% ROI.
# Keep rule after opponent-context veto: 66 bets, 65-1, +18.95% ROI;
# pre-2020 +18.91%, 2020+ +19.02%.
# Veto when:
#   A) favorite is >3.0 years older than opponent; OR
#   B) favorite is >2.0 years older AND opponent has >=0.5 min/15 more
#      historical UFC control time than the favorite.
# This adds explicit opponent age + grappling-pressure context to U8.
_U8_CTX_MAX_OLDER=3.0
_U8_CTX_OLDER_WRESTLE=2.0
_U8_CTX_CTRL_ADV=0.5

def compute_u8_opponent_context(favorite,opponent,event_date):
    r={'available':False,'pass':False,'favorite':favorite,'opponent':opponent,
       'favorite_age':None,'opponent_age':None,'favorite_older_by':None,
       'favorite_ctrl15':None,'opponent_ctrl15':None,'opponent_ctrl_adv':None,
       'age_veto':False,'age_wrestling_veto':False}
    try:
        idx=load_veteran_decline_index()
        fp=_vd_resolve(favorite,idx or {}); op=_vd_resolve(opponent,idx or {})
    except Exception:
        return r
    if not fp or not op or not fp.get('dob') or not op.get('dob'):
        return r
    try:
        ed=pd.Timestamp(event_date).normalize()
        fd=pd.Timestamp(fp.get('dob')).normalize(); od=pd.Timestamp(op.get('dob')).normalize()
        fa=(ed-fd).days/365.2425; oa=(ed-od).days/365.2425
        older=fa-oa
        fc=float(fp.get('ctrl15') or 0.0); oc=float(op.get('ctrl15') or 0.0)
        cadv=oc-fc
    except Exception:
        return r
    age_veto=older>_U8_CTX_MAX_OLDER
    aw_veto=(older>_U8_CTX_OLDER_WRESTLE and cadv>=_U8_CTX_CTRL_ADV)
    r.update({'available':True,'favorite_age':fa,'opponent_age':oa,'favorite_older_by':older,
              'favorite_ctrl15':fc,'opponent_ctrl15':oc,'opponent_ctrl_adv':cadv,
              'age_veto':age_veto,'age_wrestling_veto':aw_veto,
              'pass':not (age_veto or aw_veto)})
    return r

_orig_predict_bout_u8ctx=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_u8ctx(*args,**kwargs)
    sm=out.get('striking_methods') or {}
    if not sm.get('striking_differential'):
        return out
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    fav=str(sm.get('favorite') or out.get('favorite') or '').strip()
    opp=str(sm.get('opponent') or out.get('underdog') or '').strip()
    ctx=compute_u8_opponent_context(fav,opp,event_date) if fav and opp and event_date is not None else {'available':False,'pass':False}
    sm['striking_differential_raw']=True
    sm['striking_differential_context']=ctx
    # Fail closed: U8 no longer qualifies without opponent-context data.
    if not ctx.get('pass'):
        sm['striking_differential']=False
        sm['striking_differential_context_veto']=True
    else:
        sm['striking_differential_context_veto']=False
    out['striking_methods']=sm
    return out
'''
s=s.replace(marker,addon+'\n'+marker,1)

# Update U8 public historical stats to the context-gated backtest.
old="('striking_differential','STRIKING DIFFERENTIAL',68,66,2,.971,.1734,.1623,.1902)"
new="('striking_differential','STRIKING DIFFERENTIAL — OPPONENT CONTEXT',66,65,1,65/66,.1895,.1891,.1902)"
if old in s:
    s=s.replace(old,new,1)
p.write_text(s)
print('Installed U8 opponent-context gate')
PY

"$ROOT/venv/bin/python" -m py_compile "$WATCHER"

"$ROOT/venv/bin/python" - <<'PY'
import importlib.util,pandas as pd
from pathlib import Path
root=Path.home()/'ufc-predictor-v1'; p=root/'ufc_email_watcher.py'
spec=importlib.util.spec_from_file_location('w',p);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
bundle=w.joblib.load(w.BUNDLE_PATH);reach=w.load_reach_index();skills=w.load_skill_index();feed=w.fetch_feed()
raw=kept=veto=0
details=[]
for e in feed.get('events') or []:
    try: ed=pd.Timestamp(e['date']).date()
    except Exception: continue
    for b in e.get('bouts') or []:
        try:q=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError:
            try:q=w.predict_bout(b,ed,bundle,reach)
            except TypeError:q=w.predict_bout(b,ed,bundle)
        sm=q.get('striking_methods') or {}
        raw+=int(bool(sm.get('striking_differential_raw')))
        kept+=int(bool(sm.get('striking_differential')))
        veto+=int(bool(sm.get('striking_differential_context_veto')))
        if sm.get('striking_differential_raw'):
            details.append((e.get('date'),b.get('fighter_a'),b.get('fighter_b'),sm.get('favorite'),sm.get('striking_differential'),sm.get('striking_differential_context')))
print('U8 raw/kept/vetoed',raw,kept,veto)
for x in details: print('U8_DETAIL',x)
PY
sudo systemctl restart ufc-model-watcher.timer
echo "U8 opponent-context gate active"
