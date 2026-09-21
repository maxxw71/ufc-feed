#!/usr/bin/env python3
"""Discover historical CompuBox fight-stat pages only from already captured links.

No numeric IDs or URLs are guessed. New candidates must appear as links inside
previously captured public evidence pages. This keeps expansion provenance
traceable and bounded.
"""
from __future__ import annotations
import argparse,datetime as dt,json,re,sqlite3
from urllib.parse import urlsplit

DB='/home/anestishkurti92/boxing-research/boxing.sqlite3'
FIGHT_PATTERNS=[
    re.compile(r'/compubox-stats-[^/?#]+/?$',re.I),
    re.compile(r'/throwdownfantasy-com-stats-[^/?#]+/?$',re.I),
    re.compile(r'/stat_files/[A-Za-z0-9_.-]+\.html$',re.I),
    re.compile(r'/news\.php\?.*(?:news_id=\d+|news=[^&#]+)',re.I),
]
BLOCK_PATTERNS=re.compile(r'/(?:category|tag|author|feed|about|contact|clients|store|services|bio|links|recordbook)(?:/|$)',re.I)

def likely(url):
    s=str(url or '')
    if not s.startswith(('https://web.archive.org/','http://web.archive.org/')):
        return False
    low=s.lower()
    if '/screenshot/' in low or '/mailto:' in low or 'mailto:' in low:
        return False
    # only archived CompuBox-owned pages
    if 'compuboxonline.com' not in s.lower():
        return False
    if BLOCK_PATTERNS.search(s):
        return False
    return any(p.search(s) for p in FIGHT_PATTERNS)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=500);args=ap.parse_args()
    d=sqlite3.connect(DB,timeout=60);d.row_factory=sqlite3.Row
    existing={r[0] for r in d.execute("select url from external_evidence_queue")}
    added=[];seen=set(existing)
    rows=d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id").fetchall()
    for row in rows:
        try:payload=json.loads(row['data'])
        except Exception:continue
        for url in payload.get('stat_links') or []:
            if len(added)>=args.limit:break
            if url in seen or not likely(url):continue
            seen.add(url)
            d.execute("insert or ignore into external_evidence_queue(url,kind,discovered_on,label,status) values(?,?,?,?,?)",
                      (url,'punch',dt.datetime.now(dt.timezone.utc).isoformat(),f'linked from {row["source_id"]}','pending'))
            if d.total_changes:added.append({'url':url,'from':row['source_id']})
        if len(added)>=args.limit:break
    d.commit()
    print(json.dumps({'captured_sources_scanned':len(rows),'existing_queue':len(existing),'new_historical_punch_links':len(added),'sample':added[:40]},indent=2))

if __name__=='__main__':main()
