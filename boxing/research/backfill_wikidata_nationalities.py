#!/usr/bin/env python3
"""Backfill missing boxing nationality from exact Wikipedia->Wikidata citizenship.

Strict policy:
- target must already be an exact Wikipedia career identity in PROFILE_GAP_AUDIT;
- resolve each page's wikibase_item through the English Wikipedia API;
- use only Wikidata P27 (country of citizenship);
- accept only one unique country entity after preferred-rank handling;
- never infer nationality from birthplace, residence, flag, language, or surname;
- existing nationality is never overwritten.

Network calls are batched to avoid per-entity request bursts and rate limits.
"""
from __future__ import annotations
import datetime as dt,json,re,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wikidata_nationality_backfill_report.json'
UA='Mozilla/5.0 AppwizaBoxingNationality/1.1 (https://appwiza.com/)'

COUNTRY_CANON={
  'United States of America':'United States',
  'United Kingdom of Great Britain and Northern Ireland':'United Kingdom',
  'Russian Federation':'Russia',
  'Republic of Korea':'South Korea',
  "Democratic People's Republic of Korea":'North Korea',
  'Czech Republic':'Czechia',
  'Republic of Ireland':'Ireland',
}
def canon_country(value):
    return COUNTRY_CANON.get(str(value or '').strip(),str(value or '').strip())

def get_json(url,limit=8_000_000,retries=5):
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={
              'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'
            })
            with urllib.request.urlopen(req,timeout=45) as r:
                raw=r.read(limit+1)
                if len(raw)>limit:raise ValueError('response too large')
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as e:
            last=e
            if e.code not in {429,500,502,503,504}:raise
            retry=e.headers.get('Retry-After')
            delay=float(retry) if retry and str(retry).isdigit() else 1.5*(2**attempt)
            time.sleep(min(delay,20))
        except urllib.error.URLError as e:
            last=e;time.sleep(min(1.5*(2**attempt),20))
    raise last or RuntimeError('request failed')

def chunks(seq,n=45):
    for i in range(0,len(seq),n):yield seq[i:i+n]

def wiki_title(url):
    path=urllib.parse.urlsplit(url).path
    if '/wiki/' not in path:return None
    return urllib.parse.unquote(path.split('/wiki/',1)[1]).replace('_',' ')

def batch_page_qids(targets):
    """Return target_source_id -> {qid, canonical_title} using batched Wikipedia API."""
    by_requested={wiki_title(t['id']):t for t in targets if wiki_title(t['id'])}
    out={}
    for titles in chunks(list(by_requested),40):
        q=urllib.parse.urlencode({
          'action':'query','format':'json','redirects':1,'prop':'pageprops',
          'titles':'|'.join(titles),'origin':'*'
        })
        data=get_json('https://en.wikipedia.org/w/api.php?'+q)
        query=data.get('query') or {}
        # Normalize/redirect maps let us connect the returned canonical title to
        # the original requested page title deterministically.
        alias={x['to']:x['from'] for x in query.get('normalized',[]) if x.get('from') and x.get('to')}
        for x in query.get('redirects',[]) or []:
            if x.get('from') and x.get('to'):
                root=alias.get(x['from'],x['from']);alias[x['to']]=root
        for page in (query.get('pages') or {}).values():
            canonical=page.get('title')
            requested=alias.get(canonical,canonical)
            target=by_requested.get(requested)
            if not target:
                # Fallback by normalized title key when API formatting differs.
                ck=str(canonical or '').replace('_',' ').casefold()
                target=next((t for k,t in by_requested.items() if k.replace('_',' ').casefold()==ck),None)
            if not target:continue
            qid=(page.get('pageprops') or {}).get('wikibase_item')
            out[target['id']]={'qid':qid,'canonical_title':canonical}
        time.sleep(.1)
    return out

def batch_entities(qids,props='claims|labels'):
    out={}
    ids=[x for x in sorted(set(qids)) if x]
    for batch in chunks(ids,45):
        q=urllib.parse.urlencode({
          'action':'wbgetentities','format':'json','ids':'|'.join(batch),
          'props':props,'languages':'en','languagefallback':1,'origin':'*'
        })
        data=get_json('https://www.wikidata.org/w/api.php?'+q)
        out.update(data.get('entities') or {})
        time.sleep(.15)
    return out

def p27_ids(ent):
    claims=(ent.get('claims') or {}).get('P27') or []
    preferred=[x for x in claims if x.get('rank')=='preferred']
    use=preferred or [x for x in claims if x.get('rank')!='deprecated']
    qids=[]
    for c in use:
        dv=((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(dv,dict) and dv.get('id'):qids.append(dv['id'])
    return sorted(set(qids))

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in (audit.get('missing_ranked') or {}).get('nationality',[])
             if x.get('career_source')=='wikipedia'
             and str(x.get('id','')).startswith('https://en.wikipedia.org/wiki/')]

    rows=[]
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            rows.append(json.loads(line))
    byid={x['target_source_id']:i for i,x in enumerate(rows)}

    # Skip targets already filled by another source since the last gap audit.
    pending=[t for t in targets
             if byid.get(t['id']) is None
             or not (rows[byid[t['id']]].get('fields') or {}).get('nationality')]

    page_map=batch_page_qids(pending)
    person_qids=[v.get('qid') for v in page_map.values() if v.get('qid')]
    persons=batch_entities(person_qids,'claims|labels')

    country_ids=set()
    person_countries={}
    for target in pending:
        meta=page_map.get(target['id']) or {}
        qid=meta.get('qid')
        ids=p27_ids(persons.get(qid,{}) if qid else {})
        person_countries[target['id']]=ids
        country_ids.update(ids)
    countries=batch_entities(country_ids,'labels')

    accepted=[];rejected=[]
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    for t in pending:
        meta=page_map.get(t['id']) or {}
        qid=meta.get('qid')
        if not qid:
            rejected.append({'name':t['name'],'reason':'no_wikibase_item'});continue
        cids=person_countries.get(t['id']) or []
        if len(cids)!=1:
            rejected.append({'name':t['name'],'reason':'ambiguous_or_missing_P27',
                             'wikidata_qid':qid,'country_qids':cids});continue
        cent=countries.get(cids[0]) or {}
        label=((cent.get('labels') or {}).get('en') or {}).get('value')
        country=canon_country(label)
        if not country:
            rejected.append({'name':t['name'],'reason':'country_label_missing',
                             'wikidata_qid':qid,'country_qid':cids[0]});continue

        evidence={
          'source':'wikidata_country_of_citizenship',
          'wikipedia_url':t['id'],
          'wikipedia_canonical_title':meta.get('canonical_title'),
          'wikidata_qid':qid,'property':'P27','country_qid':cids[0],
          'country':country,'fields':{'nationality':country}
        }
        idx=byid.get(t['id'])
        if idx is None:
            rows.append({
              'target_source_id':t['id'],'name':t['name'],'career_source':'wikipedia',
              'fields':{'nationality':country},'evidence':[evidence],'conflicts':{},
              'quality':'exact_wikipedia_identity_wikidata_single_country_of_citizenship',
              'collected_at':stamp
            })
            byid[t['id']]=len(rows)-1
        else:
            row=rows[idx];fields=row.setdefault('fields',{})
            if fields.get('nationality'):
                rejected.append({'name':t['name'],'reason':'existing_nationality_present'});continue
            fields['nationality']=country
            row.setdefault('evidence',[]).append(evidence)
            row['quality']='exact_wikipedia_identity_wikidata_single_country_of_citizenship'
        accepted.append({'name':t['name'],'country':country,'wikidata_qid':qid,'country_qid':cids[0]})

    rows.sort(key=lambda x:(x.get('name') or '',x.get('target_source_id') or ''))
    OUT.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows))
    report={
      'generated_at':stamp,
      'targets_from_gap_audit':len(targets),'already_filled_before_run':len(targets)-len(pending),
      'pending_targets':len(pending),'accepted':len(accepted),'rejected':len(rejected),
      'accepted_items':accepted,'rejected_items':rejected,
      'network_strategy':'batched Wikipedia pageprops + batched Wikidata wbgetentities',
      'policy':'Exact Wikipedia identity -> Wikidata P27 only; one unique non-deprecated/preferred country; no birthplace or residence inference; existing values never overwritten.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in (
      'targets_from_gap_audit','already_filled_before_run','pending_targets','accepted','rejected'
    )},indent=2))

if __name__=='__main__':main()
