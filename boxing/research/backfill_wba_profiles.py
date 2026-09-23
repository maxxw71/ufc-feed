#!/usr/bin/env python3
"""Backfill missing strict-sample boxer profile fields from official WBA profiles.

Discovery comes from boxing/profile_supplements/wba_profile_index.json, which is
built only from explicit official WBA profile links. This job fetches ONLY exact
unique WBA identities that are missing useful fields in PROFILE_GAP_AUDIT.

Acceptance:
- exact normalized WBA profile title == target name;
- only requested missing fields are added;
- existing supplement values are never overwritten;
- height/reach must pass plausible adult-boxer ranges.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os,re,sqlite3,time,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
INDEX=ROOT/'profile_supplements'/'wba_profile_index.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wba_profile_backfill_report.json'
DB=ROOT/'research'/'boxing.sqlite3'
UA='Mozilla/5.0 AppwizaBoxingWBAProfile/1.1'

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('profile response too large')
        return r.geturl(),raw

def label_value(soup,label):
    want=label.upper().rstrip(':')
    for tr in soup.find_all('tr'):
        cells=[re.sub(r'\s+',' ',x.get_text(' ',strip=True)).strip() for x in tr.find_all(['th','td'])]
        if len(cells)>=2 and cells[0].upper().replace(' :','').rstrip(':')==want:
            return cells[1].strip() or None
    strings=[re.sub(r'\s+',' ',x).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',x).strip()]
    for i,x in enumerate(strings[:-1]):
        if x.upper().replace(' :','').rstrip(':')==want:
            return strings[i+1]
    return None

def cm_measure(s):
    if not s:return None
    txt=str(s).replace('’',"'").replace('′',"'").replace('“','"').replace('”','"').replace('″','"')
    txt=txt.replace('½','.5').replace('¼','.25').replace('¾','.75')
    m=re.search(r"(\d+)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?",txt)
    if m:return round((float(m.group(1))*12+float(m.group(2) or 0))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*"',txt)
    if m:return round(float(m.group(1))*2.54,2)
    m=re.search(r'(\d+(?:\.\d+)?)\s*cm\b',txt,re.I)
    if m:return round(float(m.group(1)),2)
    return None

def born_iso(s):
    if not s:return None
    for fmt in ('%m-%d-%Y','%m/%d/%Y','%Y-%m-%d','%d-%m-%Y','%d/%m/%Y'):
        try:return dt.datetime.strptime(str(s).strip(),fmt).date().isoformat()
        except Exception:pass
    return None

def profile_name(soup):
    t=soup.title.get_text(' ',strip=True) if soup.title else ''
    m=re.match(r'\s*Boxer:\s*(.+?)\s*$',t,re.I)
    return re.sub(r'\s+',' ',m.group(1)).strip() if m else None

def parse_profile(url,raw):
    soup=BeautifulSoup(raw,'lxml');name=profile_name(soup);data={}
    h=cm_measure(label_value(soup,'HEIGHT'));r=cm_measure(label_value(soup,'REACH'))
    b=born_iso(label_value(soup,'BORN'));n=label_value(soup,'COUNTRY')
    if h and 120<=h<=250:data['height_cm']=h
    if r and 120<=r<=270:data['reach_cm']=r
    if b:data['born']=b
    if n and 2<=len(n)<=80:data['nationality']=n
    return name,data

def known_profiles():
    out={}
    if not DB.exists():return out
    con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    try:
        for r in con.execute("select source_id,name,born,height_cm,nationality from normalized_fighters"):
            out[r['source_id']]={'name':r['name'],'born':r['born'],'height_cm':r['height_cm'],'nationality':r['nationality']}
    finally:
        con.close()
    return out

def ncountry(s):
    return re.sub(r'[^a-z]','',str(s or '').casefold())

def resolve_ambiguous(target,entries,known,args):
    """Return one exact WBA profile only under strict independent-field agreement."""
    base=known.get(target['target_source_id']) or {}
    checked=[]
    for entry in entries:
        try:
            final,raw=fetch(entry['url'])
            name,data=parse_profile(final,raw)
            if nk(name)!=nk(target['name']):continue
            checked.append((entry,final,data))
        except Exception:
            continue
        time.sleep(max(0,args.sleep))
    if not checked:return None,None

    born=str(base.get('born') or '').strip()
    if born:
        hits=[x for x in checked if x[2].get('born')==born]
        if len(hits)==1:return hits[0],'existing_dob_exact_match'

    h=base.get('height_cm')
    nat=ncountry(base.get('nationality'))
    if h is not None and nat:
        hits=[]
        for x in checked:
            wh=x[2].get('height_cm');wn=ncountry(x[2].get('nationality'))
            if wh is not None and abs(float(wh)-float(h))<=1.0 and wn and wn==nat:
                hits.append(x)
        if len(hits)==1:return hits[0],'existing_height_and_nationality_match'
    return None,None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=500)
    ap.add_argument('--sleep',type=float,default=.08)
    args=ap.parse_args()
    if not AUDIT.exists() or not INDEX.exists():
        raise SystemExit('missing PROFILE_GAP_AUDIT or WBA profile index')

    audit=json.loads(AUDIT.read_text());idx=json.loads(INDEX.read_text()).get('index') or {}
    targets={}
    for field_name,items in (audit.get('missing_ranked') or {}).items():
        if field_name not in {'born','height_cm','reach_cm','nationality'}:continue
        for x in items:
            key=nk(x.get('name'))
            if not key:continue
            t=targets.setdefault(key,{'name':x['name'],'target_source_id':x['id'],'career_source':x.get('career_source'),'strict_bout_appearances':int(x.get('strict_bout_appearances') or 0),'missing':set()})
            t['missing'].add(field_name)

    existing={}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            try:
                x=json.loads(line);existing[x['target_source_id']]=x
            except Exception:pass

    candidates=[]
    ambiguous=0
    ambiguous_resolved=0
    known=known_profiles()
    for key,t in targets.items():
        rows=idx.get(key,[])
        if len(rows)==1:
            candidates.append((t,rows[0],'unique_exact_name'))
        elif len(rows)>1:
            ambiguous+=1
            resolved,why=resolve_ambiguous(t,rows,known,args)
            if resolved:
                entry,final,data=resolved
                # Preserve prefetched data so the main loop need not fetch again.
                entry=dict(entry);entry['_prefetched_url']=final;entry['_prefetched_data']=data
                candidates.append((t,entry,why));ambiguous_resolved+=1
    # prioritize fighters missing reach, then height, then appearance order already
    # encoded by PROFILE_GAP_AUDIT.
    candidates.sort(key=lambda x:(0 if 'reach_cm' in x[0]['missing'] else 1,0 if 'height_cm' in x[0]['missing'] else 1,-int(x[0].get('strict_bout_appearances') or 0),x[0]['name']))
    candidates=candidates[:args.limit]

    found=[];fail=[];counts={'born':0,'height_cm':0,'reach_cm':0,'nationality':0};fetched=0
    for t,entry,resolution in candidates:
        try:
            if entry.get('_prefetched_data') is not None:
                final=entry.get('_prefetched_url') or entry['url'];data=entry['_prefetched_data'];name=t['name']
            else:
                final,raw=fetch(entry['url']);fetched+=1
                name,data=parse_profile(final,raw)
            if nk(name)!=nk(t['name']):raise ValueError(f'identity mismatch: {name!r}')
            add={k:v for k,v in data.items() if k in t['missing']}
            if not add:
                time.sleep(max(0,args.sleep));continue
            cur=existing.get(t['target_source_id'])
            if cur:
                fields=dict(cur.get('fields') or {})
                actual={k:v for k,v in add.items() if k not in fields}
                if not actual:
                    time.sleep(max(0,args.sleep));continue
                fields.update(actual);cur['fields']=fields
                ev=list(cur.get('evidence') or [])
                ev.append({'source':'wba_official_profile','url':final,'profile_id':entry['profile_id'],'identity_resolution':resolution,'fields':actual})
                cur['evidence']=ev;cur['quality']='public_profile_exact_identity_missing_fields_only'
                existing[t['target_source_id']]=cur
            else:
                actual=add
                existing[t['target_source_id']]={
                  'target_source_id':t['target_source_id'],'name':t['name'],'career_source':t['career_source'],
                  'fields':actual,
                  'evidence':[{'source':'wba_official_profile','url':final,'profile_id':entry['profile_id'],'identity_resolution':resolution,'fields':actual}],
                  'conflicts':{},'quality':'official_wba_structured_profile_exact_identity_missing_fields_only',
                  'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
                }
            for k in actual:counts[k]+=1
            found.append({'name':t['name'],'profile_id':entry['profile_id'],'url':final,'fields':actual})
        except Exception as e:
            fail.append({'name':t['name'],'profile_id':entry.get('profile_id'),'url':entry.get('url'),'error':str(e)[:220]})
        time.sleep(max(0,args.sleep))

    tmp=OUT.with_suffix('.jsonl.tmp');OUT.parent.mkdir(parents=True,exist_ok=True)
    with tmp.open('w',encoding='utf-8') as fh:
        for x in sorted(existing.values(),key=lambda x:(x.get('name') or '',x.get('target_source_id') or '')):
            fh.write(json.dumps(x,ensure_ascii=False)+'\n')
    os.replace(tmp,OUT)

    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'target_fighters':len(targets),'exact_unique_index_candidates':len(candidates),
      'ambiguous_index_targets':ambiguous,'profiles_fetched':fetched,
      'profiles_updated':len(found),'new_field_counts':counts,
      'ambiguous_index_targets_resolved':ambiguous_resolved,'known_profile_rows_loaded':len(known),'found':found,'failures':fail,
      'policy':'Official WBA explicit-link identity index; exact profile title; strict missing-fields-only enrichment; no existing values overwritten.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in [
      'target_fighters','exact_unique_index_candidates','ambiguous_index_targets','ambiguous_index_targets_resolved','known_profile_rows_loaded',
      'profiles_fetched','profiles_updated','new_field_counts'
    ]},indent=2))

if __name__=='__main__':main()
