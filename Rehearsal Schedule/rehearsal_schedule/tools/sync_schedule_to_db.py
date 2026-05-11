#!/usr/bin/env python3
"""Sync a schedule JSON file into the DB by calling data_access.write_schedule().

Usage:
  python tools/sync_schedule_to_db.py <schedule_id>

Example:
  python tools/sync_schedule_to_db.py sched_d9bbc91f23
"""
import sys
import os
import json
from pathlib import Path

# Ensure project root is on sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from data_access import db_available, write_schedule

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python tools/sync_schedule_to_db.py <schedule_id>')
        sys.exit(2)
    sid = sys.argv[1]
    schedule_path = Path(project_root) / 'site' / 'data' / 'schedules' / f"{sid}.json"
    if not schedule_path.exists():
        print('Schedule JSON not found:', schedule_path)
        sys.exit(1)
    with open(schedule_path, 'r', encoding='utf-8') as fh:
        s = json.load(fh)
    if not db_available():
        print('DB not available. Ensure DATABASE_URL is set and restart the app.')
        sys.exit(1)
    try:
        ok = write_schedule(s)
        if ok:
            print('Synced schedule to DB:', sid)
        else:
            print('write_schedule returned False')
    except Exception as e:
        print('Error while syncing:', e)
        raise
