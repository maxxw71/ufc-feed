#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,unicodedata,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis
from rapidfuzz import fuzz, process
from mls_bootstrap_warehouse import canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/availability';OUT.mkdir(parents=True,exist_ok=True)
URL='https://www.mlssoccer.com/news/mlssoccer-com-injury-report'
JINA='https://r.jina.ai/'
UA='Mozilla/5.0 AppwizaMLSAvailability/1.2'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    x=re.sub(r'[^a-z0-9]+',' ',x)
    return re.sub(r'\s+',' ',x).strip()

TEAM_ALIASES={
 'atlanta united':'Atlanta United FC','atlanta united fc':'Atlanta United FC','austin fc':'Austin FC',
 'charlotte fc':'Charlotte FC','chicago fire':'Chicago Fire FC','chicago fire fc':'Chicago Fire FC',
 'fc cincinnati':'FC Cincinnati','colorado rapids':'Colorado Rapids','columbus crew':'Columbus Crew',
 'fc dallas':'FC Dallas','d c united':'D.C. United','dc united':'D.C. United',
 'houston dynamo':'Houston Dynamo FC','houston dynamo fc':'Houston Dynamo FC',
 'sporting kansas city':'Sporting Kansas City','la galaxy':'LA Galaxy',
 'los angeles football club':'Los Angeles FC','los angeles fc':'Los Angeles FC','lafc':'Los Angeles FC',
 'inter miami cf':'Inter Miami CF','inter miami':'Inter Miami CF','minnesota united fc':'Minnesota United FC',
 'cf montreal':'CF Montréal','montreal':'CF Montréal','nashville sc':'Nashville SC',
 'new england revolution':'New England Revolution','red bull new york':'New York Red Bulls',
 'new york red bulls':'New York Red Bulls','new york city football club':'New York City FC',
 'new york city fc':'New York City FC','orlando city':'Orlando City SC','orlando city sc':'Orlando City SC',
 'philadelphia union':'Philadelphia Union','portland timbers':'Portland Timbers','portland timbers fc':'Portland Timbers',
 'real salt lake':'Real Salt Lake','san diego fc':'San Diego FC','san jose earthquakes':'San Jose Earthquakes',
 'seattle sounders fc':'Seattle Sounders FC','seattle sounders':'Seattle Sounders FC',
 'st louis city sc':'St. Louis City SC','st louis city':'St. Louis City SC',
 'toronto fc':'Toronto FC','vancouver whitecaps fc':'Vancouver Whitecaps FC',
 'vancouver whitecaps':'Vancouver Whitecaps FC'
}

def canon_report_team(s):
    n=norm(s)
    return TEAM_ALIASES.get(n,canon_team(s))

def reason_category(reason):
    n=norm(reason)
    if not n:return 'unspecified'
    if 'suspend' in n or 'red card' in n or 'yellow card' in n:return 'suspension'
    if 'international' in n:return 'international_duty'
    if 'not due to injury' in n:return 'non_injury'
    if 'illness' in n:return 'illness'
    if 'concussion' in n or 'head injury evaluation' in n:return 'concussion'
    return 'injury'

def fetch_text():
    req=urllib.request.Request(JINA+URL,headers={'User-Agent':UA,'Accept':'text/plain'})
    return urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')

def clean_md(line):
    line=re.sub(r'!\[[^\]]*\]\([^)]*\)',' ',str(line or ''))
    line=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',line)
    line=re.sub(r'^#{1,6}\s*','',line)
    line=re.sub(r'^[-*]\s*','',line)
    line=line.replace('**','').replace('__','')
    return re.sub(r'\s+',' ',line).strip()

def parse(text):
    lines=[clean_md(x) for x in text.splitlines()]
    team=None;rows=[];teams_seen=set();clear_teams=set()
    for line in lines:
        if not line:continue
        n=norm(line)
        if n.startswith('image ') and n.endswith(' logo'):continue
        if n in TEAM_ALIASES:
            team=TEAM_ALIASES[n];teams_seen.add(team);continue
        if not team:continue
        if n in {'none','no players listed','n a'}:
            clear_teams.add(team);continue
        m=re.match(r'^(OUT|QUESTIONABLE)\s*:\s*(.+?)(?:\s*\((.+)\))?\s*$',line,re.I)
        if m:
            status=m.group(1).upper();player=m.group(2).strip();reason=(m.group(3) or '').strip()
            rows.append({'team_reported':team,'player_name':player,'status':status,'reason':reason,'reason_category':reason_category(reason)})
            continue
        m=re.match(r'^(.+?)\s*[-–—]\s*(.+?)\s*\((Out|Questionable)\)\s*\)?\s*$',line,re.I)
        if m:
            reason=m.group(2).strip()
            rows.append({'team_reported':team,'player_name':m.group(1).strip(),'status':m.group(3).upper(),'reason':reason,'reason_category':reason_category(reason)})
    return rows,sorted(teams_seen),sorted(clear_teams)

def main():
    now=datetime.now(timezone.utc);text=fetch_text();rows,teams_seen,clear_teams=parse(text)
    if not rows:
        raise RuntimeError('Official MLS availability page parsed zero player entries; fail closed')
    if len(teams_seen)<20:
        raise RuntimeError(f'Official MLS availability page exposed only {len(teams_seen)} recognized clubs; fail closed')

    asa=AmericanSoccerAnalysis()
    players=asa.get_players(leagues='mls');teams=asa.get_teams(leagues='mls')
    if not isinstance(players,pd.DataFrame):players=pd.DataFrame(players)
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    players=players.drop_duplicates('player_id',keep='last').copy()
    by_pid={str(r.player_id):r for _,r in players.iterrows()}
    pnames={}
    for _,r in players.iterrows():pnames.setdefault(norm(r.player_name),[]).append(r)
    team_names={str(r.team_id):canon_report_team(r.team_name) for _,r in teams.iterrows()} if {'team_id','team_name'}.issubset(teams.columns) else {}

    start=(now-timedelta(days=120)).date().isoformat();end=now.date().isoformat()
    xg=asa.get_player_xgoals(leagues='mls',start_date=start,end_date=end,split_by_teams=True)
    gp=asa.get_player_goals_added(leagues='mls',start_date=start,end_date=end,split_by_teams=True)
    if not isinstance(xg,pd.DataFrame):xg=pd.DataFrame(xg)
    if not isinstance(gp,pd.DataFrame):gp=pd.DataFrame(gp)
    gpt={}
    for _,r in gp.iterrows():
        gpt[(str(r.player_id),str(r.team_id))]=sum(float(x.get('goals_added_raw') or 0) for x in (r.data or []))

    sal=asa.get_player_salaries(leagues='mls',season_name=str(now.year))
    if not isinstance(sal,pd.DataFrame):sal=pd.DataFrame(sal)
    if len(sal):
        sal['mlspa_release']=pd.to_datetime(sal.mlspa_release,errors='coerce',utc=True)
        sal=sal[sal.mlspa_release.le(pd.Timestamp(now))].sort_values('mlspa_release').drop_duplicates('player_id',keep='last')
    salmap={str(r.player_id):float(r.guaranteed_compensation) for _,r in sal.iterrows() if pd.notna(r.guaranteed_compensation)}

    stats={};team_tot={};team_rosters={}
    for _,r in xg.iterrows():
        pid=str(r.player_id);tid=str(r.team_id);tname=team_names.get(tid)
        v={'team_id':tid,'team_name':tname,'minutes':float(r.minutes_played or 0),
           'xg':float(r.xgoals or 0),'xa':float(r.xassists or 0),'xgi':float(r.xgoals_plus_xassists or 0),
           'goals':float(r.goals or 0),'assists':float(r.primary_assists or 0),'gplus':float(gpt.get((pid,tid),0)),
           'salary':float(salmap.get(pid,0))}
        stats[pid]=v
        if tname:team_rosters.setdefault(tname,set()).add(pid)
        t=team_tot.setdefault(tid,{'minutes':0,'xgi':0,'gplus':0,'salary':0})
        for k in t:t[k]+=v[k]

    def resolve_player(name,report_team):
        exact=pnames.get(norm(name),[])
        if len(exact)==1:return exact[0],'exact',100.0
        if exact:
            same=[r for r in exact if stats.get(str(r.player_id),{}).get('team_name')==report_team]
            if len(same)==1:return same[0],'exact_team',100.0
        roster=list(team_rosters.get(report_team,set()))
        choices={pid:str(getattr(by_pid.get(pid),'player_name',pid)) for pid in roster if pid in by_pid}
        if choices:
            ranked=process.extract(name,choices,scorer=fuzz.WRatio,limit=2)
            if ranked:
                top_name,score,pid=ranked[0]
                second=ranked[1][1] if len(ranked)>1 else 0.0
                if score>=80.0 and score-second>=8.0:
                    return by_pid[str(pid)],'fuzzy_team',float(score)
        return None,'unmatched',None

    enriched=[];unmatched=[]
    for r in rows:
        reported_team=canon_report_team(r['team_reported'])
        pr,method,score=resolve_player(r['player_name'],reported_team)
        e=dict(r);e['team_reported']=reported_team;e['match_method']=method;e['match_score']=score;e['matched']=bool(pr is not None)
        if pr is None:
            unmatched.append(r);enriched.append(e);continue
        pid=str(pr.player_id);e['player_id']=pid;e['position']=getattr(pr,'primary_general_position',None)
        st=stats.get(pid,{})
        e.update({k:st.get(k) for k in ['team_id','team_name','minutes','xg','xa','xgi','goals','assists','gplus','salary']})
        tot=team_tot.get(str(st.get('team_id')),{})
        w=1.0 if r['status']=='OUT' else .5
        e['status_weight']=w
        for k in ['minutes','xgi','gplus','salary']:
            den=float(tot.get(k,0) or 0);num=float(st.get(k,0) or 0)
            e[k+'_team_share']=num/den if den>0 else None
            e['weighted_'+k+'_share']=(num/den*w) if den>0 else None
        enriched.append(e)

    base_summary={'out':0,'questionable':0,'weighted_minutes_share':0,'weighted_xgi_share':0,'weighted_gplus_share':0,
                  'weighted_salary_share':0,'gk_out':0,'injury_out':0,'non_injury_out':0,'suspension_out':0,
                  'international_duty_out':0,'illness_out':0,'concussion_out':0,'unmatched':0}
    teamsum={canon_report_team(t):dict(base_summary) for t in teams_seen}
    for e in enriched:
        t=canon_report_team(e.get('team_name') or e.get('team_reported'))
        s=teamsum.setdefault(t,dict(base_summary))
        if e.get('status')=='OUT':
            s['out']+=1
            cat=e.get('reason_category')
            key={'injury':'injury_out','non_injury':'non_injury_out','suspension':'suspension_out',
                 'international_duty':'international_duty_out','illness':'illness_out','concussion':'concussion_out'}.get(cat)
            if key:s[key]+=1
        elif e.get('status')=='QUESTIONABLE':s['questionable']+=1
        for k in ['minutes','xgi','gplus','salary']:
            v=e.get('weighted_'+k+'_share')
            if v is not None:s['weighted_'+k+'_share']+=float(v)
        if e.get('status')=='OUT' and e.get('position')=='GK':s['gk_out']=1
        if not e.get('matched'):s['unmatched']+=1

    payload={'captured_at':now.isoformat(),'source_url':URL,'raw_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'lookback_start':start,'teams_seen':sorted(teamsum),'clear_teams':sorted(canon_report_team(t) for t in clear_teams),
             'entries':enriched,'team_summary':teamsum,'unmatched':unmatched}
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    (OUT/f'{stamp}.json').write_text(json.dumps(payload,indent=2,default=str))
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    with (OUT/'history.jsonl').open('a') as f:f.write(json.dumps(payload,default=str,separators=(',',':'))+'\n')
    print(json.dumps({'captured_at':payload['captured_at'],'entries':len(enriched),'matched':sum(bool(x.get('matched')) for x in enriched),
                      'fuzzy_matched':sum(x.get('match_method')=='fuzzy_team' for x in enriched),'unmatched':len(unmatched),
                      'teams_seen':len(teamsum),'clear_teams':len(clear_teams),'team_summary':teamsum},indent=2,default=str))
if __name__=='__main__':main()
