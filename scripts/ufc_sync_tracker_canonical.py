#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/home/anestishkurti92/ufc-predictor-v1')
CANON=ROOT/'canonical_selections.json'
DB=Path('/home/anestishkurti92/betting-ledger/assumed_bets.sqlite3')

def dt(v):
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if x.tzinfo is None:return None
        return x.astimezone(timezone.utc)
    except Exception:return None

def main():
    now=datetime.now(timezone.utc)
    canon=json.loads(CANON.read_text()) if CANON.exists() else {}
    current={str(x.get('id') or x.get('key') or '') for x in canon.get('selections') or [] if str(x.get('id') or x.get('key') or '')}

    db=sqlite3.connect(str(DB));db.row_factory=sqlite3.Row
    rows=db.execute("select key,start,result,payload from bets where sport='UFC' and result='pending'").fetchall()
    withdrawn=[];reactivated=[]
    for r in rows:
        st=dt(r['start'])
        if not st or st<=now:continue
        key=str(r['key'])
        try:p=json.loads(r['payload'] or '{}')
        except Exception:p={}
        if key in current:
            if p.get('withdrawn') or p.get('current_approval_status')=='withdrawn':
                p['withdrawn']=False
                p['current_approval_status']='approved'
                p['reactivated_at']=now.isoformat()
                p.pop('withdrawal_reason',None)
                db.execute("update bets set payload=? where key=?",(json.dumps(p,default=str),key))
                db.execute("insert into audit(at,bet_key,action,details) values (?,?,?,?)",
                           (now.isoformat(),key,'ufc_canonical_reactivated',json.dumps({'canonical':True})))
                reactivated.append(key)
            continue

        if p.get('withdrawn') is True and p.get('current_approval_status')=='withdrawn':
            continue
        why=['no_longer_in_canonical_approved_set']
        p['withdrawn']=True
        p['current_approval_status']='withdrawn'
        p['withdrawn_at']=now.isoformat()
        p['withdrawal_reason']=why
        # Preserve first_sent, price, methods and result. This is current-approval
        # state only; it never rewrites what was originally sent or tracked.
        db.execute("update bets set payload=? where key=?",(json.dumps(p,default=str),key))
        db.execute("insert into audit(at,bet_key,action,details) values (?,?,?,?)",
                   (now.isoformat(),key,'ufc_canonical_withdrawal',json.dumps({'reasons':why})))
        withdrawn.append({'key':key,'reasons':why})
    db.commit();db.close()
    print(json.dumps({'updated_at':now.isoformat(),'canonical_count':len(current),
                      'withdrawn':withdrawn,'reactivated':reactivated},indent=2))

if __name__=='__main__':main()
