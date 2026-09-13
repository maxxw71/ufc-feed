#!/usr/bin/env python3
"""Patch the live Appwiza UFC watcher from the reviewed repository patch.

This helper deliberately does not send email or modify systemd.  It applies the
reviewed U7 gate, syntax-checks the watcher, then optionally regenerates the
public UFC page using the existing publisher.  It is intended for the dedicated
Appwiza self-hosted runner after narrow filesystem ACLs are granted.
"""
from __future__ import annotations
import argparse,ast,os,py_compile,re,shutil,subprocess,sys,time
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
PATCH_SOURCE=REPO/'scripts/apply_live_striking_td_risk_gate.sh'
DEFAULT_WATCHER=Path('/home/anestishkurti92/ufc-predictor-v1/ufc_email_watcher.py')
PUBLIC=Path('/srv/appwiza-sports/public/ufc/index.html')
PUBLISHER=Path('/opt/sports-publisher/initial_ufc.py')
VENV_PY=Path('/home/anestishkurti92/ufc-predictor-v1/venv/bin/python')

def extract_patch(src:str):
    m=re.search(r"addon=r'''(.*?)'''\ns=s\.replace",src,re.S)
    if not m:raise RuntimeError('Could not extract reviewed U7 addon from patch source')
    addon=m.group(1)
    om=re.search(r'^old=(.+)$',src,re.M);nm=re.search(r'^new=(.+)$',src,re.M)
    if not om or not nm:raise RuntimeError('Could not extract reviewed U7 stat tuple replacement')
    return addon,ast.literal_eval(om.group(1)),ast.literal_eval(nm.group(1))

def patch_watcher(path:Path):
    s=path.read_text()
    if 'STRIKING_TD_RISK_GATE_V2' in s:
        return None,'already-installed'
    if 'STRIKING_METHODS_RULE_V1' not in s:
        raise RuntimeError('Existing striking-method patch not present; refusing partial U7 install')
    addon,old,new=extract_patch(PATCH_SOURCE.read_text())
    marker='if __name__ == "__main__":'
    if marker not in s:marker="if __name__ == '__main__':"
    if marker not in s:raise RuntimeError('Watcher main marker not found')
    if old not in s:raise RuntimeError('Existing U7 historical tuple not found; refusing partial install')
    backup=path.with_name(path.name+f'.pre-u7-risk-gate.{int(time.time())}.bak')
    shutil.copy2(path,backup)
    updated=s.replace(marker,addon+'\n'+marker,1).replace(old,new,1)
    path.write_text(updated)
    try:py_compile.compile(str(path),doraise=True)
    except Exception:
        shutil.copy2(backup,path)
        raise
    return backup,'patched'

def publish():
    env=dict(os.environ);env['HOME']='/home/anestishkurti92';env['PYTHONPATH']='/opt/sports-publisher'
    cp=subprocess.run([str(VENV_PY),str(PUBLISHER)],cwd='/opt/sports-publisher',env=env,text=True,capture_output=True,timeout=300)
    print(cp.stdout,end='');print(cp.stderr,end='',file=sys.stderr)
    if cp.returncode:raise RuntimeError(f'Publisher failed with {cp.returncode}')

def verify():
    s=PUBLIC.read_text(errors='ignore')
    checks={
        'new_title':'Striking + TD Defense — Risk Gated' in s or 'STRIKING + TD DEFENSE — RISK GATED' in s,
        '40_40':'40 / 40' in s,
        'roi_23_08':'+23.08%' in s,
        'talbott_present':'Payton Talbott' in s,
        'old_upcoming_stats_possible_history_only':('94.4%' in s or '+17.64%' in s),
    }
    print('VERIFY',checks)
    if not all(checks[k] for k in ('new_title','40_40','roi_23_08')):
        raise RuntimeError('Live UFC page did not show the new U7 method after publish')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--watcher',type=Path,default=DEFAULT_WATCHER);ap.add_argument('--publish',action='store_true')
    a=ap.parse_args()
    backup,status=patch_watcher(a.watcher);print('WATCHER',status,'backup=',backup)
    if a.publish:publish();verify()
