#!/usr/bin/env python3
"""Build a sourced, date-gated professional pre-UFC / regional fight ledger.

Input A: frozen DWCS regional *prior fight* archive (derived, not individual
fight-page-verified); Input B: explicitly cross-checked pro-fight rows.
Original UFCStats data and U1–U12 models are never edited.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,re
from collections import Counter
from datetime import date,datetime,timezone
from pathlib import Path

COLUMNS=['event_date','fighter','opponent','result','method','round','organization',
         'source','source_url','source_url_2','verification','source_cutoff_date']
VALID_RESULTS={'W','L','D','NC'}
def clean(s):return re.sub(r'[^a-z0-9]+',' ',str(s or '').casefold()).strip()
def dt(s):
    try:return date.fromisoformat(str(s)[:10])
    except (ValueError,TypeError):return None
def finish(s):
    v=str(s or '').lower()
    if 'sub' in v or 'choke' in v:return 'Submission'
    if 'ko' in v or 'tko' in v or 'knockout' in v:return 'KO/TKO'
    if 'dec' in v:return 'Decision'
    if 'draw' in v:return 'Draw'
    if 'no contest' in v:return 'NC'
    return 'Other'
def rows_from_archive(path):
    with path.open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            fight_date=dt(row.get('prior_fight_date'))
            cutoff=dt(row.get('dwcs_date'))
            result=str(row.get('result') or '').strip().upper()
            fighter=str(row.get('fighter') or '').strip()
            opponent=str(row.get('opponent') or '').strip()
            if not fight_date or not cutoff or fight_date>=cutoff or result not in VALID_RESULTS:
                continue
            if not clean(fighter) or not clean(opponent) or clean(fighter)==clean(opponent):continue
            yield {'event_date':fight_date.isoformat(),'fighter':fighter,'opponent':opponent,
                   'result':result,'method':finish(row.get('method')),'round':'',
                   'organization':str(row.get('organization') or ''),
                   'source':'dwcs_prefight_regional_archive',
                   'source_url':'https://github.com/maxxw71/ufc-feed/blob/main/dwcs/research/regional_history/dwcs_prefight_regional_fight_detail.csv',
                   'source_url_2':'','verification':'archive_derived',
                   'source_cutoff_date':cutoff.isoformat()}
def rows_from_verified(path):
    with path.open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            d=dt(row.get('event_date'))
            if not d or not clean(row.get('fighter')) or not clean(row.get('opponent')):continue
            if clean(row.get('fighter'))==clean(row.get('opponent')):continue
            if row.get('result') not in VALID_RESULTS:continue
            if not str(row.get('source_url','')).startswith('https://'):continue
            if str(row.get('verification',''))!='independently_checked':continue
            rr={k:str(row.get(k) or '') for k in COLUMNS}
            rr['event_date']=d.isoformat()
            rr['method']=finish(row.get('method'))
            rr['source']=rr['source'] or 'independent_fight_record'
            yield rr
def fingerprint(r):
    # Deduplicate identical fighter and opponent/date, not all fights on a
    # fighter/day. Opponent aliases are not fuzzy-matched automatically.
    return (r['event_date'],clean(r['fighter']),clean(r['opponent']))
def merge(archive,verified):
    chosen={};conflicts=[]
    for r in [*archive,*verified]:
        k=fingerprint(r)
        old=chosen.get(k)
        if old is None:
            chosen[k]=r
        elif old['verification']=='independently_checked':
            if r['verification']=='independently_checked' and (old['method'],old['result'])!=(r['method'],r['result']):
                conflicts.append({'key':k,'existing':old,'incoming':r})
        elif r['verification']=='independently_checked':
            chosen[k]=r
        elif (old['method'],old['result'])!=(r['method'],r['result']):
            # Ambiguity is quarantined, never silently resolved by row order.
            conflicts.append({'key':k,'existing':old,'incoming':r})
            chosen.pop(k,None)
    return sorted(chosen.values(),key=lambda r:(r['fighter'].lower(),r['event_date'],r['opponent'].lower())),conflicts
def build(archive,verified,outdir):
    src=[Path(archive),Path(verified)]
    for p in src:
        if not p.is_file():raise FileNotFoundError(str(p))
    raw_archive=list(rows_from_archive(src[0]))
    raw_verified=list(rows_from_verified(src[1]))
    result,conflicts=merge(raw_archive,raw_verified)
    outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    path=outdir/'regional_fight_history.csv';tmp=path.with_suffix('.tmp')
    with tmp.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(result)
    tmp.replace(path)
    report={'build_at':datetime.now(timezone.utc).isoformat(),
      'archive_input_rows_valid':len(raw_archive),'independently_checked_rows':len(raw_verified),
      'merged_fighter_fight_rows':len(result),'fighters':len({clean(r['fighter']) for r in result}),
      'submission_wins':sum(r['result']=='W' and r['method']=='Submission' for r in result),
      'submission_losses':sum(r['result']=='L' and r['method']=='Submission' for r in result),
      'sources':dict(Counter(r['source'] for r in result)),
      'verification':dict(Counter(r['verification'] for r in result)),
      'conflicts_quarantined':len(conflicts),
      'archive_sha256':hashlib.sha256(src[0].read_bytes()).hexdigest(),
      'verified_sha256':hashlib.sha256(src[1].read_bytes()).hexdigest(),
      'ledger_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'scope':'DWCS participant archived prior regional fights plus individually checked professional fights; not complete all-pro career history',
      'original_ufcstats_unchanged':True}
    for fname,obj in [('regional_history_manifest.json',report),('regional_history_conflicts.json',conflicts)]:
        target=outdir/fname;tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2,default=str)+'\n');tmp.replace(target)
    print('REGIONAL_BUILD',json.dumps(report,sort_keys=True))
    return report
if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--archive',type=Path,required=True)
    ap.add_argument('--verified',type=Path,required=True)
    ap.add_argument('--outdir',type=Path,required=True)
    args=ap.parse_args()
    build(args.archive,args.verified,args.outdir)
