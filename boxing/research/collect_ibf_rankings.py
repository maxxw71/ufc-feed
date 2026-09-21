#!/usr/bin/env python3
"""Collect official IBF men's historical rankings from the site's public ratings API.

The IBF's own ratings frontend calls /wp-json/ratings/v1/filter and receives:
- rating_month (YYYYMMDD)
- post_date
- wc
- ratings: semicolon-separated rank slots, each "NAME,COUNTRY,..."
- champ / interim_champ

Collection is bounded to the 17 men's IBF weight classes used by the official UI.
No rank slot, fighter, date or vacancy is inferred.

Timing policy:
- nominal rating period comes from rating_month;
- safe_effective_date is one calendar day after the official post_date, avoiding
  same-day intraday look-ahead ambiguity.
"""
from __future__ import annotations
import datetime as dt,json,re,urllib.parse,urllib.request,urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'rankings';OUT.mkdir(parents=True,exist_ok=True)
BASE='https://www.ibf-usba-boxing.com'
UA='Mozilla/5.0 AppwizaBoxingIBFArchive/1.1'

WEIGHTS=[
 ('heavyweight','heavyweight'),
 ('cruiserweight','cruiserweight'),
 ('light-heavyweight','light heavyweight'),
 ('super-middleweight','super middleweight'),
 ('middleweight','middleweight'),
 ('jr-middleweight','junior middleweight'),
 ('welterweight','welterweight'),
 ('jr-welterweight','junior welterweight'),
 ('lightweight','lightweight'),
 ('jr-lightweight','junior lightweight'),
 ('featherweight','featherweight'),
 ('jr-featherweight','junior featherweight'),
 ('bantamweight','bantamweight'),
 ('jr-bantamweight','junior bantamweight'),
 ('flyweight','flyweight'),
 ('jr-flyweight','junior flyweight'),
 ('mini-flyweight','minimumweight'),
]

def get_json(url,limit=20_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json','Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=50) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('response too large')
        return json.loads(raw.decode('utf-8'))

def clean(s):
    return re.sub(r'\s+',' ',str(s or '')).strip(' .-–')

def parse_date(value):
    s=clean(value)
    if not s:return None
    candidates=[
      ('%Y-%m-%dT%H:%M:%S',s[:19]),
      ('%Y-%m-%d',s[:10]),
      ('%m/%d/%Y',s),
      ('%m/%d/%y',s),
      ('%B %d, %Y',s),
      ('%b %d, %Y',s),
    ]
    for fmt,v in candidates:
        try:return dt.datetime.strptime(v,fmt).date()
        except Exception:pass
    try:return dt.datetime.fromisoformat(s.replace('Z','+00:00')).date()
    except Exception:return None

def period(value):
    s=re.sub(r'\D','',str(value or ''))
    if len(s)<6:return None
    y=int(s[:4]);m=int(s[4:6])
    if not (2000<=y<=dt.date.today().year and 1<=m<=12):return None
    return y,m

def boxer_token(token):
    parts=[clean(x) for x in str(token or '').split(',')]
    name=parts[0] if parts else ''
    country=parts[1] if len(parts)>1 else None
    if not name:return None
    return name,(country or None)

def main():
    allr=[];allc=[];docs=[];failures=[]
    for slug,division in WEIGHTS:
        url=BASE+'/wp-json/ratings/v1/filter?'+urllib.parse.urlencode({'weight':slug,'org':'ibf','ppp':-1})
        try:
            items=get_json(url)
        except Exception as e:
            failures.append({'division':division,'url':url,'error':repr(e)});continue
        if not isinstance(items,list):
            failures.append({'division':division,'url':url,'error':'non-list response'});continue
        for item in items:
            per=period(item.get('rating_month'))
            posted=parse_date(item.get('post_date'))
            if not per or not posted:continue
            year,month=per
            safe=(posted+dt.timedelta(days=1)).isoformat()
            raw_slots=str(item.get('ratings') or '').split(';')
            ranks=[]
            for i in range(15):
                tok=raw_slots[i] if i<len(raw_slots) else ''
                parsed=boxer_token(tok)
                if not parsed:continue # explicit NOT RATED / vacant slot
                name,country=parsed
                ranks.append({'division':division,'rank':i+1,'name':name,'country':country,
                              'rating_year':year,'rating_month':month,
                              'published_date':posted.isoformat(),'safe_effective_date':safe,
                              'source_url':url,'source_kind':'official_ibf_ratings_api'})
            champs=[]
            champ_raw=str(item.get('champ') or '').split(';')[0]
            ch=boxer_token(champ_raw)
            if ch:
                name,country=ch
                champs.append({'division':division,'status':'Champion','name':name,'country':country,
                               'rating_year':year,'rating_month':month,
                               'published_date':posted.isoformat(),'safe_effective_date':safe,
                               'source_url':url,'source_kind':'official_ibf_ratings_api'})
            interim_raw=str(item.get('interim_champ') or '').split(';')[0]
            ich=boxer_token(interim_raw)
            if ich:
                name,country=ich
                champs.append({'division':division,'status':'Interim Champion','name':name,'country':country,
                               'rating_year':year,'rating_month':month,
                               'published_date':posted.isoformat(),'safe_effective_date':safe,
                               'source_url':url,'source_kind':'official_ibf_ratings_api'})
            allr.extend(ranks);allc.extend(champs)
            docs.append({'division':division,'rating_year':year,'rating_month':month,
                         'rating_month_raw':item.get('rating_month'),'published_date':posted.isoformat(),
                         'safe_effective_date':safe,'ranking_rows':len(ranks),'champions':len(champs),'source_url':url})

    # Exact duplicates collapse. If the API ever exposes conflicting names for
    # one body/date/division/rank, quarantine that slot instead of choosing.
    grouped={}
    for r in allr:grouped.setdefault((r['safe_effective_date'],r['division'],r['rank']),[]).append(r)
    clean_rows=[];conflicts=[]
    for key,group in grouped.items():
        names={re.sub(r'[^a-z0-9]+','',x['name'].lower()) for x in group}
        if len(names)>1:
            conflicts.append({'slot':key,'rows':group})
        else:
            clean_rows.append(sorted(group,key=lambda x:(x['rating_year'],x['rating_month'],x['name']))[-1])
    ranks=sorted(clean_rows,key=lambda x:(x['safe_effective_date'],x['division'],x['rank']))

    uniqc={}
    for r in allc:
        uniqc[(r['safe_effective_date'],r['division'],r['status'],re.sub(r'[^a-z0-9]+','',r['name'].lower()))]=r
    champs=sorted(uniqc.values(),key=lambda x:(x['safe_effective_date'],x['division'],x['status'],x['name']))

    (OUT/'ibf_monthly_rankings.json').write_text(json.dumps(ranks,indent=2,ensure_ascii=False))
    (OUT/'ibf_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    years=sorted({r['rating_year'] for r in ranks})
    periods=sorted({(r['rating_year'],r['rating_month']) for r in ranks})
    meta={
      'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'weight_classes_requested':len(WEIGHTS),'api_failures':failures,
      'division_period_documents':len(docs),'ranking_rows':len(ranks),'champion_rows':len(champs),
      'years':years,'rating_periods':len(periods),
      'date_min':min((r['safe_effective_date'] for r in ranks),default=None),
      'date_max':max((r['safe_effective_date'] for r in ranks),default=None),
      'quarantined_conflicting_rank_slots':len(conflicts),'conflicting_rank_slot_sample':conflicts[:30],
      'policy':'Official IBF public ratings API only. Safe effective date is one day after official post_date. Empty rank slots remain missing; nothing is inferred.'
    }
    (OUT/'ibf_monthly_rankings_meta.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['weight_classes_requested','division_period_documents','ranking_rows','champion_rows','years','rating_periods','date_min','date_max','quarantined_conflicting_rank_slots']},indent=2))

if __name__=='__main__':main()
