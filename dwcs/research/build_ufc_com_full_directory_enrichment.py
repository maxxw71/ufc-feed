#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import pandas as pd,requests
from rapidfuzz import fuzz,process

ROOT=Path(".")
OUT=ROOT/"dwcs/research/ufc_com_directory_full"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def get(url,tries=5,timeout=45):
    last=None
    for i in range(tries):
        try:
            r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=timeout)
            last=r
            if r.status_code==200:return r
            if r.status_code==429:
                time.sleep(min(20,2**i));continue
            return r
        except Exception:
            time.sleep(min(10,i+1))
    return last

def grab(txt,pat):
    m=re.search(pat,txt,re.I|re.S)
    return m.group(1).strip() if m else ""

def page_links(p):
    r=get(f"https://www.ufc.com/athletes/all?page={p}",tries=4,timeout=50)
    if r is None:return p,0,[],0
    links=re.findall(r"\[Athlete Profile\]\((https://www\.ufc\.com/athlete/[^)]+)\)",r.text,re.I) if r.status_code==200 else []
    return p,r.status_code,links,len(r.text)

def profile(row):
    r=get(row["profile_url"],tries=5,timeout=40)
    if r is None:return {**row,"verified":False,"error":"no_response"}
    txt=r.text if r.status_code==200 else ""
    slug=row["profile_url"].rstrip("/").split("/")[-1]
    matched=r.status_code==200 and (norm(row["dwcs_fighter"]) in norm(txt) or norm(slug.replace("-"," ")) in norm(txt))
    z={**row,"profile_status":r.status_code,"verified":matched,"snapshot_scope":"current_official_profile_not_historical"}
    if matched:
        z.update({
          "record_current":grab(txt,r"(\d+-\d+(?:-\d+)?)\s*\(W-L-D\)"),
          "age_current":grab(txt,r"\bAge\s*\n?\s*(\d{1,2})\b"),
          "height_current":grab(txt,r"\bHeight\s*\n?\s*([0-9]+\s*['’]\s*[0-9]+(?:\s*[\"”])?|[0-9.]+)"),
          "reach_current":grab(txt,r"\bReach\s*\n?\s*([0-9.]+)"),
          "leg_reach_current":grab(txt,r"\bLeg reach\s*\n?\s*([0-9.]+)"),
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
          "raw_excerpt":txt[:16000]
        })
    return z

def main():
    directory=[]
    empty=0
    for p in range(1,340):
        pg,st,links,chars=page_links(p)
        for u in links:
            directory.append({"page":p,"profile_url":u,"slug":u.rstrip("/").split("/")[-1]})
        if not links:empty+=1
        else:empty=0
        if p%25==0:print("directory page",p,"profiles",len(directory),flush=True)
        if empty>=5 and p>250:break
        time.sleep(.18)
    d=pd.DataFrame(directory).drop_duplicates("profile_url")
    d["slug_norm"]=d.slug.map(lambda x:norm(x.replace("-"," ")))
    d.to_csv(OUT/"official_directory.csv",index=False)

    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    targets=sorted(set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str)))
    exact={x.slug_norm:x for _,x in d.iterrows()}
    pool=list(exact)
    matches=[]
    for name in targets:
        q=norm(name); hit=exact.get(q)
        if hit is not None:
            matches.append({"dwcs_fighter":name,"profile_url":hit.profile_url,"directory_slug":hit.slug,"match_type":"exact","score":100.0})
            continue
        cand=process.extractOne(q,pool,scorer=fuzz.WRatio,score_cutoff=85)
        if not cand:continue
        cn=cand[0]; score=float(cand[1]); z=exact[cn]
        qp=q.split();cp=cn.split()
        same_last=bool(qp and cp and qp[-1]==cp[-1])
        token_score=fuzz.token_set_ratio(q,cn)
        if score>=96 or (score>=91 and same_last) or token_score>=96:
            matches.append({"dwcs_fighter":name,"profile_url":z.profile_url,"directory_slug":z.slug,"match_type":"fuzzy","score":score,"token_score":token_score})
    m=pd.DataFrame(matches).drop_duplicates("dwcs_fighter")
    m.to_csv(OUT/"dwcs_directory_matches.csv",index=False)

    prof=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut=[ex.submit(profile,x.to_dict()) for _,x in m.iterrows()]
        for i,f in enumerate(as_completed(fut),1):
            prof.append(f.result())
            if i%50==0:print("profile pages",i,"/",len(m),flush=True)
    pdf=pd.DataFrame(prof)
    pdf.to_csv(OUT/"official_profile_metrics.csv",index=False)

    status={
      "directory_profiles":len(d),"dwcs_unique_fighters":len(targets),
      "directory_matches":len(m),"directory_match_rate":len(m)/len(targets) if targets else 0,
      "profile_pages_verified":int(pdf.verified.fillna(False).sum()) if len(pdf) and "verified" in pdf else 0,
      "with_height":int(pdf.height_current.astype(str).str.len().gt(0).sum()) if len(pdf) and "height_current" in pdf else 0,
      "with_reach":int(pdf.reach_current.astype(str).str.len().gt(0).sum()) if len(pdf) and "reach_current" in pdf else 0,
      "with_sig_str_landed_per_min":int(pdf.sig_str_landed_per_min.astype(str).str.len().gt(0).sum()) if len(pdf) and "sig_str_landed_per_min" in pdf else 0,
      "with_sig_str_defense":int(pdf.sig_str_defense_pct.astype(str).str.len().gt(0).sum()) if len(pdf) and "sig_str_defense_pct" in pdf else 0,
      "with_td_defense":int(pdf.takedown_defense_pct.astype(str).str.len().gt(0).sum()) if len(pdf) and "takedown_defense_pct" in pdf else 0,
      "current_dynamic_metrics_quarantined_from_historical_backtests":True,
      "height_reach_static_supplement_requires_crosscheck_before_merge":True
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
