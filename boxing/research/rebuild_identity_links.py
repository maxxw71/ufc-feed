#!/usr/bin/env python3
"""Recover missing opponent identities from stored reciprocal career rows.

No fuzzy guessing. A new link is accepted only when exactly one candidate fighter
identity has a same-date reciprocal bout, the opponent names point back through
observed aliases, and the outcomes are mutually consistent. Accepted career
sources keep separate provenance but share this identity graph.
"""
from __future__ import annotations
import collections,json,sqlite3,unicodedata,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'
ALLOWED_SOURCES=('wikipedia','champinon','wba_consensus')

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


def ibf_bridge(rows,fighter_names,existing,ibf_rows):
    """Bridge an unresolved career opponent using exact official IBF bout evidence.

    Candidate career identities must already exist locally. IBF evidence only
    confirms date/pair/result; it never creates a standalone career identity.
    """
    histories=collections.defaultdict(list);aliases=collections.defaultdict(set)
    for r in rows:
        histories[r['url']].append(r)
        if r.get('boxer_a'):aliases[r['url']].add(namekey(r['boxer_a']))
    for fid,name in fighter_names.items():
        if fid in histories and name:aliases[fid].add(namekey(name))
    alias_index=collections.defaultdict(set)
    for fid,nameset in aliases.items():
        for n in nameset:
            if n:alias_index[n].add(fid)

    by_date=collections.defaultdict(list)
    for x in ibf_rows or []:
        date=str(x.get('date') or '')
        a=namekey(x.get('fighter_a'));b=namekey(x.get('fighter_b'))
        if not date or not a or not b or a==b:continue
        if x.get('winner_side') not in {'A','B','DRAW'}:continue
        by_date[date].append(x)

    additions={};ambiguous=0;checked=0
    for r in rows:
        bid=r['source_id']
        if bid in existing:continue
        target=namekey(r.get('boxer_b'))
        candidates=[x for x in alias_index.get(target,set()) if x!=r['url']]
        if not candidates:continue
        own=aliases[r['url']]
        passed=[]
        for cand in candidates:
            ca=aliases[cand]
            ok=False
            for x in by_date.get(r['date'],[]):
                checked+=1
                oa=namekey(x.get('fighter_a'));ob=namekey(x.get('fighter_b'))
                pair_ok=(oa in own and ob in ca) or (ob in own and oa in ca)
                if not pair_ok:continue
                ow=x.get('winner_side')
                if r.get('winner')=='DRAW':
                    result_ok=(ow=='DRAW')
                elif r.get('winner')=='BOXER A':
                    wk=namekey(x.get('fighter_a') if ow=='A' else x.get('fighter_b')) if ow in {'A','B'} else ''
                    result_ok=wk in own
                elif r.get('winner')=='BOXER B':
                    wk=namekey(x.get('fighter_a') if ow=='A' else x.get('fighter_b')) if ow in {'A','B'} else ''
                    result_ok=wk in ca
                else:
                    result_ok=False
                if result_ok:
                    ok=True;break
            if ok:passed.append(cand)
        passed=sorted(set(passed))
        if len(passed)==1:additions[bid]=passed[0]
        elif len(passed)>1:ambiguous+=1
    return additions,{'ibf_bridge_links':len(additions),'ibf_bridge_ambiguous':ambiguous,'ibf_rows_loaded':len(ibf_rows or []),'ibf_rows_checked':checked}

def main():
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    marks=','.join('?'*len(ALLOWED_SOURCES))
    rows=[dict(r) for r in con.execute(f"select * from bouts where source in ({marks}) and status='FINISHED' and date is not null order by date,source_id",ALLOWED_SOURCES)]
    names={r['source_id']:r['name'] for r in con.execute(f"select source_id,name from fighters where source in ({marks})",ALLOWED_SOURCES)}
    existing={r['bout_id']:r['opponent_id'] for r in con.execute("select bout_id,opponent_id from opponent_links where evidence='reciprocal_result_confirmed'")}

    # Secondary-source rows can carry a direct hyperlink to the opponent's exact
    # profile page. If that URL is itself a verified fighter identity in our DB,
    # it is stronger evidence than any name match and does not require fuzzy logic.
    known_ids=set(names)
    direct={}
    for r in rows:
        if r['source_id'] in existing:continue
        try:data=json.loads(r.get('data') or '{}')
        except Exception:continue
        target=str(data.get('opponent_source_url') or '').strip().rstrip('/')+'/'
        if target and target!='/' and target in known_ids and target!=r['url']:
            direct[r['source_id']]=target

    seed={**existing,**direct}
    additions,diag=discover(rows,names,seed)

    ibf_file=ROOT.parent/'official_bouts'/'ibf_bouts.json'
    ibf_rows=[]
    if ibf_file.exists():
        try:ibf_rows=json.loads(ibf_file.read_text())
        except Exception:ibf_rows=[]
    bridge_seed={**seed,**additions}
    ibf_additions,ibf_diag=ibf_bridge(rows,names,bridge_seed,ibf_rows)

    con.execute('''create table if not exists opponent_links_v2(
        bout_id text primary key, opponent_id text not null, evidence text not null,
        built_at text not null)''')
    con.execute('delete from opponent_links_v2')
    stamp=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    con.executemany('insert into opponent_links_v2 values(?,?,?,?)',[(bid,opp,'direct_source_profile_url',stamp) for bid,opp in direct.items()])
    con.executemany('insert into opponent_links_v2 values(?,?,?,?)',[(bid,opp,'reciprocal_result_observed_alias',stamp) for bid,opp in additions.items()])
    con.executemany('insert into opponent_links_v2 values(?,?,?,?)',[(bid,opp,'ibf_official_exact_date_pair_result',stamp) for bid,opp in ibf_additions.items()])
    con.commit()
    combined=len(existing)+len(direct)+len(additions)+len(ibf_additions)
    report={'existing_verified_links':len(existing),'new_direct_source_url_links':len(direct),'new_verified_alias_links':len(additions),
            'new_ibf_official_bridge_links':len(ibf_additions),'combined_links':combined,
            'sources':list(ALLOWED_SOURCES),'diagnostics':{**diag,**ibf_diag},
            'method':'direct exact opponent profile URL; else exact observed alias + same-date reciprocal row + reciprocal outcome; else existing unique career candidate + exact official IBF date/pair/result confirmation',
            'limitations':['Does not guess unresolved identities.','Does not use fuzzy edit distance.','IBF evidence never creates a standalone career identity.','Cannot recover opponents whose career/profile identity is absent from the database.']}
    (ROOT/'IDENTITY_LINKS_V2.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
