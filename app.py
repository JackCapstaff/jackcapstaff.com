import importlib.util
import json
import csv
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse

from flask import Flask, flash, redirect, render_template, request, url_for, abort, send_from_directory
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.dispatcher import DispatcherMiddleware

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
app.config['SMTP_HOST'] = os.environ.get('SMTP_HOST', '').strip()
app.config['SMTP_PORT'] = int(os.environ.get('SMTP_PORT', '587'))
app.config['SMTP_USERNAME'] = os.environ.get('SMTP_USERNAME', '').strip()
app.config['SMTP_PASSWORD'] = os.environ.get('SMTP_PASSWORD', '').strip()
app.config['SMTP_USE_TLS'] = os.environ.get('SMTP_USE_TLS', '1').strip() not in {'0', 'false', 'False'}
app.config['CONTACT_TO_EMAIL'] = os.environ.get('CONTACT_TO_EMAIL', 'jack@jackcapstaff.com').strip()
app.config['CONTACT_FROM_EMAIL'] = os.environ.get('CONTACT_FROM_EMAIL', app.config['SMTP_USERNAME'] or 'noreply@jackcapstaff.com').strip()
app.config['SITE_TITLE'] = os.environ.get('SITE_TITLE', 'Jack Capstaff')

REHEARSAL_SCHEDULE_ROOT = os.path.join(BASE_DIR, 'Rehearsal Schedule', 'rehearsal_schedule')
REHEARSAL_SCHEDULE_DATA_DIR = os.path.join(REHEARSAL_SCHEDULE_ROOT, 'site', 'data', 'schedules')
REHEARSAL_SCHEDULE_PREFIX = '/rehearsal-schedule'
IMAGES_DIR = os.path.join(BASE_DIR, 'images')


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
# Expose models and db to app context for access in blueprints
app.db = db
app.User = User
app.NewsItem = NewsItem
app.Event = Event
app.PageContent = PageContent
app.ContactMessage = ContactMessage


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


def _is_safe_redirect_target(target):
    if not target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def send_contact_email(name, email, message, send_copy=False):
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


def load_schedule_events():
    csv_path = os.path.join(BASE_DIR, 'events.csv')
    events = []

    if not os.path.exists(csv_path):
        return events

    with open(csv_path, newline='', encoding='utf-8-sig') as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            if (row.get('Private') or '').strip().upper() == 'TRUE':
                continue

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
                'subject': (row.get('Subject') or '').strip(),
                'start_dt': start_dt,
                'date_label': start_dt.strftime('%d %b %Y') if start_dt != datetime.min else start_date,
                'time_label': start_dt.strftime('%H:%M') if start_dt != datetime.min and start_time else '',
                'location': (row.get('Location') or '').strip(),
                'description': (row.get('Description') or '').strip(),
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
    recent_news = NewsItem.query.filter_by(published=True).order_by(NewsItem.published_at.desc()).limit(5).all()
    return render_template('index.html', 
                         home_content=home_content,
                         upcoming_events=upcoming_events,
                         recent_news=recent_news)


@app.route('/Biography')
@app.route('/Biography.html')
def biography():
    biography_content = PageContent.query.filter_by(page='biography', published=True).order_by(PageContent.order).all()
    return render_template('Biography.html', biography_content=biography_content)


@app.route('/schedule')
@app.route('/Schedule')
@app.route('/Schedule.html')
def schedule():
    public_calendar_url = f'{REHEARSAL_SCHEDULE_PREFIX}/my'
    upcoming_events = Event.query.filter_by(published=True).filter(
        Event.event_date >= datetime.utcnow()
    ).order_by(Event.event_date).all()

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
    if request.method == 'POST':
        name = request.form.get('demo-name', '').strip()
        email = request.form.get('demo-email', '').strip()
        message = request.form.get('demo-message', '').strip()
        send_copy = request.form.get('demo-copy') == 'on'

        if not name or not email or not message:
            flash('Please complete the contact form.', 'warning')
            return render_template('Contact.html')

        db.session.add(ContactMessage(name=name, email=email, message=message))
        db.session.commit()

        sent, detail = send_contact_email(name, email, message, send_copy=send_copy)
        if sent:
            flash('Your message has been sent successfully.', 'success')
        else:
            flash(f'Your message was saved, but email could not be sent: {detail}', 'warning')
        return redirect(url_for('contact'))

    return render_template('Contact.html')


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


with app.app_context():
    db.create_all()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', '0') == '1')
