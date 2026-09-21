#!/usr/bin/env python3
"""Backfill missing strict-sample boxer profile fields from official WBA profiles.

Discovery is graph-based, never ID guessing:
1. Seed from official WBA ranking/results/schedule pages that explicitly link
   to /wba-boxer-profile/?id=...
2. Fetch bounded official profile pages.
3. Follow only explicit opponent profile links found on those pages.
4. Exact normalized identity match is required before any field is accepted.

Only fields listed as missing by PROFILE_GAP_AUDIT are filled. Existing profile
supplements are never overwritten.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os,re,time,unicodedata,urllib.parse,urllib.request,urllib.error
from collections import deque
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wba_profile_backfill_report.json'
BASE='https://www.wbaboxing.com'
UA='Mozilla/5.0 AppwizaBoxingWBAProfile/1.0'
PROFILE_RE=re.compile(r'/wba-boxer-profile/?\?id=(\d+)',re.I)

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,timeout=35):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('profile response too large')
        return r.geturl(),raw

def links(soup,base):
    out=[]
    for a in soup.find_all('a',href=True):
        url=urllib.parse.urljoin(base,a['href'])
        m=PROFILE_RE.search(url)
        if m:out.append((m.group(1),url))
    return out

def field(soup,label):
    want=label.upper().rstrip(':')
    for tr in soup.find_all('tr'):
        cells=[re.sub(r'\s+',' ',x.get_text(' ',strip=True)).strip() for x in tr.find_all(['th','td'])]
        if len(cells)>=2 and cells[0].upper().replace(' :','').rstrip(':')==want:
            return cells[1].strip() or None
    # Some profile templates use label/value divs instead of table cells.
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    for i,x in enumerate(strings[:-1]):
        if x.upper().replace(' :','').rstrip(':')==want:
            return strings[i+1]
    return None

def parse_number(s):
    if not s:return None
    s=str(s).replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)',s)
    return float(m.group(1)) if m else None

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    # 6' 1", 5' 10.5"
    m=re.search(r"(\d+)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?",txt)
    if m:
        feet=float(m.group(1));inch=float(m.group(2) or 0)
        return round((feet*12+inch)*2.54,2)
    # reach often just 76.5"
    m=re.search(r'(\d+(?:\.\d+)?)\s*"',txt)
    if m:return round(float(m.group(1))*2.54,2)
    # sometimes centimeters are supplied
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:return round(float(m.group(1)),2)
    return None

def born_iso(s):
    if not s:return None
    for fmt in ('%m-%d-%Y','%m/%d/%Y','%Y-%m-%d'):
        try:return dt.datetime.strptime(str(s).strip(),fmt).date().isoformat()
        except Exception:pass
    return None

def profile_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    if m:return re.sub(r'\s+',' ',m.group(1)).strip()
    for tag in soup.find_all(['h1','h2','h3']):
        x=re.sub(r'\s+',' ',tag.get_text(' ',strip=True)).strip()
        if x and 'WBA BOXER PROFILE' not in x.upper():return x
    return None

def parse_profile(url,raw):
    soup=BeautifulSoup(raw,'lxml')
    name=profile_name(soup)
    data={}
    h=cm_measure(field(soup,'HEIGHT'))
    r=cm_measure(field(soup,'REACH'))
    b=born_iso(field(soup,'BORN'))
    n=field(soup,'COUNTRY')
    if h and 120<=h<=250:data['height_cm']=h
    if r and 120<=r<=270:data['reach_cm']=r
    if b:data['born']=b
    if n and 2<=len(n)<=80:data['nationality']=n
    return name,data,links(soup,url)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=900)
    ap.add_argument('--sleep',type=float,default=.18)
    args=ap.parse_args()
    audit=json.loads(AUDIT.read_text())
    targets={}
    for field_name,items in (audit.get('missing_ranked') or {}).items():
        if field_name not in {'born','height_cm','reach_cm','nationality'}:continue
        for x in items:
            key=nk(x.get('name'))
            if not key:continue
            t=targets.setdefault(key,{'name':x['name'],'target_source_id':x['id'],'career_source':x.get('career_source'),'missing':set()})
            t['missing'].add(field_name)

    existing={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);existing[x['target_source_id']]=x
            except Exception:pass

    queue=deque();seen=set();discovered=0
    seeds=[
      BASE+'/wba-ranking',
      BASE+'/boxing-results',
      BASE+'/boxing-schedule',
      BASE+'/current-wba-champions',
      BASE+'/',
    ]
    seed_errors=[]
    for url in seeds:
        try:
            final,raw=fetch(url)
            soup=BeautifulSoup(raw,'lxml')
            for pid,purl in links(soup,final):
                if pid not in seen:
                    seen.add(pid);queue.append((pid,purl));discovered+=1
        except Exception as e:seed_errors.append({'url':url,'error':str(e)[:200]})

    fetched=0;matched=0;field_counts={'born':0,'height_cm':0,'reach_cm':0,'nationality':0};failures=[];found=[]
    while queue and fetched<args.limit:
        pid,url=queue.popleft()
        try:
            final,raw=fetch(url);fetched+=1
            name,data,newlinks=parse_profile(final,raw)
            for npid,nurl in newlinks:
                if npid not in seen:
                    seen.add(npid);queue.append((npid,nurl));discovered+=1
            key=nk(name)
            t=targets.get(key)
            if t and key==nk(t['name']):
                matched+=1
                add={k:v for k,v in data.items() if k in t['missing']}
                if add:
                    cur=existing.get(t['target_source_id'])
                    if cur:
                        fields=dict(cur.get('fields') or {})
                        actual={k:v for k,v in add.items() if k not in fields}
                        if not actual:
                            continue
                        fields.update(actual);cur['fields']=fields
                        ev=list(cur.get('evidence') or [])
                        ev.append({'source':'wba_official_profile','url':final,'profile_id':pid,'fields':actual})
                        cur['evidence']=ev
                        cur['quality']='public_profile_exact_identity_missing_fields_only'
                        existing[t['target_source_id']]=cur
                    else:
                        actual=add
                        existing[t['target_source_id']]={
                          'target_source_id':t['target_source_id'],'name':t['name'],'career_source':t['career_source'],
                          'fields':actual,
                          'evidence':[{'source':'wba_official_profile','url':final,'profile_id':pid,'fields':actual}],
                          'conflicts':{},'quality':'official_wba_structured_profile_exact_identity_missing_fields_only',
                          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
                        }
                    for k in actual:field_counts[k]+=1
                    found.append({'name':t['name'],'profile_id':pid,'url':final,'fields':actual})
        except Exception as e:
            failures.append({'profile_id':pid,'url':url,'error':str(e)[:220]})
        time.sleep(max(0,args.sleep))

    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as fh:
        for x in sorted(existing.values(),key=lambda x:(x.get('name') or '',x.get('target_source_id') or '')):
            fh.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'target_fighters':len(targets),
      'profiles_fetched':fetched,'profile_ids_discovered':discovered,'queue_remaining':len(queue),
      'exact_target_matches':matched,'new_field_counts':field_counts,'profiles_updated':len(found),
      'found':found,'seed_errors':seed_errors,'fetch_failures':failures[:100],
      'policy':'Official WBA profile graph only; IDs must be explicitly linked; exact normalized identity; missing fields only; existing values never overwritten.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ['target_fighters','profiles_fetched','profile_ids_discovered','queue_remaining','exact_target_matches','new_field_counts','profiles_updated']},indent=2))

if __name__=='__main__':main()
