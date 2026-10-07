#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,date
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";TX=NBA/"transactions"/"bref_team_transactions.csv.gz";COACH=NBA/"transactions"/"bref_coach_events.csv.gz"
OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)

ID_TO_BREF={
 "1":"ATL","2":"BOS","3":"NOP","4":"CHI","5":"CLE","6":"DAL","7":"DEN","8":"DET","9":"GSW","10":"HOU",
 "11":"IND","12":"LAC","13":"LAL","14":"MIA","15":"MIL","16":"MIN","17":"BRK","18":"NYK","19":"ORL","20":"PHI",
 "21":"PHO","22":"POR","23":"SAC","24":"SAS","25":"OKC","26":"UTA","27":"WAS","28":"TOR","29":"MEM","30":"CHO"
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
def d(v):
    try:return date.fromisoformat(str(v)[:10])
    except:return None

events=defaultdict(list)
for r in rgz(TX):
    day=d(r.get("date"));kind=r.get("kind")
    if not day:continue
    teams=set((r.get("from_teams") or "").split("|"))|set((r.get("to_teams") or "").split("|"))
    for tc in teams:
        if tc:events[tc].append((day,kind,r))
for arr in events.values():arr.sort(key=lambda x:x[0])

coach_events=defaultdict(list)
for r in rgz(COACH):
    day=d(r.get("date"));tc=r.get("team_bref")
    if day and tc:coach_events[tc].append((day,r))
for arr in coach_events.values():arr.sort(key=lambda x:x[0])

games=[]
for p in DATA.glob("*_*/regular_season/games.csv.gz"):games.extend(rgz(p))
out=[]
for g in games:
    gd=d(g.get("game_date"))
    if not gd:continue
    for home in (True,False):
        tid=str(g.get("home_team_id") if home else g.get("away_team_id"))
        tc=ID_TO_BREF.get(tid)
        if not tc:continue
        prior=[x for x in events.get(tc,[]) if x[0] < gd]
        cp=[x for x in coach_events.get(tc,[]) if x[0] < gd]
        row={"season":g.get("season"),"game_id":g.get("game_id"),"game_date":g.get("game_date"),"team_id":tid,
             "team_bref":tc,"pregame_only_feature":True}
        if prior:
            row["days_since_any_transaction"]=(gd-prior[-1][0]).days
            trades=[x for x in prior if x[1]=="trade"]
            signs=[x for x in prior if x[1]=="signed"]
            waives=[x for x in prior if x[1] in ("waived","released","claimed")]
            row["days_since_trade"]=(gd-trades[-1][0]).days if trades else None
            row["days_since_signing"]=(gd-signs[-1][0]).days if signs else None
            row["days_since_waiver_release"]=(gd-waives[-1][0]).days if waives else None
        for days in (7,14,30,60):
            recent=[x for x in prior if (gd-x[0]).days<=days]
            row[f"transactions_{days}d"]=len(recent)
            row[f"trades_{days}d"]=sum(1 for x in recent if x[1]=="trade")
            row[f"signings_{days}d"]=sum(1 for x in recent if x[1]=="signed")
            row[f"waiver_release_{days}d"]=sum(1 for x in recent if x[1] in ("waived","released","claimed"))
        if cp:
            row["days_since_coach_event"]=(gd-cp[-1][0]).days
            for days in (7,14,30,60):row[f"coach_event_{days}d"]=sum(1 for x in cp if (gd-x[0]).days<=days)
        out.append(row)

wgz(OUT/"transaction_pregame.csv.gz",out)
summary={
 "generated_at_utc":datetime.utcnow().isoformat()+"Z","rows":len(out),
 "teams_with_transactions":len(events),"teams_with_coach_events":len(coach_events),
 "transaction_events":sum(len(x) for x in events.values()),"coach_events":sum(len(x) for x in coach_events.values()),
 "policy":"Only transaction dates strictly before target game calendar date are included; same-day events are excluded because time relative to tipoff is unknown.",
 "coach_note":"Coach-event features are descriptive only until recent-season transaction-page coach coverage is separately validated."
}
(OUT/"transaction_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
