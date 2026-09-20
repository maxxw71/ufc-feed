#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
MARKET=ROOT/'live/market_snapshots/latest.json'
OUT=ROOT/'live/shadow';OUT.mkdir(parents=True,exist_ok=True)
ELO_CURRENT=ROOT/'live/current/elo_current.json'

ACTIVE_METHOD_IDS={'MLS-R01V2','MLS-R02V2','MLS-R03','MLS-A02','MLS-A03','MLS-A05','MLS-A06'}
WATCH_ONLY_METHOD_IDS={'MLS-A04'}

# Active prospective methods only. Historical IDs are versioned rather than
# silently changing rule definitions in the immutable forward ledger.
METHODS=[
 {'id':'MLS-R01V2','name':'Home xG Surge vs Market/Elo + Opponent GD Veto','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('home_edge_last3_xgfpg','>=',.45538),('home_elo_edge','<=',.91),('away_last10_gdpg','<',.6)],
  'requires_elo':True,'research_status':'SHADOW_READY_REFINED'},
 {'id':'MLS-R02V2','name':'Home Strong xG Surge + Defensive Instability','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('home_edge_last3_xgfpg','>=',.5782850000000002),('home_edge_last3_xgapg','>=',.0536)],
  'requires_elo':False,'research_status':'SHADOW_READY_REFINED'},
 {'id':'MLS-R03','name':'Away Elo Underdog + xGA Veto','side':'AWAY','prob_lo':.25,'prob_hi':.35,
  'rules':[('away_elo_edge','<=',-41.31),('away_edge_last5_xgapg','<',.01113)],
  'requires_elo':True,'research_status':'SHADOW_READY'},

 {'id':'MLS-A02','name':'Home Favorite — GK Rebound + Form Floor','side':'HOME','prob_lo':.50,'prob_hi':.60,
  'rules':[('home_edge_gk_save_pct5','<=',-0.10148378191856453),('home_last10_ppg','>',1.1)],
  'requires_elo':False,'research_status':'PROSPECTIVE_PRIORITY_SHADOW'},
 {'id':'MLS-A03','name':'Away Underdog — Contrarian PPG + xGD','side':'AWAY','prob_lo':.25,'prob_hi':.35,
  'rules':[('away_edge_last10_ppg','<=',-0.3999999999999999),('away_edge_last10_xgdpg','<',-0.13458100000000023)],
  'requires_elo':False,'research_status':'PROSPECTIVE_PRIORITY_SHADOW'},
 {'id':'MLS-A04','name':'Home xGD Lag + Stable Roster vs Older Opponent','side':'HOME','prob_lo':.40,'prob_hi':.50,
  'rules':[('home_edge_last5_xgdpg','<=',-0.04208999999999996),
           ('home_roster_new_players3','<=',0.0),
           ('away_player_weighted_age5','>',28.068592458747545)],
  'requires_elo':False,'research_status':'SHADOW_WATCH'},
 {'id':'MLS-A05','name':'Away Underdog — Low-Scoring Referee','side':'AWAY','prob_lo':.25,'prob_hi':.35,
  'rules':[('referee_prior_over25_rate','<=',0.5348837209302325)],
  'enhanced_rules':[('home_roster_new_players3','>',2.0)],
  'requires_elo':False,'requires_referee':True,'research_status':'PROSPECTIVE_PRIORITY_SHADOW'},
 {'id':'MLS-A06','name':'Deep Away Longshot — Stable Opponent + xGD Fade','side':'AWAY','prob_lo':.20,'prob_hi':.30,
  'rules':[('home_roster_new_players3','<=',0.0),('away_edge_last10_xgdpg','<',-0.2933600000000003)],
  'requires_elo':False,'research_status':'PROSPECTIVE_PRIORITY_SHADOW'},
]

HISTORY={
 'MLS-R01V2':{'bets':137,'wins':82,'losses':55,'roi':.29270072992700724,'holdout_roi':.36431818181818176,'holdout_n':44,
               'positive_seasons':'11/13','status':'SHADOW_READY_REFINED'},
 'MLS-R02V2':{'bets':102,'wins':64,'losses':38,'roi':.3484313725490196,'holdout_roi':.19146341463414632,'holdout_n':41,
               'active_positive_seasons':'11/11','status':'SHADOW_READY_REFINED'},
 'MLS-R03':{'bets':109,'wins':46,'losses':63,'roi':.45045871559633033,'holdout_roi':.4594594594594595,'holdout_n':37},
 'MLS-A02':{'bets':208,'wins':147,'losses':61,'roi':.25466346153846153,'holdout_roi':.15181818181818182,'holdout_n':66,
            'positive_seasons':'12/13','bootstrap95_roi':[.14567307692307693,.36409134615384603],
            'perturbation_positive':'13/13','status':'PROSPECTIVE_PRIORITY_SHADOW'},
 'MLS-A03':{'bets':89,'wins':43,'losses':46,'roi':.6625842696629214,'holdout_roi':.6585714285714286,'holdout_n':35,
            'active_positive_seasons':'9/9','perturbation_positive':'13/13','status':'PROSPECTIVE_PRIORITY_SHADOW'},
 'MLS-A04':{'bets':79,'wins':54,'losses':25,'roi':.4834177215189874,'holdout_roi':.24285714285714283,'holdout_n':21,
            'active_positive_seasons':'9/9','bootstrap95_roi':[.2588575949367089,.6940537974683545],
            'perturbation_positive':'15/15','status':'SHADOW_WATCH','note':'Robust but sample-thin; do not promote.'},
 'MLS-A05':{'bets':588,'wins':204,'losses':384,'roi':.14846938775510207,'holdout_roi':.23952380952380947,'holdout_n':105,
            'positive_seasons':'10/13','bootstrap95_roi':[.02141071428571429,.275862670068027],
            'status':'PROSPECTIVE_PRIORITY_SHADOW',
            'enhanced_subset':{'rule':'opponent roster new players3 > 2','bets':144,'wins':68,'losses':76,
                               'roi':.5485416666666667,'holdout_roi':.8320588235294117,'holdout_n':34,
                               'bootstrap95_roi':[.2867204861111111,.8220954861111108],'perturbation_positive':'10/11'}},
 'MLS-A06':{'bets':143,'wins':53,'losses':90,'roi':.54,'holdout_roi':.6527777777777778,'holdout_n':54,
            'positive_seasons':'10/13','bootstrap95_roi':[.19499131944444442,.8650017361111112],
            'perturbation_positive':'10/10','status':'PROSPECTIVE_PRIORITY_SHADOW'},
}

def now():return datetime.now(timezone.utc)

def fetch_elo():
    if not ELO_CURRENT.exists():raise RuntimeError('Fresh MLS Elo bridge missing')
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
    if not isinstance(games,pd.DataFrame):games=pd.DataFrame(games)
    if not isinstance(xg,pd.DataFrame):xg=pd.DataFrame(xg)
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    tid=next((c for c in ['team_id','id'] if c in teams.columns),None)
    tname=next((c for c in ['team_name','name'] if c in teams.columns),None)
    if not tid or not tname:raise RuntimeError('ASA team schema unavailable')
    mp={str(r[tid]):canon_team(r[tname]) for _,r in teams.iterrows()}
    for z in [games,xg]:
        z['home_team']=z.home_team_id.astype(str).map(mp)
        z['away_team']=z.away_team_id.astype(str).map(mp)
        z['dt']=pd.to_datetime(z.date_time_utc,errors='coerce',utc=True)
    return asa,games,xg,teams,mp

def rolling_team_features(games,xg,kickoff,home,away):
    out={}
    pts=defaultdict(lambda:deque(maxlen=20));gd=defaultdict(lambda:deque(maxlen=20))
    z=games[games.dt.lt(kickoff)].sort_values('dt')
    for _,r in z.iterrows():
        if pd.isna(r.get('home_score')) or pd.isna(r.get('away_score')):continue
        ht,at=r.home_team,r.away_team
        if not ht or not at:continue
        hs=float(r.home_score);aw=float(r.away_score)
        pts[ht].append(3 if hs>aw else 1 if hs==aw else 0)
        pts[at].append(3 if aw>hs else 1 if hs==aw else 0)
        gd[ht].append(hs-aw);gd[at].append(aw-hs)
    def avg(hist,team,n):
        v=list(hist[team])[-n:]
        return sum(v)/len(v) if len(v)>=n else None
    hp=avg(pts,home,10);ap=avg(pts,away,10)
    hgd=avg(gd,home,10);agd=avg(gd,away,10)
    if hp is not None and ap is not None:
        out['home_last10_ppg']=hp;out['away_last10_ppg']=ap
        out['home_edge_last10_ppg']=hp-ap;out['away_edge_last10_ppg']=ap-hp
    if hgd is not None and agd is not None:
        out['home_last10_gdpg']=hgd;out['away_last10_gdpg']=agd
        out['home_edge_last10_gdpg']=hgd-agd;out['away_edge_last10_gdpg']=agd-hgd

    hist=defaultdict(lambda:deque(maxlen=20))
    z=xg[xg.dt.lt(kickoff)].sort_values('dt')
    for _,r in z.iterrows():
        if pd.isna(r.get('home_team_xgoals')) or pd.isna(r.get('away_team_xgoals')):continue
        ht,at=r.home_team,r.away_team
        if not ht or not at:continue
        hx=float(r.home_team_xgoals);ax=float(r.away_team_xgoals)
        hist[ht].append((hx,ax));hist[at].append((ax,hx))
    def xv(team,n):
        v=list(hist[team])[-n:]
        if len(v)<n:return None
        xgf=sum(a for a,b in v)/n;xga=sum(b for a,b in v)/n
        return {'xgf':xgf,'xga':xga,'xgd':xgf-xga}
    for n in [3,5,10]:
        hv=xv(home,n);av=xv(away,n)
        if not hv or not av:continue
        for stat in ['xgf','xga','xgd']:
            out[f'home_last{n}_{stat}pg']=hv[stat];out[f'away_last{n}_{stat}pg']=av[stat]
            out[f'home_edge_last{n}_{stat}pg']=hv[stat]-av[stat]
            out[f'away_edge_last{n}_{stat}pg']=av[stat]-hv[stat]
    return out

def referee_features(games,kickoff,home,away):
    if 'referee_id' not in games.columns:return {}
    target=games[(games.home_team.eq(home))&(games.away_team.eq(away))&games.dt.notna()].copy()
    if len(target):
        target=target[(target.dt-kickoff).abs()<=pd.Timedelta(hours=18)]
    if len(target)!=1:return {}
    ref=target.iloc[0].get('referee_id')
    if pd.isna(ref) or str(ref).strip() in {'','nan','None'}:return {}
    z=games[games.dt.lt(kickoff)&games.referee_id.astype(str).eq(str(ref))].copy()
    z=z[pd.to_numeric(z.home_score,errors='coerce').notna()&pd.to_numeric(z.away_score,errors='coerce').notna()]
    if z.empty:return {}
    goals=pd.to_numeric(z.home_score,errors='coerce')+pd.to_numeric(z.away_score,errors='coerce')
    return {'referee_id':str(ref),'referee_prior_games':int(len(z)),
            'referee_prior_over25_rate':float((goals>=3).mean())}

def load_player_game_window(asa,games,teams,team_map):
    today=pd.Timestamp(now());start=(today-pd.Timedelta(days=180)).date().isoformat();end=today.date().isoformat()
    px=asa.get_player_xgoals(leagues='mls',start_date=start,end_date=end,split_by_games=True)
    players=asa.get_players(leagues='mls')
    if not isinstance(px,pd.DataFrame):px=pd.DataFrame(px)
    if not isinstance(players,pd.DataFrame):players=pd.DataFrame(players)
    if px.empty:return px
    gm=games[['game_id','date_time_utc']].copy();gm['game_id']=gm.game_id.astype(str)
    gm['dt']=pd.to_datetime(gm.date_time_utc,errors='coerce',utc=True);dtmap=dict(zip(gm.game_id,gm.dt))
    px['game_id']=px.game_id.astype(str);px['player_id']=px.player_id.astype(str)
    px['team']=px.team_id.astype(str).map(team_map);px['dt']=px.game_id.map(dtmap)
    px['minutes_played']=pd.to_numeric(px.minutes_played,errors='coerce').fillna(0)
    if 'birth_date' in players.columns:
        bp=players[['player_id','birth_date']].copy();bp['player_id']=bp.player_id.astype(str)
        bp['birth_date']=pd.to_datetime(bp.birth_date,errors='coerce',utc=True);bmap=dict(zip(bp.player_id,bp.birth_date))
        px['birth_date']=px.player_id.map(bmap);px['age_years']=(px.dt-px.birth_date).dt.total_seconds()/(365.25*86400)
    else:px['age_years']=np.nan
    return px[px.dt.notna()&px.team.notna()].copy()

def player_context_features(px,kickoff,home,away):
    if px is None or px.empty:return {}
    z=px[px.dt.lt(kickoff)&px.team.isin([home,away])].copy();out={}
    for label,team in [('home',home),('away',away)]:
        tr=z[z.team.eq(team)];game_rows=[]
        for (dt,gid),g in tr.groupby(['dt','game_id']):
            plist=[{'player_id':str(r.player_id),'minutes':float(r.minutes_played),
                    'age':r.age_years if pd.notna(r.age_years) else np.nan}
                   for _,r in g.iterrows() if float(r.minutes_played)>0]
            game_rows.append((dt,str(gid),plist))
        hist=sorted(game_rows,key=lambda x:x[0])[-10:]
        if len(hist)>=4:
            last3=hist[-3:];before3=hist[-10:-3]
            last_set={p['player_id'] for _,__,ps in last3 for p in ps}
            before_set={p['player_id'] for _,__,ps in before3 for p in ps}
            out[f'{label}_roster_new_players3']=len(last_set-before_set)
        h5=hist[-5:];age_num=age_den=0.0
        for _,__,ps in h5:
            for p in ps:
                m=float(p['minutes'])
                if pd.notna(p['age']):age_num+=m*float(p['age']);age_den+=m
        if age_den>0:out[f'{label}_player_weighted_age5']=age_num/age_den
    return out

def load_gk_window(asa,games,team_map):
    today=pd.Timestamp(now());start=(today-pd.Timedelta(days=180)).date().isoformat();end=today.date().isoformat()
    gk=asa.get_goalkeeper_xgoals(leagues='mls',start_date=start,end_date=end,split_by_games=True)
    if not isinstance(gk,pd.DataFrame):gk=pd.DataFrame(gk)
    if gk.empty:return gk
    gm=games[['game_id','date_time_utc']].copy();gm['game_id']=gm.game_id.astype(str)
    gm['dt']=pd.to_datetime(gm.date_time_utc,errors='coerce',utc=True);dtmap=dict(zip(gm.game_id,gm.dt))
    gk['game_id']=gk.game_id.astype(str);gk['team']=gk.team_id.astype(str).map(team_map);gk['dt']=gk.game_id.map(dtmap)
    for c in ['minutes_played','shots_faced','saves']:gk[c]=pd.to_numeric(gk[c],errors='coerce').fillna(0)
    return gk[gk.dt.notna()&gk.team.notna()].copy()

def gk_features(gk,kickoff,home,away):
    if gk is None or gk.empty:return {}
    z=gk[gk.dt.lt(kickoff)&gk.team.isin([home,away])].copy();vals={};out={}
    for label,team in [('home',home),('away',away)]:
        rows=[]
        for (dt,gid),g in z[z.team.eq(team)].groupby(['dt','game_id']):
            r=g.sort_values('minutes_played',ascending=False).iloc[0]
            rows.append((dt,float(r.shots_faced),float(r.saves)))
        rows=sorted(rows,key=lambda x:x[0])[-5:]
        if len(rows)>=5:
            shots=sum(x[1] for x in rows);saves=sum(x[2] for x in rows)
            vals[label]=saves/shots if shots>0 else None;out[f'{label}_gk_save_pct5']=vals[label]
    if vals.get('home') is not None and vals.get('away') is not None:
        out['home_edge_gk_save_pct5']=vals['home']-vals['away']
        out['away_edge_gk_save_pct5']=vals['away']-vals['home']
    return out

def pass_rule(v,op,t):
    if v is None or pd.isna(v):return False
    return {'>=':v>=t,'<=':v<=t,'<':v<t,'>':v>t}.get(op,False)

def main():
    if not MARKET.exists():raise RuntimeError('No MLS market snapshot')
    market=json.loads(MARKET.read_text());events=market.get('events') or []
    asa,games,xg,teams,team_map=asa_data();elo,elo_updated,elo_age=fetch_elo()
    player_window=load_player_game_window(asa,games,teams,team_map)
    gk_window=load_gk_window(asa,games,team_map)
    captured=pd.to_datetime((market.get('summary') or {}).get('captured_at'),errors='coerce',utc=True)
    if pd.isna(captured):raise RuntimeError('Market capture timestamp missing')
    rows=[]
    for ev in events:
        kickoff=pd.to_datetime(ev['commence_time'],errors='coerce',utc=True)
        if pd.isna(kickoff) or kickoff<=pd.Timestamp(now()):continue
        home=canon_team(ev['home_team']);away=canon_team(ev['away_team'])
        fx=rolling_team_features(games,xg,kickoff,home,away)
        if home in elo and away in elo:
            fx['home_elo_edge']=elo[home]-elo[away];fx['away_elo_edge']=elo[away]-elo[home]
        fx.update(player_context_features(player_window,kickoff,home,away))
        fx.update(gk_features(gk_window,kickoff,home,away))
        fx.update(referee_features(games,kickoff,home,away))

        for m in METHODS:
            if m['side']=='HOME':prob=ev['home_novig_prob'];selection=home;opponent=away
            elif m['side']=='AWAY':prob=ev['away_novig_prob'];selection=away;opponent=home
            else:prob=ev['draw_novig_prob'];selection='DRAW';opponent=f'{home} vs {away}'
            available=True;reasons=[];stale_required_input=False
            if not (m['prob_lo']<=prob<=m['prob_hi']):
                available=False;reasons.append(f"market_prob {prob:.3f} outside {m['prob_lo']:.2f}-{m['prob_hi']:.2f}")
            if m.get('requires_elo') and elo_age>3:
                available=False;stale_required_input=True;reasons.append(f'Elo stale: {elo_age:.1f} days old')
            checks=[]
            for feat,op,t in m['rules']:
                v=fx.get(feat);ok=pass_rule(v,op,t)
                checks.append({'feature':feat,'op':op,'threshold':t,'value':v,'pass':ok})
                if not ok:
                    available=False
                    if v is None or pd.isna(v):reasons.append(f'missing {feat}')
            confidence_tier='BASE'
            enhanced_checks=[]
            if available and m.get('enhanced_rules'):
                enhanced=True
                for feat,op,t in m['enhanced_rules']:
                    v=fx.get(feat);ok=pass_rule(v,op,t)
                    enhanced_checks.append({'feature':feat,'op':op,'threshold':t,'value':v,'pass':ok})
                    enhanced &= ok
                if enhanced:confidence_tier='ENHANCED_SUBSET'
            rows.append({
              'captured_at':market['summary']['captured_at'],'event_id':ev['event_id'],'commence_time':ev['commence_time'],
              'home_team':home,'away_team':away,'selection':selection,'opponent':opponent,'side':m['side'],
              'method_id':m['id'],'method_name':m['name'],'research_status':m['research_status'],
              'tracking_tier':'ACTIVE_PROSPECTIVE' if m['id'] in ACTIVE_METHOD_IDS else 'WATCH_ONLY',
              'status':'QUALIFIES_SHADOW' if available else ('BLOCKED_STALE_INPUT' if stale_required_input else 'NO_MATCH'),
              'confidence_tier':confidence_tier if available else None,'market_prob':prob,
              'american_price':ev['home_american'] if m['side']=='HOME' else ev['away_american'] if m['side']=='AWAY' else ev['draw_american'],
              'decimal_price':ev['home_odds'] if m['side']=='HOME' else ev['away_odds'] if m['side']=='AWAY' else ev['draw_odds'],
              'book':ev['provider'],'checks':checks,'enhanced_checks':enhanced_checks,'blocking_reasons':reasons,
              'features':fx,'history':HISTORY[m['id']],'elo_source_updated_at':elo_updated,'elo_age_days':elo_age,'market_source':ev['source']
            })
    qualifiers=[r for r in rows if r['status']=='QUALIFIES_SHADOW']
    active_qualifiers=[r for r in qualifiers if r['method_id'] in ACTIVE_METHOD_IDS]
    watch_qualifiers=[r for r in qualifiers if r['method_id'] in WATCH_ONLY_METHOD_IDS]

    # Research-level overlap/conflicts include watch-only methods for diagnostics.
    event_groups=defaultdict(list)
    for q in qualifiers:event_groups[str(q['event_id'])].append(q)
    conflicts=[]
    consensus=[]
    for eid,grp in event_groups.items():
        sels={str(x['selection']) for x in grp}
        if len(grp)>1 and len(sels)==1:
            consensus.append({'event_id':eid,'home_team':grp[0]['home_team'],'away_team':grp[0]['away_team'],
                              'methods':[x['method_id'] for x in grp],'selection':grp[0]['selection'],
                              'policy':'CONSENSUS_SHADOW_SIGNAL'})
            for x in grp:x['same_side_consensus']=True
        if len(sels)>1:
            conflicts.append({'event_id':eid,'home_team':grp[0]['home_team'],'away_team':grp[0]['away_team'],
                              'methods':[x['method_id'] for x in grp],'selections':[x['selection'] for x in grp],
                              'policy':'TRACK_FOR_RESEARCH'})

    # Portfolio eligibility is determined ONLY from active methods.
    active_groups=defaultdict(list)
    for q in active_qualifiers:active_groups[str(q['event_id'])].append(q)
    portfolio_conflicts=[]
    portfolio_matches=[]
    for eid,grp in active_groups.items():
        sels={str(x['selection']) for x in grp}
        if len(sels)>1:
            portfolio_conflicts.append({'event_id':eid,'home_team':grp[0]['home_team'],'away_team':grp[0]['away_team'],
                                        'methods':[x['method_id'] for x in grp],'selections':[x['selection'] for x in grp],
                                        'policy':'NO_PORTFOLIO_ACTION'})
            for x in grp:
                x['portfolio_conflict']=True
                x['portfolio_eligible']=False
        else:
            for x in grp:
                x['portfolio_conflict']=False
                x['portfolio_eligible']=True
            portfolio_matches.append({
                'event_id':eid,'home_team':grp[0]['home_team'],'away_team':grp[0]['away_team'],
                'selection':grp[0]['selection'],'methods':[x['method_id'] for x in grp],
                'consensus':len(grp)>1,'max_bankroll_exposure_pct':1.0,
                'staking_policy':'MAX_1_PERCENT_CURRENT_BANKROLL_PER_MATCH'
            })
    for q in watch_qualifiers:
        q['portfolio_eligible']=False
        q['portfolio_conflict']=False
    payload={
      'built_at':now().isoformat(),'market_captured_at':market['summary']['captured_at'],
      'elo_updated_at':elo_updated,'elo_age_days':elo_age,'shadow_only':True,'official_autopromotions':0,
      'retired_methods':{
        'MLS-P01':{'status':'RESEARCH_ONLY_REJECT_RECENT','reason':'2025 historical collapse; no preholdout-derived veto rescued recent stability.'},
        'MLS-R01':{'status':'LEGACY_REPLACED_BY_R01V2','reason':'Refined rule improved pre-holdout and untouched holdout; old ledger entries preserved.'},
        'MLS-R02':{'status':'LEGACY_REPLACED_BY_R02V2','reason':'Stricter xG rule improved consistency; old ledger entries preserved.'},
        'MLS-A07':{'status':'RESEARCH_ONLY_REDUNDANT','reason':'60-74% exact overlap with R01/R02 family; useful signal absorbed into refinements instead of added separately.'}
      },
      'active_method_ids':sorted(ACTIVE_METHOD_IDS),'watch_only_method_ids':sorted(WATCH_ONLY_METHOD_IDS),
      'methods':{m['id']:{'name':m['name'],'status':m['research_status'],
                          'tracking_tier':'ACTIVE_PROSPECTIVE' if m['id'] in ACTIVE_METHOD_IDS else 'WATCH_ONLY'} for m in METHODS},
      'rows':rows,'qualifiers':qualifiers,'active_qualifiers':active_qualifiers,'watch_qualifiers':watch_qualifiers,
      'conflicts':conflicts,'consensus':consensus,'portfolio_conflicts':portfolio_conflicts,'portfolio_matches':portfolio_matches,
      'portfolio_policy':{'opposite_side_conflict':'ACTIVE_METHOD_CONFLICTS_GET_NO_PORTFOLIO_ACTION',
                          'same_side_consensus':'TAG_AND_TRACK_SEPARATELY_NO_STAKE_MULTIPLIER',
                          'staking_policy':'MAX_1_PERCENT_CURRENT_BANKROLL_PER_MATCH',
                          'max_match_exposure_pct':1.0,
                          'historical_clean_portfolio_roi':.31467619848612277,
                          'historical_clean_holdout_roi':.36385852090032156},
      'status_counts':{m['id']:{st:sum(1 for r in rows if r['method_id']==m['id'] and r['status']==st)
                       for st in ['QUALIFIES_SHADOW','NO_MATCH','BLOCKED_STALE_INPUT']} for m in METHODS}
    }
    (OUT/'current_shadow_board.json').write_text(json.dumps(payload,indent=2,default=str))
    with (OUT/'shadow_observations.jsonl').open('a') as f:f.write(json.dumps(payload,default=str,separators=(',',':'))+'\n')
    print(json.dumps({'built_at':payload['built_at'],'market_captured_at':payload['market_captured_at'],
      'elo_updated_at':elo_updated,'elo_age_days':elo_age,'events':len(events),'method_checks':len(rows),
      'qualifiers':len(qualifiers),'active_qualifiers':len(active_qualifiers),'watch_qualifiers':len(watch_qualifiers),
      'portfolio_matches':portfolio_matches,'portfolio_conflicts':portfolio_conflicts,
      'status_counts':payload['status_counts'],'conflicts':conflicts,'consensus':consensus,
      'qualifying':[(r['method_id'],r['selection'],r['opponent'],r['american_price'],r.get('confidence_tier')) for r in qualifiers],
      'retired_methods':payload['retired_methods'],'official_autopromotions':0},indent=2))

if __name__=='__main__':main()
