#!/usr/bin/env python3
import csv,gzip,json
from collections import Counter
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"availability"/"availability_snapshots.csv.gz"

with gzip.open(P,"rt",encoding="utf-8",newline="") as f:
    rows=list(csv.DictReader(f))

latest=max((r.get("captured_at_utc") or "" for r in rows),default="")
rows=[r for r in rows if r.get("captured_at_utc")==latest]
fields=["team_id","team_abbreviation","team_name","person_id","player_name","status","injury_type","detail"]
report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "capture":latest,"rows":len(rows),
 "nonempty":{k:sum(1 for r in rows if r.get(k)) for k in fields},
 "distinct_team_ids":sorted(set(r.get("team_id") for r in rows if r.get("team_id"))),
 "distinct_team_abbrs":sorted(set(r.get("team_abbreviation") for r in rows if r.get("team_abbreviation"))),
 "status_counts":Counter(r.get("status") or "<blank>" for r in rows),
 "sample":[{k:r.get(k) for k in fields} for r in rows[:12]]
}
out=ROOT/"availability"/"diagnostic.json"
out.write_text(json.dumps(report,indent=2,default=lambda x:dict(x))+"\n")
print(json.dumps(report,indent=2,default=lambda x:dict(x)))
