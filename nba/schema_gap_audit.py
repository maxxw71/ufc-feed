#!/usr/bin/env python3
import csv,gzip,json
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent
DATA=NBA/"data"
OUT=NBA/"research"/"gap_audit"
OUT.mkdir(parents=True,exist_ok=True)

def header(p):
    if not p.exists(): return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:
        r=csv.reader(f)
        return next(r,[])

def sample_rows(p,n=25):
    if not p.exists(): return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        out=[]
        for i,row in enumerate(r):
            out.append(row)
            if i+1>=n: break
        return out

kinds={"games":"games.csv.gz","team_boxscores":"team_boxscores.csv.gz","player_boxscores":"player_boxscores.csv.gz","playbyplay":"playbyplay.csv.gz"}
schemas=defaultdict(Counter)
examples={}
files_seen=defaultdict(int)
for base in sorted(DATA.glob("*_*/regular_season")):
    for kind,name in kinds.items():
        p=base/name
        if not p.exists(): continue
        h=header(p)
        schemas[kind].update(h);files_seen[kind]+=1
        if kind not in examples:
            examples[kind]=sample_rows(p,5)

keywords=["official","referee","crew","starter","start","position","coordinate","x","y","shot","distance","venue","arena","city","state","attendance","lead","clock","period","type","text","play","team","athlete","person"]
keyword_hits={}
for kind,c in schemas.items():
    fs=sorted(c)
    keyword_hits[kind]={kw:[f for f in fs if kw.lower() in f.lower()] for kw in keywords}
    keyword_hits[kind]={k:v for k,v in keyword_hits[kind].items() if v}

report={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "files_seen":dict(files_seen),
 "schemas":{k:sorted(v) for k,v in schemas.items()},
 "keyword_hits":keyword_hits,
 "sample_nonempty_values":{
   kind:{field:next((r.get(field) for r in rows if r.get(field) not in (None,"")),None) for field in sorted(schemas[kind])}
   for kind,rows in examples.items()
 },
 "current_feature_gaps":[
   "historical officials/referee assignments",
   "travel distance/time-zone/altitude context",
   "historical injury/availability status",
   "exact in-season coaching change dates",
   "starter-vs-bench/rotation role production",
   "shot-location/rim/midrange/corner-three profile if raw coordinates/types allow",
   "clutch scoring/efficiency rather than clutch action count only",
   "quarter/half team rolling splits beyond current halftime/second-half margins",
   "roster transactions/trades and days-since-acquisition",
   "market microstructure: book disagreement, open-to-close dispersion and stale-price detection"
 ]
}
(OUT/"raw_schema_gap_audit.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"files_seen":report["files_seen"],"keyword_hits":keyword_hits},indent=2))
