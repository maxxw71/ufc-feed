#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
from io import StringIO
import json, re, urllib.request
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
SRC=ROOT/"feature_expansion"/"prefight_favorite_features_v3.csv"
OUT=ROOT/"feature_expansion"/"prefight_favorite_features_v4.csv"
META=ROOT/"feature_expansion"/"weight_history_meta.json"
RAWOUT=ROOT/"feature_expansion"/"weight_history_source.csv"
URL="https://raw.githubusercontent.com/Renaissanc3Man/MMA-Data/master/Input%20Data/mma_data_weight_cutting.csv"

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def load_source():
    req=urllib.request.Request(URL,headers={'User-Agent':'appwiza-ufc-weight-history/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:
        txt=r.read().decode('utf-8-sig','replace')
    RAWOUT.write_text(txt,encoding='utf-8')
    d=pd.read_csv(StringIO(txt),low_memory=False)
    d['_date']=pd.to_datetime(d['Date'],errors='coerce')
    d['_fighter']=d['Fighter'].map(norm)
    d['_missed_by']=pd.to_numeric(d['Missed by'],errors='coerce')
    d['_withdrawn']=d['Withdrawn'].astype(str).str.lower().str.contains('withdrawn',na=False)
    notes=d['Notes'].fillna('').astype(str).str.lower()
    d['_weight_cut_issue']=d['_withdrawn'] & notes.str.contains('weight|kidney|hospital|gastro|make weight',regex=True)
    d['_illness_withdrawal']=d['_withdrawn'] & notes.str.contains('ill|sick|food poisoning|gastro|hospital',regex=True)
    d['_second_attempt']=notes.str.contains('2nd attempt|second attempt',regex=True)
    d['_repeat_note']=notes.str.contains('time missing|successive|repeat offender',regex=True)
    return d.dropna(subset=['_date','_fighter']).sort_values('_date')

def history_features(g,dt):
    if g is None or pd.isna(dt): return {
        'misses':0,'withdrawals':0,'weight_cut_withdrawals':0,'illness_withdrawals':0,'repeat_miss_flag':0,
        'max_miss_lbs':np.nan,'avg_miss_lbs':np.nan,'days_since_miss':np.nan,'days_since_weight_cut_withdrawal':np.nan,
        'miss_last_365':0,'miss_last_730':0,'second_attempts':0}
    q=g[g['_date']<dt]
    if q.empty: return {
        'misses':0,'withdrawals':0,'weight_cut_withdrawals':0,'illness_withdrawals':0,'repeat_miss_flag':0,
        'max_miss_lbs':np.nan,'avg_miss_lbs':np.nan,'days_since_miss':np.nan,'days_since_weight_cut_withdrawal':np.nan,
        'miss_last_365':0,'miss_last_730':0,'second_attempts':0}
    misses=q[q['_missed_by'].fillna(0)>0]
    wcut=q[q['_weight_cut_issue']]
    days_miss=(dt-misses['_date'].max()).days if not misses.empty else np.nan
    days_wcut=(dt-wcut['_date'].max()).days if not wcut.empty else np.nan
    return {
        'misses':int(len(misses)),
        'withdrawals':int(q['_withdrawn'].sum()),
        'weight_cut_withdrawals':int(q['_weight_cut_issue'].sum()),
        'illness_withdrawals':int(q['_illness_withdrawal'].sum()),
        'repeat_miss_flag':int(len(misses)>=2 or q['_repeat_note'].any()),
        'max_miss_lbs':float(misses['_missed_by'].max()) if not misses.empty else np.nan,
        'avg_miss_lbs':float(misses['_missed_by'].mean()) if not misses.empty else np.nan,
        'days_since_miss':days_miss,
        'days_since_weight_cut_withdrawal':days_wcut,
        'miss_last_365':int(((dt-misses['_date']).dt.days<=365).sum()) if not misses.empty else 0,
        'miss_last_730':int(((dt-misses['_date']).dt.days<=730).sum()) if not misses.empty else 0,
        'second_attempts':int(q['_second_attempt'].sum())
    }

def main():
    base=pd.read_csv(SRC,low_memory=False)
    base['_date']=pd.to_datetime(base['event_date'],errors='coerce')
    src=load_source()
    idx={f:g.copy() for f,g in src.groupby('_fighter')}
    rows=[]; covered=0
    for _,r in base.iterrows():
        dt=r['_date']; fn,on=norm(r['favorite']),norm(r['opponent'])
        fh=history_features(idx.get(fn),dt); oh=history_features(idx.get(on),dt)
        if fh['misses'] or fh['withdrawals'] or oh['misses'] or oh['withdrawals']: covered+=1
        out={}
        for k in sorted(set(fh)|set(oh)):
            out['f4_f_'+k]=fh.get(k,np.nan); out['f4_o_'+k]=oh.get(k,np.nan)
            a,b=fh.get(k,np.nan),oh.get(k,np.nan)
            if isinstance(a,(int,float,np.integer,np.floating)) and isinstance(b,(int,float,np.integer,np.floating)) and pd.notna(a) and pd.notna(b):
                out['f4_diff_'+k]=float(a)-float(b)
        rows.append(out)
    outdf=pd.concat([base.drop(columns=['_date']),pd.DataFrame(rows)],axis=1)
    outdf.to_csv(OUT,index=False)
    meta={
      'built_at':datetime.now(timezone.utc).isoformat(),
      'source_url':URL,
      'source_rows':int(len(src)),
      'source_date_min':str(src['_date'].min().date()) if len(src) else None,
      'source_date_max':str(src['_date'].max().date()) if len(src) else None,
      'rows':int(len(outdf)),
      'columns':int(len(outdf.columns)),
      'added_columns':int(len(outdf.columns)-len(base.columns)+1),
      'point_in_time_strict_before_fight':True,
      'rows_with_prior_weight_history':int(covered),
      'output':str(OUT)
    }
    META.write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta,indent=2))
    print([c for c in outdf.columns if c.startswith('f4_')])

if __name__=='__main__': main()
