from __future__ import annotations
import re,time,json,urllib.parse
from pathlib import Path
import pandas as pd,requests
from bs4 import BeautifulSoup

ROOT=Path(".")
OUT=ROOT/"dwcs/research/historical_odds_recovery"
OUT.mkdir(parents=True,exist_ok=True)
FIGHTS=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
BASE=ROOT/"dwcs/research/historical_odds/historical_odds.csv"

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def toks(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

def fmt_date(dt):
    m=dt.strftime("%b");d=dt.day
    suf="th" if 10<=d%100<=20 else {1:"st",2:"nd",3:"rd"}.get(d%10,"th")
    return f"{m} {d}{suf} {dt.year}"

s=requests.Session();s.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Odds-Recovery/1.0","Accept-Language":"en-US,en;q=0.9"})
stats={"search_503":0,"http_errors":0,"resolved_profiles":0,"recovered":0}
cache={}

def get(url,tries=10):
    last=None
    for i in range(tries):
        try:r=s.get(url,timeout=30)
        except Exception:
            time.sleep(min(15,2+i*1.5));continue
        last=r
        if r.status_code==200:return r
        if r.status_code in (429,503):
            stats["search_503"]+=1
            time.sleep(min(25,2.5*(i+1)))
            continue
        stats["http_errors"]+=1;return r
    return last

def resolve(name):
    k=norm(name)
    if k in cache:return cache[k]
    q="https://www.bestfightodds.com/search?query="+urllib.parse.quote_plus(name)
    r=get(q)
    if r is None or r.status_code!=200:
        cache[k]=None;return None
    soup=BeautifulSoup(r.text,"html.parser")
    exact=[]
    for a in soup.find_all("a",href=True):
        if not a["href"].startswith("/fighters/"):continue
        txt=" ".join(a.get_text(" ",strip=True).split())
        if norm(txt)==k:exact.append("https://www.bestfightodds.com"+a["href"])
    url=exact[0] if exact else None
    cache[k]=url
    if url:stats["resolved_profiles"]+=1
    time.sleep(0.35)
    return url

f=pd.read_csv(FIGHTS,low_memory=False)
f["event_date"]=pd.to_datetime(f.event_date,errors="coerce")
base=pd.read_csv(BASE,low_memory=False)
basekeys=set((str(x.event_date),norm(x.fighter_a),norm(x.fighter_b)) for _,x in base.iterrows())
missing=[]
for _,x in f.iterrows():
    k=(x.event_date.date().isoformat(),norm(x.fighter_a),norm(x.fighter_b))
    k2=(x.event_date.date().isoformat(),norm(x.fighter_b),norm(x.fighter_a))
    if k not in basekeys and k2 not in basekeys:missing.append(x)
print("missing",len(missing),flush=True)

rows=[]
for idx,r in enumerate(missing,1):
    found=False
    for selected,opp in [(r.fighter_a,r.fighter_b),(r.fighter_b,r.fighter_a)]:
        purl=resolve(str(selected))
        if not purl:continue
        pr=get(purl,tries=6)
        if pr is None or pr.status_code!=200:continue
        soup=BeautifulSoup(pr.text,"html.parser")
        trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
        mon=r.event_date.strftime("%b").lower();yr=str(r.event_date.year)
        sn,on=norm(selected),norm(opp)
        for j,txt in enumerate(trs):
            if "contender series" not in txt.lower() or sn not in norm(txt):continue
            window=" ".join(trs[max(0,j-2):min(len(trs),j+4)])
            if yr not in window or mon not in window.lower():continue
            tt=toks(txt)
            if not tt:continue
            open_odds=tt[0]
            pl=[x for x in tt[1:] if 100<=abs(x)<=10000]
            close_odds=pl[-1] if pl else open_odds
            oo=oc=None
            for k2 in range(j+1,min(len(trs),j+5)):
                t2=trs[k2]
                if on in norm(t2):
                    ot=toks(t2)
                    if ot:
                        oo=ot[0];op=[x for x in ot[1:] if 100<=abs(x)<=10000];oc=op[-1] if op else oo
                    break
            rows.append({"season":r.season,"event_date":r.event_date.date().isoformat(),"event_name":r.event_name,
                         "fighter_a":r.fighter_a,"fighter_b":r.fighter_b,"winner":r.winner,
                         "selected_profile":selected,"opponent":opp,"selected_open":open_odds,"selected_close":close_odds,
                         "opponent_open":oo,"opponent_close":oc,"selected_won":norm(r.winner)==sn,
                         "profile_url":purl,"raw_row":txt,"recovery_source":"BestFightOdds missing-only retry"})
            stats["recovered"]+=1;found=True;break
        if found:break
    if idx%10==0:
        print(idx,"/",len(missing),stats,flush=True)
        pd.DataFrame(rows).drop_duplicates(["event_date","fighter_a","fighter_b"]).to_csv(OUT/"recovered_partial.csv",index=False)
    time.sleep(0.55)

rec=pd.DataFrame(rows).drop_duplicates(["event_date","fighter_a","fighter_b"])
rec.to_csv(OUT/"recovered_odds.csv",index=False)
merged=pd.concat([base,rec],ignore_index=True).drop_duplicates(["event_date","fighter_a","fighter_b"],keep="first")
merged.to_csv(OUT/"historical_odds_merged.csv",index=False)
stats.update({"base_priced":len(base),"recovered_unique":len(rec),"merged_priced":len(merged),"total_fights":len(f),
              "coverage":len(merged)/len(f) if len(f) else 0})
(OUT/"status.json").write_text(json.dumps(stats,indent=2)+"\n")
print(json.dumps(stats,indent=2))
