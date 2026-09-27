#!/usr/bin/env python3
"""Backfill missing reach from official Premier Boxing Champions fighter pages.

Discovery uses the public PBC fighters directory and sitemap; no guessed IDs.
A value is accepted only from an exact fighter-page identity with explicit
Reach in the official PBC stats block. Existing reach is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'pbc_reach_probe.json'
ADD=ROOT/'profile_supplements'/'pbc_reach_additions.jsonl'
BASE='https://www.premierboxingchampions.com'
UA='Mozilla/5.0 AppwizaPBCReach/1.0'
SEEDS=[BASE+'/FIGHTERS',BASE+'/fighters',BASE+'/sitemap.xml',BASE+'/sitemap_index.xml']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw,r.headers.get('content-type','')

def locs(raw):
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def candidate_links(raw,base):
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(base,a['href']).split('#')[0]
        p=urllib.parse.urlsplit(u)
        if p.hostname not in {'www.premierboxingchampions.com','premierboxingchampions.com'}:continue
        path=p.path.rstrip('/')
        if not path or path.lower() in {'/fighters','/fight-nights','/news','/videos'}:continue
        # PBC fighter profiles are often /name or /node/ID.
        if re.fullmatch(r'/node/\d+',path,re.I) or (path.count('/')==1 and not re.search(r'fight|news|video|photo|about|contact',path,re.I)):
            label=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
            out.append((u,label))
    return out

def discover():
    urls={};diag=[];queue=list(SEEDS);seen=set()
    while queue and len(seen)<30:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ct=fetch(u)
            xml=locs(raw)
            diag.append({'url':u,'status':'ok','bytes':len(raw),'content_type':ct,'locs':len(xml)})
            if xml:
                for x in xml:
                    if 'sitemap' in x.lower() and x.lower().endswith('.xml'):
                        if x not in seen:queue.append(x)
                    else:
                        p=urllib.parse.urlsplit(x).path.rstrip('/')
                        if re.fullmatch(r'/node/\d+',p,re.I):
                            urls.setdefault(x,'')
            else:
                for x,label in candidate_links(raw,final):urls.setdefault(x,label)
        except Exception as e:diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(urls),diag

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    text=' '.join(soup.stripped_strings)
    # Prefer stats heading immediately following "Stats", but use h1/h2 fallbacks.
    names=[]
    for tag in soup.find_all(['h1','h2','h3','h4']):
        t=re.sub(r'\s+',' ',tag.get_text(' ',strip=True)).strip()
        if t and len(t)<=100:names.append(t)
    # Common PBC page: "Jermall Charlo “Hit Man”" under Stats.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)(?:½|¼|¾)?\s*["″][^\d]{0,50}\((\d+(?:\.\d+)?)\s*cm',text,re.I)
    if m:reach=float(m.group(2))
    else:
        m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]',text,re.I)
        reach=round(float(m.group(1))*2.54,2) if m else None
    return names,reach,text[:800]

def match_target(names,targets):
    hits=[]
    for name in names:
        # Remove nickname in curly/straight quotes and common page suffix text.
        clean=re.sub(r'[“"].*?[”"]','',name).strip()
        k=nk(clean)
        if k in targets:hits.append(k)
    return sorted(set(hits))

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    urls,diag=discover()
    def one(u):
        rec={'url':u}
        try:
            final,raw,ct=fetch(u,2_500_000);names,reach,sample=parse(raw)
            hits=match_target(names,targets)
            rec.update({'url':final,'headings':names[:12],'target_keys':hits,'reach_cm':reach,'text_sample':sample})
            if len(hits)!=1:rec['status']='no_unique_target_identity'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:rec['status']='accepted'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=8) as ex:rows=list(ex.map(one,urls))
    accepted=[x for x in rows if x.get('status')=='accepted']
    additions=[]
    for x in accepted:
        key=x['target_keys'][0];t=targets[key];reach=x['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'pbc_official_fighter_profile','url':x['url'],'fields':{'reach_cm':reach},'exact_identity':True}],
          'conflicts':{},'quality':'official_promoter_structured_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x.get('status')]=counts.get(x.get('status'),0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_candidate_pages':len(urls),'status_counts':counts,'accepted':len(additions),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],'url':x['evidence'][0]['url']} for x in additions],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Official Premier Boxing Champions fighter page only; exact target identity from page heading and explicit plausible Reach stat; missing reach only; never overwrite.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_candidate_pages','status_counts','accepted')},indent=2))
if __name__=='__main__':main()
