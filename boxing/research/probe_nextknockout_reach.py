#!/usr/bin/env python3
"""Probe NextKnockout public fighter profiles for missing boxing reach.

NextKnockout is treated as a third-party structured source. Discovery comes
from robots/sitemap/listing surfaces only; numeric IDs are never guessed.
A lead requires exact rendered fighter identity plus an explicit Reach field.
Automatic additions require agreement within 1 cm with at least one independent
persisted exact-identity source (MartialBot, Ready To Fight, official WBA,
BoxNow, or Boxing Data). Conflicts are retained for audit and never averaged.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,gzip,json,re,statistics,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
RTF=ROOT/'profile_supplements'/'ready_to_fight_reach_probe.json'
WBA=ROOT/'profile_supplements'/'wba_direct_reach_probe.json'
BOXNOW=ROOT/'profile_supplements'/'boxnow_reach_probe.json'
BOXDATA=ROOT/'profile_supplements'/'boxingdata_reach_probe.json'
OUT=ROOT/'profile_supplements'/'nextknockout_reach_probe.json'
ADD=ROOT/'profile_supplements'/'nextknockout_reach_additions.jsonl'
BASE='https://www.nextknockout.com'
UA='Mozilla/5.0 AppwizaNextKnockoutReach/1.0'
SEEDS=[BASE+'/robots.txt',BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap-index.xml',BASE+'/fighters']

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=12_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw,r.headers.get('content-type','')

def xml_locs(raw):
    if raw[:2]==b'\x1f\x8b':
        try:raw=gzip.decompress(raw)
        except Exception:return []
    try:root=ET.fromstring(raw)
    except Exception:return []
    return [(x.text or '').strip() for x in root.iter() if x.tag.endswith('loc') and (x.text or '').strip()]

def fighter_slug_key(url):
    p=urllib.parse.urlsplit(url)
    parts=[x for x in p.path.split('/') if x]
    if len(parts)<2 or parts[0].lower()!='fighters':return None
    slug=parts[1]
    slug=re.sub(r'-\d+$','',slug)
    return nk(urllib.parse.unquote(slug).replace('-',' '))

def discover(targets):
    urls=[];diag=[];queue=list(SEEDS);seen=set()
    while queue and len(seen)<120:
        u=queue.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            final,raw,ctype=fetch(u)
            text=raw.decode('utf-8','replace')
            locs=xml_locs(raw)
            robots=re.findall(r'(?im)^\s*Sitemap:\s*(https?://\S+)\s*$',text)
            found=[]
            for x in [*robots,*locs]:
                low=x.lower()
                if 'sitemap' in low and x not in seen and x not in queue:
                    queue.append(x);continue
                key=fighter_slug_key(x)
                if key in targets:found.append(x.split('#')[0])
            if not locs:
                soup=BeautifulSoup(raw,'lxml')
                for a in soup.find_all('a',href=True):
                    x=urllib.parse.urljoin(final,a['href']).split('#')[0]
                    key=fighter_slug_key(x)
                    if key in targets:found.append(x)
            urls.extend(found)
            diag.append({'url':u,'status':'ok','content_type':ctype,'locs':len(locs),
                         'robots_sitemaps':robots[:20],'matching_target_urls':len(found),'bytes':len(raw)})
        except Exception as e:
            diag.append({'url':u,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return list(dict.fromkeys(urls)),diag

def cm_measure(v):
    if v is None:return None
    s=str(v).replace('½','.5').replace('¼','.25').replace('¾','.75').replace('″','"').replace('”','"')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s,re.I)
    if m:
        x=float(m.group(1))
        if 120<=x<=270:return round(x,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:["]|in(?:ches)?)\b',s,re.I)
    if m:
        x=round(float(m.group(1))*2.54,2)
        if 120<=x<=270:return x
    return None

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=' '.join(h.stripped_strings).strip() if h else ''
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    height=reach=None
    for i,s in enumerate(strings):
        key=s.casefold().rstrip(':')
        if key=='height' and i+1<len(strings):
            height=cm_measure(strings[i+1])
        if key=='reach' and i+1<len(strings):
            nxt=strings[i+1]
            if nxt not in {'-','—','N/A','n/a'}:reach=cm_measure(nxt)
    return name,height,reach

def independent_map():
    out=defaultdict(list)
    def add(name,source,value,url=None):
        try:v=float(value)
        except Exception:return
        if not name or not 120<=v<=270:return
        out[nk(name)].append({'source':source,'reach_cm':v,'url':url})
    if MB.exists():
        j=json.loads(MB.read_text())
        for x in j.get('leads') or []:add(x.get('name'),'martialbot',x.get('reach_cm'),x.get('url'))
    if RTF.exists():
        j=json.loads(RTF.read_text())
        for x in j.get('rows') or []:
            if x.get('status')=='lead':add(x.get('name'),'ready_to_fight',x.get('reach_cm'),x.get('url'))
    if WBA.exists():
        j=json.loads(WBA.read_text())
        for x in j.get('rows') or []:
            if x.get('status')=='accepted':add(x.get('target_name') or x.get('name'),'wba_official',x.get('reach_cm'),x.get('url'))
    if BOXNOW.exists():
        j=json.loads(BOXNOW.read_text())
        for x in j.get('rows') or []:
            if x.get('status')=='lead':add(x.get('name') or x.get('h1'),'boxnow',x.get('reach_cm'),x.get('url'))
    if BOXDATA.exists():
        j=json.loads(BOXDATA.read_text())
        for x in j.get('leads') or []:add(x.get('name'),'boxingdata',x.get('reach_cm'),x.get('url'))
    return out

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    refs=independent_map()
    urls,diag=discover(targets)

    def one(u):
        rec={'url':u,'slug_key':fighter_slug_key(u)}
        try:
            final,raw,ctype=fetch(u,2_500_000);name,height,reach=parse(raw);key=nk(name)
            rec.update({'url':final,'h1':name,'target_key':key if key in targets else None,
                        'height_cm':height,'reach_cm':reach})
            if key not in targets:rec['status']='identity_mismatch';return rec
            target_height=None
            try:target_height=float((targets[key].get('present') or {}).get('height_cm'))
            except Exception:target_height=None
            if target_height is not None and height is not None and abs(target_height-height)>2:
                rec['status']='identity_mismatch_physical';rec['target_height_cm']=target_height;return rec
            if reach is None:rec['status']='no_reach';return rec
            rec['independent_sources']=refs.get(key) or []
            rec['agreeing_sources']=[x for x in rec['independent_sources'] if abs(float(x['reach_cm'])-float(reach))<=1]
            rec['conflicting_sources']=[x for x in rec['independent_sources'] if abs(float(x['reach_cm'])-float(reach))>2]
            rec['status']='lead'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=10) as ex:rows=list(ex.map(one,urls))
    leads=[x for x in rows if x.get('status')=='lead']
    corr=[x for x in leads if x.get('agreeing_sources')]
    conflicts=[x for x in leads if not x.get('agreeing_sources') and x.get('conflicting_sources')]

    additions=[]
    for x in corr:
        key=x['target_key'];t=targets[key]
        vals=[float(x['reach_cm'])]+[float(s['reach_cm']) for s in x['agreeing_sources']]
        reach=round(statistics.median(vals),2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'nextknockout_cross_source_reach_consensus','fields':{'reach_cm':reach},
            'exact_identity':True,'agreement_tolerance_cm':1,
            'sources':[{'source':'nextknockout','url':x['url'],'reported_reach_cm':x['reach_cm']},
                       *x['agreeing_sources']]}],
          'conflicts':{},'quality':'two_source_reach_consensus_nextknockout_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x.get('status')]=counts.get(x.get('status'),0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_matching_urls':len(urls),'status_counts':counts,'lead_count':len(leads),
      'corroborated_additions':len(additions),'conflicts_quarantined':len(conflicts),
      'corroborated_profiles':[{'name':targets[x['target_key']]['name'],'nextknockout_reach_cm':x['reach_cm'],
        'agreeing_sources':x['agreeing_sources'],'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':targets[x['target_key']]['name'],'nextknockout_reach_cm':x['reach_cm'],
        'conflicting_sources':x['conflicting_sources'],'url':x['url']} for x in conflicts],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Public NextKnockout fighter profile discovered from public sitemap/listing only; exact rendered H1 identity and explicit Reach required. Third-party reach auto-merges only with at least one independent exact-identity persisted source within 1 cm; conflicts quarantined.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_matching_urls','status_counts','lead_count','corroborated_additions','conflicts_quarantined')},indent=2))

if __name__=='__main__':main()
