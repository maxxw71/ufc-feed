#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
from io import StringIO
import json, re, urllib.request
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
SRC=ROOT/"feature_expansion"/"prefight_favorite_features_v4.csv"
OUT=ROOT/"feature_expansion"/"prefight_favorite_features_v5.csv"
META=ROOT/"feature_expansion"/"context_crosscheck_meta.json"
RAWOUT=ROOT/"feature_expansion"/"tidytuesday_ultimate_ufc_dataset.csv"
URL="https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2026/2026-07-07/ultimate_ufc_dataset.csv"

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def load_remote():
    req=urllib.request.Request(URL,headers={'User-Agent':'appwiza-ufc-context/1.0'})
    with urllib.request.urlopen(req,timeout=120) as r: data=r.read()
    RAWOUT.write_bytes(data)
    d=pd.read_csv(StringIO(data.decode('utf-8-sig','replace')),low_memory=False)
    d['_date']=pd.to_datetime(d['date'],errors='coerce')
    d['_r']=d['r_fighter'].map(norm); d['_b']=d['b_fighter'].map(norm)
    d['_key']=d.apply(lambda x:(str(x['_date'].date()) if pd.notna(x['_date']) else '',)+tuple(sorted((x['_r'],x['_b']))),axis=1)
    return d

def main():
    base=pd.read_csv(SRC,low_memory=False)
    remote=load_remote()
    idx={k:g.iloc[-1] for k,g in remote.dropna(subset=['_date']).groupby('_key')}
    rows=[]; matched=0
    fields=['current_lose_streak','current_win_streak','longest_win_streak','total_rounds_fought','total_title_bouts','weight_lbs']
    for _,r in base.iterrows():
        dt=pd.to_datetime(r['event_date'],errors='coerce'); fn,on=norm(r['favorite']),norm(r['opponent'])
        key=(str(dt.date()) if pd.notna(dt) else '',)+tuple(sorted((fn,on)))
        z=idx.get(key); out={}
        if z is not None:
            matched+=1
            fav_side='r' if norm(z.get('r_fighter'))==fn else 'b'
            opp_side='b' if fav_side=='r' else 'r'
            for f in fields:
                fv=pd.to_numeric(pd.Series([z.get(fav_side+'_'+f)]),errors='coerce').iloc[0]
                ov=pd.to_numeric(pd.Series([z.get(opp_side+'_'+f)]),errors='coerce').iloc[0]
                out['f5_f_'+f]=fv; out['f5_o_'+f]=ov
                if pd.notna(fv) and pd.notna(ov): out['f5_diff_'+f]=float(fv)-float(ov)
            out['f5_title_bout']=int(bool(z.get('title_bout')))
            out['f5_no_of_rounds']=pd.to_numeric(pd.Series([z.get('no_of_rounds')]),errors='coerce').iloc[0]
            out['f5_empty_arena']=pd.to_numeric(pd.Series([z.get('empty_arena')]),errors='coerce').iloc[0]
        rows.append(out)
    outdf=pd.concat([base,pd.DataFrame(rows)],axis=1)
    outdf.to_csv(OUT,index=False)
    meta={
      'built_at':datetime.now(timezone.utc).isoformat(),
      'source_url':URL,
      'source_rows':int(len(remote)),
      'rows':int(len(outdf)),
      'columns':int(len(outdf.columns)),
      'added_columns':int(len(outdf.columns)-len(base.columns)),
      'matched_rows':int(matched),
      'match_rate':float(matched/len(base)) if len(base) else 0.0,
      'prefight_fields_verified_by_source_dictionary':True,
      'source_dictionary_note':'streak/loss/win/round/title fields are documented as historical strictly prior to current bout',
      'output':str(OUT)
    }
    META.write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta,indent=2))
    print([c for c in outdf.columns if c.startswith('f5_')])

if __name__=='__main__': main()
