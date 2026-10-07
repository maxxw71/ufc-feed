#!/usr/bin/env python3
import json,re,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from html import unescape

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
url="https://www.basketball-reference.com/leagues/NBA_2020_transactions.html"
req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"})
with urllib.request.urlopen(req,timeout=25) as r:body=r.read().decode("utf-8","replace")
snips=[]
for pat in ("Head Coach"," traded "," signed "," waived "):
    for m in list(re.finditer(re.escape(pat),body,re.I))[:8]:
        raw=body[max(0,m.start()-700):min(len(body),m.end()+900)]
        text=re.sub(r"<[^>]+>"," ",raw)
        text=re.sub(r"\s+"," ",unescape(text)).strip()
        snips.append({"pattern":pat.strip(),"raw":raw,"text":text})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"url":url,"snippets":snips}
(OUT/"bref_transaction_structure_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"snippets":len(snips)},indent=2))
