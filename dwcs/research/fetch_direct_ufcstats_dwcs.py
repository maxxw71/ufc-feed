#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np,pandas as pd,requests
from lxml import html

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufcstats_direct"
OUT.mkdir(parents=True,exist_ok=True)
EVENTS_URL="http://ufcstats.com/statistics/events/completed?page=all"
UA="appwiza-dwcs-ufcstats/1.0"

NONCE_RE=re.compile(r"\bnonce\s*=\s*['\"]([0-9a-fA-F]{1,128})['\"]")
DIFF_RE=re.compile(r"\btarget\s*=\s*new\s+Array\(\s*(\d+)\s*\+\s*1\s*\)\.join\(\s*['\"]0['\"]\s*\)")

def clean(v): return " ".join(str(v or "").split()).strip()
def norm(s): return re.sub(r"[^a-z0-9]+"," ",str(s or "").lower()).strip()
def one(tree,xp):
    vals=tree.xpath(xp)
    if not vals:return ""
    v=vals[0]
    return clean(v if isinstance(v,str) else v.text_content())

def challenge(resp):
    return resp.status_code==200 and "<title>Loading" in resp.text and "Checking your browser" in resp.text
def solve(resp,max_work=2_000_000):
    nm=NONCE_RE.search(resp.text);dm=DIFF_RE.search(resp.text)
    if not nm or not dm: raise RuntimeError("UFCStats challenge format changed")
    nonce=nm.group(1);difficulty=int(dm.group(1));target="0"*difficulty
    for n in range(max_work):
        if hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest().startswith(target):return nonce,n
    raise RuntimeError("UFCStats proof-of-work exceeded work limit")
def get(session,url,timeout=45):
    r=session.get(url,timeout=timeout)
    if challenge(r):
        nonce,n=solve(r);u=urlsplit(url)
        c=session.post(f"{u.scheme}://{u.netloc}/__c",data={"nonce":nonce,"n":str(n)},timeout=timeout)
        if c.status_code!=204: raise RuntimeError(f"UFCStats clearance rejected {c.status_code}")
        r=session.get(url,timeout=timeout)
    r.raise_for_status();return r

def event_index(session):
    tree=html.fromstring(get(session,EVENTS_URL,60).content)
    rows=[]
    for tr in tree.xpath('//tr[contains(@class,"b-statistics__table-row")]'):
        href=tr.xpath('.//a/@href')
        if not href: continue
        u=clean(href[0])
        if "/event-details/" not in u: continue
        rows.append({"event_name":one(tr,'.//a/text()'),
                     "event_date_listing":one(tr,'.//span[contains(@class,"b-statistics__date")]/text()'),
                     "event_url":u})
    return rows

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
    return out

def parse_fight(session,url,event_date,event_name,event_url):
    tree=html.fromstring(get(session,url,45).content)
    if "not currently available" in " ".join(tree.xpath("//text()")).lower(): return None
    fighters=[clean(x) for x in tree.xpath('//h3[contains(@class,"b-fight-details__person-name")]/a/text()') if clean(x)]
    if len(fighters)<2:return None
    urls=[clean(x) for x in tree.xpath('//h3[contains(@class,"b-fight-details__person-name")]/a/@href')]
    row={"event_name":event_name,"event_date":event_date,"event_url":event_url,"fight_url":url,
         "result":one(tree,"/html/body/section/div/div/div[1]/div[1]/i/text()"),
         "player1":fighters[0],"player2":fighters[1],
         "player1_url":urls[0] if len(urls)>0 else "","player2_url":urls[1] if len(urls)>1 else "",
         "weightclass":one(tree,"/html/body/section/div/div/div[2]/div[1]/i/text()"),
         "method":one(tree,'//i[contains(text(),"Method:")]/following-sibling::i[@style="font-style: normal"]/text()'),
         "round":one(tree,'//i[contains(text(),"Round:")]/following-sibling::text()'),
         "time":one(tree,'//i[contains(text(),"Time:")]/following-sibling::text()')}
    row.update(parse_section(tree,"/html/body/section/div/div/section[3]/table"))
    row.update(parse_section(tree,"/html/body/section/div/div/section[5]/table"))
    return row

def parse_event(session,meta):
    tree=html.fromstring(get(session,meta["event_url"],45).content)
    date=one(tree,'//i[contains(text(),"Date:")]/following-sibling::text()') or meta.get("event_date_listing","")
    links=[]
    for tr in tree.xpath('//tr[contains(@class,"b-fight-details__table-row__hover")]'):
        href=tr.get("data-link") or ""
        if not href:
            m=re.search(r"['\"](https?://[^'\"]+/fight-details/[^'\"]+)['\"]",tr.get("onclick") or "")
            href=m.group(1) if m else ""
        if href and href not in links:links.append(href)
    rows=[]
    for link in links:
        x=parse_fight(session,link,date,meta["event_name"],meta["event_url"])
        if x:rows.append(x)
    return rows

def of(v):
    m=re.search(r"([0-9.]+)\s+of\s+([0-9.]+)",str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)
def ctrl(v):
    m=re.match(r"\s*(\d+):(\d+)\s*$",str(v))
    return int(m.group(1))+int(m.group(2))/60 if m else 0.
def num(v):
    try:return float(v)
    except:return 0.
def mins(r):
    try:rnd=int(float(r.get("round",1) or 1))
    except:rnd=1
    m=re.match(r"(\d+):(\d+)",str(r.get("time","0:00")))
    sec=(int(m.group(1))*60+int(m.group(2))) if m else 0
    return max(.1,(rnd-1)*5+sec/60)
def side(r,side,opp):
    M=mins(r); q=dict(mins=M,sig=0.,siga=0.,osig=0.,osiga=0.,tdl=0.,tda=0.,otdl=0.,otda=0.,kd=0.,okd=0.,sub=0.,ctrl=0.,oppctrl=0.)
    for rd in range(1,6):
        a,b=of(r.get(f"{side}_rd{rd}_Sig_str"));q["sig"]+=a;q["siga"]+=b
        a,b=of(r.get(f"{opp}_rd{rd}_Sig_str"));q["osig"]+=a;q["osiga"]+=b
        a,b=of(r.get(f"{side}_rd{rd}_Td"));q["tdl"]+=a;q["tda"]+=b
        a,b=of(r.get(f"{opp}_rd{rd}_Td"));q["otdl"]+=a;q["otda"]+=b
        q["kd"]+=num(r.get(f"{side}_rd{rd}_KD"));q["okd"]+=num(r.get(f"{opp}_rd{rd}_KD"))
        q["sub"]+=num(r.get(f"{side}_rd{rd}_Sub_att"))
        q["ctrl"]+=ctrl(r.get(f"{side}_rd{rd}_Ctrl"));q["oppctrl"]+=ctrl(r.get(f"{opp}_rd{rd}_Ctrl"))
    return q

def rolling(raw):
    state=defaultdict(list);snaps=[]
    raw=raw.copy();raw["_date"]=pd.to_datetime(raw.event_date,errors="coerce")
    raw=raw[raw._date.notna()].sort_values(["_date","event_url","player1","player2"])
    for (_,ev),grp in raw.groupby(["_date","event_url"],sort=True):
        for _,r in grp.iterrows():
            for fighter,opp in [(r.player1,r.player2),(r.player2,r.player1)]:
                h=state[norm(fighter)]
                def sm(k):return sum(x[k] for x in h)
                M=sm("mins");sig=sm("sig");siga=sm("siga");osig=sm("osig");osiga=sm("osiga");tdl=sm("tdl");tda=sm("tda");otdl=sm("otdl");otda=sm("otda");c=sm("ctrl");oc=sm("oppctrl")
                snaps.append({"event_name":r.event_name,"event_date":r.event_date,"fighter":fighter,"opponent":opp,
                  "prior_official_dwcs_fights":len(h),"prior_minutes":M,
                  "sig_l_pm":sig/M if M else np.nan,"sig_abs_pm":osig/M if M else np.nan,
                  "sig_diff_pm":(sig-osig)/M if M else np.nan,"sig_acc":sig/siga if siga else np.nan,"sig_def":1-osig/osiga if osiga else np.nan,
                  "td_l15":15*tdl/M if M else np.nan,"td_a15":15*tda/M if M else np.nan,"td_acc":tdl/tda if tda else np.nan,"td_def":1-otdl/otda if otda else np.nan,
                  "ctrl15":15*c/M if M else np.nan,"ctrl_diff15":15*(c-oc)/M if M else np.nan,
                  "kd15":15*sm("kd")/M if M else np.nan,"kd_abs15":15*sm("okd")/M if M else np.nan,"sub15":15*sm("sub")/M if M else np.nan,
                  "source":"ufcstats.com direct","strict_before_fight_date":True})
        for _,r in grp.iterrows():
            state[norm(r.player1)].append(side(r,"p1","p2"));state[norm(r.player2)].append(side(r,"p2","p1"))
    return pd.DataFrame(snaps)

def main():
    s=requests.Session();s.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9"})
    e=pd.DataFrame(event_index(s));e.to_csv(OUT/"all_completed_event_index.csv",index=False)
    de=e[e.event_name.astype(str).str.contains("contender series",case=False,na=False)].copy()
    de.to_csv(OUT/"dwcs_event_index.csv",index=False)
    rows=[];warnings=[]
    for _,meta in de.iloc[::-1].iterrows():
        try:
            x=parse_event(s,meta);rows.extend(x);print(meta.event_name,len(x),flush=True)
        except Exception as exc:warnings.append({"event_name":meta.event_name,"event_url":meta.event_url,"error":repr(exc)})
        time.sleep(.03)
    raw=pd.DataFrame(rows);raw.to_csv(OUT/"dwcs_official_fight_rows.csv",index=False)
    snap=rolling(raw) if len(raw) else pd.DataFrame();snap.to_csv(OUT/"prefight_direct_ufcstats_snapshots.csv",index=False)
    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    hist["event_date"]=pd.to_datetime(hist.event_date,errors="coerce").dt.date.astype(str)
    matched=0
    if len(raw):
        raw["_d"]=pd.to_datetime(raw.event_date,errors="coerce").dt.date.astype(str)
        keys=set((d,tuple(sorted((norm(a),norm(b))))) for d,a,b in zip(raw._d,raw.player1,raw.player2))
        matched=sum((d,tuple(sorted((norm(a),norm(b))))) in keys for d,a,b in zip(hist.event_date,hist.fighter_a,hist.fighter_b))
    status={"ufcstats_completed_events":len(e),"ufcstats_contender_events":len(de),
      "official_dwcs_fight_rows":len(raw),"archive_expected_fights":len(hist),
      "archive_fights_matched_to_direct_ufcstats":int(matched),"archive_match_rate":float(matched/len(hist)) if len(hist) else 0,
      "snapshot_rows":len(snap),"snapshots_with_prior_official_dwcs_history":int((pd.to_numeric(snap.get("prior_official_dwcs_fights"),errors="coerce")>0).sum()) if len(snap) else 0,
      "warnings":len(warnings)}
    (OUT/"warnings.json").write_text(json.dumps(warnings,indent=2)+"\n");(OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
