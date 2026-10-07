#!/usr/bin/env python3
import csv,gzip,json,re,urllib.request
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"transactions";OUT.mkdir(parents=True,exist_ok=True)

class TxParser(HTMLParser):
    def __init__(self):
        super().__init__();self.in_ul=False;self.in_li=False;self.in_span=False;self.in_p=False
        self.date_buf=[];self.p_text=[];self.p_links=[];self.rows=[];self.current_date=None
        self.a=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="ul" and "page_index" in a.get("class",""):self.in_ul=True
        elif self.in_ul and tag=="li":self.in_li=True;self.current_date=None
        elif self.in_li and tag=="span" and self.current_date is None:self.in_span=True;self.date_buf=[]
        elif self.in_li and tag=="p":self.in_p=True;self.p_text=[];self.p_links=[]
        elif self.in_p and tag=="a":
            self.a={"href":a.get("href"),"from":a.get("data-attr-from"),"to":a.get("data-attr-to"),"text":[]}
    def handle_data(self,data):
        if self.in_span:self.date_buf.append(data)
        if self.in_p:self.p_text.append(data)
        if self.a is not None:self.a["text"].append(data)
    def handle_endtag(self,tag):
        if tag=="span" and self.in_span:
            self.in_span=False;self.current_date=" ".join("".join(self.date_buf).split())
        elif tag=="a" and self.a is not None:
            x=dict(self.a);x["text"]=" ".join("".join(x["text"]).split());self.p_links.append(x);self.a=None
        elif tag=="p" and self.in_p:
            self.in_p=False
            text=" ".join("".join(self.p_text).split())
            if self.current_date and text:self.rows.append({"date":self.current_date,"text":text,"links":self.p_links[:]})
        elif tag=="li" and self.in_li:self.in_li=False;self.current_date=None
        elif tag=="ul" and self.in_ul:self.in_ul=False

def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

def fetch(y):
    url=f"https://www.basketball-reference.com/leagues/NBA_{y}_transactions.html"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; AppwizaNBAResearch/1.0; +https://appwiza.com)"})
    with urllib.request.urlopen(req,timeout=30) as r:return r.read().decode("utf-8","replace")

def iso_date(s):
    for fmt in ("%B %d, %Y","%b %d, %Y"):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except:pass
    return None

allrows=[];coach=[];edges=[]
for y in range(2019,2027):
    body=fetch(y);p=TxParser();p.feed(body)
    for x in p.rows:
        date=iso_date(x["date"])
        if not date:continue
        text=x["text"];lo=text.lower()
        from_teams=sorted({a["from"] for a in x["links"] if a.get("from")})
        to_teams=sorted({a["to"] for a in x["links"] if a.get("to")})
        players=[a for a in x["links"] if (a.get("href") or "").startswith("/players/")]
        coaches=[a for a in x["links"] if (a.get("href") or "").startswith("/coaches/")]
        kind=("trade" if " traded " in f" {lo} " else
              "signed" if " signed " in f" {lo} " else
              "waived" if " waived " in f" {lo} " else
              "claimed" if " claimed " in f" {lo} " else
              "released" if " released " in f" {lo} " else
              "coach" if "head coach" in lo else "other")
        if kind!="other":
            source_url=f"https://www.basketball-reference.com/leagues/NBA_{y}_transactions.html"
            allrows.append({
              "season_end_year":y,"date":date,"kind":kind,"from_teams":"|".join(from_teams),"to_teams":"|".join(to_teams),
              "player_ids":"|".join((a.get("href") or "").split("/")[-1].replace(".html","") for a in players),
              "player_names":"|".join(a.get("text") or "" for a in players),"text":text,
              "source_url":source_url
            })
            for a in players:
                pid=(a.get("href") or "").split("/")[-1].replace(".html","")
                edges.append({
                  "season_end_year":y,"date":date,"kind":kind,
                  "player_bref_id":pid,"player_name":a.get("text") or "",
                  "from_team_bref":a.get("from") or "",
                  "to_team_bref":a.get("to") or "",
                  "text":text,"source_url":source_url
                })
        if "head coach" in lo:
            action=("fired" if " fired " in f" {lo} " else "resigned" if "resign" in lo else
                    "appointed" if " appointed " in f" {lo} " else "hired" if " hired " in f" {lo} " else "coach_event")
            team_codes=to_teams if action in ("appointed","hired") else from_teams
            for tc in team_codes:
                coach.append({
                  "season_end_year":y,"date":date,"team_bref":tc,"action":action,
                  "coach_ids":"|".join((a.get("href") or "").split("/")[-1].replace(".html","") for a in coaches),
                  "coach_names":"|".join(a.get("text") or "" for a in coaches),"text":text,
                  "source_url":f"https://www.basketball-reference.com/leagues/NBA_{y}_transactions.html"
                })

wgz(OUT/"bref_team_transactions.csv.gz",allrows)
wgz(OUT/"bref_player_transaction_edges.csv.gz",edges)
wgz(OUT/"bref_coach_events.csv.gz",coach)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "transaction_rows":len(allrows),"player_edge_rows":len(edges),"coach_event_rows":len(coach),
 "by_kind":{k:sum(1 for r in allrows if r["kind"]==k) for k in sorted(set(r["kind"] for r in allrows))},
 "season_end_years":list(range(2019,2027)),
 "source":"Basketball-Reference NBA season transaction pages",
 "policy":"Transaction date is treated as day-level only. Target-game features exclude events on the same calendar date because event time relative to tipoff is unknown."
}
(OUT/"bref_transactions_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
