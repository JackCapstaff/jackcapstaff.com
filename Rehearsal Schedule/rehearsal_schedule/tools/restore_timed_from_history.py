import json
import os
import sys
from tempfile import NamedTemporaryFile

def atomic_write(path, data):
    dirn = os.path.dirname(path)
    with NamedTemporaryFile('w', dir=dirn, delete=False, encoding='utf-8') as tf:
        json.dump(data, tf, indent=2, ensure_ascii=False)
        tmpname = tf.name
    os.replace(tmpname, path)

def restore(schedule_id):
    base = os.path.join('site', 'data', 'schedules')
    path = os.path.join(base, f'{schedule_id}.json')
    if not os.path.exists(path):
        print('Schedule file not found:', path)
        return 1
    with open(path, 'r', encoding='utf-8') as f:
        s = json.load(f)

    history = s.get('timed_history') or []
    if not history:
        print('No timed_history present in schedule file; nothing to restore.')
        return 1

    last = history[-1]
    timed_snapshot = last.get('timed')
    if not timed_snapshot:
        print('Last history entry has no timed snapshot')
        return 1

    print(f'Restoring {len(timed_snapshot)} timed rows from history timestamp {last.get("timestamp")}.')
    s['timed'] = timed_snapshot
    s['updated_at'] = int(__import__('time').time())

    atomic_write(path, s)
    print('Restore complete; schedule file updated at', path)
    return 0

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python restore_timed_from_history.py <schedule_id>')
        sys.exit(2)
    sid = sys.argv[1]
    sys.exit(restore(sid))
