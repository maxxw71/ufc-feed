from __future__ import annotations
import os,re,time,json,csv
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

OUT=Path(os.environ["OUT"]); OUT.mkdir(parents=True,exist_ok=True)
BASE="https://www.tapology.com"
PROMO="/fightcenter/promotions/2026-dana-whites-contender-series-dwcs"
S=requests.Session()
S.headers.update({
 "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
 "Accept-Language":"en-US,en;q=0.9",
})

def get(url):
    last=None
    for i in range(4):
        try:
            r=S.get(url,timeout=30)
            if r.status_code==200:return r.text
            last=RuntimeError(f"{r.status_code} {url}")
        except Exception as e:last=e
        time.sleep(1+i)
    raise last

def clean(s):return re.sub(r"\s+"," ",s or "").strip()
def norm(s):return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",(s or "").lower())).strip()

event_links={}
for page in range(1,15):
    url=BASE+PROMO+(f"?page={page}" if page>1 else "")
    try: html=get(url)
    except Exception as e:
        if page==1: raise
        break
    soup=BeautifulSoup(html,"html.parser")
    found=0
    for a in soup.find_all("a",href=True):
        href=a["href"]
        txt=clean(a.get_text(" ",strip=True))
        if "/fightcenter/events/" in href and ("contender series" in txt.lower() or "dwcs" in txt.lower()):
            full=urljoin(BASE,href.split("?")[0])
            event_links[full]=txt
            found+=1
    if found==0 and page>1: break
    time.sleep(.2)

# Fallback: event links can have truncated anchor text. Capture all event links whose surrounding text says Contender.
if not event_links:
    html=get(BASE+PROMO); soup=BeautifulSoup(html,"html.parser")
    for a in soup.find_all("a",href=True):
        href=a["href"]
        if "/fightcenter/events/" not in href: continue
        parent=clean(a.parent.get_text(" ",strip=True) if a.parent else "")
        if "contender series" in parent.lower():
            event_links[urljoin(BASE,href.split("?")[0])]=clean(a.get_text(" ",strip=True))

events=[]; raw=[]
for eurl,anchor in sorted(event_links.items()):
    html=get(eurl); soup=BeautifulSoup(html,"html.parser")
    txt=clean(soup.get_text(" ",strip=True))
    h=soup.find(["h1","h2"])
    title=clean(h.get_text(" ",strip=True)) if h else anchor
    dm=re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}",txt)
    events.append({"event_url":eurl,"event_name":title,"date_text":dm.group(0) if dm else ""})
    raw.append({"event_url":eurl,"text":txt[:120000]})
    time.sleep(.15)

with (OUT/"tapology_events.csv").open("w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=["event_url","event_name","date_text"]);w.writeheader();w.writerows(events)
with (OUT/"tapology_event_text.jsonl").open("w",encoding="utf-8") as f:
    for r in raw:f.write(json.dumps(r,ensure_ascii=False)+"\n")
(OUT/"status.json").write_text(json.dumps({"event_links":len(event_links),"events_fetched":len(events)},indent=2)+"\n")
print(json.dumps({"event_links":len(event_links),"events_fetched":len(events)},indent=2))
