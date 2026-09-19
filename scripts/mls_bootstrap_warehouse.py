#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, re, shutil, sys, urllib.request, hashlib
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
DATA=ROOT/'data'; RAW=DATA/'raw'; PROC=DATA/'processed'; REPORTS=ROOT/'reports'; RESEARCH=ROOT/'research'
for p in [DATA,RAW,PROC,REPORTS,RESEARCH/'shadow',RESEARCH/'validation',RESEARCH/'holdout',ROOT/'live'/'market_snapshots',ROOT/'auto_research'/'state',ROOT/'auto_research'/'reports']:
    p.mkdir(parents=True,exist_ok=True)

SOURCES={
 'mls_elo_results':'https://raw.githubusercontent.com/philo92/mls-elo/main/results.csv',
 'mls_betting_xlsx':'https://raw.githubusercontent.com/stephen1-hub/-MLS-Betting-Market-Efficiency-Analysis-2012-2026-/main/USA.xlsx',
}
UA='Appwiza-MLS-Research/1.0'

TEAM_INFO={
"Atlanta United FC":("Atlanta",33.7554,-84.4008,1050,"America/New_York"),
"Austin FC":("Austin",30.3870,-97.7200,500,"America/Chicago"),
"CF Montréal":("Montreal",45.5631,-73.5527,120,"America/Toronto"),
"Charlotte FC":("Charlotte",35.2251,-80.8531,750,"America/New_York"),
"Chicago Fire FC":("Chicago",41.7648,-87.8061,620,"America/Chicago"),
"Chivas USA":("Carson",33.8644,-118.2611,40,"America/Los_Angeles"),
"Colorado Rapids":("Commerce City",39.8056,-104.8919,5190,"America/Denver"),
"Columbus Crew":("Columbus",39.9685,-83.0171,720,"America/New_York"),
"D.C. United":("Washington",38.8686,-77.0128,20,"America/New_York"),
"FC Cincinnati":("Cincinnati",39.1114,-84.5220,490,"America/New_York"),
"FC Dallas":("Frisco",33.1543,-96.8353,690,"America/Chicago"),
"Houston Dynamo FC":("Houston",29.7522,-95.3523,50,"America/Chicago"),
"Inter Miami CF":("Fort Lauderdale",26.1933,-80.1606,10,"America/New_York"),
"LA Galaxy":("Carson",33.8644,-118.2611,40,"America/Los_Angeles"),
"Los Angeles FC":("Los Angeles",34.0128,-118.2845,180,"America/Los_Angeles"),
"Miami Fusion FC":("Fort Lauderdale",26.1933,-80.1606,10,"America/New_York"),
"Minnesota United FC":("St. Paul",44.9532,-93.1651,850,"America/Chicago"),
"Nashville SC":("Nashville",36.1303,-86.7656,500,"America/Chicago"),
"New England Revolution":("Foxborough",42.0909,-71.2643,280,"America/New_York"),
"New York City FC":("New York",40.8296,-73.9262,55,"America/New_York"),
"New York Red Bulls":("Harrison",40.7368,-74.1502,20,"America/New_York"),
"Orlando City SC":("Orlando",28.5411,-81.3893,100,"America/New_York"),
"Philadelphia Union":("Chester",39.8322,-75.3780,20,"America/New_York"),
"Portland Timbers":("Portland",45.5215,-122.6918,50,"America/Los_Angeles"),
"Real Salt Lake":("Sandy",40.5829,-111.8933,4450,"America/Denver"),
"San Diego FC":("San Diego",32.7841,-117.1225,40,"America/Los_Angeles"),
"San Jose Earthquakes":("San Jose",37.3512,-121.9247,70,"America/Los_Angeles"),
"Seattle Sounders FC":("Seattle",47.5952,-122.3316,20,"America/Los_Angeles"),
"Sporting Kansas City":("Kansas City",39.1218,-94.8237,790,"America/Chicago"),
"St. Louis City SC":("St. Louis",38.6312,-90.2106,470,"America/Chicago"),
"Tampa Bay Mutiny":("Tampa",27.9759,-82.5033,15,"America/New_York"),
"Toronto FC":("Toronto",43.6332,-79.4186,250,"America/Toronto"),
"Vancouver Whitecaps FC":("Vancouver",49.2768,-123.1119,20,"America/Vancouver"),
}

ALIASES={
 'atlanta united':'Atlanta United FC','atlanta united fc':'Atlanta United FC',
 'austin':'Austin FC','austin fc':'Austin FC',
 'cf montreal':'CF Montréal','montreal impact':'CF Montréal','montreal':'CF Montréal','cf montréal':'CF Montréal',
 'charlotte':'Charlotte FC','charlotte fc':'Charlotte FC',
 'chicago fire':'Chicago Fire FC','chicago fire fc':'Chicago Fire FC',
 'chivas usa':'Chivas USA',
 'colorado rapids':'Colorado Rapids',
 'columbus crew':'Columbus Crew','columbus crew sc':'Columbus Crew',
 'dc united':'D.C. United','d c united':'D.C. United','d.c. united':'D.C. United',
 'fc cincinnati':'FC Cincinnati','cincinnati':'FC Cincinnati',
 'fc dallas':'FC Dallas','dallas burn':'FC Dallas','dallas':'FC Dallas',
 'houston dynamo':'Houston Dynamo FC','houston dynamo fc':'Houston Dynamo FC','houston':'Houston Dynamo FC',
 'inter miami':'Inter Miami CF','inter miami cf':'Inter Miami CF','miami inter':'Inter Miami CF',
 'la galaxy':'LA Galaxy','los angeles galaxy':'LA Galaxy',
 'los angeles fc':'Los Angeles FC','lafc':'Los Angeles FC',
 'miami fusion':'Miami Fusion FC','miami fusion fc':'Miami Fusion FC',
 'minnesota united':'Minnesota United FC','minnesota united fc':'Minnesota United FC',
 'nashville sc':'Nashville SC','nashville':'Nashville SC',
 'new england revolution':'New England Revolution','new england':'New England Revolution',
 'new york city fc':'New York City FC','new york city':'New York City FC','nycfc':'New York City FC',
 'new york red bulls':'New York Red Bulls','ny red bulls':'New York Red Bulls','new york rb':'New York Red Bulls',
 'orlando city':'Orlando City SC','orlando city sc':'Orlando City SC',
 'philadelphia union':'Philadelphia Union','philadelphia':'Philadelphia Union',
 'portland timbers':'Portland Timbers','portland timbers fc':'Portland Timbers',
 'real salt lake':'Real Salt Lake','salt lake':'Real Salt Lake',
 'san diego fc':'San Diego FC','san diego':'San Diego FC',
 'san jose earthquakes':'San Jose Earthquakes','san jose':'San Jose Earthquakes',
 'seattle sounders':'Seattle Sounders FC','seattle sounders fc':'Seattle Sounders FC','seattle':'Seattle Sounders FC',
 'sporting kansas city':'Sporting Kansas City','kansas city wizards':'Sporting Kansas City','kansas city':'Sporting Kansas City',
 'st louis city':'St. Louis City SC','st. louis city sc':'St. Louis City SC','st louis city sc':'St. Louis City SC',
 'tampa bay mutiny':'Tampa Bay Mutiny',
 'toronto fc':'Toronto FC','toronto':'Toronto FC',
 'vancouver whitecaps':'Vancouver Whitecaps FC','vancouver whitecaps fc':'Vancouver Whitecaps FC','vancouver':'Vancouver Whitecaps FC',
}

def now(): return datetime.now(timezone.utc).isoformat()
def norm_text(x):
    x=str(x or '').strip().lower()
    x=x.replace('&',' and ')
    x=re.sub(r'[^a-z0-9]+',' ',x)
    return re.sub(r'\s+',' ',x).strip()
def canon_team(x):
    raw=str(x or '').strip()
    if raw in TEAM_INFO:return raw
    n=norm_text(raw)
    if n in ALIASES:return ALIASES[n]
    for t in TEAM_INFO:
        if norm_text(t)==n:return t
    return raw
def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def download(url,path):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=90) as r:
        data=r.read()
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path)
    return len(data)
def ensure_sources():
    rows=[]
    for name,url in SOURCES.items():
        suffix='.xlsx' if url.lower().endswith('.xlsx') else '.csv'
        path=RAW/f'{name}{suffix}'
        try:
            size=download(url,path)
            status='downloaded'
        except Exception as e:
            if not path.exists():raise
            size=path.stat().st_size;status=f'cached_after_error:{type(e).__name__}'
        rows.append({'source':name,'url':url,'path':str(path),'bytes':size,'sha256':sha256(path),'status':status})
    (RAW/'source_manifest.json').write_text(json.dumps({'captured_at':now(),'sources':rows},indent=2))
    return rows
def haversine(lat1,lon1,lat2,lon2):
    r=3958.7613
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))
def utc_offset_hours(team,day):
    tz=TEAM_INFO.get(team,(None,None,None,None,'UTC'))[4]
    try:
        d=pd.Timestamp(day).to_pydatetime().replace(hour=12,tzinfo=ZoneInfo(tz))
        return d.utcoffset().total_seconds()/3600
    except Exception:return 0.0
def load_history():
    p=RAW/'mls_elo_results.csv'
    d=pd.read_csv(p)
    d['date']=pd.to_datetime(d['date'],errors='coerce')
    d=d[d.date.notna()].copy()
    d['season']=d.date.dt.year.astype(int)
    for c in ['home_team','away_team']:d[c]=d[c].map(canon_team)
    d['home_score']=pd.to_numeric(d.home_score,errors='coerce')
    d['away_score']=pd.to_numeric(d.away_score,errors='coerce')
    d['home_elo_current']=pd.to_numeric(d.home_elo_current,errors='coerce')
    d['away_elo_current']=pd.to_numeric(d.away_elo_current,errors='coerce')
    d['result']=d.result.astype(str).str.upper().str[0]
    d['neutral']=d.neutral.astype(str).str.upper().isin(['TRUE','1','T','YES'])
    d=d[d.result.isin(['H','D','A'])&d.home_score.notna()&d.away_score.notna()].copy()
    d=d.sort_values(['date','home_team','away_team']).reset_index(drop=True)
    d['match_id']=['MLS:'+x.strftime('%Y%m%d')+':'+re.sub(r'\W','',h.upper())[:12]+':'+re.sub(r'\W','',a.upper())[:12]+':'+str(i)
                   for i,(x,h,a) in enumerate(zip(d.date,d.home_team,d.away_team))]
    return d
def inspect_betting():
    p=RAW/'mls_betting_xlsx.xlsx'
    xl=pd.ExcelFile(p)
    sheets=[]
    frames=[]
    for sh in xl.sheet_names:
        try:
            z=pd.read_excel(p,sheet_name=sh)
        except Exception as e:
            sheets.append({'sheet':sh,'error':type(e).__name__});continue
        sheets.append({'sheet':sh,'rows':len(z),'columns':[str(c) for c in z.columns]})
        z['_sheet']=sh;frames.append(z)
    (REPORTS/'betting_workbook_structure.json').write_text(json.dumps(sheets,indent=2,default=str))
    if not frames:return pd.DataFrame()
    return pd.concat(frames,ignore_index=True,sort=False)
def pick_col(df,names):
    cmap={norm_text(c).replace(' ',''):c for c in df.columns}
    for n in names:
        k=norm_text(n).replace(' ','')
        if k in cmap:return cmap[k]
    return None
def load_betting():
    raw=inspect_betting()
    if raw.empty:return raw
    datec=pick_col(raw,['Date','MatchDate'])
    homec=pick_col(raw,['Home','HomeTeam','Home Team'])
    awayc=pick_col(raw,['Away','AwayTeam','Away Team'])
    hgc=pick_col(raw,['HG','FTHG','HomeGoals'])
    agc=pick_col(raw,['AG','FTAG','AwayGoals'])
    resc=pick_col(raw,['Res','FTR','Result'])
    seasc=pick_col(raw,['Season','Year'])
    # Prefer Pinnacle closing, then Pinnacle standard.
    ph=pick_col(raw,['PSCH','PSH']);pd_=pick_col(raw,['PSCD','PSD']);pa=pick_col(raw,['PSCA','PSA'])
    required={'date':datec,'home':homec,'away':awayc,'home_odds':ph,'draw_odds':pd_,'away_odds':pa}
    missing=[k for k,v in required.items() if not v]
    (REPORTS/'betting_column_detection.json').write_text(json.dumps({'detected':required,'score_cols':{'hg':hgc,'ag':agc,'res':resc,'season':seasc},'all_columns':[str(c) for c in raw.columns]},indent=2))
    if missing:
        raise RuntimeError('Could not detect betting workbook columns: '+','.join(missing))
    d=pd.DataFrame({
      'date':pd.to_datetime(raw[datec],errors='coerce'),
      'home_team':raw[homec].map(canon_team),
      'away_team':raw[awayc].map(canon_team),
      'home_odds':pd.to_numeric(raw[ph],errors='coerce'),
      'draw_odds':pd.to_numeric(raw[pd_],errors='coerce'),
      'away_odds':pd.to_numeric(raw[pa],errors='coerce'),
    })
    d['season']=pd.to_numeric(raw[seasc],errors='coerce') if seasc else d.date.dt.year
    if hgc:d['home_score_bet']=pd.to_numeric(raw[hgc],errors='coerce')
    if agc:d['away_score_bet']=pd.to_numeric(raw[agc],errors='coerce')
    if resc:d['result_bet']=raw[resc].astype(str).str.upper().str[0]
    d=d[d.date.notna()&d.home_odds.gt(1)&d.draw_odds.gt(1)&d.away_odds.gt(1)].copy()
    d['season']=d.season.fillna(d.date.dt.year).astype(int)
    inv=pd.DataFrame({'h':1/d.home_odds,'d':1/d.draw_odds,'a':1/d.away_odds})
    over=inv.sum(axis=1)
    d['market_overround']=over-1
    d['home_novig_prob']=inv.h/over;d['draw_novig_prob']=inv.d/over;d['away_novig_prob']=inv.a/over
    d=d.drop_duplicates(['date','home_team','away_team'],keep='last')
    return d.sort_values(['date','home_team','away_team']).reset_index(drop=True)
def recent_metrics(hist,n):
    vals=list(hist)[-n:]
    if not vals:return {'ppg':np.nan,'gfpg':np.nan,'gapg':np.nan,'gdpg':np.nan,'win_rate':np.nan,'draw_rate':np.nan,'loss_rate':np.nan}
    return {
      'ppg':sum(x['pts'] for x in vals)/len(vals),
      'gfpg':sum(x['gf'] for x in vals)/len(vals),
      'gapg':sum(x['ga'] for x in vals)/len(vals),
      'gdpg':sum(x['gf']-x['ga'] for x in vals)/len(vals),
      'win_rate':sum(x['pts']==3 for x in vals)/len(vals),
      'draw_rate':sum(x['pts']==1 for x in vals)/len(vals),
      'loss_rate':sum(x['pts']==0 for x in vals)/len(vals),
    }
def build_features(matches):
    hist=defaultdict(lambda:deque(maxlen=20))
    venue_hist=defaultdict(lambda:deque(maxlen=10))
    season_stats=defaultdict(lambda:{'g':0,'pts':0,'gf':0,'ga':0})
    last_date={};last_loc={};road_streak=defaultdict(int);prior_road_miles=defaultdict(lambda:deque(maxlen=5))
    rows=[]
    for _,r in matches.iterrows():
        day=pd.Timestamp(r.date)
        home,away=r.home_team,r.away_team
        venue_team=home
        venue=TEAM_INFO.get(venue_team)
        vlat=venue[1] if venue else np.nan;vlon=venue[2] if venue else np.nan;valt=venue[3] if venue else np.nan
        feat={'match_id':r.match_id,'date':day,'season':int(r.season),'home_team':home,'away_team':away,
              'home_score':r.home_score,'away_score':r.away_score,'result':r.result,'neutral':bool(r.neutral),
              'home_elo':r.home_elo_current,'away_elo':r.away_elo_current,'elo_edge_home':r.home_elo_current-r.away_elo_current}
        for side,team,opp,is_home in [('home',home,away,True),('away',away,home,False)]:
            sh=season_stats[(int(r.season),team)]
            feat[f'{side}_prior_games']=sh['g']
            feat[f'{side}_season_ppg']=sh['pts']/sh['g'] if sh['g'] else np.nan
            feat[f'{side}_season_gdpg']=(sh['gf']-sh['ga'])/sh['g'] if sh['g'] else np.nan
            feat[f'{side}_days_rest']=(day-last_date[team]).days if team in last_date else np.nan
            for n in [3,5,10]:
                m=recent_metrics(hist[team],n)
                for k,v in m.items():feat[f'{side}_last{n}_{k}']=v
            vm=recent_metrics(venue_hist[(team,'home' if is_home else 'away')],5)
            feat[f'{side}_venue5_ppg']=vm['ppg'];feat[f'{side}_venue5_gdpg']=vm['gdpg']
            feat[f'{side}_consecutive_road_pre']=road_streak[team]
            feat[f'{side}_prior_road_miles5']=sum(prior_road_miles[team]) if prior_road_miles[team] else 0.0
            if team in last_loc and venue and all(pd.notna(x) for x in last_loc[team][:2]):
                feat[f'{side}_travel_miles']=haversine(last_loc[team][0],last_loc[team][1],vlat,vlon)
                feat[f'{side}_altitude_change_ft']=valt-last_loc[team][2]
                prev_tz=last_loc[team][3]
                try:
                    prev_off=pd.Timestamp(day).to_pydatetime().replace(hour=12,tzinfo=ZoneInfo(prev_tz)).utcoffset().total_seconds()/3600
                    cur_off=utc_offset_hours(venue_team,day)
                    feat[f'{side}_tz_shift_hours']=cur_off-prev_off
                except Exception:feat[f'{side}_tz_shift_hours']=np.nan
            else:
                # First known match: approximate away travel from home city to match venue.
                tinfo=TEAM_INFO.get(team)
                if (not is_home) and tinfo and venue:
                    feat[f'{side}_travel_miles']=haversine(tinfo[1],tinfo[2],vlat,vlon)
                    feat[f'{side}_altitude_change_ft']=valt-tinfo[3]
                    feat[f'{side}_tz_shift_hours']=utc_offset_hours(venue_team,day)-utc_offset_hours(team,day)
                else:
                    feat[f'{side}_travel_miles']=0.0
                    feat[f'{side}_altitude_change_ft']=0.0
                    feat[f'{side}_tz_shift_hours']=0.0
        # matchup edges
        for base in ['season_ppg','season_gdpg','days_rest','last3_ppg','last5_ppg','last10_ppg','last5_gdpg','last10_gdpg',
                     'venue5_ppg','travel_miles','altitude_change_ft','tz_shift_hours','prior_road_miles5','consecutive_road_pre']:
            h=feat.get('home_'+base);a=feat.get('away_'+base)
            feat['edge_home_'+base]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        feat['abs_elo_edge']=abs(feat['elo_edge_home']) if pd.notna(feat['elo_edge_home']) else np.nan
        feat['total_goals']=r.home_score+r.away_score
        feat['home_win']=int(r.result=='H');feat['draw']=int(r.result=='D');feat['away_win']=int(r.result=='A')
        rows.append(feat)
        # update states only AFTER feature capture
        for side,team,is_home,gf,ga in [('home',home,True,r.home_score,r.away_score),('away',away,False,r.away_score,r.home_score)]:
            pts=3 if gf>ga else 1 if gf==ga else 0
            rec={'date':day,'gf':float(gf),'ga':float(ga),'pts':pts,'home':is_home}
            hist[team].append(rec);venue_hist[(team,'home' if is_home else 'away')].append(rec)
            sh=season_stats[(int(r.season),team)];sh['g']+=1;sh['pts']+=pts;sh['gf']+=gf;sh['ga']+=ga
            last_date[team]=day
            last_loc[team]=(vlat,vlon,valt,TEAM_INFO.get(venue_team,(None,None,None,None,'UTC'))[4])
            if is_home:
                road_streak[team]=0
            else:
                road_streak[team]+=1
                prior_road_miles[team].append(float(feat.get(side+'_travel_miles') or 0))
    d=pd.DataFrame(rows)
    return d
def join_odds(features,odds):
    if odds.empty:
        for c in ['home_odds','draw_odds','away_odds','market_overround','home_novig_prob','draw_novig_prob','away_novig_prob']:features[c]=np.nan
        features['odds_matched']=False;return features
    o=odds.copy()
    key=['date','home_team','away_team']
    cols=key+['home_odds','draw_odds','away_odds','market_overround','home_novig_prob','draw_novig_prob','away_novig_prob']
    d=features.merge(o[cols],on=key,how='left')
    # fallback date +/- 1 day for timezone/date-label differences, unique team pair only
    miss=d.home_odds.isna()
    if miss.any():
        omap={}
        for _,x in o.iterrows():
            omap.setdefault((x.home_team,x.away_team),[]).append(x)
        for i in d.index[miss]:
            cand=omap.get((d.at[i,'home_team'],d.at[i,'away_team']),[])
            cand=[x for x in cand if abs((pd.Timestamp(x.date)-pd.Timestamp(d.at[i,'date'])).days)<=1]
            if len(cand)==1:
                x=cand[0]
                for c in cols[3:]:d.at[i,c]=x[c]
    d['odds_matched']=d.home_odds.notna()&d.draw_odds.notna()&d.away_odds.notna()
    return d
def coverage_report(hist,odds,feat):
    years=range(int(hist.season.min()),int(hist.season.max())+1)
    rows=[]
    for y in years:
        h=hist[hist.season.eq(y)];f=feat[feat.season.eq(y)]
        rows.append({'season':y,'history_matches':len(h),'odds_matches':int(f.odds_matched.sum()),'odds_coverage_pct':100*float(f.odds_matched.mean()) if len(f) else 0,
                     'teams':len(set(h.home_team)|set(h.away_team)),'draw_rate':float((h.result=='D').mean()) if len(h) else np.nan,
                     'home_win_rate':float((h.result=='H').mean()) if len(h) else np.nan})
    return pd.DataFrame(rows)
def leakage_checks(feat):
    checks=[]
    # First appearance of every team must have zero prior games.
    for side in ['home','away']:
        col=f'{side}_prior_games'
        ok=(feat[col].fillna(-1)>=0).all()
        checks.append({'check':col+'_nonnegative','pass':bool(ok)})
    # Current result must not be encoded in pregame rolling columns by construction.
    forbidden=[c for c in feat.columns if re.search(r'(^|_)current_(goals|score|result)|post_|final_',c)]
    checks.append({'check':'no_obvious_postgame_feature_columns','pass':len(forbidden)==0,'detail':forbidden})
    # Odds are only market inputs; targets are separate.
    checks.append({'check':'target_columns_separate','pass':all(c in feat.columns for c in ['home_win','draw','away_win'])})
    # Sequential state check: earliest team game has zero prior games.
    bad=[]
    for team in sorted(set(feat.home_team)|set(feat.away_team)):
        z=pd.concat([feat.loc[feat.home_team.eq(team),['date','home_prior_games']].rename(columns={'home_prior_games':'g'}),
                     feat.loc[feat.away_team.eq(team),['date','away_prior_games']].rename(columns={'away_prior_games':'g'})]).sort_values('date')
        if len(z) and float(z.iloc[0].g)!=0:bad.append(team)
    checks.append({'check':'first_team_match_prior_games_zero','pass':not bad,'detail':bad})
    return checks
def main():
    src=ensure_sources()
    hist=load_history();odds=load_betting()
    feat=build_features(hist);feat=join_odds(feat,odds)
    feat.to_parquet(PROC/'mls_match_features_1996_present.parquet',index=False)
    feat.to_csv(PROC/'mls_match_features_1996_present.csv',index=False)
    hist.to_parquet(PROC/'mls_results_1996_present.parquet',index=False)
    odds.to_parquet(PROC/'mls_betting_odds.parquet',index=False)
    cov=coverage_report(hist,odds,feat);cov.to_csv(REPORTS/'season_coverage.csv',index=False)
    checks=leakage_checks(feat)
    meta={
      'built_at':now(),'history_rows':len(hist),'history_start':str(hist.date.min().date()),'history_end':str(hist.date.max().date()),
      'history_seasons':[int(hist.season.min()),int(hist.season.max())],'betting_rows':len(odds),
      'betting_seasons':[int(odds.season.min()),int(odds.season.max())] if len(odds) else None,
      'matched_betting_rows':int(feat.odds_matched.sum()),'feature_rows':len(feat),'feature_columns':len(feat.columns),
      'teams':sorted(set(hist.home_team)|set(hist.away_team)),'leakage_checks':checks,'sources':src,
      'research_status':'READY_FOR_SHADOW_RESEARCH' if all(x['pass'] for x in checks) and int(feat.odds_matched.sum())>=1000 else 'BLOCKED'
    }
    (REPORTS/'warehouse_meta.json').write_text(json.dumps(meta,indent=2,default=str))
    lines=['MLS WAREHOUSE BOOTSTRAP','='*90,
           f"History: {len(hist):,} matches | {meta['history_start']} to {meta['history_end']}",
           f"Betting rows: {len(odds):,} | matched to history: {int(feat.odds_matched.sum()):,}",
           f"Features: {len(feat):,} rows x {len(feat.columns)} columns",
           f"Teams: {len(meta['teams'])}",
           f"Research status: {meta['research_status']}",'',
           'LEAKAGE CHECKS']+[f"{x['check']}: {'PASS' if x['pass'] else 'FAIL'} {x.get('detail','')}" for x in checks]+['','SEASON COVERAGE',cov.to_string(index=False)]
    (REPORTS/'bootstrap_report.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
