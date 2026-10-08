#!/usr/bin/env python3
import argparse,csv,gzip,io,json,re,time,urllib.request,urllib.error,hashlib
from datetime import datetime,timezone
from pathlib import Path
from pypdf import PdfReader

NBA=Path(__file__).resolve().parent
ROOT=NBA/"availability"/"historical_by_season"

ap=argparse.ArgumentParser()
ap.add_argument("--season",default="2022-23")
ap.add_argument("--sleep",type=float,default=0.12)
args=ap.parse_args()
SEASON=args.season
OUT=ROOT/SEASON.replace("-","_")
GAMES=OUT/"coverage_games.csv.gz"

def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)
def download(url,tries=5,timeout=30):
    headers={
      "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
      "Accept":"application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
      "Accept-Language":"en-US,en;q=0.8",
      "Connection":"close"
    }
    last=None
    for attempt in range(tries):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=timeout) as r:b=r.read()
            if b[:5]==b"%PDF-":return b,None
            last=f"non-pdf payload ({len(b)} bytes)"
        except urllib.error.HTTPError as e:
            last=f"HTTP {e.code}"
            if e.code==404:return b"",last
        except Exception as e:last=str(e)
        time.sleep((attempt+1)*1.25)
    return b"",last

if not GAMES.exists():
    raise SystemExit(f"missing mapped coverage file: {GAMES}")

rows=rgz(GAMES)
urls=sorted({r.get("report_url") for r in rows if r.get("report_url")})
texts={};errors={};report_rows=[]
for i,u in enumerate(urls,1):
    b,err=download(u)
    txt=""
    if b:
        try:txt="\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(b)))
        except Exception as e:err=f"parse:{e}"
    if txt:
        texts[u]=txt
        report_rows.append({
          "season":SEASON,"report_url":u,
          "text_sha256":hashlib.sha256(txt.encode("utf-8")).hexdigest(),
          "text_chars":len(txt),
          "status_terms":len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)),
          "report_text":txt
        })
    else:
        texts[u]="";errors[u]=err or "empty_text"
    if i%50==0:print(f"parsed {i}/{len(urls)} official reports; success={sum(bool(x) for x in texts.values())}",flush=True)
    time.sleep(args.sleep)

for r in rows:
    txt=texts.get(r.get("report_url"),"")
    home=r.get("home_team") or "";away=r.get("away_team") or ""
    r["report_text_found"]=bool(txt)
    r["home_mentioned"]=bool(txt and home.lower() in txt.lower())
    r["away_mentioned"]=bool(txt and away.lower() in txt.lower())
    r["both_teams_mentioned"]=bool(txt and home.lower() in txt.lower() and away.lower() in txt.lower())
    r["status_terms"]=len(re.findall(r"\b(?:Out|Questionable|Probable|Doubtful|Available)\b",txt,re.I)) if txt else 0
    r["not_yet_submitted"]=bool(re.search(r"NOT YET SUBMITTED",txt,re.I)) if txt else False

wgz(GAMES,rows)
wgz(OUT/"report_texts.csv.gz",report_rows)

summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),"season":SEASON,"games":len(rows),
 "unique_game_dates":len({str(r.get("tip_et") or "")[:10] for r in rows if r.get("tip_et")}),
 "games_with_pre_tip_report":sum(1 for r in rows if str(r.get("report_found")).lower() in ("true","1")),
 "games_with_report_text":sum(1 for r in rows if str(r.get("report_text_found")).lower() in ("true","1")),
 "games_with_both_teams_mentioned":sum(1 for r in rows if str(r.get("both_teams_mentioned")).lower() in ("true","1")),
 "unique_reports_selected":len(urls),"unique_reports_downloaded":sum(1 for u in urls if texts.get(u)),
 "download_errors":len(errors),"preserved_report_text_rows":len(report_rows),
 "policy":"Reuses the already point-in-time selected official NBA pre-tip report URL per game, downloads each unique PDF, preserves extracted report text, and never substitutes a later report."
}
(OUT/"coverage_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
(OUT/"download_errors.json").write_text(json.dumps(errors,indent=2)+"\n")
print(json.dumps(summary,indent=2))
