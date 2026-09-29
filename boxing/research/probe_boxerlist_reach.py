#!/usr/bin/env python3
"""Probe BoxerList public boxer profiles for missing reach.

BoxerList is a third-party structured source and is used only for corroboration.
Discovery is limited to its public sitemap/robots/listing surfaces; no numeric
IDs are guessed. Candidate URLs encode a name slug and numeric ID. A strict
addition requires:
- currently missing reach target;
- exact rendered profile identity;
- explicit plausible Reach field;
- independent exact-identity MartialBot agreement within 1 cm.
Conflicts are quarantined and existing strict reach is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,gzip,json,re,statistics,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxerlist_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxerlist_reach_additions.jsonl'
BASE='https://boxerlist.com'
UA='Mozilla/5.0 AppwizaBoxerListReach/1.0'
SEEDS=[
 BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml',
 BASE+'/robots.txt',BASE+'/boxer',BASE+'/en/boxer'
]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def xml_locs(raw):
    # BoxerList's boxer sitemap shards are served as .xml.gz.  Decompress by
    # magic bytes rather than trusting content-type/extension.
    if raw[:2]==b'\x1f\x8b':
        try:raw=gzip.decompress(raw)
        except Exception:return []
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def url_slug_key(url):
    p=urllib.parse.urlsplit(url)
    parts=[x for x in p.path.split('/') if x]
    # /boxer/name-slug/123 or /en/boxer/name-slug/123
    try:i=parts.index('boxer')
    except ValueError:return None
    if i+1>=len(parts):return None
    return nk(urllib.parse.unquote(parts[i+1]).replace('-',' '))

def discover(targets):
    urls=[];diag=[];queue=list(SEEDS);seen=set()
    while queue and len(seen)<100:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw=fetch(u)
            txt=raw.decode('utf-8','replace')
            locs=xml_locs(raw)
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',txt)
            page_urls=[]
            for x in [*robots,*locs]:
                low=x.lower()
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x);continue
                k=url_slug_key(x)
                if k in targets:page_urls.append(x.split('#')[0])
            # HTML listing fallback.
            if not locs:
                soup=BeautifulSoup(raw,'lxml')
                for a in soup.find_all('a',href=True):
                    x=urllib.parse.urljoin(final,a['href']).split('#')[0]
                    k=url_slug_key(x)
                    if k in targets:page_urls.append(x)
            urls.extend(page_urls)
            diag.append({'url':u,'status':'ok','locs':len(locs),'robots_sitemaps':robots[:20],
                         'matching_target_urls':len(page_urls),'bytes':len(raw)})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('”','"').replace('″','"').replace('“','"').replace('′',"'")
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
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
    h=soup.find('h1')
    name=re.sub(r"\\s*\\([^)]*\\)\\s*boxer\\s*$",'',h.get_text(' ',strip=True),flags=re.I).strip() if h else ''
    name=re.sub(r'\\s+boxer\\s*$','',name,flags=re.I).strip()
    strings=[re.sub(r'\\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\\s+',' ',x).strip()]
    vals=[];height=None
    for i,s in enumerate(strings):
        key=s.casefold().rstrip(':')
        if key=='height' and i+1<len(strings):
            v=cm_measure(strings[i+1])
            if v is not None and 120<=v<=250:height=v
        if key!='reach':continue
        # BoxerList renders fields as Height VALUE, Reach VALUE. Never inspect
        # the previous string: that is the height and caused false reach values
        # whenever Reach was "-".
        if i+1<len(strings):
            nxt=strings[i+1]
            if nxt not in {'-','—','N/A','n/a'}:
                v=cm_measure(nxt)
                if v is not None:vals.append(v)
    text=' '.join(strings)
    # Inline fallback must have a numeric value immediately after Reach.
    for m in re.finditer(r'\\bReach\\s*:?\\s*(\\d+(?:\\.\\d+)?\\s*(?:cm|["″]|in(?:ches)?))',text,re.I):
        v=cm_measure(m.group(1))
        if v is not None:vals.append(v)
    vals=sorted(set(round(v,2) for v in vals))
    return name,height,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[]) if x.get('reach_cm') is not None}

    urls,diag=discover(targets)

    def one(u):
        rec={'url':u,'slug_key':url_slug_key(u)}
        try:
            final,raw=fetch(u,2_500_000);name,page_height,reach,vals=parse(raw);key=nk(name)
            rec.update({'url':final,'h1':name,'target_key':key if key in targets else None,
                        'page_height_cm':page_height,'reach_cm':reach,'reach_values':vals})
            target_height=None
            if key in targets:
                try:target_height=float((targets[key].get('present') or {}).get('height_cm'))
                except Exception:target_height=None
            if key not in targets:rec['status']='identity_mismatch'
            elif target_height is not None and page_height is not None and abs(target_height-page_height)>2:
                rec['status']='identity_mismatch_physical'
                rec['target_height_cm']=target_height
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:
                rec['status']='lead'
                m=mb.get(key)
                if m:
                    rec['martialbot_reach_cm']=float(m['reach_cm'])
                    rec['martialbot_url']=m.get('url')
                    rec['agrees_martialbot']=abs(float(reach)-float(m['reach_cm']))<=1
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        rows=list(ex.map(one,urls))
    leads=[x for x in rows if x.get('status')=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    additions=[]
    for x in corr:
        key=x['target_key'];t=targets[key]
        reach=round(statistics.median([float(x['reach_cm']),float(x['martialbot_reach_cm'])]),2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxerlist_martialbot_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'boxerlist','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       {'source':'martialbot','url':x['martialbot_url'],'reported_reach_cm':x['martialbot_reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxerlist_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r.get('status')]=counts.get(r.get('status'),0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'missing_targets':len(targets),'discovered_matching_urls':len(urls),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':targets[x['target_key']]['name'],'boxerlist_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x['martialbot_reach_cm'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':targets[x['target_key']]['name'],'boxerlist_reach_cm':x['reach_cm'],
        'martialbot_reach_cm':x.get('martialbot_reach_cm'),'url':x['url']} for x in conflicts],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Public BoxerList profile discovered from sitemap/listing only; exact rendered identity and explicit plausible Reach required. Third-party values auto-merge only with independent exact-identity MartialBot agreement within 1 cm; conflicts quarantined.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_matching_urls','status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
