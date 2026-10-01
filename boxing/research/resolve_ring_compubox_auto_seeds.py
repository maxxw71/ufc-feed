#!/usr/bin/env python3
"""Resolve discovered Ring CompuBox post-fight pages into strict seed candidates.

Only explicit post-fight title forms like "A vs B - CompuBox Punch Stats" are
eligible. Names are parsed from the rendered H1, then a unique exact normalized
pair/date must exist in the local bouts table within article date or the prior
2 days. Rounds come from the verified local bout record. No punch values are
read or trusted here.
"""
from __future__ import annotations
import datetime as dt,json,re,sqlite3,unicodedata,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
DISC=ROOT/'public_punch_audit'/'ring_compubox_sitemap_discovery.json'
MANUAL=ROOT/'research'/'ring_compubox_seed_urls.json'
OUT=ROOT/'research'/'ring_compubox_auto_seed_urls.json'
DB=ROOT/'research'/'boxing.sqlite3'
UA='Mozilla/5.0 AppwizaRingAutoSeedResolver/1.0'
def nk(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',x)
def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read(3_500_001)
        if len(raw)>3_500_000:raise ValueError('page too large')
        return r.geturl(),raw
def article_date(soup):
    for tag in soup.find_all('meta'):
        k=(tag.get('property') or tag.get('name') or '').casefold()
        if k in {'article:published_time','datepublished','date','publishdate'}:
            m=re.search(r'(20\d{2}-\d{2}-\d{2})',str(tag.get('content') or ''))
            if m:return m.group(1)
    txt=' '.join(soup.stripped_strings)
    m=re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}\b',txt,re.I)
    if m:
        try:return dt.datetime.strptime(m.group(0).title(),'%b %d, %Y').date().isoformat()
        except Exception:pass
    return None
def title_pair(soup):
    h=soup.find('h1')
    title=' '.join(h.stripped_strings).strip() if h else ''
    # Require an explicit completed-fight stats title, not CompuBox Corner,
    # previews, or multi-fight "by the numbers" pieces.
    pats=[
      r'^(.+?)\s+vs\.?\s+(.+?)\s*[-–:]\s*CompuBox\s+Punch\s+Stats\b',
      r'^(.+?)\s+vs\.?\s+(.+?)\s*[-–:]\s*Compu\s*Box\s+Punch\s+Stats\b',
      r'^(.+?)\s+vs\.?\s+(.+?)\s*[-–:]\s*Compu\s*Box\s+Stats\b'
    ]
    for pat in pats:
        m=re.search(pat,title,re.I)
        if m:return title,m.group(1).strip(),m.group(2).strip()
    return title,None,None
def local_match(con,a,b,pub):
    want=sorted([nk(a),nk(b)])
    p=dt.date.fromisoformat(pub)
    matches=[]
    for delta in (0,-1,-2):
        day=(p+dt.timedelta(days=delta)).isoformat()
        for r in con.execute("select date,boxer_a,boxer_b,rounds,method,status from bouts where date=?",(day,)):
            if sorted([nk(r['boxer_a']),nk(r['boxer_b'])])!=want:continue
            try:rounds=int(r['rounds'] or 0)
            except Exception:rounds=0
            if rounds<1:continue
            matches.append({'bout_date':day,'rounds':rounds,'boxer_a':r['boxer_a'],'boxer_b':r['boxer_b'],
                            'method':r['method'],'status':r['status']})
    uniq={}
    for x in matches:uniq[(x['bout_date'],x['rounds'],nk(x['boxer_a']),nk(x['boxer_b']))]=x
    vals=list(uniq.values())
    return vals[0] if len(vals)==1 else None,vals
def main():
    disc=json.loads(DISC.read_text()) if DISC.exists() else {}
    manual=json.loads(MANUAL.read_text()) if MANUAL.exists() else {}
    existing={x.get('url') for x in manual.get('pages') or []}
    urls=disc.get('new_candidate_urls') or []
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    pages=[];diag=[]
    for u in urls:
        rec={'url':u}
        try:
            final,raw=fetch(u);soup=BeautifulSoup(raw,'lxml');title,a,b=title_pair(soup);pub=article_date(soup)
            rec.update({'final_url':final,'title':title,'article_date':pub,'fighters':[a,b] if a and b else None})
            if not a or not b:
                rec['status']='not_explicit_postfight_stats_title';diag.append(rec);continue
            if not pub:
                rec['status']='missing_article_date';diag.append(rec);continue
            match,allm=local_match(con,a,b,pub)
            rec['local_candidates']=allm
            if not match:
                rec['status']='no_unique_exact_local_pair_date';diag.append(rec);continue
            page={'url':final,'bout_date':match['bout_date'],'fighters':[a,b],'rounds':match['rounds'],
                  'auto_resolution':'explicit_ring_compubox_stats_title_plus_unique_local_date_pair'}
            if final in existing:
                rec['status']='already_seeded'
            else:
                pages.append(page);rec['status']='accepted_auto_seed';rec['resolved_seed']=page
        except Exception as e:
            rec.update({'status':'error','error':type(e).__name__+': '+str(e)[:220]})
        diag.append(rec)
    con.close()
    # URL de-dupe, deterministic output.
    uniq={x['url']:x for x in pages}
    pages=sorted(uniq.values(),key=lambda x:(x['bout_date'],x['url']))
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
         'discovered_new_urls':len(urls),'accepted_auto_seeds':len(pages),'pages':pages,'diagnostics':diag,
         'policy':'Explicit A-vs-B CompuBox Punch Stats title only; unique exact normalized local bout pair on article date or prior two days; rounds from local verified bout row. No punch values trusted by resolver.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({'discovered_new_urls':len(urls),'accepted_auto_seeds':len(pages),'pages':pages},indent=2))
if __name__=='__main__':main()
