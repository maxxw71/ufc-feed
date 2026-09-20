#!/usr/bin/env python3
from __future__ import annotations

import io, json, math, os, re, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'; REP=ROOT/'reports'; RAW=ROOT/'data/raw/the_odds_api_mls'
for p in [PROC,REP,RAW]: p.mkdir(parents=True,exist_ok=True)

BASE_CANDIDATES=[
    PROC/'mls_match_features_master.parquet',
    PROC/'mls_match_features_weather_enriched.parquet',
    PROC/'mls_match_features_advanced.parquet',
]
ASA_GAMES=PROC/'asa_mls_games_2012_present.parquet'
OUT=PROC/'mls_historical_multibook_closing_odds.parquet'
FEATURES=PROC/'mls_historical_multibook_market_features.parquet'
META=REP/'historical_multibook_odds_meta.json'
MOVEMENT_META=REP/'historical_multibook_movement_meta.json'

SPORT='soccer_usa_mls'
FIRST_AVAILABLE=pd.Timestamp('2020-06-27T03:05:00Z')
PUBLIC_SOURCE='https://raw.githubusercontent.com/stephen1-hub/-MLS-Betting-Market-Efficiency-Analysis-2012-2026-/main/USA.xlsx'
UA='Mozilla/5.0 AppwizaMLSHistoricalOdds/2.0'

TEAM_ALIASES={
    'Atlanta United':'Atlanta United FC','Atlanta Utd':'Atlanta United FC',
    'CF Montreal':'CF Montréal','Montreal Impact':'CF Montréal',
    'D.C. United':'D.C. United','DC United':'D.C. United',
    'Houston Dynamo':'Houston Dynamo FC','Houston Dynamo FC':'Houston Dynamo FC',
    'Inter Miami':'Inter Miami CF','LA Galaxy':'Los Angeles Galaxy',
    'Los Angeles Galaxy':'Los Angeles Galaxy','LAFC':'Los Angeles FC',
    'Los Angeles FC':'Los Angeles FC','Minnesota United':'Minnesota United FC',
    'NYCFC':'New York City FC','New York City':'New York City FC',
    'New York Red Bulls':'New York Red Bulls','Orlando City':'Orlando City SC',
    'Orlando City SC':'Orlando City SC','Seattle Sounders':'Seattle Sounders FC',
    'Seattle Sounders FC':'Seattle Sounders FC','St. Louis City':'St. Louis City SC',
    'St. Louis City SC':'St. Louis City SC','St. Louis CITY SC':'St. Louis City SC',
    'Vancouver Whitecaps':'Vancouver Whitecaps FC','Vancouver Whitecaps FC':'Vancouver Whitecaps FC',
    'San Jose Earthquakes':'San Jose Earthquakes','Sporting KC':'Sporting Kansas City',
    'Sporting Kansas City':'Sporting Kansas City','Columbus Crew SC':'Columbus Crew',
    'Columbus Crew':'Columbus Crew','Chicago Fire':'Chicago Fire FC',
    'Chicago Fire FC':'Chicago Fire FC','New England Revolution':'New England Revolution',
    'Philadelphia Union':'Philadelphia Union','Portland Timbers':'Portland Timbers',
    'Real Salt Lake':'Real Salt Lake','Colorado Rapids':'Colorado Rapids',
    'FC Dallas':'FC Dallas','Toronto FC':'Toronto FC','Charlotte FC':'Charlotte FC',
    'Nashville SC':'Nashville SC','Austin FC':'Austin FC','San Diego FC':'San Diego FC',
}

def now(): return datetime.now(timezone.utc).isoformat()

def cteam(x):
    raw=str(x or '').strip()
    return canon_team(TEAM_ALIASES.get(raw,raw))

def norm(x):
    return re.sub(r'[^a-z0-9]+','',cteam(x).lower())

def slug(x):
    return re.sub(r'[^a-z0-9]+','_',str(x).lower()).strip('_')

def valid_triplet(vals):
    a=np.array(vals,dtype=float)
    return bool(np.isfinite(a).all() and (a>1.0).all())

def novig(h,d,a):
    vals=np.array([h,d,a],dtype=float); inv=1.0/vals; s=float(inv.sum())
    return (float(inv[0]/s),float(inv[1]/s),float(inv[2]/s),float(s-1.0))

def load_base():
    p=next((x for x in BASE_CANDIDATES if x.exists()),None)
    if not p: raise RuntimeError('MLS base warehouse missing')
    d=pd.read_parquet(p).copy()
    d['match_id']=d.match_id.astype(str)
    return d,p

def fetch_public_workbook():
    req=urllib.request.Request(PUBLIC_SOURCE,headers={'User-Agent':UA,'Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    data=urllib.request.urlopen(req,timeout=120).read()
    if len(data)<100000: raise RuntimeError(f'Public MLS workbook unexpectedly small: {len(data)} bytes')
    p=RAW/'public_USA.xlsx'; p.write_bytes(data)
    return data,p

def detect_groups(columns):
    opens=defaultdict(dict); closes=defaultdict(dict)
    for c0 in columns:
        c=str(c0).strip()
        m=re.fullmatch(r'([A-Za-z0-9]+)([HDA])',c)
        if m: opens[m.group(1)][m.group(2)]=c
        m=re.fullmatch(r'([A-Za-z0-9]+)C([HDA])',c)
        if m: closes[m.group(1)][m.group(2)]=c
    complete_open={k:v for k,v in opens.items() if set(v)=={'H','D','A'}}
    complete_close={k:v for k,v in closes.items() if set(v)=={'H','D','A'}}
    paired=sorted(set(complete_open)&set(complete_close))
    return complete_open,complete_close,paired

def prepare_public(data):
    x=pd.read_excel(io.BytesIO(data))
    x.columns=[str(c).strip() for c in x.columns]
    required=['Season','Date','Home','Away','HG','AG','Res']
    miss=[c for c in required if c not in x.columns]
    if miss: raise RuntimeError(f'Public workbook missing base columns {miss}; got={list(x.columns)}')
    op,cl,paired=detect_groups(x.columns)
    if not paired:
        raise RuntimeError(f'No paired opening/closing 1X2 bookmaker groups detected; columns={list(x.columns)}')
    keep=required+sorted({c for p in paired for c in list(op[p].values())+list(cl[p].values())})
    x=x[keep].copy()
    x['Season']=pd.to_numeric(x.Season,errors='coerce').astype('Int64')
    x['Date']=pd.to_datetime(x.Date,errors='coerce',dayfirst=True,utc=True)
    for c in keep:
        if c not in required: x[c]=pd.to_numeric(x[c],errors='coerce')
    x['HomeCanon']=x.Home.map(cteam); x['AwayCanon']=x.Away.map(cteam)
    x=x[x.Season.notna()&x.Date.notna()&x.HomeCanon.notna()&x.AwayCanon.notna()].copy().reset_index(drop=True)
    x['source_row_id']=np.arange(len(x),dtype=int)
    return x,op,cl,paired

def match_public_matches(src,base):
    b=base.copy()
    b['match_day']=pd.to_datetime(b.date,errors='coerce',utc=True).dt.normalize()
    b['home_canon']=b.home_team.map(cteam); b['away_canon']=b.away_team.map(cteam)
    b['home_score_n']=pd.to_numeric(b.home_score,errors='coerce'); b['away_score_n']=pd.to_numeric(b.away_score,errors='coerce')
    by_teams={k:z.copy() for k,z in b.groupby(['home_canon','away_canon'])}
    matched={};unmatched=[];ambiguous=[]
    for _,r in src.iterrows():
        cand=by_teams.get((r.HomeCanon,r.AwayCanon),pd.DataFrame()).copy()
        if cand.empty:
            unmatched.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'reason':'TEAM_PAIR'})
            continue
        cand=cand[pd.to_numeric(cand.season,errors='coerce').eq(int(r.Season))]
        if cand.empty:
            unmatched.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'reason':'SEASON'})
            continue
        target=pd.Timestamp(r.Date).normalize()
        cand['day_diff']=(cand.match_day-target).abs().dt.days
        exact=cand[cand.day_diff.eq(0)].copy(); pick=None
        if len(exact)==1:
            pick=exact.iloc[0]
        else:
            score=cand[cand.day_diff.le(1)].copy()
            hg=pd.to_numeric(r.HG,errors='coerce'); ag=pd.to_numeric(r.AG,errors='coerce')
            if pd.notna(hg) and pd.notna(ag):
                score=score[score.home_score_n.eq(float(hg))&score.away_score_n.eq(float(ag))]
            if len(score)==1: pick=score.iloc[0]
            else:
                pool=exact if len(exact) else score
                if len(pool)==1: pick=pool.iloc[0]
                else:
                    ambiguous.append({'source_row_id':int(r.source_row_id),'season':int(r.Season),'date':str(r.Date.date()),'home':r.Home,'away':r.Away,'candidates':int(len(pool))})
        if pick is not None: matched[int(r.source_row_id)]=str(pick.match_id)
    return matched,unmatched,ambiguous

def public_long_rows(src,op,cl,paired,matched):
    rows=[];coverage={p:0 for p in paired}
    for _,r in src.iterrows():
        sid=int(r.source_row_id); mid=matched.get(sid)
        if not mid: continue
        for p in paired:
            ov=[pd.to_numeric(r[op[p][s]],errors='coerce') for s in 'HDA']
            cv=[pd.to_numeric(r[cl[p][s]],errors='coerce') for s in 'HDA']
            if not valid_triplet(ov) or not valid_triplet(cv): continue
            oh,od,oa=[float(v) for v in ov]; ch,cd,ca=[float(v) for v in cv]
            onh,ond,ona,oor=novig(oh,od,oa); cnh,cnd,cna,cor=novig(ch,cd,ca)
            aggregate=bool(re.search(r'avg|max|average|mean',p,re.I))
            rows.append({
                'match_id':mid,'source':'public_football_data_style_workbook','source_row_id':sid,
                'source_season':int(r.Season),'source_date':r.Date.date().isoformat(),
                'bookmaker_key':slug(p),'bookmaker_title':p,'provider_type':'aggregate' if aggregate else 'bookmaker',
                'home_open_odds':oh,'draw_open_odds':od,'away_open_odds':oa,
                'home_close_odds':ch,'draw_close_odds':cd,'away_close_odds':ca,
                'open_overround':oor,'close_overround':cor,
                'home_open_novig_prob':onh,'draw_open_novig_prob':ond,'away_open_novig_prob':ona,
                'home_close_novig_prob':cnh,'draw_close_novig_prob':cnd,'away_close_novig_prob':cna,
                'home_prob_move':cnh-onh,'draw_prob_move':cnd-ond,'away_prob_move':cna-ona,
                'home_odds_move_pct':ch/oh-1.0,'draw_odds_move_pct':cd/od-1.0,'away_odds_move_pct':ca/oa-1.0,
            })
            coverage[p]+=1
    return pd.DataFrame(rows),coverage

def derive_public_features(long):
    if long.empty: return pd.DataFrame(columns=['match_id'])
    rows=[]
    for mid,g in long.groupby('match_id'):
        row={'match_id':str(mid)}
        books=g[g.provider_type.eq('bookmaker')].copy()
        row['hist_mb_book_count']=int(books.bookmaker_key.nunique())
        row['hist_mb_provider_count_including_aggregates']=int(g.bookmaker_key.nunique())
        if len(books):
            row['hist_mb_open_mean_overround']=float(books.open_overround.mean())
            row['hist_mb_close_mean_overround']=float(books.close_overround.mean())
            row['hist_mb_mean_abs_prob_move']=float(books[['home_prob_move','draw_prob_move','away_prob_move']].abs().to_numpy().mean())
            for side in ['home','draw','away']:
                oo=pd.to_numeric(books[f'{side}_open_odds'],errors='coerce').dropna()
                co=pd.to_numeric(books[f'{side}_close_odds'],errors='coerce').dropna()
                opb=pd.to_numeric(books[f'{side}_open_novig_prob'],errors='coerce').dropna()
                cpb=pd.to_numeric(books[f'{side}_close_novig_prob'],errors='coerce').dropna()
                mv=pd.to_numeric(books[f'{side}_prob_move'],errors='coerce').dropna()
                row[f'hist_mb_open_{side}_mean_odds']=float(oo.mean()) if len(oo) else np.nan
                row[f'hist_mb_open_{side}_median_odds']=float(oo.median()) if len(oo) else np.nan
                row[f'hist_mb_open_{side}_std_odds']=float(oo.std(ddof=0)) if len(oo)>1 else 0.0 if len(oo)==1 else np.nan
                row[f'hist_mb_close_{side}_mean_odds']=float(co.mean()) if len(co) else np.nan
                row[f'hist_mb_close_{side}_median_odds']=float(co.median()) if len(co) else np.nan
                row[f'hist_mb_close_{side}_std_odds']=float(co.std(ddof=0)) if len(co)>1 else 0.0 if len(co)==1 else np.nan
                row[f'hist_mb_open_{side}_consensus_novig_prob']=float(opb.mean()) if len(opb) else np.nan
                row[f'hist_mb_close_{side}_consensus_novig_prob']=float(cpb.mean()) if len(cpb) else np.nan
                row[f'hist_mb_move_{side}_consensus_prob']=float(mv.mean()) if len(mv) else np.nan
                row[f'hist_mb_move_{side}_prob_up_share']=float((mv>0).mean()) if len(mv) else np.nan
        for _,q in g.iterrows():
            p=slug(q.bookmaker_key)
            for side in ['home','draw','away']:
                row[f'hist_mb_{p}_open_{side}_odds']=float(q[f'{side}_open_odds'])
                row[f'hist_mb_{p}_close_{side}_odds']=float(q[f'{side}_close_odds'])
                row[f'hist_mb_{p}_open_{side}_novig_prob']=float(q[f'{side}_open_novig_prob'])
                row[f'hist_mb_{p}_close_{side}_novig_prob']=float(q[f'{side}_close_novig_prob'])
                row[f'hist_mb_{p}_move_{side}_prob']=float(q[f'{side}_prob_move'])
                row[f'hist_mb_{p}_move_{side}_odds_pct']=float(q[f'{side}_odds_move_pct'])
            row[f'hist_mb_{p}_open_overround']=float(q.open_overround)
            row[f'hist_mb_{p}_close_overround']=float(q.close_overround)
        rows.append(row)
    return pd.DataFrame(rows)

def run_public():
    base,base_path=load_base()
    data,raw_path=fetch_public_workbook()
    src,op,cl,paired=prepare_public(data)
    matched,unmatched,ambiguous=match_public_matches(src,base)
    long,coverage=public_long_rows(src,op,cl,paired,matched)
    if long.empty: raise RuntimeError('Public multi-book source produced zero complete opening/closing pairs')
    long=long.drop_duplicates(['match_id','bookmaker_key'],keep='last')
    long.to_parquet(OUT,index=False)
    feat=derive_public_features(long)
    if feat.empty: raise RuntimeError('Public multi-book feature derivation produced zero rows')
    feat.to_parquet(FEATURES,index=False)

    provider_meta=[]
    for p in paired:
        n=int(coverage.get(p,0))
        if n==0: continue
        aggregate=bool(re.search(r'avg|max|average|mean',p,re.I))
        provider_meta.append({'source_prefix':p,'key':slug(p),'provider_type':'aggregate' if aggregate else 'bookmaker','matched_open_close_rows':n})
    bookkeys=sorted(long.loc[long.provider_type.eq('bookmaker'),'bookmaker_key'].unique().tolist())
    aggkeys=sorted(long.loc[long.provider_type.eq('aggregate'),'bookmaker_key'].unique().tolist())
    byseason={str(int(k)):int(v) for k,v in long.groupby('source_season').match_id.nunique().to_dict().items()}
    feature_cols=[c for c in feat.columns if c!='match_id']
    counts=long[long.provider_type.eq('bookmaker')].groupby('match_id').bookmaker_key.nunique()
    meta={
        'built_at':now(),'status':'PUBLIC_OPEN_CLOSE_COMPLETE','provider':'Public MLS workbook fallback',
        'source_url':PUBLIC_SOURCE,'raw_file':str(raw_path),'base_file':str(base_path),
        'source_rows_after_cleaning':len(src),'matched_source_matches':len(set(matched.values())),
        'match_rate':len(set(matched.values()))/len(src) if len(src) else 0.0,
        'unmatched_source_rows':len(unmatched),'ambiguous_source_rows':len(ambiguous),
        'paired_open_close_groups':paired,'bookmaker_keys':bookkeys,'aggregate_keys':aggkeys,
        'providers':provider_meta,'book_rows':int(len(long)),'matches_with_multibook_movement':int(long.match_id.nunique()),
        'matches_with_2plus_books':int((counts>=2).sum()),'matches_with_3plus_books':int((counts>=3).sum()),
        'median_books_per_match':float(counts.median()) if len(counts) else 0.0,
        'max_books_per_match':int(counts.max()) if len(counts) else 0,
        'season_match_coverage':byseason,'feature_rows':int(len(feat)),'feature_columns':int(len(feature_cols)),
        'output':str(OUT),'features_output':str(FEATURES),
        'opening_columns_pattern':'hist_mb_*_open_*',
        'closing_columns_pattern':'hist_mb_*_close_*',
        'movement_columns_pattern':'hist_mb_*_move_*',
        'timing_policy':'Opening fields are opening-time information. Closing and movement fields are valid only for closing-time/near-kickoff research or ex-post market validation; they must not be used in earlier-decision backtests.',
        'integrity_note':'Only rows with complete valid (>1.0) home/draw/away opening AND closing triplets are used for a provider. Missing provider quotes remain missing and are never imputed.',
        'unmatched_sample':unmatched[:25],'ambiguous_sample':ambiguous[:25],
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    MOVEMENT_META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

def fetch_json(url,timeout=90,retries=5):
    last=None
    for a in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                body=resp.read().decode('utf-8','replace'); headers={k.lower():v for k,v in resp.headers.items()}
            return json.loads(body),headers
        except Exception as e:
            last=e
            if getattr(e,'code',None)==429: time.sleep(min(120,10*(a+1)))
            else: time.sleep(min(30,2*(a+1)))
    raise last

def ensure_asa_games():
    if ASA_GAMES.exists(): return pd.read_parquet(ASA_GAMES)
    from itscalledsoccer import AmericanSoccerAnalysis
    asa=AmericanSoccerAnalysis();parts=[]
    for y in range(2013,2027):
        z=asa.get_games(leagues='mls',season_name=str(y))
        if not isinstance(z,pd.DataFrame): z=pd.DataFrame(z)
        if len(z): z['_season_requested']=y;parts.append(z)
    if not parts: raise RuntimeError('Unable to rebuild ASA games')
    g=pd.concat(parts,ignore_index=True,sort=False);g.to_parquet(ASA_GAMES,index=False);return g

def match_schedule(base):
    games=ensure_asa_games().copy();games['game_id']=games.game_id.astype(str)
    games['kickoff_utc']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    gkick=dict(zip(games.game_id,games.kickoff_utc));rows=[]
    for _,r in base.iterrows():
        gid=str(r.asa_game_id) if 'asa_game_id' in base.columns and pd.notna(r.get('asa_game_id')) else None
        kick=gkick.get(gid)
        if pd.isna(kick) or kick<FIRST_AVAILABLE: continue
        rows.append({'match_id':str(r.match_id),'season':int(r.season),'kickoff_utc':pd.Timestamp(kick),'home_team':cteam(r.home_team),'away_team':cteam(r.away_team)})
    return pd.DataFrame(rows)

def snapshot_url(api_key,date_iso):
    bookmakers=os.getenv('THE_ODDS_API_BOOKMAKERS','pinnacle,draftkings,fanduel,betmgm,williamhill_us,bovada,betrivers,betonlineag,betfair_ex_eu,williamhill').strip()
    q={'apiKey':api_key,'markets':'h2h','oddsFormat':'decimal','dateFormat':'iso','date':date_iso}
    if bookmakers:q['bookmakers']=bookmakers
    else:q['regions']=os.getenv('THE_ODDS_API_REGIONS','us,uk,eu')
    return f'https://api.the-odds-api.com/v4/historical/sports/{SPORT}/odds?'+urllib.parse.urlencode(q)

def find_event(data,match):
    events=data.get('data') if isinstance(data,dict) and isinstance(data.get('data'),list) else data
    if not isinstance(events,list): return None
    h=norm(match.home_team);a=norm(match.away_team);c=[]
    for ev in events:
        eh=norm(ev.get('home_team'));ea=norm(ev.get('away_team'))
        if eh==h and ea==a:return ev
        if {eh,ea}=={h,a}:c.append(ev)
    return c[0] if len(c)==1 else None

def parse_h2h(event,match_id,snapshot_requested,snapshot_actual):
    rows=[];home=cteam(event.get('home_team'));away=cteam(event.get('away_team'))
    for book in event.get('bookmakers') or []:
        bkey=str(book.get('key') or book.get('title') or 'unknown');btitle=str(book.get('title') or bkey)
        for market in book.get('markets') or []:
            if market.get('key')!='h2h':continue
            prices={}
            for o in market.get('outcomes') or []:
                name=cteam(o.get('name')); price=pd.to_numeric(o.get('price'),errors='coerce')
                if pd.isna(price):continue
                if norm(name)==norm(home):sel='home'
                elif norm(name)==norm(away):sel='away'
                elif str(o.get('name','')).strip().lower() in {'draw','tie'}:sel='draw'
                else:continue
                prices[sel]=float(price)
            if {'home','draw','away'}<=set(prices):
                nh,nd,na,ov=novig(prices['home'],prices['draw'],prices['away'])
                rows.append({'match_id':match_id,'source':'The Odds API','snapshot_requested_utc':snapshot_requested,'snapshot_actual_utc':snapshot_actual,
                    'event_id':str(event.get('id')),'commence_time':event.get('commence_time'),'home_team':home,'away_team':away,
                    'bookmaker_key':bkey,'bookmaker_title':btitle,'provider_type':'bookmaker',
                    'bookmaker_last_update':book.get('last_update') or market.get('last_update'),
                    'home_close_odds':prices['home'],'draw_close_odds':prices['draw'],'away_close_odds':prices['away'],
                    'close_overround':ov,'home_close_novig_prob':nh,'draw_close_novig_prob':nd,'away_close_novig_prob':na})
    return rows

def run_licensed():
    api_key=os.getenv('THE_ODDS_API_KEY','').strip()
    if not api_key: raise RuntimeError('MLS_HIST_ODDS_MODE=licensed requires THE_ODDS_API_KEY')
    base,base_path=load_base(); schedule=match_schedule(base)
    newrows=[];quota={};requests=0;errors=[]
    for n,(kick,g) in enumerate(schedule.groupby('kickoff_utc'),1):
        requested=(pd.Timestamp(kick)-pd.Timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        try:
            data,headers=fetch_json(snapshot_url(api_key,requested));requests+=1
            quota={k:v for k,v in headers.items() if k.startswith('x-requests-')}
            actual=(data.get('timestamp') if isinstance(data,dict) else None) or requested
            for _,m in g.iterrows():
                ev=find_event(data,m)
                if ev:newrows.extend(parse_h2h(ev,str(m.match_id),requested,actual))
            print('snapshot',n,'matches',len(g),'rows',len(newrows),'quota',quota,flush=True);time.sleep(.15)
        except Exception as e:
            errors.append({'requested':requested,'error':type(e).__name__+': '+str(e)[:300]})
            if getattr(e,'code',None) in {401,402,403}:break
    odds=pd.DataFrame(newrows)
    if len(odds): odds=odds.drop_duplicates(['match_id','bookmaker_key','snapshot_actual_utc'],keep='last');odds.to_parquet(OUT,index=False)
    feat=pd.DataFrame({'match_id':sorted(odds.match_id.astype(str).unique())}) if len(odds) else pd.DataFrame(columns=['match_id'])
    feat.to_parquet(FEATURES,index=False)
    meta={'built_at':now(),'status':'LICENSED_CLOSE_ONLY','provider':'The Odds API','coverage_start':FIRST_AVAILABLE.isoformat(),
          'target_matches':len(schedule),'matches_with_multibook_close':int(odds.match_id.nunique()) if len(odds) else 0,
          'book_rows':len(odds),'requests':requests,'errors':errors[-25:],'quota_headers':quota,'base_file':str(base_path),
          'output':str(OUT),'features_output':str(FEATURES),'timing_policy':'Licensed mode currently provides close snapshots only, not full movement.'}
    META.write_text(json.dumps(meta,indent=2,default=str)); MOVEMENT_META.write_text(json.dumps(meta,indent=2,default=str));print(json.dumps(meta,indent=2,default=str))

def main():
    mode=os.getenv('MLS_HIST_ODDS_MODE','public').strip().lower()
    if mode in {'licensed','the_odds_api','api'}: run_licensed()
    else: run_public()

if __name__=='__main__': main()
