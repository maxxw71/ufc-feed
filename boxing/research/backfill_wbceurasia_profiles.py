#!/usr/bin/env python3
"""Backfill reach/nationality from structured WBC Eurasia fighter profiles.

Acceptance:
- only targets currently missing reach and/or nationality in PROFILE_GAP_AUDIT;
- exact normalized fighter identity from page heading;
- source host must be wbceurasia.org;
- reach must be explicit in the profile's Physical Parameters and plausible;
- country is accepted only from the profile's own country field/header;
- existing supplement values are never overwritten.

Discovery is bounded and deterministic: first exact slug, then site sitemap URLs
whose slug normalizes to the target name. No fuzzy edit-distance matching.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os,re,time,unicodedata,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
import pycountry

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wbceurasia_profile_backfill_report.json'
BASE='https://www.wbceurasia.org'
UA='Mozilla/5.0 AppwizaBoxingWBCEurasia/1.0'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)

def slug(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    x=re.sub(r'[^a-z0-9]+','-',x).strip('-')
    return x

def fetch(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return r.geturl(),raw

def sitemap_index():
    out={}
    candidates=[BASE+'/sitemap.xml',BASE+'/sitemap_index.xml']
    seen=set()
    def add_url(u):
        p=urllib.parse.urlsplit(u).path.rstrip('/')
        m=re.search(r'/fighters/([^/]+)$',p,re.I)
        if m:
            out.setdefault(nk(urllib.parse.unquote(m.group(1).replace('-',' '))),[]).append(u)
    for root in candidates:
        try:
            _,raw=fetch(root,8_000_000)
            tree=ET.fromstring(raw)
        except Exception:
            continue
        locs=[(x.text or '').strip() for x in tree.iter() if x.tag.endswith('loc') and (x.text or '').strip()]
        for u in locs:
            if u in seen:continue
            seen.add(u)
            if u.lower().endswith('.xml'):
                try:
                    _,sub=fetch(u,8_000_000);st=ET.fromstring(sub)
                    for z in st.iter():
                        if z.tag.endswith('loc') and (z.text or '').strip():add_url((z.text or '').strip())
                except Exception:pass
            else:add_url(u)
    return out

def text_lines(soup):
    return [re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]

def identity(soup):
    h=soup.find('h1')
    return re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip() if h else None

def parse_cm(value):
    s=str(value or '').replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',s,re.I)
    if m:return round(float(m.group(1)),2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:inches?|["”″])',s,re.I)
    if m:return round(float(m.group(1))*2.54,2)
    # WBC Eurasia profile currently renders bare metric values as e.g. "180 cm";
    # do not accept bare numbers without unit.
    return None

def fields(soup):
    lines=text_lines(soup)
    data={}
    # Structured label/value parsing from the profile text.
    for i,x in enumerate(lines[:-1]):
        key=x.casefold().rstrip(':')
        val=lines[i+1]
        if key=='reach':
            cm=parse_cm(val)
            if cm is not None and 120<=cm<=270:data['reach_cm']=cm
        elif key=='height':
            cm=parse_cm(val)
            if cm is not None and 120<=cm<=250:data['height_cm']=cm
    # Country is usually rendered immediately above nickname/name and may not
    # have a literal "Country" label. Prefer explicit structured labels first.
    for i,x in enumerate(lines[:-1]):
        if x.casefold().rstrip(':') in {'country','nationality'}:
            v=lines[i+1]
            if 2<=len(v)<=80:data['nationality']=v
    if 'nationality' not in data:
        h=soup.find('h1')
        if h:
            # Country is rendered immediately above nickname/name. Accept only
            # strings that resolve through ISO country metadata; nicknames and
            # navigation labels can therefore never be mistaken for nationality.
            prev=[];cur=h
            for _ in range(12):
                cur=cur.find_previous(string=True)
                if cur is None:break
                v=re.sub(r'\s+',' ',str(cur)).strip()
                if v:prev.append(v)
            aliases={
              'UK':'United Kingdom','U.K.':'United Kingdom','US':'United States',
              'U.S.':'United States','USA':'United States','DOM. REP.':'Dominican Republic',
              'DOMINICAN REP.':'Dominican Republic','RUSSIA':'Russian Federation',
              'SOUTH KOREA':'Korea, Republic of','IRAN':'Iran, Islamic Republic of',
              'VENEZUELA':'Venezuela, Bolivarian Republic of','BOLIVIA':'Bolivia, Plurinational State of',
              'TANZANIA':'Tanzania, United Republic of','MOLDOVA':'Moldova, Republic of'
            }
            for v in prev:
                probe=aliases.get(v.upper(),v)
                try:
                    country=pycountry.countries.lookup(probe)
                except LookupError:
                    country=None
                if country:
                    name={
                      'Russian Federation':'Russia',
                      'Korea, Republic of':'South Korea',
                      'Iran, Islamic Republic of':'Iran',
                      'Venezuela, Bolivarian Republic of':'Venezuela',
                      'Bolivia, Plurinational State of':'Bolivia',
                      'Tanzania, United Republic of':'Tanzania',
                      'Moldova, Republic of':'Moldova'
                    }.get(country.name,country.name)
                    data['nationality']=name
                    break
    return data

def load_existing():
    out={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);out[x['target_source_id']]=x
            except Exception:pass
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=250)
    ap.add_argument('--sleep',type=float,default=.08)
    ap.add_argument('--min-appearances',type=int,default=1)
    args=ap.parse_args()
    audit=json.loads(AUDIT.read_text())
    targets={}
    for field in ('reach_cm','nationality'):
        for x in (audit.get('missing_ranked') or {}).get(field,[]):
            if int(x.get('strict_bout_appearances') or 0)<args.min_appearances:continue
            t=targets.setdefault(x['id'],{'id':x['id'],'name':x['name'],'career_source':x.get('career_source'),'appearances':int(x.get('strict_bout_appearances') or 0),'missing':set()})
            t['missing'].add(field)
    ordered=sorted(targets.values(),key=lambda x:(-x['appearances'],x['name']))[:args.limit]
    smap=sitemap_index();existing=load_existing()
    found=[];fail=[];fetched=0
    for i,t in enumerate(ordered,1):
        urls=[f"{BASE}/fighters/{slug(t['name'])}"]
        for u in smap.get(nk(t['name']),[]):
            if u not in urls:urls.append(u)
        accepted=None
        for url in urls[:4]:
            try:
                final,raw=fetch(url);fetched+=1
                if urllib.parse.urlsplit(final).hostname not in {'wbceurasia.org','www.wbceurasia.org'}:continue
                soup=BeautifulSoup(raw,'lxml');name=identity(soup)
                if nk(name)!=nk(t['name']):continue
                data=fields(soup)
                add={k:v for k,v in data.items() if k in t['missing']}
                if add:
                    accepted=(final,add);break
            except Exception as e:
                fail.append({'name':t['name'],'url':url,'error':str(e)[:180]})
            time.sleep(max(0,args.sleep))
        if not accepted:continue
        final,add=accepted
        cur=existing.get(t['id'])
        evidence={'source':'wbc_eurasia_official_fighter_profile','url':final,'fields':add,'identity_gate':'exact_h1'}
        if cur:
            fs=dict(cur.get('fields') or {})
            actual={k:v for k,v in add.items() if k not in fs}
            if not actual:continue
            fs.update(actual);cur['fields']=fs
            ev=list(cur.get('evidence') or []);ev.append({**evidence,'fields':actual});cur['evidence']=ev
            cur['quality']='official_profile_exact_identity_missing_fields_only'
            existing[t['id']]=cur
        else:
            actual=add
            existing[t['id']]={
              'target_source_id':t['id'],'name':t['name'],'career_source':t.get('career_source'),
              'fields':actual,'evidence':[{**evidence,'fields':actual}],'conflicts':{},
              'quality':'official_wbc_eurasia_profile_exact_identity',
              'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
            }
        found.append({'name':t['name'],'strict_bout_appearances':t['appearances'],'url':final,'fields':actual})
        print(i,t['name'],actual,flush=True)

    tmp=OUT.with_suffix('.jsonl.tmp')
    with tmp.open('w',encoding='utf-8') as fh:
        for x in sorted(existing.values(),key=lambda z:(z.get('name') or '',z.get('target_source_id') or '')):
            fh.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)
    counts={'reach_cm':sum('reach_cm' in x['fields'] for x in found),'nationality':sum('nationality' in x['fields'] for x in found)}
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(ordered),'profiles_fetched':fetched,
            'profiles_updated':len(found),'new_field_counts':counts,'found':found,'failures_sample':fail[:100],
            'policy':'WBC Eurasia exact fighter profile only; explicit reach with units; exact H1 identity; missing fields only; existing values never overwritten.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ['targets','profiles_fetched','profiles_updated','new_field_counts']},indent=2))

if __name__=='__main__':main()
