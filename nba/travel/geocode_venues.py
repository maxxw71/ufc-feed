#!/usr/bin/env python3
import csv,gzip,json,time,urllib.parse,urllib.request
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
SRC=NBA/"officials"/"historical_game_context.csv.gz"
OUT=NBA/"travel";OUT.mkdir(parents=True,exist_ok=True)
CACHE=OUT/"venue_geocode_cache.json"

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

rows=rgz(SRC)
venues={}
for r in rows:
    vid=r.get("venue_id")
    if not vid:continue
    venues[vid]={
      "venue_id":vid,"venue_name":r.get("venue_name"),"venue_city":r.get("venue_city"),"venue_state":r.get("venue_state")
    }

cache={}
if CACHE.exists():
    try:cache=json.loads(CACHE.read_text())
    except:cache={}

UA="Appwiza-NBA-research/1.0 (https://appwiza.com)"
def query(q):
    url="https://nominatim.openstreetmap.org/search?"+urllib.parse.urlencode({"q":q,"format":"jsonv2","limit":1,"addressdetails":1})
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=25) as resp:
        data=json.load(resp)
    return data[0] if data else None

for i,(vid,v) in enumerate(sorted(venues.items()),1):
    if vid in cache and cache[vid].get("latitude") is not None:
        continue
    queries=[
      ", ".join(x for x in [v.get("venue_name"),v.get("venue_city"),v.get("venue_state")] if x),
      ", ".join(x for x in [v.get("venue_city"),v.get("venue_state")] if x)
    ]
    hit=None
    for q in queries:
        try:
            hit=query(q)
            if hit:break
        except Exception:
            hit=None
        time.sleep(1.1)
    cache[vid]={**v,
      "latitude":float(hit["lat"]) if hit else None,
      "longitude":float(hit["lon"]) if hit else None,
      "geocode_display_name":hit.get("display_name") if hit else None,
      "geocode_type":hit.get("type") if hit else None,
      "geocode_source":"OpenStreetMap Nominatim" if hit else None,
      "geocoded_at_utc":datetime.now(timezone.utc).isoformat()
    }
    CACHE.write_text(json.dumps(cache,indent=2)+"\n")
    time.sleep(1.1)

# Add timezone from local coordinate lookup when installed.
try:
    from timezonefinder import TimezoneFinder
    tf=TimezoneFinder()
    for v in cache.values():
        lat=v.get("latitude");lon=v.get("longitude")
        if lat is not None and lon is not None:
            v["timezone"]=tf.timezone_at(lat=lat,lng=lon)
except Exception as e:
    for v in cache.values():v.setdefault("timezone",None)

CACHE.write_text(json.dumps(cache,indent=2)+"\n")
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "unique_venues":len(venues),
 "geocoded":sum(1 for v in cache.values() if v.get("latitude") is not None),
 "with_timezone":sum(1 for v in cache.values() if v.get("timezone")),
 "source":"OpenStreetMap Nominatim + timezonefinder",
 "policy":"Geocoded venue coordinates are cached and reused; Nominatim requests are rate-limited to approximately one per second."
}
(OUT/"venue_geocode_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
