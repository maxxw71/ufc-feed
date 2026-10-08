#!/usr/bin/env python3
import csv,gzip,json,re
from pathlib import Path
from datetime import datetime,timezone
NBA=Path(__file__).resolve().parent
p=NBA/"availability"/"historical_by_season"/"2022_23"/"report_texts.csv.gz"
with gzip.open(p,"rt",encoding="utf-8",newline="") as f:rows=list(csv.DictReader(f))
sample=[]
for r in rows[:5]:
    txt=r.get("report_text") or ""
    sample.append({"report_url":r.get("report_url"),"text":txt[:12000],"lines":[x for x in txt.splitlines() if x.strip()][:100]})
out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"reports":len(rows),"sample":sample}
q=NBA/"availability"/"historical_by_season"/"2022_23"/"text_format_probe.json"
q.write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({"reports":len(rows),"first_lines":[x["lines"][:20] for x in sample[:2]]},indent=2))
