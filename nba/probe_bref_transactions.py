#!/usr/bin/env python3
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent;OUT=NBA/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
years=range(2019,2027)
rows=[]
for y in years:
    url=f"https://www.basketball-reference.com/leagues/NBA_{y}_transactions.html"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"})
        with urllib.request.urlopen(req,timeout=20) as r:
            body=r.read().decode("utf-8","replace");status=r.status
        text=re.sub(r"<[^>]+>"," ",body)
        text=re.sub(r"\s+"," ",text)
        rows.append({"season_end_year":y,"url":url,"http_status":status,"bytes":len(body),
                     "head_coach_mentions":len(re.findall(r"Head Coach",text,re.I)),
                     "trade_mentions":len(re.findall(r"\btraded\b",text,re.I)),
                     "signed_mentions":len(re.findall(r"\bsigned\b",text,re.I)),
                     "waived_mentions":len(re.findall(r"\bwaived\b",text,re.I)),
                     "sample":text[:1000]})
    except Exception as e:
        code=getattr(e,"code",None)
        rows.append({"season_end_year":y,"url":url,"http_status":code,"error":str(e)})
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":rows,
        "usable":sum(1 for r in rows if r.get("http_status")==200 and r.get("bytes",0)>10000)>=6}
(OUT/"bref_transactions_probe.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
