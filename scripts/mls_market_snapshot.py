#!/usr/bin/env python3
from __future__ import annotations
import json,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/market_snapshots';OUT.mkdir(parents=True,exist_ok=True)
JINA='https://r.jina.ai/'
BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard?dates='
UA='Mozilla/5.0 AppwizaMLS/2.0'

def american_to_decimal(v):
    v=float(v);return 1+v/100 if v>0 else 1+100/abs(v)
def novig(h,d,a):
    inv=[1/h,1/d,1/a];s=sum(inv);return inv[0]/s,inv[1]/s,inv[2]/s,s-1
def fetch_day(day):
    url=JINA+BASE+day.strftime('%Y%m%d')
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/plain'})
    raw=urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')
    payload=raw.split('Markdown Content:',1)[-1].strip()
    p=payload.find('{')
    return json.loads(payload[p:])

def parse_quote(o):
    ml=o.get('moneyline') or {}
    def get(side):
        z=(ml.get(side) or {}).get('close') or {}
        return z.get('odds')
    ah,ad,aa=get('home'),get('draw'),get('away')
    if ah is None or ad is None or aa is None:return None
    try:dh,dd,da=map(american_to_decimal,[ah,ad,aa])
    except Exception:return None
    ph,pd_,pa,over=novig(dh,dd,da)
    provider=(o.get('provider') or {}).get('displayName') or (o.get('provider') or {}).get('name') or 'Unknown'
    return {'provider':provider,'home_american':ah,'draw_american':ad,'away_american':aa,
            'home_odds':dh,'draw_odds':dd,'away_odds':da,
            'home_novig_prob':ph,'draw_novig_prob':pd_,'away_novig_prob':pa,'overround':over}

def main():
    captured=datetime.now(timezone.utc);rows=[]
    for off in range(0,15):
        day=(captured+timedelta(days=off)).date()
        try:d=fetch_day(day)
        except Exception as e:
            print('warn',day,type(e).__name__,str(e)[:180]);continue
        for ev in d.get('events') or []:
            comp=(ev.get('competitions') or [{}])[0]
            st=(comp.get('status') or {}).get('type') or {}
            if st.get('completed'):continue
            teams={x.get('homeAway'):x.get('team',{}).get('displayName') for x in comp.get('competitors') or []}
            home,away=teams.get('home'),teams.get('away')
            if not home or not away:continue
            quotes=[q for q in (parse_quote(o) for o in (comp.get('odds') or [])) if q]
            if not quotes:continue
            # Keep one primary quote for every existing downstream consumer.
            primary=quotes[0]
            rows.append({
              'captured_at':captured.isoformat(),'event_id':str(ev.get('id')),'commence_time':ev.get('date'),
              'home_team':home,'away_team':away,**primary,'quotes':quotes,
              'source':'ESPN scoreboard via Jina relay'
            })
    ded={str(r['event_id']):r for r in rows};rows=sorted(ded.values(),key=lambda r:r['commence_time'] or '')
    stamp=captured.strftime('%Y%m%dT%H%M%SZ')
    path=OUT/f'{stamp}.jsonl'
    with path.open('w') as f:
        for r in rows:f.write(json.dumps(r,sort_keys=True)+'\n')
    quote_count=sum(len(r.get('quotes') or []) for r in rows)
    providers=sorted({q['provider'] for r in rows for q in (r.get('quotes') or [])})
    summary={'captured_at':captured.isoformat(),'events':len(rows),'complete_3way_quotes':quote_count,
             'providers':providers,'primary_provider':rows[0]['provider'] if rows else None,
             'snapshot':str(path)}
    (OUT/'latest.json').write_text(json.dumps({'summary':summary,'events':rows},indent=2))
    (OUT/'latest_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
