#!/usr/bin/env python3
from __future__ import annotations

import io,json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pypdf import PdfReader
try:
    import pdfplumber
except Exception:
    pdfplumber=None

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
MANIFEST=ROOT/'game_notes_availability_manifest.json'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
OUT_ROWS=PROC/'mls_game_notes_availability_rows.parquet'
OUT=PROC/'mls_game_notes_availability_features.parquet'
META=REP/'game_notes_availability_meta.json'
UA='Mozilla/5.0 AppwizaMLSGameNotes/1.0'

def now():return datetime.now(timezone.utc).isoformat()
def normspace(x):return re.sub(r'\s+',' ',str(x or '')).strip()
def reason_category(reason):
    n=normspace(reason).lower()
    if 'international' in n:return 'international_duty'
    if 'suspend' in n or 'red card' in n or 'yellow card' in n:return 'suspension'
    if 'concussion' in n or 'head' in n:return 'concussion'
    if 'illness' in n:return 'illness'
    if 'not due to injury' in n:return 'non_injury'
    return 'injury' if n else 'unspecified'

def fetch_pdf(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/pdf'})
    return urllib.request.urlopen(req,timeout=60).read()

def extract_report(text):
    text=text.replace('\u00a0',' ')
    m=re.search(r'PLAYER\s+AVAILABILITY\s+REPORT(.*?)(?:SOCIAL\s+MEDIA|PRONUNCIATION\s+GUIDE|2023\s+MLS\s+SCHEDULE|MLS\s+SCHEDULE)',text,re.I|re.S)
    if not m:return []
    sec=m.group(1)
    sec=re.sub(r'[\t\r]+',' ',sec)
    sec=re.sub(r' {2,}',' ',sec)
    # Normalize status headings, including "OUT - INTERNATIONAL DUTY".
    marks=[]
    for mm in re.finditer(r'\b(OUT(?:\s*-\s*INTERNATIONAL\s+DUTY)?|QUESTIONABLE)\s*:\s*',sec,re.I):
        marks.append((mm.start(),mm.end(),mm.group(1).upper()))
    if not marks:return []
    rows=[]
    for i,(s,e,status_label) in enumerate(marks):
        chunk=sec[e:(marks[i+1][0] if i+1<len(marks) else len(sec))].strip()
        chunk=re.sub(r'\n+',' | ',chunk)
        status='OUT' if status_label.startswith('OUT') else 'QUESTIONABLE'
        forced_intl='INTERNATIONAL DUTY' in status_label
        # Most game notes render "Player – Reason" repeatedly. Split at likely name/reason pairs
        # using dash characters while preserving multi-word reasons.
        parts=re.split(r'(?<=\w)\s*[–—-]\s*',chunk)
        if len(parts)>=2:
            j=0
            while j+1<len(parts):
                player=normspace(parts[j]).strip(' ,;')
                rest=normspace(parts[j+1])
                # Reason ends before the next capitalized player-name pattern if PDF flattened lines.
                nxt=re.search(r'(?=\b[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\'\.]+\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\'\.]+\s*[–—-])',rest)
                reason=rest[:nxt.start()].strip(' ,;') if nxt else rest.strip(' ,;')
                if forced_intl and not reason.lower().startswith('international'):reason='International duty'+((' - '+reason) if reason else '')
                if 2<=len(player)<=80 and reason:
                    rows.append({'status':status,'player_name':player,'reason':reason,'reason_category':reason_category(reason)})
                j+=2
        # Fallback line-style pattern
        if not rows:
            for mm in re.finditer(r'([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\'\. -]{2,70})\s*[–—-]\s*([^;|]{2,70})',chunk):
                reason=normspace(mm.group(2))
                if forced_intl:reason='International duty'+((' - '+reason) if reason else '')
                rows.append({'status':status,'player_name':normspace(mm.group(1)),'reason':reason,'reason_category':reason_category(reason)})
    # De-dupe obvious extraction repeats.
    out=[];seen=set()
    for r in rows:
        k=(r['status'],r['player_name'].lower(),r['reason'].lower())
        if k not in seen:seen.add(k);out.append(r)
    return out

def main():
    if not MANIFEST.exists():raise RuntimeError('Game-note manifest missing')
    if not BASE.exists():raise RuntimeError('MLS base missing')
    manifest=json.loads(MANIFEST.read_text())
    base=pd.read_parquet(BASE).copy()
    base['date_day']=pd.to_datetime(base.date,errors='coerce',utc=True).dt.strftime('%Y-%m-%d')
    rows=[];audit=[]
    for item in manifest:
        team=canon_team(item['team']);day=item['date'];url=item['url']
        try:
            data=fetch_pdf(url);reader=PdfReader(io.BytesIO(data))
            text=''
            if pdfplumber is not None:
                try:
                    with pdfplumber.open(io.BytesIO(data)) as pdf:
                        text='\n'.join((p.extract_text(layout=True) or '') for p in pdf.pages[:3])
                except Exception:
                    text=''
            if not text.strip():
                text='\n'.join((p.extract_text() or '') for p in reader.pages[:3])
            entries=extract_report(text)
            matches=base[(base.date_day.eq(day))&((base.home_team.map(canon_team).eq(team))|(base.away_team.map(canon_team).eq(team)))]
            mid=str(matches.iloc[0].match_id) if len(matches)==1 else None
            for e in entries:rows.append({'match_id':mid,'season':item['season'],'date':day,'team':team,'url':url,**e})
            probe=re.search(r'.{0,220}AVAILAB.{0,800}',normspace(text),re.I)
            audit.append({'team':team,'date':day,'url':url,'pdf_bytes':len(data),'pages':len(reader.pages),'matched_games':len(matches),'entries':len(entries),'availability_probe':probe.group(0)[:1000] if probe else None,'status':'OK' if mid and entries else 'PARTIAL'})
        except Exception as e:
            audit.append({'team':team,'date':day,'url':url,'status':'ERROR','error':type(e).__name__+': '+str(e)[:240]})
    raw=pd.DataFrame(rows)
    if len(raw):raw.to_parquet(OUT_ROWS,index=False)
    # Match-level sparse features. Unknown side remains NaN, never zero.
    feat=[]
    if len(raw):
        for mid,z in raw[raw.match_id.notna()].groupby('match_id'):
            b=base[base.match_id.astype(str).eq(str(mid))].iloc[0]
            row={'match_id':str(mid)}
            for side in ['home','away']:
                team=canon_team(b[f'{side}_team']);q=z[z.team.eq(team)]
                if q.empty:
                    row[f'{side}_game_notes_availability_report']=np.nan
                    continue
                row[f'{side}_game_notes_availability_report']=1.0
                row[f'{side}_game_notes_out_count']=float(q.status.eq('OUT').sum())
                row[f'{side}_game_notes_questionable_count']=float(q.status.eq('QUESTIONABLE').sum())
                for cat in ['injury','suspension','international_duty','illness','concussion','non_injury','unspecified']:
                    row[f'{side}_game_notes_{cat}_out_count']=float(((q.status.eq('OUT'))&(q.reason_category.eq(cat))).sum())
            feat.append(row)
    fdf=pd.DataFrame(feat)
    if fdf.empty:fdf=pd.DataFrame(columns=['match_id'])
    fdf.to_parquet(OUT,index=False)
    meta={'built_at':now(),'manifest_sources':len(manifest),'parsed_status_rows':len(raw),'feature_matches':len(fdf),
          'audit':audit,'output_rows':str(OUT_ROWS),'output_features':str(OUT),
          'coverage_note':'This is intentionally sparse first-party game-note coverage. A missing side/report is NaN/unknown, never inferred clear.',
          'source_note':'Manifest contains only individually verified MLS-hosted club game-note PDFs.'}
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
