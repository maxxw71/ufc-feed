#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import pandas as pd,requests

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufc_prefight_records"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def fetch(url,timeout=45):
    r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=timeout)
    return r.status_code,r.text

def sitemap_urls():
    urls=set(); rows=[]
    for p in range(1,48):
        status,txt=fetch(f"https://www.ufc.com/sitemap.xml?page={p}",60)
        found=re.findall(r"https://www\.ufc\.com/[^\s\]\)<>]+",txt,re.I)
        found=[u.rstrip(".,") for u in found]
        rows.append({"page":p,"status":status,"urls":len(found)})
        urls.update(found)
        if p%10==0: print("sitemap",p,"unique",len(urls),flush=True)
        time.sleep(.15)
    return sorted(urls),rows

def is_dwcs_url(u):
    s=u.lower()
    keys=["contender-series","contender_series","dwcs","dwtncs","dana-whites-tuesday-night-contender","dana-white-tuesday-night-contender"]
    return any(k in s for k in keys) and ("/news/" in s or "/article/" in s or "/event/" in s)

def parse_page(url,txt):
    # Match explicit pre-event matchup headings/lines where records are printed alongside both fighters.
    lines=[" ".join(x.strip().split()) for x in txt.splitlines() if x.strip()]
    matches=[]
    # broad record token; allow draws and NC as fourth field but keep W/L/D.
    pat=re.compile(
      r"([A-Za-zÀ-ÖØ-öø-ÿ'’\.\- ]{2,60}?)\s*\((\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?(?:,?\s*\d+\s*NC)?\)\s*"
      r"(?:VS\.?|vs\.?)\s*"
      r"([A-Za-zÀ-ÖØ-öø-ÿ'’\.\- ]{2,60}?)\s*\((\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?(?:,?\s*\d+\s*NC)?\)",
      re.I
    )
    for line in lines:
        clean=re.sub(r"^[#*\-\s]+","",line)
        for m in pat.finditer(clean):
            a=" ".join(m.group(1).split()); b=" ".join(m.group(5).split())
            # reject prose-like prefixes
            a=re.sub(r"^(?:MAIN EVENT|CO-MAIN EVENT|FEATURED BOUT)\s*[:\-]?\s*","",a,flags=re.I)
            matches.append({
              "source_url":url,"fighter_a":a,"a_wins":int(m.group(2)),"a_losses":int(m.group(3)),"a_draws":int(m.group(4) or 0),
              "fighter_b":b,"b_wins":int(m.group(6)),"b_losses":int(m.group(7)),"b_draws":int(m.group(8) or 0),
              "raw_line":clean[:1000]
            })
    return matches

def main():
    urls,sm=sitemap_urls()
    pd.DataFrame(sm).to_csv(OUT/"sitemap_scan.csv",index=False)
    cand=[u for u in urls if is_dwcs_url(u)]
    (OUT/"candidate_urls.json").write_text(json.dumps(cand,indent=2))
    print("DWCS candidate URLs",len(cand),flush=True)

    allm=[]; fetched=[]
    def job(u):
        try:
            st,txt=fetch(u,45)
            return u,st,len(txt),parse_page(u,txt) if st==200 else []
        except Exception as e:
            return u,0,0,[],repr(e)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut={ex.submit(job,u):u for u in cand}
        for i,f in enumerate(as_completed(fut),1):
            z=f.result()
            if len(z)==4:u,st,n,mm=z;err=""
            else:u,st,n,mm,err=z
            fetched.append({"url":u,"status":st,"chars":n,"matches":len(mm),"error":err})
            allm.extend(mm)
            if i%50==0:print("articles",i,"/",len(cand),"match rows",len(allm),flush=True)
    pd.DataFrame(fetched).to_csv(OUT/"article_fetch_status.csv",index=False)
    raw=pd.DataFrame(allm)
    raw.to_csv(OUT/"raw_record_matchups.csv",index=False)

    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    hist["event_date"]=pd.to_datetime(hist.event_date,errors="coerce").dt.normalize()
    resolved=[]
    if len(raw):
        # pair matching ensures article records belong to the actual historical matchup.
        pairmap={}
        for _,h in hist.iterrows():
            pairmap.setdefault(tuple(sorted((norm(h.fighter_a),norm(h.fighter_b)))),[]).append(h)
        for _,x in raw.iterrows():
            key=tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))
            hs=pairmap.get(key,[])
            if not hs:continue
            for h in hs:
                # Same pair can rarely rematch; UFC preview source is accepted only if record totals
                # are plausible prior to the stored fight.
                for fighter,w,l,d in [(h.fighter_a,x.a_wins,x.a_losses,x.a_draws),(h.fighter_b,x.b_wins,x.b_losses,x.b_draws)]:
                    # orient article record to historical fighter by normalized name
                    if norm(fighter)==norm(x.fighter_a): rw,rl,rd=x.a_wins,x.a_losses,x.a_draws
                    elif norm(fighter)==norm(x.fighter_b): rw,rl,rd=x.b_wins,x.b_losses,x.b_draws
                    else:continue
                    resolved.append({
                      "season":h.season,"dwcs_event":h.event_name,"dwcs_date":h.event_date,
                      "fighter":fighter,"opponent_dwcs":h.fighter_b if norm(fighter)==norm(h.fighter_a) else h.fighter_a,
                      "official_prefight_wins":rw,"official_prefight_losses":rl,"official_prefight_draws":rd,
                      "official_prefight_fights":rw+rl+rd,
                      "source_url":x.source_url,"raw_line":x.raw_line,
                      "point_in_time_source":True
                    })
    rdf=pd.DataFrame(resolved)
    if len(rdf):
        rdf=rdf.drop_duplicates(["dwcs_date","fighter"],keep="first")
    rdf.to_csv(OUT/"official_prefight_records.csv",index=False)

    reg=pd.read_csv(ROOT/"dwcs/research/regional_history/dwcs_prefight_regional_features.csv",low_memory=False)
    reg["dwcs_date"]=pd.to_datetime(reg.dwcs_date,errors="coerce").dt.normalize()
    before=int(reg.regional_history_matched.fillna(False).sum())
    new=0;conflicts=0
    if len(rdf):
        rm={(str(x.dwcs_date.date()),norm(x.fighter)):x for _,x in rdf.iterrows()}
        for _,x in reg.iterrows():
            z=rm.get((str(x.dwcs_date.date()),norm(x.fighter)))
            if z is None:continue
            if not bool(x.regional_history_matched) and z.official_prefight_fights>0:new+=1
            if bool(x.regional_history_matched) and pd.notna(x.prior_fights) and int(x.prior_fights)!=int(z.official_prefight_fights):conflicts+=1
    status={
      "sitemap_unique_urls":len(urls),"dwcs_candidate_urls":len(cand),
      "raw_matchup_records":len(raw),"resolved_fighter_fight_records":len(rdf),
      "regional_rows_before":len(reg),"regional_matched_before":before,
      "new_rows_recoverable_from_official_ufc_records":new,
      "record_count_conflicts_for_manual_review":conflicts,
      "strict_point_in_time":True
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
