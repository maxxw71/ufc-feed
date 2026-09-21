#!/usr/bin/env python3
"""Fill missing strict-sample boxer profile fields from public biography pages.

This layer is intentionally separate from career reconstruction. It only fills
missing static/profile proxy fields and never overwrites existing database
values. Identity must match exactly after normalization. When both sources
provide the same field, conflicting values are omitted instead of guessed.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,time,unicodedata,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
from dateutil.parser import parse as dateparse

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUTDIR=ROOT/'profile_supplements';OUTDIR.mkdir(exist_ok=True)
OUT=OUTDIR/'verified_profiles.jsonl'
REPORT=OUTDIR/'latest_profile_backfill_report.json'
UA='Mozilla/5.0 AppwizaBoxingProfileResearch/1.0'
PHYSICAL={'born','height_cm','reach_cm','stance','nationality'}

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]','',x)

def slug(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','-',x).strip('-')

def clean(s):
    return re.sub(r'\s+',' ',str(s or '')).strip()

def fetch(url,timeout=25):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(2_500_001)
        final=r.geturl()
    if len(raw)>2_500_000:raise ValueError('profile page too large')
    return final,raw

def cm(v):
    s=clean(v).replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s,re.I)
    if m:return float(m.group(1))
    m=re.search(r"(\d+)\s*['′]\s*(\d+(?:\.\d+)?)?\s*(?:[\"″]|in)?",s)
    if m:return round(int(m.group(1))*30.48+float(m.group(2) or 0)*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:in|″)\b',s,re.I)
    return round(float(m.group(1))*2.54,2) if m else None

def iso_date(v):
    try:
        d=dateparse(clean(v),fuzzy=True).date()
        if 1940<=d.year<=2012:return d.isoformat()
    except Exception:pass
    return None

def stance(v):
    s=clean(v).casefold()
    return next((x for x in ('orthodox','southpaw','switch') if x in s),None)

def labeled(lines,label):
    target=label.casefold()
    for i,x in enumerate(lines[:-1]):
        if x.casefold().rstrip(':')==target:
            return lines[i+1]
        if x.casefold().startswith(target+':'):
            return clean(x.split(':',1)[1])
    return None

def parse_boxrec(name):
    title=name.replace(' ','_')
    url='https://boxrec.com/wiki/index.php/'+urllib.parse.quote(title,safe='_-()')
    final,raw=fetch(url)
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    heading=clean(h.get_text(' ',strip=True)) if h else ''
    if nk(heading)!=nk(name):raise ValueError(f'BoxRec identity mismatch: {heading!r}')
    text=soup.get_text('\n',strip=True)
    # BoxRec wiki biography fields are rendered as "Label: value" text.
    def field(label):
        m=re.search(r'(?:^|\n)\s*'+re.escape(label)+r'\s*:\s*([^\n]+)',text,re.I)
        return clean(m.group(1)) if m else None
    data={}
    hv=cm(field('Height'));rv=cm(field('Reach'))
    if hv is not None and 120<=hv<=250:data['height_cm']=hv
    if rv is not None and 120<=rv<=270:data['reach_cm']=rv
    sv=stance(field('Stance'))
    if sv:data['stance']=sv
    bv=iso_date(field('Born') or field('Date of Birth'))
    if bv:data['born']=bv
    nv=field('Nationality')
    if nv and len(nv)<=80:data['nationality']=nv
    return {'source':'boxrec_wiki','url':final,'fields':data}

def parse_boxingscene(name):
    url='https://www.boxingscene.com/fighters/'+slug(name)
    final,raw=fetch(url)
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    heading=clean(h.get_text(' ',strip=True)) if h else ''
    heading=re.sub(r'\s+(?:Record|Profile(?:\s*&\s*Stats)?)\s*$','',heading,flags=re.I)
    if nk(heading)!=nk(name):raise ValueError(f'BoxingScene identity mismatch: {heading!r}')
    lines=[clean(x) for x in soup.get_text('\n').splitlines() if clean(x)]
    data={}
    hv=cm(labeled(lines,'Height'));rv=cm(labeled(lines,'Reach'))
    if hv is not None and 120<=hv<=250:data['height_cm']=hv
    if rv is not None and 120<=rv<=270:data['reach_cm']=rv
    sv=stance(labeled(lines,'Stance'))
    if sv:data['stance']=sv
    bv=iso_date(labeled(lines,'Born'))
    if bv:data['born']=bv
    return {'source':'boxingscene','url':final,'fields':data}

def reconcile(evidence):
    out={};conflicts={}
    for field in PHYSICAL:
        vals=[(e['source'],e['fields'][field]) for e in evidence if field in e.get('fields',{})]
        if not vals:continue
        unique=[]
        for _,v in vals:
            if v not in unique:unique.append(v)
        # DOB is too easy to misread from generic biography text. Require
        # agreement from two independent exact-identity sources before filling it.
        if field=='born':
            if len({src for src,_ in vals})>=2 and len(unique)==1:out[field]=unique[0]
            elif len(vals)>1:conflicts[field]=vals
            continue
        if field in {'height_cm','reach_cm'} and len(unique)>1:
            nums=[float(v) for v in unique]
            if max(nums)-min(nums)<=2.0:out[field]=round(sum(nums)/len(nums),2)
            else:conflicts[field]=vals
        elif len(unique)==1:
            out[field]=unique[0]
        else:
            conflicts[field]=vals
    return out,conflicts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=160);args=ap.parse_args()
    if not AUDIT.exists():raise SystemExit(f'missing audit: {AUDIT}')
    audit=json.loads(AUDIT.read_text())
    existing={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);existing[x['target_source_id']]=x
            except Exception:pass
    candidates=[]
    for x in audit.get('fighters',[]):
        missing=set(x.get('missing') or [])
        if not (missing&PHYSICAL):continue
        if x.get('id') in existing:continue
        candidates.append(x)
    candidates.sort(key=lambda x:(-int(x.get('strict_bout_appearances') or 0),-len(set(x.get('missing') or [])&PHYSICAL),x.get('name') or ''))
    attempted=accepted=0;failures=[];field_counts={k:0 for k in PHYSICAL};new=[]
    for x in candidates[:args.limit]:
        attempted+=1;name=x['name'];evidence=[]
        for parser in (parse_boxrec,parse_boxingscene):
            try:
                e=parser(name)
                if e['fields']:evidence.append(e)
            except Exception as exc:
                failures.append({'name':name,'source':parser.__name__,'error':str(exc)[:180]})
            time.sleep(.25)
        needed=set(x.get('missing') or [])
        # Drop unrelated fields before reconciliation/provenance storage. This
        # prevents an unused field from an otherwise useful source from being
        # mistaken for verified evidence later.
        evidence=[{**e,'fields':{k:v for k,v in e.get('fields',{}).items() if k in needed}} for e in evidence]
        evidence=[e for e in evidence if e['fields']]
        fields,conflicts=reconcile(evidence)
        fields={k:v for k,v in fields.items() if k in needed}
        if fields:
            item={
              'target_source_id':x['id'],'name':name,'career_source':x.get('career_source'),
              'fields':fields,'evidence':evidence,'conflicts':conflicts,
              'quality':'public_profile_exact_identity_missing_fields_only',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
            }
            new.append(item);accepted+=1
            for k in fields:field_counts[k]+=1
        else:
            failures.append({'name':name,'source':'combined','error':'no non-conflicting missing fields found'})
    merged={**existing,**{x['target_source_id']:x for x in new}}
    with OUT.open('w',encoding='utf-8') as f:
        for x in sorted(merged.values(),key=lambda z:(z.get('name',''),z.get('target_source_id',''))):
            f.write(json.dumps(x,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'attempted':attempted,'accepted_profiles':accepted,'new_field_counts':field_counts,
      'total_profile_supplements':len(merged),'failures':failures[:400],
      'policy':'Exact identity only; fill missing fields only; conflicting values omitted; no residence-to-nationality inference.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:v for k,v in report.items() if k!='failures'},indent=2))

if __name__=='__main__':main()
