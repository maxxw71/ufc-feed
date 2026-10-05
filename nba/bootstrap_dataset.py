#!/usr/bin/env python3
import argparse, csv, gzip, json, re, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
NBA_ROOT = REPO_ROOT / "nba"
DATA_ROOT = NBA_ROOT / "data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

SEASON_TYPE_CODE = {"Pre Season": 1, "Regular Season": 2, "Post Season": 3}

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")

def as_num(v):
    if v is None or v == "": return None
    try:
        f=float(str(v).replace("%","").replace(",",""))
        return int(f) if f.is_integer() else f
    except Exception:
        return v

def get_json(session, url, params=None, tries=4, timeout=30):
    last=None
    status=None
    for attempt in range(tries):
        try:
            r=session.get(url,params=params,headers=HEADERS,timeout=timeout)
            status=r.status_code
            r.raise_for_status()
            return r.json(),status,None
        except Exception as e:
            last=str(e)
            if attempt+1<tries:
                time.sleep(min(6,1.25*(attempt+1)))
    return None,status,last

def season_years(season):
    a,b=season.split("-")
    start=int(a)
    end=(start//100)*100+int(b)
    if end < start: end += 100
    return start,end

def slice_window(season, season_type):
    start,end=season_years(season)
    if season_type=="Pre Season":
        return date(start,9,20),date(start,10,25)
    if season_type=="Regular Season":
        return date(start,10,1),date(end,4,30)
    if season_type=="Post Season":
        return date(end,4,1),date(end,6,30)
    raise ValueError(f"unsupported season type: {season_type}")

def daterange(a,b):
    d=a
    while d<=b:
        yield d
        d+=timedelta(days=1)

def scoreboard_url():
    return "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

def summary_url(event_id):
    return "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"

def discover_events(session, season, season_type, provenance):
    want=SEASON_TYPE_CODE[season_type]
    start,end=slice_window(season,season_type)
    events={}
    stamp=now_utc()
    for i,d in enumerate(daterange(start,end),1):
        ds=d.strftime("%Y%m%d")
        data,status,err=get_json(session,scoreboard_url(),params={"dates":ds,"limit":100},tries=3,timeout=25)
        provenance.append({
            "season":season,"season_type":season_type,"game_id":"","source":"espn_scoreboard",
            "url":f"{scoreboard_url()}?dates={ds}&limit=100","http_status":status,"ok":bool(data),
            "error":err,"ingested_at_utc":stamp
        })
        if not data:
            continue
        for ev in data.get("events") or []:
            es=(ev.get("season") or {}).get("type")
            if es is not None and int(es)!=want:
                continue
            eid=str(ev.get("id") or "")
            if eid:
                events[eid]=ev
        if i%50==0:
            print(f"{season} {season_type}: scanned {i} calendar days / {len(events)} events",flush=True)
        time.sleep(0.03)
    return events

def competition(ev):
    comps=ev.get("competitions") or []
    return comps[0] if comps else {}

def competitors(ev):
    comp=competition(ev)
    out={}
    for c in comp.get("competitors") or []:
        side=c.get("homeAway")
        if side in ("home","away"): out[side]=c
    return out

def team_bits(c):
    t=c.get("team") or {}
    return {
        "team_id":str(t.get("id") or ""),
        "team_name":t.get("displayName") or t.get("shortDisplayName") or t.get("name"),
        "team_tricode":t.get("abbreviation"),
    }

def event_game_row(season, season_type, ev, stamp):
    comp=competition(ev)
    sides=competitors(ev)
    h=sides.get("home") or {}
    a=sides.get("away") or {}
    ht=team_bits(h); at=team_bits(a)
    venue=comp.get("venue") or {}
    addr=venue.get("address") or {}
    st=((ev.get("status") or {}).get("type") or {})
    return {
        "season":season,"season_type":season_type,"game_id":str(ev.get("id") or ""),
        "source_game_id":str(ev.get("id") or ""),"source_system":"espn",
        "game_date":ev.get("date"),"home_team_id":ht["team_id"],"home_team":ht["team_name"],
        "home_tricode":ht["team_tricode"],"away_team_id":at["team_id"],"away_team":at["team_name"],
        "away_tricode":at["team_tricode"],"home_score":as_num(h.get("score")),"away_score":as_num(a.get("score")),
        "status":st.get("detail") or st.get("description") or st.get("name"),"status_state":st.get("state"),
        "completed":st.get("completed"),"arena_name":venue.get("fullName"),"arena_city":addr.get("city"),
        "arena_state":addr.get("state"),"source_boxscore_url":f"{summary_url(ev.get('id'))}?event={ev.get('id')}",
        "ingested_at_utc":stamp
    }

def team_seed_rows(season, season_type, ev, stamp):
    out=[]
    for side,c in competitors(ev).items():
        tb=team_bits(c)
        out.append({
            "season":season,"season_type":season_type,"game_id":str(ev.get("id") or ""),
            "team_id":tb["team_id"],"team_tricode":tb["team_tricode"],"team_name":tb["team_name"],
            "is_home":side=="home","points":as_num(c.get("score")),"winner":c.get("winner"),
            "ingested_at_utc":stamp
        })
    return out

TEAM_MAP={
    "fieldgoalpct":"field_goals_percentage","threepointfieldgoalpct":"three_pointers_percentage",
    "freethrowpct":"free_throws_percentage","offensiverebounds":"rebounds_offensive",
    "defensiverebounds":"rebounds_defensive","totalrebounds":"rebounds_total","assists":"assists",
    "steals":"steals","blocks":"blocks","turnovers":"turnovers","fouls":"fouls_personal",
    "points":"points","pointsinthepaint":"points_in_the_paint","fastbreakpoints":"points_fast_break",
    "secondchancepoints":"points_second_chance"
}

def parse_made_attempted(v):
    s=str(v or "")
    m=re.match(r"^\s*(\d+)\s*[-/]\s*(\d+)\s*$",s)
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def enrich_team_rows(seed, summary):
    by_id={str(x.get("team_id")):x for x in seed}
    box=(summary or {}).get("boxscore") or {}
    for entry in box.get("teams") or []:
        team=entry.get("team") or {}
        tid=str(team.get("id") or "")
        row=by_id.get(tid)
        if not row: continue
        for st in entry.get("statistics") or []:
            name=slug(st.get("name") or st.get("label") or "stat")
            raw=st.get("displayValue")
            if raw is None: raw=st.get("value")
            row[f"stat_{name}"]=raw
            key=TEAM_MAP.get(name)
            if key: row[key]=as_num(st.get("value") if st.get("value") is not None else raw)
            if name in ("fieldgoalsmade-fieldgoalsattempted","fieldgoals"):
                m,a=parse_made_attempted(raw); row["field_goals_made"]=m; row["field_goals_attempted"]=a
            elif name in ("threepointfieldgoalsmade-threepointfieldgoalsattempted","threepointfieldgoals"):
                m,a=parse_made_attempted(raw); row["three_pointers_made"]=m; row["three_pointers_attempted"]=a
            elif name in ("freethrowsmade-freethrowsattempted","freethrows"):
                m,a=parse_made_attempted(raw); row["free_throws_made"]=m; row["free_throws_attempted"]=a
    return list(by_id.values())

PLAYER_LABEL_MAP={
    "MIN":"minutes","PTS":"points","AST":"assists","REB":"rebounds_total","OREB":"rebounds_offensive",
    "DREB":"rebounds_defensive","STL":"steals","BLK":"blocks","TO":"turnovers","PF":"fouls_personal",
    "+/-":"plus_minus"
}

def player_rows(season, season_type, game_id, summary, stamp):
    out=[]
    box=(summary or {}).get("boxscore") or {}
    for teamblock in box.get("players") or []:
        team=teamblock.get("team") or {}
        tid=str(team.get("id") or "")
        tri=team.get("abbreviation")
        for group in teamblock.get("statistics") or []:
            labels=group.get("labels") or group.get("names") or []
            for p in group.get("athletes") or []:
                ath=p.get("athlete") or {}
                vals=p.get("stats") or []
                row={
                    "season":season,"season_type":season_type,"game_id":game_id,"team_id":tid,
                    "team_tricode":tri,"person_id":str(ath.get("id") or ""),
                    "player_name":ath.get("displayName") or ath.get("shortName"),
                    "starter":p.get("starter"),"played":not bool(p.get("didNotPlay")),
                    "status":"DNP" if p.get("didNotPlay") else "played",
                    "not_playing_reason":p.get("reason"),"ingested_at_utc":stamp
                }
                for label,val in zip(labels,vals):
                    row[f"stat_{slug(label)}"]=val
                    if label in PLAYER_LABEL_MAP: row[PLAYER_LABEL_MAP[label]]=as_num(val)
                    if label=="FG":
                        m,a=parse_made_attempted(val); row["field_goals_made"]=m; row["field_goals_attempted"]=a
                    elif label=="3PT":
                        m,a=parse_made_attempted(val); row["three_pointers_made"]=m; row["three_pointers_attempted"]=a
                    elif label=="FT":
                        m,a=parse_made_attempted(val); row["free_throws_made"]=m; row["free_throws_attempted"]=a
                out.append(row)
    return out

def play_rows(season, season_type, game_id, summary, stamp):
    out=[]
    for i,a in enumerate((summary or {}).get("plays") or []):
        period=a.get("period") or {}
        clock=a.get("clock") or {}
        typ=a.get("type") or {}
        team=a.get("team") or {}
        participants=a.get("participants") or []
        person=(participants[0].get("athlete") or {}) if participants else {}
        coord=a.get("coordinate") or {}
        out.append({
            "season":season,"season_type":season_type,"game_id":game_id,
            "action_number":i+1,"source_action_id":a.get("id"),"source_sequence":a.get("sequenceNumber"),
            "period":period.get("number"),"clock":clock.get("displayValue"),"time_actual":a.get("wallclock"),
            "action_type":typ.get("text") or typ.get("type"),"sub_type":typ.get("id"),
            "description":a.get("text"),"team_id":str(team.get("id") or ""),"team_tricode":team.get("abbreviation"),
            "person_id":str(person.get("id") or ""),"player_name":person.get("displayName"),
            "x":coord.get("x"),"y":coord.get("y"),"shot_distance":a.get("shotDistance"),
            "shot_result":a.get("shootingPlay"),"is_field_goal":a.get("shootingPlay"),
            "score_home":a.get("homeScore"),"score_away":a.get("awayScore"),
            "points_total":a.get("scoreValue"),"scoring_play":a.get("scoringPlay"),"ingested_at_utc":stamp
        })
    return out

def write_gz(path, rows, preferred=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=list(rows)
    fields=[]
    for f in preferred or []:
        if f not in fields: fields.append(f)
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields:
        fields=["_empty"]
    with gzip.open(path,"wt",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow(r)

def run_slice(session, season, season_type, max_games=None):
    provenance=[]
    events=discover_events(session,season,season_type,provenance)
    ordered=sorted(events.values(),key=lambda e:e.get("date") or "")
    if max_games: ordered=ordered[-max_games:]
    stamp=now_utc()
    games=[]; teams=[]; players=[]; plays=[]
    for idx,ev in enumerate(ordered,1):
        gid=str(ev.get("id") or "")
        games.append(event_game_row(season,season_type,ev,stamp))
        seed=team_seed_rows(season,season_type,ev,stamp)
        state=((((ev.get("status") or {}).get("type") or {}).get("state")) or "")
        if state=="pre":
            teams.extend(seed)
            continue
        data,status,err=get_json(session,summary_url(gid),params={"event":gid},tries=3,timeout=30)
        provenance.append({
            "season":season,"season_type":season_type,"game_id":gid,"source":"espn_summary",
            "url":f"{summary_url(gid)}?event={gid}","http_status":status,"ok":bool(data),
            "error":err,"ingested_at_utc":stamp
        })
        if data:
            teams.extend(enrich_team_rows(seed,data))
            players.extend(player_rows(season,season_type,gid,data,stamp))
            plays.extend(play_rows(season,season_type,gid,data,stamp))
        else:
            teams.extend(seed)
        if idx%100==0:
            print(f"{season} {season_type}: normalized {idx}/{len(ordered)} events",flush=True)
        time.sleep(0.04)

    out=DATA_ROOT/slug(season)/slug(season_type)
    write_gz(out/"games.csv.gz",games)
    write_gz(out/"team_boxscores.csv.gz",teams)
    write_gz(out/"player_boxscores.csv.gz",players)
    write_gz(out/"playbyplay.csv.gz",plays)
    write_gz(out/"provenance.csv.gz",provenance)
    summary={
        "season":season,"season_type":season_type,"source":"espn_site_api",
        "resolved_game_ids":len(events),"attempted_game_ids":len(ordered),
        "games_written":len(games),"team_rows":len(teams),"player_rows":len(players),
        "playbyplay_rows":len(plays),
        "scoreboard_requests":sum(1 for x in provenance if x["source"]=="espn_scoreboard"),
        "scoreboard_success":sum(1 for x in provenance if x["source"]=="espn_scoreboard" and x["ok"]),
        "summary_requests":sum(1 for x in provenance if x["source"]=="espn_summary"),
        "summary_success":sum(1 for x in provenance if x["source"]=="espn_summary" and x["ok"]),
        "generated_at_utc":now_utc()
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary),flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--slice",action="append",help="SEASON|SEASON_TYPE, repeatable")
    ap.add_argument("--max-games",type=int,default=None)
    args=ap.parse_args()
    slices=args.slice or ["2025-26|Regular Season","2026-27|Pre Season","2026-27|Regular Season"]
    s=requests.Session()
    failures=[]
    for spec in slices:
        season,season_type=spec.split("|",1)
        try:
            run_slice(s,season,season_type,args.max_games)
        except Exception as e:
            failures.append({"slice":spec,"error":str(e)})
            print(f"ERROR {spec}: {e}",file=sys.stderr,flush=True)
    status={"generated_at_utc":now_utc(),"source_strategy":"ESPN discovery/summary; NBA official feeds retained for later cross-check lane","slices":slices,"failures":failures,"ok":not failures}
    (NBA_ROOT/"bootstrap_status.json").write_text(json.dumps(status,indent=2)+"\n")
    if failures: sys.exit(2)

if __name__=="__main__":
    main()
