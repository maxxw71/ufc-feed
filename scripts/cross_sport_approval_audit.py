#!/usr/bin/env python3
from __future__ import annotations
import json, os, sqlite3, re
from datetime import datetime, timezone
from pathlib import Path

NOW=datetime.now(timezone.utc)
ROOT=Path('/srv/appwiza-sports')
LEDGER=Path('/home/anestishkurti92/betting-ledger/assumed_bets.sqlite3')
NFL_ROOT=Path('/home/anestishkurti92/nfl-predictor-v1/research_v2')
UFC_ROOT=Path('/home/anestishkurti92/ufc-predictor-v1')
MLS_ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
BOX_ROOT=Path('/home/anestishkurti92/boxing-research')

errors=[];warnings=[];checks=[]

def add(name,ok,detail='',hard=True):
    row={'name':name,'ok':bool(ok),'detail':detail,'hard':hard}
    checks.append(row)
    if not ok:
        (errors if hard else warnings).append(row)

def load_json(p,required=True):
    try:return json.loads(Path(p).read_text())
    except Exception as e:
        if required:add(f'json_read:{p}',False,f'{type(e).__name__}: {e}')
        return {}

def dt(v):
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if x.tzinfo is None:return None
        return x.astimezone(timezone.utc)
    except Exception:return None

def methods_from_card(c):
    out=[]
    for m in c.get('methods') or []:
        if isinstance(m,dict):
            v=m.get('id')
        else:v=m
        if v is not None:out.append(str(v))
    return sorted(set(out))

def norm_nfl_id(v):
    s=str(v or '')
    return s[4:] if s.startswith('NFL:') else s

def ledger_rows():
    if not LEDGER.exists():
        add('ledger_exists',False,str(LEDGER));return []
    db=sqlite3.connect(str(LEDGER));db.row_factory=sqlite3.Row
    rows=[dict(r) for r in db.execute("select key,sport,start,selection,opponent,price,book,methods,result,payload from bets order by start,key")]
    db.close();return rows

ledger=ledger_rows()
future_pending=[r for r in ledger if r.get('result')=='pending' and dt(r.get('start')) and dt(r.get('start'))>NOW]
pending_by_sport={}
for r in future_pending:pending_by_sport.setdefault(str(r.get('sport') or '').upper(),[]).append(r)

# Inventory all public/state sports surfaces.
public_dirs=sorted([p.name for p in (ROOT/'public').iterdir() if p.is_dir()]) if (ROOT/'public').exists() else []
state_files=sorted([p.stem for p in (ROOT/'state').glob('*.json')]) if (ROOT/'state').exists() else []
add('sports_surface_inventory',True,f'public_dirs={public_dirs}; state_files={state_files}',hard=False)

# ---------------- NFL ----------------
ncanon=load_json(NFL_ROOT/'home_opener_email_state/canonical_selections.json')
npub=load_json(ROOT/'public/nfl/current.json')
nstate=load_json(ROOT/'state/nfl.json')
nrej=load_json(NFL_ROOT/'home_opener_email_state/production_match_rejections.json',required=False)
npreview=(NFL_ROOT/'home_opener_email_state/preview.txt').read_text(errors='ignore') if (NFL_ROOT/'home_opener_email_state/preview.txt').exists() else ''

def nfl_map(d):
    out={}
    for x in d.get('selections') or []:
        gid=norm_nfl_id(x.get('game_id') or x.get('id'))
        if gid:
            out[gid]={'selection':str(x.get('selection') or x.get('selection_code') or ''),
                      'opponent':str(x.get('opponent') or x.get('opponent_code') or ''),
                      'methods':sorted(str(m) for m in (x.get('rules') or x.get('methods') or [])),
                      'price':x.get('price'),'book':x.get('book')}
    return out
nm=nfl_map(ncanon);npm=nfl_map(npub)
add('NFL canonical_public_current_exact',nm==npm,f'canonical={sorted(nm)} public={sorted(npm)}')

active={}
for c in nstate.get('cards') or []:
    st=dt(c.get('start'))
    if not st or st<=NOW or c.get('withdrawn'):continue
    gid=norm_nfl_id(c.get('id'))
    active[gid]={'selection':str(c.get('selection') or ''),'opponent':str(c.get('opponent') or ''),
                 'methods':methods_from_card(c),'price':c.get('price'),'book':c.get('book')}
# IDs/methods are the hard contract; display names can differ code/full-name.
add('NFL canonical_state_ids_exact',set(nm)==set(active),f'canonical={sorted(nm)} state={sorted(active)}')
for gid in sorted(set(nm)&set(active)):
    add(f'NFL methods_exact:{gid}',nm[gid]['methods']==active[gid]['methods'],f"canonical={nm[gid]['methods']} state={active[gid]['methods']}")
rejected_ids={str(x.get('game_id') or '') for x in (nrej.get('rejections') or [])}
add('NFL rejected_not_canonical',not (rejected_ids & set(nm)),f'overlap={sorted(rejected_ids & set(nm))}')

nfut={norm_nfl_id(r.get('key')) for r in pending_by_sport.get('NFL',[])}
add('NFL canonical_future_tracker_exact',set(nm)==nfut,f'canonical={sorted(nm)} tracker={sorted(nfut)}')

for gid,x in nm.items():
    if x['selection'] and x['selection'] not in npreview:
        warnings.append({'name':f'NFL preview_selection_text:{gid}','ok':False,'detail':x['selection'],'hard':False})
# Static live-code protection markers.
scanner=(NFL_ROOT/'nfl_home_opener_scanner.py').read_text(errors='ignore') if (NFL_ROOT/'nfl_home_opener_scanner.py').exists() else ''
guard=(NFL_ROOT/'production_match_guard.py').read_text(errors='ignore') if (NFL_ROOT/'production_match_guard.py').exists() else ''
page=(NFL_ROOT/'nfl_public_page.py').read_text(errors='ignore') if (NFL_ROOT/'nfl_public_page.py').exists() else ''
add('NFL common_production_guard_installed','match_guard.filter_records(raw_production_records,G,now)' in scanner)
add('NFL canonical_surface_installed','CANONICAL_SELECTIONS_V1' in scanner)
add('NFL render_isolation_installed','NFL_RENDER_ISOLATION_V1' in scanner)
add('NFL m_record_fail_closed',all(s in guard for s in ['M_FAMILY_MIN_PRIOR_SEASON_PCT = 0.500','prior_season_home_record_below_500','missing_prior_season_home_record']))
add('NFL page_missing_audit_fails_closed',"if not row:return ('veto','NO BET'" in page or "if not row: return ('veto','NO BET'" in page,
    'NFL public deep-audit must never treat a missing row as scanner-qualified/monitor')
add('NFL page_missing_preseason_fails_closed',"preseason is None" in page and "'veto','NO BET'" in page,
    'missing deep-audit preseason status must be no-bet')

# ---------------- UFC ----------------
ucanon=load_json(UFC_ROOT/'canonical_selections.json')
upub=load_json(ROOT/'public/ufc/current.json')
ustate=load_json(ROOT/'state/ufc.json')

def ufc_map(d):
    out={}
    for x in d.get('selections') or []:
        key=str(x.get('id') or x.get('key') or '')
        if key:out[key]={'selection':str(x.get('selection') or ''),'opponent':str(x.get('opponent') or ''),
                         'methods':sorted(str(m) for m in (x.get('methods') or [])),
                         'price':x.get('price'),'book':x.get('book')}
    return out
um=ufc_map(ucanon);upm=ufc_map(upub)
add('UFC canonical_public_current_exact',um==upm,f'canonical={sorted(um)} public={sorted(upm)}')
uactive={}
for c in ustate.get('cards') or []:
    st=dt(c.get('start'))
    if not st or st<=NOW or c.get('withdrawn'):continue
    key=str(c.get('id') or '')
    uactive[key]={'selection':str(c.get('selection') or ''),'opponent':str(c.get('opponent') or ''),
                  'methods':methods_from_card(c),'price':c.get('price'),'book':c.get('book')}
add('UFC canonical_state_ids_exact',set(um)==set(uactive),f'canonical={sorted(um)} state={sorted(uactive)}')
for key in sorted(set(um)&set(uactive)):
    add(f'UFC methods_exact:{key}',um[key]['methods']==uactive[key]['methods'],f"canonical={um[key]['methods']} state={uactive[key]['methods']}")
ufut={str(r.get('key') or '') for r in pending_by_sport.get('UFC',[])}
add('UFC canonical_future_tracker_exact',set(um)==ufut,f'canonical={sorted(um)} tracker={sorted(ufut)}')
no_bet={str(x.get('id') or '') for x in ucanon.get('no_bet_extreme_price') or []}
add('UFC extreme_no_bet_absent_from_canonical',not (no_bet & set(um)),f'overlap={sorted(no_bet & set(um))}')
add('UFC extreme_no_bet_absent_from_state',not (no_bet & set(uactive)),f'overlap={sorted(no_bet & set(uactive))}')
add('UFC extreme_no_bet_absent_from_tracker',not (no_bet & ufut),f'overlap={sorted(no_bet & ufut)}')
bad_price=[(k,v.get('price')) for k,v in um.items() if isinstance(v.get('price'),(int,float)) and float(v['price'])<=-2000]
add('UFC no_live_price_at_or_below_minus2000',not bad_price,f'bad={bad_price}')
watcher=(UFC_ROOT/'ufc_email_watcher.py').read_text(errors='ignore') if (UFC_ROOT/'ufc_email_watcher.py').exists() else ''
add('UFC canonical_surface_installed','UFC_CANONICAL_SURFACE_V1' in watcher)
add('UFC extreme_price_gate_installed','UFC_EXTREME_PRICE_NO_BET_V1' in watcher and 'UFC_NO_BET_AMERICAN_FAVORITE_CEILING=-2000' in watcher)
add('UFC render_isolation_installed','UFC_RENDER_ISOLATION_V1' in watcher)

# ---------------- MLS ----------------
mshadow=load_json(MLS_ROOT/'live/shadow/current_shadow_board.json',required=False)
mfwd=load_json(MLS_ROOT/'live/shadow/forward_record.json',required=False)
add('MLS shadow_only',bool(mshadow.get('shadow_only',True)) and int(mshadow.get('official_autopromotions',0) or 0)==0,
    f"shadow_only={mshadow.get('shadow_only')} official_autopromotions={mshadow.get('official_autopromotions')}")
add('MLS forward_record_no_autopromotion',int(mfwd.get('official_autopromotions',0) or 0)==0,f"official_autopromotions={mfwd.get('official_autopromotions')}")
add('MLS no_future_tracker_bets',len(pending_by_sport.get('MLS',[]))==0,f"pending={len(pending_by_sport.get('MLS',[]))}")
mstate=load_json(ROOT/'state/mls.json',required=False) if (ROOT/'state/mls.json').exists() else {}
mactive=[c for c in (mstate.get('cards') or []) if dt(c.get('start')) and dt(c.get('start'))>NOW and not c.get('withdrawn')]
add('MLS no_live_public_bets_while_shadow_only',len(mactive)==0,f'active_cards={len(mactive)}')

# ---------------- BOXING ----------------
bmethods={}
try:bmethods=json.loads((BOX_ROOT/'methods.json').read_text())
except Exception:
    # repo-synced path may not exist; live integration is research only.
    bmethods={}
if bmethods:
    add('BOXING registry_research_only',str(bmethods.get('status'))=='research_watchlist',f"status={bmethods.get('status')}")
    live=[x.get('id') for x in bmethods.get('methods') or [] if str(x.get('tracking_status','')).lower() in {'live','production','official'}]
    add('BOXING no_live_methods',not live,f'live={live}')
else:
    warnings.append({'name':'BOXING live methods registry unreadable','ok':False,'detail':str(BOX_ROOT/'methods.json'),'hard':False})
add('BOXING no_future_tracker_bets',len(pending_by_sport.get('BOXING',[]))==0,f"pending={len(pending_by_sport.get('BOXING',[]))}")
bstate=load_json(ROOT/'state/boxing.json',required=False) if (ROOT/'state/boxing.json').exists() else {}
bactive=[c for c in (bstate.get('cards') or []) if dt(c.get('start')) and dt(c.get('start'))>NOW and not c.get('withdrawn')]
add('BOXING no_live_public_bets_while_research_only',len(bactive)==0,f'active_cards={len(bactive)}')

# Unknown future pending sports are surfaced loudly.
known={'NFL','UFC','MLS','BOXING'}
unknown={k:len(v) for k,v in pending_by_sport.items() if k not in known}
add('no_unknown_future_pending_sports',not unknown,f'unknown={unknown}')

payload={
 'built_at':NOW.isoformat(),
 'status':'PASS' if not errors else 'FAIL',
 'hard_failures':errors,
 'warnings':warnings,
 'checks':checks,
 'inventory':{'public_dirs':public_dirs,'state_files':state_files,'future_pending_by_sport':{k:len(v) for k,v in pending_by_sport.items()}},
 'contract':{
   'NFL':'LIVE_CANONICAL_ONLY',
   'UFC':'LIVE_CANONICAL_ONLY_WITH_MINUS2000_NO_BET',
   'MLS':'SHADOW_ONLY_NO_OFFICIAL_BETS',
   'BOXING':'RESEARCH_ONLY_NO_OFFICIAL_BETS'
 }
}
print(json.dumps(payload,indent=2,default=str))
