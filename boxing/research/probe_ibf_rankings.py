#!/usr/bin/env python3
"""Read-only probe of official IBF ratings archive/backend."""
import json,re,urllib.parse,urllib.request
from bs4 import BeautifulSoup

UA='Mozilla/5.0 AppwizaBoxingIBFProbe/1.0'
BASE='https://www.ibf-usba-boxing.com'

def get(url,limit=4_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('too large')
        return r.geturl(),raw.decode('utf-8','replace'),r.headers.get('content-type')

def main():
    out={'ratings_page':{},'archive_pages':[],'rest':{}}
    final,html,ctype=get(BASE+'/ratings/')
    soup=BeautifulSoup(html,'lxml')
    out['ratings_page']={
      'final':final,
      'forms':[{'action':f.get('action'),'method':f.get('method'),
                'inputs':[{'name':x.get('name'),'type':x.get('type'),'value':x.get('value')} for x in f.find_all('input')],
                'selects':[{'name':s.get('name'),'options':[(o.get('value'),o.get_text(' ',strip=True)) for o in s.find_all('option')]} for s in f.find_all('select')]}
               for f in soup.find_all('form')],
      'scripts':[s.get('src') for s in soup.find_all('script',src=True)],
      'ajax_tokens':sorted(set(x.rstrip('",\'<>);') for x in re.findall(r'https?://\\S+|admin-ajax\\.php|wp-json\\S*',html,re.I)))[:100]
    }
    out['rest_sample_ratings']={}
    for endpoint in [
        BASE+'/wp-json/wp/v2/ratings?per_page=1&orderby=date&order=desc',
        BASE+'/wp-json/wp/v2/ratings?per_page=1&orderby=date&order=asc',
        BASE+'/wp-content/themes/base-blocks-theme-master/js/ratings-filter.js?ver=1.0.0',
    ]:
        try:
            final,body,ct=get(endpoint)
            out['rest_sample_ratings'][endpoint]={'final':final,'body':body[:30000],'content_type':ct}
        except Exception as e:
            out['rest_sample_ratings'][endpoint]={'error':repr(e)}
    links={}
    for page in range(1,9):
        url=BASE+'/org/ibf/'+('' if page==1 else f'page/{page}/')
        try:_,h,_=get(url)
        except Exception as e:
            out['archive_pages'].append({'page':page,'error':repr(e)});continue
        s=BeautifulSoup(h,'lxml');found=[]
        for a in s.find_all('a',href=True):
            title=a.get_text(' ',strip=True)
            href=urllib.parse.urljoin(url,a['href'])
            if re.search(r'^IBF:',title,re.I) and re.search(r'\d{2}/\d{4}',title):
                found.append({'title':title,'url':href});links[href]=title
        out['archive_pages'].append({'page':page,'count':len(found),'sample':found[:8]})
    out['unique_rating_links']=len(links)
    out['rating_years']=sorted(set(int(m.group(1)) for t in links.values() for m in [re.search(r'/(\d{4})',t)] if m))
    probes=[]
    for url,title in list(links.items())[:6]:
        slug=urllib.parse.urlsplit(url).path.strip('/').split('/')[-1]
        item={'url':url,'title':title,'slug':slug}
        try:
            f,h,ct=get(url);item['direct']={'final':f,'bytes':len(h),'title':BeautifulSoup(h,'lxml').title.get_text(' ',strip=True) if BeautifulSoup(h,'lxml').title else None}
        except Exception as e:item['direct']={'error':repr(e)}
        for endpoint in [
            f'{BASE}/wp-json/wp/v2/posts?slug={urllib.parse.quote(slug)}',
            f'{BASE}/wp-json/wp/v2/pages?slug={urllib.parse.quote(slug)}',
            f'{BASE}/wp-json/wp/v2/search?search={urllib.parse.quote(title)}&per_page=5'
        ]:
            try:
                _,body,ct=get(endpoint);item.setdefault('rest',[]).append({'url':endpoint,'body':body[:1200],'content_type':ct})
            except Exception as e:item.setdefault('rest',[]).append({'url':endpoint,'error':repr(e)})
        probes.append(item)
    out['probes']=probes
    for endpoint in ['/wp-json/','/wp-json/wp/v2/types','/wp-json/wp/v2/taxonomies']:
        try:_,body,ct=get(BASE+endpoint);out['rest'][endpoint]={'body':body[:6000],'content_type':ct}
        except Exception as e:out['rest'][endpoint]={'error':repr(e)}
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
