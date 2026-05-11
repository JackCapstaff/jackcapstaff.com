#!/usr/bin/env python3
"""
Migrate schedules from JSON files under `site/data/schedules/` into the DB.

Usage:
  python tools/migrate_json_to_db.py --dry-run
  python tools/migrate_json_to_db.py --commit

The script requires `DATABASE_URL` in the environment (or pass --db-url).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any

# Ensure project root (parent of this tools/ folder) is on sys.path so imports like
# `from db import get_session` work regardless of working directory.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from db import get_session, Base
import models


def load_json_files(path: str):
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    for fp in files:
        with open(fp, 'r', encoding='utf-8') as fh:
            yield fp, json.load(fh)


def map_schedule(json_obj: Dict[str, Any]):
    now = int(time.time())
    def parse_ts(val):
        if val is None:
            return None
        # if already numeric
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            s = val
            # handle trailing Z
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            try:
                # try fromisoformat
                from datetime import datetime

                dt = datetime.fromisoformat(s)
                return int(dt.timestamp())
            except Exception:
                try:
                    # fallback: try parsing as int-string
                    return int(s)
                except Exception:
                    return None

    sched = models.Schedule(
        id=json_obj.get('id') or json_obj.get('schedule_id') or f"sched_{now}",
        ensemble_id=json_obj.get('ensemble_id') or json_obj.get('ensemble') or None,
        name=json_obj.get('name') or json_obj.get('title') or 'Imported schedule',
        status=json_obj.get('status', 'draft'),
        created_by=json_obj.get('created_by'),
        created_at=parse_ts(json_obj.get('created_at') or json_obj.get('generated_at')) or now,
        updated_at=parse_ts(json_obj.get('updated_at') or json_obj.get('generated_at')) or now,
        G=json_obj.get('G', 5),
        generated_at=parse_ts(json_obj.get('generated_at')),
        published_at=parse_ts(json_obj.get('published_at')),
        followup_notifications=json_obj.get('followup_notifications') or {},
        meta={k: v for k, v in json_obj.items() if k not in ('works', 'rehearsals', 'allocation', 'schedule', 'timed', 'timed_history', 'audit_log', 'attendance')},
    )
    return sched


def map_rehearsals(schedule_id: str, rehearsals_raw):
    rows = []
    if not rehearsals_raw:
        return rows
    for idx, r in enumerate(rehearsals_raw):
        rehearsal_num = None
        if isinstance(r, dict):
            rehearsal_num = r.get('Rehearsal') or r.get('rehearsal') or (idx + 1)
            rows.append(models.Rehearsal(
                schedule_id=schedule_id,
                rehearsal_num=rehearsal_num,
                date=r.get('Date') or r.get('date'),
                start_time=r.get('Start') or r.get('Start_time') or r.get('start'),
                end_time=r.get('End') or r.get('end'),
                break_minutes=r.get('Break') or r.get('Break_minutes') or None,
                section=r.get('Section') or r.get('section'),
                event_type=r.get('Event_type') or r.get('event_type') or 'Rehearsal',
                raw=r,
            ))
        else:
            rows.append(models.Rehearsal(schedule_id=schedule_id, rehearsal_num=idx + 1))
    return rows


def map_timed_items(schedule_id: str, timed_raw):
    rows = []
    if not timed_raw:
        return rows
    for t in timed_raw:
        if not isinstance(t, dict):
            continue
        rehearsal_num = t.get('Rehearsal') or t.get('rehearsal') or t.get('Rehearsal_num')
        rows.append(models.TimedItem(
            schedule_id=schedule_id,
            rehearsal_num=rehearsal_num,
            ordering=t.get('Order') or t.get('ordering'),
            work_id=str(t.get('Work')) if t.get('Work') is not None else None,
            title=t.get('Title') or t.get('title') or None,
            start=t.get('Start') or t.get('start'),
            end=t.get('End') or t.get('end'),
            meta=t,
        ))
    return rows


def map_attendance(schedule_id: str, attendance_raw):
    rows = []
    if not attendance_raw:
        return rows
    for a in attendance_raw:
        if not isinstance(a, dict):
            continue
        def parse_ts(val):
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                s = val
                if s.endswith('Z'):
                    s = s[:-1] + '+00:00'
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(s)
                    return int(dt.timestamp())
                except Exception:
                    try:
                        return int(s)
                    except Exception:
                        return None

        rows.append(models.Attendance(
            schedule_id=schedule_id,
            rehearsal_num=a.get('Rehearsal') or a.get('rehearsal') or None,
            user_id=str(a.get('user_id') or a.get('user') or a.get('User')),
            status=a.get('status') or a.get('Status'),
            note=a.get('note') or a.get('Note'),
            responded_at=parse_ts(a.get('responded_at') or a.get('timestamp') or None),
        ))
    return rows


def map_audit(schedule_id: str, audit_raw):
    rows = []
    if not audit_raw:
        return rows
    for e in audit_raw:
        if not isinstance(e, dict):
            continue
        def parse_ts(val):
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                s = val
                if s.endswith('Z'):
                    s = s[:-1] + '+00:00'
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(s)
                    return int(dt.timestamp())
                except Exception:
                    try:
                        return int(s)
                    except Exception:
                        return None

        rows.append(models.AuditEntry(
            schedule_id=schedule_id,
            ts=parse_ts(e.get('timestamp') or e.get('ts') or None),
            action=e.get('action'),
            description=e.get('description') or e.get('action') or None,
            actor_id=e.get('actor_id'),
            actor_email=e.get('actor_email'),
            actor_name=e.get('actor_name'),
            meta=e,
        ))
    return rows


def migrate_one(fp: str, obj: Dict[str, Any], session, commit: bool = False):
    schedule = map_schedule(obj)
    rehearsals = map_rehearsals(schedule.id, obj.get('rehearsals'))
    timed = map_timed_items(schedule.id, obj.get('timed'))
    attendance = map_attendance(schedule.id, obj.get('attendance'))
    audit = map_audit(schedule.id, obj.get('audit_log') or obj.get('audit'))

    print(f"File: {fp}")
    print(f"  schedule id: {schedule.id}, rehearsals: {len(rehearsals)}, timed: {len(timed)}, attendance: {len(attendance)}, audit: {len(audit)}")

    if not commit:
        return {'schedule': schedule, 'rehearsals': rehearsals, 'timed': timed, 'attendance': attendance, 'audit': audit}

    # persist
    session.add(schedule)
    for r in rehearsals:
        session.add(r)
    for t in timed:
        session.add(t)
    for a in attendance:
        session.add(a)
    for e in audit:
        session.add(e)

    return {'schedule': schedule, 'rehearsals': rehearsals, 'timed': timed, 'attendance': attendance, 'audit': audit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='site/data/schedules', help='Directory with JSON schedules')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--commit', action='store_true', help='Write to DB (default: dry-run)')
    parser.add_argument('--db-url', help='Optional DB URL override')
    args = parser.parse_args()

    if args.db_url:
        os.environ['DATABASE_URL'] = args.db_url

    commit = args.commit
    files = list(glob.glob(os.path.join(args.data_dir, '*.json')))
    if not files:
        print('No schedule JSON files found in', args.data_dir)
        return

    if commit:
        session = get_session()
        try:
            for fp, obj in load_json_files(args.data_dir):
                migrate_one(fp, obj, session, commit=True)
            session.commit()
            print('Committed all schedules to DB')
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    else:
        # dry-run summary
        for fp, obj in load_json_files(args.data_dir):
            migrate_one(fp, obj, None, commit=False)
        print('\nDry-run complete. To write to DB re-run with --commit')


if __name__ == '__main__':
    main()
