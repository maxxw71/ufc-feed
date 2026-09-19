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
ELO_CURRENT=ROOT/'live/current/elo_current.json'
UA='Appwiza-MLS-Shadow/1.0'

METHODS=[
 {'id':'MLS-P01','name':'Draw — Near-Equal Starting XI Continuity','side':'DRAW','prob_lo':0.0,'prob_hi':1.0,
  'rules':[('balance_player_starter_proxy_continuity','<=',.049450549450549386)],'requires_elo':False,'requires_player_continuity':True},
 {'id':'MLS-R01','name':'Home xG Surge vs Market/Elo','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('edge_last3_xgfpg','>=',.45538),('elo_edge','<=',.91)],'requires_elo':True,'requires_player_continuity':False},
 {'id':'MLS-R02','name':'Home xG Surge + Defensive Instability','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('edge_last3_xgfpg','>=',.45538),('edge_last3_xgapg','>=',.0536)],'requires_elo':False,'requires_player_continuity':False},
 {'id':'MLS-R03','name':'Away Elo Underdog + xGA Veto','side':'AWAY','prob_lo':.25,'prob_hi':.35,
  'rules':[('elo_edge','<=',-41.31),('edge_last5_xgapg','<',.01113)],'requires_elo':True,'requires_player_continuity':False},
]
HISTORY={
 'MLS-P01':{'bets':805,'wins':237,'losses':568,'roi':.15090683229813665,'holdout_roi':.04317391304347824,'holdout_n':230,
            'status':'PROSPECTIVE_PRIORITY_SHADOW','bootstrap95_roi':[.02637919254658385,.2717211180124224]},
 'MLS-R01':{'bets':175,'wins':96,'losses':79,'roi':.18737142857142852,'holdout_roi':.27125,'holdout_n':56},
 'MLS-R02':{'bets':144,'wins':84,'losses':60,'roi':.2579166666666667,'holdout_roi':.16886792452830188,'holdout_n':53},
 'MLS-R03':{'bets':109,'wins':46,'losses':63,'roi':.450,'holdout_roi':.459,'holdout_n':37},
}
def now():return datetime.now(timezone.utc)
def fetch_elo():
    if not ELO_CURRENT.exists():
        raise RuntimeError('Fresh MLS Elo bridge missing')
    d=json.loads(ELO_CURRENT.read_text())
    mp={canon_team(k):float(v) for k,v in (d.get('ratings') or {}).items()}
    last=pd.to_datetime(d.get('bridged_through'),errors='coerce',utc=True)
    age_days=(pd.Timestamp(now())-last).total_seconds()/86400 if pd.notna(last) else 999
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
def load_player_game_window(games):
    asa=AmericanSoccerAnalysis()
    today=pd.Timestamp(now())
    end=today.date()
    start=(today-pd.Timedelta(days=120)).date()
    px=asa.get_player_xgoals(
        leagues='mls',
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        split_by_games=True,
    )
    teams=asa.get_teams(leagues='mls')
    if not isinstance(px,pd.DataFrame):px=pd.DataFrame(px)
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    if px.empty:return px
    tid=next((x for x in ['team_id','id'] if x in teams.columns),None)
    tname=next((x for x in ['team_name','name'] if x in teams.columns),None)
    if not tid or not tname:return pd.DataFrame()
    mp={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}
    gm=games[['game_id','date_time_utc']].copy()
    gm['game_id']=gm.game_id.astype(str)
    gm['dt']=pd.to_datetime(gm.date_time_utc,errors='coerce',utc=True)
    dtmap=dict(zip(gm.game_id,gm.dt))
    px['game_id']=px.game_id.astype(str)
    px['team']=px.team_id.astype(str).map(mp)
    px['dt']=px.game_id.map(dtmap)
    px['minutes_played']=pd.to_numeric(px.minutes_played,errors='coerce').fillna(0)
    return px[px.dt.notna()&px.team.notna()].copy()

def player_continuity_features(px,kickoff,home,away):
    if px is None or px.empty:return {}
    z=px[px.dt.lt(kickoff)&px.team.isin([home,away])].copy()
    out={};vals={}
    for team in [home,away]:
        team_rows=z[z.team.eq(team)]
        game_rows=[]
        for (dt,gid),g in team_rows.groupby(['dt','game_id']):
            starters=set(g.loc[g.minutes_played.ge(45),'player_id'].astype(str))
            game_rows.append((dt,gid,starters))
        game_rows=sorted(game_rows,key=lambda x:x[0])
        if len(game_rows)<2:
            vals[team]=None
            continue
        a=game_rows[-1][2];b=game_rows[-2][2]
        vals[team]=len(a&b)/max(1,len(a|b))
    if vals.get(home) is not None and vals.get(away) is not None:
        out['home_player_starter_proxy_continuity']=vals[home]
        out['away_player_starter_proxy_continuity']=vals[away]
        out['balance_player_starter_proxy_continuity']=abs(vals[home]-vals[away])
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
    player_window=load_player_game_window(games)
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
        continuity_loaded=False
        for m in METHODS:
            if m.get('requires_player_continuity') and not continuity_loaded:
                fx.update(player_continuity_features(player_window,kickoff,home,away))
                continuity_loaded=True
            if m['side']=='HOME':
                prob=ev['home_novig_prob'];selection=home;opponent=away
            elif m['side']=='AWAY':
                prob=ev['away_novig_prob'];selection=away;opponent=home
            else:
                prob=ev['draw_novig_prob'];selection='DRAW';opponent=f'{home} vs {away}'
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
              'market_prob':prob,
              'american_price':ev['home_american'] if m['side']=='HOME' else ev['away_american'] if m['side']=='AWAY' else ev['draw_american'],
              'decimal_price':ev['home_odds'] if m['side']=='HOME' else ev['away_odds'] if m['side']=='AWAY' else ev['draw_odds'],
              'book':ev['provider'],
              'checks':checks,'blocking_reasons':reasons,'features':fx,'history':HISTORY[m['id']],
              'elo_source_updated_at':elo_updated,'elo_age_days':elo_age,'market_source':ev['source']
            })
    payload={'built_at':now().isoformat(),'market_captured_at':market['summary']['captured_at'],
             'elo_updated_at':elo_updated,'elo_age_days':elo_age,
             'methods':{m['id']:{'name':m['name'],'status':'PROSPECTIVE_PRIORITY_SHADOW' if m['id']=='MLS-P01' else 'SHADOW_READY'} for m in METHODS},
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
