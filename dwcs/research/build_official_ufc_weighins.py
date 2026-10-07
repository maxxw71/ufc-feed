#!/usr/bin/env python3
from __future__ import annotations
import json,re,unicodedata
from pathlib import Path
import pandas as pd,numpy as np,requests
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufc_weighins"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"
URLS={
  6:"https://www.ufc.com/news/season-6-weigh-results-dana-whites-contender-series",
  7:"https://www.ufc.com/news/season-7-weigh-results-dana-whites-contender-series",
  8:"https://www.ufc.com/news/weigh-results-dana-whites-contender-series-season-8",
  9:"https://www.ufc.com/news/weigh-results-dana-whites-contender-series-season-9",
  10:"https://www.ufc.com/news/official-weigh-in-results-dana-whites-contender-series-season-10",
}
def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())
def clean_md(s):
    s=re.sub(r"\[([^\]]+)\]\([^)]*\)",r"\1",s)
    s=re.sub(r"[*_#]"," ",s)
    return " ".join(s.split()).strip()
def fetch(url):
    r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=60)
    r.raise_for_status();return r.text
def parse_page(season,txt,url):
    rows=[];week=None;lines=txt.splitlines()
    for i,raw in enumerate(lines):
        line=clean_md(raw)
        wm=re.search(r"\bweek\s*(\d{1,2})\b",line,re.I)
        if wm and ("weigh" in line.lower() or line.upper().startswith("WEEK") or "official" in line.lower()):
            week=int(wm.group(1))
        if week is None or (" vs " not in line.lower() and " vs." not in line.lower()):continue
        weight_class_label=line.split(":",1)[0].strip() if ":" in line else ""
        body=line.split(":",1)[1].strip() if ":" in line else line
        m=re.search(r"(.+?)\s*\((\d{2,3}(?:\.\d+)?)?\)\s*(\*)?\s*vs\.?\s*(.+?)\s*\((\d{2,3}(?:\.\d+)?)?\)\s*(\*)?",body,re.I)
        if not m:continue
        a=clean_md(m.group(1)).strip(" -")
        b=clean_md(m.group(4)).strip(" -")
        if not a or not b:continue
        wa=float(m.group(2)) if m.group(2) else np.nan
        wb=float(m.group(5)) if m.group(5) else np.nan
        nearby_raw=" ".join(lines[i:i+7])
        note=clean_md(nearby_raw)
        rows.append({"season":season,"week":week,"weight_class_label":weight_class_label,
                     "fighter_a":a,"fighter_b":b,"weight_a":wa,"weight_b":wb,
                     "fighter_a_marker":bool(m.group(3)),"fighter_b_marker":bool(m.group(6)),
                     "weight_gap_abs":abs(wa-wb) if pd.notna(wa) and pd.notna(wb) else np.nan,
                     "note":note,"source_url":url})
    return rows

def main():
    rows=[];pages=[]
    for season,url in URLS.items():
        try:
            txt=fetch(url)
            x=parse_page(season,txt,url);rows.extend(x)
            pages.append({"season":season,"url":url,"status":"ok","parsed_bouts":len(x),"chars":len(txt)})
        except Exception as e:
            pages.append({"season":season,"url":url,"status":"error","error":repr(e),"parsed_bouts":0})
    w=pd.DataFrame(rows)
    if len(w):
        def limit_for(label):
            s=str(label).lower()
            # DWCS non-title allowance: one pound above the class championship limit.
            if "strawweight" in s:return 116.0
            if "flyweight" in s:return 126.0
            if "bantamweight" in s:return 136.0
            if "featherweight" in s:return 146.0
            if "light heavyweight" in s:return 206.0
            if "lightweight" in s:return 156.0
            if "welterweight" in s:return 171.0
            if "middleweight" in s:return 186.0
            if "heavyweight" in s:return 266.0
            return np.nan
        w["class_limit_lb"]=w.weight_class_label.map(limit_for)
        w["fighter_a_missed_weight"]=w.apply(lambda z:bool(z.fighter_a_marker) or
            (pd.notna(z.weight_a) and pd.notna(z.class_limit_lb) and float(z.weight_a)>float(z.class_limit_lb)+1e-9),axis=1)
        w["fighter_b_missed_weight"]=w.apply(lambda z:bool(z.fighter_b_marker) or
            (pd.notna(z.weight_b) and pd.notna(z.class_limit_lb) and float(z.weight_b)>float(z.class_limit_lb)+1e-9),axis=1)
        w["fighter_a_no_weight"]=w.weight_a.isna()
        w["fighter_b_no_weight"]=w.weight_b.isna()
        has_issue=w.fighter_a_missed_weight|w.fighter_b_missed_weight|w.fighter_a_no_weight|w.fighter_b_no_weight
        w["bout_cancelled_weight_or_medical"]=has_issue & w.note.astype(str).str.contains("cancelled|canceled|removed from.*card",case=False,regex=True,na=False)
        w["bout_proceeded_despite_weight_issue"]=(w.fighter_a_missed_weight|w.fighter_b_missed_weight) & w.note.astype(str).str.contains("proceeds as scheduled|proceed as scheduled",case=False,regex=True,na=False)
    w.to_csv(OUT/"official_weighins_s6_s10.csv",index=False)
    pd.DataFrame(pages).to_csv(OUT/"page_status.csv",index=False)

    candidates=[]
    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    for _,x in hist.iterrows():
        m=re.search(r"DWCS\s+(\d+)\.(\d+)",str(x.event_name))
        wk=int(m.group(2)) if m else None
        candidates.append({"season":int(x.season),"week":wk,"event_name":x.event_name,"fighter_a":x.fighter_a,"fighter_b":x.fighter_b})
    s10p=ROOT/"dwcs/data/season10_2026_master.csv"
    if s10p.exists():
        s10=pd.read_csv(s10p,low_memory=False)
        for _,x in s10.iterrows():
            fa=x.get("winner","") if str(x.get("status",""))=="completed" else x.get("fighter_a","")
            fb=x.get("loser","") if str(x.get("status",""))=="completed" else x.get("fighter_b","")
            candidates.append({"season":10,"week":int(x.week),"event_name":"DWCS 10."+str(int(x.week)),"fighter_a":fa,"fighter_b":fb})
    matches=[]
    for c in candidates:
        if c["season"]<6 or c["week"] is None or not len(w):continue
        q=w[(w.season==c["season"])&(w.week==c["week"])]
        best=None;bestscore=0
        for _,z in q.iterrows():
            direct=fuzz.WRatio(norm(c["fighter_a"]),norm(z.fighter_a))+fuzz.WRatio(norm(c["fighter_b"]),norm(z.fighter_b))
            flip=fuzz.WRatio(norm(c["fighter_a"]),norm(z.fighter_b))+fuzz.WRatio(norm(c["fighter_b"]),norm(z.fighter_a))
            s1=max(direct,flip)
            if s1>bestscore:bestscore=s1;best=z
        if best is not None and bestscore>=175:
            rec={"archive_season":c["season"],"archive_week":c["week"],"event_name":c["event_name"],
                 "archive_fighter_a":c["fighter_a"],"archive_fighter_b":c["fighter_b"],"match_score":bestscore}
            rec.update(best.to_dict());matches.append(rec)
    m=pd.DataFrame(matches);m.to_csv(OUT/"matched_archive_weighins.csv",index=False)
    status={"pages_attempted":len(URLS),"pages_ok":sum(x["status"]=="ok" for x in pages),
            "official_weighin_bouts":len(w),"matched_archive_bouts":len(m),
            "weight_a_known":int(w.weight_a.notna().sum()) if len(w) else 0,
            "weight_b_known":int(w.weight_b.notna().sum()) if len(w) else 0,
            "marked_weight_miss_rows":int((w["fighter_a_missed_weight"]|w["fighter_b_missed_weight"]).sum()) if len(w) else 0,
            "cancelled_weight_or_medical_rows":int(w["bout_cancelled_weight_or_medical"].sum()) if len(w) else 0,
            "proceeded_despite_weight_issue_rows":int(w["bout_proceeded_despite_weight_issue"].sum()) if len(w) else 0}
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))
if __name__=="__main__":main()
