#!/usr/bin/env python3
from __future__ import annotations
import json,re,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/market_snapshots';OUT.mkdir(parents=True,exist_ok=True)
JINA='https://r.jina.ai/'
BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard?dates='
UA='Mozilla/5.0 AppwizaMLS/1.0'

def american_to_decimal(v):
    v=float(v)
    return 1+v/100 if v>0 else 1+100/abs(v)
def novig(h,d,a):
    inv=[1/h,1/d,1/a];s=sum(inv);return inv[0]/s,inv[1]/s,inv[2]/s,s-1
def fetch_day(day):
    url=JINA+BASE+day.strftime('%Y%m%d')
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/plain'})
    raw=urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')
    mark='Markdown Content:\n'
    payload=raw.split(mark,1)[1] if mark in raw else raw
    return json.loads(payload.strip())
def main():
    now=datetime.now(timezone.utc)
    rows=[]
    for off in range(0,15):
        day=(now+timedelta(days=off)).date()
        try:d=fetch_day(day)
        except Exception as e:
            print('warn',day,type(e).__name__,str(e)[:180]);continue
        for ev in d.get('events') or []:
            comp=(ev.get('competitions') or [{}])[0]
            st=(comp.get('status') or {}).get('type') or {}
            if st.get('state') not in {'pre','in'} and st.get('completed'):continue
            teams={x.get('homeAway'):x.get('team',{}).get('displayName') for x in comp.get('competitors') or []}
            home,away=teams.get('home'),teams.get('away')
            odds=(comp.get('odds') or [])
            if not home or not away or not odds:continue
            o=odds[0];ml=(o.get('moneyline') or {})
            def get(side):
                z=(ml.get(side) or {}).get('close') or {}
                return z.get('odds')
            ah,ad,aa=get('home'),get('draw'),get('away')
            if ah is None or ad is None or aa is None:continue
            dec_h,dec_d,dec_a=map(american_to_decimal,[ah,ad,aa])
            ph,pd_,pa,over=novig(dec_h,dec_d,dec_a)
            rows.append({
              'captured_at':now.isoformat(),'event_id':ev.get('id'),'commence_time':ev.get('date'),
              'home_team':home,'away_team':away,'provider':(o.get('provider') or {}).get('displayName') or (o.get('provider') or {}).get('name') or 'DraftKings',
              'home_american':ah,'draw_american':ad,'away_american':aa,
              'home_odds':dec_h,'draw_odds':dec_d,'away_odds':dec_a,
              'home_novig_prob':ph,'draw_novig_prob':pd_,'away_novig_prob':pa,'overround':over,
              'source':'ESPN scoreboard via Jina relay'
            })
    # exact duplicate event ids can appear in multiple day queries; keep one.
    ded={str(r['event_id']):r for r in rows};rows=sorted(ded.values(),key=lambda r:r['commence_time'] or '')
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    path=OUT/f'{stamp}.jsonl'
    with path.open('w') as f:
        for r in rows:f.write(json.dumps(r,sort_keys=True)+'\n')
    summary={'captured_at':now.isoformat(),'events':len(rows),'complete_3way_quotes':len(rows),
             'provider':'DraftKings via ESPN/Jina','snapshot':str(path)}
    (OUT/'latest.json').write_text(json.dumps({'summary':summary,'events':rows},indent=2))
    (OUT/'latest_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
