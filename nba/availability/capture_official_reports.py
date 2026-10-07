#!/usr/bin/env python3
import hashlib,json,gzip,io,urllib.request,urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"availability"/"official_reports"
RAW=OUT/"raw";TEXT=OUT/"text"
INDEX=OUT/"index.json"
ET=ZoneInfo("America/New_York")
BASE="https://ak-static.cms.nba.com/referee/injury/"
UA={"User-Agent":"Mozilla/5.0 AppwizaNBAProspective/1.0"}

def load_index():
    try:return json.loads(INDEX.read_text())
    except:return {"reports":{}}
def fname(day,h):
    ap="AM" if h<12 else "PM"
    hh=h if 1<=h<=12 else (12 if h in (0,12) else h-12)
    return f"Injury-Report_{day}_{hh:02d}{ap}.pdf"
def fetch(url,method="HEAD",timeout=15):
    try:
        req=urllib.request.Request(url,method=method,headers=UA)
        with urllib.request.urlopen(req,timeout=timeout) as r:
            if method=="HEAD":
                return (r.status==200 and "pdf" in (r.headers.get("Content-Type") or "").lower(),None)
            return (r.read(),None)
    except urllib.error.HTTPError as e:
        return ((False if method=="HEAD" else b""),f"HTTP {e.code}")
    except Exception as e:
        return ((False if method=="HEAD" else b""),str(e))

def main():
    t=datetime.now(timezone.utc);local=t.astimezone(ET)
    idx=load_index();reports=idx.setdefault("reports",{})
    found=[];saved=[];errors=[]
    # Today + tomorrow reports can contain tonight/tomorrow games; also check yesterday
    # so a late previous-day report used for today's early games is preserved.
    for delta in (-1,0,1):
        day=(local.date()+timedelta(days=delta)).isoformat()
        for h in range(6,24):
            fn=fname(day,h);url=BASE+fn
            ok,err=fetch(url,"HEAD")
            if not ok:
                if err and "404" not in err:errors.append({"url":url,"error":err})
                continue
            found.append(url)
            if url in reports:continue
            b,derr=fetch(url,"GET",timeout=30)
            if not b:
                errors.append({"url":url,"error":derr or "empty download"});continue
            sha=hashlib.sha256(b).hexdigest()
            rday=day
            rawdir=RAW/rday;rawdir.mkdir(parents=True,exist_ok=True)
            rawp=rawdir/fn;rawp.write_bytes(b)
            text=""
            pages=0
            try:
                reader=PdfReader(io.BytesIO(b));pages=len(reader.pages)
                text="\n".join((p.extract_text() or "") for p in reader.pages)
            except Exception as e:
                errors.append({"url":url,"error":f"parse:{e}"})
            textdir=TEXT/rday;textdir.mkdir(parents=True,exist_ok=True)
            textp=textdir/(fn.replace(".pdf",".txt.gz"))
            with gzip.open(textp,"wt",encoding="utf-8") as f:f.write(text)
            rec={
              "url":url,"filename":fn,"report_date":day,"nominal_hour_et":h,
              "captured_at_utc":t.isoformat(),"sha256":sha,"bytes":len(b),"pages":pages,"text_chars":len(text),
              "raw_path":str(rawp.relative_to(ROOT)),"text_path":str(textp.relative_to(ROOT))
            }
            reports[url]=rec;saved.append(rec)
    idx["updated_at_utc"]=t.isoformat()
    idx["report_count"]=len(reports)
    INDEX.parent.mkdir(parents=True,exist_ok=True)
    INDEX.write_text(json.dumps(idx,indent=2,sort_keys=True)+"\n")
    latest={"captured_at_utc":t.isoformat(),"urls_found":len(found),"new_reports_saved":len(saved),
            "total_reports_preserved":len(reports),"errors":errors[-20:],
            "policy":"Preserve official NBA injury-report PDF bytes and extracted text prospectively. Existing URLs are never re-downloaded or overwritten."}
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    print(json.dumps(latest,indent=2))

if __name__=="__main__":main()
