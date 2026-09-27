#!/usr/bin/env python3
"""Audit unresolved staged legacy CompuBox pages for row-label identity candidates."""
from __future__ import annotations
import argparse,json,re,sqlite3
from pathlib import Path
from import_archived_compubox_rounds import surname,unique_from_rows,pair_candidates

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'public_punch_audit'/'legacy_compubox_fetched_candidates.json'
OUT=ROOT/'public_punch_audit'/'legacy_compubox_identity_audit.json'

def total_section(text):
    for pat in (r'Total Punches Landed\s*/\s*Thrown',r'Total Punches Landed/Thrown'):
        m=re.search(pat,text,re.I)
        if m:
            s=text[m.end():]
            stops=[]
            for q in (r'Total Jabs',r'Jabs Landed',r'Power Punches',r'Final Punch'):
                z=re.search(q,s,re.I)
                if z:stops.append(z.start())
            return s[:min(stops)] if stops else s
    return ''

def labels(sec):
    out=[]
    # More permissive: capture the final 1-5 words before a run of at least four L/T pairs.
    pat=re.compile(r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 .\'’\-]{0,70}?)\s+((?:\d{1,3}\s*/\s*\d{1,3}\s+){3,}\d{1,3}\s*/\s*\d{1,3})',re.I)
    for m in pat.finditer(sec):
        raw=' '.join(m.group(1).split())
        toks=raw.split()
        raw=' '.join(toks[-5:])
        pairs=re.findall(r'\d{1,3}\s*/\s*\d{1,3}',m.group(2))
        out.append({'raw':raw,'surname':surname(raw),'pairs':len(pairs)})
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);args=ap.parse_args()
    src=json.loads(IN.read_text())
    db=sqlite3.connect(args.db);db.row_factory=sqlite3.Row
    rows=[]
    for x in src.get('rows') or []:
        if x.get('status')!='fetched':continue
        text=str(x.get('text_sample') or '')
        ls=labels(total_section(text))
        cand=[]
        for i,a in enumerate(ls):
            for b in ls[i+1:]:
                if not a['surname'] or not b['surname'] or a['surname']==b['surname']:continue
                prs=pair_candidates(db,a['surname'],b['surname'])
                pair=unique_from_rows(prs)
                if pair:
                    cand.append({'label_a':a,'label_b':b,'pair_date':pair[0],'fighter_a':pair[1],'fighter_b':pair[2],
                                 'db_rows':len(prs)})
        rows.append({'canonical_key':x.get('canonical_key'),'title':x.get('title'),'labels':ls,'unique_pair_candidates':cand,
                     'text_head':text[:1200]})
    db.close()
    out={'pages':len(rows),'pages_with_labels':sum(bool(x['labels']) for x in rows),
         'pages_with_unique_pair_candidate':sum(bool(x['unique_pair_candidates']) for x in rows),
         'rows':rows}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({k:out[k] for k in ('pages','pages_with_labels','pages_with_unique_pair_candidate')},indent=2))
if __name__=='__main__':main()
