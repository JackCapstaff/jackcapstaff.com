from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, has_request_context, Response, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from functools import wraps, lru_cache
from math import radians, sin, cos, sqrt, atan2
import html
import csv
import os
import re
import secrets
from io import StringIO
from html import unescape
import requests  # For Facebook API integration
from urllib.parse import urlparse, parse_qs
import bleach
from bleach.css_sanitizer import CSSSanitizer
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text, inspect, or_
from zoneinfo import ZoneInfo

LONDON_TZ = ZoneInfo('Europe/London')
UTC_TZ = ZoneInfo('UTC')
from sqlalchemy.exc import ProgrammingError
import threading

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here-change-in-production')
app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY')
app.config['BREVO_FROM_EMAIL'] = os.environ.get('BREVO_FROM_EMAIL', 'noreply@brassingaround.co.uk')
app.config['BREVO_FROM_NAME'] = os.environ.get('BREVO_FROM_NAME', 'Brassing Around')
app.config['BREVO_PRIMARY_TO'] = os.environ.get('BREVO_PRIMARY_TO', 'aroundbrassing@gmail.com')
app.config['PASSWORD_RESET_TOKEN_MAX_AGE'] = int(os.environ.get('PASSWORD_RESET_TOKEN_MAX_AGE', '3600'))

COOKIE_CONSENT_NAME = 'ba_cookie_consent'
COOKIE_CONSENT_ACCEPT = 'accept'
COOKIE_CONSENT_REJECT = 'reject'
COOKIE_CONSENT_MAX_AGE = 60 * 60 * 24 * 180

def resolve_database_url():
    primary_url = os.environ.get('DATABASE_URL', '').strip()
    if primary_url:
        return primary_url

    # Heroku Postgres may exist as a color-named config var if not promoted to DATABASE_URL.
    heroku_postgres_vars = sorted(
        (key for key in os.environ if re.fullmatch(r'HEROKU_POSTGRESQL_[A-Z]+_URL', key)),
    )
    for env_var in heroku_postgres_vars:
        candidate = os.environ.get(env_var, '').strip()
        if candidate:
            app.logger.warning('DATABASE_URL not set; falling back to %s.', env_var)
            return candidate

    return ''

is_heroku_dyno = bool(os.environ.get('DYNO'))
configured_database_url = resolve_database_url()
if is_heroku_dyno and not configured_database_url:
    raise RuntimeError('A persistent Postgres URL is required in Heroku production. Set DATABASE_URL or attach a HEROKU_POSTGRESQL_*_URL config var.')

# Prefer persistent DATABASE_URL when available, otherwise use local SQLite for non-production development.
database_url = configured_database_url or 'sqlite:///brassing_around.db'
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url

configured_schema = os.environ.get('APP_DB_SCHEMA', 'brassing_around').strip()
if configured_schema and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', configured_schema):
    raise ValueError('APP_DB_SCHEMA must be a valid SQL schema identifier')
app.config['APP_DB_SCHEMA'] = configured_schema

# When sharing a Postgres instance across apps, isolate this app's tables.
if database_url.startswith('postgresql://') and app.config['APP_DB_SCHEMA']:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'options': f"-csearch_path={app.config['APP_DB_SCHEMA']},public"}
    }

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_UPLOAD_MB', '25')) * 1024 * 1024  # Default 25MB max upload size

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
REACTION_TYPES = ('like', 'love', 'dislike')
WEEK_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# WSGI middleware to run idempotent startup migrations once per worker process.
class StartupMigrationMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app
        self._lock = threading.Lock()
        self._ran = False

    def __call__(self, environ, start_response):
        if not self._ran:
            with self._lock:
                if not self._ran:
                    try:
                        self.run_migrations()
                    except Exception:
                        app.logger.exception('Startup migration middleware failed')
                    self._ran = True
        return self.wsgi_app(environ, start_response)

    def run_migrations(self):
        # Run migrations inside the Flask application context so SQLAlchemy has access
        # to the configured engines and current_app.
        with app.app_context():
            try:
                engine = db.engine
                inspector = inspect(engine)
            except Exception:
                app.logger.exception('Database engine not available during startup migration')
                return

            dialect = engine.dialect.name.lower()
            app.logger.info('Startup DB migration (middleware) check: dialect=%s', dialect)

            changes = {
                'event': [
                    ('facebook_post_id', 'VARCHAR(100)', 'VARCHAR(100)'),
                ],
                'news_item': [
                    ('facebook_post_id', 'VARCHAR(100)', 'VARCHAR(100)'),
                ],
                'site_settings': [
                    ('auto_share_programmes', 'BOOLEAN', 'BOOLEAN'),
                    ('auto_share_news', 'BOOLEAN', 'BOOLEAN'),
                    ('auto_share_events', 'BOOLEAN', 'BOOLEAN'),
                    ('auto_share_competitions', 'BOOLEAN', 'BOOLEAN'),
                ],
            }

            try:
                existing_tables = inspector.get_table_names()
            except Exception:
                app.logger.exception('Could not retrieve table list for startup migration')
                return

            for table, cols in changes.items():
                if table not in existing_tables:
                    app.logger.info('Startup migration: table %s not present, skipping', table)
                    continue

                try:
                    existing_cols = {c['name'] for c in inspector.get_columns(table)}
                except Exception:
                    app.logger.exception('Could not inspect columns for table %s', table)
                    continue

                for col_name, sqlite_type, pg_type in cols:
                    if col_name in existing_cols:
                        continue
                    sql_type = sqlite_type if dialect == 'sqlite' else pg_type
                    ddl = f'ALTER TABLE "{table}" ADD COLUMN {col_name} {sql_type}'
                    try:
                        with engine.connect() as conn:
                            conn.execute(text(ddl))
                            conn.commit()
                        app.logger.info('Startup migration: added column %s to %s', col_name, table)
                    except Exception:
                        app.logger.exception('Startup migration failed adding %s to %s', col_name, table)
                        # Don't raise; we want the app to continue where possible


# Wrap the WSGI app so migrations run once per worker on first request
app.wsgi_app = StartupMigrationMiddleware(app.wsgi_app)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_cloudinary_configured():
    return cloudinary is not None and bool(os.environ.get('CLOUDINARY_URL'))

def save_uploaded_image(file, prefix):
    if not file or not file.filename or not allowed_file(file.filename):
        return None

    if is_cloudinary_configured():
        try:
            result = cloudinary.uploader.upload(
                file,
                folder=f'brassing_around/{prefix}',
                resource_type='image'
            )
            return result['secure_url']
        except Exception:
            app.logger.exception('Cloudinary upload failed; falling back to local storage.')
            if has_request_context():
                flash('Cloudinary upload failed, so the image was saved locally instead.', 'warning')

    filename = secure_filename(file.filename)
    unique_filename = f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
    return unique_filename

def delete_uploaded_image(filename):
    if not filename:
        return
    if filename.startswith('http://') or filename.startswith('https://'):
        if is_cloudinary_configured() and 'cloudinary.com' in filename:
            try:
                parts = filename.split('/upload/')
                if len(parts) == 2:
                    path_part = re.sub(r'^v\d+/', '', parts[1])
                    public_id = path_part.rsplit('.', 1)[0]
                    cloudinary.uploader.destroy(public_id)
            except Exception:
                app.logger.exception('Cloudinary delete failed for %s', filename)
        return
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)

def sanitize_story_content(raw_content):
    """Allow basic rich text while stripping unsafe HTML/attributes."""
    css_sanitizer = CSSSanitizer(allowed_css_properties=['font-size', 'text-align', 'color'])
    cleaned = bleach.clean(
        raw_content or '',
        tags=[
            'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's',
            'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3',
            'span', 'a'
        ],
        attributes={
            'a': ['href', 'target', 'rel'],
            'span': ['style'],
            'p': ['style'],
            'h1': ['style'],
            'h2': ['style'],
            'h3': ['style'],
        },
        protocols=['http', 'https', 'mailto'],
        strip=True,
        css_sanitizer=css_sanitizer,
    )

    # Preserve line breaks for legacy plain text entries.
    if '<' not in (raw_content or ''):
        cleaned = cleaned.replace('\n', '<br>')

    return cleaned

def richtext_to_facebook_text(raw_content):
    """Convert stored rich HTML content into readable plain text for Facebook."""
    content = raw_content or ''
    if not content.strip():
        return ''

    # Preserve common block-level structure before stripping tags.
    content = re.sub(r'(?i)<br\s*/?>', '\n', content)
    content = re.sub(r'(?i)</p\s*>', '\n\n', content)
    content = re.sub(r'(?i)</div\s*>', '\n', content)
    content = re.sub(r'(?i)</h[1-6]\s*>', '\n\n', content)
    content = re.sub(r'(?i)<li\b[^>]*>', '• ', content)
    content = re.sub(r'(?i)</li\s*>', '\n', content)

    # Remove remaining markup and decode HTML entities.
    plain = bleach.clean(content, tags=[], attributes={}, strip=True)
    plain = unescape(plain)

    # Keep intentional paragraph breaks but avoid excessive blank lines.
    plain = re.sub(r'\n{3,}', '\n\n', plain)
    return plain.strip()

def get_public_base_url():
    configured_url = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if configured_url:
        return configured_url
    if has_request_context() and request.url_root:
        return request.url_root.rstrip('/')
    return 'https://brassingaround.com'

def build_public_url(path):
    return f"{get_public_base_url()}/{path.lstrip('/')}"

def summarize_plain_text(text, max_length=220):
    if len(text) <= max_length:
        return text
    trimmed = text[: max_length - 3].rsplit(' ', 1)[0].strip()
    return f"{trimmed}..." if trimmed else text[: max_length - 3] + '...'

def get_story_public_url(story):
    return build_public_url(url_for('public_story', story_id=story.id))

def get_programme_public_url(entry):
    return build_public_url(url_for('programme_detail', entry_id=entry.id))

def get_event_public_url(event):
    return build_public_url(url_for('view_event', event_id=event.id))

def get_competition_public_url(event):
    return build_public_url(url_for('competition_share', event_id=event.id))

def get_story_preview_image_url(story, event):
    if story.photos:
        return build_public_url(url_for('static', filename='uploads/' + story.photos[0].filename))
    if event.event_photo:
        return build_public_url(url_for('static', filename='uploads/' + event.event_photo))
    return build_public_url(url_for('static', filename='og_logo_centered.png'))

def get_programme_preview_image_url(entry):
    if entry.photo:
        return build_public_url(url_for('static', filename='uploads/' + entry.photo))
    return build_public_url(url_for('static', filename='og_logo_centered.png'))

def get_event_preview_image_url(event):
    if event.event_photo:
        return build_public_url(url_for('static', filename='uploads/' + event.event_photo))
    return build_public_url(url_for('static', filename='og_logo_centered.png'))

def summarize_story_for_preview(story, max_length=220):
    summary = richtext_to_facebook_text(story.content)
    return summarize_plain_text(summary, max_length=max_length)

def summarize_programme_for_preview(entry, max_length=220):
    summary = richtext_to_facebook_text(entry.review)
    return summarize_plain_text(summary, max_length=max_length)

@app.template_filter('preview_text')
def preview_text_filter(raw_content, max_length=160):
    """Render rich text as compact plain preview while preserving paragraph breaks."""
    summary = richtext_to_facebook_text(raw_content)
    return summarize_plain_text(summary, max_length=max_length)

@app.template_filter('image_url')
def image_url_filter(filename):
    if not filename:
        return ''
    if filename.startswith('http://') or filename.startswith('https://'):
        return filename
    return url_for('static', filename=f'uploads/{filename}')

@app.template_filter('og_image_url')
def og_image_url_filter(filename):
    """Return an og:image-optimised URL (1200x630, smart crop) for Cloudinary images."""
    url = image_url_filter(filename)
    if not url:
        return ''
    # Cloudinary URLs contain /upload/ — insert transformation after it
    if '/upload/' in url:
        return url.replace('/upload/', '/upload/c_fill,w_1200,h_630,g_auto/', 1)
    return url

@app.template_filter('youtube_urls')
def youtube_urls_filter(value):
    if not value:
        return []
    return [u.strip() for u in value.splitlines() if u.strip()]

def get_facebook_share_status(item):
    return 'shared' if getattr(item, 'facebook_post_id', None) else 'not_shared'

def get_competition_share_status(event):
    return 'shared' if getattr(event, 'competition_facebook_post_id', None) else 'not_shared'

def get_database_backend_label():
    if database_url.startswith('postgresql://'):
        return 'PostgreSQL'
    if database_url.startswith('sqlite:///'):
        return 'SQLite'
    return 'Custom'

def get_facebook_comments(post_id):
    """Fetch comments from a Facebook post. Returns list of dicts or empty list."""
    if not post_id:
        return []
    settings, error = get_facebook_settings()
    if error:
        return []
    try:
        response = requests.get(
            f"https://graph.facebook.com/v18.0/{post_id}/comments",
            params={
                'fields': 'message,from,created_time',
                'access_token': settings.facebook_access_token,
                'limit': 100,
            },
            timeout=8,
        )
        if response.status_code != 200:
            return []
        data = response.json().get('data', [])
        result = []
        for c in data:
            raw_time = c.get('created_time', '')
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(raw_time, '%Y-%m-%dT%H:%M:%S+%f')
                formatted_time = dt.strftime('%d %b %Y at %H:%M')
            except Exception:
                formatted_time = raw_time[:16].replace('T', ' ') if raw_time else ''
            result.append({
                'author': c.get('from', {}).get('name', 'Someone'),
                'message': c.get('message', ''),
                'created_time': formatted_time,
            })
        return result
    except Exception:
        return []

def refresh_facebook_link_preview(public_url, access_token):
    try:
        requests.post(
            'https://graph.facebook.com',
            data={
                'id': public_url,
                'scrape': 'true',
                'access_token': access_token,
            },
            timeout=10,
        )
    except requests.RequestException:
        app.logger.warning('Could not refresh Facebook link preview for %s', public_url)

def get_facebook_settings(require_auto_share=False, share_type=None):
    settings = SiteSettings.query.first()
    if not settings or not settings.facebook_access_token or not settings.facebook_page_id:
        return None, 'Facebook not configured'
    if require_auto_share:
        # If a specific share_type is requested, check the corresponding flag
        if share_type == 'programme' and not getattr(settings, 'auto_share_programmes', False):
            return None, 'Auto-sharing for programmes disabled'
        if share_type == 'news' and not getattr(settings, 'auto_share_news', False):
            return None, 'Auto-sharing for news disabled'
        # default/legacy: stories
        if share_type is None and not settings.auto_share_stories:
            return None, 'Auto-sharing disabled'
    return settings, None

def publish_to_facebook(message, link_url, existing_post_id=None, image_url=None):
    settings, error = get_facebook_settings()
    if error:
        return False, error, existing_post_id

    try:
        if existing_post_id:
            update_data = {
                'message': f"{message}\n\n{link_url}",
                'access_token': settings.facebook_access_token,
            }
            update_response = requests.post(
                f"https://graph.facebook.com/v18.0/{existing_post_id}",
                data=update_data,
                timeout=10,
            )
            if update_response.status_code == 200:
                refresh_facebook_link_preview(link_url, settings.facebook_access_token)
                return True, 'Facebook post updated', existing_post_id

        # Use /feed with both 'message' and 'link'. The link parameter generates
        # the image preview card from og:image. We also scrape first so the image
        # is ready immediately rather than after a delay.
        refresh_facebook_link_preview(link_url, settings.facebook_access_token)
        full_message = f"{message}\n\n{link_url}"
        create_response = requests.post(
            f"https://graph.facebook.com/v18.0/{settings.facebook_page_id}/feed",
            data={
                'message': full_message,
                'link': link_url,
                'published': 'true',
                'access_token': settings.facebook_access_token,
            },
            timeout=10,
        )
        if create_response.status_code == 200:
            post_id = create_response.json().get('id') or existing_post_id
            return True, 'Shared to Facebook', post_id

        error_msg = create_response.json().get('error', {}).get('message', 'Unknown error')
        return False, f'Facebook API error: {error_msg}', existing_post_id
    except requests.exceptions.RequestException as exc:
        return False, f'Connection error: {str(exc)}', existing_post_id
    except Exception as exc:
        return False, f'Error: {str(exc)}', existing_post_id

def build_competition_display_context(event):
    competition_entries = EventCompetitionEntry.query.filter_by(event_id=event.id).all()
    competition_entries = [entry for entry in competition_entries if entry.band_name and entry.band_name.strip()]

    competition_programme = sorted(
        competition_entries,
        key=lambda entry: (
            entry.programme_order is None,
            entry.programme_order if entry.programme_order is not None else 10**9,
            entry.band_name.lower(),
        )
    )
    competition_draw_public = sorted(
        [entry for entry in competition_entries if entry.draw_order is not None],
        key=lambda entry: (entry.draw_order, entry.band_name.lower())
    )
    competition_results_public = sorted(
        [entry for entry in competition_entries if entry.result is not None],
        key=lambda entry: (
            entry.result if entry.result is not None else 10**9,
            entry.draw_order if entry.draw_order is not None else 10**9,
            entry.band_name.lower(),
        )
    )

    has_draw_data = any(entry.draw_order is not None for entry in competition_entries)
    has_result_data = any(entry.result is not None for entry in competition_entries)
    show_competing_table = bool(competition_programme) and not has_draw_data and not has_result_data
    show_draw_table = bool(competition_draw_public) and has_draw_data and not has_result_data
    show_results_table = bool(competition_results_public) and has_result_data

    stage_label = None
    stage_rows = []
    if show_results_table:
        stage_label = 'Results'
        stage_rows = [
            f"{entry.result}. {entry.band_name} (Programme {entry.programme_order or '-'}, Draw {entry.draw_order or '-'})"
            for entry in competition_results_public
        ]
    elif show_draw_table:
        stage_label = 'Draw order'
        stage_rows = [
            f"Draw {entry.draw_order}: {entry.band_name} (Programme {entry.programme_order or '-'})"
            for entry in competition_draw_public
        ]
    elif show_competing_table:
        stage_label = 'Competing bands'
        stage_rows = [
            f"Programme {entry.programme_order or '-'}: {entry.band_name}"
            for entry in competition_programme
        ]

    return {
        'competition_entries': competition_entries,
        'competition_programme': competition_programme,
        'competition_draw_public': competition_draw_public,
        'competition_results_public': competition_results_public,
        'show_competing_table': show_competing_table,
        'show_draw_table': show_draw_table,
        'show_results_table': show_results_table,
        'competition_stage_label': stage_label,
        'competition_stage_rows': stage_rows,
    }

def summarize_competition_for_preview(event, context, max_length=220):
    if not context['competition_stage_rows']:
        return f'Competition updates for {event.title}.'
    summary = f"{context['competition_stage_label']}: " + '; '.join(context['competition_stage_rows'][:4])
    return summarize_plain_text(summary, max_length=max_length)

def normalize_external_url(raw_url):
    url = (raw_url or '').strip()
    if not url:
        return None

    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        url = f'https://{url}'

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None

    return url

def normalize_youtube_embed_url(raw_url):
    """Convert common YouTube URL formats to a safe embed URL."""
    url = (raw_url or '').strip()
    if not url:
        return None

    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        url = f'https://{url}'

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]

    video_id = None
    if host in ('youtube.com', 'm.youtube.com'):
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

def parse_youtube_embed_from_form(field_name='youtube_url'):
    raw_value = request.form.get(field_name, '')
    lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    embed_urls = []
    had_invalid = False
    for line in lines:
        embed = normalize_youtube_embed_url(line)
        if embed:
            embed_urls.append(embed)
        else:
            had_invalid = True
    if had_invalid:
        flash('Some YouTube URLs were invalid and have been skipped. Supported formats: youtube.com/watch, youtu.be, shorts, or embed links.', 'warning')
    return '\n'.join(embed_urls) if embed_urls else None

def ensure_optional_columns():
    """Add optional columns for legacy DBs when models evolve without migrations.
    Each column is altered in its own connection/transaction so one failure
    never blocks the others from being applied.
    """
    def q(identifier):
        # Quote identifiers so reserved names like "user" are valid on Postgres.
        return '"' + identifier.replace('"', '""') + '"'

    expected_columns = {
        'event': {
            'tickets_info_url': 'TEXT',
            'livestream_url': 'TEXT',
            'youtube_embed_url': 'TEXT',
            'competition_facebook_post_id': 'TEXT',
            'ba_favourites_count': 'INTEGER DEFAULT 6',
            'audience_top_n': 'INTEGER DEFAULT 3',
        },
        'user': {
            'marketing_opt_in': 'BOOLEAN DEFAULT FALSE',
            'postcode': 'VARCHAR(10)',
            'instrument': 'VARCHAR(100)',
        },
        'programme_entry': {
            'tickets_info_url': 'TEXT',
            'livestream_url': 'TEXT',
            'youtube_embed_url': 'TEXT',
            'facebook_post_id': 'TEXT',
        },
        'story': {
            'facebook_post_id': 'TEXT',
            'competition_entry_id': 'INTEGER',
        },
        'profile': {
            'short_bio': 'VARCHAR(500)',
            'card_photo': 'VARCHAR(300)',
        },
        'site_settings': {
            'facebook_page_id': 'VARCHAR(200)',
            'facebook_access_token': 'VARCHAR(500)',
            'auto_share_stories': 'BOOLEAN DEFAULT FALSE',
            'updated_by_id': 'INTEGER',
        },
        'event_competition_entry': {
            'conductor': 'VARCHAR(200)',
        },
        'gallery_photo': {
            'album_id': 'INTEGER',
        },
        'gallery_album': {
            'caption': 'TEXT',
            'display_order': 'INTEGER DEFAULT 0',
            'cover_photo_id': 'INTEGER',
        },
        'band': {
            'default_rehearsal_days': 'VARCHAR(500)',
            'updated_by_id': 'INTEGER',
        },
    }

    # Create any missing whole tables first (needs its own connection scope)
    new_tables = [
        ('gallery_photo', GalleryPhoto),
        ('gallery_album', GalleryAlbum),
        ('news_item', NewsItem),
        ('audience_vote', AudienceVote),
        ('competition_prediction', CompetitionPrediction),
        ('band', Band),
        ('band_member', BandMember),
        ('position_vacant', PositionVacant),
    ]
    for table_name, model_class in new_tables:
        try:
            with db.engine.connect() as conn:
                if not inspect(conn).has_table(table_name):
                    model_class.__table__.create(bind=db.engine, checkfirst=True)
        except Exception:
            app.logger.exception('Could not create table %s', table_name)

    # Add each missing column in its own connection so failures are isolated
    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        app.logger.exception('Could not inspect DB for column migration.')
        return

    for table_name, columns in expected_columns.items():
        if table_name not in existing_tables:
            continue
        try:
            existing_columns = {col['name'] for col in inspect(db.engine).get_columns(table_name)}
        except Exception:
            app.logger.exception('Could not read columns for table %s', table_name)
            continue

        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE {q(table_name)} ADD COLUMN IF NOT EXISTS {q(column_name)} {column_type}'
                    ))
                app.logger.info('Added column %s.%s', table_name, column_name)
            except Exception:
                # Fallback without IF NOT EXISTS (SQLite < 3.37)
                try:
                    with db.engine.begin() as conn:
                        conn.execute(text(
                            f'ALTER TABLE {q(table_name)} ADD COLUMN {q(column_name)} {column_type}'
                        ))
                    app.logger.info('Added column %s.%s (fallback)', table_name, column_name)
                except Exception:
                    app.logger.exception('Could not add column %s.%s', table_name, column_name)

def get_token_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'])

def brevo_send_email(subject, html_content, text_content, recipients, bcc_mode=False):
    """Send transactional email through Brevo API."""
    api_key = app.config.get('BREVO_API_KEY')
    if not api_key:
        app.logger.warning('BREVO_API_KEY missing; skipping email send for subject %s', subject)
        return False

    if not recipients:
        app.logger.warning('No recipients supplied for email subject %s', subject)
        return False

    recipient_list = [email for email in recipients if email]
    if not recipient_list:
        return False

    payload = {
        'sender': {
            'name': app.config['BREVO_FROM_NAME'],
            'email': app.config['BREVO_FROM_EMAIL']
        },
        'subject': subject,
        'htmlContent': html_content,
        'textContent': text_content
    }

    if bcc_mode:
        payload['to'] = [{'email': app.config['BREVO_PRIMARY_TO']}]
        payload['bcc'] = [{'email': email} for email in recipient_list]
    else:
        payload['to'] = [{'email': email} for email in recipient_list]

    try:
        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'api-key': api_key,
                'content-type': 'application/json'
            },
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
        response_body = getattr(getattr(exc, 'response', None), 'text', '')
        app.logger.exception(
            'Brevo email send failed for subject %s (status=%s, response=%s)',
            subject,
            status_code,
            (response_body[:500] if response_body else 'n/a')
        )
        return False

def send_invitation_email(recipient_email, invitation, inviter_username):
    role_name = 'Admin' if invitation.role == 'admin' else 'Contributor'
    register_url = url_for('register', invitation_code=invitation.code, _external=True)
    subject = f'{role_name} invitation to Brassing Around'
    html_content = f"""
    <p>Hello,</p>
    <p>{html.escape(inviter_username)} has invited you to join Brassing Around as a <strong>{role_name}</strong>.</p>
    <p>Your invitation code is:</p>
    <p><strong>{html.escape(invitation.code)}</strong></p>
    <p>This invitation expires on {invitation.expires_at.strftime('%d %b %Y at %H:%M UTC')}.</p>
    <p><a href=\"{register_url}\">Register using this invitation</a></p>
    <p>If the button does not work, use this link: {register_url}</p>
    """
    text_content = (
        f"Hello,\n\n"
        f"{inviter_username} has invited you to join Brassing Around as a {role_name}.\n\n"
        f"Invitation code: {invitation.code}\n"
        f"Expires: {invitation.expires_at.strftime('%d %b %Y at %H:%M UTC')}\n\n"
        f"Register here: {register_url}\n"
    )
    return brevo_send_email(subject, html_content, text_content, [recipient_email])

def send_password_reset_email(user):
    token = get_token_serializer().dumps(user.email, salt='password-reset')
    reset_url = url_for('reset_password', token=token, _external=True)
    html_content = f"""
    <p>Hello {html.escape(user.username)},</p>
    <p>We received a request to reset your Brassing Around password.</p>
    <p><a href=\"{reset_url}\">Reset your password</a></p>
    <p>This link expires in 1 hour.</p>
    <p>If you did not request this, you can ignore this email.</p>
    """
    text_content = (
        f"Hello {user.username},\n\n"
        f"We received a request to reset your Brassing Around password.\n"
        f"Reset your password here: {reset_url}\n\n"
        f"This link expires in 1 hour. If you did not request this, ignore this email.\n"
    )
    return brevo_send_email('Reset your Brassing Around password', html_content, text_content, [user.email])

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if not current_user.has_admin_access():
            flash('You need admin privileges to access this page.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def contributor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if not (current_user.is_contributor or current_user.has_admin_access()):
            flash('You need contributor or admin privileges to access this page.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)  # Deprecated - use role instead
    role = db.Column(db.String(20), default='user')  # 'admin', 'contributor', 'user'
    bio = db.Column(db.Text)  # User biography
    profile_photo = db.Column(db.String(300))  # Profile photo filename
    postcode = db.Column(db.String(10))  # Postcode for filtering positions by distance
    instrument = db.Column(db.String(100))  # Primary instrument played
    marketing_opt_in = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def is_contributor(self):
        return self.role in ['contributor', 'admin']

    def has_admin_access(self):
        # Support both new `role` field and legacy `is_admin` flag
        return self.role == 'admin' or bool(self.is_admin)


# Marketing-related models
class MarketingListEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), nullable=False, unique=True)
    name = db.Column(db.String(200))
    active = db.Column(db.Boolean, default=True, nullable=False)
    added_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)


class SentMarketingEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(300), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    text_content = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    recipients_count = db.Column(db.Integer, default=0)
    recipients_summary = db.Column(db.Text)  # small JSON/CSV summary
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def generate_marketing_token(email):
    return get_token_serializer().dumps(email, salt='marketing-unsubscribe')


def verify_marketing_token(token, max_age=60 * 60 * 24 * 30):
    try:
        email = get_token_serializer().loads(token, salt='marketing-unsubscribe', max_age=max_age)
        return True, email
    except SignatureExpired:
        return False, 'expired'
    except BadSignature:
        return False, 'invalid'
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200))
    tickets_info_url = db.Column(db.Text)
    livestream_url = db.Column(db.Text)
    youtube_embed_url = db.Column(db.Text)
    competition_facebook_post_id = db.Column(db.String(100))
    facebook_post_id = db.Column(db.String(100))
    event_photo = db.Column(db.String(300))  # Featured photo for the event
    ba_favourites_count = db.Column(db.Integer, default=6)
    audience_top_n = db.Column(db.Integer, default=3)
    voting_closed = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    stories = db.relationship('Story', backref='event', lazy=True, cascade='all, delete-orphan')
    competition_entries = db.relationship('EventCompetitionEntry', backref='event', lazy=True, cascade='all, delete-orphan')
    
    creator = db.relationship('User', backref='events')


class NewsItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    subtitle = db.Column(db.String(300))
    content = db.Column(db.Text, nullable=False)
    published_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    news_photo = db.Column(db.String(300))
    facebook_post_id = db.Column(db.String(100))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = db.relationship('User', backref='news_items')

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    competition_entry_id = db.Column(db.Integer, db.ForeignKey('event_competition_entry.id'))
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    facebook_post_id = db.Column(db.String(100))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    photos = db.relationship('Photo', backref='story', lazy=True, cascade='all, delete-orphan')
    
    creator = db.relationship('User', backref='stories')

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey('story.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    caption = db.Column(db.String(500))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey('story.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    story = db.relationship('Story', backref=db.backref('comments', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref='comments')

class StoryReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey('story.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reaction_type = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('story_id', 'user_id', name='uq_story_reaction_user'),)

    story = db.relationship('Story', backref=db.backref('reactions', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref='story_reactions')

class CommentReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reaction_type = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('comment_id', 'user_id', name='uq_comment_reaction_user'),)

    comment = db.relationship('Comment', backref=db.backref('reactions', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref='comment_reactions')


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    verb = db.Column(db.String(80), nullable=False)  # e.g. 'commented', 'reacted', 'posted'
    target_type = db.Column(db.String(50))  # e.g. 'story', 'comment', 'news'
    target_id = db.Column(db.Integer)
    data = db.Column(db.Text)  # optional metadata
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='notifications')
    actor = db.relationship('User', foreign_keys=[actor_id])


def create_notification(recipient_id, actor_id, verb, target_type=None, target_id=None, data=None, commit=True):
    """Create a notification for a recipient.

    If commit=True the DB session will be committed; otherwise caller should commit.
    """
    try:
        note = Notification(
            recipient_id=recipient_id,
            actor_id=actor_id,
            verb=verb,
            target_type=target_type,
            target_id=target_id,
            data=(data[:200] if isinstance(data, str) and len(data) > 200 else data),
        )
        db.session.add(note)
        if commit:
            db.session.commit()
        return note
    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to create notification')
        return None

class EventCompetitionEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    band_name = db.Column(db.String(200), nullable=False)
    conductor = db.Column(db.String(200))
    programme_order = db.Column(db.Integer)
    draw_order = db.Column(db.Integer)
    result = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stories = db.relationship('Story', backref='competition_entry', lazy=True)
    predictions = db.relationship('CompetitionPrediction', backref='entry', lazy=True, cascade='all, delete-orphan')

class CompetitionPrediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_id = db.Column(db.Integer, db.ForeignKey('event_competition_entry.id'), nullable=False)
    predicted_position = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('event_id', 'user_id', 'entry_id', name='uq_prediction_user_entry'),)

    event = db.relationship('Event', backref='predictions')
    user = db.relationship('User', backref='predictions')

class AudienceVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_id = db.Column(db.Integer, db.ForeignKey('event_competition_entry.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('event_id', 'user_id', name='uq_audience_vote_user_event'),)

    event = db.relationship('Event', backref='audience_votes')
    user = db.relationship('User', backref='audience_votes')
    entry = db.relationship('EventCompetitionEntry', backref='audience_votes')

class AdminInvitation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    used_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='admin')  # 'admin' or 'contributor'
    
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_invitations')
    user = db.relationship('User', foreign_keys=[used_by], backref='used_invitation')

class Profile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    short_bio = db.Column(db.String(500))  # Short one-line bio for card display
    bio = db.Column(db.Text, nullable=False)  # Full bio for detail page
    card_photo = db.Column(db.String(300))  # Smaller portrait image for people cards
    photo = db.Column(db.String(300))  # Cover photo for the full profile page
    display_order = db.Column(db.Integer, default=0)  # For manual ordering
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    homepage_bg_image = db.Column(db.String(300))  # Background image for homepage welcome section
    facebook_page_id = db.Column(db.String(200))  # Facebook Page ID
    facebook_access_token = db.Column(db.String(500))  # Long-lived page access token
    auto_share_stories = db.Column(db.Boolean, default=False)  # Auto-share new stories to Facebook
    auto_share_programmes = db.Column(db.Boolean, default=False)  # Auto-share programme/concert entries
    auto_share_news = db.Column(db.Boolean, default=False)  # Auto-share news items
    auto_share_events = db.Column(db.Boolean, default=False)  # Auto-share event announcements
    auto_share_competitions = db.Column(db.Boolean, default=False)  # Auto-share competition tables
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    updated_by = db.relationship('User', backref='site_settings_updates')

class GalleryAlbum(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    caption = db.Column(db.Text)
    display_order = db.Column(db.Integer, default=0)
    cover_photo_id = db.Column(db.Integer, db.ForeignKey('gallery_photo.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    photos = db.relationship('GalleryPhoto', foreign_keys='GalleryPhoto.album_id', backref='album', lazy=True)
    cover_photo = db.relationship('GalleryPhoto', foreign_keys=[cover_photo_id], post_update=True)


class GalleryPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey('gallery_album.id'))
    filename = db.Column(db.String(300), nullable=False)
    caption = db.Column(db.Text)
    display_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProgrammeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    entry_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200))
    review = db.Column(db.Text, nullable=False)
    tickets_info_url = db.Column(db.Text)
    livestream_url = db.Column(db.Text)
    youtube_embed_url = db.Column(db.Text)
    facebook_post_id = db.Column(db.String(100))
    photo = db.Column(db.String(300))
    entry_type = db.Column(db.String(20), nullable=False, default='concert')
    section = db.Column(db.String(20), nullable=False, default='promotion')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = db.relationship('User', backref='programme_entries')
    programme_works = db.relationship('ProgrammeWork', backref='programme_entry', lazy=True, cascade='all, delete-orphan')

class ProgrammeWork(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    programme_entry_id = db.Column(db.Integer, db.ForeignKey('programme_entry.id'), nullable=False)
    work_title = db.Column(db.String(300), nullable=False)
    composer = db.Column(db.String(300))
    arranger = db.Column(db.String(300))
    display_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Band(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.Text)
    postcode = db.Column(db.String(10), nullable=False)  # Band's base location postcode
    default_rehearsal_days = db.Column(db.String(500))  # Comma-separated default rehearsal days
    logo = db.Column(db.String(300))  # Band logo/image
    website = db.Column(db.String(500))
    email = db.Column(db.String(120))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    creator = db.relationship('User', foreign_keys=[created_by], backref='bands_created')
    updated_by = db.relationship('User', foreign_keys=[updated_by_id], backref='bands_updated')
    members = db.relationship('BandMember', backref='band', lazy=True, cascade='all, delete-orphan')
    positions = db.relationship('PositionVacant', backref='band', lazy=True, cascade='all, delete-orphan')

class BandMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(50), default='member')  # 'member', 'conductor', 'band_admin'
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('band_id', 'user_id', name='uq_band_member'),)
    
    user = db.relationship('User', backref='band_memberships')

class PositionVacant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=False)
    instrument = db.Column(db.String(100), nullable=False)  # e.g. 'Cornet', 'Trombone', 'Tuba'
    section = db.Column(db.String(50), nullable=False)  # '4th', '3rd', '2nd', '1st', 'championship'
    rehearsal_days = db.Column(db.String(500))  # JSON string or comma-separated days (e.g. "Monday,Wednesday,Friday")
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = db.Column(db.DateTime)  # Optional expiration date for the position posting

def empty_reaction_counts():
    return {reaction_type: 0 for reaction_type in REACTION_TYPES}

def parse_optional_int(value):
    cleaned = (value or '').strip()
    if cleaned == '':
        return None
    return int(cleaned)


def normalize_postcode(postcode):
    cleaned = re.sub(r'\s+', '', (postcode or '').upper())
    return cleaned


@lru_cache(maxsize=1024)
def geocode_uk_postcode(postcode):
    """Resolve UK postcode to (latitude, longitude) using postcodes.io."""
    normalized = normalize_postcode(postcode)
    if not normalized:
        return None

    try:
        response = requests.get(f'https://api.postcodes.io/postcodes/{normalized}', timeout=4)
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if response.status_code != 200 or payload.get('status') != 200:
        return None

    result = payload.get('result') or {}
    latitude = result.get('latitude')
    longitude = result.get('longitude')
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


def distance_km_between(lat1, lon1, lat2, lon2):
    """Compute great-circle distance in kilometers with the Haversine formula."""
    radius_km = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius_km * c


def normalize_rehearsal_days(days):
    """Return unique valid weekday names while preserving input order."""
    seen = set()
    normalized = []
    for day in days:
        cleaned = (day or '').strip().title()
        if cleaned in WEEK_DAYS and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


class ListPagination:
    """Minimal pagination adapter for already-filtered in-memory results."""

    def __init__(self, items, page, per_page):
        self.total = len(items)
        self.page = max(page, 1)
        self.per_page = per_page
        self.pages = max((self.total + per_page - 1) // per_page, 1)
        start = (self.page - 1) * per_page
        end = start + per_page
        self.items = items[start:end]

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1

    @property
    def next_num(self):
        return self.page + 1

    def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            in_left = num <= left_edge
            in_middle = self.page - left_current <= num <= self.page + right_current
            in_right = num > self.pages - right_edge
            if in_left or in_middle or in_right:
                if last + 1 != num:
                    yield None
                yield num
                last = num

def bootstrap_admin_user():
    """Create a first admin account from env vars if no admin exists yet."""
    admin_exists = User.query.filter(
        (User.role == 'admin') | (User.is_admin.is_(True))
    ).first()
    if admin_exists:
        return

    username = os.environ.get('ADMIN_BOOTSTRAP_USERNAME')
    email = os.environ.get('ADMIN_BOOTSTRAP_EMAIL')
    password = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD')

    if not username or not email or not password:
        return

    # Do not create duplicates if the account already exists as non-admin.
    existing_user = User.query.filter(
        (User.username == username) | (User.email == email)
    ).first()
    if existing_user:
        existing_user.role = 'admin'
        existing_user.is_admin = True
        existing_user.set_password(password)
        db.session.commit()
        return

    user = User(username=username, email=email, role='admin', is_admin=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

# Ensure tables exist when app is imported by Gunicorn on Heroku.
with app.app_context():
    schema = app.config.get('APP_DB_SCHEMA')
    if database_url.startswith('postgresql://') and schema and schema != 'public':
        db.session.execute(text(f'CREATE SCHEMA IF NOT EXISTS {schema}'))
        db.session.commit()

    db.create_all()
    ensure_optional_columns()
    try:
        bootstrap_admin_user()
    except Exception:
        db.session.rollback()
        app.logger.exception('Admin bootstrap failed; continuing startup without seeded admin.')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Jinja2 filter to convert UTC datetime to Europe/London (BST/GMT) for display
@app.template_filter('to_london')
def to_london_filter(dt, fmt='%d %b %Y %H:%M'):
    if dt is None:
        return ''
    aware = dt.replace(tzinfo=UTC_TZ)
    return aware.astimezone(LONDON_TZ).strftime(fmt)

# Jinja2 filter to preserve line breaks
@app.template_filter('nl2br')
def nl2br_filter(text):
    """Convert newlines to <br> tags for HTML display"""
    if not text:
        return text
    return text.replace('\n', '<br>')

@app.template_filter('safe_richtext')
def safe_richtext_filter(text):
    return sanitize_story_content(text)

# Facebook sharing helper function
def resolve_photo_url(filename):
    """Return a fully-qualified public URL for a photo filename or Cloudinary URL, or None."""
    if not filename:
        return None
    if filename.startswith('http://') or filename.startswith('https://'):
        return filename
    return url_for('static', filename=f'uploads/{filename}', _external=True)

def share_story_to_facebook(story, event, require_auto_share=False):
    """Share a post to Facebook Page."""
    settings, error = get_facebook_settings(require_auto_share=require_auto_share)
    if error:
        return False, error

    content_text = richtext_to_facebook_text(story.content)
    message_parts = [event.title.strip(), story.title.strip(), f"Posted by: {story.creator.username}"]
    if content_text:
        message_parts.append(content_text)

    # Use first story photo, falling back to the event photo
    story_photo = story.photos[0].filename if story.photos else None
    photo_url = resolve_photo_url(story_photo) or resolve_photo_url(getattr(event, 'event_photo', None))

    success, message, post_id = publish_to_facebook(
        '\n\n'.join(part for part in message_parts if part),
        get_story_public_url(story),
        existing_post_id=story.facebook_post_id,
        image_url=photo_url,
    )
    if success:
        story.facebook_post_id = post_id
        db.session.commit()
    return success, message

def share_programme_to_facebook(entry):
    review_text = richtext_to_facebook_text(entry.review)
    entry_label = 'Archive entry' if entry.section == 'archive' else entry.entry_type.title()
    message_parts = [entry.title.strip(), f"{entry_label} by: {entry.creator.username}"]
    if review_text:
        message_parts.append(review_text)

    if entry.programme_works:
        work_lines = [
            f"{work.work_title} | {work.composer or '-'} | {work.arranger or '-'}"
            for work in sorted(entry.programme_works, key=lambda work: work.display_order)
        ]
        if work_lines:
            message_parts.append('Programme:\n' + '\n'.join(work_lines))

    success, message, post_id = publish_to_facebook(
        '\n\n'.join(part for part in message_parts if part),
        get_programme_public_url(entry),
        existing_post_id=entry.facebook_post_id,
        image_url=resolve_photo_url(getattr(entry, 'photo', None)),
    )
    if success:
        entry.facebook_post_id = post_id
        db.session.commit()
    return success, message

def share_news_to_facebook(item, force_new=False):
    """Share a news item to Facebook page."""
    settings, error = get_facebook_settings()
    if error:
        return False, error, None

    message_parts = [item.title.strip()]
    if item.subtitle:
        message_parts.append(item.subtitle.strip())
    if item.content:
        # strip richtext to plain-ish text
        message_parts.append(richtext_to_facebook_text(item.content))

    # Resolve the photo to a fully-qualified public URL
    photo_url = resolve_photo_url(item.news_photo)

    success, message, post_id = publish_to_facebook(
        '\n\n'.join(part for part in message_parts if part),
        url_for('news_detail', news_id=item.id, _external=True),
        existing_post_id=None if force_new else item.facebook_post_id,
        image_url=photo_url,
    )
    if success:
        item.facebook_post_id = post_id
        db.session.commit()
    return success, message, post_id

def share_competition_to_facebook(event, publisher_username):
    context = build_competition_display_context(event)
    if not context['competition_stage_rows']:
        return False, 'No competition table data is ready to publish yet.'

    message_parts = [
        event.title.strip(),
        f"Competition update: {context['competition_stage_label']}",
        f"Published by: {publisher_username}",
        '\n'.join(context['competition_stage_rows']),
    ]
    success, message, post_id = publish_to_facebook(
        '\n\n'.join(part for part in message_parts if part),
        get_competition_public_url(event),
        existing_post_id=event.competition_facebook_post_id,
        image_url=resolve_photo_url(getattr(event, 'event_photo', None)),
    )
    if success:
        event.competition_facebook_post_id = post_id
        db.session.commit()
    return success, message

def share_event_announcement(event):
    settings, error = get_facebook_settings()
    if error:
        return False, error, None

    parts = [event.title.strip()]
    if event.event_date:
        parts.append(event.event_date.strftime('%A %d %B %Y at %I:%M %p'))
    if event.location:
        parts.append(event.location.strip())
    if event.description:
        parts.append(summarize_plain_text(richtext_to_facebook_text(event.description), max_length=300))

    success, message, post_id = publish_to_facebook(
        '\n\n'.join(part for part in parts if part),
        get_event_public_url(event),
        existing_post_id=event.facebook_post_id,
        image_url=resolve_photo_url(getattr(event, 'event_photo', None)),
    )
    if success:
        event.facebook_post_id = post_id
        db.session.commit()
    return success, message, post_id

def share_ba_favourites_to_facebook(event):
    # Build a short summary of BA favourites for the event
    try:
        favs = get_ba_favourites_for_event(event.id)
    except Exception:
        return False, 'Could not compute favourites', None

    if not favs:
        return False, 'No favourites data available', None

    lines = [f"BA Favourites — {event.title.strip()}"]
    for entry, pick_count, avg_rank in favs[:10]:
        lines.append(f"{entry.band_name} — picks: {pick_count}, avg: {avg_rank:.1f}")

    success, message, post_id = publish_to_facebook(
        '\n'.join(lines),
        get_event_public_url(event),
        existing_post_id=None,
        image_url=resolve_photo_url(getattr(event, 'event_photo', None)),
    )
    return success, message, post_id

def get_ba_favourites_for_event(event_id):
    """Return sorted list of (entry, pick_count, avg_rank) for BA team members."""
    predictions = CompetitionPrediction.query.filter_by(event_id=event_id).all()
    ba_data = {}  # entry_id -> list of positions
    for pred in predictions:
        if pred.user and (pred.user.is_contributor or pred.user.has_admin_access()):
            ba_data.setdefault(pred.entry_id, []).append(pred.predicted_position)

    results = []
    for entry_id, positions in ba_data.items():
        entry = EventCompetitionEntry.query.get(entry_id)
        if entry:
            results.append((entry, len(positions), sum(positions) / len(positions)))

    results.sort(key=lambda x: (-x[1], x[2]))
    return results

def get_audience_votes_for_event(event_id):
    """Return sorted list of (entry, vote_count) for audience votes."""
    votes = AudienceVote.query.filter_by(event_id=event_id).all()
    vote_counts = {}
    for vote in votes:
        vote_counts[vote.entry_id] = vote_counts.get(vote.entry_id, 0) + 1

    results = []
    for entry_id, count in vote_counts.items():
        entry = EventCompetitionEntry.query.get(entry_id)
        if entry:
            results.append((entry, count))

    results.sort(key=lambda x: -x[1])
    return results

def get_user_predictions_for_event(event_id, user_id):
    """Get the current BA user's saved favourite picks for an event."""
    predictions = CompetitionPrediction.query.filter_by(
        event_id=event_id,
        user_id=user_id
    ).all()
    return {pred.entry_id: pred.predicted_position for pred in predictions}

def get_live_events():
    """Get events that are happening right now (within 3 hours before/after event time)"""
    now = datetime.utcnow()
    time_window = timedelta(hours=3)
    try:
        live_events = Event.query.filter(
            Event.event_date >= now - time_window,
            Event.event_date <= now + time_window
        ).order_by(Event.event_date.asc()).all()
        return live_events
    except ProgrammingError:
        app.logger.warning('Live events query failed with ProgrammingError; attempting startup migration and retry')
        # Postgres marks the transaction as aborted after any error; must roll back before re-querying.
        db.session.rollback()
        try:
            runner = getattr(app.wsgi_app, 'run_migrations', None)
            if callable(runner):
                runner()
        except Exception:
            app.logger.exception('Startup migration triggered from get_live_events failed')

        # Retry once
        try:
            return Event.query.filter(
                Event.event_date >= now - time_window,
                Event.event_date <= now + time_window
            ).order_by(Event.event_date.asc()).all()
        except Exception:
            app.logger.exception('Live events query still failing after attempted migration')
            return []

def get_cookie_consent_choice():
    if not has_request_context():
        return None
    consent_choice = (request.cookies.get(COOKIE_CONSENT_NAME, '') or '').strip().lower()
    if consent_choice in (COOKIE_CONSENT_ACCEPT, COOKIE_CONSENT_REJECT):
        return consent_choice
    return None

def has_media_cookie_consent():
    return get_cookie_consent_choice() == COOKIE_CONSENT_ACCEPT

def get_safe_next_url(raw_next):
    candidate = (raw_next or '').strip()
    if not candidate:
        return url_for('index')
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return url_for('index')
    if not candidate.startswith('/'):
        return url_for('index')
    return candidate

@app.context_processor
def inject_live_events():
    """Make live events available to all templates"""
    consent_choice = get_cookie_consent_choice()
    return dict(
        live_events=get_live_events(),
        cookie_consent_choice=consent_choice,
        allow_third_party_media=(consent_choice == COOKIE_CONSENT_ACCEPT),
    )

# Public Routes
@app.route('/')
def index():
    # Get next upcoming events, promoted concerts, and latest news for carousel.
    now = datetime.utcnow()
    
    # Fetch upcoming events
    events = Event.query.filter(Event.event_date >= now).order_by(Event.event_date.asc()).all()
    
    # Fetch promoted concert entries
    concerts = ProgrammeEntry.query.filter(
        ProgrammeEntry.section == 'promotion',
        ProgrammeEntry.entry_date >= now
    ).order_by(ProgrammeEntry.entry_date.asc()).all()

    # Fetch most recent news items
    news_items = NewsItem.query.order_by(NewsItem.published_at.desc()).limit(6).all()
    
    # Combine and sort by date, take top 3
    combined = (
        [{'kind': 'event', 'date': e.event_date, 'item': e} for e in events] +
        [{'kind': 'programme', 'date': c.entry_date, 'item': c} for c in concerts] +
        [{'kind': 'news', 'date': n.published_at, 'item': n} for n in news_items]
    )
    combined.sort(key=lambda x: x['date'], reverse=True)
    carousel_items = combined[:6]
    
    # Get site settings (create if doesn't exist)
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    
    return render_template('index.html', carousel_items=carousel_items, settings=settings)

@app.route('/events')
def events_page():
    # Show all events in date order (upcoming first, then past)
    now = datetime.utcnow()
    upcoming = Event.query.filter(Event.event_date >= now).order_by(Event.event_date.asc()).all()
    past = Event.query.filter(Event.event_date < now).order_by(Event.event_date.desc()).all()
    return render_template('events.html', upcoming_events=upcoming, past_events=past)

@app.route('/events/search')
def search_events():
    """Search events and stories by various criteria"""
    query = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    
    # Start with all events
    events = Event.query
    
    # Filter by search query (event title, location, description, or story titles)
    if query:
        # Search in events OR their stories
        events = events.filter(
            db.or_(
                Event.title.ilike(f'%{query}%'),
                Event.location.ilike(f'%{query}%'),
                Event.description.ilike(f'%{query}%'),
                Event.competition_entries.any(EventCompetitionEntry.band_name.ilike(f'%{query}%')),
                Event.competition_entries.any(EventCompetitionEntry.conductor.ilike(f'%{query}%')),
                Event.stories.any(Story.title.ilike(f'%{query}%')),
                Event.stories.any(Story.content.ilike(f'%{query}%'))
            )
        )
    
    # Filter by date range
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            events = events.filter(Event.event_date >= from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d')
            # Add one day to include the entire end date
            to_date = to_date + timedelta(days=1)
            events = events.filter(Event.event_date < to_date)
        except ValueError:
            pass
    
    # Order by date (most recent first)
    results = events.order_by(Event.event_date.desc()).all()

    band_matches = []
    if query:
        band_entries = EventCompetitionEntry.query.filter(
            db.or_(
                EventCompetitionEntry.band_name.ilike(f'%{query}%'),
                EventCompetitionEntry.conductor.ilike(f'%{query}%')
            )
        ).order_by(EventCompetitionEntry.band_name.asc()).all()

        for entry in band_entries:
            linked_posts = Story.query.filter_by(competition_entry_id=entry.id).order_by(Story.timestamp.desc()).all()
            if linked_posts:
                band_matches.append({
                    'entry': entry,
                    'event': entry.event,
                    'stories': linked_posts,
                })
    
    return render_template('search_results.html', 
                         results=results, 
                         band_matches=band_matches,
                         query=query, 
                         date_from=date_from, 
                         date_to=date_to)

@app.route('/our-people')
def our_people():
    # Get all profiles ordered by display_order and name
    profiles = Profile.query.order_by(Profile.display_order.desc(), Profile.name).all()
    return render_template('our_people.html', profiles=profiles)

@app.route('/our-people/<int:profile_id>')
def profile_detail(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    return render_template('profile_detail.html', profile=profile)

@app.route('/concerts')
def concerts():
    query = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    entries_query = ProgrammeEntry.query.filter_by(section='promotion')
    if query:
        entries_query = entries_query.filter(
            db.or_(
                ProgrammeEntry.title.ilike(f'%{query}%'),
                ProgrammeEntry.location.ilike(f'%{query}%'),
                ProgrammeEntry.review.ilike(f'%{query}%'),
                ProgrammeEntry.entry_type.ilike(f'%{query}%')
            )
        )

    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            entries_query = entries_query.filter(ProgrammeEntry.entry_date >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            entries_query = entries_query.filter(ProgrammeEntry.entry_date < to_date)
        except ValueError:
            pass

    entries = entries_query.order_by(ProgrammeEntry.entry_date.desc()).all()
    return render_template('concerts.html', entries=entries, q=query, date_from=date_from, date_to=date_to)

@app.route('/archive')
def archive():
    query = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    entries_query = ProgrammeEntry.query.filter_by(section='archive')
    if query:
        entries_query = entries_query.filter(
            db.or_(
                ProgrammeEntry.title.ilike(f'%{query}%'),
                ProgrammeEntry.location.ilike(f'%{query}%'),
                ProgrammeEntry.review.ilike(f'%{query}%'),
                ProgrammeEntry.entry_type.ilike(f'%{query}%')
            )
        )

    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            entries_query = entries_query.filter(ProgrammeEntry.entry_date >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            entries_query = entries_query.filter(ProgrammeEntry.entry_date < to_date)
        except ValueError:
            pass

    entries = entries_query.order_by(ProgrammeEntry.entry_date.desc()).all()
    return render_template('archive.html', entries=entries, q=query, date_from=date_from, date_to=date_to)


@app.route('/news')
def news_page():
    query = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    items_query = NewsItem.query
    if query:
        items_query = items_query.filter(
            db.or_(
                NewsItem.title.ilike(f'%{query}%'),
                NewsItem.subtitle.ilike(f'%{query}%'),
                NewsItem.content.ilike(f'%{query}%')
            )
        )

    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            items_query = items_query.filter(NewsItem.published_at >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            items_query = items_query.filter(NewsItem.published_at < to_date)
        except ValueError:
            pass

    items = items_query.order_by(NewsItem.published_at.desc()).all()
    return render_template('news.html', items=items, q=query, date_from=date_from, date_to=date_to)


@app.route('/news/<int:news_id>')
def news_detail(news_id):
    item = NewsItem.query.get_or_404(news_id)
    return render_template('news_detail.html', item=item)

@app.route('/gallery')
def gallery():
    """Public gallery page showing album folders and ungrouped photos."""
    albums = GalleryAlbum.query.order_by(GalleryAlbum.display_order.desc(), GalleryAlbum.created_at.desc()).all()
    ungrouped_photos = GalleryPhoto.query.filter_by(album_id=None).order_by(
        GalleryPhoto.display_order.desc(),
        GalleryPhoto.created_at.desc(),
    ).all()
    return render_template('gallery.html', albums=albums, ungrouped_photos=ungrouped_photos)


@app.route('/gallery/album/<int:album_id>')
def gallery_album_detail(album_id):
    album = GalleryAlbum.query.get_or_404(album_id)
    photos = GalleryPhoto.query.filter_by(album_id=album.id).order_by(
        GalleryPhoto.display_order.desc(),
        GalleryPhoto.created_at.desc(),
    ).all()
    return render_template('gallery_album.html', album=album, photos=photos)

@app.route('/programme/<int:entry_id>')
def programme_detail(entry_id):
    entry = ProgrammeEntry.query.get_or_404(entry_id)
    return render_template(
        'programme_detail.html',
        entry=entry,
        share_url=get_programme_public_url(entry),
        preview_text=summarize_programme_for_preview(entry),
        og_image_url=get_programme_preview_image_url(entry),
        fb_comments=get_facebook_comments(entry.facebook_post_id),
    )

@app.route('/stories/<int:story_id>')
def public_story(story_id):
    story = Story.query.get_or_404(story_id)
    event = story.event
    return render_template(
        'story_detail.html',
        story=story,
        event=event,
        share_url=get_story_public_url(story),
        preview_text=summarize_story_for_preview(story),
        og_image_url=get_story_preview_image_url(story, event),
        fb_comments=get_facebook_comments(story.facebook_post_id),
    )

@app.route('/event/<int:event_id>/competition-share')
def competition_share(event_id):
    event = Event.query.get_or_404(event_id)
    competition_context = build_competition_display_context(event)
    return render_template(
        'competition_share.html',
        event=event,
        share_url=get_competition_public_url(event),
        preview_text=summarize_competition_for_preview(event, competition_context),
        og_image_url=get_event_preview_image_url(event),
        fb_comments=get_facebook_comments(event.competition_facebook_post_id),
        **competition_context,
    )

@app.route('/event/<int:event_id>')
def view_event(event_id):
    event = Event.query.get_or_404(event_id)
    stories = Story.query.filter_by(event_id=event_id).order_by(Story.timestamp.desc()).all()
    competition_context = build_competition_display_context(event)

    competition_story_links = {}
    for story in stories:
        if not story.competition_entry_id:
            continue
        competition_story_links.setdefault(story.competition_entry_id, []).append(story)

    # Get BA favourites and audience vote tallies for pinned cards
    ba_favourites = get_ba_favourites_for_event(event_id)
    audience_votes = get_audience_votes_for_event(event_id)
    user_audience_vote = None
    if current_user.is_authenticated:
        user_audience_vote = AudienceVote.query.filter_by(
            event_id=event_id, user_id=current_user.id
        ).first()

    story_ids = [story.id for story in stories]

    story_reaction_counts = {story_id: empty_reaction_counts() for story_id in story_ids}
    story_user_reactions = {}
    if story_ids:
        story_reactions = StoryReaction.query.filter(StoryReaction.story_id.in_(story_ids)).all()
        for reaction in story_reactions:
            if reaction.reaction_type in REACTION_TYPES:
                story_reaction_counts[reaction.story_id][reaction.reaction_type] += 1
            if current_user.is_authenticated and reaction.user_id == current_user.id:
                story_user_reactions[reaction.story_id] = reaction.reaction_type

    comment_ids = []
    comment_reaction_counts = {}
    comment_user_reactions = {}

    # Get comments and reactions for each story
    for story in stories:
        story.comments_list = Comment.query.filter_by(story_id=story.id).order_by(Comment.created_at.asc()).all()
        story.reaction_counts = story_reaction_counts.get(story.id, empty_reaction_counts())
        story.user_reaction = story_user_reactions.get(story.id)

        for comment in story.comments_list:
            comment_ids.append(comment.id)
            comment_reaction_counts[comment.id] = empty_reaction_counts()

    if comment_ids:
        comment_reactions = CommentReaction.query.filter(CommentReaction.comment_id.in_(comment_ids)).all()
        for reaction in comment_reactions:
            if reaction.reaction_type in REACTION_TYPES:
                comment_reaction_counts[reaction.comment_id][reaction.reaction_type] += 1
            if current_user.is_authenticated and reaction.user_id == current_user.id:
                comment_user_reactions[reaction.comment_id] = reaction.reaction_type

    for story in stories:
        for comment in story.comments_list:
            comment.reaction_counts = comment_reaction_counts.get(comment.id, empty_reaction_counts())
            comment.user_reaction = comment_user_reactions.get(comment.id)

    return render_template(
        'event_detail.html',
        event=event,
        stories=stories,
        competition_story_links=competition_story_links,
        ba_favourites=ba_favourites,
        audience_votes=audience_votes,
        user_audience_vote=user_audience_vote,
        **competition_context,
    )

# Contributor Dashboard
@app.route('/contribute')
@contributor_required
def contribute_dashboard():
    """Dashboard for contributors to manage their stories"""
    # Get all events ordered by date (upcoming first, then past)
    upcoming = Event.query.filter(Event.event_date >= datetime.utcnow()).order_by(Event.event_date.asc()).all()
    past = Event.query.filter(Event.event_date < datetime.utcnow()).order_by(Event.event_date.desc()).limit(10).all()
    
    # Get stories by current user
    my_stories = Story.query.filter_by(created_by=current_user.id).order_by(Story.timestamp.desc()).all()
    
    return render_template('contribute/dashboard.html', upcoming_events=upcoming, past_events=past, my_stories=my_stories)

# Authentication Routes
@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')

@app.route('/sitemap.xml')
def sitemap():
    base = get_public_base_url()
    today = datetime.utcnow().strftime('%Y-%m-%d')

    static_urls = [
        ('/', '1.0', 'daily'),
        ('/news', '0.9', 'daily'),
        ('/events', '0.8', 'weekly'),
        ('/concerts', '0.7', 'weekly'),
        ('/archive', '0.6', 'weekly'),
        ('/our-people', '0.6', 'monthly'),
        ('/gallery', '0.6', 'weekly'),
        ('/contact', '0.5', 'monthly'),
        ('/contribute', '0.5', 'monthly'),
        ('/privacy-policy', '0.3', 'yearly'),
    ]

    urls = []
    for path, priority, changefreq in static_urls:
        urls.append({'loc': base + path, 'lastmod': today,
                     'changefreq': changefreq, 'priority': priority})

    news_items = NewsItem.query.order_by(NewsItem.published_at.desc()).all()
    for item in news_items:
        lastmod = (item.updated_at or item.published_at).strftime('%Y-%m-%d')
        urls.append({'loc': f"{base}/news/{item.id}", 'lastmod': lastmod,
                     'changefreq': 'monthly', 'priority': '0.8'})

    events = Event.query.order_by(Event.event_date.desc()).all()
    for ev in events:
        lastmod = (ev.created_at or datetime.utcnow()).strftime('%Y-%m-%d')
        urls.append({'loc': f"{base}/event/{ev.id}", 'lastmod': lastmod,
                     'changefreq': 'weekly', 'priority': '0.7'})

    stories = Story.query.order_by(Story.timestamp.desc()).all()
    for s in stories:
        lastmod = s.timestamp.strftime('%Y-%m-%d')
        urls.append({'loc': f"{base}/stories/{s.id}", 'lastmod': lastmod,
                     'changefreq': 'monthly', 'priority': '0.6'})

    profiles = Profile.query.all()
    for p in profiles:
        urls.append({'loc': f"{base}/our-people/{p.id}", 'lastmod': today,
                     'changefreq': 'monthly', 'priority': '0.5'})

    albums = GalleryAlbum.query.all()
    for a in albums:
        urls.append({'loc': f"{base}/gallery/album/{a.id}", 'lastmod': today,
                     'changefreq': 'monthly', 'priority': '0.5'})

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml_lines.append(
            f"  <url>\n"
            f"    <loc>{u['loc']}</loc>\n"
            f"    <lastmod>{u['lastmod']}</lastmod>\n"
            f"    <changefreq>{u['changefreq']}</changefreq>\n"
            f"    <priority>{u['priority']}</priority>\n"
            f"  </url>"
        )
    xml_lines.append('</urlset>')
    response = make_response('\n'.join(xml_lines))
    response.headers['Content-Type'] = 'application/xml'
    return response

@app.route('/robots.txt')
def robots_txt():
    base = get_public_base_url()
    content = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /admin\n"
        "Disallow: /login\n"
        "Disallow: /register\n"
        "Disallow: /logout\n"
        "Disallow: /forgot-password\n"
        "Disallow: /reset-password/\n"
        "Disallow: /notifications\n"
        "Disallow: /notifications/\n"
        "Disallow: /contribute\n"
        "Disallow: /event/*/competition\n"
        "Disallow: /event/*/competition-share\n"
        "Disallow: /event/*/predict\n"
        "Disallow: /event/*/vote\n"
        "Disallow: /marketing/\n"
        "Disallow: /cookie-consent\n"
        f"\nSitemap: {base}/sitemap.xml\n"
    )
    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    return response

@app.route('/cookie-consent', methods=['POST'])
def cookie_consent():
    choice = (request.form.get('choice', '') or '').strip().lower()
    next_url = get_safe_next_url(request.form.get('next'))

    if choice not in (COOKIE_CONSENT_ACCEPT, COOKIE_CONSENT_REJECT):
        flash('Cookie preference was invalid. Please try again.', 'warning')
        return redirect(next_url)

    response = redirect(next_url)
    response.set_cookie(
        COOKIE_CONSENT_NAME,
        choice,
        max_age=COOKIE_CONSENT_MAX_AGE,
        secure=bool(os.environ.get('DYNO')),
        httponly=True,
        samesite='Lax',
    )
    flash('Your cookie preferences have been updated.', 'success')
    return response

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()

        if not name or not email or not message:
            flash('Please fill in all fields.', 'warning')
            return render_template('contact.html', name=name, email=email, message=message)

        html_content = f"""
<p><strong>New contact form message from Brassing Around</strong></p>
<p><strong>Name:</strong> {name}</p>
<p><strong>Email:</strong> {email}</p>
<hr>
<p>{message.replace(chr(10), '<br>')}</p>
"""
        text_content = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}"

        sent = brevo_send_email(
            subject=f'Brassing Around contact: {name}',
            html_content=html_content,
            text_content=text_content,
            recipients=['aroundbrassing@gmail.com'],
        )

        if sent:
            flash("Thanks for getting in touch! We'll get back to you soon.", 'success')
        else:
            flash("Your message couldn't be sent right now — please try emailing us directly at aroundbrassing@gmail.com.", 'warning')

        return redirect(url_for('contact'))

    return render_template('contact.html', name='', email='', message='')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username_or_email = request.form.get('username')
        password = request.form.get('password')
        
        # Check if input is username or email
        user = User.query.filter(
            db.or_(
                User.username == username_or_email,
                User.email == username_or_email
            )
        ).first()
        
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully!', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Invalid username/email or password', 'danger')
    
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()

        if user:
            send_password_reset_email(user)

        flash('If that email address exists in our system, a password reset link has been sent.', 'info')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    try:
        email = get_token_serializer().loads(
            token,
            salt='password-reset',
            max_age=app.config['PASSWORD_RESET_TOKEN_MAX_AGE']
        )
    except SignatureExpired:
        flash('That password reset link has expired. Please request a new one.', 'danger')
        return redirect(url_for('forgot_password'))
    except BadSignature:
        flash('That password reset link is invalid.', 'danger')
        return redirect(url_for('forgot_password'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash('That password reset link is invalid.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not password:
            flash('Please enter a new password.', 'danger')
            return redirect(url_for('reset_password', token=token))

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('reset_password', token=token))

        user.set_password(password)
        user.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Your password has been reset. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        invitation_code = request.form.get('invitation_code', '').strip()
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists', 'danger')
            return redirect(url_for('register'))
        
        # Check if this is an admin/contributor invitation
        user_role = 'user'
        invitation = None
        if invitation_code:
            invitation = AdminInvitation.query.filter_by(code=invitation_code, is_used=False).first()
            if invitation and invitation.expires_at > datetime.utcnow():
                user_role = invitation.role or 'admin'  # Use invitation role
                invitation.is_used = True
                invitation.used_by = None  # Will be set after user is created
            elif invitation and invitation.expires_at <= datetime.utcnow():
                flash('This invitation code has expired.', 'danger')
                return redirect(url_for('register'))
            elif invitation and invitation.is_used:
                flash('This invitation code has already been used.', 'danger')
                return redirect(url_for('register'))
            else:
                flash('Invalid invitation code.', 'danger')
                return redirect(url_for('register'))
        
        # Create user with appropriate role
        marketing_opt_in = request.form.get('marketing_opt_in') == 'on'
        is_admin = (user_role == 'admin')
        user = User(username=username, email=email, is_admin=is_admin, role=user_role,
                    marketing_opt_in=marketing_opt_in)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # Get user ID before commit
        
        # Update invitation if used
        if invitation:
            invitation.used_by = user.id
        
        db.session.commit()
        
        if user_role == 'admin':
            flash('Registration successful! You now have admin privileges. Please log in.', 'success')
        elif user_role == 'contributor':
            flash('Registration successful! You can create and edit posts. Please log in.', 'success')
        else:
            flash('Registration successful! You can now view and comment on events. Please log in.', 'success')
        return redirect(url_for('login'))
    
    invitation_code = request.args.get('invitation_code', '').strip()
    return render_template('register.html', invitation_code=invitation_code)

# Comment Routes
@app.route('/story/<int:story_id>/comment', methods=['POST'])
@login_required
def add_comment(story_id):
    story = Story.query.get_or_404(story_id)
    content = request.form.get('content', '').strip()
    
    if not content:
        flash('Comment cannot be empty.', 'danger')
        return redirect(url_for('view_event', event_id=story.event_id))
    
    comment = Comment(
        story_id=story_id,
        user_id=current_user.id,
        content=content
    )
    db.session.add(comment)
    db.session.commit()
    
    # Create notifications:
    # Notify story creator (if not the commenter)
    try:
        if story.creator and story.creator.id != current_user.id:
            create_notification(
                recipient_id=story.creator.id,
                actor_id=current_user.id,
                verb='commented',
                target_type='story',
                target_id=story.id,
                data=comment.content,
            )

        # Notify all admins and contributors (except the actor and story creator)
        admins_and_contribs = User.query.filter(
            (User.is_admin.is_(True)) | (User.role == 'contributor')
        ).all()
        for u in admins_and_contribs:
            if u.id in (current_user.id, getattr(story.creator, 'id', None)):
                continue
            create_notification(
                recipient_id=u.id,
                actor_id=current_user.id,
                verb='commented',
                target_type='story',
                target_id=story.id,
                data=comment.content,
            )
    except Exception:
        app.logger.exception('Notification creation failed for comment')

    flash('Comment added successfully!', 'success')
    return redirect(url_for('view_event', event_id=story.event_id) + f'#story-{story_id}')

@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    event_id = comment.story.event_id
    
    # Only comment author or admins can delete
    if comment.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    db.session.delete(comment)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/event/<int:event_id>/competition', methods=['GET', 'POST'])
@contributor_required
def manage_event_competition(event_id):
    event = Event.query.get_or_404(event_id)

    if request.method == 'POST':
        entry_ids = request.form.getlist('entry_id')
        band_names = request.form.getlist('band_name')
        conductors = request.form.getlist('conductor')
        programme_orders = request.form.getlist('programme_order')
        draw_orders = request.form.getlist('draw_order')
        results = request.form.getlist('result')

        existing_entries = {str(entry.id): entry for entry in EventCompetitionEntry.query.filter_by(event_id=event.id).all()}
        kept_ids = set()

        row_count = len(band_names)
        for idx in range(row_count):
            band_name = (band_names[idx] if idx < len(band_names) else '').strip()
            if not band_name:
                continue

            try:
                programme_order = parse_optional_int(programme_orders[idx] if idx < len(programme_orders) else '')
                draw_order = parse_optional_int(draw_orders[idx] if idx < len(draw_orders) else '')
                result = parse_optional_int(results[idx] if idx < len(results) else '')
            except ValueError:
                flash(f'Invalid number entered for band "{band_name}". Please use whole numbers only.', 'danger')
                return redirect(url_for('manage_event_competition', event_id=event.id))

            raw_entry_id = (entry_ids[idx] if idx < len(entry_ids) else '').strip()
            if raw_entry_id and raw_entry_id in existing_entries:
                entry = existing_entries[raw_entry_id]
                kept_ids.add(raw_entry_id)
            else:
                entry = EventCompetitionEntry(event_id=event.id)
                db.session.add(entry)

            entry.band_name = band_name
            entry.conductor = (conductors[idx] if idx < len(conductors) else '').strip() or None
            entry.programme_order = programme_order
            entry.draw_order = draw_order
            entry.result = result

        for entry_id, entry in existing_entries.items():
            if entry_id not in kept_ids:
                Story.query.filter_by(competition_entry_id=entry.id).update(
                    {Story.competition_entry_id: None},
                    synchronize_session=False,
                )
                db.session.delete(entry)

        db.session.commit()
        # Optionally auto-share competition tables
        settings = SiteSettings.query.first()
        if 'share_competition' in request.form:
            success, message = share_competition_to_facebook(event, current_user.username)
            if success:
                flash(message, 'success')
            else:
                flash(f'Competition tables updated. (Facebook share failed: {message})', 'warning')
        elif settings and settings.auto_share_competitions:
            success, message = share_competition_to_facebook(event, current_user.username)
            if success:
                flash('Competition tables updated and shared to Facebook!', 'success')
            else:
                flash(f'Competition tables updated. (Facebook share failed: {message})', 'warning')
        else:
            flash('Competition tables updated successfully.', 'success')
        return redirect(url_for('view_event', event_id=event.id))

    entries = EventCompetitionEntry.query.filter_by(event_id=event.id).all()
    entries = sorted(
        entries,
        key=lambda entry: (
            entry.programme_order is None,
            entry.programme_order if entry.programme_order is not None else 10**9,
            entry.band_name.lower(),
        )
    )
    entry_story_links = {}
    if entries:
        entry_ids = [entry.id for entry in entries]
        linked_stories = Story.query.filter(Story.competition_entry_id.in_(entry_ids)).order_by(Story.timestamp.desc()).all()
        for story in linked_stories:
            if story.competition_entry_id not in entry_story_links:
                entry_story_links[story.competition_entry_id] = {'count': 0, 'latest_story_id': story.id}
            entry_story_links[story.competition_entry_id]['count'] += 1

    return render_template('contribute/manage_event_competition.html', event=event, entries=entries, entry_story_links=entry_story_links)


@app.route('/event/<int:event_id>/competition/export', methods=['GET'])
@contributor_required
def export_event_competition_csv(event_id):
    event = Event.query.get_or_404(event_id)
    entries = EventCompetitionEntry.query.filter_by(event_id=event.id).all()
    entries = sorted(
        entries,
        key=lambda entry: (
            entry.programme_order is None,
            entry.programme_order if entry.programme_order is not None else 10**9,
            entry.band_name.lower(),
        )
    )

    csv_buffer = StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(['Programme Order', 'Band Name', 'Conductor', 'Draw Order', 'Result'])

    for entry in entries:
        writer.writerow([
            entry.programme_order or '',
            entry.band_name,
            entry.conductor or '',
            entry.draw_order or '',
            entry.result or '',
        ])

    safe_title = re.sub(r'[^A-Za-z0-9_-]+', '_', (event.title or '').strip()).strip('_') or f'event_{event.id}'
    filename = f'competition_{safe_title}.csv'

    return Response(
        csv_buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

@app.route('/event/<int:event_id>/predict', methods=['GET', 'POST'])
@login_required
def make_predictions(event_id):
    if not (current_user.is_contributor or current_user.has_admin_access()):
        flash('Only BA contributors and admins can submit favourite picks.', 'warning')
        return redirect(url_for('view_event', event_id=event_id))

    event = Event.query.get_or_404(event_id)
    ba_count = (event.ba_favourites_count or 6) if hasattr(event, 'ba_favourites_count') else 6
    entries = EventCompetitionEntry.query.filter_by(event_id=event_id).all()
    entries = sorted(
        entries,
        key=lambda e: (
            e.programme_order is None,
            e.programme_order if e.programme_order is not None else 10**9,
            e.band_name.lower(),
        )
    )

    if request.method == 'POST':
        CompetitionPrediction.query.filter_by(event_id=event_id, user_id=current_user.id).delete()

        entry_ids = request.form.getlist('entry_id')
        positions = request.form.getlist('position')

        for entry_id, position in zip(entry_ids, positions):
            try:
                entry_id = int(entry_id)
                position = int(position)
                if position > ba_count:
                    continue  # Only save the top N picks
                entry = EventCompetitionEntry.query.get(entry_id)
                if entry and entry.event_id == event_id:
                    pred = CompetitionPrediction(
                        event_id=event_id,
                        user_id=current_user.id,
                        entry_id=entry_id,
                        predicted_position=position,
                    )
                    db.session.add(pred)
            except (ValueError, TypeError):
                pass

        db.session.commit()
        flash(f'Your top {ba_count} favourites have been saved!', 'success')
        return redirect(url_for('view_event', event_id=event_id))

    user_predictions = get_user_predictions_for_event(event_id, current_user.id)

    # Order: previously picked bands first (by their saved rank), then the rest
    if user_predictions:
        picked = sorted(
            [e for e in entries if e.id in user_predictions],
            key=lambda e: user_predictions[e.id]
        )
        unpicked = [e for e in entries if e.id not in user_predictions]
        ordered_entries = picked + unpicked
    else:
        ordered_entries = entries

    return render_template(
        'predict_results.html',
        event=event,
        entries=ordered_entries,
        user_predictions=user_predictions,
        ba_count=ba_count,
    )


@app.route('/event/<int:event_id>/vote', methods=['GET', 'POST'])
@login_required
def audience_vote(event_id):
    event = Event.query.get_or_404(event_id)

    # Load all votes for admin view
    all_votes = None
    if current_user.is_authenticated and current_user.has_admin_access():
        all_votes = AudienceVote.query.filter_by(event_id=event_id).order_by(AudienceVote.updated_at.desc()).all()

    if event.voting_closed:
        return render_template('audience_vote.html', event=event, entries=[], existing_vote=None, voting_locked=False, voting_closed=True, all_votes=all_votes)

    if datetime.utcnow() < event.event_date:
        return render_template('audience_vote.html', event=event, entries=[], existing_vote=None, voting_locked=True, voting_closed=False, all_votes=all_votes)

    entries = EventCompetitionEntry.query.filter_by(event_id=event_id).order_by(
        EventCompetitionEntry.band_name
    ).all()
    existing_vote = AudienceVote.query.filter_by(
        event_id=event_id, user_id=current_user.id
    ).first()

    if request.method == 'POST':
        entry_id_raw = request.form.get('entry_id', '')
        try:
            entry_id = int(entry_id_raw)
        except (ValueError, TypeError):
            flash('Please select a band to vote for.', 'warning')
            return render_template('audience_vote.html', event=event, entries=entries, existing_vote=existing_vote, voting_locked=False, voting_closed=False, all_votes=all_votes)

        entry = EventCompetitionEntry.query.get(entry_id)
        if not entry or entry.event_id != event_id:
            flash('Invalid band selection.', 'danger')
            return render_template('audience_vote.html', event=event, entries=entries, existing_vote=existing_vote, voting_locked=False, voting_closed=False, all_votes=all_votes)

        if existing_vote:
            existing_vote.entry_id = entry_id
            existing_vote.updated_at = datetime.utcnow()
        else:
            db.session.add(AudienceVote(
                event_id=event_id,
                user_id=current_user.id,
                entry_id=entry_id,
            ))

        db.session.commit()
        flash('Your vote has been saved!', 'success')
        return redirect(url_for('view_event', event_id=event_id))

    return render_template('audience_vote.html', event=event, entries=entries, existing_vote=existing_vote, voting_locked=False, voting_closed=False, all_votes=all_votes)

@app.route('/admin/programme/<int:entry_id>/facebook-share', methods=['POST'])
@admin_required
def update_programme_facebook(entry_id):
    entry = ProgrammeEntry.query.get_or_404(entry_id)
    success, message = share_programme_to_facebook(entry)
    if success:
        flash(message, 'success')
    else:
        flash(f'Facebook update failed: {message}', 'warning')
    return redirect(request.referrer or url_for('edit_programme', entry_id=entry.id))


@app.route('/admin/news/<int:news_id>/facebook-share', methods=['POST'])
@admin_required
def update_news_facebook(news_id):
    item = NewsItem.query.get_or_404(news_id)
    force_new = request.form.get('force_new') == '1'
    success, message, _ = share_news_to_facebook(item, force_new=force_new)
    if success:
        flash(message, 'success')
    else:
        flash(f'Facebook update failed: {message}', 'warning')
    return redirect(request.referrer or url_for('edit_news', news_id=item.id))

@app.route('/story/<int:story_id>/react', methods=['POST'])
@login_required
def react_to_story(story_id):
    story = Story.query.get_or_404(story_id)
    payload = request.get_json(silent=True) or {}
    reaction_type = payload.get('reaction', '').strip().lower()

    if reaction_type not in REACTION_TYPES:
        return jsonify({'success': False, 'error': 'Invalid reaction type'}), 400

    existing = StoryReaction.query.filter_by(story_id=story.id, user_id=current_user.id).first()
    user_reaction = reaction_type

    if existing and existing.reaction_type == reaction_type:
        db.session.delete(existing)
        user_reaction = None
    else:
        if existing:
            existing.reaction_type = reaction_type
        else:
            db.session.add(StoryReaction(story_id=story.id, user_id=current_user.id, reaction_type=reaction_type))

    db.session.commit()

    counts = empty_reaction_counts()
    for reaction in StoryReaction.query.filter_by(story_id=story.id).all():
        if reaction.reaction_type in REACTION_TYPES:
            counts[reaction.reaction_type] += 1

    # Notify story creator only when a reaction was added (not removed)
    try:
        if user_reaction and story.creator and story.creator.id != current_user.id:
            create_notification(
                recipient_id=story.creator.id,
                actor_id=current_user.id,
                verb=f'reacted:{user_reaction}',
                target_type='story',
                target_id=story.id,
            )
        # Also notify admins/contributors (except actor and creator)
        admins_and_contribs = User.query.filter(
            (User.is_admin.is_(True)) | (User.role == 'contributor')
        ).all()
        for u in admins_and_contribs:
            if u.id in (current_user.id, getattr(story.creator, 'id', None)):
                continue
            if user_reaction:  # only notify on add/change
                create_notification(
                    recipient_id=u.id,
                    actor_id=current_user.id,
                    verb=f'reacted:{user_reaction}',
                    target_type='story',
                    target_id=story.id,
                )
    except Exception:
        app.logger.exception('Notification creation failed for story reaction')

    return jsonify({'success': True, 'counts': counts, 'user_reaction': user_reaction})

@app.route('/comment/<int:comment_id>/react', methods=['POST'])
@login_required
def react_to_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    payload = request.get_json(silent=True) or {}
    reaction_type = payload.get('reaction', '').strip().lower()

    if reaction_type not in REACTION_TYPES:
        return jsonify({'success': False, 'error': 'Invalid reaction type'}), 400

    existing = CommentReaction.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()
    user_reaction = reaction_type

    if existing and existing.reaction_type == reaction_type:
        db.session.delete(existing)
        user_reaction = None
    else:
        if existing:
            existing.reaction_type = reaction_type
        else:
            db.session.add(CommentReaction(comment_id=comment.id, user_id=current_user.id, reaction_type=reaction_type))

    db.session.commit()

    counts = empty_reaction_counts()
    for reaction in CommentReaction.query.filter_by(comment_id=comment.id).all():
        if reaction.reaction_type in REACTION_TYPES:
            counts[reaction.reaction_type] += 1

    # Notify comment owner when reaction added
    try:
        if user_reaction and comment.user and comment.user.id != current_user.id:
            create_notification(
                recipient_id=comment.user.id,
                actor_id=current_user.id,
                verb=f'reacted:{user_reaction}',
                target_type='comment',
                target_id=comment.id,
            )
        # Notify admins/contributors as well
        admins_and_contribs = User.query.filter(
            (User.is_admin.is_(True)) | (User.role == 'contributor')
        ).all()
        for u in admins_and_contribs:
            if u.id in (current_user.id, getattr(comment.user, 'id', None)):
                continue
            if user_reaction:
                create_notification(
                    recipient_id=u.id,
                    actor_id=current_user.id,
                    verb=f'reacted:{user_reaction}',
                    target_type='comment',
                    target_id=comment.id,
                )
    except Exception:
        app.logger.exception('Notification creation failed for comment reaction')

    return jsonify({'success': True, 'counts': counts, 'user_reaction': user_reaction})

# Admin Routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    events = Event.query.order_by(Event.event_date.desc()).all()
    news_items = NewsItem.query.order_by(NewsItem.published_at.desc()).all()
    invitations = AdminInvitation.query.filter_by(is_used=False).filter(
        AdminInvitation.expires_at > datetime.utcnow()
    ).order_by(AdminInvitation.created_at.desc()).all()
    return render_template('admin/dashboard.html', events=events, news_items=news_items, invitations=invitations)


@app.route('/notifications')
@login_required
def notifications():
    notes = Notification.query.filter_by(recipient_id=current_user.id).order_by(Notification.created_at.desc()).all()
    for n in notes:
        n.target_url = None
        if n.target_type == 'story' and n.target_id:
            story = Story.query.get(n.target_id)
            if story:
                n.target_url = url_for('view_event', event_id=story.event_id) + f'#story-{n.target_id}'
        elif n.target_type == 'comment' and n.target_id:
            comment = Comment.query.get(n.target_id)
            if comment and comment.story:
                n.target_url = url_for('view_event', event_id=comment.story.event_id) + f'#story-{comment.story_id}'
    return render_template('notifications.html', notifications=notes)


@app.route('/notifications/mark-read', methods=['POST'])
@login_required
def mark_notifications_read():
    try:
        Notification.query.filter_by(recipient_id=current_user.id, is_read=False).update({'is_read': True})
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to mark notifications read')
    return redirect(request.referrer or url_for('notifications'))


@app.route('/notifications/<int:note_id>/read', methods=['POST'])
@login_required
def mark_notification_read(note_id):
    note = Notification.query.get_or_404(note_id)
    if note.recipient_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    note.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/notifications/recent')
@login_required
def notifications_recent():
    notes = Notification.query.filter_by(recipient_id=current_user.id).order_by(Notification.created_at.desc()).limit(6).all()
    unread_count = Notification.query.filter_by(recipient_id=current_user.id, is_read=False).count()
    result = []
    for n in notes:
        event_id = None
        if n.target_type == 'story' and n.target_id:
            story = Story.query.get(n.target_id)
            if story:
                event_id = story.event_id
        result.append({
            'id': n.id,
            'actor': n.actor.username if n.actor else 'System',
            'verb': n.verb,
            'target_type': n.target_type,
            'target_id': n.target_id,
            'event_id': event_id,
            'data': n.data,
            'is_read': bool(n.is_read),
            'created_at': n.created_at.replace(tzinfo=UTC_TZ).astimezone(LONDON_TZ).strftime('%Y-%m-%dT%H:%M:%S')
        })
    return jsonify({'unread_count': unread_count, 'notifications': result})

@app.route('/admin/test-email', methods=['POST'])
@admin_required
def admin_test_email():
    recipient = request.form.get('test_email', '').strip()
    if not recipient:
        flash('Please enter an email address for the test message.', 'warning')
        return redirect(url_for('admin_dashboard'))

    html_content = f"""
<p><strong>Brassing Around admin email test</strong></p>
<p>This confirms Brevo API delivery from the live site.</p>
<p><strong>Sent at (UTC):</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p><strong>Triggered by admin:</strong> {html.escape(current_user.username)}</p>
<p><strong>Configured sender:</strong> {html.escape(app.config.get('BREVO_FROM_EMAIL', ''))}</p>
"""
    text_content = (
        "Brassing Around admin email test\n\n"
        "This confirms Brevo API delivery from the live site.\n"
        f"Sent at (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Triggered by admin: {current_user.username}\n"
        f"Configured sender: {app.config.get('BREVO_FROM_EMAIL', '')}\n"
    )

    sent = brevo_send_email(
        subject='Brassing Around email test',
        html_content=html_content,
        text_content=text_content,
        recipients=[recipient],
    )

    if sent:
        flash(f'Test email sent to {recipient}.', 'success')
    else:
        flash('Test email failed. Check Heroku logs for Brevo status/response details.', 'danger')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/marketing/recipients', methods=['GET', 'POST'])
@admin_required
def admin_marketing_recipients():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        if not email:
            flash('Please provide an email address.', 'warning')
            return redirect(url_for('admin_marketing_recipients'))
        existing = MarketingListEntry.query.filter_by(email=email).first()
        if existing:
            existing.active = True
            existing.name = name or existing.name
            db.session.commit()
            flash(f'{email} updated in marketing list.', 'success')
            return redirect(url_for('admin_marketing_recipients'))
        entry = MarketingListEntry(email=email, name=name, added_by=current_user.id)
        db.session.add(entry)
        db.session.commit()
        flash(f'Added {email} to marketing list.', 'success')
        return redirect(url_for('admin_marketing_recipients'))

    entries = MarketingListEntry.query.order_by(MarketingListEntry.added_at.desc()).all()
    opted_users = User.query.filter_by(marketing_opt_in=True).order_by(User.created_at.desc()).all()

    # Build a combined list for display: source='user' or 'list'
    combined = []
    for u in opted_users:
        combined.append({
            'source': 'user',
            'id': u.id,
            'email': u.email,
            'name': u.username,
            'added_at': u.created_at,
            'active': True
        })
    for e in entries:
        combined.append({
            'source': 'list',
            'id': e.id,
            'email': e.email,
            'name': e.name,
            'added_at': e.added_at,
            'active': e.active
        })

    return render_template('admin/marketing_recipients.html', entries=combined)


@app.route('/admin/marketing/recipients/<int:entry_id>/remove', methods=['POST'])
@admin_required
def admin_marketing_remove_recipient(entry_id):
    entry = MarketingListEntry.query.get_or_404(entry_id)
    entry.active = False
    db.session.commit()
    flash(f'Removed {entry.email} from marketing list.', 'success')
    return redirect(url_for('admin_marketing_recipients'))


@app.route('/admin/marketing/recipients/user/<int:user_id>/remove', methods=['POST'])
@admin_required
def admin_marketing_remove_user_optin(user_id):
    user = User.query.get_or_404(user_id)
    user.marketing_opt_in = False
    db.session.commit()
    flash(f'Updated marketing preference for {user.email}.', 'success')
    return redirect(url_for('admin_marketing_recipients'))


@app.route('/admin/marketing/compose', methods=['GET', 'POST'])
@admin_required
def admin_marketing_compose():
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        html_content = request.form.get('html_content', '').strip()
        manual_recipients = request.form.get('manual_recipients', '').strip()
        send_to_opted_in = bool(request.form.get('send_to_opted_in'))

        if not subject or not html_content:
            flash('Subject and content are required.', 'warning')
            return redirect(url_for('admin_marketing_compose'))

        recipients = set()
        if send_to_opted_in:
            for u in User.query.filter_by(marketing_opt_in=True).all():
                if u.email:
                    recipients.add(u.email.lower())

        for e in MarketingListEntry.query.filter_by(active=True).all():
            recipients.add(e.email.lower())

        if manual_recipients:
            for part in re.split(r'[\n,;]+', manual_recipients):
                addr = part.strip().lower()
                if addr:
                    recipients.add(addr)

        recipients = sorted(recipients)
        if not recipients:
            flash('No recipients selected.', 'warning')
            return redirect(url_for('admin_marketing_compose'))

        sent_count = 0
        # Send individualized messages so unsubscribe link is unique per recipient
        for email in recipients:
            token = generate_marketing_token(email)
            unsubscribe_url = url_for('marketing_unsubscribe', token=token, _external=True)
            personalised_html = html_content + f"<hr><p style=\"font-size:0.8rem;color:#666;\">If you'd like to stop receiving marketing emails, <a href=\"{unsubscribe_url}\">click here to unsubscribe</a>.</p>"
            text_content = re.sub(r'<[^>]+>', '', personalised_html)
            ok = brevo_send_email(subject, personalised_html, text_content, [email])
            if ok:
                sent_count += 1

        # Record sent email
        sent = SentMarketingEmail(
            subject=subject,
            html_content=html_content,
            text_content=re.sub(r'<[^>]+>', '', html_content),
            created_by=current_user.id,
            recipients_count=sent_count,
            recipients_summary=','.join(recipients[:200])
        )
        db.session.add(sent)
        db.session.commit()
        flash(f'Sent marketing email to {sent_count} recipients.', 'success')
        return redirect(url_for('admin_marketing_sent'))

    return render_template('admin/marketing_compose.html')


@app.route('/admin/marketing/sent')
@admin_required
def admin_marketing_sent():
    sent_items = SentMarketingEmail.query.order_by(SentMarketingEmail.created_at.desc()).all()
    return render_template('admin/marketing_sent.html', items=sent_items)


@app.route('/marketing/unsubscribe/<token>')
def marketing_unsubscribe(token):
    ok, result = verify_marketing_token(token)
    if not ok:
        if result == 'expired':
            return render_template('marketing_unsubscribe.html', status='expired')
        return render_template('marketing_unsubscribe.html', status='invalid')

    email = result
    # Prefer updating registered user
    user = User.query.filter_by(email=email).first()
    if user:
        user.marketing_opt_in = False
        db.session.commit()
        return render_template('marketing_unsubscribe.html', status='unsubscribed', email=email)

    entry = MarketingListEntry.query.filter_by(email=email).first()
    if entry:
        entry.active = False
        db.session.commit()
        return render_template('marketing_unsubscribe.html', status='unsubscribed', email=email)

    return render_template('marketing_unsubscribe.html', status='not_found')


@app.route('/admin/marketing/upload', methods=['POST'])
@admin_required
def admin_marketing_upload():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    filename = file.filename.lower()
    ext = filename.rsplit('.', 1)[1] if '.' in filename else ''
    video_exts = {'mp4', 'webm', 'mov', 'avi', 'm4v'}
    resource_type = 'video' if ext in video_exts else 'image'

    try:
        if is_cloudinary_configured():
            result = cloudinary.uploader.upload(
                file,
                folder='brassing_around/marketing',
                resource_type=resource_type
            )
            url = result.get('secure_url') or result.get('url')
        else:
            # Local fallback: save to uploads (images only)
            if resource_type == 'image':
                saved = save_uploaded_image(file, 'marketing')
                if not saved:
                    return jsonify({'success': False, 'error': 'Invalid image type'}), 400
                url = url_for('static', filename='uploads/' + saved, _external=True)
            else:
                # Save video locally
                filename_secure = secure_filename(file.filename)
                unique_filename = f"marketing_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename_secure}"
                path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(path)
                url = url_for('static', filename='uploads/' + unique_filename, _external=True)

        if not url:
            return jsonify({'success': False, 'error': 'Upload failed'}), 500
        return jsonify({'success': True, 'url': url})
    except Exception:
        app.logger.exception('Marketing upload failed')
        return jsonify({'success': False, 'error': 'Upload error'}), 500

@app.route('/admin/db-preview')
@admin_required
def admin_db_preview():
    backend_label = get_database_backend_label()
    settings = SiteSettings.query.first()
    masked_token = ''

    if settings and settings.facebook_access_token:
        token = settings.facebook_access_token
        if len(token) <= 8:
            masked_token = '*' * len(token)
        else:
            masked_token = f'{token[:4]}...{token[-4:]}'

    counts = {
        'users': User.query.count(),
        'events': Event.query.count(),
        'stories': Story.query.count(),
        'comments': Comment.query.count(),
        'photos': Photo.query.count(),
        'gallery_photos': GalleryPhoto.query.count(),
        'profiles': Profile.query.count(),
        'programme_entries': ProgrammeEntry.query.count(),
        'news_items': NewsItem.query.count(),
        'invitations': AdminInvitation.query.count(),
        'story_reactions': StoryReaction.query.count(),
        'comment_reactions': CommentReaction.query.count(),
    }

    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    recent_events = Event.query.order_by(Event.created_at.desc()).limit(10).all()
    recent_stories = Story.query.order_by(Story.timestamp.desc()).limit(10).all()
    recent_comments = Comment.query.order_by(Comment.created_at.desc()).limit(10).all()
    recent_profiles = Profile.query.order_by(Profile.updated_at.desc()).limit(10).all()
    recent_programmes = ProgrammeEntry.query.order_by(ProgrammeEntry.updated_at.desc()).limit(10).all()
    recent_news = NewsItem.query.order_by(NewsItem.updated_at.desc()).limit(10).all()

    return render_template(
        'admin/db_preview.html',
        counts=counts,
        database_backend=backend_label,
        database_schema=app.config.get('APP_DB_SCHEMA') or 'public',
        using_ephemeral_database=is_heroku_dyno and backend_label == 'SQLite',
        settings=settings,
        masked_facebook_token=masked_token,
        recent_users=recent_users,
        recent_events=recent_events,
        recent_stories=recent_stories,
        recent_comments=recent_comments,
        recent_profiles=recent_profiles,
        recent_programmes=recent_programmes,
        recent_news=recent_news,
    )

@app.route('/admin/invite', methods=['POST'])
@admin_required
def create_invitation():
    days_valid = int(request.form.get('days_valid', 7))
    role = request.form.get('role', 'admin')  # Get role from form
    invite_email = request.form.get('invite_email', '').strip()
    code = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=days_valid)
    
    invitation = AdminInvitation(
        code=code,
        created_by=current_user.id,
        expires_at=expires_at,
        role=role
    )
    db.session.add(invitation)
    db.session.commit()
    
    role_name = 'Admin' if role == 'admin' else 'Contributor'
    if invite_email:
        email_sent = send_invitation_email(invite_email, invitation, current_user.username)
        if email_sent:
            flash(f'{role_name} invitation created and emailed to {invite_email}.', 'success')
        else:
            flash(f'{role_name} invitation created, but the email to {invite_email} could not be sent.', 'warning')
    else:
        flash(f'{role_name} invitation code created successfully!', 'success')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/invite/<int:invite_id>/revoke', methods=['POST'])
@admin_required
def revoke_invitation(invite_id):
    invitation = AdminInvitation.query.get_or_404(invite_id)
    invitation.expires_at = datetime.utcnow()  # Expire it immediately
    db.session.commit()
    
    flash('Invitation revoked successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/event/create', methods=['GET', 'POST'])
@admin_required
def create_event():
    if request.method == 'POST':
        title = request.form.get('title')
        description = sanitize_story_content(request.form.get('description'))
        event_date = datetime.strptime(request.form.get('event_date'), '%Y-%m-%dT%H:%M')
        location = request.form.get('location')
        
        event = Event(
            title=title,
            description=description,
            event_date=event_date,
            location=location,
            tickets_info_url=normalize_external_url(request.form.get('tickets_info_url')),
            livestream_url=normalize_external_url(request.form.get('livestream_url')),
            youtube_embed_url=parse_youtube_embed_from_form('youtube_url'),
            created_by=current_user.id
        )
        
        # Handle event photo upload
        if 'event_photo' in request.files:
            uploaded = save_uploaded_image(request.files['event_photo'], 'event')
            if uploaded:
                event.event_photo = uploaded
        
        db.session.add(event)
        db.session.commit()

        # Auto-share event announcement if enabled
        settings = SiteSettings.query.first()
        if 'share_to_facebook' in request.form:
            success, message, _ = share_event_announcement(event)
            if success:
                flash('Event created and shared to Facebook!', 'success')
            else:
                flash(f'Event created. (Facebook share failed: {message})', 'warning')
        elif settings and settings.auto_share_events:
            success, message, _ = share_event_announcement(event)
            if success:
                flash('Event created and shared to Facebook!', 'success')
            else:
                flash(f'Event created. (Facebook share failed: {message})', 'warning')
        else:
            flash('Event created successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/create_event.html')

@app.route('/admin/event/<int:event_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    
    if request.method == 'POST':
        event.title = request.form.get('title')
        event.description = sanitize_story_content(request.form.get('description'))
        event.event_date = datetime.strptime(request.form.get('event_date'), '%Y-%m-%dT%H:%M')
        event.location = request.form.get('location')
        event.tickets_info_url = normalize_external_url(request.form.get('tickets_info_url'))
        event.livestream_url = normalize_external_url(request.form.get('livestream_url'))
        event.youtube_embed_url = parse_youtube_embed_from_form('youtube_url')

        try:
            event.ba_favourites_count = max(1, int(request.form.get('ba_favourites_count') or 6))
        except (ValueError, TypeError):
            event.ba_favourites_count = 6
        try:
            event.audience_top_n = max(1, int(request.form.get('audience_top_n') or 3))
        except (ValueError, TypeError):
            event.audience_top_n = 3

        # Handle event photo upload
        if 'event_photo' in request.files:
            uploaded = save_uploaded_image(request.files['event_photo'], 'event')
            if uploaded:
                delete_uploaded_image(event.event_photo)
                event.event_photo = uploaded
        
        db.session.commit()
        if 'share_to_facebook' in request.form:
            success, message, _ = share_event_announcement(event)
            if success:
                flash('Event updated and shared to Facebook!', 'success')
            else:
                flash(f'Event updated. (Facebook share failed: {message})', 'warning')
        else:
            flash('Event updated successfully!', 'success')
        return redirect(url_for('admin_event_detail', event_id=event.id))
    
    return render_template('admin/edit_event.html', event=event)

@app.route('/admin/event/<int:event_id>')
@admin_required
def admin_event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    stories = Story.query.filter_by(event_id=event_id).order_by(Story.timestamp.desc()).all()
    votes = AudienceVote.query.filter_by(event_id=event_id).order_by(AudienceVote.updated_at.desc()).all()
    return render_template('admin/event_detail.html', event=event, stories=stories, votes=votes)

@app.route('/admin/event/<int:event_id>/toggle-voting', methods=['POST'])
@admin_required
def admin_toggle_voting(event_id):
    event = Event.query.get_or_404(event_id)
    event.voting_closed = not event.voting_closed
    db.session.commit()
    state = 'closed' if event.voting_closed else 're-opened'
    flash(f'Audience voting has been {state}.', 'success')
    return redirect(url_for('admin_event_detail', event_id=event_id))


@app.route('/admin/vote/<int:vote_id>/delete', methods=['POST'])
@admin_required
def admin_delete_vote(vote_id):
    vote = AudienceVote.query.get_or_404(vote_id)
    event_id = vote.event_id
    db.session.delete(vote)
    db.session.commit()
    flash('Vote deleted.', 'success')
    return redirect(url_for('admin_event_detail', event_id=event_id))


@app.route('/admin/event/<int:event_id>/delete', methods=['POST'])
@admin_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Event deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/event/<int:event_id>/facebook-share', methods=['POST'])
@admin_required
def admin_share_event_facebook(event_id):
    event = Event.query.get_or_404(event_id)
    success, message, _ = share_event_announcement(event)
    if success:
        flash(message, 'success')
    else:
        flash(f'Facebook share failed: {message}', 'warning')
    return redirect(request.referrer or url_for('admin_event_detail', event_id=event.id))


@app.route('/admin/event/<int:event_id>/facebook-share-favourites', methods=['POST'])
@admin_required
def admin_share_event_favourites(event_id):
    event = Event.query.get_or_404(event_id)
    success, message, _ = share_ba_favourites_to_facebook(event)
    if success:
        flash('BA favourites shared to Facebook!', 'success')
    else:
        flash(f'Favourites share failed: {message}', 'warning')
    return redirect(request.referrer or url_for('admin_event_detail', event_id=event.id))

@app.route('/admin/event/<int:event_id>/duplicate', methods=['POST'])
@admin_required
def duplicate_event(event_id):
    source_event = Event.query.get_or_404(event_id)

    duplicated_event = Event(
        title=f'{source_event.title} (Copy)',
        description=source_event.description,
        event_date=source_event.event_date,
        location=source_event.location,
        tickets_info_url=source_event.tickets_info_url,
        livestream_url=source_event.livestream_url,
        youtube_embed_url=source_event.youtube_embed_url,
        event_photo=source_event.event_photo,
        created_by=current_user.id,
    )

    db.session.add(duplicated_event)
    db.session.commit()

    flash('Event copied successfully. Update the new draft below.', 'success')
    return redirect(url_for('edit_event', event_id=duplicated_event.id))


@app.route('/admin/news')
@admin_required
def manage_news():
    items = NewsItem.query.order_by(NewsItem.published_at.desc()).all()
    return render_template('admin/manage_news.html', items=items)


@app.route('/admin/news/create', methods=['GET', 'POST'])
@admin_required
def create_news():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        content = sanitize_story_content(request.form.get('content'))
        published_raw = request.form.get('published_at', '').strip()

        if not title or not content:
            flash('Title and content are required.', 'danger')
            return redirect(url_for('create_news'))

        published_at = datetime.utcnow()
        if published_raw:
            try:
                published_at = datetime.strptime(published_raw, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Invalid publish date format.', 'danger')
                return redirect(url_for('create_news'))

        item = NewsItem(
            title=title,
            subtitle=subtitle or None,
            content=content,
            published_at=published_at,
            created_by=current_user.id,
        )

        if 'news_photo' in request.files:
            uploaded = save_uploaded_image(request.files['news_photo'], 'news')
            if uploaded:
                item.news_photo = uploaded
        elif request.form.get('news_photo_url', '').strip():
            item.news_photo = request.form['news_photo_url'].strip()

        db.session.add(item)
        db.session.commit()
        # Try to auto-share to Facebook if enabled
        settings = SiteSettings.query.first()
        if settings and settings.auto_share_news:
            success, message, _ = share_news_to_facebook(item)
            if success:
                flash('News item created and shared to Facebook!', 'success')
            else:
                flash(f'News item created. (Facebook share failed: {message})', 'warning')
        else:
            flash('News item created.', 'success')
        return redirect(url_for('manage_news'))

    return render_template('admin/create_news.html')


@app.route('/admin/news/<int:news_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_news(news_id):
    item = NewsItem.query.get_or_404(news_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subtitle = request.form.get('subtitle', '').strip()
        content = sanitize_story_content(request.form.get('content'))
        published_raw = request.form.get('published_at', '').strip()

        if not title or not content:
            flash('Title and content are required.', 'danger')
            return redirect(url_for('edit_news', news_id=item.id))

        try:
            item.published_at = datetime.strptime(published_raw, '%Y-%m-%dT%H:%M') if published_raw else datetime.utcnow()
        except ValueError:
            flash('Invalid publish date format.', 'danger')
            return redirect(url_for('edit_news', news_id=item.id))

        item.title = title
        item.subtitle = subtitle or None
        item.content = content

        if 'news_photo' in request.files:
            uploaded = save_uploaded_image(request.files['news_photo'], 'news')
            if uploaded:
                delete_uploaded_image(item.news_photo)
                item.news_photo = uploaded
        elif request.form.get('news_photo_url', '').strip():
            item.news_photo = request.form['news_photo_url'].strip()

        db.session.commit()
        if 'share_to_facebook' in request.form:
            success, message, _ = share_news_to_facebook(item)
            if success:
                flash('News item updated and shared to Facebook!', 'success')
            else:
                flash(f'News item updated. (Facebook share failed: {message})', 'warning')
        else:
            flash('News item updated.', 'success')
        return redirect(url_for('manage_news'))

    return render_template('admin/edit_news.html', item=item)


@app.route('/admin/news/<int:news_id>/delete', methods=['POST'])
@admin_required
def delete_news(news_id):
    item = NewsItem.query.get_or_404(news_id)
    delete_uploaded_image(item.news_photo)
    db.session.delete(item)
    db.session.commit()
    flash('News item deleted.', 'success')
    return redirect(url_for('manage_news'))

@app.route('/admin/programmes')
@admin_required
def manage_programmes():
    entries = ProgrammeEntry.query.order_by(ProgrammeEntry.section.asc(), ProgrammeEntry.entry_date.desc()).all()
    return render_template('admin/manage_programmes.html', entries=entries)

@app.route('/admin/programme/create', methods=['GET', 'POST'])
@admin_required
def create_programme():
    if request.method == 'POST':
        entry = ProgrammeEntry(
            title=request.form.get('title', '').strip(),
            entry_date=datetime.strptime(request.form.get('entry_date'), '%Y-%m-%d'),
            location=request.form.get('location', '').strip(),
            review=sanitize_story_content(request.form.get('review', '')),
            tickets_info_url=normalize_external_url(request.form.get('tickets_info_url')),
            livestream_url=normalize_external_url(request.form.get('livestream_url')),
            youtube_embed_url=parse_youtube_embed_from_form('youtube_url'),
            entry_type=request.form.get('entry_type', 'concert'),
            section=request.form.get('section', 'promotion'),
            created_by=current_user.id
        )

        if 'photo' in request.files:
            entry.photo = save_uploaded_image(request.files['photo'], 'programme')

        db.session.add(entry)
        db.session.flush()

        work_titles = request.form.getlist('work_title')
        composers = request.form.getlist('work_composer')
        arrangers = request.form.getlist('work_arranger')

        for idx, work_title in enumerate(work_titles):
            title = (work_title or '').strip()
            composer = (composers[idx] if idx < len(composers) else '').strip()
            arranger = (arrangers[idx] if idx < len(arrangers) else '').strip()
            if not title and not composer and not arranger:
                continue

            if not title:
                continue

            db.session.add(ProgrammeWork(
                programme_entry_id=entry.id,
                work_title=title,
                composer=composer,
                arranger=arranger,
                display_order=idx,
            ))

        db.session.commit()
        # Auto-share new programme entries if enabled
        settings = SiteSettings.query.first()
        if settings and settings.auto_share_programmes:
            success, message = share_programme_to_facebook(entry)
            if success:
                flash('Programme entry created and shared to Facebook!', 'success')
            else:
                flash(f'Programme entry created. (Facebook share failed: {message})', 'warning')
        else:
            flash('Programme entry created successfully!', 'success')
        return redirect(url_for('manage_programmes'))

    return render_template(
        'admin/create_programme.html',
        section=request.args.get('section', 'promotion'),
        entry_type=request.args.get('entry_type', 'concert')
    )

@app.route('/admin/programme/<int:entry_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_programme(entry_id):
    entry = ProgrammeEntry.query.get_or_404(entry_id)

    if request.method == 'POST':
        entry.title = request.form.get('title', '').strip()
        entry.entry_date = datetime.strptime(request.form.get('entry_date'), '%Y-%m-%d')
        entry.location = request.form.get('location', '').strip()
        entry.review = sanitize_story_content(request.form.get('review', ''))
        entry.tickets_info_url = normalize_external_url(request.form.get('tickets_info_url'))
        entry.livestream_url = normalize_external_url(request.form.get('livestream_url'))
        entry.youtube_embed_url = parse_youtube_embed_from_form('youtube_url')
        entry.entry_type = request.form.get('entry_type', 'concert')
        entry.section = request.form.get('section', 'promotion')

        if 'photo' in request.files:
            uploaded_photo = save_uploaded_image(request.files['photo'], 'programme')
            if uploaded_photo:
                delete_uploaded_image(entry.photo)
                entry.photo = uploaded_photo

        ProgrammeWork.query.filter_by(programme_entry_id=entry.id).delete()

        work_titles = request.form.getlist('work_title')
        composers = request.form.getlist('work_composer')
        arrangers = request.form.getlist('work_arranger')

        for idx, work_title in enumerate(work_titles):
            title = (work_title or '').strip()
            composer = (composers[idx] if idx < len(composers) else '').strip()
            arranger = (arrangers[idx] if idx < len(arrangers) else '').strip()
            if not title and not composer and not arranger:
                continue

            if not title:
                continue

            db.session.add(ProgrammeWork(
                programme_entry_id=entry.id,
                work_title=title,
                composer=composer,
                arranger=arranger,
                display_order=idx,
            ))

        db.session.commit()
        if 'share_to_facebook' in request.form:
            success, message = share_programme_to_facebook(entry)
            if success:
                flash('Programme entry updated and shared to Facebook!', 'success')
            else:
                flash(f'Programme entry updated. (Facebook share failed: {message})', 'warning')
        else:
            flash('Programme entry updated successfully!', 'success')
        return redirect(url_for('manage_programmes'))

    return render_template('admin/edit_programme.html', entry=entry)

@app.route('/admin/programme/<int:entry_id>/delete', methods=['POST'])
@admin_required
def delete_programme(entry_id):
    entry = ProgrammeEntry.query.get_or_404(entry_id)
    delete_uploaded_image(entry.photo)
    db.session.delete(entry)
    db.session.commit()
    flash('Programme entry deleted successfully!', 'success')
    return redirect(url_for('manage_programmes'))

@app.route('/admin/programme/<int:entry_id>/duplicate', methods=['POST'])
@admin_required
def duplicate_programme(entry_id):
    source_entry = ProgrammeEntry.query.get_or_404(entry_id)

    duplicated_entry = ProgrammeEntry(
        title=f'{source_entry.title} (Copy)',
        entry_date=source_entry.entry_date,
        location=source_entry.location,
        review=source_entry.review,
        tickets_info_url=source_entry.tickets_info_url,
        livestream_url=source_entry.livestream_url,
        youtube_embed_url=source_entry.youtube_embed_url,
        photo=source_entry.photo,
        entry_type=source_entry.entry_type,
        section=source_entry.section,
        created_by=current_user.id,
    )

    db.session.add(duplicated_entry)
    db.session.flush()

    source_works = ProgrammeWork.query.filter_by(programme_entry_id=source_entry.id).order_by(ProgrammeWork.display_order.asc()).all()
    for work in source_works:
        db.session.add(ProgrammeWork(
            programme_entry_id=duplicated_entry.id,
            work_title=work.work_title,
            composer=work.composer,
            arranger=work.arranger,
            display_order=work.display_order,
        ))

    db.session.commit()

    flash('Concert/archive entry copied successfully. Update the new draft below.', 'success')
    return redirect(url_for('edit_programme', entry_id=duplicated_entry.id))

@app.route('/admin/story/create/<int:event_id>', methods=['GET', 'POST'])
@contributor_required
def create_story(event_id):
    event = Event.query.get_or_404(event_id)
    prefilled_title = request.args.get('prefill_title', '').strip()
    selected_entry = None
    selected_entry_id = request.args.get('competition_entry_id', '').strip()

    if selected_entry_id.isdigit():
        selected_entry = EventCompetitionEntry.query.filter_by(id=int(selected_entry_id), event_id=event_id).first()
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        timestamp_str = request.form.get('timestamp')
        selected_entry = None

        competition_entry_id = request.form.get('competition_entry_id', '').strip()
        if competition_entry_id.isdigit():
            selected_entry = EventCompetitionEntry.query.filter_by(id=int(competition_entry_id), event_id=event_id).first()
        
        if timestamp_str:
            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M')
        else:
            timestamp = datetime.utcnow()
        
        story = Story(
            event_id=event_id,
            competition_entry_id=selected_entry.id if selected_entry else None,
            title=title,
            content=sanitize_story_content(content),
            timestamp=timestamp,
            created_by=current_user.id
        )
        db.session.add(story)
        db.session.flush()  # Get story.id before committing
        
        # Handle photo uploads
        if 'photos' in request.files:
            files = request.files.getlist('photos')
            for file in files:
                saved = save_uploaded_image(file, 'story')
                if saved:
                    photo = Photo(
                        story_id=story.id,
                        filename=saved,
                        caption=request.form.get(f'caption_{file.filename}', '')
                    )
                    db.session.add(photo)
        
        db.session.commit()
        
        # Try to share to Facebook if auto-sharing is enabled
        if settings := SiteSettings.query.first():
            if settings.auto_share_stories:
                success, message = share_story_to_facebook(story, event, require_auto_share=True)
                if success:
                    flash('Post created and shared to Facebook!', 'success')
                else:
                    flash(f'Post created successfully! (Facebook share failed: {message})', 'warning')
            else:
                flash('Post created successfully!', 'success')
        else:
            flash('Post created successfully!', 'success')

        # Notify admins and contributors about new story (except the creator)
        try:
            admins_and_contribs = User.query.filter(
                (User.is_admin.is_(True)) | (User.role == 'contributor')
            ).all()
            for u in admins_and_contribs:
                if u.id == current_user.id:
                    continue
                create_notification(
                    recipient_id=u.id,
                    actor_id=current_user.id,
                    verb='posted',
                    target_type='story',
                    target_id=story.id,
                    data=story.title,
                )
        except Exception:
            app.logger.exception('Notification creation failed for new story')
        
        return redirect(url_for('admin_event_detail', event_id=event_id))
    
    return render_template(
        'admin/create_story.html',
        event=event,
        prefilled_title=prefilled_title,
        competition_entry=selected_entry,
    )

@app.route('/admin/story/<int:story_id>/edit', methods=['GET', 'POST'])
@contributor_required
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)
    
    if request.method == 'POST':
        story.title = request.form.get('title')
        story.content = sanitize_story_content(request.form.get('content'))
        timestamp_str = request.form.get('timestamp')
        
        if timestamp_str:
            story.timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M')
        
        # Handle new photo uploads
        if 'photos' in request.files:
            files = request.files.getlist('photos')
            for file in files:
                saved = save_uploaded_image(file, 'story')
                if saved:
                    photo = Photo(
                        story_id=story.id,
                        filename=saved
                    )
                    db.session.add(photo)
        
        db.session.commit()
        if 'share_to_facebook' in request.form:
            success, message = share_story_to_facebook(story, story.event)
            if success:
                flash(f'Post updated and {message.lower()}!', 'success')
            else:
                flash(f'Post updated, but Facebook publish failed: {message}', 'warning')
        else:
            flash('Post updated successfully!', 'success')
        return redirect(url_for('admin_event_detail', event_id=story.event_id))
    
    return render_template('admin/edit_story.html', story=story)

@app.route('/admin/story/<int:story_id>/facebook-share', methods=['POST'])
@contributor_required
def update_story_facebook(story_id):
    story = Story.query.get_or_404(story_id)
    success, message = share_story_to_facebook(story, story.event)
    if success:
        flash(message, 'success')
    else:
        flash(f'Facebook update failed: {message}', 'warning')
    return redirect(request.referrer or url_for('edit_story', story_id=story.id))

@app.route('/event/<int:event_id>/competition/facebook-share', methods=['POST'])
@contributor_required
def update_competition_facebook(event_id):
    event = Event.query.get_or_404(event_id)
    success, message = share_competition_to_facebook(event, current_user.username)
    if success:
        flash(message, 'success')
    else:
        flash(f'Facebook update failed: {message}', 'warning')
    return redirect(request.referrer or url_for('manage_event_competition', event_id=event.id))

@app.route('/admin/story/<int:story_id>/delete', methods=['POST'])
@contributor_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    event_id = story.event_id
    
    # Delete associated photos from filesystem
    for photo in story.photos:
        delete_uploaded_image(photo.filename)
    
    db.session.delete(story)
    db.session.commit()
    flash('Post deleted successfully!', 'success')
    return redirect(url_for('admin_event_detail', event_id=event_id))

@app.route('/admin/photo/<int:photo_id>/delete', methods=['POST'])
@admin_required
def delete_photo(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    story_id = photo.story_id
    
    # Delete file from filesystem
    delete_uploaded_image(photo.filename)
    
    db.session.delete(photo)
    db.session.commit()
    
    return jsonify({'success': True})

# Profile Management Routes
@app.route('/admin/profiles')
@admin_required
def manage_profiles():
    profiles = Profile.query.order_by(Profile.display_order.desc(), Profile.name).all()
    return render_template('admin/manage_profiles.html', profiles=profiles)

@app.route('/admin/gallery')
@admin_required
def manage_gallery():
    """Admin gallery management page with album organization tools."""
    albums = GalleryAlbum.query.order_by(GalleryAlbum.display_order.desc(), GalleryAlbum.created_at.desc()).all()
    photos = GalleryPhoto.query.order_by(GalleryPhoto.display_order.desc(), GalleryPhoto.created_at.desc()).all()
    return render_template('admin/manage_gallery.html', photos=photos, albums=albums)


@app.route('/admin/gallery/albums/create', methods=['POST'])
@admin_required
def create_gallery_album():
    name = request.form.get('name', '').strip()
    caption = request.form.get('caption', '').strip()
    display_order = parse_optional_int(request.form.get('display_order', ''))

    if not name:
        flash('Album name is required.', 'danger')
        return redirect(url_for('manage_gallery'))

    existing = GalleryAlbum.query.filter(db.func.lower(GalleryAlbum.name) == name.lower()).first()
    if existing:
        flash('An album with that name already exists.', 'warning')
        return redirect(url_for('manage_gallery'))

    album = GalleryAlbum(
        name=name,
        caption=caption or None,
        display_order=display_order if display_order is not None else 0,
    )
    db.session.add(album)
    db.session.commit()
    flash('Album created.', 'success')
    return redirect(url_for('manage_gallery'))


@app.route('/admin/gallery/albums/<int:album_id>/delete', methods=['POST'])
@admin_required
def delete_gallery_album(album_id):
    album = GalleryAlbum.query.get_or_404(album_id)

    # Keep photos but move them out of the deleted album.
    GalleryPhoto.query.filter_by(album_id=album.id).update({GalleryPhoto.album_id: None}, synchronize_session=False)
    db.session.delete(album)
    db.session.commit()
    flash('Album deleted. Photos were moved to ungrouped.', 'success')
    return redirect(url_for('manage_gallery'))

@app.route('/admin/gallery/upload', methods=['POST'])
@admin_required
def upload_gallery_photo():
    """Upload a new gallery photo"""
    if 'photo' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    
    file = request.files['photo']
    caption = request.form.get('caption', '').strip()
    album_id_raw = request.form.get('album_id', '').strip()
    album_id = int(album_id_raw) if album_id_raw.isdigit() else None
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Save the file using the existing helper
    filename = save_uploaded_image(file, 'gallery')
    if not filename:
        return jsonify({'success': False, 'error': 'Failed to save image'}), 400
    
    # Create gallery photo record
    photo = GalleryPhoto(filename=filename, caption=caption or None, album_id=album_id)
    db.session.add(photo)

    # If album has no cover photo yet, use the first uploaded photo as its cover.
    if album_id:
        album = GalleryAlbum.query.get(album_id)
        if album and not album.cover_photo_id:
            db.session.flush()
            album.cover_photo_id = photo.id

    db.session.commit()
    
    return jsonify({
        'success': True,
        'photo_id': photo.id,
        'filename': photo.filename,
        'caption': photo.caption,
        'album_id': photo.album_id,
    })


@app.route('/admin/gallery/photo/<int:photo_id>/set-album', methods=['POST'])
@admin_required
def set_gallery_photo_album(photo_id):
    photo = GalleryPhoto.query.get_or_404(photo_id)
    album_id_raw = request.form.get('album_id', '').strip()
    album_id = int(album_id_raw) if album_id_raw.isdigit() else None

    if album_id is not None and not GalleryAlbum.query.get(album_id):
        return jsonify({'success': False, 'error': 'Album not found'}), 404

    photo.album_id = album_id
    db.session.commit()
    return jsonify({'success': True, 'album_id': photo.album_id})


@app.route('/admin/gallery/photos/bulk-organize', methods=['POST'])
@admin_required
def bulk_organize_gallery_photos():
    action = request.form.get('action', '').strip().lower()
    album_id_raw = request.form.get('album_id', '').strip()
    photo_ids_raw = request.form.getlist('photo_ids')

    if action not in ('move', 'copy'):
        return jsonify({'success': False, 'error': 'Invalid action'}), 400

    album_id = int(album_id_raw) if album_id_raw.isdigit() else None
    if album_id is not None and not GalleryAlbum.query.get(album_id):
        return jsonify({'success': False, 'error': 'Album not found'}), 404

    photo_ids = [int(raw_id) for raw_id in photo_ids_raw if raw_id.isdigit()]
    if not photo_ids:
        return jsonify({'success': False, 'error': 'No photos selected'}), 400

    photos = GalleryPhoto.query.filter(GalleryPhoto.id.in_(photo_ids)).all()

    if action == 'move':
        for photo in photos:
            photo.album_id = album_id
    else:
        for photo in photos:
            clone = GalleryPhoto(
                filename=photo.filename,
                caption=photo.caption,
                display_order=photo.display_order,
                album_id=album_id,
            )
            db.session.add(clone)

    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/gallery/albums/<int:album_id>/set-cover', methods=['POST'])
@admin_required
def set_gallery_album_cover(album_id):
    album = GalleryAlbum.query.get_or_404(album_id)
    photo_id_raw = request.form.get('photo_id', '').strip()

    if not photo_id_raw.isdigit():
        return jsonify({'success': False, 'error': 'Invalid photo id'}), 400

    photo = GalleryPhoto.query.get_or_404(int(photo_id_raw))
    if photo.album_id != album.id:
        return jsonify({'success': False, 'error': 'Photo is not in this album'}), 400

    album.cover_photo_id = photo.id
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/gallery/photo/<int:photo_id>/update-caption', methods=['POST'])
@admin_required
def update_gallery_caption(photo_id):
    """Update caption for a gallery photo"""
    photo = GalleryPhoto.query.get_or_404(photo_id)
    caption = request.form.get('caption', '').strip()
    photo.caption = caption or None
    photo.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'caption': photo.caption})

@app.route('/admin/gallery/photo/<int:photo_id>/delete', methods=['POST'])
@admin_required
def delete_gallery_photo(photo_id):
    """Delete a gallery photo"""
    photo = GalleryPhoto.query.get_or_404(photo_id)
    GalleryAlbum.query.filter_by(cover_photo_id=photo.id).update({GalleryAlbum.cover_photo_id: None}, synchronize_session=False)
    delete_uploaded_image(photo.filename)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/gallery/photos/json')
@admin_required
def gallery_photos_json():
    """Return all gallery photos as JSON for use in image pickers."""
    photos = GalleryPhoto.query.order_by(GalleryPhoto.created_at.desc()).all()
    result = []
    for p in photos:
        result.append({
            'id': p.id,
            'url': image_url_filter(p.filename),
            'caption': p.caption or '',
        })
    return jsonify(result)


@app.route('/admin/profile/create', methods=['GET', 'POST'])
@admin_required
def create_profile():
    if request.method == 'POST':
        name = request.form.get('name')
        short_bio = request.form.get('short_bio')
        bio = request.form.get('bio')
        display_order = int(request.form.get('display_order', 0))
        
        profile = Profile(
            name=name,
            short_bio=short_bio,
            bio=bio,
            display_order=display_order
        )
        
        # Handle profile card photo upload
        if 'card_photo' in request.files:
            uploaded = save_uploaded_image(request.files['card_photo'], 'profile_card')
            if uploaded:
                profile.card_photo = uploaded

        # Handle profile cover photo upload
        if 'photo' in request.files:
            uploaded = save_uploaded_image(request.files['photo'], 'profile')
            if uploaded:
                profile.photo = uploaded
        
        db.session.add(profile)
        db.session.commit()
        
        flash('Profile created successfully!', 'success')
        return redirect(url_for('manage_profiles'))
    
    return render_template('admin/create_profile.html')

@app.route('/admin/profile/<int:profile_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    
    if request.method == 'POST':
        profile.name = request.form.get('name')
        profile.short_bio = request.form.get('short_bio')
        profile.bio = request.form.get('bio')
        profile.display_order = int(request.form.get('display_order', 0))
        
        # Handle profile card photo upload
        if 'card_photo' in request.files:
            uploaded = save_uploaded_image(request.files['card_photo'], 'profile_card')
            if uploaded:
                delete_uploaded_image(profile.card_photo)
                profile.card_photo = uploaded

        # Handle profile cover photo upload
        if 'photo' in request.files:
            uploaded = save_uploaded_image(request.files['photo'], 'profile')
            if uploaded:
                delete_uploaded_image(profile.photo)
                profile.photo = uploaded
        
        profile.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('manage_profiles'))
    
    return render_template('admin/edit_profile.html', profile=profile)

@app.route('/admin/profile/<int:profile_id>/delete', methods=['POST'])
@admin_required
def delete_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    
    # Delete profile images from storage if they exist
    for filename in {profile.card_photo, profile.photo}:
        delete_uploaded_image(filename)
    
    db.session.delete(profile)
    db.session.commit()
    
    return jsonify({'success': True})

# Site Settings Management (Admin Only)
@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def site_settings():
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    
    if request.method == 'POST':
        # Handle homepage background image upload
        if 'homepage_bg_image' in request.files:
            uploaded = save_uploaded_image(request.files['homepage_bg_image'], 'homepage_bg')
            if uploaded:
                delete_uploaded_image(settings.homepage_bg_image)
                settings.homepage_bg_image = uploaded
        
        # Handle Facebook settings
        settings.facebook_page_id = request.form.get('facebook_page_id', '').strip()
        settings.facebook_access_token = request.form.get('facebook_access_token', '').strip()
        settings.auto_share_stories = 'auto_share_stories' in request.form
        settings.auto_share_programmes = 'auto_share_programmes' in request.form
        settings.auto_share_news = 'auto_share_news' in request.form
        settings.auto_share_events = 'auto_share_events' in request.form
        settings.auto_share_competitions = 'auto_share_competitions' in request.form
        
        settings.updated_by_id = current_user.id
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash('Site settings updated successfully!', 'success')
        return redirect(url_for('site_settings'))
    
    return render_template('admin/site_settings.html', settings=settings)

@app.route('/admin/settings/remove-bg-image', methods=['POST'])
@admin_required
def remove_bg_image():
    settings = SiteSettings.query.first()
    if settings and settings.homepage_bg_image:
        delete_uploaded_image(settings.homepage_bg_image)
        
        settings.homepage_bg_image = None
        settings.updated_by_id = current_user.id
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash('Background image removed successfully!', 'success')
    
    return redirect(url_for('site_settings'))

# Band and Positions Vacant Routes
@app.route('/positions-vacant')
def positions_vacant():
    """Display list of vacant positions with filtering options."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get filter parameters
    instrument = request.args.get('instrument', '').strip()
    section = request.args.get('section', '').strip()
    rehearsal_days = normalize_rehearsal_days(request.args.getlist('rehearsal_days'))
    postcode = request.args.get('postcode', '').strip()
    distance_km = request.args.get('distance', 50, type=float) or 50
    distance_km = min(max(distance_km, 1), 250)
    distance_warning = None
    
    # Start with active positions
    query = PositionVacant.query.join(Band).filter(
        PositionVacant.is_active == True,
        (PositionVacant.expires_at.is_(None) | (PositionVacant.expires_at > datetime.utcnow()))
    )
    
    # Filter by instrument
    if instrument:
        query = query.filter(PositionVacant.instrument.ilike(f'%{instrument}%'))
    
    # Filter by section
    if section:
        query = query.filter(PositionVacant.section == section)
    
    # Filter by one or more rehearsal days (matches any selected day)
    if rehearsal_days:
        query = query.filter(or_(*[PositionVacant.rehearsal_days.ilike(f'%{day}%') for day in rehearsal_days]))

    ordered_query = query.order_by(PositionVacant.created_at.desc())

    # Optional distance filter from search postcode.
    if postcode:
        search_coords = geocode_uk_postcode(postcode)
        if not search_coords:
            distance_warning = 'Could not locate that postcode, so distance filtering was not applied.'
            positions = ordered_query.paginate(page=page, per_page=per_page)
        else:
            lat1, lon1 = search_coords
            filtered_positions = []
            for position in ordered_query.all():
                band_coords = geocode_uk_postcode(position.band.postcode)
                if not band_coords:
                    continue

                lat2, lon2 = band_coords
                km = distance_km_between(lat1, lon1, lat2, lon2)
                if km <= distance_km:
                    position.distance_km = round(km, 1)
                    filtered_positions.append(position)

            filtered_positions.sort(key=lambda p: (getattr(p, 'distance_km', 9999), -p.created_at.timestamp()))
            positions = ListPagination(filtered_positions, page, per_page)
    else:
        positions = ordered_query.paginate(page=page, per_page=per_page)
    
    sections = ['4th', '3rd', '2nd', '1st', 'championship']
    days = WEEK_DAYS
    
    return render_template('positions_vacant.html', 
                         positions=positions,
                         sections=sections,
                         days=days,
                         current_instrument=instrument,
                         current_section=section,
                         current_rehearsal_days=rehearsal_days,
                         current_postcode=postcode,
                         current_distance=distance_km,
                         distance_warning=distance_warning)

@app.route('/position/<int:position_id>')
def position_detail(position_id):
    """View details of a specific position vacancy."""
    position = PositionVacant.query.get_or_404(position_id)
    
    # Check if position is still active
    if not position.is_active or (position.expires_at and position.expires_at <= datetime.utcnow()):
        flash('This position is no longer available.', 'warning')
        return redirect(url_for('positions_vacant'))
    
    band = position.band
    return render_template('position_detail.html', position=position, band=band)

@app.route('/band/register', methods=['GET', 'POST'])
@login_required
def register_band():
    """Create a new band."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        postcode = request.form.get('postcode', '').strip()
        description = request.form.get('description', '').strip()
        website = request.form.get('website', '').strip()
        email = request.form.get('email', '').strip()
        
        # Validate required fields
        if not name or not postcode:
            flash('Band name and postcode are required.', 'danger')
            return redirect(url_for('register_band'))
        
        # Check if band name already exists
        if Band.query.filter_by(name=name).first():
            flash('A band with this name already exists.', 'danger')
            return redirect(url_for('register_band'))
        
        # Create new band
        band = Band(
            name=name,
            postcode=postcode,
            description=description,
            website=website,
            email=email,
            created_by=current_user.id,
            updated_by_id=current_user.id
        )
        
        # Handle logo upload
        if 'logo' in request.files:
            uploaded = save_uploaded_image(request.files['logo'], f'band_{name.replace(" ", "_")}')
            if uploaded:
                band.logo = uploaded
        
        db.session.add(band)
        db.session.flush()  # Get the band ID
        
        # Add creator as band admin
        member = BandMember(band_id=band.id, user_id=current_user.id, role='band_admin')
        db.session.add(member)
        db.session.commit()
        
        flash(f'Band {name} created successfully!', 'success')
        return redirect(url_for('band_detail', band_id=band.id))
    
    return render_template('band_register.html')

@app.route('/band/<int:band_id>')
def band_detail(band_id):
    """View band details and current vacancies."""
    band = Band.query.get_or_404(band_id)
    active_positions = PositionVacant.query.filter_by(band_id=band_id, is_active=True).all()
    members = BandMember.query.filter_by(band_id=band_id).join(User).all()
    
    # Check if current user is a band member/admin
    is_band_member = False
    is_band_admin = False
    if current_user.is_authenticated:
        member = BandMember.query.filter_by(band_id=band_id, user_id=current_user.id).first()
        if member:
            is_band_member = True
            is_band_admin = (member.role in ['band_admin', 'conductor'])
    
    return render_template('band_detail.html', 
                         band=band, 
                         positions=active_positions,
                         members=members,
                         is_band_member=is_band_member,
                         is_band_admin=is_band_admin)

@app.route('/band/<int:band_id>/manage', methods=['GET', 'POST'])
@login_required
def band_manage(band_id):
    """Manage band: add members, post positions, edit details."""
    band = Band.query.get_or_404(band_id)
    
    # Check if user is band admin
    member = BandMember.query.filter_by(band_id=band_id, user_id=current_user.id).first()
    if not member or member.role != 'band_admin':
        flash('You do not have permission to manage this band.', 'danger')
        return redirect(url_for('band_detail', band_id=band_id))
    
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_band':
            name = request.form.get('name', '').strip()
            postcode = request.form.get('postcode', '').strip()
            description = request.form.get('description', '').strip()
            website = request.form.get('website', '').strip()
            email = request.form.get('email', '').strip()
            default_days = normalize_rehearsal_days(request.form.getlist('default_rehearsal_days'))

            if not name or not postcode:
                flash('Band name and postcode are required.', 'danger')
            else:
                existing_band = Band.query.filter(Band.name == name, Band.id != band.id).first()
                if existing_band:
                    flash('Another band already uses that name.', 'danger')
                else:
                    band.name = name
                    band.postcode = postcode
                    band.description = description
                    band.website = website
                    band.email = email
                    band.default_rehearsal_days = ', '.join(default_days)
                    band.updated_by_id = current_user.id

                    if request.form.get('remove_logo') == '1' and band.logo:
                        delete_uploaded_image(band.logo)
                        band.logo = None

                    if 'logo' in request.files:
                        uploaded_logo = save_uploaded_image(request.files['logo'], f'band_{band.id}')
                        if uploaded_logo:
                            delete_uploaded_image(band.logo)
                            band.logo = uploaded_logo

                    band.updated_at = datetime.utcnow()
                    db.session.commit()
                    flash('Band details updated successfully!', 'success')
        
        if action == 'add_member':
            username = request.form.get('username', '').strip()
            role = request.form.get('role', 'member')
            
            user = User.query.filter_by(username=username).first()
            if not user:
                flash(f'User {username} not found.', 'danger')
            elif BandMember.query.filter_by(band_id=band_id, user_id=user.id).first():
                flash(f'{username} is already a band member.', 'warning')
            else:
                new_member = BandMember(band_id=band_id, user_id=user.id, role=role)
                db.session.add(new_member)
                db.session.commit()
                flash(f'{username} added to the band as {role}!', 'success')
        
        elif action == 'remove_member':
            member_id = request.form.get('member_id', type=int)
            member_to_remove = BandMember.query.get_or_404(member_id)
            
            if member_to_remove.band_id != band_id:
                flash('Invalid member.', 'danger')
            elif member_to_remove.role == 'band_admin' and member_to_remove.user_id == current_user.id:
                flash('Cannot remove yourself as band admin.', 'danger')
            else:
                db.session.delete(member_to_remove)
                db.session.commit()
                flash('Member removed successfully.', 'success')
        
        elif action == 'post_position':
            instrument = request.form.get('instrument', '').strip()
            section = request.form.get('section', '').strip()
            selected_days = normalize_rehearsal_days(request.form.getlist('rehearsal_days'))
            if not selected_days:
                selected_days = normalize_rehearsal_days((band.default_rehearsal_days or '').split(','))
            rehearsal_days = ', '.join(selected_days)
            description = request.form.get('description', '').strip()
            
            if not instrument or not section:
                flash('Instrument and section are required.', 'danger')
            else:
                position = PositionVacant(
                    band_id=band_id,
                    instrument=instrument,
                    section=section,
                    rehearsal_days=rehearsal_days,
                    description=description,
                    is_active=True
                )
                db.session.add(position)
                db.session.commit()
                flash(f'Position for {instrument} ({section} section) posted successfully!', 'success')

        elif action == 'close_position':
            position_id = request.form.get('position_id', type=int)
            position = PositionVacant.query.get_or_404(position_id)

            if position.band_id != band_id:
                flash('Invalid position for this band.', 'danger')
            else:
                position.is_active = False
                position.updated_at = datetime.utcnow()
                db.session.commit()
                flash('Position closed successfully.', 'success')
        
        return redirect(url_for('band_manage', band_id=band_id))
    
    members = BandMember.query.filter_by(band_id=band_id).join(User).all()
    positions = PositionVacant.query.filter_by(band_id=band_id).all()
    sections = ['4th', '3rd', '2nd', '1st', 'championship']
    days = WEEK_DAYS
    band_default_days = normalize_rehearsal_days((band.default_rehearsal_days or '').split(','))
    
    return render_template('band_manage.html', 
                         band=band, 
                         members=members, 
                         positions=positions,
                         sections=sections,
                         days=days,
                         band_default_days=band_default_days)

@app.route('/position/<int:position_id>/close', methods=['POST'])
@login_required
def close_position(position_id):
    """Close/deactivate a position vacancy."""
    position = PositionVacant.query.get_or_404(position_id)
    band = position.band
    
    # Check if user is band admin
    member = BandMember.query.filter_by(band_id=band.id, user_id=current_user.id).first()
    if not member or member.role != 'band_admin':
        flash('You do not have permission to manage this position.', 'danger')
        return redirect(url_for('band_detail', band_id=band.id))
    
    position.is_active = False
    position.updated_at = datetime.utcnow()
    db.session.commit()
    
    flash('Position closed successfully.', 'success')
    return redirect(url_for('band_manage', band_id=band.id))

# User Management Routes (Admin Only)
@app.route('/admin/users')
@admin_required
def manage_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/manage_users.html', users=users)

@app.route('/admin/users/export-marketing')
@admin_required
def export_marketing_list():
    """Download a CSV of all users who have opted in to marketing emails."""
    import io, csv as csv_module
    opted_in = User.query.filter_by(marketing_opt_in=True).order_by(User.created_at.desc()).all()
    buf = io.StringIO()
    writer = csv_module.writer(buf)
    writer.writerow(['username', 'email', 'role', 'joined'])
    for u in opted_in:
        writer.writerow([u.username, u.email, u.role, u.created_at.strftime('%Y-%m-%d')])
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="marketing_opt_ins.csv"'},
    )

@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        user.role = request.form.get('role', 'user')
        
        # Update legacy is_admin field for compatibility
        user.is_admin = (user.role == 'admin')
        
        # Optional password change
        new_password = request.form.get('new_password', '').strip()
        if new_password:
            user.set_password(new_password)
        
        user.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash(f'User {user.username} updated successfully!', 'success')
        return redirect(url_for('manage_users'))
    
    return render_template('admin/edit_user.html', user=user)

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Don't allow deleting yourself
    if user.id == current_user.id:
        return jsonify({'success': False, 'error': 'Cannot delete your own account'}), 400
    
    # Delete user's profile photo if exists
    delete_uploaded_image(user.profile_photo)
    
    db.session.delete(user)
    db.session.commit()
    
    return jsonify({'success': True})

# User Profile Routes (All authenticated users)
@app.route('/profile')
@login_required
def view_profile():
    return render_template('profile.html', user=current_user)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_my_profile():
    if request.method == 'POST':
        current_user.username = request.form.get('username')
        current_user.email = request.form.get('email')
        current_user.bio = request.form.get('bio', '').strip()
        
        # Handle profile photo upload
        if 'profile_photo' in request.files:
            uploaded = save_uploaded_image(request.files['profile_photo'], f'user_{current_user.id}')
            if uploaded:
                delete_uploaded_image(current_user.profile_photo)
                current_user.profile_photo = uploaded
        
        # Optional password change
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        
        if new_password:
            if not current_password:
                flash('Current password is required to set a new password.', 'danger')
                return redirect(url_for('edit_my_profile'))
            
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('edit_my_profile'))
            
            current_user.set_password(new_password)
            flash('Password updated successfully!', 'success')
        
        current_user.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('view_profile'))
    
    return render_template('edit_profile.html', user=current_user)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
