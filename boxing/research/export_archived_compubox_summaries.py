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

from import_archived_compubox_rounds import resolve_fight,norm,surname,fighter_row_aliases

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

def flatten_payload_text(payload):
    """Flatten captured table/text payload for identity and final-total parsing."""
    vals=[]
    def walk(x):
        if isinstance(x,str):
            y=clean(x)
            if y:vals.append(y)
        elif isinstance(x,list):
            for v in x:walk(v)
        elif isinstance(x,dict):
            for k,v in x.items():
                if k in {'tables','text_sample','full_text','page_text'}:walk(v)
    walk(payload)
    return clean(' '.join(vals))

def legacy_full_round_pairs():
    path=ROOT/'punch_supplements'/'legacy_compubox_round_reports.jsonl'
    out=set()
    if not path.exists():return out
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:x=json.loads(line)
        except Exception:continue
        fs=x.get('fighters') or []
        if len(fs)==2 and x.get('bout_date'):
            out.add((str(x['bout_date']),tuple(sorted(norm(v) for v in fs))))
    return out

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
        # Explicit two-fighter landed comparison: "Garcia outlanded Amir Khan
        # 44-23 in power shots." Both values are unambiguous landed counts.
        for fighter,opponent in ((a,b),(b,a)):
            for fa in aliases(fighter):
                for oa in aliases(opponent):
                    m=re.search(
                        r'(?<![A-Za-z0-9])'+re.escape(fa)+
                        r'(?![A-Za-z0-9])[^.!?]{0,90}?outlanded\s+'+
                        re.escape(oa)+r'\s+(\d{1,3})\s*[-–]\s*(\d{1,3})\s+in\s+power\s+shots',
                        sentence,re.I)
                    if m:
                        setv(fighter,'power_landed',m.group(1))
                        setv(opponent,'power_landed',m.group(2))

        # Direct subject construction: "Fighter landed 47 power shots (54%)."
        # The fighter name must own the finite verb; do not infer from an
        # opponent mention followed by a trailing participial "landing" clause.
        for fighter,opponent in ((a,b),(b,a)):
            for fa in aliases(fighter):
                m=re.search(
                    r'(?<![A-Za-z0-9])'+re.escape(fa)+
                    r"(?![A-Za-z0-9])(?:['’]s)?[^.!?]{0,70}?\blanded\s+"
                    r'(\d{1,4})\s+power\s+shots?\s*\((\d+(?:\.\d+)?)%\)',
                    sentence,re.I)
                if m:
                    setv(fighter,'power_landed',m.group(1))
                    setv(fighter,'power_accuracy_pct',m.group(2))

        # Narrow subject-verb-object construction:
        # "Cotto ... mugged Rodriguez, landing 47 power shots (54%)."
        for fighter,opponent in ((a,b),(b,a)):
            for fa in aliases(fighter):
                for oa in aliases(opponent):
                    m=re.search(
                        r'(?<![A-Za-z0-9])'+re.escape(fa)+
                        r'(?![A-Za-z0-9])[^.!?]{0,100}?'
                        r'(?:mugged|dominated|outworked|overwhelmed)\s+'+re.escape(oa)+
                        r"\s*,?\s*(?:landing|landed)\s+(\d{1,4})\s+power\s+shots?\s*"
                        r'\((\d+(?:\.\d+)?)%\)',
                        sentence,re.I)
                    if m:
                        setv(fighter,'power_landed',m.group(1))
                        setv(fighter,'power_accuracy_pct',m.group(2))

        # Possessive identity may be normalized without an apostrophe:
        # "27 of Cottos 55 landed punches were to Rodriguezs body."
        for fighter,opponent in ((a,b),(b,a)):
            for fa in aliases(fighter):
                m=re.search(
                    r'(\d{1,4})\s+of\s+'+re.escape(fa)+
                    r"(?:['’]?s)?\s+(\d{1,4})\s+landed\s+punches?\s+were\s+to\s+"
                    r'(?:his\s+)?(?:the\s+)?(?:[A-Za-zÀ-ÿ0-9 .\'’\-]+?\s+)?body',
                    sentence,re.I)
                if m:
                    setv(fighter,'body_landed',m.group(1))
                    setv(fighter,'total_landed',m.group(2))

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

                # "Mathebula ... averaging 92 thrown per round"
                m=re.search(
                    r"(?:avg(?:'d|d)?|averaged|averaging)\s+(?:just\s+)?"
                    r'(\d+(?:\.\d+)?)\s+thrown\s+per\s+round',
                    seg,re.I)
                if m:setv(fighter,'total_thrown_per_round',m.group(1))

                # "27 of Cotto's 55 landed punches were to the body"
                for fa in aliases(fighter):
                    m=re.search(
                        r'(\d{1,4})\s+of\s+'+re.escape(fa)+
                        r"(?:['’]?s)?\s+(\d{1,4})\s+landed\s+punches?\s+were\s+to\s+"
                        r'(?:his\s+)?(?:the\s+)?body',
                        sentence,re.I)
                    if m:
                        setv(fighter,'body_landed',m.group(1))
                        setv(fighter,'total_landed',m.group(2))

                # "9 jabs landed per round/24 thrown - 10 power landed/13 thrown"
                m=re.search(
                    r'(\d+(?:\.\d+)?)\s+jabs?\s+landed\s+per\s+round\s*/\s*'
                    r'(\d+(?:\.\d+)?)\s+thrown[^.!?]{0,50}?'
                    r'(\d+(?:\.\d+)?)\s+power\s+landed\s*/\s*'
                    r'(\d+(?:\.\d+)?)\s+thrown',
                    seg,re.I)
                if m:
                    setv(fighter,'jab_landed_per_round',m.group(1))
                    setv(fighter,'jab_thrown_per_round',m.group(2))
                    setv(fighter,'power_landed_per_round',m.group(3))
                    setv(fighter,'power_thrown_per_round',m.group(4))

                # "Pianeta ... landed just 24 total punches all fight"
                m=re.search(
                    r'(?:landed|landing)\s+(?:just\s+)?(\d{1,4})\s+total\s+punches?\s+all\s+fight',
                    seg,re.I)
                if m:setv(fighter,'total_landed',m.group(1))

    for fighter in (a,b):
        if conflicts[fighter]:
            out[fighter]={'_invalid_conflict':True,'_conflict_details':conflicts[fighter]}
    return out


def parse_final_table_metrics(text,a,b,rounds=None):
    """Parse a legacy CompuBox final Total/Jab/Power table with exact arithmetic.

    This is intentionally fight-total only. It never synthesizes round rows.
    """
    s=clean(text)
    m=re.search(r'Final\s+Punch(?:Stat)?\s+(?:Report|Stats)',s,re.I)
    if not m:
        return {a:{},b:{}}
    tail=s[m.end():]
    out={a:{},b:{}}
    used_spans=[]
    for fighter in (a,b):
        labels=fighter_row_aliases(fighter,surname(fighter))
        label_pat='(?:'+'|'.join(re.escape(x) for x in labels)+')'
        # Final tables are: Fighter TOTAL_L/T JAB_L/T POWER_L/T [percentages].
        fm=re.search(
            r'(?<![A-Za-z0-9])'+label_pat+r'(?![A-Za-z0-9])\s+'
            r'(\d{1,4})\s*/\s*(\d{1,4})\s+'
            r'(\d{1,4})\s*/\s*(\d{1,4})\s+'
            r'(\d{1,4})\s*/\s*(\d{1,4})(?:\s+\d{1,3}%\s+\d{1,3}%\s+\d{1,3}%)?',
            tail,re.I)
        if not fm:
            continue
        span=fm.span()
        if any(not (span[1]<=a0 or span[0]>=b0) for a0,b0 in used_spans):
            continue
        used_spans.append(span)
        tl,tt,jl,jt,pl,pt=map(int,fm.groups())
        if min(tl,tt,jl,jt,pl,pt)<0 or tl>tt or jl>jt or pl>pt:
            continue
        if (tl,tt)!=(jl+pl,jt+pt):
            continue
        d={'total_landed':tl,'total_thrown':tt,'jab_landed':jl,'jab_thrown':jt,
           'power_landed':pl,'power_thrown':pt}
        if rounds:
            d.update({
              'total_landed_per_round':tl/rounds,'total_thrown_per_round':tt/rounds,
              'jab_landed_per_round':jl/rounds,'jab_thrown_per_round':jt/rounds,
              'power_landed_per_round':pl/rounds,'power_thrown_per_round':pt/rounds
            })
        d['total_accuracy_pct']=100*tl/tt if tt else None
        d['power_accuracy_pct']=100*pl/pt if pt else None
        out[fighter]=d
    return out

def merge_metric_sources(base,extra):
    out={k:dict(v) for k,v in base.items()}
    for fighter,vals in extra.items():
        dst=out.setdefault(fighter,{})
        if dst.get('_invalid_conflict'):continue
        conflicts=[]
        for k,v in vals.items():
            if k in dst and dst[k] is not None and v is not None and abs(float(dst[k])-float(v))>1e-9:
                conflicts.append({'field':k,'values':[dst[k],v]})
            elif v is not None:
                dst[k]=v
        if conflicts:
            out[fighter]={'_invalid_conflict':True,'_conflict_details':conflicts}
    return out

def merge_rows(rows):
    groups=defaultdict(list)
    for r in rows:
        groups[(r['bout_date'],norm(r['fighter']),norm(r['opponent']))].append(r)
    merged=[];quarantined=[]
    metric_fields=(
      'total_landed','total_thrown','total_landed_per_round','total_thrown_per_round','total_accuracy_pct','body_landed',
      'jab_landed','jab_thrown','jab_landed_per_round','jab_thrown_per_round',
      'power_landed','power_thrown','power_landed_per_round','power_thrown_per_round','power_accuracy_pct'
    )
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
    full_round_pairs=legacy_full_round_pairs()
    for row in d.execute("select source_id,data from source_rows where source='external_evidence' and kind='punch' order by source_id"):
        captured+=1
        url=str(row['source_id'] or '')
        if 'web.archive.org' not in url.lower() or 'compuboxonline.com' not in url.lower():
            rejections['not_archived_compubox']+=1;continue
        try:payload=json.loads(row['data'])
        except Exception:
            rejections['invalid_payload']+=1;continue
        prose=' '.join(paragraphs(payload))
        raw_text=flatten_payload_text(payload)
        identity_text=clean(' '.join(x for x in (prose,raw_text) if x))
        if not identity_text:
            rejections['no_relevant_text_or_tables']+=1;continue
        fight=resolve_fight(d,identity_text,payload)
        if not fight:
            rejections['unresolved_fight']+=1;continue
        resolved+=1
        date,a,b,sources,rounds,header,resolution=fight
        parsed=parse_summary_metrics(prose,a,b) if prose else {a:{},b:{}}
        pair_key=(date,tuple(sorted((norm(a),norm(b)))))
        final_stats=parse_final_table_metrics(raw_text,a,b,rounds)
        if pair_key in full_round_pairs:
            if any(final_stats.get(x) for x in (a,b)):
                rejections['final_table_duplicate_full_round']+=1
            final_stats={a:{},b:{}}
        parsed=merge_metric_sources(parsed,final_stats)
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
              'quality':('compubox_owned_archived_final_total_table_exact_arithmetic_verified_bout'
                         if final_stats.get(fighter) else
                         'compubox_owned_archived_explicit_numeric_summary_exact_verified_bout'),
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
      'policy':'Archived CompuBox-owned captures only; strict verified bout resolution; explicit fighter-attributed prose metrics or exact-arithmetic Final Total/Jab/Power tables; full-round legacy pairs are deduplicated from the lower-resolution final-table tier; historical-reference sentences excluded; duplicate captures must agree; research availability begins at earliest verified Wayback capture date, never the fight date.'
    }
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
