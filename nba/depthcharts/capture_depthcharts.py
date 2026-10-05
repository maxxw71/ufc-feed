#!/usr/bin/env python3
import gzip,json
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"depthcharts"
URL="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/depthcharts"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"}

def main():
    t=datetime.now(timezone.utc); stamp=t.isoformat()
    bundle={"captured_at_utc":stamp,"teams":{}}
    meta=[]
    for team_id in range(1,31):
        url=URL.format(team_id=team_id)
        try:
            r=requests.get(url,headers=HEADERS,timeout=30)
            status=r.status_code
            r.raise_for_status()
            payload=r.json()
            bundle["teams"][str(team_id)]=payload
            meta.append({
              "team_id":team_id,"ok":True,"http_status":status,
              "top_level_keys":sorted(payload.keys()) if isinstance(payload,dict) else [],
              "payload_size_chars":len(json.dumps(payload,separators=(",",":")))
            })
        except Exception as e:
            meta.append({"team_id":team_id,"ok":False,"http_status":locals().get("status"),"error":str(e)})
    d=OUT/"raw"/t.strftime("%Y-%m-%d"); d.mkdir(parents=True,exist_ok=True)
    p=d/(t.strftime("%H%M%SZ")+".json.gz")
    with gzip.open(p,"wt",encoding="utf-8") as f: json.dump(bundle,f,separators=(",",":"))
    latest={
      "captured_at_utc":stamp,"teams_attempted":30,"teams_success":sum(1 for x in meta if x.get("ok")),
      "raw_snapshot":str(p.relative_to(ROOT)),"requests":meta,
      "notes":"Raw point-in-time depth-chart preservation; normalized role parser intentionally versioned separately."
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps({k:v for k,v in latest.items() if k!="requests"},indent=2))

if __name__=="__main__": main()
