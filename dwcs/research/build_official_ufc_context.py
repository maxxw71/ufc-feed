#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from pathlib import Path
import pandas as pd,numpy as np,requests

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufc_context"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"
HUBS=[
 "https://www.ufc.com/news/season-6-weigh-results-dana-whites-contender-series",
 "https://www.ufc.com/news/season-7-weigh-results-dana-whites-contender-series",
 "https://www.ufc.com/news/weigh-results-dana-whites-contender-series-season-8",
 "https://www.ufc.com/news/weigh-results-dana-whites-contender-series-season-9",
 "https://www.ufc.com/news/official-weigh-in-results-dana-whites-contender-series-season-10",
 "https://www.ufc.com/dwcs",
]
def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())
def get(url,tries=5):
    last=None
    for i in range(tries):
        try:r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=45)
        except Exception:
            time.sleep(1+i);continue
        last=r
        if r.status_code==200:return r
        if r.status_code==429:
            time.sleep(min(12,2*(i+1)));continue
        return r
    return last
def news_links(txt):
    links=re.findall(r"https://www\.ufc\.com/news/[A-Za-z0-9._~/%?=&-]+",txt,re.I)
    out=[]
    for u in links:
        u=u.rstrip(".,);]").split("?")[0].rstrip("/")
        low=u.lower()
        if any(k in low for k in ["contender","dwcs","season-","week-","week_"]):
            if u not in out:out.append(u)
    return out
def meta(txt):
    m=re.search(r"^Title:\s*(.+)$",txt,re.M);title=m.group(1).strip() if m else ""
    m=re.search(r"^Published Time:\s*(.+)$",txt,re.M);published=m.group(1).strip() if m else ""
    return title,published
def clean_text(txt):
    txt=re.sub(r"!\[[^\]]*\]\([^)]*\)"," ",txt)
    txt=re.sub(r"\[([^\]]+)\]\([^)]*\)",r"\1",txt)
    txt=re.sub(r"[#*_]"," ",txt)
    return re.sub(r"\s+"," ",txt).strip()

def main():
    links=[];hub_rows=[]
    for hub in HUBS:
        r=get(hub)
        if r is None:
            hub_rows.append({"hub":hub,"status":0,"links":0});continue
        ll=news_links(r.text)
        hub_rows.append({"hub":hub,"status":r.status_code,"links":len(ll)})
        links.extend(ll)
    links=list(dict.fromkeys(links))
    known=[
      "https://www.ufc.com/news/dwcs-season-10-episode-9-preview-athletes-bouts-start-time-streaming",
      "https://www.ufc.com/news/dana-whites-contender-series-season-10-week-9-results",
      "https://www.ufc.com/news/dana-whites-contender-series-season-10-week-7-results",
    ]
    for u in known:
        if u not in links:links.append(u)

    articles=[];texts={}
    for i,u in enumerate(links):
        r=get(u)
        if r is None or r.status_code!=200:
            articles.append({"url":u,"status":0 if r is None else r.status_code});continue
        title,pub=meta(r.text);plain=clean_text(r.text)
        articles.append({"url":u,"status":r.status_code,"title":title,"published":pub,"chars":len(r.text),
                         "short_notice":bool(re.search(r"short notice|short-notice|weeks?['’]? notice|late replacement|stepping in",plain,re.I)),
                         "weight_issue":bool(re.search(r"missed weight|weighed in above|unable to weigh|weight management",plain,re.I))})
        texts[u]=plain
        if i%10==0:print("articles",i+1,"/",len(links),flush=True)
        time.sleep(.12)
    adf=pd.DataFrame(articles);adf.to_csv(OUT/"article_index.csv",index=False)
    pd.DataFrame(hub_rows).to_csv(OUT/"hub_status.csv",index=False)

    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    names=set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str))
    s10p=ROOT/"dwcs/data/season10_2026_master.csv"
    if s10p.exists():
        s10=pd.read_csv(s10p,low_memory=False)
        for col in ["winner","loser","fighter_a","fighter_b"]:
            if col in s10:names|=set(s10[col].dropna().astype(str))
    mentions=[]
    for u,plain in texts.items():
        nplain=norm(plain);row=adf[adf.url==u].iloc[0]
        for name in sorted(names):
            nn=norm(name)
            if len(nn)<4 or nn not in nplain:continue
            surname=nn.split()[-1]
            m=re.search(r".{0,500}\b"+re.escape(surname)+r"\b.{0,900}",plain,re.I)
            ex=m.group(0).strip() if m else ""
            rec_match=re.search(r"\b(\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?\b",ex)
            age_match=re.search(r"\b(\d{2})-year-old\b",ex,re.I)
            mentions.append({"fighter":name,"article_url":u,"title":row.get("title",""),"published":row.get("published",""),
                             "excerpt":ex[:1600],
                             "stated_record":("-".join(x for x in rec_match.groups() if x is not None)) if rec_match else "",
                             "stated_age":int(age_match.group(1)) if age_match else np.nan,
                             "short_notice_context":bool(re.search(r"short notice|short-notice|weeks?['’]? notice|late replacement|stepping in",ex,re.I)),
                             "undefeated_context":bool(re.search(r"undefeated|unbeaten",ex,re.I)),
                             "wrestling_context":bool(re.search(r"wrestl|grappl|judo|jiu-jitsu|jiu jitsu",ex,re.I)),
                             "striking_context":bool(re.search(r"strik|kickbox|boxing|knockout|knockouts|KO",ex,re.I))})
    mdf=pd.DataFrame(mentions);mdf.to_csv(OUT/"fighter_article_mentions.csv",index=False)
    status={"hub_pages":len(HUBS),"discovered_article_links":len(links),
            "articles_fetched":int((adf.status==200).sum()) if len(adf) else 0,
            "fighter_mentions":len(mdf),"fighters_with_mentions":int(mdf.fighter.nunique()) if len(mdf) else 0,
            "short_notice_mentions":int(mdf.short_notice_context.sum()) if len(mdf) else 0,
            "policy":"Article context is source-dated. Only articles published on/before a fight date may feed that fight's historical features."}
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))
if __name__=="__main__":main()
