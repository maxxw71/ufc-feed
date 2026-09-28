#!/usr/bin/env python3
"""Recover missing reach from official Salita Promotions fighter profiles.

Discovery is restricted to the official Salita boxer directory. Candidate
profiles must resolve to an exact H1 identity in the strict sample and expose
an explicit Tale-of-the-Tape Reach value. Existing reach is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,statistics,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'salita_reach_probe.json'
ADD=ROOT/'profile_supplements'/'salita_reach_additions.jsonl'
BASE='https://www.salitapromotions.com'
SEEDS=[BASE+'/boxers?status=active',BASE+'/boxers']
UA='Mozilla/5.0 AppwizaSalitaReach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=3_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r"(\d+)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?",txt)
    if m:return round((float(m.group(1))*12+float(m.group(2) or 0))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*"',txt)
    if m:return round(float(m.group(1))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    return round(float(m.group(1)),2) if m else None

def discover():
    urls={};diag=[]
    for seed in SEEDS:
        try:
            final,raw=fetch(seed);soup=BeautifulSoup(raw,'lxml');n=0
            for a in soup.find_all('a',href=True):
                u=urllib.parse.urljoin(final,a['href']).split('#')[0]
                p=urllib.parse.urlsplit(u)
                if p.hostname not in {'salitapromotions.com','www.salitapromotions.com'}:continue
                if not re.fullmatch(r'/boxers/[A-Za-z0-9._~-]+/?',p.path):continue
                urls[u.rstrip('/')]=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
                n+=1
            diag.append({'seed':seed,'status':'ok','links_seen':n,'unique_profiles_so_far':len(urls)})
        except Exception as e:
            diag.append({'seed':seed,'status':'error','error':type(e).__name__+': '+str(e)[:180]})
    return urls,diag

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    name=re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else None
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    vals=[]
    for i,x in enumerate(strings):
        if x.casefold().rstrip(':')!='reach':continue
        for j in (i+1,i-1):
            if 0<=j<len(strings):
                v=cm_measure(strings[j])
                if v is not None:vals.append(v)
    # Fallback for rendered "Reach 5'10\" 178 cm" in one text block.
    if not vals:
        text=' '.join(strings)
        m=re.search(r'\bReach\s+([^A-Za-z]{0,8}\d[^A-Za-z]{0,20}(?:cm|["″]))',text,re.I)
        if m:
            v=cm_measure(m.group(1))
            if v is not None:vals.append(v)
    reach=None
    if vals and max(vals)-min(vals)<=1:reach=round(statistics.median(vals),2)
    return name,reach,vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    urls,diag=discover()

    def one(item):
        url,label=item;rec={'url':url,'directory_label':label}
        try:
            final,raw=fetch(url);name,reach,vals=parse(raw);key=nk(name)
            rec.update({'url':final,'profile_name':name,'target_key':key if key in targets else None,
                        'reach_cm':reach,'reach_candidates_cm':vals})
            if key not in targets:rec['status']='not_missing_target'
            elif reach is None:rec['status']='no_reach'
            elif not 120<=reach<=270:rec['status']='implausible_reach'
            else:rec['status']='accepted'
        except Exception as e:
            rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        rows=list(ex.map(one,urls.items()))

    additions=[]
    for r in rows:
        if r.get('status')!='accepted':continue
        t=targets[r['target_key']];reach=r['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({
          'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'salita_official_tale_of_tape','url':r['url'],
                       'fields':{'reach_cm':reach},'exact_identity':True}],
          'conflicts':{},'quality':'official_promoter_tale_of_tape_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
        })

    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for r in rows:counts[r.get('status')]=counts.get(r.get('status'),0)+1
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'discovered_profiles':len(urls),'status_counts':counts,'accepted':len(additions),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],'url':x['evidence'][0]['url']} for x in additions],
      'discovery_diagnostics':diag,'rows':rows,
      'policy':'Official Salita boxer directory/profile only; exact H1 identity; explicit Tale-of-the-Tape Reach; plausible numeric range; missing reach only; never overwrite.'
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','discovered_profiles','status_counts','accepted')},indent=2))

if __name__=='__main__':main()
