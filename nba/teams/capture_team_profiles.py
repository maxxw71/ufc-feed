#!/usr/bin/env python3
import csv,gzip,json
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"teams"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def write(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def main():
    stamp=datetime.now(timezone.utc).isoformat()
    rows=[]; raw={}; failures=[]
    for i in range(1,31):
        url=URL.format(team_id=i)
        try:
            r=requests.get(url,headers=HEADERS,timeout=30); r.raise_for_status()
            payload=r.json(); raw[str(i)]=payload
            t=payload.get("team") or {}
            venue=t.get("venue") or {}
            addr=venue.get("address") or {}
            coach=t.get("coach") or {}
            if not coach:
                coaches=t.get("coaches") or []
                coach=coaches[0] if coaches else {}
            rows.append({
              "captured_at_utc":stamp,"team_id":str(t.get("id") or i),"team":t.get("displayName"),
              "team_tricode":t.get("abbreviation"),"location":t.get("location"),"nickname":t.get("name"),
              "head_coach_id":str(coach.get("id") or ""),"head_coach":coach.get("displayName") or coach.get("name"),
              "venue":venue.get("fullName"),"venue_city":addr.get("city"),"venue_state":addr.get("state"),
              "venue_country":addr.get("country"),"color":t.get("color"),"alternate_color":t.get("alternateColor"),
              "source":"espn_team_profile","source_url":url
            })
        except Exception as e:
            failures.append({"team_id":i,"error":str(e)})
    write(OUT/"current_team_profiles.csv.gz",rows)
    d=OUT/"raw"/stamp[:10]; d.mkdir(parents=True,exist_ok=True)
    p=d/(stamp[11:19].replace(":","")+"Z.json.gz")
    with gzip.open(p,"wt",encoding="utf-8") as f: json.dump(raw,f,separators=(",",":"))
    meta={"captured_at_utc":stamp,"rows":len(rows),"failures":failures,"raw_snapshot":str(p.relative_to(ROOT))}
    (OUT/"latest.json").write_text(json.dumps(meta,indent=2)+"\n")
    print(json.dumps(meta,indent=2))

if __name__=="__main__": main()
