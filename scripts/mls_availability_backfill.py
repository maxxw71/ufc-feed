#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd

from mls_bootstrap_warehouse import TEAM_INFO, canon_team

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
OUT=ROOT/'data/raw/availability_archive'
OUT.mkdir(parents=True,exist_ok=True)

JINA='https://r.jina.ai/'
MLS='https://www.mlssoccer.com'
MEDIA=MLS+'/media-resources/press-releases/more/'
UA='Mozilla/5.0 AppwizaMLSAvailabilityArchive/2.0'
TARGET_YEARS={2024,2025}
TEAM_CANON={canon_team(x) for x in TEAM_INFO}

def fetch_text(url):
    req=urllib.request.Request(JINA+url,headers={'User-Agent':UA,'Accept':'text/plain'})
    return urllib.request.urlopen(req,timeout=30).read().decode('utf-8','replace')

def fetch_html(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html'})
    return urllib.request.urlopen(req,timeout=15).read().decode('utf-8','replace')

def clean_md(line):
    line=re.sub(r'!\[[^\]]*\]\([^)]*\)',' ',str(line or ''))
    line=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',line)
    line=re.sub(r'^#{1,6}\s*','',line)
    line=re.sub(r'^[-*]\s*','',line)
    line=line.replace('**','').replace('__','')
    return re.sub(r'\s+',' ',line).strip()

def is_team_line(line):
    if not line:
        return None
    c=canon_team(clean_md(line))
    return c if c in TEAM_CANON else None

def parse_entries(text):
    team=None
    teams_seen=[]
    clear_teams=set()
    rows=[]
    for raw in text.splitlines():
        line=clean_md(raw)
        if not line:
            continue

        maybe_team=is_team_line(line)
        if maybe_team:
            team=maybe_team
            if team not in teams_seen:
                teams_seen.append(team)
            continue

        if not team:
            continue

        n=line.strip().lower().rstrip('.')
        if n in {'none','no players listed','n/a','na'}:
            clear_teams.add(team)
            continue

        m=re.match(r'^(OUT|QUESTIONABLE)\s*:\s*(.+?)(?:\s*\((.+)\))?\s*$',line,re.I)
        if m:
            rows.append({
                'team_reported':team,
                'player_name':m.group(2).strip(),
                'status':m.group(1).upper(),
                'reason':(m.group(3) or '').strip(),
            })
            continue

        m=re.match(r'^(.+?)\s*[-–—]\s*(.+?)\s*\((Out|Questionable)\)\s*\)?\s*$',line,re.I)
        if m:
            rows.append({
                'team_reported':team,
                'player_name':m.group(1).strip(),
                'status':m.group(3).upper(),
                'reason':m.group(2).strip(),
            })

    status_teams={r['team_reported'] for r in rows}
    # A team explicitly present in a full report with no status rows is a real
    # negative observation, not missing data.
    for t in teams_seen:
        if t not in status_teams:
            clear_teams.add(t)
    return rows,teams_seen,sorted(clear_teams)

def extract_matchday(text):
    m=re.search(r'\bMatchday\s+(\d+)\b',text,re.I)
    return int(m.group(1)) if m else None

def extract_dates(text,year):
    months={m:i for i,m in enumerate(
        ['January','February','March','April','May','June','July','August','September','October','November','December'],1
    )}
    dates=[]
    pattern=r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+([A-Za-z]+)\s+(\d{1,2})'
    for m in re.finditer(pattern,text,re.I):
        mon=months.get(m.group(1).title())
        if mon:
            try:
                dates.append(datetime(year,mon,int(m.group(2))).date().isoformat())
            except ValueError:
                pass
    return sorted(set(dates))

def extract_published_year(url):
    try:
        html=fetch_html(url)
    except Exception:
        return None

    patterns=[
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"publishedAt"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        m=re.search(pattern,html,re.I)
        if not m:
            continue
        y=re.match(r'\s*(20\d{2})',m.group(1))
        if y:
            return int(y.group(1))
    return None

def discover_status_urls():
    discovered={}

    def get_media(page):
        url=MEDIA+str(page)
        try:
            return page,fetch_text(url),None
        except Exception as e:
            return page,None,(type(e).__name__,str(e)[:100])

    # Fetch Media Resources pages concurrently; this is discovery only and
    # preserves the same first-party source and publication-year verification.
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(get_media,page) for page in range(1,31)]
        for fut in as_completed(futs):
            page,text,err=fut.result()
            if err:
                print('MEDIA_MISS',page,*err,flush=True)
                continue
            for label,href in re.findall(r'\[([^\]]*Player Status Report[^\]]*)\]\((https?://[^)]+)\)',text,re.I):
                full=urljoin(MLS,href)
                md=extract_matchday(label)
                if md is None:
                    continue
                discovered.setdefault(full,{'url':full,'label':clean_md(label),'media_pages':set()})['media_pages'].add(page)

    records=list(discovered.values())
    for rec in records:
        rec['media_pages']=sorted(rec['media_pages'])

    def add_year(rec):
        return rec,extract_published_year(rec['url'])

    out=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(add_year,rec) for rec in records]
        for fut in as_completed(futs):
            rec,year=fut.result()
            rec['published_year']=year
            if year in TARGET_YEARS:
                out.append(rec)

    return sorted(out,key=lambda x:(x['published_year'],extract_matchday(x['label']) or 999,x['url']))

def legacy_candidates():
    # Retain historical guessed slugs as a fallback; discovered Media Resources
    # URLs win when available.
    for n in range(1,41):
        yield {'published_year':2024,'url':MLS+f'/news/mls-player-status-report-matchday-{n}','label':f'Matchday {n}','media_pages':[]}
    for n in range(1,41):
        yield {'published_year':2025,'url':MLS+f'/news/mls-player-status-report-matchday-{n}-2025','label':f'Matchday {n}','media_pages':[]}

def main():
    discovered=discover_status_urls()
    print('DISCOVERED',len(discovered),'media-resource status URLs',flush=True)

    candidates={}
    for rec in list(discovered)+list(legacy_candidates()):
        candidates.setdefault(rec['url'],rec)

    fetched=[]
    def work(rec):
        try:
            return rec,fetch_text(rec['url']),None
        except Exception as e:
            return rec,None,(type(e).__name__,str(e)[:140])

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs=[ex.submit(work,rec) for rec in candidates.values()]
        for fut in as_completed(futs):
            rec,text,err=fut.result()
            if err:
                continue
            fetched.append((rec,text))

    # Deduplicate by (year, matchday) after scoring parse quality. This avoids
    # old 2024 and newer 2025 articles that share the same simple slug.
    by_key={}
    rejected=[]
    for rec,text in fetched:
        if 'Player Status Report' not in text or not re.search(r'\b(?:Out|Questionable|None)\b',text,re.I):
            continue
        year=rec.get('published_year')
        if year not in TARGET_YEARS:
            # Legacy fallback already carries a year; discovered URLs require
            # publication metadata to prevent cross-season slug collisions.
            continue
        md=extract_matchday(text) or extract_matchday(rec.get('label',''))
        if md is None:
            continue

        rows,teams_seen,clear_teams=parse_entries(text)
        if len(teams_seen)<2:
            rejected.append({'year':year,'matchday':md,'url':rec['url'],'reason':'too_few_teams','teams_seen':len(teams_seen),'entries':len(rows)})
            continue

        sha=hashlib.sha256(text.encode()).hexdigest()
        dates=extract_dates(text,year)
        candidate={
            'year':year,
            'matchday':md,
            'url':rec['url'],
            'media_pages':rec.get('media_pages',[]),
            'raw_sha256':sha,
            'report_dates':dates,
            'teams_seen':teams_seen,
            'clear_teams':clear_teams,
            'entries':rows,
        }
        # Prefer broader club coverage; then more status rows; then discovered URLs.
        score=(len(teams_seen),len(rows),1 if rec.get('media_pages') else 0)
        key=(year,md)
        if key not in by_key or score>by_key[key][0]:
            by_key[key]=(score,candidate)

    reports=[x[1] for _,x in sorted(by_key.items())]
    flat=[]
    for r in reports:
        status_by_team={}
        for x in r['entries']:
            status_by_team.setdefault(x['team_reported'],[]).append(x)

        # One CLEAR row preserves explicit report coverage for clubs with no
        # unavailable players. Downstream impact code ignores CLEAR for burden.
        for team in r['teams_seen']:
            if team in status_by_team:
                for x in status_by_team[team]:
                    flat.append({
                        'year':r['year'],'matchday':r['matchday'],'url':r['url'],
                        'raw_sha256':r['raw_sha256'],'report_dates':';'.join(r['report_dates']),
                        'team_reported':team,**x,
                    })
            else:
                flat.append({
                    'year':r['year'],'matchday':r['matchday'],'url':r['url'],
                    'raw_sha256':r['raw_sha256'],'report_dates':';'.join(r['report_dates']),
                    'team_reported':team,'player_name':'','status':'CLEAR','reason':'',
                })

        (OUT/f"{r['year']}_matchday_{r['matchday']:02d}.json").write_text(
            json.dumps(r,indent=2,ensure_ascii=False)
        )
        print('FOUND',r['year'],r['matchday'],'teams',len(r['teams_seen']),'entries',len(r['entries']),'url',r['url'],flush=True)

    df=pd.DataFrame(flat)
    if df.empty:
        raise RuntimeError('MLS availability archive rebuild produced zero rows')
    df.to_parquet(OUT/'availability_archive_2024_2025.parquet',index=False)
    df.to_csv(OUT/'availability_archive_2024_2025.csv',index=False)

    meta={
        'built_at':datetime.now(timezone.utc).isoformat(),
        'discovered_media_urls':len(discovered),
        'reports':len(reports),
        'rows':len(flat),
        'status_entries':int((df.status!='CLEAR').sum()),
        'clear_team_rows':int((df.status=='CLEAR').sum()),
        'by_year':{str(y):sum(r['year']==y for r in reports) for y in sorted(TARGET_YEARS)},
        'status_entries_by_year':{
            str(y):int(((df.year==y)&(df.status!='CLEAR')).sum()) for y in sorted(TARGET_YEARS)
        },
        'report_matchdays_by_year':{
            str(y):sorted(int(r['matchday']) for r in reports if r['year']==y) for y in sorted(TARGET_YEARS)
        },
        'parse_rejections':rejected,
        'discovery_note':'Primary source is MLS Media Resources pagination; guessed slugs are fallback only. Duplicate season/matchdays prefer broader recognized-team coverage.',
    }
    (OUT/'meta.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2),flush=True)

if __name__=='__main__':
    main()
