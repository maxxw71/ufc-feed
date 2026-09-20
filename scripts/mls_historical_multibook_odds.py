#!/usr/bin/env python3
from __future__ import annotations

import json, math, os, re, time, urllib.parse, urllib.request
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
ASA_TEAMS=PROC/'asa_teams.parquet'
OUT=PROC/'mls_historical_multibook_closing_odds.parquet'
FEATURES=PROC/'mls_historical_multibook_market_features.parquet'
META=REP/'historical_multibook_odds_meta.json'
CHECKPOINT=RAW/'checkpoint.json'

SPORT='soccer_usa_mls'
FIRST_AVAILABLE=pd.Timestamp('2020-06-27T03:05:00Z')
UA='Mozilla/5.0 AppwizaMLSHistoricalOdds/1.0'

TEAM_ALIASES={
    'CF Montreal':'CF Montréal',
    'Montreal Impact':'CF Montréal',
    'LAFC':'Los Angeles FC',
    'St. Louis CITY SC':'St. Louis City SC',
    'St Louis City SC':'St. Louis City SC',
    'DC United':'D.C. United',
    'D.C. United':'D.C. United',
    'Houston Dynamo':'Houston Dynamo FC',
    'Minnesota United':'Minnesota United FC',
    'Seattle Sounders':'Seattle Sounders FC',
    'Vancouver Whitecaps':'Vancouver Whitecaps FC',
    'Orlando City':'Orlando City SC',
    'Inter Miami':'Inter Miami CF',
    'Atlanta United':'Atlanta United FC',
}

def now(): return datetime.now(timezone.utc).isoformat()

def cteam(x):
    raw=str(x or '').strip()
    return canon_team(TEAM_ALIASES.get(raw,raw))

def norm(x):
    x=cteam(x)
    return re.sub(r'[^a-z0-9]+','',x.lower())

def american_to_decimal(v):
    v=float(v)
    return 1+v/100 if v>0 else 1+100/abs(v)

def fetch_json(url,timeout=90,retries=5):
    last=None
    for a in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                body=resp.read().decode('utf-8','replace')
                headers={k.lower():v for k,v in resp.headers.items()}
            return json.loads(body),headers
        except Exception as e:
            last=e
            if getattr(e,'code',None)==429:
                time.sleep(min(120,10*(a+1)))
            else:
                time.sleep(min(30,2*(a+1)))
    raise last

def load_base():
    p=next((x for x in BASE_CANDIDATES if x.exists()),None)
    if not p: raise RuntimeError('MLS base warehouse missing')
    d=pd.read_parquet(p).copy()
    d['match_id']=d.match_id.astype(str)
    return d,p

def ensure_asa_games():
    if ASA_GAMES.exists(): return pd.read_parquet(ASA_GAMES)
    from itscalledsoccer import AmericanSoccerAnalysis
    asa=AmericanSoccerAnalysis();parts=[]
    for y in range(2013,2027):
        z=asa.get_games(leagues='mls',season_name=str(y))
        if not isinstance(z,pd.DataFrame): z=pd.DataFrame(z)
        if len(z):
            z['_season_requested']=y;parts.append(z)
    if not parts:raise RuntimeError('Unable to rebuild ASA games')
    g=pd.concat(parts,ignore_index=True,sort=False);g.to_parquet(ASA_GAMES,index=False);return g

def match_schedule(base):
    games=ensure_asa_games().copy()
    games['game_id']=games.game_id.astype(str)
    games['kickoff_utc']=pd.to_datetime(games.date_time_utc,errors='coerce',utc=True)
    gkick=dict(zip(games.game_id,games.kickoff_utc))
    rows=[]
    for _,r in base.iterrows():
        gid=str(r.asa_game_id) if 'asa_game_id' in base.columns and pd.notna(r.get('asa_game_id')) else None
        kick=gkick.get(gid)
        if pd.isna(kick):continue
        if kick<FIRST_AVAILABLE:continue
        rows.append({
          'match_id':str(r.match_id),'season':int(r.season),'kickoff_utc':pd.Timestamp(kick),
          'home_team':cteam(r.home_team),'away_team':cteam(r.away_team),
        })
    return pd.DataFrame(rows)

def checkpoint():
    if CHECKPOINT.exists():
        try:return json.loads(CHECKPOINT.read_text())
        except Exception:pass
    return {'completed_snapshot_dates':[],'requests':0,'errors':[]}

def save_checkpoint(cp):
    CHECKPOINT.write_text(json.dumps(cp,indent=2,default=str))

def snapshot_url(api_key,date_iso):
    regions=os.getenv('THE_ODDS_API_REGIONS','us,uk,eu')
    bookmakers=os.getenv('THE_ODDS_API_BOOKMAKERS','').strip()
    q={
      'apiKey':api_key,'markets':'h2h','oddsFormat':'decimal','dateFormat':'iso','date':date_iso
    }
    if bookmakers:q['bookmakers']=bookmakers
    else:q['regions']=regions
    return f'https://api.the-odds-api.com/v4/historical/sports/{SPORT}/odds?'+urllib.parse.urlencode(q)

def parse_h2h(event,match_id,snapshot_requested,snapshot_actual):
    rows=[]
    home=cteam(event.get('home_team'));away=cteam(event.get('away_team'))
    for book in event.get('bookmakers') or []:
        bkey=str(book.get('key') or book.get('title') or 'unknown')
        btitle=str(book.get('title') or bkey)
        for market in book.get('markets') or []:
            if market.get('key')!='h2h':continue
            prices={}
            updates={}
            for o in market.get('outcomes') or []:
                name=cteam(o.get('name'))
                price=pd.to_numeric(o.get('price'),errors='coerce')
                if pd.isna(price):continue
                if norm(name)==norm(home):sel='home'
                elif norm(name)==norm(away):sel='away'
                elif str(o.get('name','')).strip().lower() in {'draw','tie'}:sel='draw'
                else:continue
                prices[sel]=float(price)
            if {'home','draw','away'}<=set(prices):
                inv=[1/prices[x] for x in ['home','draw','away']];s=sum(inv)
                rows.append({
                  'match_id':match_id,'source':'The Odds API','sport_key':SPORT,
                  'snapshot_requested_utc':snapshot_requested,'snapshot_actual_utc':snapshot_actual,
                  'event_id':str(event.get('id')),'commence_time':event.get('commence_time'),
                  'home_team':home,'away_team':away,'bookmaker_key':bkey,'bookmaker_title':btitle,
                  'bookmaker_last_update':book.get('last_update') or market.get('last_update'),
                  'home_close_odds':prices['home'],'draw_close_odds':prices['draw'],'away_close_odds':prices['away'],
                  'overround':s-1,
                  'home_close_novig_prob':(1/prices['home'])/s,
                  'draw_close_novig_prob':(1/prices['draw'])/s,
                  'away_close_novig_prob':(1/prices['away'])/s,
                })
    return rows

def find_event(data,match):
    events=data.get('data') if isinstance(data,dict) and isinstance(data.get('data'),list) else data
    if not isinstance(events,list):return None
    h=norm(match.home_team);a=norm(match.away_team)
    candidates=[]
    for ev in events:
        eh=norm(ev.get('home_team'));ea=norm(ev.get('away_team'))
        if eh==h and ea==a:return ev
        # Keep a date/team fuzzy fallback only for exact canonical pairs reversed nowhere.
        if {eh,ea}=={h,a}:candidates.append(ev)
    return candidates[0] if len(candidates)==1 else None

def derive_features(odds,schedule):
    if odds.empty:return pd.DataFrame(columns=['match_id'])
    rows=[]
    sharp_keys={'pinnacle','pinnacle_us','pinnacle_eu'}
    for mid,g in odds.groupby('match_id'):
        row={'match_id':str(mid),'hist_close_book_count':int(g.bookmaker_key.nunique())}
        for side in ['home','draw','away']:
            col=f'{side}_close_odds';v=pd.to_numeric(g[col],errors='coerce').dropna()
            row[f'hist_close_{side}_mean_odds']=float(v.mean()) if len(v) else np.nan
            row[f'hist_close_{side}_median_odds']=float(v.median()) if len(v) else np.nan
            row[f'hist_close_{side}_max_odds']=float(v.max()) if len(v) else np.nan
            row[f'hist_close_{side}_min_odds']=float(v.min()) if len(v) else np.nan
            row[f'hist_close_{side}_std_odds']=float(v.std(ddof=0)) if len(v)>1 else 0.0 if len(v)==1 else np.nan
            p=pd.to_numeric(g[f'{side}_close_novig_prob'],errors='coerce').dropna()
            row[f'hist_close_{side}_consensus_novig_prob']=float(p.mean()) if len(p) else np.nan
        row['hist_close_mean_overround']=float(pd.to_numeric(g.overround,errors='coerce').mean())
        sharp=g[g.bookmaker_key.astype(str).str.lower().isin(sharp_keys)]
        row['hist_close_pinnacle_available']=int(len(sharp)>0)
        if len(sharp):
            s=sharp.iloc[-1]
            for side in ['home','draw','away']:
                row[f'hist_close_pinnacle_{side}_odds']=s[f'{side}_close_odds']
                row[f'hist_close_pinnacle_{side}_novig_prob']=s[f'{side}_close_novig_prob']
        rows.append(row)
    return pd.DataFrame(rows)

def main():
    api_key=os.getenv('THE_ODDS_API_KEY','').strip()
    base,base_path=load_base()
    schedule=match_schedule(base)

    if not api_key:
        meta={
          'built_at':now(),'status':'CREDENTIAL_REQUIRED','provider':'The Odds API',
          'coverage_start':FIRST_AVAILABLE.isoformat(),'target_matches':len(schedule),
          'target_kickoff_groups':int(schedule.kickoff_utc.nunique()) if len(schedule) else 0,
          'estimated_credits_us_only':int(schedule.kickoff_utc.nunique())*10 if len(schedule) else 0,
          'estimated_credits_default_3_regions':int(schedule.kickoff_utc.nunique())*30 if len(schedule) else 0,
          'base_file':str(base_path),'output':str(OUT),'features_output':str(FEATURES),
          'required_env':'THE_ODDS_API_KEY',
          'optional_env':['THE_ODDS_API_REGIONS','THE_ODDS_API_BOOKMAKERS'],
          'integrity_note':'No unlicensed public workbook is imported. Missing historical licensed data remains missing.'
        }
        META.write_text(json.dumps(meta,indent=2))
        print(json.dumps(meta,indent=2));return

    cp=checkpoint()
    done=set(cp.get('completed_snapshot_dates') or [])
    existing=pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame()
    existing_mids=set(existing.match_id.astype(str)) if len(existing) else set()
    pending=schedule[~schedule.match_id.isin(existing_mids)].copy()

    # One request can settle every MLS match sharing a kickoff timestamp.
    groups=[]
    for kick,g in pending.groupby('kickoff_utc'):
        requested=(pd.Timestamp(kick)-pd.Timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        groups.append((pd.Timestamp(kick),requested,g.copy()))

    newrows=[];quota={}
    for n,(kick,requested,g) in enumerate(groups,1):
        if requested in done:continue
        url=snapshot_url(api_key,requested)
        try:
            data,headers=fetch_json(url)
            cp['requests']=int(cp.get('requests',0))+1
            quota={k:v for k,v in headers.items() if k.startswith('x-requests-')}
            actual=(data.get('timestamp') if isinstance(data,dict) else None) or requested
            matched=0
            for _,m in g.iterrows():
                ev=find_event(data,m)
                if not ev:continue
                z=parse_h2h(ev,str(m.match_id),requested,actual)
                if z:newrows.extend(z);matched+=1
            done.add(requested);cp['completed_snapshot_dates']=sorted(done);save_checkpoint(cp)
            print('snapshot',n,'/',len(groups),'kickoff',kick,'matches',len(g),'matched',matched,'rows',len(newrows),'quota',quota,flush=True)
            time.sleep(.15)
        except Exception as e:
            cp.setdefault('errors',[]).append({'requested':requested,'error':type(e).__name__+': '+str(e)[:300]})
            save_checkpoint(cp)
            print('ERROR',requested,type(e).__name__,str(e)[:240],flush=True)
            if getattr(e,'code',None) in {401,402,403}:break

    if newrows:
        new=pd.DataFrame(newrows)
        odds=pd.concat([existing,new],ignore_index=True,sort=False) if len(existing) else new
        odds=odds.drop_duplicates(['match_id','bookmaker_key','snapshot_actual_utc'],keep='last')
    else:odds=existing.copy()
    if len(odds):odds.to_parquet(OUT,index=False)
    feat=derive_features(odds,schedule)
    feat.to_parquet(FEATURES,index=False)

    cov=odds.groupby('match_id').bookmaker_key.nunique() if len(odds) else pd.Series(dtype=float)
    meta={
      'built_at':now(),'status':'PARTIAL_OR_COMPLETE','provider':'The Odds API',
      'coverage_start':FIRST_AVAILABLE.isoformat(),'target_matches':len(schedule),
      'matches_with_multibook_close':int(cov.size),'book_rows':len(odds),
      'median_books_per_match':float(cov.median()) if len(cov) else 0.0,
      'max_books_per_match':int(cov.max()) if len(cov) else 0,
      'providers':sorted(odds.bookmaker_key.astype(str).unique()) if len(odds) else [],
      'quota_headers':quota,'checkpoint':str(CHECKPOINT),'output':str(OUT),'features_output':str(FEATURES),
      'definition':'Closing quote = The Odds API historical snapshot closest to and not later than kickoff, requested at kickoff minus one second. Soccer h2h is regulation 3-way.',
      'integrity_note':'No unlicensed fallback is used. A match is absent if the licensed source does not return a complete home/draw/away quote.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
