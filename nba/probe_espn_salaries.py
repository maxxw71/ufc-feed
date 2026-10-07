#!/usr/bin/env python3
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
rows=[]
for y in range(2019,2027):
    url=f"https://www.espn.com/nba/salaries/_/year/{y}/page/1"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)","Accept":"text/html"})
        with urllib.request.urlopen(req,timeout=25) as r:
            body=r.read().decode("utf-8","replace");status=r.status
        title=re.search(r"<title>(.*?)</title>",body,re.I|re.S)
        money=len(re.findall(r"\$[0-9][0-9,]+",body))
        # Salary pages often include athlete/profile links.
        player_links=len(re.findall(r'/nba/player/_/id/\d+/',body,re.I))
        page_count=max([int(x) for x in re.findall(r'/page/(\d+)',body)] or [1])
        rows.append({"year":y,"url":url,"http_status":status,"bytes":len(body),
                     "title":" ".join((title.group(1) if title else "").split()),"salary_tokens":money,"player_links":player_links,"max_page_seen":page_count})
    except Exception as e:
        rows.append({"year":y,"url":url,"http_status":getattr(e,"code",None),"error":str(e)})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":rows,
        "usable_seasons":sum(1 for r in rows if r.get("http_status")==200 and r.get("salary_tokens",0)>20)}
(OUT/"espn_salary_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
