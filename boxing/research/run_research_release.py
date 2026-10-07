"""Build an isolated, auditable boxing release on appwiza; atomically publish on success."""
import argparse,datetime as dt,fcntl,gzip,json,os,pathlib,shutil,sqlite3,subprocess,sys,zipfile,tarfile,hashlib
from build_release_audit import sha,write

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=pathlib.Path,required=True)
    ap.add_argument('--work',type=pathlib.Path,required=True)
    ap.add_argument('--releases',type=pathlib.Path,default=pathlib.Path('/srv/appwiza-sports/boxing-releases'))
    ap.add_argument('--archive',type=pathlib.Path,default=pathlib.Path('/srv/appwiza-sports/public/downloads/boxing/research-database.zip'))
    ap.add_argument('--resume-master',action='store_true',help='Resume this isolated attempt after the master was built')
    a=ap.parse_args()
    if not pathlib.Path('/opt/sports-python').is_dir():raise SystemExit('Run on appwiza only')
    a.releases.mkdir(parents=True,exist_ok=True)
    lock=open(a.releases/'build.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    private_careers=pathlib.Path(os.environ.get('APPWIZA_BOXING_CAREER_SUPPLEMENTS','/srv/appwiza-sports/boxing-maintenance/private-supplements/verified_careers.jsonl'))
    inventory={}
    if private_careers.exists():inventory['private_verified_careers']=sha(private_careers)
    for folder in ['research','profile_supplements','supplemental_careers','punch_supplements','rankings','official_bouts']:
        for p in sorted((a.repo/'boxing'/folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and (folder!='research' or p.suffix=='.py'):
                inventory[str(p.relative_to(a.repo))]=sha(p)
    for name in ['methods.json','public_reports/HISTORICAL_ODDS_STRICT_UNION_ROWS.json']:
        inventory[name]=sha(a.repo/'boxing'/name)
    inventory['base_archive']=sha(a.archive)
    fingerprint=hashlib.sha256(json.dumps(inventory,sort_keys=True).encode()).hexdigest()
    current=a.releases/'CURRENT.json'
    if current.exists() and not a.resume_master:
        previous=json.loads(current.read_text())
        if previous.get('input_fingerprint')==fingerprint:
            print('UNCHANGED_INPUTS '+previous['release'],flush=True);return
    if shutil.disk_usage(a.releases).free<(1 if a.resume_master else 4)*1024**3:raise SystemExit('Insufficient headroom for isolated build; no snapshots removed')
    if a.work.exists() and not a.resume_master:raise SystemExit('Work directory must be new; previous attempts are preserved')
    a.work.mkdir(parents=True,exist_ok=a.resume_master);root=a.work/'research';root.mkdir(exist_ok=a.resume_master)
    for p in (a.repo/'boxing/research').glob('*.py'):
        if not a.resume_master or p.name in {'market_consensus.py','build_release_audit.py'}:shutil.copy2(p,root/p.name)
    for name in ['profile_supplements','supplemental_careers','punch_supplements','rankings','official_bouts','collectors']:
        src=a.repo/'boxing'/name
        if src.exists() and not a.resume_master:shutil.copytree(src,a.work/name)
    if private_careers.exists() and not a.resume_master:
        staged=a.work/'supplemental_careers/private_verified_careers.jsonl'
        staged.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(private_careers,staged)
        assert sha(staged)==inventory['private_verified_careers'],'Private career inputs changed during staging'
    for p in (a.repo/'boxing/public_reports').glob('HISTORICAL_ODDS*ROWS.json'):shutil.copy2(p,root/p.name)
    archive_hash=sha(a.archive)
    if not a.resume_master:
        with zipfile.ZipFile(a.archive) as z:
            members=[n for n in z.namelist() if n.endswith('.sqlite3')]
            if len(members)!=1:raise SystemExit('Expected exactly one input database')
            with z.open(members[0]) as src,open(root/'boxing.sqlite3','wb') as dest:shutil.copyfileobj(src,dest)
        write(a.work/'input_archive.json',{'sha256':archive_hash})
    else:
        assert json.loads((a.work/'input_archive.json').read_text())['sha256']==archive_hash,'Input archive changed since initial attempt'
    if archive_hash!=sha(a.archive):raise SystemExit('Input archive changed during extraction')
    def run(name,*args):
        print('STAGE '+name,flush=True)
        subprocess.run([sys.executable,str(root/name),*args],cwd=root,check=True)
    if not a.resume_master:
        run('build_punch_profiles.py','--db','boxing.sqlite3','--out','punch_profiles')
        run('apply_supplemental_careers.py');run('apply_profile_supplements.py');run('rebuild_identity_links.py')
        run('build_chronological_master.py')
    run_dir=pathlib.Path((root/'LATEST_CHRONOLOGICAL_MASTER.txt').read_text().strip())
    run('validated_price_method_search.py');run('audit_strict_profile_gaps.py');run('audit_reach_integrity.py')
    reach=json.loads((root/'REACH_INTEGRITY_AUDIT.json').read_text())
    if reach.get('critical_issue_count'):raise SystemExit('Critical reach integrity failure')
    release=a.releases/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    release.mkdir()
    run('build_release_audit.py','--run',str(run_dir),'--repo',str(a.repo),'--out',str(release))
    frozen=json.loads((release/'frozen_hypotheses.json').read_text())
    registry=a.releases/'hypotheses';registry.mkdir(exist_ok=True)
    hypothesis=registry/(frozen['methods_sha256']+'.json')
    if not hypothesis.exists():write(hypothesis,frozen)
    shutil.copy2(hypothesis,release/'frozen_hypotheses.json')
    for p in run_dir.glob('*.json'):shutil.copy2(p,release/p.name)
    for p in run_dir.glob('*.txt'):shutil.copy2(p,release/p.name)
    for name in ['REACH_INTEGRITY_AUDIT.json','PROFILE_GAP_AUDIT.json','SUPPLEMENT_APPLY_REPORT.json','PROFILE_SUPPLEMENT_APPLY_REPORT.json','IDENTITY_LINKS_V2.json']:
        p=root/name
        if p.exists():shutil.copy2(p,release/name)
    shutil.copy2(root/'punch_profiles/coverage.json',release/'punch_profile_coverage.json')
    # Retain reproducible artifacts compressed; remove only this job's disposable inputs later.
    for name in ['source.sqlite3','boxing_prefight_master.jsonl']:
        with open(run_dir/name,'rb') as src,gzip.open(release/(name+'.gz'),'wb',compresslevel=1) as dest:shutil.copyfileobj(src,dest)
        with gzip.open(release/(name+'.gz'),'rb') as archived:
            assert hashlib.file_digest(archived,'sha256').hexdigest()==sha(run_dir/name),'Compressed artifact verification failed'
    with tarfile.open(release/'build_inputs.tar.gz','w:gz') as tar:
        for p in root.glob('*.py'):tar.add(p,arcname='research/'+p.name)
        for name in ['profile_supplements','supplemental_careers','punch_supplements','rankings','official_bouts','collectors']:
            if (a.work/name).exists():tar.add(a.work/name,arcname=name)
    manifest=json.loads((release/'release_manifest.json').read_text())
    manifest.update(base_archive_sha256=archive_hash,base_archive=str(a.archive),source_db=str(release/'source.sqlite3.gz'),master_path=str(release/'boxing_prefight_master.jsonl.gz'),artifact_storage='gzip; database/master hashes identify uncompressed contents')
    manifest['artifacts']={p.name:sha(p) for p in release.iterdir() if p.is_file() and p.name!='release_manifest.json'}
    write(release/'release_manifest.json',manifest)
    write(a.releases/'CURRENT.json',{'release':str(release),'manifest_sha256':sha(release/'release_manifest.json'),'status':manifest['status'],'eligibility':manifest['eligibility'],'input_fingerprint':fingerprint})
    public=a.repo/'boxing/public_phase2';public.mkdir(exist_ok=True)
    # Detailed ledgers, manifests and hypotheses remain private on the server.
    # Only the established aggregate reports enter the repository publication path.
    for name in ['coverage.json','punch_profile_coverage.json','PROFILE_GAP_AUDIT.json','REACH_INTEGRITY_AUDIT.json','validated_price_method_search.json','validated_price_method_search.txt']:
        shutil.copy2(release/name,public/name)
    summary=json.loads((public/'validated_price_method_search.json').read_text())
    for tier in summary.get('tiers',{}).values():
        for row in tier.get('existing_candidates',[])+tier.get('notable_fixed_rule_screen',[]):row.pop('selected_bouts',None)
    write(public/'validated_price_method_search.json',summary)
    write(public/'status.json',{'status':'audited_research_release_not_live_methods','built_at':manifest['built_at'],'eligibility':manifest['eligibility'],'detailed_audit_location':'private_appwiza_server'})
    subprocess.run([sys.executable,str(a.repo/'boxing/research/build_current_dataset_status.py')],check=True,stdout=subprocess.DEVNULL)
    print('RELEASE '+str(release),flush=True)
    # Preserve every pre-existing research snapshot. Only our successful build workspace is disposable.
    shutil.rmtree(a.work)

if __name__=='__main__':main()
