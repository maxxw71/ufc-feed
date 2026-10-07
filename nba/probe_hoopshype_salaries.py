#!/usr/bin/env python3
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
rows=[]
for start in range(2018,2026):
    season=f"{start}-{start+1}"
    url=f"https://hoopshype.com/salaries/players/{season}/"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"})
        with urllib.request.urlopen(req,timeout=25) as r:
            body=r.read().decode("utf-8","replace");status=r.status
        title=re.search(r"<title>(.*?)</title>",body,re.I|re.S)
        money=len(re.findall(r"\$[0-9][0-9,]+",body))
        player_links=len(re.findall(r'href="https?://hoopshype\.com/player/[^"]+/',body,re.I))
        rows.append({"season":season,"url":url,"http_status":status,"bytes":len(body),
                     "title":" ".join((title.group(1) if title else "").split()),"salary_tokens":money,"player_links":player_links})
    except Exception as e:
        rows.append({"season":season,"url":url,"http_status":getattr(e,"code",None),"error":str(e)})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":rows,
        "usable_seasons":sum(1 for r in rows if r.get("http_status")==200 and r.get("salary_tokens",0)>300)}
(OUT/"hoopshype_salary_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
