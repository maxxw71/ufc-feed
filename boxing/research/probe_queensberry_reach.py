#!/usr/bin/env python3
"""Backfill missing reach from official Queensberry Promotions fighter profiles.

Discovery uses the official boxer directory. Exact profile identity is required.
Only explicit numeric REACH values are accepted; n/a is ignored. Existing reach
is never overwritten.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'queensberry_reach_probe.json'
ADD=ROOT/'profile_supplements'/'queensberry_reach_additions.jsonl'
BASE='https://queensberry.co.uk'
UA='Mozilla/5.0 AppwizaQueensberryReach/1.0'
DIRECTORY=BASE+'/pages/boxers'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url,limit=5_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def discover():
    final,raw=fetch(DIRECTORY)
    soup=BeautifulSoup(raw,'lxml');out=[]
    for a in soup.find_all('a',href=True):
        u=urllib.parse.urljoin(final,a['href']).split('#')[0]
        p=urllib.parse.urlsplit(u)
        if p.hostname not in {'queensberry.co.uk','www.queensberry.co.uk'}:continue
        if re.fullmatch(r'/pages/[a-z0-9-]+',p.path,re.I) and p.path.lower()!='/pages/boxers':
            label=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
            out.append((u,label))
    uniq={}
    for u,l in out:uniq.setdefault(u,l)
    return [(u,l) for u,l in uniq.items()]

def parse(raw):
    soup=BeautifulSoup(raw,'lxml');h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    vals=[]
    # Queensberry: REACH 72” (182 cm)
    for m in re.finditer(r'\bREACH\s+(\d+(?:\.\d+)?)\s*[”″"]\s*\((\d+(?:\.\d+)?)\s*cm\)',text,re.I):
        cm=float(m.group(2))
        if 120<=cm<=270:vals.append(cm)
    for m in re.finditer(r'\bREACH\s+(\d+(?:\.\d+)?)\s*[”″"]',text,re.I):
        cm=round(float(m.group(1))*2.54,2)
        if 120<=cm<=270:vals.append(cm)
    vals=sorted(set(round(v,2) for v in vals))
    return name,(vals[0] if len(vals)==1 else None),vals

def main():
    audit=json.loads(AUDIT.read_text())
    targets={nk(x['name']):x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])}
    try:links=discover()
    except Exception as e:
        raise SystemExit('directory fetch failed: '+repr(e))

    def one(item):
        u,label=item
        rec={'url':u,'directory_label':label}
        try:
            final,raw=fetch(u,2_000_000);name,reach,vals=parse(raw)
            key=nk(name);rec.update({'url':final,'h1':name,'target_key':key if key in targets else None,'reach_cm':reach,'reach_values':vals})
            if key not in targets:rec['status']='not_missing_target'
            elif reach is None:rec['status']='no_reach'
            else:rec['status']='accepted'
        except Exception as e:rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
        return rec
    with cf.ThreadPoolExecutor(max_workers=6) as ex:rows=list(ex.map(one,links))
    acc=[x for x in rows if x['status']=='accepted']
    additions=[]
    for x in acc:
        t=targets[x['target_key']];reach=x['reach_cm']
        if float(reach).is_integer():reach=int(reach)
        additions.append({'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
          'fields':{'reach_cm':reach},
          'evidence':[{'source':'queensberry_official_fighter_profile','url':x['url'],'fields':{'reach_cm':reach},'exact_identity':True}],
          'conflicts':{},'quality':'official_promoter_structured_profile_exact_identity_missing_reach_only',
          'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    ADD.parent.mkdir(parents=True,exist_ok=True)
    with ADD.open('w') as f:
        for x in additions:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    counts={}
    for x in rows:counts[x['status']]=counts.get(x['status'],0)+1
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'missing_targets':len(targets),
      'directory_profiles':len(links),'status_counts':counts,'accepted':len(additions),
      'accepted_profiles':[{'name':x['name'],'reach_cm':x['fields']['reach_cm'],'url':x['evidence'][0]['url']} for x in additions],
      'rows':rows,
      'policy':'Official Queensberry public fighter directory/profile only; exact H1 identity and explicit plausible numeric reach; missing reach only; never overwrite.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('missing_targets','directory_profiles','status_counts','accepted')},indent=2))

if __name__=='__main__':main()
