#!/usr/bin/env python3
"""Strictly import full archived CompuBox round tables from captured evidence.

Acceptance gates:
- archived CompuBox-owned page captured in source_rows;
- explicit full bout date in the page text;
- outcome/round header exposing both fighter surnames;
- unique finished bout in the local archive on that date matching both surnames;
- total, jab and power rows for both fighters for every observed round;
- landed/thrown validity and total == jab + power for every round.

No dates, identities, rounds, categories or missing values are inferred.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3,unicodedata
from urllib.parse import urlsplit

DB='/home/anestishkurti92/boxing-research/boxing.sqlite3'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def surname(s):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(s or ''))
    while toks and toks[-1].lower().rstrip('.') in {'jr','sr','ii','iii','iv'}:toks.pop()
    return norm(toks[-1]) if toks else ''

def flatten(payload):
    vals=[]
    for t in payload.get('tables') or []:
        for row in t or []:
            for cell in row or []:
                if cell:vals.append(str(cell))
    vals.extend(str(x) for x in (payload.get('relevant_paragraphs') or []) if x)
    return re.sub(r'\s+',' ',' '.join(vals)).strip()

HEADER=re.compile(
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,100}?)\s+"
    r"(?:W|L|D|DRAW|UD|SD|MD|KO|TKO|RTD|TD)\s+(\d{1,2})\s+"
    r"([A-Za-zÀ-ÿ0-9 .,'’\-]{2,100}?)\s+"
    r"(\d{1,2}/\d{1,2}/\d{2,4})",re.I)

def parse_date(s):
    for fmt in ('%m/%d/%Y','%m/%d/%y'):
        try:return dt.datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    return None

def find_header(text):
    candidates=[]
    for m in HEADER.finditer(text):
        date=parse_date(m.group(4))
        if not date:continue
        a=m.group(1).strip();b=m.group(3).strip();rounds=int(m.group(2))
        sa,sb=surname(a),surname(b)
        if sa and sb and sa!=sb and 1<=rounds<=15:
            candidates.append((date,rounds,sa,sb,m.group(0)))
    # Strong preference for header near an explicit PunchStat phrase.
    if not candidates:return None
    return candidates[-1]

def canonical_pair(db,date,sa,sb):
    rows=db.execute("select boxer_a,boxer_b,source,source_id from bouts where date=? and status='FINISHED'",(date,)).fetchall()
    matches=[]
    target=sorted([sa,sb])
    for a,b,source,sid in rows:
        if sorted([surname(a),surname(b)])==target:
            matches.append((a,b,source,sid))
    # multiple source rows can describe same canonical names; require one name-pair.
    pairs={}
    for a,b,source,sid in matches:
        key=tuple(sorted([norm(a),norm(b)]));pairs.setdefault(key,(a,b,[]))[2].append((source,sid))
    return next(iter(pairs.values())) if len(pairs)==1 else None

def section(text,kind):
    heads={
      'total':[r'Total Punches Landed\s*/\s*Thrown',r'Total Punches Landed/Thrown'],
      'jab':[r'(?:Total )?Jabs Landed\s*/\s*Thrown',r'Total Jabs Thrown/Landed',r'Jabs Landed / Thrown'],
      'power':[r'(?:Total )?Power Punches Landed\s*/\s*Thrown',r'Power Punches Landed / Thrown'],
    }
    starts=[]
    for pat in heads[kind]:
        m=re.search(pat,text,re.I)
        if m:starts.append(m)
    if not starts:return None
    m=min(starts,key=lambda x:x.start())
    stop=len(text)
    boundary=[
      r'(?:Total )?Jabs Landed\s*/\s*Thrown',r'Total Jabs Thrown/Landed',
      r'(?:Total )?Power Punches Landed\s*/\s*Thrown',
      r'Final Punch(?:Stat)? Report',r'Final Punch Stats'
    ]
    for pat in boundary:
        z=re.search(pat,text[m.end():],re.I)
        if z and m.end()+z.start()>m.end()+10:stop=min(stop,m.end()+z.start())
    return text[m.end():stop]

def pairs_for(section_text,last,rounds):
    if not section_text:return None
    # Stat row labels are normally surnames. Require the exact surname token.
    pat=re.compile(r'\b'+re.escape(last)+r'\b\s+((?:\d{1,3}\s*/\s*\d{1,3}\s+){'+str(rounds-1)+r'}\d{1,3}\s*/\s*\d{1,3})',re.I)
    m=pat.search(section_text)
    if not m:return None
    vals=[tuple(map(int,re.split(r'\s*/\s*',x))) for x in re.findall(r'\d{1,3}\s*/\s*\d{1,3}',m.group(1))]
    if len(vals)!=rounds:return None
    if any(l<0 or t<l for l,t in vals):return None
    return vals

def parse_candidate(db,url,payload):
    if 'web.archive.org' not in url.lower() or 'compuboxonline.com' not in url.lower():return None,'not archived compubox'
    text=flatten(payload)
    h=find_header(text)
    if not h:return None,'no explicit full-date fight header'
    date,rounds,sa,sb,header=h
    pair=canonical_pair(db,date,sa,sb)
    if not pair:return None,'no unique local date+surnames bout'
    a,b,sources=pair
    # map canonical fighters to header surnames
    bysurname={surname(a):a,surname(b):b}
    cats={}
    for kind in ('total','jab','power'):
        s=section(text,kind)
        if not s:return None,f'missing {kind} section'
        cats[kind]={}
        for last,full in bysurname.items():
            vals=pairs_for(s,last,rounds)
            if not vals:return None,f'missing {kind} round row for {full}'
            cats[kind][full]=vals
    for full in (a,b):
        for i in range(rounds):
            tl,tt=cats['total'][full][i];jl,jt=cats['jab'][full][i];pl,pt=cats['power'][full][i]
            if (jl+pl,jt+pt)!=(tl,tt):
                return None,f'category arithmetic mismatch {full} round {i+1}'
    return {'url':url,'date':date,'rounds':rounds,'fighters':[a,b],'cats':cats,'header':header,'bout_sources':sources},None

def ensure_tables(d):
    tabs={r[0] for r in d.execute("select name from sqlite_master where type='table'")}
    if not {'punch_reports','round_punches','fight_punch_totals'}<=tabs:
        raise RuntimeError('normalized punch tables missing')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    d=sqlite3.connect(DB,timeout=120);d.row_factory=sqlite3.Row;ensure_tables(d)
    rows=d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id").fetchall()
    accepted=[];rejected=[]
    for row in rows:
        url=row['source_id']
        try:payload=json.loads(row['data'])
        except Exception as e:
            rejected.append({'url':url,'reason':'invalid payload'});continue
        parsed,err=parse_candidate(d,url,payload)
        if not parsed:
            rejected.append({'url':url,'reason':err});continue
        accepted.append(parsed)
        if args.apply:
            title=f"{parsed['fighters'][0]} vs {parsed['fighters'][1]} archived CompuBox"
            d.execute("insert or replace into punch_reports(url,bout_date,title,status) values(?,?,?,?)",(url,parsed['date'],title,'parsed'))
            d.execute("delete from round_punches where report_url=?",(url,))
            d.execute("delete from fight_punch_totals where report_url=?",(url,))
            for kind in ('total','jab','power'):
                for full,vals in parsed['cats'][kind].items():
                    for rnd,(landed,thrown) in enumerate(vals,1):
                        d.execute("insert into round_punches(report_url,fighter_label,round,category,landed,thrown) values(?,?,?,?,?,?)",
                                  (url,full,rnd,kind,landed,thrown))
                    d.execute("insert into fight_punch_totals(report_url,fighter_label,category,landed,body_landed,thrown) values(?,?,?,?,?,?)",
                              (url,full,kind,sum(x[0] for x in vals),None,sum(x[1] for x in vals)))
    if args.apply:d.commit()
    report={'captured_pages':len(rows),'accepted_full_round_reports':len(accepted),
            'accepted':[{'url':x['url'],'date':x['date'],'fighters':x['fighters'],'rounds':x['rounds']} for x in accepted],
            'rejection_reasons':{}}
    for x in rejected:report['rejection_reasons'][x['reason']]=report['rejection_reasons'].get(x['reason'],0)+1
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
