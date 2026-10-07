#!/usr/bin/env python3
import csv,gzip,json,urllib.request
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent;DATA=NBA/"data";OUT=NBA/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def walk(obj,prefix=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            q=f"{prefix}.{k}" if prefix else k
            if any(tok in k.lower() for tok in ("injur","inactive","availability","status","roster")):
                if isinstance(v,(str,int,float,bool,type(None))):sample=v
                elif isinstance(v,list):sample={"type":"list","len":len(v),"first":v[0] if v else None}
                else:sample={"type":"dict","keys":list(v)[:20]}
                out.append({"path":q,"sample":sample})
            out.extend(walk(v,q))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:12]):out.extend(walk(v,f"{prefix}[{i}]"))
    return out

samples=[]
for p in sorted(DATA.glob("*_*/regular_season/games.csv.gz")):
    rows=[r for r in rgz(p) if str(r.get("completed")).lower() in ("true","1")]
    if rows:
        for idx in (len(rows)//4,len(rows)//2,3*len(rows)//4):
            g=rows[idx]
            gid=g["game_id"];url=f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
            try:
                req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 Appwiza-NBA-research"})
                with urllib.request.urlopen(req,timeout=20) as resp:data=json.load(resp)
                hits=walk(data)
                samples.append({"season":g.get("season"),"game_id":gid,"hits":hits})
            except Exception as e:samples.append({"season":g.get("season"),"game_id":gid,"error":str(e)})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"samples":samples,
        "games_with_injury_like_hits":sum(1 for r in samples if any("injur" in h["path"].lower() or "inactive" in h["path"].lower() for h in r.get("hits",[])))}
(OUT/"historical_availability_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"samples":len(samples),"games_with_injury_like_hits":report["games_with_injury_like_hits"]},indent=2))
