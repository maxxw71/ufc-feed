#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import quote
import pandas as pd,requests
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/ufc_com_search_resolved"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def get(url,max_tries=6):
    for k in range(max_tries):
        try:
            r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=35)
            if r.status_code==200:return r
            if r.status_code==429:
                time.sleep(min(8,0.75*(2**k)));continue
            return r
        except Exception:
            if k==max_tries-1:raise
            time.sleep(min(8,0.75*(2**k)))
    return None

def profile_links(txt):
    return list(dict.fromkeys(re.findall(r"https://www\.ufc\.com/athlete/[A-Za-z0-9._~-]+",txt,re.I)))

def search_one(name):
    target="https://www.ufc.com/athletes/all?search="+quote(name,safe="")
    try:
        r=get(JINA+target)
        if r is None:return {"fighter":name,"resolved":False,"error":"no_response"}
        txt=r.text
        links=profile_links(txt)
        if not links:return {"fighter":name,"resolved":False,"search_status":r.status_code,"search_chars":len(txt)}
        q=norm(name)
        scored=[]
        for u in links:
            slug=u.rstrip("/").split("/")[-1].replace("-"," ")
            scored.append((fuzz.WRatio(q,norm(slug)),u))
        scored.sort(reverse=True)
        score,url=scored[0]
        return {"fighter":name,"resolved":score>=75,"profile_url":url if score>=75 else "",
                "slug_score":score,"search_status":r.status_code,"candidate_count":len(links)}
    except Exception as e:
        return {"fighter":name,"resolved":False,"error":repr(e)}

def grab(txt,pat):
    m=re.search(pat,txt,re.I|re.S)
    return m.group(1).strip() if m else ""

def fetch_profile(row):
    name=row["fighter"];url=row["profile_url"]
    try:
        r=get(JINA+url)
        txt=r.text if r is not None and r.status_code==200 else ""
        heading=grab(txt,r"^#\s+([^\n]+)$")
        score=fuzz.WRatio(norm(name),norm(heading)) if heading else 0
        matched=bool(txt and score>=75)
        rec={**row,"profile_status":r.status_code if r is not None else 0,
             "official_heading":heading,"heading_score":score,"verified":matched,
             "snapshot_scope":"current_official_profile_not_historical"}
        if matched:
            rec.update({
              "record_current":grab(txt,r"(\d+-\d+(?:-\d+)?)\s*\(W-L-D\)"),
              "status_current":grab(txt,r"\bStatus\s*\n?\s*([^\n]+)"),
              "place_of_birth":grab(txt,r"Place of Birth\s*\n?\s*([^\n]+)"),
              "trains_at":grab(txt,r"Trains at\s*\n?\s*([^\n]+)"),
              "fighting_style":grab(txt,r"Fighting style\s*\n?\s*([^\n]+)"),
              "age_current":grab(txt,r"\bAge\s*\n?\s*(\d{1,2})\b"),
              "height_current":grab(txt,r"\bHeight\s*\n?\s*([0-9.]+)"),
              "weight_current":grab(txt,r"\bWeight\s*\n?\s*([0-9.]+)"),
              "reach_current":grab(txt,r"\bReach\s*\n?\s*([0-9.]+)"),
              "leg_reach_current":grab(txt,r"\bLeg reach\s*\n?\s*([0-9.]+)"),
              "octagon_debut":grab(txt,r"Octagon Debut\s*\n?\s*([^\n]+)"),
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
              "first_round_finishes_current":grab(txt,r"(\d+)\s+first-round finishes?"),
              "raw_excerpt":txt[:14000]
            })
        return rec
    except Exception as e:
        return {**row,"verified":False,"error":repr(e),"snapshot_scope":"current_official_profile_not_historical"}

def main():
    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    targets=sorted(set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str)))

    # Reuse any already-verified direct matches.
    prior={}
    p=ROOT/"dwcs/research/official_ufc_history/ufc_com_current_profiles.csv"
    if p.exists():
        old=pd.read_csv(p,low_memory=False)
        for _,x in old.iterrows():
            if str(x.get("matched","")).lower()=="true":
                prior[str(x.fighter)]={"fighter":str(x.fighter),"resolved":True,"profile_url":str(x.ufc_url),
                                       "slug_score":100.0,"source":"prior_direct"}
    unresolved=[n for n in targets if n not in prior]
    results=list(prior.values())
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut=[ex.submit(search_one,n) for n in unresolved]
        for i,f in enumerate(as_completed(fut),1):
            results.append(f.result())
            if i%50==0:print("searched",i,"/",len(fut),flush=True)
    rdf=pd.DataFrame(results)
    rdf.to_csv(OUT/"search_resolution.csv",index=False)

    resolved=rdf[rdf.resolved.fillna(False)].copy()
    profiles=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut=[ex.submit(fetch_profile,r.to_dict()) for _,r in resolved.iterrows()]
        for i,f in enumerate(as_completed(fut),1):
            profiles.append(f.result())
            if i%50==0:print("profiles",i,"/",len(fut),flush=True)
    pdf=pd.DataFrame(profiles)
    pdf.to_csv(OUT/"official_profiles.csv",index=False)

    verified=int(pdf.verified.fillna(False).sum()) if len(pdf) and "verified" in pdf else 0
    metric_cols=["sig_str_landed_per_min","sig_str_absorbed_per_min","sig_str_defense_pct",
                 "takedown_avg_15","takedown_defense_pct","submission_avg_15","knockdown_avg",
                 "height_current","reach_current"]
    cov={c:int(pdf[c].astype(str).str.strip().replace("nan","").ne("").sum()) if c in pdf else 0 for c in metric_cols}
    status={"dwcs_unique_fighters":len(targets),"prior_direct_matches":len(prior),
            "search_resolved":int(rdf.resolved.fillna(False).sum()),"verified_official_profiles":verified,
            "verified_profile_rate":verified/len(targets) if targets else 0,
            "metric_coverage":cov,
            "historical_backtest_policy":"Current UFC.com aggregates are source-tagged current-only. Historical method rows use date-bounded UFCStats/DWCS/regional history instead."}
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
