#!/usr/bin/env python3
import io,json,re,urllib.parse,urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
DATES=["2019-01-15","2019-11-15","2020-02-15","2021-02-15","2022-02-15","2023-02-15","2024-02-15","2025-02-15","2026-02-15"]
CDX="https://web.archive.org/cdx/search/cdx"
PREFIX="https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
RX=re.compile(r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?([AP]M)\.pdf",re.I)

def get_text(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-NBA-research/1.0 (+https://appwiza.com)"})
    with urllib.request.urlopen(req,timeout=45) as r:return r.read().decode("utf-8","replace")
def get_bytes(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-NBA-research/1.0 (+https://appwiza.com)"})
    with urllib.request.urlopen(req,timeout=30) as r:return r.read(),r.status,r.headers.get("Content-Type")
def report_key(url):
    m=RX.search(url)
    if not m:return ("",0,0)
    day,hh,mm,ap=m.groups();h=int(hh)%12+(12 if ap.upper()=="PM" else 0);minute=int(mm) if mm else 30
    return day,h,minute

by_day=defaultdict(list);year_errors={}
for year in sorted({int(x[:4]) for x in DATES}):
    wildcard=urllib.parse.quote(f"ak-static.cms.nba.com/referee/injury/Injury-Report_{year}-*",safe="")
    url=f"{CDX}?url={wildcard}&output=txt&fl=original&filter=statuscode%3A200&filter=mimetype%3Aapplication%2Fpdf&collapse=urlkey"
    try:
        body=get_text(url)
        for raw in body.splitlines():
            src=raw.strip().split("?",1)[0]
            m=RX.search(src)
            if m:by_day[m.group(1)].append(src)
    except Exception as e:year_errors[str(year)]=str(e)

try:
    from pypdf import PdfReader
except Exception:
    PdfReader=None
rows=[]
for day in DATES:
    urls=sorted(set(by_day.get(day,[])),key=report_key)
    item={"date":day,"candidate_count":len(urls),"candidates":urls[:30],"download":None}
    if urls:
        src=urls[-1]
        try:
            b,status,ctype=get_bytes(src);text="";pages=0
            if PdfReader:
                reader=PdfReader(io.BytesIO(b));pages=len(reader.pages);text="\n".join((p.extract_text() or "") for p in reader.pages[:4])
            item["download"]={"url":src,"status":status,"content_type":ctype,"bytes":len(b),"pdf_magic":b[:5].decode("latin1","replace"),
                              "pages":pages,"text_chars":len(text),"text_sample":" ".join(text.split())[:5000],
                              "has_status_terms":bool(re.search(r"Available|Probable|Questionable|Doubtful|Out|Not Yet Submitted",text,re.I))}
        except Exception as e:item["download_error"]=str(e)
    rows.append(item)
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"year_errors":year_errors,"dates":rows,
        "successful_pdfs":sum(1 for r in rows if (r.get("download") or {}).get("status")==200 and (r.get("download") or {}).get("pdf_magic")=="%PDF-"),
        "text_native":sum(1 for r in rows if (r.get("download") or {}).get("text_chars",0)>100),
        "policy":"Internet Archive CDX is discovery evidence only; report bytes are fetched directly from the official NBA ak-static.cms.nba.com host."}
(OUT/"official_injury_pdf_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
