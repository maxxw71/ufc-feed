#!/usr/bin/env python3
"""Install opponent-specific UFC live selection gate after existing U1-U12 patches.

Idempotent, backups handled by the workflow. The old qualification thresholds
and immutable betting/history rows are deliberately untouched.
"""
from pathlib import Path
import sys

watcher=Path(sys.argv[1])
s=watcher.read_text()
MARKER="# UFC_MATCHUP_REVIEW_GATE_V1"
if MARKER in s:
    print("Matchup review gate already installed")
    raise SystemExit(0)
main='if __name__ == "__main__":\n    main()'
if main not in s:
    raise SystemExit("Refusing to modify UFC watcher: main anchor missing")
required=["ufc_email_design.method_cards", "canonical_ufc_all_picks", "write_ufc_canonical"]
for item in required:
    if item not in s:
        raise SystemExit("Required canonical publication marker not present: "+item)

addon=r'''
# UFC_MATCHUP_REVIEW_GATE_V1
# Every prospective U1-U12 selection passes a separate point-in-time opponent
# review. These postmortem guardrails are conservative holds, NOT claimed
# validated improvements to historical ROI.
import json as _review_json
import re as _review_re
from pathlib import Path as _review_Path
from ufc_matchup_review_v1 import (
    assess_selection as _review_assess,
    check_line as _review_summary,
    VERSION as _review_version,
)
_UFC_MATCHUP_REVIEWS = {}
_orig_predict_bout_matchup=predict_bout

def _review_fight_key(p, event_date):
    a=_review_re.sub(r'[^a-z0-9]+',' ',str(p.get('fighter_a') or '').lower()).strip()
    b=_review_re.sub(r'[^a-z0-9]+',' ',str(p.get('fighter_b') or '').lower()).strip()
    return str(event_date)[:10]+'|'+ '|'.join(sorted((a,b)))

def _review_for(p, favorite):
    date0=p.get('_matchup_review_event_date')
    by_fav=p.setdefault('matchup_reviews',{})
    if favorite not in by_fav:
        try:
            by_fav[favorite]=_review_assess(p,favorite,date0,ROOT)
        except Exception as e:
            by_fav[favorite]={
                'version':_review_version,'favorite':favorite,
                'status':'HOLD','reasons':['MATCHUP_AUDIT_EXCEPTION'],
                'warnings':[type(e).__name__]
            }
    return by_fav[favorite]

def predict_bout(*args,**kwargs):
    p=_orig_predict_bout_matchup(*args,**kwargs)
    b=args[0] if args else kwargs.get('bout',{})
    date0=args[1] if len(args)>1 else kwargs.get('event_date')
    p['_matchup_review_event_date']=str(date0)[:10] if date0 is not None else None
    p.setdefault('matchup_reviews',{})
    # Audit BOTH sides for EVERY fight, regardless of qualification.
    for name in (str((b or {}).get('fighter_a') or ''),
                 str((b or {}).get('fighter_b') or '')):
        if name:
            _review_for(p,name)
    _UFC_MATCHUP_REVIEWS[_review_fight_key(p,date0)]={
        'date':str(date0)[:10],
        'fighter_a':str(p.get('fighter_a') or ''),
        'fighter_b':str(p.get('fighter_b') or ''),
        'prediction_status':p.get('status'),
        'market_status':(p.get('market') or {}).get('status'),
        'sides':dict(p['matchup_reviews'])
    }
    return p

_orig_method_cards_matchup=ufc_email_design.method_cards
def _matchup_review_method_cards(p,w):
    cards=list(_orig_method_cards_matchup(p,w))
    approved=[]
    for card in cards:
        fav=str(card.get('favorite') or '').strip()
        if not fav:continue
        review=_review_for(p,fav)
        # Fail closed on submission danger, incomplete prior UFCStats context,
        # missing raw archive, unresolved fighter identity, or processing errors.
        if review.get('status')!='PASS':
            continue
        c=dict(card)
        checks=list(c.get('checks') or [])
        checks.append(_review_summary(review))
        warnings=list(review.get('warnings') or [])
        if warnings:
            checks.append("Additional matchup cautions: "+", ".join(warnings[:5]))
        checks.append("Risk-review data: prior UFCStats fights only; regional/DWCS record NOT assumed complete")
        c['checks']=checks
        note=str(c.get('note') or '').strip()
        c['note']=(note+' | ' if note else '')+_review_version+' — independent fighter-versus-fighter pre-bet review passed'
        approved.append(c)
    return approved
ufc_email_design.method_cards=_matchup_review_method_cards

_orig_write_ufc_canonical_matchup=write_ufc_canonical
def write_ufc_canonical(picks,now=None):
    payload=_orig_write_ufc_canonical_matchup(picks,now=now)
    # Dedicated all-matchups audit includes both fighters, reason codes and
    # pre-fight source coverage. No previously settled rows are changed.
    rows=list(_UFC_MATCHUP_REVIEWS.values())
    rows.sort(key=lambda x:(str(x.get('date')),str(x.get('fighter_a'))))
    accepted={(str(x.get('event_date'))[:10],str(x.get('selection') or '').lower()) for x in picks}
    for row in rows:
        row['approved_sides']=[
            name for name,review in (row.get('sides') or {}).items()
            if (str(row.get('date')),str(name).lower()) in accepted
        ]
    report={
        'version':_review_version,
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'scope':'all evaluated UFC matchups; prior UFCStats archive only',
        'history_policy':'strictly before event date; same-day and later results excluded',
        'reviewed_matchups':len(rows),
        'officially_approved_picks':len(picks),
        'reviews':rows,
    }
    output=_review_Path('/srv/appwiza-sports/public/ufc/matchup_reviews.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp')
    temp.write_text(_review_json.dumps(report,indent=2,default=str))
    temp.chmod(0o644)
    temp.replace(output)
    print('UFC_MATCHUP_REVIEW_PUBLISHED',len(rows),'approved',len(picks),
          'held',sum(1 for r in rows for v in r['sides'].values() if v.get('status')=='HOLD'))
    return payload
'''
s=s.replace(main,addon+'\n'+main,1)
watcher.write_text(s)
print("Installed "+MARKER)
