#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed';REP=ROOT/'reports'
BASE=PROC/'mls_match_features_weather_enriched.parquet'
LINEUPS=PROC/'espn_confirmed_lineups.parquet'
CARDS=PROC/'espn_match_cards.parquet'
OUT=PROC/'mls_referee_discipline_features.parquet'
META=REP/'referee_discipline_feature_meta.json'

def now():return datetime.now(timezone.utc).isoformat()

def metrics(hist):
    if not hist:return {}
    n=len(hist)
    y=[x['yellow'] for x in hist];r=[x['red'] for x in hist];t=[x['total'] for x in hist]
    return {
      'card_games':n,'yellow_pg':float(np.mean(y)),'red_pg':float(np.mean(r)),'cards_pg':float(np.mean(t)),
      'cards_ge4_rate':float(np.mean([x>=4 for x in t])),
      'cards_ge5_rate':float(np.mean([x>=5 for x in t])),
      'cards_ge6_rate':float(np.mean([x>=6 for x in t])),
    }

def main():
    for p in [BASE,LINEUPS]:
        if not p.exists():raise RuntimeError(f'Missing {p}')
    base=pd.read_parquet(BASE).copy();base['dt']=pd.to_datetime(base.date,errors='coerce',utc=True)
    line=pd.read_parquet(LINEUPS).copy()
    cards=pd.read_parquet(CARDS).copy() if CARDS.exists() else pd.DataFrame()

    line['espn_event_id']=line.espn_event_id.astype(str)
    sc=(line[line.starter.eq(True)].groupby(['espn_event_id','home_away']).size().unstack(fill_value=0))
    complete=set(sc[(sc.get('home',0).eq(11))&(sc.get('away',0).eq(11))].index.astype(str))
    event_match=(line[line.espn_event_id.isin(complete)][['espn_event_id','match_id']]
                 .drop_duplicates('espn_event_id').set_index('espn_event_id').match_id.astype(str).to_dict())

    counts=defaultdict(lambda:{'yellow':0,'red':0})
    if len(cards):
        cards['espn_event_id']=cards.espn_event_id.astype(str)
        for _,r in cards[cards.espn_event_id.isin(complete)].iterrows():
            mid=event_match.get(str(r.espn_event_id))
            if not mid:continue
            typ=str(r.card_type).upper()
            if typ=='YELLOW':counts[mid]['yellow']+=1
            elif typ=='RED':counts[mid]['red']+=1

    # Every complete lineup summary is an explicit observed-card match, including zero-card matches.
    observed={}
    for eid,mid in event_match.items():
        c=counts[str(mid)]
        observed[str(mid)]={'yellow':int(c['yellow']),'red':int(c['red']),'total':int(c['yellow']+c['red'])}

    hist=defaultdict(list);rows=[]
    for _,g in base[base.dt.notna()].sort_values(['dt','match_id']).iterrows():
        ref=str(g.referee_id) if 'referee_id' in g and pd.notna(g.referee_id) else None
        row={'match_id':str(g.match_id),'referee_discipline_source_available':int(str(g.match_id) in observed)}
        if ref:
            h=hist[ref]
            career=metrics(h)
            recent=metrics(h[-10:])
            for k,v in career.items():row['referee_cards_prior_'+k]=v
            for k,v in recent.items():row['referee_cards_recent10_'+k]=v
            if career and recent:
                for k in ['yellow_pg','red_pg','cards_pg','cards_ge4_rate','cards_ge5_rate','cards_ge6_rate']:
                    row['referee_cards_delta_recent10_'+k]=recent[k]-career[k]
        rows.append(row)
        obs=observed.get(str(g.match_id))
        if ref and obs is not None:hist[ref].append(obs)

    feat=pd.DataFrame(rows);feat.to_parquet(OUT,index=False)
    meta={
      'built_at':now(),'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
      'explicit_card_matches':len(observed),
      'matches_with_referee_card_history':int(feat.get('referee_cards_prior_card_games',pd.Series(dtype=float)).fillna(0).gt(0).sum()),
      'complete_lineup_events_used_as_card_observation_gate':len(complete),
      'leakage_note':'Referee card history is read before the target match, then updated after it. Zero cards are accepted only for ESPN events with complete 11v11 lineup summaries, avoiding silent parse-failure zeros.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
