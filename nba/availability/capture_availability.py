#!/usr/bin/env python3
import csv, gzip, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"availability"
RAW=OUT/"raw"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def now():
    return datetime.now(timezone.utc)

def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and d.get(k) not in (None,""):
            return d.get(k)
    return None

def flatten(payload, captured):
    rows=[]
    groups=payload.get("injuries") or []
    if isinstance(groups,dict): groups=[groups]
    for group in groups:
        team=group.get("team") or {}
        items=group.get("injuries") or group.get("items") or []
        if not items and any(k in group for k in ("athlete","status","type")):
            items=[group]
        for item in items:
            ath=item.get("athlete") or {}
            status=item.get("status")
            if isinstance(status,dict):
                status_text=first(status,"name","description","type","displayName")
            else:
                status_text=status
            typ=item.get("type")
            if isinstance(typ,dict):
                type_text=first(typ,"name","description","type","displayName")
            else:
                type_text=typ
            details=item.get("details")
            if isinstance(details,dict):
                detail_text=first(details,"detail","description","type","fantasyStatus","returnDate")
            else:
                detail_text=details
            rows.append({
                "captured_at_utc":captured,
                "team_id":str(first(team,"id") or ""),
                "team_abbreviation":first(team,"abbreviation","shortDisplayName"),
                "team_name":first(team,"displayName","name"),
                "person_id":str(first(ath,"id") or first(item,"athleteId","playerId") or ""),
                "player_name":first(ath,"displayName","fullName","shortName") or first(item,"name","playerName"),
                "status":status_text,
                "injury_type":type_text,
                "detail":detail_text,
                "injury_date":first(item,"date"),
                "source":"espn_injuries",
                "source_url":URL
            })
    return rows

def read_existing(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_gz_csv(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=["captured_at_utc","team_id","team_abbreviation","team_name","person_id","player_name",
            "status","injury_type","detail","injury_date","source","source_url"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    t=now()
    captured=t.isoformat()
    r=requests.get(URL,headers=HEADERS,timeout=30)
    r.raise_for_status()
    payload=r.json()

    raw_dir=RAW/t.strftime("%Y-%m-%d")
    raw_dir.mkdir(parents=True,exist_ok=True)
    raw_path=raw_dir/(t.strftime("%H%M%SZ")+".json.gz")
    with gzip.open(raw_path,"wt",encoding="utf-8") as f:
        json.dump(payload,f,separators=(",",":"))

    new_rows=flatten(payload,captured)
    hist=OUT/"availability_snapshots.csv.gz"
    rows=read_existing(hist)+new_rows
    write_gz_csv(hist,rows)

    latest={
        "captured_at_utc":captured,
        "source":"espn_injuries",
        "source_url":URL,
        "http_status":r.status_code,
        "rows":len(new_rows),
        "raw_snapshot":str(raw_path.relative_to(ROOT)),
        "notes":"Prospective point-in-time capture. Raw payload retained for later parser upgrades."
    }
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps(latest,indent=2))

if __name__=="__main__":
    main()
