#!/usr/bin/env python3
import argparse,csv,gzip,hashlib,json,os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"prospective"
RAW=OUT/"raw"
SCOREBOARD="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
SUMMARY="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
HEADERS={"User-Agent":"Mozilla/5.0 AppwizaNBAProspective/1.0","Accept":"application/json, text/plain, */*"}

def atomic_text(path,text):
    tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(text);os.replace(tmp,path)

def now(): return datetime.now(timezone.utc)
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def load_json(p,default):
    try:return json.loads(p.read_text())
    except:return default
def read_gz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def write_gz(p,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)
def append_jsonl(p,rows):
    rows=list(rows)
    if not rows:return
    p.parent.mkdir(parents=True,exist_ok=True)
    with open(p,"a",encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r,separators=(",",":"),sort_keys=True,default=str)+"\n")
def scalar(x):
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    return json.dumps(x,separators=(",",":"),sort_keys=True)
def team_comp(comp,side):
    for c in comp.get("competitors") or []:
        if c.get("homeAway")==side:return c
    return {}
def extract_starters(summary):
    out=[]
    box=(summary or {}).get("boxscore") or {}
    for team_block in box.get("players") or []:
        team=team_block.get("team") or {}
        tid=str(team.get("id") or "")
        for stat_group in team_block.get("statistics") or []:
            for a in stat_group.get("athletes") or []:
                if a.get("starter") is True:
                    ath=a.get("athlete") or {}
                    out.append({"team_id":tid,"person_id":str(ath.get("id") or ""),"player_name":ath.get("displayName") or ath.get("fullName")})
    # deterministic dedup
    seen=set();res=[]
    for r in out:
        k=(r["team_id"],r["person_id"] or r["player_name"])
        if k not in seen:seen.add(k);res.append(r)
    return res
def extract_officials(summary):
    gi=(summary or {}).get("gameInfo") or {}
    out=[]
    for o in gi.get("officials") or []:
        name=o.get("fullName") or o.get("displayName")
        if name:out.append({"name":name,"order":o.get("order"),"position":((o.get("position") or {}).get("name"))})
    return out
def injury_digest(summary):
    # Prospective only: retain whatever the live summary exposed at capture time.
    inj=(summary or {}).get("injuries") or []
    return inj
def stable_state(ev,summary):
    comp=(ev.get("competitions") or [{}])[0]
    home=team_comp(comp,"home");away=team_comp(comp,"away")
    venue=(comp.get("venue") or ((summary or {}).get("gameInfo") or {}).get("venue") or {})
    status=(ev.get("status") or {}).get("type") or {}
    return {
      "game_id":str(ev.get("id") or ""),
      "scheduled_utc":ev.get("date"),
      "status_name":status.get("name"),"status_state":status.get("state"),"completed":status.get("completed"),
      "home_team_id":str(((home.get("team") or {}).get("id")) or ""),"away_team_id":str(((away.get("team") or {}).get("id")) or ""),
      "venue_id":str(venue.get("id") or ""),"venue_name":venue.get("fullName") or venue.get("shortName"),
      "neutral_site":comp.get("neutralSite"),
      "officials":extract_officials(summary),"starters":extract_starters(summary),"injuries":injury_digest(summary),
      "odds":comp.get("odds") or []
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--near-tip-only",action="store_true")
    ap.add_argument("--near-tip-hours",type=float,default=2.0)
    ap.add_argument("--post-tip-hours",type=float,default=4.0)
    args=ap.parse_args()
    t=now();captured=t.isoformat()
    events={}
    for off in (-1,0,1,2):
        day=(t+timedelta(days=off)).strftime("%Y%m%d")
        r=requests.get(SCOREBOARD,params={"dates":day,"limit":100},headers=HEADERS,timeout=30);r.raise_for_status()
        for ev in r.json().get("events") or []:events[str(ev.get("id"))]=ev

    hashes_path=OUT/"latest_state_hashes.json"
    old_hashes=load_json(hashes_path,{})
    new_hashes=dict(old_hashes)
    snapshot_rows=[];starter_rows=[];official_rows=[];odds_rows=[];change_events=[];raw_changes={}
    summaries_attempted=0;summaries_ok=0

    for gid,ev in sorted(events.items()):
        game_time=dt(ev.get("date"))
        if not game_time:continue
        hours=(game_time-t).total_seconds()/3600
        if args.near_tip_only and not (-args.post_tip_hours <= hours <= args.near_tip_hours):
            continue
        # Rich summary capture from 48h before tip through four hours after tip.
        # This preserves confirmed starters/officials and normalized live-game state.
        summary={}
        if -4.0<=hours<=48:
            summaries_attempted+=1
            try:
                rr=requests.get(SUMMARY,params={"event":gid},headers=HEADERS,timeout=30);rr.raise_for_status()
                summary=rr.json();summaries_ok+=1
            except Exception:
                summary={}
        state=stable_state(ev,summary)
        canon=json.dumps(state,separators=(",",":"),sort_keys=True,default=str)
        h=hashlib.sha256(canon.encode()).hexdigest()
        changed=old_hashes.get(gid)!=h
        new_hashes[gid]=h

        snapshot_rows.append({
          "captured_at_utc":captured,"game_id":gid,"scheduled_utc":state["scheduled_utc"],
          "hours_to_tip":hours,"status_name":state["status_name"],"status_state":state["status_state"],"completed":state["completed"],
          "home_team_id":state["home_team_id"],"away_team_id":state["away_team_id"],
          "venue_id":state["venue_id"],"venue_name":state["venue_name"],"neutral_site":state["neutral_site"],
          "official_count":len(state["officials"]),"starter_count":len(state["starters"]),
          "injury_groups_count":len(state["injuries"]) if isinstance(state["injuries"],list) else None,
          "state_hash":h,"state_changed":changed
        })
        for s in state["starters"]:
            starter_rows.append({"captured_at_utc":captured,"game_id":gid,"scheduled_utc":state["scheduled_utc"],"hours_to_tip":hours,**s})
        for o in state["officials"]:
            official_rows.append({"captured_at_utc":captured,"game_id":gid,"scheduled_utc":state["scheduled_utc"],"hours_to_tip":hours,
                                  "official_name":o["name"],"official_order":o.get("order"),"official_position":o.get("position")})
        odds=state.get("odds") or []
        if isinstance(odds,dict): odds=[odds]
        for o in odds:
            provider=o.get("provider") or {}
            ho=o.get("homeTeamOdds") or {}; ao=o.get("awayTeamOdds") or {}
            odds_rows.append({"captured_at_utc":captured,"game_id":gid,"scheduled_utc":state["scheduled_utc"],"hours_to_tip":hours,
                              "provider_id":str(provider.get("id") or ""),"provider":provider.get("name"),"details":o.get("details"),
                              "spread":o.get("spread"),"over_under":o.get("overUnder"),
                              "home_moneyline":ho.get("moneyLine"),"away_moneyline":ao.get("moneyLine"),
                              "home_spread_odds":ho.get("spreadOdds"),"away_spread_odds":ao.get("spreadOdds"),
                              "over_odds":o.get("overOdds") if o.get("overOdds") is not None else (o.get("over") or {}).get("odds"),
                              "under_odds":o.get("underOdds") if o.get("underOdds") is not None else (o.get("under") or {}).get("odds")})
        if changed:
            change_events.append({"captured_at_utc":captured,"game_id":gid,"scheduled_utc":state["scheduled_utc"],"hours_to_tip":hours,
                                  "previous_hash":old_hashes.get(gid),"state_hash":h,
                                  "official_count":len(state["officials"]),"starter_count":len(state["starters"]),
                                  "status_name":state["status_name"],"venue_name":state["venue_name"]})
            raw_changes[gid]={"event":ev,"summary":summary,"normalized_state":state}

    # High-frequency prospective history is partitioned by capture date as append-only
    # JSONL so 10-minute scans do not rewrite giant binary gzip files all season.
    histdir=OUT/"history"/t.strftime("%Y-%m-%d")
    append_jsonl(histdir/"game_state.jsonl",snapshot_rows)
    append_jsonl(histdir/"starters.jsonl",starter_rows)
    append_jsonl(histdir/"officials.jsonl",official_rows)
    append_jsonl(histdir/"odds.jsonl",odds_rows)
    append_jsonl(histdir/"changes.jsonl",change_events)

    if raw_changes:
        d=RAW/t.strftime("%Y-%m-%d");d.mkdir(parents=True,exist_ok=True)
        p=d/(t.strftime("%H%M%SZ")+"_changes.json.gz")
        with gzip.open(p,"wt",encoding="utf-8") as f:json.dump({"captured_at_utc":captured,"games":raw_changes},f,separators=(",",":"))
        raw_ref=str(p.relative_to(ROOT))
    else:raw_ref=None

    atomic_text(hashes_path,json.dumps(new_hashes,indent=2,sort_keys=True)+"\n")
    latest={"captured_at_utc":captured,"events_seen":len(events),"summary_attempted":summaries_attempted,"summary_success":summaries_ok,
            "game_snapshot_rows":len(snapshot_rows),"starter_rows":len(starter_rows),"official_rows":len(official_rows),"odds_rows":len(odds_rows),
            "state_changes":len(change_events),"raw_change_snapshot":raw_ref,
            "near_tip_only":args.near_tip_only,"near_tip_hours":args.near_tip_hours,"post_tip_hours":args.post_tip_hours,
            "policy":"Prospective 2026+ event ledger. Raw summary payloads are preserved only when stable pregame state changes; normalized snapshots retain every capture time."}
    atomic_text(OUT/"latest.json",json.dumps(latest,indent=2)+"\n")
    print(json.dumps(latest,indent=2))

if __name__=="__main__":main()
