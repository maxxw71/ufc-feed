#!/usr/bin/env python3
"""Discover and parse dated official WBC monthly ratings PDFs.

The collector uses a finite month/year URL pattern set only; it does not
enumerate numeric endpoints. Rankings become research-eligible conservatively
on the first day of the FOLLOWING month, preventing same-month publication
timing leakage when exact publication timestamp is unknown.
"""
from __future__ import annotations
import datetime as dt, hashlib, io, json, re, urllib.request
from pathlib import Path
from pypdf import PdfReader

OUT=Path('boxing/rankings');OUT.mkdir(parents=True,exist_ok=True)
UA='Mozilla/5.0 AppwizaBoxingRankings/1.0'
MONTHS=['JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER']
DIVS=[
 ('heavyweight',['HEAVYWEIGHT']),
 ('bridgerweight',['BRIDGERWEIGHT']),
 ('cruiserweight',['CRUISERWEIGHT']),
 ('light heavyweight',['LIGHT HEAVYWEIGHT','LT. HEAVYWEIGHT','LT HEAVYWEIGHT']),
 ('super middleweight',['SUPER MIDDLEWEIGHT','SUPER-MIDDLEWEIGHT']),
 ('middleweight',['MIDDLEWEIGHT']),
 ('super welterweight',['SUPER WELTERWEIGHT','SUPERWELTERWEIGHT','SUPER-WELTERWEIGHT']),
 ('welterweight',['WELTERWEIGHT']),
 ('super lightweight',['SUPER LIGHTWEIGHT','SUPERLIGHTWEIGHT','SUPER-LIGHTWEIGHT']),
 ('lightweight',['LIGHTWEIGHT']),
 ('super featherweight',['SUPER FEATHERWEIGHT','SUPERFEATHERWEIGHT','SUPER-FEATHERWEIGHT']),
 ('featherweight',['FEATHERWEIGHT']),
 ('super bantamweight',['SUPER BANTAMWEIGHT','SUPERBANTAMWEIGHT','SUPER-BANTAMWEIGHT']),
 ('bantamweight',['BANTAMWEIGHT']),
 ('super flyweight',['SUPER FLYWEIGHT','SUPERFLYWEIGHT','SUPER-FLYWEIGHT']),
 ('flyweight',['FLYWEIGHT']),
 ('light flyweight',['LIGHT FLYWEIGHT','LIGHTFLYWEIGHT','LIGHT-FLYWEIGHT','LT. FLYWEIGHT']),
 ('minimumweight',['MINIMUMWEIGHT','STRAWWEIGHT','MINI FLYWEIGHT']),
]
COUNTRY_TAIL=re.compile(r'\s*\([^)]{1,45}\)\s*(?:[A-Z][A-Z0-9*/. -]{0,40})?\s*$')

def next_month(y,m):
    return dt.date(y+1,1,1) if m==12 else dt.date(y,m+1,1)

def urls(y,month):
    # WBC file layout changed over time; finite documented filename variants.
    names=[
      f'WBC_RATINGS_{month}_{y}.pdf',
      f'WBC_RATINGS_{month}_%20{y}.pdf',
      f'WBC_RATINGS_{month}%20_{y}.pdf',
      f'WBC_RATINGS_{month}_%20{y}_.pdf',
    ]
    bases=[
      f'https://wbcboxing.com/mailing/{y}/ratings_pdf/',
      f'https://wbcboxing.com/mailing/{y}/',
    ]
    for b in bases:
      for n in names: yield b+n

def fetch_pdf(y,month):
    for url in urls(y,month):
      try:
        req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/pdf'})
        with urllib.request.urlopen(req,timeout=25) as r:
          data=r.read(12_000_001)
          final=r.geturl()
        if data.startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
          return final,data
      except Exception: pass
    return None,None

def clean_line(x):
    return re.sub(r'\s+',' ',str(x or '')).strip()

def div_header(line):
    u=line.upper()
    for canonical,aliases in DIVS:
      if any(re.match(r'^'+re.escape(a)+r'(?:\.|\s|-|$)',u) for a in aliases):
        return canonical
    return None

def boxer_name(text):
    s=clean_line(text)
    # Drop country/nationality parenthetical and common belt suffixes.
    s=COUNTRY_TAIL.sub('',s)
    s=re.sub(r'\s+(?:SILVER|INTL|INTERNATIONAL|NABF|OPBF|EBU|FECARBOX|FECONSUR|USWBC|YOUTH|FRANCOPHONE|AUSTRALASIA|CONT\..*)$','',s,flags=re.I)
    s=clean_line(s)
    return s if len(s)>=3 else None

def parse(text,y,m,url):
    # Preserve large whitespace runs from layout-mode extraction: the WBC PDFs
    # use visual table columns and collapsing whitespace can merge NAME/COUNTRY.
    raw_lines=[str(x).replace('\\xa0',' ').rstrip() for x in text.splitlines() if clean_line(x)]
    rows=[];champions=[];division=None;in_contenders=False;i=0
    while i<len(raw_lines):
      raw=raw_lines[i]
      line=clean_line(raw)
      dh=div_header(line)
      if dh:
        division=dh;in_contenders=False;i+=1;continue
      if division:
        cm=re.match(r'^(?:(INTERIM)\\s+)?CHAMPION\\s*:\\s*(.+)
def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          candidates=[]
          try:
            layout='\n'.join((p.extract_text(extraction_mode='layout') or '') for p in reader.pages)
            candidates.append(('layout',*parse(layout,y,m,url)))
          except (TypeError,ValueError):
            pass
          plain='\n'.join((p.extract_text() or '') for p in reader.pages)
          candidates.append(('plain',*parse(plain,y,m,url)))
          extract_mode,rows,cs=max(candidates,key=lambda x:(len(x[1]),len(x[2])))
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'extract_mode':extract_mode,'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
,line,re.I)
        if cm:
          name=boxer_name(cm.group(2))
          if name: champions.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'status':'interim_champion' if cm.group(1) else 'champion','name':name,'source_url':url})
        u=line.upper()
        if u.startswith('CONTENDERS') or re.match(r'^(?:RANK|NO\\.?)(?:\\s|$)',u):
          in_contenders=True;i+=1;continue
        if in_contenders:
          rank=None;name_text=None
          # Layout extraction normally preserves table columns with 2+ spaces.
          cols=[clean_line(x) for x in re.split(r'\\s{2,}',raw.strip()) if clean_line(x)]
          if cols:
            m0=re.match(r'^(\\d{1,2})[.)-]?\\s*(.*)
def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          text='\n'.join((p.extract_text() or '') for p in reader.pages)
          rows,cs=parse(text,y,m,url)
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
,cols[0])
            if m0 and 1<=int(m0.group(1))<=40:
              rank=int(m0.group(1))
              name_text=clean_line(m0.group(2)) or (cols[1] if len(cols)>1 else None)
          # Fallback for PDFs that flatten rank and name onto one line.
          if rank is None:
            rm=re.match(r'^(\\d{1,2})[.)-]?\\s+(.+)
def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          text='\n'.join((p.extract_text() or '') for p in reader.pages)
          rows,cs=parse(text,y,m,url)
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
,line)
            if rm and 1<=int(rm.group(1))<=40:
              rank=int(rm.group(1));name_text=rm.group(2)
          # Fallback for a standalone rank followed by the boxer name.
          if rank is None and re.fullmatch(r'\\d{1,2}',line) and i+1<len(raw_lines):
            rank=int(line)
            if 1<=rank<=40:
              nxt=raw_lines[i+1]
              ncols=[clean_line(x) for x in re.split(r'\\s{2,}',nxt.strip()) if clean_line(x)]
              name_text=ncols[0] if ncols else clean_line(nxt)
              i+=1
            else:
              rank=None
          if rank is not None and name_text:
            # On layout rows, only the first text column after rank is the name.
            # This avoids appending country/record columns to fighter identity.
            if len(cols)>1 and re.match(r'^\\d{1,2}[.)-]?(?:\\s|$)',cols[0]):
              m0=re.match(r'^\\d{1,2}[.)-]?\\s*(.*)
def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          text='\n'.join((p.extract_text() or '') for p in reader.pages)
          rows,cs=parse(text,y,m,url)
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
,cols[0])
              if not clean_line(m0.group(1) if m0 else ''):
                name_text=cols[1]
            name=boxer_name(name_text)
            if name and not re.match(r'^(?:RANK|NO\\.?|NAME|COUNTRY|RECORD)
def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          text='\n'.join((p.extract_text() or '') for p in reader.pages)
          rows,cs=parse(text,y,m,url)
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
,name,re.I):
              rows.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'rank':rank,'name':name,'source_url':url})
      i+=1
    return rows,champions

def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2024,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader=PdfReader(io.BytesIO(data))
          text='\n'.join((p.extract_text() or '') for p in reader.pages)
          rows,cs=parse(text,y,m,url)
          status='parsed' if len(rows)>=100 else 'parse_review'
          docs.append({'year':y,'month':m,'month_name':month,'status':status,'source_url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),'ranking_rows':len(rows),'champions':len(cs),'safe_effective_date':next_month(y,m).isoformat()})
          for r in rows:r['safe_effective_date']=next_month(y,m).isoformat()
          for r in cs:r['safe_effective_date']=next_month(y,m).isoformat()
          if status=='parsed':allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({'year':y,'month':m,'month_name':month,'status':'parse_error','source_url':url,'error':type(e).__name__+': '+str(e)[:200]})
    # Deduplicate exact organization/month/division/rank.
    uniq={}
    for r in allrows:uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False))
    (OUT/'wbc_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    report={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,'parsed_documents':sum(x['status']=='parsed' for x in docs),'ranking_rows':len(allrows),'champion_rows':len(champs),'years':sorted(set(r['rating_year'] for r in allrows)),'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'}
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'parsed_documents':report['parsed_documents'],'ranking_rows':len(allrows),'champion_rows':len(champs),'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}},indent=2))

if __name__=='__main__':main()
