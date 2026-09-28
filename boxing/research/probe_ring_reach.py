#!/usr/bin/env python3
"""Probe The Ring public fighter profiles for missing boxer reaches.

Discovery uses only The Ring's public sitemap/robots structure and fighter
directory. No profile URLs or IDs are guessed. A reach is accepted into the
strict merge only when:
- the rendered fighter identity exactly matches a currently missing target;
- The Ring exposes an explicit plausible reach;
- independent exact-identity MartialBot agrees within 1 cm.

The Ring remains a corroboration source here; conflicts are quarantined.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'ring_reach_probe.json'
ADD=ROOT/'profile_supplements'/'ring_reach_additions.jsonl'

BASE='https://www.ringmagazine.com'
DIR=BASE+'/fighters'
SITEMAP_SEEDS=[
    BASE+'/sitemap.xml',
    BASE+'/sitemap_index.xml',
    BASE+'/sitemap-index.xml',
    BASE+'/robots.txt',
]
UA='Mozilla/5.0 AppwizaRingReach/2.1'


def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)


def fetch(url,limit=10_000_000):
    req=urllib.request.Request(
        url,
        headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'}
    )
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:
            raise ValueError('response too large')
        return r.geturl(),raw


def xml_locs(raw):
    try:
        root=ET.fromstring(raw)
    except Exception:
        return []
    return [
        (x.text or '').strip()
        for x in root.iter()
        if x.tag.endswith('loc') and (x.text or '').strip()
    ]


def sitemap_urls():
    urls=[]
    diag=[]
    queue=list(SITEMAP_SEEDS)
    seen=set()

    while queue and len(seen)<100:
        u=queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            final,raw=fetch(u)
            txt=raw.decode('utf-8','replace')
            locs=xml_locs(raw)
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',txt)
            diag.append({
                'url':u,'final_url':final,'status':'ok','locs':len(locs),
                'robots_sitemaps':robots[:30],'bytes':len(raw)
            })
            for x in [*robots,*locs]:
                p=urllib.parse.urlsplit(x)
                low=x.lower()
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x)
                    continue
                if p.hostname not in {'ringmagazine.com','www.ringmagazine.com'}:
                    continue
                if re.search(r'/fighters/[^/?#]+/?$',p.path,re.I):
                    urls.append(x.split('#')[0].rstrip('/'))
        except Exception as e:
            diag.append({
                'url':u,'status':'error',
                'error':type(e).__name__+': '+str(e)[:180]
            })

    return list(dict.fromkeys(urls)),diag


def directory_urls():
    urls=[]
    diag=[]
    try:
        final,raw=fetch(DIR,5_000_000)
        soup=BeautifulSoup(raw,'lxml')
        for a in soup.find_all('a',href=True):
            u=urllib.parse.urljoin(final,a['href']).split('#')[0]
            p=urllib.parse.urlsplit(u)
            if p.hostname not in {'ringmagazine.com','www.ringmagazine.com'}:
                continue
            if re.search(r'/fighters/[^/?#]+/?$',p.path,re.I):
                urls.append(u.rstrip('/'))
        diag.append({'url':DIR,'status':'ok','links':len(urls),'bytes':len(raw)})
    except Exception as e:
        diag.append({
            'url':DIR,'status':'error',
            'error':type(e).__name__+': '+str(e)[:180]
        })
    return list(dict.fromkeys(urls)),diag


def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    strings=[
        re.sub(r'\s+',' ',x).strip()
        for x in soup.stripped_strings
        if re.sub(r'\s+',' ',x).strip()
    ]

    # Ring fighter pages expose the canonical fighter identity in the
    # structured "Name" field. The page H1 is not reliable for this layout.
    name=''
    for i,s in enumerate(strings[:-1]):
        if s.casefold().rstrip(':')=='name':
            candidate=strings[i+1]
            if candidate and candidate.casefold() not in {'natl.','nationality','division'}:
                name=candidate
                break
    if not name:
        h=soup.find('h1')
        name=' '.join(h.stripped_strings).strip() if h else ''

    text=' '.join(strings)
    cm_vals=[];inch_vals=[]

    # Inline Ring forms such as "Reach 68\" 173 cm".
    for m in re.finditer(
        r'\bReach\s*:?[ ]*(\d+(?:\.\d+)?)\s*[\"″]\s*'
        r'(?:[/|,-]\s*)?(\d+(?:\.\d+)?)\s*cm\b',
        text,re.I
    ):
        inches=float(m.group(1));cm=float(m.group(2))
        if 120<=cm<=270:cm_vals.append(round(cm,2))
        converted=round(inches*2.54,2)
        if 120<=converted<=270:inch_vals.append(converted)

    for m in re.finditer(r'\bReach\s*:?[ ]*(\d+(?:\.\d+)?)\s*cm\b',text,re.I):
        cm=float(m.group(1))
        if 120<=cm<=270:cm_vals.append(round(cm,2))

    for m in re.finditer(r'\bReach\s*:?[ ]*(\d+(?:\.\d+)?)\s*[\"″]',text,re.I):
        cm=round(float(m.group(1))*2.54,2)
        if 120<=cm<=270:inch_vals.append(cm)

    # Structured DOM fallback. Ring often splits "Reach", "68\"", "173",
    # "cm" into separate text nodes. Read only values AFTER the Reach label
    # until the next profile field; never inspect the preceding field.
    stop_labels={'name','natl.','nationality','division','weight','stance','age','record'}
    for i,s in enumerate(strings):
        if s.casefold().rstrip(':')!='reach':continue
        chunk=[]
        for j in range(i+1,min(len(strings),i+6)):
            token=strings[j]
            if token.casefold().rstrip(':') in stop_labels:break
            chunk.append(token)
        joined=' '.join(chunk).replace('”','"').replace('″','"').replace('“','"')
        for m in re.finditer(r'(\d+(?:\.\d+)?)\s*cm\b',joined,re.I):
            cm=float(m.group(1))
            if 120<=cm<=270:cm_vals.append(round(cm,2))
        for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:\"|in(?:ches)?)',joined,re.I):
            cm=round(float(m.group(1))*2.54,2)
            if 120<=cm<=270:inch_vals.append(cm)

    cm_vals=sorted(set(round(v,2) for v in cm_vals))
    inch_vals=sorted(set(round(v,2) for v in inch_vals))
    all_vals=cm_vals+inch_vals
    if not all_vals:return name,None,[]
    # Metric is explicitly rendered by Ring and is preferred when present.
    # Inch conversion is a consistency check; ordinary rounding (68" = 172.72
    # vs rendered 173 cm) must not create a false conflict.
    if max(all_vals)-min(all_vals)>1.01:
        return name,None,sorted(set(all_vals))
    if cm_vals:
        reach=round(sum(cm_vals)/len(cm_vals),2)
    else:
        reach=round(sum(inch_vals)/len(inch_vals),2)
    return name,reach,sorted(set(all_vals))


def main():
    audit=json.loads(AUDIT.read_text())
    targets={
        nk(x['name']):x
        for x in audit.get('fighters',[])
        if 'reach_cm' in set(x.get('missing') or [])
    }

    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={
        nk(x['name']):x
        for x in mbobj.get('leads',[])
        if x.get('reach_cm') is not None
    }

    sitemap,diag1=sitemap_urls()
    directory,diag2=directory_urls()
    urls=sorted(set(sitemap)|set(directory))
    diagnostics=diag1+diag2

    def one(u):
        rec={'url':u}
        try:
            final,raw=fetch(u,2_500_000)
            h,reach,vals=parse(raw)
            key=nk(h)
            rec.update({
                'url':final,'h1':h,'target_key':key if key in targets else None,
                'reach_cm':reach,'reach_values':vals
            })
            if key not in targets:
                rec['status']='not_missing_target'
            elif reach is None:
                rec['status']='no_unique_reach'
            elif not 120<=reach<=270:
                rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m:
                    rec['martialbot_reach_cm']=float(m['reach_cm'])
                    rec['martialbot_url']=m.get('url')
                    rec['agrees_martialbot']=abs(float(reach)-float(m['reach_cm']))<=1
        except Exception as e:
            rec.update({
                'status':'fetch_error',
                'error':type(e).__name__+': '+str(e)[:180]
            })
        return rec

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        allrows=list(ex.map(one,urls))

    rows=[x for x in allrows if x.get('target_key') or x.get('status')=='fetch_error']
    leads=[x for x in rows if x.get('status')=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    additions=[]
    for x in corr:
        key=x['target_key']
        t=targets[key]
        vals=[float(x['reach_cm']),float(x['martialbot_reach_cm'])]
        reach=round(sum(vals)/len(vals),2)
        if float(reach).is_integer():
            reach=int(reach)
        additions.append({
            'target_source_id':t['id'],
            'name':t['name'],
            'career_source':t.get('career_source'),
            'fields':{'reach_cm':reach},
            'evidence':[{
                'source':'the_ring_martialbot_reach_consensus',
                'fields':{'reach_cm':reach},
                'exact_identity':True,
                'agreement_tolerance_cm':1,
                'sources':[
                    {
                        'source':'the_ring',
                        'url':x['url'],
                        'reported_reach_cm':x['reach_cm']
                    },
                    {
                        'source':'martialbot',
                        'url':x['martialbot_url'],
                        'reported_reach_cm':x['martialbot_reach_cm']
                    }
                ]
            }],
            'conflicts':{},
            'quality':'two_source_reach_consensus_ring_martialbot_exact_identity',
            'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:
            f.write(json.dumps(x,ensure_ascii=False)+'\n')

    counts={}
    for x in rows:
        counts[x.get('status')]=counts.get(x.get('status'),0)+1

    report={
        'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'missing_targets':len(targets),
        'sitemap_fighter_urls':len(sitemap),
        'directory_fighter_urls':len(directory),
        'candidate_fighter_urls':len(urls),
        'profiles_fetched':len(allrows),
        'matched_target_profiles':sum(1 for x in rows if x.get('target_key')),
        'status_counts':counts,
        'lead_count':len(leads),
        'martialbot_corroborated':len(corr),
        'martialbot_conflicts':len(conflicts),
        'corroborated_profiles':[
            {
                'name':targets[x['target_key']]['name'],
                'ring_reach_cm':x['reach_cm'],
                'martialbot_reach_cm':x['martialbot_reach_cm'],
                'url':x['url']
            }
            for x in corr
        ],
        'conflicting_profiles':[
            {
                'name':targets[x['target_key']]['name'],
                'ring_reach_cm':x['reach_cm'],
                'martialbot_reach_cm':x.get('martialbot_reach_cm'),
                'url':x['url']
            }
            for x in conflicts
        ],
        'discovery_diagnostics':diagnostics,
        'rows':rows,
        'policy':'Explicit public The Ring fighter profile discovered from Ring sitemap/directory; exact rendered H1 identity. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in (
        'missing_targets','sitemap_fighter_urls','directory_fighter_urls',
        'candidate_fighter_urls','profiles_fetched','matched_target_profiles',
        'status_counts','lead_count','martialbot_corroborated',
        'martialbot_conflicts'
    )},indent=2))


if __name__=='__main__':
    main()
