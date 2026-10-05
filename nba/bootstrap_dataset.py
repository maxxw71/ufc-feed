#!/usr/bin/env python3
import argparse, csv, gzip, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
NBA_ROOT = REPO_ROOT / "nba"
DATA_ROOT = NBA_ROOT / "data"

STATS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

LIVE_HEADERS = {
    "User-Agent": STATS_HEADERS["User-Agent"],
    "Referer": "https://www.nba.com/",
    "Accept": "application/json, text/plain, */*",
}

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def get_json(session, url, params=None, headers=None, tries=4, timeout=40):
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json(), r.status_code, None
        except Exception as e:
            last = str(e)
            if attempt + 1 < tries:
                time.sleep(1.5 * (attempt + 1))
    return None, None, last

def league_games(session, season, season_type):
    url = "https://stats.nba.com/stats/leaguegamefinder"
    params = {
        "PlayerOrTeam": "T",
        "Season": season,
        "SeasonType": season_type,
        "LeagueID": "00",
    }
    data, status, err = get_json(session, url, params=params, headers=STATS_HEADERS, tries=5, timeout=60)
    if not data:
        raise RuntimeError(f"LeagueGameFinder failed for {season} {season_type}: {err}")
    sets = data.get("resultSets") or data.get("resultSet")
    if isinstance(sets, dict):
        sets = [sets]
    target = None
    for rs in sets or []:
        if rs.get("name") in ("LeagueGameFinderResults", "LeagueGameFinderTeamResults"):
            target = rs
            break
    if not target:
        target = (sets or [None])[0]
    if not target:
        raise RuntimeError("LeagueGameFinder returned no result set")
    headers = target["headers"]
    rows = [dict(zip(headers, r)) for r in target["rowSet"]]
    by_game = {}
    for row in rows:
        gid = str(row.get("GAME_ID") or "")
        if not gid:
            continue
        by_game.setdefault(gid, []).append(row)
    return by_game

def live_urls(game_id):
    return (
        f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json",
        f"https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json",
    )

def side_rows(game):
    return [("away", game.get("awayTeam") or {}), ("home", game.get("homeTeam") or {})]

def team_name(t):
    return " ".join(x for x in [t.get("teamCity"), t.get("teamName")] if x).strip()

def parse_minutes(v):
    if v is None: return None
    s = str(v)
    if s.startswith("PT"):
        m = re.search(r"(\d+)M", s)
        sec = re.search(r"([\d.]+)S", s)
        return round((float(m.group(1)) if m else 0) + (float(sec.group(1)) if sec else 0)/60, 4)
    try: return float(s)
    except: return s

def flatten_game(season, season_type, game, box_url, stamp):
    arena = game.get("arena") or {}
    home = game.get("homeTeam") or {}
    away = game.get("awayTeam") or {}
    return {
        "season": season, "season_type": season_type, "game_id": game.get("gameId"),
        "game_date": game.get("gameTimeLocal") or game.get("gameTimeUTC") or game.get("gameEt"),
        "home_team_id": home.get("teamId"), "home_team": team_name(home), "home_tricode": home.get("teamTricode"),
        "away_team_id": away.get("teamId"), "away_team": team_name(away), "away_tricode": away.get("teamTricode"),
        "home_score": home.get("score"), "away_score": away.get("score"),
        "status": game.get("gameStatusText") or game.get("gameStatus"),
        "arena_name": arena.get("arenaName"), "arena_city": arena.get("arenaCity"), "arena_state": arena.get("arenaState"),
        "source_boxscore_url": box_url, "ingested_at_utc": stamp,
    }

def team_box_rows(season, season_type, game, stamp):
    out=[]
    keymap = {
        "points":"points","fieldGoalsMade":"field_goals_made","fieldGoalsAttempted":"field_goals_attempted",
        "fieldGoalsPercentage":"field_goals_percentage","threePointersMade":"three_pointers_made",
        "threePointersAttempted":"three_pointers_attempted","threePointersPercentage":"three_pointers_percentage",
        "freeThrowsMade":"free_throws_made","freeThrowsAttempted":"free_throws_attempted",
        "freeThrowsPercentage":"free_throws_percentage","reboundsOffensive":"rebounds_offensive",
        "reboundsDefensive":"rebounds_defensive","reboundsTotal":"rebounds_total","assists":"assists",
        "steals":"steals","blocks":"blocks","turnoversTotal":"turnovers","turnovers":"turnovers",
        "foulsPersonal":"fouls_personal","pointsInThePaint":"points_in_the_paint","pointsFastBreak":"points_fast_break",
        "pointsSecondChance":"points_second_chance","benchPoints":"bench_points","trueShootingPercentage":"true_shooting_percentage",
        "fieldGoalsEffectiveAdjusted":"effective_field_goal_percentage"
    }
    for side,t in side_rows(game):
        st=t.get("statistics") or {}
        row={"season":season,"season_type":season_type,"game_id":game.get("gameId"),"team_id":t.get("teamId"),
             "team_tricode":t.get("teamTricode"),"is_home":side=="home","ingested_at_utc":stamp}
        for src,dst in keymap.items():
            if src in st and (dst not in row or row.get(dst) is None):
                row[dst]=st.get(src)
        out.append(row)
    return out

def player_box_rows(season, season_type, game, stamp):
    out=[]
    statmap={
      "points":"points","assists":"assists","reboundsTotal":"rebounds_total","reboundsOffensive":"rebounds_offensive",
      "reboundsDefensive":"rebounds_defensive","steals":"steals","blocks":"blocks","turnovers":"turnovers",
      "foulsPersonal":"fouls_personal","fieldGoalsMade":"field_goals_made","fieldGoalsAttempted":"field_goals_attempted",
      "fieldGoalsPercentage":"field_goals_percentage","threePointersMade":"three_pointers_made",
      "threePointersAttempted":"three_pointers_attempted","threePointersPercentage":"three_pointers_percentage",
      "freeThrowsMade":"free_throws_made","freeThrowsAttempted":"free_throws_attempted","freeThrowsPercentage":"free_throws_percentage",
      "plusMinusPoints":"plus_minus","pointsInThePaint":"points_in_the_paint","pointsFastBreak":"points_fast_break",
      "pointsSecondChance":"points_second_chance"
    }
    for side,t in side_rows(game):
        for p in t.get("players") or []:
            st=p.get("statistics") or {}
            row={"season":season,"season_type":season_type,"game_id":game.get("gameId"),"team_id":t.get("teamId"),
                 "team_tricode":t.get("teamTricode"),"is_home":side=="home","person_id":p.get("personId"),
                 "player_name":p.get("name") or " ".join(x for x in [p.get("firstName"),p.get("familyName")] if x),
                 "starter":p.get("starter"),"played":p.get("played"),"status":p.get("status"),
                 "not_playing_reason":p.get("notPlayingReason") or p.get("notPlayingDescription"),
                 "minutes":parse_minutes(st.get("minutes")),"ingested_at_utc":stamp}
            for src,dst in statmap.items(): row[dst]=st.get(src)
            out.append(row)
    return out

def pbp_rows(season, season_type, game_id, data, stamp):
    out=[]
    game=(data or {}).get("game") or {}
    for a in game.get("actions") or []:
        out.append({
          "season":season,"season_type":season_type,"game_id":game_id,"action_number":a.get("actionNumber"),
          "period":a.get("period"),"clock":a.get("clock"),"time_actual":a.get("timeActual"),
          "action_type":a.get("actionType"),"sub_type":a.get("subType"),"description":a.get("description"),
          "team_id":a.get("teamId"),"team_tricode":a.get("teamTricode"),"person_id":a.get("personId"),
          "player_name":a.get("playerName"),"x":a.get("x"),"y":a.get("y"),"shot_distance":a.get("shotDistance"),
          "shot_result":a.get("shotResult"),"is_field_goal":a.get("isFieldGoal"),"score_home":a.get("scoreHome"),
          "score_away":a.get("scoreAway"),"points_total":a.get("pointsTotal"),"ingested_at_utc":stamp
        })
    return out

def write_gz(path, rows, preferred=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows=list(rows)
    fields=[]
    for f in preferred or []:
        if f not in fields: fields.append(f)
    for r in rows:
        for k in r.keys():
            if k not in fields: fields.append(k)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows: w.writerow(r)

def run_slice(session, season, season_type, max_games=None):
    ids=league_games(session, season, season_type)
    game_ids=sorted(ids.keys())
    if max_games: game_ids=game_ids[-max_games:]
    games=[]; teams=[]; players=[]; pbp=[]; prov=[]
    stamp=now_utc()
    for idx,gid in enumerate(game_ids,1):
        box_url,pbp_url=live_urls(gid)
        box,bs,be=get_json(session,box_url,headers=LIVE_HEADERS)
        prov.append({"season":season,"season_type":season_type,"game_id":gid,"source":"nba_live_boxscore","url":box_url,
                     "http_status":bs,"ok":bool(box),"error":be,"ingested_at_utc":stamp})
        if not box or not box.get("game"):
            continue
        game=box["game"]
        games.append(flatten_game(season,season_type,game,box_url,stamp))
        teams.extend(team_box_rows(season,season_type,game,stamp))
        players.extend(player_box_rows(season,season_type,game,stamp))
        pd,ps,pe=get_json(session,pbp_url,headers=LIVE_HEADERS)
        prov.append({"season":season,"season_type":season_type,"game_id":gid,"source":"nba_live_playbyplay","url":pbp_url,
                     "http_status":ps,"ok":bool(pd),"error":pe,"ingested_at_utc":stamp})
        if pd: pbp.extend(pbp_rows(season,season_type,gid,pd,stamp))
        if idx % 100 == 0: print(f"{season} {season_type}: {idx}/{len(game_ids)}", flush=True)
        time.sleep(0.08)

    out=DATA_ROOT/slug(season)/slug(season_type)
    write_gz(out/"games.csv.gz",games)
    write_gz(out/"team_boxscores.csv.gz",teams)
    write_gz(out/"player_boxscores.csv.gz",players)
    write_gz(out/"playbyplay.csv.gz",pbp)
    write_gz(out/"provenance.csv.gz",prov)
    summary={
      "season":season,"season_type":season_type,"resolved_game_ids":len(ids),"attempted_game_ids":len(game_ids),
      "games_written":len(games),"team_rows":len(teams),"player_rows":len(players),"playbyplay_rows":len(pbp),
      "boxscore_success":sum(1 for x in prov if x["source"]=="nba_live_boxscore" and x["ok"]),
      "playbyplay_success":sum(1 for x in prov if x["source"]=="nba_live_playbyplay" and x["ok"]),
      "generated_at_utc":now_utc()
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary))

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
        try: run_slice(s,season,season_type,args.max_games)
        except Exception as e:
            failures.append({"slice":spec,"error":str(e)})
            print(f"ERROR {spec}: {e}",file=sys.stderr)
    status={"generated_at_utc":now_utc(),"slices":slices,"failures":failures,"ok":not failures}
    (NBA_ROOT/"bootstrap_status.json").write_text(json.dumps(status,indent=2)+"\n")
    if failures: sys.exit(2)

if __name__=="__main__":
    main()
