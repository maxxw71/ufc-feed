#!/usr/bin/env python3
"""Backfill missing reach from official Top Rank fighter profiles.

Discovery uses Top Rank public sitemap/listing URLs only. Exact profile identity
is required. Official promoter reach is accepted as strict evidence by itself,
because it is a first-party structured fighter profile. Existing reach is never
overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'toprank_reach_probe.json'
ADD=ROOT/'profile_supplements'/'toprank_reach_additions.jsonl'
BASE='https://toprank.com'
UA='Mozilla/5.0 AppwizaTopRankReach/1.0'
SITEMAPS=[BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml']
LISTS=[BASE+'/fighters']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=10_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=40) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw,r.headers.get('content-type','')

def locs(raw):
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def html_links(raw):
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(BASE,a['href']).split('#')[0]
        if re.search(r'/fighters/[^/?#]+/?$',urllib.parse.urlsplit(u).path,re.I):out.append(u)
    return list(dict.fromkeys(out))

def discover():
    queue=list(SITEMAPS);seen=set();urls=[];diag=[]
    while queue and len(seen)<60:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ct=fetch(u)
            xs=locs(raw)
            diag.append({'url':u,'status':'ok','locs':len(xs),'bytes':len(raw),'content_type':ct})
            for x in xs:
                lx=x.lower()
                if 'sitemap' in lx and (lx.endswith('.xml') or '.xml?' in lx):
                    if x not in seen:queue.append(x)
                elif re.search(r'/fighters/[^/?#]+/?$',urllib.parse.urlsplit(x).path,re.I):
                    urls.append(x)
        except Exception as e:diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    if not urls:
        for u in LISTS:
            try:
                final,raw,ct=fetch(u);xs=html_links(raw);urls.extend(xs)
                diag.append({'url':u,'status':'ok_html','links':len(xs),'bytes':len(raw)})
            except Exception as e:diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def slug_key(url):
    return nk(urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1])

def parse(raw):
    soup=BeautifulSoup(raw,'lxml');h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    text=' '.join(strings)
    vals=[]

    def add_measure(value):
        s=str(value or '').replace('”','"').replace('″','"').replace('“','"')
        m=re.search(r'(\d+(?:\.\d+)?)\s*(?:\"|in(?:ches)?)\b',s,re.I)
        if m:
            cm=round(float(m.group(1))*2.54,2)
            if 120<=cm<=270:vals.append(cm)
        m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s,re.I)
        if m:
            cm=float(m.group(1))
            if 120<=cm<=270:vals.append(cm)

    # Current Top Rank cards may render Reach and its value as separate DOM
    # strings; support both label->value and inline "Reach 73\"" layouts.
    for i,s in enumerate(strings):
        if s.casefold().rstrip(':')=='reach':
            for j in (i+1,i-1):
                if 0<=j<len(strings):add_measure(strings[j])
    for m in re.finditer(r'\bReach\s+([^A-Za-z]{0,5}\d[^A-Za-z]{0,12}(?:\"|″|in(?:ches)?|cm))',text,re.I):
        add_measure(m.group(1))
    # Simple inline fallback.
    for m in re.finditer(r'\bReach\s+(\d+(?:\.\d+)?)\s*(?:[\"″]|in(?:ches)?)',text,re.I):
        add_measure(m.group(0))

    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    urls,diag=discover()
    cmap={}
    for u in urls:cmap.setdefault(slug_key(u),[]).append(u)
    jobs=[]
    for k,t in targets.items():
        cand=list(dict.fromkeys(cmap.get(k,[])))
        if len(cand)==1:jobs.append((k,t,cand[0]))
    def one(job):
        k,t,u=job
        rec={'id':t['id'],'name':t['name'],'url':u,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw,ct=fetch(u,2_000_000);h,reach,vals=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':reach,'reach_values':vals})
            if nk(h)!=k:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            else:rec['status']='accepted'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,jobs))
    acc=[x for x in rows if x['status']=='accepted']
    additions=[]
    for x in acc:
        t=targets[nk(x['name'])]
        reach=x['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'top_rank_official_fighter_profile','url':x['url'],'fields':{'reach_cm':reach},
                       'exact_identity':True}],
          'conflicts':{},'quality':'official_promoter_structured_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_fighter_urls':len(urls),'exact_slug_targets':len(jobs),'status_counts':counts,
      'accepted':len(acc),'accepted_profiles':[{'name':x['name'],'reach_cm':x['reach_cm'],'url':x['url']} for x in acc],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Official Top Rank public fighter profile only; exact normalized H1 identity; plausible explicit reach; missing reach only; never overwrite.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_fighter_urls','exact_slug_targets','status_counts','accepted')},indent=2))

if __name__=='__main__':main()
