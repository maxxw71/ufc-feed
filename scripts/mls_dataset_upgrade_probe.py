#!/usr/bin/env python3
from __future__ import annotations
import json,urllib.request
from pathlib import Path
import pandas as pd

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
PROC=ROOT/'data/processed'

def brief_df(path):
    p=Path(path)
    if not p.exists(): return {'exists':False}
    d=pd.read_parquet(p)
    return {'exists':True,'rows':len(d),'cols':list(d.columns),'head':d.head(3).astype(str).to_dict('records')}

def fetch(url,timeout=30):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 AppwizaMLSAudit/1.0','Accept':'*/*'})
    body=urllib.request.urlopen(req,timeout=timeout).read().decode('utf-8','replace')
    return {'ok':True,'len':len(body),'head':body[:1000]}

def main():
    out={}
    for name in [
      'asa_player_salaries.parquet','asa_team_salaries.parquet','asa_players.parquet',
      'asa_mls_games_2012_present.parquet','espn_confirmed_lineups.parquet','espn_match_cards.parquet',
      'mls_match_features_weather_enriched.parquet','mls_match_features_confirmed_lineup_enriched.parquet'
    ]:
        out[name]=brief_df(PROC/name)

    urls={
      'tx2023_direct':'https://www.mlssoccer.com/news/2023-mls-transactions',
      'tx2023_jina':'https://r.jina.ai/https://www.mlssoccer.com/news/2023-mls-transactions',
      'transfers2026_direct':'https://www.mlssoccer.com/league-reports/all-transfers/',
      'transfers2026_jina':'https://r.jina.ai/https://www.mlssoccer.com/league-reports/all-transfers/',
      'open2024':'https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/usa.open/scoreboard?dates=2024&limit=1000',
      'leagues2024':'https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/concacaf.leagues.cup/scoreboard?dates=2024&limit=1000',
      'ccc2024':'https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/concacaf.champions/scoreboard?dates=2024&limit=1000',
      'canada2024':'https://r.jina.ai/https://site.api.espn.com/apis/site/v2/sports/soccer/canadian.championship/scoreboard?dates=2024&limit=1000',
    }
    probes={}
    for k,u in urls.items():
        try: probes[k]=fetch(u)
        except Exception as e: probes[k]={'ok':False,'error':type(e).__name__+': '+str(e)[:300]}
    out['probes']=probes
    print(json.dumps(out,indent=2,default=str))

if __name__=='__main__':
    main()
