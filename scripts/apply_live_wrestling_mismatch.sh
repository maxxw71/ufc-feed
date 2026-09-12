#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"

[ -f "$WATCHER" ] || { echo "ERROR: Missing $WATCHER"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: Missing $ENV_FILE"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-wrestling-mismatch.$(date +%Y%m%d-%H%M%S).bak"

python - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if 'WRESTLING_MISMATCH_RULE_V1' in s:
    print('Wrestling Mismatch already present in watcher.')
    raise SystemExit(0)

marker='if __name__ == "__main__":'
if marker not in s:
    marker="if __name__ == '__main__':"
if marker not in s:
    raise RuntimeError('Could not find watcher main marker')

addon=r'''
# WRESTLING_MISMATCH_RULE_V1
# Broad historical rule: 72 bets, 56-16, 77.8% wins, +16.24% ROI.
# Strong historical rule: 38 bets, 31-7, 81.6% wins, +20.07% ROI.
_WM_INDEX = None

def _wm_parse_ctrl(v):
    import re as _re
    if v is None: return 0.0
    z=_re.match(r"\s*(\d+):(\d+)\s*$",str(v))
    return (60*int(z.group(1))+int(z.group(2))) if z else 0.0

def load_wrestling_index():
    global _WM_INDEX
    if _WM_INDEX is not None: return _WM_INDEX
    try:
        # Refresh the same UFCStats cache used by the live skill veto.
        if 'load_skill_index' in globals():
            try: load_skill_index()
            except Exception: pass
        path=globals().get('SKILL_RAW_CACHE_PATH', ROOT/'ufcstats_competitions_skill_cache.csv')
        if not path.exists():
            _WM_INDEX={}; return _WM_INDEX
        df=pd.read_csv(path,low_memory=False)
    except Exception:
        _WM_INDEX={}; return _WM_INDEX
    agg={}
    for _,row in df.iterrows():
        try: mins=_fight_minutes(row)
        except Exception: mins=0.0
        if not mins or mins<=0: continue
        for side in (1,2):
            name=str(row.get(f'player{side}') or '').strip()
            if not name: continue
            td_l=td_a=ctrl_sec=0.0
            for rd in range(1,6):
                try:
                    a,b=_parse_of_stat(row.get(f'p{side}_rd{rd}_Td')); td_l+=a; td_a+=b
                except Exception: pass
                ctrl_sec+=_wm_parse_ctrl(row.get(f'p{side}_rd{rd}_Ctrl'))
            k=norm_name(name)
            q=agg.setdefault(k,{'m':0.0,'td_l':0.0,'td_a':0.0,'ctrl':0.0,'f':0})
            q['m']+=mins; q['td_l']+=td_l; q['td_a']+=td_a; q['ctrl']+=ctrl_sec/60.0; q['f']+=1
    out={}
    for k,q in agg.items():
        if q['m']<=0: continue
        sc=15.0/q['m']
        out[k]={
            'fights':q['f'],
            'td_l15':q['td_l']*sc,
            'td_a15':q['td_a']*sc,
            'ctrl15':q['ctrl']*sc,
        }
    _WM_INDEX=out
    return out

def _wm_resolve(name,idx):
    k=norm_name(name)
    if k in idx: return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_wrestling_mismatch(favorite,opponent):
    idx=load_wrestling_index()
    fp=_wm_resolve(favorite,idx or {}); op=_wm_resolve(opponent,idx or {})
    r={'available':bool(fp and op),'qualifies':False,'strong':False,'tier':None,'favorite':favorite,'opponent':opponent,'fav_profile':fp,'opp_profile':op}
    if not fp or not op: return r
    base=(fp.get('fights',0)>=2 and op.get('fights',0)>=2 and
          fp.get('td_a15',0)>=5.0 and fp.get('td_l15',0)>=1.0 and fp.get('ctrl15',0)>=1.0)
    if not base: return r
    broad=(op.get('td_a15',999)<=2.0 and op.get('ctrl15',999)<=0.75)
    strong=(op.get('td_a15',999)<=1.0 and op.get('ctrl15',999)<=0.50)
    r['qualifies']=bool(broad)
    r['strong']=bool(strong)
    if strong:
        r['tier']='strong'; r.update({'hist_n':38,'hist_wins':31,'hist_losses':7,'hist_win_rate':0.816,'hist_roi':0.2007,'hist_pre_roi':0.1882,'hist_recent_roi':0.2132})
    elif broad:
        r['tier']='qualifies'; r.update({'hist_n':72,'hist_wins':56,'hist_losses':16,'hist_win_rate':0.778,'hist_roi':0.1624,'hist_pre_roi':0.0950,'hist_recent_roi':0.2261})
    return r

# Wrap the existing predictor so the new category is independent of Age/Reach.
_orig_predict_bout_wm = predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_wm(*args,**kwargs)
    bout=args[0] if args else kwargs.get('bout',{})
    a=str((bout or {}).get('fighter_a') or out.get('fighter_a') or out.get('a') or '').strip()
    b=str((bout or {}).get('fighter_b') or out.get('fighter_b') or out.get('b') or '').strip()
    fav=str(out.get('favorite') or '').strip()
    if not fav and a and b:
        m=(bout or {}).get('market') or {}
        pa=m.get('consensus_no_vig_a'); pb=m.get('consensus_no_vig_b')
        try: fav=a if float(pa)>=float(pb) else b
        except Exception: fav=''
    opp=''
    if fav:
        if a and fav==a: opp=b
        elif b and fav==b: opp=a
        else: opp=str(out.get('underdog') or '').strip()
    out['wrestling_mismatch']=compute_wrestling_mismatch(fav,opp) if fav and opp else {'available':False,'qualifies':False,'strong':False,'tier':None}
    return out

# Add a clean Wrestling Mismatch section to each event email.
_orig_event_block_wm = event_block
def event_block(e,preds):
    import html as _html
    base=_orig_event_block_wm(e,preds)
    cards=[]
    for p in preds or []:
        w=p.get('wrestling_mismatch') or {}
        if not w.get('qualifies'): continue
        fp=w.get('fav_profile') or {}; op=w.get('opp_profile') or {}
        title='WRESTLING MISMATCH STRONG' if w.get('strong') else 'WRESTLING MISMATCH'
        cards.append(
            '<div style="margin:14px 18px;padding:14px;border:1px solid #aaa;border-radius:10px;">'
            f'<div style="font-weight:700;font-size:18px;">{title}</div>'
            f'<div style="margin-top:6px;"><b>{_html.escape(str(w.get("favorite") or ""))}</b> vs. {_html.escape(str(w.get("opponent") or ""))}</div>'
            f'<div style="margin-top:6px;">Favorite wrestling: {fp.get("td_a15",0):.2f} TD attempts/15 · {fp.get("td_l15",0):.2f} TD landed/15 · {fp.get("ctrl15",0):.2f} min control/15</div>'
            f'<div>Opponent: {op.get("td_a15",0):.2f} TD attempts/15 · {op.get("ctrl15",0):.2f} min control/15</div>'
            f'<div style="margin-top:6px;"><b>Historical:</b> {int(w.get("hist_n",0))} bets · {int(w.get("hist_wins",0))}-{int(w.get("hist_losses",0))} · {float(w.get("hist_win_rate",0))*100:.1f}% wins · <b>{float(w.get("hist_roi",0))*100:+.2f}% ROI</b></div>'
            f'<div>Pre-2020 ROI {float(w.get("hist_pre_roi",0))*100:+.2f}% · 2020+ ROI {float(w.get("hist_recent_roi",0))*100:+.2f}%</div>'
            '</div>'
        )
    if not cards: return base
    section='<div style="margin-top:18px;"><div style="font-weight:700;font-size:19px;margin:0 18px 8px;">Wrestling Mismatch Signals</div>'+''.join(cards)+'</div>'
    pos=base.rfind('</div>')
    return base[:pos]+section+base[pos:] if pos>=0 else base+section

'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Patched live watcher with Wrestling Mismatch signals.')
PY

cd "$ROOT"
source "$ROOT/venv/bin/activate"
python -m py_compile "$WATCHER"

python - <<'PY'
import importlib.util
from pathlib import Path
import pandas as pd
root=Path.home()/"ufc-predictor-v1"; wp=root/"ufc_email_watcher.py"
spec=importlib.util.spec_from_file_location("w",wp); w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
bundle=w.joblib.load(w.BUNDLE_PATH); reach=w.load_reach_index(); skills=w.load_skill_index(); wrest=w.load_wrestling_index(); feed=w.fetch_feed()
q=s=0
for e in feed.get('events') or []:
    try: ed=pd.Timestamp(e['date']).date()
    except Exception: continue
    for b in e.get('bouts') or []:
        p=w.predict_bout(b,ed,bundle,reach,skills)
        wm=p.get('wrestling_mismatch') or {}
        q+=bool(wm.get('qualifies')); s+=bool(wm.get('strong'))
print(f'Wrestling profiles loaded: {len(wrest)}')
print(f'Current Wrestling Mismatch signals: {q} total / {s} strong')
PY

set -a
source "$ENV_FILE"
set +a
python "$WATCHER" --run --force-initial
sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer

echo
echo "WRESTLING MISMATCH ACTIVE"
echo "Broad:  TDatt>=5/15, TDland>=1/15, control>=1m/15; opponent TDatt<=2/15, control<=0.75m/15; prior>=2"
echo "        72 bets | 56-16 | 77.8% wins | +16.24% ROI"
echo "Strong: same wrestler base; opponent TDatt<=1/15, control<=0.50m/15"
echo "        38 bets | 31-7 | 81.6% wins | +20.07% ROI"
echo "Preview email sent; timer restarted."
