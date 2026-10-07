#!/usr/bin/env python3
import io,json,re,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
DATES=["2019-01-15","2019-11-15","2020-02-15","2021-02-15","2022-02-15","2023-02-15","2024-02-15","2025-02-15","2026-02-15"]
CDX="https://web.archive.org/cdx/search/cdx"

def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-NBA-research/1.0 (+https://appwiza.com)"})
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)

def get_bytes(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Appwiza-NBA-research/1.0 (+https://appwiza.com)"})
    with urllib.request.urlopen(req,timeout=30) as r:return r.read(),r.status,r.headers.get("Content-Type")

rows=[]
try:
    from pypdf import PdfReader
except Exception:
    PdfReader=None

for day in DATES:
    wildcard=f"ak-static.cms.nba.com/referee/injury/Injury-Report_{day}_*"
    params={"url":wildcard,"output":"json","filter":["statuscode:200","mimetype:application/pdf"],"fl":"timestamp,original,statuscode,mimetype,digest,length","collapse":"urlkey"}
    # urllib doseq handles multiple filters.
    url=CDX+"?"+urllib.parse.urlencode(params,doseq=True)
    try:
        data=get_json(url)
        header=data[0] if data else []
        recs=[dict(zip(header,x)) for x in data[1:]] if len(data)>1 else []
    except Exception as e:
        rows.append({"date":day,"cdx_error":str(e)});continue
    item={"date":day,"candidate_count":len(recs),"candidates":recs[:20],"download":None}
    if recs:
        # Prefer the latest report of the day; URL itself carries pregame timestamp.
        rec=sorted(recs,key=lambda x:x.get("original",""))[-1]
        src=rec["original"]
        try:
            b,status,ctype=get_bytes(src)
            text=""
            pages=0
            if PdfReader:
                reader=PdfReader(io.BytesIO(b));pages=len(reader.pages)
                text="\n".join((p.extract_text() or "") for p in reader.pages[:3])
            item["download"]={
                "url":src,"status":status,"content_type":ctype,"bytes":len(b),"pdf_magic":b[:5].decode("latin1","replace"),
                "pages":pages,"text_chars":len(text),"text_sample":" ".join(text.split())[:4000],
                "has_status_terms":bool(re.search(r"Available|Probable|Questionable|Doubtful|Out|Not Yet Submitted",text,re.I)),
                "has_team_terms":bool(re.search(r"Celtics|Lakers|Warriors|Knicks|Heat|Bucks|Nuggets|Suns",text,re.I))
            }
        except Exception as e:item["download_error"]=str(e)
    rows.append(item)

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"dates":rows,
        "successful_pdfs":sum(1 for r in rows if (r.get("download") or {}).get("status")==200 and (r.get("download") or {}).get("pdf_magic")=="%PDF-"),
        "text_native":sum(1 for r in rows if (r.get("download") or {}).get("text_chars",0)>100),
        "policy":"Internet Archive CDX is used only to discover official NBA PDF URLs. Document bytes are downloaded directly from ak-static.cms.nba.com."}
(OUT/"official_injury_pdf_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
