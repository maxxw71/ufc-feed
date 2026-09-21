#!/usr/bin/env python3
"""Rebuild WBO ranking rows from already-verified official PDF URLs.

This is a repair/validation pass, not discovery. It reuses the source_url and
official publication date already stored in wbo_monthly_rankings_meta.json,
then reparses those PDFs with the current parser and integrity gates.
"""
from __future__ import annotations
import datetime as dt,hashlib,json,re
from pathlib import Path
from collect_wbo_rankings import OUT,get,parse_pdf,BASE

META=OUT/'wbo_monthly_rankings_meta.json'

def main():
    if not META.exists():
        raise SystemExit('missing existing WBO metadata')
    old=json.loads(META.read_text())
    docs=[];allr=[];allc=[]
    seen=set()
    for d in old.get('documents') or []:
        if d.get('status')!='parsed':continue
        url=d.get('source_url');published=d.get('published_date');article=d.get('article_url') or ''
        if not url or not published:continue
        key=(url,published)
        if key in seen:continue
        seen.add(key)
        try:
            post_date=dt.date.fromisoformat(published)
            final,raw=get(url)
            if not raw.lstrip().startswith(b'%PDF') or len(raw)<20_000:
                raise ValueError('not a valid official PDF')
            ranks,champs,pages=parse_pdf(raw,post_date,final,article)
            allr.extend(ranks);allc.extend(champs)
            docs.append({'article_url':article,'published_date':published,'status':'parsed',
                         'source_url':final,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                         'pages':pages,'ranking_rows':len(ranks),'champions':len(champs)})
        except Exception as e:
            docs.append({'article_url':article,'published_date':published,'status':'parse_review',
                         'source_url':url,'error':str(e)[:300]})

    # If several nominal periods share one publication date, only the latest
    # rating period is usable at that timestamp.
    latest_period={}
    for r in allr+allc:
        key=r['safe_effective_date'];per=(int(r.get('rating_year') or 0),int(r.get('rating_month') or 0))
        if per>latest_period.get(key,(0,0)):latest_period[key]=per
    filtered=[r for r in allr if (int(r.get('rating_year') or 0),int(r.get('rating_month') or 0))==latest_period.get(r['safe_effective_date'],(0,0))]
    filteredc=[r for r in allc if (int(r.get('rating_year') or 0),int(r.get('rating_month') or 0))==latest_period.get(r['safe_effective_date'],(0,0))]

    grouped={}
    for r in filtered:grouped.setdefault((r['safe_effective_date'],r['division'],int(r['rank'])),[]).append(r)
    ranks=[];conflicts=[]
    for key,group in grouped.items():
        names={re.sub(r'[^a-z0-9]+','',str(x.get('name') or '').lower()) for x in group}
        if len(names)>1:
            conflicts.append({'slot':key,'rows':group})
        else:
            ranks.append(sorted(group,key=lambda x:(x.get('source_url') or '',x.get('name') or ''))[-1])

    uniqc={}
    for r in filteredc:
        uniqc[(r['safe_effective_date'],r['division'],r['status'],r['name'])]=r
    champs=list(uniqc.values())
    ranks.sort(key=lambda x:(x['safe_effective_date'],x['division'],x['rank']))
    champs.sort(key=lambda x:(x['safe_effective_date'],x['division'],x['status'],x['name']))

    (OUT/'wbo_monthly_rankings.json').write_text(json.dumps(ranks,indent=2,ensure_ascii=False))
    (OUT/'wbo_monthly_champions.json').write_text(json.dumps(champs,indent=2,ensure_ascii=False))
    meta={
      'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'documents':docs,
      'parsed_documents':sum(d.get('status')=='parsed' for d in docs),
      'ranking_rows':len(ranks),'champion_rows':len(champs),
      'quarantined_conflicting_rank_slots':len(conflicts),
      'conflicting_rank_slot_sample':conflicts[:30],
      'safe_date_policy':'Official WBO article publication date; if multiple rating periods share one publication date, only the later nominal period is retained; conflicting slots are quarantined.',
      'archive_url':BASE+'/explanations/','rebuild_mode':'known_verified_official_pdfs'
    }
    META.write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    print(json.dumps({k:meta[k] for k in ['parsed_documents','ranking_rows','champion_rows','quarantined_conflicting_rank_slots','rebuild_mode']},indent=2))

if __name__=='__main__':main()
