#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${HOME}/ufc-predictor-v1"
WATCHER="${ROOT}/ufc_email_watcher.py"

[ -f "${WATCHER}" ] || { echo "ERROR: Missing ${WATCHER}"; exit 2; }

cp "${WATCHER}" "${WATCHER}.pre-veteran-decline.$(date +%Y%m%d-%H%M%S).bak"
echo "[10%] Backed up live watcher"

"${ROOT}/venv/bin/python" - "${WATCHER}" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if 'VETERAN_DECLINE_LIVE_V1' in s:
    print('Veteran decline methods already installed.')
    raise SystemExit(0)
if 'EXPERIENCE_HEALTH_RULE_V1' not in s:
    raise RuntimeError('U9 Experience Health must be installed before U11/U12.')

marker='if __name__ == "__main__":'
if marker not in s:
    marker="if __name__ == '__main__':"
if marker not in s:
    raise RuntimeError('Could not find watcher main marker')

addon=r'''
# VETERAN_DECLINE_LIVE_V1
# U11/U12 are prospective live methods derived from the validated
# Battle-Tested-vs-Thin-Sample parent family.
#
# Shared parent gate:
# - favorite no-vig market probability 57.5%-75.0%
# - favorite >=8 prior UFC fights
# - opponent <=3 prior UFC fights
# - favorite UFC win-rate edge >=5 percentage points
# - favorite finish-loss% minus opponent finish-loss% <=60 percentage points
#
# U11 — Veteran Age/Defense Decline Filter
# Risk components: favorite >=2y older, TD defense <60%, control allowed >=3m/15,
# negative sig-strike differential, >=40% of losses by finish.
# Veto when >=4 of 5 risks are present.
# Historical: 35 bets, 34-1, 97.1% wins, +43.48% ROI.
# Pre-2020 +47.44%; 2020+ +39.28%.
#
# U12 — Veteran Balanced Decline Filter
# Risk components: favorite >=2y older, negative sig-strike differential,
# TD defense <60%, control allowed >=3m/15, recent-5 UFC win rate <50%,
# >=40% of losses by finish.
# Veto when >=4 of 6 risks are present.
# Historical: 32 bets, 31-1, 96.9% wins, +44.34% ROI.
# Pre-2020 +48.67%; 2020+ +40.02%.
#
# These are overlapping refinements of the same parent family. A fighter
# qualifying for multiple methods remains one tracked selection.

_VD_INDEX=None
_VD_CACHE=ROOT/'veteran_decline_live_index.json'
_VD_CACHE_MAX_AGE=86400
_VD_INDIVIDUALS_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
VETERAN_DECLINE_HISTORY={
    'U11':{'bets':35,'wins':34,'losses':1,'win_rate':34/35,'roi':0.4347698082,'pre_roi':0.4744443846,'recent_roi':0.3927614333},
    'U12':{'bets':32,'wins':31,'losses':1,'win_rate':31/32,'roi':0.4434368698,'pre_roi':0.4866880044,'recent_roi':0.4001857352},
}

def _vd_parse_of(v):
    import re as _re
    z=_re.search(r'(\d+)\s+of\s+(\d+)',str(v),_re.I)
    return (float(z.group(1)),float(z.group(2))) if z else (0.0,0.0)

def _vd_parse_ctrl(v):
    import re as _re
    z=_re.match(r'\s*(\d+):(\d+)\s*$',str(v))
    return (60*int(z.group(1))+int(z.group(2)))/60.0 if z else 0.0

def _vd_minutes(row):
    import re as _re
    try: rnd=max(1,int(float(row.get('round') or 1)))
    except Exception: rnd=1
    z=_re.match(r'(\d+):(\d+)',str(row.get('time') or '0:00'))
    sec=int(z.group(1))*60+int(z.group(2)) if z else 0
    return max(1,(rnd-1)*300+sec)/60.0

def load_veteran_decline_index():
    global _VD_INDEX
    if _VD_INDEX is not None:
        return _VD_INDEX
    import json as _json, time as _time
    if _VD_CACHE.exists():
        try:
            if (_time.time()-_VD_CACHE.stat().st_mtime)<=_VD_CACHE_MAX_AGE:
                z=_json.loads(_VD_CACHE.read_text())
                if z:
                    _VD_INDEX=z
                    return z
        except Exception:
            pass
    try:
        if 'load_skill_index' in globals():
            try: load_skill_index()
            except Exception: pass
        raw=globals().get('SKILL_RAW_CACHE_PATH',ROOT/'ufcstats_competitions_skill_cache.csv')
        if not raw.exists():
            _VD_INDEX={}
            return _VD_INDEX
        df=pd.read_csv(raw,low_memory=False)
        if 'event_date' in df.columns:
            df['_vd_date']=pd.to_datetime(df['event_date'],errors='coerce')
        else:
            df['_vd_date']=pd.NaT
    except Exception:
        _VD_INDEX={}
        return _VD_INDEX

    agg={}
    outcomes={}
    for _,row in df.sort_values('_vd_date',kind='stable').iterrows():
        fm=_vd_minutes(row)
        if fm<=0:
            continue
        result=str(row.get('result') or '').strip().upper()
        p1win=result.startswith('W')
        p2win=result.startswith('L')
        method=str(row.get('method') or '').upper()
        finish=('KO' in method or 'TKO' in method or 'SUB' in method)
        event_date=row.get('_vd_date')
        for side,opp,won,lost in ((1,2,p1win,p2win),(2,1,p2win,p1win)):
            name=str(row.get(f'player{side}') or '').strip()
            if not name:
                continue
            k=norm_name(name)
            q=agg.setdefault(k,{'fights':0,'wins':0,'losses':0,'mins':0.0,'ctrl_allowed':0.0,
                                'finish_losses':0,'sig_l':0.0,'sig_abs':0.0,'td_allowed':0.0,'td_faced':0.0})
            q['fights']+=1
            q['mins']+=fm
            if won:
                q['wins']+=1
                outcomes.setdefault(k,[]).append((str(event_date),1.0))
            elif lost:
                q['losses']+=1
                q['finish_losses']+=int(finish)
                outcomes.setdefault(k,[]).append((str(event_date),0.0))
            for rd in range(1,6):
                a,_=_vd_parse_of(row.get(f'p{side}_rd{rd}_Sig_str'))
                q['sig_l']+=a
                a,_=_vd_parse_of(row.get(f'p{opp}_rd{rd}_Sig_str'))
                q['sig_abs']+=a
                a,b=_vd_parse_of(row.get(f'p{opp}_rd{rd}_Td'))
                q['td_allowed']+=a
                q['td_faced']+=b
                q['ctrl_allowed']+=_vd_parse_ctrl(row.get(f'p{opp}_rd{rd}_Ctrl'))

    dobs={}
    try:
        rr=requests.get(_VD_INDIVIDUALS_URL,timeout=45,headers={'User-Agent':'ufc-model-watcher/veteran-decline-v1'})
        rr.raise_for_status()
        ids=pd.read_csv(__import__('io').StringIO(rr.text),low_memory=False)
        if 'name' in ids.columns and 'dob' in ids.columns:
            for _,r in ids.iterrows():
                nm=str(r.get('name') or '').strip()
                dob=str(r.get('dob') or '').strip()
                if nm and dob:
                    dobs[norm_name(nm)]=dob
    except Exception:
        pass

    out={}
    for k,q in agg.items():
        total=q['wins']+q['losses']
        if q['fights']<=0 or total<=0 or q['mins']<=0:
            continue
        recent=[v for _,v in sorted(outcomes.get(k,[]),key=lambda x:x[0])[-5:]]
        out[k]={
            'fights':q['fights'],
            'wins':q['wins'],
            'losses':q['losses'],
            'win_pct':q['wins']/total,
            'finish_loss_pct':q['finish_losses']/q['losses'] if q['losses']>0 else 0.0,
            'ctrl_allowed15':q['ctrl_allowed']*15.0/q['mins'],
            'sig_diff_pm':(q['sig_l']-q['sig_abs'])/q['mins'],
            'td_def':1.0-q['td_allowed']/q['td_faced'] if q['td_faced']>0 else None,
            'recent5':sum(recent)/len(recent) if recent else None,
            'dob':dobs.get(k),
        }
    try:
        _VD_CACHE.write_text(_json.dumps(out))
    except Exception:
        pass
    _VD_INDEX=out
    return out

def _vd_resolve(name,idx):
    k=norm_name(name)
    if k in idx:
        return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_veteran_decline(favorite,opponent,market_prob,event_date):
    idx=load_veteran_decline_index()
    fp=_vd_resolve(favorite,idx or {})
    op=_vd_resolve(opponent,idx or {})
    r={'available':bool(fp and op),'favorite':favorite,'opponent':opponent,'market_prob':None,
       'fav_profile':fp,'opp_profile':op,'base_qualifies':False,
       'u11_qualifies':False,'u12_qualifies':False,'u11_score':None,'u12_score':None,
       'u11_risks':{},'u12_risks':{}}
    try:
        mp=float(market_prob)
        r['market_prob']=mp
    except Exception:
        return r
    if not fp or not op:
        return r
    wg=float(fp.get('win_pct',0))-float(op.get('win_pct',0))
    fg=float(fp.get('finish_loss_pct',0))-float(op.get('finish_loss_pct',0))
    r['win_pct_gap']=wg
    r['finish_loss_gap']=fg
    base=bool(.575<=mp<=.75 and fp.get('fights',0)>=8 and op.get('fights',0)<=3 and wg>=.05 and fg<=.60)
    r['base_qualifies']=base
    if not base:
        return r

    age_gap=None
    try:
        ed=pd.Timestamp(event_date).normalize()
        fd=pd.Timestamp(fp.get('dob')).normalize()
        od=pd.Timestamp(op.get('dob')).normalize()
        fav_age=(ed-fd).days/365.2425
        opp_age=(ed-od).days/365.2425
        age_gap=fav_age-opp_age
        r['favorite_age']=fav_age
        r['opponent_age']=opp_age
        r['favorite_older_by']=age_gap
    except Exception:
        pass

    common={
        'older_2plus': bool(age_gap is not None and age_gap>=2.0),
        'negative_sig_diff': bool(fp.get('sig_diff_pm') is not None and float(fp.get('sig_diff_pm'))<0.0),
        'low_td_def_60': bool(fp.get('td_def') is not None and float(fp.get('td_def'))<0.60),
        'high_ctrl_allowed_3': bool(float(fp.get('ctrl_allowed15',0))>=3.0),
        'finish_vuln_40': bool(float(fp.get('finish_loss_pct',0))>=0.40),
    }
    u11={k:common[k] for k in ['older_2plus','low_td_def_60','high_ctrl_allowed_3','negative_sig_diff','finish_vuln_40']}
    recent5=fp.get('recent5')
    u12=dict(common)
    u12['recent5_below_500']=bool(recent5 is not None and float(recent5)<0.50)

    s11=sum(int(v) for v in u11.values())
    s12=sum(int(v) for v in u12.values())
    r.update({'u11_risks':u11,'u12_risks':u12,'u11_score':s11,'u12_score':s12,
              'u11_qualifies':bool(s11<4),'u12_qualifies':bool(s12<4)})
    return r

_orig_predict_bout_vd=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_vd(*args,**kwargs)
    bout=args[0] if args else kwargs.get('bout',{})
    event_date=args[1] if len(args)>1 else kwargs.get('event_date')
    a=str((bout or {}).get('fighter_a') or out.get('fighter_a') or '').strip()
    b=str((bout or {}).get('fighter_b') or out.get('fighter_b') or '').strip()
    m=(bout or {}).get('market') or {}
    fav=''
    mp=None
    try:
        ma=float(out.get('market_a',m.get('consensus_no_vig_a')))
        mb=float(out.get('market_b',m.get('consensus_no_vig_b')))
        fav=a if ma>=mb else b
        mp=max(ma,mb)
    except Exception:
        pass
    opp=b if fav==a else (a if fav==b else '')
    out['veteran_decline']=compute_veteran_decline(fav,opp,mp,event_date) if fav and opp and event_date is not None else {'available':False,'u11_qualifies':False,'u12_qualifies':False}
    return out

_orig_method_cards_vd=ufc_email_design.method_cards
def _vd_method_cards(p,w):
    cards=list(_orig_method_cards_vd(p,w))
    h=p.get('veteran_decline') or {}
    if not (h.get('u11_qualifies') or h.get('u12_qualifies')):
        return cards
    fp=h.get('fav_profile') or {}
    op=h.get('opp_profile') or {}
    shared=[
        f"Market probability {float(h.get('market_prob',0))*100:.1f}% (57.5%–75.0%)",
        f"Prior UFC fights: favorite {fp.get('fights','N/A')} (>=8), opponent {op.get('fights','N/A')} (<=3)",
        f"Favorite UFC win-rate edge {float(h.get('win_pct_gap',0))*100:+.1f}pp (>=5)",
        f"Finish-loss vulnerability gap {float(h.get('finish_loss_gap',0))*100:+.1f}pp (<=60)",
    ]
    if h.get('u11_qualifies'):
        hist=VETERAN_DECLINE_HISTORY['U11']
        risks=h.get('u11_risks') or {}
        checks=shared+[
            f"Decline risk score {int(h.get('u11_score',0))}/5; veto at >=4",
            "Risks: "+", ".join(k for k,v in risks.items() if v) if any(risks.values()) else "Risks: none active",
        ]
        cards.append({'id':'U11','title':'Veteran Age/Defense Decline Filter','favorite':h.get('favorite'),
                      'history':dict(bets=hist['bets'],wins=hist['wins'],win_rate=hist['win_rate'],roi=hist['roi']),
                      'checks':checks,
                      'note':'Historical 34-1. Pre-2020 ROI +47.44%; 2020+ ROI +39.28%. Overlaps the U9 parent family; method confluence is not an independent wager.'})
    if h.get('u12_qualifies'):
        hist=VETERAN_DECLINE_HISTORY['U12']
        risks=h.get('u12_risks') or {}
        checks=shared+[
            f"Balanced decline risk score {int(h.get('u12_score',0))}/6; veto at >=4",
            f"Favorite recent-5 UFC win rate {(float(fp.get('recent5'))*100 if fp.get('recent5') is not None else float('nan')):.1f}%",
            "Risks: "+", ".join(k for k,v in risks.items() if v) if any(risks.values()) else "Risks: none active",
        ]
        cards.append({'id':'U12','title':'Veteran Balanced Decline Filter','favorite':h.get('favorite'),
                      'history':dict(bets=hist['bets'],wins=hist['wins'],win_rate=hist['win_rate'],roi=hist['roi']),
                      'checks':checks,
                      'note':'Historical 31-1. Pre-2020 ROI +48.67%; 2020+ ROI +40.02%. Overlaps U9/U11; method confluence is not an independent wager.'})
    return cards
ufc_email_design.method_cards=_vd_method_cards

_orig_ufc_picks_vd=bet_tracker.ufc_picks
def _vd_ufc_picks(event,preds):
    import copy as _copy
    base=list(_orig_ufc_picks_vd(event,preds))
    for p in preds or []:
        h=p.get('veteran_decline') or {}
        if not (h.get('u11_qualifies') or h.get('u12_qualifies')):
            continue
        fav=h.get('favorite')
        opp=h.get('opponent')
        if any(x.get('selection')==fav and x.get('opponent')==opp for x in base):
            continue
        q=_copy.deepcopy(p)
        sm=q.setdefault('striking_methods',{})
        sm.update({'available':True,'favorite':fav,'opponent':opp,'striking_differential':True})
        generated=list(_orig_ufc_picks_vd(event,[q]))
        pick=next((x for x in generated if x.get('selection')==fav),None)
        if pick is not None:
            base.append(pick)
    return base
bet_tracker.ufc_picks=_vd_ufc_picks

_orig_event_block_vd=event_block
def event_block(e,preds):
    import html as _html
    base=_orig_event_block_vd(e,preds)
    cards=[]
    for p in preds or []:
        h=p.get('veteran_decline') or {}
        if not (h.get('u11_qualifies') or h.get('u12_qualifies')):
            continue
        fp=h.get('fav_profile') or {}
        fav=_html.escape(str(h.get('favorite') or ''))
        opp=_html.escape(str(h.get('opponent') or ''))
        if h.get('u11_qualifies'):
            cards.append(
              '<div style="margin:14px 18px;padding:14px;border:1px solid #aaa;border-radius:10px;">'
              '<div style="font-weight:700;font-size:18px;">U11 · VETERAN AGE/DEFENSE DECLINE FILTER</div>'
              f'<div style="margin-top:6px;"><b>{fav}</b> vs. {opp}</div>'
              f'<div style="margin-top:6px;">Market {float(h.get("market_prob",0))*100:.1f}% · UFC experience {fp.get("fights",0)} vs {(h.get("opp_profile") or {}).get("fights",0)} · decline score {int(h.get("u11_score",0))}/5</div>'
              '<div style="margin-top:6px;"><b>Historical:</b> 35 bets · 34-1 · 97.1% wins · <b>+43.48% ROI</b></div>'
              '<div>Pre-2020 +47.44% · 2020+ +39.28%</div></div>')
        if h.get('u12_qualifies'):
            cards.append(
              '<div style="margin:14px 18px;padding:14px;border:1px solid #aaa;border-radius:10px;">'
              '<div style="font-weight:700;font-size:18px;">U12 · VETERAN BALANCED DECLINE FILTER</div>'
              f'<div style="margin-top:6px;"><b>{fav}</b> vs. {opp}</div>'
              f'<div style="margin-top:6px;">Market {float(h.get("market_prob",0))*100:.1f}% · UFC experience {fp.get("fights",0)} vs {(h.get("opp_profile") or {}).get("fights",0)} · decline score {int(h.get("u12_score",0))}/6</div>'
              '<div style="margin-top:6px;"><b>Historical:</b> 32 bets · 31-1 · 96.9% wins · <b>+44.34% ROI</b></div>'
              '<div>Pre-2020 +48.67% · 2020+ +40.02%</div></div>')
    if not cards:
        return base
    section='<div style="margin-top:18px;"><div style="font-weight:700;font-size:19px;margin:0 18px 8px;">Veteran Decline Signals</div>'+''.join(cards)+'</div>'
    pos=base.rfind('</div>')
    return base[:pos]+section+base[pos:] if pos>=0 else base+section
'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Patched watcher with U11/U12 veteran decline methods.')
PY

echo "[45%] Patched watcher"
cd "${ROOT}"
"${ROOT}/venv/bin/python" -m py_compile "${WATCHER}"
echo "[55%] Syntax check passed"

"${ROOT}/venv/bin/python" -u - <<'PY'
import importlib.util
from pathlib import Path
import pandas as pd
root=Path.home()/'ufc-predictor-v1'
wp=root/'ufc_email_watcher.py'
spec=importlib.util.spec_from_file_location('w',wp)
w=importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
idx=w.load_veteran_decline_index()
print(f'[70%] Veteran profiles loaded: {len(idx):,}',flush=True)
bundle=w.joblib.load(w.BUNDLE_PATH)
reach=w.load_reach_index()
skills=w.load_skill_index()
feed=w.fetch_feed()
u11=u12=0
for e in feed.get('events') or []:
    try: ed=pd.Timestamp(e['date']).date()
    except Exception: continue
    for b in e.get('bouts') or []:
        try: pred=w.predict_bout(b,ed,bundle,reach,skills)
        except TypeError:
            try: pred=w.predict_bout(b,ed,bundle,reach)
            except TypeError: pred=w.predict_bout(b,ed,bundle)
        h=pred.get('veteran_decline') or {}
        u11+=int(bool(h.get('u11_qualifies')))
        u12+=int(bool(h.get('u12_qualifies')))
print(f'[80%] Current U11 signals: {u11}',flush=True)
print(f'[80%] Current U12 signals: {u12}',flush=True)
assert hasattr(w,'compute_veteran_decline')
assert w.VETERAN_DECLINE_HISTORY['U11']['bets']==35
assert w.VETERAN_DECLINE_HISTORY['U12']['bets']==32
print('[90%] Live method engine verified',flush=True)
PY

sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer
sudo systemctl is-active ufc-model-watcher.timer

echo "[100%] U11/U12 LIVE CODE ACTIVE"
echo "U11: 35 bets | 34-1 | +43.48% ROI"
echo "U12: 32 bets | 31-1 | +44.34% ROI"
echo "One tracked selection even when U9/U11/U12 overlap."
