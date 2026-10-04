#!/usr/bin/env python3
"""Collect publicly published Boxing Data punch-stat tables.

This is a DEEP_STATS supplemental collector, not a strict-source importer.
It discovers public boxing-data.com blog fight-review pages, extracts tables
that contain punch/round statistics, and preserves provenance. Nothing from
this collector is promoted into strict research automatically.

No RapidAPI key is required.
"""
from __future__ import annotations

import csv, json, re, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/"punch_supplements"
OUTDIR.mkdir(parents=True,exist_ok=True)
REPORT=OUTDIR/"boxing_data_public_stats_report.json"
ROWS=OUTDIR/"boxing_data_public_stats_rows.csv"

BASE="https://boxing-data.com"
SEEDS=[
    BASE+"/blog/",
    BASE+"/sitemap.xml",
    BASE+"/sitemap-index.xml",
]
UA="Appwiza Boxing Research/1.0 (+public provenance audit)"
MAX_ARTICLES=1000
MAX_DISCOVERY_PAGES=100
SLEEP=0.20

PUNCH_WORDS=re.compile(r"\b(punch|punches|jab|jabs|power|landed|thrown|accuracy)\b",re.I)
ROUND_WORDS=re.compile(r"\b(round|r\d{1,2})\b",re.I)
SOURCE_WORDS=re.compile(r"(boxing data api|powered by boxing data|data sourced from boxing data)",re.I)
DATE_RE=re.compile(r"\b(20\d{2}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2})\b",re.I)

s=requests.Session()
s.headers.update({"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})

def get(url):
    try:
        r=s.get(url,timeout=25)
        print("GET",r.status_code,url)
        if r.status_code==200:return r
    except Exception as e:
        print("ERR",url,type(e).__name__,str(e)[:180])
    return None

def same_site(url):
    try:return urlparse(url).netloc in ("boxing-data.com","www.boxing-data.com")
    except:return False

def norm_space(x):
    return re.sub(r"\s+"," ",str(x or "")).strip()

def discover():
    q=deque(SEEDS)
    seen=set()
    articles=set()
    discovery_pages=0
    while q and discovery_pages<MAX_DISCOVERY_PAGES and len(articles)<MAX_ARTICLES:
        url=q.popleft()
        if url in seen:continue
        seen.add(url)
        r=get(url)
        discovery_pages+=1
        if not r:continue
        ctype=r.headers.get("content-type","")
        txt=r.text
        if "xml" in ctype or txt.lstrip().startswith("<?xml"):
            soup=BeautifulSoup(txt,"xml")
            for loc in soup.find_all("loc"):
                u=norm_space(loc.get_text())
                if not same_site(u):continue
                if "/blog/" in u and not re.search(r"/blog/?$",u):
                    articles.add(u.rstrip("/"))
                elif "sitemap" in u and u not in seen:
                    q.append(u)
            continue
        soup=BeautifulSoup(txt,"html.parser")
        for a in soup.find_all("a",href=True):
            u=urljoin(url,a["href"]).split("#")[0].rstrip("/")
            if not same_site(u):continue
            p=urlparse(u).path
            if p.startswith("/blog/") and p not in ("/blog","/blog/"):
                # Pagination/listing routes remain discovery pages; article-like slugs become candidates.
                tail=p[len("/blog/"):].strip("/")
                if re.fullmatch(r"(page/)?\d+",tail) or "page=" in u:
                    if u not in seen:q.append(u)
                elif tail:
                    articles.add(u)
        # Common static-site pagination links.
        for a in soup.find_all("a",string=re.compile(r"(next|older|more)",re.I)):
            if a.get("href"):
                u=urljoin(url,a["href"]).split("#")[0].rstrip("/")
                if same_site(u) and u not in seen:q.append(u)
    return sorted(articles)[:MAX_ARTICLES],discovery_pages

def parse_date(soup,text):
    for attrs in (
        {"property":"article:published_time"},
        {"name":"article:published_time"},
        {"name":"date"},
        {"itemprop":"datePublished"},
    ):
        tag=soup.find("meta",attrs=attrs)
        raw=(tag.get("content") if tag else None) or ""
        if raw:
            m=DATE_RE.search(raw)
            if m:return m.group(1)
            if re.match(r"20\\d{2}-\\d{2}-\\d{2}",raw):return raw[:10]
    for tag in soup.find_all(["time"]):
        raw=tag.get("datetime") or tag.get_text(" ",strip=True)
        m=DATE_RE.search(raw or "")
        if m:return m.group(1)
    m=DATE_RE.search(text)
    return m.group(1) if m else None

def normalized_date(raw):
    if not raw:return None
    raw=raw.strip()
    for fmt in ("%Y-%m-%d","%b %d, %Y","%B %d, %Y","%d %b %Y","%d %B %Y"):
        try:return datetime.strptime(raw,fmt).date().isoformat()
        except ValueError:pass
    return raw

def norm_name(s):
    x=str(s or "").lower()
    x=re.sub(r"\\b(jr|jnr|sr|ii|iii|iv)\\b"," ",x)
    x=re.sub(r"[^a-z0-9]+"," ",x)
    return re.sub(r"\\s+"," ",x).strip()

def slug_pair(url):
    slug=urlparse(url).path.rstrip("/").split("/")[-1]
    if "-vs-" not in slug:return None,None
    left,right=slug.split("-vs-",1)
    stops=("fight-results","fight-result","fight-review","review","fight-preview","preview","results","result",
           "statistical-analysis","stats-analysis","analysis","prediction","betting","key-stats","super-",
           "wba-","wbc-","wbo-","ibf-","title-")
    cut=len(right)
    for st in stops:
        pos=right.find("-"+st)
        if pos>=0:cut=min(cut,pos)
    right=right[:cut]
    def pretty(x):
        return " ".join(w.capitalize() if w not in ("jr","jnr","ii","iii") else w.upper() for w in x.split("-") if w)
    return pretty(left),pretty(right)

def clean_title(title):
    x=norm_space(title)
    x=re.sub(r"\s*\|\s*Boxing Stats Data API.*$","",x,flags=re.I)
    return x

def infer_pair(title):
    t=clean_title(title)
    # Only a candidate identity hint; never strict-link from this alone.
    m=re.search(r"(.+?)\s+vs\.?\s+(.+?)(?:\s*[:\-–—]|\s+Results|\s+Review|\s+Fight|\s+Defeats|$)",t,re.I)
    if not m:return None,None
    a=norm_space(m.group(1));b=norm_space(m.group(2))
    # Remove common leading title boilerplate.
    a=re.sub(r"^(?:Boxing Stats Data API\s*\|\s*)","",a,flags=re.I).strip()
    return a,b

def table_to_matrix(table):
    matrix=[]
    for tr in table.find_all("tr"):
        cells=[norm_space(x.get_text(" ",strip=True)) for x in tr.find_all(["th","td"])]
        if cells:matrix.append(cells)
    return matrix

def classify_matrix(matrix):
    blob=" ".join(" ".join(r) for r in matrix)
    score=sum(bool(re.search(w,blob,re.I)) for w in ["punch","landed","thrown","jab","power","accuracy","round"])
    if score<2:return None
    if ROUND_WORDS.search(blob) and (re.search(r"landed\s*/\s*thrown",blob,re.I) or re.search(r"\bround\b",blob,re.I)):
        return "round_level_or_round_summary"
    return "fight_total_or_summary"

def parse_article(url):
    r=get(url)
    if not r:return None
    soup=BeautifulSoup(r.text,"html.parser")
    h=soup.find("h1")
    title=clean_title(h.get_text(" ",strip=True) if h else soup.title.get_text(" ",strip=True) if soup.title else "")
    text=norm_space(soup.get_text(" ",strip=True))
    if not PUNCH_WORDS.search(text):return None
    matrices=[]
    for idx,table in enumerate(soup.find_all("table")):
        m=table_to_matrix(table)
        typ=classify_matrix(m)
        if typ:
            matrices.append({"table_index":idx,"type":typ,"rows":m})
    if not matrices:return None
    a,b=infer_pair(title,url)
    explicit_source=bool(SOURCE_WORDS.search(text))
    return {
        "url":url,
        "title":title,
        "published_date":normalized_date(parse_date(soup,text)),
        "fighter_a_hint":a,
        "fighter_b_hint":b,
        "explicit_boxing_data_api_attribution":explicit_source,
        "tables":matrices,
        "table_count":len(matrices),
        "round_table_count":sum(x["type"]=="round_level_or_round_summary" for x in matrices),
    }

def flatten(records):
    out=[]
    for rec in records:
        for t in rec["tables"]:
            matrix=t["rows"]
            if not matrix:continue
            width=max(len(r) for r in matrix)
            hdr=(matrix[0]+[""]*width)[:width]
            for ridx,row in enumerate(matrix[1:],1):
                vals=(row+[""]*width)[:width]
                out.append({
                    "source":"boxing-data.com public article",
                    "source_url":rec["url"],
                    "published_date":rec.get("published_date"),
                    "title":rec["title"],
                    "fighter_a_hint":rec.get("fighter_a_hint"),
                    "fighter_b_hint":rec.get("fighter_b_hint"),
                    "explicit_boxing_data_api_attribution":rec["explicit_boxing_data_api_attribution"],
                    "table_type":t["type"],
                    "table_index":t["table_index"],
                    "row_index":ridx,
                    "headers_json":json.dumps(hdr,ensure_ascii=False),
                    "values_json":json.dumps(vals,ensure_ascii=False),
                })
    return out

def load_strict_names():
    p=ROOT/"public_phase2"/"PROFILE_GAP_AUDIT.json"
    try:
        j=json.loads(p.read_text(encoding="utf-8"))
        return {norm_name(x.get("name")):x.get("name") for x in j.get("fighters",[]) if x.get("name")}
    except Exception:return {}

def main():
    strict_names=load_strict_names()
    urls,discovery_pages=discover()
    print("DISCOVERED_ARTICLES",len(urls),"DISCOVERY_PAGES",discovery_pages)
    records=[]
    for i,url in enumerate(urls,1):
        if i>1:time.sleep(SLEEP)
        rec=parse_article(url)
        if rec:
            matches=[]
            for hint in (rec.get("fighter_a_hint"),rec.get("fighter_b_hint")):
                n=norm_name(hint)
                matches.append(strict_names.get(n) if n else None)
            rec["strict_fighter_matches"]=matches
            rec["strict_both_sides_matched"]=bool(len(matches)==2 and all(matches))
            records.append(rec)
            print("ACCEPT",rec["table_count"],rec["round_table_count"],matches,url)
    rows=flatten(records)
    fieldnames=list(rows[0].keys()) if rows else [
        "source","source_url","published_date","title","fighter_a_hint","fighter_b_hint",
        "explicit_boxing_data_api_attribution","table_type","table_index","row_index",
        "headers_json","values_json"
    ]
    with ROWS.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader();w.writerows(rows)
    report={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "status":"SUPPLEMENTAL_QUARANTINED_NOT_STRICT",
        "source":"boxing-data.com public fight-review/blog pages",
        "api_key_required":False,
        "discovery_pages_fetched":discovery_pages,
        "article_urls_discovered":len(urls),
        "articles_with_punch_tables":len(records),
        "articles_explicitly_attributing_boxing_data_api":sum(r["explicit_boxing_data_api_attribution"] for r in records),
        "articles_with_round_tables":sum(r["round_table_count"]>0 for r in records),
        "tables_extracted":sum(r["table_count"] for r in records),
        "round_tables_extracted":sum(r["round_table_count"] for r in records),
        "normalized_table_rows":len(rows),
        "strict_profile_articles_both_sides_matched":sum(r.get("strict_both_sides_matched",False) for r in records),
        "strict_profile_unique_fighters_matched":len({x for r in records for x in (r.get("strict_fighter_matches") or []) if x}),
        "article_date_min":min((r["published_date"] for r in records if r.get("published_date")),default=None),
        "article_date_max":max((r["published_date"] for r in records if r.get("published_date")),default=None),
        "records":records,
        "policy":[
            "Public article tables are supplemental evidence, not automatically strict punch observations.",
            "Exact fighter/bout identity and bout date must be resolved against the boxing identity graph before use.",
            "Rows must be cross-checked against CompuBox or another independent published source before strict DEEP_STATS admission when possible.",
            "Article publication dates are preserved to prevent pre-fight leakage.",
            "No paid Boxing Data API subscription or historical API access is required for this collector."
        ]
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:report[k] for k in [
        "article_urls_discovered","articles_with_punch_tables","articles_explicitly_attributing_boxing_data_api",
        "articles_with_round_tables","tables_extracted","round_tables_extracted","normalized_table_rows",
        "strict_profile_articles_both_sides_matched","strict_profile_unique_fighters_matched",
        "article_date_min","article_date_max"
    ]},indent=2))

if __name__=="__main__":main()
