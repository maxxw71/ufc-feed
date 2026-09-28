#!/usr/bin/env python3
"""Probe The Ring fighter directory for missing strict boxer reaches.

The Ring is a separate current source. Discovery comes only from explicit links
on The Ring's public fighter directory; no guessed profile IDs. An automatic
strict addition requires exact identity plus agreement with the independently
calibrated MartialBot lead within 1 cm.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'ring_reach_probe.json'
ADD=ROOT/'profile_supplements'/'ring_reach_additions.jsonl'
BASE='https://www.ringmagazine.com'
DIR=BASE+'/fighters'
UA='Mozilla/5.0 AppwizaRingReach/2.0'
SITEMAP_SEEDS=[BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml',BASE+'/robots.txt']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def directory(raw):
    soup=BeautifulSoup(raw,'lxml');out={}
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(BASE,a['href']).split('#')[0]
        if '/fighters/' not in urllib.parse.urlsplit(u).path:continue
        if urllib.parse.urlsplit(u).path.rstrip('/')=='/fighters':continue
        label=' '.join(a.stripped_strings).strip()
        h=a.find(['h2','h3','h4'])
        if h:label=' '.join(h.stripped_strings).strip()
        label=re.sub(r'\s+\d+[-–]\d+[-–]\d+.*
def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings).strip() if h else ''
    text=' '.join(soup.stripped_strings)
    # Ring renders "Reach 68\" 173 cm" and sometimes just one unit.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]',text,re.I)
    return name,(round(float(m.group(1))*2.54,2) if m else None)

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    discovered,diag=sitemap_urls()
    # Fall back to the public directory, but don't require it to render cards.
    try:
        final,raw=fetch(DIR)
        idx=directory(raw)
    except Exception as e:
        idx={}
        diag.append({'url':DIR,'status':'directory_error','error':type(e).__name__+': '+str(e)[:180]})

    # Sitemap URLs often contain opaque suffixes. Fetch all bounded fighter URLs
    # and match the rendered H1 to the missing target set.
    candidates=set(discovered)
    for items in idx.values():
        for item in items:candidates.add(item['url'])

    def one(u):
        rec={'url':u}
        try:
            final,raw=fetch(u,2_000_000);h,reach=parse(raw);key=nk(h)
            rec.update({'url':final,'h1':h,'target_key':key if key in targets else None,'reach_cm':reach})
            if key not in targets:rec['status']='not_missing_target'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        allrows=list(ex.map(one,sorted(candidates)))

    rows=[x for x in allrows if x.get('target_key') or x.get('status')=='fetch_error']
    leads=[x for x in rows if x.get('status')=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    adds=[]
    for x in corr:
        t=targets[x['target_key']];m=mb[x['target_key']]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        adds.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'the_ring_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'the_ring','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_ring_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in adds:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'sitemap_fighter_urls':len(discovered),'candidate_fighter_urls':len(candidates),
      'matched_target_profiles':sum(1 for x in rows if x.get('target_key')),'status_counts':counts,
      'lead_count':len(leads),'martialbot_corroborated':len(corr),'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':targets[x['target_key']]['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':targets[x['target_key']]['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Explicit public The Ring fighter profile discovered from Ring sitemap/directory; exact rendered H1 identity. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('missing_targets','sitemap_fighter_urls','candidate_fighter_urls','matched_target_profiles','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
,'',label).strip()
        if not label:continue
        out.setdefault(nk(label),[]).append({'name':label,'url':u})
    return {k:list({x['url']:x for x in v}.values()) for k,v in out.items()}

def xml_locs(raw):
    try:
        root=ET.fromstring(raw)
    except Exception:
        return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def sitemap_urls():
    """Discover Ring fighter URLs from sitemap/robots structure."""
    urls=[];diag=[];queue=list(SITEMAP_SEEDS);seen=set()
    while queue and len(seen)<80:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw=fetch(u,10_000_000)
            txt=raw.decode('utf-8','replace')
            locs=xml_locs(raw)
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*
def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings).strip() if h else ''
    text=' '.join(soup.stripped_strings)
    # Ring renders "Reach 68\" 173 cm" and sometimes just one unit.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]',text,re.I)
    return name,(round(float(m.group(1))*2.54,2) if m else None)

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    final,raw=fetch(DIR)
    idx=directory(raw)
    jobs=[]
    for key,t in targets.items():
        xs=idx.get(key,[])
        if len(xs)==1:jobs.append((key,t,xs[0]))

    def one(job):
        key,t,item=job
        rec={'id':t['id'],'name':t['name'],'url':item['url'],'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw=fetch(item['url'],2_000_000);h,reach=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':reach})
            if nk(h)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,jobs))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    adds=[]
    for x in corr:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        adds.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'the_ring_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'the_ring','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_ring_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in adds:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'directory_name_keys':len(idx),'exact_directory_targets':len(jobs),'status_counts':counts,
      'lead_count':len(leads),'martialbot_corroborated':len(corr),'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':x['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'rows':rows,
      'policy':'Explicit public The Ring directory profile + exact H1 identity. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('missing_targets','directory_name_keys','exact_directory_targets','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
,txt)
            diag.append({'url':u,'status':'ok','locs':len(locs),'robots_sitemaps':robots[:20],'bytes':len(raw)})
            for x in [*robots,*locs]:
                low=x.lower()
                path=urllib.parse.urlsplit(x).path
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x)
                elif re.search(r'/fighters/[^/?#]+',path,re.I):
                    urls.append(x.split('#')[0])
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings).strip() if h else ''
    text=' '.join(soup.stripped_strings)
    # Ring renders "Reach 68\" 173 cm" and sometimes just one unit.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*["″]',text,re.I)
    return name,(round(float(m.group(1))*2.54,2) if m else None)

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}

    final,raw=fetch(DIR)
    idx=directory(raw)
    jobs=[]
    for key,t in targets.items():
        xs=idx.get(key,[])
        if len(xs)==1:jobs.append((key,t,xs[0]))

    def one(job):
        key,t,item=job
        rec={'id':t['id'],'name':t['name'],'url':item['url'],'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw=fetch(item['url'],2_000_000);h,reach=parse(raw)
            rec.update({'url':final,'h1':h,'reach_cm':reach})
            if nk(h)!=key:rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(reach))<=1
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,jobs))
    leads=[x for x in rows if x['status']=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    adds=[]
    for x in corr:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        adds.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'the_ring_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'the_ring','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_ring_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in adds:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'directory_name_keys':len(idx),'exact_directory_targets':len(jobs),'status_counts':counts,
      'lead_count':len(leads),'martialbot_corroborated':len(corr),'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':x['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':x['name'],'ring_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'rows':rows,
      'policy':'Explicit public The Ring directory profile + exact H1 identity. Automatic addition only when independent exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('missing_targets','directory_name_keys','exact_directory_targets','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
