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
    return None

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

def infer_pair(title,url=None):
    t=clean_title(title)
    # Only a candidate identity hint; never strict-link from this alone.
    m=re.search(r"(.+?)\s+vs\.?\s+(.+?)(?:\s*[:\-–—]|\s+Results|\s+Review|\s+Fight|\s+Defeats|$)",t,re.I)
    if m:
        a=norm_space(m.group(1));b=norm_space(m.group(2))
        a=re.sub(r"^(?:Boxing Stats Data API\s*\|\s*)","",a,flags=re.I).strip()
        return a,b
    return slug_pair(url) if url else (None,None)

def table_to_matrix(table):
    matrix=[]
    for tr in table.find_all("tr"):
        cells=[norm_space(x.get_text(" ",strip=True)) for x in tr.find_all(["th","td"])]
        if cells:matrix.append(cells)
    return matrix

def classify_matrix(matrix):
    if not matrix:return None
    blob=" ".join(" ".join(r) for r in matrix)
    score=sum(bool(re.search(w,blob,re.I)) for w in ["punch","landed","thrown","jab","power","accuracy","round"])
    if score<2:return None

    header=" ".join(matrix[0]).lower()
    body=matrix[1:]
    # True round history requires repeated round observations tied to punch metrics.
    metric_header=bool(re.search(r"(punch|landed|thrown|acc|jab|power)",header,re.I))
    round_header=bool(re.search(r"\bround\b",header,re.I))
    numbered_round_rows=0
    round_metric_rows=0
    for row in body:
        label=(row[0] if row else "").strip().lower()
        is_round=bool(re.fullmatch(r"r?\s*\d{1,2}",label) or re.match(r"round\s+\d{1,2}\b",label))
        if is_round:
            numbered_round_rows+=1
            rowblob=" ".join(row[1:])
            if re.search(r"\d+\s*/\s*\d+|\b\d+(?:\.\d+)?%\b",rowblob):
                round_metric_rows+=1
    if (round_header and metric_header and round_metric_rows>=2) or round_metric_rows>=2:
        return "round_level_punch_stats"
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
        "round_table_count":sum(x["type"]=="round_level_punch_stats" for x in matrices),
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
        names=[x.get("name") for x in j.get("fighters",[]) if x.get("name")]
        return names
    except Exception:return []

def resolve_name(hint, strict_names):
    n=norm_name(hint)
    if not n:return None
    exact=[x for x in strict_names if norm_name(x)==n]
    if len(exact)==1:return exact[0]
    # Many article slugs/titles use only a surname (Fundora, Thurman, Wilder)
    # or omit a second surname. Accept only a unique deterministic match.
    ht=n.split()
    if len(ht)==1:
        cand=[x for x in strict_names if ht[0] in (norm_name(x).split()[-1:], norm_name(x).split()[:1])]
        if len(cand)==1:return cand[0]
        cand=[x for x in strict_names if ht[0] in norm_name(x).split()]
        if len(cand)==1:return cand[0]
        return None
    pref=[x for x in strict_names if norm_name(x).startswith(n+" ") or n.startswith(norm_name(x)+" ")]
    if len(pref)==1:return pref[0]
    # First-name + any surname token agreement, still unique only.
    cand=[]
    for x in strict_names:
        xt=norm_name(x).split()
        if xt and ht and xt[0]==ht[0] and any(t in xt[1:] for t in ht[1:]):
            cand.append(x)
    return cand[0] if len(cand)==1 else None

def load_validated_price_bouts():
    p=ROOT/"public_reports"/"HISTORICAL_ODDS_STRICT_UNION_ROWS.json"
    try:j=json.loads(p.read_text(encoding="utf-8"))
    except Exception:return []
    grouped={}
    for row in j.get("rows",[]):
        bid=str(row.get("bout_id") or "")
        if not bid:continue
        g=grouped.setdefault(bid,{"bout_id":bid,"event_date":row.get("event_date"),"selections":set(),"event_url":row.get("event_url")})
        if row.get("selection"):g["selections"].add(str(row["selection"]))
    out=[]
    for g in grouped.values():
        if len(g["selections"])==2:
            sels=sorted(g["selections"])
            g["selection_names"]=sels
            g["selection_norms"]=sorted(norm_name(x) for x in sels)
            del g["selections"]
            out.append(g)
    return out

def article_price_matches(rec, validated_bouts):
    hints=[rec.get("fighter_a_hint"),rec.get("fighter_b_hint")]
    if not all(hints):return []
    hnorms=[norm_name(x) for x in hints]
    matches=[]
    for b in validated_bouts:
        sn=b["selection_norms"]
        # Exact normalized pair.
        if sorted(hnorms)==sn:
            matches.append(b);continue
        # Conservative containment for omitted suffix/maternal surname.
        used=[False,False];ok=True
        for h in hnorms:
            hit=False
            for i,sn_i in enumerate(sn):
                if used[i]:continue
                if h==sn_i or h.startswith(sn_i+" ") or sn_i.startswith(h+" "):
                    used[i]=True;hit=True;break
            if not hit:ok=False;break
        if ok:matches.append(b)
    # Use article publication date as a tie-breaker only, never as a required bout date.
    if len(matches)>1 and rec.get("published_date"):
        try:
            ad=datetime.fromisoformat(rec["published_date"]).date()
            close=[]
            for b in matches:
                try:
                    bd=datetime.fromisoformat(str(b.get("event_date"))[:10]).date()
                    if 0 <= (ad-bd).days <= 7:close.append(b)
                except Exception:
                    pass
            if len(close)==1:return close
        except Exception:
            pass
    return matches

def main():
    strict_names=load_strict_names()
    validated_bouts=load_validated_price_bouts()
    urls,discovery_pages=discover()
    print("DISCOVERED_ARTICLES",len(urls),"DISCOVERY_PAGES",discovery_pages)
    records=[]
    for i,url in enumerate(urls,1):
        if i>1:time.sleep(SLEEP)
        rec=parse_article(url)
        if rec:
            matches=[resolve_name(hint,strict_names) for hint in (rec.get("fighter_a_hint"),rec.get("fighter_b_hint"))]
            rec["strict_fighter_matches"]=matches
            rec["strict_both_sides_matched"]=bool(len(matches)==2 and all(matches))
            pm=article_price_matches(rec,validated_bouts)
            rec["validated_price_bout_matches"]=[{
                "bout_id":x["bout_id"],"event_date":x.get("event_date"),
                "selection_names":x.get("selection_names"),"event_url":x.get("event_url")
            } for x in pm]
            rec["validated_price_bout_unique_match"]=len(pm)==1
            records.append(rec)
            print("ACCEPT",rec["table_count"],rec["round_table_count"],matches,
                  "price_matches",len(pm),url)
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
        "validated_price_articles_unique_match":sum(r.get("validated_price_bout_unique_match",False) for r in records),
        "validated_price_unique_bouts_matched":len({
            r["validated_price_bout_matches"][0]["bout_id"] for r in records
            if r.get("validated_price_bout_unique_match") and r.get("validated_price_bout_matches")
        }),
        "validated_price_round_articles_unique_match":sum(
            r.get("validated_price_bout_unique_match",False) and r.get("round_table_count",0)>0 for r in records
        ),
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
        "validated_price_articles_unique_match","validated_price_unique_bouts_matched",
        "validated_price_round_articles_unique_match","article_date_min","article_date_max"
    ]},indent=2))

if __name__=="__main__":main()
