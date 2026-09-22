#!/usr/bin/env python3
"""Backfill missing reach from official WBC statistics / special-preview pages.

Conservative acceptance:
- target must be in PROFILE_GAP_AUDIT reach-missing list;
- page must be on wbcboxing.com;
- exact normalized fighter name must occur in a compact structured block;
- same block must contain Age/Date of birth, Record, Height and Reach labels;
- only plausible adult reach values are accepted;
- existing reach is never overwritten.

Discovery uses WBC's own search pages. No external search engine or guessed
profile ID is required.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os,re,time,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wbc_reach_backfill_report.json'
BASE='https://wbcboxing.com'
UA='Mozilla/5.0 AppwizaBoxingWBCReach/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()

def fetch(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def search_links(name):
    urls=[
      BASE+'/?'+urllib.parse.urlencode({'s':name}),
      BASE+'/en/?'+urllib.parse.urlencode({'s':name}),
    ]
    # WBC search pages contain many sitewide navigation links. Keep only links
    # whose visible label or slug mentions the target surname.
    toks=re.findall(r'[A-Za-zÀ-ÿ0-9]+',ascii_text(name))
    last=norm_token(toks[-1]) if toks else ''
    out=[];seen=set()
    for u in urls:
        try:
            final,raw=fetch(u);s=BeautifulSoup(raw,'lxml')
        except Exception:
            continue
        for a in s.find_all('a',href=True):
            href=urllib.parse.urljoin(final,a['href']).split('#')[0]
            host=urllib.parse.urlsplit(href).hostname or ''
            if host not in {'wbcboxing.com','www.wbcboxing.com'}:continue
            path=urllib.parse.urlsplit(href).path
            if not path.startswith('/en/'):continue
            if any(x in path for x in ('/category/','/tag/','/author/','/page/')):continue
            label=ascii_text(a.get_text(' ',strip=True)+' '+urllib.parse.unquote(path))
            if last and last not in norm_token(label):continue
            if href not in seen:
                seen.add(href);out.append(href)
    return out[:6]

def norm_token(s):
    return re.sub(r'[^a-z0-9]+','',ascii_text(s).casefold())

def parse_measure(text):
    # Prefer cm when explicitly supplied.
    m=re.search(r'Reach\s*:\s*[^/\n]{0,45}?(\d{2,3}(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:
        v=float(m.group(1))
        if 120<=v<=270:return round(v,2)
        if 1.2<=v<=2.7:return round(v*100,2)
    # Often "Reach: 73” – 185cm" or simply "Reach: 73”".
    m=re.search(r'Reach\s*:\s*(\d{2,3}(?:\.\d+)?)\s*(?:inches?|["”″])',text,re.I)
    if m:
        v=float(m.group(1))*2.54
        if 120<=v<=270:return round(v,2)
    # More permissive WBC spelling: "reach of seventy-three inches" is not
    # parsed because converting words would add avoidable ambiguity.
    return None

def structured_reach(name,raw):
    soup=BeautifulSoup(raw,'lxml')
    text=' '.join(re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip())
    txt=ascii_text(text)
    target=ascii_text(name)
    positions=[m.start() for m in re.finditer(re.escape(target),txt,re.I)]
    accepted=[]
    for pos in positions:
        seg=txt[pos:pos+1200]
        low=seg.casefold()
        if 'reach' not in low or 'height' not in low or 'record' not in low:continue
        if ('age:' not in low and 'date of birth' not in low):continue
        r=parse_measure(seg)
        if r is not None:accepted.append(r)
    vals=sorted(set(accepted))
    if len(vals)==1:return vals[0],text
    return None,text

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=180)
    ap.add_argument('--min-appearances',type=int,default=2)
    ap.add_argument('--sleep',type=float,default=.08)
    args=ap.parse_args()

    audit=json.loads(AUDIT.read_text())
    targets=[x for x in (audit.get('missing_ranked') or {}).get('reach_cm',[])
             if int(x.get('strict_bout_appearances') or 0)>=args.min_appearances]
    targets=targets[:args.limit]

    existing={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);existing[x['target_source_id']]=x
            except Exception:pass

    found=[];failures=[];pages=0
    for i,t in enumerate(targets,1):
        if (existing.get(t['id']) or {}).get('fields',{}).get('reach_cm') is not None:
            continue
        links=search_links(t['name'])
        matches=[]
        for url in links:
            try:
                final,raw=fetch(url);pages+=1
                reach,_=structured_reach(t['name'],raw)
                if reach is not None:matches.append((reach,final))
            except Exception as e:
                failures.append({'name':t['name'],'url':url,'error':str(e)[:180]})
            time.sleep(max(0,args.sleep))
        values={x[0] for x in matches}
        if len(values)!=1:continue
        reach=next(iter(values))
        urls=sorted({u for v,u in matches if v==reach})
        cur=existing.get(t['id'])
        evidence={'source':'wbc_official_statistics_page','urls':urls[:5],
                  'field':'reach_cm','value':reach,'identity_gate':'exact_name_structured_age_record_height_reach_block'}
        if cur:
            fields=dict(cur.get('fields') or {})
            if 'reach_cm' in fields:continue
            fields['reach_cm']=reach;cur['fields']=fields
            ev=list(cur.get('evidence') or []);ev.append(evidence);cur['evidence']=ev
            cur['quality']='official_profile_or_statistics_missing_fields_only'
            existing[t['id']]=cur
        else:
            existing[t['id']]={
              'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
              'fields':{'reach_cm':reach},'evidence':[evidence],'conflicts':{},
              'quality':'official_wbc_statistics_exact_identity',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
            }
        found.append({'name':t['name'],'reach_cm':reach,'strict_bout_appearances':t.get('strict_bout_appearances'),'urls':urls[:5]})
        print(i,t['name'],'REACH',reach,flush=True)

    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as fh:
        for x in sorted(existing.values(),key=lambda z:(z.get('name') or '',z.get('target_source_id') or '')):
            fh.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)

    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'targets_attempted':len(targets),'pages_fetched':pages,'profiles_updated':len(found),
            'found':found,'failures_sample':failures[:100],
            'policy':'Official WBC pages only; exact name in structured Age/Record/Height/Reach block; unique plausible reach; existing reach never overwritten.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ['targets_attempted','pages_fetched','profiles_updated']},indent=2))

if __name__=='__main__':main()
