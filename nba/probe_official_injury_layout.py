#!/usr/bin/env python3
"""Compare PDF text extraction modes for historical report format changes."""
import csv,gzip,io,json,urllib.request,re
from pathlib import Path
from pypdf import PdfReader
NBA=Path(__file__).resolve().parent
ROOT=NBA/"availability"/"historical_by_season"
def records(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as h:return list(csv.DictReader(h))
result={}
for season in ("2022_23","2023_24"):
    path=ROOT/season/"coverage_games.csv.gz"
    if not path.exists():continue
    games=records(path)
    example=next((r for r in games if r.get("report_url")),None)
    if example is None:continue
    url=example["report_url"]
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/pdf"})
    with urllib.request.urlopen(req,timeout=35) as resp:data=resp.read()
    reader=PdfReader(io.BytesIO(data))
    variants={}
    for mode in ("plain","layout"):
        try:
            text="\n".join((p.extract_text(extraction_mode=mode) or "") for p in reader.pages)
            lines=[x.rstrip() for x in text.splitlines() if x.strip()]
            variants[mode]={
               "chars":len(text),"lines":len(lines),
               "home_found":example.get("home_team","").lower() in text.lower(),
               "away_found":example.get("away_team","").lower() in text.lower(),
               "first_20_lines":lines[:20],
               "status_terms":len(re.findall(r"\b(?:Out|Questionable|Doubtful|Available|Probable)\b",text))
            }
        except Exception as e:
            variants[mode]={"error":str(e)}
    result[season]={"source":url,"game_id":example["game_id"],"home":example["home_team"],
                    "away":example["away_team"],"variants":variants}
output=ROOT/"pdf_layout_probe.json"
output.write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result,indent=2)[:9000])
