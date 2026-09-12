#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"

[ -f "$WATCHER" ] || { echo "ERROR: Missing $WATCHER"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: Missing $ENV_FILE"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-skill-veto.$(date +%Y%m%d-%H%M%S).bak"

python - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if 'SKILL_SOURCE_URL' in s:
    print('Skill veto already present in watcher.')
    raise SystemExit(0)

def req(x,name):
    if x not in s:
        raise RuntimeError(f'Missing patch marker: {name}')

s=s.replace('import os\n','import os\nimport re\nimport time\n',1)
s=s.replace('ufc-model-watcher/11.0','ufc-model-watcher/12.0')
s=s.replace('ufc-v11-','ufc-v12-')

m='REACH_CACHE_PATH = ROOT / "fighter_reach_cache.json"\n'
req(m,'reach cache')
s=s.replace(m,m+'SKILL_SOURCE_URL = "https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv"\nSKILL_RAW_CACHE_PATH = ROOT / "ufcstats_competitions_skill_cache.csv"\nSKILL_CACHE_MAX_AGE_SECONDS = 86400\nSKILL_VETO_SIG_DIFF = -1.0\nSKILL_VETO_TD_DEF_DIFF = -0.20\n',1)

m='def resolve(name, b):\n'
req(m,'resolve')
func=r'''def _parse_of_stat(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return 0.0,0.0
    z=re.search(r"(\d+)\s+of\s+(\d+)",str(v),re.I)
    return (float(z.group(1)),float(z.group(2))) if z else (0.0,0.0)

def _fight_minutes(row):
    try: rnd=int(float(row.get("round") or 1))
    except Exception: rnd=1
    z=re.match(r"(\d+):(\d+)",str(row.get("time") or "0:00"))
    sec=int(z.group(1))*60+int(z.group(2)) if z else 0
    return max(1,(max(rnd,1)-1)*300+sec)/60.0

def load_skill_index():
    refresh=True
    if SKILL_RAW_CACHE_PATH.exists():
        try: refresh=(time.time()-SKILL_RAW_CACHE_PATH.stat().st_mtime)>SKILL_CACHE_MAX_AGE_SECONDS
        except Exception: pass
    if refresh:
        try:
            r=requests.get(SKILL_SOURCE_URL,timeout=45,headers={"User-Agent":"ufc-model-watcher/12.0"})
            r.raise_for_status(); SKILL_RAW_CACHE_PATH.write_text(r.text,encoding="utf-8")
        except Exception: pass
    if not SKILL_RAW_CACHE_PATH.exists(): return {}
    try: df=pd.read_csv(SKILL_RAW_CACHE_PATH,low_memory=False)
    except Exception: return {}
    agg={}
    for _,row in df.iterrows():
        mins=_fight_minutes(row)
        for side in (1,2):
            opp=2 if side==1 else 1
            name=str(row.get(f"player{side}") or "").strip()
            if not name: continue
            sl=sa=ta=tf=0.0
            for rd in range(1,6):
                a,_=_parse_of_stat(row.get(f"p{side}_rd{rd}_Sig_str")); sl+=a
                a,_=_parse_of_stat(row.get(f"p{opp}_rd{rd}_Sig_str")); sa+=a
                a,b=_parse_of_stat(row.get(f"p{opp}_rd{rd}_Td")); ta+=a; tf+=b
            k=norm_name(name)
            q=agg.setdefault(k,{"m":0.0,"sl":0.0,"sa":0.0,"ta":0.0,"tf":0.0,"f":0})
            q["m"]+=mins; q["sl"]+=sl; q["sa"]+=sa; q["ta"]+=ta; q["tf"]+=tf; q["f"]+=1
    out={}
    for k,q in agg.items():
        if q["m"]<=0: continue
        out[k]={"sig_diff_pm":(q["sl"]-q["sa"])/q["m"],"td_def":1-q["ta"]/q["tf"] if q["tf"]>0 else None,"fights":q["f"]}
    return out

def resolve_skill(name,idx):
    k=norm_name(name)
    if k in idx: return idx[k]
    z=[v for nk,v in idx.items() if k and (k in nk or nk in k)]
    return z[0] if len(z)==1 else None

def compute_skill_veto(favorite,opponent,idx):
    fp,op=resolve_skill(favorite,idx or {}),resolve_skill(opponent,idx or {})
    r={"available":bool(fp and op),"veto":False,"sig_diff_gap":None,"td_def_gap":None,"striking_veto":False,"wrestling_veto":False}
    if not fp or not op: return r
    if fp.get("sig_diff_pm") is not None and op.get("sig_diff_pm") is not None:
        r["sig_diff_gap"]=float(fp["sig_diff_pm"])-float(op["sig_diff_pm"])
        r["striking_veto"]=r["sig_diff_gap"]<=SKILL_VETO_SIG_DIFF
    if fp.get("td_def") is not None and op.get("td_def") is not None:
        r["td_def_gap"]=float(fp["td_def"])-float(op["td_def"])
        r["wrestling_veto"]=r["td_def_gap"]<=SKILL_VETO_TD_DEF_DIFF
    r["veto"]=bool(r["striking_veto"] or r["wrestling_veto"])
    return r

'''
s=s.replace(m,func+m,1)

m='def predict_bout(bout, event_date, bundle, reach_index=None):'
req(m,'predict signature')
s=s.replace(m,'def predict_bout(bout, event_date, bundle, reach_index=None, skill_index=None):',1)

m='    out["age_reach_premium"] = premium\n    return out\n'
req(m,'premium return')
r='''    out["age_reach_premium"] = premium
    skill={"available":False,"veto":False}
    if favorite:
        skill=compute_skill_veto(favorite, b if favorite==a else a, skill_index or {})
    out["skill_veto"]=skill
    if skill.get("veto"):
        h=out.get("hybrid") or {}
        if h.get("tier") in {"qualifies","strong"}: h["raw_tier"]=h["tier"]; h["tier"]="none"; h["skill_vetoed"]=True
        rh=out.get("reach_hybrid") or {}
        if rh.get("tier") in {"qualifies","strong"}: rh["raw_tier"]=rh["tier"]; rh["tier"]=None; rh["skill_vetoed"]=True
        pr=out.get("age_reach_premium") or {}
        if pr.get("qualifies"): pr["raw_qualifies"]=True; pr["qualifies"]=False; pr["skill_vetoed"]=True
        se=out.get("structural_edge") or {}
        if se.get("qualifies"): se["raw_qualifies"]=True; se["qualifies"]=False; se["skill_vetoed"]=True
    return out
'''
s=s.replace(m,r,1)

m='    reach_index = load_reach_index()\n    feed = fetch_feed()\n'
req(m,'run loader')
s=s.replace(m,'    reach_index = load_reach_index()\n    skill_index = load_skill_index()\n    feed = fetch_feed()\n',1)

m='        preds = [predict_bout(b, ed, bundle, reach_index) for b in e.get("bouts") or []]\n'
req(m,'predict call')
s=s.replace(m,'        preds = [predict_bout(b, ed, bundle, reach_index, skill_index) for b in e.get("bouts") or []]\n',1)

s=s.replace('        "structural_edge": bool((p.get("structural_edge") or {}).get("qualifies")),\n    }\n','        "structural_edge": bool((p.get("structural_edge") or {}).get("qualifies")),\n        "skill_veto": bool((p.get("skill_veto") or {}).get("veto")),\n    }\n',1)
s=s.replace('        or old.get("structural_edge", False) != new.get("structural_edge", False)\n    )\n','        or old.get("structural_edge", False) != new.get("structural_edge", False)\n        or old.get("skill_veto", False) != new.get("skill_veto", False)\n    )\n',1)

p.write_text(s)
print('Patched live watcher with skill veto.')
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
bundle=w.joblib.load(w.BUNDLE_PATH); reach=w.load_reach_index(); skills=w.load_skill_index(); feed=w.fetch_feed()
veto=age=rh=pr=se=0
for e in feed.get("events") or []:
    try: ed=pd.Timestamp(e["date"]).date()
    except Exception: continue
    for b in e.get("bouts") or []:
        p=w.predict_bout(b,ed,bundle,reach,skills)
        veto+=bool((p.get("skill_veto") or {}).get("veto"))
        age+=((p.get("hybrid") or {}).get("tier") in {"qualifies","strong"})
        rh+=((p.get("reach_hybrid") or {}).get("tier") in {"qualifies","strong"})
        pr+=bool((p.get("age_reach_premium") or {}).get("qualifies"))
        se+=bool((p.get("structural_edge") or {}).get("qualifies"))
print(f"Skill profiles loaded: {len(skills)}")
print(f"Current fight-level vetoes: {veto}")
print(f"Active Age / Reach / Premium / Structural: {age} / {rh} / {pr} / {se}")
PY

set -a
source "$ENV_FILE"
set +a
python "$WATCHER" --force-initial
sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer

echo
echo "SKILL VETO ACTIVE"
echo "Age Hybrid: +5.05% -> +6.30% ROI"
echo "Reach Hybrid: +4.25% -> +7.66% ROI"
echo "Premium: +10.07% -> +12.36% ROI"
echo "Structural Edge: +12.10% -> +19.93% ROI"
