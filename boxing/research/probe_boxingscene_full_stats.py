#!/usr/bin/env python3
"""Probe explicitly linked BoxingScene historical full-punch-stat targets."""
from __future__ import annotations
import json,re,urllib.request,urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'punch_supplements'/'boxingscene_compubox_summary_report.json'
UA='Mozilla/5.0 AppwizaBoxingFullStatsProbe/1.0'

def fetch(url,limit=6_000_000):
    req=urllib.request.Request(url,headers={
      'User-Agent':UA,
      'Accept-Language':'en-US,en;q=0.8',
      'Referer':'https://www.boxingscene.com/'
    })
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw,r.headers.get('content-type')


def archive_candidates(url,limit=6):
    q=urllib.parse.urlencode({
      'url':url,'output':'json','fl':'timestamp,original,statuscode,mimetype,digest',
      'filter':['statuscode:200','mimetype:text/html'],'collapse':'digest'
    },doseq=True)
    api='https://web.archive.org/cdx/search/cdx?'+q
    req=urllib.request.Request(api,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=40) as r:
        data=json.loads(r.read(2_000_000).decode('utf-8','replace'))
    rows=data[1:] if data and isinstance(data[0],list) else []
    rows=sorted(rows,key=lambda x:x[0])
    return [f'https://web.archive.org/web/{x[0]}/{x[1]}' for x in rows[:limit]]

def fetch_with_archive(url):
    try:
        return fetch(url),None
    except Exception as first:
        errs=[repr(first)]
    for archived in archive_candidates(url):
        try:
            return fetch(archived),archived
        except Exception as e:
            errs.append(repr(e))
    raise RuntimeError('current+archive fetch failed: '+' | '.join(errs[:6]))

def table_rows(soup):
    out=[]
    for ti,t in enumerate(soup.find_all('table')):
        rows=[]
        for tr in t.find_all('tr'):
            cells=[re.sub(r'\s+',' ',c.get_text(' ',strip=True)).strip() for c in tr.find_all(['th','td'])]
            if cells:rows.append(cells)
        if rows:out.append({'index':ti,'rows':rows[:80]})
    return out

def main():
    data=json.loads(REPORT.read_text())
    targets=data.get('explicit_full_stat_targets') or []
    out=[]
    for x in targets:
        url=x['url'];item={'url':url,'label':x.get('label'),'article_urls':x.get('article_urls')}
        try:
            (final,raw,ct),archive_url=fetch_with_archive(url);soup=BeautifulSoup(raw,'lxml')
            text=re.sub(r'\s+',' ',' '.join(soup.stripped_strings)).strip()
            item.update({
              'final':final,'archive_url':archive_url,'content_type':ct,'bytes':len(raw),
              'title':soup.title.get_text(' ',strip=True) if soup.title else None,
              'h1':[re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() for h in soup.find_all('h1')][:6],
              'tables':table_rows(soup),
              'text_sample':text[:12000],
              'links':[urllib.parse.urljoin(final,a.get('href')) for a in soup.find_all('a',href=True)
                       if re.search(r'compubox|punch|stat|pdf',str(a.get('href')),re.I)][:80]
            })
        except Exception as e:item['error']=repr(e)
        out.append(item)
    print(json.dumps({'targets':len(targets),'items':out},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
