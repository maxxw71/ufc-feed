#!/usr/bin/env python3
"""Read-only coordinate probe for one official WBA monthly PDF."""
import io,json,urllib.request
import pdfplumber

URL='https://www.wbaboxing.com/wba-ranking-pdf/2021/WBA-Official-Ratings-February-2021.pdf'
req=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0 AppwizaWBAProbe/1.0'})
with urllib.request.urlopen(req,timeout=45) as r:raw=r.read()
out={'url':URL,'bytes':len(raw),'pages':[]}
with pdfplumber.open(io.BytesIO(raw)) as pdf:
    for pi,page in enumerate(pdf.pages[:4]):
        w,h=page.width,page.height
        item={'page':pi+1,'width':w,'height':h,'columns':[]}
        # probe both 3 and 1 column boundaries; WBA commonly lays divisions in 3 columns.
        for ci,(x0,x1) in enumerate([(0,w/3),(w/3,2*w/3),(2*w/3,w)]):
            crop=page.crop((x0,0,x1,h))
            txt=crop.extract_text(x_tolerance=2,y_tolerance=2) or ''
            item['columns'].append({'column':ci+1,'x0':x0,'x1':x1,'text':txt[:9000]})
        words=page.extract_words(x_tolerance=2,y_tolerance=2,keep_blank_chars=False)
        item['word_sample']=[{k:x.get(k) for k in ('text','x0','x1','top','bottom')} for x in words[:180]]
        out['pages'].append(item)
print(json.dumps(out,indent=2,ensure_ascii=False))
