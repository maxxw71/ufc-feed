import re, requests, pandas as pd
from bs4 import BeautifulSoup

ODDS='https://www.kaggle.com/api/v1/datasets/download/binduvr/ufc-betting-odds'
# Use a known recent UFCStats fight + fighters from the odds dataset if available.
SAMPLES=[
 'http://ufcstats.com/fight-details/d215c4e6dc1346ae',
 'http://ufcstats.com/fighter-details/1eacf73d6a0055dc',
]

s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0'})
for u in SAMPLES:
    r=s.get(u,timeout=30)
    print('URL',u,'STATUS',r.status_code,'BYTES',len(r.content))
    print(r.text[:500].replace('\n',' '))
