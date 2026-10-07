#!/usr/bin/env python3
import json,math,re
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests

NBA=Path(__file__).resolve().parents[1]
SC=NBA/"scanner";HIST=SC/"history";OUT=SC/"settlements.jsonl"
SCOREBOARD="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
HEADERS={"User-Agent":"Mozilla/5.0 AppwizaNBASettlement/1.0","Accept":"application/json, text/plain, */*"}

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return None
def n(v):
    try:return float(v)
    except:return None
def read_jsonl(p):
    out=[]
    if not p.exists():return out
    with open(p,"r",encoding="utf-8") as f:
        for line in f:
            try:out.append(json.loads(line))
            except:pass
    return out
def append_jsonl(p,rows):
    rows=list(rows)
    if not rows:return
    p.parent.mkdir(parents=True,exist_ok=True)
    with open(p,"a",encoding="utf-8") as f:
        for r in rows:f.write(json.dumps(r,separators=(",",":"),sort_keys=True,default=str)+"\n")
def profit(price,outcome):
    p=n(price)
    if outcome=="push":return 0.0
    if outcome!="win":return -1.0
    if p is None or abs(p)<100:return None
    return p/100 if p>0 else 100/abs(p)
def fetch_finals():
    now=datetime.now(timezone.utc);games={}
    for delta in (-2,-1,0):
        day=(now+timedelta(days=delta)).strftime("%Y%m%d")
        r=requests.get(SCOREBOARD,params={"dates":day,"limit":100},headers=HEADERS,timeout=30)
        r.raise_for_status()
        for ev in r.json().get("events") or []:
            comp=(ev.get("competitions") or [{}])[0]
            st=((ev.get("status") or {}).get("type") or {})
            if not st.get("completed"):continue
            sides={}
            for c in comp.get("competitors") or []:
                if c.get("homeAway") in ("home","away"):sides[c["homeAway"]]=c
            h=sides.get("home") or {};a=sides.get("away") or {}
            games[str(ev.get("id") or "")]={
              "game_id":str(ev.get("id") or ""),"scheduled_utc":ev.get("date"),
              "home_team_id":str(((h.get("team") or {}).get("id")) or ""),
              "away_team_id":str(((a.get("team") or {}).get("id")) or ""),
              "home_score":n(h.get("score")),"away_score":n(a.get("score"))
            }
    return games

# Recent evaluation history is enough for newly final games.
now=datetime.now(timezone.utc);evaluations=[]
for delta in range(5):
    p=HIST/(now.date()-timedelta(days=delta)).isoformat()/"method_evaluations.jsonl"
    evaluations.extend(read_jsonl(p))
bykey=defaultdict(list)
for e in evaluations:
    key="|".join([str(e.get("game_id") or ""),str(e.get("method_id") or ""),str(e.get("selection") or "")])
    if e.get("game_id") and e.get("method_id"):bykey[key].append(e)
for arr in bykey.values():arr.sort(key=lambda x:dt(x.get("scanned_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))

existing=read_jsonl(OUT)
settled_keys={r.get("settlement_key") for r in existing}
finals=fetch_finals();new=[]
for key,arr in bykey.items():
    gid=arr[0].get("game_id");g=finals.get(gid)
    if not g:continue
    tip=dt(arr[0].get("scheduled_utc") or g.get("scheduled_utc"))
    pre=[e for e in arr if dt(e.get("scanned_at_utc")) and (tip is None or dt(e.get("scanned_at_utc"))<tip)]
    if not pre:continue
    qualified=[e for e in pre if e.get("qualified") in (True,"true","True",1,"1")]
    if not qualified:continue
    first=qualified[0];lastq=qualified[-1];close=pre[-1]
    skey=key
    if skey in settled_keys:continue
    closeq=close.get("qualified") in (True,"true","True",1,"1")
    market=first.get("market")
    outcome=None;actual=None;used_line=n(close.get("line") if closeq else lastq.get("line"))
    if market=="moneyline":
        tid=str(first.get("selection_team_id") or "")
        if tid==g["home_team_id"]:
            outcome="win" if g["home_score"]>g["away_score"] else "loss"
        elif tid==g["away_team_id"]:
            outcome="win" if g["away_score"]>g["home_score"] else "loss"
    elif market=="total_over":
        actual=(g["home_score"] or 0)+(g["away_score"] or 0)
        if used_line is not None:
            outcome="win" if actual>used_line else ("loss" if actual<used_line else "push")
    if outcome is None:continue
    first_price=n(first.get("price"));close_price=n(close.get("price")) if closeq else None
    chosen_price=close_price if closeq else n(lastq.get("price"))
    new.append({
      "settlement_key":skey,"settled_at_utc":now.isoformat(),"game_id":gid,"scheduled_utc":first.get("scheduled_utc"),
      "method_id":first.get("method_id"),"market":market,"selection":first.get("selection"),"selection_team_id":first.get("selection_team_id"),
      "first_qualified_at":first.get("scanned_at_utc"),"last_qualified_at":lastq.get("scanned_at_utc"),
      "last_pretip_scan_at":close.get("scanned_at_utc"),"survived_to_close":bool(closeq),
      "qualified_scan_count":len(qualified),"pretip_scan_count":len(pre),
      "first_price":first_price,"last_qualified_price":n(lastq.get("price")),"close_qualified_price":close_price,
      "first_line":n(first.get("line")),"last_qualified_line":n(lastq.get("line")),"settlement_line":used_line,
      "market_source_first":first.get("market_source"),"market_source_close":close.get("market_source"),
      "home_score":g["home_score"],"away_score":g["away_score"],"actual_total":actual,
      "outcome":outcome,"unit_profit_at_last_qualified_price":profit(n(lastq.get("price")),outcome),
      "unit_profit_if_survived_to_close":profit(close_price,outcome) if closeq else None
    })
append_jsonl(OUT,new)
latest={"settled_at_utc":now.isoformat(),"final_games_seen":len(finals),"evaluation_keys":len(bykey),"new_settlements":len(new),"total_settlements":len(existing)+len(new)}
(SC/"settlement_latest.json").write_text(json.dumps(latest,indent=2)+"\n")
print(json.dumps(latest,indent=2))
