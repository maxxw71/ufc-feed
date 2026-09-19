#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,unicodedata,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/availability';OUT.mkdir(parents=True,exist_ok=True)
URL='https://www.mlssoccer.com/news/mlssoccer-com-injury-report'
JINA='https://r.jina.ai/'
UA='Mozilla/5.0 AppwizaMLSAvailability/1.0'

def norm(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    x=re.sub(r'[^a-z0-9]+',' ',x)
    return re.sub(r'\s+',' ',x).strip()
def fetch_text():
    req=urllib.request.Request(JINA+URL,headers={'User-Agent':UA,'Accept':'text/plain'})
    return urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')
def parse(text):
    lines=[x.strip() for x in text.splitlines()]
    team=None;rows=[]
    for line in lines:
        h=re.match(r'^#{2,4}\s+(.+?)\s*$',line)
        if h:
            cand=h.group(1).strip()
            if cand not in {'MLS Communications','Player Availability Report'} and not re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)day\b',cand,re.I):
                team=cand
            continue
        if not team or not line or line.lower()=='none':continue
        line=re.sub(r'^[-*]\s*','',line).strip()
        # OUT: Player (reason)
        m=re.match(r'^(OUT|QUESTIONABLE)\s*:\s*(.+?)(?:\s*\((.+)\))?$',line,re.I)
        if m:
            status=m.group(1).upper();player=m.group(2).strip();reason=(m.group(3) or '').strip()
            rows.append({'team_reported':team,'player_name':player,'status':status,'reason':reason});continue
        # Player - Reason (Out)
        m=re.match(r'^(.+?)\s*[-–—]\s*(.+?)\s*\((Out|Questionable)\)\s*$',line,re.I)
        if m:
            rows.append({'team_reported':team,'player_name':m.group(1).strip(),'status':m.group(3).upper(),'reason':m.group(2).strip()})
    return rows
def main():
    now=datetime.now(timezone.utc);text=fetch_text();rows=parse(text)
    asa=AmericanSoccerAnalysis()
    players=asa.get_players(leagues='mls');teams=asa.get_teams(leagues='mls')
    if not isinstance(players,pd.DataFrame):players=pd.DataFrame(players)
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    pnames={norm(r.player_name):r for _,r in players.iterrows()}
    team_names={str(r.team_id):str(r.team_name) for _,r in teams.iterrows()} if {'team_id','team_name'}.issubset(teams.columns) else {}

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

    stats={}
    team_tot= {}
    for _,r in xg.iterrows():
        pid=str(r.player_id);tid=str(r.team_id)
        v={'team_id':tid,'team_name':team_names.get(tid),'minutes':float(r.minutes_played or 0),
           'xg':float(r.xgoals or 0),'xa':float(r.xassists or 0),'xgi':float(r.xgoals_plus_xassists or 0),
           'goals':float(r.goals or 0),'assists':float(r.primary_assists or 0),'gplus':float(gpt.get((pid,tid),0)),
           'salary':float(salmap.get(pid,0))}
        stats[pid]=v
        t=team_tot.setdefault(tid,{'minutes':0,'xgi':0,'gplus':0,'salary':0})
        for k in t:t[k]+=v[k]

    enriched=[]
    unmatched=[]
    for r in rows:
        pr=pnames.get(norm(r['player_name']))
        e=dict(r);e['matched']=bool(pr is not None)
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

    teamsum={}
    for e in enriched:
        t=e.get('team_name') or e.get('team_reported')
        s=teamsum.setdefault(t,{'out':0,'questionable':0,'weighted_minutes_share':0,'weighted_xgi_share':0,'weighted_gplus_share':0,'weighted_salary_share':0,'gk_out':0})
        if e.get('status')=='OUT':s['out']+=1
        elif e.get('status')=='QUESTIONABLE':s['questionable']+=1
        for k in ['minutes','xgi','gplus','salary']:
            v=e.get('weighted_'+k+'_share')
            if v is not None:s['weighted_'+k+'_share']+=float(v)
        if e.get('status')=='OUT' and e.get('position')=='GK':s['gk_out']=1

    payload={'captured_at':now.isoformat(),'source_url':URL,'raw_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'lookback_start':start,'entries':enriched,'team_summary':teamsum,'unmatched':unmatched}
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    (OUT/f'{stamp}.json').write_text(json.dumps(payload,indent=2,default=str))
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    with (OUT/'history.jsonl').open('a') as f:f.write(json.dumps(payload,default=str,separators=(',',':'))+'\n')
    print(json.dumps({'captured_at':payload['captured_at'],'entries':len(enriched),'matched':sum(x.get('matched') for x in enriched),
                      'unmatched':len(unmatched),'teams':len(teamsum),'team_summary':teamsum},indent=2,default=str))
if __name__=='__main__':main()
