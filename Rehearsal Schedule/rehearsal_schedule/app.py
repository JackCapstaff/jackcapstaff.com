import os
import re
import json
import math
import html
import subprocess
import importlib.util
import uuid
import time
import requests
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import datetime as _dt

import numpy as np
import pandas as pd
import io
import threading
import csv
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort, send_file, session, Response
from flask_caching import Cache
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup
import html as _html

# Tracing (OpenTelemetry)
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
except Exception:
    trace = None
    FlaskInstrumentor = None

if trace is not None:
    resource = Resource.create({"service.name": "rehearsal_web"})
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    _tracer = trace.get_tracer(__name__)
else:
    _tracer = None

# ----------------------------
# Config
# ----------------------------
SCRIPT1 = "1-Import-Time_per_work_per_rehearsal.py"
SCRIPT2 = "2-Orchestration_organisation.py"
SCRIPT3 = "3-Organised_rehearsal_with_time.py"
SCRIPT4 = "4 - Final Compile and PDF.py"  # optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "site", "data")
DATA_DIR = os.environ.get("DATA_DIR", DEFAULT_DATA_DIR)
DATA_DIR = os.path.abspath(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
SCHEDULES_DIR = os.path.join(DATA_DIR, "schedules")
os.makedirs(SCHEDULES_DIR, exist_ok=True)

# In-memory timers for attendance batching: keyed by (schedule_id, user_id)
_attendance_pending_timers = {}


def _attendance_schedule_key(schedule_id, user_id):
    return f"{schedule_id}::{user_id}"


def _send_pending_attendance_notifications(schedule_id, user_id):
    """Send accumulated attendance actions for a single user to admins and clear pending list.
    This function is intended to be called from a background Timer or synchronously.
    """
    try:
        s = load_schedule(schedule_id)
        if not s:
            return

        pending = (s.get("attendance_notifications") or {}).get(str(user_id)) or {}
        items = pending.get("items", [])
        if not items:
            return

        # Compose summary email
        ensemble = get_ensemble_by_id(s.get("ensemble_id")) or {}
        ensemble_name = ensemble.get("name") or s.get("ensemble_id")
        member = next((u for u in load_users() if u.get("id") == user_id), {})
        member_name = user_display_name(member)

        html_lines = [f"<p>{member_name} performed the following attendance actions:</p>", "<ul>"]
        for it in items:
            reh = it.get("rehearsal")
            status = html.escape(it.get("status") or "")
            note = html.escape(it.get("note") or "")
            ts = _dt.datetime.utcfromtimestamp(it.get("ts", 0)).isoformat() if it.get("ts") else ""
            html_lines.append(f"<li>Rehearsal {reh}: <strong>{status}</strong> {('— ' + note) if note else ''} <em>({ts})</em></li>")
        html_lines.append("</ul>")
        html_body = "\n".join(html_lines)

        # Send to ensemble and global admins
        ensemble_admins = get_ensemble_admin_emails(s.get("ensemble_id")) or []
        global_admins = get_global_admin_emails() or []
        admin_emails = sorted(set(ensemble_admins + global_admins))
        if admin_emails:
            subject = f"Attendance updates: {member_name} — {ensemble_name}"
            brevo_send_email(admin_emails, subject, html_body, bcc_mode=True)

        # Clear pending for this user
        notif = s.get("attendance_notifications") or {}
        if str(user_id) in notif:
            del notif[str(user_id)]
            s["attendance_notifications"] = notif
            s["updated_at"] = int(time.time())
            try:
                save_schedule(s)
            except Exception:
                pass
    finally:
        # Clean up in-memory timer
        key = _attendance_schedule_key(schedule_id, user_id)
        t = _attendance_pending_timers.pop(key, None)
        try:
            if t and hasattr(t, "cancel"):
                t.cancel()
        except Exception:
            pass


def _queue_attendance_notification(schedule_id, user_id, rehearsal_num, status, note):
    """Add an attendance action to pending notifications for a user and (re)schedule the send.
    We wait 5 minutes after the last action before sending a batched summary.
    """
    now_ts = int(time.time())
    s = load_schedule(schedule_id)
    if not s:
        return

    notif = s.get("attendance_notifications") or {}
    user_key = str(user_id)
    entry = {"rehearsal": rehearsal_num, "status": status, "note": note, "ts": now_ts}
    if user_key not in notif:
        notif[user_key] = {"items": [entry], "scheduled_at": now_ts + 300}
    else:
        notif[user_key]["items"].append(entry)
        notif[user_key]["scheduled_at"] = now_ts + 300

    s["attendance_notifications"] = notif
    s["updated_at"] = now_ts
    try:
        save_schedule(s)
    except Exception:
        pass

    # (Re)start in-memory timer for this scheduled send
    key = _attendance_schedule_key(schedule_id, user_id)
    prev = _attendance_pending_timers.get(key)
    if prev:
        try:
            prev.cancel()
        except Exception:
            pass

    t = threading.Timer(300, lambda: _send_pending_attendance_notifications(schedule_id, user_id))
    _attendance_pending_timers[key] = t
    t.daemon = True
    t.start()


def _flush_user_pending_notifications(user_id: str):
    """Find any schedules with pending attendance_notifications for `user_id` and send them now."""
    try:
        for sid in list_schedule_ids():
            s = load_schedule(sid)
            if not s:
                continue
            notif = (s.get("attendance_notifications") or {}).get(str(user_id))
            if notif and notif.get("items"):
                try:
                    _send_pending_attendance_notifications(sid, user_id)
                except Exception:
                    # best-effort: keep trying others
                    pass
    except Exception:
        pass


# ----------------------------
# App
# ----------------------------
app = Flask(__name__)
if FlaskInstrumentor is not None:
    try:
        FlaskInstrumentor().instrument_app(app)
    except Exception:
        pass
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")  # set in Azure App Settings

# Cache configuration (15 minutes for Outlook calendar data)
app.config['CACHE_TYPE'] = 'simple'
app.config['CACHE_DEFAULT_TIMEOUT'] = 900  # 15 minutes
cache = Cache(app)


@app.post('/internal/process_due_notifications')
def internal_process_due_notifications():
    """Process any due attendance notifications across all schedules.
    Protected by edit token: call with ?token=<EDIT_TOKEN> or X-Edit-Token header.
    Intended to be invoked by a scheduler (Heroku Scheduler or similar).
    """
    _require_edit_token_or_abort()
    now_ts = int(time.time())
    processed = 0
    try:
        for sid in list_schedule_ids():
            s = load_schedule(sid)
            if not s:
                continue
            notif = s.get("attendance_notifications") or {}
            for user_key, data in list(notif.items()):
                scheduled_at = data.get("scheduled_at") or 0
                if scheduled_at and int(scheduled_at) <= now_ts:
                    try:
                        _send_pending_attendance_notifications(sid, int(user_key))
                        processed += 1
                    except Exception:
                        pass
        return jsonify({"ok": True, "processed": processed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# If PROJECT_FILE is not explicitly set, store it in the persistent DATA_DIR.
PROJECT_FILE = os.environ.get("PROJECT_FILE", os.path.join(DATA_DIR, "project.json"))
EDIT_TOKEN = os.environ.get("EDIT_TOKEN", "changeme")  # set in Azure App Settings!
DEFAULT_G = int(os.environ.get("G_MINUTES", "5"))

TIMED_XLSX_OUT = "timed_rehearsal.xlsx"  # for script4 compatibility

ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()

# Instrument options (orchestral + brass band core list)
INSTRUMENT_OPTIONS = [
    "Violin", "Viola", "Cello", "Double Bass", "Harp",
    "Flute", "Piccolo", "Alto Flute", "Bass Flute", "Oboe", "Cor Anglais",
    "Bassoon", "Contrabassoon", "Bb Clarinet", "A Clarinet", "Eb Clarinet", "Bass Clarinet",
    "Saxophone - Soprano", "Saxophone - Alto", "Saxophone - Tenor", "Saxophone - Baritone",
    "Trumpet", "Cornet", "Soprano Cornet (Eb)", "Flugelhorn", "French Horn",
    "Tenor Horn (Eb)", "Baritone Horn", "Euphonium", "Tenor Trombone", "Bass Trombone",
    "Tuba (Eb)", "Tuba (Bb)", "Sousaphone",
    "Percussion", "Timpani", "Drum Kit",
    "Piano/Keyboard", "Organ", "Guitar", "Bass Guitar", "Voice",
    "Other / Not listed",
]

# Outbound email (Brevo) configuration
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
BREVO_FROM_EMAIL = os.environ.get("BREVO_FROM_EMAIL", "rehearsals@jackcapstaff.com").strip()
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "Rehearsal Schedule").strip()
BREVO_PRIMARY_TO = os.environ.get("BREVO_PRIMARY_TO", BREVO_FROM_EMAIL).strip()
AUDIT_LOG_LIMIT = 500


def _outlook_calendar_config():
    tenant_id = (os.environ.get("OUTLOOK_GRAPH_TENANT_ID") or "").strip()
    client_id = (os.environ.get("OUTLOOK_GRAPH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("OUTLOOK_GRAPH_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("OUTLOOK_GRAPH_REFRESH_TOKEN") or "").strip()
    user_principal_name = (os.environ.get("OUTLOOK_GRAPH_USER_PRINCIPAL_NAME") or "").strip()
    calendar_id = (os.environ.get("OUTLOOK_GRAPH_CALENDAR_ID") or "").strip()
    timezone_name = (os.environ.get("OUTLOOK_GRAPH_TIMEZONE") or "Europe/London").strip() or "Europe/London"

    if not (tenant_id and client_id and client_secret and refresh_token and user_principal_name):
        return None

    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "user_principal_name": user_principal_name,
        "calendar_id": calendar_id,
        "timezone": timezone_name,
    }


def _graph_access_token(config: dict) -> Optional[str]:
    token_url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    payload = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": config["refresh_token"],
        "scope": "https://graph.microsoft.com/Calendars.Read.Shared offline_access",
    }

    try:
        response = requests.post(token_url, data=payload, timeout=20)
        response.raise_for_status()
        return (response.json() or {}).get("access_token")
    except Exception:
        app.logger.exception("Graph token request failed in rehearsal scheduler")
        return None


@cache.cached(timeout=900, key_prefix='outlook_events_dashboard')
def _outlook_events_for_dashboard() -> List[dict]:
    config = _outlook_calendar_config()
    if not config:
        return []

    access_token = _graph_access_token(config)
    if not access_token:
        return []

    start_dt = _dt.datetime.utcnow().replace(microsecond=0)
    end_dt = start_dt + _dt.timedelta(days=365)

    base_user = config["user_principal_name"]
    calendar_id = config.get("calendar_id") or ""
    if calendar_id:
        endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/calendarView"
    else:
        endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendarView"

    params = {
        "startDateTime": start_dt.isoformat() + "Z",
        "endDateTime": end_dt.isoformat() + "Z",
        "$select": "id,subject,start,end,location,bodyPreview,sensitivity,isAllDay,isCancelled",
        "$orderby": "start/dateTime",
        "$top": "200",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Prefer": f"outlook.timezone=\"{config['timezone']}\"",
    }

    output = []
    next_url = endpoint
    try:
        while next_url:
            response = requests.get(next_url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json() or {}

            for row in payload.get("value", []):
                if row.get("isCancelled"):
                    continue

                start_info = row.get("start") or {}
                start_raw = (start_info.get("dateTime") or "").strip()
                if not start_raw:
                    continue

                # Keep date handling resilient across ISO forms with/without timezone offsets.
                try:
                    dt_value = _dt.datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    if dt_value.tzinfo is not None:
                        dt_value = dt_value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                except Exception:
                    try:
                        dt_value = pd.to_datetime(start_raw).to_pydatetime()
                        if dt_value.tzinfo is not None:
                            dt_value = dt_value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                    except Exception:
                        continue

                sensitivity = (row.get("sensitivity") or "").strip().lower()
                is_private = sensitivity == "private"
                subject = (row.get("subject") or "").strip() or "Outlook event"
                location = ((row.get("location") or {}).get("displayName") or "").strip()
                body_preview = (row.get("bodyPreview") or "").strip()

                if is_private:
                    subject = "Private appointment"
                    location = ""
                    body_preview = ""

                source_id = (row.get("id") or "").strip()
                synthetic_id = source_id or uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{subject}|{dt_value.isoformat()}|{location}",
                ).hex

                output.append({
                    "type": "concert",
                    "id": f"outlook_{synthetic_id}",
                    "concert_id": f"outlook_{synthetic_id}",
                    "ensemble_id": "outlook-calendar",
                    "ensemble_name": "Outlook Calendar",
                    "title": subject,
                    "date": dt_value.date().isoformat(),
                    "time": "" if row.get("isAllDay") else dt_value.strftime("%H:%M"),
                    "venue": location,
                    "uniform": "",
                    "programme": "",
                    "other_info": body_preview,
                    "schedule_id": None,
                    "status": "scheduled",
                    "is_private": is_private,
                })

            next_url = payload.get("@odata.nextLink")
            params = None
    except Exception:
        app.logger.exception("Graph calendar fetch failed in rehearsal scheduler")
        return []

    output.sort(key=lambda item: (item.get("date") or "", item.get("time") or ""))
    return output

# Custom Jinja filter for UK date format with ordinal suffix
@app.template_filter('uk_date_format')
def uk_date_format(date_value):
    """Format date as 'Sunday 18th January, 2026'"""
    if not date_value or pd.isna(date_value):
        return ""
    
    try:
        # Convert to datetime if it isn't already
        if isinstance(date_value, str):
            dt = pd.to_datetime(date_value)
        elif hasattr(date_value, 'to_pydatetime'):
            dt = date_value.to_pydatetime()
        else:
            dt = date_value
        
        # Get day with ordinal suffix
        day = dt.day
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        # Format: Sunday 18th January, 2026
        return dt.strftime(f'%A {day}{suffix} %B, %Y')
    except:
        return str(date_value)

@app.template_filter('timestamp_to_date')
def timestamp_to_date(timestamp):
    """Convert Unix timestamp to human-readable date"""
    if not timestamp:
        return ""
    try:
        dt = _dt.datetime.fromtimestamp(int(timestamp))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return str(timestamp)

@app.template_filter('format_duration')
def format_duration(seconds):
    """Format seconds as MM:SS"""
    if seconds is None or seconds == 0:
        return "0:00"
    try:
        seconds = int(seconds)
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}:{secs:02d}"
    except:
        return str(seconds)


@app.template_filter('format_programme')
def format_programme(programme_text):
    """Format programme text with sets as columns and preserved line breaks"""
    if not programme_text:
        return ""
    
    # Split by set markers (e.g., "Set 1:", "Set 2:", etc.)
    set_pattern = re.compile(r'^(Set\s+\d+:?\s*)', re.IGNORECASE | re.MULTILINE)
    parts = set_pattern.split(programme_text)
    
    # Group into sets
    sets = []
    current_set = None
    current_content = []
    
    for i, part in enumerate(parts):
        if set_pattern.match(part):
            # Save previous set if exists
            if current_set is not None:
                sets.append((current_set, '\n'.join(current_content).strip()))
            current_set = part.strip()
            current_content = []
        elif part.strip():
            current_content.append(part.strip())
    
    # Add last set
    if current_set:
        sets.append((current_set, '\n'.join(current_content).strip()))
    
    # If no sets found, treat as single block
    if not sets:
        escaped = programme_text.replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
        return Markup(f'<div style="background:white; padding:8px; border-radius:4px; font-size:0.9rem; border:1px solid #e0e0e0;">{escaped}</div>')
    
    # Render as columns
    columns_html = []
    for set_title, set_content in sets:
        escaped_content = set_content.replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
        columns_html.append(f'''
            <div style="flex:1; min-width:200px; background:white; padding:12px; border-radius:4px; border:1px solid #e0e0e0;">
                <div style="font-weight:bold; color:#3b82f6; margin-bottom:8px; border-bottom:2px solid #e0e0e0; padding-bottom:4px;">{set_title}</div>
                <div style="font-size:0.9rem; line-height:1.6;">{escaped_content}</div>
            </div>
        ''')
    
    return Markup(f'<div style="display:flex; gap:12px; flex-wrap:wrap;">{" ".join(columns_html)}</div>')


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def format_human_date(date_value) -> str:
    """Format a date-like value as '16th March 2026'."""
    if not date_value:
        return ""
    try:
        dt = pd.to_datetime(date_value)
        day = dt.day
        month = dt.strftime('%B')
        year = dt.year
        return f"{_ordinal(day)} {month} {year}"
    except Exception:
        return str(date_value)


def schedule_path(schedule_id: str) -> str:
    return os.path.join(SCHEDULES_DIR, f"{schedule_id}.json")

def list_schedule_ids() -> list[str]:
    if not os.path.exists(SCHEDULES_DIR):
        return []
    return sorted(
        [fn[:-5] for fn in os.listdir(SCHEDULES_DIR) if fn.endswith(".json")]
    )

def load_schedule(schedule_id: str) -> dict:
    # Try DB first (if configured), otherwise fall back to file storage
    try:
        from data_access import get_schedule, db_available
    except Exception:
        get_schedule = None
        db_available = lambda: False

    if db_available():
        try:
            db_result = get_schedule(schedule_id)
        except Exception:
            db_result = None
        if db_result is not None:
            s = db_result
            print(f"[LOAD] schedule={schedule_id} source=DB rehearsals={len(s.get('rehearsals', []))} timed={len(s.get('timed', []))}")
        else:
            # Fallback to file storage if DB has no entry for this schedule
            p = schedule_path(schedule_id)
            s = read_json(p, None) or {}
            print(f"[LOAD] schedule={schedule_id} source=FILE (fallback)")
    else:
        p = schedule_path(schedule_id)
        s = read_json(p, None) or {}  # see safe read_json note below
        print(f"[LOAD] schedule={schedule_id} source=FILE (db disabled)")
    
    # Ensure the schedule has an id field (derive from filename if missing)
    if "id" not in s:
        s["id"] = schedule_id
    
    # Ensure all rehearsals have the "Include in allocation" column
    if "rehearsals" in s:
        s["rehearsals"] = ensure_include_in_allocation_column(s["rehearsals"])
    
    # Ensure all rehearsals have the "Section" column
    if "rehearsals" in s:
        s["rehearsals"] = ensure_section_column(s["rehearsals"])
    
    # Ensure all rehearsals have the "Event Type" column
    if "rehearsals" in s:
        s["rehearsals"] = ensure_event_type_column(s["rehearsals"])
    
    # Ensure rehearsals_cols includes "Event Type", "Section", "Include in allocation" (for old schedules)
    if "rehearsals" in s and s["rehearsals"] and isinstance(s["rehearsals"], list) and len(s["rehearsals"]) > 0:
        # If rehearsals_cols doesn't exist or is incomplete, rebuild it from actual rehearsal data
        if "rehearsals_cols" not in s or not s["rehearsals_cols"]:
            # Use all columns from the first rehearsal row (if it's a dict)
            if isinstance(s["rehearsals"][0], dict):
                s["rehearsals_cols"] = list(s["rehearsals"][0].keys())
        else:
            cols = s["rehearsals_cols"]
            # Add missing columns that should always be present
            for required_col in ["Include in allocation", "Section", "Event Type"]:
                if required_col not in cols:
                    if required_col == "Event Type" and "Include in allocation" in cols:
                        # Insert "Event Type" after "Include in allocation"
                        idx = cols.index("Include in allocation") + 1
                        cols.insert(idx, "Event Type")
                    else:
                        cols.append(required_col)
            s["rehearsals_cols"] = cols

    # Ensure audit log fields exist
    if "audit_log" not in s or not isinstance(s.get("audit_log"), list):
        s["audit_log"] = []
    if "last_notified_at" not in s:
        s["last_notified_at"] = None
    if "attendance" not in s or not isinstance(s.get("attendance"), dict):
        s["attendance"] = {}
    if "conducting_logs" not in s or not isinstance(s.get("conducting_logs"), list):
        s["conducting_logs"] = []
    
    return s

def save_schedule(s: dict):
    if "id" not in s:
        raise ValueError("Schedule missing id")
    # Always write the canonical JSON file for backup and compatibility
    write_json_atomic(schedule_path(s["id"]), s)

    # If DB is available, also persist to Postgres (upsert). Errors here
    # should not prevent the JSON save, but we log them for debugging.
    try:
        from data_access import db_available, write_schedule
    except Exception:
        db_available = lambda: False
        write_schedule = None

    if db_available():
        try:
            write_schedule(s)
        except Exception as ex:
            print(f"[SAVE] DB write failed for {s.get('id')}: {ex}")

def migrate_unnamed_columns_to_day():
    """Rename 'Unnamed: 2' to 'Day' in all schedules"""
    schedule_ids = list_schedule_ids()
    migrated_count = 0
    
    for schedule_id in schedule_ids:
        schedule = load_schedule(schedule_id)
        modified = False
        
        # Migrate rehearsals_cols
        if "rehearsals_cols" in schedule:
            cols = schedule["rehearsals_cols"]
            if "Unnamed: 2" in cols:
                schedule["rehearsals_cols"] = ["Day" if c == "Unnamed: 2" else c for c in cols]
                modified = True
        
        # Migrate rehearsal objects
        if "rehearsals" in schedule:
            for rehearsal in schedule["rehearsals"]:
                if "Unnamed: 2" in rehearsal:
                    rehearsal["Day"] = rehearsal.pop("Unnamed: 2")
                    modified = True
        
        if modified:
            save_schedule(schedule)
            migrated_count += 1
    
    return migrated_count

def make_new_schedule(ensemble_id: str, name: str, created_by: str | None = None) -> dict:
    sid = f"sched_{uuid.uuid4().hex[:10]}"
    return {
        "id": sid,
        "ensemble_id": ensemble_id,
        "name": name or "Untitled",
        "status": "draft",          # draft | published
        "created_by": created_by,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "G": DEFAULT_G,
        "works": [],
        "rehearsals": [],
        "allocation": [],
        "schedule": [],
        "timed": [],
        "timed_history": [],
        "attendance": {},
        "audit_log": [],
        "conducting_logs": [],
        "last_notified_at": None,
        "generated_at": None,
    }

DEFAULT_SCHEDULE_ID_FILE = os.path.join(DATA_DIR, "default_schedule_id.txt")

def get_default_schedule_id() -> str | None:
    if not os.path.exists(DEFAULT_SCHEDULE_ID_FILE):
        return None
    try:
        return open(DEFAULT_SCHEDULE_ID_FILE, "r", encoding="utf-8").read().strip() or None
    except Exception:
        return None

def set_default_schedule_id(sid: str):
    with open(DEFAULT_SCHEDULE_ID_FILE, "w", encoding="utf-8") as f:
        f.write(sid)

def default_schedule():
    sid = get_default_schedule_id()
    if not sid:
        migrate_project_json_if_needed()
        sid = get_default_schedule_id()
    if not sid:  # type: ignore
        return {}, None
    return load_schedule(sid), sid  # type: ignore

def migrate_project_json_if_needed():
    # If schedules already exist, do nothing
    if list_schedule_ids():
        return

    # If old project.json exists, migrate it
    if os.path.exists(PROJECT_FILE):
        old = read_json(PROJECT_FILE, None)
        if isinstance(old, dict):
            ensembles = load_ensembles()
            default_ens = ensembles[0]["id"] if ensembles else "default"

            s = make_new_schedule(default_ens, "Migrated Schedule", created_by=None)
            # carry over old fields
            for k in ["G", "works", "rehearsals", "allocation", "schedule", "timed", "timed_history", "generated_at"]:
                if k in old:
                    s[k] = old[k]
            save_schedule(s)
            set_default_schedule_id(s["id"])
            return

    # Otherwise, create a blank default schedule
    ensembles = load_ensembles()
    default_ens = ensembles[0]["id"] if ensembles else "default"
    s = make_new_schedule(default_ens, "Default Schedule", created_by=None)
    # Audit: record that a default schedule was created during migration
    add_audit_entry(s, action="schedule_created", description="Default schedule created", actor=None, meta={})
    save_schedule(s)
    set_default_schedule_id(s["id"])




# ----------------------------
# JSON storage helpers (atomic writes)
# ----------------------------
def read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return default
            return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        # If file is corrupt/empty, fall back safely
        return default
    

def write_json_atomic(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_json_safe)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _json_safe(obj):
    # pandas Timestamp / datetime / date
    try:
        import pandas as pd
        if isinstance(obj, pd.Timestamp):
            # keep timezone if present, else ISO
            return obj.isoformat()
    except Exception:
        pass

    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()

    # numpy scalars
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            v = float(obj)
            # handle NaN/inf
            if v != v or v in (float("inf"), float("-inf")):
                return None
            return v
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
    except Exception:
        pass

    # fallback: string-ify unknown objects
    return str(obj)


def sanitize_df_records(records: List[dict]) -> List[dict]:
    """Convert NaN/inf values in a list of dicts to None (null in JSON)."""
    if not isinstance(records, list):
        return records
    
    import math
    def clean_value(val):
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return None
        if isinstance(val, dict):
            return {k: clean_value(v) for k, v in val.items()}
        if isinstance(val, list):
            return [clean_value(v) for v in val]
        return val

    result = []
    for row in records:
        if not isinstance(row, dict):
            result.append(row)
            continue
        sanitized = {k: clean_value(v) for k, v in row.items()}
        result.append(sanitized)
    return result


def ensure_include_in_allocation_column(rehearsals: List[dict]) -> List[dict]:
    """Ensure all rehearsals have 'Include in allocation' column, defaulting to 'Y'."""
    if not isinstance(rehearsals, list):
        return rehearsals
    
    if not rehearsals:
        return rehearsals
    
    # Add missing column with default value 'Y'
    for row in rehearsals:
        if "Include in allocation" not in row or pd.isna(row.get("Include in allocation")):
            row["Include in allocation"] = "Y"
    
    return rehearsals


def normalize_section_column(rehearsals: List[dict]) -> List[dict]:
    """Normalize 'Ensemble' column to 'Section' if present, then clean up.
    
    This allows Excel files to use either "Ensemble" or "Section" column names.
    If both exist, "Section" takes priority. If neither exists, defaults to "Full Ensemble".
    """
    if not isinstance(rehearsals, list):
        return rehearsals
    
    if not rehearsals:
        return rehearsals
    
    # Check if first row has "Ensemble" but not "Section"
    for row in rehearsals:
        if "Ensemble" in row and "Section" not in row:
            # Rename "Ensemble" to "Section"
            row["Section"] = row.pop("Ensemble")
        elif "Ensemble" in row and "Section" in row:
            # Both exist, keep "Section" and remove "Ensemble"
            row.pop("Ensemble")
    
    return rehearsals


def ensure_section_column(rehearsals: List[dict]) -> List[dict]:
    """Ensure all rehearsals have 'Section' column, defaulting to 'Full Ensemble'."""
    if not isinstance(rehearsals, list):
        return rehearsals
    
    if not rehearsals:
        return rehearsals
    
    # Add missing column with default value 'Full Ensemble'
    for row in rehearsals:
        if "Section" not in row or not row.get("Section") or pd.isna(row.get("Section")):
            row["Section"] = "Full Ensemble"
        else:
            # Clean up section value
            row["Section"] = str(row.get("Section", "Full Ensemble")).strip()
            if not row["Section"]:
                row["Section"] = "Full Ensemble"
    
    return rehearsals


def ensure_event_type_column(rehearsals: List[dict]) -> List[dict]:
    """Ensure all rehearsals have 'Event Type' column, defaulting to 'Rehearsal'.
    
    Automatically detects event type from alternative field names:
    - "event", "Event", "event type", "Event Type", "Event_Type", "event_type", etc.
    This allows users to have an 'event' field in their Excel import that auto-populates Event Type.
    """
    if not isinstance(rehearsals, list):
        return rehearsals
    
    if not rehearsals:
        return rehearsals
    
    # Possible alternate field names for event type (case-insensitive)
    ALTERNATE_FIELDS = ["event", "event type", "event_type", "eventtype"]
    
    # Add missing column with default value 'Rehearsal'
    for row in rehearsals:
        event_type_found = None
        
        # FIRST: Check for alternate field names (like "event", "Event") - prioritize these
        for key in row.keys():
            key_lower = key.lower().replace(" ", "").replace("_", "")
            if key_lower in ALTERNATE_FIELDS:
                value = row.get(key, "")
                # Check if value is not empty and not NaN
                if value is not None and value != "" and not pd.isna(value):
                    event_type_found = str(value).strip()
                    break
        
        # SECOND: If no alternate field found, check if Event Type is already set and valid
        if not event_type_found:
            existing_value = row.get("Event Type", "")
            if existing_value and not pd.isna(existing_value):
                event_type_found = str(existing_value).strip()
        
        # Now normalize the found value to standard values
        if event_type_found:
            event_lower = event_type_found.lower()
            if "concert" in event_lower:
                row["Event Type"] = "Concert"
            elif "contest" in event_lower:
                row["Event Type"] = "Contest"
            elif "sectional" in event_lower:
                row["Event Type"] = "Sectional"
            elif "rehearsal" in event_lower:
                row["Event Type"] = "Rehearsal"
            else:
                # Preserve custom event types
                row["Event Type"] = event_type_found
        else:
            # Default to Rehearsal
            row["Event Type"] = "Rehearsal"
    
    return rehearsals


def clean_timed_data(timed_data: List[dict]) -> List[dict]:
    """Clean timed data to ensure valid sections and sanitize NaN/inf values."""
    if not isinstance(timed_data, list):
        return timed_data
    
    if not timed_data:
        return timed_data
    
    result = []
    for row in timed_data:
        if not isinstance(row, dict):
            result.append(row)
            continue
        
        # Sanitize NaN/inf values
        sanitized = {}
        for key, value in row.items():
            if pd.isna(value) or (isinstance(value, float) and (value != value or value in (float("inf"), float("-inf")))):
                sanitized[key] = None
            else:
                sanitized[key] = value
        
        # Ensure Section column exists with default
        if "Section" not in sanitized or sanitized["Section"] is None or sanitized["Section"] == "":
            sanitized["Section"] = "Full Ensemble"
        else:
            # Clean up section value
            sanitized["Section"] = str(sanitized["Section"]).strip()
            if not sanitized["Section"]:
                sanitized["Section"] = "Full Ensemble"
        
        result.append(sanitized)
    
    return result


def upsert_concert_timed_rows(
    timed_data: List[dict],
    rehearsal_num: int,
    concert_info: dict,
    section: str = "Full Ensemble",
) -> List[dict]:
    """Update or insert timed rows for a concert rehearsal without wiping other edits."""
    if rehearsal_num is None:
        return clean_timed_data(timed_data)

    concert_id = concert_info.get("id")
    concert_date = concert_info.get("date")
    concert_time = concert_info.get("time") or concert_info.get("start_time")
    concert_title = concert_info.get("title") or "Concert"
    updated_any = False

    for row in timed_data:
        if int(row.get("Rehearsal", 0)) == int(rehearsal_num):
            row["Event Type"] = "Concert"
            if concert_id:
                row["concert_id"] = concert_id
            if concert_date:
                row["Date"] = concert_date
            if concert_time:
                row["Time in Rehearsal"] = concert_time
            row["Section"] = row.get("Section") or section or "Full Ensemble"
            if not row.get("Title"):
                row["Title"] = concert_title
            updated_any = True

    if not updated_any:
        timed_data.append({
            "Rehearsal": rehearsal_num,
            "Date": concert_date or "",
            "Title": concert_title,
            "Time in Rehearsal": concert_time or "19:00",
            "Break Start (HH:MM)": "",
            "Break End (HH:MM)": "",
            "Section": section or "Full Ensemble",
            "Event Type": "Concert",
            "concert_id": concert_id,
        })

    return clean_timed_data(timed_data)


def auto_number_rehearsals(rehearsals: List[dict]) -> List[dict]:
    """Auto-assign sequential Rehearsal numbers if missing or empty."""
    if not isinstance(rehearsals, list) or not rehearsals:
        return rehearsals
    
    # Check if Rehearsal column needs numbering
    df = pd.DataFrame(rehearsals)
    if "Rehearsal" not in df.columns:
        # Add Rehearsal column with sequential numbers
        df["Rehearsal"] = range(1, len(df) + 1)
    else:
        # Fill empty/NaN rehearsal numbers with sequential numbers
        empty_mask = df["Rehearsal"].astype(str).str.strip().eq("") | df["Rehearsal"].isna()
        if empty_mask.any():
            # Get existing numbers to avoid conflicts
            existing_nums = pd.to_numeric(df["Rehearsal"], errors="coerce").dropna().astype(int).unique()
            next_num = max(existing_nums.tolist() + [0]) + 1
            
            # Fill empty ones with sequential numbers
            fill_count = 0
            for idx in df[empty_mask].index:
                df.at[idx, "Rehearsal"] = str(next_num + fill_count)
                fill_count += 1
    
    return df.fillna("").to_dict(orient="records")


def users_path() -> str:
    return os.path.join(DATA_DIR, "users.json")


def ensembles_path() -> str:
    return os.path.join(DATA_DIR, "ensembles.json")


def memberships_path() -> str:
    return os.path.join(DATA_DIR, "memberships.json")


def load_users() -> List[dict]:
    return read_json(users_path(), [])


def save_users(users: List[dict]):
    write_json_atomic(users_path(), users)


def load_ensembles() -> List[dict]:
    ensembles = read_json(ensembles_path(), [])
    if not ensembles:
        # Seed a starter ensemble so the app is usable immediately.
        ensembles = [{"id": "default", "name": "Default Ensemble"}]
        write_json_atomic(ensembles_path(), ensembles)
    # Migrate legacy concert titles that include the placeholder {ensemble_name}
    migrated = False
    for ens in ensembles:
        if "concerts" in ens and isinstance(ens.get("concerts"), list):
            for c in ens.get("concerts", []):
                if c.get("title") and "{ensemble_name}" in c["title"]:
                    c["title"] = c["title"].replace("{ensemble_name}", ens.get("name", ""))
                    migrated = True
    if migrated:
        try:
            write_json_atomic(ensembles_path(), ensembles)
        except Exception:
            # If saving fails, continue without blocking load
            pass
    return ensembles


def save_ensembles(ensembles: List[dict]):
    write_json_atomic(ensembles_path(), ensembles)


def get_ensemble_by_id(ensemble_id: str) -> Optional[dict]:
    """Get a single ensemble by ID."""
    ensembles = load_ensembles()
    return next((e for e in ensembles if e.get("id") == ensemble_id), None)


def ensure_ensemble_concerts(ensembles: List[dict]) -> List[dict]:
    """Ensure each ensemble has a 'concerts' array."""
    for ens in ensembles:
        if "concerts" not in ens:
            ens["concerts"] = []
    return ensembles


def add_concert_to_ensemble(ensemble_id: str, concert_data: dict) -> dict:
    """Add a concert to an ensemble and save."""
    ensembles = load_ensembles()
    ensembles = ensure_ensemble_concerts(ensembles)
    
    ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
    if not ensemble:
        raise ValueError(f"Ensemble {ensemble_id} not found")
    
    # Create concert object with auto-generated fields
    title = concert_data.get("title")
    if title and "{ensemble_name}" in title:
        title = title.replace("{ensemble_name}", ensemble.get("name", ""))
    concert = {
        "id": f"concert_{uuid.uuid4().hex[:10]}",
        "title": title,
        "date": concert_data.get("date"),
        "time": concert_data.get("time"),
        "venue": concert_data.get("venue"),
        "uniform": concert_data.get("uniform"),
        "programme": concert_data.get("programme", ""),
        "other_info": concert_data.get("other_info", ""),
        "schedule_id": concert_data.get("schedule_id"),  # Optional link to rehearsal schedule
        "status": concert_data.get("status", "scheduled"),  # scheduled | completed | cancelled
        "created_at": int(time.time()),
    }
    
    ensemble["concerts"].append(concert)
    save_ensembles(ensembles)
    # Audit
    schedule_id = concert.get("schedule_id")
    if schedule_id:
        s = load_schedule(schedule_id)
        if s:
            add_audit_entry(s, action="concert_added", description=f"Concert added: {concert.get('title')}", actor=current_user(), meta={"concert_id": concert.get("id"), "date": concert.get("date"), "time": concert.get("time")})
            save_schedule(s)
    return concert


def _format_concert_title(ensemble_name: str, date_str: str) -> str:
    """Generate default concert title with ordinal date suffix."""
    try:
        dt = pd.to_datetime(date_str)
        day = dt.day
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        formatted_date = dt.strftime(f"%A %B {day}{suffix}")
        return f"{ensemble_name} - Concert - {formatted_date}"
    except Exception:
        return f"{ensemble_name} - Concert"


def update_concert(ensemble_id: str, concert_id: str, concert_data: dict) -> Optional[dict]:
    """Update a concert in an ensemble."""
    ensembles = load_ensembles()
    ensembles = ensure_ensemble_concerts(ensembles)
    
    ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
    if not ensemble:
        return None
    
    concert = next((c for c in ensemble.get("concerts", []) if c.get("id") == concert_id), None)
    if not concert:
        return None
    # Keep a copy of the previous concert state for audit diffs
    old_concert = dict(concert)
    # Track if date changed
    old_date = concert.get("date")
    new_date = concert_data.get("date", old_date)
    date_changed = old_date != new_date
    ensemble_name = ensemble.get("name", "Unknown Ensemble")

    # Recompute default title if the concert still uses the default pattern or no title was provided
    old_default_title = _format_concert_title(ensemble_name, old_date) if old_date else None
    new_default_title = _format_concert_title(ensemble_name, new_date) if new_date else None
    incoming_title = concert_data.get("title")
    should_update_title = (
        not incoming_title
        or (old_default_title and concert.get("title") == old_default_title)
    )
    
    # Update fields (preserve id and created_at)
    incoming_title = concert_data.get("title", concert.get("title"))
    if incoming_title and "{ensemble_name}" in incoming_title:
        incoming_title = incoming_title.replace("{ensemble_name}", ensemble_name)
    if should_update_title and new_default_title:
        concert["title"] = new_default_title
    else:
        concert["title"] = incoming_title
    concert["date"] = new_date
    concert["time"] = concert_data.get("time", concert["time"])
    concert["venue"] = concert_data.get("venue", concert["venue"])
    concert["uniform"] = concert_data.get("uniform", concert["uniform"])
    concert["programme"] = concert_data.get("programme", concert["programme"])
    concert["other_info"] = concert_data.get("other_info", concert["other_info"])
    concert["schedule_id"] = concert_data.get("schedule_id", concert.get("schedule_id"))
    concert["status"] = concert_data.get("status", concert["status"])
    
    save_ensembles(ensembles)
    # Audit: compute field-level diffs
    try:
        changed = []
        keys_of_interest = ["title", "date", "time", "venue", "uniform", "programme", "other_info", "status", "schedule_id"]
        for k in keys_of_interest:
            o = old_concert.get(k)
            n = concert.get(k)
            if (o or None) != (n or None):
                changed.append({"field": k, "old": o, "new": n})
        schedule_id = concert.get("schedule_id")
        if schedule_id:
            s = load_schedule(schedule_id)
            if s:
                desc = f"Concert updated: {concert.get('title')} ({len(changed)} change(s))"
                add_audit_entry(s, action="concert_updated", description=desc, actor=current_user(), meta={"concert_id": concert_id, "changes": changed})
                save_schedule(s)
    except Exception as e:
        print(f"[AUDIT] Failed to compute concert diffs: {e}")
    
    # SYNC: Update corresponding rehearsal row in schedule
    schedule_id = concert.get("schedule_id")
    if schedule_id:
        schedule = load_schedule(schedule_id)
        if schedule:
            updated_schedule = False
            timed_data = schedule.get("timed", [])
            for reh_row in schedule.get("rehearsals", []):
                # Find rehearsal row by concert_id link (fallback to Event Type match without id)
                if reh_row.get("concert_id") == concert_id or (
                    reh_row.get("Event Type") in ["Concert", "Contest"] and not reh_row.get("concert_id")
                ):
                    print(f"[UPDATE CONCERT] Syncing concert {concert_id} changes to rehearsal row")
                    # Update rehearsal row with concert data
                    reh_row["Date"] = new_date
                    reh_row["Start Time"] = concert["time"]
                    reh_row["Venue"] = concert["venue"]
                    reh_row["Uniform"] = concert["uniform"]
                    if not reh_row.get("concert_id"):
                        reh_row["concert_id"] = concert_id
                    updated_schedule = True

                    rehearsal_num = int(reh_row.get("Rehearsal", 0))

                    # Update or insert timed rows for this concert without wiping timeline edits
                    timed_data = upsert_concert_timed_rows(
                        timed_data,
                        rehearsal_num,
                        {
                            "id": concert_id,
                            "date": new_date,
                            "time": concert.get("time"),
                            "title": concert.get("title"),
                        },
                        reh_row.get("Section", "Full Ensemble"),
                    )
                    break
            
            if updated_schedule:
                schedule["timed"] = timed_data
                schedule["updated_at"] = int(time.time())
                save_schedule(schedule)
                print(f"[UPDATE CONCERT] Updated schedule {schedule_id} rehearsal table")
    
    return concert


def delete_concert(ensemble_id: str, concert_id: str) -> bool:
    """Delete a concert from an ensemble."""
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
    if not ensemble:
        return False
    
    concerts = ensemble.get("concerts", [])
    original_len = len(concerts)
    ensemble["concerts"] = [c for c in concerts if c.get("id") != concert_id]
    
    if len(ensemble["concerts"]) < original_len:
        save_ensembles(ensembles)
        # Audit
        schedules = [c.get("schedule_id") for c in concerts if c.get("id") == concert_id and c.get("schedule_id")]
        for sid in schedules:
            s = load_schedule(sid)
            if s:
                add_audit_entry(s, action="concert_deleted", description=f"Concert deleted: {concert_id}", actor=current_user(), meta={"concert_id": concert_id})
                save_schedule(s)
        return True
    return False


def get_ensemble_concerts(ensemble_id: str) -> List[dict]:
    """Get all concerts for an ensemble, sorted by date."""
    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        return []
    concerts = ensemble.get("concerts", [])
    ensemble_name = ensemble.get("name", "")
    # Replace {ensemble_name} in title for all concerts (legacy/old data fix)
    for c in concerts:
        if c.get("title") and "{ensemble_name}" in c["title"]:
            c["title"] = c["title"].replace("{ensemble_name}", ensemble_name)
    # Sort by date descending (upcoming first)
    concerts.sort(key=lambda x: x.get("date", ""), reverse=True)
    return concerts


def load_memberships() -> List[dict]:
    return read_json(memberships_path(), [])


def save_memberships(memberships: List[dict]):
    write_json_atomic(memberships_path(), memberships)


# ----------------------------
# Invitations storage
# ----------------------------
def invitations_path() -> str:
    return os.path.join(DATA_DIR, "invitations.json")


def load_invitations() -> List[dict]:
    return read_json(invitations_path(), [])


def save_invitations(invitations: List[dict]):
    write_json_atomic(invitations_path(), invitations)


# ----------------------------
# Admin management endpoints (wrap tools/manage_schedule.py)
# ----------------------------
def _require_edit_token_or_abort():
    token = request.args.get("token") or request.form.get("token") or request.headers.get("X-Edit-Token")
    if not token or token != EDIT_TOKEN:
        abort(403)


def _run_manage_script(args: list[str]) -> dict:
    script_path = os.path.join(os.path.dirname(__file__), "tools", "manage_schedule.py")
    cmd = [sys.executable, script_path] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except Exception as e:
        return {"returncode": 254, "stdout": "", "stderr": str(e)}


@app.route("/admin/api/manage/rename-field", methods=["POST"])  # token required
def api_manage_rename_field():
    _require_edit_token_or_abort()
    data = request.get_json() or request.form
    old = data.get("old")
    new = data.get("new")
    target = data.get("target", "all")
    dry = data.get("dry_run") or data.get("dry-run") or False
    backup = data.get("backup") or False
    if not old or not new:
        return jsonify({"error": "missing old/new"}), 400
    args = ["rename-field", "--old", str(old), "--new", str(new), "--target", str(target)]
    if dry:
        args.append("--dry-run")
    if backup:
        args.append("--backup")
    res = _run_manage_script(args)
    return jsonify(res)


@app.route("/admin/api/manage/replace-work-id", methods=["POST"])  # token required
def api_manage_replace_work_id():
    _require_edit_token_or_abort()
    data = request.get_json() or request.form
    try:
        old_id = int(data.get("old_id") or data.get("old-id"))
        new_id = int(data.get("new_id") or data.get("new-id"))
    except Exception:
        return jsonify({"error": "old_id and new_id must be integers"}), 400
    merge = bool(data.get("merge"))
    force = bool(data.get("force"))
    dry = bool(data.get("dry_run") or data.get("dry-run"))
    backup = bool(data.get("backup"))
    args = ["replace-work-id", "--old-id", str(old_id), "--new-id", str(new_id)]
    if merge:
        args.append("--merge")
    if force:
        args.append("--force")
    if dry:
        args.append("--dry-run")
    if backup:
        args.append("--backup")
    res = _run_manage_script(args)
    return jsonify(res)


@app.route("/admin/api/manage/replace-work-title", methods=["POST"])  # token required
def api_manage_replace_work_title():
    _require_edit_token_or_abort()
    data = request.get_json() or request.form
    find = data.get("find")
    replace = data.get("replace")
    if not find or replace is None:
        return jsonify({"error": "missing find or replace"}), 400
    ignore_case = bool(data.get("ignore_case") or data.get("ignore-case"))
    dry = bool(data.get("dry_run") or data.get("dry-run"))
    backup = bool(data.get("backup"))
    args = ["replace-work-title", "--find", str(find), "--replace", str(replace)]
    if ignore_case:
        args.append("--ignore-case")
    if dry:
        args.append("--dry-run")
    if backup:
        args.append("--backup")
    res = _run_manage_script(args)
    return jsonify(res)



def build_invitation_link(invite_code: str) -> str:
    """Return an absolute invitation URL for the register page."""
    if not invite_code:
        return ""
    try:
        return url_for("register_view", invite=invite_code, _external=True)
    except Exception:
        # Fallback to relative path if app lacks SERVER_NAME context
        return url_for("register_view", invite=invite_code)


# ----------------------------
# Session auth helpers (Phase 1)
# ----------------------------
def current_user() -> Optional[dict]:
    uid = session.get("user_id")
    if not uid:
        return None
    return next((u for u in load_users() if u.get("id") == uid), None)


def login_required_or_redirect():
    if not current_user():
        return redirect(url_for("login_view", next=request.path))
    return None


def admin_required_or_403():
    u = current_user()
    if not u:
        return redirect(url_for("login_view", next=request.path))
    if not u.get("is_admin"):
        abort(403)
    return None


# ----------------------------
# Audit log helpers
# ----------------------------
def add_audit_entry(schedule: dict, action: str, description: str, actor: Optional[dict] = None, meta: Optional[dict] = None):
    """Append an audit entry to the schedule in-memory object."""
    if schedule is None:
        return
    log = schedule.get("audit_log")
    if not isinstance(log, list):
        log = []
        schedule["audit_log"] = log
    entry = {
        "ts": int(time.time()),
        "action": action,
        "description": description,
        "actor_id": (actor or {}).get("id"),
        "actor_email": (actor or {}).get("email"),
        "actor_name": (actor or {}).get("name"),
        "meta": meta or {},
    }
    log.append(entry)
    if len(log) > AUDIT_LOG_LIMIT:
        # Keep most recent entries
        schedule["audit_log"] = log[-AUDIT_LOG_LIMIT:]


@app.template_filter('render_audit')
def render_audit(entry: dict) -> Markup:
    """Render an audit `entry` into a human-friendly HTML snippet.

    Supports common actions: `timed_edit`, `rehearsal_update`, `concert_updated`, `concert_added`, `concert_deleted`.
    Falls back to JSON rendering for unknown action/meta shapes.
    """
    if not entry or not isinstance(entry, dict):
        return Markup("")
    action = entry.get('action', '')
    meta = entry.get('meta') or {}
    parts: list[str] = []

    def esc(x):
        return _html.escape(str(x)) if x is not None else ''

    if action == 'timed_edit' and isinstance(meta, dict):
        changes = meta.get('changes') or []
        if not changes:
            parts.append('No detailed changes recorded.')
        else:
            for c in changes:
                ctype = c.get('type')
                if ctype == 'row_removed':
                    parts.append(f"Removed: <strong>{esc(c.get('title'))}</strong> — rehearsal {esc(c.get('rehearsal'))} @ {esc(c.get('time'))} ({esc(c.get('duration'))} min)")
                elif ctype == 'row_added':
                    parts.append(f"Added: <strong>{esc(c.get('title'))}</strong> — rehearsal {esc(c.get('rehearsal'))} @ {esc(c.get('time'))} ({esc(c.get('duration'))} min)")
                elif ctype == 'moved':
                    parts.append(f"Moved: <strong>{esc(c.get('title'))}</strong> — from rehearsals {esc(','.join(map(str, c.get('from', []))))} to {esc(','.join(map(str, c.get('to', []))))}")
                elif ctype == 'duration_changed':
                    parts.append(f"Duration changed: <strong>{esc(c.get('title'))}</strong> — rehearsal {esc(c.get('rehearsal'))}: {esc(c.get('old'))} → {esc(c.get('new'))} min")
                else:
                    # Generic fallback for unknown change objects
                    parts.append(esc(c))
    elif action in ('rehearsal_update', 'concert_updated') and isinstance(meta, dict):
        changes = meta.get('changes') or []
        if not changes:
            parts.append('No detailed changes recorded.')
        else:
            for c in changes:
                field = esc(c.get('field') or c.get('name') or '')
                old = esc(c.get('old'))
                new = esc(c.get('new'))
                parts.append(f"{field}: {old} → {new}")
    elif action in ('concert_added', 'concert_deleted') and isinstance(meta, dict):
        cid = esc(meta.get('concert_id') or entry.get('meta', {}).get('concert_id'))
        if action == 'concert_added':
            parts.append(f"Concert created (id: {cid})")
        else:
            parts.append(f"Concert deleted (id: {cid})")
    else:
        # Fallback: show pretty JSON
        try:
            parts.append(_html.escape(json.dumps(meta, ensure_ascii=False, indent=2)))
        except Exception:
            parts.append(_html.escape(str(meta)))

    return Markup('<br/>'.join(parts))


def audit_entries_since(schedule: dict, since_ts: Optional[int]) -> List[dict]:
    if not schedule or not isinstance(schedule.get("audit_log"), list):
        return []
    if since_ts is None:
        return schedule.get("audit_log", [])
    return [e for e in schedule.get("audit_log", []) if e.get("ts") and e.get("ts") > since_ts]


# ----------------------------
# Email notifications (Brevo)
# ----------------------------
def brevo_send_email(to_emails: List[str], subject: str, html_body: str, text_body: Optional[str] = None, bcc_mode: bool = True) -> bool:
    """Send an email via Brevo's transactional API. Returns True on success."""
    if not BREVO_API_KEY:
        print("[BREVO] Missing BREVO_API_KEY; skipping email send")
        return False
    if not to_emails:
        print("[BREVO] No recipients provided; skipping email send")
        return False

    unique_recipients = sorted({e.strip() for e in to_emails if e and "@" in e})
    if not unique_recipients:
        print("[BREVO] No valid recipient emails after filtering")
        return False

    if bcc_mode:
        # Use a single primary "To" and blind-copy all actual recipients
        primary_to = BREVO_PRIMARY_TO if (BREVO_PRIMARY_TO and "@" in BREVO_PRIMARY_TO) else None
        if not primary_to:
            primary_to = unique_recipients[0]

        bcc_list = [e for e in unique_recipients if e != primary_to]
        payload = {
            "sender": {"email": BREVO_FROM_EMAIL, "name": BREVO_FROM_NAME},
            "to": [{"email": primary_to}],
            "bcc": ([{"email": e} for e in bcc_list] if bcc_list else []),
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body or re.sub(r"<[^>]+>", "", html_body),
        }
    else:
        payload = {
            "sender": {"email": BREVO_FROM_EMAIL, "name": BREVO_FROM_NAME},
            "to": [{"email": e} for e in unique_recipients],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body or re.sub(r"<[^>]+>", "", html_body),
        }

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        resp = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers, timeout=10)
        if resp.status_code >= 300:
            print(f"[BREVO] Send failed ({resp.status_code}): {resp.text}")
            return False
        try:
            body = resp.json()
        except Exception:
            body = {}
        msg_id = body.get("messageId") or body.get("messageIds")
        if bcc_mode:
            print(f"[BREVO] Sent to {payload['to'][0]['email']} (bcc {len(payload.get('bcc') or [])}); messageId={msg_id}")
        else:
            print(f"[BREVO] Sent to {unique_recipients}; messageId={msg_id}")
        return True
    except Exception as exc:
        print(f"[BREVO] Exception sending email: {exc}")
        return False


def get_member_recipients(
    ensemble_id: str,
    exclude_user_id: Optional[str] = None,
    include_actor: bool = False,
) -> List[dict]:
    """Return recipient dicts for ensemble members (active or pending)."""
    memberships = load_memberships()
    users_by_id = {u.get("id"): u for u in load_users()}
    recipients = []
    for m in memberships:
        if m.get("ensemble_id") != ensemble_id:
            continue
        if m.get("status", "active") not in {"active", "pending"}:
            continue
        uid = m.get("user_id")
        if not include_actor and exclude_user_id and uid == exclude_user_id:
            continue
        user = users_by_id.get(uid) or {}
        email = user.get("email")
        if email and "@" in email:
            recipients.append({
                "user_id": uid,
                "email": email.strip(),
                "name": user_display_name(user),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "instrument": user.get("instrument"),
                "status": m.get("status", "active"),
            })
    # Deduplicate by email
    seen = set()
    unique = []
    for r in recipients:
        if r["email"] in seen:
            continue
        seen.add(r["email"])
        unique.append(r)
    return unique


def get_ensemble_admin_emails(ensemble_id: str) -> List[str]:
    """Return emails of users who are marked as admin for a given ensemble (memberships.role=='admin')."""
    memberships = load_memberships()
    users_by_id = {u.get("id"): u for u in load_users()}
    emails = []
    for m in memberships:
        if m.get("ensemble_id") != ensemble_id:
            continue
        if m.get("role") != "admin":
            continue
        uid = m.get("user_id")
        u = users_by_id.get(uid) or {}
        email = u.get("email")
        if email and "@" in email:
            emails.append(email.strip())
    # deduplicate
    return sorted(set(emails))


def get_global_admin_emails() -> List[str]:
    """Return emails of global admins (users with is_admin True)."""
    users = load_users()
    emails = [u.get("email").strip() for u in users if u.get("is_admin") and u.get("email") and "@" in u.get("email")]
    return sorted(set(emails))


def _default_notification_subject(ensemble_name: str) -> str:
    return f"Rehearsal schedule updates for {ensemble_name}"


def _default_notification_body(ensemble_name: str) -> str:
    return f"There have been some updates to a rehearsal schedule for {ensemble_name}:"


_ALLOWED_MSG_TAGS = {"b", "strong", "i", "em", "u", "br", "ul", "ol", "li", "p"}


def _sanitize_message_html(raw: str) -> str:
    """Escape HTML but allow a tiny safe subset of tags for formatting."""
    escaped = html.escape(raw or "")
    for tag in _ALLOWED_MSG_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
        escaped = escaped.replace(f"&lt;{tag}/&gt;", f"<{tag}/>" )
    return escaped


def _html_to_plain_text(msg_html: str) -> str:
    txt = msg_html
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"</p>", "\n\n", txt, flags=re.I)
    txt = re.sub(r"</li>", "\n", txt, flags=re.I)
    txt = re.sub(r"</(ul|ol)>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    # Collapse excessive newlines
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def notify_schedule_update(schedule: dict, description: str, actor: Optional[dict] = None, recipients_override: Optional[List[str]] = None, subject_override: Optional[str] = None):
    """Notify ensemble members that a schedule has changed. Returns (sent:boolean, recipients:list, error:str|None)."""
    if not schedule:
        return False, [], "No schedule provided"

    ensemble_id = schedule.get("ensemble_id")
    ensemble = get_ensemble_by_id(ensemble_id)
    ensemble_name = (ensemble or {}).get("name", ensemble_id or "Schedule")
    schedule_id = schedule.get("id")

    users_by_email = {normalize_email(u.get("email")): u for u in load_users()}

    if recipients_override:
        recipients_info = []
        for addr in recipients_override:
            if addr and "@" in addr:
                norm = normalize_email(addr)
                u = users_by_email.get(norm) or {}
                recipients_info.append({
                    "email": addr.strip(),
                    "name": user_display_name(u),
                    "first_name": u.get("first_name"),
                    "last_name": u.get("last_name"),
                    "instrument": u.get("instrument"),
                })
    else:
        recipients_info = get_member_recipients(
            ensemble_id,
            exclude_user_id=(actor or {}).get("id") if actor else None,
            include_actor=False,
        )
        if (not recipients_info) and actor:
            recipients_info = get_member_recipients(ensemble_id, exclude_user_id=None, include_actor=True)

    if not recipients_info:
        msg = f"No recipients for ensemble {ensemble_id}"
        print(f"[BREVO] {msg}; skipping notification")
        return False, [], msg

    schedule_link = url_for("schedule_view", schedule_id=schedule_id, _external=True)
    base_message = (description or _default_notification_body(ensemble_name)).strip()
    # Split provided base_message into message body and any explicit sign-off (e.g., "Best wishes,\nJack")
    lines = (base_message or "").replace("\r\n", "\n").split("\n")
    signoff_start = None
    for idx, ln in enumerate(lines):
        if re.match(r"^\s*(best wishes|kind regards|regards|sincerely|thanks),?\s*$", ln, flags=re.IGNORECASE):
            signoff_start = idx
            break

    if signoff_start is not None:
        message_body = "\n".join(lines[:signoff_start]).strip()
        signoff_lines = [l for l in lines[signoff_start:] if l is not None]
    else:
        message_body = base_message
        signoff_lines = []

    safe_message = _sanitize_message_html((message_body or "").replace("\r\n", "\n")).replace("\n", "<br>")
    subject = (subject_override or _default_notification_subject(ensemble_name) or "").strip() or _default_notification_subject(ensemble_name)

    # Detect if the user already included link to avoid duplicates
    include_link_paragraph = schedule_link not in base_message
    include_signoff = bool(signoff_lines) is False and not re.search(r"best wishes", base_message, flags=re.IGNORECASE)

    # Gather changes since last notification
    changes = audit_entries_since(schedule, schedule.get("last_notified_at"))
    def _format_meta_changes(meta: dict) -> str:
        # Simplified HTML change lines: only include change type, rehearsal number and date,
        # and concert title/date when available.
        parts = []
        chs = meta.get('changes') if isinstance(meta, dict) else None
        if not chs:
            return ''

        # If changes are field-level diffs (e.g., concert_updated / rehearsal_update),
        # render concise sentences like "Programme updated for <concert> on <date>".
        first = chs[0] if isinstance(chs, list) and chs else None
        if isinstance(first, dict) and ('field' in first or 'name' in first):
            # concert_id may be present in meta
            cid = meta.get('concert_id') or meta.get('id')
            concert = None
            if ensemble and cid:
                concert = next((c for c in ensemble.get('concerts', []) if c.get('id') == cid), None)
            concert_title = concert.get('title') if concert else (meta.get('title') or meta.get('concert_title') or '')
            concert_date = concert.get('date') if concert else (meta.get('date') or '')
            # If programme changed, call that out specifically
            prog_changed = any((c.get('field') or '').lower() == 'programme' for c in chs)
            if prog_changed:
                if concert_title:
                    parts.append(f"Programme updated for {html.escape(str(concert_title))} on {html.escape(str(uk_date_format(concert_date) or concert_date or 'Date TBC'))}")
                else:
                    parts.append("Programme updated")
                return '<br/>'.join(parts)
            # Otherwise summarize changes
            if concert_title:
                parts.append(f"Concert updated: {html.escape(str(concert_title))} on {html.escape(str(uk_date_format(concert_date) or concert_date or 'Date TBC'))} ({len(chs)} change(s))")
            else:
                parts.append(f"Updated ({len(chs)} change(s))")
            return '<br/>'.join(parts)

        # helper to look up rehearsal date and concert info from the schedule
        def _reh_info(reh_num):
            try:
                reh_num_i = int(reh_num)
            except Exception:
                return None, None, None
            reh_row = next((r for r in schedule.get('rehearsals', []) if int(r.get('Rehearsal', -1)) == reh_num_i), None)
            reh_date = reh_row.get('Date') if reh_row else None
            # find linked concert (by concert_id on rehearsal or matching date)
            concert_title = None
            concert_date = None
            if ensemble:
                concerts = [c for c in ensemble.get('concerts', []) if c.get('schedule_id') == schedule.get('id')]
                # match by concert_id on rehearsal
                if reh_row and reh_row.get('concert_id'):
                    cid = reh_row.get('concert_id')
                    concert = next((c for c in concerts if c.get('id') == cid), None)
                    if concert:
                        concert_title = concert.get('title')
                        concert_date = concert.get('date')
                # fallback: match by date
                if not concert_title and reh_date:
                    for c in concerts:
                        if str(c.get('date', ''))[:10] == str(reh_date)[:10]:
                            concert_title = c.get('title')
                            concert_date = c.get('date')
                            break
            return reh_num_i, reh_date, (concert_title, concert_date)

        # Aggregate non-move changes by (type, rehearsal) to reduce verbosity
        agg = {}
        moved_entries = []
        for c in chs:
            ctype = (c.get('type') or c.get('action') or 'change')
            reh = c.get('rehearsal')
            if reh is None:
                if c.get('to'):
                    reh = c.get('to')[0]
                elif c.get('from'):
                    reh = c.get('from')[0]

            if ctype == 'moved':
                moved_entries.append(c)
                continue

            try:
                reh_i = int(reh) if reh is not None else None
            except Exception:
                reh_i = None

            key = (ctype, reh_i)
            agg.setdefault(key, {'count': 0, 'examples': [], 'reh': reh_i})
            agg[key]['count'] += 1
            agg[key]['examples'].append(c)

        # Build sentences for aggregated changes
        for (ctype, reh_i), info in agg.items():
            label = ''
            if ctype == 'row_removed':
                label = 'Items removed'
            elif ctype == 'row_added':
                label = 'Items added'
            elif ctype == 'duration_changed':
                label = 'Time changes'
            else:
                label = str(ctype).replace('_', ' ').capitalize()

            reh_date = None
            concert_title = None
            concert_date = None
            if reh_i is not None:
                reh_date = next((r.get('Date') for r in schedule.get('rehearsals', []) if int(r.get('Rehearsal', -1)) == reh_i), None)
                # find concert if any
                if ensemble:
                    concerts = [c for c in ensemble.get('concerts', []) if c.get('schedule_id') == schedule.get('id')]
                    concert = next((c for c in concerts if str(c.get('date',''))[:10] == str(reh_date)[:10]), None)
                    if concert:
                        concert_title = concert.get('title')
                        concert_date = concert.get('date')

            date_txt = uk_date_format(reh_date) if reh_date else 'Date TBC'
            concert_txt = f" — {html.escape(str(concert_title))} on {html.escape(str(uk_date_format(concert_date) or concert_date or 'Date TBC'))}" if concert_title else ''

            if reh_i is not None:
                cnt = info['count']
                if cnt > 1:
                    parts.append(f"{label}: {cnt} items on rehearsal {reh_i}, {html.escape(str(date_txt))}{concert_txt}")
                else:
                    parts.append(f"{label} on rehearsal {reh_i}, {html.escape(str(date_txt))}{concert_txt}")
            else:
                parts.append(f"{label}: {html.escape(str(info['examples'][0]))}")

        # Add moved entries as short lines
        for m in moved_entries:
            title = m.get('title') or m.get('name') or ''
            frm = m.get('from') or []
            to = m.get('to') or []
            frm_txt = ','.join(map(str, frm)) if frm else ''
            to_txt = ','.join(map(str, to)) if to else ''
            if frm_txt and to_txt:
                parts.append(f"Moved items: from rehearsals {html.escape(frm_txt)} to {html.escape(to_txt)}")
            else:
                parts.append("Moved items")

        return '<br/>'.join(parts)

    def format_change(entry: dict) -> str:
        actor_label = entry.get("actor_name") or entry.get("actor_email") or ""
        actor_txt = f" — {html.escape(actor_label)}" if actor_label else ""
        base = f"<li><strong>{html.escape(entry.get('action',''))}</strong>: {html.escape(entry.get('description',''))}{actor_txt}"
        meta_html = _format_meta_changes(entry.get('meta') or {})
        if meta_html:
            base += f"<div style=\"margin-left:8px; margin-top:6px; font-size:0.95rem; color:#374151;\">{meta_html}</div>"
        base += "</li>"
        return base

    # Build simplified change HTML using the helper that reads schedule/ensemble context
    def _simple_change_html(e: dict) -> str:
        actor_label = e.get("actor_name") or e.get("actor_email") or ""
        actor_txt = f" — {html.escape(actor_label)}" if actor_label else ""
        c_meta_html = _format_meta_changes(e.get('meta') or {})
        base = f"<li><strong>{html.escape(e.get('action',''))}</strong>: {html.escape(e.get('description',''))}{actor_txt}"
        if c_meta_html:
            base += f"<div style=\"margin-left:8px; margin-top:6px; font-size:0.95rem; color:#374151;\">{c_meta_html}</div>"
        base += "</li>"
        return base

    changes_html = "".join(_simple_change_html(e) for e in changes[:10])
    if len(changes) > 10:
        changes_html += f"<li>…and {len(changes)-10} more</li>"

    success_count = 0
    failed_count = 0
    for r in recipients_info:
        first_name = r.get("first_name") or (r.get("name") or "").split(" ")[0] or r.get("email") or "there"
        greeting = html.escape(first_name)

        # Assemble HTML: greeting, message body, updates, link, then sign-off (explicit or default)
        body_parts = [f"<p>Hi {greeting},</p>", f"<p>{safe_message}</p>"]
        if changes_html:
            body_parts.append("<p>Updates:</p><ul>" + changes_html + "</ul>")
        else:
            body_parts.append("<p>Updates were made, but there are no detailed items to show.</p>")
        if include_link_paragraph:
            body_parts.append(f"<p>Check out the rehearsal schedule <a href='{schedule_link}'>here</a>.</p>")

        # Append explicit sign-off lines (if the user included them) or the default sign-off
        if signoff_lines:
            sf_html = _sanitize_message_html("\n".join(signoff_lines)).replace("\n", "<br>")
            body_parts.append(sf_html)
        elif include_signoff:
            body_parts.append("<p>Best wishes,</p>")
            body_parts.append("<p>Jack</p>")

        html_body = "\n".join(body_parts)

        text_greeting = first_name or r.get('email') or 'there'
        plain_message = _html_to_plain_text(safe_message)

        # Plain-text assembly: place changes before sign-off
        text_lines = [f"Hi {text_greeting},", "", plain_message]
        if changes:
            for e in changes[:10]:
                actor_label = e.get("actor_name") or e.get("actor_email") or ""
                actor_txt = f" — {actor_label}" if actor_label else ""
                meta_html = _format_meta_changes(e.get('meta') or {})
                meta_text = _html_to_plain_text(meta_html)
                text_lines.append(f"- {e.get('action')}: {e.get('description')}{actor_txt}")
                if meta_text:
                    # indent the meta details slightly for readability
                    for ml in meta_text.split('\n'):
                        if ml.strip():
                            text_lines.append(f"  {ml}")
            if len(changes) > 10:
                text_lines.append(f"…and {len(changes)-10} more")
        else:
            text_lines.append("- Updates were made, but there are no detailed items to show.")
        text_lines.append("")
        if include_link_paragraph:
            text_lines.append(f"Check out the rehearsal schedule here: {schedule_link}")
            text_lines.append("")

        # Append explicit sign-off lines (if provided) or default
        if signoff_lines:
            text_lines.extend([l for l in signoff_lines if l is not None and l.strip() != ""]) 
        elif include_signoff:
            text_lines.append("Best wishes,")
            text_lines.append("Jack")

        text_body = "\n".join(text_lines)

        sent = brevo_send_email([r["email"]], subject, html_body, text_body, bcc_mode=False)
        if sent:
            success_count += 1
        else:
            failed_count += 1

    if success_count == 0:
        if not BREVO_API_KEY:
            return False, [r["email"] for r in recipients_info], "Email disabled (missing BREVO_API_KEY)"
        return False, [r["email"] for r in recipients_info], "Email send failed"

    # Update notification timestamp and audit log
    schedule["last_notified_at"] = int(time.time())
    add_audit_entry(
        schedule,
        action="notification_sent",
        description=f"Sent update to {success_count} recipient(s)",
        actor=actor,
        meta={"success": success_count, "failed": failed_count, "subject": subject},
    )
    save_schedule(schedule)

    return True, [r["email"] for r in recipients_info], None


def is_member(user_id: str, ensemble_id: str) -> bool:
    mem = load_memberships()
    return any(
        m for m in mem
        if m.get("user_id") == user_id
        and m.get("ensemble_id") == ensemble_id
        and m.get("status", "active") == "active"
    )


def ensure_attendance(schedule: dict) -> dict:
    """Guarantee attendance dict exists on schedule."""
    if schedule is None:
        return {}
    att = schedule.get("attendance")
    if not isinstance(att, dict):
        att = {}
        schedule["attendance"] = att
    return att


def get_rehearsal_row(schedule: dict, rehearsal_num: int) -> Optional[dict]:
    """Fetch a rehearsal row by its Rehearsal number."""
    try:
        return next((r for r in schedule.get("rehearsals", []) if int(r.get("Rehearsal", -1)) == int(rehearsal_num)), None)
    except Exception:
        return None


def get_event_start(schedule: dict, rehearsal_num: int) -> Optional[_dt.datetime]:
    """Derive a start datetime for a rehearsal event using Date + Start Time if available."""
    reh = get_rehearsal_row(schedule, rehearsal_num)
    if not reh:
        return None

    date_val = reh.get("Date")
    time_val = reh.get("Start Time") or reh.get("Time") or reh.get("Time in Rehearsal")

    # Parse date
    try:
        date_dt = pd.to_datetime(date_val) if date_val is not None else None
    except Exception:
        date_dt = None

    if date_dt is None or pd.isna(date_dt):
        return None

    # If no time, assume midnight to let date-only comparisons work
    hh = 0
    mm = 0
    if time_val:
        mins = minutes_from_timecell(time_val)
        if mins is not None:
            hh = mins // 60
            mm = mins % 60
    return _dt.datetime(date_dt.year, date_dt.month, date_dt.day, hh, mm)


def event_is_past(schedule: dict, rehearsal_num: int) -> bool:
    dt = get_event_start(schedule, rehearsal_num)
    if dt is None:
        return False  # if unknown, allow but we will rely on admin data quality
    now = _dt.datetime.now()
    return dt < now


def ensemble_member_required_or_403(ensemble_id: str):
    u = current_user()
    if not u:
        return redirect(url_for("login_view", next=request.path))
    if u.get("is_admin"):
        return None
    if not is_member(u["id"], ensemble_id):
        abort(403)
    return None

def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def user_display_name(user: dict) -> str:
    first = (user or {}).get("first_name") or ""
    last = (user or {}).get("last_name") or ""
    name = (f"{first} {last}".strip()) or user.get("name") or user.get("email") or ""
    return name

def slugify_id(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or uuid.uuid4().hex[:8]

def ensure_admin_if_matches_email(user: dict) -> bool:
    """Returns True if user was modified."""
    global ADMIN_EMAIL
    if ADMIN_EMAIL and normalize_email(user.get("email") or "") == ADMIN_EMAIL and not user.get("is_admin"):
        user["is_admin"] = True
        return True
    return False

def set_user_admin_flag(user_id: str, is_admin: bool):
    users = load_users()
    u = next((x for x in users if x.get("id") == user_id), None)
    if not u:
        abort(404)
    u["is_admin"] = bool(is_admin)
    save_users(users)

def upsert_membership(user_id: str, ensemble_id: str, role: str = "member", status: str = "active"):
    mem = load_memberships()
    m = next((x for x in mem if x.get("user_id") == user_id and x.get("ensemble_id") == ensemble_id), None)
    if m:
        m["role"] = role
        m["status"] = status
    else:
        mem.append({"user_id": user_id, "ensemble_id": ensemble_id, "role": role, "status": status})
    save_memberships(mem)

def remove_membership(user_id: str, ensemble_id: str):
    mem = load_memberships()
    mem = [m for m in mem if not (m.get("user_id") == user_id and m.get("ensemble_id") == ensemble_id)]
    save_memberships(mem)


# ----------------------------
# Utilities / loading script modules
# ----------------------------
def load_module_from_path(name: str, path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod1 = load_module_from_path("script1", SCRIPT1)
mod2 = load_module_from_path("script2", SCRIPT2)
mod3 = load_module_from_path("script3", SCRIPT3)
mod4_exists = os.path.exists(SCRIPT4)


def safe_int(x, default=0):
    try:
        if pd.isna(x):
            return default
        return int(float(x))
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def parse_truthy(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip().upper()
    return s in {"Y", "YES", "TRUE", "T", "1"}


def minutes_from_timecell(val) -> Optional[int]:
    if pd.isna(val):
        return None
    if hasattr(val, "hour") and hasattr(val, "minute"):
        try:
            return int(val.hour) * 60 + int(val.minute)
        except Exception:
            pass

    s = str(val).strip().replace("：", ":")
    if re.match(r"^\d{1,2}\.\d{2}(\s*(AM|PM|am|pm))?$", s):
        s = s.replace(".", ":")

    m = re.match(r"^\s*(\d{1,2}):(\d{2})", s)
    if m:
        hh = int(m.group(1)) % 24
        mm = int(m.group(2))
        if re.search(r"pm$", s, flags=re.I) and hh < 12:
            hh += 12
        if re.search(r"am$", s, flags=re.I) and hh == 12:
            hh = 0
        return hh * 60 + mm

    m = re.fullmatch(r"^(\d{3,4})$", s)
    if m:
        v = int(m.group(1))
        hh = v // 100
        mm = v % 100
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh * 60 + mm

    try:
        f = float(s)
        if 0 <= f < 1:
            return int(round(f * 24 * 60))
        if 1 <= f < 24 * 60 + 1:
            return int(round(f))
    except Exception:
        pass

    t = pd.to_datetime(s, errors="coerce")
    if pd.notna(t):
        return int(t.hour) * 60 + int(t.minute)
    return None


def parse_break_minutes(val) -> int:
    if pd.isna(val):
        return 0
    num = pd.to_numeric(val, errors="coerce")
    if pd.notna(num):
        return int(round(float(num)))
    t = pd.to_datetime(val, errors="coerce")
    if pd.notna(t):
        return int(t.hour) * 60 + int(t.minute)
    s = str(val).strip()
    if ":" in s:
        p = s.split(":")
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            return int(p[0]) * 60 + int(p[1])
    return 0


def hhmm_from_minutes(m: int) -> str:
    m = int(m)
    h = (m // 60) % 24
    mm = m % 60
    return f"{h:02d}:{mm:02d}"


# ----------------------------
# Project persistence
# ----------------------------
def default_works_df() -> pd.DataFrame:
    cols = [
        "Title", "Duration", "Difficulty", "Rehearsal Time Required",
        "Flute", "Oboe", "Clarinet", "Bassoon",
        "Horn", "Trumpet", "Trombone", "Tuba",
        "Violin 1", "Violin 2", "Viola", "Cello", "Bass",
        "Percussion", "Timpani", "Piano", "Harp", "Soloist"
    ]
    return pd.DataFrame(columns=cols)


def default_rehearsals_df() -> pd.DataFrame:
    cols = ["Rehearsal", "Date", "Day", "Include in allocation", "Event Type", "Section", "Start Time", "End Time", "Break", "Percs", "Piano", "Harp", "Brass", "Soloist"]
    return pd.DataFrame(columns=cols)


def load_project() -> dict:
    return read_json(
        PROJECT_FILE,
        {
            "G": DEFAULT_G,
            "works": [],
            "rehearsals": [],
            "allocation": [],
            "schedule": [],
            "timed": [],
            "timed_history": [],
            "generated_at": None,
        },
    )


def save_project(p: dict):
    write_json_atomic(PROJECT_FILE, p)


def get_frames(s: dict) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Load works with saved column order if available
    works_cols = s.get("works_cols")
    works = pd.DataFrame(s.get("works", []))
    if works_cols:
        # Use saved columns in saved order, fill missing columns with empty strings
        works = works.reindex(columns=works_cols).fillna("")
    else:
        # Fall back to defaults
        default_cols = default_works_df().columns.tolist()
        # Only keep existing columns and add missing ones from defaults
        existing_cols = [c for c in works.columns if c in default_cols]
        missing_cols = [c for c in default_cols if c not in works.columns]
        if missing_cols:
            for col in missing_cols:
                works[col] = ""
        works = works[existing_cols + missing_cols]
    
    # Load rehearsals with saved column order if available
    rehearsals_cols = s.get("rehearsals_cols")
    rehearsals = pd.DataFrame(s.get("rehearsals", []))
    if rehearsals_cols:
        # Use saved columns in saved order, fill missing columns with empty strings
        rehearsals = rehearsals.reindex(columns=rehearsals_cols).fillna("")
    else:
        # Fall back to defaults
        default_cols = default_rehearsals_df().columns.tolist()
        # Only keep existing columns and add missing ones from defaults
        existing_cols = [c for c in rehearsals.columns if c in default_cols]
        missing_cols = [c for c in default_cols if c not in rehearsals.columns]
        if missing_cols:
            for col in missing_cols:
                rehearsals[col] = ""
        rehearsals = rehearsals[existing_cols + missing_cols]
    
    alloc = pd.DataFrame(s.get("allocation", []))
    sched = pd.DataFrame(s.get("schedule", []))
    return works, rehearsals, alloc, sched


# ----------------------------
# Auth helper
# ----------------------------
def require_edit_token():
    tok = request.args.get("token") or request.headers.get("X-Edit-Token")
    if tok != EDIT_TOKEN:
        abort(403)



META_COLS = {
    "title", "composer", "duration", "difficulty", "soloist",
    "notes", "piece", "work"
}

def infer_instrument_columns(works_df: pd.DataFrame) -> list[str]:
    cols = []
    for c in works_df.columns:
        key = str(c).strip().lower()
        if key in META_COLS:
            continue
        # if any value looks numeric, treat as instrument demand col
        series = works_df[c]
        # coerce to numeric, ignore blanks
        numeric = pd.to_numeric(series.replace("", pd.NA), errors="coerce")
        if numeric.notna().any():
            cols.append(c)
    return cols

def find_duration_col(cols):
    # common variants
    candidates = ["duration", "mins", "minutes", "time", "length"]
    for c in cols:
        if str(c).strip().lower() in candidates:
            return c
    return None


# ----------------------------
# Core compute helpers (allocator + schedule + timing)
# ----------------------------
def prepare_works_for_allocator(works_df: pd.DataFrame) -> pd.DataFrame:
    w = works_df.copy()
    
    # Ensure Title column exists
    if "Title" not in w.columns:
        raise ValueError("Works table must have a 'Title' column")
    
    w["Title"] = w["Title"].astype(str).str.strip()
    w = w[w["Title"].str.len() > 0].copy()

    if w.empty:
        raise ValueError("Works table has no valid works (all titles are empty)")

    w["Duration"] = pd.to_numeric(w["Duration"], errors="coerce").fillna(0.0)
    w["Difficulty"] = pd.to_numeric(w["Difficulty"], errors="coerce").fillna(1.0)

    try:
        w2 = mod1.normalise_works_columns(w)
    except Exception:
        w2 = w.copy()
        w2["duration_norm"] = w2["Duration"].astype(float)
        w2["difficulty_norm"] = w2["Difficulty"].astype(float).clip(lower=0.1)

    for c in (
        mod1.WIND_COLS + mod1.BRASS_COLS + mod1.STRING_COLS +
        mod1.PERC_COLS + mod1.PIANO_COLS + mod1.HARP_COLS + mod1.SOLOIST_COLS
    ):
        if c not in w2.columns:
            w2[c] = 0
    return w2.reset_index(drop=True)


def prepare_all_rehearsals(rehearsals_df: pd.DataFrame) -> pd.DataFrame:
    """Prepare all rehearsals (including non-allocated ones) for timed schedule generation.
    Unlike prepare_rehearsals_for_allocator, this does NOT filter by "Include in allocation"."""
    r = rehearsals_df.copy()
    print(f"[TIMING] Input rehearsals: {len(r)} rows")
    
    # Ensure required columns exist
    if "Rehearsal" not in r.columns:
        r = r.reindex(columns=default_rehearsals_df().columns, fill_value="")
        print(f"[TIMING] WARNING: 'Rehearsal' column was missing, reindexed with defaults")
    
    # Process rehearsal numbers
    r = r[r["Rehearsal"].astype(str).str.strip().ne("")].copy()
    r["Rehearsal"] = pd.to_numeric(r["Rehearsal"], errors="coerce").astype("Int64")
    r = r[r["Rehearsal"].notna()].copy()
    
    # DO NOT FILTER by "Include in allocation" - we want all rehearsals for the timed schedule
    print(f"[TIMING] Processing {len(r)} rehearsals (including non-allocated)")
    
    # Debug: Show Date column before conversion
    print(f"[TIMING] Date column before conversion:")
    for idx, row in r.iterrows():
        reh_num = row.get("Rehearsal")
        date_val = row.get("Date")
        print(f"  Rehearsal {reh_num}: Date = {repr(date_val)} (type: {type(date_val).__name__})")
    
    r["Date"] = pd.to_datetime(r.get("Date"), errors="coerce").dt.date  # type: ignore
    
    # Debug: Show Date column after conversion
    print(f"[TIMING] Date column after conversion:")
    for idx, row in r.iterrows():
        reh_num = row.get("Rehearsal")
        date_val = row.get("Date")
        print(f"  Rehearsal {reh_num}: Date = {repr(date_val)} (type: {type(date_val).__name__})")

    start_min = r.get("Start Time").apply(minutes_from_timecell) if "Start Time" in r.columns else pd.Series([None]*len(r))  # type: ignore
    end_min = r.get("End Time").apply(minutes_from_timecell) if "End Time" in r.columns else pd.Series([None]*len(r))  # type: ignore
    start_min = start_min.fillna(19 * 60).astype(int)
    end_min = end_min.fillna(21 * 60 + 30).astype(int)

    gross = (end_min - start_min).astype(int)
    gross = gross.where(gross >= 0, gross + 24 * 60)

    br = r.get("Break").apply(parse_break_minutes) if "Break" in r.columns else pd.Series([0]*len(r))  # type: ignore
    r["Break (minutes)"] = br.astype(int)
    r["Duration"] = (gross - r["Break (minutes)"]).clip(lower=0).astype(int)

    for c in ["Percs", "Piano", "Harp", "Brass", "Soloist"]:
        if c not in r.columns:
            r[c] = False
        r[c] = r[c].apply(parse_truthy)

    hh = (start_min // 60).astype(int).astype(str).str.zfill(2)
    mm = (start_min % 60).astype(int).astype(str).str.zfill(2)
    hhmm = hh + ":" + mm
    date_str = np.where(pd.notna(pd.Series(r["Date"])), pd.Series(r["Date"]).astype(str), "2000-01-01")
    r["Start DateTime"] = pd.to_datetime(date_str + " " + hhmm, errors="coerce").fillna(pd.to_datetime("2000-01-01 19:00"))

    # Preserve Section column if it exists, otherwise default to "Full Ensemble"
    if "Section" not in r.columns:
        r["Section"] = "Full Ensemble"
    else:
        r["Section"] = r["Section"].fillna("Full Ensemble").astype(str)
        r.loc[r["Section"].str.strip() == "", "Section"] = "Full Ensemble"

    return r.sort_values("Rehearsal").reset_index(drop=True)


def prepare_rehearsals_for_allocator(rehearsals_df: pd.DataFrame) -> pd.DataFrame:
    r = rehearsals_df.copy()
    print(f"[ALLOCATOR] Input rehearsals: {len(r)} rows")
    
    # Ensure required columns exist
    if "Rehearsal" not in r.columns:
        # Reindex to include all default columns
        r = r.reindex(columns=default_rehearsals_df().columns, fill_value="")
        print(f"[ALLOCATOR] WARNING: 'Rehearsal' column was missing, reindexed with defaults")
    
    if len(r) > 0:
        print(f"[ALLOCATOR] Include in allocation values: {r.get('Include in allocation', pd.Series()).unique().tolist()}")
    
    r = r[r["Rehearsal"].astype(str).str.strip().ne("")].copy()
    r["Rehearsal"] = pd.to_numeric(r["Rehearsal"], errors="coerce").astype("Int64")
    r = r[r["Rehearsal"].notna()].copy()
    
    # Filter out rehearsals where "Include in allocation" is not "Y"
    if "Include in allocation" in r.columns:
        print(f"[ALLOCATOR] Before filtering: {len(r)} rehearsals")
        r = r[r["Include in allocation"].apply(parse_truthy)].copy()
        print(f"[ALLOCATOR] After filtering: {len(r)} rehearsals")
    
    r["Date"] = pd.to_datetime(r.get("Date"), errors="coerce").dt.date  # type: ignore

    start_min = r.get("Start Time").apply(minutes_from_timecell) if "Start Time" in r.columns else pd.Series([None]*len(r))  # type: ignore
    end_min = r.get("End Time").apply(minutes_from_timecell) if "End Time" in r.columns else pd.Series([None]*len(r))  # type: ignore
    start_min = start_min.fillna(19 * 60).astype(int)
    end_min = end_min.fillna(21 * 60 + 30).astype(int)

    gross = (end_min - start_min).astype(int)
    gross = gross.where(gross >= 0, gross + 24 * 60)

    br = r.get("Break").apply(parse_break_minutes) if "Break" in r.columns else pd.Series([0]*len(r))  # type: ignore
    r["Break (minutes)"] = br.astype(int)
    r["Duration"] = (gross - r["Break (minutes)"]).clip(lower=0).astype(int)

    for c in ["Percs", "Piano", "Harp", "Brass", "Soloist"]:
        if c not in r.columns:
            r[c] = False
        r[c] = r[c].apply(parse_truthy)

    hh = (start_min // 60).astype(int).astype(str).str.zfill(2)
    mm = (start_min % 60).astype(int).astype(str).str.zfill(2)
    hhmm = hh + ":" + mm
    date_str = np.where(pd.notna(pd.Series(r["Date"])), pd.Series(r["Date"]).astype(str), "2000-01-01")
    r["Start DateTime"] = pd.to_datetime(date_str + " " + hhmm, errors="coerce").fillna(pd.to_datetime("2000-01-01 19:00"))

    # Preserve Section column if it exists, otherwise default to "Full Ensemble"
    if "Section" not in r.columns:
        r["Section"] = "Full Ensemble"
    else:
        r["Section"] = r["Section"].fillna("Full Ensemble").astype(str)
        r.loc[r["Section"].str.strip() == "", "Section"] = "Full Ensemble"

    return r.sort_values("Rehearsal").reset_index(drop=True)
def run_allocation_compute(works_df: pd.DataFrame, rehearsals_df: pd.DataFrame, G: int) -> Tuple[pd.DataFrame, List[str]]:
    works = prepare_works_for_allocator(works_df)
    rehe = prepare_rehearsals_for_allocator(rehearsals_df)
    
    print(f"[ALLOCATION] Works count: {len(works)}, Rehearsals count: {len(rehe)}")
    if len(rehe) > 0:
        print(f"[ALLOCATION] Rehearsal numbers: {rehe['Rehearsal'].unique().tolist()}")
        print(f"[ALLOCATION] First rehearsal: {rehe.iloc[0].to_dict() if len(rehe) > 0 else 'N/A'}")

    if works.empty or rehe.empty:
        raise ValueError("Need at least 1 work and 1 rehearsal.")

    if rehe["Rehearsal"].nunique() < 2:
        raise ValueError("Need at least 2 rehearsals (first/last constraint).")

    tokens_per = (rehe["Duration"].astype(float) // G).astype(int)
    snapped_caps = (tokens_per * G).astype(int)
    snapped_total = int(snapped_caps.sum())
    if snapped_total <= 0:
        raise ValueError("Total capacity is 0 minutes after snapping. Check times/breaks.")

    req = mod1.compute_required_minutes(works, snapped_total, G)
    export_df, warnings = mod1.allocate_across_rehearsals(works, rehe, req, G)
    return export_df, list(warnings or [])


def estimate_playerload_map(works_df: pd.DataFrame) -> Dict[str, float]:
    works = works_df.copy()
    works["Title"] = works["Title"].astype(str)
    groups = mod2.gather_resolved_groups(works)
    out = {}
    if "Title" not in works.columns:
        return out
    for _, row in works.iterrows():
        t = str(row.get("Title", "")).strip()
        if not t:
            continue
        out[t] = float(mod2.estimate_player_load(row, groups))
    return out


def group_hint_map(works_df: pd.DataFrame) -> Dict[str, Optional[str]]:
    hint = {}
    try:
        group_col = mod2._first_matching_col(works_df, mod2.GROUP_ALIASES)
    except Exception:
        group_col = None
    if group_col and group_col in works_df.columns:
        for _, r in works_df.iterrows():
            t = str(r.get("Title", "")).strip()
            if not t:
                continue
            v = r.get(group_col)
            hint[t] = str(v).strip() if pd.notna(v) and str(v).strip() else None
    return hint


def parse_group_and_movement(title: str, group_hint: Optional[str]):
    try:
        return mod2.parse_group_and_movement(title, group_hint)
    except Exception:
        s = str(title).strip()
        group = group_hint.strip() if group_hint else s.split(":")[0].strip()
        return group or s, None, None


def build_signature_map(works_df: pd.DataFrame) -> Dict[str, Dict[str, int]]:
    sig_map: Dict[str, Dict[str, int]] = {}
    for _, r in works_df.iterrows():
        title = str(r.get("Title", "")).strip()
        if not title:
            continue
        try:
            sig = mod3.signature_for_work(r)
        except Exception:
            sig = {"Percs": 0, "PercProfile": 0, "Piano": 0, "Harp": 0, "Winds": 0, "Brass": 0, "Strings": 0}
        sig_map[title] = {k: int(sig.get(k, 0)) for k in ["Percs","PercProfile","Piano","Harp","Winds","Brass","Strings"]}
    return sig_map


@dataclass
class Bundle:
    key: str
    items: pd.DataFrame
    mins: int
    playerload: float
    sig: Dict[str, int]


def build_bundles_for_rehearsal(df_r: pd.DataFrame, sig_map: Dict[str, Dict[str, int]]) -> List[Bundle]:
    bundles: List[Bundle] = []
    for gk, grp in df_r.groupby("GroupKey", sort=False):
        grp2 = grp.copy()
        if "MovementOrder" in grp2.columns:
            grp2["MovementOrder"] = pd.to_numeric(grp2["MovementOrder"], errors="coerce")
            grp2 = grp2.sort_values(["MovementOrder", "Title"], na_position="last")
        mins = int(pd.to_numeric(grp2["Rehearsal Time (minutes)"], errors="coerce").fillna(0).sum())
        playerload = float(pd.to_numeric(grp2["PlayerLoad"], errors="coerce").fillna(0).max())

        sig = {"Percs": 0, "PercProfile": 0, "Piano": 0, "Harp": 0, "Winds": 0, "Brass": 0, "Strings": 0}
        for t in grp2["Title"].astype(str).tolist():
            s = sig_map.get(t)
            if not s:
                continue
            for k in sig.keys():
                sig[k] = max(int(sig[k]), int(s.get(k, 0)))
        bundles.append(Bundle(key=str(gk), items=grp2, mins=mins, playerload=playerload, sig=sig))
    return bundles


def order_bundles_descending_load_with_similarity(
    bundles: List[Bundle],
    transition_cost_fn,
    increase_penalty_weight: float = 100.0
) -> List[Bundle]:
    if not bundles:
        return []
    remaining = bundles[:]
    remaining.sort(key=lambda b: (b.playerload, b.mins), reverse=True)
    ordered = [remaining.pop(0)]

    while remaining:
        last = ordered[-1]
        last_load = last.playerload
        best_i = 0
        best_key = None
        for i, cand in enumerate(remaining):
            inc = max(0.0, cand.playerload - last_load)
            inc_pen = inc * increase_penalty_weight
            tc = transition_cost_fn(last.sig, cand.sig)
            key = (inc_pen, tc, -cand.playerload, -cand.mins)
            if best_key is None or key < best_key:
                best_key = key
                best_i = i
        ordered.append(remaining.pop(best_i))

    return ordered


def build_schedule_from_allocation(works_df: pd.DataFrame, alloc_df: pd.DataFrame) -> pd.DataFrame:
    alloc = alloc_df.copy()
    alloc = alloc[~alloc["Title"].astype(str).str.startswith("[Summary]")].copy()

    reh_cols = [c for c in alloc.columns if str(c).strip().startswith("Rehearsal ")]
    col_to_num = {}
    for c in reh_cols:
        try:
            col_to_num[c] = int(str(c).split()[-1])
        except Exception:
            pass
    if not col_to_num:
        raise ValueError("No 'Rehearsal N' columns found in allocation output.")

    pl_map = estimate_playerload_map(works_df)
    gh_map = group_hint_map(works_df)
    sig_map = build_signature_map(prepare_works_for_allocator(works_df))  # signature_for_work expects normalized-like row

    rows = []
    for _, r in alloc.iterrows():
        title = str(r.get("Title", "")).strip()
        if not title:
            continue
        for col, rnum in col_to_num.items():
            mins = pd.to_numeric(r.get(col, 0), errors="coerce")
            if pd.isna(mins) or mins <= 0:
                continue
            group_hint = gh_map.get(title)
            group_title, _, mov_ord = parse_group_and_movement(title, group_hint)

            rows.append({
                "Rehearsal": int(rnum),
                "Title": title,
                "Rehearsal Time (minutes)": int(round(float(mins))),
                "PlayerLoad": float(pl_map.get(title, 0.0)),
                "GroupKey": str(group_title),
                "MovementOrder": mov_ord if mov_ord is not None else np.nan,
            })

    if not rows:
        raise ValueError("No scheduled minutes found to build a schedule.")

    sched = pd.DataFrame(rows)
    sched["Rehearsal"] = pd.to_numeric(sched["Rehearsal"], errors="coerce").astype("Int64")
    sched["MovementOrder"] = pd.to_numeric(sched["MovementOrder"], errors="coerce")

    ordered_rows = []
    for rnum, df_r in sched.groupby("Rehearsal", sort=True):
        bundles = build_bundles_for_rehearsal(df_r, sig_map)
        ordered_bundles = order_bundles_descending_load_with_similarity(
            bundles,
            transition_cost_fn=mod3.transition_cost,
            increase_penalty_weight=100.0,
        )
        for b in ordered_bundles:
            ordered_rows.append(b.items)

    return pd.concat(ordered_rows, ignore_index=True)


def choose_break_offset_favor_longer_first_half(durations: List[int]) -> int:
    if not durations:
        return 0
    boundaries = [0]
    for m in durations:
        boundaries.append(boundaries[-1] + int(m))
    total = boundaries[-1]
    if len(boundaries) <= 2:
        return 0
    ideal = total / 2.0
    candidates = range(1, len(boundaries) - 1)

    def key(i: int):
        b = boundaries[i]
        return (abs(2 * b - total), 0 if b >= ideal else 1, -b)

    best_idx = min(candidates, key=key)
    return int(boundaries[best_idx])


def compute_timed_df(schedule_df: pd.DataFrame, rehearsals_prepared: pd.DataFrame) -> pd.DataFrame:
    if schedule_df.empty and rehearsals_prepared.empty:
        return pd.DataFrame()

    rehe = rehearsals_prepared.set_index("Rehearsal", drop=False)
    out_rows = []
    processed_rehearsals = set()

    # Process rehearsals that have allocated works
    for rnum, df_r in schedule_df.groupby("Rehearsal", sort=True):
        rnum_i = int(rnum)  # type: ignore
        processed_rehearsals.add(rnum_i)
        
        if rnum_i not in rehe.index:
            continue

        rrow = rehe.loc[rnum_i]
        
        # Get Event Type from the rehearsal row
        event_type = str(rrow.get("Event Type", "Rehearsal")).strip() if pd.notna(rrow.get("Event Type")) else "Rehearsal"
        if not event_type:
            event_type = "Rehearsal"
        
        # Skip adding scheduled works to concerts - concerts only get placeholder rows
        if event_type == "Concert":
            continue
        
        date = rrow.get("Date")
        start_dt = pd.to_datetime(rrow.get("Start DateTime"), errors="coerce")  # type: ignore
        if pd.isna(start_dt):
            start_dt = pd.to_datetime("2000-01-01 19:00")

        break_mins = safe_int(rrow.get("Break (minutes)", 0), 0)
        
        # Get Section from the rehearsal row
        section = str(rrow.get("Section", "Full Ensemble")).strip() if pd.notna(rrow.get("Section")) else "Full Ensemble"
        if not section:
            section = "Full Ensemble"

        durs = [safe_int(x, 0) for x in df_r["Rehearsal Time (minutes)"].tolist()]
        durs = [d for d in durs if d > 0]

        break_offset = 0
        if break_mins > 0 and len(durs) >= 2:
            break_offset = choose_break_offset_favor_longer_first_half(durs)

        elapsed = 0

        for _, item in df_r.iterrows():
            mins = safe_int(item["Rehearsal Time (minutes)"], 0)
            if mins <= 0:
                continue

            if break_mins > 0 and break_offset > 0 and elapsed == break_offset:
                br_start = start_dt + pd.Timedelta(minutes=elapsed)
                br_end = br_start + pd.Timedelta(minutes=break_mins)
                out_rows.append({
                    "Rehearsal": rnum_i,
                    "Date": date,
                    "Title": "Break",
                    "Time in Rehearsal": br_start.strftime("%H:%M"),
                    "Break Start (HH:MM)": br_start.strftime("%H:%M"),
                    "Break End (HH:MM)": br_end.strftime("%H:%M"),
                    "Section": section,
                    "Event Type": event_type,
                })
                elapsed += break_mins

            it_start = start_dt + pd.Timedelta(minutes=elapsed)
            out_rows.append({
                "Rehearsal": rnum_i,
                "Date": date,
                "Title": str(item["Title"]),
                "Time in Rehearsal": it_start.strftime("%H:%M"),
                "Break Start (HH:MM)": "",
                "Break End (HH:MM)": "",
                "Section": section,
                "Event Type": event_type,
            })
            elapsed += mins

    # Add placeholder rows for rehearsals without allocated works (e.g., sectionals)
    print(f"[TIMED] Processed rehearsals: {processed_rehearsals}")
    print(f"[TIMED] All rehearsals in prepared data: {rehearsals_prepared['Rehearsal'].unique().tolist()}")
    
    for _, rrow in rehearsals_prepared.iterrows():
        rnum_i = safe_int(rrow.get("Rehearsal"), None)
        if rnum_i is None or rnum_i in processed_rehearsals:
            continue
        
        print(f"[TIMED] Adding placeholder for rehearsal {rnum_i}")
        
        date = rrow.get("Date")
        print(f"[TIMED]   Date from rrow: {repr(date)} (type: {type(date).__name__})")
        
        # Ensure date is in a format that pd.to_datetime can handle later
        # Handle NaT explicitly
        if pd.isna(date):
            print(f"[TIMED]   Date is NaT/NaN - using placeholder date")
            date_for_placeholder = "2000-01-01"  # Fallback date
        elif isinstance(date, str):
            # Keep string as-is (e.g., "2026-02-15")
            date_for_placeholder = date
        elif hasattr(date, 'strftime'):
            # Convert date/datetime objects to ISO string
            date_for_placeholder = date.strftime("%Y-%m-%d")
        else:
            date_for_placeholder = str(date)
        
        print(f"[TIMED]   Date for placeholder: {repr(date_for_placeholder)} (type: {type(date_for_placeholder).__name__})")
        
        start_dt = pd.to_datetime(rrow.get("Start DateTime"), errors="coerce")
        if pd.isna(start_dt):
            # Use the actual date from rehearsal and default time
            start_time_str = rrow.get("Start Time", "14:00")
            if pd.notna(start_time_str) and start_time_str:
                start_dt = pd.to_datetime(f"{date_for_placeholder} {start_time_str}", errors="coerce")
            if pd.isna(start_dt):
                start_dt = pd.to_datetime(f"{date_for_placeholder} 14:00")
        
        section = str(rrow.get("Section", "Full Ensemble")).strip() if pd.notna(rrow.get("Section")) else "Full Ensemble"
        if not section:
            section = "Full Ensemble"
        
        event_type = str(rrow.get("Event Type", "Rehearsal")).strip() if pd.notna(rrow.get("Event Type")) else "Rehearsal"
        if not event_type:
            event_type = "Rehearsal"
        
        # Determine display title based on event type
        if event_type == "Concert":
            display_title = "Concert"
        elif event_type == "Sectional":
            display_title = "Sectional Rehearsal"
        else:
            display_title = "Rehearsal"
        
        # Add a placeholder row for this rehearsal/concert/sectional
        # For concerts, this creates a visible column in the timeline editor
        out_rows.append({
            "Rehearsal": rnum_i,
            "Date": date_for_placeholder,  # Use normalized date
            "Title": display_title,
            "Time in Rehearsal": start_dt.strftime("%H:%M"),
            "Break Start (HH:MM)": "",
            "Break End (HH:MM)": "",
            "Section": section,
            "Event Type": event_type,
        })

    out = pd.DataFrame(out_rows)
    print(f"[TIMED] Before date conversion, sample dates: {[r.get('Date') for r in out_rows[:3]]}")
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    print(f"[TIMED] After date conversion, sample dates: {out['Date'].head(3).tolist()}")
    print(f"[TIMED] NaT count in Date column: {out['Date'].isna().sum()}")
    out["Rehearsal"] = pd.to_numeric(out["Rehearsal"], errors="coerce").astype("Int64")
    return out


# ----------------------------
# Routes
# ----------------------------

from flask import request, redirect, url_for

@app.before_request
def require_login_for_everything():
    # allow static assets
    if request.endpoint == "static":
        return None

    # allow login/register endpoints
    allowed = {
        "login_view", "login_post",
        "register_view", "register_post",
        "home", "my_view", "api_member_rehearsals",
    }
    # allow password reset endpoints
    allowed.update({
        "forgot_password_view", "forgot_password_post",
        "reset_password_view", "reset_password_post",
    })
    if request.endpoint in allowed:
        return None

    # allow None endpoints (very rare) without crashing
    if request.endpoint is None:
        return None

    # if not logged in, bounce to login
    if not current_user():
        return redirect(url_for("login_view", next=request.path))


@app.get("/api/s/<schedule_id>/state")
def api_schedule_state(schedule_id):
    r = admin_required_or_403()
    if r: return r
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    # Debug: Log that Event Type is being passed through
    rehearsals = s.get("rehearsals", [])
    if rehearsals and len(rehearsals) > 0:
        first_reh = rehearsals[0]
        has_event_type = "Event Type" in first_reh
        print(f"[API STATE] Returning {len(rehearsals)} rehearsals, Event Type present: {has_event_type}, first reh: {list(first_reh.keys())[:5]}...")
    
    # Get timed data and add concert_id links (same as /data endpoint)
    timed_data = s.get("timed", [])
    print(f"\n[API STATE] Schedule: {schedule_id}")
    print(f"[API STATE] Timed data rows: {len(timed_data)}")
    print(f"[API STATE] Rehearsals: {len(rehearsals)}")
    
    # Add concert_id to timed rows for concert rehearsals
    ensembles = load_ensembles()
    print(f"[API STATE] Loaded {len(ensembles)} ensembles")
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    print(f"[API STATE] Looking for ensemble_id: {s.get('ensemble_id')}")
    print(f"[API STATE] Found ensemble: {ensemble['name'] if ensemble else 'None'}")
    
    schedule_concerts = []  # Initialize outside if block
    if ensemble:
        concerts = ensemble.get("concerts", [])
        print(f"[API STATE] Total concerts in ensemble: {len(concerts)}")
        print(f"[API STATE] All concerts: {[{'id': c.get('id'), 'title': c.get('title'), 'schedule_id': c.get('schedule_id')} for c in concerts]}")
        schedule_concerts = [c for c in concerts if c.get("schedule_id") == schedule_id]
        print(f"[API STATE] Concerts for schedule {schedule_id}: {len(schedule_concerts)}")
        for c in schedule_concerts:
            print(f"  - {c.get('id')}: {c.get('title')} on {c.get('date')}")
        # Link concerts into rehearsals by concert_id or by date as a fallback
        if rehearsals:
            for reh in rehearsals:
                if reh.get("Event Type") in ["Concert", "Contest"]:
                    # If rehearsal already has concert_id, ensure it exists; otherwise, try to find by date
                    cid = reh.get("concert_id")
                    concert = None
                    if cid:
                        concert = next((c for c in schedule_concerts if c.get("id") == cid), None)
                    if not concert:
                        reh_date_norm = str(reh.get("Date", ""))[:10]
                        concert = next((c for c in schedule_concerts if str(c.get("date", ""))[:10] == reh_date_norm), None)
                        if concert:
                            reh["concert_id"] = concert.get("id")
                    # If we found a concert, push master fields into rehearsal for display consistency
                    if concert:
                        reh["Date"] = concert.get("date", reh.get("Date"))
                        reh["Start Time"] = concert.get("time", reh.get("Start Time"))
                        reh["Venue"] = concert.get("venue", reh.get("Venue", ""))
                        reh["Uniform"] = concert.get("uniform", reh.get("Uniform", ""))
                    else:
                        print(f"[API STATE] Concert rehearsal without linked concert: Rehearsal {reh.get('Rehearsal')} Date {reh.get('Date')}")

            # Also propagate concert_id into timed rows
            for timed_row in timed_data:
                reh_num = timed_row.get("Rehearsal")
                if reh_num:
                    reh = next((r for r in rehearsals if int(r.get("Rehearsal", 0)) == reh_num), None)
                    if reh and reh.get("Event Type") in ["Concert", "Contest"]:
                        cid = reh.get("concert_id")
                        if cid:
                            timed_row["concert_id"] = cid
    
    return jsonify({
        "works_cols": s.get("works_cols", []),
        "rehearsals_cols": s.get("rehearsals_cols", []),
        "works": s.get("works", []),
        "rehearsals": s.get("rehearsals", []),
        "allocation": s.get("allocation", []),
        "schedule": s.get("schedule", []),
        "timed": timed_data,
        "concerts": schedule_concerts,  # Always use schedule_concerts (initialized above)
        "ensemble_id": s.get("ensemble_id"),
    })

@app.get("/api/s/<schedule_id>/data")
def api_schedule_data(schedule_id):
    """Get current schedule data (timed, rehearsals, schedule, allocation)."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    # Clean the timed data before returning
    timed_data = clean_timed_data(s.get("timed", []))
    
    # Add concert_id to timed rows for concert rehearsals
    rehearsals_list = s.get("rehearsals", [])
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    
    schedule_concerts = []
    if ensemble and rehearsals_list:
        # Build a map of rehearsal date -> concert
        concerts = ensemble.get("concerts", [])
        schedule_concerts = [c for c in concerts if c.get("schedule_id") == schedule_id]
        
        for timed_row in timed_data:
            reh_num = timed_row.get("Rehearsal")
            if reh_num:
                # Find rehearsal by number
                reh = next((r for r in rehearsals_list if int(r.get("Rehearsal", 0)) == reh_num), None)
                if reh and reh.get("Event Type") in ["Concert", "Contest"]:
                    # Find the concert for this rehearsal by matching date
                    reh_date = reh.get("Date")
                    # Normalize dates for comparison (strip time component)
                    reh_date_normalized = str(reh_date)[:10] if reh_date else None
                    
                    # Try to match concert by date (comparing just YYYY-MM-DD)
                    concert = None
                    for c in concerts:
                        concert_date_normalized = str(c.get("date", ""))[:10]
                        if concert_date_normalized == reh_date_normalized and c.get("schedule_id") == schedule_id:
                            concert = c
                            break
                    
                    if concert:
                        timed_row["concert_id"] = concert.get("id")
    
    return jsonify({
        "timed": timed_data,
        "rehearsals": s.get("rehearsals", []),
        "schedule": s.get("schedule", []),
        "allocation": s.get("allocation", []),
        "concerts": schedule_concerts,  # Add concerts for editor
    })


@app.get("/api/s/<schedule_id>/concerts")
def api_schedule_concerts(schedule_id):
    """Get concerts linked to this schedule."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    # Get linked concerts from the ensemble
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    
    if not ensemble:
        return jsonify({"concerts": []})
    
    # Get rehearsal dates from this schedule for matching
    s = load_schedule(schedule_id)
    rehearsal_dates = set()
    if s:
        for reh in s.get("rehearsals", []):
            if reh.get("Date"):
                try:
                    dt = pd.to_datetime(reh.get("Date"))
                    rehearsal_dates.add(dt.strftime("%Y-%m-%d 00:00:00"))
                except:
                    pass
    
    # Include concerts that match this schedule_id OR have dates matching rehearsals in this schedule
    all_concerts = ensemble.get("concerts", [])
    linked_concerts = []
    for c in all_concerts:
        if c.get("status") == "scheduled":
            concert_date_str = c.get("date", "")
            if c.get("schedule_id") == schedule_id or concert_date_str in rehearsal_dates:
                linked_concerts.append(c)
    
    return jsonify({"concerts": linked_concerts})


@app.put("/api/s/<schedule_id>/concert/<concert_id>")
def api_update_schedule_concert(schedule_id, concert_id):
    """Update a concert linked to this schedule."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    data = request.get_json() or {}
    ensemble_id = s.get("ensemble_id")
    
    # Update the concert in ensembles.json
    concert = update_concert(ensemble_id, concert_id, data)
    if not concert:
        return jsonify({"ok": False, "error": "Concert not found"}), 404

    # Also update the linked rehearsal row in the schedule (if present)
    updated_rehearsal = False
    for reh_row in s.get("rehearsals", []):
        if reh_row.get("concert_id") == concert_id or (
            reh_row.get("Event Type") in ["Concert", "Contest"] and not reh_row.get("concert_id")
        ):
            # Update rehearsal row with concert data
            reh_row["Title"] = concert.get("title")
            reh_row["Date"] = concert.get("date")
            reh_row["Start Time"] = concert.get("time")
            reh_row["Venue"] = concert.get("venue")
            reh_row["Uniform"] = concert.get("uniform")
            reh_row["concert_id"] = concert_id
            updated_rehearsal = True
            break
    if updated_rehearsal:
        save_schedule(s)

    # Ensure response JSON has no NaN/inf values that would break JSON.parse in the client
    concert_clean = {k: _json_safe(v) for k, v in concert.items()}
    return jsonify({"ok": True, "concert": concert_clean})


@app.post("/api/s/<schedule_id>/save_inputs")
def api_schedule_save_inputs(schedule_id):
    r = admin_required_or_403()
    if r: return r
    payload = request.get_json(force=True)

    s = load_schedule(schedule_id)
    if not s: abort(404)

    # PERSISTENCE FIX: Ensure all values are strings to prevent loss of data
    works = payload.get("works", [])
    rehearsals = payload.get("rehearsals", [])
    
    # Sanitize works - ensure all values are strings
    works = [{k: (str(v) if v not in (None, "") else "") for k, v in row.items()} for row in works]
    rehearsals = [{k: (str(v) if v not in (None, "") else "") for k, v in row.items()} for row in rehearsals]
    
    s["works"] = works
    s["rehearsals"] = rehearsals
    # PERSISTENCE FIX: Save column metadata so it persists across page reloads
    if payload.get("works_cols"):
        s["works_cols"] = payload.get("works_cols")
    if payload.get("rehearsals_cols"):
        s["rehearsals_cols"] = payload.get("rehearsals_cols")
    if payload.get("clear_computed"):
        s["allocation"] = []
        s["schedule"] = []
        s["timed"] = []
    
    # Sync concert dates: update ensemble.concerts when concert rehearsal dates change
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    if ensemble and "concerts" in ensemble:
        for reh in rehearsals:
            event_type = reh.get("Event Type", "")
            if event_type == "Concert":
                reh_date = reh.get("Date", "")
                concert_id = reh.get("concert_id")
                
                # Normalize the date
                if reh_date:
                    try:
                        dt = pd.to_datetime(reh_date)
                        normalized_date = dt.strftime("%Y-%m-%d 00:00:00")
                    except:
                        normalized_date = str(reh_date)
                    
                    # Find and update the concert
                    if concert_id:
                        concert = next((c for c in ensemble["concerts"] if c.get("id") == concert_id), None)
                        if concert:
                            print(f"[SAVE INPUTS] Updating concert {concert_id} date from {concert.get('date')} to {normalized_date}")
                            concert["date"] = normalized_date
        
        save_ensembles(ensembles)
    
    s["updated_at"] = int(time.time())
    save_schedule(s)
    return jsonify({"ok": True})


@app.post("/api/s/<schedule_id>/run_allocation")
def api_schedule_run_allocation(schedule_id):
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    works, rehearsals, _, _ = get_frames(s)
    G = int(s.get("G", DEFAULT_G))
    
    # Filter to only include rehearsals where Event Type is "Rehearsal"
    # This excludes Sectionals and Concerts from allocation
    print(f"[ALLOCATION] Pre-filter rehearsals: {len(rehearsals)} rows")
    rehearsals_for_allocation = rehearsals[rehearsals['Event Type'].str.lower() == 'rehearsal'].copy()
    print(f"[ALLOCATION] Post-filter rehearsals (Event Type='Rehearsal' only): {len(rehearsals_for_allocation)} rows")
    if len(rehearsals_for_allocation) < 2:
        return jsonify({
            "ok": False, 
            "error": f"Need at least 2 'Rehearsal' type events for allocation. Found {len(rehearsals_for_allocation)} rehearsals (Sectionals and Concerts are excluded from allocation)."
        }), 400

    try:
        result = run_allocation_compute(works, rehearsals_for_allocation, G)
        print("[DEBUG] run_allocation_compute result:", result)
        if result is None:
            raise ValueError("run_allocation_compute returned None")
        alloc_df, warnings = result
        if alloc_df is None:
            raise ValueError("alloc_df is None after run_allocation_compute")
        raw_records = alloc_df.to_dict(orient="records")
        print("[DEBUG] Raw allocation records before sanitize_df_records:", raw_records)
        allocation_records = sanitize_df_records(raw_records)
        print("[DEBUG] Output from sanitize_df_records:", allocation_records)
        if allocation_records is None:
            raise ValueError("sanitize_df_records returned None")
        s["allocation"] = allocation_records
        s["warnings"] = warnings
        s["updated_at"] = int(time.time())
        save_schedule(s)
        # After saving, reload and print allocation from file
        try:
            s2 = load_schedule(schedule_id)
            print("[DEBUG] Allocation in saved file:", s2.get("allocation"))
        except Exception as e2:
            print(f"[DEBUG] Error reading saved schedule: {e2}")
        return jsonify({"ok": True, "warnings": warnings})
    except ValueError as e:
        print(f"[ALLOCATION] ValueError: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[ALLOCATION] Exception: {str(e)}\n{error_detail}")
        return jsonify({"ok": False, "error": f"Allocation computation failed: {str(e)}"}), 500


@app.post("/api/s/<schedule_id>/run_allocation_preview")
def api_schedule_run_allocation_preview(schedule_id):
    """Run allocation computation but don't save results to the schedule file.
    Returns allocation records and warnings so the client can preview without
    overwriting server-side timed/schedule data.
    """
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    works, rehearsals, _, _ = get_frames(s)
    G = int(s.get("G", DEFAULT_G))
    # Debug: show rehearsal frame structure before filtering
    try:
        print(f"[ALLOCATION DEBUG] rehearsals columns: {list(rehearsals.columns)}")
        print(f"[ALLOCATION DEBUG] rehearsals head:\n{rehearsals.head().to_dict(orient='records')}")
        if 'Event Type' in rehearsals.columns:
            print(f"[ALLOCATION DEBUG] Event Type unique values: {rehearsals['Event Type'].dropna().unique().tolist()}")
    except Exception as _:
        pass

    # Filter to only include rehearsals where Event Type is "Rehearsal"
    # Use safe string operations in case values are missing
    if 'Event Type' in rehearsals.columns:
        rehearsals_for_allocation = rehearsals[rehearsals['Event Type'].astype(str).str.strip().str.lower() == 'rehearsal'].copy()
    else:
        rehearsals_for_allocation = rehearsals[[]]
    if len(rehearsals_for_allocation) < 2:
        return jsonify({"ok": False, "error": f"Need at least 2 'Rehearsal' type events for allocation. Found {len(rehearsals_for_allocation)} rehearsals."}), 400

    try:
        result = run_allocation_compute(works, rehearsals_for_allocation, G)
        if result is None:
            raise ValueError("run_allocation_compute returned None")
        alloc_df, warnings = result
        if alloc_df is None:
            raise ValueError("alloc_df is None after run_allocation_compute")
        raw_records = alloc_df.to_dict(orient="records")
        allocation_records = sanitize_df_records(raw_records)
        if allocation_records is None:
            allocation_records = []
        return jsonify({"ok": True, "allocation": allocation_records, "warnings": warnings})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[ALLOCATION PREVIEW] Exception: {str(e)}\n{error_detail}")
        return jsonify({"ok": False, "error": f"Allocation preview failed: {str(e)}"}), 500


@app.post("/api/s/<schedule_id>/generate_schedule")
def api_schedule_generate(schedule_id):
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    works, rehearsals, alloc, _ = get_frames(s)
    if alloc.empty:
        return jsonify({"ok": False, "error": "No allocation yet"}), 400

    sched = build_schedule_from_allocation(works, alloc)
    s["schedule"] = sanitize_df_records(sched.to_dict(orient="records"))

    # Use prepare_all_rehearsals to include non-allocated rehearsals (sectionals)
    rehe_prep = prepare_all_rehearsals(rehearsals)
    timed = compute_timed_df(sched, rehe_prep)
    s["timed"] = sanitize_df_records(timed.to_dict(orient="records"))
    s["generated_at"] = pd.Timestamp.utcnow().isoformat() + "Z"
    s["updated_at"] = int(time.time())
    save_schedule(s)

    return jsonify({"ok": True})


@app.get("/api/s/<schedule_id>/rehearsal/<int:rehearsal_num>/concerts")
def api_get_rehearsal_concerts(schedule_id, rehearsal_num):
    """
    Get associated concerts for a rehearsal.
    """
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    # Get concerts from the schedule's ensemble
    ensemble_id = s.get("ensemble_id")
    if not ensemble_id:
        return jsonify({"concerts": []})
    
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
    if not ensemble:
        return jsonify({"concerts": []})
    
    concerts_data = ensemble.get("concerts", [])
    associated_concerts = [
        {"id": c.get("id"), "title": c.get("title"), "date": c.get("date")}
        for c in concerts_data 
        if (c.get("schedule_id") == schedule_id 
           and int(c.get("rehearsal_num", -1)) == rehearsal_num)
    ]
    
    return jsonify({"concerts": associated_concerts})


@app.post("/api/s/<schedule_id>/import_csv")
def api_schedule_import_csv(schedule_id):
    r = admin_required_or_403()
    if r: return r

    actor = current_user()

    kind = (request.args.get("kind") or "").lower()
    if kind not in {"works", "rehearsals"}:
        return jsonify({"ok": False, "error": "kind must be works or rehearsals"}), 400

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    df = pd.read_csv(f.stream)  # type: ignore

    s = load_schedule(schedule_id)
    if not s: abort(404)

    if kind == "works":
        cols = list(default_works_df().columns)
        df = df.reindex(columns=[c for c in df.columns if c in cols])
        df = df.reindex(columns=cols, fill_value="")
        s["works"] = df.fillna("").to_dict(orient="records")
    else:
        cols = list(default_rehearsals_df().columns)
        df = df.reindex(columns=[c for c in df.columns if c in cols])
        df = df.reindex(columns=cols, fill_value="")
        rehearsals_data = df.fillna("").to_dict(orient="records")
        # Auto-number rehearsals if missing or empty
        rehearsals_data = auto_number_rehearsals(rehearsals_data)
        # Ensure Include in allocation column is present
        rehearsals_data = ensure_include_in_allocation_column(rehearsals_data)
        # Ensure Section column is present
        rehearsals_data = ensure_section_column(rehearsals_data)
        # Ensure Event Type column is present
        rehearsals_data = ensure_event_type_column(rehearsals_data)
        s["rehearsals"] = rehearsals_data

    # importing should clear computed artifacts
    s["allocation"] = []
    s["schedule"] = []
    s["timed"] = []
    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="import_csv", description=f"Imported {kind} table", actor=actor, meta={"kind": kind})
    save_schedule(s)

    return jsonify({"ok": True})


@app.delete("/api/s/<schedule_id>/rehearsal/<int:rehearsal_num>")
def api_delete_rehearsal(schedule_id, rehearsal_num):
    """
    Delete a rehearsal and optionally associated concerts.
    If GET: returns list of associated concerts
    If DELETE: removes rehearsal and specified concerts (from concert_ids in request body)
    """
    r = admin_required_or_403()
    if r: return r

    actor = current_user()

    s = load_schedule(schedule_id)
    if not s: abort(404)

    # Get concerts from the schedule's ensemble
    ensemble_id = s.get("ensemble_id")
    ensembles = load_ensembles() if ensemble_id else []
    ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
    
    concerts_data = ensemble.get("concerts", []) if ensemble else []
    associated_concerts = [
        c for c in concerts_data 
        if (c.get("schedule_id") == schedule_id 
           and int(c.get("rehearsal_num", -1)) == rehearsal_num)
    ]
    
    # Get list of concert IDs to delete from request body
    data = request.get_json() or {}
    concert_ids_to_delete = data.get("concert_ids", [])
    
    # Remove from rehearsals table
    rehearsals = s.get("rehearsals", [])
    s["rehearsals"] = [r for r in rehearsals if int(r.get("Rehearsal", -1)) != rehearsal_num]

    # Remove from timed table
    timed = s.get("timed", [])
    s["timed"] = [t for t in timed if int(t.get("Rehearsal", -1)) != rehearsal_num]

    # Remove from allocation (any columns like "Rehearsal X")
    allocation = s.get("allocation", [])
    col_name = f"Rehearsal {rehearsal_num}"
    for row in allocation:
        if col_name in row:
            del row[col_name]
    
    # Remove from schedule table
    schedule = s.get("schedule", [])
    s["schedule"] = [w for w in schedule if int(w.get("Rehearsal", -1)) != rehearsal_num]

    # --- CLEANUP: Remove orphaned timed entries referencing deleted rehearsals ---
    valid_reh_nums = set(int(r.get("Rehearsal", -1)) for r in s["rehearsals"])
    s["timed"] = [t for t in s["timed"] if int(t.get("Rehearsal", -1)) in valid_reh_nums]
    # Optionally, clean up allocation and schedule as well
    s["schedule"] = [w for w in s["schedule"] if int(w.get("Rehearsal", -1)) in valid_reh_nums]
    for row in s["allocation"]:
        for k in list(row.keys()):
            if k.startswith("Rehearsal "):
                try:
                    k_num = int(k.split(" ")[1])
                except Exception:
                    continue
                if k_num not in valid_reh_nums:
                    del row[k]

    # Remove only selected concerts from the ensemble
    if concert_ids_to_delete and ensemble:
        concerts_data = [c for c in concerts_data if c.get("id") not in concert_ids_to_delete]
        ensemble["concerts"] = concerts_data
        save_ensembles(ensembles)

    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="delete_rehearsal", description=f"Deleted rehearsal {rehearsal_num}", actor=actor)
    save_schedule(s)

    return jsonify({"ok": True, "concerts_deleted": len(concert_ids_to_delete)})


@app.put("/api/s/<schedule_id>/timed_edit")
def api_timed_edit(schedule_id):
    """
    Update timed schedule with drag-reorder or resize edits.
    Payload: {
        "timed": [...updated timed rows...],
        "action": "reorder" | "resize" | "delete",
        "description": "human-readable description of change"
    }
    """
    r = admin_required_or_403()
    if r: return r

    actor = current_user()

    s = load_schedule(schedule_id)
    if not s: abort(404)

    data = request.get_json() or {}
    timed_updates = data.get("timed", [])
    action = data.get("action", "edit")
    description = data.get("description", f"Timed edit ({action})")

    if not isinstance(timed_updates, list):
        return jsonify({"ok": False, "error": "timed must be a list"}), 400

    # Save current timed as history entry before updating
    if s.get("timed"):
        history_entry = {
            "timestamp": int(time.time()),
            "action": action,
            "description": description,
            "timed": s["timed"],  # snapshot before edit
        }
        if "timed_history" not in s:
            s["timed_history"] = []
        s["timed_history"].append(history_entry)
        # Keep last 50 versions to avoid unbounded growth
        if len(s["timed_history"]) > 50:
            s["timed_history"] = s["timed_history"][-50:]

    # Update timed schedule and clean up missing Section values
    timed_updates = clean_timed_data(timed_updates)
    # Prepare new timed and compute diffs against previous snapshot for richer audit
    old_timed = history_entry.get("timed") if 'history_entry' in locals() else []
    new_timed = clean_timed_data(timed_updates)
    s["timed"] = new_timed
    s["updated_at"] = int(time.time())
    
    # Recalculate allocation from the new timed data
    # Group timed data by (Title, Rehearsal) and sum durations
    if timed_updates:
        timed_df = pd.DataFrame(timed_updates)
        
        # Build allocation: group by Title, then sum time per rehearsal
        alloc_rows = []
        
        # Get the original allocation to preserve "Required Minutes" from original
        orig_alloc = s.get("allocation", [])
        orig_by_title = {row.get("Title"): row for row in orig_alloc if row.get("Title") != "Break"}
        
        # Get all unique titles and rehearsals
        titles = set(timed_df[timed_df["Title"] != "Break"]["Title"].unique())
        rehearsals = sorted(set(int(r) for r in timed_df[timed_df["Title"] != "Break"]["Rehearsal"].unique()))
        
        for title in sorted(titles):
            alloc_row = {"Title": title}
            
            # Get Required Minutes from original allocation (cached persistent value)
            orig_row = orig_by_title.get(title, {})
            required_mins = orig_row.get("Required Minutes", 0)
            alloc_row["Required Minutes"] = required_mins
            
            # Sum time allocated in each rehearsal
            total_allocated = 0
            for rnum in rehearsals:
                col = f"Rehearsal {rnum}"
                time_in_reh = timed_df[(timed_df["Title"] == title) & (timed_df["Rehearsal"] == str(rnum))]["Rehearsal Time (minutes)"]
                
                # Handle both column names
                if time_in_reh.empty:
                    time_in_reh = timed_df[(timed_df["Title"] == title) & (timed_df["Rehearsal"] == str(rnum))]["Time in Rehearsal"]
                
                time_val = int(time_in_reh.sum()) if not time_in_reh.empty else 0
                alloc_row[col] = time_val
                total_allocated += time_val
            
            # Calculate Time Remaining
            alloc_row["Time Remaining"] = required_mins - total_allocated
            alloc_rows.append(alloc_row)
        
        s["allocation"] = alloc_rows
    
    # Update the schedule with new durations from timed, but preserve all other metadata
    # This keeps the original ordering and player load calculations intact
    if s.get("timed") and s.get("schedule"):
        timed_df = pd.DataFrame(s.get("timed", []))
        
        # Build a map of timed durations keyed by (Title, Rehearsal as int)
        timed_map = {}
        for _, row in timed_df.iterrows():
            title = row.get("Title", "").strip()
            rehearsal = int(row.get("Rehearsal", 0)) if row.get("Rehearsal") else 0
            # Use the duration column (could be "Rehearsal Time (minutes)" or "Time in Rehearsal")
            duration = row.get("Rehearsal Time (minutes)") or row.get("Time in Rehearsal") or 0
            if duration and not pd.isna(duration):
                try:
                    duration = int(duration)
                except (ValueError, TypeError):
                    duration = 0
            else:
                duration = 0
            timed_map[(title, rehearsal)] = duration
        
        print(f"DEBUG: Built timed_map with {len(timed_map)} entries")
        if timed_map:
            sample_keys = list(timed_map.keys())[:3]
            print(f"  Sample keys: {sample_keys}")
        
        # Update schedule rows with new durations, preserve all other columns
        updated_count = 0
        for sched_row in s.get("schedule", []):
            title = sched_row.get("Title", "").strip()
            if title != "Break":
                rehearsal = int(sched_row.get("Rehearsal", 0)) if sched_row.get("Rehearsal") else 0
                key = (title, rehearsal)
                
                if key in timed_map:
                    new_duration = timed_map[key]
                    # Update the duration columns
                    sched_row["Rehearsal Time (minutes)"] = new_duration
                    if "Time in Rehearsal" in sched_row:
                        sched_row["Time in Rehearsal"] = new_duration
                    updated_count += 1
                else:
                    print(f"  WARNING: No timed entry found for {key}")
        
        print(f"  Updated {updated_count} schedule rows with new durations")
    
    save_schedule(s)

    # Compute human-readable changes between old_timed and new_timed
    try:
        def _build_aggregates(timed_list):
            # aggregates durations per (title, rehearsal)
            agg = {}
            for row in (timed_list or []):
                title = (row.get("Title") or "").strip()
                try:
                    reh = int(row.get("Rehearsal") or 0)
                except Exception:
                    reh = 0
                dur = row.get("Rehearsal Time (minutes)") or row.get("Time in Rehearsal") or 0
                try:
                    dur = int(dur)
                except Exception:
                    dur = 0
                agg.setdefault(title, {}).setdefault(reh, 0)
                agg[title][reh] += dur
            return agg

        def _build_row_signatures(timed_list):
            # Exact row signatures to detect added/removed rows
            sigs = set()
            for row in (timed_list or []):
                title = (row.get("Title") or "").strip()
                try:
                    reh = int(row.get("Rehearsal") or 0)
                except Exception:
                    reh = 0
                dur = row.get("Rehearsal Time (minutes)") or row.get("Time in Rehearsal") or 0
                time_str = str(row.get("Time in Rehearsal") or row.get("Rehearsal Time (minutes)") or "")
                sigs.add((title, reh, str(dur), time_str))
            return sigs

        old_agg = _build_aggregates(old_timed)
        new_agg = _build_aggregates(new_timed)
        old_sigs = _build_row_signatures(old_timed)
        new_sigs = _build_row_signatures(new_timed)

        changes = []

        # Detect removed and added exact rows
        removed = sorted(list(old_sigs - new_sigs))
        added = sorted(list(new_sigs - old_sigs))
        for r in removed:
            changes.append({"type": "row_removed", "title": r[0], "rehearsal": r[1], "duration": int(r[2]) if r[2].isdigit() else r[2], "time": r[3]})
        for a in added:
            changes.append({"type": "row_added", "title": a[0], "rehearsal": a[1], "duration": int(a[2]) if a[2].isdigit() else a[2], "time": a[3]})

        # Detect moved rehearsals per title
        titles = set(list(old_agg.keys()) + list(new_agg.keys()))
        for t in sorted(titles):
            old_rehs = set(old_agg.get(t, {}).keys())
            new_rehs = set(new_agg.get(t, {}).keys())
            if old_rehs and new_rehs and old_rehs != new_rehs:
                changes.append({"type": "moved", "title": t, "from": sorted(list(old_rehs)), "to": sorted(list(new_rehs))})

        # Detect duration changes per (title, rehearsal)
        common_titles = set(old_agg.keys()) & set(new_agg.keys())
        for t in sorted(common_titles):
            rehs = set(old_agg.get(t, {}).keys()) & set(new_agg.get(t, {}).keys())
            for reh in sorted(rehs):
                old_d = old_agg[t].get(reh, 0)
                new_d = new_agg[t].get(reh, 0)
                if old_d != new_d:
                    changes.append({"type": "duration_changed", "title": t, "rehearsal": reh, "old": old_d, "new": new_d})

        short_summary = f"{len(changes)} change(s)"
        add_audit_entry(s, action="timed_edit", description=f"Timed edit ({action}): {short_summary}", actor=actor, meta={"action": action, "count": len(timed_updates), "changes": changes})
        # Persist the audit entry we just added
        try:
            save_schedule(s)
            print(f"[AUDIT] Saved timed_edit audit for schedule {schedule_id} ({short_summary})")
        except Exception as e:
            print(f"[AUDIT] Failed to save schedule after adding timed_edit audit: {e}")
    except Exception as e:
        print(f"[AUDIT] Failed to compute timed diff: {e}")
    
    # Log for verification
    print(f"✓ Saved {len(timed_updates)} timed entries to schedule {schedule_id}")
    if timed_updates:
        print(f"  First 3 entries: {timed_updates[:3]}")
    print(f"  Updated allocation: {len(s.get('allocation', []))} rows")
    print(f"  Updated schedule with new durations: {len(s.get('schedule', []))} rows (preserved metadata)")

    # Optional email notification (opt-in to avoid noise)
    notified = False
    recipient_count = 0
    error = None
    if data.get("notify"):
        notified, recipients, error = notify_schedule_update(
            s,
            description,
            actor=actor,
            recipients_override=data.get("recipients") if isinstance(data.get("recipients"), list) else None,
        )
        recipient_count = len(recipients)

    return jsonify({
        "ok": True,
        "history_len": len(s.get("timed_history", [])),
        "notified": bool(notified),
        "recipient_count": recipient_count,
        "error": error,
    })


@app.route("/api/s/<schedule_id>/notify_update", methods=["GET", "POST"])
def api_notify_update(schedule_id):
    """Manually trigger a schedule update notification without changing data."""
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    if s.get("status") != "published":
        return jsonify({"ok": False, "error": "Schedule is not published"}), 400

    # Provide recipient list for UI selection
    recipients = get_member_recipients(s.get("ensemble_id"), exclude_user_id=None, include_actor=True)
    if request.method == "GET":
        ensemble_name = (get_ensemble_by_id(s.get("ensemble_id")) or {}).get("name", s.get("ensemble_id") or "Schedule")
        schedule_link = url_for("schedule_view", schedule_id=schedule_id, _external=True)
        default_body = (
            f"There have been some updates to a rehearsal schedule for {ensemble_name}:\n\n"
            "Best wishes,\nJack"
        )

        return jsonify({
            "ok": True,
            "recipients": recipients,
            "default_subject": _default_notification_subject(ensemble_name),
            "default_body": default_body,
            "schedule_link": schedule_link,
        })

    data = request.get_json() or {}
    actor = current_user()

    ensemble_name = (get_ensemble_by_id(s.get("ensemble_id")) or {}).get("name", s.get("ensemble_id") or "Schedule")
    subject = (data.get("subject") or "").strip() or _default_notification_subject(ensemble_name)
    body_message = (data.get("body") or data.get("description") or "").strip() or _default_notification_body(ensemble_name)

    recipients_override = data.get("recipients") if isinstance(data.get("recipients"), list) else None

    notified, recipients, error = notify_schedule_update(
        s,
        body_message,
        actor=actor,
        recipients_override=recipients_override,
        subject_override=subject,
    )
    return jsonify({"ok": True, "notified": bool(notified), "recipient_count": len(recipients), "recipients": recipients, "error": error})


@app.post("/api/s/<schedule_id>/timed_revert")
def api_timed_revert(schedule_id):
    """
    Revert timed schedule to a previous version from history.
    Payload: {"history_index": <int>}
    history_index=-1 means most recent, -2 means second most recent, etc.
    """
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    data = request.get_json() or {}
    history_index = data.get("history_index", -1)

    history = s.get("timed_history", [])
    if not history:
        return jsonify({"ok": False, "error": "No history available"}), 400

    if history_index < -len(history) or history_index >= 0:
        return jsonify({"ok": False, "error": "Invalid history index"}), 400

    # Save current state as a new history entry (for redo capability)
    if s.get("timed"):
        history_entry = {
            "timestamp": int(time.time()),
            "action": "revert",
            "description": f"Reverted to version {history_index}",
            "timed": s["timed"],
        }
        history.append(history_entry)

    # Restore the requested version
    target_entry = history[history_index]
    s["timed"] = target_entry["timed"]
    s["updated_at"] = int(time.time())
    save_schedule(s)

    return jsonify({"ok": True, "restored_at": target_entry["timestamp"]})


@app.get("/api/s/<schedule_id>/timed_history")
def api_timed_history(schedule_id):
    """Get list of timed edits with timestamps and descriptions."""
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s: abort(404)

    history = s.get("timed_history", [])
    # Return without the full 'timed' data, just metadata
    return jsonify([
        {
            "index": i - len(history),  # negative indices for reverting
            "timestamp": h["timestamp"],
            "action": h["action"],
            "description": h["description"],
        }
        for i, h in enumerate(history)
    ])


@app.put("/api/s/<schedule_id>/section/<int:rehearsal_num>")
def api_update_section(schedule_id, rehearsal_num):
    """Update the Section field for a specific rehearsal."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    data = request.get_json() or {}
    section = data.get("section", "Full Ensemble").strip()
    
    # Update all rows for this rehearsal with the new section
    timed = s.get("timed", [])
    for row in timed:
        if int(row.get("Rehearsal", 0)) == rehearsal_num:
            row["Section"] = section if section else "Full Ensemble"
    
    s["timed"] = timed
    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="rehearsal_section", description=f"Section set to {section}", actor=current_user(), meta={"rehearsal": rehearsal_num})
    save_schedule(s)
    
    return jsonify({"ok": True, "section": section})


@app.put("/api/s/<schedule_id>/rehearsal-event-type/<int:rehearsal_num>")
def api_update_rehearsal_event_type(schedule_id, rehearsal_num):
    """Update Event Type and Section for a specific rehearsal."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    data = request.get_json() or {}
    event_type = data.get("Event Type", "Rehearsal").strip()
    section = data.get("Section", "Full Ensemble").strip()
    
    # Update rehearsals table
    rehearsals = s.get("rehearsals", [])
    for row in rehearsals:
        if int(row.get("Rehearsal", 0)) == rehearsal_num:
            row["Event Type"] = event_type if event_type else "Rehearsal"
            row["Section"] = section if section else "Full Ensemble"
    
    # Update timed data
    timed = s.get("timed", [])
    for row in timed:
        if int(row.get("Rehearsal", 0)) == rehearsal_num:
            row["Section"] = section if section else "Full Ensemble"
    
    s["rehearsals"] = rehearsals
    s["timed"] = timed
    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="rehearsal_event_type", description=f"Event type set to {event_type}", actor=current_user(), meta={"rehearsal": rehearsal_num, "section": section})
    save_schedule(s)
    
    return jsonify({"ok": True, "event_type": event_type, "section": section})


@app.put("/api/s/<schedule_id>/rehearsal/<int:rehearsal_num>")
def api_update_rehearsal(schedule_id, rehearsal_num):
    """Update rehearsal details (date, time, event, include_in_allocation, section, venue, uniform, event_type) in rehearsals table."""
    r = admin_required_or_403()
    if r: return r
    
    s = load_schedule(schedule_id)
    if not s: abort(404)
    
    data = request.get_json() or {}
    print(f"[DEBUG] api_update_rehearsal called with data: {data}")
    
    # Update rehearsals table
    rehearsals_data = s.get("rehearsals", [])
    updated_row = None
    for row in rehearsals_data:
        if int(row.get("Rehearsal", 0)) == rehearsal_num:
            print(f"[DEBUG] Found rehearsal {rehearsal_num}")
            # Keep copy of previous state for audit diff
            old_row = dict(row)
            # Update the fields provided
            if "date" in data:
                row["Date"] = data["date"]
            if "start_time" in data:
                row["Start Time"] = data["start_time"]
            if "end_time" in data:
                row["End Time"] = data["end_time"]
            if "event" in data:
                row["Event"] = data["event"]
            if "include_in_allocation" in data:
                row["Include in allocation"] = data["include_in_allocation"]
            if "section" in data:
                row["Section"] = data["section"] if data["section"] else "Full Ensemble"
            if "event_type" in data:
                row["Event Type"] = data["event_type"]
            if "venue" in data:
                row["Venue"] = data["venue"]
            if "uniform" in data:
                row["Uniform"] = data["uniform"]
            if "time" in data:
                row["Time"] = data["time"]
            if "work" in data:
                row["work"] = data["work"]
            updated_row = row
            break
    
    s["rehearsals"] = rehearsals_data
    
    # Clean NaN values from rehearsals data before saving
    for row in s["rehearsals"]:
        for key, value in list(row.items()):
            if pd.isna(value):
                row[key] = None
    
    # Re-ensure Event Type column is set based on Event field if event_type wasn't explicitly provided
    if "event_type" not in data:
        s["rehearsals"] = ensure_event_type_column(s["rehearsals"])
    
    # If this is a Concert or Contest event type, sync with concert object
    if updated_row and (updated_row.get("Event Type") in ["Concert", "Contest"] or 
                       (data.get("event_type") in ["Concert", "Contest"]) or
                       (data.get("event") and str(data.get("event")).strip().lower() in ["concert", "contest"])):
        ensembles = load_ensembles()
        ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
        if ensemble:
            concert_date = updated_row.get("Date", "")
            concert_time = updated_row.get("Start Time", "")
            concert_venue = data.get("venue", updated_row.get("Venue", ""))
            concert_uniform = data.get("uniform", updated_row.get("Uniform", ""))
            concert_id = updated_row.get("concert_id")
            
            # Normalize date for concert storage
            if concert_date:
                try:
                    dt = pd.to_datetime(concert_date)
                    concert_date = dt.strftime("%Y-%m-%d 00:00:00")
                    updated_row["Date"] = concert_date  # Update rehearsal row with normalized date
                except:
                    concert_date = str(concert_date)
            
            # Try to find existing concert by concert_id or date
            concert = None
            if concert_id:
                concert = next((c for c in ensemble.get("concerts", []) if c.get("id") == concert_id), None)
            
            if not concert and concert_date:
                # Try to find by schedule_id + old date (in case date is being changed)
                concert = next((c for c in ensemble.get("concerts", []) 
                               if c.get("schedule_id") == schedule_id and 
                               c.get("date") != concert_date), None)
            
            if concert:
                # Update existing concert
                print(f"[UPDATE REHEARSAL] Updating existing concert {concert.get('id')}")
                
                # Regenerate title with new date
                try:
                    dt = pd.to_datetime(concert_date)
                    day = dt.day
                    if 10 <= day % 100 <= 20:
                        suffix = 'th'
                    else:
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                    formatted_date = dt.strftime(f'%A %B {day}{suffix}')
                    concert["title"] = f"{ensemble.get('name', 'Unknown Ensemble')} - Concert - {formatted_date}"
                except:
                    pass
                
                concert["date"] = concert_date
                concert["time"] = concert_time
                if concert_venue:
                    concert["venue"] = concert_venue
                if concert_uniform:
                    concert["uniform"] = concert_uniform
                # Store concert_id in rehearsal row for easy linking
                updated_row["concert_id"] = concert["id"]
            else:
                # Create new concert
                print(f"[UPDATE REHEARSAL] Creating new concert for {concert_date}")
                concert_id = f"concert_{uuid.uuid4().hex[:12]}"
                
                # Format date for title
                try:
                    dt = pd.to_datetime(concert_date)
                    day = dt.day
                    if 10 <= day % 100 <= 20:
                        suffix = 'th'
                    else:
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                    formatted_date = dt.strftime(f'%A %B {day}{suffix}')
                except:
                    formatted_date = str(concert_date)
                
                new_concert = {
                    "id": concert_id,
                    "title": f"{ensemble.get('name', 'Unknown Ensemble')} - Concert - {formatted_date}",
                    "date": concert_date,
                    "time": concert_time,
                    "venue": concert_venue,
                    "uniform": concert_uniform,
                    "programme": "",
                    "other_info": "",
                    "status": "scheduled",
                    "schedule_id": schedule_id,
                    "is_auto_generated": True
                }
                if "concerts" not in ensemble:
                    ensemble["concerts"] = []
                ensemble["concerts"].append(new_concert)
                updated_row["concert_id"] = concert_id
                print(f"[UPDATE REHEARSAL] Created concert {concert_id}")
            
            save_ensembles(ensembles)
    
    # Track event type changes so we can adjust timed rows (e.g., strip works when converting to a concert)
    event_type_changed = False
    if "event_type" in data and updated_row:
        old_event_type = None
        # Find the old event type value
        for row in s.get("rehearsals", []):
            if int(row.get("Rehearsal", 0)) == rehearsal_num:
                old_event_type = row.get("Event Type", "Rehearsal")
                break
        
        new_event_type = data["event_type"]
        if old_event_type != new_event_type:
            event_type_changed = True
            print(f"[UPDATE REHEARSAL] Event type changed from {old_event_type} to {new_event_type}, updating timed rows")
    
    # Update timed schedule - sync dates and section from rehearsals table
    timed_data = s.get("timed", [])

    new_event_type = updated_row.get("Event Type", "Rehearsal") if updated_row else "Rehearsal"
    section_value = updated_row.get("Section") if updated_row else None
    section_value = section_value if section_value else "Full Ensemble"

    start_time_value = data.get("start_time") or data.get("time")
    if not start_time_value and updated_row:
        start_time_value = updated_row.get("Start Time") or updated_row.get("Time")
    updated_existing_row = False

    if event_type_changed and new_event_type == "Concert":
        # Converting to a concert: drop prior timed rows for this rehearsal and add a concert placeholder
        timed_data = [row for row in timed_data if int(row.get("Rehearsal", 0)) != rehearsal_num]
        timed_data = upsert_concert_timed_rows(
            timed_data,
            rehearsal_num,
            {
                "id": updated_row.get("concert_id") if updated_row else None,
                "date": updated_row.get("Date") if updated_row else data.get("date"),
                "time": start_time_value,
                "title": (updated_row.get("Event") if updated_row else None) or "Concert",
            },
            section_value,
        )
    else:
        # Update existing timed rows in place
        for row in timed_data:
            if int(row.get("Rehearsal", 0)) == rehearsal_num:
                if "date" in data:
                    row["Date"] = data["date"]
                if "section" in data:
                    row["Section"] = section_value
                if event_type_changed:
                    row["Event Type"] = new_event_type
                    if new_event_type != "Concert":
                        row.pop("concert_id", None)
                if new_event_type == "Concert":
                    row["Event Type"] = "Concert"
                    if updated_row and updated_row.get("concert_id"):
                        row["concert_id"] = updated_row.get("concert_id")
                    if start_time_value:
                        row["Time in Rehearsal"] = start_time_value
                else:
                    # If event label was edited, normalize and propagate
                    if "event" in data and data["event"]:
                        event_val = str(data["event"]).strip().lower()
                        if event_val in ["concert", "sectional", "rehearsal"]:
                            row["Event Type"] = event_val.capitalize()
                updated_existing_row = True

        # If no timed rows existed for this concert, add a placeholder so it shows immediately
        if new_event_type == "Concert" and not updated_existing_row:
            timed_data = upsert_concert_timed_rows(
                timed_data,
                rehearsal_num,
                {
                    "id": updated_row.get("concert_id") if updated_row else None,
                    "date": updated_row.get("Date") if updated_row else data.get("date"),
                    "time": start_time_value,
                    "title": (updated_row.get("Event") if updated_row else None) or "Concert",
                },
                section_value,
            )

    s["timed"] = clean_timed_data(timed_data)
    
    s["updated_at"] = int(time.time())
    # Build detailed change list
    try:
        changes = []
        for k in data.keys():
            old = old_row.get(k) if 'old_row' in locals() else None
            # Map input keys to stored field names where needed
            field_map = {
                'start_time': 'Start Time',
                'end_time': 'End Time',
                'event': 'Event',
                'include_in_allocation': 'Include in allocation',
                'section': 'Section',
                'event_type': 'Event Type',
                'venue': 'Venue',
                'uniform': 'Uniform',
                'time': 'Time',
                'work': 'work',
                'date': 'Date',
            }
            stored_key = field_map.get(k, k)
            old_val = old_row.get(stored_key) if 'old_row' in locals() else None
            new_val = None
            # derive new_val from updated_row
            if updated_row:
                new_val = updated_row.get(stored_key)
            if (old_val or None) != (new_val or None):
                changes.append({"field": stored_key, "old": old_val, "new": new_val})
        add_audit_entry(s, action="rehearsal_update", description=f"Rehearsal {rehearsal_num} updated ({len(changes)} change(s))", actor=current_user(), meta={"rehearsal": rehearsal_num, "changes": changes})
    except Exception as e:
        print(f"[AUDIT] Failed to compute rehearsal diffs: {e}")
    save_schedule(s)
    
    return jsonify({"ok": True, "rehearsal": rehearsal_num, "updated": data})


# ----------------------------
# Attendance
# ----------------------------


def _ensure_event_bucket(schedule: dict, rehearsal_num: int) -> dict:
    att = ensure_attendance(schedule)
    key = str(rehearsal_num)
    bucket = att.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
        att[key] = bucket
    return bucket


def _ensemble_members(ensemble_id: str) -> List[dict]:
    memberships = load_memberships()
    users_by_id = {u.get("id"): u for u in load_users()}
    members = []
    for m in memberships:
        if m.get("ensemble_id") != ensemble_id:
            continue
        if m.get("status", "active") not in {"active", "pending"}:
            continue
        u = users_by_id.get(m.get("user_id")) or {}
        members.append({
            "user_id": m.get("user_id"),
            "email": u.get("email"),
            "name": user_display_name(u),
            "instrument": u.get("instrument"),
            "status": m.get("status", "active"),
        })
    return members


@app.post("/api/s/<schedule_id>/attendance/respond")
def api_attendance_respond(schedule_id):
    """Member/Admin respond to an event. Past events are locked."""
    u = current_user()
    if not u:
        return redirect(url_for("login_view", next=request.path))

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    # Auth: admins always allowed; members must belong to ensemble
    if not u.get("is_admin") and not is_member(u.get("id"), s.get("ensemble_id")):
        abort(403)

    payload = request.get_json(force=True) or {}
    rehearsal_num = payload.get("event_id") or payload.get("rehearsal_num")
    status = (payload.get("status") or "").strip().lower()
    note = (payload.get("note") or "").strip()

    if rehearsal_num is None:
        return jsonify({"ok": False, "error": "event_id is required"}), 400
    try:
        rehearsal_num = int(rehearsal_num)
    except Exception:
        return jsonify({"ok": False, "error": "event_id must be a number"}), 400

    if status not in {"yes", "no", "maybe"}:
        return jsonify({"ok": False, "error": "status must be yes, no, or maybe"}), 400
    if status == "maybe" and not note:
        return jsonify({"ok": False, "error": "note is required for maybe"}), 400

    if event_is_past(s, rehearsal_num):
        return jsonify({"ok": False, "error": "Cannot respond to past events"}), 400

    bucket = _ensure_event_bucket(s, rehearsal_num)
    bucket[u.get("id")] = {
        "status": status,
        "note": note,
        "responded_at": int(time.time()),
    }
    s["attendance"] = s.get("attendance") or {}
    s["attendance"][str(rehearsal_num)] = bucket
    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="attendance_response", description=f"{status} for rehearsal {rehearsal_num}", actor=u, meta={"rehearsal": rehearsal_num})
    save_schedule(s)

    # Queue batched notification for admins (debounced 5 minutes after last action by this user)
    try:
        _queue_attendance_notification(s.get("id"), u.get("id"), rehearsal_num, status, note)
    except Exception as e:
        print(f"[ATTENDANCE] Failed to queue notification: {e}")

    return jsonify({"ok": True, "attendance": bucket.get(u.get("id"))})


@app.get("/api/s/<schedule_id>/attendance/mine")
def api_attendance_mine(schedule_id):
    u = current_user()
    if not u:
        return redirect(url_for("login_view", next=request.path))

    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    if not u.get("is_admin") and not is_member(u.get("id"), s.get("ensemble_id")):
        abort(403)

    att = ensure_attendance(s)
    mine = {}
    for ev_id, responses in att.items():
        if not isinstance(responses, dict):
            continue
        if u.get("id") in responses:
            mine[ev_id] = responses[u.get("id")]
    return jsonify({"ok": True, "attendance": mine})


@app.get("/api/s/<schedule_id>/attendance/event/<int:rehearsal_num>")
def api_attendance_event(schedule_id, rehearsal_num):
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    responses = _ensure_event_bucket(s, rehearsal_num)
    members = _ensemble_members(s.get("ensemble_id"))
    rows = []
    for m in members:
        row = {
            "user_id": m.get("user_id"),
            "email": m.get("email"),
            "name": m.get("name"),
            "instrument": m.get("instrument"),
            "status": None,
            "note": None,
            "responded_at": None,
        }
        if m.get("user_id") in responses:
            row.update(responses[m.get("user_id")])
        rows.append(row)

    return jsonify({
        "ok": True,
        "event_id": rehearsal_num,
        "responses": rows,
    })


@app.get('/api/s/<schedule_id>/attendance/summary')
def api_attendance_summary(schedule_id):
    r = admin_required_or_403()
    if r: return r
    s = load_schedule(schedule_id)
    if not s: abort(404)

    members = _ensemble_members(s.get('ensemble_id'))
    rehearsals = s.get('rehearsals', [])
    # build list of labelled columns (use Rehearsal number order)
    dates = []
    reh_order = []
    for reh in rehearsals:
        try:
            rnum = int(reh.get('Rehearsal', -1))
        except Exception:
            continue
        reh_order.append(rnum)
        # Build a human-friendly column label including rehearsal number / concert and formatted date
        event_type = (reh.get('Event Type') or reh.get('Event') or '').strip()
        if not event_type:
            event_type = reh.get('Event Type') or 'Rehearsal'
        date_label = format_human_date(reh.get('Date') or reh.get('date') or '')
        if event_type and event_type.lower().startswith('concert'):
            title = reh.get('Title') or 'Concert'
            label = f"Concert ({title}) — {date_label}"
        else:
            label = f"Rehearsal {rnum} — {date_label}"
        dates.append(label)

    att = ensure_attendance(s)

    rows = []
    for idx, m in enumerate(members):
        user_id = m.get('user_id')
        row = {'instrument': m.get('instrument') or '', 'name': m.get('name') or '', 'email': m.get('email') or '', 'responses': {}}
        for i, reh in enumerate(reh_order):
            bucket = att.get(str(reh)) or {}
            resp = bucket.get(user_id) or {}
            col_label = dates[i] if i < len(dates) else str(safe_date_for_column(reh, rehearsals))
            row['responses'][col_label] = resp.get('status') if resp.get('status') else ''
        rows.append(row)

    return jsonify({'ok': True, 'dates': dates, 'rows': rows})


def safe_date_for_column(reh_num, rehearsals):
    for reh in rehearsals:
        try:
            if int(reh.get('Rehearsal', -1)) == reh_num:
                return str(reh.get('Date') or '')
        except Exception:
            continue
    return ''


@app.get('/api/s/<schedule_id>/attendance/export')
def api_attendance_export(schedule_id):
    r = admin_required_or_403()
    if r: return r
    s = load_schedule(schedule_id)
    if not s: abort(404)

    members = _ensemble_members(s.get('ensemble_id'))
    rehearsals = s.get('rehearsals', [])
    reh_order = []
    dates = []
    # Build labelled column headers like 'Rehearsal 1 — 16th March 2026' or 'Concert (Title) — 16th March 2026'
    for reh in rehearsals:
        try:
            rnum = int(reh.get('Rehearsal', -1))
        except Exception:
            continue
        reh_order.append(rnum)
        event_type = (reh.get('Event Type') or reh.get('Event') or '').strip()
        if not event_type:
            event_type = reh.get('Event Type') or 'Rehearsal'
        date_label = format_human_date(reh.get('Date') or reh.get('date') or '')
        if event_type and event_type.lower().startswith('concert'):
            title = reh.get('Title') or 'Concert'
            label = f"Concert ({title}) — {date_label}"
        else:
            label = f"Rehearsal {rnum} — {date_label}"
        dates.append(label)

    att = ensure_attendance(s)

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    header = ['Instrument','Name','Email'] + dates
    writer.writerow(header)
    for idx, m in enumerate(members):
        row = [m.get('instrument') or '', m.get('name') or '', m.get('email') or '']
        for i, reh in enumerate(reh_order):
            bucket = att.get(str(reh)) or {}
            resp = bucket.get(m.get('user_id')) or {}
            row.append(resp.get('status') or '')
        writer.writerow(row)

    csv_data = output.getvalue()
    output.close()
    return Response(csv_data, mimetype='text/csv', headers={"Content-Disposition": f"attachment; filename=attendance_{schedule_id}.csv"})


def _upcoming_events(schedule: dict) -> List[int]:
    ids = []
    for reh in schedule.get("rehearsals", []):
        try:
            rnum = int(reh.get("Rehearsal", -1))
        except Exception:
            continue
        if rnum < 0:
            continue
        if not event_is_past(schedule, rnum):
            ids.append(rnum)
    return ids


@app.post("/api/s/<schedule_id>/attendance/remind")
def api_attendance_remind(schedule_id):
    """Send reminder emails to members who have not responded for upcoming events."""
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    ensemble = get_ensemble_by_id(s.get("ensemble_id"))
    ensemble_name = (ensemble or {}).get("name", "Ensemble")
    att = ensure_attendance(s)

    payload = request.get_json(silent=True) or {}
    target_event = payload.get("event_id")
    if target_event is not None:
        try:
            target_event = int(target_event)
        except Exception:
            return jsonify({"ok": False, "error": "event_id must be numeric"}), 400
        event_ids = [target_event] if not event_is_past(s, target_event) else []
    else:
        event_ids = _upcoming_events(s)

    if not event_ids:
        return jsonify({"ok": False, "error": "No upcoming events to remind"}), 400

    members = _ensemble_members(s.get("ensemble_id"))
    by_user = {m.get("user_id"): m for m in members}

    reminders_by_user = {}
    for ev_id in event_ids:
        bucket = att.get(str(ev_id)) or {}
        for uid, member in by_user.items():
            if not member.get("email"):
                continue
            if uid in bucket and bucket[uid].get("status"):
                continue  # already responded
            reminders_by_user.setdefault(uid, {"member": member, "events": []})["events"].append(ev_id)

    if not reminders_by_user:
        return jsonify({"ok": False, "message": "No recipients (all responded)"})

    sent = 0
    for uid, payload in reminders_by_user.items():
        member = payload["member"]
        ev_list = sorted(payload["events"])
        # Build a simple list of dates for the email body
        lines = []
        for ev_id in ev_list:
            reh = get_rehearsal_row(s, ev_id) or {}
            date_str = reh.get("Date") or "(date TBC)"
            title = reh.get("Title") or reh.get("Event Type") or "Rehearsal"
            lines.append(f"- {title} on {date_str} (Rehearsal {ev_id})")

        body = [
            f"<p>Hi {html.escape(member.get('name') or member.get('email') or 'there')},</p>",
            f"<p>Please confirm your attendance for upcoming {html.escape(ensemble_name)} events:</p>",
            "<ul>" + "".join(f"<li>{html.escape(l)}</li>" for l in lines) + "</ul>",
            f"<p>Respond via the schedule: <a href='{url_for('schedule_view', schedule_id=schedule_id, _external=True)}'>view schedule</a>.</p>",
            "<p>Thank you!</p>",
        ]
        brevo_send_email([member.get("email")], f"Attendance needed for {ensemble_name}", "\n".join(body), bcc_mode=False)
        sent += 1

    add_audit_entry(s, action="attendance_reminder", description=f"Sent reminders to {sent} member(s)", actor=current_user(), meta={"events": event_ids})
    s["updated_at"] = int(time.time())
    save_schedule(s)

    return jsonify({"ok": True, "recipients": len(reminders_by_user)})


@app.get("/s/<schedule_id>")
def schedule_view(schedule_id):
    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    u = current_user()
    # Permissions:
    if u and u.get("is_admin"):
        pass
    else:
        if s.get("status") != "published":
            abort(404)  # hide drafts
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r

    # Get ensemble name and concerts
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    ensemble_name = ensemble["name"] if ensemble else s.get("ensemble_id", "Schedule")
    
    # Get all concerts for this ensemble (filter by schedule_id if set, but also include concerts without schedule_id)
    linked_concerts = []
    if ensemble:
        # Get all concerts that either match this schedule_id OR have no schedule_id but match dates in this schedule
        all_concerts = ensemble.get("concerts", [])
        
        # Get rehearsal dates from this schedule for matching
        rehearsal_dates = set()
        rehearsal_dates_norm = set()
        for reh in s.get("rehearsals", []):
            if reh.get("Date"):
                try:
                    dt = pd.to_datetime(reh.get("Date"))
                    rehearsal_dates.add(dt.strftime("%Y-%m-%d 00:00:00"))
                    rehearsal_dates_norm.add(dt.strftime("%Y-%m-%d"))
                except:
                    pass
        
        for c in all_concerts:
            # Include if: linked to this schedule OR concert date matches a rehearsal in this schedule
            if c.get("status") == "scheduled":
                concert_date_str = c.get("date", "")
                concert_date_norm = str(concert_date_str)[:10]
                if (
                    c.get("schedule_id") == schedule_id
                    or concert_date_str in rehearsal_dates
                    or concert_date_norm in rehearsal_dates_norm
                ):
                    linked_concerts.append(c)
        
        # Sort by date
        concerts_with_dates = []
        for c in linked_concerts:
            try:
                concert_date = pd.to_datetime(c.get("date", "")).date() if c.get("date") else None
                concerts_with_dates.append((concert_date, c))
            except:
                # Fallback: keep raw value (could be string) but mark as None-equivalent
                concerts_with_dates.append((c.get("date") if isinstance(c.get("date"), str) else None, c))

        # Normalize sort key so we never compare mixed types (str vs date)
        def _concert_key(item):
            d = item[0]
            # Dates and datetimes -> ISO string, sort before unknowns
            try:
                if isinstance(d, (_dt.date, _dt.datetime)):
                    return (0, d.isoformat())
            except Exception:
                pass
            # Strings that look like dates or raw date strings
            if isinstance(d, str) and d:
                return (0, d)
            # None or empty -> sort last
            return (1, "")

        concerts_with_dates.sort(key=_concert_key)
        # Preserve order but remove duplicate concerts with same id
        seen = set()
        deduped = []
        for _, c in concerts_with_dates:
            cid = c.get("id") or c.get("concert_id") or None
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            deduped.append(c)
        linked_concerts = deduped

    # Get timed data and clean it to ensure sections are populated
    timed_data = clean_timed_data(s.get("timed", []))
    
    # Add Event Type, Section, and work from rehearsals table to timed data
    rehearsals = s.get("rehearsals", [])
    event_type_map = {}
    work_map = {}
    section_map = {}
    for reh_row in rehearsals:
        reh_num = reh_row.get("Rehearsal")
        if reh_num is not None:
            event_type_map[int(reh_num)] = reh_row.get("Event Type", "Rehearsal")
            work_map[int(reh_num)] = reh_row.get("work", "")
            section_map[int(reh_num)] = reh_row.get("Section", "")
    
    for row in timed_data:
        reh_num = int(row.get("Rehearsal", 0))
        row["Event Type"] = event_type_map.get(reh_num, "Rehearsal")
        # Add Section from rehearsals table (overrides timed data if present)
        if section_map.get(reh_num):
            row["Section"] = section_map.get(reh_num)
        # For sectionals, add the work field if present
        if row["Event Type"] == "Sectional" and work_map.get(reh_num):
            row["work"] = work_map.get(reh_num)

    # Repair concert/contest timed rows to use the real date/time/title instead of placeholders
    concert_map = {c.get("id"): c for c in linked_concerts if c.get("id")}
    for row in timed_data:
        if row.get("Event Type") not in ["Concert", "Contest"]:
            continue
        cid = row.get("concert_id")
        concert = concert_map.get(cid)
        if not concert:
            continue
        date_val = row.get("Date")
        # Treat 2000-01-01 placeholder or missing/empty as stale
        if not date_val or str(date_val).startswith("2000-01-01"):
            row["Date"] = concert.get("date", date_val)
        time_val = row.get("Time in Rehearsal")
        if (not time_val or time_val == "13:00:00") and concert.get("time"):
            row["Time in Rehearsal"] = concert.get("time")
        title_val = row.get("Title")
        if not title_val or title_val in ["Concert", "Contest"]:
            row["Title"] = concert.get("title", title_val)

    # Fallback: ensure every linked concert/contest shows in the schedule view
    # If a concert has no timed rows (e.g., date change without regenerate), add a placeholder timed row
    concert_timed_reh_nums = {int(r.get("Rehearsal", 0)) for r in timed_data if r.get("Event Type") in ["Concert", "Contest"]}
    concert_ids_in_timed = {r.get("concert_id") for r in timed_data if r.get("Event Type") in ["Concert", "Contest"] and r.get("concert_id")}

    for idx, concert in enumerate(linked_concerts):
        if concert.get("id") in concert_ids_in_timed:
            continue

        # Try to find a matching concert/contest rehearsal by concert_id or date
        match_reh = None
        for reh in rehearsals:
            if reh.get("Event Type") not in ["Concert", "Contest"]:
                continue
            if reh.get("concert_id") and reh.get("concert_id") == concert.get("id"):
                match_reh = reh
                break
            # fallback match by date
            reh_date_norm = str(reh.get("Date", ""))[:10]
            if reh_date_norm and reh_date_norm == str(concert.get("date", ""))[:10]:
                match_reh = reh
                break

        if match_reh:
            reh_num = int(match_reh.get("Rehearsal", 0) or 0)
        else:
            # create a virtual rehearsal number far from real ones to avoid collision
            reh_num = 900000 + idx

        if reh_num in concert_timed_reh_nums:
            continue

        # Use the correct Event Type from the matched rehearsal
        event_type = match_reh.get("Event Type", "Concert") if match_reh else "Concert"
        
        timed_data.append({
            "Rehearsal": reh_num,
            "Date": concert.get("date", ""),
            "Title": concert.get("title", "Concert"),
            "Time in Rehearsal": concert.get("time", "19:00"),
            "Break Start (HH:MM)": "",
            "Break End (HH:MM)": "",
            "Section": "Full Ensemble",
            "Event Type": event_type,
            "concert_id": concert.get("id")
        })
        concert_timed_reh_nums.add(reh_num)

    
    # Debug logging
    print(f"\n[SCHEDULE VIEW] Schedule: {schedule_id}")
    print(f"[SCHEDULE VIEW] Linked concerts: {len(linked_concerts)}")
    for c in linked_concerts:
        print(f"  - {c.get('title')} on {c.get('date')}")
    print(f"[SCHEDULE VIEW] Timed data rows: {len(timed_data)}")
    concert_rows = [r for r in timed_data if r.get('Event Type') in ['Concert', 'Contest']]
    print(f"[SCHEDULE VIEW] Concert event type rows: {len(concert_rows)}")
    for cr in concert_rows:
        print(f"  - Rehearsal {cr.get('Rehearsal')}, Date: {cr.get('Date')}")
    
    # Build rehearsal grouping and a chronological order for rendering
    by_reh = {}
    for row in timed_data:
        rnum = row.get("Rehearsal")
        if rnum is None:
            continue
        by_reh.setdefault(rnum, []).append(row)

    def reh_sort_key(item):
        rnum, rows = item
        date_val = None
        if rows:
            date_val = rows[0].get("Date")
        try:
            dt = pd.to_datetime(date_val) if date_val is not None else None
        except Exception:
            dt = None
        return (dt if dt is not None else pd.Timestamp.max, int(rnum))

    reh_order = sorted(by_reh.items(), key=reh_sort_key)

    # Build map of rehearsal num to conducting log (if exists)
    conducting_logs_by_reh = {}
    for log in s.get("conducting_logs", []):
        reh_num = log.get("rehearsal_num")
        if reh_num:
            conducting_logs_by_reh[reh_num] = log

    timed = pd.DataFrame(timed_data)
    response = render_template(
        "view.html",
        timed=timed.to_dict(orient="records"),
        has_schedule=not timed.empty,
        ensemble_name=ensemble_name,
        schedule_id=schedule_id,
        user=u,
        concerts=linked_concerts,
        reh_order=reh_order,
        conducting_logs=conducting_logs_by_reh,
    )
    # Prevent caching so members see fresh data
    return response, 200, {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.post("/admin/schedules/create")
def admin_create_schedule():
    r = admin_required_or_403()
    if r: return r

    name = (request.form.get("name") or "Untitled").strip()
    ensemble_id = request.form.get("ensemble_id")
    if not ensemble_id:
        abort(400)

    u = current_user()
    s = make_new_schedule(ensemble_id, name, created_by=u["id"] if u else None)
    save_schedule(s)
    return redirect(url_for("admin_edit_schedule", schedule_id=s["id"]))

@app.post("/admin/s/<schedule_id>/set_status")
def admin_set_schedule_status(schedule_id):
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    status = request.form.get("status")
    if status not in {"draft", "published"}:
        abort(400)

    notify_publish = request.form.get("notify_publish") in {"on", "true", "1"}

    s["status"] = status
    s["updated_at"] = int(time.time())
    add_audit_entry(s, action="status_change", description=f"Status set to {status}", actor=current_user())
    # If publishing for the first time, record published_at and ensure followup tracking exists
    if status == "published" and not s.get("published_at"):
        s["published_at"] = int(time.time())
    if "followup_notifications" not in s or not isinstance(s.get("followup_notifications"), dict):
        s["followup_notifications"] = {}
    save_schedule(s)

    if status == "published" and notify_publish:
        actor = current_user()
        desc = f"{s.get('name', 'Schedule')} published"
        notify_schedule_update(s, desc, actor=actor)

    return redirect(url_for("admin_edit_schedule", schedule_id=schedule_id))


@app.get("/admin/s/<schedule_id>/audit")
def admin_schedule_audit(schedule_id):
    r = admin_required_or_403()
    if r: return r

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    ensemble = get_ensemble_by_id(s.get("ensemble_id"))
    log = sorted(s.get("audit_log", []), key=lambda e: e.get("ts", 0), reverse=True)
    return render_template("admin_audit.html", schedule=s, audit_log=log, ensemble=ensemble)


@app.get('/admin/s/<schedule_id>/attendance')
def admin_schedule_attendance(schedule_id):
    r = admin_required_or_403()
    if r: return r
    s = load_schedule(schedule_id)
    if not s: abort(404)
    return render_template('admin_attendance.html', schedule_id=schedule_id)


@app.get("/admin")
def admin_home():
    r = admin_required_or_403()
    if r: return r

    users = load_users()
    ensembles = load_ensembles()
    mem = load_memberships()

    # decorate: memberships by user
    mem_by_user = {}
    ens_by_id = {e["id"]: e for e in ensembles}
    for m in mem:
        mem_by_user.setdefault(m["user_id"], []).append(m)

    schedules = []
    for sid in list_schedule_ids():
        s = load_schedule(sid)
        if s:
            schedules.append({
                "id": s.get("id"),
                "name": s.get("name", "Untitled"),
                "ensemble_id": s.get("ensemble_id"),
                "status": s.get("status", "draft"),
                "updated_at": s.get("updated_at"),
            })

    schedules.sort(key=lambda x: (x["ensemble_id"] or "", x["name"] or ""))

    return render_template(
        "admin_home.html",
        user=current_user(),
        users=users,
        ensembles=ensembles,
        mem_by_user=mem_by_user,
        ens_by_id=ens_by_id,
        schedules=schedules,   # ✅ add this
    )


@app.post("/admin/migrate-day-column")
def admin_migrate_day_column():
    r = admin_required_or_403()
    if r: return r
    
    count = migrate_unnamed_columns_to_day()
    return {"success": True, "migrated": count}


@app.post("/admin/s/<schedule_id>/delete")
def admin_delete_schedule(schedule_id):
    r = admin_required_or_403()
    if r: return r

    # Load schedule to find ensemble_id
    s = load_schedule(schedule_id)
    ensemble_id = s.get("ensemble_id") if s else None
    
    # Delete associated concerts from ensemble
    if ensemble_id:
        ensembles = load_ensembles()
        ensemble = next((e for e in ensembles if e["id"] == ensemble_id), None)
        if ensemble:
            # Remove all concerts linked to this schedule
            original_count = len(ensemble.get("concerts", []))
            ensemble["concerts"] = [c for c in ensemble.get("concerts", []) if c.get("schedule_id") != schedule_id]
            removed_count = original_count - len(ensemble.get("concerts", []))
            if removed_count > 0:
                save_ensembles(ensembles)
                print(f"[DELETE SCHEDULE] Removed {removed_count} concert(s) linked to schedule {schedule_id}")
    
    # Delete schedule file
    p = schedule_path(schedule_id)
    if os.path.exists(p):
        os.remove(p)
    return redirect(url_for("admin_home"))



@app.post("/admin/ensembles/create")
def admin_create_ensemble():
    r = admin_required_or_403()
    if r: return r

    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)

    ensembles = load_ensembles()
    new_id = slugify_id(name)

    if any(e["id"] == new_id for e in ensembles):
        # ensure uniqueness
        new_id = f"{new_id}-{uuid.uuid4().hex[:4]}"

    ensembles.append({"id": new_id, "name": name})
    save_ensembles(ensembles)
    return redirect(url_for("admin_home"))

@app.post("/admin/ensembles/<ensemble_id>/rename")
def admin_rename_ensemble(ensemble_id):
    r = admin_required_or_403()
    if r: return r

    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        abort(400)

    ensembles = load_ensembles()
    e = next((x for x in ensembles if x["id"] == ensemble_id), None)
    if not e:
        abort(404)

    e["name"] = new_name
    save_ensembles(ensembles)
    return redirect(url_for("admin_home"))

@app.post("/admin/ensembles/<ensemble_id>/delete")
def admin_delete_ensemble(ensemble_id):
    r = admin_required_or_403()
    if r: return r

    # remove ensemble
    ensembles = [e for e in load_ensembles() if e["id"] != ensemble_id]
    save_ensembles(ensembles)

    # remove memberships pointing at it
    mem = [m for m in load_memberships() if m.get("ensemble_id") != ensemble_id]
    save_memberships(mem)

    # NOTE: when we add per-ensemble schedules, we’ll also delete schedules for this ensemble here.
    return redirect(url_for("admin_home"))

@app.post("/admin/users/<user_id>/set_admin")
def admin_set_admin(user_id):
    r = admin_required_or_403()
    if r: return r

    val = request.form.get("is_admin") == "true"
    set_user_admin_flag(user_id, val)
    return redirect(url_for("admin_home"))


@app.post("/admin/users/<user_id>/update_profile")
def admin_update_user_profile(user_id):
    r = admin_required_or_403()
    if r: return r

    users = load_users()
    u = next((x for x in users if x.get("id") == user_id), None)
    if not u:
        abort(404)

    new_email = (request.form.get("email") or u.get("email") or "").strip().lower()
    first_name = (request.form.get("first_name") or u.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or u.get("last_name") or "").strip()
    instrument = (request.form.get("instrument") or u.get("instrument") or "").strip()
    is_admin_flag = (request.form.get("is_admin") or "false").lower() in {"true", "1", "on"}

    if not new_email:
        abort(400)

    # Ensure unique email
    if any(other for other in users if other.get("id") != user_id and (other.get("email") or "").lower() == new_email):
        ensembles = load_ensembles()
        mem = load_memberships()
        mem_by_user = {}
        ens_by_id = {e["id"]: e for e in ensembles}
        for m in mem:
            mem_by_user.setdefault(m.get("user_id"), []).append(m)
        return render_template(
            "admin_home.html",
            error="Email already in use",
            users=users,
            ensembles=ensembles,
            mem=mem,
            ens_by_id=ens_by_id,
            mem_by_user=mem_by_user,
            user=current_user(),
        ), 400

    u["email"] = new_email
    u["first_name"] = first_name
    u["last_name"] = last_name
    u["instrument"] = instrument
    u["name"] = user_display_name(u)
    u["is_admin"] = is_admin_flag

    ensure_admin_if_matches_email(u)
    if u.get("is_admin") != is_admin_flag:
        set_user_admin_flag(user_id, is_admin_flag)
    save_users(users)

    return redirect(url_for("admin_home"))

@app.post("/admin/memberships/upsert")
def admin_membership_upsert():
    r = admin_required_or_403()
    if r: return r

    user_id = request.form.get("user_id")
    ensemble_id = request.form.get("ensemble_id")
    role = request.form.get("role", "member")
    status = request.form.get("status", "active")

    if not user_id or not ensemble_id:
        abort(400)
    if role not in {"member", "admin"}:
        abort(400)
    if status not in {"active", "pending"}:
        abort(400)

    upsert_membership(user_id, ensemble_id, role=role, status=status)
    return redirect(url_for("admin_home"))

@app.post("/admin/memberships/remove")
def admin_membership_remove():
    r = admin_required_or_403()
    if r: return r

    user_id = request.form.get("user_id")
    ensemble_id = request.form.get("ensemble_id")
    if not user_id or not ensemble_id:
        abort(400)
    remove_membership(user_id, ensemble_id)
    return redirect(url_for("admin_home"))


# ----------------------------
# API: Check for Outlook Events
# ----------------------------

@app.get("/api/admin/check-outlook-events")
def api_check_outlook_events():
    """Check for existing Outlook calendar events on a specific date."""
    r = admin_required_or_403()
    if r: return r
    
    selected_date = request.args.get("date", "").strip()
    if not selected_date:
        return {"events": []}, 400
    
    config = _outlook_calendar_config()
    if not config:
        return {"events": []}, 200  # No Outlook configured
    
    access_token = _graph_access_token(config)
    if not access_token:
        return {"events": []}, 200  # Failed to get token
    
    try:
        # Parse the date and create search window
        dt_date = pd.to_datetime(selected_date).date()
        start_dt = _dt.datetime.combine(dt_date, _dt.time(0, 0)).isoformat() + "Z"
        end_dt = _dt.datetime.combine(dt_date, _dt.time(23, 59)).isoformat() + "Z"
        
        base_user = config["user_principal_name"]
        calendar_id = config.get("calendar_id") or ""
        if calendar_id:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/calendarView"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendarView"
        
        params = {
            "startDateTime": start_dt,
            "endDateTime": end_dt,
            "$select": "id,subject",
            "$top": "100",
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        
        response = requests.get(endpoint, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json() or {}
        
        # Filter out cancelled events
        events = [
            {"id": event.get("id"), "subject": event.get("subject", "Untitled")}
            for event in payload.get("value", [])
            if event.get("id") and event.get("subject")
        ]
        
        return {"events": events}, 200
    except Exception as e:
        app.logger.exception(f"Failed to check Outlook events: {e}")
        return {"events": []}, 200


# ----------------------------
# Concert Management Routes (Admin)
# ----------------------------

@app.get("/admin/ensembles/<ensemble_id>/concerts")
def admin_view_concerts(ensemble_id):
    """View all concerts for an ensemble."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)

    concerts = get_ensemble_concerts(ensemble_id)
    schedules = [
        {"id": s.get("id"), "name": s.get("name", "Untitled")}
        for s in [load_schedule(sid) for sid in list_schedule_ids()]
        if s and s.get("ensemble_id") == ensemble_id
    ]

    return render_template("concerts.html", ensemble=ensemble, concerts=concerts, schedules=schedules)


@app.get("/admin/ensembles/<ensemble_id>/concerts/add")
def admin_add_concert_form(ensemble_id):
    """Show form to add a new concert."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)

    schedules = [
        {"id": s.get("id"), "name": s.get("name", "Untitled")}
        for s in [load_schedule(sid) for sid in list_schedule_ids()]
        if s and s.get("ensemble_id") == ensemble_id
    ]

    return render_template("concert_form.html", ensemble=ensemble, concert=None, schedules=schedules)


@app.post("/admin/ensembles/<ensemble_id>/concerts/add")
def admin_add_concert(ensemble_id):
    """Create a new concert."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)

    concert_data = {
        "date": request.form.get("date"),
        "time": request.form.get("time"),
        "venue": request.form.get("venue"),
        "uniform": request.form.get("uniform"),
        "programme": request.form.get("programme"),
        "other_info": request.form.get("other_info"),
        "schedule_id": request.form.get("schedule_id") or None,
        "status": request.form.get("status", "scheduled"),
    }

    try:
        concert = add_concert_to_ensemble(ensemble_id, concert_data)
        
        # Check if user selected an existing Outlook event to link to
        selected_outlook_event_id = request.form.get("selected_outlook_event_id", "").strip()
        
        # Sync to Outlook calendar if configured
        config = _outlook_calendar_config()
        if config and concert.get("status") == "scheduled":
            access_token = _graph_access_token(config)
            if access_token:
                event_title = f"{ensemble.get('name')} - {concert.get('title', 'Concert')}"
                
                if selected_outlook_event_id:
                    # Link to existing event
                    concert["outlook_event_id"] = selected_outlook_event_id
                    app.logger.info(f"Linked concert to existing Outlook event: {selected_outlook_event_id}")
                else:
                    # Create new event with dedup check
                    outlook_event_id = _outlook_event_write(
                        config, access_token,
                        event_title,
                        concert.get("date", ""),
                        concert.get("time", ""),
                        concert.get("venue", ""),
                        concert.get("programme", "")
                    )
                    if outlook_event_id:
                        concert["outlook_event_id"] = outlook_event_id
                        app.logger.info(f"Created new Outlook event: {outlook_event_id}")
                
                update_concert(ensemble_id, concert.get("id"), concert)
        
        return redirect(url_for("admin_view_concerts", ensemble_id=ensemble_id))
    except Exception as e:
        app.logger.exception(f"Failed to add concert: {e}")
        abort(400)


@app.get("/admin/ensembles/<ensemble_id>/concerts/<concert_id>/edit")
def admin_edit_concert_form(ensemble_id, concert_id):
    """Show form to edit a concert."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)

    concert = next((c for c in ensemble.get("concerts", []) if c.get("id") == concert_id), None)
    if not concert:
        abort(404)

    # Normalize date/time for HTML inputs
    if concert.get("date"):
        try:
            dt = pd.to_datetime(concert["date"])
            concert["date"] = dt.strftime("%Y-%m-%d")
        except Exception:
            concert["date"] = str(concert.get("date", ""))[:10]
    if concert.get("time"):
        try:
            t = pd.to_datetime(concert["time"]).time()
            concert["time"] = t.strftime("%H:%M")
        except Exception:
            concert["time"] = str(concert.get("time", ""))[:5]

    schedules = [
        {"id": s.get("id"), "name": s.get("name", "Untitled")}
        for s in [load_schedule(sid) for sid in list_schedule_ids()]
        if s and s.get("ensemble_id") == ensemble_id
    ]

    return render_template("concert_form.html", ensemble=ensemble, concert=concert, schedules=schedules)


@app.post("/admin/ensembles/<ensemble_id>/concerts/<concert_id>/edit")
def admin_edit_concert(ensemble_id, concert_id):
    """Update a concert."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)

    concert_data = {
        "title": request.form.get("title"),
        "date": request.form.get("date"),
        "time": request.form.get("time"),
        "venue": request.form.get("venue"),
        "uniform": request.form.get("uniform"),
        "programme": request.form.get("programme"),
        "other_info": request.form.get("other_info"),
        "schedule_id": request.form.get("schedule_id") or None,
        "status": request.form.get("status", "scheduled"),
    }

    try:
        concert = update_concert(ensemble_id, concert_id, concert_data)
        
        # Check if user selected an existing Outlook event to link to
        selected_outlook_event_id = request.form.get("selected_outlook_event_id", "").strip()
        
        # Sync to Outlook calendar if configured
        config = _outlook_calendar_config()
        if config and concert.get("status") == "scheduled":
            access_token = _graph_access_token(config)
            if access_token:
                event_title = f"{ensemble.get('name')} - {concert.get('title', 'Concert')}"
                
                if selected_outlook_event_id:
                    # User selected an existing event to link to
                    concert["outlook_event_id"] = selected_outlook_event_id
                    update_concert(ensemble_id, concert_id, concert)
                    app.logger.info(f"Linked concert to existing Outlook event: {selected_outlook_event_id}")
                elif concert.get("outlook_event_id"):
                    # Update existing linked event
                    success = _outlook_event_update(
                        config, access_token,
                        concert.get("outlook_event_id"),
                        event_title,
                        concert.get("date", ""),
                        concert.get("time", ""),
                        concert.get("venue", ""),
                        concert.get("programme", "")
                    )
                    if success:
                        app.logger.info(f"Updated Outlook event for concert: {event_title}")
                else:
                    # Concert is scheduled but no Outlook event - create one now
                    outlook_event_id = _outlook_event_write(
                        config, access_token,
                        event_title,
                        concert.get("date", ""),
                        concert.get("time", ""),
                        concert.get("venue", ""),
                        concert.get("programme", "")
                    )
                    if outlook_event_id:
                        concert["outlook_event_id"] = outlook_event_id
                        update_concert(ensemble_id, concert_id, concert)
                        app.logger.info(f"Created Outlook event for concert: {event_title}")
        
        # Apply venue to all events if requested
        apply_to_all = request.form.get("apply_venue_to_all", "").lower() == "on"
        if apply_to_all and concert_data.get("venue"):
            all_concerts = get_ensemble_concerts(ensemble_id)
            venue = concert_data.get("venue")
            for c in all_concerts:
                if c.get("id") != concert_id:  # Don't re-update the one we just edited
                    c["venue"] = venue
                    update_concert(ensemble_id, c.get("id"), c)
            app.logger.info(f"Applied venue '{venue}' to all concerts in ensemble {ensemble_id}")
        
        return redirect(url_for("admin_view_concerts", ensemble_id=ensemble_id))
    except Exception as e:
        app.logger.exception(f"Failed to edit concert: {e}")
        abort(400)


@app.post("/admin/ensembles/<ensemble_id>/concerts/<concert_id>/delete")
def admin_delete_concert(ensemble_id, concert_id):
    """Delete a concert."""
    r = admin_required_or_403()
    if r: return r

    ensemble = get_ensemble_by_id(ensemble_id)
    if not ensemble:
        abort(404)
    
    # Get the concert before deleting to access the Outlook event ID
    concert = next((c for c in ensemble.get("concerts", []) if c.get("id") == concert_id), None)
    outlook_event_id = concert.get("outlook_event_id") if concert else None
    
    # Delete from Outlook if it exists
    if outlook_event_id:
        config = _outlook_calendar_config()
        if config:
            access_token = _graph_access_token(config)
            if access_token:
                success = _outlook_event_delete(config, access_token, outlook_event_id)
                if success:
                    app.logger.info(f"Deleted Outlook event {outlook_event_id} for concert")
    
    if delete_concert(ensemble_id, concert_id):
        return redirect(url_for("admin_view_concerts", ensemble_id=ensemble_id))
    else:
        abort(404)


@app.get("/debug/paths")
def debug_paths():
    return {
        "DATA_DIR": DATA_DIR,
        "users_path": users_path(),
        "ensembles_path": ensembles_path(),
        "memberships_path": memberships_path(),
    }


# ---- Auth: register/login/logout (Phase 1)
@app.get("/register")
def register_view():
    ensembles = load_ensembles()
    invite_code = request.args.get("invite", "").strip()
    invite = None
    
    # Validate invitation code if provided
    if invite_code:
        invites = load_invitations()
        invite = next((i for i in invites if i.get("code") == invite_code and i.get("status") == "active"), None)
        
        # Check expiration and usage limits
        if invite:
            if invite.get("expires_at", 0) < int(time.time()):
                invite = None  # Expired
            elif invite.get("used_count", 0) >= invite.get("max_uses", 0):
                invite = None  # Max uses exceeded
            else:
                # Add ensemble name to invite for display
                ensemble = next((e for e in ensembles if e["id"] == invite.get("ensemble_id")), None)
                if ensemble:
                    invite["ensemble_name"] = ensemble.get("name")
    
    return render_template("register.html", ensembles=ensembles, invite_code=invite_code, invite=invite, instrument_options=INSTRUMENT_OPTIONS)


@app.post("/register")
def register_post():
    email = (request.form.get("email") or "").strip().lower()
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    instrument = (request.form.get("instrument") or "").strip()
    password = request.form.get("password") or ""
    ensemble_id = (request.form.get("ensemble_id") or "").strip()
    invite_code = (request.form.get("invite_code") or "").strip()

    if not email or not password or not first_name or not last_name:
        abort(400)

    users = load_users()
    if any(u for u in users if u.get("email") == email):
        return render_template(
            "register.html",
            ensembles=load_ensembles(),
            invite_code=invite_code,
            invite=None,
            instrument_options=INSTRUMENT_OPTIONS,
            error="Email already registered.",
        ), 400

    if instrument and instrument not in INSTRUMENT_OPTIONS:
        return render_template(
            "register.html",
            ensembles=load_ensembles(),
            invite_code=invite_code,
            invite=None,
            instrument_options=INSTRUMENT_OPTIONS,
            error="Please select an instrument from the list.",
        ), 400
    
    # Process invitation code
    invite = None
    if invite_code:
        invites = load_invitations()
        invite = next((i for i in invites if i.get("code") == invite_code and i.get("status") == "active"), None)
        
        if invite:
            # Check expiration and usage limits
            if invite.get("expires_at", 0) < int(time.time()):
                invite = None
            elif invite.get("used_count", 0) >= invite.get("max_uses", 0):
                invite = None
            else:
                # Valid invitation - use its ensemble_id
                ensemble_id = invite.get("ensemble_id")
                invite["used_count"] = invite.get("used_count", 0) + 1
                save_invitations(invites)

    # If no valid invitation and no ensemble selected, require ensemble
    if not ensemble_id:
        return render_template(
            "register.html",
            ensembles=load_ensembles(),
            invite_code=invite_code,
            invite=invite,
            instrument_options=INSTRUMENT_OPTIONS,
            error="Please select an ensemble or use a valid invitation code.",
        ), 400

    uid = uuid.uuid4().hex
    display_name = f"{first_name} {last_name}".strip()
    users.append({
        "id": uid,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "name": display_name,
        "instrument": instrument,
        "password_hash": generate_password_hash(password),
        "is_admin": False,
        "created_at": int(time.time()),
    })

    for user in users:
        ensure_admin_if_matches_email(user)
    save_users(users)

    memberships = load_memberships()
    memberships.append({"user_id": uid, "ensemble_id": ensemble_id, "status": "active"})
    save_memberships(memberships)

    session["user_id"] = uid
    return redirect(url_for("my_view"))


@app.get("/login")
def login_view():
    return render_template("login.html")


@app.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    u = next((x for x in load_users() if x.get("email") == email), None)
    if not u or not check_password_hash(u.get("password_hash", ""), password):
        return render_template("login.html", error="Invalid email or password."), 403
    
    changed = ensure_admin_if_matches_email(u)
    if changed:
        users = load_users()
        for x in users:
            if x.get("id") == u["id"]:
                x["is_admin"] = True
        save_users(users)

    session["user_id"] = u["id"]
    nxt = request.args.get("next")
    return redirect(nxt or url_for("my_view"))


@app.get("/logout")
def logout_view():
    # If the user has any pending attendance notifications, flush them immediately
    u = current_user()
    if u:
        try:
            _flush_user_pending_notifications(u.get("id"))
        except Exception:
            pass
    session.clear()
    return redirect(url_for("login_view"))


# ----------------------------
# Password reset (forgot password)
# ----------------------------
RESET_TOKEN_EXPIRY_SECONDS = int(os.environ.get("RESET_TOKEN_EXPIRY_SECONDS", "3600"))  # 1 hour default


def _clear_reset_token_for_user(user: dict):
    user.pop("reset_token", None)
    user.pop("reset_expires", None)


@app.get("/forgot_password")
def forgot_password_view():
    return render_template("forgot_password_request.html")


@app.post("/forgot_password")
def forgot_password_post():
    email = (request.form.get("email") or "").strip().lower()
    users = load_users()
    user = next((u for u in users if (u.get("email") or "").strip().lower() == email), None)

    # Always show a neutral success message to avoid disclosing account existence
    msg = "If an account with that email exists, a password reset link has been sent."

    if user:
        token = uuid.uuid4().hex
        expires = int(time.time()) + RESET_TOKEN_EXPIRY_SECONDS
        user['reset_token'] = token
        user['reset_expires'] = expires
        save_users(users)

        # Build reset link
        try:
            reset_link = url_for('reset_password_view', token=token, _external=True)
        except Exception:
            # Fallback if _external fails in some environments
            reset_link = f"/reset_password?token={token}"

        subject = f"Reset your password"
        html_body = f"<p>Hello {user.get('first_name','')},</p>\n" \
            f"<p>We received a request to reset your Rehearsal Schedule password. " \
            f"If you made this request, click the link below to choose a new password. This link will expire in {int(RESET_TOKEN_EXPIRY_SECONDS/60)} minutes.</p>\n" \
            f"<p><a href=\"{reset_link}\">Reset your password</a></p>\n" \
            f"<p>If you did not request a password reset, you can ignore this message.</p>\n"

        text_body = f"Reset your password: {reset_link}\nThis link expires in {int(RESET_TOKEN_EXPIRY_SECONDS/60)} minutes."

        try:
            # Password reset must go only to the user (no primary/bcc routing)
            brevo_send_email([user.get('email')], subject, html_body, text_body, bcc_mode=False)
        except Exception:
            app.logger.exception("Failed to send password reset email")

    return render_template("forgot_password_request.html", message=msg)


@app.get('/reset_password')
def reset_password_view():
    token = request.args.get('token')
    if not token:
        return render_template('reset_password.html', error='Missing token.'), 400

    users = load_users()
    user = next((u for u in users if u.get('reset_token') == token), None)
    if not user:
        return render_template('reset_password.html', error='Invalid or expired token.'), 400

    expires = user.get('reset_expires', 0)
    if int(time.time()) > int(expires):
        _clear_reset_token_for_user(user)
        save_users(users)
        return render_template('reset_password.html', error='Token expired.'), 400

    return render_template('reset_password.html', token=token)


@app.post('/reset_password')
def reset_password_post():
    token = request.form.get('token')
    password = request.form.get('password') or ''
    password2 = request.form.get('password2') or ''

    if not token:
        return render_template('reset_password.html', error='Missing token.'), 400
    if not password or password != password2:
        return render_template('reset_password.html', token=token, error='Passwords do not match.'), 400

    users = load_users()
    user = next((u for u in users if u.get('reset_token') == token), None)
    if not user:
        return render_template('reset_password.html', error='Invalid or expired token.'), 400

    expires = user.get('reset_expires', 0)
    if int(time.time()) > int(expires):
        _clear_reset_token_for_user(user)
        save_users(users)
        return render_template('reset_password.html', error='Token expired.'), 400

    # Update password
    user['password_hash'] = generate_password_hash(password)
    _clear_reset_token_for_user(user)
    save_users(users)

    return render_template('login.html', message='Password updated. You can now log in.')


@app.get("/my")
def my_view():
    u = current_user()
    
    print(f"\n=== my_view called ===")
    print(f"Current user: {u.get('email') if u else 'None'}")
    
    # Get user's ensembles (for concert display)
    ensembles = load_ensembles()
    memberships = load_memberships()
    
    if u and u.get("is_admin"):
        user_ensembles = ensembles
        print(f"User is admin, showing all {len(ensembles)} ensembles")
    elif u:
        my_ids = {m.get("ensemble_id") for m in memberships if m.get("user_id") == (u or {}).get("id") and m.get("status", "active") == "active"}
        user_ensembles = [e for e in ensembles if e.get("id") in my_ids]
        print(f"User is member of {len(user_ensembles)} ensembles: {[e['id'] for e in user_ensembles]}")
    else:
        user_ensembles = []
        print("Public viewer: calendar-only mode")
    
    # Get all concerts for user's ensembles (hidden in public mode)
    all_concerts = []
    if u:
        for ensemble in user_ensembles:
            concerts = get_ensemble_concerts(ensemble.get("id"))
            print(f"Ensemble '{ensemble.get('name')}' has {len(concerts)} concerts (normalized)")
            for concert in concerts:
                concert_with_ensemble = concert.copy()
                concert_with_ensemble["ensemble_id"] = ensemble.get("id")
                concert_with_ensemble["ensemble_name"] = ensemble.get("name")
                all_concerts.append(concert_with_ensemble)
                print(f"  Added concert: {concert.get('date')} at {concert.get('venue')}")
    
    # Sort by date (soonest first for upcoming strip)
    all_concerts.sort(key=lambda x: x.get("date", ""))
    
    print(f"Total concerts to display: {len(all_concerts)}")
    print(f"=== my_view complete ===\n")
    
    return render_template("member_dashboard.html", user=u, concerts=all_concerts, public_mode=(u is None))


@app.get("/api/ensembles/<ensemble_id>/concerts")
def api_get_ensemble_concerts(ensemble_id):
    """Get all concerts for an ensemble (JSON API)."""
    r = admin_required_or_403()
    if r: return r
    
    concerts = get_ensemble_concerts(ensemble_id)
    return jsonify(concerts)


@app.post("/api/ensembles/<ensemble_id>/concerts")
def api_create_concert(ensemble_id):
    """Create a concert via JSON API."""
    r = admin_required_or_403()
    if r: return r
    
    payload = request.get_json(force=True)
    try:
        concert = add_concert_to_ensemble(ensemble_id, payload)
        return jsonify(concert), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.put("/api/ensembles/<ensemble_id>/concerts/<concert_id>")
def api_update_concert(ensemble_id, concert_id):
    """Update a concert via JSON API."""
    r = admin_required_or_403()
    if r: return r
    
    payload = request.get_json(force=True)
    try:
        concert = update_concert(ensemble_id, concert_id, payload)
        if not concert:
            return jsonify({"error": "Concert not found"}), 404
        return jsonify(concert)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/ensembles/<ensemble_id>/concerts/<concert_id>")
def api_delete_concert(ensemble_id, concert_id):
    """Delete a concert via JSON API."""
    r = admin_required_or_403()
    if r: return r
    
    if delete_concert(ensemble_id, concert_id):
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Concert not found"}), 404


@app.post("/api/admin/cleanup-duplicate-concerts")
def api_cleanup_duplicate_concerts():
    """Remove auto_concert_* duplicates that were created before the deduplication fix."""
    r = admin_required_or_403()
    if r: return r
    
    ensembles = load_ensembles()
    removed_count = 0
    
    for ensemble in ensembles:
        if "concerts" not in ensemble:
            continue
        
        # Remove any concerts with IDs starting with "auto_concert_"
        original_count = len(ensemble["concerts"])
        ensemble["concerts"] = [c for c in ensemble["concerts"] if not c.get("id", "").startswith("auto_concert_")]
        removed_count += original_count - len(ensemble["concerts"])
    
    save_ensembles(ensembles)
    
    return jsonify({
        "success": True,
        "removed_count": removed_count,
        "message": f"Removed {removed_count} duplicate auto-generated concerts"
    })


def _outlook_event_find_by_date_and_title(config: dict, access_token: str, event_title: str, event_date: str) -> Optional[str]:
    """Search for an existing event with matching date and title. Returns event ID or None."""
    try:
        # Parse the date to search window (full day)
        dt_date = pd.to_datetime(event_date).date()
        start_dt = _dt.datetime.combine(dt_date, _dt.time(0, 0)).isoformat() + "Z"
        end_dt = _dt.datetime.combine(dt_date, _dt.time(23, 59)).isoformat() + "Z"
        
        base_user = config["user_principal_name"]
        calendar_id = config.get("calendar_id") or ""
        if calendar_id:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/calendarView"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendarView"
        
        params = {
            "startDateTime": start_dt,
            "endDateTime": end_dt,
            "$select": "id,subject",
            "$top": "100",
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        
        response = requests.get(endpoint, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json() or {}
        
        # Search for exact or partial title match on the same date
        for row in payload.get("value", []):
            subject = (row.get("subject") or "").strip()
            if event_title.lower() in subject.lower() or subject.lower() in event_title.lower():
                event_id = row.get("id")
                if event_id:
                    app.logger.info(f"Found existing Outlook event {event_id} on {event_date}: {subject}")
                    return event_id
        
        return None
    except Exception:
        app.logger.exception(f"Failed to search for existing Outlook event")
        return None


def _outlook_event_write(config: dict, access_token: str, event_title: str, event_date: str,
                        event_time: str = "", event_venue: str = "", event_body: str = "", skip_duplicate_check: bool = False) -> Optional[str]:
    """Create a calendar event in Outlook and return the event ID, or None on failure.
    If skip_duplicate_check is False, will search for existing event with same date/title first."""
    try:
        # Check for existing event to avoid duplicates (unless explicitly skipped)
        if not skip_duplicate_check:
            existing_id = _outlook_event_find_by_date_and_title(config, access_token, event_title, event_date)
            if existing_id:
                app.logger.info(f"Skipping duplicate: event {existing_id} already exists on {event_date}")
                return existing_id
        
        # Parse date and optionally merge with time
        dt_date = pd.to_datetime(event_date).date()
        
        if event_time:
            try:
                time_obj = pd.to_datetime(event_time).time()
                dt_value = _dt.datetime.combine(dt_date, time_obj)
            except Exception:
                dt_value = _dt.datetime.combine(dt_date, _dt.time(20, 0))  # Default 20:00
        else:
            dt_value = _dt.datetime.combine(dt_date, _dt.time(20, 0))
        
        # Create event body
        start_dt = dt_value.isoformat()
        end_dt = (dt_value + _dt.timedelta(hours=2)).isoformat()
        
        event_payload = {
            "subject": event_title,
            "start": {
                "dateTime": start_dt,
                "timeZone": config.get("timezone", "Europe/London"),
            },
            "end": {
                "dateTime": end_dt,
                "timeZone": config.get("timezone", "Europe/London"),
            },
        }
        
        if event_venue:
            event_payload["location"] = {"displayName": event_venue}
        
        if event_body:
            event_payload["bodyPreview"] = event_body
        
        base_user = config["user_principal_name"]
        calendar_id = config.get("calendar_id") or ""
        if calendar_id:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/events"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/events"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        response = requests.post(endpoint, json=event_payload, headers=headers, timeout=20)
        response.raise_for_status()
        result = response.json() or {}
        event_id = result.get("id")
        if event_id:
            app.logger.info(f"Created Outlook event {event_id}: {event_title}")
        return event_id
    except Exception:
        app.logger.exception(f"Failed to create Outlook event: {event_title}")
        return None


def _outlook_event_update(config: dict, access_token: str, event_id: str, event_title: str,
                         event_date: str, event_time: str = "", event_venue: str = "", event_body: str = "") -> bool:
    """Update a calendar event in Outlook and return True on success."""
    try:
        # Parse date and optionally merge with time
        dt_date = pd.to_datetime(event_date).date()
        
        if event_time:
            try:
                time_obj = pd.to_datetime(event_time).time()
                dt_value = _dt.datetime.combine(dt_date, time_obj)
            except Exception:
                dt_value = _dt.datetime.combine(dt_date, _dt.time(20, 0))
        else:
            dt_value = _dt.datetime.combine(dt_date, _dt.time(20, 0))
        
        start_dt = dt_value.isoformat()
        end_dt = (dt_value + _dt.timedelta(hours=2)).isoformat()
        
        event_payload = {
            "subject": event_title,
            "start": {
                "dateTime": start_dt,
                "timeZone": config.get("timezone", "Europe/London"),
            },
            "end": {
                "dateTime": end_dt,
                "timeZone": config.get("timezone", "Europe/London"),
            },
        }
        
        if event_venue:
            event_payload["location"] = {"displayName": event_venue}
        
        if event_body:
            event_payload["bodyPreview"] = event_body
        
        base_user = config["user_principal_name"]
        calendar_id = config.get("calendar_id") or ""
        if calendar_id:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/events/{event_id}"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/events/{event_id}"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        response = requests.patch(endpoint, json=event_payload, headers=headers, timeout=20)
        response.raise_for_status()
        app.logger.info(f"Updated Outlook event {event_id}: {event_title}")
        return True
    except Exception:
        app.logger.exception(f"Failed to update Outlook event: {event_id}")
        return False


def _outlook_event_delete(config: dict, access_token: str, event_id: str) -> bool:
    """Delete a calendar event from Outlook and return True on success."""
    try:
        base_user = config["user_principal_name"]
        calendar_id = config.get("calendar_id") or ""
        if calendar_id:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/events/{event_id}"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/events/{event_id}"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
        }
        
        response = requests.delete(endpoint, headers=headers, timeout=20)
        response.raise_for_status()
        app.logger.info(f"Deleted Outlook event {event_id}")
        return True
    except Exception:
        app.logger.exception(f"Failed to delete Outlook event: {event_id}")
        return False


@app.get("/api/member/rehearsals")
def api_member_rehearsals():
    u = current_user()
    public_mode = u is None
    print(f"\n=== api_member_rehearsals called ===")
    print(f"Current user: {u.get('email') if u else 'None'}")
    print(f"Is admin: {u.get('is_admin') if u else False}")
    
    ensembles = load_ensembles()
    memberships = load_memberships()

    if u and u.get("is_admin"):
        my_ensembles = ensembles
        print(f"User is admin, showing all {len(ensembles)} ensembles")
    elif u:
        my_ids = {m.get("ensemble_id") for m in memberships if m.get("user_id") == (u or {}).get("id") and m.get("status", "active") == "active"}
        my_ensembles = [e for e in ensembles if e.get("id") in my_ids]
        print(f"User is member of {len(my_ensembles)} ensembles: {[e['id'] for e in my_ensembles]}")
    else:
        my_ensembles = []
        print("Public viewer: include published schedules only")

    # Get published schedules for user's ensembles
    schedule_summaries = []
    all_schedule_ids = list_schedule_ids()
    print(f"Total schedules in system: {len(all_schedule_ids)}")
    
    for sid in all_schedule_ids:
        try:
            s = load_schedule(sid)
        except Exception as e:
            print(f"  [ERROR] Failed to load schedule {sid}: {e}")
            import traceback
            traceback.print_exc()
            continue
        if not s:
            continue

        if u and u.get("is_admin"):
            schedule_summaries.append(s)
            print(f"  Admin: including schedule {sid} ({s.get('name')})")
        elif u:
            if s.get("status") != "published":
                print(f"  Schedule {sid} not published (status: {s.get('status')}), skipping for member")
                continue
            if any(e["id"] == s.get("ensemble_id") for e in my_ensembles):
                schedule_summaries.append(s)
                print(f"  Member: including published schedule {sid} for ensemble {s.get('ensemble_id')}")
            else:
                print(f"  Schedule {sid} is for ensemble {s.get('ensemble_id')}, not in user's ensembles")
        else:
            if s.get("status") == "published":
                schedule_summaries.append(s)
                print(f"  Public: including published schedule {sid}")

    print(f"Found {len(schedule_summaries)} schedules to process")

    # Extract rehearsal data with dates and times
    rehearsals_list = []
    ensemble_map = {e["id"]: e["name"] for e in ensembles}
    
    for sched in schedule_summaries:
        # Safety check: ensure schedule has an id
        if not sched or not sched.get("id"):
            print(f"  [WARNING] Skipping schedule without id: {sched}")
            continue
            
        schedule_id = sched["id"]
        ensemble_id = sched.get("ensemble_id")
        ensemble_name = ensemble_map.get(ensemble_id, "Unknown")
        
        print(f"\nProcessing schedule {schedule_id} ({ensemble_name})")
        
        # Build a map of rehearsal number -> event type from the rehearsals table
        rehearsals_table = sched.get("rehearsals", [])
        event_type_map = {}
        for reh_row in rehearsals_table:
            reh_num = reh_row.get("Rehearsal")
            # Skip invalid rehearsal numbers (None, empty string, NaN)
            if reh_num is not None and reh_num != "" and not pd.isna(reh_num):
                try:
                    event_type_map[int(reh_num)] = reh_row.get("Event Type", "Rehearsal")
                except (ValueError, TypeError):
                    # Skip if conversion to int fails
                    pass
        
        # Get timed schedule data directly from the schedule
        try:
            timed_data = sched.get("timed", [])
            print(f"  Timed data items: {len(timed_data)}")
            
            if not timed_data:
                print(f"  No timed data, skipping")
                continue
            
            # Create a DataFrame to group by rehearsal
            timed_df = pd.DataFrame(timed_data)
            if timed_df.empty:
                print(f"  Empty DataFrame after conversion, skipping")
                continue
            
            # Group by rehearsal number
            if "Rehearsal" not in timed_df.columns:
                print(f"  No Rehearsal column in DataFrame, skipping")
                print(f"  Available columns: {list(timed_df.columns)}")
                continue
                
            grouped = timed_df.groupby("Rehearsal")
            print(f"  Found {len(grouped)} rehearsals")
            
            for reh_num, group in grouped:
                # Ensure reh_num is a clean integer
                try:
                    reh_num_int = int(reh_num)
                except (ValueError, TypeError):
                    print(f"    Skipping invalid rehearsal number: {reh_num}")
                    continue
                
                # Get date from first row
                if "Date" not in group.columns:
                    print(f"    Rehearsal {reh_num_int}: No Date column, skipping")
                    continue
                    
                date_val = group.iloc[0]["Date"]
                if pd.isna(date_val):
                    print(f"    Rehearsal {reh_num_int}: NaN date, skipping")
                    continue
                
                # Parse the date - it could be a string like "2026-01-16T00:00:00" or a datetime
                try:
                    if isinstance(date_val, str):
                        # Parse ISO format string
                        date_obj = pd.to_datetime(date_val).date()
                    else:
                        # Assume it's already a datetime-like object
                        date_obj = pd.to_datetime(date_val).date()
                    date_str = date_obj.isoformat()
                except Exception as e:
                    print(f"    Rehearsal {reh_num_int}: Error parsing date '{date_val}': {e}")
                    continue
                
                # Get event type from rehearsals table
                event_type = event_type_map.get(reh_num_int, "Rehearsal")
                
                # Get section if specified (first row's Section value, default to "Full Ensemble")
                section = "Full Ensemble"
                if "Section" in group.columns:
                    section_val = group.iloc[0]["Section"]
                    if pd.notna(section_val):
                        section = str(section_val).strip()
                
                # Build items list for this rehearsal
                items = []
                for _, row in group.iterrows():
                    title = row.get("Title", "")
                    time_in_reh = row.get("Time in Rehearsal", "")
                    
                    if title == "Break":
                        items.append({
                            "is_break": True,
                            "time": time_in_reh
                        })
                    else:
                        items.append({
                            "is_break": False,
                            "title": title,
                            "time": time_in_reh
                        })
                
                rehearsals_list.append({
                    "schedule_id": None if public_mode else schedule_id,
                    "ensemble_name": ensemble_name,
                    "rehearsal_num": reh_num_int,
                    "date": date_str,
                    "event_type": "Rehearsal" if public_mode else event_type,
                    "section": "Full Ensemble" if public_mode else section,
                    "items": [] if public_mode else items
                })
                print(f"    Rehearsal {reh_num_int}: {date_str}, Event Type: {event_type}, Section: {section}, {len(items)} items")
                
        except Exception as e:
            print(f"  Error processing schedule {schedule_id}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Sort by date
    rehearsals_list.sort(key=lambda x: (x["date"], x["ensemble_name"], x["rehearsal_num"]))
    
    # Build concerts list (for standalone display, separate from rehearsals)
    concerts_list = []
    if public_mode:
        print("Public viewer: omitting ensemble concert details from API")
    
    if not public_mode:
        for ensemble in ensembles:
            ens_id = ensemble["id"]
            ens_name = ensemble["name"]
            
            # Check if user has access to this ensemble
            if not (u and u.get("is_admin")) and not any(e["id"] == ens_id for e in my_ensembles):
                continue
            
            # Get scheduled concerts for this ensemble
            for concert in ensemble.get("concerts", []):
                if concert.get("status") == "scheduled":
                    try:
                        # Parse concert date for sorting
                        concert_date = concert.get("date", "")
                        if isinstance(concert_date, str):
                            concert_date_obj = pd.to_datetime(concert_date).date()
                        else:
                            concert_date_obj = pd.to_datetime(concert_date).date()
                        concert_date_str = concert_date_obj.isoformat()
                    except:
                        concert_date_str = concert.get("date", "")
                    
                    concerts_list.append({
                        "type": "concert",
                        "id": concert.get("id"),  # Add 'id' for JavaScript compatibility
                        "concert_id": concert.get("id"),
                        "ensemble_id": ens_id,  # Add ensemble_id
                        "ensemble_name": ens_name,
                        "title": concert.get("title", "Concert"),
                        "date": concert_date_str,
                        "time": concert.get("time"),
                        "venue": concert.get("venue"),
                        "uniform": concert.get("uniform"),
                        "programme": concert.get("programme", ""),
                        "other_info": concert.get("other_info", ""),
                        "schedule_id": concert.get("schedule_id")
                    })
    
    # Merge Outlook events into dashboard calendar/events stream.
    outlook_events = _outlook_events_for_dashboard()
    if outlook_events:
        concerts_list.extend(outlook_events)
        print(f"Added {len(outlook_events)} Outlook events to dashboard feed")
    elif public_mode:
        print("No Outlook events added for public dashboard feed")

    # Combine concerts and rehearsals, sort by date
    all_events = []
    for rehearsal in rehearsals_list:
        all_events.append({
            "type": "rehearsal",
            "date": rehearsal["date"],
            "sort_key": (rehearsal["date"], rehearsal["ensemble_name"], 0, rehearsal["rehearsal_num"]),
            "data": rehearsal
        })
    for concert in concerts_list:
        all_events.append({
            "type": "concert",
            "date": concert["date"],
            "sort_key": (concert["date"], concert["ensemble_name"], -1),  # Concerts come before rehearsals on same date
            "data": concert
        })
    
    all_events.sort(key=lambda x: x["sort_key"])
    
    print(f"\n=== Total events to return: {len(all_events)} ({len(rehearsals_list)} rehearsals + {len(concerts_list)} concerts) ===\n")
    
    return jsonify({"events": all_events, "rehearsals": rehearsals_list, "concerts": concerts_list, "public_mode": public_mode})


@app.get("/")
def home():
    return redirect(url_for("my_view"))



@app.get("/admin/s/<schedule_id>/edit")
def admin_edit_schedule(schedule_id):
    r = admin_required_or_403()
    if r:
        return r

    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    works, rehearsals, alloc, sched = get_frames(s)

    # lightweight preview timed
    timed = pd.DataFrame(s.get("timed", []))
    if timed.empty and not sched.empty and not rehearsals.empty:
        # Use prepare_all_rehearsals to include non-allocated rehearsals (sectionals)
        timed = compute_timed_df(sched, prepare_all_rehearsals(rehearsals))
    
    # Clean timed data to ensure Section field has proper defaults
    if not timed.empty:
        cleaned_timed = clean_timed_data(timed.to_dict(orient="records"))
        s["timed"] = cleaned_timed
        # Reload into DataFrame so template gets clean data
        timed = pd.DataFrame(cleaned_timed)

    # Resolve ensemble display name and get concerts
    ensembles = load_ensembles()
    ens = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    ensemble_name = ens["name"] if ens else s.get("ensemble_id", "")
    
    # Get concerts for this schedule
    schedule_concerts = []
    if ens:
        concerts = ens.get("concerts", [])
        schedule_concerts = [c for c in concerts if c.get("schedule_id") == schedule_id]
    
    # Add concert_id to timed rows for concert rehearsals (same logic as /state endpoint)
    timed_data = s.get("timed", [])
    if ens and not rehearsals.empty:
        for timed_row in timed_data:
            reh_num = timed_row.get("Rehearsal")
            if reh_num:
                # Find rehearsal by number
                reh_dict = next((r for r in rehearsals.to_dict(orient="records") if int(r.get("Rehearsal", 0)) == reh_num), None)
                if reh_dict and reh_dict.get("Event Type") in ["Concert", "Contest"]:
                    # Find the concert for this rehearsal by matching date
                    reh_date = reh_dict.get("Date")
                    # Normalize dates for comparison (strip time component)
                    reh_date_normalized = str(reh_date)[:10] if reh_date else None
                    
                    # Try to match concert by date (comparing just YYYY-MM-DD)
                    concert = None
                    for c in schedule_concerts:
                        concert_date_normalized = str(c.get("date", ""))[:10]
                        if concert_date_normalized == reh_date_normalized:
                            concert = c
                            break
                    
                    if concert:
                        timed_row["concert_id"] = concert.get("id")
                        print(f"[EDIT PAGE] Added concert_id {concert.get('id')} to timed row for rehearsal {reh_num}")
        
        # Update timed DataFrame with concert_id links
        timed = pd.DataFrame(timed_data)

    print("ADMIN CHECK:", current_user())

    return render_template(
        "edit.html",
        schedule_id=s.get("id"),
        schedule_name=s.get("name", "Untitled"),
        ensemble_name=ensemble_name,
        status=s.get("status", "draft"),
        works=works.fillna("").to_dict(orient="records"),
        works_cols=s.get("works_cols") or list(default_works_df().columns),
        rehearsals_cols=s.get("rehearsals_cols") or list(default_rehearsals_df().columns),
        rehearsals=rehearsals.fillna("").to_dict(orient="records"),
        allocation=alloc.fillna("").to_dict(orient="records") if not alloc.empty else [],
        schedule=sched.fillna("").to_dict(orient="records") if not sched.empty else [],
        timed=timed.fillna("").to_dict(orient="records") if not timed.empty else [],
        concerts=schedule_concerts,  # Add concerts for timeline editor
        pdf_enabled=mod4_exists,
    )


@app.get("/edit")
def edit_view():
    require_edit_token()
    s, sid = default_schedule()
    return redirect(url_for("admin_edit_schedule", schedule_id=sid))


# ---- API: save inputs
@app.post("/api/save_inputs")
def api_save_inputs():
    require_edit_token()
    payload = request.get_json(force=True)

    s, sid = default_schedule()

    s["G"] = int(payload.get("G", s.get("G", DEFAULT_G)))

    s["works"] = payload.get("works", [])
    # Auto-number rehearsals if needed
    rehearsals_data = payload.get("rehearsals", [])
    rehearsals_data = auto_number_rehearsals(rehearsals_data)
    s["rehearsals"] = rehearsals_data

    # Keep computed artifacts unless explicitly clearing
    if payload.get("clear_computed"):
        s["allocation"] = []
        s["schedule"] = []
        s["timed"] = []

    save_schedule(s)
    return jsonify({"ok": True})


# ---- API: run allocation
@app.post("/api/run_allocation")
def api_run_allocation():
    require_edit_token()
    s, sid = default_schedule()

    works, rehearsals, _, _ = get_frames(s)
    G = int(s.get("G", DEFAULT_G))

    alloc_df, warnings = run_allocation_compute(works, rehearsals, G)
    s["allocation"] = sanitize_df_records(alloc_df.to_dict(orient="records"))
    s["warnings"] = warnings
    save_schedule(s)
    return jsonify({"ok": True, "warnings": warnings})


# ---- API: generate schedule
@app.post("/api/generate_schedule")
def api_generate_schedule():
    require_edit_token()
    s, sid = default_schedule()

    works, rehearsals, alloc, _ = get_frames(s)
    if alloc.empty:
        return jsonify({"ok": False, "error": "No allocation yet"}), 400

    sched = build_schedule_from_allocation(works, alloc)
    s["schedule"] = sanitize_df_records(sched.to_dict(orient="records"))

    # Use prepare_all_rehearsals to include non-allocated rehearsals (sectionals)
    rehe_prep = prepare_all_rehearsals(rehearsals)
    timed = compute_timed_df(sched, rehe_prep)
    s["timed"] = sanitize_df_records(timed.to_dict(orient="records"))
    s["generated_at"] = pd.Timestamp.utcnow().isoformat() + "Z"

    save_schedule(s)
    return jsonify({"ok": True})

@app.post("/api/s/<schedule_id>/import_xlsx")
def api_schedule_import_xlsx(schedule_id):
    r = admin_required_or_403()
    if r:
        return r

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]

    # Load schedule up front
    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    # Read Excel container
    try:
        xls = pd.ExcelFile(f.stream)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not read Excel file: {e}"}), 400

    sheet_names = list(xls.sheet_names)

    def pick_sheet(names, keywords):
        for name in names:
            low = name.strip().lower()
            if any(k in low for k in keywords):
                return name
        return None

    works_sheet = pick_sheet(sheet_names, ["work"])
    rehe_sheet = pick_sheet(sheet_names, ["rehears"])

    if not works_sheet or not rehe_sheet:
        if len(sheet_names) < 2:
            return jsonify({
                "ok": False,
                "error": "Excel file must contain two sheets (Works and Rehearsals).",
                "sheets_found": sheet_names,
            }), 400
        works_sheet = works_sheet or sheet_names[0]
        rehe_sheet = rehe_sheet or sheet_names[1]

    # --- THE FIX: Load the Dataframes first, THEN replace NaNs ---
    try:
        df_works = pd.read_excel(xls, sheet_name=works_sheet)
        df_rehe = pd.read_excel(xls, sheet_name=rehe_sheet)
        
        # Replace NaN/Inf with None so JSON conversion doesn't break
        df_works = df_works.replace({np.nan: None, np.inf: None, -np.inf: None})
        df_rehe = df_rehe.replace({np.nan: None, np.inf: None, -np.inf: None})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed reading sheets: {e}"}), 400

    if df_works is None: df_works = pd.DataFrame()
    if df_rehe is None: df_rehe = pd.DataFrame()

    # Normalize headers
    works_cols = [str(c).strip() for c in df_works.columns]
    rehe_cols = [str(c).strip() for c in df_rehe.columns]
    df_works.columns = works_cols
    df_rehe.columns = rehe_cols

    # Internal helper to handle DateTimes and empty cells
    def df_to_json_safe_strings(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.copy()
        for col in out.columns:
            try:
                # Convert actual datetime types to string
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        def conv(x):
            if isinstance(x, pd.Timestamp):
                return x.isoformat()
            # This is where we ensure "NaN" becomes "" for the UI
            if x is None or (isinstance(x, float) and math.isnan(x)):
                return ""
            return x

        return out.map(conv)

    df_works = df_to_json_safe_strings(df_works)
    df_rehe = df_to_json_safe_strings(df_rehe)

    # Save to state
    s["works_cols"] = works_cols
    
    # Auto-number rehearsals if missing or empty
    rehearsals_data = df_rehe.to_dict(orient="records")
    rehearsals_data = auto_number_rehearsals(rehearsals_data)
    # Normalize "Ensemble" column to "Section" if present
    rehearsals_data = normalize_section_column(rehearsals_data)
    # Ensure Include in allocation column is present
    rehearsals_data = ensure_include_in_allocation_column(rehearsals_data)
    # Ensure Section column is present
    rehearsals_data = ensure_section_column(rehearsals_data)
    # Ensure Event Type column is present
    rehearsals_data = ensure_event_type_column(rehearsals_data)
    s["rehearsals"] = rehearsals_data
    
    # Auto-create concerts for rehearsals marked as "Concert" event type
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    if ensemble:
        print(f"\n[CONCERT CREATE] Starting concert auto-creation for {len(rehearsals_data)} rehearsals")
        print(f"[CONCERT CREATE] Ensemble: {ensemble.get('name')} (ID: {ensemble.get('id')})")
        concerts_created = 0
        concerts_updated = 0
        
        for idx, reh_row in enumerate(rehearsals_data):
            event_type_value = reh_row.get("Event Type")
            print(f"\n[CONCERT CREATE] Row {idx+1}: Event Type = {repr(event_type_value)} | Date = {reh_row.get('Date')}")
            
            # More robust matching - handle case variations and whitespace
            event_type_normalized = str(event_type_value).strip() if event_type_value else ""
            is_concert = event_type_normalized.lower() == "concert"
            
            print(f"[CONCERT CREATE]   Normalized: '{event_type_normalized}' | Is Concert: {is_concert}")
            
            if is_concert:
                print(f"[CONCERT CREATE] ✓ Processing rehearsal with Event Type=Concert")
                # Check if concert already exists for this date/time combination
                concert_date = reh_row.get("Date", "")
                concert_time = reh_row.get("Start Time", "")
                
                # Normalize date to string format YYYY-MM-DD HH:MM:SS (to match other rehearsals)
                if concert_date:
                    try:
                        dt = pd.to_datetime(concert_date)
                        concert_date = dt.strftime("%Y-%m-%d 00:00:00")  # Include time component for consistency
                        reh_row["Date"] = concert_date  # Update the rehearsal row with normalized date
                    except:
                        concert_date = str(concert_date)
                
                # Find existing concert for this schedule and date (deduplication)
                existing_concert = None
                for c in ensemble.get("concerts", []):
                    if c.get("schedule_id") == schedule_id and c.get("date") == concert_date:
                        existing_concert = c
                        break
                
                if concert_date:
                    concert_id = None
                    print(f"\n[CONCERT CREATE] Processing rehearsal with Event Type=Concert")
                    print(f"[CONCERT CREATE] Date: {concert_date}, Schedule: {schedule_id}")
                    print(f"[CONCERT CREATE] Existing concerts in ensemble: {len(ensemble.get('concerts', []))}")
                    
                    # Format date for title
                    try:
                        dt = pd.to_datetime(concert_date)
                        day = dt.day
                        if 10 <= day % 100 <= 20:
                            suffix = 'th'
                        else:
                            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                        formatted_date = dt.strftime(f'%A %B {day}{suffix}')
                    except:
                        formatted_date = str(concert_date)
                    
                    default_title = f"{ensemble.get('name', 'Unknown Ensemble')} - Concert - {formatted_date}"
                    
                    # Prepare sensible defaults: use repertoire from imported works if Programme empty
                    def _extract_work_titles(df):
                        if df is None or df.empty:
                            return []
                        title_col = None
                        for candidate in ["Title", "Work", "Work Title", "Piece"]:
                            if candidate in df.columns:
                                title_col = candidate
                                break
                        if not title_col:
                            # fallback to first string-like column
                            for c in df.columns:
                                if df[c].dtype == object:
                                    title_col = c
                                    break
                        if not title_col:
                            return []
                        titles = []
                        for v in df[title_col].tolist():
                            if v is None:
                                continue
                            s = str(v).strip()
                            if s:
                                titles.append(s)
                        return titles

                    default_programme = "\n".join(_extract_work_titles(df_works))
                    # Prefer explicit Programme from Excel, otherwise use default_programme
                    programme_value = reh_row.get("Programme") or default_programme or ""
                    # Default Venue/Uniform to 'TBC' when not provided
                    venue_value = reh_row.get("Venue") or "TBC"
                    uniform_value = reh_row.get("Uniform") or "TBC"

                    if existing_concert:
                        # Update existing concert with new data from Excel
                        print(f"[CONCERT CREATE] Updating existing concert: {existing_concert['id']}")
                        existing_concert["title"] = reh_row.get("Concert Title") or existing_concert.get("title", default_title)
                        existing_concert["time"] = reh_row.get("Start Time", existing_concert.get("time", ""))
                        existing_concert["venue"] = reh_row.get("Venue") or existing_concert.get("venue", venue_value)
                        existing_concert["uniform"] = reh_row.get("Uniform") or existing_concert.get("uniform", uniform_value)
                        # Only overwrite programme if Excel provided one, otherwise preserve existing or set default
                        existing_concert["programme"] = reh_row.get("Programme") or existing_concert.get("programme", programme_value)
                        existing_concert["other_info"] = reh_row.get("Other Info", existing_concert.get("other_info", ""))
                        concert_id = existing_concert["id"]
                        concerts_updated += 1
                    else:
                        # Create a new concert with details from Excel
                        concert_id = f"concert_{uuid.uuid4().hex[:12]}"
                        print(f"[CONCERT CREATE] Creating NEW concert: {concert_id}")
                        new_concert = {
                            "id": concert_id,
                            "title": reh_row.get("Concert Title") or default_title,
                            "date": concert_date,
                            "time": reh_row.get("Start Time", ""),
                            "venue": venue_value,
                            "uniform": uniform_value,
                            "programme": programme_value,
                            "other_info": reh_row.get("Other Info", ""),
                            "status": "scheduled",
                            "schedule_id": schedule_id,
                            "is_auto_generated": True
                        }
                        if "concerts" not in ensemble:
                            ensemble["concerts"] = []
                        ensemble["concerts"].append(new_concert)
                        concerts_created += 1
                        print(f"[CONCERT CREATE] Concert added to ensemble. Total concerts: {len(ensemble['concerts'])}")
                    
                    # Link concert_id back to rehearsal row for easy access
                    reh_row["concert_id"] = concert_id
                    print(f"[CONCERT CREATE] Linked concert_id {concert_id} to rehearsal row")
        
        # Save updated ensemble with new/updated concerts
        if concerts_created > 0 or concerts_updated > 0:
            save_ensembles(ensembles)
            print(f"\n[CONCERT CREATE] SUMMARY: Created {concerts_created}, Updated {concerts_updated} concerts")
            print(f"[CONCERT CREATE] Total concerts in ensemble: {len(ensemble.get('concerts', []))}")
        else:
            print(f"\n[CONCERT CREATE] No concerts created or updated")
    
    # Ensure rehearsals_cols always includes "Rehearsal" and "Include in allocation"
    # Use the columns from the processed data to ensure consistency
    if rehearsals_data and len(rehearsals_data) > 0:
        actual_cols = list(rehearsals_data[0].keys())
        # Ensure "Rehearsal" column is in the list
        if "Rehearsal" not in actual_cols:
            actual_cols.insert(0, "Rehearsal")
        s["rehearsals_cols"] = actual_cols
    else:
        # Ensure default columns include "Rehearsal"
        default_cols = list(default_rehearsals_df().columns)
        s["rehearsals_cols"] = default_cols
    
    s["works"] = df_works.to_dict(orient="records")

    # Clear computed artifacts
    s["allocation"] = []
    s["schedule"] = []
    s["timed"] = []
    s["generated_at"] = None
    s["updated_at"] = int(time.time())

    save_schedule(s)

    return jsonify({
        "ok": True,
        "works_sheet": works_sheet,
        "rehearsals_sheet": rehe_sheet,
        "works_rows": int(len(df_works)),
        "rehearsals_rows": int(len(df_rehe)),
    })


@app.post("/api/s/<schedule_id>/import_complete_schedule")
def api_schedule_import_complete(schedule_id):
    """Import complete schedule with events (rehearsals) and schedule (timed works) from single Excel file."""
    r = admin_required_or_403()
    if r:
        return r

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]

    # Load schedule
    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    # Read Excel container
    try:
        xls = pd.ExcelFile(f.stream)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not read Excel file: {e}"}), 400

    sheet_names = [str(name).strip() for name in xls.sheet_names]

    # Find events and schedule sheets
    def pick_sheet(names, keywords):
        for name in names:
            low = name.strip().lower()
            if any(k in low for k in keywords):
                return name
        return None

    events_sheet = pick_sheet(sheet_names, ["event", "rehears"])
    schedule_sheet = pick_sheet(sheet_names, ["schedul", "work", "timed"])

    # If either sheet missing, attempt to handle single-sheet exports that contain
    # both rehearsal rows and timed schedule rows (legacy format). If there is
    # exactly one sheet and it looks like a timed schedule, derive events by
    # grouping on `Rehearsal` and taking the first row per rehearsal.
    if not events_sheet or not schedule_sheet:
        if len(sheet_names) == 1:
            try:
                df_all = pd.read_excel(xls, sheet_name=sheet_names[0])
                cols = [str(c).strip().lower() for c in df_all.columns]
                # Heuristics: must have a Rehearsal column and either Title/Work
                # or Time in Rehearsal/Duration columns to be treated as schedule
                if any(c in cols for c in ("rehearsal", "rehearsal #")) and ("title" in cols or "work" in cols or "time in rehearsal" in cols or "rehearsal time (minutes)" in cols):
                    # build schedule from full sheet
                    schedule_sheet = sheet_names[0]
                    events_sheet = sheet_names[0]
                    df_schedule = df_all.copy()
                    # derive events by taking first row per rehearsal
                    if "Rehearsal" not in df_all.columns and "rehearsal" in cols:
                        # fix capitalization mismatch by finding the actual col name
                        for c in df_all.columns:
                            if str(c).strip().lower() == "rehearsal":
                                df_all = df_all.rename(columns={c: "Rehearsal"})
                                break
                    # ensure Date column present (try common variants)
                    date_col = None
                    for candidate in ("Date", "date", "Rehearsal Date", "rehearsal_date"):
                        if candidate in df_all.columns:
                            date_col = candidate
                            break
                    # create events frame: first row per rehearsal
                    if date_col is None:
                        # fallback: use distinct rehearsals with empty dates
                        df_events = df_all[[c for c in df_all.columns if str(c).strip().lower() in ("rehearsal",)]].drop_duplicates()
                    else:
                        df_events = df_all[["Rehearsal", date_col] + [c for c in df_all.columns if str(c).strip().lower() in ("start time","start","start_time","start time")]]
                        df_events = df_events.groupby("Rehearsal", dropna=False, as_index=False).first()
                        # normalize column name to Date/Start Time
                        if date_col != "Date":
                            df_events = df_events.rename(columns={date_col: "Date"})
                    # assign df_events/df_schedule and continue
                else:
                    return jsonify({
                        "ok": False,
                        "error": "Excel file must contain 'events' and 'schedule' sheets.",
                        "sheets_found": sheet_names,
                    }), 400
            except Exception as e:
                return jsonify({"ok": False, "error": f"Could not parse single-sheet schedule: {e}", "sheets_found": sheet_names}), 400
        else:
            return jsonify({
                "ok": False,
                "error": "Excel file must contain 'events' and 'schedule' sheets.",
                "sheets_found": sheet_names,
            }), 400

    # Load dataframes
    try:
        df_events = pd.read_excel(xls, sheet_name=events_sheet)
        df_schedule = pd.read_excel(xls, sheet_name=schedule_sheet)
        
        # Replace NaN/Inf with None
        df_events = df_events.replace({np.nan: None, np.inf: None, -np.inf: None})
        df_schedule = df_schedule.replace({np.nan: None, np.inf: None, -np.inf: None})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed reading sheets: {e}"}), 400

    if df_events is None: df_events = pd.DataFrame()
    if df_schedule is None: df_schedule = pd.DataFrame()

    # Normalize headers
    events_cols = [str(c).strip() for c in df_events.columns]
    schedule_cols = [str(c).strip() for c in df_schedule.columns]
    df_events.columns = events_cols
    df_schedule.columns = schedule_cols

    # Helper: convert datetime and clean NaN
    def df_to_json_safe_strings(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.copy()
        for col in out.columns:
            try:
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        def conv(x):
            if isinstance(x, pd.Timestamp):
                return x.isoformat()
            if x is None or (isinstance(x, float) and math.isnan(x)):
                return ""
            return x

        return out.map(conv)

    df_events = df_to_json_safe_strings(df_events)
    df_schedule = df_to_json_safe_strings(df_schedule)

    # Parse events into rehearsals
    rehearsals_data = df_events.to_dict(orient="records")
    rehearsals_data = auto_number_rehearsals(rehearsals_data)
    rehearsals_data = normalize_section_column(rehearsals_data)
    rehearsals_data = ensure_include_in_allocation_column(rehearsals_data)
    rehearsals_data = ensure_section_column(rehearsals_data)
    
    # First run ensure_event_type_column to get initial Event Type from Event column
    rehearsals_data = ensure_event_type_column(rehearsals_data)
    
    # Then override if the Event Type column in Excel has explicit non-Rehearsal values
    # This handles cases where both Event and Event Type columns exist
    for reh_row in rehearsals_data:
        # Check if there's an explicit Event Type column value in the original data
        if "Event Type" in df_events.columns:
            idx = rehearsals_data.index(reh_row)
            if idx < len(df_events):
                explicit_event_type = df_events.iloc[idx].get("Event Type", "")
                if explicit_event_type and str(explicit_event_type).strip() and str(explicit_event_type).strip().lower() not in ["", "nan", "rehearsal"]:
                    reh_row["Event Type"] = str(explicit_event_type).strip()

    # Auto-create concerts for Event Type=Concert OR Contest rows
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    if ensemble:
        concerts_created = 0
        concerts_updated = 0
        
        for idx, reh_row in enumerate(rehearsals_data):
            event_type_normalized = str(reh_row.get("Event Type", "")).strip().lower()
            is_concert_or_contest = event_type_normalized in ["concert", "contest"]
            
            # Set Include in allocation to N for concerts and contests
            if is_concert_or_contest:
                reh_row["Include in allocation"] = "N"
            
            if is_concert_or_contest:
                concert_date = reh_row.get("Date", "")
                concert_time = reh_row.get("Start Time", "")
                
                # Normalize date
                if concert_date:
                    try:
                        dt = pd.to_datetime(concert_date)
                        concert_date = dt.strftime("%Y-%m-%d 00:00:00")
                        reh_row["Date"] = concert_date
                    except:
                        concert_date = str(concert_date)
                
                # Find existing concert for this schedule and date
                existing_concert = None
                for c in ensemble.get("concerts", []):
                    if c.get("schedule_id") == schedule_id and c.get("date") == concert_date:
                        existing_concert = c
                        break
                
                if concert_date:
                    concert_id = None
                    
                    # Format date for title
                    try:
                        dt = pd.to_datetime(concert_date)
                        day = dt.day
                        if 10 <= day % 100 <= 20:
                            suffix = 'th'
                        else:
                            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                        formatted_date = dt.strftime(f'%A %B {day}{suffix}')
                    except:
                        formatted_date = str(concert_date)
                    
                    event_label = "Contest" if event_type_normalized == "contest" else "Concert"
                    default_title = f"{ensemble.get('name', 'Unknown Ensemble')} - {event_label} - {formatted_date}"
                    
                    if existing_concert:
                        # Update existing concert
                        existing_concert["title"] = reh_row.get("Concert Title") or existing_concert.get("title", default_title)
                        existing_concert["time"] = reh_row.get("Start Time", existing_concert.get("time", ""))
                        existing_concert["venue"] = reh_row.get("Venue", existing_concert.get("venue", ""))
                        existing_concert["uniform"] = reh_row.get("Uniform", existing_concert.get("uniform", ""))
                        existing_concert["programme"] = reh_row.get("Programme", existing_concert.get("programme", ""))
                        existing_concert["other_info"] = reh_row.get("Other Info", existing_concert.get("other_info", ""))
                        concert_id = existing_concert["id"]
                        concerts_updated += 1
                    else:
                        # Create new concert
                        concert_id = f"concert_{uuid.uuid4().hex[:12]}"
                        new_concert = {
                            "id": concert_id,
                            "title": reh_row.get("Concert Title") or default_title,
                            "date": concert_date,
                            "time": reh_row.get("Start Time", ""),
                            "venue": reh_row.get("Venue", ""),
                            "uniform": reh_row.get("Uniform", ""),
                            "programme": reh_row.get("Programme", ""),
                            "other_info": reh_row.get("Other Info", ""),
                            "status": "scheduled",
                            "schedule_id": schedule_id,
                            "is_auto_generated": True
                        }
                        if "concerts" not in ensemble:
                            ensemble["concerts"] = []
                        ensemble["concerts"].append(new_concert)
                        concerts_created += 1
                    
                    # Link concert_id back to rehearsal row
                    reh_row["concert_id"] = concert_id
        
        # Save updated ensemble with new/updated concerts
        if concerts_created > 0 or concerts_updated > 0:
            save_ensembles(ensembles)

    # Ensure rehearsals_cols includes all columns
    if rehearsals_data and len(rehearsals_data) > 0:
        actual_cols = list(rehearsals_data[0].keys())
        if "Rehearsal" not in actual_cols:
            actual_cols.insert(0, "Rehearsal")
        s["rehearsals_cols"] = actual_cols
    else:
        s["rehearsals_cols"] = list(default_rehearsals_df().columns)
    
    s["rehearsals"] = rehearsals_data

    # Parse schedule into timed works
    # Map columns: Rehearsal, Work (Title), Start Time (Time in Rehearsal), Duration (mins)
    timed_data = []
    for idx, row in df_schedule.iterrows():
        rehearsal_num = row.get("Rehearsal")
        
        # Skip rows with empty/invalid rehearsal numbers
        if pd.isna(rehearsal_num) or rehearsal_num == "" or rehearsal_num is None:
            continue
        
        # Try to convert to int
        try:
            rehearsal_num = int(float(rehearsal_num))
        except (ValueError, TypeError):
            continue  # Skip invalid rehearsal numbers
        
        work_title = row.get("Work") or row.get("Title") or ""
        start_time = row.get("Start Time") or row.get("Time") or "00:00:00"
        duration = row.get("Duration (mins)") or row.get("Duration") or 0
        
        # Convert duration to int
        try:
            duration = int(float(duration))
        except:
            duration = 0
        
        # Build timed row
        timed_row = {
            "_index": int(idx),
            "Rehearsal": rehearsal_num,
            "Title": str(work_title),
            "Time in Rehearsal": str(start_time),
            "Rehearsal Time (minutes)": duration,
            "Break Start (HH:MM)": "",
            "Break End (HH:MM)": "",
            "Section": "Full Ensemble",
        }
        
        # Lookup rehearsal to get Date and Event Type
        reh_row = next((r for r in rehearsals_data if int(r.get("Rehearsal", 0)) == rehearsal_num), None)
        if reh_row:
            timed_row["Date"] = reh_row.get("Date", "")
            timed_row["Event Type"] = reh_row.get("Event Type", "Rehearsal")
            timed_row["Section"] = reh_row.get("Section", "Full Ensemble")
            if reh_row.get("concert_id"):
                timed_row["concert_id"] = reh_row["concert_id"]
        
        timed_data.append(timed_row)
    
    # Add empty placeholder rows for rehearsals that have no scheduled works
    # This ensures all rehearsals appear in the schedule view
    # Make these editable by not marking them as placeholders
    rehearsal_nums_with_works = set(row["Rehearsal"] for row in timed_data)
    for reh_row in rehearsals_data:
        reh_num = reh_row.get("Rehearsal")
        if reh_num and int(reh_num) not in rehearsal_nums_with_works:
            # Create placeholder - just a normal timed row with zero duration
            placeholder_row = {
                "_index": len(timed_data),
                "Rehearsal": int(reh_num),
                "Title": "",  # Empty title so it can be edited
                "Time in Rehearsal": reh_row.get("Start Time", "00:00:00"),
                "Rehearsal Time (minutes)": 0,
                "Break Start (HH:MM)": "",
                "Break End (HH:MM)": "",
                "Section": reh_row.get("Section", "Full Ensemble"),
                "Date": reh_row.get("Date", ""),
                "Event Type": reh_row.get("Event Type", "Rehearsal"),
            }
            if reh_row.get("concert_id"):
                placeholder_row["concert_id"] = reh_row["concert_id"]
            timed_data.append(placeholder_row)
    
    s["timed"] = sanitize_df_records(timed_data)
    s["works"] = []  # No separate works needed
    s["works_cols"] = []
    s["allocation"] = []  # Skip allocation since we have pre-built schedule
    s["schedule"] = []  # Skip intermediate schedule step
    s["generated_at"] = pd.Timestamp.utcnow().isoformat() + "Z"
    s["updated_at"] = int(time.time())

    save_schedule(s)

    return jsonify({
        "ok": True,
        "events_sheet": events_sheet,
        "schedule_sheet": schedule_sheet,
        "rehearsals_rows": int(len(df_events)),
        "timed_rows": int(len(timed_data)),
    })


# ---- API: update schedule for a single rehearsal (order + minutes)
@app.post("/api/update_rehearsal_schedule")
def api_update_rehearsal_schedule():
    require_edit_token()
    payload = request.get_json(force=True)
    rehearsal = int(payload["rehearsal"])
    items = payload["items"]  # list of {Title, Minutes, GroupKey, PlayerLoad}

    s, sid = default_schedule()

    works, rehearsals, alloc, sched = get_frames(s)
    if sched.empty:
        return jsonify({"ok": False, "error": "No schedule exists"}), 400

    sched = sched.copy()
    sched["Rehearsal"] = pd.to_numeric(sched["Rehearsal"], errors="coerce").astype("Int64")

    # remove old rows for this rehearsal
    sched_other = sched[sched["Rehearsal"] != rehearsal].copy()

    # rebuild rows for this rehearsal
    new_rows = []
    for i, it in enumerate(items):
        new_rows.append({
            "Rehearsal": rehearsal,
            "Title": str(it.get("Title", "")).strip(),
            "Rehearsal Time (minutes)": safe_int(it.get("Minutes", 0), 0),
            "GroupKey": str(it.get("GroupKey", "")).strip() or str(it.get("Title", "")).strip(),
            "PlayerLoad": safe_float(it.get("PlayerLoad", 0.0), 0.0),
            "MovementOrder": np.nan,
        })

    sched_new = pd.DataFrame(new_rows)
    sched2 = pd.concat([sched_other, sched_new], ignore_index=True)
    s["schedule"] = sanitize_df_records(sched2.to_dict(orient="records"))

    # Use prepare_all_rehearsals to include non-allocated rehearsals (sectionals)
    rehe_prep = prepare_all_rehearsals(rehearsals)
    timed = compute_timed_df(sched2, rehe_prep)
    s["timed"] = sanitize_df_records(timed.to_dict(orient="records"))
    s["generated_at"] = pd.Timestamp.utcnow().isoformat() + "Z"

    save_schedule(s)
    return jsonify({"ok": True})


# ---- API: export timed excel (for PDF script or download)
@app.get("/export/timed.xlsx")
def export_timed_xlsx():
    s, sid = default_schedule()

    timed = pd.DataFrame(s.get("timed", []))
    if timed.empty:
        abort(404)
    timed.to_excel(TIMED_XLSX_OUT, index=False)
    return send_file(TIMED_XLSX_OUT, as_attachment=True)


# ---- Optional: generate PDF via existing Script 4
@app.post("/api/export_pdf")
def api_export_pdf():
    require_edit_token()
    if not mod4_exists:
        return jsonify({"ok": False, "error": "Script 4 not found"}), 400

    s, sid = default_schedule()

    timed = pd.DataFrame(s.get("timed", []))
    if timed.empty:
        return jsonify({"ok": False, "error": "No timed schedule available"}), 400

    timed.to_excel(TIMED_XLSX_OUT, index=False)

    try:
        subprocess.check_call(["python", SCRIPT4])
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.get("/export/pdf")
def download_pdf():
    # Script 4 usually writes DCO_Rehearsal_Schedule.pdf
    pdf_path = "DCO_Rehearsal_Schedule.pdf"
    if not os.path.exists(pdf_path):
        abort(404)
    return send_file(pdf_path, as_attachment=True)


@app.get("/s/<schedule_id>/pdf")
def download_schedule_pdf(schedule_id):
    """Download PDF for a specific schedule - accessible by members"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)

    u = current_user()
    # Check permissions
    if u and u.get("is_admin"):
        pass
    else:
        if s.get("status") != "published":
            abort(404)
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r

    # Get ensemble name and concerts
    ensembles = load_ensembles()
    ensemble = next((e for e in ensembles if e["id"] == s.get("ensemble_id")), None)
    ensemble_name = ensemble["name"] if ensemble else s.get("ensemble_id", "Schedule")
    
    # Get linked concerts
    linked_concerts = []
    if ensemble:
        linked_concerts = [c for c in ensemble.get("concerts", []) if c.get("schedule_id") == schedule_id and c.get("status") == "scheduled"]

    # --- Unified PDF export logic for both schedule and user views ---
    # Build a unified event list (rehearsals and concerts/contests) in chronological order
    timed_data = clean_timed_data(s.get("timed", []))
    timed = pd.DataFrame(timed_data)
    if timed.empty:
        return "No schedule data available", 404
    rehearsals_data = s.get("rehearsals", [])
    concerts = []
    ensemble = ensemble or {"name": ensemble_name}
    # Gather concerts for this schedule
    if ensemble and "concerts" in ensemble:
        concerts = []
        for c in ensemble["concerts"]:
            if c.get("schedule_id") == schedule_id and c.get("status") == "scheduled":
                # Replace {ensemble_name} in title for web/API (not just PDF)
                title = c.get("title", "")
                if "{ensemble_name}" in title:
                    title = title.replace("{ensemble_name}", ensemble_name)
                c = dict(c)  # Copy to avoid mutating original
                c["title"] = title
                concerts.append(c)
    # Build event list
    events_list = []
    grouped = timed.groupby("Rehearsal")
    for reh_num, group in grouped:
        first_row = group.iloc[0]
        event_type = first_row.get("Event Type", "Rehearsal")
        date_val = first_row.get("Date")
        try:
            if isinstance(date_val, (int, float)):
                date_obj = pd.to_datetime(date_val, unit='ms')
            elif hasattr(date_val, 'strftime'):
                date_obj = date_val
            else:
                date_obj = pd.to_datetime(date_val)
        except Exception:
            date_obj = pd.to_datetime(date_val, errors="coerce")
        if event_type in ("Concert", "Contest"):
            # Find concert/contest details robustly
            cid = first_row.get("concert_id")
            concert = None
            if cid:
                concert = next((c for c in concerts if c.get("id") == cid), None)
            # Fallback: match by date and ensemble if id is missing or not found
            if not concert:
                concert = next((c for c in concerts if str(c.get("date"))[:10] == str(date_obj.date()) and c.get("schedule_id") == schedule_id), None)
            # Final fallback: use rehearsal row info
            if not concert:
                concert = {
                    "title": first_row.get("Title", event_type),
                    "date": str(date_obj.date()),
                    "time": first_row.get("Time in Rehearsal", ""),
                    "schedule_id": schedule_id,
                    "id": cid,
                }
            events_list.append({
                "type": event_type.lower(),
                "date": date_obj,
                "ensemble_name": ensemble_name,
                "concert": concert,
            })
        else:
            section = first_row.get("Section", "Full Ensemble")
            # Sort by actual time in minutes for robust ordering
            import core
            def _parse_minutes(val):
                try:
                    return core.minutes_from_timecell(val)
                except Exception:
                    return None
            group_sorted = group.copy()
            if "Time in Rehearsal" in group_sorted.columns:
                group_sorted["__sort_minutes"] = group_sorted["Time in Rehearsal"].apply(_parse_minutes)
                group_sorted = group_sorted.sort_values("__sort_minutes", na_position="last")
            events_list.append({
                "type": "rehearsal",
                "date": date_obj,
                "ensemble_name": ensemble_name,
                "rehearsal_num": int(reh_num),
                "section": section,
                "items": group_sorted.to_dict(orient="records"),
            })
    # Sort all events by date
    events_list.sort(key=lambda x: x.get("date", pd.Timestamp.max))
    # Exclude events before today
    import datetime
    today = datetime.date.today()
    def is_future_or_today(ev):
        d = ev.get('date')
        if d is None or pd.isna(d):
            return False
        d = pd.to_datetime(d).date()
        return d >= today
    events_list = [ev for ev in events_list if is_future_or_today(ev)]
    # --- PDF rendering (same as /my/pdf) ---
    try:
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            PageBreak,
            KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER
    except Exception as e:
        import traceback
        print("[PDF] Import error while preparing PDF libraries:", e)
        print(traceback.format_exc())
        abort(500, description="PDF generation dependencies are not available on the server.")
    base_font = 'Times-Roman'
    base_font_bold = 'Times-Bold'
    buffer = BytesIO()
    def draw_footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFont(base_font, 9)
        footer_y = 1.8 * cm
        canvas_obj.setFillColor(colors.HexColor('#374151'))
        logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
        logo_drawn = False
        if os.path.exists(logo_path):
            try:
                canvas_obj.drawImage(logo_path, 1.5 * cm, footer_y - 0.2 * cm, width=2.5 * cm, height=1.5 * cm, preserveAspectRatio=True, mask='auto')
                logo_drawn = True
            except Exception as e:
                print(f'Could not load logo: {e}')
                pass
        contact_x = 4.5 * cm if logo_drawn else 1.5 * cm
        canvas_obj.setFont(base_font_bold, 10)
        canvas_obj.drawString(contact_x, footer_y + 0.8 * cm, 'Jack Capstaff')
        canvas_obj.setFont(base_font, 8)
        canvas_obj.drawString(contact_x, footer_y + 0.5 * cm, 'M: 07805 165842  |  W: jackcapstaff.com')
        canvas_obj.drawString(contact_x, footer_y + 0.2 * cm, 'E: jack@jackcapstaff.com')
        canvas_obj.setFont(base_font, 9)
        page_text = f'Page {doc_obj.page}'
        canvas_obj.drawRightString(A4[0] - 1.5 * cm, footer_y + 0.5 * cm, page_text)
        canvas_obj.restoreState()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=3 * cm,
    )
    styles = getSampleStyleSheet()
    title_page_style = ParagraphStyle('TitlePage', parent=styles['Heading1'], alignment=TA_CENTER,
                                      fontSize=28, spaceAfter=22, fontName=base_font_bold, textColor=colors.HexColor('#1e40af'))
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Heading2'], alignment=TA_CENTER,
                                    fontSize=16, spaceAfter=10, fontName=base_font, textColor=colors.HexColor('#3b82f6'))
    date_range_style = ParagraphStyle('DateRange', parent=styles['Normal'], alignment=TA_CENTER,
                                      fontSize=12, spaceAfter=28, fontName=base_font, textColor=colors.HexColor('#6b7280'))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14,
                                   spaceAfter=8, spaceBefore=12, fontName=base_font_bold)
    programme_style = ParagraphStyle('Programme', parent=styles['Normal'], fontSize=10,
                                     fontName=base_font, leading=14, leftIndent=0)
    story = []
    story.append(Spacer(1, 6 * cm))
    story.append(Paragraph(f"{ensemble_name}", title_page_style))
    story.append(Paragraph("Rehearsal Schedule", subtitle_style))
    all_dates = [ev.get('date') for ev in events_list if ev.get('date') is not None and not pd.isna(ev.get('date'))]
    if all_dates:
        dates_series = pd.Series(all_dates)
        start_date = dates_series.min().strftime('%B %d, %Y')
        end_date = dates_series.max().strftime('%B %d, %Y')
        story.append(Paragraph(f"{start_date} — {end_date}", date_range_style))
    story.append(PageBreak())
    for ev in events_list:
        date_obj = ev.get('date')
        if pd.isna(date_obj):
            continue
        date_obj = pd.to_datetime(date_obj)
        day = date_obj.day
        suffix = 'th'
        if not (10 <= day % 100 <= 20):
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        date_str = date_obj.strftime(f'%A {day}{suffix} %B, %Y')
        if ev.get('type') in ('concert', 'contest'):
            concert = ev.get('concert', {})
            concert_data = []
            concert_title = concert.get('title', ev.get('type', 'Concert').capitalize())
            # Replace {ensemble_name} in title if present
            if '{ensemble_name}' in concert_title:
                concert_title = concert_title.replace('{ensemble_name}', ev.get('ensemble_name', ''))
            concert_data.append([Paragraph(f"<b>{ev.get('ensemble_name', '')}</b> — {concert_title}", styles['Normal'])])
            concert_data.append([Paragraph(f"<b>Date:</b> {date_str}", styles['Normal'])])
            concert_data.append([Paragraph(f"<b>Time:</b> {concert.get('time', 'TBA')}", styles['Normal'])])
            if concert.get('venue'):
                concert_data.append([Paragraph(f"<b>Venue:</b> {concert.get('venue')}", styles['Normal'])])
            if concert.get('uniform'):
                concert_data.append([Paragraph(f"<b>Uniform:</b> {concert.get('uniform')}", styles['Normal'])])
            programme_text = concert.get('programme') or ""
            if programme_text:
                def parse_programme_sets(text: str):
                    norm_text = text.replace('\r\n', '\n').replace('\r', '\n')
                    lines = norm_text.split('\n')
                    heading = re.compile(r'^\s*set\s+(\d+)\s*:?\s*(.*)$', re.IGNORECASE)
                    sets = []
                    current = None
                    for line in lines:
                        m = heading.match(line)
                        if m:
                            if current:
                                sets.append(current)
                            current = {'num': m.group(1), 'lines': []}
                            inline = m.group(2)
                            if inline:
                                current['lines'].append(inline)
                        else:
                            if current is None:
                                current = {'num': None, 'lines': []}
                            current['lines'].append(line)
                    if current:
                        sets.append(current)
                    return sets
                sets = parse_programme_sets(programme_text)
                has_numbered_sets = any(s.get('num') for s in sets)
                def format_lines(lines):
                    return '<br/>'.join([html.escape(line) for line in lines])
                if has_numbered_sets:
                    from reportlab.platypus import Table as InnerTable
                    set_cells = []
                    for s in sets:
                        set_num = s.get('num', '?')
                        formatted_lines = format_lines(s.get('lines', []))
                        set_cells.append(Paragraph(f"<b>Set {set_num}:</b><br/>{formatted_lines}", programme_style))
                    sets_per_row = 2 if len(set_cells) > 1 else 1
                    set_rows = []
                    for i in range(0, len(set_cells), sets_per_row):
                        set_rows.append(set_cells[i:i + sets_per_row])
                    sets_table = InnerTable(set_rows, colWidths=[8 * cm] * (sets_per_row))
                    sets_table.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ]))
                    concert_data.append([sets_table])
                else:
                    formatted_lines = format_lines(sets[0].get('lines', [])) if sets else ""
                    concert_data.append([Paragraph(f"<b>Programme:</b><br/>{formatted_lines}", programme_style)])
            if concert.get('other_info'):
                concert_data.append([Paragraph(f"<b>Notes:</b> {concert.get('other_info')}", styles['Normal'])])
            concert_table = Table(concert_data, colWidths=[16 * cm])
            concert_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeafe')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), base_font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('FONTNAME', (0, 1), (-1, -1), base_font),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#93c5fd')),
                ('BOX', (0, 0), (-1, -1), 2, colors.HexColor('#3b82f6')),
            ]))
            story.append(KeepTogether([concert_table, Spacer(1, 0.5 * cm)]))
        else:
            reh_num = ev.get('rehearsal_num')
            section = ev.get('section')
            reh_heading = f"<b>{ev.get('ensemble_name', '')}</b> — Rehearsal {reh_num} - {date_str}"
            if section and section != 'Full Ensemble':
                reh_heading += f" ({section})"
            table_data = [['Time', 'Item']]
            for row in ev.get('items', []):
                time_str = row.get('Time in Rehearsal', '')
                title = row.get('Title', '')
                table_data.append([time_str, title])
            table = Table(table_data, colWidths=[3 * cm, 13 * cm])
            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), base_font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('FONTNAME', (0, 1), (-1, -1), base_font),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]
            for i in range(1, len(table_data)):
                if i % 2 == 1:
                    table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#dbeafe')))
                else:
                    table_style.append(('BACKGROUND', (0, i), (-1, i), colors.white))
            table.setStyle(TableStyle(table_style))
            story.append(KeepTogether([
                Paragraph(reh_heading, heading_style),
                table,
                Spacer(1, 0.5 * cm)
            ]))
    try:
        doc.page = 0
        doc.page_count = 0
        doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=draw_footer)
        buffer.seek(0)
        filename = f"{ensemble_name.replace(' ', '_')}_Schedule.pdf"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        import traceback
        print(f"[PDF] Exception while building schedule PDF: {e}")
        print(traceback.format_exc())
        abort(500, description="An error occurred while generating the PDF. See server logs for details.")


@app.get("/my/pdf")
def download_my_schedule_pdf():
    """Download personalized PDF showing all rehearsals for the current user"""
    redir = login_required_or_redirect()
    if redir:
        return redir
    
    u = current_user()
    ensembles = load_ensembles()
    memberships = load_memberships()

    if u and u.get("is_admin"):
        my_ensembles = ensembles
    else:
        my_ids = {m.get("ensemble_id") for m in memberships if m.get("user_id") == (u or {}).get("id") and m.get("status", "active") == "active"}
        my_ensembles = [e for e in ensembles if e.get("id") in my_ids]

    # Get published schedules for user's ensembles
    schedule_summaries = []
    all_schedule_ids = list_schedule_ids()
    
    for sid in all_schedule_ids:
        s = load_schedule(sid)
        if not s:
            continue

        if u and u.get("is_admin"):
            schedule_summaries.append(s)
        else:
            if s.get("status") != "published":
                continue
            if any(e["id"] == s.get("ensemble_id") for e in my_ensembles):
                schedule_summaries.append(s)

    # Extract rehearsal + concert data
    events_list = []
    ensemble_map = {e["id"]: e["name"] for e in ensembles}
    
    for sched in schedule_summaries:
        schedule_id = sched["id"]
        ensemble_id = sched.get("ensemble_id")
        ensemble_name = ensemble_map.get(ensemble_id, "Unknown")
        
        try:
            timed_data = sched.get("timed", [])
            if not timed_data:
                continue

            timed_df = pd.DataFrame(timed_data)
            if timed_df.empty:
                continue

            # Build concert lookup for this schedule (match by id or date)
            ensemble = next((e for e in ensembles if e.get("id") == ensemble_id), None)
            ensemble_concerts = ensemble.get("concerts", []) if ensemble else []
            # Gather rehearsal dates for fallback matching
            rehearsal_dates = set()
            rehearsal_dates_norm = set()
            for reh_row in sched.get("rehearsals", []):
                dval = reh_row.get("Date")
                if dval:
                    try:
                        dt = pd.to_datetime(dval)
                        rehearsal_dates.add(dt.strftime("%Y-%m-%d 00:00:00"))
                        rehearsal_dates_norm.add(dt.strftime("%Y-%m-%d"))
                    except Exception:
                        pass

            linked_concerts = []
            for c in ensemble_concerts:
                if c.get("status") != "scheduled":
                    continue
                c_date = c.get("date", "")
                c_date_norm = str(c_date)[:10]
                if (
                    c.get("schedule_id") == schedule_id
                    or c_date in rehearsal_dates
                    or c_date_norm in rehearsal_dates_norm
                ):
                    linked_concerts.append(c)

            concert_by_id = {c.get("id"): c for c in linked_concerts if c.get("id")}
            concert_by_date = {str(c.get("date", ""))[:10]: c for c in linked_concerts if c.get("date")}

            # Group by rehearsal number
            grouped = timed_df.groupby("Rehearsal")

            for reh_num, group in grouped:
                date_val = group.iloc[0].get("Date")

                # Skip if date is NaT or missing
                if pd.isna(date_val) or date_val is None:
                    print(f"[PDF] Skipping rehearsal {reh_num} - NaT or None date")
                    continue

                try:
                    if isinstance(date_val, (int, float)):
                        date_obj = pd.to_datetime(date_val, unit='ms')
                    elif hasattr(date_val, 'to_pydatetime'):
                        date_obj = date_val.to_pydatetime()
                    else:
                        date_obj = pd.to_datetime(date_val)

                    if pd.isna(date_obj):
                        print(f"[PDF] Skipping rehearsal {reh_num} - date conversion resulted in NaT")
                        continue

                except Exception as e:
                    print(f"[PDF] Skipping rehearsal {reh_num} - date parsing failed: {e}")
                    continue

                event_type = group.iloc[0].get("Event Type", "Rehearsal")

                if event_type == "Concert":
                    cid = group.iloc[0].get("concert_id")
                    concert = concert_by_id.get(cid)
                    if not concert:
                        concert = concert_by_date.get(str(date_obj.date())) or concert_by_date.get(str(date_val)[:10])

                    # Fallback: synthesize from timed row
                    if not concert:
                        concert = {
                            "title": group.iloc[0].get("Title", "Concert"),
                            "date": str(date_obj.date()),
                            "time": group.iloc[0].get("Time in Rehearsal", ""),
                            "schedule_id": schedule_id,
                            "id": cid,
                        }

                    events_list.append({
                        "type": "concert",
                        "date": date_obj,
                        "ensemble_name": ensemble_name,
                        "concert": concert,
                    })
                else:
                    section = group.iloc[0].get("Section", "Full Ensemble") if not group.empty else "Full Ensemble"

                    events_list.append({
                        "type": "rehearsal",
                        "date": date_obj,
                        "ensemble_name": ensemble_name,
                        "rehearsal_num": int(reh_num),
                        "section": section,
                        "items": group.to_dict(orient="records"),
                    })
        except Exception as e:
            print(f"Error processing schedule {schedule_id}: {e}")
            continue

    # Sort by date
    events_list.sort(key=lambda x: x.get("date", pd.Timestamp.max))

    if not events_list:
        return "No rehearsals found", 404

    # Create PDF aligned with schedule view styling (title page, serif fonts, footer)
    try:
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            PageBreak,
            KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER

        base_font = 'Times-Roman'
        base_font_bold = 'Times-Bold'

        buffer = BytesIO()
    except Exception as e:
        import traceback
        print("[PDF] Import error while preparing PDF libraries for /my/pdf:", e)
        print(traceback.format_exc())
        abort(500, description="PDF generation dependencies are not available on the server.")

    def draw_footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFont(base_font, 9)
        footer_y = 1.8 * cm
        canvas_obj.setFillColor(colors.HexColor('#374151'))

        logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
        logo_drawn = False
        if os.path.exists(logo_path):
            try:
                canvas_obj.drawImage(logo_path, 1.5 * cm, footer_y - 0.2 * cm, width=2.5 * cm, height=1.5 * cm, preserveAspectRatio=True, mask='auto')
                logo_drawn = True
            except Exception as e:
                print(f'Could not load logo: {e}')
                pass

        contact_x = 4.5 * cm if logo_drawn else 1.5 * cm
        canvas_obj.setFont(base_font_bold, 10)
        canvas_obj.drawString(contact_x, footer_y + 0.8 * cm, 'Jack Capstaff')
        canvas_obj.setFont(base_font, 8)
        canvas_obj.drawString(contact_x, footer_y + 0.5 * cm, 'M: 07805 165842  |  W: jackcapstaff.com')
        canvas_obj.drawString(contact_x, footer_y + 0.2 * cm, 'E: jack@jackcapstaff.com')

        canvas_obj.setFont(base_font, 9)
        page_text = f'Page {doc_obj.page}'
        canvas_obj.drawRightString(A4[0] - 1.5 * cm, footer_y + 0.5 * cm, page_text)
        canvas_obj.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=3 * cm,
    )

    styles = getSampleStyleSheet()
    title_page_style = ParagraphStyle('TitlePage', parent=styles['Heading1'], alignment=TA_CENTER,
                                      fontSize=28, spaceAfter=22, fontName=base_font_bold, textColor=colors.HexColor('#1e40af'))
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Heading2'], alignment=TA_CENTER,
                                    fontSize=16, spaceAfter=10, fontName=base_font, textColor=colors.HexColor('#3b82f6'))
    date_range_style = ParagraphStyle('DateRange', parent=styles['Normal'], alignment=TA_CENTER,
                                      fontSize=12, spaceAfter=28, fontName=base_font, textColor=colors.HexColor('#6b7280'))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14,
                                   spaceAfter=8, spaceBefore=12, fontName=base_font_bold)
    programme_style = ParagraphStyle('Programme', parent=styles['Normal'], fontSize=10,
                                     fontName=base_font, leading=14, leftIndent=0)

    story = []

    story.append(Spacer(1, 6 * cm))
    story.append(Paragraph("My Rehearsal Schedule", title_page_style))
    story.append(Paragraph(u.get('email', 'Member'), subtitle_style))

    all_dates = []
    for ev in events_list:
        d = ev.get('date')
        if d is not None and not pd.isna(d):
            try:
                all_dates.append(pd.to_datetime(d))
            except Exception:
                pass
    if all_dates:
        dates_series = pd.Series(all_dates)
        start_date = dates_series.min().strftime('%B %d, %Y')
        end_date = dates_series.max().strftime('%B %d, %Y')
        story.append(Paragraph(f"{start_date} — {end_date}", date_range_style))

    story.append(PageBreak())

    for ev in events_list:
        date_obj = ev.get('date')
        if pd.isna(date_obj):
            continue
        date_obj = pd.to_datetime(date_obj)
        day = date_obj.day
        suffix = 'th'
        if not (10 <= day % 100 <= 20):
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        date_str = date_obj.strftime(f'%A {day}{suffix} %B, %Y')

        if ev.get('type') == 'concert':
            concert = ev.get('concert', {})
            concert_data = []
            concert_title = concert.get('title', 'Concert')

            concert_data.append([Paragraph(f"<b>{ev.get('ensemble_name', '')}</b> — 🎵 {concert_title}", styles['Normal'])])
            concert_data.append([Paragraph(f"<b>Date:</b> {date_str}", styles['Normal'])])
            concert_data.append([Paragraph(f"<b>Time:</b> {concert.get('time', 'TBA')}", styles['Normal'])])
            if concert.get('venue'):
                concert_data.append([Paragraph(f"<b>Venue:</b> {concert.get('venue')}", styles['Normal'])])
            if concert.get('uniform'):
                concert_data.append([Paragraph(f"<b>Uniform:</b> {concert.get('uniform')}", styles['Normal'])])

            programme_text = concert.get('programme') or ""
            if programme_text:
                def parse_programme_sets(text: str):
                    norm_text = text.replace('\r\n', '\n').replace('\r', '\n')
                    lines = norm_text.split('\n')
                    heading = re.compile(r'^\s*set\s+(\d+)\s*:?\s*(.*)$', re.IGNORECASE)
                    sets = []
                    current = None
                    for line in lines:
                        m = heading.match(line)
                        if m:
                            if current:
                                sets.append(current)
                            current = {'num': m.group(1), 'lines': []}
                            inline = m.group(2)
                            if inline:
                                current['lines'].append(inline)
                        else:
                            if current is None:
                                current = {'num': None, 'lines': []}
                            current['lines'].append(line)
                    if current:
                        sets.append(current)
                    return sets

                sets = parse_programme_sets(programme_text)
                has_numbered_sets = any(s.get('num') for s in sets)

                def format_lines(lines):
                    return '<br/>'.join([html.escape(line) for line in lines])

                if has_numbered_sets:
                    from reportlab.platypus import Table as InnerTable
                    set_cells = []
                    for s in sets:
                        set_num = s.get('num', '?')
                        formatted_lines = format_lines(s.get('lines', []))
                        set_cells.append(Paragraph(f"<b>Set {set_num}:</b><br/>{formatted_lines}", programme_style))

                    sets_per_row = 2 if len(set_cells) > 1 else 1
                    set_rows = []
                    for i in range(0, len(set_cells), sets_per_row):
                        set_rows.append(set_cells[i:i + sets_per_row])

                    sets_table = InnerTable(set_rows, colWidths=[8 * cm] * (sets_per_row))
                    sets_table.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ]))
                    concert_data.append([sets_table])
                else:
                    formatted_lines = format_lines(sets[0].get('lines', [])) if sets else ""
                    concert_data.append([Paragraph(f"<b>Programme:</b><br/>{formatted_lines}", programme_style)])
            if concert.get('other_info'):
                concert_data.append([Paragraph(f"<b>Notes:</b> {concert.get('other_info')}", styles['Normal'])])

            concert_table = Table(concert_data, colWidths=[16 * cm])
            concert_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeafe')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), base_font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('FONTNAME', (0, 1), (-1, -1), base_font),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#93c5fd')),
                ('BOX', (0, 0), (-1, -1), 2, colors.HexColor('#3b82f6')),
            ]))

            story.append(KeepTogether([concert_table, Spacer(1, 0.5 * cm)]))

        else:
            reh_num = ev.get('rehearsal_num')
            section = ev.get('section')
            reh_heading = f"<b>{ev.get('ensemble_name', '')}</b> — Rehearsal {reh_num} - {date_str}"
            if section and section != 'Full Ensemble':
                reh_heading += f" ({section})"

            heading_para = Paragraph(reh_heading, heading_style)

            table_data = [['Time', 'Item']]
            for item in ev.get('items', []):
                time_str = item.get('Time in Rehearsal', '')
                title = item.get('Title', '')
                table_data.append([time_str, title])

            table = Table(table_data, colWidths=[3 * cm, 12.5 * cm])
            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5e7eb')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), base_font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#9ca3af')),
                ('FONTNAME', (0, 1), (-1, -1), base_font),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ]
            for i in range(1, len(table_data)):
                table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f8fafc') if i % 2 == 1 else colors.white))
            table.setStyle(TableStyle(table_style))

            story.append(KeepTogether([heading_para, table, Spacer(1, 0.4 * cm)]))

    try:
        doc.page = 0
        doc.page_count = 0
        doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=draw_footer)
        buffer.seek(0)

        filename = "My_Rehearsal_Schedule.pdf"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        import traceback
        print(f"[PDF] Exception while building /my/pdf: {e}")
        print(traceback.format_exc())
        abort(500, description="An error occurred while generating the PDF. See server logs for details.")


# ----------------------------
# Invitation Management
# ----------------------------
@app.route("/admin/ensembles/<ensemble_id>/invitations", methods=["GET"])
def admin_view_invitations(ensemble_id):
    r = admin_required_or_403()
    if r: return r
    
    ensemble = next((e for e in load_ensembles() if e["id"] == ensemble_id), None)
    if not ensemble:
        abort(404)
    
    invites = [i for i in load_invitations() if i.get("ensemble_id") == ensemble_id]
    # Sort by creation date descending
    invites.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    # Attach shareable links
    for inv in invites:
        inv["invite_url"] = build_invitation_link(inv.get("code", ""))
    
    u = current_user()
    return render_template("admin_invitations.html", ensemble=ensemble, invitations=invites, user=u)

@app.route("/admin/ensembles/<ensemble_id>/invitations/create", methods=["POST"])
def admin_create_invitation(ensemble_id):
    r = admin_required_or_403()
    if r: return r
    
    ensemble = next((e for e in load_ensembles() if e["id"] == ensemble_id), None)
    if not ensemble:
        abort(404)
    
    # Generate unique code
    code = f"{ensemble_id[:6]}-{uuid.uuid4().hex[:8]}"
    
    # Get form parameters
    expires_days = int(request.form.get("expires_days", 30))
    max_uses = int(request.form.get("max_uses", 10))
    
    expires_at = int(time.time()) + (expires_days * 24 * 60 * 60)
    
    u = current_user()
    invite = {
        "id": f"inv_{uuid.uuid4().hex[:10]}",
        "ensemble_id": ensemble_id,
        "code": code,
        "created_by": u.get("id") if u else None,
        "created_at": int(time.time()),
        "expires_at": expires_at,
        "max_uses": max_uses,
        "used_count": 0,
        "status": "active",  # active | expired | disabled
    }
    
    invites = load_invitations()
    invites.append(invite)
    save_invitations(invites)
    
    return redirect(url_for("admin_view_invitations", ensemble_id=ensemble_id))

@app.route("/admin/invitations/<invitation_id>/disable", methods=["POST"])
def admin_disable_invitation(invitation_id):
    r = admin_required_or_403()
    if r: return r
    
    invites = load_invitations()
    invite = next((i for i in invites if i["id"] == invitation_id), None)
    if not invite:
        abort(404)
    
    invite["status"] = "disabled"
    save_invitations(invites)
    
    return redirect(url_for("admin_view_invitations", ensemble_id=invite["ensemble_id"]))


# ----------------------------
# Conductor Mode
# ----------------------------
@app.get("/conduct/<schedule_id>/<int:rehearsal_num>")
def conduct_view(schedule_id, rehearsal_num):
    """Full-screen conductor view for live rehearsal tracking"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    
    u = current_user()
    # Permissions: Must be logged in and either admin or member of ensemble
    if not u:
        return redirect(url_for("login_page"))
    
    if not u.get("is_admin"):
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r
    
    # Get rehearsal
    rehearsal = next((r for r in s.get("rehearsals", []) if int(r.get("Rehearsal", 0)) == rehearsal_num), None)
    if not rehearsal:
        abort(404)
    
    return render_template("conduct.html",
                          schedule_id=schedule_id,
                          rehearsal_num=rehearsal_num,
                          schedule_name=s.get("name", "Untitled"),
                          user=u)

@app.get("/api/s/<schedule_id>/conduct/<int:rehearsal_num>/data")
def conduct_get_data(schedule_id, rehearsal_num):
    """Get rehearsal data for conductor mode"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    
    u = current_user()
    if not u:
        return jsonify({"error": "Not logged in"}), 401
    
    if not u.get("is_admin"):
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r
    
    # Get rehearsal
    rehearsal = next((r for r in s.get("rehearsals", []) if int(r.get("Rehearsal", 0)) == rehearsal_num), None)
    if not rehearsal:
        return jsonify({"error": "Rehearsal not found"}), 404
    
    # Get timed schedule for this rehearsal
    timed_items = [t for t in s.get("timed", []) if int(t.get("Rehearsal", 0)) == rehearsal_num]
    
    # Get existing conducting log if any
    existing_log = next((log for log in s.get("conducting_logs", []) if log.get("rehearsal_num") == rehearsal_num), None)
    
    return jsonify({
        "ok": True,
        "rehearsal": rehearsal,
        "timed_items": timed_items,
        "existing_log": existing_log,
        "schedule_name": s.get("name", "Untitled")
    })

@app.get("/api/s/<schedule_id>/conducting_insights")
def get_conducting_insights(schedule_id):
    """Get aggregated conducting data for variance insights"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    
    u = current_user()
    if not u:
        return jsonify({"error": "Not logged in"}), 401
    
    if not u.get("is_admin"):
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r
    
    # Aggregate conducting logs by work title
    work_insights = {}
    
    for log in s.get("conducting_logs", []):
        for item in log.get("items", []):
            if item.get("status") != "completed":
                continue
            
            title = item.get("title", "Unknown")
            actual_secs = item.get("actual_duration_seconds", 0) or (item.get("actual_duration", 0) * 60)
            scheduled_secs = item.get("scheduled_duration_seconds", 0) or (item.get("scheduled_duration", 0) * 60)
            
            if not scheduled_secs:
                continue
            
            if title not in work_insights:
                work_insights[title] = {
                    "title": title,
                    "rehearsals": [],
                    "total_times": 0,
                    "avg_actual_seconds": 0,
                    "avg_scheduled_seconds": 0,
                    "avg_variance_seconds": 0,
                    "avg_variance_pct": 0
                }
            
            variance_secs = actual_secs - scheduled_secs
            variance_pct = (variance_secs / scheduled_secs * 100) if scheduled_secs else 0
            
            work_insights[title]["rehearsals"].append({
                "rehearsal_num": log.get("rehearsal_num"),
                "rehearsal_date": next((r.get("Date") for r in s.get("rehearsals", []) if r.get("Rehearsal") == log.get("rehearsal_num")), None),
                "actual_seconds": actual_secs,
                "scheduled_seconds": scheduled_secs,
                "variance_seconds": variance_secs,
                "variance_pct": variance_pct
            })
    
    # Calculate averages
    for title, data in work_insights.items():
        count = len(data["rehearsals"])
        if count > 0:
            data["total_times"] = count
            data["avg_actual_seconds"] = sum(r["actual_seconds"] for r in data["rehearsals"]) / count
            data["avg_scheduled_seconds"] = sum(r["scheduled_seconds"] for r in data["rehearsals"]) / count
            data["avg_variance_seconds"] = sum(r["variance_seconds"] for r in data["rehearsals"]) / count
            data["avg_variance_pct"] = sum(r["variance_pct"] for r in data["rehearsals"]) / count
    
    return jsonify({
        "ok": True,
        "insights": list(work_insights.values())
    })


@app.post("/api/s/<schedule_id>/conduct/<int:rehearsal_num>/log")
def conduct_save_log(schedule_id, rehearsal_num):
    """Save conducting log"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    
    u = current_user()
    if not u:
        return jsonify({"error": "Not logged in"}), 401
    
    if not u.get("is_admin"):
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r
    
    data = request.get_json() or {}
    log_data = data.get("log", {})
    
    # Validate log structure
    if not log_data.get("rehearsal_num") or not log_data.get("items"):
        return jsonify({"error": "Invalid log data"}), 400
    
    # Add conductor info
    log_data["conducted_by"] = u.get("id")
    log_data["saved_at"] = int(time.time())
    
    # Find and replace existing log for this rehearsal, or append new
    logs = s.get("conducting_logs", [])
    existing_idx = next((i for i, log in enumerate(logs) if log.get("rehearsal_num") == rehearsal_num), None)
    
    if existing_idx is not None:
        logs[existing_idx] = log_data
    else:
        logs.append(log_data)
    
    s["conducting_logs"] = logs
    s["updated_at"] = int(time.time())
    save_schedule(s)
    
    return jsonify({"ok": True})

@app.get("/conduct/<schedule_id>/<int:rehearsal_num>/report")
def conduct_report_view(schedule_id, rehearsal_num):
    """View post-rehearsal report"""
    s = load_schedule(schedule_id)
    if not s:
        abort(404)
    
    u = current_user()
    if not u:
        return redirect(url_for("login_page"))
    
    # Permissions: Admin or member of ensemble
    if not u.get("is_admin"):
        r = ensemble_member_required_or_403(s["ensemble_id"])
        if r: return r
    
    # Get conducting log
    log = next((log for log in s.get("conducting_logs", []) if log.get("rehearsal_num") == rehearsal_num), None)
    if not log:
        abort(404, description="No conducting log found for this rehearsal")
    
    # Get rehearsal details
    rehearsal = next((r for r in s.get("rehearsals", []) if int(r.get("Rehearsal", 0)) == rehearsal_num), None)
    
    # Get conductor name
    conductor_user = None
    if log.get("conducted_by"):
        users = load_users()
        conductor_user = next((usr for usr in users if usr.get("id") == log.get("conducted_by")), None)
    
    return render_template("conduct_report.html",
                          schedule_id=schedule_id,
                          schedule_name=s.get("name", "Untitled"),
                          rehearsal_num=rehearsal_num,
                          rehearsal=rehearsal,
                          log=log,
                          conductor=conductor_user,
                          user=u)


# ----------------------------
# Main
# ----------------------------
def run_followup_reminders(dry_run: bool = False) -> dict:
    """Run follow-up reminder rules across all published schedules.

    Rules implemented:
      - 1 week after publish: if no attendance responses at all, notify ensemble admins + global admins.
      - 1 week before each event: remind non-responders and send a summary to admins.

    Returns a summary dict of actions taken.
    """
    now = int(time.time())
    summary = {"schedules_checked": 0, "actions": []}

    for sid in list_schedule_ids():
        s = load_schedule(sid)
        if not s or s.get("status") != "published":
            continue
        summary["schedules_checked"] += 1
        ensemble_id = s.get("ensemble_id")
        ens = get_ensemble_by_id(ensemble_id) or {}
        ensemble_name = ens.get("name") or ensemble_id
        fn = s.setdefault("followup_notifications", {})

        # 1 week after publish
        published_at = s.get("published_at")
        if published_at and not fn.get("post_publish_week_sent") and now >= (published_at + 7 * 24 * 3600):
            # determine if any attendance responses exist
            att = s.get("attendance") or {}
            any_responses = False
            for bucket in att.values():
                if isinstance(bucket, dict):
                    for v in bucket.values():
                        if isinstance(v, dict) and v.get("status"):
                            any_responses = True
                            break
                if any_responses:
                    break

            if not any_responses:
                recipients = sorted(set(get_ensemble_admin_emails(ensemble_id) + get_global_admin_emails()))
                if recipients:
                    subject = f"No attendance responses for {ensemble_name} — follow-up"
                    body = (
                        f"<p>Hello,</p>"
                        f"<p>The schedule '{html.escape(s.get('name') or sid)}' was published one week ago but no attendance responses have been recorded.</p>"
                        f"<p>Please consider prompting members to confirm attendance.</p>"
                        f"<p>Open schedule: <a href='{url_for('admin_edit_schedule', schedule_id=sid, _external=True)}'>Open schedule</a></p>"
                    )
                    if not dry_run:
                        brevo_send_email(recipients, subject, body, bcc_mode=True)
                        fn["post_publish_week_sent"] = now
                        s["updated_at"] = now
                        save_schedule(s)
                    summary["actions"].append({"schedule": sid, "action": "post_publish_week_sent", "recipients": recipients})

        # 1 week before each rehearsal event: remind non-responders and notify admins
        rehearsals = s.get("rehearsals") or []
        for reh in rehearsals:
            try:
                reh_num = int(reh.get("Rehearsal"))
            except Exception:
                continue
            date_val = reh.get("Date") or reh.get("date")
            if not date_val:
                continue
            try:
                dt = pd.to_datetime(date_val)
                event_ts = int(dt.timestamp())
            except Exception:
                continue

            # Only consider events between now and one week ahead
            if now < (event_ts - 7 * 24 * 3600) or now >= event_ts:
                continue

            pre_sent = fn.setdefault("pre_event_sent", {})
            if pre_sent.get(str(reh_num)):
                continue

            # Find non-responders
            att_bucket = (s.get("attendance") or {}).get(str(reh_num)) or {}
            members = _ensemble_members(ensemble_id)
            nonresponders = []
            for m in members:
                uid = m.get("user_id")
                email = m.get("email")
                if not email:
                    continue
                resp = att_bucket.get(uid)
                if resp and resp.get("status"):
                    continue
                nonresponders.append(m)

            if nonresponders:
                # Send individual reminders to non-responders
                sent_count = 0
                for nr in nonresponders:
                    try:
                        member_email = nr.get("email")
                        member_name = nr.get("name") or member_email
                        lines = [f"- {reh.get('Title') or reh.get('Event Type') or 'Rehearsal'} on {reh.get('Date') or '(date TBC)'} (Rehearsal {reh_num})"]
                        body = (
                            f"<p>Hi {html.escape(member_name)},</p>"
                            f"<p>Please confirm your attendance for the upcoming {html.escape(ensemble_name)} rehearsal:</p>"
                            "<ul>" + "".join(f"<li>{html.escape(l)}</li>" for l in lines) + "</ul>"
                            f"<p>Respond via the schedule: <a href='{url_for('schedule_view', schedule_id=sid, _external=True)}'>view schedule</a>.</p>"
                        )
                        if not dry_run:
                            brevo_send_email([member_email], f"Attendance needed for {ensemble_name}", body, bcc_mode=False)
                        sent_count += 1
                    except Exception as e:
                        print(f"[FOLLOWUPS] Failed to send reminder to {nr.get('email')}: {e}")

                # Notify admins with a summary
                admin_recipients = sorted(set(get_ensemble_admin_emails(ensemble_id) + get_global_admin_emails()))
                if admin_recipients:
                    try:
                        lines = [f"- {nr.get('name') or nr.get('email')}" for nr in nonresponders]
                        admin_body = (
                            f"<p>Admins,</p>"
                            f"<p>The following members have not responded for rehearsal {reh_num} ({html.escape(str(reh.get('Date') or 'TBC'))}):</p>"
                            "<ul>" + "".join(f"<li>{html.escape(l)}</li>" for l in lines) + "</ul>"
                            f"<p>Open schedule: <a href='{url_for('admin_edit_schedule', schedule_id=sid, _external=True)}'>Open schedule</a></p>"
                        )
                        if not dry_run:
                            brevo_send_email(admin_recipients, f"Non-responders for rehearsal {reh_num} — {ensemble_name}", admin_body, bcc_mode=True)
                    except Exception as e:
                        print(f"[FOLLOWUPS] Failed to notify admins for {sid} rehearsal {reh_num}: {e}")

                # Mark as sent for this rehearsal
                pre_sent[str(reh_num)] = now
                s["updated_at"] = now
                save_schedule(s)
                summary["actions"].append({"schedule": sid, "action": "pre_event_reminders", "rehearsal": reh_num, "sent_to": sent_count})

    return summary


@app.post('/api/cron/run_followups')
def api_run_followups():
    r = admin_required_or_403()
    if r: return r
    try:
        summary = run_followup_reminders(dry_run=False)
        return jsonify({"ok": True, "summary": summary})
    except Exception as e:
        print(f"[CRON] Exception during followups run: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _followups_worker_loop():
    # Run once on startup, then sleep and run daily.
    while True:
        try:
            print("[FOLLOWUPS] Running scheduled follow-up reminders")
            run_followup_reminders()
        except Exception as e:
            print(f"[FOLLOWUPS] Error in scheduled run: {e}")
        # Sleep for 24 hours
        time.sleep(24 * 3600)


# Start background thread for scheduled follow-ups
try:
    t = threading.Thread(target=_followups_worker_loop, daemon=True)
    t.start()
except Exception:
    print("[FOLLOWUPS] Failed to start followups background thread")
if __name__ == "__main__":
    # For local dev:
    #   EDIT_TOKEN=... flask --app app run --debug
    # Note: use_reloader=False to avoid watchdog issues watching too many site-packages files
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True, use_reloader=False)
