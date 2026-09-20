#!/usr/bin/env python3
from __future__ import annotations

import json,re,time,urllib.request
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mls_bootstrap_warehouse import TEAM_INFO,canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
RAW=ROOT/'data/raw/cross_comp'; PROC=ROOT/'data/processed'; REP=ROOT/'reports'
for p in [RAW,PROC,REP]:p.mkdir(parents=True,exist_ok=True)

BASE=PROC/'mls_match_features_weather_enriched.parquet'
EVENTS_OUT=PROC/'mls_cross_comp_events.parquet'
OUT=PROC/'mls_cross_comp_features.parquet'
META=REP/'cross_comp_feature_meta.json'
JINA='https://r.jina.ai/'
UA='Mozilla/5.0 AppwizaMLSCrossComp/1.0'

ESPN_COMPETITIONS={
  'US_OPEN_CUP':'usa.open',
  'LEAGUES_CUP':'concacaf.leagues.cup',
  'CONCACAF_CHAMPIONS':'concacaf.champions',
}
MLS_TEAMS={canon_team(x) for x in TEAM_INFO}
ALIASES={
 'Atlanta United':'Atlanta United FC','LAFC':'Los Angeles FC','CF Montreal':'CF Montréal','Montreal Impact':'CF Montréal',
 'New York Red Bulls':'New York Red Bulls','Red Bull New York':'New York Red Bulls',
 'St. Louis CITY SC':'St. Louis City SC','St. Louis City SC':'St. Louis City SC',
 'Seattle Sounders':'Seattle Sounders FC','Vancouver Whitecaps':'Vancouver Whitecaps FC',
 'Houston Dynamo':'Houston Dynamo FC','Minnesota United':'Minnesota United FC','Orlando City':'Orlando City SC',
}

def now():return datetime.now(timezone.utc).isoformat()
def cteam(x):
    raw=str(x or '').strip()
    return canon_team(ALIASES.get(raw,raw))

def fetch(url,timeout=90,retries=4):
    last=None
    for a in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/plain'})
            return urllib.request.urlopen(req,timeout=timeout).read().decode('utf-8','replace')
        except Exception as e:
            last=e;time.sleep(1.5*(a+1))
    raise last

def jina_json(url):
    body=fetch(JINA+url)
    payload=body.split('Markdown Content:',1)[-1].strip()
    p=payload.find('{')
    if p<0:raise RuntimeError('No JSON object in Jina response')
    return json.loads(payload[p:]),body

def espn_year(label,slug,year):
    cache=RAW/f'espn_{slug.replace(".","_")}_{year}.json'
    rows=None
    if cache.exists():
        try: rows=json.loads(cache.read_text())
        except Exception: rows=None
    if rows is None:
        url=f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={year}&limit=1000'
        d,_=jina_json(url)
        rows=[]
        for ev in d.get('events') or []:
            comp=(ev.get('competitions') or [{}])[0]
            status=(comp.get('status') or ev.get('status') or {})
            stype=status.get('type') or {}
            completed=bool(stype.get('completed')) or str(stype.get('name','')).upper() in {'STATUS_FINAL','FINAL'}
            period=pd.to_numeric(status.get('period'),errors='coerce')
            detail=' '.join(str(x or '') for x in [stype.get('detail'),stype.get('shortDetail'),status.get('displayClock')])
            extra=bool((pd.notna(period) and float(period)>2) or re.search(r'\b(AET|EXTRA TIME|ET)\b',detail,re.I))
            teams={};scores={}
            for q in comp.get('competitors') or []:
                ha=q.get('homeAway')
                teams[ha]=cteam((q.get('team') or {}).get('displayName'))
                scores[ha]=pd.to_numeric(q.get('score'),errors='coerce')
            if not teams.get('home') or not teams.get('away'):continue
            rows.append({
              'event_id':str(ev.get('id')),'competition':label,'source':'ESPN',
              'date_time_utc':ev.get('date'),'home_team':teams['home'],'away_team':teams['away'],
              'home_score':None if pd.isna(scores.get('home')) else float(scores['home']),
              'away_score':None if pd.isna(scores.get('away')) else float(scores['away']),
              'completed':completed,'extra_time':extra,'minutes_estimate':120 if extra else 90,
              'source_url':url,
            })
    # Hard quality gate: a year query may return stale/adjacent-season events.
    clean=[]
    for r in rows:
        dt=pd.to_datetime(r.get('date_time_utc'),errors='coerce',utc=True)
        if pd.isna(dt) or int(dt.year)!=int(year):continue
        clean.append(r)
    cache.write_text(json.dumps(clean,ensure_ascii=False,separators=(',',':')))
    return clean

def parse_image_team(line):
    x=line.strip()
    # Jina/Canada Soccer may render images as markdown links:
    # ![Image: Toronto FC](...) Toronto FC 5
    alt=None
    ma=re.search(r'Image:\s*([^\]]+)',x,re.I)
    if ma:alt=ma.group(1).strip()
    x=re.sub(r'!?\[[^\]]*\]\([^\)]*\)',' ',x)
    x=re.sub(r'^.*?Image:\s*','',x,flags=re.I).strip()
    x=re.sub(r'\s+',' ',x)
    # Score can be "0", "2 (5)" etc.; penalty score is not the regulation score.
    m=re.match(r'^(.*?)(?:\s+(-?\d+))(?:\s*\(\d+\))?\s*$',x)
    if m:
        body=(m.group(1) or '').strip();score=float(m.group(2))
    else:
        body=x;score=None
    name=body
    if alt:
        # Prefer the explicit image alt club name when it is sensible.
        ca=cteam(alt)
        if ca: name=alt
    words=name.split()
    if len(words)%2==0 and len(words)>=2:
        h=len(words)//2
        if words[:h]==words[h:]:name=' '.join(words[:h])
    return cteam(name),score

def canada_year(year):
    cache=RAW/f'canadian_championship_{year}.json'
    if cache.exists():
        cached=json.loads(cache.read_text())
        # Old zero-row cache is not authoritative; retry source.
        if cached:return cached
    url=f'https://www.canadasoccer.com/championship/canChamp/{year}/'
    text=fetch(JINA+url)
    md=text.split('Markdown Content:',1)[-1]
    # Canada Soccer Markdown consistently renders:
    # Image: Team Team SCORE ... Image: Team Team SCORE ... DD Mon YYYY ... Full Time
    block_re=re.compile(
      r'Image:\s*(.+?)\s+(-?\d+)(?:\s*\([^\n]+\))?\s*\n+'
      r'(?:.*?\n+){0,3}?Image:\s*(.+?)\s+(-?\d+)(?:\s*\([^\n]+\))?\s*\n+'
      r'(?:.*?\n+){0,5}?(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\s*\n+'
      r'(?:.*?\n+){0,3}?Full\s+Time',
      re.I
    )
    months={m[:3].lower():i for i,m in enumerate(
      ['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
    def dedupe_name(raw):
        raw=clean(raw)
        words=raw.split()
        for n in range(1,len(words)//2+1):
            if words[:n]==words[n:2*n] and 2*n==len(words):
                return ' '.join(words[:n])
        # common Canada Soccer image alt duplicates exact club name before score
        half=len(words)//2
        if len(words)%2==0 and words[:half]==words[half:]:return ' '.join(words[:half])
        return raw
    rows=[]
    for m in block_re.finditer(md):
        ht=cteam(dedupe_name(m.group(1)));at=cteam(dedupe_name(m.group(3)))
        mon=months.get(m.group(6)[:3].lower())
        if not mon:continue
        try:dt=pd.Timestamp(datetime(int(m.group(7)),mon,int(m.group(5)),12,tzinfo=timezone.utc))
        except Exception:continue
        rows.append({
          'event_id':f'CAN-{year}-{len(rows)+1:03d}','competition':'CANADIAN_CHAMPIONSHIP','source':'Canada Soccer',
          'date_time_utc':dt.isoformat(),'home_team':ht,'away_team':at,
          'home_score':float(m.group(2)),'away_score':float(m.group(4)),
          'completed':True,'extra_time':False,'minutes_estimate':90,'source_url':url,
        })
    # Fallback line parser for formats not caught by block regex.
    if not rows:
        lines=[re.sub(r'\s+',' ',x).strip() for x in md.splitlines() if x.strip()]
        for i,line in enumerate(lines):
            if not line.lower().startswith('image:'):continue
            for j in range(i+1,min(i+6,len(lines))):
                if not lines[j].lower().startswith('image:'):continue
                ht,hs=parse_image_team(line);at,as_=parse_image_team(lines[j])
                dt=None;full=False
                for k in range(j+1,min(j+10,len(lines))):
                    mm=re.match(r'^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$',lines[k])
                    if mm:
                        mon=months.get(mm.group(2)[:3].lower())
                        if mon:dt=pd.Timestamp(datetime(int(mm.group(3)),mon,int(mm.group(1)),12,tzinfo=timezone.utc))
                    if re.search(r'Full\s+Time',lines[k],re.I):full=True
                if dt is not None and full and ht and at and hs is not None and as_ is not None:
                    rows.append({'event_id':f'CAN-{year}-{len(rows)+1:03d}','competition':'CANADIAN_CHAMPIONSHIP',
                                 'source':'Canada Soccer','date_time_utc':dt.isoformat(),'home_team':ht,'away_team':at,
                                 'home_score':hs,'away_score':as_,'completed':True,'extra_time':False,
                                 'minutes_estimate':90,'source_url':url})
                break
    seen=set();clean_rows=[]
    for r in rows:
        k=(r['date_time_utc'][:10],r['home_team'],r['away_team'])
        if k in seen:continue
        seen.add(k);clean_rows.append(r)
    cache.write_text(json.dumps(clean_rows,ensure_ascii=False,separators=(',',':')))
    return clean_rows

def build_events():
    rows=[];coverage={}
    for y in range(2013,2027):
        for label,slug in ESPN_COMPETITIONS.items():
            try:
                z=espn_year(label,slug,y)
                rows.extend(z);coverage[f'{label}_{y}']=len(z)
            except Exception as e:
                coverage[f'{label}_{y}']={'error':type(e).__name__+': '+str(e)[:180]}
        try:
            z=canada_year(y);rows.extend(z);coverage[f'CANADIAN_CHAMPIONSHIP_{y}']=len(z)
        except Exception as e:
            coverage[f'CANADIAN_CHAMPIONSHIP_{y}']={'error':type(e).__name__+': '+str(e)[:180]}
    d=pd.DataFrame(rows)
    if d.empty:raise RuntimeError('No cross-competition events collected')
    d['date_time_utc']=pd.to_datetime(d.date_time_utc,errors='coerce',utc=True)
    d=d[d.date_time_utc.notna()].drop_duplicates(['competition','date_time_utc','home_team','away_team']).copy()
    # Only events involving an MLS club are relevant to MLS workload.
    d=d[d.home_team.isin(MLS_TEAMS)|d.away_team.isin(MLS_TEAMS)].copy()
    d.to_parquet(EVENTS_OUT,index=False)
    return d,coverage

def team_external_rows(events):
    out=defaultdict(list)
    for _,r in events[events.completed.eq(True)].sort_values('date_time_utc').iterrows():
        for side,opp_side in [('home','away'),('away','home')]:
            t=cteam(r[f'{side}_team'])
            if t not in MLS_TEAMS:continue
            out[t].append({
              'dt':pd.Timestamp(r.date_time_utc),'competition':r.competition,'is_home':int(side=='home'),
              'opponent':cteam(r[f'{opp_side}_team']),'minutes':float(r.minutes_estimate or 90),
              'extra_time':int(bool(r.extra_time)),
            })
    return out

def main():
    if not BASE.exists():raise RuntimeError(f'Missing base {BASE}')
    base=pd.read_parquet(BASE).copy()
    base['dt']=pd.to_datetime(base.date,errors='coerce',utc=True)+pd.Timedelta(hours=12)
    events,coverage=build_events()
    ext=team_external_rows(events)

    # All-competition history includes every prior MLS warehouse match plus external cup matches.
    mls_hist=defaultdict(list)
    for _,r in base[base.dt.notna()].sort_values(['dt','match_id']).iterrows():
        for side,opp in [('home','away'),('away','home')]:
            t=cteam(r[f'{side}_team'])
            mls_hist[t].append({'dt':pd.Timestamp(r['dt']),'competition':'MLS','is_home':int(side=='home'),
                                'opponent':cteam(r[f'{opp}_team']),'minutes':90.0,'extra_time':0,'match_id':str(r.match_id)})

    rows=[]
    for _,g in base[base.dt.notna()].sort_values(['dt','match_id']).iterrows():
        row={'match_id':g.match_id}
        for side in ['home','away']:
            team=cteam(g[f'{side}_team']);target=pd.Timestamp(g['dt'])
            history=[]
            for x in mls_hist.get(team,[]):
                if x['dt']<target:history.append(x)
            for x in ext.get(team,[]):
                if x['dt']<target:history.append(x)
            history.sort(key=lambda x:x['dt'])
            prior=[x for x in history if not (x.get('match_id')==str(g.match_id))]
            last=prior[-1] if prior else None
            row[f'{side}_allcomp_days_rest']=(target-last['dt']).total_seconds()/86400 if last else np.nan
            row[f'{side}_allcomp_last_competition']=last['competition'] if last else None
            for days in [3,7,14,21,30]:
                lo=target-pd.Timedelta(days=days)
                q=[x for x in prior if x['dt']>=lo]
                qe=[x for x in q if x['competition']!='MLS']
                row[f'{side}_allcomp_matches_{days}d']=len(q)
                row[f'{side}_external_matches_{days}d']=len(qe)
                row[f'{side}_external_minutes_{days}d']=sum(x['minutes'] for x in qe)
                row[f'{side}_extra_time_matches_{days}d']=sum(x['extra_time'] for x in qe)
            qext=[x for x in prior if x['competition']!='MLS']
            row[f'{side}_days_since_external_match']=(target-qext[-1]['dt']).total_seconds()/86400 if qext else np.nan
            for comp in ESPN_COMPETITIONS.keys()|{'CANADIAN_CHAMPIONSHIP'}:
                q=[x for x in prior if x['competition']==comp and x['dt']>=target-pd.Timedelta(days=30)]
                row[f'{side}_{comp.lower()}_matches_30d']=len(q)

        for k in ['allcomp_days_rest','allcomp_matches_3d','allcomp_matches_7d','allcomp_matches_14d',
                  'external_matches_7d','external_matches_14d','external_minutes_7d','external_minutes_14d',
                  'extra_time_matches_14d','days_since_external_match']:
            h=row.get('home_'+k);a=row.get('away_'+k)
            row['edge_'+k]=(h-a) if pd.notna(h) and pd.notna(a) else np.nan
        rows.append(row)

    feat=pd.DataFrame(rows);feat.to_parquet(OUT,index=False)
    comp_counts={str(k):int(v) for k,v in events.groupby('competition').size().to_dict().items()}
    meta={
      'built_at':now(),'rows':len(feat),'columns':len(feat.columns),'output':str(OUT),
      'external_events':len(events),'external_event_counts':comp_counts,'source_coverage':coverage,
      'feature_non_null':{c:int(feat[c].notna().sum()) for c in feat.columns if c!='match_id'},
      'leakage_note':'All workload features include only competitive matches strictly before the target MLS match. External cup data is sourced from ESPN yearly scoreboards and Canada Soccer championship pages.',
      'limitations':'External-event minutes are estimated as 90, or 120 when ESPN explicitly indicates extra time. Player-level cup minutes are a separate enrichment stage.'
    }
    META.write_text(json.dumps(meta,indent=2,default=str))
    print(json.dumps(meta,indent=2,default=str))

if __name__=='__main__':main()
