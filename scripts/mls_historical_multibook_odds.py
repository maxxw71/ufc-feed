#!/usr/bin/env python3
from __future__ import annotations

import csv, gzip, io, json, math, re, urllib.request, zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/beat_the_bookie'
PROC=ROOT/'data/processed'
REP=ROOT/'reports'
for p in [RAW,PROC,REP]: p.mkdir(parents=True,exist_ok=True)

BASE=PROC/'mls_match_features_weather_enriched.parquet'
RAW_ZIP=RAW/'beat_the_bookie_worldwide.zip'
RAW_LONG=PROC/'mls_historical_multibook_movement_long.parquet'
FEATURES=PROC/'mls_historical_multibook_market_features.parquet'
META=REP/'historical_multibook_odds_meta.json'
MOVEMENT_META=REP/'historical_multibook_movement_meta.json'

SOURCE='https://www.kaggle.com/api/v1/datasets/download/austro/beat-the-bookie-worldwide-football-dataset'
SOURCE_NAME='Kaunitz-Zhong-Kreiner BeatTheBookie public archive'
UA='Mozilla/5.0 AppwizaMLSMovement/1.0'

BOOKIES=[
 'Interwetten','bwin','bet-at-home','Unibet','Stan James','Expekt','10Bet','William Hill',
 'bet365','Pinnacle Sports','DOXXbet','Betsafe','Betway','888sport','Ladbrokes','Betclic',
 'Sportingbet','myBet','Betsson','188BET','Jetbull','Paddy Power','Tipico','Coral',
 'SBOBET','BetVictor','12BET','Titanbet','youwin','ComeOn','Betadonis','Betfair Sports'
]
HORIZONS=[71,48,24,12,6,3,1,0]
MOVES=[(71,24),(24,6),(6,0),(24,0),(71,0)]
ANCHOR_BOOKS=['Pinnacle Sports','bet365']
ALIASES={
 'Atlanta United':'Atlanta United FC','Atlanta Utd':'Atlanta United FC',
 'CF Montreal':'CF Montréal','Montreal Impact':'CF Montréal',
 'D.C. United':'D.C. United','DC United':'D.C. United',
 'Houston Dynamo':'Houston Dynamo FC','Houston Dynamo FC':'Houston Dynamo FC',
 'Inter Miami':'Inter Miami CF','LA Galaxy':'Los Angeles Galaxy',
 'Los Angeles Galaxy':'Los Angeles Galaxy','LAFC':'Los Angeles FC',
 'Minnesota United':'Minnesota United FC','NYCFC':'New York City FC',
 'New York City':'New York City FC','Orlando City':'Orlando City SC',
 'Seattle Sounders':'Seattle Sounders FC','Vancouver Whitecaps':'Vancouver Whitecaps FC',
 'Sporting KC':'Sporting Kansas City','Columbus Crew SC':'Columbus Crew',
 'Chicago Fire':'Chicago Fire FC','St. Louis City':'St. Louis City SC',
}
TRUNC_ALIASES={
 'new england revoluti':'New England Revolution',
 'vancouver whitecaps':'Vancouver Whitecaps FC',
 'los angeles galaxy':'Los Angeles Galaxy',
 'montreal impact':'CF Montréal',
 'seattle sounders':'Seattle Sounders FC',
}

def now(): return datetime.now(timezone.utc).isoformat()

def cteam(x):
    raw=str(x or '').strip()
    low=raw.lower()
    raw=TRUNC_ALIASES.get(low,ALIASES.get(raw,raw))
    return canon_team(raw)

def norm(x):
    return re.sub(r'[^a-z0-9]+','',cteam(x).lower())

def sim(a,b):
    a=norm(a); b=norm(b)
    if not a or not b:return 0.0
    if a==b:return 1.0
    if min(len(a),len(b))>=8 and (a.startswith(b) or b.startswith(a)):return 0.985
    return SequenceMatcher(None,a,b).ratio()

def safe_float(x):
    try:
        v=float(x)
        return v if math.isfinite(v) and v>1.0 else np.nan
    except Exception:
        return np.nan

def novig(h,d,a):
    vals=np.asarray([h,d,a],dtype=float)
    if not np.isfinite(vals).all() or (vals<=1).any():return (np.nan,)*4
    inv=1.0/vals; total=float(inv.sum())
    return float(inv[0]/total),float(inv[1]/total),float(inv[2]/total),float(total-1.0)

def download_archive():
    if RAW_ZIP.exists() and RAW_ZIP.stat().st_size>80_000_000:return
    req=urllib.request.Request(SOURCE,headers={'User-Agent':UA,'Accept':'application/zip'})
    tmp=RAW_ZIP.with_suffix('.tmp')
    with urllib.request.urlopen(req,timeout=240) as r,tmp.open('wb') as f:
        while True:
            b=r.read(1024*1024)
            if not b:break
            f.write(b)
    if tmp.stat().st_size<80_000_000:raise RuntimeError(f'Unexpected archive size: {tmp.stat().st_size}')
    tmp.replace(RAW_ZIP)

def read_gz_from_zip(z,name):
    return gzip.GzipFile(fileobj=io.BytesIO(z.read(name)),mode='rb')

def load_source_meta(z):
    rows=[]
    for dataset,mfn,sfn in [
        ('A','odds_series_matches.csv.gz','odds_series.csv.gz'),
        ('B','odds_series_b_matches.csv.gz','odds_series_b.csv.gz')]:
        with read_gz_from_zip(z,mfn) as raw,io.TextIOWrapper(raw,encoding='utf-8',errors='replace',newline='') as txt:
            rd=csv.DictReader(txt)
            for r in rd:
                league=(r.get('league') or r.get(' league') or '').strip()
                if league!='USA: MLS':continue
                score=str(r.get('score') or '').strip()
                m=re.match(r'\s*(\d+)\s*:\s*(\d+)',score)
                rows.append({
                    'dataset':dataset,'series_file':sfn,'source_match_id':str(r['match_id']).strip(),
                    'source_home':str(r['home_team']).strip(),'source_away':str(r['away_team']).strip(),
                    'source_match_datetime':str(r['match_datetime']).strip(),
                    'source_date':pd.to_datetime(r['match_datetime'],errors='coerce').date() if pd.notna(pd.to_datetime(r['match_datetime'],errors='coerce')) else None,
                    'source_home_score':int(m.group(1)) if m else None,'source_away_score':int(m.group(2)) if m else None,
                })
    return pd.DataFrame(rows)

def match_to_appwiza(src,base):
    b=base[['match_id','season','date','home_team','away_team','home_score','away_score']].copy()
    b['match_id']=b.match_id.astype(str)
    b['day']=pd.to_datetime(b.date,errors='coerce').dt.date
    b['hs']=pd.to_numeric(b.home_score,errors='coerce')
    b['as']=pd.to_numeric(b.away_score,errors='coerce')
    matched=[];unmatched=[];ambiguous=[]
    for r in src.itertuples(index=False):
        if r.source_date is None:
            unmatched.append({'source_match_id':r.source_match_id,'reason':'DATE'});continue
        cand=b[pd.to_numeric(b.season,errors='coerce').eq(r.source_date.year)].copy()
        if r.source_home_score is not None:
            cand=cand[cand.hs.eq(r.source_home_score)&cand['as'].eq(r.source_away_score)]
        cand['day_diff']=cand.day.map(lambda d:abs((d-r.source_date).days) if d else 999)
        cand=cand[cand.day_diff.le(1)].copy()
        if cand.empty:
            unmatched.append({'source_match_id':r.source_match_id,'date':str(r.source_date),'home':r.source_home,'away':r.source_away,'reason':'DATE_SCORE'});continue
        cand['home_sim']=cand.home_team.map(lambda x:sim(r.source_home,x))
        cand['away_sim']=cand.away_team.map(lambda x:sim(r.source_away,x))
        cand['map_score']=cand.home_sim+cand.away_sim-cand.day_diff*0.08
        good=cand[(cand.home_sim.ge(.82))&(cand.away_sim.ge(.82))].sort_values('map_score',ascending=False)
        if good.empty:
            unmatched.append({'source_match_id':r.source_match_id,'date':str(r.source_date),'home':r.source_home,'away':r.source_away,'reason':'TEAM','candidates':cand[['home_team','away_team','day_diff','home_sim','away_sim']].head(5).to_dict('records')});continue
        if len(good)>1 and float(good.iloc[0].map_score-good.iloc[1].map_score)<0.03:
            ambiguous.append({'source_match_id':r.source_match_id,'date':str(r.source_date),'home':r.source_home,'away':r.source_away,'candidates':good[['match_id','home_team','away_team','map_score']].head(5).to_dict('records')});continue
        top=good.iloc[0]
        matched.append({'source_match_id':r.source_match_id,'dataset':r.dataset,'series_file':r.series_file,
                        'source_home':r.source_home,'source_away':r.source_away,'source_match_datetime':r.source_match_datetime,
                        'match_id':str(top.match_id),'home_similarity':float(top.home_sim),'away_similarity':float(top.away_sim),'day_diff':int(top.day_diff)})
    return pd.DataFrame(matched),unmatched,ambiguous

def parse_target_series(z,mapping):
    targets={k:set(g.source_match_id.astype(str)) for k,g in mapping.groupby('series_file')}
    map_by_source=dict(zip(mapping.source_match_id.astype(str),mapping.match_id.astype(str)))
    meta_by_source=mapping.set_index(mapping.source_match_id.astype(str)).to_dict('index')
    long_rows=[];seen=set();series_stats={}
    for sfn,target_ids in targets.items():
        with read_gz_from_zip(z,sfn) as raw,io.TextIOWrapper(raw,encoding='utf-8',errors='replace',newline='') as txt:
            rd=csv.reader(txt);hdr=next(rd);idx={c:i for i,c in enumerate(hdr)}
            expected=5+len(BOOKIES)*3*72
            if len(hdr)!=expected:raise RuntimeError(f'{sfn}: unexpected columns {len(hdr)} != {expected}')
            hit=0
            for row in rd:
                if not row:continue
                sid=str(row[0]).strip()
                if sid not in target_ids:continue
                hit+=1;seen.add(sid);m=meta_by_source[sid]
                for bi,book in enumerate(BOOKIES,1):
                    for ci in range(72):
                        h=safe_float(row[idx[f'home_b{bi}_{ci}']]);d=safe_float(row[idx[f'draw_b{bi}_{ci}']]);a=safe_float(row[idx[f'away_b{bi}_{ci}']])
                        if not np.isfinite([h,d,a]).all():continue
                        ph,pd_,pa,ov=novig(h,d,a)
                        long_rows.append({
                            'match_id':map_by_source[sid],'source_match_id':sid,'source_dataset':m['dataset'],
                            'source_match_datetime':m['source_match_datetime'],'bookmaker':book,'bookmaker_index':bi,
                            'hour_before':71-ci,'home_odds':h,'draw_odds':d,'away_odds':a,
                            'home_novig_prob':ph,'draw_novig_prob':pd_,'away_novig_prob':pa,'overround':ov,
                        })
            series_stats[sfn]={'target_ids':len(target_ids),'series_rows_found':hit}
    out=pd.DataFrame(long_rows)
    if len(out):
        out=out.sort_values(['match_id','bookmaker_index','hour_before'],ascending=[True,True,False]).reset_index(drop=True)
    return out,seen,series_stats

def feature_name_h(h):return 'close' if h==0 else f'h{h}'

def build_features(long,mapping):
    rows=[]
    if long.empty:return pd.DataFrame(columns=['match_id'])
    for mid,g in long.groupby('match_id',sort=False):
        r={'match_id':str(mid),'hist_mb_movement_available':1}
        src=mapping[mapping.match_id.astype(str).eq(str(mid))].iloc[0]
        r['hist_mb_source_match_id']=str(src.source_match_id)
        r['hist_mb_source_dataset']=str(src.dataset)
        r['hist_mb_source_match_datetime']=str(src.source_match_datetime)
        r['hist_mb_total_books_seen']=int(g.bookmaker.nunique())
        r['hist_mb_total_valid_book_hours']=int(len(g))
        for h in HORIZONS:
            gh=g[g.hour_before.eq(h)]
            tag=feature_name_h(h)
            r[f'hist_mb_{tag}_book_count']=int(gh.bookmaker.nunique()) if len(gh) else 0
            r[f'hist_mb_{tag}_mean_overround']=float(gh.overround.mean()) if len(gh) else np.nan
            for side in ['home','draw','away']:
                p=pd.to_numeric(gh[f'{side}_novig_prob'],errors='coerce').dropna()
                o=pd.to_numeric(gh[f'{side}_odds'],errors='coerce').dropna()
                r[f'hist_mb_{tag}_{side}_prob_mean']=float(p.mean()) if len(p) else np.nan
                r[f'hist_mb_{tag}_{side}_prob_std']=float(p.std(ddof=0)) if len(p)>1 else (0.0 if len(p)==1 else np.nan)
                r[f'hist_mb_{tag}_{side}_odds_mean']=float(o.mean()) if len(o) else np.nan
                r[f'hist_mb_{tag}_{side}_odds_max']=float(o.max()) if len(o) else np.nan

        for h0,h1 in MOVES:
            a=g[g.hour_before.eq(h0)][['bookmaker','home_novig_prob','draw_novig_prob','away_novig_prob']]
            b=g[g.hour_before.eq(h1)][['bookmaker','home_novig_prob','draw_novig_prob','away_novig_prob']]
            p=a.merge(b,on='bookmaker',suffixes=('_start','_end'))
            tag=f'h{h0}_to_'+feature_name_h(h1)
            r[f'hist_mb_move_{tag}_paired_books']=int(len(p))
            for side in ['home','draw','away']:
                if len(p):
                    delta=pd.to_numeric(p[f'{side}_novig_prob_end'],errors='coerce')-pd.to_numeric(p[f'{side}_novig_prob_start'],errors='coerce')
                    delta=delta.dropna()
                else: delta=pd.Series(dtype=float)
                r[f'hist_mb_move_{tag}_{side}_prob_mean']=float(delta.mean()) if len(delta) else np.nan
                r[f'hist_mb_move_{tag}_{side}_prob_median']=float(delta.median()) if len(delta) else np.nan
                r[f'hist_mb_move_{tag}_{side}_up_share']=float((delta>0).mean()) if len(delta) else np.nan

        for book in ANCHOR_BOOKS:
            bk=re.sub(r'[^a-z0-9]+','_',book.lower()).strip('_')
            gb=g[g.bookmaker.eq(book)]
            for h in [24,6,0]:
                z=gb[gb.hour_before.eq(h)]
                tag=feature_name_h(h)
                for side in ['home','draw','away']:
                    r[f'hist_mb_{bk}_{tag}_{side}_odds']=float(z.iloc[0][f'{side}_odds']) if len(z) else np.nan
                    r[f'hist_mb_{bk}_{tag}_{side}_novig_prob']=float(z.iloc[0][f'{side}_novig_prob']) if len(z) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)

def main():
    if not BASE.exists():raise RuntimeError(f'Missing base warehouse: {BASE}')
    download_archive()
    base=pd.read_parquet(BASE)
    with zipfile.ZipFile(RAW_ZIP) as z:
        src=load_source_meta(z)
        if len(src)!=445:raise RuntimeError(f'Expected 445 MLS source matches, got {len(src)}')
        mapping,unmatched,ambiguous=match_to_appwiza(src,base)
        long,seen,series_stats=parse_target_series(z,mapping)
    if len(mapping)<400:raise RuntimeError(f'Movement mapping unexpectedly low: {len(mapping)} / {len(src)}')
    if long.empty:raise RuntimeError('No historical movement rows parsed')
    features=build_features(long,mapping)
    if len(features)<400:raise RuntimeError(f'Movement feature coverage unexpectedly low: {len(features)}')

    long.to_parquet(RAW_LONG,index=False)
    features.to_parquet(FEATURES,index=False)

    by_season={}
    tmp=base[['match_id','season']].copy();tmp.match_id=tmp.match_id.astype(str)
    fc=features[['match_id']].merge(tmp,on='match_id',how='left')
    by_season={str(int(k)):int(v) for k,v in fc.groupby('season').size().to_dict().items() if pd.notna(k)}
    books=long.groupby('match_id').bookmaker.nunique()
    hcov={str(h):int(long[long.hour_before.eq(h)].match_id.nunique()) for h in HORIZONS}
    book_cov={b:int(long[long.bookmaker.eq(b)].match_id.nunique()) for b in BOOKIES}
    numeric=[c for c in features.columns if c!='match_id' and pd.api.types.is_numeric_dtype(features[c])]
    payload={
      'built_at':now(),'status':'PUBLIC_CONTINUOUS_MOVEMENT_MERGED_READY','source':SOURCE_NAME,'source_url':SOURCE,
      'source_archive_bytes':RAW_ZIP.stat().st_size,'source_mls_matches':len(src),'mapped_matches':len(mapping),
      'mapping_rate':len(mapping)/len(src),'unmatched_matches':len(unmatched),'ambiguous_matches':len(ambiguous),
      'series_matches_found':len(seen),'series_stats':series_stats,'movement_long_rows':len(long),
      'matches_with_movement_features':len(features),'feature_columns_ex_match_id':len(features.columns)-1,
      'numeric_feature_columns':len(numeric),'coverage_by_appwiza_season':by_season,
      'median_books_per_match':float(books.median()),'min_books_per_match':int(books.min()),'max_books_per_match':int(books.max()),
      'horizon_match_coverage':hcov,'book_match_coverage':book_cov,'bookmaker_order':BOOKIES,'horizons_hours_before':HORIZONS,
      'hour_index_rule':'Source *_0 is approximately 71 hours before kickoff and *_71 is the final pre-kickoff marker. hour_before = 71 - source_index.',
      'engineered_movement_intervals':[f'{a}->{b}' for a,b in MOVES],
      'anchor_books':ANCHOR_BOOKS,'raw_long_output':str(RAW_LONG),'features_output':str(FEATURES),
      'timing_policy':'Each horizon feature is usable only at or after its stated hours-before-kickoff snapshot. Features ending in close or *_to_close are closing-time features and must not be used for an earlier decision horizon. No post-kickoff odds are used.',
      'integrity_note':'Only complete valid 1X2 bookmaker triplets are retained. Missing bookmaker-hour quotes stay missing; no odds are imputed. Source match mapping uses season, final score, date within one day, and home/away name similarity.',
      'unmatched_sample':unmatched[:25],'ambiguous_sample':ambiguous[:25],
    }
    META.write_text(json.dumps(payload,indent=2,default=str))
    MOVEMENT_META.write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps(payload,indent=2,default=str))

if __name__=='__main__':main()
