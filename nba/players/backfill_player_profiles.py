#!/usr/bin/env python3
import csv,gzip,json,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
OUT=ROOT/"players"
URL="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/athletes/{player_id}"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

ids=set()
name_hint={}
for p in DATA.glob("*/*/player_boxscores.csv.gz"):
    for r in read(p):
        pid=str(r.get("person_id") or "")
        if pid:
            ids.add(pid); name_hint[pid]=r.get("player_name")

def fetch(pid):
    url=URL.format(player_id=pid)
    try:
        r=requests.get(url,headers=HEADERS,timeout=25); status=r.status_code; r.raise_for_status()
        a=r.json()
        pos=a.get("position") or {}; exp=a.get("experience") or {}; college=a.get("college") or {}
        return {
          "person_id":pid,"player_name":a.get("displayName") or a.get("fullName") or name_hint.get(pid),
          "first_name":a.get("firstName"),"last_name":a.get("lastName"),
          "date_of_birth":a.get("dateOfBirth"),"age_current":a.get("age"),
          "height":a.get("displayHeight"),"height_inches":a.get("height"),
          "weight_lbs":a.get("weight"),"position":pos.get("name"),"position_abbr":pos.get("abbreviation"),
          "experience_years":exp.get("years"),"college":college.get("name"),
          "debut_year":a.get("debutYear"),"active_current":a.get("active"),
          "source":"espn_core_athlete","source_url":url,"http_status":status
        },None
    except Exception as e:
        return None,{"person_id":pid,"error":str(e)}

rows=[];fail=[]
with ThreadPoolExecutor(max_workers=10) as ex:
    futs={ex.submit(fetch,pid):pid for pid in sorted(ids)}
    for i,f in enumerate(as_completed(futs),1):
        row,err=f.result()
        if row: rows.append(row)
        if err: fail.append(err)
        if i%200==0: print(f"profiles {i}/{len(ids)}",flush=True)

write(OUT/"historical_player_profiles.csv.gz",rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"unique_player_ids":len(ids),
 "profiles":len(rows),"failures":len(fail),"failure_examples":fail[:50],
 "height_coverage":sum(1 for r in rows if r.get("height") or r.get("height_inches"))/len(rows) if rows else 0,
 "weight_coverage":sum(1 for r in rows if r.get("weight_lbs") not in (None,""))/len(rows) if rows else 0,
 "dob_coverage":sum(1 for r in rows if r.get("date_of_birth"))/len(rows) if rows else 0,
 "position_coverage":sum(1 for r in rows if r.get("position_abbr"))/len(rows) if rows else 0
}
(OUT/"historical_player_profiles_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
