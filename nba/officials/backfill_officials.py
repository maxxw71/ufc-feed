#!/usr/bin/env python3
import argparse,csv,gzip,json,time,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";OUT=NBA/"officials";OUT.mkdir(parents=True,exist_ok=True)

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

def fetch(g):
    gid=g["game_id"];url=f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 Appwiza-NBA-research"})
            with urllib.request.urlopen(req,timeout=20) as r:data=json.load(r)
            gi=data.get("gameInfo") or {};venue=gi.get("venue") or {};addr=venue.get("address") or {}
            offs=gi.get("officials") or []
            return {
              "game_id":gid,"season":g.get("season"),"game_date":g.get("game_date"),
              "venue_id":venue.get("id"),"venue_name":venue.get("fullName") or venue.get("shortName"),
              "venue_city":addr.get("city"),"venue_state":addr.get("state"),"attendance":gi.get("attendance"),
              "officials":[{"full_name":o.get("fullName") or o.get("displayName"),"order":o.get("order"),
                            "position":(o.get("position") or {}).get("name")} for o in offs if (o.get("fullName") or o.get("displayName"))],
              "ok":True
            }
        except Exception as e:
            last=str(e);time.sleep(0.4*(attempt+1))
    return {"game_id":gid,"season":g.get("season"),"game_date":g.get("game_date"),"ok":False,"error":last}

ap=argparse.ArgumentParser();ap.add_argument("--workers",type=int,default=6);args=ap.parse_args()
games=[]
for p in sorted(DATA.glob("*_*/regular_season/games.csv.gz")):
    for g in rgz(p):
        if g.get("season") in {"2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"} and str(g.get("completed")).lower() in ("true","1") and g.get("game_id"):
            games.append(g)

results=[]
with ThreadPoolExecutor(max_workers=args.workers) as ex:
    futs=[ex.submit(fetch,g) for g in games]
    for i,f in enumerate(as_completed(futs),1):
        results.append(f.result())
        if i%500==0:print(f"fetched {i}/{len(games)}",flush=True)

results.sort(key=lambda r:(r.get("game_date") or "",r.get("game_id") or ""))
game_rows=[];official_rows=[]
for r in results:
    game_rows.append({k:v for k,v in r.items() if k!="officials"})
    for o in r.get("officials") or []:
        official_rows.append({"season":r.get("season"),"game_id":r.get("game_id"),"game_date":r.get("game_date"),
                              "official_name":o.get("full_name"),"official_order":o.get("order"),"official_position":o.get("position")})
wgz(OUT/"historical_game_context.csv.gz",game_rows)
wgz(OUT/"historical_game_officials.csv.gz",official_rows)
byseason={}
for s in sorted(set(r.get("season") for r in results if r.get("season"))):
    rr=[r for r in results if r.get("season")==s]
    byseason[s]={"games":len(rr),"success":sum(1 for r in rr if r.get("ok")),"games_with_officials":sum(1 for r in rr if r.get("officials")),
                 "official_rows":sum(len(r.get("officials") or []) for r in rr)}
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"games_attempted":len(results),
         "success":sum(1 for r in results if r.get("ok")),"games_with_officials":sum(1 for r in results if r.get("officials")),
         "official_rows":len(official_rows),"by_season":byseason,
         "source":"ESPN game summary gameInfo.officials/gameInfo.venue","workers":args.workers}
(OUT/"historical_officials_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
