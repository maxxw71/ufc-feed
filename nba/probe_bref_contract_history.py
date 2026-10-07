#!/usr/bin/env python3
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
urls=[]
for y in range(2019,2027):
    for pattern in (
        f"https://www.basketball-reference.com/contracts/players.html?year={y}",
        f"https://www.basketball-reference.com/contracts/players.html?season={y}",
    ):
        urls.append((y,pattern))
rows=[]
for y,url in urls:
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"})
        with urllib.request.urlopen(req,timeout=25) as r:
            body=r.read().decode("utf-8","replace");status=r.status;final=r.geturl()
        title=re.search(r"<title>(.*?)</title>",body,re.I|re.S)
        headers=re.findall(r'data-stat="([^"]+)"',body)
        rows.append({"year":y,"url":url,"final_url":final,"http_status":status,"bytes":len(body),
                     "title":" ".join((title.group(1) if title else "").split()),"data_stats":sorted(set(headers))[:80],
                     "has_year_string":str(y) in body})
    except Exception as e:
        rows.append({"year":y,"url":url,"http_status":getattr(e,"code",None),"error":str(e)})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"attempts":rows}
(OUT/"bref_contract_history_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
