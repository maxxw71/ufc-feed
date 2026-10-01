#!/usr/bin/env python3
"""Probe BoxingMetrics public fighter profiles for missing reach.

BoxingMetrics is corroboration-only. A candidate is accepted only when the
rendered fighter identity is exact, reach is explicit/plausible, and an
independent exact-identity MartialBot value agrees within 1 cm. Existing reach
is never overwritten; conflicts stay out of verified profiles.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxingmetrics_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxingmetrics_reach_additions.jsonl'
BASE='https://boxingmetrics.com'
UA='Mozilla/5.0 AppwizaBoxingMetricsReach/1.0'
SEEDS=[BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/robots.txt',BASE+'/fighters',BASE+'/fighter']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit: raise ValueError('response too large')
        return r.geturl(),raw

def xml_locs(raw):
    try: root=ET.fromstring(raw)
    except Exception: return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def slug_key(url):
    p=urllib.parse.urlsplit(url)
    m=re.search(r'/fighter/\d+/([^/?#]+)',p.path,re.I)
    return nk(urllib.parse.unquote(m.group(1)).replace('-',' ')) if m else None

def discover(targets):
    urls=[];diag=[];queue=list(SEEDS);seen=set()
    while queue and len(seen)<80:
        u=queue.pop(0)
        if u in seen: continue
        seen.add(u)
        try:
            final,raw=fetch(u)
            txt=raw.decode('utf-8','replace'); locs=xml_locs(raw)
            for x in re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',txt):
                if x not in seen and x not in queue: queue.append(x)
            candidates=list(locs)
            if not locs:
                soup=BeautifulSoup(raw,'lxml')
                candidates=[urllib.parse.urljoin(final,a['href']).split('#')[0] for a in soup.find_all('a',href=True)]
            hit=0
            for x in candidates:
                if 'boxingmetrics.com' not in (urllib.parse.urlsplit(x).hostname or ''): continue
                if 'sitemap' in x.casefold() and x not in seen and x not in queue:
                    queue.append(x); continue
                k=slug_key(x)
                if k in targets:
                    urls.append(x); hit+=1
            diag.append({'url':u,'status':'ok','locs':len(locs),'matches':hit,'bytes':len(raw)})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def cm_measure(s):
    txt=str(s or '').replace('”','"').replace('″','"')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:
        v=float(m.group(1))
        if 120<=v<=270:return round(v,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:\"|in(?:ches)?)\b',txt,re.I)
    if m:
        v=round(float(m.group(1))*2.54,2)
        if 120<=v<=270:return v
    return None

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1'); name=h.get_text(' ',strip=True) if h else ''
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    height=reach=None
    for i,s in enumerate(strings):
        low=s.casefold().rstrip(':')
        if low=='height' and i+1<len(strings): height=cm_measure(strings[i+1])
        if low=='reach' and i+1<len(strings): reach=cm_measure(strings[i+1])
        if height is None:
            m=re.search(r'\bHeight\s*:?[ ]*(\d+(?:\.\d+)?\s*cm)',s,re.I)
            if m:height=cm_measure(m.group(1))
        if reach is None:
            m=re.search(r'\bReach\s*:?[ ]*(\d+(?:\.\d+)?\s*cm)',s,re.I)
            if m:reach=cm_measure(m.group(1))
    return name,height,reach

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[]) if x.get('reach_cm') is not None}
    urls,diag=discover(targets)
    def one(u):
        rec={'url':u}
        try:
            final,raw=fetch(u,2_500_000);name,height,reach=parse(raw);key=nk(name)
            rec.update({'url':final,'h1':name,'target_key':key if key in targets else None,'page_height_cm':height,'reach_cm':reach})
            if key not in targets: rec['status']='identity_mismatch'
            elif reach is None: rec['status']='no_reach'
            else:
                rec['status']='lead';m=mb.get(key)
                if m:
                    rec['martialbot_reach_cm']=float(m['reach_cm']);rec['martialbot_url']=m.get('url')
                    rec['agrees_martialbot']=abs(float(reach)-float(m['reach_cm']))<=1
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=10) as ex: rows=list(ex.map(one,urls))
    corr=[x for x in rows if x.get('status')=='lead' and x.get('agrees_martialbot') is True]
    adds=[]
    for x in corr:
        t=targets[x['target_key']]
        reach=round(statistics.median([float(x['reach_cm']),float(x['martialbot_reach_cm'])]),2)
        if float(reach).is_integer(): reach=int(reach)
        adds.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxingmetrics_martialbot_reach_consensus','fields':{'reach_cm':reach},'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxingmetrics','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':x['martialbot_url'],'reported_reach_cm':x['martialbot_reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxingmetrics_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in adds:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r.get('status')]=counts.get(r.get('status'),0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),'discovered_matching_urls':len(urls),
      'status_counts':counts,'corroborated':len(corr),'rows':rows,'discovery_diagnostics':diag,
      'policy':'BoxingMetrics is corroboration-only; exact profile identity + explicit reach + independent MartialBot agreement within 1 cm. Missing reach only; never overwrite.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_matching_urls','status_counts','corroborated')},indent=2))

if __name__=='__main__': main()
