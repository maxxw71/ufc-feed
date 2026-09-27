#!/usr/bin/env python3
"""Probe BoxingScene fighter profiles for strict missing-reach targets.

Discovery only. A lead is recorded only when the requested fighter maps to a
BoxingScene fighter page whose H1 normalizes exactly to the target name and the
page exposes a numeric Reach value. Leads are not merged automatically.
"""
from __future__ import annotations
import concurrent.futures as cf,datetime as dt,json,re,subprocess,tempfile,unicodedata,urllib.parse,urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'public_phase2'/'PROFILE_GAP_AUDIT.json'
OUT=ROOT/'profile_supplements'/'boxingscene_reach_probe.json'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36'

def ascii_text(s):
    return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()

def nk(s):
    return re.sub(r'[^a-z0-9]+','',ascii_text(s).lower())

def slug(s):
    return re.sub(r'[^a-z0-9]+','-',ascii_text(s).lower()).strip('-')

COOKIE_JAR=Path(tempfile.gettempdir())/'appwiza_boxingscene_cookies.txt'

def curl_fetch(url,referer=None,warm=False):
    """Fetch using a normal cookie-preserving browser-style HTTP flow.

    This is diagnostic/research access only: homepage warm-up, ordinary browser
    headers, redirects, and a persistent cookie jar. No challenge solving or
    bypass logic is attempted.
    """
    cmd=[
      'curl','-L','--compressed','--silent','--show-error',
      '--connect-timeout','15','--max-time','35',
      '-A',UA,
      '-H','Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
      '-H','Accept-Language: en-US,en;q=0.9',
      '-H','Cache-Control: no-cache',
      '-H','Pragma: no-cache',
      '-H','Upgrade-Insecure-Requests: 1',
      '-b',str(COOKIE_JAR),'-c',str(COOKIE_JAR),
      '-D','-',
      '-o','-',
      '-w','\\n__APPWIZA_HTTP__:%{http_code}|%{url_effective}|%{content_type}\\n',
    ]
    if referer:
        cmd += ['-e',referer]
    cmd += [url]
    p=subprocess.run(cmd,capture_output=True,timeout=45)
    raw=p.stdout
    marker=b'\\n__APPWIZA_HTTP__:'
    if marker not in raw:
        raise RuntimeError('curl_no_status: '+p.stderr.decode('utf-8','replace')[:160])
    body,meta=raw.rsplit(marker,1)
    meta=meta.decode('utf-8','replace').strip()
    parts=meta.split('|',2)
    code=int(parts[0]) if parts and parts[0].isdigit() else 0
    final=parts[1] if len(parts)>1 else url
    ctype=parts[2] if len(parts)>2 else ''
    # -D - prepends response headers; split off last header block heuristically.
    if body.startswith(b'HTTP/'):
        chunks=re.split(br'\\r?\\n\\r?\\n',body)
        body=chunks[-1] if chunks else body
    if len(body)>1_500_000:
        raise ValueError('response too large')
    return final,body,{'http_code':code,'content_type':ctype,'stderr':p.stderr.decode('utf-8','replace')[:160]}

def warm_session():
    try:
        final,raw,diag=curl_fetch('https://www.boxingscene.com/',warm=True)
        return {'ok':diag['http_code']==200,'final_url':final,'bytes':len(raw),**diag}
    except Exception as e:
        return {'ok':False,'error':type(e).__name__+': '+str(e)[:220]}

def get(url):
    final,raw,diag=curl_fetch(url,referer='https://www.boxingscene.com/')
    if diag['http_code']!=200:
        raise RuntimeError(f"HTTP {diag['http_code']} final={final} type={diag['content_type']}")
    return final,raw,diag

def parse(raw):
    soup=BeautifulSoup(raw,'lxml')
    h=soup.find('h1')
    name=' '.join(h.stripped_strings) if h else ''
    text=' '.join(soup.stripped_strings)
    # Current fighter pages render e.g. Reach 70" / 179cm.
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*[″"]?\s*/\s*(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(2))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*cm\b',text,re.I)
    if m:return name,float(m.group(1))
    m=re.search(r'\bReach\s+(\d+(?:\.\d+)?)\s*(?:in|inch|inches|[″"])\b',text,re.I)
    if m:return name,round(float(m.group(1))*2.54,2)
    return name,None

def worker(x):
    name=x['name'];u='https://www.boxingscene.com/fighters/'+slug(name)
    rec={'id':x.get('id'),'name':name,'strict_bout_appearances':x.get('strict_bout_appearances'),'candidate_url':u}
    try:
        final,raw,diag=get(u);h,reach=parse(raw)
        rec.update({'status':'fetched','final_url':final,'h1':h,'reach_cm':reach,
                    'http_code':diag.get('http_code'),'content_type':diag.get('content_type'),
                    'exact_identity':nk(h)==nk(name)})
        if not rec['exact_identity']:rec['status']='identity_mismatch'
        elif reach is None:rec['status']='no_reach'
        elif not 120<=reach<=270:rec['status']='implausible_reach'
        else:rec['status']='lead'
    except Exception as e:
        rec.update({'status':'fetch_error','error':type(e).__name__+': '+str(e)[:180]})
    return rec

def main():
    if COOKIE_JAR.exists():
        try:COOKIE_JAR.unlink()
        except Exception:pass
    session_diag=warm_session()
    audit=json.loads(AUDIT.read_text())
    targets=[x for x in audit.get('fighters',[]) if 'reach_cm' in set(x.get('missing') or [])]
    targets.sort(key=lambda x:(-int(x.get('strict_bout_appearances') or 0),x.get('name') or ''))
    # Start with a small sequential probe so session cookies are established
    # before any parallel requests. If those succeed, continue at low concurrency.
    first=targets[:8]
    rows=[worker(x) for x in first]
    rest=targets[8:]
    if rest:
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            rows.extend(ex.map(worker,rest))
    leads=[r for r in rows if r['status']=='lead']
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    out={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'targets':len(targets),'session_diagnostic':session_diag,
         'status_counts':counts,'lead_count':len(leads),
         'leads':[{'id':r['id'],'name':r['name'],'reach_cm':r['reach_cm'],'url':r.get('final_url'),
                   'strict_bout_appearances':r.get('strict_bout_appearances')} for r in leads],
         'rows':rows,
         'policy':'Discovery only; ordinary browser-style curl session with homepage warm-up and cookies; exact normalized H1 identity + numeric plausible reach required. No challenge solving and no automatic merge without independent corroboration.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False))
    print(json.dumps({'targets':len(targets),'lead_count':len(leads),'status_counts':counts},indent=2))
if __name__=='__main__':main()
