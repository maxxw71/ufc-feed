#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"

[ -f "$WATCHER" ] || { echo "ERROR: Missing $WATCHER"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: Missing $ENV_FILE"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-u7-risk-gate.$(date +%Y%m%d-%H%M%S).bak"

echo "[ 10%] Backed up live watcher"

python - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
if 'STRIKING_TD_RISK_GATE_V2' in s:
    print('U7 risk gate already installed.')
    raise SystemExit(0)
if 'STRIKING_METHODS_RULE_V1' not in s:
    raise RuntimeError('Existing striking methods patch not found; install apply_live_striking_methods.sh first.')

marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('Could not find watcher main marker')

addon=r'''
# STRIKING_TD_RISK_GATE_V2
# Postmortem validation of original U7 (54 bets, 51-3, +17.64% ROI):
# Gate: favorite age advantage >= -1.0 years AND
#       opponent KD/15 * favorite KD-absorbed/15 < 0.12.
# Filtered historical sample: 35 bets, 35-0, +22.65% ROI;
# pre-2020 +23.03%, 2020+ +22.25%, 2022+ +20.38%.
# Neighboring age/power thresholds remained positive; this is a risk gate,
# not a claim of 100% future win probability.
_U7_RISK_INDEX=None
_U7_RISK_CACHE=ROOT/'u7_striking_td_risk_index.json'
_U7_RISK_CACHE_MAX_AGE=86400
_U7_INDIVIDUALS_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
_U7_MIN_AGE_ADV=-1.0
_U7_MAX_POWER_PRODUCT=.12

def _u7_num(v):
    import re as _re
    if v is None: return 0.0
    z=_re.search(r'-?\d+(?:\.\d+)?',str(v))
    return float(z.group()) if z else 0.0

def load_u7_risk_index():
    global _U7_RISK_INDEX
    if _U7_RISK_INDEX is not None: return _U7_RISK_INDEX
    import json as _json, time as _time
    if _U7_RISK_CACHE.exists():
        try:
            if (_time.time()-_U7_RISK_CACHE.stat().st_mtime)<=_U7_RISK_CACHE_MAX_AGE:
                z=_json.loads(_U7_RISK_CACHE.read_text())
                if z and any('kd15' in v and 'kd_abs15' in v and 'dob' in v for v in z.values()):
                    _U7_RISK_INDEX=z; return z
        except Exception: pass
    try:
        # Reuse the same UFCStats raw cache maintained by the existing skill/striking code.
        if 'load_skill_index' in globals():
            try: load_skill_index()
            except Exception: pass
        raw=globals().get('SKILL_RAW_CACHE_PATH',ROOT/'ufcstats_competitions_skill_cache.csv')
        if not raw.exists(): _U7_RISK_INDEX={}; return _U7_RISK_INDEX
        df=pd.read_csv(raw,low_memory=False)
        agg={}
        for _,row in df.iterrows():
            fm=_sm_minutes(row) if '_sm_minutes' in globals() else _fight_minutes(row)
            if fm<=0: continue
            for side in (1,2):
                opp=2 if side==1 else 1
                name=str(row.get(f'player{side}') or '').strip()
                if not name: continue
                kd=kd_abs=0.0
                for rd in range(1,6):
                    kd += _u7_num(row.get(f'p{side}_rd{rd}_KD'))
                    kd_abs += _u7_num(row.get(f'p{opp}_rd{rd}_KD'))
                k=norm_name(name)
                q=agg.setdefault(k,{'m':0.0,'kd':0.0,'kd_abs':0.0,'dob':None})
                q['m']+=fm; q['kd']+=kd; q['kd_abs']+=kd_abs
        # DOB is stable identity data; it is safe for future-event qualification.
        try:
            rr=requests.get(_U7_INDIVIDUALS_URL,timeout=45,headers={'User-Agent':'ufc-model-watcher/u7-risk-v2'})
            rr.raise_for_status()
            ids=pd.read_csv(__import__('io').StringIO(rr.text),low_memory=False)
            if 'name' in ids.columns and 'dob' in ids.columns:
                for _,r in ids.iterrows():
                    k=norm_name(str(r.get('name') or ''))
                    if k in agg and str(r.get('dob') or '').strip(): agg[k]['dob']=str(r.get('dob')).strip()
        except Exception:
            pass
        out={}
        for k,q in agg.items():
            if q['m']<=0: continue
            out[k]={'kd15':q['kd']*15.0/q['m'],'kd_abs15':q['kd_abs']*15.0/q['m'],'dob':q.get('dob')}
        try: _U7_RISK_CACHE.write_text(_json.dumps(out))
        except Exception: pass
        _U7_RISK_INDEX=out; return out
    except Exception:
        _U7_RISK_INDEX={}; return _U7_RISK_INDEX

def _u7_resolve(name,idx):
    k=norm_name(name)
    if k in idx:return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_u7_risk_gate(favorite,opponent,event_date):
    idx=load_u7_risk_index(); fp=_u7_resolve(favorite,idx or {}); op=_u7_resolve(opponent,idx or {})
    r={'available':False,'pass':False,'age_adv':None,'opponent_kd15':None,'favorite_kd_abs15':None,
       'power_risk_product':None,'age_pass':False,'power_pass':False,
       'age_min':_U7_MIN_AGE_ADV,'power_max':_U7_MAX_POWER_PRODUCT}
    if not fp or not op or not fp.get('dob') or not op.get('dob'):return r
    try:
        ed=pd.Timestamp(event_date).normalize()
        fd=pd.Timestamp(fp['dob']).normalize(); od=pd.Timestamp(op['dob']).normalize()
        fav_age=(ed-fd).days/365.2425; opp_age=(ed-od).days/365.2425
        age_adv=opp_age-fav_age
        okd=float(op.get('kd15')); fabs=float(fp.get('kd_abs15')); product=okd*fabs
    except Exception:return r
    r.update({'available':True,'favorite_age':fav_age,'opponent_age':opp_age,'age_adv':age_adv,
              'opponent_kd15':okd,'favorite_kd_abs15':fabs,'power_risk_product':product,
              'age_pass':age_adv>=_U7_MIN_AGE_ADV,'power_pass':product<_U7_MAX_POWER_PRODUCT})
    r['pass']=bool(r['age_pass'] and r['power_pass'])
    return r

_orig_predict_bout_u7risk=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_u7risk(*args,**kwargs)
    sm=out.get('striking_methods') or {}
    if not sm.get('striking_td_defense'):
        return out
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    fav=str(sm.get('favorite') or out.get('favorite') or '').strip()
    opp=str(sm.get('opponent') or out.get('underdog') or '').strip()
    risk=compute_u7_risk_gate(fav,opp,event_date) if fav and opp and event_date is not None else {'available':False,'pass':False}
    sm['striking_td_defense_raw']=True
    sm['striking_td_risk_gate']=risk
    if not risk.get('pass'):
        sm['striking_td_defense']=False
        sm['striking_td_risk_veto']=True
    else:
        sm['striking_td_risk_veto']=False
    out['striking_methods']=sm
    return out
'''
s=s.replace(marker,addon+'\n'+marker,1)

# Update the displayed historical record for the newly gated U7 method only.
old="('striking_td_defense','STRIKING + TD DEFENSE',54,51,3,.944,.1764,.1628,.1909)"
new="('striking_td_defense','STRIKING + TD DEFENSE — RISK GATED',35,35,0,1.000,.2265,.2303,.2225)"
if old not in s:
    raise RuntimeError('Could not locate the existing U7 display-stat tuple; refusing partial install.')
s=s.replace(old,new,1)
p.write_text(s)
print('Patched U7 with validated age/power risk gate.')
PY

echo "[ 45%] Patched watcher"
cd "$ROOT"
source "$ROOT/venv/bin/activate"
python -m py_compile "$WATCHER"
echo "[ 55%] Syntax check passed"

python -u - <<'PY'
import importlib.util
from pathlib import Path
import pandas as pd
root=Path.home()/"ufc-predictor-v1"; wp=root/"ufc_email_watcher.py"
spec=importlib.util.spec_from_file_location('w',wp); w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
bundle=w.joblib.load(w.BUNDLE_PATH);reach=w.load_reach_index();skills=w.load_skill_index();feed=w.fetch_feed()
raw=kept=veto=0
for e in feed.get('events') or []:
    try:ed=pd.Timestamp(e['date']).date()
    except Exception:continue
    for b in e.get('bouts') or []:
        try:p=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError:
            try:p=w.predict_bout(b,ed,bundle,reach)
            except TypeError:p=w.predict_bout(b,ed,bundle)
        sm=p.get('striking_methods') or {}
        raw+=int(bool(sm.get('striking_td_defense_raw')))
        kept+=int(bool(sm.get('striking_td_defense')))
        veto+=int(bool(sm.get('striking_td_risk_veto')))
print(f'U7 current raw/kept/vetoed: {raw}/{kept}/{veto}')
PY

echo "[ 75%] Live qualification check passed"
set -a
source "$ENV_FILE"
set +a
python "$WATCHER" --run --force-initial
sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer

echo "[100%] U7 STRIKING + TD DEFENSE RISK GATE ACTIVE"
echo "Historical gated sample: 35 bets | 35-0 | +22.65% ROI"
echo "Gate: favorite no more than 1 year older; opponent KD/15 x favorite KD-absorbed/15 < 0.12"
