#!/usr/bin/env python3
"""Audit provenance available for historical ProBoxingOdds quotes.

Read-only against the server boxing DB. This report does NOT validate quotes.
It identifies whether any existing fields can prove a quote was available
before the associated event, and isolates candidate rows for a stricter pass.
"""
from __future__ import annotations
import collections,datetime as dt,json,re,sqlite3
from pathlib import Path

DB=Path('/home/anestishkurti92/boxing-research/boxing.sqlite3')
OUT=Path('boxing/public_reports/HISTORICAL_ODDS_PROVENANCE_AUDIT.json')

TIME_KEYS=('fetched_at','captured_at','collected_at','created_at','updated_at','timestamp','observed_at','quote_time','odds_time','published_at')
URL_KEYS=('url','source_url','page_url','href','source_page')
DATE_RE=re.compile(r'^\d{4}-\d{2}-\d{2}')

def cols(d,table):
    return [r[1] for r in d.execute(f'pragma table_info({table})')]

def parse_json(v):
    try:return json.loads(v) if v else {}
    except Exception:return {}

def flat_keys(obj,prefix=''):
    out=set()
    if isinstance(obj,dict):
        for k,v in obj.items():
            key=f'{prefix}.{k}' if prefix else str(k)
            out.add(key)
            if isinstance(v,(dict,list)):out|=flat_keys(v,key)
    elif isinstance(obj,list):
        for v in obj[:20]:
            if isinstance(v,(dict,list)):out|=flat_keys(v,prefix+'[]')
    return out

def value_for_keys(obj,names):
    found=[]
    def walk(x,path=''):
        if isinstance(x,dict):
            for k,v in x.items():
                p=f'{path}.{k}' if path else k
                if str(k).casefold() in names and v not in (None,''):
                    found.append((p,v))
                if isinstance(v,(dict,list)):walk(v,p)
        elif isinstance(x,list):
            for i,v in enumerate(x[:100]):
                if isinstance(v,(dict,list)):walk(v,f'{path}[{i}]')
    walk(obj)
    return found

def main():
    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=120);d.row_factory=sqlite3.Row
    tables={r[0] for r in d.execute("select name from sqlite_master where type='table'")}
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'policy':'Discovery only. No historical quote is validated unless evidence independently proves that exact bookmaker/selection/price was available before the event.',
      'tables':{}
    }
    for t in ('odds','source_rows','priced_bout_research','reported_historical_odds'):
        if t in tables:
            report['tables'][t]={'columns':cols(d,t),'rows':d.execute(f'select count(*) from {t}').fetchone()[0]}

    odds_cols=set(cols(d,'odds')) if 'odds' in tables else set()
    source_cols=set(cols(d,'source_rows')) if 'source_rows' in tables else set()

    # Inspect direct odds-table metadata.
    direct_time_cols=sorted(c for c in odds_cols if c.casefold() in TIME_KEYS or any(k in c.casefold() for k in ('time','date','fetch','capture','collect','observ')))
    direct_url_cols=sorted(c for c in odds_cols if c.casefold() in URL_KEYS or 'url' in c.casefold())
    report['odds_direct_metadata']={'time_like_columns':direct_time_cols,'url_like_columns':direct_url_cols}

    where=" where source='proboxingodds'" if 'source' in odds_cols else ''
    odds_rows=d.execute('select rowid as _rowid,* from odds'+where+' limit 50000').fetchall() if 'odds' in tables else []
    direct_time_nonempty=collections.Counter()
    direct_url_nonempty=collections.Counter()
    direct_samples=[]
    for r in odds_rows:
        item=dict(r)
        for c in direct_time_cols:
            if item.get(c) not in (None,''):direct_time_nonempty[c]+=1
        for c in direct_url_cols:
            if item.get(c) not in (None,''):direct_url_nonempty[c]+=1
        if len(direct_samples)<20 and any(item.get(c) not in (None,'') for c in direct_time_cols+direct_url_cols):
            direct_samples.append({k:item.get(k) for k in ['_rowid','bout_id','bookmaker','selection','decimal_price',*direct_time_cols,*direct_url_cols] if k in item})
    report['odds_direct_metadata'].update({
      'rows_scanned':len(odds_rows),
      'nonempty_time_like':dict(direct_time_nonempty),
      'nonempty_url_like':dict(direct_url_nonempty),
      'samples':direct_samples
    })

    # Inspect ProBoxingOdds source_rows payloads without assuming a schema.
    kinds=collections.Counter();payload_keys=collections.Counter()
    payload_time_paths=collections.Counter();payload_url_paths=collections.Counter()
    archive_url_rows=0; payload_samples=[]
    if 'source_rows' in tables and {'source','data'}<=source_cols:
        q="select rowid as _rowid,* from source_rows where source='proboxingodds' order by rowid"
        for r in d.execute(q):
            x=dict(r);k=str(x.get('kind') or '');kinds[k]+=1
            obj=parse_json(x.get('data'))
            for key in flat_keys(obj):payload_keys[key]+=1
            tv=value_for_keys(obj,set(TIME_KEYS))
            uv=value_for_keys(obj,set(URL_KEYS))
            for p,v in tv:payload_time_paths[p]+=1
            for p,v in uv:
                payload_url_paths[p]+=1
                if 'web.archive.org' in str(v):archive_url_rows+=1
            if len(payload_samples)<30 and (tv or uv):
                payload_samples.append({
                  'rowid':x.get('_rowid'),'source_id':x.get('source_id'),'kind':k,
                  'time_values':tv[:10],'url_values':uv[:10]
                })
    report['source_rows_payloads']={
      'kinds':dict(kinds),
      'top_payload_keys':payload_keys.most_common(120),
      'time_like_paths':dict(payload_time_paths),
      'url_like_paths':dict(payload_url_paths),
      'rows_with_wayback_url_values':archive_url_rows,
      'samples':payload_samples
    }

    # Determine event-date linkage and any candidate provable timing from direct
    # structured fields only. Collector timestamps created after the event are
    # explicitly NOT accepted as quote-time proof.
    matchup={}
    if 'source_rows' in tables and {'source','kind','source_id','data'}<=source_cols:
        for r in d.execute("select source_id,data from source_rows where source='proboxingodds' and kind='matchup'"):
            matchup[str(r['source_id'])]=parse_json(r['data'])
    candidate=collections.Counter();candidate_samples=[]
    for r in odds_rows:
        x=dict(r);bid=str(x.get('bout_id') or '')
        event=str((matchup.get(bid) or {}).get('event_date') or '')
        if not DATE_RE.match(event):continue
        for c in direct_time_cols:
            raw=str(x.get(c) or '')
            m=re.search(r'(\d{4}-\d{2}-\d{2})',raw)
            if not m:continue
            day=m.group(1)
            if day<event:
                candidate[f'direct_{c}_before_event']+=1
                if len(candidate_samples)<30:
                    candidate_samples.append({'quote_rowid':x.get('_rowid'),'bout_id':bid,'event_date':event,'field':c,'value':raw,
                                              'bookmaker':x.get('bookmaker'),'selection':x.get('selection'),'decimal_price':x.get('decimal_price')})
    report['candidate_pre_event_timing_fields']={
      'counts':dict(candidate),
      'samples':candidate_samples,
      'warning':'These are candidates only. A database/collector timestamp must be shown to represent the original quote observation, not a later import or rebuild time, before it can validate a historical price.'
    }
    d.close()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({
      'tables':report['tables'],
      'odds_direct_metadata':report['odds_direct_metadata'],
      'source_rows_payloads':{k:v for k,v in report['source_rows_payloads'].items() if k!='samples'},
      'candidate_pre_event_timing_fields':report['candidate_pre_event_timing_fields']
    },indent=2,ensure_ascii=False))

if __name__=='__main__':main()
