#!/usr/bin/env python3
"""Build a bounded official-WBA boxer identity index from explicit profile links.

No numeric ID guessing.

Fast graph policy:
1. Seed explicit boxer links from official WBA ranking/results/schedule/champion pages.
2. Fetch only those seed profile pages.
3. Index every explicit opponent-profile link found on those pages using its
   visible anchor/title identity WITHOUT recursively fetching every opponent.
4. Exact duplicate names collapse; ambiguous names remain multi-entry.

This produces a much broader exact name -> WBA profile-ID map with a few
hundred requests instead of recursively fetching thousands of profiles.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,time,unicodedata,urllib.parse,urllib.request
from collections import deque
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'profile_supplements'/'wba_profile_index.json'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAProfileIndex/1.1'
RX=re.compile(r'/wba-boxer-profile/?\?id=(\d+)',re.I)

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def identity_from_link(a):
    href=a.get('href') or ''
    url=urllib.parse.urljoin(BASE,href).split('#')[0]
    m=RX.search(url)
    if not m:return None
    pid=int(m.group(1))
    visible=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
    title=re.sub(r'\s+',' ',str(a.get('title') or '')).strip()
    tm=re.match(r'Boxing record\s*-\s*(.+)$',title,re.I)
    name=(tm.group(1).strip() if tm else visible)
    if not name or len(name)<2:return None
    return {'name':name,'profile_id':pid,'url':f'{BASE}/wba-boxer-profile?id={pid}'}

def page_links(soup):
    out=[]
    for a in soup.find_all('a',href=True):
        item=identity_from_link(a)
        if item:out.append(item)
    return out

def title_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    return re.sub(r'\s+',' ',m.group(1)).strip() if m else None

def add(index,item):
    key=nk(item.get('name'))
    if not key:return
    entry={'name':item['name'],'profile_id':int(item['profile_id']),'url':item['url']}
    bucket=index.setdefault(key,[])
    if not any(int(x['profile_id'])==entry['profile_id'] for x in bucket):
        bucket.append(entry)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seed-limit',type=int,default=500)
    ap.add_argument('--sleep',type=float,default=.08)
    args=ap.parse_args()

    index={};seed_by_id={};fail=[]
    seeds=[
      BASE+'/wba-ranking',
      BASE+'/boxing-results',
      BASE+'/boxing-schedule',
      BASE+'/current-wba-champions',
      BASE+'/',
    ]
    for url in seeds:
        try:
            final,raw=fetch(url);soup=BeautifulSoup(raw,'lxml')
            for item in page_links(soup):
                add(index,item);seed_by_id.setdefault(item['profile_id'],item)
        except Exception as e:
            fail.append({'url':url,'error':str(e)[:200]})

    fetched=0;linked_identities=0
    for pid,item in list(seed_by_id.items())[:args.seed_limit]:
        try:
            final,raw=fetch(item['url']);fetched+=1
            soup=BeautifulSoup(raw,'lxml')
            canonical=title_name(soup)
            if canonical:
                add(index,{'name':canonical,'profile_id':pid,'url':item['url']})
            before=sum(len(v) for v in index.values())
            for linked in page_links(soup):add(index,linked)
            after=sum(len(v) for v in index.values())
            linked_identities+=max(0,after-before)
        except Exception as e:
            fail.append({'profile_id':pid,'url':item['url'],'error':str(e)[:200]})
        time.sleep(max(0,args.sleep))

    for rows in index.values():
        rows.sort(key=lambda x:(x['profile_id'],x['name']))

    data={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'seed_pages':seeds,'seed_profile_ids':len(seed_by_id),
      'seed_profiles_fetched':fetched,'linked_identities_added':linked_identities,
      'unique_name_keys':len(index),'profile_id_entries':sum(len(v) for v in index.values()),
      'ambiguous_name_keys':sum(len(v)>1 for v in index.values()),
      'index':index,'failures':fail[:200],
      'policy':'Official WBA explicit profile links only; seed profiles fetched, opponent identities indexed from explicit links without guessed IDs.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(data,indent=2,ensure_ascii=False))
    print(json.dumps({k:data[k] for k in [
      'seed_profile_ids','seed_profiles_fetched','linked_identities_added',
      'unique_name_keys','profile_id_entries','ambiguous_name_keys'
    ]},indent=2))

if __name__=='__main__':main()
