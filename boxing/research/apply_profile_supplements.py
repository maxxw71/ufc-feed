#!/usr/bin/env python3
"""Apply verified static profile supplements without overwriting existing data."""
from __future__ import annotations
import json,os,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'boxing.sqlite3'
SUP=Path(os.environ.get('BOXING_PROFILE_SUPPLEMENTS',str(ROOT.parent/'profile_supplements'/'verified_profiles.jsonl')))
QUAR=Path(os.environ.get('BOXING_PROFILE_QUARANTINE',str(ROOT.parent/'profile_supplements'/'profile_field_quarantine.jsonl')))
REPORT=ROOT/'PROFILE_SUPPLEMENT_APPLY_REPORT.json'
FIELDS=('born','height_cm','reach_cm','stance','nationality')

def main():
    report={'rows':0,'matched':0,'updated_profiles':0,'field_updates':{k:0 for k in FIELDS},'missing_targets':0,
            'quarantine_rows':0,'quarantined_profiles':0,'quarantined_fields':{k:0 for k in FIELDS},
            'missing_quarantine_targets':0}
    if not SUP.exists():
        REPORT.write_text(json.dumps(report,indent=2));print(json.dumps(report));return
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    for line in SUP.read_text().splitlines():
        if not line.strip():continue
        report['rows']+=1;x=json.loads(line);sid=x.get('target_source_id')
        row=con.execute('SELECT * FROM normalized_fighters WHERE source_id=?',(sid,)).fetchone()
        if not row:
            report['missing_targets']+=1;continue
        report['matched']+=1;updates={};fields=x.get('fields') or {}
        for field in FIELDS:
            current=row[field]
            if current not in (None,''):continue
            value=fields.get(field)
            if value in (None,''):continue
            if field=='height_cm' and not 120<=float(value)<=250:continue
            if field=='reach_cm' and not 120<=float(value)<=270:continue
            if field=='stance' and value not in {'orthodox','southpaw','switch'}:continue
            updates[field]=value
        if not updates:continue
        parts=[];params=[]
        for k,v in updates.items():
            parts.append(f'{k}=?');params.append(v);report['field_updates'][k]+=1
        quality=(row['quality'] or '')
        marker='profile_supplement_exact_identity'
        if marker not in quality:
            parts.append('quality=?');params.append((quality+';'+marker).strip(';'))
        params.append(sid)
        con.execute('UPDATE normalized_fighters SET '+','.join(parts)+' WHERE source_id=?',params)
        report['updated_profiles']+=1
    # Explicit invalid-field quarantine runs last and therefore wins over any
    # stale base value or supplement. A field remains null until its quarantine
    # row is deliberately removed after independent verification.
    if QUAR.exists():
        for line in QUAR.read_text().splitlines():
            if not line.strip():continue
            report['quarantine_rows']+=1
            try:q=json.loads(line)
            except Exception:continue
            sid=q.get('target_source_id');fields=q.get('fields') or []
            row=con.execute('SELECT * FROM normalized_fighters WHERE source_id=?',(sid,)).fetchone()
            if not row:
                report['missing_quarantine_targets']+=1;continue
            updates=[]
            for field in fields:
                if field not in FIELDS:continue
                if row[field] not in (None,''):
                    updates.append(field)
            if not updates:continue
            parts=[f'{k}=NULL' for k in updates]
            quality=(row['quality'] or '')
            marker='profile_field_quarantine'
            if marker not in quality:
                parts.append('quality=?')
                params=[(quality+';'+marker).strip(';'),sid]
            else:
                params=[sid]
            con.execute('UPDATE normalized_fighters SET '+','.join(parts)+' WHERE source_id=?',params)
            report['quarantined_profiles']+=1
            for k in updates:report['quarantined_fields'][k]+=1
    con.commit();con.close()
    REPORT.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
