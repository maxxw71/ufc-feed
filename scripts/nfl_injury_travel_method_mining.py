from __future__ import annotations

from pathlib import Path
from math import radians, sin, cos, asin, sqrt
from zoneinfo import ZoneInfo
import json
import os
import re
import time
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(os.environ.get("NFL_SOURCE_ROOT", "/home/anestishkurti92/nfl-predictor-v1"))
CTX = Path(os.environ.get("NFL_CONTEXT_ROOT", "/home/appwiza-runner/nfl-context-data"))
RAW = CTX / "raw"
OUT = CTX / "injury_travel_mining"
OUT.mkdir(parents=True, exist_ok=True)

SCHEDULES = ROOT / "data" / "raw" / "schedules_2006_2026.parquet"
BASE = ROOT / "research_v2" / "feature_store" / "pregame_team_sides.csv"
CONTEXT = CTX / "derived" / "team_game_pregame_context_2006_2026.parquet"
SNAPS = RAW / "snap_counts_2012_2026.parquet"
HIST_INJ = RAW / "injuries_2009_2024.parquet"

TEAM_MAP = {"SD":"LAC","OAK":"LV","STL":"LA","LAR":"LA"}
def canon(x):
    if pd.isna(x): return x
    return TEAM_MAP.get(str(x), str(x))

def norm_name(x):
    if pd.isna(x): return None
    return ''.join(ch for ch in str(x).lower() if ch.isalnum()) or None

def nnum(x): return pd.to_numeric(x, errors="coerce")

def haversine(lat1, lon1, lat2, lon2):
    if any(pd.isna(v) for v in [lat1,lon1,lat2,lon2]): return np.nan
    R=3958.7613
    p1,p2=radians(lat1),radians(lat2)
    dp=radians(lat2-lat1); dl=radians(lon2-lon1)
    a=sin(dp/2)**2+cos(p1)*cos(p2)*sin(dl/2)**2
    return 2*R*asin(sqrt(a))

def tz_offset_hours(tz, date):
    try:
        dt=pd.Timestamp(date).to_pydatetime().replace(hour=12,tzinfo=ZoneInfo(tz))
        return dt.utcoffset().total_seconds()/3600
    except Exception:
        return np.nan

# Approximate franchise home bases. Historical relocations are handled below.
BASES = {
'ARI':(33.5276,-112.2626,'America/Phoenix',1070),'ATL':(33.7554,-84.4008,'America/New_York',1050),
'BAL':(39.2780,-76.6227,'America/New_York',30),'BUF':(42.7738,-78.7868,'America/New_York',600),
'CAR':(35.2258,-80.8528,'America/New_York',750),'CHI':(41.8623,-87.6167,'America/Chicago',595),
'CIN':(39.0955,-84.5161,'America/New_York',490),'CLE':(41.5061,-81.6995,'America/New_York',585),
'DAL':(32.7473,-97.0945,'America/Chicago',610),'DEN':(39.7439,-105.0201,'America/Denver',5280),
'DET':(42.3400,-83.0456,'America/Detroit',600),'GB':(44.5013,-88.0622,'America/Chicago',640),
'HOU':(29.6847,-95.4107,'America/Chicago',50),'IND':(39.7601,-86.1639,'America/Indiana/Indianapolis',715),
'JAX':(30.3239,-81.6373,'America/New_York',15),'KC':(39.0489,-94.4839,'America/Chicago',890),
'LAC':(33.9535,-118.3392,'America/Los_Angeles',115),'LA':(33.9535,-118.3392,'America/Los_Angeles',115),
'LV':(36.0908,-115.1830,'America/Los_Angeles',2180),'MIA':(25.9580,-80.2389,'America/New_York',10),
'MIN':(44.9738,-93.2577,'America/Chicago',840),'NE':(42.0909,-71.2643,'America/New_York',290),
'NO':(29.9511,-90.0812,'America/Chicago',3),'NYG':(40.8135,-74.0745,'America/New_York',7),
'NYJ':(40.8135,-74.0745,'America/New_York',7),'PHI':(39.9008,-75.1675,'America/New_York',20),
'PIT':(40.4468,-80.0158,'America/New_York',730),'SEA':(47.5952,-122.3316,'America/Los_Angeles',20),
'SF':(37.4030,-121.9700,'America/Los_Angeles',40),'TB':(27.9759,-82.5033,'America/New_York',40),
'TEN':(36.1665,-86.7713,'America/Chicago',430),'WAS':(38.9076,-76.8645,'America/New_York',50)
}

def home_base(team, season):
    t=canon(team); season=int(season)
    if t=='LAC' and season<=2016: return (32.7832,-117.1225,'America/Los_Angeles',55)
    if t=='LA' and season<=2015: return (38.6327,-90.1886,'America/Chicago',465)
    if t=='LV' and season<=2019: return (37.7516,-122.2005,'America/Los_Angeles',25)
    if t=='SF' and season<=2013: return (37.7135,-122.3863,'America/Los_Angeles',20)
    return BASES.get(t,(np.nan,np.nan,None,np.nan))

NEUTRAL = [
    (re.compile(r'wembley|tottenham|twickenham',re.I),(51.5560,-0.2796,'Europe/London',115,'London')),
    (re.compile(r'azteca',re.I),(19.3029,-99.1505,'America/Mexico_City',7349,'Mexico City')),
    (re.compile(r'allianz arena',re.I),(48.2188,11.6247,'Europe/Berlin',1600,'Munich')),
    (re.compile(r'deutsche bank|waldstadion|commerzbank',re.I),(50.0686,8.6455,'Europe/Berlin',330,'Frankfurt')),
    (re.compile(r'corinthians|neo quimica',re.I),(-23.5453,-46.4741,'America/Sao_Paulo',2500,'Sao Paulo')),
    (re.compile(r'bernabeu|bernabéu',re.I),(40.4531,-3.6883,'Europe/Madrid',2188,'Madrid')),
    (re.compile(r'croke park',re.I),(53.3607,-6.2512,'Europe/Dublin',65,'Dublin')),
    (re.compile(r'rogers centre|sky dome|skydome',re.I),(43.6414,-79.3894,'America/Toronto',250,'Toronto')),
    (re.compile(r'levi',re.I),(37.4030,-121.9700,'America/Los_Angeles',40,'Santa Clara')),
    (re.compile(r'sofi',re.I),(33.9535,-118.3392,'America/Los_Angeles',115,'Los Angeles')),
    (re.compile(r'hard rock',re.I),(25.9580,-80.2389,'America/New_York',10,'Miami')),
    (re.compile(r'caesars superdome|superdome',re.I),(29.9511,-90.0812,'America/Chicago',3,'New Orleans')),
    (re.compile(r'allegiant',re.I),(36.0908,-115.1830,'America/Los_Angeles',2180,'Las Vegas')),
]

def venue_for(row):
    season=int(row.season); home=canon(row.home_team)
    loc=str(row.get('location','Home'))
    stadium=str(row.get('stadium',row.get('game_stadium','')) or '')
    if loc.lower()!='neutral':
        lat,lon,tz,alt=home_base(home,season)
        return lat,lon,tz,alt,home,1
    for pat,v in NEUTRAL:
        if pat.search(stadium): return (*v[:4],v[4],1)
    return np.nan,np.nan,None,np.nan,stadium or 'UNKNOWN_NEUTRAL',0

TEAM_NAMES={
'Arizona Cardinals':'ARI','Cardinals':'ARI','Atlanta Falcons':'ATL','Falcons':'ATL','Baltimore Ravens':'BAL','Ravens':'BAL',
'Buffalo Bills':'BUF','Bills':'BUF','Carolina Panthers':'CAR','Panthers':'CAR','Chicago Bears':'CHI','Bears':'CHI',
'Cincinnati Bengals':'CIN','Bengals':'CIN','Cleveland Browns':'CLE','Browns':'CLE','Dallas Cowboys':'DAL','Cowboys':'DAL',
'Denver Broncos':'DEN','Broncos':'DEN','Detroit Lions':'DET','Lions':'DET','Green Bay Packers':'GB','Packers':'GB',
'Houston Texans':'HOU','Texans':'HOU','Indianapolis Colts':'IND','Colts':'IND','Jacksonville Jaguars':'JAX','Jaguars':'JAX',
'Kansas City Chiefs':'KC','Chiefs':'KC','Los Angeles Chargers':'LAC','Chargers':'LAC','Los Angeles Rams':'LA','Rams':'LA',
'Las Vegas Raiders':'LV','Raiders':'LV','Miami Dolphins':'MIA','Dolphins':'MIA','Minnesota Vikings':'MIN','Vikings':'MIN',
'New England Patriots':'NE','Patriots':'NE','New Orleans Saints':'NO','Saints':'NO','New York Giants':'NYG','Giants':'NYG',
'New York Jets':'NYJ','Jets':'NYJ','Philadelphia Eagles':'PHI','Eagles':'PHI','Pittsburgh Steelers':'PIT','Steelers':'PIT',
'San Francisco 49ers':'SF','49ers':'SF','Seattle Seahawks':'SEA','Seahawks':'SEA','Tampa Bay Buccaneers':'TB','Buccaneers':'TB',
'Tennessee Titans':'TEN','Titans':'TEN','Washington Commanders':'WAS','Commanders':'WAS','Washington Football Team':'WAS'
}

def scrape_nfl_injuries(season, weeks):
    rows=[]; headers={'User-Agent':'Mozilla/5.0'}
    for week in weeks:
        url=f'https://www.nfl.com/injuries/league/{season}/reg{week}'
        try:
            r=requests.get(url,headers=headers,timeout=25)
            if r.status_code!=200: continue
            soup=BeautifulSoup(r.content,'html.parser')
            tables=soup.find_all('table',class_='d3-o-table')
            if not tables: continue
            for table in tables:
                link=table.find_previous('a',class_='nfl-c-matchup-strip__team-fullname')
                team_text=link.get_text(' ',strip=True) if link else ''
                team=TEAM_NAMES.get(team_text)
                if not team:
                    for k,v in TEAM_NAMES.items():
                        if k.lower()==team_text.lower() or team_text.lower().endswith(k.lower()): team=v; break
                if not team: continue
                tbody=table.find('tbody')
                if not tbody: continue
                for tr in tbody.find_all('tr'):
                    td=tr.find_all('td')
                    if len(td)<5: continue
                    name=td[0].get_text(' ',strip=True); pos=td[1].get_text(' ',strip=True)
                    injury=td[2].get_text(' ',strip=True); practice=td[3].get_text(' ',strip=True); status=td[4].get_text(' ',strip=True)
                    if name:
                        rows.append({'season':season,'game_type':'REG','team':team,'week':week,'position':pos,'full_name':name,
                                     'report_primary_injury':injury,'report_status':status,'practice_status':practice,'source':'NFL.com'})
            time.sleep(.12)
        except Exception:
            continue
    return pd.DataFrame(rows)

print('Loading schedules/base/context...')
if not SCHEDULES.exists(): raise FileNotFoundError(SCHEDULES)
if not BASE.exists(): raise FileNotFoundError(BASE)
sched=pd.read_parquet(SCHEDULES)
sched=sched[sched.game_type.astype(str).eq('REG')].copy()
sched['season']=nnum(sched.season).astype('Int64'); sched['week']=nnum(sched.week).astype('Int64')
sched['home_team']=sched.home_team.map(canon); sched['away_team']=sched.away_team.map(canon)
base=pd.read_csv(BASE,low_memory=False)
base['team']=base.team.map(canon); base['opponent']=base.opponent.map(canon)

# ---------- Travel/timezone/altitude ----------
print('Building travel/timezone/altitude features...')
venue=sched.apply(venue_for,axis=1,result_type='expand')
venue.columns=['venue_lat','venue_lon','venue_tz','venue_altitude_ft','venue_label','venue_known']
sched=pd.concat([sched.reset_index(drop=True),venue.reset_index(drop=True)],axis=1)
travel_rows=[]
for side in ['home','away']:
    for _,r in sched.iterrows():
        team=canon(r[f'{side}_team']); season=int(r.season)
        hlat,hlon,htz,halt=home_base(team,season)
        dist=haversine(hlat,hlon,r.venue_lat,r.venue_lon)
        goff=tz_offset_hours(htz,r.gameday) if htz else np.nan
        voff=tz_offset_hours(r.venue_tz,r.gameday) if r.venue_tz else np.nan
        shift=voff-goff if pd.notna(voff) and pd.notna(goff) else np.nan
        altchg=r.venue_altitude_ft-halt if pd.notna(r.venue_altitude_ft) and pd.notna(halt) else np.nan
        travel_rows.append({'game_id':r.game_id,'season':season,'week':int(r.week),'team':team,
            'travel_miles':dist,'tz_shift_hours':shift,'abs_tz_shift_hours':abs(shift) if pd.notna(shift) else np.nan,
            'eastward_tz_hours':max(shift,0) if pd.notna(shift) else np.nan,'westward_tz_hours':max(-shift,0) if pd.notna(shift) else np.nan,
            'venue_altitude_ft':r.venue_altitude_ft,'altitude_change_ft':altchg,
            'high_altitude_game':int(pd.notna(r.venue_altitude_ft) and r.venue_altitude_ft>=3000),
            'neutral_site':int(str(r.get('location','Home')).lower()=='neutral'),
            'international_game':int(r.venue_label in {'London','Mexico City','Munich','Frankfurt','Sao Paulo','Madrid','Dublin'}),
            'venue_known':int(r.venue_known)})
travel=pd.DataFrame(travel_rows).sort_values(['team','season','week','game_id'])
travel['road_game']=(travel.travel_miles>50).astype(int)
travel['consecutive_road_games']=0
for (season,team),idx in travel.groupby(['season','team']).groups.items():
    run=0
    for i in sorted(idx,key=lambda j:(travel.loc[j,'week'],travel.loc[j,'game_id'])):
        if travel.loc[i,'road_game']==1: run+=1
        else: run=0
        travel.loc[i,'consecutive_road_games']=run
travel.to_parquet(OUT/'travel_team_game_2006_2026.parquet',index=False)

# ---------- Injury/availability ----------
print('Building injury/player availability features...')
inj_parts=[]
if HIST_INJ.exists():
    h=pd.read_parquet(HIST_INJ)
    h=h[h.game_type.astype(str).eq('REG')].copy() if 'game_type' in h.columns else h
    h['source']='nflverse'
    inj_parts.append(h)
# nflverse injury source died after 2024; fill 2025 and current 2026 from official NFL.com pages.
for yr,maxw in [(2025,18),(2026,18)]:
    live=scrape_nfl_injuries(yr,range(1,maxw+1))
    if len(live): inj_parts.append(live)
inj=pd.concat(inj_parts,ignore_index=True,sort=False) if inj_parts else pd.DataFrame()
if len(inj):
    inj['season']=nnum(inj.season).astype('Int64'); inj['week']=nnum(inj.week).astype('Int64'); inj['team']=inj.team.map(canon)
    if 'full_name' not in inj.columns and 'player_name' in inj.columns: inj['full_name']=inj.player_name
    inj['name_key']=inj.full_name.map(norm_name)
    inj['position']=inj.position.fillna('').astype(str).str.upper()
    inj['report_status']=inj.get('report_status',pd.Series('',index=inj.index)).fillna('').astype(str)
    inj['practice_status']=inj.get('practice_status',pd.Series('',index=inj.index)).fillna('').astype(str)
    inj=inj.sort_values(['season','week','team','name_key']).drop_duplicates(['season','week','team','name_key'],keep='last')

# Strictly pregame player importance from prior snap participation.
importance=pd.DataFrame()
if SNAPS.exists() and len(inj):
    sn=pd.read_parquet(SNAPS)
    if 'game_type' in sn.columns: sn=sn[sn.game_type.astype(str).eq('REG')].copy()
    sn['team']=sn.team.map(canon); sn['season']=nnum(sn.season).astype('Int64'); sn['week']=nnum(sn.week).astype('Int64')
    sn['name_key']=sn.get('player',pd.Series('',index=sn.index)).map(norm_name)
    sn['offense_snaps']=nnum(sn.get('offense_snaps',0)).fillna(0); sn['defense_snaps']=nnum(sn.get('defense_snaps',0)).fillna(0)
    w=sn.groupby(['season','week','team','name_key'],as_index=False)[['offense_snaps','defense_snaps']].sum()
    teamw=w.groupby(['season','week','team'],as_index=False)[['offense_snaps','defense_snaps']].sum().rename(columns={'offense_snaps':'team_off_sum','defense_snaps':'team_def_sum'})
    w=w.merge(teamw,on=['season','week','team'],how='left')
    w['team_off_plays']=w.team_off_sum/11.0; w['team_def_plays']=w.team_def_sum/11.0
    w=w.sort_values(['season','team','name_key','week'])
    w['player_off_prior']=w.groupby(['season','team','name_key']).offense_snaps.cumsum()-w.offense_snaps
    w['player_def_prior']=w.groupby(['season','team','name_key']).defense_snaps.cumsum()-w.defense_snaps
    tw=w[['season','week','team','team_off_plays','team_def_plays']].drop_duplicates().sort_values(['season','team','week'])
    tw['team_off_prior']=tw.groupby(['season','team']).team_off_plays.cumsum()-tw.team_off_plays
    tw['team_def_prior']=tw.groupby(['season','team']).team_def_plays.cumsum()-tw.team_def_plays
    w=w.drop(columns=['team_off_plays','team_def_plays']).merge(tw[['season','week','team','team_off_prior','team_def_prior']],on=['season','week','team'],how='left')
    w['off_share_prior']=np.where(w.team_off_prior>0,w.player_off_prior/w.team_off_prior,np.nan)
    w['def_share_prior']=np.where(w.team_def_prior>0,w.player_def_prior/w.team_def_prior,np.nan)
    w['importance_prior']=w[['off_share_prior','def_share_prior']].max(axis=1).clip(0,1)
    importance=w[['season','week','team','name_key','importance_prior']].copy()
    # Previous-season fallback for week 1 / newly missing same-season history.
    seas=sn.groupby(['season','team','name_key'],as_index=False)[['offense_snaps','defense_snaps']].sum()
    ts=seas.groupby(['season','team'],as_index=False)[['offense_snaps','defense_snaps']].sum().rename(columns={'offense_snaps':'toff','defense_snaps':'tdef'})
    seas=seas.merge(ts,on=['season','team'],how='left')
    seas['prev_importance']=np.maximum(np.where(seas.toff>0,seas.offense_snaps/(seas.toff/11),np.nan),np.where(seas.tdef>0,seas.defense_snaps/(seas.tdef/11),np.nan))
    seas['season']=seas.season+1; seas['prev_importance']=seas.prev_importance.clip(0,1)
    inj=inj.merge(importance,on=['season','week','team','name_key'],how='left').merge(seas[['season','team','name_key','prev_importance']],on=['season','team','name_key'],how='left')
    inj['player_importance']=inj.importance_prior.fillna(inj.prev_importance)
else:
    inj['player_importance']=np.nan

if len(inj):
    pos=inj.position.fillna('').astype(str).str.upper()
    inj.loc[inj.player_importance.isna() & pos.isin(['K','P']),'player_importance']=0.45
    inj['player_importance']=inj.player_importance.fillna(0.35).clip(0,1)
    st=inj.report_status.str.lower(); pr=inj.practice_status.str.lower()
    inj['is_out']=st.eq('out').astype(int); inj['is_doubtful']=st.eq('doubtful').astype(int); inj['is_questionable']=st.eq('questionable').astype(int)
    inj['is_dnp']=pr.str.contains('did not participate',na=False).astype(int); inj['is_limited']=pr.str.contains('limited',na=False).astype(int)
    inj['status_weight']=np.select([st.eq('out'),st.eq('doubtful'),st.eq('questionable')],[1.0,.75,.35],default=np.where(inj.is_dnp.eq(1),.15,0.0))
    inj['unavailable_equiv']=inj.status_weight*inj.player_importance
    inj['out_equiv']=inj.is_out*inj.player_importance
    groups={
      'qb':pos.eq('QB'),'ol':pos.isin(['C','G','OG','LG','RG','T','OT','LT','RT','OL']),
      'skill':pos.isin(['RB','FB','WR','TE']),'front7':pos.isin(['DE','DT','DL','NT','LB','OLB','ILB','EDGE']),
      'secondary':pos.isin(['CB','DB','S','FS','SS'])}
    for k,m in groups.items(): inj[f'{k}_unavail_equiv']=inj.unavailable_equiv*np.asarray(m,dtype=float)
    inj['star_out']=((inj.is_out==1)&(inj.player_importance>=.65)).astype(int)
    agg={'injury_listed':('name_key','count'),'out_players':('is_out','sum'),'doubtful_players':('is_doubtful','sum'),'questionable_players':('is_questionable','sum'),
         'dnp_players':('is_dnp','sum'),'limited_players':('is_limited','sum'),'unavailable_equiv':('unavailable_equiv','sum'),'out_equiv':('out_equiv','sum'),'star_outs':('star_out','sum')}
    for k in groups: agg[f'{k}_unavail_equiv']=(f'{k}_unavail_equiv','sum')
    ia=inj.groupby(['season','week','team'],as_index=False).agg(**agg)
    inj.to_parquet(OUT/'injury_player_week_2009_2026.parquet',index=False)
    ia.to_parquet(OUT/'injury_team_week_2009_2026.parquet',index=False)
else:
    ia=pd.DataFrame(columns=['season','week','team'])

# ---------- Merge all pregame feature families ----------
print('Merging complete pregame feature set...')
enr=base.merge(travel,on=['game_id','season','week','team'],how='left',suffixes=('','_travel'))
if len(ia): enr=enr.merge(ia,on=['season','week','team'],how='left')
if CONTEXT.exists():
    ctx=pd.read_parquet(CONTEXT); ctx['team']=ctx.team.map(canon)
    # Add non-duplicate context columns only.
    keys=['game_id','season','week','team']; extra=[c for c in ctx.columns if c not in enr.columns and c not in ['team_canon','opponent_canon','gameday_dt']]
    enr=enr.merge(ctx[keys+extra],on=keys,how='left')

# Injury zero means no player listed only when that season/week is covered by an injury source.
covered=set(zip(inj.season.astype(int),inj.week.astype(int))) if len(inj) else set()
enr['injury_data_available']=[int((int(s),int(w)) in covered) for s,w in zip(enr.season,enr.week)]
for c in ['injury_listed','out_players','doubtful_players','questionable_players','dnp_players','limited_players','unavailable_equiv','out_equiv','star_outs','qb_unavail_equiv','ol_unavail_equiv','skill_unavail_equiv','front7_unavail_equiv','secondary_unavail_equiv']:
    if c in enr.columns: enr.loc[(enr.injury_data_available==1)&enr[c].isna(),c]=0

# opponent versions + intuitive advantages
new_bad=['injury_listed','out_players','doubtful_players','questionable_players','unavailable_equiv','out_equiv','star_outs','qb_unavail_equiv','ol_unavail_equiv','skill_unavail_equiv','front7_unavail_equiv','secondary_unavail_equiv','travel_miles','abs_tz_shift_hours','eastward_tz_hours','westward_tz_hours','altitude_change_ft','consecutive_road_games']
new_good=['returning_offense_snap_share','returning_defense_snap_share','returning_ol_snap_share','returning_skill_snap_share']
for c in [x for x in new_bad+new_good if x in enr.columns]:
    opp=enr[['game_id','team',c]].rename(columns={'team':'opponent',c:'opp_'+c})
    enr=enr.merge(opp,on=['game_id','opponent'],how='left')
    if c in new_bad: enr['adv_'+c]=nnum(enr['opp_'+c])-nnum(enr[c])
    else: enr['adv_'+c]=nnum(enr[c])-nnum(enr['opp_'+c])

enr.to_parquet(OUT/'complete_pregame_team_sides.parquet',index=False)

# ---------- Honest chronological method mining ----------
print('Mining positive-ROI interpretable moneyline methods...')
d=enr.copy()
d=d[(d.completed==1) & d.win.notna() & d.moneyline.notna()].copy() if 'completed' in d.columns else d[d.win.notna() & d.moneyline.notna()].copy()
d=d[d.game_type.astype(str).eq('REG')].copy() if 'game_type' in d.columns else d
# avoid earliest weeks where rolling team form is immature
if 'prior_games' in d.columns: d=d[nnum(d.prior_games)>=3].copy()
d['moneyline']=nnum(d.moneyline); d['win']=nnum(d.win)
d=d[d.win.isin([0,1])].copy()
d['profit_units']=np.where(d.win.eq(1),np.where(d.moneyline>0,d.moneyline/100,100/d.moneyline.abs()),-1.0)
d['market_prob_calc']=np.where(d.moneyline>0,100/(d.moneyline+100),d.moneyline.abs()/(d.moneyline.abs()+100))

exclude_tokens=['win','profit','completed','push','score','result','points_for','points_against','market_prob_calc']
feature_cols=[]
preferred_prefix=('rank_edge_','recent_edge_','adv_')
for c in d.columns:
    if c in ['market_prob','elo_edge','rest_edge','week','moneyline','prior_games','qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','neutral_site','international_game','high_altitude_game'] or c.startswith(preferred_prefix):
        if any(tok in c.lower() for tok in exclude_tokens): continue
        z=nnum(d[c]);
        if z.notna().sum()>=150 and z.nunique(dropna=True)>=2: feature_cols.append(c)
# keep discovery tractable and interpretable
feature_cols=list(dict.fromkeys(feature_cols))

price_bands=[('ALL',0,1),('DOG35_49',.35,.4999),('PK_55',.50,.55),('FAV55_65',.55,.65),('FAV65_75',.65,.75),('FAV75_85',.75,.85),('FAV85_95',.85,.95)]
venue_bands=[('ANY',None),('HOME',1),('AWAY',0)]

def metrics(x):
    if len(x)==0: return None
    by=x.groupby('season').profit_units.agg(['count','sum'])
    elig=by[by['count']>=3]
    return {'n':len(x),'wins':int(x.win.sum()),'win_rate':float(x.win.mean()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum()),
            'active_seasons':int(len(elig)),'positive_seasons':int((elig['sum']>0).sum()),'negative_seasons':int((elig['sum']<0).sum())}

def splits(track):
    if track=='COMPLETE': return [(2012,2018),(2019,2022),(2023,2025)]
    return [(2006,2014),(2015,2019),(2020,2025)]

def eval_mask(mask,track,meta):
    sub=d[mask].copy(); sp=splits(track); vals=[]
    for lo,hi in sp: vals.append(metrics(sub[sub.season.between(lo,hi)]))
    full=metrics(sub)
    if not full or any(v is None for v in vals): return None
    mins=[25,15,15] if track=='COMPLETE' else [35,20,25]
    if any(v['n']<m for v,m in zip(vals,mins)): return None
    if any(v['roi']<=0 for v in vals): return None
    if full['n']<60 or full['roi']<=0: return None
    if full['active_seasons']>=6 and full['negative_seasons']/max(1,full['active_seasons'])>.35: return None
    out={**meta,**{f'full_{k}':v for k,v in full.items()}}
    for name,v in zip(['train','validation','holdout'],vals): out.update({f'{name}_{k}':vv for k,vv in v.items()})
    return out

rules=[]; univ=[]
for track in ['COMPLETE','LONG']:
    dt=d[d.season.between(2012,2025)].copy() if track=='COMPLETE' else d[d.season.between(2006,2025)].copy()
    # COMPLETE requires injury report coverage for candidate injury features, but non-injury rules can still use all rows in era.
    train_lo,train_hi=splits(track)[0]
    tr=dt[dt.season.between(train_lo,train_hi)]
    for c in feature_cols:
        if track=='LONG' and ('injur' in c or 'unavail' in c or 'out_' in c or 'star_out' in c): continue
        vals=nnum(tr[c]).dropna()
        if len(vals)<100: continue
        qs=sorted(set(float(x) for x in vals.quantile([.15,.25,.35,.50,.65,.75,.85]).dropna()))
        for q in qs:
            for op in ['>=','<=']:
                fc=nnum(dt[c]); fmask=(fc>=q) if op=='>=' else (fc<=q)
                for pb,plo,phi in price_bands:
                    mp=nnum(dt.get('market_prob',dt.market_prob_calc)); pm=mp.between(plo,phi,inclusive='both')
                    for vb,h in venue_bands:
                        vm=pd.Series(True,index=dt.index) if h is None else (nnum(dt.is_home)==h)
                        local=fmask & pm & vm
                        mask=pd.Series(False,index=d.index); mask.loc[dt.index]=local
                        r=eval_mask(mask,track,{'track':track,'family':'UNIVARIATE','feature1':c,'op1':op,'threshold1':q,'price_band':pb,'venue':vb})
                        if r:
                            rules.append(r); univ.append((r['train_roi']*np.sqrt(r['train_n']),track,c,op,q,pb,vb,mask))

# Pair promising independently selected rules; chronological holdout requirements remain unchanged.
univ=sorted(univ,key=lambda x:x[0],reverse=True)[:160]
for i,a in enumerate(univ):
    for b in univ[i+1:i+80]:
        if a[1]!=b[1] or a[2]==b[2] or a[5]!=b[5] or a[6]!=b[6]: continue
        mask=a[7]&b[7]
        r=eval_mask(mask,a[1],{'track':a[1],'family':'PAIR','feature1':a[2],'op1':a[3],'threshold1':a[4],
             'feature2':b[2],'op2':b[3],'threshold2':b[4],'price_band':a[5],'venue':a[6]})
        if r: rules.append(r)

res=pd.DataFrame(rules)
if len(res):
    res['score']=res.holdout_roi*np.sqrt(res.holdout_n)+.35*res.validation_roi*np.sqrt(res.validation_n)
    res=res.sort_values(['score','full_n'],ascending=[False,False]).drop_duplicates(['track','family','feature1','op1','threshold1','feature2','op2','threshold2','price_band','venue'] if 'feature2' in res.columns else None)
    res.to_csv(OUT/'positive_roi_method_candidates.csv',index=False)
    top=res.head(40)
else:
    top=pd.DataFrame(); res.to_csv(OUT/'positive_roi_method_candidates.csv',index=False)

coverage={
 'base_rows':int(len(base)), 'travel_rows':int(len(travel)), 'travel_known_pct':float(100*travel.venue_known.mean()) if len(travel) else 0,
 'injury_player_rows':int(len(inj)), 'injury_team_weeks':int(len(ia)), 'injury_seasons':sorted([int(x) for x in inj.season.dropna().unique()]) if len(inj) else [],
 'injury_2025_rows':int((inj.season==2025).sum()) if len(inj) else 0, 'injury_2026_rows':int((inj.season==2026).sum()) if len(inj) else 0,
 'complete_rows':int(len(enr)), 'candidate_features':len(feature_cols), 'qualified_methods':int(len(res))
}
(OUT/'coverage.json').write_text(json.dumps(coverage,indent=2))

report=['NFL INJURY + TRAVEL + COMPLETE FEATURE METHOD MINING','',json.dumps(coverage,indent=2),'','TOP POSITIVE-ROI METHODS']
if len(top):
    for _,r in top.iterrows():
        f1=f"{r.feature1} {r.op1} {r.threshold1:.5g}"; f2=''
        if pd.notna(r.get('feature2',np.nan)): f2=f" AND {r.feature2} {r.op2} {r.threshold2:.5g}"
        report.append(f"{r.track} | {r.price_band} {r.venue} | {f1}{f2} | n={int(r.full_n)} W={int(r.full_wins)} WR={100*r.full_win_rate:.1f}% ROI={100*r.full_roi:+.1f}% | train {100*r.train_roi:+.1f}% n={int(r.train_n)} | val {100*r.validation_roi:+.1f}% n={int(r.validation_n)} | holdout {100*r.holdout_roi:+.1f}% n={int(r.holdout_n)} | neg seasons {int(r.full_negative_seasons)}/{int(r.full_active_seasons)}")
else: report.append('No rules survived all chronological ROI/sample/season-consistency gates.')
(OUT/'report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report))
