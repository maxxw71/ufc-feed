#!/usr/bin/env python3
"""Read-only probe of public profile indexes for scalable reach consensus."""
from __future__ import annotations
import json,re,urllib.request,xml.etree.ElementTree as ET

UA='Mozilla/5.0 AppwizaReachIndexProbe/1.0'
SITES={
 'rtfight':['https://rtfight.com/robots.txt','https://rtfight.com/sitemap.xml','https://rtfight.com/sitemap_index.xml'],
 'martialbot':['https://www.martialbot.com/robots.txt','https://www.martialbot.com/boxing/sitemap.xml','https://www.martialbot.com/sitemap.xml','https://www.martialbot.com/sitemap_index.xml'],
}

def get(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw,r.headers.get('content-type')

def locs(raw):
    try:
        root=ET.fromstring(raw)
    except Exception:return []
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

out={}
for site,urls in SITES.items():
    item={'probes':[],'discovered_sitemaps':[],'profile_like_urls':[]}
    seen=set()
    queue=[]
    for u in urls:
        try:
            final,raw,ct=get(u)
            text=raw.decode('utf-8','replace')
            row={'url':u,'final':final,'status':'ok','content_type':ct,'bytes':len(raw),'sample':text[:7000]}
            ls=locs(raw)
            robots_sitemaps=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',text)
            row['loc_count']=len(ls);row['loc_sample']=ls[:40];row['robots_sitemaps']=robots_sitemaps[:20]
            item['probes'].append(row)
            for x in [*robots_sitemaps,*ls]:
                low=x.lower()
                if 'sitemap' in low and x not in seen and x not in queue:queue.append(x)
                if re.search(r'/boxing-profiles/|/boxers/|/boxing/boxers/',low):
                    item['profile_like_urls'].append(x)
        except Exception as e:
            item['probes'].append({'url':u,'status':'error','error':repr(e)})
    # bounded child sitemap expansion
    for u in queue[:30]:
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ct=get(u)
            ls=locs(raw)
            item['discovered_sitemaps'].append({'url':u,'loc_count':len(ls),'sample':ls[:20]})
            for x in ls:
                low=x.lower()
                if re.search(r'/boxing-profiles/|/boxers/|/boxing/boxers/',low):
                    item['profile_like_urls'].append(x)
        except Exception as e:
            item['discovered_sitemaps'].append({'url':u,'error':repr(e)})
    item['profile_like_urls']=list(dict.fromkeys(item['profile_like_urls']))
    item['profile_like_count']=len(item['profile_like_urls'])
    item['profile_like_sample']=item['profile_like_urls'][:100]
    out[site]=item

summary={}
for site,item in out.items():
    summary[site]={
      'probe_status':[{'url':x.get('url'),'status':x.get('status'),'content_type':x.get('content_type'),
                       'bytes':x.get('bytes'),'loc_count':x.get('loc_count'),'error':x.get('error'),
                       'robots_sitemaps':x.get('robots_sitemaps')}
                      for x in item.get('probes',[])],
      'child_sitemaps':[{'url':x.get('url'),'loc_count':x.get('loc_count'),'error':x.get('error')}
                        for x in item.get('discovered_sitemaps',[])],
      'profile_like_count':item.get('profile_like_count'),
      'profile_like_sample':(item.get('profile_like_sample') or [])[:30]
    }
print(json.dumps(summary,indent=2,ensure_ascii=False))
