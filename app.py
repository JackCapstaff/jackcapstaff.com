import importlib.util
import json
import csv
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import parse_qs, urljoin, urlparse
import uuid

import requests

from flask import Flask, flash, redirect, render_template, request, session, url_for, abort, send_from_directory
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from sqlalchemy import inspect, text

try:
    import cloudinary
    import cloudinary.uploader
except Exception:
    cloudinary = None

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def resolve_database_url():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        return database_url.replace('postgres://', 'postgresql://', 1)

    if os.environ.get('DYNO'):
        raise RuntimeError(
            'A persistent DATABASE_URL is required in Heroku production. '
            'Attach Heroku Postgres or set DATABASE_URL.'
        )

    return 'sqlite:///' + os.path.join(BASE_DIR, 'jackcapstaff.db')


app = Flask(__name__, template_folder='templates', static_folder='assets', static_url_path='/assets')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = resolve_database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Cache configuration (15 minutes for Outlook calendar data)
app.config['CACHE_TYPE'] = 'simple'
app.config['CACHE_DEFAULT_TIMEOUT'] = 900  # 15 minutes
cache = Cache(app)

app.config['SMTP_HOST'] = os.environ.get('SMTP_HOST', '').strip()
app.config['SMTP_PORT'] = int(os.environ.get('SMTP_PORT', '587'))
app.config['SMTP_USERNAME'] = os.environ.get('SMTP_USERNAME', '').strip()
app.config['SMTP_PASSWORD'] = os.environ.get('SMTP_PASSWORD', '').strip()
app.config['SMTP_USE_TLS'] = os.environ.get('SMTP_USE_TLS', '1').strip() not in {'0', 'false', 'False'}
app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY', '').strip()
app.config['BREVO_FROM_EMAIL'] = os.environ.get('BREVO_FROM_EMAIL', '').strip()
app.config['BREVO_FROM_NAME'] = os.environ.get('BREVO_FROM_NAME', app.config['SITE_TITLE'] if 'SITE_TITLE' in app.config else 'Jack Capstaff').strip()
app.config['BREVO_PRIMARY_TO'] = os.environ.get('BREVO_PRIMARY_TO', '').strip()
app.config['CONTACT_TO_EMAIL'] = os.environ.get('CONTACT_TO_EMAIL', app.config.get('BREVO_PRIMARY_TO', '').strip() or 'jack@jackcapstaff.com').strip()
app.config['CONTACT_FROM_EMAIL'] = os.environ.get('CONTACT_FROM_EMAIL', app.config['SMTP_USERNAME'] or 'noreply@jackcapstaff.com').strip()
app.config['SITE_TITLE'] = os.environ.get('SITE_TITLE', 'Jack Capstaff')

REHEARSAL_SCHEDULE_ROOT = os.path.join(BASE_DIR, 'Rehearsal Schedule', 'rehearsal_schedule')
REHEARSAL_SCHEDULE_DATA_DIR = os.path.join(REHEARSAL_SCHEDULE_ROOT, 'site', 'data', 'schedules')
REHEARSAL_SCHEDULE_PREFIX = '/rehearsal-schedule'
IMAGES_DIR = os.path.join(BASE_DIR, 'images')
UPLOADS_DIR = os.path.join(BASE_DIR, 'assets', 'uploads')
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
os.makedirs(UPLOADS_DIR, exist_ok=True)


def load_rehearsal_schedule_app():
    app_path = os.path.join(REHEARSAL_SCHEDULE_ROOT, 'app.py')
    if not os.path.exists(app_path):
        return None

    spec = importlib.util.spec_from_file_location('jackcapstaff_rehearsal_schedule_app', app_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    current_dir = os.getcwd()
    try:
        os.chdir(REHEARSAL_SCHEDULE_ROOT)
        spec.loader.exec_module(module)
    finally:
        os.chdir(current_dir)
    return getattr(module, 'app', None)


def resolve_default_schedule_id():
    if not os.path.isdir(REHEARSAL_SCHEDULE_DATA_DIR):
        return None

    published_candidates = []
    fallback_candidates = []

    for filename in os.listdir(REHEARSAL_SCHEDULE_DATA_DIR):
        if not filename.endswith('.json'):
            continue

        file_path = os.path.join(REHEARSAL_SCHEDULE_DATA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as json_file:
                schedule_data = json.load(json_file)
        except Exception:
            continue

        schedule_id = schedule_data.get('id') or filename[:-5]
        score = (
            int(schedule_data.get('published_at') or 0),
            int(schedule_data.get('updated_at') or 0),
            os.path.getmtime(file_path),
            schedule_id,
        )
        fallback_candidates.append((score, schedule_id))
        if schedule_data.get('status') == 'published':
            published_candidates.append((score, schedule_id))

    candidates = published_candidates or fallback_candidates
    if not candidates:
        return None

    return max(candidates, key=lambda item: item[0])[1]


def resolve_rehearsal_schedule_url():
    schedule_id = resolve_default_schedule_id()
    if not schedule_id:
        return None

    return f'{REHEARSAL_SCHEDULE_PREFIX}/s/{schedule_id}'


db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize models with database instance
from models import init_models
models_dict = init_models(db)
User = models_dict['User']
NewsItem = models_dict['NewsItem']
Event = models_dict['Event']
PageContent = models_dict['PageContent']
ContactMessage = models_dict['ContactMessage']
Testimonial = models_dict['Testimonial']
# Expose models and db to app context for access in blueprints
app.db = db
app.User = User
app.NewsItem = NewsItem
app.Event = Event
app.PageContent = PageContent
app.ContactMessage = ContactMessage
app.Testimonial = Testimonial


# Import and register admin blueprint
from admin import admin_bp
app.register_blueprint(admin_bp)

rehearsal_schedule_app = load_rehearsal_schedule_app()
if rehearsal_schedule_app is not None:
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
        REHEARSAL_SCHEDULE_PREFIX: rehearsal_schedule_app,
    })


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


@app.context_processor
def inject_globals():
    return {
        'site_title': app.config['SITE_TITLE'],
        'current_year': datetime.utcnow().year,
        'media_url': normalize_media_url,
        'normalize_youtube_embed_url': normalize_youtube_embed_url,
    }


def normalize_media_url(value):
    value = (value or '').strip()
    if not value:
        return value

    lowered = value.lower()
    if lowered.startswith(('http://', 'https://', '//', 'data:', '/assets/', '/images/')):
        return value

    if value.startswith('assets/'):
        return f'/{value}'

    if value.startswith('images/'):
        return f'/{value}'

    if value.startswith('/'):
        return value

    return f'/images/{value.lstrip("/")}'


def normalize_youtube_embed_url(raw_url):
    url = (raw_url or '').strip()
    if not url:
        return None

    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        url = f'https://{url}'

    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]

    video_id = None
    if host in {'youtube.com', 'm.youtube.com'}:
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [None])[0]
        elif parsed.path.startswith('/shorts/'):
            video_id = parsed.path.split('/shorts/', 1)[1].split('/', 1)[0]
        elif parsed.path.startswith('/embed/'):
            video_id = parsed.path.split('/embed/', 1)[1].split('/', 1)[0]
    elif host == 'youtu.be':
        video_id = parsed.path.lstrip('/').split('/', 1)[0]

    if not video_id or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return None

    return f'https://www.youtube.com/embed/{video_id}'


def ensure_optional_columns():
    """Apply lightweight schema upgrades for legacy databases."""
    required_columns = {
        'page_content': {
            'youtube_embed_url': 'VARCHAR(512)',
        },
    }

    for table_name, columns in required_columns.items():
        try:
            with db.engine.connect() as conn:
                existing_columns = {column['name'] for column in inspect(conn).get_columns(table_name)}
        except Exception:
            continue

        missing_columns = [column_name for column_name in columns if column_name not in existing_columns]
        if not missing_columns:
            continue

        with db.engine.begin() as conn:
            for column_name in missing_columns:
                conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {columns[column_name]}'))


def _allowed_image_file(filename):
    return bool(filename) and '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def _is_cloudinary_configured():
    return cloudinary is not None and bool(os.environ.get('CLOUDINARY_URL', '').strip())


def upload_media_image(file_storage, prefix='content'):
    if not file_storage or not getattr(file_storage, 'filename', None):
        return None

    if not _allowed_image_file(file_storage.filename):
        return None

    if _is_cloudinary_configured():
        try:
            result = cloudinary.uploader.upload(
                file_storage,
                folder=f'jackcapstaff/{prefix}',
                resource_type='image',
            )
            return result.get('secure_url')
        except Exception:
            app.logger.exception('Cloudinary upload failed; falling back to local uploads')

    safe_name = secure_filename(file_storage.filename)
    unique_name = f"{prefix}_{uuid.uuid4().hex}_{safe_name}"
    out_path = os.path.join(UPLOADS_DIR, unique_name)
    file_storage.save(out_path)
    return f'/assets/uploads/{unique_name}'


app.upload_media_image = upload_media_image


def _is_safe_redirect_target(target):
    if not target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def _send_contact_email_via_brevo(name, email, message, send_copy=False):
    api_key = app.config.get('BREVO_API_KEY', '').strip()
    to_email = app.config.get('CONTACT_TO_EMAIL', '').strip()
    from_email = app.config.get('BREVO_FROM_EMAIL', '').strip() or app.config.get('CONTACT_FROM_EMAIL', '').strip()
    from_name = app.config.get('BREVO_FROM_NAME', '').strip() or app.config.get('SITE_TITLE', 'Jack Capstaff')

    if not api_key or not to_email or not from_email:
        return False, 'Brevo settings are incomplete.'

    subject = 'New message from jackcapstaff.com'
    html_body = (
        '<p><strong>You have received a new message from jackcapstaff.com</strong></p>'
        f'<p><strong>Name:</strong> {name}</p>'
        f'<p><strong>Email:</strong> {email}</p>'
        f'<p><strong>Message:</strong><br>{message.replace(chr(10), "<br>")}</p>'
    )
    text_body = (
        'You have received a new message from jackcapstaff.com\n\n'
        f'Name: {name}\n'
        f'Email: {email}\n\n'
        f'Message:\n{message}\n'
    )

    payload = {
        'sender': {'name': from_name, 'email': from_email},
        'to': [{'email': to_email}],
        'subject': subject,
        'htmlContent': html_body,
        'textContent': text_body,
        'replyTo': {'email': email},
    }
    if send_copy and email:
        payload['cc'] = [{'email': email}]

    try:
        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'api-key': api_key,
                'content-type': 'application/json',
            },
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return False, f'Brevo API error: {response.status_code} {response.text[:250]}'
    except Exception as exc:
        app.logger.exception('Brevo send failed')
        return False, str(exc)

    return True, 'sent'


def send_contact_email(name, email, message, send_copy=False):
    if app.config.get('BREVO_API_KEY', '').strip():
        sent, detail = _send_contact_email_via_brevo(name, email, message, send_copy=send_copy)
        if sent:
            return True, detail

    to_email = app.config['CONTACT_TO_EMAIL']
    from_email = app.config['CONTACT_FROM_EMAIL']
    smtp_host = app.config['SMTP_HOST']
    smtp_username = app.config['SMTP_USERNAME']
    smtp_password = app.config['SMTP_PASSWORD']

    if not smtp_host or not to_email or not from_email:
        return False, 'Email settings are not configured.'

    subject = 'New message from jackcapstaff.com'
    text_body = (
        'You have received a new message from jackcapstaff.com\n\n'
        f'Name: {name}\n'
        f'Email: {email}\n\n'
        f'Message:\n{message}\n'
    )
    html_body = (
        '<p><strong>You have received a new message from jackcapstaff.com</strong></p>'
        f'<p><strong>Name:</strong> {name}</p>'
        f'<p><strong>Email:</strong> {email}</p>'
        f'<p><strong>Message:</strong><br>{message.replace(chr(10), "<br>")}</p>'
    )

    mail = EmailMessage()
    mail['Subject'] = subject
    mail['From'] = from_email
    mail['To'] = to_email
    mail['Reply-To'] = email
    mail.set_content(text_body)
    mail.add_alternative(html_body, subtype='html')

    recipients = [to_email]
    if send_copy and email:
        recipients.append(email)

    try:
        if app.config['SMTP_USE_TLS']:
            with smtplib.SMTP(smtp_host, app.config['SMTP_PORT']) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(mail, from_addr=from_email, to_addrs=recipients)
        else:
            with smtplib.SMTP(smtp_host, app.config['SMTP_PORT']) as server:
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(mail, from_addr=from_email, to_addrs=recipients)
    except Exception as exc:
        app.logger.exception('Contact email send failed')
        return False, str(exc)

    return True, 'sent'


def _send_password_reset_email_via_brevo(user_email, reset_url):
    """Send password reset email via Brevo"""
    api_key = app.config.get('BREVO_API_KEY', '').strip()
    from_email = app.config.get('BREVO_FROM_EMAIL', '').strip()
    from_name = app.config.get('BREVO_FROM_NAME', '').strip() or app.config.get('SITE_TITLE', 'Jack Capstaff')

    if not api_key or not from_email:
        return False, 'Brevo settings are incomplete.'

    subject = 'Password Reset Request'
    html_body = (
        '<p>You requested a password reset for your Jack Capstaff account.</p>'
        f'<p><a href="{reset_url}">Click here to reset your password</a></p>'
        '<p>This link will expire in 1 hour.</p>'
        '<p>If you did not request this, please ignore this email.</p>'
    )
    text_body = (
        'You requested a password reset for your Jack Capstaff account.\n\n'
        f'Reset link: {reset_url}\n\n'
        'This link will expire in 1 hour.\n'
        'If you did not request this, please ignore this email.\n'
    )

    payload = {
        'sender': {'name': from_name, 'email': from_email},
        'to': [{'email': user_email}],
        'subject': subject,
        'htmlContent': html_body,
        'textContent': text_body,
    }

    try:
        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'api-key': api_key,
                'content-type': 'application/json',
            },
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return False, f'Brevo API error: {response.status_code}'
    except Exception:
        app.logger.exception('Brevo password reset email send failed')
        return False, 'Failed to send email'

    return True, 'sent'


def send_password_reset_email(user_email, reset_url):
    """Send password reset email using Brevo or SMTP fallback"""
    if app.config.get('BREVO_API_KEY', '').strip():
        sent, detail = _send_password_reset_email_via_brevo(user_email, reset_url)
        if sent:
            return True, detail

    from_email = app.config['CONTACT_FROM_EMAIL']
    smtp_host = app.config['SMTP_HOST']
    smtp_username = app.config['SMTP_USERNAME']
    smtp_password = app.config['SMTP_PASSWORD']

    if not smtp_host or not from_email:
        return False, 'Email settings are not configured.'

    subject = 'Password Reset Request'
    text_body = (
        'You requested a password reset for your Jack Capstaff account.\n\n'
        f'Reset link: {reset_url}\n\n'
        'This link will expire in 1 hour.\n'
        'If you did not request this, please ignore this email.\n'
    )
    html_body = (
        '<p>You requested a password reset for your Jack Capstaff account.</p>'
        f'<p><a href="{reset_url}">Click here to reset your password</a></p>'
        '<p>This link will expire in 1 hour.</p>'
        '<p>If you did not request this, please ignore this email.</p>'
    )

    mail = EmailMessage()
    mail['Subject'] = subject
    mail['From'] = from_email
    mail['To'] = user_email
    mail.set_content(text_body)
    mail.add_alternative(html_body, subtype='html')

    try:
        if app.config['SMTP_USE_TLS']:
            with smtplib.SMTP(smtp_host, app.config['SMTP_PORT']) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(mail, from_addr=from_email, to_addrs=[user_email])
        else:
            with smtplib.SMTP(smtp_host, app.config['SMTP_PORT']) as server:
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(mail, from_addr=from_email, to_addrs=[user_email])
    except Exception:
        app.logger.exception('Password reset email send failed')
        return False, 'Failed to send email'

    return True, 'sent'


def _outlook_calendar_config():
    tenant_id = (os.environ.get('OUTLOOK_GRAPH_TENANT_ID') or '').strip()
    client_id = (os.environ.get('OUTLOOK_GRAPH_CLIENT_ID') or '').strip()
    client_secret = (os.environ.get('OUTLOOK_GRAPH_CLIENT_SECRET') or '').strip()
    refresh_token = (os.environ.get('OUTLOOK_GRAPH_REFRESH_TOKEN') or '').strip()
    user_principal_name = (os.environ.get('OUTLOOK_GRAPH_USER_PRINCIPAL_NAME') or '').strip()
    calendar_id = (os.environ.get('OUTLOOK_GRAPH_CALENDAR_ID') or '').strip()
    timezone = (os.environ.get('OUTLOOK_GRAPH_TIMEZONE') or 'UTC').strip() or 'UTC'

    if not (tenant_id and client_id and client_secret and refresh_token and user_principal_name):
        return None

    return {
        'tenant_id': tenant_id,
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'user_principal_name': user_principal_name,
        'calendar_id': calendar_id,
        'timezone': timezone,
    }


def _graph_access_token(config: dict) -> str | None:
    token_url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    payload = {
        'client_id': config['client_id'],
        'client_secret': config['client_secret'],
        'grant_type': 'refresh_token',
        'refresh_token': config['refresh_token'],
        'scope': 'https://graph.microsoft.com/Calendars.Read.Shared offline_access',
    }

    try:
        response = requests.post(token_url, data=payload, timeout=20)
        response.raise_for_status()
        token_data = response.json()
        return token_data.get('access_token')
    except Exception:
        app.logger.exception('Microsoft Graph token request failed')
        return None


def _graph_calendar_events(config: dict) -> list[dict]:
    access_token = _graph_access_token(config)
    if not access_token:
        return []

    start_dt = datetime.utcnow().replace(microsecond=0)
    end_dt = start_dt + timedelta(days=365)
    base_user = config['user_principal_name']
    calendar_id = config.get('calendar_id') or ''

    if calendar_id:
        endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendars/{calendar_id}/calendarView"
    else:
        endpoint = f"https://graph.microsoft.com/v1.0/users/{base_user}/calendarView"

    params = {
        'startDateTime': start_dt.isoformat() + 'Z',
        'endDateTime': end_dt.isoformat() + 'Z',
        '$select': 'subject,start,end,location,bodyPreview,sensitivity,isAllDay,isCancelled,showAs,webLink,isOnlineMeeting,onlineMeetingUrl',
        '$orderby': 'start/dateTime',
        '$top': '200',
    }
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'Prefer': f'outlook.timezone="{config["timezone"]}"',
    }

    events = []
    next_url = endpoint

    try:
        while next_url:
            response = requests.get(next_url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            for row in payload.get('value', []):
                if row.get('isCancelled'):
                    continue

                start_info = row.get('start') or {}
                end_info = row.get('end') or {}
                start_raw = (start_info.get('dateTime') or '').strip()
                end_raw = (end_info.get('dateTime') or '').strip()

                try:
                    if start_raw:
                        parsed_dt = datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
                        if parsed_dt.tzinfo is not None:
                            parsed_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
                        start_dt_value = parsed_dt
                    else:
                        start_dt_value = datetime.min
                except ValueError:
                    start_dt_value = datetime.min

                sensitivity = (row.get('sensitivity') or '').strip().lower()
                is_private = sensitivity == 'private'
                subject = (row.get('subject') or '').strip()
                location = ((row.get('location') or {}).get('displayName') or '').strip()
                description = (row.get('bodyPreview') or '').strip()

                if is_private:
                    subject = 'Private appointment'
                    location = ''
                    description = ''

                events.append({
                    'subject': subject or 'Untitled event',
                    'start_dt': start_dt_value,
                    'date_label': start_dt_value.strftime('%d %b %Y') if start_dt_value != datetime.min else '',
                    'time_label': start_dt_value.strftime('%H:%M') if start_dt_value != datetime.min else '',
                    'location': location,
                    'description': description,
                    'is_private': is_private,
                    'is_all_day': bool(row.get('isAllDay')),
                    'end_label': end_raw,
                })

            next_url = payload.get('@odata.nextLink')
            params = None
    except Exception:
        app.logger.exception('Microsoft Graph calendar fetch failed')
        return []

    events.sort(key=lambda item: item['start_dt'])
    return events


@cache.cached(timeout=900, key_prefix='schedule_events')
def load_schedule_events():
    graph_config = _outlook_calendar_config()
    if graph_config:
        graph_events = _graph_calendar_events(graph_config)
        if graph_events:
            return graph_events

    csv_path = os.path.join(BASE_DIR, 'events.csv')
    events = []

    if not os.path.exists(csv_path):
        return events

    with open(csv_path, newline='', encoding='utf-8-sig') as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            is_private = (row.get('Private') or '').strip().upper() == 'TRUE'
            raw_subject = (row.get('Subject') or '').strip()
            subject = 'Private appointment' if is_private else raw_subject
            location = '' if is_private else (row.get('Location') or '').strip()
            description = '' if is_private else (row.get('Description') or '').strip()

            start_date = (row.get('Start Date') or '').strip()
            start_time = (row.get('Start Time') or '').strip()
            try:
                start_dt = datetime.strptime(f'{start_date} {start_time}', '%m/%d/%Y %H:%M:%S')
            except ValueError:
                try:
                    start_dt = datetime.strptime(start_date, '%m/%d/%Y')
                except ValueError:
                    start_dt = datetime.min

            events.append({
                'subject': subject or 'Untitled event',
                'start_dt': start_dt,
                'date_label': start_dt.strftime('%d %b %Y') if start_dt != datetime.min else start_date,
                'time_label': start_dt.strftime('%H:%M') if start_dt != datetime.min and start_time else '',
                'location': location,
                'description': description,
                'is_private': is_private,
            })

    events.sort(key=lambda item: item['start_dt'])
    return events


@app.route('/')
@app.route('/index.html')
def index():
    home_content = PageContent.query.filter_by(page='home', published=True).order_by(PageContent.order).all()
    upcoming_events = Event.query.filter_by(published=True).filter(
        Event.event_date >= datetime.utcnow()
    ).order_by(Event.event_date).limit(3).all()
    recent_news = NewsItem.query.filter_by(published=True).order_by(NewsItem.published_at.desc()).limit(1).all()
    
    # Testimonials carousel
    testimonials = Testimonial.query.filter_by(published=True).order_by(Testimonial.order).all()
    
    # Hero video background (optional) - set via HERO_VIDEO_URL environment variable
    hero_video_url = os.environ.get('HERO_VIDEO_URL', '').strip() or None
    
    return render_template('index.html', 
                         home_content=home_content,
                         upcoming_events=upcoming_events,
                         recent_news=recent_news,
                         testimonials=testimonials,
                         hero_video_url=hero_video_url)


@app.route('/Biography')
@app.route('/Biography.html')
def biography():
    biography_content = PageContent.query.filter_by(page='biography', published=True).order_by(PageContent.order).all()
    return render_template('Biography.html', biography_content=biography_content)



@app.route('/Schedule')
@app.route('/Schedule.html')
def schedule():
    public_calendar_url = f'{REHEARSAL_SCHEDULE_PREFIX}/my'
    upcoming_events = load_schedule_events()

    if rehearsal_schedule_app is not None:
        return redirect(public_calendar_url)

    return render_template('Schedule.html', 
                         upcoming_events=upcoming_events,
                         rehearsal_schedule_url=public_calendar_url)


@app.route('/Media')
@app.route('/Media.html')
def media():
    media_content = PageContent.query.filter_by(page='media', published=True).order_by(PageContent.order).all()
    return render_template('Media.html', media_content=media_content)


@app.route('/News')
@app.route('/News.html')
def news():
    page = request.args.get('page', 1, type=int)
    news_items = NewsItem.query.filter_by(published=True).order_by(
        NewsItem.published_at.desc()
    ).paginate(page=page, per_page=10)
    return render_template('News.html', news_items=news_items)


@app.route('/news/<slug>')
def news_detail(slug):
    news_item = NewsItem.query.filter_by(slug=slug).first_or_404()
    return render_template('news_detail.html', news_item=news_item)


@app.route('/contact', methods=['GET', 'POST'])
@app.route('/Contact')
@app.route('/Contact.html', methods=['GET', 'POST'])
def contact():
    def _prepare_contact_form_context():
        token = uuid.uuid4().hex
        rendered_at = int(datetime.now(timezone.utc).timestamp())
        session['contact_form_token'] = token
        session['contact_form_rendered_at'] = rendered_at
        return {
            'contact_form_token': token,
            'contact_rendered_at': rendered_at,
        }

    if request.method == 'POST':
        name = request.form.get('demo-name', '').strip()
        email = request.form.get('demo-email', '').strip()
        message = request.form.get('demo-message', '').strip()
        send_copy = request.form.get('demo-copy') == 'on'

        # Lightweight bot checks: hidden honeypot + timing/token validation.
        website = request.form.get('website', '').strip()
        posted_token = request.form.get('contact_form_token', '').strip()
        posted_rendered_at_raw = request.form.get('contact_rendered_at', '').strip()
        session_token = session.get('contact_form_token', '')
        session_rendered_at = int(session.get('contact_form_rendered_at') or 0)

        try:
            posted_rendered_at = int(posted_rendered_at_raw)
        except (TypeError, ValueError):
            posted_rendered_at = 0

        now_ts = int(datetime.now(timezone.utc).timestamp())
        form_age = now_ts - posted_rendered_at if posted_rendered_at else -1

        invalid_bot_submission = (
            bool(website)
            or not posted_token
            or posted_token != session_token
            or posted_rendered_at <= 0
            or posted_rendered_at != session_rendered_at
            or form_age < 3
            or form_age > 60 * 60 * 4
        )

        if invalid_bot_submission:
            app.logger.warning('Blocked suspected bot contact submission from %s', request.remote_addr)
            flash('Your message has been sent successfully.', 'success')
            return redirect(url_for('contact'))

        if not name or not email or not message:
            flash('Please complete the contact form.', 'warning')
            return render_template('Contact.html', **_prepare_contact_form_context())

        db.session.add(ContactMessage(name=name, email=email, message=message))
        db.session.commit()

        sent, detail = send_contact_email(name, email, message, send_copy=send_copy)
        if sent:
            flash('Your message has been sent successfully.', 'success')
        else:
            flash(f'Your message was saved, but email could not be sent: {detail}', 'warning')
        return redirect(url_for('contact'))

    return render_template('Contact.html', **_prepare_contact_form_context())


@app.route('/images/<path:filename>')
def legacy_images(filename):
    return send_from_directory(IMAGES_DIR, filename)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    next_page = request.args.get('next', '').strip()

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')
        next_page = request.form.get('next', '').strip() or next_page

        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully.', 'success')
            if _is_safe_redirect_target(next_page):
                return redirect(next_page)
            return redirect(url_for('index'))

        flash('Invalid username, email, or password.', 'danger')

    return render_template('login.html', next_page=next_page)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        if not username or not email or not password:
            flash('Please complete all registration fields.', 'warning')
            return render_template('register.html')

        if password != password_confirm:
            flash('Passwords do not match.', 'warning')
            return render_template('register.html')

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('That username or email already exists.', 'warning')
            return render_template('register.html')

        user = User(username=username, email=email, name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash('Registration complete. You are now logged in.', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        user = User.query.filter_by(email=email).first()
        if user:
            user.generate_reset_token()
            db.session.commit()
            
            reset_url = url_for('reset_password', token=user.reset_token, _external=True)
            sent, detail = send_password_reset_email(user.email, reset_url)
            
            if sent:
                flash('Check your email for password reset instructions.', 'success')
            else:
                flash('Password reset email could not be sent. Please try again.', 'warning')
        else:
            # Don't reveal whether email exists
            flash('Check your email for password reset instructions.', 'success')
        
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.verify_reset_token(token):
        flash('Invalid or expired reset link.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        if not password:
            flash('Password is required.', 'warning')
            return render_template('reset_password.html', token=token)

        if password != password_confirm:
            flash('Passwords do not match.', 'warning')
            return render_template('reset_password.html', token=token)

        user.set_password(password)
        user.clear_reset_token()
        db.session.commit()
        
        flash('Your password has been reset. You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)


# ============================================================================
# TESTIMONIALS ADMIN
# ============================================================================

@app.route('/admin/testimonials')
@login_required
def admin_testimonials():
    """List all testimonials"""
    if current_user.role not in ['admin', 'editor']:
        abort(403)
    
    page = request.args.get('page', 1, type=int)
    testimonials = Testimonial.query.order_by(Testimonial.order).paginate(page=page, per_page=20)
    return render_template('admin_testimonials.html', testimonials=testimonials)


@app.route('/admin/testimonials/add', methods=['GET', 'POST'])
@login_required
def admin_testimonial_add():
    """Add a new testimonial"""
    if current_user.role not in ['admin', 'editor']:
        abort(403)
    
    if request.method == 'POST':
        author = request.form.get('author', '').strip()
        role = request.form.get('role', '').strip()
        quote = request.form.get('quote', '').strip()
        organisation = request.form.get('organisation', '').strip()
        published = request.form.get('published') == 'on'
        
        if not author or not quote:
            flash('Author and quote are required.', 'warning')
            return render_template('admin_testimonial_form.html', action='Add')
        
        testimonial = Testimonial(
            author=author,
            role=role or None,
            quote=quote,
            organisation=organisation or None,
            published=published,
            author_id=current_user.id,
            order=Testimonial.query.count()
        )
        
        db.session.add(testimonial)
        db.session.commit()
        
        flash('Testimonial added successfully.', 'success')
        return redirect(url_for('admin_testimonials'))
    
    return render_template('admin_testimonial_form.html', action='Add')


@app.route('/admin/testimonials/<int:testimonial_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_testimonial_edit(testimonial_id):
    """Edit a testimonial"""
    if current_user.role not in ['admin', 'editor']:
        abort(403)
    
    testimonial = Testimonial.query.get_or_404(testimonial_id)
    
    if request.method == 'POST':
        testimonial.author = request.form.get('author', '').strip()
        testimonial.role = request.form.get('role', '').strip() or None
        testimonial.quote = request.form.get('quote', '').strip()
        testimonial.organisation = request.form.get('organisation', '').strip() or None
        testimonial.published = request.form.get('published') == 'on'
        
        if not testimonial.author or not testimonial.quote:
            flash('Author and quote are required.', 'warning')
            return render_template('admin_testimonial_form.html', testimonial=testimonial, action='Edit')
        
        db.session.commit()
        flash('Testimonial updated successfully.', 'success')
        return redirect(url_for('admin_testimonials'))
    
    return render_template('admin_testimonial_form.html', testimonial=testimonial, action='Edit')


@app.route('/admin/testimonials/<int:testimonial_id>/delete', methods=['POST'])
@login_required
def admin_testimonial_delete(testimonial_id):
    """Delete a testimonial"""
    if current_user.role not in ['admin', 'editor']:
        abort(403)
    
    testimonial = Testimonial.query.get_or_404(testimonial_id)
    db.session.delete(testimonial)
    db.session.commit()
    
    flash('Testimonial deleted successfully.', 'success')
    return redirect(url_for('admin_testimonials'))


with app.app_context():
    db.create_all()
    ensure_optional_columns()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', '0') == '1')
