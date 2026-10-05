#!/usr/bin/env python3
import csv, gzip, json
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
SCHEDULE_URL="https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
HEADERS={"User-Agent":"Mozilla/5.0","Referer":"https://www.nba.com/","Accept":"application/json, text/plain, */*"}

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_gz(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader(); w.writerows(rows)

def ymd(v):
    if not v: return ""
    return str(v)[:10]

espn=[]
for path in [DATA/"2026_27"/"pre_season"/"games.csv.gz",DATA/"2026_27"/"regular_season"/"games.csv.gz"]:
    espn.extend(read_gz(path))
espn_by_key={}
for g in espn:
    key=(ymd(g.get("game_date")),g.get("away_tricode"),g.get("home_tricode"))
    if all(key): espn_by_key[key]=g

stamp=datetime.now(timezone.utc).isoformat()
report={"generated_at_utc":stamp,"schedule_url":SCHEDULE_URL,"espn_games":len(espn),"available":False}
rows=[]
try:
    r=requests.get(SCHEDULE_URL,headers=HEADERS,timeout=30)
    report["http_status"]=r.status_code
    r.raise_for_status()
    payload=r.json()
    report["available"]=True
    league=payload.get("leagueSchedule") or payload.get("schedule") or payload
    dates=league.get("gameDates") or []
    official_games=[]
    for d in dates:
        for g in d.get("games") or []:
            official_games.append(g)
    report["official_schedule_games"]=len(official_games)
    matched=0
    score_checks=0
    score_mismatches=0
    for g in official_games:
        home=g.get("homeTeam") or {}
        away=g.get("awayTeam") or {}
        dt=g.get("gameDateTimeUTC") or g.get("gameDateEst") or g.get("gameDate") or ""
        key=(ymd(dt),away.get("teamTricode"),home.get("teamTricode"))
        e=espn_by_key.get(key)
        if not e: continue
        matched+=1
        hs=home.get("score")
        aws=away.get("score")
        espn_h=e.get("home_score")
        espn_a=e.get("away_score")
        score_match=None
        if hs not in (None,"") and aws not in (None,"") and espn_h not in (None,"") and espn_a not in (None,""):
            score_checks+=1
            score_match=(str(hs)==str(espn_h) and str(aws)==str(espn_a))
            if not score_match: score_mismatches+=1
        rows.append({
            "espn_game_id":e.get("game_id"),"nba_game_id":g.get("gameId"),"game_date":ymd(dt),
            "away_tricode":away.get("teamTricode"),"home_tricode":home.get("teamTricode"),
            "nba_game_status":g.get("gameStatusText") or g.get("gameStatus"),
            "nba_away_score":aws,"nba_home_score":hs,
            "espn_away_score":espn_a,"espn_home_score":espn_h,
            "score_match":score_match,"matched_at_utc":stamp
        })
    report.update({"matched_games":matched,"score_checks":score_checks,"score_mismatches":score_mismatches,
                   "mapping_coverage":(matched/len(espn) if espn else None)})
except Exception as e:
    report["error"]=str(e)

write_gz(ROOT/"official_game_id_map.csv.gz",rows)
(ROOT/"official_crosscheck_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
