#!/usr/bin/env python3
"""Index real Champinon boxing profile URLs from its public boxer directory.

This eliminates guessed slugs from secondary-career recovery. Only explicit
links found on Champinon's own English boxing directory are indexed.
"""
from __future__ import annotations
import datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'supplemental_careers'/'champinon_profile_index.json'
URL='https://champinon.info/boxing/'
UA='Mozilla/5.0 AppwizaChampinonIndex/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def main():
    req=urllib.request.Request(URL,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=45) as r:raw=r.read(6_000_000)
    soup=BeautifulSoup(raw,'lxml')
    index={}
    for a in soup.find_all('a',href=True):
        href=urllib.parse.urljoin(URL,a['href']).split('#')[0]
        p=urllib.parse.urlsplit(href).path.rstrip('/')+'/'
        if not p.startswith('/boxing/') or p=='/boxing/':continue
        # exclude category/special pages under boxing/
        slug=p[len('/boxing/'):].strip('/')
        if not slug or '/' in slug:continue
        name=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
        if len(name)<2:continue
        key=nk(name)
        if not key:continue
        entry={'name':name,'url':'https://champinon.info'+p}
        index.setdefault(key,[])
        if entry not in index[key]:index[key].append(entry)
    data={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_url':URL,
          'unique_name_keys':len(index),'ambiguous_name_keys':sum(len(v)>1 for v in index.values()),
          'index':index,'policy':'Explicit links from Champinon English boxing directory only; no guessed slugs.'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(data,indent=2,ensure_ascii=False))
    print(json.dumps({k:data[k] for k in ['unique_name_keys','ambiguous_name_keys','source_url']},indent=2))

if __name__=='__main__':main()
