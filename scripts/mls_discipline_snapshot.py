#!/usr/bin/env python3
from __future__ import annotations

import hashlib,json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd
from itscalledsoccer import AmericanSoccerAnalysis

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'live/discipline';OUT.mkdir(parents=True,exist_ok=True)
URL='https://www.mlssoccer.com/news/mls-disciplinary-summary'
JINA='https://r.jina.ai/'
UA='Mozilla/5.0 AppwizaMLSDiscipline/1.0'

def fetch():
    req=urllib.request.Request(JINA+URL,headers={'User-Agent':UA,'Accept':'text/plain'})
    return urllib.request.urlopen(req,timeout=60).read().decode('utf-8','replace')

def clean(line):
    line=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',str(line or ''))
    line=re.sub(r'^#{1,6}\s*','',line)
    line=re.sub(r'^[-*]\s*','',line)
    line=line.replace('**','').replace('__','')
    return re.sub(r'\s+',' ',line).strip()

def main():
    now=datetime.now(timezone.utc);text=fetch()
    asa=AmericanSoccerAnalysis()
    teams=asa.get_teams(leagues='mls')
    if not isinstance(teams,pd.DataFrame):teams=pd.DataFrame(teams)
    amap={}
    for _,r in teams.iterrows():
        ab=str(getattr(r,'team_abbreviation','') or '').upper().strip()
        if ab:amap[ab]={'team_id':str(r.team_id),'team_name':str(r.team_name)}
    # Common public abbreviations differing from ASA.
    aliases={'ATL':'Atlanta United','ATX':'Austin FC','CLT':'Charlotte FC','CHI':'Chicago Fire FC',
             'CIN':'FC Cincinnati','COL':'Colorado Rapids','CLB':'Columbus Crew','DAL':'FC Dallas',
             'DC':'D.C. United','HOU':'Houston Dynamo FC','SKC':'Sporting Kansas City','LA':'LA Galaxy',
             'LAFC':'Los Angeles FC','MIA':'Inter Miami CF','MIN':'Minnesota United FC','MTL':'CF Montréal',
             'NSH':'Nashville SC','NE':'New England Revolution','RBNY':'New York Red Bulls','NYC':'New York City FC',
             'ORL':'Orlando City SC','PHI':'Philadelphia Union','POR':'Portland Timbers','RSL':'Real Salt Lake',
             'SD':'San Diego FC','SJ':'San Jose Earthquakes','SEA':'Seattle Sounders FC','STL':'St. Louis CITY SC',
             'TOR':'Toronto FC','VAN':'Vancouver Whitecaps FC'}
    by_name={str(r.team_name):{'team_id':str(r.team_id),'team_name':str(r.team_name)} for _,r in teams.iterrows()}
    def team(ab):
        ab=ab.upper().strip()
        if ab in amap:return amap[ab]
        name=aliases.get(ab)
        return by_name.get(name,{'team_id':None,'team_name':name}) if name else {'team_id':None,'team_name':None}

    lines=[clean(x) for x in text.splitlines() if clean(x)]
    source_as_of=None;suspensions=[];warnings=[];section=None;pending=None
    for line in lines:
        low=line.lower()
        if low=='notice of suspension':
            section='suspension';continue
        if low=='caution accumulation warnings':
            if pending:suspensions.append(pending);pending=None
            section='warning';continue
        mdate=re.match(r'^As of\s+(.+)$',line,re.I)
        if mdate and not source_as_of:source_as_of=mdate.group(1).strip()
        if section=='suspension':
            m=re.match(r'^(.+?)\s*\(([A-Z]{2,5})\)\s*-\s*Suspended:\s*(.+)$',line,re.I)
            if m:
                if pending:suspensions.append(pending)
                info=team(m.group(2))
                pending={'player_name':m.group(1).strip(),'team_abbreviation':m.group(2).upper(),
                         'team_id':info['team_id'],'team_name':info['team_name'],'reason':m.group(3).strip(),'applies_to':None}
                continue
            if pending and re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b',line,re.I):
                pending['applies_to']=line
                suspensions.append(pending);pending=None
                continue
        elif section=='warning':
            m=re.match(r'^(.+?),\s*([A-Z]{2,5})$',line)
            if m:
                info=team(m.group(2))
                warnings.append({'player_name':m.group(1).strip(),'team_abbreviation':m.group(2),
                                 'team_id':info['team_id'],'team_name':info['team_name']})
    if pending:suspensions.append(pending)
    if not suspensions and not warnings:
        raise RuntimeError('MLS disciplinary summary parsed no suspensions or warnings; fail closed')

    payload={'captured_at':now.isoformat(),'source_url':URL,'source_as_of':source_as_of,
             'raw_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'suspensions':suspensions,'caution_warnings':warnings}
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    (OUT/f'{stamp}.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False))
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False))
    with (OUT/'history.jsonl').open('a') as f:f.write(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n')
    print(json.dumps({'captured_at':payload['captured_at'],'source_as_of':source_as_of,
                      'suspensions':len(suspensions),'warnings':len(warnings)},indent=2))

if __name__=='__main__':main()
