#!/usr/bin/env python3
import csv,gzip,io,json,re,subprocess,urllib.request
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"availability"/"historical_pilot";OUT.mkdir(parents=True,exist_ok=True)
SEASON="2021-22"
ET=ZoneInfo("America/New_York")
BASE="https://ak-static.cms.nba.com/referee/injury/Injury-Report_"

try:
    from pypdf import PdfReader
except Exception:
    subprocess.check_call(["python","-m","pip","install","-q","pypdf"])
    from pypdf import PdfReader

def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def dt(v):
    return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
def fname(day,h):
    ap="AM" if h<12 else "PM";hh=h if 1<=h<=12 else (12 if h in (0,12) else h-12)
    return f"{BASE}{day}_{hh:02d}{ap}.pdf"
def head_ok(url):
    try:
        req=urllib.request.Request(url,method="HEAD",headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=8) as r:
            return r.status==200 and "pdf" in (r.headers.get("Content-Type") or "").lower()
    except Exception:return False
def download_text(url,cache):
    if url in cache:return cache[url]
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=20) as r:b=r.read()
        reader=PdfReader(io.BytesIO(b));txt="\n".join((p.extract_text() or "") for p in reader.pages)
        cache[url]=txt;return txt
    except Exception:
        cache[url]="";return ""

games=rgz(DATA/"2021_22"/"regular_season"/"games.csv.gz")
games=[g for g in games if str(g.get("completed")).lower() in ("true","1")]
by_day=defaultdict(list)
for g in games:
    local=dt(g["game_date"]).astimezone(ET)
    by_day[local.date().isoformat()].append((local,g))

existing=defaultdict(list)
for day in sorted(by_day):
    for h in range(6,23):
        url=fname(day,h)
        if head_ok(url):existing[day].append((h,url))

cache={};rows=[]
for day,items in sorted(by_day.items()):
    for tip,g in items:
        candidates=[(h,u) for h,u in existing.get(day,[]) if (datetime(tip.year,tip.month,tip.day,h,30,tzinfo=ET) <= tip-timedelta(minutes=15))]
        sel=max(candidates,key=lambda x:x[0]) if candidates else None
        txt=download_text(sel[1],cache) if sel else ""
        home=g.get("home_team") or "";away=g.get("away_team") or ""
        rows.append({
          "season":SEASON,"game_id":g.get("game_id"),"tip_et":tip.isoformat(),
          "home_team":home,"away_team":away,"report_url":sel[1] if sel else "",
          "report_hour":sel[0] if sel else None,"report_found":bool(sel),
          "home_mentioned":home.lower() in txt.lower() if txt else False,
          "away_mentioned":away.lower() in txt.lower() if txt else False,
          "both_teams_mentioned":bool(txt and home.lower() in txt.lower() and away.lower() in txt.lower()),
          "status_terms":len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)) if txt else 0,
          "not_yet_submitted":bool(re.search(r"NOT YET SUBMITTED",txt,re.I)) if txt else False,
        })

fields=list(rows[0]) if rows else ["_empty"]
with gzip.open(OUT/"coverage_games.csv.gz","wt",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"season":SEASON,"games":len(rows),
 "unique_game_dates":len(by_day),"dates_with_any_report":sum(1 for d in by_day if existing.get(d)),
 "games_with_pre_tip_report":sum(1 for r in rows if r["report_found"]),
 "games_with_both_teams_mentioned":sum(1 for r in rows if r["both_teams_mentioned"]),
 "unique_reports_downloaded":len([k for k,v in cache.items() if v]),
 "policy":"Pilot chooses an official NBA injury PDF whose nominal report time (filename hour + :30 ET) is at least 15 minutes before scheduled tip. It never uses later reports."
}
(OUT/"coverage_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
