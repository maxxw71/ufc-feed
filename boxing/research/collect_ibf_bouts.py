#!/usr/bin/env python3
"""Collect official IBF male title/contender bout records from the public IBF API.

This is an independent result/context evidence archive, not a full-career source.
Rows are accepted only when:
- both participants are present;
- bout date parses exactly;
- pair is non-self;
- result winner, when stated, resolves to exactly one participant by full name
  or unique surname; unresolved winner text is retained but not asserted.

Source: https://www.ibf-usba-boxing.com/wp-json/bouts/v1/filter
"""
from __future__ import annotations
import datetime as dt,json,re,unicodedata,urllib.parse,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'official_bouts';OUT.mkdir(parents=True,exist_ok=True)
API='https://www.ibf-usba-boxing.com/wp-json/bouts/v1/filter'
UA='Mozilla/5.0 AppwizaBoxingIBFBouts/1.0'

def get_json(url,limit=25_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return json.loads(raw.decode('utf-8'))

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',x)

def person(raw):
    s=re.sub(r'\s+',' ',str(raw or '')).strip()
    s=re.sub(r'\s*\([A-Z]{2,4}\)\s*$','',s).strip()
    return s

def surname(s):
    toks=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",str(s or ''))
    while toks and toks[-1].lower().rstrip('.') in {'jr','sr','ii','iii','iv'}:toks.pop()
    return norm(toks[-1]) if toks else ''

def date_iso(s):
    for fmt in ('%B %d, %Y','%b %d, %Y','%Y-%m-%d','%m/%d/%Y'):
        try:return dt.datetime.strptime(str(s).strip(),fmt).date().isoformat()
        except Exception:pass
    return None

def outcome(text,a,b):
    s=re.sub(r'\s+',' ',str(text or '')).strip()
    low=s.casefold()
    if re.search(r'\b(draw|technical draw|majority draw|split draw)\b',low):
        winner='DRAW';quality='explicit_draw'
    else:
        winner=None;quality='unresolved'
        na,nb=norm(a),norm(b);prefix=norm(re.split(r'\bwon\b|\bdefeated\b|\bbeats?\b',s,1,flags=re.I)[0])
        if prefix:
            if prefix==na or (prefix and prefix in na and len(prefix)>=4):winner='A';quality='full_or_prefix_name'
            elif prefix==nb or (prefix and prefix in nb and len(prefix)>=4):winner='B';quality='full_or_prefix_name'
        if winner is None:
            words=re.findall(r"[A-Za-zÀ-ÿ0-9'-]+",re.split(r'\bwon\b|\bdefeated\b|\bbeats?\b',s,1,flags=re.I)[0])
            if words:
                last=norm(words[-1]);sa,sb=surname(a),surname(b)
                if last and last==sa and last!=sb:winner='A';quality='unique_surname'
                elif last and last==sb and last!=sa:winner='B';quality='unique_surname'
    method=None
    mm=re.search(r'\b(TKO|KO|RTD|DQ|UD|SD|MD|TD|PTS)\b',s,re.I)
    if mm:method=mm.group(1).upper()
    rnd=None
    rm=re.search(r'\b(?:round|in the)\s*(\d{1,2})(?:st|nd|rd|th)?\s+round\b|\b(\d{1,2})[- ]round\b',s,re.I)
    if rm:rnd=int(rm.group(1) or rm.group(2))
    return winner,quality,method,rnd

def main():
    url=API+'?'+urllib.parse.urlencode({'org':'ibf','ppp':-1})
    items=get_json(url)
    if not isinstance(items,list):raise SystemExit('IBF bouts API did not return a list')
    rows=[];rejected=[];winner_resolved=0
    for i,x in enumerate(items):
        a=person(x.get('fighter_home'));b=person(x.get('fighter_away'));date=date_iso(x.get('bout_date'))
        if not a or not b or norm(a)==norm(b) or not date:
            rejected.append({'index':i,'reason':'missing/invalid date or pair','raw':x});continue
        winner,q,method,rnd=outcome(x.get('result') or x.get('title'),a,b)
        if winner is not None:winner_resolved+=1
        rows.append({
          'source':'ibf_official_bout_api','source_index':i,'source_url':url,
          'date':date,'fighter_a':a,'fighter_b':b,
          'winner_side':winner,'winner_quality':q,'method':method,'round':rnd,
          'weight_class':x.get('wc'),'organization':x.get('org'),
          'bout_type':x.get('bout_type'),'location':x.get('location'),
          'promoter':x.get('promoter'),'result_text':x.get('result'),
          'title_text':x.get('title')
        })
    # Deduplicate exact date/pair/result records.
    uniq={}
    for r in rows:
        pair=tuple(sorted([norm(r['fighter_a']),norm(r['fighter_b'])]))
        key=(r['date'],pair,r.get('result_text') or '',r.get('bout_type') or '')
        uniq[key]=r
    rows=sorted(uniq.values(),key=lambda r:(r['date'],norm(r['fighter_a']),norm(r['fighter_b'])))
    (OUT/'ibf_bouts.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
    meta={
      'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_url':url,
      'api_items':len(items),'accepted_rows':len(rows),'rejected_rows':len(rejected),
      'winner_resolved_rows':sum(r['winner_side'] is not None for r in rows),
      'winner_unresolved_rows':sum(r['winner_side'] is None for r in rows),
      'date_min':min((r['date'] for r in rows),default=None),
      'date_max':max((r['date'] for r in rows),default=None),
      'organizations':sorted({str(r.get('organization')) for r in rows}),
      'bout_types':sorted({str(r.get('bout_type')) for r in rows if r.get('bout_type')}),
      'policy':'Official IBF bouts API; independent result/context evidence only, not assumed complete careers. Winner asserted only on exact/unique participant resolution.',
      'rejected_sample':rejected[:50]
    }
    (OUT/'ibf_bouts_meta.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['api_items','accepted_rows','rejected_rows','winner_resolved_rows','winner_unresolved_rows','date_min','date_max']},indent=2))

if __name__=='__main__':main()
