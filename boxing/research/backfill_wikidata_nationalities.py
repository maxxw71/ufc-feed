#!/usr/bin/env python3
"""Backfill missing boxing nationality from exact Wikipedia->Wikidata citizenship.

Strict policy:
- target must already be an exact Wikipedia career identity in PROFILE_GAP_AUDIT;
- resolve the page's wikibase_item through the English Wikipedia API;
- use only Wikidata P27 (country of citizenship);
- accept only one unique country entity after preferred-rank handling;
- never infer nationality from birthplace, residence, flag, language, or surname;
- existing nationality is never overwritten.
"""
from __future__ import annotations
import datetime as dt,json,re,time,unicodedata,urllib.parse,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'verified_profiles.jsonl'
REPORT=ROOT/'profile_supplements'/'wikidata_nationality_backfill_report.json'
UA='Mozilla/5.0 AppwizaBoxingNationality/1.0'
COUNTRY_CANON={
  'United States of America':'United States',
  'United Kingdom of Great Britain and Northern Ireland':'United Kingdom',
  'Russian Federation':'Russia',
  'Republic of Korea':'South Korea',
  'Democratic People\'s Republic of Korea':'North Korea',
  'Czech Republic':'Czechia',
  'Republic of Ireland':'Ireland',
}
def canon_country(value):
    return COUNTRY_CANON.get(str(value or '').strip(),str(value or '').strip())

def get_json(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return json.loads(raw.decode('utf-8'))

def wiki_title(url):
    path=urllib.parse.urlsplit(url).path
    marker='/wiki/'
    if marker not in path:return None
    return urllib.parse.unquote(path.split(marker,1)[1]).replace('_',' ')

def page_qid(title):
    q=urllib.parse.urlencode({
      'action':'query','format':'json','redirects':1,'prop':'pageprops','titles':title,'origin':'*'
    })
    data=get_json('https://en.wikipedia.org/w/api.php?'+q)
    pages=(data.get('query') or {}).get('pages') or {}
    page=next(iter(pages.values()),{})
    return (page.get('pageprops') or {}).get('wikibase_item'),page.get('title')

def entity(qid):
    return get_json(f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json').get('entities',{}).get(qid) or {}

def country_from_entity(ent):
    claims=(ent.get('claims') or {}).get('P27') or []
    preferred=[x for x in claims if x.get('rank')=='preferred']
    use=preferred or [x for x in claims if x.get('rank')!='deprecated']
    qids=[]
    for c in use:
        dv=((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(dv,dict) and dv.get('id'):qids.append(dv['id'])
    qids=sorted(set(qids))
    if len(qids)!=1:return None,qids
    cent=entity(qids[0])
    labels=cent.get('labels') or {}
    label=(labels.get('en') or {}).get('value')
    return canon_country(label),qids

def main():
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in (audit.get('missing_ranked') or {}).get('nationality',[])
             if x.get('career_source')=='wikipedia' and str(x.get('id','')).startswith('https://en.wikipedia.org/wiki/')]
    rows=[]
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():continue
            rows.append(json.loads(line))
    byid={x['target_source_id']:i for i,x in enumerate(rows)}
    accepted=[];rejected=[]
    for t in targets:
        try:
            title=wiki_title(t['id'])
            if not title:raise ValueError('invalid wikipedia url')
            qid,canonical=page_qid(title)
            if not qid:raise ValueError('no wikibase_item')
            country,country_qids=country_from_entity(entity(qid))
            if not country:
                rejected.append({'name':t['name'],'reason':'ambiguous_or_missing_P27','wikidata_qid':qid,'country_qids':country_qids})
                continue
            idx=byid.get(t['id'])
            evidence={
              'source':'wikidata_country_of_citizenship',
              'wikipedia_url':t['id'],'wikipedia_canonical_title':canonical,
              'wikidata_qid':qid,'property':'P27','country_qid':country_qids[0],
              'country':country,'fields':{'nationality':country}
            }
            if idx is None:
                rows.append({
                  'target_source_id':t['id'],'name':t['name'],'career_source':'wikipedia',
                  'fields':{'nationality':country},'evidence':[evidence],'conflicts':{},
                  'quality':'exact_wikipedia_identity_wikidata_single_country_of_citizenship',
                  'collected_at':dt.datetime.now(dt.timezone.utc).isoformat()
                })
                byid[t['id']]=len(rows)-1
            else:
                row=rows[idx];fields=row.setdefault('fields',{})
                if fields.get('nationality'):
                    rejected.append({'name':t['name'],'reason':'existing_nationality_present'});continue
                fields['nationality']=country
                row.setdefault('evidence',[]).append(evidence)
                row['quality']='exact_wikipedia_identity_wikidata_single_country_of_citizenship'
            accepted.append({'name':t['name'],'country':country,'wikidata_qid':qid,'country_qid':country_qids[0]})
        except Exception as e:
            rejected.append({'name':t.get('name'),'reason':str(e)[:220]})
        time.sleep(.05)
    rows.sort(key=lambda x:(x.get('name') or '',x.get('target_source_id') or ''))
    OUT.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows))
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'targets':len(targets),'accepted':len(accepted),'rejected':len(rejected),
      'accepted_items':accepted,'rejected_items':rejected,
      'policy':'Exact Wikipedia identity -> Wikidata P27 only; one unique non-deprecated/preferred country; no birthplace or residence inference; existing values never overwritten.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k] for k in ('targets','accepted','rejected')},indent=2))

if __name__=='__main__':main()
