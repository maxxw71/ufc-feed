#!/usr/bin/env python3
"""Discover and parse dated official WBC monthly ratings PDFs.

The collector uses a finite month/year URL pattern set only; it does not
enumerate numeric endpoints. Rankings become research-eligible conservatively
on the first day of the FOLLOWING month, preventing same-month publication
timing leakage when exact publication timestamp is unknown.
"""
from __future__ import annotations
import datetime as dt, hashlib, io, json, re, subprocess, time, urllib.error, urllib.request
from pathlib import Path
from pypdf import PdfReader

OUT=Path('boxing/rankings');OUT.mkdir(parents=True,exist_ok=True)
UA='Mozilla/5.0 AppwizaBoxingRankings/1.1'
MONTHS=['JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER']
DIVS=[
 ('heavyweight',['HEAVYWEIGHT']),
 ('bridgerweight',['BRIDGERWEIGHT']),
 ('cruiserweight',['CRUISERWEIGHT']),
 ('light heavyweight',['LIGHT HEAVYWEIGHT','LT. HEAVYWEIGHT','LT HEAVYWEIGHT']),
 ('super middleweight',['SUPER MIDDLEWEIGHT','SUPERMIDDLEWEIGHT','SUPER-MIDDLEWEIGHT']),
 ('middleweight',['MIDDLEWEIGHT']),
 ('super welterweight',['SUPER WELTERWEIGHT','SUPERWELTERWEIGHT','SUPER-WELTERWEIGHT']),
 ('welterweight',['WELTERWEIGHT']),
 ('super lightweight',['SUPER LIGHTWEIGHT','SUPERLIGHTWEIGHT','SUPER-LIGHTWEIGHT']),
 ('lightweight',['LIGHTWEIGHT']),
 ('super featherweight',['SUPER FEATHERWEIGHT','SUPERFEATHERWEIGHT','SUPER-FEATHERWEIGHT']),
 ('featherweight',['FEATHERWEIGHT']),
 ('super bantamweight',['SUPER BANTAMWEIGHT','SUPERBANTAMWEIGHT','SUPER-BANTAMWEIGHT']),
 ('bantamweight',['BANTAMWEIGHT']),
 ('super flyweight',['SUPER FLYWEIGHT','SUPERFLYWEIGHT','SUPER-FLYWEIGHT']),
 ('flyweight',['FLYWEIGHT']),
 ('light flyweight',['LIGHT FLYWEIGHT','LIGHTFLYWEIGHT','LIGHT-FLYWEIGHT','LT. FLYWEIGHT']),
 ('minimumweight',['MINIMUMWEIGHT','STRAWWEIGHT','MINI FLYWEIGHT']),
]
COUNTRY_TAIL=re.compile(r'\s*\([^)]{1,45}\)\s*(?:[A-Z][A-Z0-9*/. -]{0,40})?\s*$')
WBC_ARCHIVE_CAPTURES={
  (2023,'APRIL'):('20230525040938','https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_APRIL_2023_.pdf'),
  (2024,'JUNE'):('20240620103321','https://wbcboxing.com/mailing/2024/ratings_pdf/_WBC_RATINGS_JUNE_2024.pdf'),
}

def next_month(y,m):
    return dt.date(y+1,1,1) if m==12 else dt.date(y,m+1,1)

def urls(y,month):
    # WBC file layout changed over time; use finite observed filename variants.
    names=[
      f'WBC_RATINGS_{month}_{y}.pdf',
      f'_WBC_RATINGS_{month}_{y}.pdf',
      f'WBC_RATINGS_{month}_{y}_.pdf',
      f'_WBC_RATINGS_{month}_{y}_.pdf',
      f'WBC_RATINGS_{month}_%20{y}.pdf',
      f'_WBC_RATINGS_{month}_%20{y}.pdf',
      f'WBC_RATINGS_{month}%20_{y}.pdf',
      f'_WBC_RATINGS_{month}%20_{y}.pdf',
      f'WBC_RATINGS_{month}_%20{y}_.pdf',
      f'_WBC_RATINGS_{month}_%20{y}_.pdf',
    ]
    if y==2023:
      # Verified official WBC PDF filenames. For this older year, use only
      # observed filenames so missing months do not burn minutes probing many
      # Cloudflare-protected variants.
      observed={
        'APRIL':['WBC_RATINGS_APRIL_2023_.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_2023.pdf'],
        'JULY':['WBC_RATINGS_JULY_2023.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2023.pdf'],
        'NOVEMBER':['WBC_RATINGS_NOVEMBER_2023__.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2024:
      observed={
        'JANUARY':['WBC_RATINGS_JANUARY__2024.pdf'],
        'FEBRUARY':['WBC_RATINGS_FEBRUARY_2024.pdf'],
        'APRIL':['WBC_RATINGS_APRIL_2024_.pdf'],
        'MAY':['WBC_RATINGS_MAY_2024.pdf'],
        'JUNE':['_WBC_RATINGS_JUNE_2024.pdf'],
        'JULY':['WBC_RATINGS_JULY_2024.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2024.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2024_.pdf'],
        'OCTOBER':['WBC_RATINGS_OCTOBER_2024.pdf'],
        'DECEMBER':['WBC_RATINGS_CONVENTION_HAMBURG_GERMANY_2024_.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2025:
      observed={
        'MARCH':['WBC_RATINGS_MARCH_2025.pdf'],
        'APRIL':['WBC_RATINGS_APRIL_2025.pdf'],
        'MAY':['WBC_RATINGS_MAY_%202025.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_%202025.pdf'],
        'JULY':['WBC_RATINGS_JULY_2025.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2025.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2025.pdf'],
        'OCTOBER':['WBC_RATINGS_OCTOBER_2025.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2026:
      observed={
        'MAY':['WBC_RATINGS_MAY_2026.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_2026_.pdf'],
        'JULY':['WBC_RATINGS_JULY_2026.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2026.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2026.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    bases=[
      f'https://wbcboxing.com/mailing/{y}/ratings_pdf/',
      f'https://wbcboxing.com/mailing/{y}/',
    ]
    for b in bases:
      for n in names:
        yield b+n

def _curl_pdf(url):
    try:
      p=subprocess.run([
        'curl','-4','--http1.1','-L','--compressed','--fail','--silent','--show-error',
        '--connect-timeout','15','--max-time','60','--retry','2','--retry-delay','2',
        '-A',UA,'-e','https://wbcboxing.com/',url
      ],capture_output=True,timeout=70)
      data=p.stdout if p.returncode==0 else b''
      if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
        return url,data
    except Exception:
      pass
    return None,None

def _archive_pdf(y,month):
    item=WBC_ARCHIVE_CAPTURES.get((y,month))
    if not item:return None,None
    ts,original=item
    archived=f'https://web.archive.org/web/{ts}id_/{original}'
    try:
      req=urllib.request.Request(archived,headers={'User-Agent':UA,'Accept':'application/pdf,*/*;q=0.8'})
      with urllib.request.urlopen(req,timeout=45) as r:
        data=r.read(12_000_001)
        final=r.geturl()
      if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
        return final,data
    except Exception:
      pass
    try:
      p=subprocess.run([
        'curl','-4','--http1.1','-L','--compressed','--fail','--silent','--show-error',
        '--connect-timeout','15','--max-time','60','--retry','1','--retry-delay','2',
        '-A',UA,archived
      ],capture_output=True,timeout=70)
      data=p.stdout if p.returncode==0 else b''
      if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
        return archived,data
    except Exception:
      pass
    return None,None

def archive_capture_date(url):
    m=re.search(r'/web/(\d{8})\d*',str(url or ''))
    if not m:return None
    try:return dt.datetime.strptime(m.group(1),'%Y%m%d').date()
    except ValueError:return None

def fetch_pdf(y,month):
    for url in urls(y,month):
      for attempt in range(3):
        try:
          req=urllib.request.Request(url,headers={
            'User-Agent':UA,
            'Accept':'application/pdf,*/*;q=0.8',
            'Referer':'https://wbcboxing.com/',
            'Accept-Language':'en-US,en;q=0.8',
          })
          with urllib.request.urlopen(req,timeout=30) as r:
            data=r.read(12_000_001)
            final=r.geturl()
          if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
            return final,data
          break
        except urllib.error.HTTPError as e:
          if e.code not in {403,408,425,429,500,502,503,504,520,521,522,523,524}:
            break
        except urllib.error.URLError:
          pass
        if attempt<2:
          time.sleep(1.0*(2**attempt))
      # WBC/Cloudflare has intermittently rejected Python's urllib while the
      # exact official PDF remains publicly reachable. Use curl only for the
      # same finite official URL candidate; never enumerate IDs or directories.
      final,data=_curl_pdf(url)
      if data:return final,data
    archived,data=_archive_pdf(y,month)
    if data:return archived,data
    return None,None

def clean_line(x):
    return re.sub(r'\s+',' ',str(x or '')).strip()

def div_header(line):
    u=clean_line(line).upper()
    for canonical,aliases in DIVS:
      if any(re.match(r'^'+re.escape(a)+r'(?:\.|\s|-|$)',u) for a in aliases):
        return canonical
    return None

def boxer_name(text):
    s=clean_line(text)
    s=COUNTRY_TAIL.sub('',s)
    s=re.sub(r'\s+(?:SILVER|INTL|INTERNATIONAL|NABF|OPBF|EBU|FECARBOX|FECONSUR|USWBC|YOUTH|FRANCOPHONE|AUSTRALASIA|CONT\..*)$','',s,flags=re.I)
    s=clean_line(s)
    return s if len(s)>=3 else None

def ranked_boxer(text):
    """Extract boxer + official WBC country label from a contender cell."""
    s=clean_line(text)
    m=re.match(r'^(.+?)\s+\(([^()]{2,45})\)(?:\s+.*)?$',s)
    if not m:
        return None
    name=clean_line(m.group(1))
    country=clean_line(m.group(2))
    if not name or ':' in name or len(name)>80:
        return None
    if re.search(r'\b(?:champion|contender|available|required|program|affiliated|federation|rating|www)\b',name,re.I):
        return None
    if not re.search(r'[A-Za-zÀ-ÿ]{2}',name):
        return None
    if not country or len(country)>45:
        return None
    return name,country

def parse(text,y,m,url):
    """Parse WBC layout text using only the left contender column."""
    raw_lines=[str(x).replace('\xa0',' ').rstrip() for x in text.splitlines() if clean_line(x)]
    rows=[];champions=[];division=None;in_contenders=False
    for raw in raw_lines:
      line=clean_line(raw)
      dh=div_header(line)
      if dh:
        division=dh;in_contenders=False;continue
      if not division:continue
      cm=re.match(r'^(?:(INTERIM)\s+)?CHAMPION\s*:\s*(.+)$',line,re.I)
      if cm:
        name=boxer_name(cm.group(2))
        if name:
          champions.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'status':'interim_champion' if cm.group(1) else 'champion','name':name,'source_url':url})
        continue
      if line.upper().startswith('CONTENDERS'):
        in_contenders=True;continue
      if not in_contenders:continue
      # WBC layout pages contain a left contender table and explanatory text
      # on the right. Split only on a large visual-column gap and never parse
      # the right side as a fighter.
      left=re.split(r'\s{3,}',raw.strip(),maxsplit=1)[0].strip()
      rm=re.match(r'^(\d{1,2})[.)-]?\s+(.+)$',left)
      if not rm:continue
      rank=int(rm.group(1))
      if not 1<=rank<=40:continue
      parsed=ranked_boxer(rm.group(2))
      if not parsed:continue
      name,country=parsed
      rows.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'rank':rank,'name':name,'country':country,'source_url':url})
    return rows,champions

def validate_rows(rows):
    """Reject partial/misaligned documents even when raw row count looks large."""
    by={}
    for r in rows:
        key=(r['division'],r['rank'])
        if key in by and by[key]!=r['name']:
            return False,'conflicting duplicate rank'
        by[key]=r['name']
    divisions={}
    for (division,rank),name in by.items():
        divisions.setdefault(division,set()).add(rank)
    if len(divisions)<14:
        return False,f'too few divisions {len(divisions)}'
    thin={d:len(rs) for d,rs in divisions.items() if len(rs)<20 or 1 not in rs}
    if thin:
        return False,'thin divisions '+json.dumps(thin,sort_keys=True)
    if len(by)<500:
        return False,f'too few unique rows {len(by)}'
    return True,None

def extract_document(data,y,m,url):
    reader=PdfReader(io.BytesIO(data))
    candidates=[]
    try:
      layout='\n'.join((p.extract_text(extraction_mode='layout') or '') for p in reader.pages)
      candidates.append(('layout',*parse(layout,y,m,url)))
    except (TypeError,ValueError):
      pass
    plain='\n'.join((p.extract_text() or '') for p in reader.pages)
    candidates.append(('plain',*parse(plain,y,m,url)))
    mode,rows,champs=max(candidates,key=lambda x:(len(x[1]),len(x[2])))
    return reader,mode,rows,champs

def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2023,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:
          continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader,mode,rows,cs=extract_document(data,y,m,url)
          # 18 divisions x up to 40 contenders means a healthy PDF is normally
          # several hundred rows. Keep a conservative floor so partial parses
          # never enter research features.
          valid,integrity_error=validate_rows(rows)
          status='parsed' if valid else 'parse_review'
          effective_date=next_month(y,m)
          capture_date=archive_capture_date(url)
          if capture_date and capture_date>effective_date:
            effective_date=capture_date
          effective=effective_date.isoformat()
          docs.append({
            'year':y,'month':m,'month_name':month,'status':status,
            'source_url':url,'bytes':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),
            'extract_mode':mode,'ranking_rows':len(rows),'champions':len(cs),
            'integrity_error':integrity_error,
            'archive_capture_date':archive_capture_date(url).isoformat() if archive_capture_date(url) else None,
            'safe_effective_date':effective
          })
          for r in rows:
            r['safe_effective_date']=effective
          for r in cs:
            r['safe_effective_date']=effective
          if status=='parsed':
            allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({
            'year':y,'month':m,'month_name':month,'status':'parse_error',
            'source_url':url,'error':type(e).__name__+': '+str(e)[:200]
          })
    uniq={}
    for r in allrows:
      uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    champs_uniq={}
    for r in champs:
      champs_uniq[(r['rating_year'],r['rating_month'],r['division'],r['status'],r['name'])]=r
    champs=list(champs_uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(
      json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False)
    )
    (OUT/'wbc_monthly_champions.json').write_text(
      json.dumps(sorted(champs,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['status'],r['name'])),indent=2,ensure_ascii=False)
    )
    report={
      'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'documents':docs,
      'parsed_documents':sum(x['status']=='parsed' for x in docs),
      'ranking_rows':len(allrows),
      'champion_rows':len(champs),
      'years':sorted(set(r['rating_year'] for r in allrows)),
      'policy':'Official WBC monthly PDFs only. Direct official PDFs preferred; verified Wayback captures of the same official PDF are allowed. Safe effective date is the later of first day of following month or verified archive capture date; no same-month backfill.'
    }
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({
      'parsed_documents':report['parsed_documents'],
      'ranking_rows':len(allrows),
      'champion_rows':len(champs),
      'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}
    },indent=2))

if __name__=='__main__':
    main()
)
WBC_ARCHIVE_CAPTURES={
  (2023,'APRIL'):('20230525040938','https://wbcboxing.com/mailing/2023/ratings_pdf/WBC_RATINGS_APRIL_2023_.pdf'),
  (2024,'JUNE'):('20240620103321','https://wbcboxing.com/mailing/2024/ratings_pdf/_WBC_RATINGS_JUNE_2024.pdf'),
}

def next_month(y,m):
    return dt.date(y+1,1,1) if m==12 else dt.date(y,m+1,1)

def urls(y,month):
    # WBC file layout changed over time; use finite observed filename variants.
    names=[
      f'WBC_RATINGS_{month}_{y}.pdf',
      f'_WBC_RATINGS_{month}_{y}.pdf',
      f'WBC_RATINGS_{month}_{y}_.pdf',
      f'_WBC_RATINGS_{month}_{y}_.pdf',
      f'WBC_RATINGS_{month}_%20{y}.pdf',
      f'_WBC_RATINGS_{month}_%20{y}.pdf',
      f'WBC_RATINGS_{month}%20_{y}.pdf',
      f'_WBC_RATINGS_{month}%20_{y}.pdf',
      f'WBC_RATINGS_{month}_%20{y}_.pdf',
      f'_WBC_RATINGS_{month}_%20{y}_.pdf',
    ]
    if y==2023:
      # Verified official WBC PDF filenames. For this older year, use only
      # observed filenames so missing months do not burn minutes probing many
      # Cloudflare-protected variants.
      observed={
        'APRIL':['WBC_RATINGS_APRIL_2023_.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_2023.pdf'],
        'JULY':['WBC_RATINGS_JULY_2023.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2023.pdf'],
        'NOVEMBER':['WBC_RATINGS_NOVEMBER_2023__.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2024:
      observed={
        'JANUARY':['WBC_RATINGS_JANUARY__2024.pdf'],
        'FEBRUARY':['WBC_RATINGS_FEBRUARY_2024.pdf'],
        'APRIL':['WBC_RATINGS_APRIL_2024_.pdf'],
        'MAY':['WBC_RATINGS_MAY_2024.pdf'],
        'JUNE':['_WBC_RATINGS_JUNE_2024.pdf'],
        'JULY':['WBC_RATINGS_JULY_2024.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2024.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2024_.pdf'],
        'OCTOBER':['WBC_RATINGS_OCTOBER_2024.pdf'],
        'DECEMBER':['WBC_RATINGS_CONVENTION_HAMBURG_GERMANY_2024_.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2025:
      observed={
        'MARCH':['WBC_RATINGS_MARCH_2025.pdf'],
        'APRIL':['WBC_RATINGS_APRIL_2025.pdf'],
        'MAY':['WBC_RATINGS_MAY_%202025.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_%202025.pdf'],
        'JULY':['WBC_RATINGS_JULY_2025.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2025.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2025.pdf'],
        'OCTOBER':['WBC_RATINGS_OCTOBER_2025.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    elif y==2026:
      observed={
        'MAY':['WBC_RATINGS_MAY_2026.pdf'],
        'JUNE':['WBC_RATINGS_JUNE_2026_.pdf'],
        'JULY':['WBC_RATINGS_JULY_2026.pdf'],
        'AUGUST':['WBC_RATINGS_AUGUST_2026.pdf'],
        'SEPTEMBER':['WBC_RATINGS_SEPTEMBER_2026.pdf'],
      }
      names=observed.get(month,[])
      if not names:return
    bases=[
      f'https://wbcboxing.com/mailing/{y}/ratings_pdf/',
      f'https://wbcboxing.com/mailing/{y}/',
    ]
    for b in bases:
      for n in names:
        yield b+n

def _curl_pdf(url):
    try:
      p=subprocess.run([
        'curl','-4','--http1.1','-L','--compressed','--fail','--silent','--show-error',
        '--connect-timeout','15','--max-time','60','--retry','2','--retry-delay','2',
        '-A',UA,'-e','https://wbcboxing.com/',url
      ],capture_output=True,timeout=70)
      data=p.stdout if p.returncode==0 else b''
      if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
        return url,data
    except Exception:
      pass
    return None,None

def fetch_pdf(y,month):
    for url in urls(y,month):
      for attempt in range(3):
        try:
          req=urllib.request.Request(url,headers={
            'User-Agent':UA,
            'Accept':'application/pdf,*/*;q=0.8',
            'Referer':'https://wbcboxing.com/',
            'Accept-Language':'en-US,en;q=0.8',
          })
          with urllib.request.urlopen(req,timeout=30) as r:
            data=r.read(12_000_001)
            final=r.geturl()
          if data.lstrip().startswith(b'%PDF') and 20_000<len(data)<=12_000_000:
            return final,data
          break
        except urllib.error.HTTPError as e:
          if e.code not in {403,408,425,429,500,502,503,504,520,521,522,523,524}:
            break
        except urllib.error.URLError:
          pass
        if attempt<2:
          time.sleep(1.0*(2**attempt))
      # WBC/Cloudflare has intermittently rejected Python's urllib while the
      # exact official PDF remains publicly reachable. Use curl only for the
      # same finite official URL candidate; never enumerate IDs or directories.
      final,data=_curl_pdf(url)
      if data:return final,data
    return None,None

def clean_line(x):
    return re.sub(r'\s+',' ',str(x or '')).strip()

def div_header(line):
    u=clean_line(line).upper()
    for canonical,aliases in DIVS:
      if any(re.match(r'^'+re.escape(a)+r'(?:\.|\s|-|$)',u) for a in aliases):
        return canonical
    return None

def boxer_name(text):
    s=clean_line(text)
    s=COUNTRY_TAIL.sub('',s)
    s=re.sub(r'\s+(?:SILVER|INTL|INTERNATIONAL|NABF|OPBF|EBU|FECARBOX|FECONSUR|USWBC|YOUTH|FRANCOPHONE|AUSTRALASIA|CONT\..*)$','',s,flags=re.I)
    s=clean_line(s)
    return s if len(s)>=3 else None

def ranked_boxer(text):
    """Extract boxer + official WBC country label from a contender cell."""
    s=clean_line(text)
    m=re.match(r'^(.+?)\s+\(([^()]{2,45})\)(?:\s+.*)?$',s)
    if not m:
        return None
    name=clean_line(m.group(1))
    country=clean_line(m.group(2))
    if not name or ':' in name or len(name)>80:
        return None
    if re.search(r'\b(?:champion|contender|available|required|program|affiliated|federation|rating|www)\b',name,re.I):
        return None
    if not re.search(r'[A-Za-zÀ-ÿ]{2}',name):
        return None
    if not country or len(country)>45:
        return None
    return name,country

def parse(text,y,m,url):
    """Parse WBC layout text using only the left contender column."""
    raw_lines=[str(x).replace('\xa0',' ').rstrip() for x in text.splitlines() if clean_line(x)]
    rows=[];champions=[];division=None;in_contenders=False
    for raw in raw_lines:
      line=clean_line(raw)
      dh=div_header(line)
      if dh:
        division=dh;in_contenders=False;continue
      if not division:continue
      cm=re.match(r'^(?:(INTERIM)\s+)?CHAMPION\s*:\s*(.+)$',line,re.I)
      if cm:
        name=boxer_name(cm.group(2))
        if name:
          champions.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'status':'interim_champion' if cm.group(1) else 'champion','name':name,'source_url':url})
        continue
      if line.upper().startswith('CONTENDERS'):
        in_contenders=True;continue
      if not in_contenders:continue
      # WBC layout pages contain a left contender table and explanatory text
      # on the right. Split only on a large visual-column gap and never parse
      # the right side as a fighter.
      left=re.split(r'\s{3,}',raw.strip(),maxsplit=1)[0].strip()
      rm=re.match(r'^(\d{1,2})[.)-]?\s+(.+)$',left)
      if not rm:continue
      rank=int(rm.group(1))
      if not 1<=rank<=40:continue
      parsed=ranked_boxer(rm.group(2))
      if not parsed:continue
      name,country=parsed
      rows.append({'organization':'WBC','rating_year':y,'rating_month':m,'division':division,'rank':rank,'name':name,'country':country,'source_url':url})
    return rows,champions

def validate_rows(rows):
    """Reject partial/misaligned documents even when raw row count looks large."""
    by={}
    for r in rows:
        key=(r['division'],r['rank'])
        if key in by and by[key]!=r['name']:
            return False,'conflicting duplicate rank'
        by[key]=r['name']
    divisions={}
    for (division,rank),name in by.items():
        divisions.setdefault(division,set()).add(rank)
    if len(divisions)<14:
        return False,f'too few divisions {len(divisions)}'
    thin={d:len(rs) for d,rs in divisions.items() if len(rs)<20 or 1 not in rs}
    if thin:
        return False,'thin divisions '+json.dumps(thin,sort_keys=True)
    if len(by)<500:
        return False,f'too few unique rows {len(by)}'
    return True,None

def extract_document(data,y,m,url):
    reader=PdfReader(io.BytesIO(data))
    candidates=[]
    try:
      layout='\n'.join((p.extract_text(extraction_mode='layout') or '') for p in reader.pages)
      candidates.append(('layout',*parse(layout,y,m,url)))
    except (TypeError,ValueError):
      pass
    plain='\n'.join((p.extract_text() or '') for p in reader.pages)
    candidates.append(('plain',*parse(plain,y,m,url)))
    mode,rows,champs=max(candidates,key=lambda x:(len(x[1]),len(x[2])))
    return reader,mode,rows,champs

def main():
    docs=[];allrows=[];champs=[]
    today=dt.date.today()
    for y in range(2023,today.year+1):
      for m,month in enumerate(MONTHS,1):
        if dt.date(y,m,1)>today:
          continue
        url,data=fetch_pdf(y,month)
        if not data:
          docs.append({'year':y,'month':m,'month_name':month,'status':'not_found'})
          continue
        try:
          reader,mode,rows,cs=extract_document(data,y,m,url)
          # 18 divisions x up to 40 contenders means a healthy PDF is normally
          # several hundred rows. Keep a conservative floor so partial parses
          # never enter research features.
          valid,integrity_error=validate_rows(rows)
          status='parsed' if valid else 'parse_review'
          effective=next_month(y,m).isoformat()
          docs.append({
            'year':y,'month':m,'month_name':month,'status':status,
            'source_url':url,'bytes':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'pages':len(reader.pages),
            'extract_mode':mode,'ranking_rows':len(rows),'champions':len(cs),
            'integrity_error':integrity_error,
            'safe_effective_date':effective
          })
          for r in rows:
            r['safe_effective_date']=effective
          for r in cs:
            r['safe_effective_date']=effective
          if status=='parsed':
            allrows.extend(rows);champs.extend(cs)
        except Exception as e:
          docs.append({
            'year':y,'month':m,'month_name':month,'status':'parse_error',
            'source_url':url,'error':type(e).__name__+': '+str(e)[:200]
          })
    uniq={}
    for r in allrows:
      uniq[(r['rating_year'],r['rating_month'],r['division'],r['rank'])]=r
    allrows=list(uniq.values())
    champs_uniq={}
    for r in champs:
      champs_uniq[(r['rating_year'],r['rating_month'],r['division'],r['status'],r['name'])]=r
    champs=list(champs_uniq.values())
    (OUT/'wbc_monthly_rankings.json').write_text(
      json.dumps(sorted(allrows,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['rank'])),indent=2,ensure_ascii=False)
    )
    (OUT/'wbc_monthly_champions.json').write_text(
      json.dumps(sorted(champs,key=lambda r:(r['rating_year'],r['rating_month'],r['division'],r['status'],r['name'])),indent=2,ensure_ascii=False)
    )
    report={
      'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'documents':docs,
      'parsed_documents':sum(x['status']=='parsed' for x in docs),
      'ranking_rows':len(allrows),
      'champion_rows':len(champs),
      'years':sorted(set(r['rating_year'] for r in allrows)),
      'policy':'Official WBC monthly PDFs only. Safe effective date is first day of following month; no same-month backfill.'
    }
    (OUT/'wbc_monthly_rankings_meta.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({
      'parsed_documents':report['parsed_documents'],
      'ranking_rows':len(allrows),
      'champion_rows':len(champs),
      'statuses':{s:sum(x['status']==s for x in docs) for s in sorted(set(x['status'] for x in docs))}
    },indent=2))

if __name__=='__main__':
    main()
