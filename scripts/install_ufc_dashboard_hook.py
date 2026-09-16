#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import py_compile
import shutil

TARGET = Path('/home/anestishkurti92/ufc-predictor-v1/ufc_email_watcher.py')
ANCHOR = 'sports_publish.safe_call(sports_publish.publish_ufc,events,preds_by_event,globals())'
MARKER = '# UFC_PUBLIC_DASHBOARD_HOOK'


def main() -> None:
    text = TARGET.read_text()
    if MARKER in text:
        py_compile.compile(str(TARGET), doraise=True)
        print('UFC dashboard post-publish hook already installed')
        return

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    installed = False

    for line in lines:
        out.append(line)
        if line.strip() != ANCHOR:
            continue

        indent = line[: len(line) - len(line.lstrip())]
        hook = [
            f'{indent}{MARKER}\n',
            f'{indent}try:\n',
            f'{indent}    import ufc_public_page\n',
            f'{indent}    ufc_public_page.safe_enhance(sports_publish)\n',
            f'{indent}except Exception as exc:\n',
            f'{indent}    print("UFC_PUBLIC_DASHBOARD_HOOK_ERROR", type(exc).__name__, str(exc))\n',
        ]
        out.extend(hook)
        installed = True

    if not installed:
        raise SystemExit('UFC publish anchor not found; refusing to modify watcher')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = TARGET.with_name(f'{TARGET.name}.pre-public-dashboard-hook.{stamp}.bak')
    shutil.copy2(TARGET, backup)

    tmp = TARGET.with_name(TARGET.name + '.tmp-dashboard-hook')
    tmp.write_text(''.join(out))
    py_compile.compile(str(tmp), doraise=True)
    tmp.replace(TARGET)
    py_compile.compile(str(TARGET), doraise=True)

    print(f'UFC dashboard post-publish hook installed; backup={backup}')


if __name__ == '__main__':
    main()
