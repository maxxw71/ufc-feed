#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
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

# Verified first-party 2024 MLS Player Status Report URLs.
# Seeded explicitly so reused simple slugs are never assigned to a season by guess.
KNOWN_2024={
  3:'/news/mls-player-status-report-matchday-3',
  4:'/news/mls-player-status-report-matchday-4',
  5:'/news/mls-player-status-report-matchday-5',
  6:'/news/mls-player-status-report-matchday-6',
  7:'/news/player-status-report-matchday-7-march-30-31',
  8:'/news/mls-player-status-report-matchday-8',
  12:'/news/mls-player-status-report-matchday-12',
  13:'/news/mls-player-status-report-matchday-13',
  14:'/news/mls-player-status-report-matchday-14',
  15:'/news/mls-player-status-report-matchday-15',
  16:'/news/mls-player-status-report-matchday-16',
  17:'/news/mls-player-status-report-matchday-17',
  26:'/news/mls-player-status-report-matchday-26',
  27:'/news/mls-player-status-report-matchday-27',
  28:'/news/mls-player-status-report-matchday-28',
  32:'/news/mls-player-status-reports-matchday-32',
  33:'/news/mls-player-status-report-matchday-33',
  34:'/news/mls-player-status-report-matchday-34',
  35:'/news/mls-player-status-report-matchday-35',
  36:'/news/mls-player-status-report-matchday-36',
  37:'/news/mls-player-status-report-matchday-37',
}

# Verified from MLS Media Resources / first-party MLS article links.
# These exact 2025 URLs avoid the season ambiguity of reused /matchday-N slugs.
KNOWN_2025={
  1:'/news/mls-player-status-report-matchday-1-2025',
  2:'/news/mls-player-status-report-matchday-2',
  3:'/news/mls-player-status-report-matchday-3-x6720',
  4:'/news/mls-player-status-report-matchday-4-saturday-march-15',
  5:'/news/mls-player-status-report-matchday-5-saturday-march-22-sunday-march-23',
  6:'/news/mls-player-status-report-matchday-6-saturday-march-29',
  7:'/news/mls-player-status-report-matchday-7-saturday-april-5',
  8:'/news/mls-player-status-report-matchday-8-saturday-april-12',
  9:'/news/mls-player-status-report-matchday-9-saturday-april-19',
  10:'/news/mls-player-status-report-matchday-10-saturday-april-26-and-sunday-april-27',
  11:'/news/mls-player-status-report-matchday-11-saturday-may-3',
  12:'/news/mls-player-status-report-matchday-12-2025',
  13:'/news/mls-player-status-report-matchday-13-wednesday-may-14',
  14:'/news/mls-player-status-report-matchday-14-saturday-may-17',
  15:'/news/mls-player-status-report-matchday-15-saturday-may-24',
  16:'/news/mls-player-status-report-matchday-16-wednesday-may-28',
  17:'/news/mls-player-status-report-matchday-17-saturday-may-31',
  18:'/news/mls-player-status-report-matchday-18-saturday-june-7-and-sunday-june-8',
  19:'/news/mls-player-status-report-matchday-19-saturday-june-14',
  20:'/news/mls-player-status-report-matchday-20-wednesday-june-25',
  21:'/news/mls-player-status-report-matchday-21-saturday-june-28',
  22:'/news/mls-player-status-report-matchday-22-july-3-6',
  23:'/news/mls-player-status-report-matchday-23-july-9',
  24:'/news/mls-player-status-report-matchday-24-saturday-july-12',
  25:'/news/mls-player-status-report-matchday-25-july-16',
  26:'/news/mls-player-status-report-matchday-26-saturday-july-19',
  27:'/news/mls-player-status-report-matchday-27-friday-july-25-and-saturday-july-26',
  28:'/news/mls-player-status-report-matchday-28-saturday-august-9-and-sunday-august-10',
  29:'/news/mls-player-status-report-matchday-29-2025',
  30:'/news/mls-player-status-report-matchday-30-saturday-august-23-and-sunday-august-24',
  31:'/news/mls-player-status-report-matchday-31-saturday-august-30',
  32:'/news/mls-player-status-report-matchday-32-saturday-september-6-sunday-september-7',
  33:'/news/mls-player-status-report-matchday-33-saturday-september-13',
  34:'/news/mls-player-status-report-matchday-34-tuesday-september-16',
  35:'/news/mls-player-status-report-matchday-35-saturday-september-20-sunday-september-21',
  36:'/news/mls-player-status-report-matchday-36-wednesday-september-24',
  37:'/news/mls-player-status-report-matchday-37-saturday-september-27-sunday-september-28',
  38:'/news/mls-player-status-report-matchday-38-saturday-october-4-sunday-october-5',
  39:'/news/mls-player-status-report-matchday-39-saturday-october-18',
}

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

def _anchor_links(page_html):
    links=[]
    for href,body in re.findall(r"""<a\\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""",page_html,re.I|re.S):
        label=unescape(re.sub(r'<[^>]+>',' ',body))
        label=re.sub(r'\\s+',' ',label).strip()
        if 'player status report' in label.lower():
            links.append((label,href))
    return links

def discover_status_urls():
    discovered={}

    def get_media(page):
        url=MEDIA+str(page)
        try:
            return page,fetch_html(url),None
        except Exception as e:
            return page,None,(type(e).__name__,str(e)[:100])

    # Use the first-party HTML archive directly. Jina is retained for article
    # body extraction, but direct HTML is materially more reliable for href discovery.
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(get_media,page) for page in range(1,31)]
        for fut in as_completed(futs):
            page,page_html,err=fut.result()
            if err:
                print('MEDIA_MISS',page,*err,flush=True)
                continue
            for label,href in _anchor_links(page_html):
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

def verified_2024_candidates():
    for md,path in sorted(KNOWN_2024.items()):
        yield {
            'published_year':2024,
            'url':urljoin(MLS,path),
            'label':f'Matchday {md}',
            'media_pages':['verified_first_party_seed'],
            'expected_matchday':md,
        }

def verified_2025_candidates():
    for md,path in sorted(KNOWN_2025.items()):
        yield {
            'published_year':2025,
            'url':urljoin(MLS,path),
            'label':f'Matchday {md}',
            'media_pages':['verified_first_party_seed'],
            'expected_matchday':md,
        }

def legacy_candidates():
    # Guessed slugs are fallback discovery only. Never assign their season from
    # the guess: MLS has reused simple Matchday URLs across years.
    seen=set()
    for n in range(1,41):
        for url in [
            MLS+f'/news/mls-player-status-report-matchday-{n}',
            MLS+f'/news/mls-player-status-report-matchday-{n}-2024',
            MLS+f'/news/mls-player-status-report-matchday-{n}-2025',
        ]:
            if url in seen:
                continue
            seen.add(url)
            yield {'published_year':None,'url':url,'label':f'Matchday {n}','media_pages':[]}

def main():
    discovered=discover_status_urls()
    print('DISCOVERED',len(discovered),'media-resource status URLs',flush=True)

    candidates={}
    # Keep season as part of candidate identity. Exact verified seeds take
    # precedence inside each season; fallback URLs cannot overwrite them.
    for rec in list(verified_2024_candidates())+list(verified_2025_candidates())+list(discovered)+list(legacy_candidates()):
        key=(rec.get('published_year'),rec['url'])
        candidates.setdefault(key,rec)

    # Verify the publication year of every fallback URL before fetching/parsing
    # its report body. This prevents season contamination from reused slugs.
    to_verify=[rec for rec in candidates.values() if rec.get('published_year') not in TARGET_YEARS]
    def verify(rec):
        return rec,extract_published_year(rec['url'])
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(verify,rec) for rec in to_verify]
        for fut in as_completed(futs):
            rec,year=fut.result()
            rec['published_year']=year

    verified=[rec for rec in candidates.values() if rec.get('published_year') in TARGET_YEARS]
    print('VERIFIED_CANDIDATES',len(verified),flush=True)

    fetched=[]
    fetch_failures=[]
    def work(rec):
        last=None
        # First-party pages through Jina can intermittently rate-limit. Retry
        # deterministically and keep concurrency intentionally low.
        for attempt in range(1,5):
            try:
                return rec,fetch_text(rec['url']),None
            except Exception as e:
                last=(type(e).__name__,str(e)[:180])
                time.sleep(0.8*attempt)
        return rec,None,last

    # Verified seeds first; fallbacks are secondary. Low parallelism materially
    # reduces relay failures versus the earlier broad fan-out.
    verified=sorted(
        verified,
        key=lambda r:(0 if r.get('media_pages')==['verified_first_party_seed'] else 1,
                      int(r.get('published_year') or 9999),
                      int(r.get('expected_matchday') or 999),
                      r['url'])
    )
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs=[ex.submit(work,rec) for rec in verified]
        for fut in as_completed(futs):
            rec,text,err=fut.result()
            if err:
                fetch_failures.append({
                    'year':rec.get('published_year'),
                    'expected_matchday':rec.get('expected_matchday'),
                    'url':rec['url'],
                    'error':err,
                })
                print('FETCH_MISS',rec.get('published_year'),rec.get('expected_matchday'),*err,rec['url'],flush=True)
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
        expected=rec.get('expected_matchday')
        if expected is not None and int(md)!=int(expected):
            rejected.append({'year':year,'matchday':md,'url':rec['url'],'reason':'matchday_mismatch','expected':expected})
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
    report_counts={y:sum(r['year']==y for r in reports) for y in sorted(TARGET_YEARS)}
    # Fail closed before replacing the canonical parquet/csv. The minimums
    # guarantee enough matchdays for the downstream two-season research gate.
    minimum_reports={2024:12,2025:20}
    short={y:{'found':report_counts.get(y,0),'required':minimum_reports[y]}
           for y in minimum_reports if report_counts.get(y,0)<minimum_reports[y]}
    if short:
        raise RuntimeError(
            'Refusing partial MLS availability archive overwrite: '
            +json.dumps({'short':short,'fetch_failures':fetch_failures[:30]},default=str)
        )

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
        'verified_2024_seed_urls':len(KNOWN_2024),
        'verified_2025_seed_urls':len(KNOWN_2025),
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
        'fetch_failures':fetch_failures,
        'discovery_note':'2024 and 2025 use exact first-party MLS URLs verified from Media Resources/search where seeded. Dynamic Media Resources discovery and publication-year-verified guessed slugs are fallbacks. Duplicate season/matchdays prefer broader recognized-team coverage.',
    }
    (OUT/'meta.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2),flush=True)

if __name__=='__main__':
    main()
