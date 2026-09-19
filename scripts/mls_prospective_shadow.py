#!/usr/bin/env python3
from __future__ import annotations
import json, math, urllib.request
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
MARKET=ROOT/'live/market_snapshots/latest.json'
OUT=ROOT/'live/shadow';OUT.mkdir(parents=True,exist_ok=True)
ELO_TEAMS='https://raw.githubusercontent.com/philo92/mls-elo/main/teams.csv'
UA='Appwiza-MLS-Shadow/1.0'

METHODS=[
 {'id':'MLS-R01','name':'Home xG Surge vs Market/Elo','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('edge_last3_xgfpg','>=',.45538),('elo_edge','<=',.91)],'requires_elo':True},
 {'id':'MLS-R02','name':'Home xG Surge + Defensive Instability','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('edge_last3_xgfpg','>=',.45538),('edge_last3_xgapg','>=',.0536)],'requires_elo':False},
 {'id':'MLS-R03','name':'Away Elo Underdog + xGA Veto','side':'AWAY','prob_lo':.25,'prob_hi':.35,
  'rules':[('elo_edge','<=',-41.31),('edge_last5_xgapg','<',.01113)],'requires_elo':True},
]
HISTORY={
 'MLS-R01':{'bets':175,'wins':96,'losses':79,'roi':.18737142857142852,'holdout_roi':.27125,'holdout_n':56},
 'MLS-R02':{'bets':144,'wins':84,'losses':60,'roi':.2579166666666667,'holdout_roi':.16886792452830188,'holdout_n':53},
 'MLS-R03':{'bets':109,'wins':46,'losses':63,'roi':.450,'holdout_roi':.459,'holdout_n':37},
}
def now():return datetime.now(timezone.utc)
def fetch_elo():
    req=urllib.request.Request(ELO_TEAMS,headers={'User-Agent':UA})
    raw=urllib.request.urlopen(req,timeout=30).read()
    p=OUT/'elo_teams_latest.csv';p.write_bytes(raw)
    d=pd.read_csv(p)
    d['team']=d['team'].map(canon_team);d['elo']=pd.to_numeric(d.elo,errors='coerce')
    d['last_updated_date']=pd.to_datetime(d.last_updated_date,errors='coerce',utc=True)
    last=d.last_updated_date.max()
    age_days=(pd.Timestamp(now())-last).total_seconds()/86400 if pd.notna(last) else 999
    mp={r.team:float(r.elo) for _,r in d[d.elo.notna()].iterrows()}
    return mp,None if pd.isna(last) else last.isoformat(),age_days
def asa_data():
    asa=AmericanSoccerAnalysis()
    games=asa.get_games(leagues='mls',season_name='2026')
    xg=asa.get_game_xgoals(leagues='mls',season_name='2026')
    teams=asa.get_teams(leagues='mls')
    for name,z in [('games',games),('xg',xg),('teams',teams)]:
        if not isinstance(z,pd.DataFrame):locals()[name]=pd.DataFrame(z)
    games=games if isinstance(games,pd.DataFrame) else pd.DataFrame(games)
    xg=xg if isinstance(xg,pd.DataFrame) else pd.DataFrame(xg)
    teams=teams if isinstance(teams,pd.DataFrame) else pd.DataFrame(teams)
    tid=next((c for c in ['team_id','id'] if c in teams.columns),None)
    tname=next((c for c in ['team_name','name'] if c in teams.columns),None)
    if not tid or not tname:raise RuntimeError('ASA team schema unavailable')
    mp={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}
    for z in [games,xg]:
        z['home_team']=z.home_team_id.astype(str).map(mp)
        z['away_team']=z.away_team_id.astype(str).map(mp)
        z['dt']=pd.to_datetime(z.date_time_utc,errors='coerce',utc=True)
    return games,xg
def rolling_xg_before(xg,kickoff,home,away):
    h=defaultdict(lambda:deque(maxlen=20))
    z=xg[xg.dt.lt(kickoff)].sort_values('dt')
    for _,r in z.iterrows():
        if pd.isna(r.get('home_team_xgoals')) or pd.isna(r.get('away_team_xgoals')):continue
        ht,at=r.home_team,r.away_team
        if not ht or not at:continue
        hx=float(r.home_team_xgoals);ax=float(r.away_team_xgoals)
        h[ht].append((hx,ax));h[at].append((ax,hx))
    def vals(team,n):
        v=list(h[team])[-n:]
        if len(v)<n:return None
        return {'xgf':sum(a for a,b in v)/len(v),'xga':sum(b for a,b in v)/len(v)}
    out={}
    for n in [3,5]:
        hv=vals(home,n);av=vals(away,n)
        if hv and av:
            out[f'edge_last{n}_xgfpg']=hv['xgf']-av['xgf']
            out[f'edge_last{n}_xgapg']=hv['xga']-av['xga']
            out[f'home_last{n}_xgfpg']=hv['xgf'];out[f'away_last{n}_xgfpg']=av['xgf']
            out[f'home_last{n}_xgapg']=hv['xga'];out[f'away_last{n}_xgapg']=av['xga']
    return out
def pass_rule(v,op,t):
    if v is None or pd.isna(v):return False
    if op=='>=':return v>=t
    if op=='<=':return v<=t
    if op=='<':return v<t
    if op=='>':return v>t
    return False
def main():
    if not MARKET.exists():raise RuntimeError('No MLS market snapshot')
    market=json.loads(MARKET.read_text())
    events=market.get('events') or []
    games,xg=asa_data()
    elo,elo_updated,elo_age=fetch_elo()
    captured=pd.Timestamp((market.get('summary') or {}).get('captured_at'),tz='UTC')
    if pd.isna(captured):raise RuntimeError('Market capture timestamp missing')
    rows=[]
    for ev in events:
        kickoff=pd.Timestamp(ev['commence_time'])
        if kickoff.tzinfo is None:kickoff=kickoff.tz_localize('UTC')
        if kickoff<=pd.Timestamp(now()):continue
        home=canon_team(ev['home_team']);away=canon_team(ev['away_team'])
        fx=rolling_xg_before(xg,kickoff,home,away)
        if home in elo and away in elo:fx['elo_edge']=elo[home]-elo[away]
        for m in METHODS:
            prob=ev['home_novig_prob'] if m['side']=='HOME' else ev['away_novig_prob']
            selection=home if m['side']=='HOME' else away
            opponent=away if m['side']=='HOME' else home
            available=True;reasons=[];stale_required_input=False
            if not (m['prob_lo']<=prob<=m['prob_hi']):
                available=False;reasons.append(f"market_prob {prob:.3f} outside {m['prob_lo']:.2f}-{m['prob_hi']:.2f}")
            if m['requires_elo'] and elo_age>3:
                available=False;stale_required_input=True;reasons.append(f'Elo stale: {elo_age:.1f} days old')
            checks=[]
            for feat,op,t in m['rules']:
                v=fx.get(feat)
                ok=pass_rule(v,op,t)
                checks.append({'feature':feat,'op':op,'threshold':t,'value':v,'pass':ok})
                if not ok:available=False
            rows.append({
              'captured_at':market['summary']['captured_at'],'event_id':ev['event_id'],'commence_time':ev['commence_time'],
              'home_team':home,'away_team':away,'selection':selection,'opponent':opponent,'side':m['side'],
              'method_id':m['id'],'method_name':m['name'],'status':'QUALIFIES_SHADOW' if available else ('BLOCKED_STALE_INPUT' if stale_required_input else 'NO_MATCH'),
              'market_prob':prob,'american_price':ev['home_american'] if m['side']=='HOME' else ev['away_american'],
              'decimal_price':ev['home_odds'] if m['side']=='HOME' else ev['away_odds'],'book':ev['provider'],
              'checks':checks,'blocking_reasons':reasons,'features':fx,'history':HISTORY[m['id']],
              'elo_source_updated_at':elo_updated,'elo_age_days':elo_age,'market_source':ev['source']
            })
    payload={'built_at':now().isoformat(),'market_captured_at':market['summary']['captured_at'],
             'elo_updated_at':elo_updated,'elo_age_days':elo_age,
             'methods':{m['id']:{'name':m['name'],'status':'SHADOW_READY'} for m in METHODS},
             'rows':rows,'qualifiers':[r for r in rows if r['status']=='QUALIFIES_SHADOW'],
             'status_counts':{m['id']:{s:sum(1 for r in rows if r['method_id']==m['id'] and r['status']==s) for s in ['QUALIFIES_SHADOW','NO_MATCH','BLOCKED_STALE_INPUT']} for m in METHODS}}
    (OUT/'current_shadow_board.json').write_text(json.dumps(payload,indent=2,default=str))
    with (OUT/'shadow_observations.jsonl').open('a') as f:
        f.write(json.dumps(payload,default=str,separators=(',',':'))+'\n')
    print(json.dumps({'built_at':payload['built_at'],'market_captured_at':payload['market_captured_at'],
                      'elo_updated_at':elo_updated,'elo_age_days':elo_age,'events':len(events),
                      'method_checks':len(rows),'qualifiers':len(payload['qualifiers']),'status_counts':payload['status_counts'],
                      'qualifying':[(r['method_id'],r['selection'],r['opponent'],r['american_price']) for r in payload['qualifiers']]},indent=2))
if __name__=='__main__':main()
