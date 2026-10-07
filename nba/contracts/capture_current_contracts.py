#!/usr/bin/env python3
import csv,gzip,hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"contracts";RAW=OUT/"raw"
URL="https://www.basketball-reference.com/contracts/players.html"
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"}

def money(v):
    if not v:return None
    s=re.sub(r"[^0-9.-]","",v)
    try:return float(s)
    except:return None
def write_gz(p,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)
def read_gz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

def main():
    t=datetime.now(timezone.utc);captured=t.isoformat()
    r=requests.get(URL,headers=HEADERS,timeout=45);r.raise_for_status()
    body=r.text;sha=hashlib.sha256(body.encode()).hexdigest()
    soup=BeautifulSoup(body,"html.parser")
    title=soup.title.get_text(" ",strip=True) if soup.title else ""
    table=soup.find("table")
    if table is None:raise RuntimeError("No contract table found")
    header_labels={}
    for th in table.find_all("th"):
        stat=th.get("data-stat")
        if stat and stat not in header_labels:
            txt=" ".join(th.stripped_strings)
            if txt:header_labels[stat]=txt
    rows=[]
    for tr in table.select("tbody tr"):
        rec={"captured_at_utc":captured,"source":"basketball_reference_current_contracts","source_url":URL}
        found=False
        for cell in tr.find_all(["th","td"]):
            stat=cell.get("data-stat")
            if not stat:continue
            txt=" ".join(cell.stripped_strings)
            rec[stat]=txt
            if stat in ("player","team_id","y1","y2","y3","y4","y5","y6","remain_gtd"):
                found=True
        if not found or not rec.get("player"):continue
        for stat in ("y1","y2","y3","y4","y5","y6","remain_gtd"):
            if stat in rec:rec[stat+"_amount"]=money(rec.get(stat))
        rows.append(rec)
    if len(rows)<100:raise RuntimeError(f"Suspiciously small contract table: {len(rows)} rows")

    prev=(OUT/"latest.json")
    prev_meta={}
    try:prev_meta=json.loads(prev.read_text())
    except:pass
    raw_ref=prev_meta.get("raw_snapshot")
    if prev_meta.get("html_sha256")!=sha:
        d=RAW/t.strftime("%Y-%m-%d");d.mkdir(parents=True,exist_ok=True)
        p=d/(t.strftime("%H%M%SZ")+".html.gz")
        with gzip.open(p,"wt",encoding="utf-8") as f:f.write(body)
        raw_ref=str(p.relative_to(ROOT))

    hist=OUT/"contract_snapshots.csv.gz"
    write_gz(hist,read_gz(hist)+rows)
    latest={"captured_at_utc":captured,"title":title,"rows":len(rows),"unique_players":len({x.get("player") for x in rows}),
            "html_sha256":sha,"raw_snapshot":raw_ref,"header_labels":header_labels,
            "notes":"Prospective current-contract preservation. Raw HTML is retained whenever the source table changes; normalized rows are timestamped every capture."}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"latest.json").write_text(json.dumps(latest,indent=2,sort_keys=True)+"\n")
    print(json.dumps(latest,indent=2))

if __name__=="__main__":main()
