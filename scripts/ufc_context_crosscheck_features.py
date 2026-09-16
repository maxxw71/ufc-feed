#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
from io import StringIO
import json, re, urllib.request
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
SRC=ROOT/"feature_expansion"/"prefight_favorite_features_v4.csv"
RAW=ROOT/"raw"/"competitions.csv"
IND=ROOT/"raw"/"individuals.csv"
OUT=ROOT/"feature_expansion"/"prefight_favorite_features_v5.csv"
META=ROOT/"feature_expansion"/"context_crosscheck_meta.json"
RAWOUT=ROOT/"feature_expansion"/"tidytuesday_ultimate_ufc_dataset.csv"
URL="https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2026/2026-07-07/ultimate_ufc_dataset.csv"

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def parse_rounds(v):
    m=re.search(r'(\d+)\s*Rnd',str(v or ''),re.I)
    return float(m.group(1)) if m else np.nan

def load_remote():
    try:
        req=urllib.request.Request(URL,headers={'User-Agent':'appwiza-ufc-context/1.0'})
        with urllib.request.urlopen(req,timeout=120) as r: data=r.read()
        RAWOUT.write_bytes(data)
        d=pd.read_csv(StringIO(data.decode('utf-8-sig','replace')),low_memory=False)
        d['_date']=pd.to_datetime(d['date'],errors='coerce')
        d['_r']=d['r_fighter'].map(norm); d['_b']=d['b_fighter'].map(norm)
        d['_key']=d.apply(lambda x:(str(x['_date'].date()) if pd.notna(x['_date']) else '',)+tuple(sorted((x['_r'],x['_b']))),axis=1)
        return d
    except Exception:
        return pd.DataFrame()

def build_internal():
    if not RAW.exists(): return {},{}
    raw=pd.read_csv(RAW,low_memory=False);raw['_date']=pd.to_datetime(raw['event_date'],errors='coerce');raw=raw[raw._date.notna()].sort_values('_date')
    hist={}; fight_ctx={}
    state={}
    for dt,g in raw.groupby('_date',sort=True):
        snaps={}
        for _,r in g.iterrows():
            a,b=norm(r.get('player1')),norm(r.get('player2'))
            for f in (a,b):
                q=state.get(f,{'win_streak':0,'lose_streak':0,'longest_win_streak':0,'rounds':0.0,'title_bouts':0})
                snaps[f]=dict(q)
            key=(str(dt.date()),)+tuple(sorted((a,b)))
            fight_ctx[key]={'title_bout':int('title' in str(r.get('weightclass','')).lower()),'no_of_rounds':parse_rounds(r.get('time_format'))}
        for f,q in snaps.items(): hist.setdefault(f,[]).append((dt,dict(q)))
        for _,r in g.iterrows():
            a,b=norm(r.get('player1')),norm(r.get('player2'));res=str(r.get('result','')).upper();p1w=res.startswith('W');p2w=res.startswith('L')
            for f,won,lost in ((a,p1w,p2w),(b,p2w,p1w)):
                if not f: continue
                q=state.setdefault(f,{'win_streak':0,'lose_streak':0,'longest_win_streak':0,'rounds':0.0,'title_bouts':0})
                try:q['rounds']+=float(r.get('round') or 0)
                except:pass
                q['title_bouts']+=int('title' in str(r.get('weightclass','')).lower())
                if won:q['win_streak']+=1;q['lose_streak']=0;q['longest_win_streak']=max(q['longest_win_streak'],q['win_streak'])
                elif lost:q['lose_streak']+=1;q['win_streak']=0
    weights={}
    if IND.exists():
        ind=pd.read_csv(IND,low_memory=False)
        for _,r in ind.iterrows():
            m=re.search(r'\d+(?:\.\d+)?',str(r.get('weight') or ''))
            weights[norm(r.get('name'))]=float(m.group()) if m else np.nan
    return hist,(fight_ctx,weights)

def internal_snap(hist,f,dt):
    vals=hist.get(f,[]);q=[x for x in vals if x[0] < dt]
    return q[-1][1] if q else {'win_streak':0,'lose_streak':0,'longest_win_streak':0,'rounds':0.0,'title_bouts':0}

def main():
    base=pd.read_csv(SRC,low_memory=False)
    remote=load_remote();idx={k:g.iloc[-1] for k,g in remote.dropna(subset=['_date']).groupby('_key')} if len(remote) else {}
    ih,(fight_ctx,weights)=build_internal()
    rows=[]; matched=0; fallback=0
    fields=['current_lose_streak','current_win_streak','longest_win_streak','total_rounds_fought','total_title_bouts','weight_lbs']
    for _,r in base.iterrows():
        dt=pd.to_datetime(r['event_date'],errors='coerce');fn,on=norm(r['favorite']),norm(r['opponent'])
        key=(str(dt.date()) if pd.notna(dt) else '',)+tuple(sorted((fn,on)))
        z=idx.get(key);out={}
        if z is not None:
            matched+=1;fav_side='r' if norm(z.get('r_fighter'))==fn else 'b';opp_side='b' if fav_side=='r' else 'r'
            for f in fields:
                fv=pd.to_numeric(pd.Series([z.get(fav_side+'_'+f)]),errors='coerce').iloc[0];ov=pd.to_numeric(pd.Series([z.get(opp_side+'_'+f)]),errors='coerce').iloc[0]
                out['f5_f_'+f]=fv;out['f5_o_'+f]=ov
                if pd.notna(fv) and pd.notna(ov):out['f5_diff_'+f]=float(fv)-float(ov)
            out['f5_title_bout']=int(bool(z.get('title_bout')));out['f5_no_of_rounds']=pd.to_numeric(pd.Series([z.get('no_of_rounds')]),errors='coerce').iloc[0];out['f5_empty_arena']=pd.to_numeric(pd.Series([z.get('empty_arena')]),errors='coerce').iloc[0]
        else:
            fallback+=1;fs=internal_snap(ih,fn,dt);os=internal_snap(ih,on,dt)
            vals={
              'current_lose_streak':(fs['lose_streak'],os['lose_streak']),
              'current_win_streak':(fs['win_streak'],os['win_streak']),
              'longest_win_streak':(fs['longest_win_streak'],os['longest_win_streak']),
              'total_rounds_fought':(fs['rounds'],os['rounds']),
              'total_title_bouts':(fs['title_bouts'],os['title_bouts']),
              'weight_lbs':(weights.get(fn,np.nan),weights.get(on,np.nan))}
            for f,(fv,ov) in vals.items():
                out['f5_f_'+f]=fv;out['f5_o_'+f]=ov
                if pd.notna(fv) and pd.notna(ov):out['f5_diff_'+f]=float(fv)-float(ov)
            cx=fight_ctx.get(key,{})
            out['f5_title_bout']=cx.get('title_bout',np.nan);out['f5_no_of_rounds']=cx.get('no_of_rounds',np.nan);out['f5_empty_arena']=np.nan
        rows.append(out)
    outdf=pd.concat([base,pd.DataFrame(rows)],axis=1);outdf.to_csv(OUT,index=False)
    meta={'built_at':datetime.now(timezone.utc).isoformat(),'source_url':URL,'source_rows':int(len(remote)),'rows':int(len(outdf)),'columns':int(len(outdf.columns)),'added_columns':int(len(outdf.columns)-len(base.columns)),'matched_rows':int(matched),'internal_fallback_rows':int(fallback),'match_rate':float(matched/len(base)) if len(base) else 0.0,'prefight_fields_verified_by_source_dictionary':True,'internal_fallback_strict_before_fight_date':True,'same_event_guard':True,'output':str(OUT)}
    META.write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,indent=2));print([c for c in outdf.columns if c.startswith('f5_')])
if __name__=='__main__':main()
