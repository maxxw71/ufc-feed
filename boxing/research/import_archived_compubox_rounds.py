#!/usr/bin/env python3
"""Strictly import full archived CompuBox round tables from captured evidence.

Acceptance gates:
- archived CompuBox-owned page captured in source_rows;
- fight identity/date resolved by either an explicit full date or a uniquely
  matching verified local bout from month/day or title+method+round;
- total, jab and power rows for both fighters for every observed round;
- landed/thrown validity and total == jab + power for every round.

No ambiguous identity/date match is accepted.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,unicodedata

DB='/home/anestishkurti92/boxing-research/boxing.sqlite3'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def surname(s):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(s or ''))
    while toks and toks[-1].lower().rstrip('.') in {'jr','sr','ii','iii','iv'}:
        toks.pop()
    return norm(toks[-1]) if toks else ''

def flatten(payload):
    vals=[]
    for t in payload.get('tables') or []:
        for row in t or []:
            for cell in row or []:
                if cell:
                    vals.append(str(cell))
    vals.extend(str(x) for x in (payload.get('relevant_paragraphs') or []) if x)
    return re.sub(r'\s+',' ',' '.join(vals)).strip()

HEADER_FULL=re.compile(
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,100}?)\s+"
    r"(W|L|D|DRAW|UD|SD|MD|KO|TKO|RTD|TD)\s+(\d{1,2})\s+"
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,100}?)\s+"
    r"(\d{1,2}/\d{1,2}/\d{2,4})",re.I)

HEADER_MD=re.compile(
    r"(\d{1,2}/\d{1,2})\s*-\s*.{0,100}?"
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,70}?)\s+"
    r"(W|L|D|DRAW|UD|SD|MD|KO|TKO|RTD|TD)\s+(\d{1,2})\s+"
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,70}?)(?=\s+(?:Total Punches|CompuBox|Final|$))",re.I)

TITLE_FIGHT=re.compile(
    r"(?:Stats:\s*)?([A-Za-zÀ-ÿ0-9 .,'’\-]{2,60}?)\s+"
    r"(W|L|D|DRAW|UD|SD|MD|KO|TKO|RTD|TD)\s+(\d{1,2})\s+"
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,60})$",re.I)

def parse_date(s):
    for fmt in ('%m/%d/%Y','%m/%d/%y'):
        try:
            return dt.datetime.strptime(s,fmt).date().isoformat()
        except ValueError:
            pass
    return None

def round_terminal(value):
    m=re.search(r'\d{1,2}',str(value or ''))
    return int(m.group()) if m else None

def method_match(title_method,db_method):
    t=str(title_method or '').upper()
    d=str(db_method or '').upper()
    if t=='W':
        return True
    if t in {'D','DRAW'}:
        return d in {'D','DRAW','PTS','TD'}
    if t=='KO':
        return d in {'KO','TKO'}
    return t==d

def pair_candidates(db,sa,sb):
    target=sorted([sa,sb])
    rows=[]
    for r in db.execute("select date,boxer_a,boxer_b,source,source_id,method,rounds from bouts where status='FINISHED'"):
        if sorted([surname(r['boxer_a']),surname(r['boxer_b'])])==target:
            rows.append(r)
    return rows

def unique_from_rows(rows):
    groups={}
    for r in rows:
        key=(r['date'],tuple(sorted([norm(r['boxer_a']),norm(r['boxer_b'])])))
        groups.setdefault(key,[]).append(r)
    if len(groups)!=1:
        return None
    rs=next(iter(groups.values()))
    r=rs[0]
    return (r['date'],r['boxer_a'],r['boxer_b'],[(x['source'],x['source_id']) for x in rs])

def canonical_pair(db,date,sa,sb):
    rows=db.execute(
        "select date,boxer_a,boxer_b,source,source_id,method,rounds from bouts where date=? and status='FINISHED'",
        (date,)
    ).fetchall()
    return unique_from_rows([r for r in rows if sorted([surname(r['boxer_a']),surname(r['boxer_b'])])==sorted([sa,sb])])

def resolve_fight(db,text,payload):
    full=[]
    for m in HEADER_FULL.finditer(text):
        date=parse_date(m.group(5))
        if not date:
            continue
        a,b=m.group(1).strip(),m.group(4).strip()
        rounds=int(m.group(3))
        sa,sb=surname(a),surname(b)
        if sa and sb and sa!=sb and 1<=rounds<=15:
            full.append((date,rounds,sa,sb,m.group(0),m.group(2).upper()))
    if full:
        date,rounds,sa,sb,header,method=full[-1]
        pair=canonical_pair(db,date,sa,sb)
        if pair:
            return (*pair,rounds,header,'explicit_full_date_header')

    m=HEADER_MD.search(text)
    if m:
        mm,dd=map(int,m.group(1).split('/'))
        sa,sb=surname(m.group(2)),surname(m.group(5))
        method=m.group(3).upper()
        rounds=int(m.group(4))
        rows=[
            r for r in pair_candidates(db,sa,sb)
            if int(r['date'][5:7])==mm
            and int(r['date'][8:10])==dd
            and round_terminal(r['rounds'])==rounds
            and method_match(method,r['method'])
        ]
        pair=unique_from_rows(rows)
        if pair:
            return (*pair,rounds,m.group(0),'month_day_plus_unique_verified_bout')

    title=str(payload.get('title') or '').strip()
    m=TITLE_FIGHT.search(title)
    if m:
        sa,sb=surname(m.group(1)),surname(m.group(4))
        method=m.group(2).upper()
        rounds=int(m.group(3))
        rows=[
            r for r in pair_candidates(db,sa,sb)
            if round_terminal(r['rounds'])==rounds
            and method_match(method,r['method'])
        ]
        pair=unique_from_rows(rows)
        if pair:
            return (*pair,rounds,title,'title_method_round_plus_unique_verified_bout')
    return None

def section(text,kind):
    heads={
      'total':[r'Total Punches Landed\s*/\s*Thrown',r'Total Punches Landed/Thrown'],
      'jab':[r'(?:Total )?Jabs Landed\s*/\s*Thrown',r'Total Jabs Thrown/Landed',r'Jabs Landed / Thrown'],
      'power':[r'(?:Total )?Power Punches Landed\s*/\s*Thrown',r'Power Punches Landed / Thrown'],
    }
    starts=[]
    for pat in heads[kind]:
        m=re.search(pat,text,re.I)
        if m:
            starts.append(m)
    if not starts:
        return None
    m=min(starts,key=lambda x:x.start())
    stop=len(text)
    boundary=[
      r'(?:Total )?Jabs Landed\s*/\s*Thrown',r'Total Jabs Thrown/Landed',
      r'(?:Total )?Power Punches Landed\s*/\s*Thrown',
      r'Final Punch(?:Stat)? Report',r'Final Punch Stats'
    ]
    for pat in boundary:
        z=re.search(pat,text[m.end():],re.I)
        if z and m.end()+z.start()>m.end()+10:
            stop=min(stop,m.end()+z.start())
    return text[m.end():stop]

def pairs_for(section_text,last,rounds):
    if not section_text:
        return None
    pat=re.compile(
        r'\b'+re.escape(last)+r'\b\s+((?:\d{1,3}\s*/\s*\d{1,3}\s+){'
        +str(rounds-1)+r'}\d{1,3}\s*/\s*\d{1,3})',re.I
    )
    m=pat.search(section_text)
    if not m:
        return None
    vals=[tuple(map(int,re.split(r'\s*/\s*',x))) for x in re.findall(r'\d{1,3}\s*/\s*\d{1,3}',m.group(1))]
    if len(vals)!=rounds or any(l<0 or t<l for l,t in vals):
        return None
    return vals

def parse_candidate(db,url,payload):
    low=url.lower()
    if 'web.archive.org' not in low or 'compuboxonline.com' not in low:
        return None,'not archived compubox'
    text=flatten(payload)
    resolved=resolve_fight(db,text,payload)
    if not resolved:
        return None,'no safely resolved fight identity/date'
    date,a,b,sources,rounds,header,resolution=resolved
    bysurname={surname(a):a,surname(b):b}
    cats={}
    for kind in ('total','jab','power'):
        s=section(text,kind)
        if not s:
            return None,f'missing {kind} section'
        cats[kind]={}
        for last,full in bysurname.items():
            vals=pairs_for(s,last,rounds)
            if not vals:
                return None,f'missing {kind} round row for {full}'
            cats[kind][full]=vals
    for full in (a,b):
        for i in range(rounds):
            tl,tt=cats['total'][full][i]
            jl,jt=cats['jab'][full][i]
            pl,pt=cats['power'][full][i]
            if (jl+pl,jt+pt)!=(tl,tt):
                return None,f'category arithmetic mismatch {full} round {i+1}'
    return {
        'url':url,'date':date,'rounds':rounds,'fighters':[a,b],
        'cats':cats,'header':header,'bout_sources':sources,
        'date_identity_resolution':resolution
    },None

def ensure_tables(d):
    tabs={r[0] for r in d.execute("select name from sqlite_master where type='table'")}
    if not {'punch_reports','round_punches','fight_punch_totals'}<=tabs:
        raise RuntimeError('normalized punch tables missing')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    d=sqlite3.connect(DB,timeout=120)
    d.row_factory=sqlite3.Row
    ensure_tables(d)
    rows=d.execute(
        "select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id"
    ).fetchall()
    accepted=[]
    rejected=[]
    for row in rows:
        url=row['source_id']
        try:
            payload=json.loads(row['data'])
        except Exception:
            rejected.append({'url':url,'reason':'invalid payload'})
            continue
        parsed,err=parse_candidate(d,url,payload)
        if not parsed:
            rejected.append({'url':url,'reason':err})
            continue
        accepted.append(parsed)
        if args.apply:
            title=f"{parsed['fighters'][0]} vs {parsed['fighters'][1]} archived CompuBox"
            d.execute(
                "insert or replace into punch_reports(url,bout_date,title,status) values(?,?,?,?)",
                (url,parsed['date'],title,'parsed')
            )
            d.execute("delete from round_punches where report_url=?",(url,))
            d.execute("delete from fight_punch_totals where report_url=?",(url,))
            for kind in ('total','jab','power'):
                for full,vals in parsed['cats'][kind].items():
                    for rnd,(landed,thrown) in enumerate(vals,1):
                        d.execute(
                            "insert into round_punches(report_url,fighter_label,round,category,landed,thrown) values(?,?,?,?,?,?)",
                            (url,full,rnd,kind,landed,thrown)
                        )
                    d.execute(
                        "insert into fight_punch_totals(report_url,fighter_label,category,landed,body_landed,thrown) values(?,?,?,?,?,?)",
                        (url,full,kind,sum(x[0] for x in vals),None,sum(x[1] for x in vals))
                    )
    if args.apply:
        d.commit()
    report={
        'captured_pages':len(rows),
        'accepted_full_round_reports':len(accepted),
        'accepted':[
            {'url':x['url'],'date':x['date'],'fighters':x['fighters'],
             'rounds':x['rounds'],'resolution':x['date_identity_resolution']}
            for x in accepted
        ],
        'rejection_reasons':{}
    }
    for x in rejected:
        report['rejection_reasons'][x['reason']]=report['rejection_reasons'].get(x['reason'],0)+1
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':
    main()
