#!/usr/bin/env python3
"""Recover missing opponent identities from stored reciprocal career rows.

No fuzzy guessing. A new link is accepted only when exactly one candidate fighter
identity has a same-date reciprocal bout, the opponent names point back through
observed aliases, and the outcomes are mutually consistent. Wikipedia and
verified secondary career supplements keep separate provenance but share this
identity graph.
"""
from __future__ import annotations
import collections,json,sqlite3,unicodedata,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'
ALLOWED_SOURCES=('wikipedia','champinon')

def namekey(s):
    s=re.sub(r'\([^)]*\)|\[[^]]*\]','',str(s or ''))
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',s)

def reciprocal_result(a,b):
    if a=='BOXER A': return b=='BOXER B'
    if a=='BOXER B': return b=='BOXER A'
    if a=='DRAW': return b=='DRAW'
    if a=='NO CONTEST': return b=='NO CONTEST'
    return False

def discover(rows,fighter_names,existing):
    histories=collections.defaultdict(list);aliases=collections.defaultdict(set)
    for r in rows:
        histories[r['url']].append(r)
        if r.get('boxer_a'):aliases[r['url']].add(namekey(r['boxer_a']))
    for fid,name in fighter_names.items():
        if fid in histories and name:aliases[fid].add(namekey(name))
    alias_index=collections.defaultdict(set)
    for fid,names in aliases.items():
        for n in names:
            if n:alias_index[n].add(fid)
    by_fighter_date=collections.defaultdict(list)
    for r in rows:by_fighter_date[(r['url'],r['date'])].append(r)
    additions={};ambiguous=0;no_candidate=0;no_recip=0
    for r in rows:
        bid=r['source_id']
        if bid in existing:continue
        target=namekey(r.get('boxer_b'));candidates=[x for x in alias_index.get(target,set()) if x!=r['url']]
        if not candidates:no_candidate+=1;continue
        passed=[];own_aliases=aliases[r['url']]
        for cand in candidates:
            for rr in by_fighter_date.get((cand,r['date']),[]):
                if namekey(rr.get('boxer_b')) not in own_aliases:continue
                if reciprocal_result(r.get('winner'),rr.get('winner')):passed.append(cand);break
        passed=sorted(set(passed))
        if len(passed)==1:additions[bid]=passed[0]
        elif len(passed)>1:ambiguous+=1
        else:no_recip+=1
    return additions,{'ambiguous':ambiguous,'no_candidate':no_candidate,'no_reciprocal_match':no_recip}

def main():
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    marks=','.join('?'*len(ALLOWED_SOURCES))
    rows=[dict(r) for r in con.execute(f"select * from bouts where source in ({marks}) and status='FINISHED' and date is not null order by date,source_id",ALLOWED_SOURCES)]
    names={r['source_id']:r['name'] for r in con.execute(f"select source_id,name from fighters where source in ({marks})",ALLOWED_SOURCES)}
    existing={r['bout_id']:r['opponent_id'] for r in con.execute("select bout_id,opponent_id from opponent_links where evidence='reciprocal_result_confirmed'")}
    additions,diag=discover(rows,names,existing)
    con.execute('''create table if not exists opponent_links_v2(
        bout_id text primary key, opponent_id text not null, evidence text not null,
        built_at text not null)''')
    con.execute('delete from opponent_links_v2')
    stamp=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    con.executemany('insert into opponent_links_v2 values(?,?,?,?)',[(bid,opp,'reciprocal_result_observed_alias',stamp) for bid,opp in additions.items()])
    con.commit()
    report={'existing_verified_links':len(existing),'new_verified_alias_links':len(additions),'combined_links':len(existing)+len(additions),
            'sources':list(ALLOWED_SOURCES),'diagnostics':diag,
            'method':'exact observed alias + same-date reciprocal row + reciprocal outcome; unique candidate only',
            'limitations':['Does not guess unresolved identities.','Does not use fuzzy edit distance.','Cannot recover opponents whose career page is absent from the database.']}
    (ROOT/'IDENTITY_LINKS_V2.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
