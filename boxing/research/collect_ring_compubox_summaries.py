#!/usr/bin/env python3
"""Collect conservative CompuBox-attributed summaries from The Ring.

This is NOT the full round-chart tier. It retains only explicit numeric
fight-level/segment statistics from pages whose exact date and participant pair
resolve against the local verified boxing history. Availability begins no
earlier than the day after the bout, and never before the article date.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
SEEDS=ROOT/'research'/'ring_compubox_seed_urls.json'
PAIR_SUPPLEMENTS=ROOT/'research'/'ring_compubox_pair_supplements.json'
DB=ROOT/'research'/'boxing.sqlite3'
OUT=ROOT/'punch_supplements'/'ring_compubox_summaries.jsonl'
REPORT=ROOT/'punch_supplements'/'ring_compubox_summary_report.json'
UA='Mozilla/5.0 AppwizaRingCompuBox/1.0'

def clean(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()
    return re.sub(r'\s+',' ',x.replace('’',"'")).strip()

def nk(s):return re.sub(r'[^a-z0-9]+','',clean(s).casefold())

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(3_500_001)
        if len(raw)>3_500_000:raise ValueError('page too large')
        return r.geturl(),raw

def page_text(raw):
    soup=BeautifulSoup(raw,'lxml')
    for x in soup(['script','style','noscript']):x.decompose()
    return clean(' '.join(soup.stripped_strings)),soup

def article_date(soup):
    for tag in soup.find_all('meta'):
        k=(tag.get('property') or tag.get('name') or '').casefold()
        if k in {'article:published_time','datepublished','date','publishdate'}:
            v=str(tag.get('content') or '')
            m=re.search(r'(\d{4}-\d{2}-\d{2})',v)
            if m:return m.group(1)
    text=clean(' '.join(soup.stripped_strings[:250]))
    for fmt,pat in [('%b %d, %Y',r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}\b')]:
        m=re.search(pat,text,re.I)
        if m:
            try:return dt.datetime.strptime(m.group(0).title(),fmt).date().isoformat()
            except Exception:pass
    return None

def supplement_pair_ok(date,names,ring_url):
    if not PAIR_SUPPLEMENTS.exists():return False,'missing_pair_supplements'
    try:obj=json.loads(PAIR_SUPPLEMENTS.read_text())
    except Exception:return False,'invalid_pair_supplements'
    target=sorted(nk(x) for x in names)
    for row in obj.get('pairs') or []:
        evidence=[x for x in (row.get('evidence') or []) if str(x.get('url') or '').startswith('http')]
        if (
          str(row.get('bout_date') or '')==date
          and str(row.get('ring_url') or '')==ring_url
          and sorted(nk(x) for x in (row.get('fighters') or []))==target
          and evidence
        ):
            return True,'exact_ring_url_date_pair_plus_independent_result'
    return False,'pair_not_in_verified_supplements'

def db_pair_ok(date,names):
    if not DB.exists():return False,'missing_db'
    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);d.row_factory=sqlite3.Row
    target=sorted(nk(x) for x in names)
    try:
        tabs={r[0] for r in d.execute("select name from sqlite_master where type='table'")}
        table='pre_bout_features' if 'pre_bout_features' in tabs else ('bouts' if 'bouts' in tabs else None)
        if not table:return False,'missing_pair_table'
        cols={r[1] for r in d.execute(f'pragma table_info({table})')}
        dc=next((x for x in ('bout_date','date','event_date') if x in cols),None)
        if not dc:return False,'missing_date_column'

        # Direct name columns, if present.
        fc=next((x for x in ('fighter_name','fighter','name') if x in cols),None)
        oc=next((x for x in ('opponent_name','opponent') if x in cols),None)
        if fc and oc:
            for r in d.execute(f"select {fc} as fighter,{oc} as opponent from {table} where {dc}=?",(date,)):
                if sorted([nk(r['fighter']),nk(r['opponent'])])==target:
                    return True,'exact_'+table+'_name_pair'

        # ID columns linked through normalized_fighters.
        fic=next((x for x in ('fighter_id','fighter_source_id') if x in cols),None)
        oic=next((x for x in ('opponent_id','opponent_source_id') if x in cols),None)
        if fic and oic and 'normalized_fighters' in tabs:
            ncols={r[1] for r in d.execute('pragma table_info(normalized_fighters)')}
            nid=next((x for x in ('source_id','id','fighter_id','url') if x in ncols),None)
            nname=next((x for x in ('name','fighter_name') if x in ncols),None)
            if nid and nname:
                ids=set()
                dated=list(d.execute(f"select {fic} as fighter_id,{oic} as opponent_id from {table} where {dc}=?",(date,)))
                for r in dated:
                    if r['fighter_id'] is not None:ids.add(str(r['fighter_id']))
                    if r['opponent_id'] is not None:ids.add(str(r['opponent_id']))
                idmap={}
                if ids:
                    marks=','.join('?' for _ in ids)
                    for r in d.execute(f"select {nid} as ident,{nname} as name from normalized_fighters where cast({nid} as text) in ({marks})",tuple(ids)):
                        idmap[str(r['ident'])]=r['name']
                for r in dated:
                    a=idmap.get(str(r['fighter_id']));b=idmap.get(str(r['opponent_id']))
                    if a and b and sorted([nk(a),nk(b)])==target:
                        return True,'exact_'+table+'_id_pair_via_normalized_fighters'

        # Conservative JSON fallback: both full normalized seed names must
        # appear in the same dated row's JSON payload. This never supplies
        # punch values and is used only for identity verification.
        jcols=[x for x in ('pre_fight_json','outcome_json','data','features_json') if x in cols]
        if jcols:
            q=f"select {','.join(jcols)} from {table} where {dc}=?"
            for r in d.execute(q,(date,)):
                blob=' '.join(str(r[c] or '') for c in jcols)
                nblob=nk(blob)
                if all(x and x in nblob for x in target):
                    return True,'exact_'+table+'_json_pair'
        return False,'pair_not_found_or_unresolved_ids'
    finally:
        d.close()

def segments(text,name,other):
    aliases=[clean(name),clean(name).split()[-1]]
    other_aliases=[clean(other),clean(other).split()[-1]]
    out=[]
    for sent in re.split(r'(?<=[.!?])\s+',text):
        if not any(re.search(r'(?<![A-Za-z0-9])'+re.escape(a)+r'(?![A-Za-z0-9])',sent,re.I) for a in aliases):continue
        # Keep the whole sentence; patterns below explicitly anchor the subject.
        out.append(sent)
    return out

def subject_segments(sentence,name,other):
    """Return clauses beginning at an exact fighter mention and ending before
    the next exact opponent mention. This prevents a fighter's parser from
    consuming numeric stats that belong to the opponent later in the sentence.
    """
    own=sorted({clean(name),clean(name).split()[-1]},key=len,reverse=True)
    opp=sorted({clean(other),clean(other).split()[-1]},key=len,reverse=True)
    own_spans=[]
    for alias in own:
        for m in re.finditer(r'(?<![A-Za-z0-9])'+re.escape(alias)+r'(?![A-Za-z0-9])',sentence,re.I):
            own_spans.append((m.start(),m.end()))
    opp_spans=[]
    for alias in opp:
        for m in re.finditer(r'(?<![A-Za-z0-9])'+re.escape(alias)+r'(?![A-Za-z0-9])',sentence,re.I):
            opp_spans.append((m.start(),m.end()))
    chunks=[]
    for start,end in sorted(set(own_spans)):
        stop=min((a for a,b in opp_spans if a>=end),default=len(sentence))
        # Contrast clauses can switch the grammatical subject without naming
        # it again. Do not let generic fighter-attributed patterns cross them.
        # A separately tested pair grammar may still resolve the construction.
        for cm in re.finditer(r'\b(?:while|whereas)\b',sentence[end:],re.I):
            boundary=end+cm.start()
            if boundary<stop:
                stop=boundary
                break
        chunk=sentence[start:stop].strip()
        if chunk and chunk not in chunks:chunks.append(chunk)
    return chunks

def parse_pair(text,a,b):
    out={a:{},b:{}}
    conflicts={a:[],b:[]}
    def setv(f,k,v):
        v=float(v);old=out[f].get(k)
        if old is not None and abs(float(old)-v)>1e-9:conflicts[f].append((k,old,v))
        else:out[f][k]=v
    # Paired construction where the second clause inherits the category:
    # "Chamberlain went 287 of 952 (30%) in total punches while Rafferty went 202 of 761 (27%)."
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+r'(?![A-Za-z0-9])\s+went\s+'
                    r'(\d{1,4})\s+of\s+(\d{1,4})\s*\(?(\d+(?:\.\d+)?)%\)?\s+in\s+total\s+punches\s+'
                    r'while\s+'+re.escape(oa)+r'\s+went\s+(\d{1,4})\s+of\s+(\d{1,4})'
                    r'(?:\s*\(?(\d+(?:\.\d+)?)%\)?)?',
                    text,re.I)
                if m:
                    setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2));setv(f,'total_accuracy_pct',m.group(3))
                    setv(o,'total_landed',m.group(4));setv(o,'total_thrown',m.group(5))
                    if m.group(6):setv(o,'total_accuracy_pct',m.group(6))

    # Exact pair grammar where the first fighter is named, the opponent has
    # an explicit landed count, and a trailing "while" clause returns to the
    # first fighter. Example: Itauma ... Whyte landed just two ... while ...
    # connected on 19-of-34 punches (55.9%).
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+r'(?![A-Za-z0-9])'
                    r'[^.!?]{0,220}?'+re.escape(oa)+
                    r'\s+landed\s+just\s+(two|2)\b[^.!?]{0,140}?\bwhile\b'
                    r'[^.!?]{0,120}?connected(?:\s+on)?\s+'
                    r'(\d{1,4})\s*[-–]\s*of\s*[-–]\s*(\d{1,4})\s+'
                    r'(?:total\s+)?punches\s*\((\d+(?:\.\d+)?)%\)',
                    text,re.I)
                if m:
                    setv(o,'total_landed',2)
                    setv(f,'total_landed',m.group(2));setv(f,'total_thrown',m.group(3))
                    setv(f,'total_accuracy_pct',m.group(4))

    # Pair construction where the second clause inherits "total punches":
    # "Resendiz landed 186 of 600 total punches while Plant went 108 of 509."
    # Also accepts "X landed 170 of 442 punches and Y landed 169 of 407."
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+r'(?![A-Za-z0-9])[^.!?]{0,70}?'
                    r'(?:landed|went|connected(?:\s+on)?)\s+(\d{1,4})\s+(?:of|[-–])\s+(\d{1,4})'
                    r'(?:\s*,\s*\d+(?:\.\d+)?%)?\s+(?:in\s+)?(?:total\s+)?punches[^.!?]{0,40}?\b(?:while|and)\s+'+
                    re.escape(oa)+r'(?![A-Za-z0-9])\s+(?:landed|went|connected(?:\s+on)?)\s+'
                    r'(\d{1,4})\s+(?:of|[-–])\s+(\d{1,4})(?:\s*,\s*\d+(?:\.\d+)?%)?(?:\s+(?:in\s+)?(?:total\s+)?punches)?',
                    text,re.I)
                if m:
                    setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2))
                    setv(o,'total_landed',m.group(3));setv(o,'total_thrown',m.group(4))

    # Exact pair "connect advantage" construction, e.g. 117-115 in power punches.
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            m=re.search(
                r'(?<![A-Za-z0-9])'+re.escape(fa)+
                r'(?![A-Za-z0-9])[^.!?]{0,100}?(?:held|hold|had)[^.!?]{0,30}?'
                r'(\d{1,4})\s*(?:to|[-–])\s*(\d{1,4})\s+connect\s+advantage\s+in\s+'
                r'(power|jabs?)\s+(?:punches|shots)?',
                text,re.I)
            if m:
                cat='jab' if m.group(3).lower().startswith('jab') else 'power'
                setv(f,cat+'_landed',m.group(1));setv(o,cat+'_landed',m.group(2))

    # Catterall-Eubank style exact completed-fight totals:
    # "... Catterall ... punches (51) than Eubank (17), ... attempts (186-85)"
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+
                    r'(?![A-Za-z0-9])[^.!?]{0,120}?punches\s*\((\d{1,4})\)\s+than\s+'+
                    re.escape(oa)+r'\s*\((\d{1,4})\)[^.!?]{0,100}?attempts\s*'
                    r'\((\d{1,4})\s*[-–]\s*(\d{1,4})\)',
                    text,re.I)
                if m:
                    setv(f,'total_landed',m.group(1));setv(o,'total_landed',m.group(2))
                    setv(f,'total_thrown',m.group(3));setv(o,'total_thrown',m.group(4))

    # Inoue-Picasso style explicit category edges and per-round landed values.
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+
                    r'(?![A-Za-z0-9])[^.!?]{0,180}?(\d{1,4})\s*[-–]\s*(\d{1,4})\s+edge\s+in\s+jabs\s+landed'
                    r'[^.!?]{0,100}?power\s+shots\s*\((\d{1,4})\s*[-–]\s*(\d{1,4})\)'
                    r'[^.!?]{0,100}?body\s*\((\d{1,4})\s*[-–]\s*(\d{1,4})\)',
                    text,re.I)
                if m:
                    setv(f,'jab_landed',m.group(1));setv(o,'jab_landed',m.group(2))
                    setv(f,'power_landed',m.group(3));setv(o,'power_landed',m.group(4))
                    setv(f,'body_landed',m.group(5));setv(o,'body_landed',m.group(6))
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+
                    r'(?![A-Za-z0-9])\s+averaged\s+(\d+(?:\.\d+)?)\s+punches\s+landed\s+per\s+round'
                    r'[^.!?]{0,100}?'+re.escape(oa)+
                    r'[^.!?]{0,100}?\((\d+(?:\.\d+)?)\)\s+per\s+(?:frame|round)',
                    text,re.I)
                if m:
                    setv(f,'total_landed_per_round',m.group(1));setv(o,'total_landed_per_round',m.group(2))

    # Simple exact overall landed comparison: "Roach outlanded Davis 112 to 103 in the fight."
    for f,o in ((a,b),(b,a)):
        fa=clean(f).split()[-1];oa=clean(o).split()[-1]
        m=re.search(
            re.escape(fa)+r'\s+outlanded\s+'+re.escape(oa)+
            r'\s+(\d{1,4})\s*(?:to|[-–])\s*(\d{1,4})\s+(?:overall|in\s+the\s+fight)',
            text,re.I)
        if m:
            setv(f,'total_landed',m.group(1));setv(o,'total_landed',m.group(2))

    # Ring result-style final tally:
    # "Garcia ... final tally (66-of-210 to 57-of-280)".
    # The first pair belongs to the explicitly named subject; the second to the
    # already verified opponent for this exact bout.
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            m=re.search(
                r'(?<![A-Za-z0-9])'+re.escape(fa)+
                r'(?![A-Za-z0-9])[^.!?]{0,180}?final\s+tally\s*\('
                r'(\d{1,4})\s*[-–]?\s*of\s*[-–]?\s*(\d{1,4})\s+to\s+'
                r'(\d{1,4})\s*[-–]?\s*of\s*[-–]?\s*(\d{1,4})\)',
                text,re.I)
            if m:
                setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2))
                setv(o,'total_landed',m.group(3));setv(o,'total_thrown',m.group(4))

    # "CompuBox credited Romero and Garcia for landing only 18 power punches apiece."
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'credited\s+'+re.escape(fa)+r'\s+and\s+'+re.escape(oa)+
                    r'\s+for\s+landing\s+(?:only\s+)?(\d{1,4})\s+power\s+punches\s+apiece',
                    text,re.I)
                if m:
                    setv(f,'power_landed',m.group(1));setv(o,'power_landed',m.group(1))

    # Exact per-fighter patterns. Numeric extraction is constrained to the
    # fighter's subject clause and cannot cross an exact opponent mention.
    for f,o in ((a,b),(b,a)):
        for sent in segments(text,f,o):
            for local in subject_segments(sent,f,o):
                # "X landed/went/connected on 146 of 498 total punches"
                m=re.search(
                    r"^[^.!?]{0,180}?(?:landed|went|connected(?:\s+on)?)\s+"
                    r"(\d{1,4})\s*(?:(?:of|[-–])|[-–]\s*of\s*[-–])\s*(\d{1,4})"
                    r"(?:\s*\(?(\d+(?:\.\d+)?)%\)?)?(?:\s*,)?\s*(?:in\s+)?"
                    r"(?:total\s+)?punches",
                    local,re.I)
                if m:
                    setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2))
                    if m.group(3):setv(f,'total_accuracy_pct',m.group(3))

                # "connected on 19-of-34 punches (55.9%)"
                m=re.search(
                    r"^[^.!?]{0,180}?(?:landed|went|connected(?:\s+on)?)\s+"
                    r"(\d{1,4})\s*[-–]\s*of\s*[-–]\s*(\d{1,4})\s+"
                    r"(?:total\s+)?punches\s*\((\d+(?:\.\d+)?)%\)",
                    local,re.I)
                if m:
                    setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2))
                    setv(f,'total_accuracy_pct',m.group(3))

                # Parenthetical or bare "X (249 of 567)" when clause says punches/connects.
                m=re.search(r"^\S+(?:\s+\S+){0,4}?\s*\(?(\d{1,4})\s+(?:of|[-–])\s+(\d{1,4})\)?",local,re.I)
                if m and re.search(r'\b(?:punch|connect)',local,re.I):
                    setv(f,'total_landed',m.group(1));setv(f,'total_thrown',m.group(2))

                # category exact: "118 of 233, 51% in jabs"
                for cat,label in [('jab',r'jabs?'),('power',r'power\s+(?:punches|shots)')]:
                    m=re.search(
                        r'(\d{1,4})\s+(?:of|[-–])\s+(\d{1,4})'
                        r'(?:\s*,\s*(\d+(?:\.\d+)?)%)?\s+(?:in|on|of)?\s*'+label,
                        local,re.I)
                    if m:
                        setv(f,cat+'_landed',m.group(1));setv(f,cat+'_thrown',m.group(2))
                        if m.group(3):setv(f,cat+'_accuracy_pct',m.group(3))

                # "connected on 46% of his power punches"
                m=re.search(
                    r"^[^.!?]{0,140}?(?:connected|landed)[^.!?]{0,30}?"
                    r"(\d+(?:\.\d+)?)%\s+of\s+(?:his|her)\s+power\s+(?:punches|shots)",
                    local,re.I)
                if m:setv(f,'power_accuracy_pct',m.group(1))

                # "Bivol landed 170 punches"
                m=re.search(
                    r"^[^.!?]{0,80}?landed\s+(\d{1,4})\s+(?:total\s+)?punches\b",
                    local,re.I)
                if m:setv(f,'total_landed',m.group(1))

            # Opponent-tail comparison deliberately uses the full sentence but
            # requires the opponent's exact name after the numeric pair.
            for oa in sorted({clean(o),clean(o).split()[-1]},key=len,reverse=True):
                m=re.search(
                    r'compared\s+to\s+(\d{1,4})\s+of\s+(\d{1,4})'
                    r'(?:\s*\(?(\d+(?:\.\d+)?)%\)?)?[^.!?]{0,80}?'
                    r'(?:for|by)\s+'+re.escape(oa)+r'(?![A-Za-z0-9])',
                    sent,re.I)
                if m:
                    setv(o,'total_landed',m.group(1));setv(o,'total_thrown',m.group(2))
                    if m.group(3):setv(o,'total_accuracy_pct',m.group(3))

    # Exact named-subject overall comparison:
    # "Ball ... landed at a slightly higher clip overall (240-220) over 12 rounds"
    for f,o in ((a,b),(b,a)):
        for fa in sorted({clean(f),clean(f).split()[-1]},key=len,reverse=True):
            m=re.search(
                r'(?<![A-Za-z0-9])'+re.escape(fa)+
                r'(?![A-Za-z0-9])[^.!?]{0,150}?landed[^.!?]{0,80}?overall\s*'
                r'\((\d{1,4})\s*[-–]\s*(\d{1,4})\)',
                text,re.I)
            if m:
                setv(f,'total_landed',m.group(1));setv(o,'total_landed',m.group(2))

    # Pair comparisons: "Hrgovic outlanded Adeleye 228-92 on total punches ... power shots (169-47)"
    for f,o in ((a,b),(b,a)):
        fa=clean(f).split()[-1];oa=clean(o).split()[-1]
        for sent in re.split(r'(?<=[.!?])\s+',text):
            m=re.search(re.escape(fa)+r'\s+outlanded\s+'+re.escape(oa)+r'\s+(\d{1,4})\s*[-–]\s*(\d{1,4})\s+(?:on|in)\s+total\s+punches',sent,re.I)
            if m:
                setv(f,'total_landed',m.group(1));setv(o,'total_landed',m.group(2))
                pwr=re.search(r'power\s+(?:shots|punches)[^()]{0,30}\((\d{1,4})\s*[-–]\s*(\d{1,4})\)',sent,re.I)
                if pwr:setv(f,'power_landed',pwr.group(1));setv(o,'power_landed',pwr.group(2))
    for f in (a,b):
        if conflicts[f]:out[f]={'_invalid_conflict':True,'details':conflicts[f]}
    return out

def main():
    seeds=json.loads(SEEDS.read_text()).get('pages') or []
    rows=[];diag=[]
    for seed in seeds:
        date=seed['bout_date'];a,b=seed['fighters'];rounds=int(seed.get('rounds') or 0)
        ok,res=db_pair_ok(date,[a,b])
        if not ok:
            ok,res=supplement_pair_ok(date,[a,b],seed['url'])
        item={'url':seed['url'],'bout_date':date,'fighters':[a,b],'pair_resolution':res}
        if not ok:item['status']='rejected_identity';diag.append(item);continue
        try:
            final,raw=fetch(seed['url']);text,soup=page_text(raw)
            pub=article_date(soup)
            if 'compubox' not in text.casefold():raise ValueError('CompuBox attribution missing')
            parsed=parse_pair(text,a,b)
            added=0
            nextday=(dt.date.fromisoformat(date)+dt.timedelta(days=1))
            avail=max(nextday,dt.date.fromisoformat(pub)) if pub else nextday
            for f,o in ((a,b),(b,a)):
                vals={k:v for k,v in (parsed.get(f) or {}).items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
                if not vals:continue
                rows.append({'source_url':final,'article_date':pub,'bout_date':date,'available_from_date':avail.isoformat(),
                             'fighter':f,'opponent':o,'rounds_observed':rounds,**vals,
                             'quality':'ring_published_compubox_explicit_numeric_summary_exact_verified_bout',
                             'source_tier':'modern_publisher_compubox_summary_separate_from_full_round_reports'})
                added+=1
            item.update({'status':'accepted' if added else 'no_safe_numeric_pattern','article_date':pub,'fighter_rows':added})
        except Exception as e:item.update({'status':'error','error':type(e).__name__+': '+str(e)[:220]})
        diag.append(item)
    # Deduplicate exact fight/fighter; conflicts are quarantined.
    grouped={}
    conflicts=[]
    for r in rows:
        key=(r['bout_date'],nk(r['fighter']),nk(r['opponent']))
        if key not in grouped:grouped[key]=r;continue
        base=grouped[key]
        bad=[]
        for k,v in r.items():
            if isinstance(v,(int,float)) and not isinstance(v,bool) and k in base and float(base[k])!=float(v):bad.append((k,base[k],v))
            elif isinstance(v,(int,float)) and not isinstance(v,bool):base[k]=v
        if bad:conflicts.append({'key':key,'fields':bad})
    badkeys={tuple(x['key']) for x in conflicts}
    merged=[r for k,r in grouped.items() if tuple(k) not in badkeys]
    merged.sort(key=lambda r:(r['bout_date'],nk(r['fighter'])))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in merged),encoding='utf-8')
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'seed_pages':len(seeds),
            'accepted_pages':sum(x.get('status')=='accepted' for x in diag),'fighter_rows':len(merged),
            'distinct_bouts':len({r['bout_date']+'|'+ '|'.join(sorted([nk(r['fighter']),nk(r['opponent'])])) for r in merged}),
            'date_min':min((r['bout_date'] for r in merged),default=None),'date_max':max((r['bout_date'] for r in merged),default=None),
            'conflicts_quarantined':len(conflicts),'diagnostics':diag,'conflicts':conflicts,
            'policy':'The Ring pages explicitly attributing statistics to CompuBox; exact DB date/pair; explicit numeric text only; next-day-or-later availability; separate from full round-chart tier.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('seed_pages','accepted_pages','fighter_rows','distinct_bouts','date_min','date_max','conflicts_quarantined')},indent=2))

if __name__=='__main__':main()
