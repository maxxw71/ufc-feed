from __future__ import annotations
import io, zipfile, requests, pandas as pd

URL='https://www.kaggle.com/api/v1/datasets/download/jerzyszocik/ufc-betting-odds-daily-dataset'
r=requests.get(URL,timeout=180)
print('status',r.status_code,'bytes',len(r.content),'type',r.headers.get('content-type'))
r.raise_for_status()
z=zipfile.ZipFile(io.BytesIO(r.content))
print('files',z.namelist())
for n in z.namelist():
    if n.lower().endswith('.csv'):
        with z.open(n) as f:
            df=pd.read_csv(f,nrows=8)
        print('CSV',n,'cols',list(df.columns))
        print(df.head(5).to_dict('records'))
