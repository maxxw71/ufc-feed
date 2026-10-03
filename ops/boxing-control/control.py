"""Restricted private boxing controller. No arbitrary command or path input."""
import collections,datetime,fcntl,gzip,hashlib,json,os,pathlib,re,shutil,sqlite3,subprocess,sys,time
SOURCE=pathlib.Path('/home/anestishkurti92/boxing-research/completion_runs/20261002T1540Z/master_repair_staging_20261003T0203Z')
ROOT=pathlib.Path('/home/anestishkurti92/boxing-cloud-control/20261003-v4')
PY='/opt/sports-envs/boxing-research/bin/python'
VALIDATOR_SHA='861102f4fb1a00ced16b240aaea5e8f0b26873e115df6858ded90e8d49b5538e'
ACTIONS={'status','start-existing-validation','compare'}
INPUTS=['source.sqlite3','source_event_reviews.json','features.py','enrich_history.py','build_chronological_master.py','history_integrity.py','test_chronological_master.py','test_staged_history.py','test_exact_source_aliases.py','test_history_integrity.py','rebuild_staging.v3.py','rebuild_staging.py','build_report.json','population_diagnosis.json','boxing_prefight_master.jsonl.gz','old_new_differences.jsonl.gz','excluded_current_features.jsonl.gz','canonical_history_events.jsonl.gz','history_reconciliation.json']
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def pairs(items):
 out={}
 for k,v in items:
  if k in out:raise ValueError('duplicate JSON key')
  out[k]=v
 return out
def request(raw):
 if len(raw)>512:raise ValueError('request too large')
 r=json.loads(raw,object_pairs_hook=pairs)
 if type(r)!=dict or set(r)!={'action','request_id'}:raise ValueError('exact request schema required')
 if type(r['action'])!=str or r['action'] not in ACTIONS:raise ValueError('unsupported action')
 if type(r['request_id'])!=str or not re.fullmatch('[a-z0-9-]{8,40}',r['request_id']):raise ValueError('invalid request id')
 return r
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def write(path,value):
 temp=path.with_suffix(path.suffix+'.tmp')
 temp.write_text(json.dumps(value,indent=2));os.replace(temp,path)
def prepare():
 assert ROOT.resolve()==ROOT and ROOT.stat().st_uid==os.getuid(),'unexpected control directory ownership'
 work=ROOT/'validation';work.mkdir(exist_ok=True)
 for name in INPUTS:
  target=work/name;source=SOURCE/name
  assert source.is_file(),name
  if target.exists() or target.is_symlink():assert target.is_symlink() and target.resolve()==source.resolve(),name
  else:target.symlink_to(source)
 validator=SOURCE/'validate_staging.v4.py'
 assert sha(validator)==VALIDATOR_SHA,'staged validator changed'
 # A copied validator resolves outputs inside account-owned validation staging.
 shutil.copyfile(validator,work/'validate_staging.v4.py')
 return work
def compact_status():
 out={'at':now(),'source_staging':str(SOURCE),'output_staging':str(ROOT/'validation'),'canonical_writes':False}
 for name,keys in [('build_report.json',['counts','differences','history_integrity','safe_join_coverage','tiers']),('history_reconciliation.json',['before_observations','after_events','held_ambiguous_observations','actions','method_repairs'])]:
  p=SOURCE/name
  if p.is_file():
   d=json.loads(p.read_text());out[name]={k:d[k] for k in keys if k in d}
 for name in ['validation.json','comparison.json','worker-status.json']:
  p=ROOT/'validation'/name
  if p.is_file():out[name]=json.loads(p.read_text())
 return out
def comparison(work):
 v=json.loads((work/'validation.json').read_text());assert v['validation']=='PASS' and not v['errors'],'independent validation required'
 db=sqlite3.connect(work/'comparison-index.sqlite3');db.execute('create table if not exists prior(id text primary key, data text)');db.execute('delete from prior')
 def small(r):
  return {s:{'id':r[s]['id'],'inputs':r[s]['input_bout_ids'],'n':r[s]['summary']['observed_prior_bouts']} for s in ['fighter','opponent']}
 for line in gzip.open(SOURCE/'attempt3_blocked_duplicate_history/boxing_prefight_master.jsonl.gz','rt'):
  r=json.loads(line);db.execute('insert into prior values (?,?)',(r['source_id'],json.dumps(small(r))))
 db.commit();counts=collections.Counter();affected=collections.Counter()
 with gzip.open(work/'v3_v4_history_differences.jsonl.gz','wt',compresslevel=1) as out:
  for line in gzip.open(SOURCE/'boxing_prefight_master.jsonl.gz','rt'):
   r=json.loads(line);sid=r['source_id'];found=db.execute('select data from prior where id=?',(sid,)).fetchone()
   if not found:counts['added_target']+=1;continue
   counts['shared_target']+=1;before=json.loads(found[0]);db.execute('delete from prior where id=?',(sid,))
   for side in ['fighter','opponent']:
    previous=before[side];current=r[side];ids=current['input_bout_ids']
    if previous['inputs']!=ids:
     counts['changed_history_sides']+=1;affected[current['id']]+=1
     out.write(json.dumps({'source_id':sid,'side':side,'owner':current['id'],'prior_count_before':previous['n'],'prior_count_after':current['summary']['observed_prior_bouts'],'removed_ids':sorted(set(previous['inputs'])-set(ids)),'added_ids':sorted(set(ids)-set(previous['inputs']))})+'\n')
  counts['removed_target']=db.execute('select count(*) from prior').fetchone()[0]
 db.commit();db.close()
 write(work/'comparison.json',{'at':now(),'validation':'PASS','changes':dict(counts),'affected_history_sides_by_identity':dict(affected),'canonical_writes':False})
def worker(action):
 assert action in ACTIONS-{'status'}
 work=prepare()
 with (ROOT/'research.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  write(work/'worker-status.json',{'at':now(),'action':action,'state':'running'})
  try:
   if action=='start-existing-validation':subprocess.run([PY,'-B',str(work/'validate_staging.v4.py')],cwd=work,check=True)
   else:comparison(work)
   write(work/'worker-status.json',{'at':now(),'action':action,'state':'completed'})
  except BaseException as e:
   write(work/'worker-status.json',{'at':now(),'action':action,'state':'failed','error_type':type(e).__name__});raise
def main():
 r=request(sys.stdin.read(513));ROOT.mkdir(parents=True,exist_ok=True)
 assert ROOT.resolve()==ROOT and ROOT.stat().st_uid==os.getuid()
 if r['action']=='status':print(json.dumps(compact_status()),flush=True);return
 receipt=ROOT/(r['request_id']+'.json')
 if receipt.exists():
  previous=json.loads(receipt.read_text());assert previous['action']==r['action'],'request id reused for another action'
  print(json.dumps(previous),flush=True);return
 with (ROOT/'launch.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  unit='boxing-cloud-'+r['request_id']
  write(receipt,{'at':now(),**r,'state':'starting','unit':unit})
  cmd=['systemd-run','--user','--wait','--collect','--pipe','--unit='+unit,'--property=CPUQuota=5%','--property=MemoryHigh=300M','--property=MemoryMax=350M','--property=Nice=15','--property=IOWeight=10',PY,'-B',str(ROOT/'control.py'),'--worker',r['action']]
  child=subprocess.Popen(cmd)
  while child.poll() is None:
   print(json.dumps({'at':now(),'request_id':r['request_id'],'state':'waiting_for_capped_worker'}),flush=True);time.sleep(30)
  result={'at':now(),**r,'state':'completed' if child.returncode==0 else 'failed','exit_code':child.returncode,'unit':unit,'status':compact_status()}
  write(receipt,result);print(json.dumps(result),flush=True)
  if child.returncode:raise SystemExit(child.returncode)
if __name__=='__main__':
 if len(sys.argv)==3 and sys.argv[1]=='--worker':worker(sys.argv[2])
 elif len(sys.argv)==1:main()
 else:raise SystemExit('unsupported invocation')
