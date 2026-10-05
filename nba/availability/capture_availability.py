#!/usr/bin/env python3
import csv, gzip, json, re
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"availability"
RAW=OUT/"raw"
ROSTERS=ROOT/"rosters"/"current_roster_profiles.csv.gz"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def now():
    return datetime.now(timezone.utc)

def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and d.get(k) not in (None,""):
            return d.get(k)
    return None

def norm_name(v):
    s=(v or "").lower()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return " ".join(s.split())

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def roster_indexes():
    rows=read_gz(ROSTERS)
    teams={}
    by_name={}
    for r in rows:
        tri=r.get("team_tricode")
        if tri:
            teams.setdefault(tri,{
                "team_id":r.get("team_id"),
                "team_abbreviation":tri,
                "team_name":r.get("team")
            })
        name=norm_name(r.get("player_name"))
        if name:
            by_name[name]=r
    return teams,by_name

def injury_items(payload):
    groups=payload.get("injuries") or []
    if isinstance(groups,dict): groups=[groups]
    out=[]
    for group in groups:
        nested=group.get("injuries") or group.get("items") or []
        if nested:
            for item in nested:
                out.append((group,item))
        elif any(k in group for k in ("athlete","status","type","name","playerName")):
            out.append(({},group))
    return out

def flatten_team(payload,captured,forced_team,roster_by_name,source_url):
    rows=[]
    for group,item in injury_items(payload):
        ath=item.get("athlete") or {}
        group_team=group.get("team") or {}
        item_team=item.get("team") or {}
        athlete_team=ath.get("team") or {}
        team={}
        for candidate in (item_team,group_team,athlete_team,forced_team):
            if isinstance(candidate,dict) and candidate:
                team={**team,**{k:v for k,v in candidate.items() if v not in (None,"")}}

        status=item.get("status")
        status_text=first(status,"name","description","type","displayName") if isinstance(status,dict) else status
        typ=item.get("type")
        type_text=first(typ,"name","description","type","displayName") if isinstance(typ,dict) else typ
        details=item.get("details")
        detail_text=first(details,"detail","description","type","fantasyStatus","returnDate") if isinstance(details,dict) else details
        if not detail_text:
            detail_text=first(item,"detail","location")

        player_name=first(ath,"displayName","fullName","shortName") or first(item,"name","playerName")
        person_id=str(first(ath,"id") or first(item,"athleteId","playerId") or "")
        rr=roster_by_name.get(norm_name(player_name)) or {}
        if not person_id:
            person_id=str(rr.get("person_id") or "")
        if not team.get("id") and rr.get("team_id"):
            team["id"]=rr.get("team_id")
        if not team.get("abbreviation") and rr.get("team_tricode"):
            team["abbreviation"]=rr.get("team_tricode")
        if not team.get("displayName") and rr.get("team"):
            team["displayName"]=rr.get("team")

        rows.append({
            "captured_at_utc":captured,
            "team_id":str(first(team,"id") or forced_team.get("team_id") or ""),
            "team_abbreviation":first(team,"abbreviation","shortDisplayName") or forced_team.get("team_abbreviation"),
            "team_name":first(team,"displayName","name") or forced_team.get("team_name"),
            "person_id":person_id,
            "player_name":player_name,
            "status":status_text,
            "injury_type":type_text,
            "detail":detail_text,
            "injury_date":first(item,"date"),
            "source":"espn_injuries_team_scoped",
            "source_url":source_url
        })
    return rows

def write_gz_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=["captured_at_utc","team_id","team_abbreviation","team_name","person_id","player_name",
            "status","injury_type","detail","injury_date","source","source_url"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    t=now(); captured=t.isoformat()
    teams,roster_by_name=roster_indexes()
    if not teams:
        raise RuntimeError("No roster team index available; refusing identity-less injury capture.")

    raw_bundle={"captured_at_utc":captured,"teams":{}}
    new_rows=[]; requests_meta=[]
    for tri in sorted(teams):
        forced=teams[tri]
        try:
            r=requests.get(URL,params={"team":tri},headers=HEADERS,timeout=30)
            r.raise_for_status()
            payload=r.json()
            raw_bundle["teams"][tri]=payload
            source_url=f"{URL}?team={tri}"
            parsed=flatten_team(payload,captured,forced,roster_by_name,source_url)
            new_rows.extend(parsed)
            requests_meta.append({"team":tri,"http_status":r.status_code,"ok":True,"rows":len(parsed)})
        except Exception as e:
            requests_meta.append({"team":tri,"http_status":None,"ok":False,"rows":0,"error":str(e)})

    # Deduplicate in case ESPN returns overlapping team groups despite team= filtering.
    dedup={}
    for r in new_rows:
        key=(r.get("team_id"),r.get("person_id") or norm_name(r.get("player_name")),
             r.get("status"),r.get("injury_date"),r.get("detail"))
        dedup[key]=r
    new_rows=list(dedup.values())

    raw_dir=RAW/t.strftime("%Y-%m-%d"); raw_dir.mkdir(parents=True,exist_ok=True)
    raw_path=raw_dir/(t.strftime("%H%M%SZ")+"_team_scoped.json.gz")
    with gzip.open(raw_path,"wt",encoding="utf-8") as f:
        json.dump(raw_bundle,f,separators=(",",":"))

    hist=OUT/"availability_snapshots.csv.gz"
    rows=read_gz(hist)+new_rows
    write_gz_csv(hist,rows)

    latest={
        "captured_at_utc":captured,
        "source":"espn_injuries_team_scoped",
        "source_url":URL,
        "teams_attempted":len(teams),
        "teams_success":sum(1 for x in requests_meta if x["ok"]),
        "rows":len(new_rows),
        "rows_with_team_id":sum(1 for x in new_rows if x.get("team_id")),
        "rows_with_person_id":sum(1 for x in new_rows if x.get("person_id")),
        "unique_teams_with_injuries":len(set(x.get("team_id") for x in new_rows if x.get("team_id"))),
        "raw_snapshot":str(raw_path.relative_to(ROOT)),
        "requests":requests_meta,
        "notes":"Prospective point-in-time team-scoped capture; roster identity reconciliation applied at capture time."
    }
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps({k:v for k,v in latest.items() if k!="requests"},indent=2))

if __name__=="__main__":
    main()
