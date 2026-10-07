#!/usr/bin/env python3
import csv,gzip,json,math
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";CTX=NBA/"officials"/"historical_game_context.csv.gz";CAT=NBA/"travel"/"venue_geocode_cache.json"
OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return None
def hav(lat1,lon1,lat2,lon2):
    R=3958.7613
    a1,a2=math.radians(lat1),math.radians(lat2)
    da=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    a=math.sin(da/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))
def offset_hours(tzname,when):
    if not tzname or not when:return None
    try:return when.astimezone(ZoneInfo(tzname)).utcoffset().total_seconds()/3600
    except:return None

venue_by_game={r.get("game_id"):r for r in rgz(CTX) if r.get("game_id")}
catalog=json.loads(CAT.read_text()) if CAT.exists() else {}

games=[]
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    games.extend(rgz(p))

team_games=defaultdict(list)
for g in games:
    when=dt(g.get("game_date"));vc=venue_by_game.get(g.get("game_id"))
    if not when or not vc:continue
    v=catalog.get(str(vc.get("venue_id"))) or {}
    lat=v.get("latitude");lon=v.get("longitude");tz=v.get("timezone")
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id")
        if not tid:continue
        team_games[(g.get("season"),tid)].append({
          "season":g.get("season"),"game_id":g.get("game_id"),"game_date":g.get("game_date"),"when":when,
          "team_id":tid,"is_home":home,"venue_id":vc.get("venue_id"),"venue_name":vc.get("venue_name"),
          "venue_city":vc.get("venue_city"),"venue_state":vc.get("venue_state"),
          "lat":lat,"lon":lon,"timezone":tz,"elevation_ft":v.get("elevation_ft"),
          "completed":str(g.get("completed")).lower() in ("true","1")
        })

rows=[]
for (season,tid),arr in team_games.items():
    arr.sort(key=lambda x:x["when"]);hist=[]
    for x in arr:
        prev=hist[-1] if hist else None
        miles=None;shift=None
        if prev and None not in (prev.get("lat"),prev.get("lon"),x.get("lat"),x.get("lon")):
            miles=hav(float(prev["lat"]),float(prev["lon"]),float(x["lat"]),float(x["lon"]))
        if prev:
            po=offset_hours(prev.get("timezone"),x["when"]);co=offset_hours(x.get("timezone"),x["when"])
            if po is not None and co is not None:shift=co-po

        # Historical travel segments ending at prior games plus the known current segment.
        segments=[]
        for i in range(1,len(hist)):
            a=hist[i-1];b=hist[i]
            if None not in (a.get("lat"),a.get("lon"),b.get("lat"),b.get("lon")):
                segments.append((b["when"],hav(float(a["lat"]),float(a["lon"]),float(b["lat"]),float(b["lon"]))))
        if miles is not None:segments.append((x["when"],miles))
        cur_elev=x.get("elevation_ft")
        prev_elev=prev.get("elevation_ft") if prev else None
        elev_gain=(float(cur_elev)-float(prev_elev)) if cur_elev is not None and prev_elev is not None else None
        row={
          "season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,
          "is_home":x["is_home"],"venue_id":x["venue_id"],"venue_timezone":x.get("timezone"),
          "venue_elevation_ft":cur_elev,"prev_venue_elevation_ft":prev_elev,
          "elevation_gain_ft_from_prev":elev_gain,
          "high_altitude_4000plus":bool(float(cur_elev)>=4000) if cur_elev is not None else None,
          "elevation_gain_2500plus":bool(elev_gain>=2500) if elev_gain is not None else None,
          "travel_miles_from_prev":miles,
          "timezone_shift_hours":shift,
          "eastward_shift_hours":max(0.0,shift) if shift is not None else None,
          "westward_shift_hours":max(0.0,-shift) if shift is not None else None,
          "crossed_2plus_timezones":bool(abs(shift)>=2) if shift is not None else None,
          "long_trip_750plus":bool(miles>=750) if miles is not None else None,
          "long_trip_1500plus":bool(miles>=1500) if miles is not None else None,
          "pregame_only_feature":True
        }
        for days in (3,5,7,10):
            cutoff=x["when"]-timedelta(days=days)
            vals=[m for end,m in segments if cutoff<=end<=x["when"]]
            row[f"travel_miles_prev_{days}d_including_arrival"]=sum(vals) if vals else (0.0 if prev else None)
        if prev:
            row["prev_venue_same"]=prev.get("venue_id")==x.get("venue_id")
            row["prev_was_home"]=prev.get("is_home")
        rows.append(row)
        if x["completed"]:hist.append(x)

wgz(OUT/"travel_pregame.csv.gz",rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"team_seasons":len(team_games),
 "distance_coverage":sum(1 for r in rows if r.get("travel_miles_from_prev") is not None)/len(rows) if rows else 0,
 "timezone_coverage":sum(1 for r in rows if r.get("timezone_shift_hours") is not None)/len(rows) if rows else 0,
 "elevation_coverage":sum(1 for r in rows if r.get("venue_elevation_ft") is not None)/len(rows) if rows else 0,
 "policy":"Travel uses actual prior completed-game venue to target-game venue. Current target location/elevation is known pregame; no target outcome data are used."
}
(OUT/"travel_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
