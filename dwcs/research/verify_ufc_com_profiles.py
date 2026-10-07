#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd,requests
from rapidfuzz import fuzz

ROOT=Path(".")
SRC=ROOT/"dwcs/research/ufc_com_search_resolved/search_resolution.csv"
OUT=ROOT/"dwcs/research/ufc_com_verified"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def grab(txt,pat):
    m=re.search(pat,txt,re.I|re.M)
    return m.group(1).strip() if m else ""

def fetch(url,tries=6):
    last=None
    for k in range(tries):
        try:
            r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=35)
            last=r
            if r.status_code==200:return r
            if r.status_code==429:
                time.sleep(min(15,1.5*(2**k)));continue
            return r
        except Exception:
            time.sleep(min(10,k+1))
    return last

def parse_one(x):
    name=str(x["fighter"]); url=str(x["profile_url"])
    r=fetch(url)
    if r is None:
        return {**x,"verified":False,"verify_reason":"no_response"}
    txt=r.text if r.status_code==200 else ""
    title=grab(txt,r"^Title:\s*(.+?)\s*\|\s*UFC\s*$")
    h1=grab(txt,r"^#\s+([^\n]+)$")
    official=title or h1
    score=fuzz.WRatio(norm(name),norm(official)) if official else 0
    same_last=bool(norm(name).split() and norm(official).split() and norm(name).split()[-1]==norm(official).split()[-1])
    verified=bool(r.status_code==200 and official and (score>=96 or (score>=92 and same_last)))
    z={**x,"profile_status":r.status_code,"official_name":official,"name_score":score,
       "verified":verified,"verify_reason":"identity_match" if verified else "identity_mismatch",
       "snapshot_scope":"current_official_profile_not_historical"}
    if verified:
        z.update({
          "record_current":grab(txt,r"(\d+-\d+(?:-\d+)?)\s*\(W-L-D\)"),
          "age_current":grab(txt,r"\bAge\s*\n?\s*(\d{1,2})\b"),
          "height_current":grab(txt,r"\bHeight\s*\n?\s*([0-9]+\s*['’]\s*[0-9]+(?:\s*[\"”])?|[0-9.]+)"),
          "reach_current":grab(txt,r"\bReach\s*\n?\s*([0-9.]+)"),
          "leg_reach_current":grab(txt,r"\bLeg reach\s*\n?\s*([0-9.]+)"),
          "pro_since":grab(txt,r"Pro since\s+(\d{4})"),
          "sig_str_landed_total":grab(txt,r"Sig\. Strikes Landed\s+(\d+)"),
          "sig_str_attempted_total":grab(txt,r"Sig\. Strikes Attempted\s+(\d+)"),
          "sig_str_landed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Landed\s*Per Min"),
          "sig_str_absorbed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Absorbed\s*Per Min"),
          "sig_str_defense_pct":grab(txt,r"(\d{1,3})\s*%?\s*Sig\. Str\. Defense"),
          "takedowns_landed_total":grab(txt,r"Takedowns Landed\s+(\d+)"),
          "takedowns_attempted_total":grab(txt,r"Takedowns Attempted\s+(\d+)"),
          "takedown_avg_15":grab(txt,r"([0-9.]+)\s*Takedown avg\s*Per 15 Min"),
          "takedown_defense_pct":grab(txt,r"(\d{1,3})\s*%?\s*Takedown Defense"),
          "submission_avg_15":grab(txt,r"([0-9.]+)\s*Submission avg\s*Per 15 Min"),
          "knockdown_avg":grab(txt,r"([0-9.]+)\s*Knockdown Avg"),
          "first_round_finishes_current":grab(txt,r"(\d+)\s+first-round finishes?")
        })
    return z

def main():
    d=pd.read_csv(SRC,low_memory=False)
    d=d[d.resolved.fillna(False)&d.profile_url.notna()&d.profile_url.astype(str).str.len().gt(0)].copy()
    # Prefer high-confidence URL candidates first; low-scoring fuzzy matches are still fetched
    # but must pass page-name verification.
    rows=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        fut=[ex.submit(parse_one,x.to_dict()) for _,x in d.iterrows()]
        for i,f in enumerate(as_completed(fut),1):
            rows.append(f.result())
            if i%50==0:print("verified pages",i,"/",len(fut),flush=True)
    out=pd.DataFrame(rows)
    out.to_csv(OUT/"verified_profiles.csv",index=False)
    v=out[out.verified.fillna(False)].copy()
    metric_cols=["height_current","reach_current","sig_str_landed_per_min","sig_str_absorbed_per_min",
                 "sig_str_defense_pct","takedown_avg_15","takedown_defense_pct","submission_avg_15","knockdown_avg"]
    status={
      "candidate_urls":len(d),
      "verified_profiles":len(v),
      "verified_rate_of_dwcs_633":len(v)/633,
      "rejected_identity_mismatches":int((~out.verified.fillna(False)).sum()),
      "metric_coverage":{c:int(v[c].astype(str).str.strip().replace("nan","").ne("").sum()) if c in v else 0 for c in metric_cols},
      "historical_backtest_policy":"Current UFC.com dynamic aggregates remain excluded from historical backtests. Static identity/physical fields may be merged only after source cross-check."
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
