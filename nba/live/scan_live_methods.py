#!/usr/bin/env python3
import argparse,csv,gzip,json,math,re,statistics
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"/"2026_27"/"regular_season"
F=NBA/"features";LIVE=NBA/"live";PRO=NBA/"prospective";MARKET=NBA/"market"
OUT=NBA/"scanner";OUT.mkdir(parents=True,exist_ok=True)
SEASON="2026-27"

ap=argparse.ArgumentParser()
ap.add_argument("--scheduled",action="store_true",help="Apply 30-minute baseline / 10-minute near-tip cadence gating")
ap.add_argument("--force",action="store_true",help="Force scan after a material source update")
ARGS=ap.parse_args()

def rgz(p):
    if not p.exists(): return []
    try:
        with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
    except gzip.BadGzipFile:
        with open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"],extrasaction="ignore");w.writeheader();w.writerows(rows)
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def truth(v):return str(v).lower() in ("true","1","yes")
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return None
def norm(s):return re.sub(r"[^a-z0-9]","",str(s or "").lower())
def imp(a):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (-a)/((-a)+100) if a<0 else 100/(a+100)
def median(vals):
    vals=[n(x) for x in vals];vals=[x for x in vals if x is not None]
    return statistics.median(vals) if vals else None
def d(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k))
    return x-y if x is not None and y is not None else None
def mean(vals):
    vals=[x for x in vals if x is not None]
    return statistics.mean(vals) if vals else None
def parse_height(v):
    if v is None:return None
    s=str(v)
    m=re.search(r"(\d+)\s*['-]\s*(\d+)",s)
    if m:return int(m.group(1))*12+int(m.group(2))
    x=n(v)
    return x
def game_sort_date(g):return dt(g.get("game_date")) or datetime.min.replace(tzinfo=timezone.utc)

games=rgz(DATA/"games.csv.gz")
teams=rgz(DATA/"team_boxscores.csv.gz")
players=rgz(DATA/"player_boxscores.csv.gz")
plays=rgz(DATA/"playbyplay.csv.gz")
game_by={g.get("game_id"):g for g in games if g.get("game_id")}
completed={gid:g for gid,g in game_by.items() if truth(g.get("completed"))}
now=datetime.now(timezone.utc)

# Upcoming regular-season games: only pregame states, next 72 hours.
upcoming=[]
for g in games:
    when=dt(g.get("game_date"))
    if not when or truth(g.get("completed")):continue
    state=str(g.get("status_state") or "").lower()
    if state not in ("pre","") and "scheduled" not in str(g.get("status") or "").lower():continue
    hrs=(when-now).total_seconds()/3600
    if -0.25<=hrs<=72:upcoming.append(g)
upcoming.sort(key=game_sort_date)

# Scheduled heartbeat runs every 10 minutes. Outside the final two hours before any tip,
# suppress redundant runs until roughly 30 minutes have elapsed. Push-triggered runs use --force.
if ARGS.scheduled and not ARGS.force:
    near_tip=any(0 <= (dt(g.get("game_date"))-now).total_seconds()/3600 <= 2.0 for g in upcoming if dt(g.get("game_date")))
    last=None
    try:
        last=json.loads((OUT/"latest.json").read_text())
    except Exception:
        last=None
    last_dt=dt((last or {}).get("scanned_at_utc"))
    if (not near_tip) and last_dt and (now-last_dt).total_seconds() < 25*60:
        print(json.dumps({"skipped":True,"reason":"baseline_cadence","last_scan":last_dt.isoformat(),"near_tip":False},indent=2))
        raise SystemExit(0)

# Historical feature indexes for target game context.
def idx(path):
    return {(r.get("game_id"),r.get("team_id")):r for r in rgz(path)}
roll=idx(F/"team_rolling.csv.gz")
travel=idx(F/"travel_pregame.csv.gz")
stand=idx(F/"standings_pregame.csv.gz")
context={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}

# Latest prospective confirmed starters, strictly before scan time.
starter_rows=rgz(PRO/"starter_snapshots.csv.gz")
starter_latest={}
for r in starter_rows:
    cap=dt(r.get("captured_at_utc"))
    if not cap or cap>now:continue
    key=(r.get("game_id"),r.get("team_id"))
    old=starter_latest.get(key)
    if not old or cap>old[0]:starter_latest[key]=(cap,[])
# second pass gathers every starter at latest timestamp
for r in starter_rows:
    cap=dt(r.get("captured_at_utc"));key=(r.get("game_id"),r.get("team_id"))
    if key in starter_latest and cap==starter_latest[key][0]:
        starter_latest[key][1].append(r)

def confirmed_starters(gid,tid):
    rec=starter_latest.get((gid,tid))
    if not rec:return []
    seen=set();out=[]
    for r in rec[1]:
        pid=str(r.get("person_id") or "")
        if pid and pid not in seen:seen.add(pid);out.append(pid)
    return out if len(out)==5 else []

# Current roster/profile lookup, with historical profile fallback.
roster=rgz(NBA/"rosters"/"current_roster_profiles.csv.gz")
hist_profiles=rgz(NBA/"players"/"historical_player_profiles.csv.gz")
profiles={}
team_roster=defaultdict(set)
for r in roster:
    pid=str(r.get("person_id") or "")
    if not pid:continue
    profiles[pid]={
      "position_abbr":(r.get("position_abbr") or "").upper(),
      "weight_lbs":n(r.get("weight_lbs")),
      "height_inches":parse_height(r.get("height"))
    }
    if r.get("team_id"):team_roster[str(r["team_id"])].add(pid)
for r in hist_profiles:
    pid=str(r.get("person_id") or "")
    if not pid:continue
    p=profiles.setdefault(pid,{})
    p.setdefault("position_abbr",(r.get("position_abbr") or "").upper())
    if p.get("weight_lbs") is None:p["weight_lbs"]=n(r.get("weight_lbs"))
    if p.get("height_inches") is None:p["height_inches"]=n(r.get("height_inches"))

# Completed current-season team/player histories.
by_team_games=defaultdict(list)
team_box={(r.get("game_id"),r.get("team_id")):r for r in teams if r.get("game_id") and r.get("team_id")}
for gid,g in completed.items():
    when=dt(g.get("game_date"))
    if not when:continue
    for tid in (str(g.get("home_team_id") or ""),str(g.get("away_team_id") or "")):
        if tid:by_team_games[tid].append((when,gid))
for tid in by_team_games:by_team_games[tid].sort()

player_by_team_game=defaultdict(list)
player_hist=defaultdict(list)
for r in players:
    if r.get("game_id") and r.get("team_id"):
        player_by_team_game[(r["game_id"],str(r["team_id"]))].append(r)
    pid=str(r.get("person_id") or "")
    g=game_by.get(r.get("game_id"));when=dt((g or {}).get("game_date"))
    if pid and when and truth((g or {}).get("completed")) and str(r.get("played")).lower() not in ("false","0","dnp","none",""):
        player_hist[pid].append((when,r))
for pid in player_hist:player_hist[pid].sort(key=lambda z:z[0])

pb_by_game=defaultdict(list)
for p in plays:
    if p.get("game_id"):pb_by_game[p["game_id"]].append(p)

def prior_games(tid,target_when,limit=None):
    z=[x for x in by_team_games.get(str(tid),[]) if x[0]<target_when]
    return z[-limit:] if limit else z
def player_last5_pm(pid,target_when):
    vals=[]
    for when,r in player_hist.get(str(pid),[]):
        if when>=target_when:break
        v=n(r.get("plus_minus"))
        if v is not None:vals.append(v)
    return mean(vals[-5:])

def prev_starter_set(tid,target_when):
    pg=prior_games(tid,target_when,1)
    if not pg:return set()
    arr=player_by_team_game.get((pg[-1][1],str(tid)),[])
    return {str(r.get("person_id")) for r in arr if truth(r.get("starter"))}

def top5_minutes_3d(tid,target_when):
    roster_ids=team_roster.get(str(tid)) or None
    per=defaultdict(lambda:{"m3":0.0,"m7":0.0,"prior":0.0,"last":None})
    for when,gid in prior_games(tid,target_when):
        age=(target_when-when).total_seconds()/86400
        if age>7:continue
        for r in player_by_team_game.get((gid,str(tid)),[]):
            pid=str(r.get("person_id") or "")
            if not pid or (roster_ids is not None and pid not in roster_ids):continue
            mins=n(r.get("minutes")) or 0.0
            if age<=7:per[pid]["m7"]+=mins
            if age<=3:per[pid]["m3"]+=mins
            if per[pid]["last"] is None or when>per[pid]["last"]:
                per[pid]["last"]=when;per[pid]["prior"]=mins
    arr=sorted(per.values(),key=lambda x:(x["m7"],x["prior"]),reverse=True)
    return sum(x["m3"] for x in arr[:5])

# Current-season exact lineup stints, if postgame enrichment has run.
stints=rgz(NBA/"lineups"/"historical_lineup_stints.csv.gz")
stints=[r for r in stints if r.get("season")==SEASON]
stints_by_team=defaultdict(list)
for r in stints:stints_by_team[str(r.get("team_id"))].append(r)

def lq_values(tid,starter_ids,target_when):
    if len(starter_ids)!=5:return (None,None)
    key="|".join(sorted(starter_ids))
    sec=pm=0.0
    by_game=defaultdict(list)
    for r in stints_by_team.get(str(tid),[]):
        gid=r.get("game_id");g=game_by.get(gid);when=dt((g or {}).get("game_date"))
        if not when or when>=target_when:continue
        by_game[gid].append(r)
        if (r.get("lineup_player_ids") or "")==key:
            sec+=(n(r.get("stint_duration_sec")) or 0);pm+=(n(r.get("stint_plus_minus")) or 0)
    prior_pm48=(2880*pm/sec) if sec>0 else None
    game_stds=[]
    for gid,arr in by_game.items():
        vals=[]
        for r in arr:
            s=n(r.get("stint_duration_sec"));p=n(r.get("stint_plus_minus"))
            if s is not None and s>=60 and p is not None:vals.append(2880*p/s)
        if len(vals)>=2:
            game_stds.append((dt(game_by[gid].get("game_date")),statistics.pstdev(vals)))
    game_stds.sort()
    vol=mean([v for _,v in game_stds[-5:]])
    return prior_pm48,vol

def starter_physical(ids):
    ps=[profiles.get(pid,{}) for pid in ids]
    guards=[];bigs=[]
    for p in ps:
        pos=(p.get("position_abbr") or "").upper();w=n(p.get("weight_lbs"));h=n(p.get("height_inches"))
        if pos in {"PG","SG","G"} and w is not None:guards.append(w)
        if pos in {"PF","C","FC","F-C","C-F"} and w is not None:bigs.append(w)
    return {"guard_weight":mean(guards),"big_weight":mean(bigs)}

def bench_shape(tid,target_when):
    last5=prior_games(tid,target_when,5);last10=prior_games(tid,target_when,10)
    bshares=[];bused=[]
    for when,gid in last5:
        arr=[r for r in player_by_team_game.get((gid,str(tid)),[]) if str(r.get("played")).lower() not in ("false","0","dnp","none","")]
        pts=sum(n(r.get("points")) or 0 for r in arr)
        bench=[r for r in arr if not truth(r.get("starter"))]
        bp=sum(n(r.get("points")) or 0 for r in bench)
        if pts>0:bshares.append(bp/pts)
        bused.append(len(bench))
    # clutch margin from PBP in final 5 min Q4, score margin <=5 before action.
    clutch=[]
    for when,gid in last10:
        g=game_by.get(gid,{});hid=str(g.get("home_team_id") or "");side_home=(str(tid)==hid)
        pp=sorted(pb_by_game.get(gid,[]),key=lambda r:n(r.get("action_number")) or 0)
        prev_h=prev_a=0.0;cp=ca=0.0
        for r in pp:
            hs=n(r.get("score_home"));aa=n(r.get("score_away"));per=n(r.get("period"))
            if hs is None or aa is None or per!=4:continue
            clk=str(r.get("clock") or "");sec=None
            m=re.match(r"PT(?:(\d+)M)?([\d.]+)S",clk)
            if m:sec=60*int(m.group(1) or 0)+float(m.group(2))
            elif ":" in clk:
                try:mm,ss=clk.split(":");sec=int(mm)*60+float(ss)
                except:pass
            before=prev_h-prev_a
            if sec is not None and sec<=300 and abs(before)<=5:
                dh=max(0,hs-prev_h);da=max(0,aa-prev_a)
                if side_home:cp+=dh;ca+=da
                else:cp+=da;ca+=dh
            prev_h,prev_a=hs,aa
        clutch.append(cp-ca)
    return {"bench_share5":mean(bshares),"bench_used5":mean(bused),"clutch10":mean(clutch)}

def style_last5(tid,target_when):
    vals=[]
    for when,gid in prior_games(tid,target_when,5):
        own=team_box.get((gid,str(tid)));g=game_by.get(gid,{})
        oid=str(g.get("away_team_id") if str(g.get("home_team_id"))==str(tid) else g.get("home_team_id"))
        opp=team_box.get((gid,oid))
        if not own or not opp:continue
        pts=n(own.get("points"));paint=n(own.get("stat_pointsinpaint") or own.get("points_in_the_paint"))
        oreb=n(own.get("rebounds_offensive"));odreb=n(opp.get("rebounds_defensive"))
        fga=n(own.get("field_goals_attempted"));fta=n(own.get("free_throws_attempted"));tov=n(own.get("turnovers"));fouls=n(own.get("fouls_personal"))
        poss=fga+0.44*fta-oreb+tov if None not in (fga,fta,oreb,tov) else None
        vals.append({
          "paint_share":paint/pts if paint is not None and pts and pts>0 else None,
          "fouls100":100*fouls/poss if fouls is not None and poss and poss>0 else None
        })
    return {"paint_share5":mean([x["paint_share"] for x in vals]),"fouls100_5":mean([x["fouls100"] for x in vals])}

# Latest prices. Use near-tip ESPN odds first, then general ESPN market snapshots.
def latest_market_rows():
    rows=rgz(PRO/"near_tip_odds_snapshots.csv.gz")+rgz(MARKET/"market_snapshots.csv.gz")
    by=defaultdict(list)
    for r in rows:
        gid=r.get("game_id");cap=dt(r.get("captured_at_utc"))
        if gid and cap and cap<=now:by[gid].append((cap,r))
    out={}
    for gid,arr in by.items():
        latest=max(x[0] for x in arr)
        out[gid]=[r for cap,r in arr if cap==latest]
    return out
market_latest=latest_market_rows()

# Fresh multi-book consensus. The external feed is allowed to override ESPN only
# when its latest capture is <=2 hours old; otherwise near-tip ESPN pricing wins.
external_rows=rgz(MARKET/"external_bookmaker_snapshots.csv.gz")
external_by_match=defaultdict(list)
for r in external_rows:
    cap=dt(r.get("captured_at_utc"))
    if not cap or cap>now:continue
    key=(norm(r.get("home_team")),norm(r.get("away_team")))
    if all(key):external_by_match[key].append((cap,r))

def external_state(g):
    key=(norm(g.get("home_team")),norm(g.get("away_team")))
    arr=external_by_match.get(key,[])
    if not arr:return None
    latest=max(cap for cap,r in arr)
    if (now-latest).total_seconds()>2*3600:return None
    rows=[r for cap,r in arr if cap==latest]
    home_name=norm(g.get("home_team"));away_name=norm(g.get("away_team"))
    books=defaultdict(dict);totals=[]
    for r in rows:
        book=r.get("bookmaker_key") or r.get("bookmaker") or ""
        mk=(r.get("market") or "").lower();out=norm(r.get("outcome"))
        if mk=="h2h":
            if out==home_name:books[book]["home"]=n(r.get("price"))
            elif out==away_name:books[book]["away"]=n(r.get("price"))
        elif mk=="totals":
            totals.append(r)
    pairs=[v for v in books.values() if v.get("home") is not None and v.get("away") is not None]
    hml=median([v["home"] for v in pairs]);aml=median([v["away"] for v in pairs])
    hi,ai=imp(hml),imp(aml)
    hp=hi/(hi+ai) if hi is not None and ai is not None and hi+ai>0 else None
    ap=ai/(hi+ai) if hi is not None and ai is not None and hi+ai>0 else None
    over=[r for r in totals if str(r.get("outcome") or "").lower()=="over"]
    under=[r for r in totals if str(r.get("outcome") or "").lower()=="under"]
    line=median([r.get("point") for r in over+under])
    op=median([r.get("price") for r in over if line is None or n(r.get("point"))==line])
    up=median([r.get("price") for r in under if line is None or n(r.get("point"))==line])
    if hml is None and aml is None and line is None:return None
    return {"home_ml":hml,"away_ml":aml,"home_prob":hp,"away_prob":ap,"total":line,"over_price":op,"under_price":up,
            "captured_at":latest.isoformat(),"source":"external_consensus","books":len(pairs)}

def market_state(gid,g):
    rows=market_latest.get(gid,[])
    hml=median([r.get("home_moneyline") for r in rows]);aml=median([r.get("away_moneyline") for r in rows])
    hi,ai=imp(hml),imp(aml)
    if hi is not None and ai is not None and hi+ai>0:
        hp=hi/(hi+ai);ap=ai/(hi+ai)
    else:hp=ap=None
    total=median([r.get("over_under") for r in rows]);op=median([r.get("over_odds") for r in rows]);up=median([r.get("under_odds") for r in rows])
    espn={"home_ml":hml,"away_ml":aml,"home_prob":hp,"away_prob":ap,"total":total,"over_price":op,"under_price":up,
          "captured_at":max([r.get("captured_at_utc") for r in rows],default=None),"source":"espn_latest","books":len(rows)}
    ext=external_state(g)
    return ext or espn

# Method evaluator helpers.
def val(row,key):return n((row or {}).get(key))
def feature_target(gid,tid,oid):
    tr=roll.get((gid,tid),{});orr=roll.get((gid,oid),{})
    tc=context.get((gid,tid),{});oc=context.get((gid,oid),{})
    tv=travel.get((gid,tid),{});ov=travel.get((gid,oid),{})
    ts=stand.get((gid,tid),{});os=stand.get((gid,oid),{})
    return {
      "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),
      "def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
      "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),
      "rest_diff":d(tc,oc,"days_since_prev_game"),
      "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival"),
      "opp_seed6_gap":val(os,"winpct_gap_to_seed6"),
      "streak_gap":d(ts,os,"current_streak"),
      "prior_games":val(tr,"prior_games_available")
    }

evaluations=[];qualified=[]
arsenal=json.loads((LIVE/"arsenal.json").read_text())
for g in upcoming:
    gid=g["game_id"];when=dt(g.get("game_date"));hrs=(when-now).total_seconds()/3600
    hid=str(g.get("home_team_id") or "");aid=str(g.get("away_team_id") or "")
    ms=market_state(gid,g)
    sides=[("home",hid,aid,g.get("home_team"),ms["home_ml"],ms["home_prob"]),("away",aid,hid,g.get("away_team"),ms["away_ml"],ms["away_prob"])]
    dynamic={}
    for side,tid,oid,tname,price,prob in sides:
        st=confirmed_starters(gid,tid);ost=confirmed_starters(gid,oid)
        base=feature_target(gid,tid,oid)
        phys=starter_physical(st) if st else {}
        ophys=starter_physical(ost) if ost else {}
        starter_pm=mean([player_last5_pm(pid,when) for pid in st]) if st else None
        ostarter_pm=mean([player_last5_pm(pid,when) for pid in ost]) if ost else None
        so=len(set(st)&prev_starter_set(tid,when)) if st else None
        oso=len(set(ost)&prev_starter_set(oid,when)) if ost else None
        work=top5_minutes_3d(tid,when);owork=top5_minutes_3d(oid,when)
        lqpm,lqvol=lq_values(tid,st,when) if st else (None,None)
        olqpm,olqvol=lq_values(oid,ost,when) if ost else (None,None)
        rs=bench_shape(tid,when);ors=bench_shape(oid,when)
        sty=style_last5(tid,when);osty=style_last5(oid,when)
        dynamic[side]={
          **base,"side":side,"team_id":tid,"opponent_id":oid,"team":tname,"price":price,"market_prob":prob,
          "starters_confirmed":len(st)==5,"starter_ids":"|".join(st),
          "guard_weight_gap":(phys.get("guard_weight")-ophys.get("guard_weight")) if None not in (phys.get("guard_weight"),ophys.get("guard_weight")) else None,
          "big_weight_gap":(phys.get("big_weight")-ophys.get("big_weight")) if None not in (phys.get("big_weight"),ophys.get("big_weight")) else None,
          "starter_pm5_gap":starter_pm-ostarter_pm if None not in (starter_pm,ostarter_pm) else None,
          "starter_overlap_gap":so-oso if None not in (so,oso) else None,
          "work_top5_3d_adv":owork-work,
          "lq_pm48_gap":lqpm-olqpm if None not in (lqpm,olqpm) else None,
          "lq_volatility_adv":olqvol-lqvol if None not in (lqvol,olqvol) else None,
          "bench_share5_gap":rs["bench_share5"]-ors["bench_share5"] if None not in (rs["bench_share5"],ors["bench_share5"]) else None,
          "bench_used5_gap":rs["bench_used5"]-ors["bench_used5"] if None not in (rs["bench_used5"],ors["bench_used5"]) else None,
          "clutch10_gap":rs["clutch10"]-ors["clutch10"] if None not in (rs["clutch10"],ors["clutch10"]) else None,
          "paint_share5":sty["paint_share5"],"fouls100_5":sty["fouls100_5"]
        }

    # Moneyline methods evaluate both sides.
    for side in ("home","away"):
        x=dynamic[side]
        tests={
          "NBA_ML001_DEF_TS": [
            ("net5_gap",x["net5_gap"],lambda v:v>=7.759),("rest_diff",x["rest_diff"],lambda v:v>=2),
            ("def5_adv",x["def5_adv"],lambda v:v<=5.5),("ts5_gap",x["ts5_gap"],lambda v:v>=0.05603199206897114)],
          "NBA_PHYSCOACH_001":[
            ("guard_weight_gap",x["guard_weight_gap"],lambda v:v<=-10),("big_weight_gap",x["big_weight_gap"],lambda v:v>=8),
            ("market_prob",x["market_prob"],lambda v:v>=0.5968)],
          "NBA_INTERACT_003":[
            ("starter_pm5_gap",x["starter_pm5_gap"],lambda v:v>=3),("starter_overlap_gap",x["starter_overlap_gap"],lambda v:v>=1),
            ("work_top5_3d_adv",x["work_top5_3d_adv"],lambda v:v>=45)],
          "NBA_TRAVEL_001":[
            ("side_is_away",0 if side=="away" else 1,lambda v:v==0),("travel7d_adv",x["travel7d_adv"],lambda v:v>=576.9),
            ("market_prob",x["market_prob"],lambda v:v>=0.7023)],
          "NBA_LQ_003":[
            ("lq_pm48_gap",x["lq_pm48_gap"],lambda v:v>=10.55),("lq_volatility_adv",x["lq_volatility_adv"],lambda v:v>=10.97),
            ("market_prob",x["market_prob"],lambda v:v>=0.5968)],
          "NBA_STAND_002":[
            ("opp_seed6_gap",x["opp_seed6_gap"],lambda v:v>=0),("streak_gap",x["streak_gap"],lambda v:v<=-2),
            ("market_prob",x["market_prob"],lambda v:v>=0.5968)],
          "NBA_ROTSHAPE_001":[
            ("bench_share5_gap",x["bench_share5_gap"],lambda v:v<=-0.08308),("bench_used5_gap",x["bench_used5_gap"],lambda v:v>=1),
            ("clutch10_gap",x["clutch10_gap"],lambda v:v>=0.5)]
        }
        for mid,conds in tests.items():
            missing=[name for name,v,fn in conds if v is None]
            ready=not missing
            ok=ready and all(fn(v) for name,v,fn in conds)
            ev={"scanned_at_utc":now.isoformat(),"season":SEASON,"game_id":gid,"scheduled_utc":g.get("game_date"),"hours_to_tip":hrs,
                "method_id":mid,"market":"moneyline","selection":x["team"],"selection_team_id":x["team_id"],"side":side,
                "qualified":ok,"ready":ready,"missing_inputs":"|".join(missing),"price":x["price"],"market_prob":x["market_prob"],
                "market_captured_at":ms["captured_at"],"market_source":ms.get("source"),"market_books":ms.get("books"),
                "starters_confirmed":x["starters_confirmed"],"prior_games":x["prior_games"],
                "features_json":json.dumps({name:v for name,v,fn in conds},separators=(",",":"),sort_keys=True)}
            evaluations.append(ev)
            if ok:qualified.append(ev.copy())

    # Total method is game-level.
    h=dynamic["home"];a=dynamic["away"]
    paint=h["paint_share5"]+a["paint_share5"] if None not in (h["paint_share5"],a["paint_share5"]) else None
    foul=h["fouls100_5"]+a["fouls100_5"] if None not in (h["fouls100_5"],a["fouls100_5"]) else None
    hc=context.get((gid,hid),{});ac=context.get((gid,aid),{})
    hr=n(hc.get("days_since_prev_game"));ar=n(ac.get("days_since_prev_game"));rest=hr+ar if None not in (hr,ar) else None
    conds=[("paint_sum",paint,lambda v:v>=0.8907),("foul_sum",foul,lambda v:v<=38.5),("rest_sum",rest,lambda v:v<=3),("total_line",ms["total"],lambda v:True)]
    missing=[name for name,v,fn in conds if v is None];ready=not missing;ok=ready and all(fn(v) for name,v,fn in conds)
    ev={"scanned_at_utc":now.isoformat(),"season":SEASON,"game_id":gid,"scheduled_utc":g.get("game_date"),"hours_to_tip":hrs,
        "method_id":"NBA_STYLE_TOT_001","market":"total_over","selection":"OVER","selection_team_id":"","side":"game",
        "qualified":ok,"ready":ready,"missing_inputs":"|".join(missing),"price":ms["over_price"],"line":ms["total"],
        "market_captured_at":ms["captured_at"],"market_source":ms.get("source"),"market_books":ms.get("books"),
        "starters_confirmed":h["starters_confirmed"] and a["starters_confirmed"],
        "prior_games":min([v for v in (h["prior_games"],a["prior_games"]) if v is not None],default=None),
        "features_json":json.dumps({name:v for name,v,fn in conds},separators=(",",":"),sort_keys=True)}
    evaluations.append(ev)
    if ok:qualified.append(ev.copy())

# Append evaluation ledger.
eval_path=OUT/"method_evaluations.csv.gz"
wgz(eval_path,rgz(eval_path)+evaluations)

# Transition ledger and active picks.
state_path=OUT/"qualification_state.json"
try:state=json.loads(state_path.read_text())
except:state={}
events=[]
current={}
for ev in evaluations:
    key="|".join([ev["game_id"],ev["method_id"],ev["selection"]])
    old=state.get(key,{})
    qual=bool(ev["qualified"])
    if old.get("qualified")!=qual:
        events.append({"changed_at_utc":now.isoformat(),"game_id":ev["game_id"],"scheduled_utc":ev["scheduled_utc"],
                       "method_id":ev["method_id"],"selection":ev["selection"],"from_qualified":old.get("qualified"),
                       "to_qualified":qual,"price":ev.get("price"),"line":ev.get("line"),"features_json":ev["features_json"]})
    first=old.get("first_qualified_at")
    if qual and not first:first=now.isoformat()
    state[key]={"qualified":qual,"first_qualified_at":first,"last_evaluated_at":now.isoformat(),
                "last_qualified_at":now.isoformat() if qual else old.get("last_qualified_at"),"last_features_json":ev["features_json"]}
    if qual:
        q=ev.copy();q["first_qualified_at"]=first;q["last_qualified_at"]=now.isoformat();current[key]=q
trans_path=OUT/"qualification_events.csv.gz"
wgz(trans_path,rgz(trans_path)+events)
state_path.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n")

active=list(current.values());active.sort(key=lambda r:(r["scheduled_utc"],r["method_id"],r["selection"]))
(OUT/"active_picks.json").write_text(json.dumps({"generated_at_utc":now.isoformat(),"season":SEASON,"picks":active},indent=2)+"\n")
scan_summary={"scanned_at_utc":now.isoformat(),"season":SEASON,"upcoming_games":len(upcoming),"evaluations":len(evaluations),
              "ready_evaluations":sum(1 for e in evaluations if e["ready"]),"qualified":len(active),"transitions":len(events),
              "next_game":upcoming[0].get("game_date") if upcoming else None,
              "policy":"Regular-season only. Evaluates frozen live arsenal against latest point-in-time data; no preseason picks."}
hist_path=OUT/"scan_history.csv.gz"
wgz(hist_path,rgz(hist_path)+[scan_summary])
(OUT/"latest.json").write_text(json.dumps(scan_summary,indent=2)+"\n")
print(json.dumps(scan_summary,indent=2))
