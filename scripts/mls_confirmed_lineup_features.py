#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'
REP=ROOT/'reports'
BASE=PROC/'mls_match_features_context_enriched.parquet'
LINEUPS=PROC/'espn_confirmed_lineups.parquet'
CARDS=PROC/'espn_match_cards.parquet'
OUT_FEATURES=PROC/'mls_confirmed_lineup_card_pregame_features.parquet'
OUT=PROC/'mls_match_features_confirmed_lineup_enriched.parquet'
REPORT=REP/'confirmed_lineup_feature_meta.json'

def now():return datetime.now(timezone.utc).isoformat()

def jaccard(a,b):
    if not a and not b:return np.nan
    return len(a&b)/max(1,len(a|b))

def main():
    for p in [BASE,LINEUPS]:
        if not p.exists():raise RuntimeError(f'Missing required file: {p}')

    base=pd.read_parquet(BASE).copy()
    line=pd.read_parquet(LINEUPS).copy()
    cards=pd.read_parquet(CARDS).copy() if CARDS.exists() else pd.DataFrame()

    base['date']=pd.to_datetime(base.date,errors='coerce',utc=True)
    line['date']=pd.to_datetime(line.date,errors='coerce',utc=True)
    line['team']=line.team.map(canon_team)
    line['espn_event_id']=line.espn_event_id.astype(str)
    line['espn_player_id']=line.espn_player_id.astype('string')

    # Only events with exactly 11 confirmed starters per side are allowed to
    # update lineup state. Partial roster pages never masquerade as a lineup.
    sc=(line[line.starter.eq(True)]
        .groupby(['espn_event_id','home_away']).size().unstack(fill_value=0))
    complete_ids=set(sc[(sc.get('home',0).eq(11))&(sc.get('away',0).eq(11))].index.astype(str))
    line=line[line.espn_event_id.isin(complete_ids)].copy()

    # Resolve card team from the same event's roster identity.
    card_by_event_team=defaultdict(lambda:defaultdict(list))
    if len(cards):
        cards['espn_event_id']=cards.espn_event_id.astype(str)
        cards['espn_player_id']=cards.espn_player_id.astype('string')
        roster_identity=(line[['espn_event_id','espn_player_id','team']]
                         .dropna().drop_duplicates(['espn_event_id','espn_player_id']))
        cards=cards.merge(roster_identity,on=['espn_event_id','espn_player_id'],how='left')
        for _,r in cards[cards.team.notna()].iterrows():
            card_by_event_team[str(r.espn_event_id)][canon_team(r.team)].append({
                'player_id':str(r.espn_player_id),
                'type':str(r.card_type).upper(),
            })

    by_match_team={}
    event_for_match={}
    for (mid,team),z in line.groupby(['match_id','team']):
        event=str(z.espn_event_id.iloc[0])
        starters=set(z.loc[z.starter.eq(True),'espn_player_id'].dropna().astype(str))
        gks=set(z.loc[z.starter.eq(True)&z.position.astype(str).str.upper().isin(['G','GK']),
                      'espn_player_id'].dropna().astype(str))
        by_match_team[(str(mid),team)]={'event_id':event,'starters':starters,'gks':gks}
        event_for_match[str(mid)]=event

    lineup_hist=defaultdict(lambda:deque(maxlen=10))
    player_card_hist=defaultdict(lambda:deque(maxlen=10))
    team_card_hist=defaultdict(lambda:deque(maxlen=10))
    rows=[]

    def summary(team):
        hist=list(lineup_hist[team])
        out={
          'confirmed_lineup_prior_games':len(hist),
          'confirmed_xi_overlap_prev2':np.nan,
          'confirmed_xi_changes_prev2':np.nan,
          'confirmed_xi_mean_overlap5':np.nan,
          'confirmed_unique_starters5':np.nan,
          'confirmed_top11_start_share5':np.nan,
          'confirmed_same_gk_last3':np.nan,
          'confirmed_expected_xi_yellows5':np.nan,
          'confirmed_expected_xi_reds10':np.nan,
          'team_yellows5':np.nan,
          'team_reds5':np.nan,
        }
        if not hist:return out

        if len(hist)>=2:
            a=hist[-1]['starters'];b=hist[-2]['starters']
            out['confirmed_xi_overlap_prev2']=jaccard(a,b)
            out['confirmed_xi_changes_prev2']=11-len(a&b)

        h5=hist[-5:]
        if len(h5)>=2:
            overlaps=[jaccard(h5[i]['starters'],h5[i-1]['starters']) for i in range(1,len(h5))]
            out['confirmed_xi_mean_overlap5']=float(np.nanmean(overlaps))
        cnt=Counter(p for h in h5 for p in h['starters'])
        if cnt:
            out['confirmed_unique_starters5']=len(cnt)
            out['confirmed_top11_start_share5']=sum(v for _,v in cnt.most_common(11))/max(1,11*len(h5))

        h3=hist[-3:]
        gks=[next(iter(h['gks'])) for h in h3 if len(h['gks'])==1]
        if len(gks)>=2:out['confirmed_same_gk_last3']=float(len(set(gks))==1)

        # Expected XI = last confirmed starting XI. Card burden only uses cards
        # from matches already completed before the target match.
        expected=hist[-1]['starters']
        y=0;r=0
        for pid in expected:
            ph=list(player_card_hist[(team,pid)])
            y+=sum(x['yellow'] for x in ph[-5:])
            r+=sum(x['red'] for x in ph[-10:])
        out['confirmed_expected_xi_yellows5']=y
        out['confirmed_expected_xi_reds10']=r

        th=list(team_card_hist[team])[-5:]
        if th:
            out['team_yellows5']=sum(x['yellow'] for x in th)
            out['team_reds5']=sum(x['red'] for x in th)
        return out

    # Use full warehouse chronological order; only matches with confirmed ESPN
    # lineups update confirmed-lineup state.
    for _,g in base.sort_values(['date','match_id']).iterrows():
        row={'match_id':str(g.match_id),'asa_game_id':str(g.asa_game_id) if pd.notna(g.asa_game_id) else None}
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team'])
            sm=summary(team)
            for k,v in sm.items():row[f'{side}_{k}']=v

        for k in [
          'confirmed_xi_overlap_prev2','confirmed_xi_changes_prev2','confirmed_xi_mean_overlap5',
          'confirmed_unique_starters5','confirmed_top11_start_share5','confirmed_same_gk_last3',
          'confirmed_expected_xi_yellows5','confirmed_expected_xi_reds10','team_yellows5','team_reds5'
        ]:
            h=row.get('home_'+k);a=row.get('away_'+k)
            row['edge_'+k]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)

        mid=str(g.match_id);event=event_for_match.get(mid)
        if not event:continue
        for side in ['home','away']:
            team=canon_team(g[f'{side}_team'])
            info=by_match_team.get((mid,team))
            if not info:continue

            # Build zero card row for every expected participant before applying
            # actual cards from this completed match.
            card_counts=defaultdict(lambda:{'yellow':0,'red':0})
            for c in card_by_event_team[event].get(team,[]):
                if c['type']=='YELLOW':card_counts[c['player_id']]['yellow']+=1
                elif c['type']=='RED':card_counts[c['player_id']]['red']+=1
            for pid in info['starters']:
                cc=card_counts[pid]
                player_card_hist[(team,pid)].append({'yellow':cc['yellow'],'red':cc['red']})
            team_card_hist[team].append({
                'yellow':sum(x['yellow'] for x in card_counts.values()),
                'red':sum(x['red'] for x in card_counts.values()),
            })
            lineup_hist[team].append(info)

    feat=pd.DataFrame(rows)
    feat.to_parquet(OUT_FEATURES,index=False)
    keep=['match_id']+[c for c in feat.columns if c!='match_id' and c!='asa_game_id']
    out=base.merge(feat[keep],on='match_id',how='left',validate='1:1')
    out.to_parquet(OUT,index=False)

    added=[c for c in out.columns if c not in base.columns]
    coverage=int(feat.home_confirmed_lineup_prior_games.gt(0).sum()+feat.away_confirmed_lineup_prior_games.gt(0).sum())
    meta={
      'built_at':now(),'base_file':str(BASE),'rows':len(out),'columns':len(out.columns),
      'added_columns':len(added),'added':added,'complete_lineup_events':len(complete_ids),
      'team_side_rows_with_prior_confirmed_lineup':coverage,
      'output':str(OUT),
      'leakage_note':'Target-match starters/cards are never used as target-match pregame features. Each match reads only confirmed lineups/cards from already completed prior matches, then updates state after feature capture.'
    }
    REPORT.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':
    main()
