#!/usr/bin/env python3
import csv,gzip,json,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"
OUT=NBA/"lineups"
OUT.mkdir(parents=True,exist_ok=True)

SEASONS={"2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"}

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def truth(v): return str(v).lower() in ("true","1","yes")

def norm_name(s):
    s=(s or "").lower()
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b","",s)
    return re.sub(r"[^a-z0-9]","",s)

def score(v):
    try:return int(float(v))
    except:return None

def clock_seconds(v):
    if not v:return None
    s=str(v)
    if s.startswith("PT"):
        m=re.search(r"(\d+)M",s); sec=re.search(r"([\d.]+)S",s)
        return (int(m.group(1)) if m else 0)+(float(sec.group(1)) if sec else 0)
    try:
        mm,ss=s.split(":")
        return int(mm)*60+float(ss)
    except:return None

def elapsed(period,clock):
    try:p=int(float(period))
    except:return None
    rem=clock_seconds(clock)
    if rem is None:return None
    if p<=4:return (p-1)*720+(720-rem)
    return 4*720+(p-5)*300+(300-rem)

PATTERNS=[
  re.compile(r"^\s*(.+?)\s+enters the game for\s+(.+?)\s*$",re.I),
  re.compile(r"^\s*substitution[:\-]?\s*(.+?)\s+for\s+(.+?)\s*$",re.I),
  re.compile(r"^\s*(.+?)\s+subbed in for\s+(.+?)\s*$",re.I),
]

def parse_sub(desc):
    for p in PATTERNS:
        m=p.search(desc or "")
        if m:return m.group(1).strip(),m.group(2).strip()
    return None

def resolve_player(name,lookup):
    n=norm_name(name)
    if not n:return None
    exact=lookup.get(n)
    if exact:return exact
    # Unique containment fallback for display-name variations.
    matches=[]
    for key,val in lookup.items():
        if n in key or key in n: matches.append(val)
    uniq={m[0]:m for m in matches}
    return next(iter(uniq.values())) if len(uniq)==1 else None

def write_gz(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)

all_stints=[]; season_summary={}
for season in sorted(SEASONS):
    base=DATA/season.replace("-","_")/"regular_season"
    gp=base/"games.csv.gz"; pp=base/"player_boxscores.csv.gz"; pb=base/"playbyplay.csv.gz"
    if not (gp.exists() and pp.exists() and pb.exists()): continue
    games={g["game_id"]:g for g in read_gz(gp)}
    players=read_gz(pp)
    by_game_team=defaultdict(list)
    lookup_by_game=defaultdict(dict)
    for p in players:
        gid=p.get("game_id"); tid=p.get("team_id"); pid=p.get("person_id")
        if not gid or not tid or not pid:continue
        by_game_team[(gid,tid)].append(p)
        nm=norm_name(p.get("player_name"))
        if nm: lookup_by_game[gid][nm]=(pid,tid,p.get("player_name"))

    starters={}
    for key,arr in by_game_team.items():
        s=[p for p in arr if truth(p.get("starter"))]
        if len(s)==5: starters[key]=s

    stats={"games_total":len(games),"games_with_both_exact_starting_fives":0,"parsed_substitutions":0,
           "unresolved_substitutions":0,"lineup_stints":0,"games_with_stints":0}
    current_gid=None; plays=[]

    def process_game(gid,plays):
        if not gid or gid not in games:return []
        g=games[gid]; ht=g.get("home_team_id"); at=g.get("away_team_id")
        if (gid,ht) not in starters or (gid,at) not in starters:return []
        stats["games_with_both_exact_starting_fives"]+=1
        lineups={
          ht:{p["person_id"] for p in starters[(gid,ht)]},
          at:{p["person_id"] for p in starters[(gid,at)]}
        }
        name_by_id={p["person_id"]:p.get("player_name") for p in players if p.get("game_id")==gid}
        lookup=lookup_by_game[gid]
        start_action={ht:1,at:1}; start_elapsed={ht:0.0,at:0.0}; start_score={ht:(0,0),at:(0,0)}
        rows=[]
        last_home=last_away=0; last_elapsed=0.0; last_action=0
        for idx,r in enumerate(plays,1):
            h=score(r.get("score_home")); a=score(r.get("score_away"))
            if h is not None:last_home=h
            if a is not None:last_away=a
            e=elapsed(r.get("period"),r.get("clock"))
            if e is not None:last_elapsed=e
            last_action=idx
            parsed=parse_sub(r.get("description") or "")
            if not parsed:continue
            incoming,outgoing=parsed
            iv=resolve_player(incoming,lookup); ov=resolve_player(outgoing,lookup)
            if not iv or not ov:
                stats["unresolved_substitutions"]+=1;continue
            in_pid,in_tid,_=iv; out_pid,out_tid,_=ov
            tid=str(r.get("team_id") or "")
            if tid not in lineups:
                tid=in_tid if in_tid==out_tid and in_tid in lineups else ""
            if not tid or out_pid not in lineups.get(tid,set()) or in_pid in lineups.get(tid,set()):
                stats["unresolved_substitutions"]+=1;continue

            stats["parsed_substitutions"]+=1
            sh,sa=start_score[tid]
            sign=1 if tid==ht else -1
            rows.append({
              "season":season,"game_id":gid,"team_id":tid,
              "is_home":tid==ht,"stint_start_action":start_action[tid],"stint_end_action":idx,
              "stint_start_elapsed_sec":start_elapsed[tid],"stint_end_elapsed_sec":last_elapsed,
              "stint_duration_sec":max(0,last_elapsed-start_elapsed[tid]),
              "lineup_player_ids":"|".join(sorted(lineups[tid])),
              "lineup_player_names":"|".join(sorted(name_by_id.get(x,x) for x in lineups[tid])),
              "stint_plus_minus":sign*((last_home-sh)-(last_away-sa)),
              "ended_by_sub_in":in_pid,"ended_by_sub_out":out_pid
            })
            lineups[tid].remove(out_pid);lineups[tid].add(in_pid)
            start_action[tid]=idx;start_elapsed[tid]=last_elapsed;start_score[tid]=(last_home,last_away)

        for tid in (ht,at):
            sh,sa=start_score[tid]; sign=1 if tid==ht else -1
            rows.append({
              "season":season,"game_id":gid,"team_id":tid,
              "is_home":tid==ht,"stint_start_action":start_action[tid],"stint_end_action":last_action,
              "stint_start_elapsed_sec":start_elapsed[tid],"stint_end_elapsed_sec":last_elapsed,
              "stint_duration_sec":max(0,last_elapsed-start_elapsed[tid]),
              "lineup_player_ids":"|".join(sorted(lineups[tid])),
              "lineup_player_names":"|".join(sorted(name_by_id.get(x,x) for x in lineups[tid])),
              "stint_plus_minus":sign*((last_home-sh)-(last_away-sa)),
              "ended_by_sub_in":"","ended_by_sub_out":""
            })
        if rows:stats["games_with_stints"]+=1
        return rows

    with gzip.open(pb,"rt",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            gid=r.get("game_id")
            if current_gid is None:current_gid=gid
            if gid!=current_gid:
                all_stints.extend(process_game(current_gid,plays))
                plays=[];current_gid=gid
            plays.append(r)
        if current_gid is not None:
            all_stints.extend(process_game(current_gid,plays))

    stats["lineup_stints"]=sum(1 for r in all_stints if r.get("season")==season)
    stats["starter_coverage"]=stats["games_with_both_exact_starting_fives"]/stats["games_total"] if stats["games_total"] else 0
    stats["stint_game_coverage"]=stats["games_with_stints"]/stats["games_total"] if stats["games_total"] else 0
    season_summary[season]=stats

write_gz(OUT/"historical_lineup_stints.csv.gz",all_stints)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"seasons":season_summary,
         "total_stints":len(all_stints),
         "policy":"Only exact 5-player starter states and resolvable substitution transitions are emitted. No guessed substitutions."}
(OUT/"historical_lineup_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
