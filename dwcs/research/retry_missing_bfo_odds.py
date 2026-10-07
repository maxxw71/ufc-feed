from __future__ import annotations
import json,re,time,urllib.parse
from pathlib import Path
import pandas as pd,requests
from bs4 import BeautifulSoup

ROOT=Path(".")
SRC=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
OUT=ROOT/"dwcs/research/historical_odds"
OUT.mkdir(parents=True,exist_ok=True)
EXIST=OUT/"historical_odds.csv"

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

def fmt_date(dt):
    m=dt.strftime("%b"); d=dt.day
    suf="th" if 10<=d%100<=20 else {1:"st",2:"nd",3:"rd"}.get(d%10,"th")
    return f"{m} {d}{suf} {dt.year}"

ses=requests.Session()
ses.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Odds-Retry/1.0","Accept-Language":"en-US,en;q=0.9"})

def get(url,tries=8):
    last=None
    for i in range(tries):
        try:
            r=ses.get(url,timeout=30); last=r
            if r.status_code==200:return r
            if r.status_code in (429,503):
                time.sleep(min(45,2*(2**i)));continue
            return r
        except Exception:
            time.sleep(min(30,2*(i+1)))
    return last

cache={}
def resolve(name):
    k=norm(name)
    if k in cache:return cache[k]
    url="https://www.bestfightodds.com/search?query="+urllib.parse.quote_plus(name)
    r=get(url)
    if r is None or r.status_code!=200:
        cache[k]=None;return None
    soup=BeautifulSoup(r.text,"html.parser")
    for a in soup.find_all("a",href=True):
        if a["href"].startswith("/fighters/") and norm(a.get_text(" ",strip=True))==k:
            cache[k]="https://www.bestfightodds.com"+a["href"];return cache[k]
    cache[k]=None;return None

def main():
    fights=pd.read_csv(SRC,low_memory=False)
    fights["event_date"]=pd.to_datetime(fights.event_date,errors="coerce")
    old=pd.read_csv(EXIST,low_memory=False) if EXIST.exists() else pd.DataFrame()
    have=set()
    if len(old):
        for _,x in old.iterrows():
            have.add((str(pd.to_datetime(x.event_date).date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))))
    missing=[]
    for _,x in fights.iterrows():
        k=(str(x.event_date.date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b)))))
        if k not in have:missing.append(x)
    found=[];stats={"missing_start":len(missing),"resolved_new":0,"search_or_fetch_failures":0}
    for i,x in enumerate(missing,1):
        done=False
        for selected,opp in [(x.fighter_a,x.fighter_b),(x.fighter_b,x.fighter_a)]:
            purl=resolve(str(selected))
            if not purl:continue
            pr=get(purl)
            if pr is None or pr.status_code!=200:continue
            soup=BeautifulSoup(pr.text,"html.parser")
            trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
            sn,on=norm(selected),norm(opp); mon=x.event_date.strftime("%b").lower(); yr=str(x.event_date.year)
            for j,txt in enumerate(trs):
                if "contender series" not in txt.lower() or sn not in norm(txt):continue
                win=" ".join(trs[max(0,j-3):min(len(trs),j+5)])
                if yr not in win or mon not in win.lower():continue
                tt=tokens(txt)
                if not tt:continue
                op=tt[0]; plausible=[v for v in tt[1:] if 100<=abs(v)<=10000]
                cl=plausible[-1] if plausible else op
                oo=oc=None
                for k2 in range(j+1,min(len(trs),j+5)):
                    if on in norm(trs[k2]):
                        ot=tokens(trs[k2])
                        if ot:
                            oo=ot[0]; q=[v for v in ot[1:] if 100<=abs(v)<=10000];oc=q[-1] if q else oo
                        break
                found.append({
                  "season":x.season,"event_date":x.event_date.date().isoformat(),"event_name":x.event_name,
                  "fighter_a":x.fighter_a,"fighter_b":x.fighter_b,"winner":x.winner,
                  "selected_profile":selected,"opponent":opp,"selected_open":op,"selected_close":cl,
                  "opponent_open":oo,"opponent_close":oc,"selected_won":norm(x.winner)==sn,
                  "profile_url":purl,"raw_row":txt
                })
                done=True;stats["resolved_new"]+=1;break
            if done:break
            time.sleep(.25)
        if i%10==0:print("retry",i,"/",len(missing),"new",stats["resolved_new"],flush=True)
        time.sleep(.5)

    nd=pd.DataFrame(found)
    merged=pd.concat([old,nd],ignore_index=True) if len(old) else nd
    if len(merged):
        merged=merged.drop_duplicates(["event_date","fighter_a","fighter_b"],keep="first")
    merged.to_csv(EXIST,index=False)
    stats["total_priced_after"]=int(len(merged))
    stats["coverage_after"]=float(len(merged)/len(fights))
    stats["remaining_missing"]=int(len(fights)-len(merged))
    (OUT/"retry_status.json").write_text(json.dumps(stats,indent=2)+"\n")
    print(json.dumps(stats,indent=2))

if __name__=="__main__":main()
