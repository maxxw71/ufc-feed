#!/usr/bin/env python3
"""Inspect known official CompuBox round-stat pages for full chart structure.

Narrow verification/enrichment pass only: URLs must already exist in
boxing_data_external_verifications.json. No site-wide crawl is performed.
"""
from __future__ import annotations
import json,re,time
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"punch_supplements"
SRC=P/"boxing_data_external_verifications.json"
OUT=P/"official_compubox_known_round_page_audit.json"
UA="Mozilla/5.0 AppwizaBoxingResearch/1.0"

def clean(x):return re.sub(r"\s+"," ",str(x or "")).strip()

def main():
    src=json.loads(SRC.read_text())
    urls=[]
    for x in src.get("fights",[]):
        u=x.get("source_url")
        if u and "compuboxdata.com/round-stats/" in u and u not in urls:urls.append(u)
    sess=requests.Session();sess.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.8"})
    rows=[]
    for i,u in enumerate(urls):
        if i:time.sleep(.3)
        rec={"url":u}
        try:
            r=sess.get(u,timeout=35,allow_redirects=True)
            rec.update({"status_code":r.status_code,"final_url":r.url,"bytes":len(r.content)})
            soup=BeautifulSoup(r.content,"lxml")
            title=soup.title.get_text(" ",strip=True) if soup.title else None
            rec["title"]=clean(title)
            tables=[]
            for ti,t in enumerate(soup.find_all("table")):
                matrix=[]
                for tr in t.find_all("tr"):
                    cells=[clean(c.get_text(" ",strip=True)) for c in tr.find_all(["th","td"])]
                    if cells:matrix.append(cells)
                blob=" ".join(" ".join(x) for x in matrix)
                if re.search(r"\b(round|total|jab|power|landed|thrown)\b",blob,re.I):
                    tables.append({"table_index":ti,"rows":matrix[:80]})
            rec["candidate_tables"]=tables
            rec["candidate_table_count"]=len(tables)
            text=clean(soup.get_text(" ",strip=True))
            rec["has_round"]=bool(re.search(r"\bround\b",text,re.I))
            rec["has_jab"]=bool(re.search(r"\bjab",text,re.I))
            rec["has_power"]=bool(re.search(r"\bpower",text,re.I))
            rec["has_total"]=bool(re.search(r"\btotal",text,re.I))
        except Exception as e:
            rec.update({"error":type(e).__name__+": "+str(e)[:250]})
        rows.append(rec)
    report={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "known_urls":len(urls),
      "successful_200":sum(x.get("status_code")==200 for x in rows),
      "pages_with_candidate_tables":sum(bool(x.get("candidate_table_count")) for x in rows),
      "pages_with_total_jab_power_text":sum(x.get("has_total") and x.get("has_jab") and x.get("has_power") for x in rows),
      "rows":rows,
      "policy":"Known verification URLs only; no site-wide crawl. This audit does not automatically promote any page into strict research."
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("known_urls","successful_200","pages_with_candidate_tables","pages_with_total_jab_power_text")},indent=2))

if __name__=="__main__":main()
