"""
Admin blueprint for CMS content management.
Compatible with existing templates under templates/admin/**.
"""

from datetime import datetime
from functools import wraps
import re
import os

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _models():
    return (
        current_app.db,
        current_app.User,
        current_app.NewsItem,
        current_app.Event,
        current_app.PageContent,
        current_app.ContactMessage,
    )


def _slugify(value: str) -> str:
    value = (value or "").strip()
    slug = re.sub(r"[^\w\s-]", "", value)
    slug = re.sub(r"[-\s]+", "-", slug).strip("-").lower()
    return slug or "item"


def _parse_dt_local(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated


def editor_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not (current_user.is_admin() or current_user.is_editor()):
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated


@admin_bp.app_context_processor
def inject_admin_globals():
    _, _, _, _, _, ContactMessage = _models()
    unread_messages = ContactMessage.query.filter_by(read=False).count()
    return {"unread_messages": unread_messages}


@admin_bp.route("/")
@admin_required
def dashboard():
    _, User, NewsItem, Event, _, ContactMessage = _models()
    news_count = NewsItem.query.count()
    event_count = Event.query.count()
    user_count = User.query.count()
    unread_messages = ContactMessage.query.filter_by(read=False).count()
    recent_news = NewsItem.query.order_by(NewsItem.created_at.desc()).limit(5).all()
    upcoming_events = Event.query.filter(Event.event_date >= datetime.utcnow()).order_by(Event.event_date.asc()).limit(5).all()

    return render_template(
        "admin/dashboard.html",
        news_count=news_count,
        event_count=event_count,
        user_count=user_count,
        unread_messages=unread_messages,
        recent_news=recent_news,
        upcoming_events=upcoming_events,
    )


@admin_bp.route("/test")
def test_route():
    """Simple test route that doesn't require auth"""
    return "Admin blueprint is working!"


@admin_bp.route("/news")
@editor_required
def manage_news():
    _, _, NewsItem, _, _, _ = _models()
    page = request.args.get("page", 1, type=int)
    news = NewsItem.query.order_by(NewsItem.created_at.desc()).paginate(page=page, per_page=20)
    return render_template("admin/news/list.html", news=news)


@admin_bp.route("/news/list")
@editor_required
def news_list():
    return manage_news()


@admin_bp.route("/news/create", methods=["GET", "POST"])
@editor_required
def create_news():
    db, _, NewsItem, _, _, _ = _models()
    upload_image = getattr(current_app, "upload_media_image", None)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        if not title or not content:
            flash("Title and content are required.", "warning")
            return render_template("admin/news/create.html")

        slug = _slugify(title)
        existing = NewsItem.query.filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{int(datetime.utcnow().timestamp())}"

        published_at = _parse_dt_local(request.form.get("published_at")) or datetime.utcnow()
        featured_image = None
        image_file = request.files.get("featured_image")
        if image_file and upload_image:
            featured_image = upload_image(image_file, "news")

        item = NewsItem(
            title=title,
            slug=slug,
            subtitle=(request.form.get("subtitle") or "").strip() or None,
            excerpt=(request.form.get("excerpt") or "").strip() or None,
            content=content,
            featured_image=featured_image,
            published=request.form.get("published") == "on",
            published_at=published_at,
            author_id=current_user.id,
        )
        db.session.add(item)
        db.session.commit()
        flash("News article created.", "success")
        return redirect(url_for("admin.manage_news"))

    return render_template("admin/news/create.html")


@admin_bp.route("/news/create-new", methods=["GET", "POST"])
@editor_required
def news_create():
    return create_news()


@admin_bp.route("/news/<int:news_id>/edit", methods=["GET", "POST"])
@editor_required
def edit_news(news_id):
    db, _, NewsItem, _, _, _ = _models()
    news = NewsItem.query.get_or_404(news_id)
    upload_image = getattr(current_app, "upload_media_image", None)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        if not title or not content:
            flash("Title and content are required.", "warning")
            return render_template("admin/news/edit.html", news=news)

        news.title = title
        news.slug = _slugify(title)
        news.subtitle = (request.form.get("subtitle") or "").strip() or None
        news.excerpt = (request.form.get("excerpt") or "").strip() or None
        news.content = content
        image_file = request.files.get("featured_image")
        if image_file and upload_image:
            uploaded = upload_image(image_file, "news")
            if uploaded:
                news.featured_image = uploaded
        news.published = request.form.get("published") == "on"
        news.published_at = _parse_dt_local(request.form.get("published_at")) or news.published_at

        db.session.commit()
        flash("News article updated.", "success")
        return redirect(url_for("admin.manage_news"))

    return render_template("admin/news/edit.html", news=news)


@admin_bp.route("/news/<int:id>/edit", methods=["GET", "POST"])
@editor_required
def news_edit(id):
    return edit_news(id)


@admin_bp.route("/news/<int:news_id>/delete", methods=["POST"])
@editor_required
def delete_news(news_id):
    db, _, NewsItem, _, _, _ = _models()
    news = NewsItem.query.get_or_404(news_id)
    db.session.delete(news)
    db.session.commit()
    flash("News article deleted.", "success")
    return redirect(url_for("admin.manage_news"))


@admin_bp.route("/news/<int:id>/delete", methods=["POST"])
@editor_required
def news_delete(id):
    return delete_news(id)


@admin_bp.route("/events")
@editor_required
def manage_events():
    _, _, _, Event, _, _ = _models()
    page = request.args.get("page", 1, type=int)
    events = Event.query.order_by(Event.event_date.desc()).paginate(page=page, per_page=20)
    return render_template("admin/events/list.html", events=events)


@admin_bp.route("/events/list")
@editor_required
def events_list():
    return manage_events()


@admin_bp.route("/events/create", methods=["GET", "POST"])
@editor_required
def create_event():
    db, _, _, Event, _, _ = _models()
    upload_image = getattr(current_app, "upload_media_image", None)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        event_dt = _parse_dt_local(request.form.get("event_date"))
        if not title or not event_dt:
            flash("Title and date are required.", "warning")
            return render_template("admin/events/create.html")

        slug = _slugify(title)
        existing = Event.query.filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{int(datetime.utcnow().timestamp())}"

        featured_image = None
        image_file = request.files.get("featured_image")
        if image_file and upload_image:
            featured_image = upload_image(image_file, "events")

        event = Event(
            title=title,
            slug=slug,
            description=(request.form.get("description") or "").strip() or None,
            event_date=event_dt,
            location=(request.form.get("location") or "").strip() or None,
            tickets_url=(request.form.get("tickets_url") or "").strip() or None,
            livestream_url=(request.form.get("livestream_url") or "").strip() or None,
            youtube_embed_url=(request.form.get("youtube_embed_url") or "").strip() or None,
            featured_image=featured_image,
            published=request.form.get("published") == "on",
            author_id=current_user.id,
        )
        db.session.add(event)
        db.session.commit()
        flash("Event created.", "success")
        return redirect(url_for("admin.manage_events"))

    return render_template("admin/events/create.html")


@admin_bp.route("/events/create-new", methods=["GET", "POST"])
@editor_required
def events_create():
    return create_event()


@admin_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@editor_required
def edit_event(event_id):
    db, _, _, Event, _, _ = _models()
    event = Event.query.get_or_404(event_id)
    upload_image = getattr(current_app, "upload_media_image", None)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        event_dt = _parse_dt_local(request.form.get("event_date"))
        if not title or not event_dt:
            flash("Title and date are required.", "warning")
            return render_template("admin/events/edit.html", event=event)

        event.title = title
        event.slug = _slugify(title)
        event.description = (request.form.get("description") or "").strip() or None
        event.event_date = event_dt
        event.location = (request.form.get("location") or "").strip() or None
        event.tickets_url = (request.form.get("tickets_url") or "").strip() or None
        event.livestream_url = (request.form.get("livestream_url") or "").strip() or None
        event.youtube_embed_url = (request.form.get("youtube_embed_url") or "").strip() or None
        image_file = request.files.get("featured_image")
        if image_file and upload_image:
            uploaded = upload_image(image_file, "events")
            if uploaded:
                event.featured_image = uploaded
        event.published = request.form.get("published") == "on"

        db.session.commit()
        flash("Event updated.", "success")
        return redirect(url_for("admin.manage_events"))

    return render_template("admin/events/edit.html", event=event)


@admin_bp.route("/events/<int:id>/edit", methods=["GET", "POST"])
@editor_required
def events_edit(id):
    return edit_event(id)


@admin_bp.route("/events/<int:event_id>/delete", methods=["POST"])
@editor_required
def delete_event(event_id):
    db, _, _, Event, _, _ = _models()
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash("Event deleted.", "success")
    return redirect(url_for("admin.manage_events"))


@admin_bp.route("/events/<int:id>/delete", methods=["POST"])
@editor_required
def events_delete(id):
    return delete_event(id)


@admin_bp.route("/pages")
@editor_required
def manage_pages():
    _, _, _, _, PageContent, _ = _models()
    page = request.args.get("page", 1, type=int)
    content = PageContent.query.order_by(PageContent.page.asc(), PageContent.order.asc()).paginate(page=page, per_page=25)
    return render_template("admin/pages/list.html", content=content)


@admin_bp.route("/pages/create", methods=["GET", "POST"])
@editor_required
def create_page_content():
    db, _, _, _, PageContent, _ = _models()

    default_page = (request.args.get("page") or "").strip().lower()
    default_section = (request.args.get("section") or "").strip()
    default_title = (request.args.get("title") or "").strip()
    default_content = (request.args.get("content") or "").strip()
    default_order = request.args.get("order", 0, type=int)
    default_image_url = (request.args.get("image_url") or "").strip()

    if request.method == "POST":
        page_name = (request.form.get("page") or "").strip().lower()
        section = (request.form.get("section") or "").strip()
        if not page_name or not section:
            flash("Page and section are required.", "warning")
            return render_template(
                "admin/pages/create.html",
                default_page=page_name,
                default_section=section,
                default_title=(request.form.get("title") or "").strip(),
                default_content=(request.form.get("content") or "").strip(),
                default_image_url=(request.form.get("image_url") or "").strip(),
                default_order=request.form.get("order", type=int) or 0,
            )

        image_url = (request.form.get("image_url") or "").strip() or None

        item = PageContent(
            page=page_name,
            section=section,
            title=(request.form.get("title") or "").strip() or None,
            content=(request.form.get("content") or "").strip() or None,
            image_url=image_url,
            order=request.form.get("order", type=int) or 0,
            published=request.form.get("published") == "on",
            author_id=current_user.id,
        )
        db.session.add(item)
        db.session.commit()
        flash("Page content created.", "success")
        return redirect(url_for("admin.manage_pages"))

    return render_template(
        "admin/pages/create.html",
        default_page=default_page,
        default_section=default_section,
        default_title=default_title,
        default_content=default_content,
        default_image_url=default_image_url,
        default_order=default_order,
    )


@admin_bp.route("/pages/<int:content_id>/edit", methods=["GET", "POST"])
@editor_required
def edit_page_content(content_id):
    db, _, _, _, PageContent, _ = _models()
    content = PageContent.query.get_or_404(content_id)

    if request.method == "POST":
        content.title = (request.form.get("title") or "").strip() or None
        content.content = (request.form.get("content") or "").strip() or None
        content.image_url = (request.form.get("image_url") or "").strip() or content.image_url
        content.order = request.form.get("order", type=int) or 0
        content.published = request.form.get("published") == "on"

        db.session.commit()
        flash("Page content updated.", "success")
        return redirect(url_for("admin.manage_pages"))

    return render_template("admin/pages/edit.html", content=content)


@admin_bp.route("/pages/<int:content_id>/delete", methods=["POST"])
@editor_required
def delete_page_content(content_id):
    db, _, _, _, PageContent, _ = _models()
    content = PageContent.query.get_or_404(content_id)
    db.session.delete(content)
    db.session.commit()
    flash("Page content deleted.", "success")
    return redirect(url_for("admin.manage_pages"))


@admin_bp.route("/messages")
@admin_required
def manage_messages():
    _, _, _, _, _, ContactMessage = _models()
    page = request.args.get("page", 1, type=int)
    messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).paginate(page=page, per_page=20)

    unread_ids = [m.id for m in messages.items if not m.read]
    if unread_ids:
        current_app.db.session.query(ContactMessage).filter(ContactMessage.id.in_(unread_ids)).update(
            {ContactMessage.read: True}, synchronize_session=False
        )
        current_app.db.session.commit()

    return render_template("admin/messages/list.html", messages=messages)


@admin_bp.route("/messages/list")
@admin_required
def messages_list():
    return manage_messages()


@admin_bp.route("/messages/<int:message_id>/delete", methods=["POST"])
@admin_required
def delete_message(message_id):
    db, _, _, _, _, ContactMessage = _models()
    message = ContactMessage.query.get_or_404(message_id)
    db.session.delete(message)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(url_for("admin.manage_messages"))


@admin_bp.route("/messages/<int:id>")
@admin_required
def messages_view(id):
    return redirect(url_for("admin.manage_messages"))


@admin_bp.route("/messages/<int:id>/delete", methods=["POST"])
@admin_required
def messages_delete(id):
    return delete_message(id)


@admin_bp.route("/users")
@admin_required
def manage_users():
    _, User, _, _, _, _ = _models()
    page = request.args.get("page", 1, type=int)
    users = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=20)
    return render_template("admin/users/list.html", users=users)


@admin_bp.route("/users/list")
@admin_required
def users_list():
    return manage_users()


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    db, User, _, _, _, _ = _models()
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        user.name = (request.form.get("name") or "").strip() or None
        role = (request.form.get("role") or "viewer").strip().lower()
        if role not in {"admin", "editor", "viewer"}:
            role = "viewer"
        user.role = role
        user.is_active = request.form.get("is_active") == "on"
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.manage_users"))

    return render_template("admin/users/edit.html", user=user)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    db, User, _, _, _, _ = _models()
    if user_id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("admin.manage_users"))

    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("admin.manage_users"))


# ============= INLINE PAGE EDITOR API ENDPOINTS =============

@admin_bp.route("/api/admin-check")
def admin_check():
    """Check if current user is admin"""
    if current_user.is_authenticated and current_user.is_admin():
        return jsonify({"is_admin": True}), 200
    return jsonify({"is_admin": False}), 200

@admin_bp.route("/api/page-content", methods=["POST"])
@login_required
def save_page_content():
    """Save inline page content edits to database and optionally update HTML files"""
    if not current_user.is_admin():
        return jsonify({"error": "Unauthorized"}), 403

    db, _, _, _, PageContent, _ = _models()

    try:
        data = request.get_json()
        page = (data.get("page") or "").strip()
        section = (data.get("section") or "").strip()
        content = (data.get("content") or "").strip()

        if not page or not section:
            return jsonify({"error": "Page and section are required"}), 400

        # Find or create PageContent entry
        pc = PageContent.query.filter_by(page=page, section=section).first()

        if pc is None:
            pc = PageContent(
                page=page,
                section=section,
                content=content,
                author_id=current_user.id,
            )
            db.session.add(pc)
        else:
            pc.content = content
            pc.updated_at = datetime.utcnow()

        db.session.commit()

        # Optionally update HTML file
        try:
            _update_html_file(page, section, content)
        except Exception as e:
            current_app.logger.warning(f"Failed to update HTML file: {e}")
            # Don't fail the API call if HTML update fails

        return jsonify({"success": True, "id": pc.id}), 200

    except Exception as e:
        current_app.logger.exception("Error saving page content")
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/page-content/<page>/<section>", methods=["GET"])
@login_required
def get_page_content(page, section):
    """Get page content from database"""
    if not current_user.is_admin():
        return jsonify({"error": "Unauthorized"}), 403

    db, _, _, _, PageContent, _ = _models()

    try:
        pc = PageContent.query.filter_by(page=page, section=section).first()

        if pc is None:
            return jsonify({"error": "Content not found"}), 404

        return jsonify({
            "id": pc.id,
            "page": pc.page,
            "section": pc.section,
            "title": pc.title,
            "content": pc.content,
            "image_url": pc.image_url,
            "published": pc.published,
            "updated_at": pc.updated_at.isoformat() if pc.updated_at else None,
        }), 200

    except Exception as e:
        current_app.logger.exception("Error retrieving page content")
        return jsonify({"error": str(e)}), 500


def _update_html_file(page, section, content):
    """Update the HTML file with the new content"""
    import re
    from pathlib import Path

    # Map page names to HTML files
    page_map = {
        "home": "index.html",
        "biography": "Biography.html",
        "schedule": "Schedule.html",
        "media": "Media.html",
        "news": "News.html",
        "contact": "Contact.html",
    }

    html_file = page_map.get(page.lower())
    if not html_file:
        return

    base_dir = current_app.config.get("BASE_DIR", os.path.dirname(current_app.instance_path))
    html_path = os.path.join(base_dir, html_file)

    if not os.path.exists(html_path):
        return

    # Read the HTML file
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Find and replace the section content
    # Look for patterns like <div data-editable="section_name">...</div>
    pattern = rf'(<[^>]*data-editable=["\']?{re.escape(section)}["\']?[^>]*>)(.*?)(<\/[^>]+>)'
    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)

    if match:
        # Replace the content between the opening and closing tags
        updated_html = html_content[:match.start(2)] + content + html_content[match.end(2):]

        # Write back to file
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(updated_html)

        # Optionally commit to git
        try:
            import subprocess
            subprocess.run(
                ["git", "add", html_file],
                cwd=base_dir,
                check=False,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "commit", "-m", f"Update {page} page content: {section}"],
                cwd=base_dir,
                check=False,
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            current_app.logger.warning(f"Failed to auto-commit changes: {e}")
