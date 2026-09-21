#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1/research_v2')
CANON=ROOT/'home_opener_email_state/canonical_selections.json'
REJ=ROOT/'home_opener_email_state/production_match_rejections.json'
DB=Path('/home/anestishkurti92/betting-ledger/assumed_bets.sqlite3')

def dt(v):
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if x.tzinfo is None:return None
        return x.astimezone(timezone.utc)
    except Exception:return None

def norm(v):
    s=str(v or '')
    return s if s.startswith('NFL:') else 'NFL:'+s

def main():
    now=datetime.now(timezone.utc)
    canon=json.loads(CANON.read_text()) if CANON.exists() else {}
    current={norm(x.get('game_id') or x.get('id')) for x in canon.get('selections') or []}
    rej=json.loads(REJ.read_text()) if REJ.exists() else {}
    reasons={norm(x.get('game_id')):list(x.get('reasons') or []) for x in rej.get('rejections') or []}

    db=sqlite3.connect(str(DB));db.row_factory=sqlite3.Row
    rows=db.execute("select key,start,result,payload from bets where sport='NFL' and result='pending'").fetchall()
    changed=[];reactivated=[]
    for r in rows:
        st=dt(r['start'])
        if not st or st<=now:continue
        key=str(r['key'])
        p=json.loads(r['payload'] or '{}')
        if key in current:
            if p.get('withdrawn') or p.get('current_approval_status')=='withdrawn':
                p['withdrawn']=False
                p['current_approval_status']='approved'
                p['reactivated_at']=now.isoformat()
                p.pop('withdrawal_reason',None)
                db.execute("update bets set payload=? where key=?",(json.dumps(p,default=str),key))
                db.execute("insert into audit(at,bet_key,action,details) values (?,?,?,?)",
                           (now.isoformat(),key,'canonical_reactivated',json.dumps({'canonical':True})))
                reactivated.append(key)
            continue

        if p.get('withdrawn') is True and p.get('current_approval_status')=='withdrawn':
            continue
        why=reasons.get(key) or ['no_longer_in_canonical_approved_set']
        p['withdrawn']=True
        p['current_approval_status']='withdrawn'
        p['withdrawn_at']=now.isoformat()
        p['withdrawal_reason']=why
        # Preserve original first_sent/price/result. This is a current-approval marker,
        # not a retroactive deletion of a historically sent wager.
        db.execute("update bets set payload=? where key=?",(json.dumps(p,default=str),key))
        db.execute("insert into audit(at,bet_key,action,details) values (?,?,?,?)",
                   (now.isoformat(),key,'canonical_withdrawal',json.dumps({'reasons':why})))
        changed.append({'key':key,'reasons':why})
    db.commit();db.close()
    print(json.dumps({'updated_at':now.isoformat(),'canonical_count':len(current),'withdrawn':changed,'reactivated':reactivated},indent=2))

if __name__=='__main__':main()
