#!/usr/bin/env python3
"""Corroborate missing reaches from Boxing Showtimes public fighter profiles.

Boxing Showtimes currently renders profile fields such as "Reach: 78 cm" while
its prose biography and known-reference fighters show the numeric value is
actually inches. Therefore this source is NEVER accepted alone.

Policy:
- discover only public fighter-profile links from the site's fighter directory;
- exact normalized H1 identity to a currently missing target;
- normalize Reach values in 48..110 as inches -> cm, and 120..270 as cm;
- auto-add only when an independent exact-identity MartialBot reach agrees
  within 1 cm;
- conflicting values are quarantined; existing strict reach is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,subprocess,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxingshowtimes_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxingshowtimes_reach_additions.jsonl'
BASE='https://boxingshowtimes.com'
DIRECTORIES=[
 BASE+'/boxers',
 'https://sitemap.boxingshowtimes.com/boxers',
 'https://ofbiz.boxingshowtimes.com/boxers'
]
UA='Mozilla/5.0 AppwizaBoxingShowtimesReach/2.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    try:
        with urllib.request.urlopen(req,timeout=35) as r:
            raw=r.read(limit+1)
            if len(raw)>limit:raise ValueError('response too large')
            return r.geturl(),raw
    except Exception as first:
        # The sitemap subdomain intermittently rejects Python/OpenSSL while
        # normal browser/curl clients work. Fall back to curl without disabling
        # certificate verification.
        p=subprocess.run([
          'curl','-L','--compressed','--silent','--show-error','--fail',
          '--connect-timeout','12','--max-time','35','-A',UA,
          '-H','Accept-Language: en-US,en;q=0.8',url
        ],capture_output=True,timeout=45)
        if p.returncode!=0:
            raise RuntimeError(type(first).__name__+': '+str(first)[:100]+'; curl: '+p.stderr.decode('utf-8','replace')[:140])
        raw=p.stdout
        if len(raw)>limit:raise ValueError('response too large')
        return url,raw

def profile_links(raw,base):
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(base,a['href']).split('#')[0]
        p=urllib.parse.urlsplit(u)
        host=(p.hostname or '').lower()
        if not host.endswith('boxingshowtimes.com'):continue
        if not re.fullmatch(r'/boxers/[^/?#]+/?',p.path,re.I):continue
        if p.path.rstrip('/').lower()=='/boxers':continue
        out.append(u.rstrip('/'))
    return list(dict.fromkeys(out))

def discover(targets):
    urls=[];diag=[]
    for d in DIRECTORIES:
        try:
            final,raw=fetch(d);xs=profile_links(raw,final);urls.extend(xs)
            diag.append({'url':d,'status':'ok','links':len(xs),'bytes':len(raw)})
        except Exception as e:
            diag.append({'url':d,'status':'error','error':type(e).__name__+': '+str(e)[:180]})

    # The public fighter directory supports a name search even when the main
    # page is client-rendered. Probe exact missing names against the sitemap
    # directory; only discovered profile links are later trusted.
    def search_one(item):
        key,t=item
        u='https://sitemap.boxingshowtimes.com/boxers?search='+urllib.parse.quote(t['name'])
        try:
            final,raw=fetch(u,2_000_000);xs=profile_links(raw,final)
            return {'url':u,'status':'ok_search','links':len(xs),'profile_urls':xs}
        except Exception as e:
            return {'url':u,'status':'search_error','error':type(e).__name__+': '+str(e)[:160],'profile_urls':[]}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        searched=list(ex.map(search_one,targets.items()))
    for x in searched:
        urls.extend(x.pop('profile_urls',[]))
    diag.extend(searched)

    # Normalize duplicate subdomain versions by path; prefer primary host.
    bypath={}
    for u in urls:
        p=urllib.parse.urlsplit(u)
        path=p.path.rstrip('/')
        cur=bypath.get(path)
        if cur is None or p.hostname=='boxingshowtimes.com':bypath[path]=u
    return list(bypath.values()),diag

def normalize_measure(v):
    try:x=float(v)
    except Exception:return None,None
    if 48<=x<=110:
        return round(x*2.54,2),'numeric_profile_value_treated_as_inches'
    if 120<=x<=270:
        return round(x,2),'numeric_profile_value_treated_as_cm'
    return None,None

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1');name=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else ''
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    text=' '.join(strings)

    rawvals=[]
    # Inline/profile text: Reach: 76 cm or Reach 76
    for m in re.finditer(r'\bReach\s*:\s*(\d+(?:\.\d+)?)\s*(?:cm|in(?:ches)?|["″])?',text,re.I):
        rawvals.append(float(m.group(1)))
    # Label/value DOM split. Only the value AFTER Reach belongs to reach;
    # the previous DOM string is commonly the fighter's height.
    for i,x in enumerate(strings):
        if x.casefold().rstrip(':')!='reach':continue
        if i+1<len(strings):
            nxt=strings[i+1]
            if nxt not in {'-','—','N/A','n/a'}:
                m=re.search(r'(\d+(?:\.\d+)?)',nxt)
                if m:rawvals.append(float(m.group(1)))

    converted=[]
    for v in rawvals:
        cm,mode=normalize_measure(v)
        if cm is not None:converted.append((cm,mode,v))
    vals=sorted({round(x[0],2) for x in converted})
    reach=vals[0] if len(vals)==1 else None

    # Optional prose cross-check on same page: "78 in ... reach".
    bio=[]
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:in|inch|inches)\s+reach\b',text,re.I):
        val=round(float(m.group(1))*2.54,2)
        if 120<=val<=270:bio.append(val)
    bio=sorted(set(bio))
    return name,reach,converted,bio

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mbobj=json.loads(MB.read_text()) if MB.exists() else {}
    mb={nk(x['name']):x for x in mbobj.get('leads',[]) if x.get('reach_cm') is not None}
    urls,diag=discover(targets)

    def one(u):
        rec={'url':u}
        try:
            final,raw=fetch(u,2_500_000);name,reach,converted,bio=parse(raw);key=nk(name)
            rec.update({'url':final,'h1':name,'target_key':key if key in targets else None,
                        'reach_cm':reach,'converted_candidates':converted,'bio_reach_cm':bio})
            if key not in targets:rec['status']='not_missing_target'
            elif reach is None:rec['status']='no_unique_reach'
            elif bio and any(abs(float(x)-float(reach))>1 for x in bio):
                rec['status']='same_page_conflict'
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

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        allrows=list(ex.map(one,urls))

    rows=[x for x in allrows if x.get('target_key') or x.get('status')=='fetch_error']
    leads=[x for x in rows if x.get('status')=='lead']
    corr=[x for x in leads if x.get('agrees_martialbot') is True]
    conflicts=[x for x in leads if x.get('agrees_martialbot') is False]

    additions=[]
    for x in corr:
        t=targets[x['target_key']]
        vals=[float(x['reach_cm']),float(x['martialbot_reach_cm'])]
        reach=round(statistics.median(vals),2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxingshowtimes_martialbot_reach_consensus',
            'exact_identity':True,'agreement_tolerance_cm':1,'fields':{'reach_cm':reach},
            'sources':[
              {'source':'boxing_showtimes','url':x['url'],'reported_reach_cm':x['reach_cm'],
               'normalization':'profile numeric values 48..110 interpreted as inches because known profiles and biography prose use the same values as inches'},
              {'source':'martialbot','url':x['martialbot_url'],'reported_reach_cm':x['martialbot_reach_cm']}
            ]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxingshowtimes_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r.get('status')]=counts.get(r.get('status'),0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_profiles':len(urls),'profiles_fetched':len(allrows),
      'matched_missing_targets':sum(1 for x in rows if x.get('target_key')),
      'status_counts':counts,'lead_count':len(leads),'martialbot_corroborated':len(corr),
      'martialbot_conflicts':len(conflicts),
      'corroborated_profiles':[{'name':targets[x['target_key']]['name'],
        'boxingshowtimes_reach_cm':x['reach_cm'],'martialbot_reach_cm':x['martialbot_reach_cm'],
        'url':x['url']} for x in corr],
      'conflicting_profiles':[{'name':targets[x['target_key']]['name'],
        'boxingshowtimes_reach_cm':x['reach_cm'],'martialbot_reach_cm':x.get('martialbot_reach_cm'),
        'url':x['url']} for x in conflicts],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Boxing Showtimes is corroboration-only because current profile UI mislabels inch values as cm. Exact H1 identity plus plausible normalized value and independent MartialBot agreement within 1 cm required. Same-page prose conflicts and cross-source conflicts quarantined.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in (
      'missing_targets','discovered_profiles','profiles_fetched','matched_missing_targets',
      'status_counts','lead_count','martialbot_corroborated','martialbot_conflicts')},indent=2))

if __name__=='__main__':main()
