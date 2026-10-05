#!/usr/bin/env python3
import csv,gzip,json,re,time
from datetime import datetime,timezone
from pathlib import Path
import requests

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"coaching"
OUT.mkdir(parents=True,exist_ok=True)

HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}
BASE="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/{year}/coaches"
SEASONS={
 "2018-19":2019,"2019-20":2020,"2020-21":2021,"2021-22":2022,
 "2022-23":2023,"2023-24":2024,"2024-25":2025,"2025-26":2026,
}

def get_json(url,params=None,tries=3):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url,params=params,headers=HEADERS,timeout=30)
            r.raise_for_status()
            return r.json(),r.status_code,None
        except Exception as e:
            last=str(e); time.sleep(0.5*(i+1))
    return None,None,last

def coach_id_from_ref(ref):
    if not ref:return ""
    m=re.search(r"/coaches/(\d+)",str(ref))
    return m.group(1) if m else ""

def team_ids_from_obj(obj):
    refs=[]
    def walk(x,key_hint=""):
        if isinstance(x,dict):
            for k,v in x.items():
                if k=="$ref" and ("team" in key_hint.lower() or "/teams/" in str(v)):
                    refs.append(str(v))
                else:
                    walk(v,k)
        elif isinstance(x,list):
            for v in x: walk(v,key_hint)
    walk(obj)
    out=[]
    for ref in refs:
        m=re.search(r"/teams/(\d+)",ref)
        if m and m.group(1) not in out: out.append(m.group(1))
    return out

def write_gz(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]); w.writeheader(); w.writerows(rows)

rows=[]; summaries={}; raw={}
stamp=datetime.now(timezone.utc).isoformat()

for season,year in SEASONS.items():
    url=BASE.format(year=year)
    coll,status,err=get_json(url,{"limit":1000})
    items=(coll or {}).get("items") or []
    raw[season]={"collection":coll,"resolved":[]}
    resolved=0; with_team=0
    season_rows=[]
    for item in items:
        obj=item
        ref=item.get("$ref") if isinstance(item,dict) else None
        if ref:
            detail,_,_=get_json(ref)
            if detail:
                obj=detail; resolved+=1
        raw[season]["resolved"].append(obj)
        cid=str(obj.get("id") or coach_id_from_ref(ref) or "")
        name=obj.get("displayName") or obj.get("fullName") or " ".join(
            x for x in [obj.get("firstName"),obj.get("lastName")] if x
        ).strip()
        tids=team_ids_from_obj(item)+[x for x in team_ids_from_obj(obj) if x not in team_ids_from_obj(item)]
        if tids: with_team+=1
        if not tids:
            season_rows.append({
              "season":season,"espn_season_year":year,"coach_id":cid,"coach_name":name,
              "team_id":"","source_ref":ref or url,"captured_at_utc":stamp
            })
        else:
            for tid in tids:
                season_rows.append({
                  "season":season,"espn_season_year":year,"coach_id":cid,"coach_name":name,
                  "team_id":tid,"source_ref":ref or url,"captured_at_utc":stamp
                })
    rows.extend(season_rows)
    summaries[season]={
      "http_status":status,"collection_items":len(items),"resolved_items":resolved,
      "items_with_team_link":with_team,"normalized_rows":len(season_rows),"error":err
    }

write_gz(OUT/"historical_coaches.csv.gz",rows)
raw_path=OUT/"raw"
raw_path.mkdir(exist_ok=True)
with gzip.open(raw_path/"historical_coaches_raw.json.gz","wt",encoding="utf-8") as f:
    json.dump(raw,f,separators=(",",":"))

summary={
 "generated_at_utc":stamp,"seasons":summaries,"normalized_rows":len(rows),
 "policy":"Team assignment is normalized only when an ESPN season/coach payload exposes a team reference. No inferred assignments."
}
(OUT/"historical_coaches_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
