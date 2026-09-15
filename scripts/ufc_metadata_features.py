#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
import json, re
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
SRC=ROOT/"feature_expansion"/"prefight_favorite_features_v2.csv"
RANK=ROOT/"raw"/"rankings.csv"
IND=ROOT/"raw"/"individuals.csv"
OUT=ROOT/"feature_expansion"/"prefight_favorite_features_v3.csv"
META=ROOT/"feature_expansion"/"metadata_features_meta.json"

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def height_in(v):
    m=re.search(r"(\d+)\s*'\s*(\d+)",str(v or ''))
    return int(m.group(1))*12+int(m.group(2)) if m else np.nan

def main():
    d=pd.read_csv(SRC,low_memory=False)
    d['_date']=pd.to_datetime(d['event_date'],errors='coerce')
    r=pd.read_csv(RANK,low_memory=False)
    r['_date']=pd.to_datetime(r['date'],errors='coerce');r['_fighter']=r['fighter'].map(norm)
    r['rank']=pd.to_numeric(r['rank'],errors='coerce')
    # Build per-fighter chronological snapshots; all ranking data used must be dated <= fight date.
    rank_index={}
    for f,g in r.dropna(subset=['_date']).sort_values('_date').groupby('_fighter'):
        rank_index[f]=g[['_date','weightclass','rank']].copy()
    ind=pd.read_csv(IND,low_memory=False);ind['_fighter']=ind['name'].map(norm)
    im={x['_fighter']:x for _,x in ind.iterrows()}
    rows=[]; ranked_available=0
    for _,x in d.iterrows():
        dt=x['_date'];fn,on=norm(x['favorite']),norm(x['opponent']);out={}
        def rank_snap(name):
            g=rank_index.get(name)
            if g is None or pd.isna(dt):return np.nan,np.nan,0
            q=g[g['_date']<=dt]
            if q.empty:return np.nan,np.nan,0
            last=q['_date'].max();q=q[q['_date']==last]
            p4p=q.loc[q['weightclass'].astype(str).str.lower().eq('pound-for-pound'),'rank']
            div=q.loc[~q['weightclass'].astype(str).str.lower().eq('pound-for-pound'),'rank']
            return (float(div.min()) if div.notna().any() else np.nan,float(p4p.min()) if p4p.notna().any() else np.nan,int(div.notna().any()))
        fr,fp4,franked=rank_snap(fn);orr,op4,oranked=rank_snap(on)
        if franked or oranked:ranked_available+=1
        out.update({'f3_f_div_rank':fr,'f3_o_div_rank':orr,'f3_f_p4p_rank':fp4,'f3_o_p4p_rank':op4,
                    'f3_f_ranked':franked,'f3_o_ranked':oranked,'f3_ranked_status_diff':franked-oranked})
        if pd.notna(fr) and pd.notna(orr):out['f3_div_rank_adv']=orr-fr
        if pd.notna(fp4) and pd.notna(op4):out['f3_p4p_rank_adv']=op4-fp4
        fi,oi=im.get(fn),im.get(on)
        fh=height_in(fi.get('height')) if fi is not None else np.nan;oh=height_in(oi.get('height')) if oi is not None else np.nan
        fs=str(fi.get('stance','')).lower() if fi is not None else '';os=str(oi.get('stance','')).lower() if oi is not None else ''
        out['f3_f_height_in']=fh;out['f3_o_height_in']=oh;out['f3_height_adv']=fh-oh if pd.notna(fh) and pd.notna(oh) else np.nan
        out['f3_f_southpaw']=int('southpaw' in fs);out['f3_o_southpaw']=int('southpaw' in os)
        out['f3_f_switch']=int('switch' in fs);out['f3_o_switch']=int('switch' in os)
        out['f3_opposite_stance']=int(bool(fs and os and (('southpaw' in fs)!=('southpaw' in os))))
        rows.append(out)
    outdf=pd.concat([d.drop(columns=['_date']),pd.DataFrame(rows)],axis=1)
    outdf.to_csv(OUT,index=False)
    meta={'built_at':datetime.now(timezone.utc).isoformat(),'rows':len(outdf),'columns':len(outdf.columns),'added_columns':len(outdf.columns)-len(d.columns)+1,
          'ranking_point_in_time':True,'ranking_rows_with_any_rank_context':ranked_available,'output':str(OUT)}
    META.write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2));print([c for c in outdf.columns if c.startswith('f3_')])
if __name__=='__main__':main()
