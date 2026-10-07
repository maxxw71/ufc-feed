from __future__ import annotations
import json,re,time,urllib.parse
from pathlib import Path
import pandas as pd,requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/historical_odds"
SRC=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
EXIST=OUT/"historical_odds.csv"
BASE="https://www.bestfightodds.com"

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def odds_tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-BFO-EventMap/1.0","Accept-Language":"en-US,en;q=0.9"})

def get(url,tries=5):
    last=None
    for i in range(tries):
        try:
            r=s.get(url,timeout=30);last=r
            if r.status_code==200:return r
            if r.status_code in (429,503):
                time.sleep(min(20,2*(i+1)));continue
            return r
        except Exception:
            time.sleep(i+1)
    return last

def discover_events():
    qs=[
      "Dana White's Contender Series 1","Dana White's Contender Series 10",
      "Dana White's Contender Series 20","Dana White's Contender Series 30",
      "Dana White's Contender Series 40","Dana White's Contender Series 50",
      "Dana White's Contender Series 60","Dana White's Contender Series 70",
      "DWCS Week 1","DWCS Week 5","DWCS Week 10",
      "Contender Series 2020","Contender Series 2021","Contender Series 2022",
      "Contender Series 2023","Contender Series 2024","Contender Series 2025","Contender Series 2026"
    ]
    ev={}
    for q in qs:
        r=get(BASE+"/search?query="+urllib.parse.quote_plus(q))
        if r is None or r.status_code!=200:continue
        soup=BeautifulSoup(r.text,"html.parser")
        for a in soup.find_all("a",href=True):
            href=a["href"]; txt=" ".join(a.get_text(" ",strip=True).split())
            if href.startswith("/events/") and ("contender" in txt.lower() or "dwcs" in txt.lower()):
                ev[href]=txt
        time.sleep(.2)
    return ev

def event_pairs(href,title):
    r=get(BASE+href)
    if r is None or r.status_code!=200:return []
    soup=BeautifulSoup(r.text,"html.parser")
    main=soup.select_one("table.odds-table-responsive-header")
    if main is None:return []
    rows=main.select("tbody tr")
    out=[];i=0
    while i<len(rows):
        tr=rows[i]
        rid=tr.get("id","")
        if not rid.startswith("mu-"):
            i+=1;continue
        a=tr.find("a",href=re.compile(r"^/fighters/"))
        b=None
        j=i+1
        while j<len(rows):
            if rows[j].get("id","").startswith("mu-"):break
            x=rows[j].find("a",href=re.compile(r"^/fighters/"))
            if x:
                b=x;break
            j+=1
        if a and b:
            out.append({
              "event_title":title,"event_url":BASE+href,"matchup_id":rid[3:],
              "fighter_a":" ".join(a.get_text(" ",strip=True).split()),"fighter_a_url":BASE+a["href"],
              "fighter_b":" ".join(b.get_text(" ",strip=True).split()),"fighter_b_url":BASE+b["href"]
            })
        i+=1
    return out

def find_pair(target,pairs):
    ta,tb=norm(target.fighter_a),norm(target.fighter_b)
    exact=[]
    for p in pairs:
        if {ta,tb}=={norm(p["fighter_a"]),norm(p["fighter_b"])}: exact.append(p)
    if exact:return exact[0],100.0
    best=None;bs=0
    for p in pairs:
        pa,pb=norm(p["fighter_a"]),norm(p["fighter_b"])
        s1=(fuzz.WRatio(ta,pa)+fuzz.WRatio(tb,pb))/2
        s2=(fuzz.WRatio(ta,pb)+fuzz.WRatio(tb,pa))/2
        sc=max(s1,s2)
        # require both names individually strong
        if sc>bs:
            if s1>=s2: indiv=(fuzz.WRatio(ta,pa),fuzz.WRatio(tb,pb))
            else: indiv=(fuzz.WRatio(ta,pb),fuzz.WRatio(tb,pa))
            if min(indiv)>=88:
                best=p;bs=sc
    return best,bs

def parse_profile_row(profile_url,selected,opp,date,event_title):
    r=get(profile_url)
    if r is None or r.status_code!=200:return None
    soup=BeautifulSoup(r.text,"html.parser")
    trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
    sn,on=norm(selected),norm(opp);yr=str(date.year);mon=date.strftime("%b").lower()
    candidates=[]
    for j,txt in enumerate(trs):
        if "contender series" not in txt.lower() and "dwcs" not in txt.lower():continue
        if sn not in norm(txt):continue
        win=" ".join(trs[max(0,j-3):min(len(trs),j+5)])
        # Event identity came from the exact BFO matchup page, so use that exact
        # event label when it is present. Fall back to year/month only.
        et=norm(event_title)
        if et and et not in norm(txt) and et not in norm(win):
            if yr not in win or mon not in win.lower():continue
        tt=odds_tokens(txt)
        if not tt:continue
        op=tt[0]; plausible=[v for v in tt[1:] if 100<=abs(v)<=10000]
        cl=plausible[-1] if plausible else op
        oo=oc=None
        for k in range(max(0,j-2),min(len(trs),j+5)):
            if k==j:continue
            if on in norm(trs[k]):
                ot=odds_tokens(trs[k])
                if ot:
                    oo=ot[0];qq=[v for v in ot[1:] if 100<=abs(v)<=10000];oc=qq[-1] if qq else oo
                    break
        candidates.append((txt,op,cl,oo,oc))
    if not candidates:return None
    # exact date/month context normally leaves one candidate; use first conservatively
    txt,op,cl,oo,oc=candidates[0]
    return {"selected_open":op,"selected_close":cl,"opponent_open":oo,"opponent_close":oc,"raw_row":txt}

def main():
    fights=pd.read_csv(SRC,low_memory=False)
    fights["event_date"]=pd.to_datetime(fights.event_date,errors="coerce")
    old=pd.read_csv(EXIST,low_memory=False)
    have=set((str(pd.to_datetime(x.event_date).date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))) for _,x in old.iterrows())
    missing=[x for _,x in fights.iterrows() if (str(x.event_date.date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))) not in have]

    events=discover_events()
    allpairs=[]
    for i,(href,title) in enumerate(events.items(),1):
        allpairs.extend(event_pairs(href,title))
        if i%20==0:print("events",i,"/",len(events),"pairs",len(allpairs),flush=True)
        time.sleep(.08)
    pd.DataFrame(allpairs).to_csv(OUT/"bfo_event_matchup_map.csv",index=False)

    recovered=[];mapped=0
    for i,x in enumerate(missing,1):
        p,score=find_pair(x,allpairs)
        if p is None:continue
        mapped+=1
        # orient canonical BFO pair to target name
        if fuzz.WRatio(norm(x.fighter_a),norm(p["fighter_a"]))>=fuzz.WRatio(norm(x.fighter_a),norm(p["fighter_b"])):
            selected=x.fighter_a;opp=x.fighter_b;url=p["fighter_a_url"]
        else:
            selected=x.fighter_a;opp=x.fighter_b;url=p["fighter_b_url"]
        q=parse_profile_row(url,selected,opp,x.event_date,p["event_title"])
        if not q:
            # try canonical opponent profile
            alt=p["fighter_b_url"] if url==p["fighter_a_url"] else p["fighter_a_url"]
            q=parse_profile_row(alt,opp,selected,x.event_date,p["event_title"])
            if q:
                # parser selected opponent; flip orientation to retain selected_profile semantics
                selected,opp=opp,selected
        if q:
            recovered.append({
              "season":x.season,"event_date":x.event_date.date().isoformat(),"event_name":x.event_name,
              "fighter_a":x.fighter_a,"fighter_b":x.fighter_b,"winner":x.winner,
              "selected_profile":selected,"opponent":opp,
              "selected_open":q["selected_open"],"selected_close":q["selected_close"],
              "opponent_open":q["opponent_open"],"opponent_close":q["opponent_close"],
              "selected_won":norm(x.winner)==norm(selected),
              "profile_url":url,"raw_row":q["raw_row"],
              "bfo_event_url":p["event_url"],"event_pair_match_score":score,
              "recovery_source":"event_identity_to_fighter_history"
            })
        if i%10==0:print("missing",i,"/",len(missing),"mapped",mapped,"recovered",len(recovered),flush=True)
        time.sleep(.15)

    new=pd.DataFrame(recovered)
    merged=pd.concat([old,new],ignore_index=True)
    merged=merged.drop_duplicates(["event_date","fighter_a","fighter_b"],keep="first")
    merged.to_csv(EXIST,index=False)
    status={
      "event_pages_discovered":len(events),"event_matchups_indexed":len(allpairs),
      "missing_start":len(missing),"missing_mapped_to_canonical_bfo_event_pair":mapped,
      "new_odds_recovered":len(new),"total_priced_after":len(merged),
      "coverage_after":len(merged)/len(fights),"remaining_missing":len(fights)-len(merged)
    }
    (OUT/"event_recovery_status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
