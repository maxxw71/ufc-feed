#!/usr/bin/env python3
import csv,gzip,json
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"rosters"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}
TEAM_IDS=[str(i) for i in range(1,31)]

def now(): return datetime.now(timezone.utc)

def write(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def main():
    t=now(); captured=t.isoformat()
    rows=[]; provenance=[]
    raw={}
    for team_id in TEAM_IDS:
        url=URL.format(team_id=team_id)
        try:
            r=requests.get(url,headers=HEADERS,timeout=30)
            status=r.status_code
            r.raise_for_status()
            payload=r.json()
            raw[team_id]=payload
            team=payload.get("team") or {}
            athletes=payload.get("athletes") or []
            for group in athletes:
                items=group.get("items") if isinstance(group,dict) else None
                if items is None:
                    items=[group] if isinstance(group,dict) else []
                for a in items:
                    pos=a.get("position") or {}
                    exp=a.get("experience") or {}
                    college=a.get("college") or {}
                    status_obj=a.get("status") or {}
                    rows.append({
                      "captured_at_utc":captured,
                      "team_id":str(team.get("id") or team_id),"team":team.get("displayName"),
                      "team_tricode":team.get("abbreviation"),
                      "person_id":str(a.get("id") or ""),"player_name":a.get("displayName") or a.get("fullName"),
                      "first_name":a.get("firstName"),"last_name":a.get("lastName"),
                      "position":pos.get("name"),"position_abbr":pos.get("abbreviation"),
                      "height":a.get("displayHeight"),"weight_lbs":a.get("weight"),
                      "date_of_birth":a.get("dateOfBirth"),"age":a.get("age"),
                      "jersey":a.get("jersey"),"experience_years":exp.get("years"),
                      "college":college.get("name"),"active":a.get("active"),
                      "status":status_obj.get("name") or status_obj.get("type") if isinstance(status_obj,dict) else status_obj,
                      "source":"espn_roster","source_url":url
                    })
            provenance.append({"team_id":team_id,"http_status":status,"ok":True,"url":url})
        except Exception as e:
            provenance.append({"team_id":team_id,"http_status":locals().get("status"),"ok":False,"url":url,"error":str(e)})

    write(OUT/"current_roster_profiles.csv.gz",rows)
    raw_dir=OUT/"raw"/t.strftime("%Y-%m-%d")
    raw_dir.mkdir(parents=True,exist_ok=True)
    raw_path=raw_dir/(t.strftime("%H%M%SZ")+".json.gz")
    with gzip.open(raw_path,"wt",encoding="utf-8") as f: json.dump(raw,f,separators=(",",":"))
    latest={
      "captured_at_utc":captured,"teams_attempted":30,
      "teams_success":sum(1 for p in provenance if p.get("ok")),
      "player_rows":len(rows),"unique_players":len(set(r["person_id"] for r in rows if r["person_id"])),
      "raw_snapshot":str(raw_path.relative_to(ROOT)),"provenance":provenance
    }
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps({k:v for k,v in latest.items() if k!="provenance"},indent=2))

if __name__=="__main__":
    main()
