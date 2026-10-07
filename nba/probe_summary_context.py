#!/usr/bin/env python3
import csv,gzip,json,urllib.request
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)

def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

samples=[]
for p in sorted(DATA.glob("*_*/regular_season/games.csv.gz")):
    rows=rgz(p)
    done=[r for r in rows if str(r.get("completed")).lower() in ("true","1") and r.get("game_id")]
    if done:samples.append(done[len(done)//2])

def paths(obj,prefix=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            q=f"{prefix}.{k}" if prefix else k
            if any(x in k.lower() for x in ("official","referee","coach","crew","venue","attendance")):
                out.append((q,v if isinstance(v,(str,int,float,bool,type(None))) else type(v).__name__))
            out.extend(paths(v,q))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:8]):
            out.extend(paths(v,f"{prefix}[{i}]"))
    return out

rows=[]
for g in samples:
    gid=g["game_id"];url=f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=20) as r:data=json.load(r)
        hits=paths(data)
        rows.append({"season":g.get("season"),"game_id":gid,"http_status":200,"hits":[{"path":p,"value":v} for p,v in hits]})
    except Exception as e:
        rows.append({"season":g.get("season"),"game_id":gid,"error":str(e)})

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"samples":rows,
        "officials_present":any(any("official" in h["path"].lower() or "referee" in h["path"].lower() for h in r.get("hits",[])) for r in rows),
        "coach_present":any(any("coach" in h["path"].lower() for h in r.get("hits",[])) for r in rows)}
(OUT/"espn_summary_context_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
