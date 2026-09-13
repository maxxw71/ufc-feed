#!/usr/bin/env python3
"""Apply verified supplemental Wikipedia careers to a temporary research DB.

This mutates only the downloaded/research copy used by workflows. It does not
write back to the Appwiza production database. Every inserted career was first
accepted by full-date + opponent evidence from a priced matchup.
"""
from __future__ import annotations
import json,re,sqlite3,unicodedata
from pathlib import Path
from features import cm
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'
SUP=ROOT.parent/'supplemental_careers'/'verified_priced_careers.jsonl'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',x)

def stance_value(v):
    s=str(v or '').casefold()
    return next((x for x in ('orthodox','southpaw','switch') if x in s),None)

def outcome(result):
    return {'win':'BOXER A','loss':'BOXER B','draw':'DRAW','nc':'NO CONTEST','no contest':'NO CONTEST'}[result]

def main():
    if not SUP.exists():
        print(json.dumps({'supplemental_fighters':0,'inserted_bouts':0,'quote_links_added':0}));return
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    added_bouts=0;added_quotes=0;fighters=0
    for line in SUP.read_text().splitlines():
        if not line.strip():continue
        x=json.loads(line);url=x['source_url'];name=x['requested_name'];profile=x.get('profile') or {};born=x.get('born') or ''
        con.execute('INSERT OR REPLACE INTO fighters(source,source_id,name,born,height_cm,snapshot) VALUES(?,?,?,?,?,?)',
                    ('wikipedia',url,name,born,None,json.dumps({'attributes':profile,'historical_use':'Supplemental verified priced-career page; current profile snapshot.'})))
        h=cm(profile.get('Height',''));reach=cm(profile.get('Reach',''))
        h=h if h is not None and 120<=h<=250 else None;reach=reach if reach is not None and 120<=reach<=270 else None
        con.execute('INSERT OR REPLACE INTO normalized_fighters(source_id,name,born,height_cm,reach_cm,stance,nationality,weight_class_snapshot,source_url,quality) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (url,name,born,h,reach,stance_value(profile.get('Stance')),profile.get('Nationality'),profile.get('Weight'),url,'supplemental_verified_priced_matchup_current_profile'))
        by_key={}
        for i,r in enumerate(x.get('career_rows') or []):
            ident=url+'#supp-'+str(i)+'-'+r['date'];raw=r.get('raw') or {}
            con.execute('''INSERT OR REPLACE INTO bouts(source,source_id,date,boxer_a,boxer_b,winner,method,rounds,scheduled_rounds,division,venue,status,url,data)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        ('wikipedia',ident,r['date'],name,r['opponent'],outcome(r['result']),r.get('type') or '',r.get('round_time') or '',None,None,r.get('location') or '', 'FINISHED',url,json.dumps(raw)))
            by_key[(r['date'],nk(r['opponent']))]=ident;added_bouts+=1
        for e in x.get('matched_price_evidence') or []:
            ident=by_key.get((e['date'],nk(e['opponent'])))
            if not ident:continue
            # Attach every exact selection quote for this PBO bout/date to the newly verified career row.
            cur=con.execute('''UPDATE priced_bout_research SET feature_bout_id=? WHERE odds_bout_id=? AND event_date=? AND lower(replace(replace(selection,' ',''),'-',''))=? AND feature_bout_id IS NULL''',
                            (ident,e['odds_bout_id'],e['date'],nk(name)))
            added_quotes+=cur.rowcount
        fighters+=1
    con.commit()
    print(json.dumps({'supplemental_fighters':fighters,'inserted_bouts':added_bouts,'quote_links_added':added_quotes}))
if __name__=='__main__':main()
