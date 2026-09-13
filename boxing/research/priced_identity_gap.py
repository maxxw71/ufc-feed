#!/usr/bin/env python3
"""Rank missing fighter identities by their value to priced-bout research."""
from __future__ import annotations
import collections,json,sqlite3,unicodedata,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',x)

def main():
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    profiles=[dict(r) for r in con.execute("select source_id,name from normalized_fighters")]
    known=collections.defaultdict(set)
    for r in profiles:known[nk(r['name'])].add(r['source_id'])
    # Also index observed self-names, because profile/article titles can differ.
    for r in con.execute("select distinct url,boxer_a from bouts where source='wikipedia'"):
        known[nk(r['boxer_a'])].add(r['url'])
    quotes=[dict(r) for r in con.execute("select * from priced_bout_research where result in ('WIN','LOSS')")]
    bybout=collections.defaultdict(list)
    for r in quotes:bybout[(r['odds_bout_id'],r['event_date'])].append(r)
    missing=collections.defaultdict(lambda:{'priced_bouts':set(),'quote_rows':0,'dates':set(),'bookmakers':set(),'examples':set()})
    resolved=0;total_sides=0
    for key,rs in bybout.items():
        names=sorted({str(r['selection']).strip() for r in rs if r.get('selection')})
        for name in names:
            total_sides+=1
            if known.get(nk(name)):
                resolved+=1;continue
            x=missing[name];x['priced_bouts'].add(key);x['dates'].add(key[1]);x['examples'].add(key[0])
            for r in rs:
                if str(r['selection']).strip()==name:
                    x['quote_rows']+=1;x['bookmakers'].add(r['bookmaker'])
    ranked=[]
    for name,x in missing.items():
        ranked.append({'name':name,'priced_bouts':len(x['priced_bouts']),'quote_rows':x['quote_rows'],
                       'bookmakers':len(x['bookmakers']),'first_date':min(x['dates']) if x['dates'] else None,
                       'last_date':max(x['dates']) if x['dates'] else None,'example_odds_bout_ids':sorted(x['examples'])[:5]})
    ranked.sort(key=lambda r:(-r['priced_bouts'],-r['quote_rows'],r['name']))
    report={'priced_bout_sides':total_sides,'resolved_sides_by_existing_identity':resolved,
            'resolved_pct':100*resolved/total_sides if total_sides else None,
            'missing_unique_fighters':len(ranked),'priority_missing':ranked[:500],
            'policy':'Backfill highest priced-bout coverage first; identity still requires source verification before research use.'}
    (ROOT/'PRICED_IDENTITY_GAP.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({'priced_bout_sides':total_sides,'resolved':resolved,'missing_unique':len(ranked),'top':ranked[:20]},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
