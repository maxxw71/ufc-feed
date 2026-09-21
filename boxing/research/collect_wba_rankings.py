#!/usr/bin/env python3
"""Collect official monthly WBA rankings with conservative publication timing.

Sources:
- Official monthly WBA rating PDFs: /wba-ranking-pdf/YYYY/M
- Official WBA ranking movements archive for posting dates.

A ranking month is research-usable only when an official posting date can be
resolved. We never backdate to month-end just because the PDF says "as of".
"""
from __future__ import annotations
import datetime as dt, hashlib, io, json, re, time, urllib.request, urllib.error
from pathlib import Path
from bs4 import BeautifulSoup
import pdfplumber

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'rankings'
OUT.mkdir(parents=True,exist_ok=True)
UA='Mozilla/5.0 AppwizaBoxingWBAArchive/1.0'

DIVISIONS=[
 'heavyweight','bridgerweight','cruiserweight','light heavyweight','super middleweight',
 'middleweight','super welterweight','welterweight','super lightweight','lightweight',
 'super featherweight','featherweight','super bantamweight','bantamweight',
 'super flyweight','flyweight','light flyweight','minimumweight'
]
DIV_LOOKUP={re.sub(r'[^a-z]','',x):x for x in DIVISIONS}
DIV_LOOKUP.update({'minimum':'minimumweight','strawweight':'minimumweight','miniflyweight':'minimumweight','supercruiserweight':'bridgerweight'})
MONTHS={m.upper():i for i,m in enumerate(
 ['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
QUALIFIERS=[
 'IBERO-AMERICAN','IBERO AMERICAN','CONTINENTAL NORTH AMERICA','CONTINENTAL AMERICAS',
 'CONTINENTAL LATIN AMERICA','CONTINENTAL','INTERNATIONAL GOLD','INTERNATIONAL',
 'OCEANIA','ASIA','BALTIC','NABA','GOLD INT','C GOLD','GOLD','C/USA','C/NA','C/LA',
 'C/A','I/C','OC','INT','CON'
]

def get(url,limit=15_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        data=r.read(limit+1);final=r.geturl()
    if len(data)>limit:raise ValueError('response too large')
    return final,data

def norm_div(line):
    x=re.sub(r'[^A-Z]','',line.upper())
    return DIV_LOOKUP.get(x.lower())

def clean_name(text):
    s=re.sub(r'\s+',' ',text).strip(' .-\t')
    changed=True
    while changed:
        changed=False
        for q in sorted(QUALIFIERS,key=len,reverse=True):
            m=re.search(r'(?:\s|^)'+re.escape(q)+r'\s*$',s,re.I)
            if m:
                s=s[:m.start()].strip();changed=True;break
    return re.sub(r'\s+',' ',s).strip()

def posting_dates():
    url='https://www.wbaboxing.com/wba-ranking-movements'
    try:
        _,raw=get(url,4_000_000)
    except Exception:
        return {},None
    soup=BeautifulSoup(raw,'lxml')
    text='\n'.join(x.strip() for x in soup.stripped_strings if x.strip())
    # Capture "WBA Ratings movements as of August 2026 ... September 11th, 2026"
    pat=re.compile(
        r'WBA Ratings movements as of\s+([A-Za-z]+)\s+(\d{4}).{0,220}?'
        r'([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})',re.I|re.S)
    out={}
    for m in pat.finditer(text):
        mon=m.group(1).upper();year=int(m.group(2))
        try:posted=dt.datetime.strptime(f'{m.group(3)} {m.group(4)} {m.group(5)}','%B %d %Y').date()
        except Exception:continue
        if mon in MONTHS:out[(year,MONTHS[mon])]=posted.isoformat()
    return out,url

def fetch_pdf(year,month):
    urls=[
      f'https://www.wbaboxing.com/wba-ranking-pdf/{year}/{month}',
      f'https://www.wbaboxing.com/wba-ranking-pdf/{year}/{month:02d}',
    ]
    for url in urls:
        for attempt in range(3):
            try:
                final,data=get(url)
                if data.lstrip().startswith(b'%PDF') and len(data)>20_000:return final,data
                break
            except urllib.error.HTTPError as e:
                if e.code not in {408,425,429,500,502,503,504,520,521,522,523,524}:break
            except urllib.error.URLError:
                pass
            if attempt<2:time.sleep(1.5*(2**attempt))
    return None,None

def extract_pdf(raw):
    # WBA pages are laid out as three independent ranking columns. Extract each
    # third separately so a division header can never be associated with ranks
    # from a neighboring column.
    blocks=[]
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            w,h=page.width,page.height
            for x0,x1 in ((0,w/3),(w/3,2*w/3),(2*w/3,w)):
                txt=page.crop((x0,0,x1,h)).extract_text(x_tolerance=2,y_tolerance=2) or ''
                if txt.strip():blocks.append(txt)
    return blocks

def parse(raw,year,month):
    pages=extract_pdf(raw)
    lines=[]
    for p in pages:
        lines.extend(re.sub(r'\s+',' ',x).strip() for x in p.splitlines() if re.sub(r'\s+',' ',x).strip())
    division=None;rankings=[];champions=[]
    for line in lines:
        d=norm_div(line)
        if d:
            division=d;continue
        if not division:continue
        cm=re.match(r'^(?:WBA\s+)?(?:(SUPER|INTERIM|GOLD)\s+)?(?:WORLD\s+)?CHAMPION:\s*(.+?)\s+([A-Z]{3})$',line,re.I)
        if cm:
            champions.append({'year':year,'month':month,'division':division,
                              'title':((cm.group(1)+' ') if cm.group(1) else '')+'CHAMPION',
                              'name':clean_name(cm.group(2)),'country':cm.group(3).upper()})
            continue
        rm=re.match(r'^(\d{1,2})\.\s+(.+?)\s+([A-Z]{3})$',line)
        if rm:
            rank=int(rm.group(1))
            if 1<=rank<=15:
                name=clean_name(rm.group(2))
                if name and name.upper()!='NOT RATED':
                    rankings.append({'year':year,'month':month,'division':division,'rank':rank,
                                     'name':name,'country':rm.group(3).upper(),'raw_line':line})
    # Sanity gates: a normal monthly WBA men's sheet should cover most divisions.
    distinct=len({x['division'] for x in rankings})
    if len(rankings)<180 or distinct<14:
        raise ValueError(f'incomplete WBA parse rows={len(rankings)} divisions={distinct}')
    return rankings,champions,len(pages)

def main():
    today=dt.date.today()
    posted,posting_url=posting_dates()
    docs=[];allr=[];allc=[]
    for year in range(2021,today.year+1):
        maxm=today.month if year==today.year else 12
        for month in range(1,maxm+1):
            final,raw=fetch_pdf(year,month)
            if not raw:
                docs.append({'year':year,'month':month,'status':'not_found','safe_effective_date':posted.get((year,month))})
                continue
            sha=hashlib.sha256(raw).hexdigest()
            try:
                ranks,champs,pages=parse(raw,year,month)
                safe=posted.get((year,month))
                for x in ranks:x['source_url']=final;x['safe_effective_date']=safe
                for x in champs:x['source_url']=final;x['safe_effective_date']=safe
                allr.extend(ranks);allc.extend(champs)
                docs.append({'year':year,'month':month,'status':'parsed' if safe else 'parsed_unusable_no_post_date',
                             'source_url':final,'bytes':len(raw),'sha256':sha,'pages':pages,
                             'ranking_rows':len(ranks),'champions':len(champs),'safe_effective_date':safe})
            except Exception as e:
                docs.append({'year':year,'month':month,'status':'parse_review','source_url':final,'bytes':len(raw),
                             'sha256':sha,'error':str(e),'safe_effective_date':posted.get((year,month))})
    raw_usable=[x for x in allr if x.get('safe_effective_date')]
    raw_usablec=[x for x in allc if x.get('safe_effective_date')]

    # Multiple nominal rating months can occasionally share one official posting
    # date. Without an intraday timestamp, only the later rating month is kept for
    # that date so a bout never sees two competing snapshots as simultaneously current.
    latest_doc_by_date={}
    for x in raw_usable+raw_usablec:
        key=x['safe_effective_date'];ym=(int(x.get('year') or 0),int(x.get('month') or 0))
        if ym>latest_doc_by_date.get(key,(0,0)):latest_doc_by_date[key]=ym
    usable=[x for x in raw_usable if (int(x.get('year') or 0),int(x.get('month') or 0))==latest_doc_by_date[x['safe_effective_date']]]
    usablec=[x for x in raw_usablec if (int(x.get('year') or 0),int(x.get('month') or 0))==latest_doc_by_date[x['safe_effective_date']]]

    # Quarantine any residual conflicting numerical slot rather than choosing a
    # contender arbitrarily. Exact duplicate rows collapse to one.
    grouped={}
    for x in usable:grouped.setdefault((x['safe_effective_date'],x['division'],int(x['rank'])),[]).append(x)
    clean=[];conflicts=[]
    for key,group in grouped.items():
        names={re.sub(r'[^a-z0-9]+','',str(x.get('name') or '').lower()) for x in group}
        if len(names)>1:
            conflicts.append({'slot':key,'rows':group})
        else:
            clean.append(sorted(group,key=lambda x:(x.get('source_url') or '',x.get('name') or ''))[-1])
    usable=sorted(clean,key=lambda x:(x['safe_effective_date'],x['division'],int(x['rank'])))

    (OUT/'wba_monthly_rankings.json').write_text(json.dumps(usable,indent=2,ensure_ascii=False))
    (OUT/'wba_monthly_champions.json').write_text(json.dumps(usablec,indent=2,ensure_ascii=False))
    meta={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(d['status'].startswith('parsed') for d in docs),
          'usable_documents':sum(d['status']=='parsed' for d in docs),'ranking_rows':len(usable),
          'champion_rows':len(usablec),'posting_date_source':posting_url,
          'superseded_same_post_date_documents':len({x['safe_effective_date'] for x in raw_usable})-len(latest_doc_by_date),
          'quarantined_conflicting_rank_slots':len(conflicts),
          'conflicting_rank_slot_sample':conflicts[:30],
          'policy':'Official WBA monthly PDFs only. If multiple rating months share one official posting date, only the later rating month is retained. Conflicting rank slots are quarantined. No month-end backdating.'}
    (OUT/'wba_monthly_rankings_meta.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['parsed_documents','usable_documents','ranking_rows','champion_rows','posting_date_source']},indent=2))

if __name__=='__main__':main()
