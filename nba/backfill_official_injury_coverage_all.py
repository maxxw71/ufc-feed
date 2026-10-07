#!/usr/bin/env python3
import csv,gzip,io,json,re,urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from pypdf import PdfReader

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"availability"/"historical_coverage";OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
ET=ZoneInfo("America/New_York");BASE="https://ak-static.cms.nba.com/referee/injury/Injury-Report_"

def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def dt(v):
    return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
def fname(day,h):
    ap="AM" if h<12 else "PM";hh=h if 1<=h<=12 else (12 if h in (0,12) else h-12)
    return f"{BASE}{day}_{hh:02d}{ap}.pdf"
def head(url):
    try:
        req=urllib.request.Request(url,method="HEAD",headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return url if r.status==200 and "pdf" in (r.headers.get("Content-Type") or "").lower() else None
    except Exception:return None
def download_text(url):
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=25) as r:b=r.read()
        reader=PdfReader(io.BytesIO(b))
        return url,"\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:return url,""

all_summary={};all_rows=[]
for season in SEASONS:
    p=DATA/season.replace("-","_")/"regular_season"/"games.csv.gz"
    if not p.exists():
        all_summary[season]={"present":False};continue
    games=[g for g in rgz(p) if str(g.get("completed")).lower() in ("true","1")]
    by_day=defaultdict(list)
    for g in games:
        local=dt(g["game_date"]).astimezone(ET)
        by_day[local.date().isoformat()].append((local,g))
    urls=[(day,h,fname(day,h)) for day in by_day for h in range(0,24)]
    existing=defaultdict(list)
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs={ex.submit(head,u):(day,h,u) for day,h,u in urls}
        for fut in as_completed(futs):
            day,h,u=futs[fut]
            if fut.result():existing[day].append((h,u))
    selected={}
    for day,items in by_day.items():
        for tip,g in items:
            candidates=[]
            for h,u in existing.get(day,[]):
                nominal=datetime(tip.year,tip.month,tip.day,h,30,tzinfo=ET)
                if nominal<=tip-timedelta(minutes=15):candidates.append((nominal,u))
            sel=max(candidates,key=lambda x:x[0]) if candidates else None
            if sel:selected[g["game_id"]]=sel[1]
    unique=sorted(set(selected.values()))
    texts={}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for u,txt in ex.map(download_text,unique):
            texts[u]=txt
    season_rows=[]
    for day,items in sorted(by_day.items()):
        for tip,g in items:
            u=selected.get(g["game_id"],"");txt=texts.get(u,"")
            home=g.get("home_team") or "";away=g.get("away_team") or ""
            row={
              "season":season,"game_id":g.get("game_id"),"tip_et":tip.isoformat(),
              "home_team":home,"away_team":away,"report_url":u,"report_found":bool(u),
              "report_text_found":bool(txt),
              "home_mentioned":bool(txt and home.lower() in txt.lower()),
              "away_mentioned":bool(txt and away.lower() in txt.lower()),
              "both_teams_mentioned":bool(txt and home.lower() in txt.lower() and away.lower() in txt.lower()),
              "status_terms":len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)) if txt else 0,
              "not_yet_submitted":bool(re.search(r"NOT YET SUBMITTED",txt,re.I)) if txt else False
            }
            season_rows.append(row);all_rows.append(row)
    all_summary[season]={
      "present":True,"games":len(season_rows),"unique_game_dates":len(by_day),
      "dates_with_any_report":sum(1 for d in by_day if existing.get(d)),
      "games_with_pre_tip_report":sum(1 for r in season_rows if r["report_found"]),
      "games_with_report_text":sum(1 for r in season_rows if r["report_text_found"]),
      "games_with_both_teams_mentioned":sum(1 for r in season_rows if r["both_teams_mentioned"]),
      "unique_reports_selected":len(unique),
      "unique_reports_downloaded":sum(1 for u in unique if texts.get(u))
    }
    print(season,json.dumps(all_summary[season]),flush=True)

fields=list(all_rows[0]) if all_rows else ["_empty"]
with gzip.open(OUT/"coverage_games.csv.gz","wt",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(all_rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":all_summary,
         "policy":"For each game, use the latest official NBA injury report nominally timestamped at least 15 minutes before tip. Never use a later report."}
(OUT/"coverage_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
