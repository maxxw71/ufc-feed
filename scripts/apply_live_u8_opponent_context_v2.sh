#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
[ -f "$WATCHER" ] || exit 2
cp "$WATCHER" "$WATCHER.pre-u8-opponent-context-v2.$(date +%Y%m%d-%H%M%S).bak"

"$ROOT/venv/bin/python" - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
if 'U8_OPPONENT_CONTEXT_GATE_V2' in s:
    print('U8 V2 already installed'); raise SystemExit(0)
marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('main marker missing')
addon=r'''
# U8_OPPONENT_CONTEXT_GATE_V2
# Correct live implementation of the validated opponent-control interaction.
# Uses the wrestling index for own control/15 and veteran index only for DOB.
def compute_u8_opponent_context_v2(favorite,opponent,event_date):
    r={'available':False,'pass':False,'favorite':favorite,'opponent':opponent,
       'favorite_age':None,'opponent_age':None,'favorite_older_by':None,
       'favorite_ctrl15':None,'opponent_ctrl15':None,'opponent_ctrl_adv':None,
       'age_veto':False,'age_wrestling_veto':False}
    try:
        vidx=load_veteran_decline_index()
        vfp=_vd_resolve(favorite,vidx or {}); vop=_vd_resolve(opponent,vidx or {})
        widx=load_wrestling_index()
        wfp=_wm_resolve(favorite,widx or {}); wop=_wm_resolve(opponent,widx or {})
    except Exception:
        return r
    if not vfp or not vop or not wfp or not wop or not vfp.get('dob') or not vop.get('dob'):
        return r
    try:
        ed=pd.Timestamp(event_date).normalize()
        fd=pd.Timestamp(vfp['dob']).normalize(); od=pd.Timestamp(vop['dob']).normalize()
        fa=(ed-fd).days/365.2425; oa=(ed-od).days/365.2425
        older=fa-oa
        fc=float(wfp.get('ctrl15') or 0.0); oc=float(wop.get('ctrl15') or 0.0)
        cadv=oc-fc
    except Exception:
        return r
    age_veto=older>3.0
    age_wrestling_veto=(older>2.0 and cadv>=0.5)
    r.update({'available':True,'favorite_age':fa,'opponent_age':oa,'favorite_older_by':older,
              'favorite_ctrl15':fc,'opponent_ctrl15':oc,'opponent_ctrl_adv':cadv,
              'age_veto':age_veto,'age_wrestling_veto':age_wrestling_veto,
              'pass':not(age_veto or age_wrestling_veto)})
    return r

_orig_predict_bout_u8ctx_v2=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_u8ctx_v2(*args,**kwargs)
    sm=out.get('striking_methods') or {}
    if not sm.get('striking_differential_raw'):
        return out
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    fav=str(sm.get('favorite') or out.get('favorite') or '').strip()
    opp=str(sm.get('opponent') or out.get('underdog') or '').strip()
    ctx=compute_u8_opponent_context_v2(fav,opp,event_date) if fav and opp and event_date is not None else {'available':False,'pass':False}
    sm['striking_differential_context']=ctx
    sm['striking_differential']=bool(ctx.get('pass'))
    sm['striking_differential_context_veto']=not bool(ctx.get('pass'))
    out['striking_methods']=sm
    return out
'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Installed U8 opponent-context V2')
PY
"$ROOT/venv/bin/python" -m py_compile "$WATCHER"
cd "$ROOT"
"$ROOT/venv/bin/python" - <<'PY'
import importlib.util,pandas as pd
from pathlib import Path
p=Path('ufc_email_watcher.py');spec=importlib.util.spec_from_file_location('w',p);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
bundle=w.joblib.load(w.BUNDLE_PATH); reach=w.load_reach_index(); skills=w.load_skill_index(); feed=w.fetch_feed()
for e in feed.get('events') or []:
    try: ed=pd.Timestamp(e['date']).date()
    except: continue
    for b in e.get('bouts') or []:
        try:q=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError:q=w.predict_bout(b,ed,bundle,reach)
        sm=q.get('striking_methods') or {}
        if sm.get('striking_differential_raw'):
            print('U8',e.get('date'),b.get('fighter_a'),'vs',b.get('fighter_b'),'fav',sm.get('favorite'),'kept',sm.get('striking_differential'),'ctx',sm.get('striking_differential_context'))
PY
echo "U8 V2 installed and verified"
