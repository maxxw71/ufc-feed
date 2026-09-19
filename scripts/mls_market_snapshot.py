#!/usr/bin/env python3
from __future__ import annotations
import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/market_snapshots';OUT.mkdir(parents=True,exist_ok=True)
ENV_PATHS=[
 Path('/home/anestishkurti92/.config/ufc-watcher.env'),
 Path('/home/anestishkurti92/.config/nfl-watcher.env'),
 ROOT/'.env',
]

def load_env():
    env=dict(os.environ)
    for p in ENV_PATHS:
        if not p.exists():continue
        for line in p.read_text(errors='ignore').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            k,v=line.split('=',1);k=k.strip();v=v.strip().strip('"').strip("'")
            env.setdefault(k,v)
    return env
def get_key(env):
    for k in ['ODDS_API_KEY','THE_ODDS_API_KEY','THEODDSAPI_KEY','ODDSAPI_KEY']:
        if env.get(k):return env[k]
    for k,v in env.items():
        if 'ODDS' in k.upper() and 'KEY' in k.upper() and v:return v
    return None
def fetch():
    env=load_env();key=get_key(env)
    if not key:
        raise RuntimeError('No Odds API key found in existing Appwiza environment')
    qs=urllib.parse.urlencode({'apiKey':key,'regions':'us,us2','markets':'h2h','oddsFormat':'decimal','dateFormat':'iso'})
    url='https://api.the-odds-api.com/v4/sports/soccer_usa_mls/odds?'+qs
    req=urllib.request.Request(url,headers={'User-Agent':'Appwiza-MLS/1.0'})
    with urllib.request.urlopen(req,timeout=45) as r:
        raw=r.read();headers=dict(r.headers)
    data=json.loads(raw)
    ts=datetime.now(timezone.utc)
    stamp=ts.strftime('%Y%m%dT%H%M%SZ')
    (OUT/f'{stamp}_raw.json').write_bytes(raw)
    rows=[]
    for ev in data:
        home=ev.get('home_team');away=ev.get('away_team')
        for b in ev.get('bookmakers') or []:
            for m in b.get('markets') or []:
                if m.get('key')!='h2h':continue
                outcomes={str(o.get('name')):o.get('price') for o in m.get('outcomes') or []}
                row={'captured_at':ts.isoformat(),'event_id':ev.get('id'),'commence_time':ev.get('commence_time'),
                     'home_team':home,'away_team':away,'bookmaker_key':b.get('key'),'bookmaker':b.get('title'),
                     'bookmaker_updated_at':b.get('last_update'),'home_odds':outcomes.get(home),'away_odds':outcomes.get(away),
                     'draw_odds':outcomes.get('Draw')}
                if all(isinstance(row[x],(int,float)) and row[x]>1 for x in ['home_odds','draw_odds','away_odds']):
                    inv=[1/row['home_odds'],1/row['draw_odds'],1/row['away_odds']];tot=sum(inv)
                    row.update(home_novig_prob=inv[0]/tot,draw_novig_prob=inv[1]/tot,away_novig_prob=inv[2]/tot,overround=tot-1)
                rows.append(row)
    with (OUT/f'{stamp}.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,sort_keys=True)+'\n')
    summary={'captured_at':ts.isoformat(),'events':len(data),'quotes':len(rows),
             'complete_3way_quotes':sum(all(isinstance(r.get(x),(int,float)) for x in ['home_odds','draw_odds','away_odds']) for r in rows),
             'remaining_requests':headers.get('x-requests-remaining'),'used_requests':headers.get('x-requests-used'),
             'snapshot':str(OUT/f'{stamp}.jsonl')}
    (OUT/'latest_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
if __name__=='__main__':fetch()
