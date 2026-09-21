#!/usr/bin/env python3
"""Build a bounded official-WBA boxer identity index from explicit profile links.

No numeric ID guessing:
- seed from official WBA ranking/results/schedule/champion pages;
- follow only explicit /wba-boxer-profile/?id=... links on fetched profiles;
- exact profile page title supplies the identity.

Output is an index only. It does not assert career completeness or backdate
profile attributes.
"""
from __future__ import annotations
import argparse,json,re,time,unicodedata,urllib.parse,urllib.request
from collections import deque
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'profile_supplements'/'wba_profile_index.json'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaWBAProfileIndex/1.0'
RX=re.compile(r'/wba-boxer-profile/?\?id=(\d+)',re.I)

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('response too large')
        return r.geturl(),raw

def links(soup,base):
    out=[]
    for a in soup.find_all('a',href=True):
        url=urllib.parse.urljoin(base,a['href'])
        m=RX.search(url)
        if m:out.append((m.group(1),url.split('#')[0]))
    return out

def title_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    if m:return re.sub(r'\s+',' ',m.group(1)).strip()
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=1500)
    ap.add_argument('--sleep',type=float,default=.12)
    args=ap.parse_args()
    queue=deque();seen=set();index={};fail=[]
    for seed in [BASE+'/wba-ranking',BASE+'/boxing-results',BASE+'/boxing-schedule',BASE+'/current-wba-champions',BASE+'/']:
        try:
            final,raw=fetch(seed);soup=BeautifulSoup(raw,'lxml')
            for pid,url in links(soup,final):
                if pid not in seen:seen.add(pid);queue.append((pid,url))
        except Exception as e:fail.append({'url':seed,'error':str(e)[:200]})
    fetched=0
    while queue and fetched<args.limit:
        pid,url=queue.popleft()
        try:
            final,raw=fetch(url);fetched+=1;soup=BeautifulSoup(raw,'lxml')
            name=title_name(soup)
            if name:
                key=nk(name)
                if key:
                    index.setdefault(key,[])
                    entry={'name':name,'profile_id':int(pid),'url':f'{BASE}/wba-boxer-profile?id={pid}'}
                    if entry not in index[key]:index[key].append(entry)
            for npid,nurl in links(soup,final):
                if npid not in seen:
                    seen.add(npid);queue.append((npid,nurl))
        except Exception as e:fail.append({'profile_id':pid,'url':url,'error':str(e)[:200]})
        time.sleep(max(0,args.sleep))
    data={
      'generated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
      'profiles_fetched':fetched,'profile_ids_seen':len(seen),'queue_remaining':len(queue),
      'unique_name_keys':len(index),'ambiguous_name_keys':sum(len(v)>1 for v in index.values()),
      'index':index,'failures':fail[:200],
      'policy':'Official WBA explicit profile-link graph only; no guessed IDs; page title is identity.'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(data,indent=2,ensure_ascii=False))
    print(json.dumps({k:data[k] for k in ['profiles_fetched','profile_ids_seen','queue_remaining','unique_name_keys','ambiguous_name_keys']},indent=2))

if __name__=='__main__':main()
