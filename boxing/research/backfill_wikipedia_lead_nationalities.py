#!/usr/bin/env python3
"""Backfill remaining nationality from an exact Wikipedia lead demonym.

Strict policy:
- targets come only from PROFILE_GAP_AUDIT and already have exact Wikipedia career IDs;
- fetch only that exact page's REST summary;
- first descriptive sentence must explicitly contain boxer/boxing and exactly one
  canonical demonym group;
- English/Scottish/Welsh/Northern Irish/British canonicalize to United Kingdom;
- dual/multiple nationality signals are rejected;
- birthplace/residence/name/language are never used;
- existing nationality is never overwritten.
"""
from __future__ import annotations
import datetime as dt,json,re,time,unicodedata,urllib.parse,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wikipedia_lead_nationality_backfill_report.json'
UA='Mozilla/5.0 AppwizaBoxingNationalityLead/1.0 (https://appwiza.com/)'

DEMONYMS={
 'United Kingdom':[
   r'\bBritish\b',r'\bEnglish\b',r'\bScottish\b',r'\bWelsh\b',
   r'\bNorthern Irish\b'
 ],
 'United States':[r'\bAmerican\b',r'\bU\.S\.\b'],
 'Ireland':[r'\bIrish\b'],
 'Ukraine':[r'\bUkrainian\b'],
 'Germany':[r'\bGerman\b'],
 'Canada':[r'\bCanadian\b'],
 'Mexico':[r'\bMexican\b'],
 'Argentina':[r'\bArgentine\b',r'\bArgentinian\b'],
 'Australia':[r'\bAustralian\b'],
 'France':[r'\bFrench\b'],
 'Democratic Republic of the Congo':[r'\bCongolese\b'],
 'Republic of the Congo':[r'\bRepublic of the Congo\b'],
 'Albania':[r'\bAlbanian\b'],
 'Kosovo':[r'\bKosovan\b',r'\bKosovar\b'],
 'Nigeria':[r'\bNigerian\b'],
 'Ghana':[r'\bGhanaian\b'],
 'Jamaica':[r'\bJamaican\b'],
 'Puerto Rico':[r'\bPuerto Rican\b'],
 'New Zealand':[r'\bNew Zealander\b',r'\bNew Zealand\b'],
 'Poland':[r'\bPolish\b'],
 'Croatia':[r'\bCroatian\b'],
 'Serbia':[r'\bSerbian\b'],
 'Spain':[r'\bSpanish\b'],
 'Italy':[r'\bItalian\b'],
 'Russia':[r'\bRussian\b'],
 'Kazakhstan':[r'\bKazakh(?:stani)?\b'],
 'Uzbekistan':[r'\bUzbek(?:istani)?\b'],
 'South Africa':[r'\bSouth African\b'],
 'Venezuela':[r'\bVenezuelan\b'],
 'Colombia':[r'\bColombian\b'],
 'Brazil':[r'\bBrazilian\b'],
 'Nicaragua':[r'\bNicaraguan\b'],
 'Costa Rica':[r'\bCosta Rican\b'],
 'Thailand':[r'\bThai\b'],
 'Japan':[r'\bJapanese\b'],
 'Philippines':[r'\bFilipino\b',r'\bFilipina\b',r'\bPhilippine\b'],
}

def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def title_from_url(url):
    p=urllib.parse.urlsplit(str(url or '')).path
    if '/wiki/' not in p:return None
    return urllib.parse.unquote(p.split('/wiki/',1)[1]).replace('_',' ')

def fetch_summary(title):
    url='https://en.wikipedia.org/api/rest_v1/page/summary/'+urllib.parse.quote(title.replace(' ','_'),safe='()')
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('summary too large')
        return url,json.loads(raw.decode('utf-8'))

def first_sentence(text):
    s=re.sub(r'\s+',' ',str(text or '')).strip()
    # Wikipedia lead abbreviations make generic sentence splitting risky; the
    # first 500 chars are enough for the nationality descriptor.
    return s[:500]

def resolve_demonym(text):
    hits=[]
    for country,pats in DEMONYMS.items():
        matched=[p for p in pats if re.search(p,text,re.I)]
        if matched:hits.append((country,matched))
    # UK component demonyms are one canonical nationality family.
    countries=sorted({x[0] for x in hits})
    if len(countries)!=1:return None,hits
    return countries[0],hits

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in (audit.get('missing_ranked') or {}).get('nationality',[])
             if x.get('career_source')=='wikipedia'
             and str(x.get('id','')).startswith('https://en.wikipedia.org/wiki/')]

    rows=[]
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if line.strip():rows.append(json.loads(line))
    byid={x['target_source_id']:i for i,x in enumerate(rows)}
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    accepted=[];rejected=[]
    for t in targets:
        idx=byid.get(t['id'])
        if idx is not None and (rows[idx].get('fields') or {}).get('nationality'):
            rejected.append({'name':t['name'],'reason':'existing_nationality_present'});continue
        title=title_from_url(t['id'])
        if not title:
            rejected.append({'name':t['name'],'reason':'bad_wikipedia_url'});continue
        try:
            api_url,data=fetch_summary(title)
            extract=first_sentence(data.get('extract') or '')
            canonical_title=data.get('title')
            if not extract or not re.search(r'\bbox(?:er|ing)\b',extract,re.I):
                raise ValueError('lead lacks boxer/boxing descriptor')
            country,hits=resolve_demonym(extract)
            if not country:
                rejected.append({'name':t['name'],'reason':'ambiguous_or_missing_explicit_demonym',
                                 'canonical_title':canonical_title,
                                 'demonym_countries':sorted({x[0] for x in hits})})
                continue
            # Exact title identity gate, tolerant of parenthetical disambiguator.
            if nk(re.sub(r'\s*\([^)]*\)\s*$','',str(canonical_title or ''))) != nk(t['name']):
                raise ValueError(f'canonical title mismatch: {canonical_title!r}')

            ev={'source':'wikipedia_explicit_lead_demonym','wikipedia_url':t['id'],
                'summary_api_url':api_url,'canonical_title':canonical_title,
                'canonical_country':country,'fields':{'nationality':country},
                'identity_gate':'exact_existing_wikipedia_career_identity',
                'inference_policy':'explicit demonym only; no birthplace/residence inference'}
            if idx is None:
                rows.append({'target_source_id':t['id'],'name':t['name'],'career_source':'wikipedia',
                             'fields':{'nationality':country},'evidence':[ev],'conflicts':{},
                             'quality':'exact_wikipedia_identity_explicit_lead_demonym',
                             'collected_at':stamp})
                byid[t['id']]=len(rows)-1
            else:
                rows[idx].setdefault('fields',{})['nationality']=country
                rows[idx].setdefault('evidence',[]).append(ev)
                rows[idx]['quality']='exact_wikipedia_identity_explicit_lead_demonym'
            accepted.append({'name':t['name'],'nationality':country,'canonical_title':canonical_title})
        except Exception as e:
            rejected.append({'name':t['name'],'reason':'fetch_or_identity_failure','error':str(e)[:200]})
        time.sleep(.08)

    rows.sort(key=lambda x:(x.get('name') or '',x.get('target_source_id') or ''))
    OUT.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows))
    report={'generated_at':stamp,'targets':len(targets),'accepted':len(accepted),'rejected':len(rejected),
            'accepted_items':accepted,'rejected_items':rejected,
            'policy':'Exact existing Wikipedia career identity; explicit lead demonym only; exactly one canonical nationality family; no birthplace/residence inference; existing values never overwritten.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('targets','accepted','rejected')},indent=2))

if __name__=='__main__':main()
