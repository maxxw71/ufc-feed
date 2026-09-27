#!/usr/bin/env python3
"""Probe FiteQuant for missing strict boxer reaches and corroborate MartialBot.

Discovery uses public sitemap/listing URLs only. An automatic addition requires
exact identity on both FiteQuant and MartialBot and reach agreement <=1 cm.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'fitequant_reach_probe.json'
ADD=ROOT/'profile_supplements'/'fitequant_reach_additions.jsonl'
BASE='https://fitequant.com'
UA='Mozilla/5.0 AppwizaFiteQuantReach/1.0'
SITEMAPS=[BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml']
LISTS=[BASE+'/fighters']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
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

def html_links(raw):
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(BASE,a['href']).split('#')[0]
        if re.search(r'/fighters/\d+-[^/?#]+',urllib.parse.urlsplit(u).path,re.I):out.append(u)
    return list(dict.fromkeys(out))

def discover():
    queue=list(SITEMAPS);seen=set();urls=[];diag=[]
    while queue and len(seen)<40:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ct=fetch(u);xs=locs(raw)
            diag.append({'url':u,'status':'ok','locs':len(xs),'bytes':len(raw)})
            for x in xs:
                if 'sitemap' in x.lower() and x.lower().endswith('.xml'):
                    if x not in seen:queue.append(x)
                elif re.search(r'/fighters/\d+-[^/?#]+',urllib.parse.urlsplit(x).path,re.I):urls.append(x)
        except Exception as e:diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    if not urls:
        for u in LISTS:
            try:
                final,raw,ct=fetch(u);xs=html_links(raw);urls.extend(xs)
                diag.append({'url':u,'status':'ok_html','links':len(xs),'bytes':len(raw)})
            except Exception as e:diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def slug_name(url):
    last=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
    m=re.match(r'\d+-(.+)$',last)
    return m.group(1) if m else last

def parse(raw):
    soup=BeautifulSoup(raw,'lxml');h=soup.find('h1')
    display=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    # Prefer the explicit Full name field for identity when present.
    mfull=re.search(r'\bFull name\s+(.+?)(?=\s+Age\s+|\s+DOB\s+|\s+Nationality\s+)',text,re.I)
    fullname=mfull.group(1).strip() if mfull else display
    m=re.search(r'\bReach\s+(N/A|\d+(?:\.\d+)?\s*cm)\b',text,re.I)
    reach=None
    if m and not m.group(1).upper().startswith('N/'):
        reach=float(re.search(r'\d+(?:\.\d+)?',m.group(1)).group())
    return display,fullname,reach

def exact_identity(target,display,fullname):
    k=nk(target)
    if nk(display)==k or nk(fullname)==k:return True
    # Permit common suffix/full-name expansion only when target normalized name
    # is a full token-prefix of the explicit full name, not a fuzzy surname match.
    return nk(fullname).startswith(k) and len(k)>=8

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}
    urls,diag=discover()
    cmap={}
    for u in urls:
        key=nk(slug_name(u));cmap.setdefault(key,[]).append(u)

    # Direct exact slug matches first. FiteQuant slugs are normalized names.
    jobs=[]
    for key,t in targets.items():
        cand=list(dict.fromkeys(cmap.get(key,[])))
        if len(cand)==1:jobs.append((key,t,cand[0]))
    def one(job):
        key,t,u=job
        rec={'id':t['id'],'name':t['name'],'url':u,'strict_bout_appearances':t.get('strict_bout_appearances')}
        try:
            final,raw,ct=fetch(u,2_000_000);display,full,reach=parse(raw)
            rec.update({'url':final,'h1':display,'full_name':full,'reach_cm':reach})
            if not exact_identity(t['name'],display,full):rec['status']='identity_mismatch'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-reach)<=1
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,jobs))
    leads=[x for x in rows if x['status']=='lead'];corr=[x for x in leads if x.get('agrees_martialbot') is True]
    additions=[]
    for x in corr:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'fitequant_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'fitequant','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_fitequant_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_fighter_urls':len(urls),'exact_slug_targets':len(jobs),'status_counts':counts,
      'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'corroborated_profiles':[{'name':x['name'],'fitequant_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'FiteQuant public sitemap/listing exact slug + exact/explicit full-name identity. Automatic addition only when exact-identity MartialBot agrees within 1 cm.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('missing_targets','discovered_fighter_urls','exact_slug_targets','status_counts','lead_count','martialbot_corroborated')},indent=2))
if __name__=='__main__':main()
