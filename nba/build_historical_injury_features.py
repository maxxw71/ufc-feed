#!/usr/bin/env python3
import csv,gzip,json,re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
ROOT=NBA/"availability"/"historical_by_season";F=NBA/"features"
OUT=NBA/"features"

STATUSES=("Questionable","Doubtful","Available","Probable","Out")
STATUS_RE=re.compile(r"\s(Questionable|Doubtful|Available|Probable|Out)\s")
SKIP_PREFIX=("Injury Report:","Game Date Game Time Matchup Team Player Name Current Status Reason","Page ")

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)
def n(v):
    try:return float(v)
    except:return None
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return None
def norm(v):
    s=(v or "").lower().replace("’","'")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return " ".join(s.split())
def report_name_aliases(v):
    raw=" ".join(str(v or "").split())
    vals=[norm(raw)]
    if "," in raw:
        last,first=raw.split(",",1)
        vals.insert(0,norm(first+" "+last))
    out=[]
    for x in vals:
        if x and x not in out:out.append(x)
    return out
def canonical_report_name(v):
    raw=" ".join(str(v or "").split())
    if "," in raw:
        last,first=raw.split(",",1)
        return " ".join((first+" "+last).split())
    return raw
def truth(v):return str(v).lower() in ("true","1","yes")
def severity(status):
    s=(status or "").lower()
    if s=="out":return 1.0
    if "doubt" in s:return 0.75
    if "question" in s:return 0.5
    if "prob" in s:return 0.15
    if "available" in s:return 0.0
    return 0.25

# Canonical team names from the historical game warehouse.
teams=set()
games_by_id={}
for p in (NBA/"data").glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        games_by_id[g.get("game_id")]=g
        for k in ("home_team","away_team"):
            if g.get(k):teams.add(g[k])
team_names=sorted(teams,key=len,reverse=True)
team_norm={norm(x):x for x in team_names}

# Point-in-time player rolling timeline by normalized player name.
timeline=defaultdict(list)
for r in rgz(F/"player_rolling.csv.gz"):
    name=norm(r.get("player_name"));when=dt(r.get("game_date"))
    if name and when:timeline[name].append((when,r))
for name in timeline:timeline[name].sort(key=lambda z:z[0])
timeline_dates={name:[x[0] for x in arr] for name,arr in timeline.items()}

def latest_prior_player(name,target):
    if not target:return {}
    best=None
    for key in report_name_aliases(name):
        arr=timeline.get(key)
        if not arr:continue
        i=bisect_left(timeline_dates[key],target)-1
        if i>=0 and (best is None or arr[i][0]>best[0]):best=arr[i]
    return best[1] if best else {}

def parse_report(text):
    out=[];current_team=None
    for raw in (text or "").splitlines():
        line=" ".join(raw.split())
        if not line or line.isdigit() or line.startswith(SKIP_PREFIX):continue
        m=STATUS_RE.search(" "+line+" ")
        if not m:continue
        # Compensate for the artificial leading blank used above.
        start=max(0,m.start(1)-1);end=max(0,m.end(1)-1)
        before=line[:start].strip();status=m.group(1);reason=line[end:].strip()
        # Find the latest explicit team marker on this line.
        found=None;found_pos=-1
        low=before.lower()
        for team in team_names:
            pos=low.rfind(team.lower())
            if pos>found_pos:
                found=team;found_pos=pos
        if found is not None:
            current_team=found
            player=before[found_pos+len(found):].strip()
        else:
            player=before
        # If a metadata prefix remains, player names are still the final "Last, First" chunk.
        # Full-team detection removes the date/time/matchup prefix for the first player row.
        if current_team and player and "," in player:
            out.append({"team":current_team,"player_name":player,"status":status,"reason":reason})
    return out

player_rows=[];team_agg=defaultdict(lambda:{
 "listed_players":0,"out_players":0,"doubtful_players":0,"questionable_players":0,"probable_players":0,"available_players":0,
 "weighted_missing_minutes_last5":0.0,"weighted_missing_points_last5":0.0,
 "weighted_missing_assists_last5":0.0,"weighted_missing_rebounds_last5":0.0,
 "weighted_missing_plusminus_last5":0.0,"injury_illness_players":0,"non_injury_players":0,
 "identity_matches":0
})
parse_stats=defaultdict(lambda:{"games":0,"games_with_entries":0,"player_rows":0,"home_rows":0,"away_rows":0})

for season_dir in sorted(ROOT.glob("*_*")):
    cov=season_dir/"coverage_games.csv.gz";texts_path=season_dir/"report_texts.csv.gz"
    if not cov.exists() or not texts_path.exists():continue
    texts={r.get("report_url"):r.get("report_text") or "" for r in rgz(texts_path)}
    parsed={u:parse_report(txt) for u,txt in texts.items()}
    season=season_dir.name.replace("_","-")
    for g in rgz(cov):
        gid=g.get("game_id");tip=dt(g.get("tip_et"));url=g.get("report_url")
        home=g.get("home_team") or "";away=g.get("away_team") or ""
        entries=parsed.get(url,[])
        selected=[e for e in entries if norm(e["team"]) in {norm(home),norm(away)}]
        st=parse_stats[season];st["games"]+=1
        if selected:st["games_with_entries"]+=1
        for e in selected:
            team=e["team"];feat=latest_prior_player(e["player_name"],tip)
            sev=severity(e["status"])
            mins=n(feat.get("minutes_last5_avg")) or 0.0
            pts=n(feat.get("points_last5_avg")) or 0.0
            ast=n(feat.get("assists_last5_avg")) or 0.0
            reb=n(feat.get("rebounds_total_last5_avg")) or 0.0
            pm=n(feat.get("plus_minus_last5_avg")) or 0.0
            reason=e["reason"];is_injury=reason.lower().startswith("injury/illness")
            row={
              "season":season,"game_id":gid,"tip_utc":tip.isoformat() if tip else "",
              "report_url":url,"team":team,"side":"home" if norm(team)==norm(home) else "away",
              "player_name":canonical_report_name(e["player_name"]),"report_player_name":e["player_name"],"status":e["status"],"reason":reason,
              "severity_weight":sev,"injury_illness":is_injury,
              "person_id":feat.get("person_id") or "","rolling_identity_matched":bool(feat),
              "minutes_last5_avg":mins,"points_last5_avg":pts,"assists_last5_avg":ast,
              "rebounds_last5_avg":reb,"plus_minus_last5_avg":pm,
              "weighted_missing_minutes":sev*mins,"weighted_missing_points":sev*pts,
              "weighted_missing_assists":sev*ast,"weighted_missing_rebounds":sev*reb,
              "weighted_missing_plusminus":sev*pm
            }
            player_rows.append(row);st["player_rows"]+=1
            if row["side"]=="home":st["home_rows"]+=1
            else:st["away_rows"]+=1
            key=(season,gid,team);a=team_agg[key]
            a["listed_players"]+=1
            sl=e["status"].lower()
            if sl=="out":a["out_players"]+=1
            elif "doubt" in sl:a["doubtful_players"]+=1
            elif "question" in sl:a["questionable_players"]+=1
            elif "prob" in sl:a["probable_players"]+=1
            elif "available" in sl:a["available_players"]+=1
            a["injury_illness_players" if is_injury else "non_injury_players"]+=1
            if feat:a["identity_matches"]+=1
            a["weighted_missing_minutes_last5"]+=sev*mins
            a["weighted_missing_points_last5"]+=sev*pts
            a["weighted_missing_assists_last5"]+=sev*ast
            a["weighted_missing_rebounds_last5"]+=sev*reb
            a["weighted_missing_plusminus_last5"]+=sev*pm

team_rows=[]
for (season,gid,team),a in team_agg.items():
    g=games_by_id.get(gid,{})
    home=g.get("home_team") or "";away=g.get("away_team") or ""
    row={"season":season,"game_id":gid,"game_date":g.get("game_date"),"team":team,
         "team_id":g.get("home_team_id") if norm(team)==norm(home) else g.get("away_team_id"),
         "side":"home" if norm(team)==norm(home) else "away"}
    row.update(a);team_rows.append(row)

wgz(OUT/"historical_injury_players.csv.gz",player_rows)
wgz(OUT/"historical_injury_teams.csv.gz",team_rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "seasons":dict(parse_stats),"player_rows":len(player_rows),"team_rows":len(team_rows),
 "identity_matched_player_rows":sum(1 for r in player_rows if r["rolling_identity_matched"]),
 "injury_illness_rows":sum(1 for r in player_rows if r["injury_illness"]),
 "status_counts":{s:sum(1 for r in player_rows if r["status"]==s) for s in STATUSES},
 "policy":"Every game uses its frozen pre-tip official NBA report URL. Player impact features use only the latest rolling player row strictly before that game's tip. Report status thresholds are not outcome-tuned."
}
(OUT/"historical_injury_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
