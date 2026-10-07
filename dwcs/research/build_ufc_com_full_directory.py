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

def parse_cards(html):
    soup=BeautifulSoup(html,"html.parser")
    rows=[]
    cards=soup.select("li, .view-items-wrp > *, .c-listing-athlete")
    seen=set()
    for card in cards:
        a=card.find("a",href=re.compile(r"^/athlete/|^https://www\.ufc\.com/athlete/",re.I))
        if not a: continue
        href=a.get("href","")
        if href.startswith("/"): href=BASE+href
        if "/athlete/" not in href: continue
        name=""
        for sel in [".c-listing-athlete__name",".field--name-title","h3","h2","span"]:
            t=card.select_one(sel)
            if t:
                txt=" ".join(t.get_text(" ",strip=True).split())
                if txt and txt.lower() not in {"athlete profile","follow"}:
                    name=txt; break
        if not name:
            # profile link often says Athlete Profile, infer from slug
            name=href.rstrip("/").split("/")[-1].replace("-"," ").title()
        key=href.split("?")[0].rstrip("/")
        if key in seen: continue
        seen.add(key)
        rows.append({"name":name,"url":key})
    return rows

def get_initial(session):
    r=session.get(BASE+"/athletes/all",timeout=60)
    r.raise_for_status()
    return r.text

def ajax_page(session,page,view_dom_id=""):
    payload={
      "view_name":"all_athletes",
      "view_display_id":"page",
      "view_args":"",
      "view_path":"/athletes/all",
      "view_base_path":"",
      "pager_element":"0",
      "gender":"All",
      "page":str(page),
      "_drupal_ajax":"1",
      "ajax_page_state[theme]":"ufc",
      "ajax_page_state[theme_token]":""
    }
    if view_dom_id: payload["view_dom_id"]=view_dom_id
    r=session.post(AJAX,data=payload,timeout=60)
    r.raise_for_status()
    data=r.json()
    html_parts=[]
    for item in data if isinstance(data,list) else []:
        if isinstance(item,dict) and isinstance(item.get("data"),str):
            html_parts.append(item["data"])
    return "\n".join(html_parts)

def extract_dom_id(html):
    m=re.search(r"view-dom-id-([a-f0-9]{16,})",html,re.I)
    if m:return m.group(1)
    m=re.search(r'"view_dom_id"\s*:\s*"([^"]+)"',html)
    return m.group(1) if m else ""

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
    s=requests.Session(); s.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9"})
    directory=[]
    initial=""
    try:
        initial=get_initial(s)
        directory.extend(parse_cards(initial))
    except Exception as e:
        print("initial warning",e,flush=True)
    dom=extract_dom_id(initial)
    seen_urls={x["url"] for x in directory}
    empty=0
    for page in range(0,360):
        try:
            html=ajax_page(s,page,dom)
        except Exception as e:
            print("ajax warning",page,e,flush=True)
            empty+=1
            if empty>=5: break
            continue
        cards=parse_cards(html)
        new=0
        for x in cards:
            if x["url"] not in seen_urls:
                directory.append(x);seen_urls.add(x["url"]);new+=1
        if new==0: empty+=1
        else: empty=0
        if page%25==0: print("page",page,"profiles",len(directory),flush=True)
        if empty>=8 and page>20: break
        time.sleep(.03)
    d=pd.DataFrame(directory).drop_duplicates("url")
    d["norm"]=d["name"].map(norm)
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
