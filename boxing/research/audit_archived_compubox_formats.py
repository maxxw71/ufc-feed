#!/usr/bin/env python3
"""Classify captured archived CompuBox pages by why the strict importer rejects them."""
from __future__ import annotations
import json,sqlite3
from pathlib import Path
from import_archived_compubox_rounds import parse_candidate,flatten,resolve_fight,section

DB='/home/anestishkurti92/boxing-research/boxing.sqlite3'
OUT=Path('/home/anestishkurti92/boxing-research/ARCHIVED_COMPUBOX_FORMAT_AUDIT.json')

d=sqlite3.connect(DB,timeout=120);d.row_factory=sqlite3.Row
rows=d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id").fetchall()
buckets={}
for row in rows:
    url=row['source_id']
    try:p=json.loads(row['data'])
    except Exception:continue
    parsed,err=parse_candidate(d,url,p)
    if parsed:continue
    text=flatten(p)
    key=err or 'unknown'
    item={
      'url':url,'title':p.get('title'),'table_count':len(p.get('tables') or []),
      'resolved_fight':bool(resolve_fight(d,text,p)),
      'has_total_heading':bool(section(text,'total')),
      'has_jab_heading':bool(section(text,'jab')),
      'has_power_heading':bool(section(text,'power')),
      'paragraphs':(p.get('relevant_paragraphs') or [])[:8],
      'flat_sample':text[:3000],
      'stat_links':(p.get('stat_links') or [])[:30]
    }
    buckets.setdefault(key,[]).append(item)
report={'captured_pages':len(rows),'rejected':sum(len(v) for v in buckets.values()),
        'counts':{k:len(v) for k,v in buckets.items()},
        'samples':{k:v[:25] for k,v in buckets.items()}}
OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps(report,indent=2,ensure_ascii=False))
