#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import requests
from lxml import html

ROOT=Path.home()/"ufc-predictor-v1"
AR=ROOT/"auto_research"
CURRENT=AR/"raw"/"ufcstats_competitions_current.csv"
UA="appwiza-ufcstats-live/1.0"
EVENTS_URL="http://ufcstats.com/statistics/events/completed?page=all"

META_FIELDS=["result","player1","player2","player1_url","player2_url","weightclass","method","round","time","time_format","referee","details","player1_nickname","player2_nickname","event_date","event_location","event_url"]
TOTAL=["KD","Sig_str","Total_str","Td","Sub_att","Rev","Ctrl"]
SIG=["Head","Body","Leg","Distance","Clinch","Ground"]
FIELDS=META_FIELDS+[f"{p}_rd{r}_{s}" for p in ("p1","p2") for s in TOTAL for r in range(1,6)]+[f"{p}_rd{r}_{s}" for p in ("p1","p2") for s in SIG for r in range(1,6)]

NONCE_RE=re.compile(r"\bnonce\s*=\s*['\"]([0-9a-fA-F]{1,128})['\"]")
DIFF_RE=re.compile(r"\btarget\s*=\s*new\s+Array\(\s*(\d+)\s*\+\s*1\s*\)\.join\(\s*['\"]0['\"]\s*\)")

def clean(v):
    return " ".join(str(v or "").split()).strip()

def one(tree,xp):
    vals=tree.xpath(xp)
    if not vals:return ""
    v=vals[0]
    return clean(v if isinstance(v,str) else v.text_content())

def challenge(resp):
    body=resp.text
    return resp.status_code==200 and "<title>Loading" in body and "Checking your browser" in body

def solve(resp,max_work=2_000_000):
    nm=NONCE_RE.search(resp.text);dm=DIFF_RE.search(resp.text)
    if not nm or not dm:raise RuntimeError("UFCStats challenge format changed")
    nonce=nm.group(1);difficulty=int(dm.group(1))
    if difficulty<1 or difficulty>5:raise RuntimeError(f"unsupported UFCStats challenge difficulty {difficulty}")
    target="0"*difficulty
    for n in range(max_work):
        if hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest().startswith(target):return nonce,n
    raise RuntimeError("UFCStats proof-of-work exceeded work limit")

def get(session,url,timeout=45):
    r=session.get(url,timeout=timeout)
    if challenge(r):
        nonce,n=solve(r);u=urlsplit(url)
        c=session.post(f"{u.scheme}://{u.netloc}/__c",data={"nonce":nonce,"n":str(n)},timeout=timeout)
        if c.status_code!=204 or "_fmc" not in c.headers.get("Set-Cookie",""):
            raise RuntimeError(f"UFCStats clearance rejected HTTP {c.status_code}")
        r=session.get(url,timeout=timeout)
    r.raise_for_status();return r

def event_urls(session):
    tree=html.fromstring(get(session,EVENTS_URL,60).content)
    out=[]
    for td in tree.xpath('//td[contains(@class,"b-statistics__table-col")]'):
        if td.xpath('.//img[contains(@src,"/next.png")]'):continue
        href=td.xpath('.//a/@href')
        if href:
            u=clean(href[0])
            if "/event-details/" in u and u not in out:out.append(u)
    return out

def parse_section(tree,section_xpath):
    section=tree.xpath(section_xpath)
    if not section:return {}
    rows=section[0].xpath('.//tr[@class="b-fight-details__table-row"]')
    if not rows:return {}
    names=[clean(x) for x in rows[0].xpath("./th/text()") if clean(x)][1:]
    if names.count("Td %")>1:names[names.index("Td %")]="Td"
    out={}
    for ridx,row in enumerate(rows[1:],1):
        fighters=[clean(x) for x in row.xpath("./td/p/a/text()") if clean(x)]
        data=[clean(x) for x in row.xpath("./td/p/text()")[2:] if clean(x)]
        for fidx,_ in enumerate(fighters[:2]):
            vals=data[fidx::2]
            for sidx,val in enumerate(vals):
                if sidx>=len(names):break
                key=f"{'p1' if fidx==0 else 'p2'}_rd{ridx}_{names[sidx]}".replace(".","").replace(" ","_")
                if "%" not in key:out[key]=val
    completed={}
    for key in [k for k in out if "_rd1_" in k]:
        for rnd in range(1,6):
            rk=key.replace("rd1",f"rd{rnd}");completed[rk]=out.get(rk,np.nan)
    return completed

def parse_fight(session,url,event_date,event_location,event_url):
    tree=html.fromstring(get(session,url,45).content)
    body=" ".join(tree.xpath("//text()"))
    if "not currently available" in body.lower():return None
    fighters=[clean(x) for x in tree.xpath('//h3[contains(@class,"b-fight-details__person-name")]/a/text()') if clean(x)]
    if len(fighters)<2:return None
    urls=[clean(x) for x in tree.xpath('//h3[contains(@class,"b-fight-details__person-name")]/a/@href')]
    nick=[clean(x).replace('"',"") or "--" for x in tree.xpath('//div[contains(@class,"b-fight-details__person")]//p/text()') if clean(x)]
    wcs=[clean(x) for x in tree.xpath("/html/body/section/div/div/div[2]/div[1]/i/text()") if clean(x)]
    details=" ".join(clean(x) for x in tree.xpath('//i[contains(text(),"Details:")]/ancestor::p[1]/text()') if clean(x))
    row={
      "result":one(tree,"/html/body/section/div/div/div[1]/div[1]/i/text()"),
      "player1":fighters[0],"player2":fighters[1],
      "player1_url":urls[0] if len(urls)>0 else "","player2_url":urls[1] if len(urls)>1 else "",
      "weightclass":wcs[0] if wcs else "",
      "method":one(tree,'//i[contains(text(),"Method:")]/following-sibling::i[@style="font-style: normal"]/text()'),
      "round":one(tree,'//i[contains(text(),"Round:")]/following-sibling::text()'),
      "time":one(tree,'//i[contains(text(),"Time:")]/following-sibling::text()'),
      "time_format":one(tree,'//i[contains(text(),"Time format:")]/following-sibling::text()'),
      "referee":one(tree,'//i[contains(text(),"Referee:")]/following-sibling::span/text()'),
      "details":clean(details),"player1_nickname":nick[0] if len(nick)>0 else "--","player2_nickname":nick[1] if len(nick)>1 else "--",
      "event_date":event_date,"event_location":event_location,"event_url":event_url,
    }
    row.update(parse_section(tree,"/html/body/section/div/div/section[3]/table"))
    row.update(parse_section(tree,"/html/body/section/div/div/section[5]/table"))
    return row

def parse_event(session,url):
    tree=html.fromstring(get(session,url,45).content)
    date=one(tree,'//i[contains(text(),"Date:")]/following-sibling::text()')
    loc=one(tree,'//i[contains(text(),"Location:")]/following-sibling::text()')
    links=[]
    for tr in tree.xpath('//tr[contains(@class,"b-fight-details__table-row__hover")]'):
        href=tr.get("data-link") or ""
        if not href:
            onclick=tr.get("onclick") or ""
            m=re.search(r"['\"](https?://[^'\"]+/fight-details/[^'\"]+)['\"]",onclick)
            href=m.group(1) if m else ""
        if href and href not in links:links.append(href)
    rows=[]
    for link in links:
        x=parse_fight(session,link,date,loc,url)
        if x:rows.append(x)
    return rows

def main():
    CURRENT.parent.mkdir(parents=True,exist_ok=True)
    base=pd.read_csv(CURRENT,low_memory=False) if CURRENT.exists() else pd.DataFrame(columns=FIELDS)
    for col in FIELDS:
        if col not in base.columns:base[col]=np.nan
    existing=set(base.get("event_url",pd.Series(dtype=str)).dropna().astype(str))
    s=requests.Session();s.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9"})
    urls=event_urls(s)
    new_urls=[u for u in urls if u not in existing]
    # Listing is newest first. Bound a single refresh so a source reset cannot trigger an accidental full-site scrape.
    new_urls=new_urls[:20]
    rows=[]
    for u in reversed(new_urls):
        try:rows.extend(parse_event(s,u))
        except Exception as exc:print(json.dumps({"ufcstats_event_warning":u,"error":str(exc)}),flush=True)
    if rows:
        nd=pd.DataFrame(rows)
        for col in FIELDS:
            if col not in nd.columns:nd[col]=np.nan
        merged=pd.concat([base[FIELDS],nd[FIELDS]],ignore_index=True)
        merged=merged.drop_duplicates(subset=["event_url","player1_url","player2_url"],keep="first")
    else:merged=base[FIELDS]
    merged.to_csv(CURRENT,index=False)
    dates=pd.to_datetime(merged["event_date"],errors="coerce")
    print(json.dumps({"live_ufcstats_refresh":"ok","new_events":len(new_urls),"new_fights":len(rows),
                      "rows":len(merged),"max_date":str(dates.max().date()) if dates.notna().any() else None,
                      "at":datetime.now(timezone.utc).isoformat(timespec="seconds")},indent=2))

if __name__=="__main__":main()
