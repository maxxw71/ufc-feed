#!/usr/bin/env python3
import io,json,re,subprocess,urllib.request
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
URLS=[
"https://ak-static.cms.nba.com/referee/injury/Injury-Report_2019-01-15_05PM.pdf",
"https://ak-static.cms.nba.com/referee/injury/Injury-Report_2021-02-15_08PM.pdf",
]
try:
    from pypdf import PdfReader
except Exception:
    subprocess.check_call(["python3","-m","pip","install","pypdf","-q"])
    from pypdf import PdfReader

rows=[]
for url in URLS:
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        b=r.read()
    reader=PdfReader(io.BytesIO(b))
    text="\n".join((p.extract_text() or "") for p in reader.pages)
    norm=" ".join(text.split())
    rows.append({
      "url":url,"bytes":len(b),"pages":len(reader.pages),"text_chars":len(text),
      "has_team":bool(re.search(r"Boston|Lakers|Celtics|Nets|Knicks|Warriors|Bucks",text,re.I)),
      "has_status":bool(re.search(r"Out|Questionable|Probable|Doubtful|Available|Not Yet Submitted",text,re.I)),
      "text_sample":norm[:8000]
    })
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"reports":rows,
        "parseable":sum(1 for r in rows if r["text_chars"]>500 and r["has_status"])}
(OUT/"official_injury_pdf_parse_pilot.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
