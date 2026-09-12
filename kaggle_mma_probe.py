import io, zipfile, requests

UA='mma-hybrid-research/3.0'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept':'application/json,*/*'})
for u in [
 'https://www.kaggle.com/api/v1/datasets/view/leandroiber/mmastats',
 'https://www.kaggle.com/api/v1/datasets/metadata/leandroiber/mmastats',
]:
    r=s.get(u,timeout=60)
    print('META',u,r.status_code,r.headers.get('content-type'),len(r.content),r.text[:2000],flush=True)

# Try likely versioned download URLs after discovering metadata.
versions=[]
try:
    j=s.get('https://www.kaggle.com/api/v1/datasets/view/leandroiber/mmastats',timeout=60).json()
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if 'version' in k.lower() and isinstance(v,(int,str)):
                    print('VERSION_FIELD',k,v,flush=True)
                    try: versions.append(int(v))
                    except: pass
                walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(j)
except Exception as e:
    print('META_PARSE_ERR',e,flush=True)

urls=['https://www.kaggle.com/api/v1/datasets/download/leandroiber/mmastats']
for v in sorted(set(versions), reverse=True)[:10]:
    urls.append(f'https://www.kaggle.com/api/v1/datasets/download/leandroiber/mmastats?datasetVersionNumber={v}')
for u in urls:
    r=requests.get(u,timeout=120,headers={'User-Agent':UA},allow_redirects=True)
    print('DL',u,r.status_code,r.url,r.headers.get('content-type'),len(r.content),r.content[:40],flush=True)
    try:
        z=zipfile.ZipFile(io.BytesIO(r.content)); print('ZIP_OK',z.namelist(),flush=True); break
    except Exception as e:
        print('ZIP_BAD',type(e).__name__,str(e),flush=True)
