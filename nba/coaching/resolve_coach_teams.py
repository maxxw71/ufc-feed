#!/usr/bin/env python3
import csv,gzip,json,re,time
from datetime import datetime,timezone
from pathlib import Path
import requests

NBA=Path(__file__).resolve().parents[1]
COACH=NBA/"coaching"
SRC=COACH/"historical_coaches.csv.gz"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}
SEASONS={
 "2018-19":2019,"2019-20":2020,"2020-21":2021,"2021-22":2022,
 "2022-23":2023,"2023-24":2024,"2024-25":2025,"2025-26":2026,
}
TEAM_URL="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/{year}/teams"

def read_gz(path):
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

def write_gz(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def get(url,params=None):
    try:
        r=requests.get(url,params=params,headers=HEADERS,timeout=30);r.raise_for_status();return r.json()
    except:return None

def refs(obj,kind):
    out=[]
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if k=="$ref" and f"/{kind}/" in str(v):out.append(str(v))
                else:walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(obj);return out

def id_from(ref,kind):
    m=re.search(rf"/{kind}/(\d+)",str(ref));return m.group(1) if m else ""

rows=read_gz(SRC); by={(r["season"],r["coach_id"]):r for r in rows}
diag={}; added=0
for season,year in SEASONS.items():
    coll=get(TEAM_URL.format(year=year),{"limit":1000}) or {}
    items=coll.get("items") or []
    coach_links=0
    for item in items:
        ref=item.get("$ref") if isinstance(item,dict) else None
        obj=get(ref) if ref else item
        obj=obj or item
        team_refs=refs(item,"teams")+refs(obj,"teams")
        team_id=id_from(team_refs[0],"teams") if team_refs else id_from(ref,"teams")
        coach_refs=refs(item,"coaches")+refs(obj,"coaches")
        seen=set()
        for cr in coach_refs:
            cid=id_from(cr,"coaches")
            if not cid or cid in seen:continue
            seen.add(cid);coach_links+=1
            key=(season,cid)
            if key in by and not by[key].get("team_id") and team_id:
                by[key]["team_id"]=team_id
                by[key]["team_assignment_source"]="season_team_endpoint"
                added+=1
    diag[season]={"team_items":len(items),"coach_links_seen":coach_links}

out=list(by.values())
for r in out:
    if r.get("team_id") and not r.get("team_assignment_source"):
        r["team_assignment_source"]="season_coach_endpoint"
write_gz(COACH/"historical_coaches.csv.gz",out)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"team_endpoint_diagnostics":diag,
 "assignments_added":added,
 "team_assignment_coverage":{
   s:sum(1 for r in out if r["season"]==s and r.get("team_id"))/max(1,sum(1 for r in out if r["season"]==s))
   for s in SEASONS
 }
}
(COACH/"team_resolution_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
