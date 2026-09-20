#!/usr/bin/env python3
from __future__ import annotations
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ACTIVE={'MLS-R01V2','MLS-R02V2','MLS-R03','MLS-A02','MLS-A03','MLS-A05','MLS-A06'}
WATCH={'MLS-A04'}
RETIRED={'MLS-P01','MLS-R01','MLS-R02','MLS-A07'}

def method_ids(path):
    tree=ast.parse((ROOT/path).read_text(),filename=str(path))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='METHODS' for t in node.targets):
            value=ast.literal_eval(node.value)
            return {str(x['id']) for x in value}
    raise RuntimeError(f'METHODS not found in {path}')

def main():
    prospective=method_ids(Path('scripts/mls_prospective_shadow.py'))
    portfolio=method_ids(Path('scripts/mls_active_portfolio.py'))
    bankroll=method_ids(Path('scripts/mls_bankroll_replay.py'))

    expected_prospective=ACTIVE|WATCH
    errors=[]
    if prospective!=expected_prospective:
        errors.append(f'prospective IDs mismatch: got={sorted(prospective)} expected={sorted(expected_prospective)}')
    if portfolio!=ACTIVE:
        errors.append(f'active portfolio IDs mismatch: got={sorted(portfolio)} expected={sorted(ACTIVE)}')
    if bankroll!=ACTIVE:
        errors.append(f'bankroll IDs mismatch: got={sorted(bankroll)} expected={sorted(ACTIVE)}')
    for name,ids in [('prospective',prospective),('portfolio',portfolio),('bankroll',bankroll)]:
        bad=ids&RETIRED
        if bad: errors.append(f'{name} contains retired methods: {sorted(bad)}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('MLS method registry guard OK')
    print('ACTIVE',','.join(sorted(ACTIVE)))
    print('WATCH_ONLY',','.join(sorted(WATCH)))
    print('RETIRED',','.join(sorted(RETIRED)))

if __name__=='__main__':
    main()
