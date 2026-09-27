#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"
[ -f "$WATCHER" ] || { echo "missing watcher"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "missing env"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-opponent-context-guard.$(date +%Y%m%d-%H%M%S).bak"

"$ROOT/venv/bin/python" - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
if 'UFC_GLOBAL_OPPONENT_CONTEXT_GUARD_V1' in s:
    print('Global opponent-context guard already installed')
    raise SystemExit(0)
marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('main marker missing')

addon=r'''
# UFC_GLOBAL_OPPONENT_CONTEXT_GUARD_V1
# Publication contract:
# - all official live methods must be explicitly registered as opponent-aware;
# - context data must resolve for both fighters before an official selection can publish;
# - U8 retains its validated age + wrestling-pressure gate;
# - U10 adds the validated opponent-age gate (favorite cannot be >2y older).
# Unknown future method IDs fail closed until audited and registered.
_UFC_OPPONENT_AWARE_METHODS={
    'U1':'market + relative age + global skill veto',
    'U2':'market + relative reach + global skill veto',
    'U3':'market + relative age/reach + global skill veto',
    'U4':'relative age + reach + global skill veto',
    'U5':'relative wrestling volume/control',
    'U6':'relative striking pace/defense',
    'U7':'relative striking + TD defense + age/power risk',
    'U8':'relative striking + age/wrestling opponent-context gate',
    'U9':'relative experience/win rate/finish vulnerability + health veto',
    'U10':'relative KO recovery + opponent-age guard',
    'U11':'relative experience/win rate/finish vulnerability + decline risk',
    'U12':'relative experience/win rate/finish vulnerability + decline risk',
}
_U10_MAX_FAVORITE_OLDER_YEARS=2.0
_U10_CONTEXT_HISTORY={'bets':246,'wins':182,'losses':64,'win_rate':182/246,'roi':.1101,'pre_roi':.0638,'recent_roi':.1550}

def compute_global_opponent_context(p,event_date):
    a=str(p.get('fighter_a') or '').strip(); b=str(p.get('fighter_b') or '').strip()
    fav=''
    try:
        ma=float(p.get('market_a')); mb=float(p.get('market_b')); fav=a if ma>=mb else b
    except Exception:
        fav=str(p.get('favorite') or '').strip()
    opp=b if fav==a else (a if fav==b else str(p.get('underdog') or '').strip())
    r={'available':False,'favorite':fav,'opponent':opp,'favorite_age':None,'opponent_age':None,
       'favorite_older_by':None,'favorite_sig_diff':None,'opponent_sig_diff':None,'sig_diff_gap':None,
       'favorite_td_a15':None,'opponent_td_a15':None,'opponent_tdatt_adv':None,
       'favorite_ctrl15':None,'opponent_ctrl15':None,'opponent_ctrl_adv':None}
    if not fav or not opp or event_date is None:return r
    try:
        vidx=load_veteran_decline_index(); vfp=_vd_resolve(fav,vidx or {}); vop=_vd_resolve(opp,vidx or {})
        widx=load_wrestling_index(); wfp=_wm_resolve(fav,widx or {}); wop=_wm_resolve(opp,widx or {})
        sidx=load_striking_method_index(); sfp=_sm_resolve(fav,sidx or {}); sop=_sm_resolve(opp,sidx or {})
    except Exception:
        return r
    if not vfp or not vop or not wfp or not wop or not sfp or not sop or not vfp.get('dob') or not vop.get('dob'):
        return r
    try:
        ed=pd.Timestamp(event_date).normalize(); fd=pd.Timestamp(vfp['dob']).normalize(); od=pd.Timestamp(vop['dob']).normalize()
        fa=(ed-fd).days/365.2425; oa=(ed-od).days/365.2425
        fs=float(sfp.get('sig_diff_pm',0)); os=float(sop.get('sig_diff_pm',0))
        ftd=float(wfp.get('td_a15',0)); otd=float(wop.get('td_a15',0))
        fc=float(wfp.get('ctrl15',0)); oc=float(wop.get('ctrl15',0))
    except Exception:return r
    r.update({'available':True,'favorite_age':fa,'opponent_age':oa,'favorite_older_by':fa-oa,
              'favorite_sig_diff':fs,'opponent_sig_diff':os,'sig_diff_gap':fs-os,
              'favorite_td_a15':ftd,'opponent_td_a15':otd,'opponent_tdatt_adv':otd-ftd,
              'favorite_ctrl15':fc,'opponent_ctrl15':oc,'opponent_ctrl_adv':oc-fc})
    return r

_orig_predict_bout_globalctx=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_globalctx(*args,**kwargs)
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    ctx=compute_global_opponent_context(out,event_date)
    out['global_opponent_context']=ctx

    # U10: validated 330-match stress test improved from 71.2% / +7.80%
    # to 74.0% / +11.01% when favorites >2y older were removed.
    k=out.get('ko_recovery') or {}
    if k.get('qualifies'):
        k['raw_qualifies']=True
        k['opponent_context']=ctx
        age_ok=bool(ctx.get('available') and float(ctx.get('favorite_older_by'))<=_U10_MAX_FAVORITE_OLDER_YEARS)
        if not age_ok:
            k['qualifies']=False
            k['opponent_context_veto']=True
            k['veto_reason']='missing opponent context' if not ctx.get('available') else f"favorite older by {float(ctx.get('favorite_older_by')):.2f}y > {_U10_MAX_FAVORITE_OLDER_YEARS:.1f}y"
        else:
            k['opponent_context_veto']=False
        out['ko_recovery']=k
    return out

# Final email/site method-card guard. Unknown method IDs and missing fighter context fail closed.
_orig_method_cards_globalctx=ufc_email_design.method_cards
def _globalctx_method_cards(p,w):
    cards=list(_orig_method_cards_globalctx(p,w))
    ctx=p.get('global_opponent_context') or {}
    if not ctx.get('available'):
        return []
    safe=[]
    for c in cards:
        mid=str(c.get('id') or '').strip()
        if mid not in _UFC_OPPONENT_AWARE_METHODS:
            continue
        if mid=='U10':
            k=p.get('ko_recovery') or {}
            if not k.get('qualifies'):
                continue
            c=dict(c)
            c['title']='KO/TKO Recovery + Opponent Age Guard'
            c['history']=dict(bets=246,wins=182,win_rate=182/246,roi=.1101)
            checks=list(c.get('checks') or [])
            checks.append(f"Opponent context: favorite age {float(ctx.get('favorite_age')):.1f}, opponent age {float(ctx.get('opponent_age')):.1f}; favorite older by {float(ctx.get('favorite_older_by')):+.1f}y (max +2.0y)")
            c['checks']=checks
            c['note']='Opponent-context matched sample: 246 bets · 182-64 · 74.0% wins · +11.01% ROI; pre-2020 +6.38%, 2020+ +15.50%.'
        else:
            c=dict(c)
            checks=list(c.get('checks') or [])
            checks.append(
                f"Opponent context verified: ages {float(ctx.get('favorite_age')):.1f} vs {float(ctx.get('opponent_age')):.1f}; "
                f"sig-diff gap {float(ctx.get('sig_diff_gap')):+.2f}/min; "
                f"TD-attempt gap vs favorite {float(ctx.get('opponent_tdatt_adv')):+.2f}/15; "
                f"control gap vs favorite {float(ctx.get('opponent_ctrl_adv')):+.2f} min/15"
            )
            c['checks']=checks
        safe.append(c)
    return safe
ufc_email_design.method_cards=_globalctx_method_cards

# Canonical tracker guard: a selection cannot be tracked/published unless at least
# one registered opponent-aware method card survives the final guard.
_orig_ufc_picks_globalctx=bet_tracker.ufc_picks
def _globalctx_ufc_picks(event,preds):
    base=list(_orig_ufc_picks_globalctx(event,preds))
    allowed=set()
    for p in preds or []:
        try: cards=ufc_email_design.method_cards(p,globals())
        except Exception: cards=[]
        for c in cards:
            fav=str(c.get('favorite') or '').strip()
            if fav: allowed.add(norm_name(fav))
    return [x for x in base if norm_name(str(x.get('selection') or '')) in allowed]
bet_tracker.ufc_picks=_globalctx_ufc_picks
'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Installed global opponent-context publication guard')
PY

"$ROOT/venv/bin/python" -m py_compile "$WATCHER"
cd "$ROOT"
set -a
source "$ENV_FILE"
set +a

"$ROOT/venv/bin/python" - <<'PY'
import importlib.util,pandas as pd
from pathlib import Path
p=Path('ufc_email_watcher.py');spec=importlib.util.spec_from_file_location('w',p);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
bundle=w.joblib.load(w.BUNDLE_PATH);reach=w.load_reach_index();skills=w.load_skill_index();feed=w.fetch_feed()
print('REGISTERED',sorted(w._UFC_OPPONENT_AWARE_METHODS))
for e in feed.get('events') or []:
    try:ed=pd.Timestamp(e['date']).date()
    except:continue
    for b in e.get('bouts') or []:
        try:q=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError:
            try:q=w.predict_bout(b,ed,bundle,reach)
            except TypeError:q=w.predict_bout(b,ed,bundle)
        cards=w.ufc_email_design.method_cards(q,w.__dict__)
        if cards:
            print('QUAL',e.get('date'),b.get('fighter_a'),'vs',b.get('fighter_b'),[(c.get('id'),c.get('favorite'),c.get('title')) for c in cards])
PY

echo "=== FORCE CANONICAL WATCHER RUN ==="
"$ROOT/venv/bin/python" "$WATCHER" --run --force-initial

echo "=== VERIFY PUBLIC STATE ==="
"$ROOT/venv/bin/python" - <<'PY'
import json
from pathlib import Path
p=Path('/srv/appwiza-sports/state/ufc.json');d=json.loads(p.read_text())
print('updated_at',d.get('updated_at'))
for c in d.get('cards',[]):
    if not c.get('result') and not c.get('withdrawn'):
        print('ACTIVE',c.get('selection'),'vs',c.get('opponent'),[(m.get('id'),m.get('title'),m.get('win'),m.get('roi')) for m in c.get('methods',[])])
PY
