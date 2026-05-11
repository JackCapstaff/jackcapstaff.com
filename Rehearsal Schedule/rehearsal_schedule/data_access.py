"""Database access helpers for schedules (read-only helpers used during migration).

This module provides simple functions to load schedules from the DB and return
the same JSON-shaped dict the app expects. Writes are intentionally kept out
for now; we'll add writers with optimistic locking later.
"""
from typing import Dict, Any
import os
import json
import datetime
from numbers import Number

try:
    from db import get_session
    import models
except Exception:
    # If imports fail (no DB configured), expose a sentinel to let callers know
    get_session = None  # type: ignore
    models = None  # type: ignore


def db_available() -> bool:
    return get_session is not None


def schedule_to_dict(sched_row: models.Schedule) -> Dict[str, Any]:
    # Convert Schedule SQL row to the expected JSON shape
    d: Dict[str, Any] = {}
    d.update(sched_row.meta or {})
    d.setdefault('id', sched_row.id)
    d.setdefault('ensemble_id', sched_row.ensemble_id)
    d.setdefault('name', sched_row.name)
    d.setdefault('status', sched_row.status)
    d.setdefault('created_by', sched_row.created_by)
    d.setdefault('created_at', int(sched_row.created_at) if sched_row.created_at else None)
    d.setdefault('updated_at', int(sched_row.updated_at) if sched_row.updated_at else None)
    d.setdefault('G', sched_row.G or 5)
    d.setdefault('generated_at', int(sched_row.generated_at) if sched_row.generated_at else None)
    d.setdefault('published_at', int(sched_row.published_at) if sched_row.published_at else None)
    d.setdefault('followup_notifications', sched_row.followup_notifications or {})

    # rehearsals — normalize keys to the same shape the JS frontend expects
    rehearsals = []
    for r in sorted((sched_row.rehearsals or []), key=lambda x: (x.rehearsal_num or 0)):
        base = {
            'Rehearsal': int(r.rehearsal_num) if r.rehearsal_num is not None else None,
            'Date': r.date,
            'Start': r.start_time,
            'End': r.end_time,
            'Break': r.break_minutes,
            'Section': r.section or (r.raw or {}).get('Section'),
            # Use the exact 'Event Type' key the app checks for
            'Event Type': r.event_type or (r.raw or {}).get('Event_type') or (r.raw or {}).get('Event Type') or 'Rehearsal',
            # include original raw properties so nothing is lost
            **(r.raw or {}),
        }
        # Ensure legacy flag is present
        if 'Include in allocation' not in base:
            base['Include in allocation'] = (r.raw or {}).get('Include in allocation', 'Y')
        rehearsals.append(base)
    d['rehearsals'] = rehearsals

    # timed items — normalize types and include meta
    timed = []
    for t in sorted((sched_row.timed_items or []), key=lambda x: ((x.rehearsal_num or 0), (x.ordering or 0))):
        timed.append({
            'Rehearsal': int(t.rehearsal_num) if t.rehearsal_num is not None else None,
            'Order': int(t.ordering) if t.ordering is not None else None,
            'Work': t.work_id,
            'Title': t.title,
            'Start': t.start,
            'End': t.end,
            **(t.meta or {}),
        })
    d['timed'] = timed

    # attendance (build mapping of user->responses for each rehearsal)
    attendance_map = {}
    for a in (sched_row.audit_entries or []):
        # skip audit when building attendance
        pass
    # simpler: return empty attendance dict if none
    d['attendance'] = {}

    # audit log
    audit = []
    for e in sorted((sched_row.audit_entries or []), key=lambda x: x.ts or 0):
        audit.append({
            'timestamp': int(e.ts) if e.ts else None,
            'action': e.action,
            'description': e.description,
            'actor_id': e.actor_id,
            'actor_email': e.actor_email,
            'actor_name': e.actor_name,
            'meta': e.meta,
        })
    d['audit_log'] = audit

    return d


def get_schedule(schedule_id: str) -> Dict[str, Any] | None:
    """Return schedule as JSON-shaped dict from DB or None if not found/DB unavailable."""
    if not db_available():
        return None
    session = get_session()
    try:
        sched = session.query(models.Schedule).filter_by(id=schedule_id).first()
        if not sched:
            return None
        # eager-load relationships
        _ = list(sched.rehearsals or [])
        _ = list(sched.timed_items or [])
        _ = list(sched.audit_entries or [])
        result = schedule_to_dict(sched)
        # Debug: print counts so server logs show what was returned from DB
        try:
            print(f"[DB ACCESS] schedule={schedule_id} rehearsals={len(result.get('rehearsals', []))} timed={len(result.get('timed', []))} audit={len(result.get('audit_log', []))}")
        except Exception:
            pass
        return result
    finally:
        session.close()


def _parse_ts(val):
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


def _sanitize_for_json(obj):
    """Recursively convert non-JSON-serializable objects into JSON-friendly types.

    - datetime/date/time -> ISO string
    - numbers, bool, None, str -> unchanged
    - dict/list -> recurse
    - fallback: convert to string
    """
    if obj is None:
        return None
    if isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, Number):
        # includes int, float
        return obj
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # keys must be strings for JSON
            out[str(k)] = _sanitize_for_json(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    # fallback for unknown types (e.g., numpy types, time objects) -> str
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


def write_schedule(sched_obj: Dict[str, Any]) -> bool:
    """Upsert a schedule dict into the DB. Returns True on success.

    This performs a full replace of rehearsals, timed_items, and audit entries
    for the given schedule id.
    """
    if not db_available():
        raise RuntimeError("DB not available")

    sid = sched_obj.get('id')
    if not sid:
        raise ValueError('schedule missing id')

    session = get_session()
    try:
        sched_row = session.query(models.Schedule).filter_by(id=sid).first()
        now = int(time.time()) if 'time' in globals() else None
        if not sched_row:
            sched_row = models.Schedule(id=sid)
            session.add(sched_row)

        # Scalars
        sched_row.ensemble_id = sched_obj.get('ensemble_id')
        sched_row.name = sched_obj.get('name')
        sched_row.status = sched_obj.get('status', 'draft')
        sched_row.created_by = sched_obj.get('created_by')
        created_at = _parse_ts(sched_obj.get('created_at'))
        if created_at:
            sched_row.created_at = created_at
        updated_at = _parse_ts(sched_obj.get('updated_at')) or int(time.time())
        sched_row.updated_at = updated_at
        sched_row.G = int(sched_obj.get('G') or 5)
        sched_row.generated_at = _parse_ts(sched_obj.get('generated_at'))
        sched_row.published_at = _parse_ts(sched_obj.get('published_at'))
        sched_row.followup_notifications = sched_obj.get('followup_notifications') or {}
        # Meta: store other top-level keys not part of structured blobs
        meta = {k: v for k, v in sched_obj.items() if k not in ('works', 'rehearsals', 'allocation', 'schedule', 'timed', 'timed_history', 'audit_log', 'attendance')}
        # Persist `works` inside meta so DB-backed reads return the works table used by allocation
        meta['works'] = _sanitize_for_json(sched_obj.get('works') or [])
        # Persist allocation/schedule/timed so DB-backed workflows (allocation -> generate -> timed)
        # can operate without relying on file storage backing.
        if 'allocation' in sched_obj:
            meta['allocation'] = _sanitize_for_json(sched_obj.get('allocation') or [])
        if 'schedule' in sched_obj:
            meta['schedule'] = _sanitize_for_json(sched_obj.get('schedule') or [])
        if 'timed' in sched_obj:
            meta['timed'] = _sanitize_for_json(sched_obj.get('timed') or [])
        if 'timed_history' in sched_obj:
            meta['timed_history'] = _sanitize_for_json(sched_obj.get('timed_history') or [])
        # Persist common helper column lists if present
        if 'works_cols' in sched_obj:
            meta['works_cols'] = _sanitize_for_json(sched_obj.get('works_cols'))
        if 'rehearsals_cols' in sched_obj:
            meta['rehearsals_cols'] = _sanitize_for_json(sched_obj.get('rehearsals_cols'))
        sched_row.meta = meta

        # Replace rehearsals (sanitize raw payloads to avoid JSON serialization errors)
        session.query(models.Rehearsal).filter_by(schedule_id=sid).delete()
        for r in (sched_obj.get('rehearsals') or []):
            try:
                rehearsal_num = int(r.get('Rehearsal')) if isinstance(r, dict) and r.get('Rehearsal') is not None else None
            except Exception:
                rehearsal_num = None
            raw_val = r if isinstance(r, dict) else {}
            sanitized_raw = _sanitize_for_json(raw_val)
            rr = models.Rehearsal(
                schedule_id=sid,
                rehearsal_num=rehearsal_num,
                date=(str(r.get('Date')) if isinstance(r, dict) and r.get('Date') is not None else None),
                start_time=(str(r.get('Start')) if isinstance(r, dict) and r.get('Start') is not None else None),
                end_time=(str(r.get('End')) if isinstance(r, dict) and r.get('End') is not None else None),
                break_minutes=(r.get('Break') if isinstance(r, dict) else None),
                section=(r.get('Section') if isinstance(r, dict) else None),
                event_type=(r.get('Event Type') or r.get('Event_type') if isinstance(r, dict) else None),
                raw=sanitized_raw,
            )
            session.add(rr)

        # Replace timed_items
        session.query(models.TimedItem).filter_by(schedule_id=sid).delete()
        for t in (sched_obj.get('timed') or []):
            if not isinstance(t, dict):
                continue
            try:
                rehearsal_num = int(t.get('Rehearsal')) if t.get('Rehearsal') is not None else None
            except Exception:
                rehearsal_num = None
            try:
                ordering = int(t.get('Order')) if t.get('Order') is not None else None
            except Exception:
                ordering = None
            meta_val = t if isinstance(t, dict) else {}
            sanitized_meta = _sanitize_for_json(meta_val)
            ti = models.TimedItem(
                schedule_id=sid,
                rehearsal_num=rehearsal_num,
                ordering=ordering,
                work_id=(str(t.get('Work')) if t.get('Work') is not None else None),
                title=t.get('Title'),
                start=(str(t.get('Start')) if t.get('Start') is not None else None),
                end=(str(t.get('End')) if t.get('End') is not None else None),
                meta=sanitized_meta,
            )
            session.add(ti)

        # Replace audit entries
        session.query(models.AuditEntry).filter_by(schedule_id=sid).delete()
        for e in (sched_obj.get('audit_log') or []):
            if not isinstance(e, dict):
                continue
            sanitized_e_meta = _sanitize_for_json(e)
            ae = models.AuditEntry(
                schedule_id=sid,
                ts=_parse_ts(e.get('timestamp') or e.get('ts')),
                action=e.get('action'),
                description=e.get('description') or e.get('action'),
                actor_id=e.get('actor_id'),
                actor_email=e.get('actor_email'),
                actor_name=e.get('actor_name'),
                meta=sanitized_e_meta,
            )
            session.add(ae)

        session.commit()
        print(f"[DB WRITE] schedule={sid} rehearsals={len(sched_obj.get('rehearsals') or [])} timed={len(sched_obj.get('timed') or [])} audit={len(sched_obj.get('audit_log') or [])}")
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
