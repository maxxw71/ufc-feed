#!/usr/bin/env python3
"""Collect public Boxing Data profile/context tables for the BROAD lane.

This collector does not overwrite strict profiles. It extracts candidate
age/height/reach/stance/nationality/record/rounds/KO/debut/division/title
values from public boxing-data.com blog pages and matches them conservatively
to the existing strict fighter universe. Numeric physical candidates can then
serve as one corroborating source under the existing multi-source policy.
"""
from __future__ import annotations
import csv,json,re,time
from collections import deque,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/"profile_supplements";OUTDIR.mkdir(parents=True,exist_ok=True)
REPORT=OUTDIR/"boxing_data_public_profiles_report.json"
ROWS=OUTDIR/"boxing_data_public_profile_candidates.csv"

BASE="https://boxing-data.com"
SEEDS=[BASE+"/blog/",BASE+"/sitemap-index.xml",BASE+"/sitemap.xml"]
MAX_ARTICLES=1000
SLEEP=.15
UA="Appwiza Boxing BROAD Research/1.0"
PROFILE_KEYS=re.compile(r"\b(age|height|reach|stance|nationality|record|wins?|loss(?:es)?|draws?|rounds?(?: boxed)?|ko(?: wins?)?|knockouts?|debut|division|weight class|titles?)\b",re.I)

s=requests.Session();s.headers.update({"User-Agent":UA})

def get(url):
    try:
        r=s.get(url,timeout=25)
        print("GET",r.status_code,url)
        return r if r.status_code==200 else None
    except Exception as e:
        print("ERR",type(e).__name__,url);return None

def same(url):
    try:return urlparse(url).netloc in ("boxing-data.com","www.boxing-data.com")
    except:return False

def sp(x):return re.sub(r"\s+"," ",str(x or "")).strip()

def discover():
    q=deque(SEEDS);seen=set();arts=set()
    while q and len(seen)<100 and len(arts)<MAX_ARTICLES:
        u=q.popleft()
        if u in seen:continue
        seen.add(u);r=get(u)
        if not r:continue
        txt=r.text;ctype=r.headers.get("content-type","")
        if "xml" in ctype or txt.lstrip().startswith("<?xml"):
            soup=BeautifulSoup(txt,"xml")
            for loc in soup.find_all("loc"):
                v=sp(loc.get_text())
                if not same(v):continue
                if "/blog/" in v and not re.search(r"/blog/?$",v):arts.add(v.rstrip("/"))
                elif "sitemap" in v and v not in seen:q.append(v)
        else:
            soup=BeautifulSoup(txt,"html.parser")
            for a in soup.find_all("a",href=True):
                v=urljoin(u,a["href"]).split("#")[0].rstrip("/")
                if same(v) and "/blog/" in v and not re.search(r"/blog/?$",v):arts.add(v)
    return sorted(arts)[:MAX_ARTICLES]

def norm_name(x):
    x=str(x or "").lower()
    x=re.sub(r"\b(jr|jnr|sr|ii|iii|iv)\b"," ",x)
    x=re.sub(r"[^a-z0-9]+"," ",x)
    return re.sub(r"\s+"," ",x).strip()

def slug_pair(url):
    slug=urlparse(url).path.rstrip("/").split("/")[-1]
    if "-vs-" not in slug:return (None,None)
    a,b=slug.split("-vs-",1)
    stops=("fight-results","fight-result","fight-review","review","fight-preview","preview","results","result","analysis","prediction","betting","key-stats","statistical")
    cut=len(b)
    for st in stops:
        p=b.find("-"+st)
        if p>=0:cut=min(cut,p)
    b=b[:cut]
    pretty=lambda z:" ".join(w.capitalize() if w not in ("jr","jnr","ii","iii") else w.upper() for w in z.split("-") if w)
    return pretty(a),pretty(b)

def infer_pair(title,url):
    m=re.search(r"(.+?)\s+vs\.?\s+(.+?)(?:\s*[:\-–—]|\s+Results|\s+Review|\s+Fight|\s+Preview|\s+Prediction|$)",sp(title),re.I)
    if m:return sp(m.group(1)),sp(m.group(2))
    return slug_pair(url)

def load_strict():
    j=json.loads((ROOT/"public_phase2"/"PROFILE_GAP_AUDIT.json").read_text())
    return j.get("fighters",[])

def resolve(hint,names):
    n=norm_name(hint)
    if not n:return None
    exact=[x for x in names if norm_name(x)==n]
    if len(exact)==1:return exact[0]
    ht=n.split()
    if len(ht)==1:
        c=[x for x in names if norm_name(x).split() and norm_name(x).split()[-1]==ht[0]]
        return c[0] if len(c)==1 else None
    c=[x for x in names if norm_name(x).startswith(n+" ") or n.startswith(norm_name(x)+" ")]
    return c[0] if len(c)==1 else None

def cm(v,kind):
    x=sp(v).lower()
    # Prefer explicit cm.
    m=re.search(r"(\d{2,3}(?:\.\d+)?)\s*cm\b",x)
    if m:return float(m.group(1))
    # Inches for reach.
    if kind=="reach_cm":
        m=re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:in|inch|inches|\")",x)
        if m:return round(float(m.group(1))*2.54,1)
    # ft/in height.
    if kind=="height_cm":
        m=re.search(r"(\d)\s*['′]\s*(\d{1,2})",x)
        if m:return round((int(m.group(1))*12+int(m.group(2)))*2.54,1)
    return None

def numeric(v):
    m=re.search(r"-?\d+(?:\.\d+)?",sp(v).replace(",",""))
    return float(m.group()) if m else None

def canonical_key(label):
    x=sp(label).lower()
    if "reach" in x:return "reach_cm"
    if "height" in x:return "height_cm"
    if "stance" in x:return "stance"
    if "national" in x or x in ("country","country/region"):return "nationality"
    if re.fullmatch(r"(?:fighter )?age",x):return "age"
    if re.search(r"\b(rounds boxed|career rounds|total rounds)\b",x):return "rounds_boxed"
    if "debut" in x:return "debut"
    if "division" in x or "weight class" in x:return "division"
    if "title" in x:return "title_context"
    if re.fullmatch(r"(?:pro )?record",x):return "record"
    if re.fullmatch(r"wins?",x):return "wins"
    if re.fullmatch(r"loss(?:es)?",x):return "losses"
    if re.fullmatch(r"draws?",x):return "draws"
    if re.search(r"\bko\b|knockout",x):
        if "%" in x or "rate" in x or "percentage" in x:return "ko_pct"
        return "ko_wins"
    return None

def value_for(key,v):
    if key in ("height_cm","reach_cm"):
        z=cm(v,key)
        if z is None or not 130<=z<=230:return None
        return z
    if key=="stance":
        x=sp(v).lower()
        if x not in ("orthodox","southpaw","switch","switch-hitter","switch hitter"):return None
        return "switch" if x.startswith("switch") else x
    if key in ("age","rounds_boxed","wins","losses","draws","ko_wins","ko_pct"):
        z=numeric(v)
        if z is None:return None
        if key=="age" and not 16<=z<=60:return None
        if key=="ko_pct" and not 0<=z<=100:return None
        if key in ("rounds_boxed","wins","losses","draws","ko_wins") and z<0:return None
        return int(z) if float(z).is_integer() else z
    return sp(v) or None

def name_like(value):
    x=norm_name(value)
    toks=x.split()
    return len(toks)>=2 and not any(w in x for w in ("fighter 1","fighter 2","fighter a","fighter b","value","statistic","attribute"))

def parse_article(url,names,strict_map):
    r=get(url)
    if not r:return []
    soup=BeautifulSoup(r.text,"html.parser")
    h=soup.find("h1");title=sp(h.get_text(" ",strip=True) if h else soup.title.get_text(" ",strip=True) if soup.title else "")
    a,b=infer_pair(title,url)
    ra,rb=resolve(a,names),resolve(b,names)
    candidates=[]
    for ti,t in enumerate(soup.find_all("table")):
        matrix=[]
        for tr in t.find_all("tr"):
            cells=[sp(x.get_text(" ",strip=True)) for x in tr.find_all(["th","td"])]
            if cells:matrix.append(cells)
        if len(matrix)<2:continue
        blob=" ".join(" ".join(x) for x in matrix)
        if not PROFILE_KEYS.search(blob):continue

        # Strict tale-of-tape form. Never assume columns 2/3 belong to
        # the title fighters unless the header itself identifies those fighters.
        hdr=matrix[0]
        if len(hdr)>=3:
            colfighters=[]
            for ci in (1,2):
                label=hdr[ci] if ci<len(hdr) else ""
                resolved=resolve(label,names) if name_like(label) else None
                colfighters.append(resolved)
            # Some pages use surname-only headers. Permit those only when the
            # article pair is full-name-resolved and the header uniquely matches
            # the resolved fighter surname.
            if not all(colfighters) and ra and rb and all(len(norm_name(x).split())>=2 for x in (a,b)):
                h1,h2=norm_name(hdr[1]),norm_name(hdr[2])
                ra_n,rb_n=norm_name(ra),norm_name(rb)
                if h1 and h2 and h1 in ra_n.split() and h2 in rb_n.split():
                    colfighters=[ra,rb]
            if len(colfighters)==2 and all(colfighters) and colfighters[0]!=colfighters[1]:
                for row in matrix[1:]:
                    if len(row)<3:continue
                    key=canonical_key(row[0])
                    if not key:continue
                    for fighter,val,side in ((colfighters[0],row[1],"A"),(colfighters[1],row[2],"B")):
                        vv=value_for(key,val)
                        if vv is None:continue
                        candidates.append((fighter,key,vv,val,ti,side))
        # Two-column Field/Value tables are accepted only when exactly one
        # full-name fighter is resolvable from the article identity.
        if len(hdr)==2 and bool(ra)^bool(rb):
            fighter=ra or rb
            hint=a if ra else b
            if hint and len(norm_name(hint).split())>=2:
                for row in matrix[1:]:
                    if len(row)<2:continue
                    key=canonical_key(row[0])
                    if not key:continue
                    vv=value_for(key,row[1])
                    if vv is not None:candidates.append((fighter,key,vv,row[1],ti,"single"))

    out=[]
    for fighter,key,vv,raw,ti,side in candidates:
        sf=strict_map.get(fighter,{})
        present=(sf.get("present") or {}).get(key)
        missing=key in set(sf.get("missing") or [])
        out.append({
          "fighter":fighter,"field":key,"candidate_value":vv,"raw_value":raw,
          "currently_missing":missing,"strict_existing_value":present,
          "source_url":url,"article_title":title,"table_index":ti,"side":side,
          "source":"boxing-data.com public article","lane":"BROAD",
          "policy":"candidate_corroboration_only"
        })
    return out

def main():
    strict=load_strict();names=[x["name"] for x in strict if x.get("name")];strict_map={x["name"]:x for x in strict if x.get("name")}
    urls=discover();rows=[]
    for i,u in enumerate(urls):
        if i:time.sleep(SLEEP)
        rows.extend(parse_article(u,names,strict_map))
    # exact duplicate collapse
    uniq={}
    for r in rows:
        k=(r["fighter"],r["field"],str(r["candidate_value"]),r["source_url"])
        uniq[k]=r
    rows=list(uniq.values())
    missing=[r for r in rows if r["currently_missing"]]
    fields=defaultdict(int);missing_fields=defaultdict(int)
    for r in rows:fields[r["field"]]+=1
    for r in missing:missing_fields[r["field"]]+=1
    fighters_missing=sorted({r["fighter"] for r in missing})
    with ROWS.open("w",newline="",encoding="utf-8") as f:
        cols=list(rows[0].keys()) if rows else ["fighter","field","candidate_value","raw_value","currently_missing","strict_existing_value","source_url","article_title","table_index","side","source","lane","policy"]
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    report={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "status":"BROAD_SUPPLEMENT_CANDIDATES_NOT_AUTO_MERGED",
      "articles_scanned":len(urls),
      "strict_fighters":len(names),
      "candidate_rows":len(rows),
      "candidate_fighters":len({r["fighter"] for r in rows}),
      "candidate_rows_by_field":dict(sorted(fields.items())),
      "currently_missing_candidate_rows":len(missing),
      "currently_missing_candidate_fighters":len(fighters_missing),
      "missing_candidates_by_field":dict(sorted(missing_fields.items())),
      "missing_candidate_fighters":fighters_missing,
      "policy":[
        "No Boxing Data public value overwrites an existing strict profile.",
        "Reach/height candidates are corroboration sources and must satisfy the existing first-party or two-independent-source rule before strict merge.",
        "Age/record/round counts from articles are point-in-time context candidates, not timeless profile facts.",
        "Identity matching is conservative and unresolved names are discarded."
      ]
    }
    REPORT.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__":main()
