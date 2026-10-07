#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd,requests
from bs4 import BeautifulSoup
from rapidfuzz import process,fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/ufc_com_full"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://www.ufc.com"
AJAX=BASE+"/views/ajax?_wrapper_format=drupal_ajax"
JINA="https://r.jina.ai/"
UA="Mozilla/5.0 Appwiza-UFC-Directory/1.0"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def parse_directory_markdown(txt):
    rows=[]
    seen=set()
    for m in re.finditer(r"https://www\\.ufc\\.com/athlete/[A-Za-z0-9._~-]+",txt,re.I):
        url=m.group(0).rstrip(".,)")
        url=url.split("?")[0].rstrip("/")
        if url in seen: continue
        seen.add(url)
        slug=url.rsplit("/",1)[-1]
        rows.append({"name":slug.replace("-"," ").title(),"url":url})
    return rows

def fetch_directory_page(page):
    target=f"https://www.ufc.com/athletes/all/active?filters%5B0%5D=status%3A23&page={page}"
    try:
        r=requests.get(JINA+target,headers={"User-Agent":"ufc-feed/1.1","Accept":"text/plain"},timeout=35)
        if r.status_code!=200:return page,[],r.status_code
        return page,parse_directory_markdown(r.text),r.status_code
    except Exception:
        return page,[],0

def grab(txt,pat):
    m=re.search(pat,txt,re.I|re.S)
    return m.group(1).strip() if m else ""

def profile_fetch(row):
    url=row["url"]
    try:
        r=requests.get(JINA+url,headers={"User-Agent":"ufc-feed/1.1","Accept":"text/plain"},timeout=35)
        txt=r.text if r.status_code==200 else ""
        official_name=grab(txt,r"^#\s+([^\n]+)$")
        matched=r.status_code==200 and (norm(row["dwcs_fighter"]) in norm(txt) or norm(row["ufc_name"]) in norm(txt))
        rec={**row,"http_status":r.status_code,"page_matched":matched,"official_heading":official_name}
        if matched:
            rec.update({
              "record_current":grab(txt,r"(\d+-\d+(?:-\d+)?)\s*\(W-L-D\)"),
              "age_current":grab(txt,r"\bAge\s*\n?\s*(\d{1,2})\b"),
              "height_current":grab(txt,r"\bHeight\s*\n?\s*([0-9.]+)"),
              "reach_current":grab(txt,r"\bReach\s*\n?\s*([0-9.]+)"),
              "leg_reach_current":grab(txt,r"\bLeg reach\s*\n?\s*([0-9.]+)"),
              "octagon_debut":grab(txt,r"Octagon Debut\s*\n?\s*([^\n]+)"),
              "pro_since":grab(txt,r"Pro since\s+(\d{4})"),
              "sig_str_landed_total":grab(txt,r"Sig\. Strikes Landed\s+(\d+)"),
              "sig_str_attempted_total":grab(txt,r"Sig\. Strikes Attempted\s+(\d+)"),
              "sig_str_landed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Landed\s*Per Min"),
              "sig_str_absorbed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Absorbed\s*Per Min"),
              "sig_str_defense_pct":grab(txt,r"(\d{1,3})%\s*Sig\. Str\. Defense"),
              "takedowns_landed_total":grab(txt,r"Takedowns Landed\s+(\d+)"),
              "takedowns_attempted_total":grab(txt,r"Takedowns Attempted\s+(\d+)"),
              "takedown_avg_15":grab(txt,r"([0-9.]+)\s*Takedown avg\s*Per 15 Min"),
              "takedown_defense_pct":grab(txt,r"(\d{1,3})%\s*Takedown Defense"),
              "submission_avg_15":grab(txt,r"([0-9.]+)\s*Submission avg\s*Per 15 Min"),
              "knockdown_avg":grab(txt,r"([0-9.]+)\s*Knockdown Avg"),
              "snapshot_scope":"current_official_profile_not_historical"
            })
        return rec
    except Exception as e:
        return {**row,"page_matched":False,"error":repr(e),"snapshot_scope":"current_official_profile_not_historical"}

def main():
    directory=[]
    page_results=[]
    with ThreadPoolExecutor(max_workers=24) as ex:
        fut=[ex.submit(fetch_directory_page,p) for p in range(0,330)]
        for i,f in enumerate(as_completed(fut),1):
            page,cards,status=f.result()
            page_results.append({"page":page,"status":status,"profiles":len(cards)})
            directory.extend(cards)
            if i%50==0: print("directory pages",i,"/330",flush=True)
    d=pd.DataFrame(directory,columns=["name","url"]).drop_duplicates("url")
    if len(d):
        d["norm"]=d["name"].map(norm)
    else:
        d=pd.DataFrame(columns=["name","url","norm"])
    pd.DataFrame(page_results).sort_values("page").to_csv(OUT/"directory_page_status.csv",index=False)
    d.to_csv(OUT/"official_ufc_directory.csv",index=False)

    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    targets=sorted(set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str)))
    bynorm={x.norm:x for _,x in d.iterrows()}
    pool=list(bynorm)
    matches=[]
    for name in targets:
        q=norm(name); hit=bynorm.get(q)
        if hit is not None:
            matches.append({"dwcs_fighter":name,"ufc_name":hit["name"],"url":hit["url"],"match_type":"exact","score":100.0})
            continue
        cand=process.extractOne(q,pool,scorer=fuzz.WRatio,score_cutoff=88)
        if cand:
            z=bynorm[cand[0]]
            qparts=q.split(); cparts=cand[0].split()
            same_last=bool(qparts and cparts and qparts[-1]==cparts[-1])
            same_initial=bool(q[:1] and cand[0][:1] and q[0]==cand[0][0])
            # conservative acceptance
            if cand[1]>=96 or (cand[1]>=92 and same_last and same_initial):
                matches.append({"dwcs_fighter":name,"ufc_name":z["name"],"url":z["url"],"match_type":"fuzzy","score":cand[1]})
    mdf=pd.DataFrame(matches)
    mdf.to_csv(OUT/"dwcs_to_ufc_directory_matches.csv",index=False)

    prof=[]
    if len(mdf):
        with ThreadPoolExecutor(max_workers=16) as ex:
            fut=[ex.submit(profile_fetch,r.to_dict()) for _,r in mdf.iterrows()]
            for i,f in enumerate(as_completed(fut),1):
                prof.append(f.result())
                if i%100==0: print("profiles fetched",i,"/",len(fut),flush=True)
    pdf=pd.DataFrame(prof)
    pdf.to_csv(OUT/"matched_official_ufc_profiles.csv",index=False)

    status={
      "directory_profiles":int(len(d)),
      "dwcs_unique_fighters":int(len(targets)),
      "directory_matches":int(len(mdf)),
      "exact_matches":int((mdf.match_type=="exact").sum()) if len(mdf) else 0,
      "fuzzy_matches":int((mdf.match_type=="fuzzy").sum()) if len(mdf) else 0,
      "profile_pages_verified":int(pdf.page_matched.fillna(False).sum()) if len(pdf) and "page_matched" in pdf else 0,
      "current_profile_metrics_quarantined_from_historical_backtests":True
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
