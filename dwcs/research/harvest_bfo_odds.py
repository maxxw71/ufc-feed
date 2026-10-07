from __future__ import annotations
import os,re,time,json,urllib.parse
from pathlib import Path
import pandas as pd, requests
from bs4 import BeautifulSoup

ROOT=Path(os.environ.get("ROOT","."))
OUT=Path(os.environ["OUT"]); OUT.mkdir(parents=True,exist_ok=True)
SRC=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
f=pd.read_csv(SRC,low_memory=False)
f["event_date"]=pd.to_datetime(f.event_date,errors="coerce")

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def american_tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

def fmt_date(dt):
    m=dt.strftime("%b")
    d=dt.day
    suf="th" if 10<=d%100<=20 else {1:"st",2:"nd",3:"rd"}.get(d%10,"th")
    return f"{m} {d}{suf} {dt.year}"

ses=requests.Session()
ses.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Odds/1.0","Accept-Language":"en-US,en;q=0.9"})

cache={}
rows=[]
stats={"fights":len(f),"profile_resolved":0,"priced_rows":0,"search_503":0,"errors":0}

def get(url,tries=5):
    for i in range(tries):
        r=ses.get(url,timeout=25)
        if r.status_code==200:return r
        if r.status_code in (429,503):
            stats["search_503"]+=1
            time.sleep(min(12,1.5*(i+1)))
            continue
        return r
    return r

def resolve(name):
    k=norm(name)
    if k in cache:return cache[k]
    q="https://www.bestfightodds.com/search?query="+urllib.parse.quote_plus(name)
    r=get(q)
    if r.status_code!=200:
        cache[k]=None; return None
    soup=BeautifulSoup(r.text,"html.parser")
    choices=[]
    for a in soup.find_all("a",href=True):
        if not a["href"].startswith("/fighters/"): continue
        txt=" ".join(a.get_text(" ",strip=True).split())
        if norm(txt)==k:
            url="https://www.bestfightodds.com"+a["href"]
            cache[k]=url; stats["profile_resolved"]+=1; return url
    cache[k]=None; return None

for i,r in f.iterrows():
    # Try both sides; first successful exact profile with a matching fight row wins.
    found=False
    for selected,opp in [(r.fighter_a,r.fighter_b),(r.fighter_b,r.fighter_a)]:
        try:
            purl=resolve(str(selected))
            if not purl: continue
            pr=get(purl)
            if pr.status_code!=200: continue
            soup=BeautifulSoup(pr.text,"html.parser")
            trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
            date_txt=fmt_date(r.event_date)
            sn=norm(selected); on=norm(opp)
            # Candidate selected-fighter rows around exact event date / contender label.
            for j,txt in enumerate(trs):
                low=txt.lower()
                if "contender series" not in low or sn not in norm(txt): continue
                # nearby rows must carry exact event date somewhere.
                window=" ".join(trs[max(0,j-2):min(len(trs),j+4)])
                if str(r.event_date.year) not in window: continue
                if date_txt.split()[0].lower() not in window.lower(): continue
                toks=american_tokens(txt)
                if not toks: continue
                # BestFightOdds fighter rows: first token=open, later tokens compose close range.
                open_odds=toks[0]
                # closing representative: final American token before movement % is generally last odds token;
                # reject absurd movement-like values by keeping plausible American prices.
                plausible=[x for x in toks[1:] if abs(x)>=100 and abs(x)<=10000]
                close_odds=plausible[-1] if plausible else open_odds

                opp_open=opp_close=None
                # opponent is usually in next 1-3 rows.
                for k2 in range(j+1,min(len(trs),j+4)):
                    t2=trs[k2]
                    if on in norm(t2):
                        ot=american_tokens(t2)
                        if ot:
                            opp_open=ot[0]
                            op=[x for x in ot[1:] if abs(x)>=100 and abs(x)<=10000]
                            opp_close=op[-1] if op else opp_open
                        break

                winner=norm(r.winner)
                rows.append({
                    "season":r.season,"event_date":r.event_date.date().isoformat(),
                    "event_name":r.event_name,"fighter_a":r.fighter_a,"fighter_b":r.fighter_b,
                    "winner":r.winner,"selected_profile":selected,"opponent":opp,
                    "selected_open":open_odds,"selected_close":close_odds,
                    "opponent_open":opp_open,"opponent_close":opp_close,
                    "selected_won":winner==sn,"profile_url":purl,"raw_row":txt
                })
                stats["priced_rows"]+=1; found=True; break
            if found: break
            time.sleep(.03)
        except Exception as e:
            stats["errors"]+=1
    if (i+1)%25==0:
        print(i+1,stats)
        pd.DataFrame(rows).drop_duplicates(["event_date","fighter_a","fighter_b"]).to_csv(OUT/"historical_odds_partial.csv",index=False)

od=pd.DataFrame(rows).drop_duplicates(["event_date","fighter_a","fighter_b"])
od.to_csv(OUT/"historical_odds.csv",index=False)
stats["unique_priced_fights"]=int(len(od))
stats["coverage"]=float(len(od)/len(f)) if len(f) else 0
(OUT/"status.json").write_text(json.dumps(stats,indent=2)+"\n")
print(json.dumps(stats,indent=2))
