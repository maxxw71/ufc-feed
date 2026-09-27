#!/usr/bin/env python3
"""Recover missing boxer reach from archived BoxingScene fighter profiles.

Uses Wayback CDX to discover exact archived /fighters/ profile URLs, then parses
only preserved snapshots. This avoids the live site's WAF without bypassing it.
Discovery is strict: exact normalized profile H1 + plausible numeric reach.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
MB=ROOT/'profile_supplements'/'martialbot_missing_reach_inventory.json'
OUT=ROOT/'profile_supplements'/'boxingscene_wayback_reach_probe.json'
ADD=ROOT/'profile_supplements'/'boxingscene_wayback_reach_additions.jsonl'
UA='Mozilla/5.0 AppwizaBoxingSceneWaybackReach/1.0'
PREFIXES=[
 'https://www.boxingscene.com/fighters/',
 'https://boxingscene.com/fighters/',
 'http://www.boxingscene.com/fighters/',
 'http://boxingscene.com/fighters/'
]

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def slugkey(url):
    p=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
    return nk(p)

def get(url,limit=4_000_000,timeout=50):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cdx(prefix):
    q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode([
      ('url',prefix+'*'),('output','json'),('filter','statuscode:200'),
      ('fl','timestamp,original,statuscode,mimetype,digest'),('limit','10000'),
      ('collapse','urlkey')
    ])
    final,raw=get(q,10_000_000,90)
    x=json.loads(raw.decode('utf-8','replace'))
    return q,(x[1:] if isinstance(x,list) and x else [])

def parse(raw):
    soup=BeautifulSoup(raw,'lxml');h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    patterns=[
      r'\bReach\s+(\d+(?:\.\d+)?)\s*[″"]?\s*/\s*(\d+(?:\.\d+)?)\s*cm\b',
      r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*cm\b'
    ]
    m=re.search(patterns[0],text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(patterns[1],text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s*:?\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in|[″"])\b',text,re.I)
    return name,(round(float(m.group(1))*2.54,2) if m else None)

def snapshot_variants(ts,orig):
    out=[]
    for scheme in ('https://','http://'):
        p=urllib.parse.urlsplit(orig)
        for host in (p.hostname or '',('www.'+(p.hostname or '').removeprefix('www.'))):
            if not host:continue
            o=urllib.parse.urlunsplit((scheme[:-3],host,p.path,p.query,''))
            u=f'https://web.archive.org/web/{ts}id_/{o}'
            if u not in out:out.append(u)
    return out

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    mb={}
    try:
        obj=json.loads(MB.read_text());mb={nk(x['name']):x for x in obj.get('leads',[])}
    except Exception:pass

    raw=[];diag=[]
    for p in PREFIXES:
        try:
            q,rows=cdx(p);raw.extend(rows);diag.append({'prefix':p,'rows':len(rows),'error':None})
        except Exception as e:diag.append({'prefix':p,'rows':0,'error':type(e).__name__+': '+str(e)[:180]})
    # newest capture for each normalized profile slug
    capmap={}
    for r in raw:
        if len(r)<5:continue
        ts,orig,status,mime,digest=map(str,r[:5]);key=slugkey(orig)
        if key not in targets:continue
        old=capmap.get(key)
        if old is None or ts>old['timestamp']:
            capmap[key]={'timestamp':ts,'original':orig,'digest':digest}

    def worker(item):
        key,cap=item;t=targets[key]
        rec={'id':t['id'],'name':t['name'],'timestamp':cap['timestamp'],'original':cap['original']}
        errors=[]
        for u in snapshot_variants(cap['timestamp'],cap['original']):
            try:
                final,raw=get(u,2_000_000,35);h,r=parse(raw)
                rec.update({'snapshot_url':final,'h1':h,'reach_cm':r})
                if nk(h)!=key:
                    errors.append('identity:'+repr(h));continue
                if r is None:
                    errors.append('no_reach');continue
                if not 120<=r<=270:
                    errors.append('implausible:'+str(r));continue
                rec['status']='lead'
                m=mb.get(key)
                if m and m.get('reach_cm') is not None:
                    rec['martialbot_reach_cm']=m['reach_cm']
                    rec['agrees_martialbot']=abs(float(m['reach_cm'])-float(r))<=1
                return rec
            except Exception as e:errors.append(type(e).__name__+': '+str(e)[:120])
        rec['status']='fetch_or_parse_error';rec['errors']=errors[:8];return rec

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        rows=list(ex.map(worker,capmap.items()))
    leads=[x for x in rows if x.get('status')=='lead']
    corroborated=[x for x in leads if x.get('agrees_martialbot') is True]
    additions=[]
    for x in corroborated:
        t=targets[nk(x['name'])];reach=round((float(x['reach_cm'])+float(x['martialbot_reach_cm']))/2,2)
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'boxingscene_wayback_plus_martialbot_consensus','fields':{'reach_cm':reach},
             'exact_identity':True,'agreement_tolerance_cm':1,
             'sources':[{'source':'boxingscene_wayback','url':x['snapshot_url'],'reported_reach_cm':x['reach_cm']},
                        {'source':'martialbot','url':mb[nk(x['name'])]['url'],'reported_reach_cm':x['martialbot_reach_cm']}]}],
          'conflicts':{},'quality':'two_source_reach_consensus_boxingscene_archive_martialbot_exact_identity',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'raw_cdx_rows':len(raw),'matched_archived_profile_urls':len(capmap),'status_counts':counts,
      'lead_count':len(leads),'martialbot_corroborated':len(corroborated),
      'corroborated_profiles':[{'name':x['name'],'boxingscene_reach_cm':x['reach_cm'],
                                'martialbot_reach_cm':x['martialbot_reach_cm'],'snapshot_url':x['snapshot_url']} for x in corroborated],
      'cdx_diagnostics':diag,'rows':rows,
      'policy':'Archived BoxingScene exact profile slug + exact H1 + numeric reach. Automatic addition only when an independent exact-identity MartialBot reach agrees within 1 cm.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','raw_cdx_rows','matched_archived_profile_urls','status_counts','lead_count','martialbot_corroborated')},indent=2))
if __name__=='__main__':main()
