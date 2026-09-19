#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,unicodedata,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from pathlib import Path

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'data/raw/availability_archive';OUT.mkdir(parents=True,exist_ok=True)
JINA='https://r.jina.ai/'
BASE='https://www.mlssoccer.com/news/'
UA='Mozilla/5.0 AppwizaMLSAvailabilityArchive/1.0'

def fetch(url):
    req=urllib.request.Request(JINA+url,headers={'User-Agent':UA,'Accept':'text/plain'})
    raw=urllib.request.urlopen(req,timeout=45).read().decode('utf-8','replace')
    return raw
def parse_entries(text):
    team=None;rows=[]
    for raw in text.splitlines():
        line=raw.strip()
        h=re.match(r'^#{2,4}\s+(.+?)\s*$',line)
        if h:
            cand=h.group(1).strip()
            if cand not in {'MLS Communications','Player Availability Report'} and not re.match(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b',cand,re.I):
                team=cand
            continue
        if not team or not line or line.lower()=='none':continue
        line=re.sub(r'^[-*]\s*','',line).strip()
        m=re.match(r'^(OUT|QUESTIONABLE)\s*:\s*(.+?)(?:\s*\((.+)\))?$',line,re.I)
        if m:
            rows.append({'team_reported':team,'player_name':m.group(2).strip(),'status':m.group(1).upper(),'reason':(m.group(3) or '').strip()});continue
        m=re.match(r'^(.+?)\s*[-–—]\s*(.+?)\s*\((Out|Questionable)\)\s*\)?\s*$',line,re.I)
        if m:
            rows.append({'team_reported':team,'player_name':m.group(1).strip(),'status':m.group(3).upper(),'reason':m.group(2).strip()})
    return rows
def extract_matchday(text):
    m=re.search(r'Matchday\s+(\d+)',text,re.I);return int(m.group(1)) if m else None
def extract_dates(text,year):
    months={m:i for i,m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
    dates=[]
    for m in re.finditer(r'#{2,4}\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+([A-Za-z]+)\s+(\d{1,2})',text,re.I):
        mon=months.get(m.group(1).title())
        if mon:
            try:dates.append(datetime(year,mon,int(m.group(2))).date().isoformat())
            except:pass
    return sorted(set(dates))
def candidates():
    for n in range(1,41):
        yield 2024,n,BASE+f'mls-player-status-report-matchday-{n}'
    for n in range(1,41):
        yield 2025,n,BASE+f'mls-player-status-report-matchday-{n}-2025'
def main():
    reports=[];seen_hash=set()
    tasks=list(candidates())
    fetched=[]
    def work(item):
        year,n,url=item
        try:return item,fetch(url),None
        except Exception as e:return item,None,(type(e).__name__,str(e)[:120])
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs=[ex.submit(work,x) for x in tasks]
        for fut in as_completed(futs):
            item,text,err=fut.result();year,n,url=item
            if err:
                print('MISS',year,n,*err);continue
            fetched.append((year,n,url,text))
    for year,n,url,text in sorted(fetched):
        if 'Player Status Report' not in text or not re.search(r'\b(?:Out|Questionable)\b',text,re.I):
            continue
        md=extract_matchday(text)
        if md is not None and md!=n:continue
        rows=parse_entries(text)
        if len(rows)<3:continue
        sha=hashlib.sha256(text.encode()).hexdigest()
        if sha in seen_hash:continue
        seen_hash.add(sha)
        dates=extract_dates(text,year)
        r={'year':year,'matchday':n,'url':url,'raw_sha256':sha,'report_dates':dates,'entries':rows}
        reports.append(r)
        (OUT/f'{year}_matchday_{n:02d}.json').write_text(json.dumps(r,indent=2,ensure_ascii=False))
        print('FOUND',year,n,'dates',dates,'entries',len(rows))
    flat=[]
    for r in reports:
        for x in r['entries']:flat.append({**{k:r[k] for k in ['year','matchday','url','raw_sha256']},'report_dates':';'.join(r['report_dates']),**x})
    import pandas as pd
    pd.DataFrame(flat).to_parquet(OUT/'availability_archive_2024_2025.parquet',index=False)
    pd.DataFrame(flat).to_csv(OUT/'availability_archive_2024_2025.csv',index=False)
    meta={'built_at':datetime.utcnow().isoformat()+'Z','reports':len(reports),'entries':len(flat),
          'by_year':{str(y):sum(r['year']==y for r in reports) for y in [2024,2025]},
          'entries_by_year':{str(y):sum(x['year']==y for x in flat) for y in [2024,2025]}}
    (OUT/'meta.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2))
if __name__=='__main__':main()
