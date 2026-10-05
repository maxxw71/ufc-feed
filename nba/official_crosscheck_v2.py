#!/usr/bin/env python3
import csv,gzip,json
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
URL="https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
HEADERS={
 "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/154 Safari/537.36",
 "Referer":"https://www.nba.com/","Accept":"application/json, text/plain, */*"
}

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def ymd(v): return str(v or "")[:10]

def main():
    espn=[]
    for p in [DATA/"2026_27"/"pre_season"/"games.csv.gz",DATA/"2026_27"/"regular_season"/"games.csv.gz"]:
        espn.extend(read(p))
    by_key={(ymd(g.get("game_date")),g.get("away_tricode"),g.get("home_tricode")):g for g in espn}

    stamp=datetime.now(timezone.utc).isoformat()
    report={"generated_at_utc":stamp,"schedule_url":URL,"espn_games":len(espn)}
    mappings=[]
    try:
        r=requests.get(URL,headers=HEADERS,timeout=30)
        report["http_status"]=r.status_code
        r.raise_for_status()
        payload=r.json()
        dates=(payload.get("leagueSchedule") or {}).get("gameDates") or []
        official=[g for d in dates for g in (d.get("games") or [])]
        report["official_games"]=len(official)
        score_checks=score_mismatch=0
        for g in official:
            h=g.get("homeTeam") or {}; a=g.get("awayTeam") or {}
            gd=g.get("gameDateTimeUTC") or g.get("gameDateEst") or g.get("gameDate") or ""
            key=(ymd(gd),a.get("teamTricode"),h.get("teamTricode"))
            e=by_key.get(key)
            if not e: continue
            match=None
            if all(x not in (None,"") for x in [h.get("score"),a.get("score"),e.get("home_score"),e.get("away_score")]):
                score_checks+=1
                match=str(h.get("score"))==str(e.get("home_score")) and str(a.get("score"))==str(e.get("away_score"))
                if not match: score_mismatch+=1
            mappings.append({
              "espn_game_id":e.get("game_id"),"nba_game_id":g.get("gameId"),"game_date":ymd(gd),
              "away_tricode":a.get("teamTricode"),"home_tricode":h.get("teamTricode"),
              "nba_status":g.get("gameStatusText") or g.get("gameStatus"),"score_match":match
            })
        report.update({
          "matched_games":len(mappings),
          "mapping_coverage":len(mappings)/len(espn) if espn else 0,
          "score_checks":score_checks,"score_mismatches":score_mismatch,
          "available":True
        })
    except Exception as e:
        report.update({"available":False,"error":str(e)})

    fields=["espn_game_id","nba_game_id","game_date","away_tricode","home_tricode","nba_status","score_match"]
    with gzip.open(ROOT/"official_game_id_map_v2.csv.gz","wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(mappings)
    (ROOT/"official_crosscheck_v2_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

if __name__=="__main__": main()
