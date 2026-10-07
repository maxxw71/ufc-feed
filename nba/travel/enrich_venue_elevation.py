#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];P=NBA/"travel"/"venue_geocode_cache.json";OUT=NBA/"travel"
data=json.loads(P.read_text())
ok=0
for vid,v in data.items():
    if v.get("elevation_m") is not None:
        ok+=1;continue
    lat=v.get("latitude");lon=v.get("longitude")
    if lat is None or lon is None:continue
    url="https://api.open-meteo.com/v1/forecast?"+urllib.parse.urlencode({
      "latitude":lat,"longitude":lon,"forecast_days":1,"hourly":"temperature_2m"
    })
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-NBA-research/1.0"})
        with urllib.request.urlopen(req,timeout=20) as r:resp=json.load(r)
        elev=resp.get("elevation")
        if elev is not None:
            v["elevation_m"]=float(elev);v["elevation_ft"]=float(elev)*3.280839895;v["elevation_source"]="Open-Meteo forecast metadata";ok+=1
    except Exception as e:
        v["elevation_error"]=str(e)
    time.sleep(0.15)
P.write_text(json.dumps(data,indent=2)+"\n")
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"venues":len(data),"with_elevation":ok,
         "source":"Open-Meteo forecast metadata at cached venue coordinates"}
(OUT/"venue_elevation_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
