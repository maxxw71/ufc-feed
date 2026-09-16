#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path.home()/"ufc-predictor-v1"
WATCHER=ROOT/"ufc_email_watcher.py"

FAMILY_MAP={
    'U1':'structural','U2':'structural','U3':'structural','U4':'structural',
    'U5':'wrestling','U6':'striking','U7':'striking','U8':'striking',
    'U9':'experience','U10':'recovery'
}

# Historical confluence summary from common point-in-time v6 universe.
HIST={
    'exact':{
        '1':{'n':757,'wins':556,'losses':201,'win_pct':0.7344782034,'roi':0.0328387552},
        '2':{'n':124,'wins':115,'losses':9,'win_pct':0.9274193548,'roi':0.2153298740},
        '3':{'n':70,'wins':66,'losses':4,'win_pct':0.9428571429,'roi':0.1959560138},
        '4':{'n':38,'wins':32,'losses':6,'win_pct':0.8421052632,'roi':0.0696215118},
        '5':{'n':6,'wins':6,'losses':0,'win_pct':1.0,'roi':0.2290252340},
        '6':{'n':1,'wins':1,'losses':0,'win_pct':1.0,'roi':0.1694915254},
    },
    'at_least':{
        '2':{'n':239,'wins':220,'losses':19,'win_pct':0.9205020921,'roi':0.1866405260},
        '3':{'n':115,'wins':105,'losses':10,'win_pct':0.9130434783,'roi':0.1557059247},
        '4':{'n':45,'wins':39,'losses':6,'win_pct':0.8666666667,'roi':0.0930946751},
        '5':{'n':7,'wins':7,'losses':0,'win_pct':1.0,'roi':0.2205204185},
    }
}

ADDON=r'''
# UFC_CONFLUENCE_LAYER_V1
# Research/telemetry layer only. Does not create or veto bets.
_UFC_METHOD_FAMILIES={
 'U1':'structural','U2':'structural','U3':'structural','U4':'structural',
 'U5':'wrestling','U6':'striking','U7':'striking','U8':'striking',
 'U9':'experience','U10':'recovery'}
_UFC_CONFLUENCE_HISTORY={hist}

def _confluence_method_ids(p,w=None):
    ids=[]
    try:
        for c in ufc_email_design.method_cards(p,w):
            i=str(c.get('id') or '').upper().strip()
            if i in _UFC_METHOD_FAMILIES and i not in ids: ids.append(i)
    except Exception: pass
    return ids

def compute_method_confluence(p,w=None):
    ids=_confluence_method_ids(p,w)
    fam=[]
    for i in ids:
        f=_UFC_METHOD_FAMILIES.get(i)
        if f and f not in fam: fam.append(f)
    n=len(ids); fn=len(fam)
    return {'method_ids':ids,'method_count':n,'families':fam,'family_count':fn,
            'multi_method':n>=2,'multi_family':fn>=2,
            'historical_exact':_UFC_CONFLUENCE_HISTORY.get('exact',{}).get(str(n)),
            'historical_at_least':_UFC_CONFLUENCE_HISTORY.get('at_least',{}).get(str(n))}

_orig_predict_bout_confluence=predict_bout
def predict_bout(*args,**kwargs):
    out=_orig_predict_bout_confluence(*args,**kwargs)
    out['confluence']=compute_method_confluence(out,None)
    return out

# Decorate canonical method cards with confluence telemetry; never add a bet method.
_orig_method_cards_confluence=ufc_email_design.method_cards
def _confluence_cards(p,w):
    cards=list(_orig_method_cards_confluence(p,w))
    ids=[]
    for c in cards:
        i=str(c.get('id') or '').upper().strip()
        if i in _UFC_METHOD_FAMILIES and i not in ids: ids.append(i)
    fam=[]
    for i in ids:
        f=_UFC_METHOD_FAMILIES.get(i)
        if f and f not in fam:fam.append(f)
    p['confluence']={'method_ids':ids,'method_count':len(ids),'families':fam,'family_count':len(fam),
                     'multi_method':len(ids)>=2,'multi_family':len(fam)>=2,
                     'historical_exact':_UFC_CONFLUENCE_HISTORY.get('exact',{}).get(str(len(ids))),
                     'historical_at_least':_UFC_CONFLUENCE_HISTORY.get('at_least',{}).get(str(len(ids)))}
    return cards
ufc_email_design.method_cards=_confluence_cards

# Add confluence metadata to every official pick after the normal tracker decides whether it exists.
_orig_ufc_picks_confluence=bet_tracker.ufc_picks
def _confluence_ufc_picks(event,preds):
    base=list(_orig_ufc_picks_confluence(event,preds))
    bysel={}
    for p in preds or []:
        c=p.get('confluence') or compute_method_confluence(p,None)
        fav=str(p.get('favorite') or '').strip()
        if fav:bysel[fav]=c
    for x in base:
        c=bysel.get(str(x.get('selection') or '').strip())
        if c is not None:x['confluence']=c
    return base
bet_tracker.ufc_picks=_confluence_ufc_picks
'''

def main():
    s=WATCHER.read_text()
    if 'UFC_CONFLUENCE_LAYER_V1' in s:
        print('confluence already installed');return
    marker='if __name__ == "__main__":'
    if marker not in s: marker="if __name__ == '__main__':"
    if marker not in s: raise RuntimeError('watcher main marker missing')
    addon=ADDON.format(hist=repr(HIST))
    WATCHER.write_text(s.replace(marker,addon+'\n'+marker,1))
    print('confluence layer patched')

if __name__=='__main__':main()
