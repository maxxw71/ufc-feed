#!/usr/bin/env python3
"""Discover BoxNow fighter pages and probe missing reach values.

Discovery tries public sitemap endpoints first, then public boxer listing pages.
No guessed numeric IDs are used. A reach lead requires an exact normalized page
heading match and a plausible numeric reach. Discovery only; no automatic merge.
"""
from __future__ import annotations
import datetime as dt,json,re,subprocess,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxnow_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxnow_reach_additions.jsonl'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
UA='Mozilla/5.0 AppwizaBoxNowReach/2.0'
BASE='https://boxnow.biz'
SITEMAPS=[
 BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml',
 BASE+'/sitemaps.xml',BASE+'/sitemap/sitemap.xml'
]
LISTS=[BASE+'/boxers',BASE+'/fighters']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

_BOXNOW_IP=None

def doh_boxnow_ip():
    global _BOXNOW_IP
    if _BOXNOW_IP:return _BOXNOW_IP
    q='https://dns.google/resolve?'+urllib.parse.urlencode({'name':'boxnow.biz','type':'A'})
    req=urllib.request.Request(q,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=20) as r:
        obj=json.loads(r.read().decode('utf-8','replace'))
    ips=[str(x.get('data') or '') for x in obj.get('Answer') or [] if int(x.get('type') or 0)==1]
    if not ips:raise RuntimeError('DoH returned no A record for boxnow.biz')
    _BOXNOW_IP=ips[0]
    return _BOXNOW_IP

def fetch(url,limit=8_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    try:
        with urllib.request.urlopen(req,timeout=35) as r:
            raw=r.read(limit+1)
            if len(raw)>limit:raise ValueError('response too large')
            return r.geturl(),raw,r.headers.get('content-type','')
    except Exception as first:
        host=urllib.parse.urlsplit(url).hostname
        if host not in {'boxnow.biz','www.boxnow.biz'}:raise
        ip=doh_boxnow_ip()
        cmd=['curl','-L','--compressed','--silent','--show-error','--fail',
             '--connect-timeout','15','--max-time','35',
             '--resolve',f'{host}:443:{ip}','-A',UA,
             '-H','Accept-Language: en-US,en;q=0.8',
             '-w','\\n__APPWIZA_META__:%{http_code}|%{url_effective}|%{content_type}\\n',url]
        p=subprocess.run(cmd,capture_output=True,timeout=45)
        raw=p.stdout;marker=b'\\n__APPWIZA_META__:'
        if marker not in raw:
            raise RuntimeError('direct='+type(first).__name__+'; doh-curl='+p.stderr.decode('utf-8','replace')[:160])
        body,meta=raw.rsplit(marker,1)
        parts=meta.decode('utf-8','replace').strip().split('|',2)
        code=int(parts[0]) if parts and parts[0].isdigit() else 0
        if code!=200:raise RuntimeError(f'DoH curl HTTP {code}')
        if len(body)>limit:raise ValueError('response too large')
        return (parts[1] if len(parts)>1 else url),body,(parts[2] if len(parts)>2 else '')

def xml_locs(raw):
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [x.text.strip() for x in root.iter() if x.tag.endswith('loc') and x.text]

def boxer_links_from_html(raw):
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(BASE,a['href'])
        if re.search(r'/boxers?/[^/?#]+',urllib.parse.urlsplit(u).path,re.I):
            out.append(u.split('#')[0])
    return list(dict.fromkeys(out))

def discover():
    diag=[];urls=[]
    queue=list(SITEMAPS);seen=set()
    while queue and len(seen)<30:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ctype=fetch(u)
            locs=xml_locs(raw)
            diag.append({'url':u,'status':'ok','content_type':ctype,'locs':len(locs),'bytes':len(raw)})
            for x in locs:
                if re.search(r'sitemap.*\.xml(?:$|\?)',x,re.I):
                    if x not in seen:queue.append(x)
                elif re.search(r'/boxers?/[^/?#]+',urllib.parse.urlsplit(x).path,re.I):
                    urls.append(x)
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    if not urls:
        for u in LISTS:
            try:
                final,raw,ctype=fetch(u)
                xs=boxer_links_from_html(raw);urls.extend(xs)
                diag.append({'url':u,'status':'ok_html','links':len(xs),'bytes':len(raw)})
            except Exception as e:
                diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def page_name_and_reach(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    # Prefer cm if present and sensible. Some BoxNow pages have malformed "(0 cm)"
    # next to a valid inch value, so ignore zero-cm artifacts.
    m=re.search(r'\bREACH\s+(\d+(?:\.\d+)?)\s*["″]\s*\((\d+(?:\.\d+)?)\s*cm\)',text,re.I)
    if m:
        inches=float(m.group(1));cm=float(m.group(2))
        return name,(cm if 120<=cm<=270 else round(inches*2.54,2))
    m=re.search(r'\bREACH\s+(\d+(?:\.\d+)?)\s*["″]',text,re.I)
    if m:return name,round(float(m.group(1))*2.54,2)
    m=re.search(r'\bREACH\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    return name,(float(m.group(1)) if m else None)

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[])}
    urls,diag=discover()

    # Fetch the whole bounded official/public boxer directory and match the
    # rendered H1 identity, rather than pre-filtering by URL slug. Slugs can use
    # aliases, suffixes or alternate spellings and were silently hiding valid
    # missing-target profiles.
    def one(u):
        rec={'url':u}
        try:
            final,raw,ctype=fetch(u,2_000_000);h,r=page_name_and_reach(raw)
            key=nk(h)
            rec.update({'url':final,'h1':h,'target_key':key if key in targets else None,'reach_cm':r})
            if key not in targets:rec['status']='not_missing_target'
            elif r is None:rec['status']='no_reach'
            elif not 120<=r<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(r))<=1
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        allrows=list(ex.map(one,urls))

    rows=[x for x in allrows if x.get('target_key') or x.get('status') in {'fetch_error'}]
    leads=[]
    for rec in rows:
        if rec.get('status')!='lead':continue
        t=targets[rec['target_key']]
        leads.append({'id':t['id'],'name':t['name'],'reach_cm':rec['reach_cm'],'url':rec['url'],
          'strict_bout_appearances':t.get('strict_bout_appearances'),
          'martialbot_reach_cm':rec.get('martialbot_reach_cm'),
          'agrees_martialbot':rec.get('agrees_martialbot')})

    corroborated=[x for x in leads if x.get('agrees_martialbot') is True]
    additions=[]
    for x in corroborated:
        t=targets[nk(x['name'])];m=mb[nk(x['name'])]
        reach=round((float(x['reach_cm'])+float(m['reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxnow_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxnow','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':m['url'],'reported_reach_cm':m['reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxnow_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_reach_targets':len(targets),
      'discovered_boxer_urls':len(urls),'profiles_fetched':len(allrows),'matched_target_urls':sum(1 for x in rows if x.get('target_key')),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corroborated),
      'leads':leads,'discovery_diagnostics':diag,'rows':rows,
      'policy':'Public BoxNow current-domain sitemap/listing discovery; every discovered boxer page is fetched and matched by exact rendered H1 identity, not slug. Plausible numeric reach required. Automatic additions require independent exact-identity MartialBot agreement within 1 cm; no guessed IDs.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('missing_reach_targets','discovered_boxer_urls','profiles_fetched','matched_target_urls','status_counts','lead_count','martialbot_corroborated')},indent=2))

if __name__=='__main__':main()
