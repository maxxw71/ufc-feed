#!/usr/bin/env python3
"""Export strict fight-level summaries from captured archived CompuBox-owned pages.

This is a lower-resolution punch tier than full round tables. Acceptance gates:
- archived CompuBox-owned page already captured in source_rows;
- fight identity/date resolves through the strict archived CompuBox resolver;
- only explicit fighter-attributed numeric rates/percentages are retained;
- sentences explicitly describing an earlier/other fight are ignored;
- duplicate archive captures are merged only when numeric values agree.
"""
from __future__ import annotations
import datetime as dt,json,re,sqlite3,unicodedata
from collections import defaultdict
from pathlib import Path

from import_archived_compubox_rounds import resolve_fight,norm,surname

DB=Path('/home/anestishkurti92/boxing-research/boxing.sqlite3')
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'punch_supplements'/'archived_compubox_summaries.jsonl'
REPORT=ROOT/'punch_supplements'/'archived_compubox_summary_report.json'

HISTORICAL_CUE=re.compile(
    r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:year|years|month|months)\s+ago\b'
    r'|\b(?:previous|prior|last)\s+(?:fight|bout)\b'
    r'|\bin\s+(?:his|her)\s+(?:previous|prior|last)\s+(?:fight|bout)\b',
    re.I
)

def clean(s):
    x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()
    return re.sub(r'\s+',' ',x.replace('’',"'")).strip()

def paragraphs(payload):
    return [clean(x) for x in (payload.get('relevant_paragraphs') or []) if clean(x)]

def archive_date(url):
    m=re.search(r'/web/(\d{8})\d*/',str(url or ''))
    if not m:return None
    raw=m.group(1)
    try:return dt.datetime.strptime(raw,'%Y%m%d').date().isoformat()
    except ValueError:return None

def sentence_parts(text):
    # CompuBox prose is conventional enough that punctuation followed by a
    # capital letter is a safe sentence boundary; decimal points are preserved.
    return [x.strip() for x in re.split(r'(?<=[.!?])\s+(?=[A-Z])',clean(text)) if x.strip()]

def aliases(name):
    vals=[clean(name)]
    s=surname(name)
    if s:
        vals.append(s)
    return sorted(set(v for v in vals if v),key=len,reverse=True)

def mentions(sentence,name):
    out=[]
    for a in aliases(name):
        for m in re.finditer(r'(?<![A-Za-z0-9])'+re.escape(a)+r'(?![A-Za-z0-9])',sentence,re.I):
            out.append((m.start(),m.end()))
    return sorted(set(out))

def attributed_segments(sentence,fighter,opponent):
    own=mentions(sentence,fighter)
    if not own:return []
    all_marks=[(a,b,'self') for a,b in own]+[(a,b,'other') for a,b in mentions(sentence,opponent)]
    all_marks.sort()
    out=[]
    for start,end in own:
        nxt=min((a for a,b,t in all_marks if a>=end),default=len(sentence))
        seg=sentence[start:min(len(sentence),nxt, start+260)]
        if HISTORICAL_CUE.search(seg):
            continue
        out.append(seg)
    return out

def parse_summary_metrics(text,a,b):
    out={a:{},b:{}}
    conflicts={a:[],b:[]}

    def setv(f,key,value):
        v=float(value)
        old=out[f].get(key)
        if old is not None and abs(float(old)-v)>1e-9:
            conflicts[f].append({'field':key,'old':old,'new':v})
        else:
            out[f][key]=v

    for sentence in sentence_parts(text):
        for fighter,opponent in ((a,b),(b,a)):
            for seg in attributed_segments(sentence,fighter,opponent):
                # "Garcia ... landed 29% of his 47 punches thrown per round"
                m=re.search(
                    r'land(?:ed|ing)\s+(?:just\s+)?(\d+(?:\.\d+)?)%\s+of\s+'
                    r'(?:his|her)\s+(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+thrown\s+per\s+round',
                    seg,re.I)
                if m:
                    setv(fighter,'total_accuracy_pct',m.group(1))
                    setv(fighter,'total_thrown_per_round',m.group(2))

                # "Szpilka ... avg'd just 37 punches thrown per round"
                m=re.search(
                    r"(?:avg(?:'d|d)?|averaged|averaging)\s+(?:just\s+)?"
                    r'(\d+(?:\.\d+)?)\s+(?:total\s+)?punches?\s+thrown\s+per\s+round',
                    seg,re.I)
                if m:setv(fighter,'total_thrown_per_round',m.group(1))

                # "... 47 punches thrown per round and landed just 16%"
                m=re.search(
                    r'punches?\s+thrown\s+per\s+round[^.!?]{0,70}?'
                    r'(?:and|while|,)?\s*(?:landed|landing)\s+(?:just\s+)?'
                    r'(\d+(?:\.\d+)?)%(?!\s+of\s+(?:his|her)\s+power)',
                    seg,re.I)
                if m:setv(fighter,'total_accuracy_pct',m.group(1))

                # "... while landing 46% of his power shots"
                m=re.search(
                    r'(?:landed|landing)\s+(?:just\s+)?(\d+(?:\.\d+)?)%\s+of\s+'
                    r'(?:his|her)\s+power\s+(?:shots|punches)',
                    seg,re.I)
                if m:setv(fighter,'power_accuracy_pct',m.group(1))

    for fighter in (a,b):
        if conflicts[fighter]:
            out[fighter]={'_invalid_conflict':True,'_conflict_details':conflicts[fighter]}
    return out

def merge_rows(rows):
    groups=defaultdict(list)
    for r in rows:
        groups[(r['bout_date'],norm(r['fighter']),norm(r['opponent']))].append(r)
    merged=[];quarantined=[]
    metric_fields=('total_thrown_per_round','total_accuracy_pct','power_accuracy_pct')
    for key,items in groups.items():
        base=dict(items[0]);bad=[]
        for field in metric_fields:
            vals={float(x[field]) for x in items if x.get(field) is not None}
            if len(vals)>1:bad.append({'field':field,'values':sorted(vals)})
            elif vals:base[field]=next(iter(vals))
        if bad:
            quarantined.append({'key':key,'conflicts':bad,'source_urls':sorted({x['source_url'] for x in items})})
            continue
        base['source_urls']=sorted({x['source_url'] for x in items})
        capture_dates=sorted({x.get('available_from_date') for x in items if x.get('available_from_date')})
        base['available_from_date']=capture_dates[0] if capture_dates else None
        base['archive_capture_dates']=capture_dates
        base['archive_capture_count']=len(base['source_urls'])
        merged.append(base)
    merged.sort(key=lambda r:(r['bout_date'],norm(r['fighter']),norm(r['opponent'])))
    return merged,quarantined

def main():
    if not DB.exists():
        raise SystemExit(f'missing server boxing database: {DB}')
    d=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=120);d.row_factory=sqlite3.Row
    rows=[];captured=resolved=with_numeric=0;rejections=defaultdict(int);no_numeric_samples=[]
    for row in d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id"):
        captured+=1
        url=str(row['source_id'] or '')
        if 'web.archive.org' not in url.lower() or 'compuboxonline.com' not in url.lower():
            rejections['not_archived_compubox']+=1;continue
        try:payload=json.loads(row['data'])
        except Exception:
            rejections['invalid_payload']+=1;continue
        text=' '.join(paragraphs(payload))
        if not text:
            rejections['no_relevant_paragraphs']+=1;continue
        fight=resolve_fight(d,text,payload)
        if not fight:
            rejections['unresolved_fight']+=1;continue
        resolved+=1
        date,a,b,sources,rounds,header,resolution=fight
        parsed=parse_summary_metrics(text,a,b)
        added=0
        for fighter,opponent in ((a,b),(b,a)):
            st=parsed.get(fighter) or {}
            if st.get('_invalid_conflict'):
                rejections['metric_conflict']+=1;continue
            numeric={k:v for k,v in st.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
            if not numeric:continue
            rows.append({
              'source_url':url,'bout_date':date,'available_from_date':archive_date(url),'fighter':fighter,'opponent':opponent,
              'rounds_observed':rounds,'date_identity_resolution':resolution,
              **numeric,
              'quality':'compubox_owned_archived_explicit_numeric_summary_exact_verified_bout',
              'source_tier':'historical_summary_separate_from_full_round_reports'
            })
            added+=1
        if added:
            with_numeric+=1
        else:
            rejections['resolved_no_safe_numeric_pattern']+=1
            if len(no_numeric_samples)<40:
                no_numeric_samples.append({
                  'url':url,'bout_date':date,'fighters':[a,b],'rounds':rounds,
                  'resolution':resolution,'text_sample':text[:1800]
                })
    d.close()

    merged,quarantined=merge_rows(rows)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w',encoding='utf-8') as fh:
        for r in merged:fh.write(json.dumps(r,ensure_ascii=False)+'\n')
    report={
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'captured_punch_pages_scanned':captured,
      'resolved_archived_compubox_pages':resolved,
      'resolved_pages_with_safe_numeric_summary':with_numeric,
      'raw_fighter_rows':len(rows),
      'merged_fighter_rows':len(merged),
      'distinct_bouts':len({(r['bout_date'],tuple(sorted((norm(r['fighter']),norm(r['opponent']))))) for r in merged}),
      'date_min':min((r['bout_date'] for r in merged),default=None),
      'date_max':max((r['bout_date'] for r in merged),default=None),
      'rejections':dict(rejections),
      'resolved_no_safe_numeric_sample':no_numeric_samples,
      'quarantined_conflicts':len(quarantined),
      'quarantined_conflict_sample':quarantined[:30],
      'policy':'Archived CompuBox-owned captures only; strict verified bout resolution; explicit fighter-attributed rates/percentages only; historical-reference sentences excluded; duplicate captures must agree; research availability begins at earliest verified Wayback capture date, never the fight date.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
