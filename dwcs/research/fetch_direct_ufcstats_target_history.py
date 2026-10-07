#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np,pandas as pd,requests
from lxml import html

ROOT=Path(".")
OUT=ROOT/"dwcs/research/ufcstats_target_history"
OUT.mkdir(parents=True,exist_ok=True)
UA="appwiza-dwcs-ufcstats-target-history/1.0"

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
    if not nm or not dm:raise RuntimeError("challenge format changed")
    nonce=nm.group(1);target="0"*int(dm.group(1))
    for n in range(max_work):
        if hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest().startswith(target):return nonce,n
    raise RuntimeError("challenge work exceeded")
def get(s,url,timeout=45):
    r=s.get(url,timeout=timeout)
    if challenge(r):
        nonce,n=solve(r);u=urlsplit(url)
        c=s.post(f"{u.scheme}://{u.netloc}/__c",data={"nonce":nonce,"n":str(n)},timeout=timeout)
        if c.status_code!=204:raise RuntimeError(f"clearance {c.status_code}")
        r=s.get(url,timeout=timeout)
    r.raise_for_status();return r

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
            for sidx,val in enumerate(data[fidx::2]):
                if sidx>=len(names):break
                key=f"{'p1' if fidx==0 else 'p2'}_rd{ridx}_{names[sidx]}".replace(".","").replace(" ","_")
                if "%" not in key:out[key]=val
    return out

def event_targets(s,event_url,target_names):
    tree=html.fromstring(get(s,event_url,45).content)
    date=one(tree,'//i[contains(text(),"Date:")]/following-sibling::text()')
    found=[]
    for tr in tree.xpath('//tr[contains(@class,"b-fight-details__table-row__hover")]'):
        names=[clean(x) for x in tr.xpath('.//a[contains(@href,"fighter-details")]/text()') if clean(x)]
        if len(names)<2:
            names=[clean(x) for x in tr.xpath('.//p[contains(@class,"b-fight-details__table-text")]/a/text()') if clean(x)]
        if len(names)<2:continue
        if not ({norm(names[0]),norm(names[1])}&target_names):continue
        href=tr.get("data-link") or ""
        if not href:
            m=re.search(r"['\"](https?://[^'\"]+/fight-details/[^'\"]+)['\"]",tr.get("onclick") or "")
            href=m.group(1) if m else ""
        if href:found.append({"event_date":date,"player1":names[0],"player2":names[1],"fight_url":href})
    return found

def parse_fight(s,x):
    tree=html.fromstring(get(s,x["fight_url"],45).content)
    fighters=[clean(v) for v in tree.xpath('//h3[contains(@class,"b-fight-details__person-name")]/a/text()') if clean(v)]
    if len(fighters)<2:return None
    row=dict(x);row["player1"]=fighters[0];row["player2"]=fighters[1]
    row["result"]=one(tree,"/html/body/section/div/div/div[1]/div[1]/i/text()")
    row["method"]=one(tree,'//i[contains(text(),"Method:")]/following-sibling::i[@style="font-style: normal"]/text()')
    row["round"]=one(tree,'//i[contains(text(),"Round:")]/following-sibling::text()')
    row["time"]=one(tree,'//i[contains(text(),"Time:")]/following-sibling::text()')
    row.update(parse_section(tree,"/html/body/section/div/div/section[3]/table"))
    row.update(parse_section(tree,"/html/body/section/div/div/section[5]/table"))
    return row

def of(v):
    m=re.search(r"(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)",str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)
def ctrl(v):
    m=re.match(r"\s*(\d+):(\d+)\s*$",str(v))
    return int(m.group(1))+int(m.group(2))/60 if m else 0.
def num(v):
    try:return float(v)
    except:return 0.
def fight_minutes(r):
    try:rnd=max(1,int(float(r.get("round",1) or 1)))
    except:rnd=1
    m=re.match(r"(\d+):(\d+)",str(r.get("time","0:00")))
    sec=int(m.group(1))*60+int(m.group(2)) if m else 0
    return max(.1,(rnd-1)*5+sec/60)
def side_summary(r,side,opp):
    M=fight_minutes(r);q=dict(mins=M,sl=0.,sa=0.,abs=0.,absa=0.,kd=0.,kdabs=0.,tdl=0.,tda=0.,tdallowed=0.,tdfaced=0.,ctrl=0.,oppctrl=0.,sub=0.)
    for rd in range(1,6):
        a,b=of(r.get(f"{side}_rd{rd}_Sig_str"));q["sl"]+=a;q["sa"]+=b
        a,b=of(r.get(f"{opp}_rd{rd}_Sig_str"));q["abs"]+=a;q["absa"]+=b
        q["kd"]+=num(r.get(f"{side}_rd{rd}_KD"));q["kdabs"]+=num(r.get(f"{opp}_rd{rd}_KD"))
        a,b=of(r.get(f"{side}_rd{rd}_Td"));q["tdl"]+=a;q["tda"]+=b
        a,b=of(r.get(f"{opp}_rd{rd}_Td"));q["tdallowed"]+=a;q["tdfaced"]+=b
        q["ctrl"]+=ctrl(r.get(f"{side}_rd{rd}_Ctrl"));q["oppctrl"]+=ctrl(r.get(f"{opp}_rd{rd}_Ctrl"))
        q["sub"]+=num(r.get(f"{side}_rd{rd}_Sub_att"))
    return q

def summarize(rows):
    q=dict(fights=0,mins=0.,sl=0.,sa=0.,abs=0.,absa=0.,kd=0.,kdabs=0.,tdl=0.,tda=0.,tdallowed=0.,tdfaced=0.,ctrl=0.,oppctrl=0.,sub=0.)
    for x in rows:
        for k in q:
            if k=="fights":continue
            q[k]+=x.get(k,0.)
        q["fights"]+=1
    if not q["fights"] or not q["mins"]:return {}
    M=q["mins"];sc=15/M
    return {"prior_direct_ufc_fights":q["fights"],"prior_ufc_minutes":M,
      "ufc_sig_l_pm":q["sl"]/M,"ufc_sig_abs_pm":q["abs"]/M,"ufc_sig_diff_pm":(q["sl"]-q["abs"])/M,
      "ufc_sig_acc":q["sl"]/q["sa"] if q["sa"] else np.nan,"ufc_sig_def":1-q["abs"]/q["absa"] if q["absa"] else np.nan,
      "ufc_kd15":q["kd"]*sc,"ufc_kd_abs15":q["kdabs"]*sc,
      "ufc_td_l15":q["tdl"]*sc,"ufc_td_a15":q["tda"]*sc,"ufc_td_acc":q["tdl"]/q["tda"] if q["tda"] else np.nan,
      "ufc_td_def":1-q["tdallowed"]/q["tdfaced"] if q["tdfaced"] else np.nan,
      "ufc_ctrl15":q["ctrl"]*sc,"ufc_ctrl_diff15":(q["ctrl"]-q["oppctrl"])*sc,"ufc_sub15":q["sub"]*sc}

def main():
    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    targets=sorted(set(hist.fighter_a.dropna().astype(str))|set(hist.fighter_b.dropna().astype(str)))
    target_names={norm(x) for x in targets}
    idx=pd.read_csv(ROOT/"dwcs/research/official_ufcstats_direct/all_completed_event_index.csv",low_memory=False)
    s=requests.Session();s.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9"})
    found=[];warnings=[]
    for i,x in idx.iterrows():
        try:found.extend(event_targets(s,x.event_url,target_names))
        except Exception as e:warnings.append({"event_url":x.event_url,"error":repr(e)})
        if (i+1)%50==0:print("events",i+1,"/",len(idx),"target fight refs",len(found),flush=True)
        time.sleep(.015)
    refs=pd.DataFrame(found).drop_duplicates("fight_url") if found else pd.DataFrame(columns=["event_date","player1","player2","fight_url"])
    refs.to_csv(OUT/"target_fight_refs.csv",index=False)

    parsed=[]
    for i,x in refs.iterrows():
        try:
            z=parse_fight(s,x)
            if z:parsed.append(z)
        except Exception as e:warnings.append({"fight_url":x.fight_url,"error":repr(e)})
        if (i+1)%100==0:print("fight details",i+1,"/",len(refs),flush=True)
        time.sleep(.01)
    raw=pd.DataFrame(parsed)
    raw.to_csv(OUT/"target_official_ufc_fights.csv",index=False)

    # per-fighter technical histories from direct official UFCStats fights
    byfighter={}
    if len(raw):
        raw["_date"]=pd.to_datetime(raw.event_date,errors="coerce").dt.normalize()
        for _,r in raw[raw._date.notna()].sort_values("_date").iterrows():
            byfighter.setdefault(norm(r.player1),[]).append((r._date,side_summary(r,"p1","p2")))
            byfighter.setdefault(norm(r.player2),[]).append((r._date,side_summary(r,"p2","p1")))
    hist["event_date"]=pd.to_datetime(hist.event_date,errors="coerce").dt.normalize()
    snaps=[]
    for _,x in hist.iterrows():
        for fighter,opp in [(x.fighter_a,x.fighter_b),(x.fighter_b,x.fighter_a)]:
            prior=[z for dt,z in byfighter.get(norm(fighter),[]) if pd.notna(x.event_date) and dt<x.event_date]
            rec={"season":x.season,"event_name":x.event_name,"event_date":x.event_date,"fighter":fighter,"opponent":opp,
                 "source":"ufcstats.com direct target history","strict_before_fight_date":True}
            rec.update(summarize(prior))
            snaps.append(rec)
    sdf=pd.DataFrame(snaps);sdf.to_csv(OUT/"prefight_direct_ufc_history.csv",index=False)

    status={"ufcstats_events_scanned":len(idx),"dwcs_target_fighters":len(targets),
            "target_ufc_fight_refs":len(refs),"target_ufc_fights_parsed":len(raw),
            "historical_snapshot_rows":len(sdf),
            "rows_with_prior_direct_ufc_history":int(sdf.prior_direct_ufc_fights.notna().sum()) if "prior_direct_ufc_fights" in sdf else 0,
            "warnings":len(warnings)}
    (OUT/"warnings.json").write_text(json.dumps(warnings,indent=2)+"\n")
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
