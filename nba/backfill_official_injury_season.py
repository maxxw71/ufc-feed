#!/usr/bin/env python3
import argparse,csv,gzip,io,json,re,time,urllib.request,urllib.error,subprocess,hashlib
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from pypdf import PdfReader

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";ROOT=NBA/"availability"/"historical_by_season"
ET=ZoneInfo("America/New_York");BASE="https://ak-static.cms.nba.com/referee/injury/Injury-Report_"

ap=argparse.ArgumentParser()
ap.add_argument("--season",default="2022-23")
ap.add_argument("--sleep",type=float,default=0.18)
args=ap.parse_args()
SEASON=args.season
OUT=ROOT/SEASON.replace("-","_");OUT.mkdir(parents=True,exist_ok=True)

def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def dt(v):return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
def fname(day,h):
    apm="AM" if h<12 else "PM";hh=h if 1<=h<=12 else (12 if h in (0,12) else h-12)
    return f"{BASE}{day}_{hh:02d}{apm}.pdf"
def request(url,method="HEAD",tries=4,timeout=15):
    last=None
    headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept":"application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language":"en-US,en;q=0.8",
        "Connection":"close",
    }
    if method=="GET":
        # The official archive is sensitive to downloader/rate-limit behavior.
        # urllib succeeded in the 2021-22 pilot where the curl-only season pass
        # identified the URLs but downloaded 0 PDFs, so use urllib first and
        # keep curl only as a fallback.
        for attempt in range(tries):
            try:
                req=urllib.request.Request(url,headers=headers)
                with urllib.request.urlopen(req,timeout=timeout) as r:
                    b=r.read()
                if b[:5]==b"%PDF-":
                    return b,None
                last=f"urllib non-pdf payload ({len(b)} bytes)"
            except urllib.error.HTTPError as e:
                last=f"urllib HTTP {e.code}"
                if e.code==404:return b"",last
            except Exception as e:
                last=f"urllib {e}"
            time.sleep((attempt+1)*1.5)

        for attempt in range(2):
            try:
                cp=subprocess.run(
                    ["curl","-L","--silent","--show-error","--connect-timeout","8","--max-time",str(timeout),
                     "--retry","2","--retry-delay","1","-A",headers["User-Agent"],
                     "-H","Accept: application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",url],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False
                )
                if cp.returncode==0 and cp.stdout[:5]==b"%PDF-":
                    return cp.stdout,None
                last=cp.stderr.decode("utf-8","replace").strip() or f"curl non-pdf/exit {cp.returncode}"
            except Exception as e:
                last=f"curl {e}"
            time.sleep((attempt+1)*2)
        return b"",last

    for attempt in range(tries):
        try:
            req=urllib.request.Request(url,method="HEAD",headers=headers)
            with urllib.request.urlopen(req,timeout=timeout) as r:
                ok=r.status==200 and "pdf" in (r.headers.get("Content-Type") or "").lower()
                return ok,None
        except urllib.error.HTTPError as e:
            last=f"HTTP {e.code}"
            if e.code==404:return False,last
            # Do not permanently classify 403 as missing: it can be transient
            # archive throttling. Back off and retry.
            time.sleep((attempt+1)*1.5)
        except Exception as e:
            last=str(e);time.sleep((attempt+1)*1.5)
    return False,last

p=DATA/SEASON.replace("-","_")/"regular_season"/"games.csv.gz"
games=[g for g in rgz(p) if str(g.get("completed")).lower() in ("true","1")]
by_day=defaultdict(list)
for g in games:
    local=dt(g["game_date"]).astimezone(ET)
    by_day[local.date().isoformat()].append((local,g))

existing=defaultdict(list);probe_errors=0
for di,day in enumerate(sorted(by_day),1):
    for h in range(0,24):
        url=fname(day,h);ok,err=request(url,"HEAD")
        if ok:existing[day].append((h,url))
        elif err and "404" not in err:probe_errors+=1
        time.sleep(args.sleep)
    if di%20==0:print(f"probed {di}/{len(by_day)} dates",flush=True)

selected={}
for day,items in by_day.items():
    for tip,g in items:
        candidates=[]
        for h,u in existing.get(day,[]):
            nominal=datetime(tip.year,tip.month,tip.day,h,30,tzinfo=ET)
            if nominal<=tip-timedelta(minutes=15):candidates.append((nominal,u))
        if candidates:selected[g["game_id"]]=max(candidates,key=lambda x:x[0])[1]

texts={};download_errors={}
for i,u in enumerate(sorted(set(selected.values())),1):
    b,err=request(u,"GET",tries=5,timeout=25)
    txt=""
    if b:
        try:reader=PdfReader(io.BytesIO(b));txt="\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception as e:err=f"parse:{e}"
    texts[u]=txt
    if err and not txt:download_errors[u]=err
    time.sleep(max(args.sleep,0.25))
    if i%50==0:print(f"downloaded {i}/{len(set(selected.values()))} reports",flush=True)

rows=[]
for day,items in sorted(by_day.items()):
    for tip,g in items:
        u=selected.get(g["game_id"],"");txt=texts.get(u,"")
        home=g.get("home_team") or "";away=g.get("away_team") or ""
        rows.append({
          "season":SEASON,"game_id":g.get("game_id"),"tip_et":tip.isoformat(),
          "home_team":home,"away_team":away,"report_url":u,"report_found":bool(u),"report_text_found":bool(txt),
          "home_mentioned":bool(txt and home.lower() in txt.lower()),"away_mentioned":bool(txt and away.lower() in txt.lower()),
          "both_teams_mentioned":bool(txt and home.lower() in txt.lower() and away.lower() in txt.lower()),
          "status_terms":len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)) if txt else 0,
          "not_yet_submitted":bool(re.search(r"NOT YET SUBMITTED",txt,re.I)) if txt else False
        })
with gzip.open(OUT/"coverage_games.csv.gz","wt",encoding="utf-8",newline="") as f:
    fields=list(rows[0]) if rows else ["_empty"];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

report_rows=[]
for u in sorted(set(selected.values())):
    txt=texts.get(u,"")
    if not txt:continue
    report_rows.append({
      "season":SEASON,"report_url":u,
      "text_sha256":hashlib.sha256(txt.encode("utf-8")).hexdigest(),
      "text_chars":len(txt),
      "status_terms":len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)),
      "report_text":txt
    })
with gzip.open(OUT/"report_texts.csv.gz","wt",encoding="utf-8",newline="") as f:
    fields=list(report_rows[0]) if report_rows else ["_empty"];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(report_rows)
(OUT/"download_errors.json").write_text(json.dumps(download_errors,indent=2)+"\n")

summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"season":SEASON,"games":len(rows),
 "unique_game_dates":len(by_day),"dates_with_any_report":sum(1 for d in by_day if existing.get(d)),
 "games_with_pre_tip_report":sum(1 for r in rows if r["report_found"]),
 "games_with_report_text":sum(1 for r in rows if r["report_text_found"]),
 "games_with_both_teams_mentioned":sum(1 for r in rows if r["both_teams_mentioned"]),
 "unique_reports_selected":len(set(selected.values())),"unique_reports_downloaded":sum(1 for u in set(selected.values()) if texts.get(u)),
 "probe_non404_errors":probe_errors,"download_errors":len(download_errors),
 "preserved_report_text_rows":len(report_rows),"parser":"pypdf PdfReader.pages",
 "policy":"Latest official NBA injury PDF nominally timestamped at least 15 minutes before tip. One season at a time with retry/backoff; preserves extracted text for every successfully downloaded pre-tip report."}
(OUT/"coverage_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
