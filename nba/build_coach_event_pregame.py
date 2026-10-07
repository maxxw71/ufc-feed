#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";SRC=NBA/"transactions"/"bref_coach_events.csv.gz";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)

TEAM_MAP={
 "ATL":"ATL","BOS":"BOS","BRK":"BKN","CHO":"CHA","CHI":"CHI","CLE":"CLE","DAL":"DAL","DEN":"DEN","DET":"DET","GSW":"GSW",
 "HOU":"HOU","IND":"IND","LAC":"LAC","LAL":"LAL","MEM":"MEM","MIA":"MIA","MIL":"MIL","MIN":"MIN","NOP":"NOP","NYK":"NYK",
 "OKC":"OKC","ORL":"ORL","PHI":"PHI","PHO":"PHX","POR":"POR","SAC":"SAC","SAS":"SAS","TOR":"TOR","UTA":"UTA","WAS":"WAS"
}
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
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None

events=defaultdict(list)
for r in rgz(SRC):
    tri=TEAM_MAP.get((r.get("team_bref") or "").upper())
    d=dt((r.get("date") or "")+"T00:00:00+00:00")
    if tri and d:
        events[tri].append({"date":d,"action":r.get("action"),"coach_names":r.get("coach_names"),"text":r.get("text")})
for tri in events:events[tri].sort(key=lambda x:x["date"])

rows=[]
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        when=dt(g.get("game_date"))
        if not when:continue
        for side in ("home","away"):
            tri=(g.get(f"{side}_tricode") or "").upper()
            tid=g.get(f"{side}_team_id")
            prior=[e for e in events.get(tri,[]) if e["date"].date()<when.date()]
            last=prior[-1] if prior else None
            row={"season":g.get("season"),"game_id":g.get("game_id"),"game_date":g.get("game_date"),"team_id":tid,"team_tricode":tri,
                 "pregame_only_feature":True}
            if last:
                days=(when.date()-last["date"].date()).days
                row.update({"days_since_last_coach_event":days,"last_coach_event_action":last["action"],"last_coach_event_name":last["coach_names"]})
            for days in (7,14,30,60,90,180):
                cutoff=when.date()-timedelta(days=days)
                priorw=[e for e in prior if e["date"].date()>=cutoff]
                row[f"coach_events_prev_{days}d"]=len(priorw)
                row[f"coach_hires_prev_{days}d"]=sum(1 for e in priorw if e["action"] in ("hired","appointed"))
                row[f"coach_fires_prev_{days}d"]=sum(1 for e in priorw if e["action"]=="fired")
            rows.append(row)

wgz(OUT/"coach_event_pregame.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"teams_with_events":len(events),
         "coach_event_rows":sum(len(v) for v in events.values()),
         "policy":"Only dated Basketball-Reference head-coach transaction events strictly before target game date are used. Same-day events are excluded."}
(OUT/"coach_event_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
