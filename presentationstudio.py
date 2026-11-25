"""Layered presentation authoring and an independently clocked public player."""
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from cloudinary.exceptions import Error as CloudinaryError
from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import update
from werkzeug.exceptions import HTTPException

presentations_bp = Blueprint("presentations", __name__, url_prefix="/presentations")
MAX_UPLOAD = 100 * 1024 * 1024
KINDS = {".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
         ".mp4": "video", ".webm": "video", ".mov": "video",
         ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio"}


@presentations_bp.before_request
def admin_only():
    if request.endpoint in ("presentations.watch", "presentations.watch_state"):
        return
    if not current_user.is_authenticated:
        if "/api/" in request.path:
            abort(401, description="Sign in as an administrator.")
        return current_app.login_manager.unauthorized()
    if current_user.role != "admin":
        abort(403, description="Only administrators can manage presentations.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.is_json:
        abort(400, description="Expected a JSON request from the presentation editor.")


@presentations_bp.errorhandler(HTTPException)
def request_error(error):
    if "/api/" in request.path or request.path.endswith("/state"):
        return jsonify(error=error.description), error.code
    return error


def models():
    return current_app.db, current_app.StudioPresentation, current_app.PresentationAsset


def own(show_id):
    return current_app.StudioPresentation.query.filter_by(id=show_id, user_id=current_user.id).first_or_404()


def body():
    value = request.get_json(silent=True)
    if not request.is_json or not isinstance(value, dict):
        abort(400, description="Expected a JSON object.")
    return value


def number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        abort(400, description=f"{name} must be between {lo} and {hi}.")
    return round(value, 3)


def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        abort(400, description=f"{name} is required (maximum {limit} characters).")
    return value.strip()


def clean_cues(raw):
    if not isinstance(raw, list) or len(raw) > 500:
        abort(400, description="Use a timeline with at most 500 cues.")
    assets = {asset.id: asset for asset in current_app.PresentationAsset.query.filter_by(user_id=current_user.id)}
    cleaned, ids = [], set()
    for item in raw:
        if not isinstance(item, dict):
            abort(400, description="Each cue must be an object.")
        cue_id = text(item.get("id"), "Cue ID", 64)
        lane = item.get("lane")
        kind = item.get("kind")
        if cue_id in ids or lane not in ("picture", "text", "audio"):
            abort(400, description="Cue IDs must be unique and lanes must be picture, text or audio.")
        ids.add(cue_id)
        if kind not in {"picture": ("image", "video", "youtube", "blank"), "text": ("text",), "audio": ("audio",)}[lane]:
            abort(400, description="This media type cannot be placed in that lane.")
        start = number(item.get("start"), "Start time", 0, 86400)
        duration = number(item.get("duration"), "Duration", 0.1, 86400)
        if start + duration > 86400:
            abort(400, description="The timeline must fit within 24 hours.")
        transition = item.get("transition", "cut")
        advance = item.get("advance", "auto")
        if transition not in ("cut", "fade", "crossfade") or advance not in ("auto", "manual"):
            abort(400, description="Choose a valid transition and advance mode.")
        if lane == "audio" and transition == "crossfade":
            abort(400, description="Audio cues support fade-in and fade-out; picture and text cues also support crossfade.")
        cue = {"id": cue_id, "lane": lane, "kind": kind, "start": start, "duration": duration,
               "name": text(item.get("name", kind.title()), "Cue name", 120),
               "transition": transition, "fade": number(item.get("fade", 0.8), "Fade duration", 0, duration),
               "fadeOut": number(item.get("fadeOut", 0), "Fade to black duration", 0, duration),
               "advance": advance if lane == "picture" else "auto",
               "offset": number(item.get("offset", 0), "Media in point", 0, 86400),
               "volume": number(item.get("volume", 1 if lane == "audio" else 0), "Volume", 0, 1)}
        if kind in ("image", "video", "audio", "youtube"):
            asset_id = item.get("assetId")
            asset = assets.get(asset_id) if type(asset_id) is int else None
            if asset is None or asset.kind != kind:
                abort(400, description="Select media from your own library.")
            if asset.duration is not None and kind != "image" and cue["offset"] + duration > asset.duration + 0.05:
                abort(400, description=f"{cue['name']}: in point plus duration exceeds the media length.")
            cue["assetId"] = asset.id
        if kind in ("video", "youtube") and duration > 180:
            abort(400, description="Video cues may be at most three minutes long.")
        if kind == "text":
            cue["text"] = text(item.get("text"), "Overlay text", 4000)
            cue["fontSize"] = number(item.get("fontSize", 6), "Text size (% of screen height)", 1, 30)
            cue["position"] = item.get("position", "bottom")
            if cue["position"] not in ("top", "center", "bottom"):
                abort(400, description="Choose top, center or bottom text position.")
            cue["color"] = item.get("color", "#ffffff")
            if not isinstance(cue["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", cue["color"]):
                abort(400, description="Text color must be a six-digit hex color.")
        cleaned.append(cue)
    for lane in ("picture", "text", "audio"):
        ordered = sorted((cue for cue in cleaned if cue["lane"] == lane), key=lambda cue: cue["start"])
        if any(a["start"] + a["duration"] > b["start"] + 0.0001 for a, b in zip(ordered, ordered[1:])):
            abort(400, description=f"Cues in the {lane} lane overlap. Place them end to end; crossfades are applied at the join.")
    return sorted(cleaned, key=lambda cue: (cue["start"], cue["lane"]))


def show_json(show):
    return {"id": show.id, "title": show.title, "revision": show.revision, "cues": show.cues,
            "url": url_for("presentations.watch", token=show.token),
            "editUrl": url_for("presentations.editor", show_id=show.id),
            "controlUrl": url_for("presentations.control", show_id=show.id)}


def snapshot(show):
    assets = {asset.id: asset.to_dict() for asset in current_app.PresentationAsset.query.filter_by(user_id=show.user_id)}
    return {"title": show.title, "cues": [
        {**cue, **({"asset": assets[cue["assetId"]]} if "assetId" in cue else {})} for cue in show.cues
    ]}


def playback(live, now):
    show = live.get("show", {"title": "", "cues": []})
    position = live.get("position", 0)
    playing = live.get("playing", False)
    target = position + (max(0, now - live.get("at", now)) if playing else 0)
    end = max((cue["start"] + cue["duration"] for cue in show["cues"]), default=0)
    holds = [cue["start"] + cue["duration"] for cue in show["cues"]
             if cue["lane"] == "picture" and cue["advance"] == "manual"
             and cue["start"] + cue["duration"] > position + 0.002]
    boundary = min([end, *holds])
    held = playing and target >= boundary
    if held:
        target = max(0, boundary - 0.001)
        playing = False
    return {"show": show, "position": max(0, min(target, end)), "playing": playing,
            "active": live.get("active", False), "black": live.get("black", False), "held": held}


def response_json(value):
    response = jsonify(value)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                             "X-Robots-Tag": "noindex, nofollow"})
    return response


@presentations_bp.get("/")
def dashboard():
    return render_template("presentations/dashboard.html")


@presentations_bp.get("/<int:show_id>/edit/")
def editor(show_id):
    return render_template("presentations/editor.html", show=show_json(own(show_id)))


@presentations_bp.get("/<int:show_id>/control/")
def control(show_id):
    return render_template("presentations/control.html", show=show_json(own(show_id)))


@presentations_bp.route("/api/shows", methods=["GET", "POST"])
def shows():
    db, model, _ = models()
    if request.method == "GET":
        return response_json({"shows": [show_json(show) for show in model.query.filter_by(user_id=current_user.id).order_by(model.updated_at.desc())]})
    show = model(user_id=current_user.id, title=text(body().get("title"), "Presentation title", 255),
                 cues=[], token=secrets.token_urlsafe(32), live={})
    db.session.add(show)
    db.session.commit()
    return response_json(show_json(show)), 201


@presentations_bp.route("/api/shows/<int:show_id>", methods=["GET", "PUT", "DELETE"])
def show_detail(show_id):
    show = own(show_id)
    db, model, _ = models()
    if request.method == "GET":
        return response_json(show_json(show))
    data = body()
    if type(data.get("revision")) is not int:
        abort(400, description="Include the saved presentation revision.")
    values = {}
    if request.method == "PUT":
        values = {"title": text(data.get("title"), "Title", 255), "cues": clean_cues(data.get("cues")),
                  "revision": show.revision + 1, "updated_at": datetime.utcnow()}
    query = model.query.filter_by(id=show.id, revision=data["revision"])
    changed = query.update(values, synchronize_session=False) if values else query.delete(synchronize_session=False)
    if not changed:
        db.session.rollback()
        abort(409, description="This presentation changed in another tab. Reload before saving.")
    db.session.commit()
    db.session.expire_all()
    return response_json(show_json(own(show_id)) if values else {"deleted": True})


@presentations_bp.get("/api/assets")
def assets():
    return response_json({"assets": [asset.to_dict() for asset in current_app.PresentationAsset.query.filter_by(
        user_id=current_user.id).order_by(current_app.PresentationAsset.created_at.desc())]})


def cloudinary_ready():
    if not os.environ.get("CLOUDINARY_URL", "").strip():
        abort(503, description="Cloudinary is not configured. Set CLOUDINARY_URL on the server before uploading media.")


def remove_cloudinary_media(public_id, resource):
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type=resource, invalidate=True, timeout=15)
    except CloudinaryError:
        current_app.logger.exception("Presentation media deletion failed for %s", public_id)
        abort(502, description="Cloudinary could not delete the media. Retry, or remove the upload in Cloudinary.")
    if result.get("result") not in ("ok", "not found"):
        current_app.logger.error("Cloudinary did not confirm presentation media deletion for %s", public_id)
        abort(502, description="Cloudinary did not confirm deletion. The library entry was kept; retry later.")


@presentations_bp.post("/api/uploads/sign")
def sign_upload():
    cloudinary_ready()
    data = body()
    name = text(data.get("name"), "File name", 255)
    kind = KINDS.get(os.path.splitext(name)[1].lower())
    if kind is None:
        abort(400, description="Choose JPG, PNG, WebP, MP4, WebM, MOV, MP3, WAV, M4A or OGG media.")
    number(data.get("size"), "File size in bytes", 1, MAX_UPLOAD)
    settings = cloudinary.config()
    if not all((settings.cloud_name, settings.api_key, settings.api_secret)):
        abort(503, description="Cloudinary upload credentials are incomplete.")
    resource = "image" if kind == "image" else "video"
    parameters = {"timestamp": int(time.time()),
                  "public_id": f"jackcapstaff/presentations/{current_user.id}/{uuid.uuid4().hex}",
                  "overwrite": "false"}
    return response_json({
        "endpoint": f"https://api.cloudinary.com/v1_1/{settings.cloud_name}/{resource}/upload",
        "parameters": {**parameters, "signature": cloudinary.utils.api_sign_request(parameters, settings.api_secret),
                       "api_key": settings.api_key},
        "kind": kind, "resource": resource,
    })


@presentations_bp.post("/api/assets/youtube")
def youtube_asset():
    data = body()
    value = data.get("url", "")
    try:
        parsed = urlparse(value) if isinstance(value, str) else None
    except ValueError:
        abort(400, description="Enter a valid HTTPS YouTube video link.")
    video_id = None
    if parsed and parsed.scheme == "https":
        if parsed.hostname in ("youtu.be", "www.youtu.be"):
            video_id = parsed.path.strip("/")
        elif parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if parsed.path.startswith(("/embed/", "/shorts/")):
                video_id = parsed.path.split("/")[2]
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        abort(400, description="Enter a valid HTTPS YouTube video link.")
    asset = current_app.PresentationAsset(user_id=current_user.id,
        name=text(data.get("name", "YouTube video"), "Media name", 255), kind="youtube", url=video_id)
    current_app.db.session.add(asset)
    current_app.db.session.commit()
    return response_json(asset.to_dict()), 201


@presentations_bp.post("/api/assets")
def upload_asset():
    cloudinary_ready()
    data = body()
    name = text(data.get("name"), "File name", 255)
    kind = KINDS.get(os.path.splitext(name)[1].lower())
    if kind is None:
        abort(400, description="Choose JPG, PNG, WebP, MP4, WebM, MOV, MP3, WAV, M4A or OGG media.")
    public_id = data.get("publicId")
    if not isinstance(public_id, str) or not re.fullmatch(
        rf"jackcapstaff/presentations/{current_user.id}/[a-f0-9]{{32}}", public_id
    ):
        abort(400, description="Use an upload signed for your own presentation library.")
    existing = current_app.PresentationAsset.query.filter_by(user_id=current_user.id, public_id=public_id).first()
    if existing is not None:
        return response_json(existing.to_dict())
    resource = "image" if kind == "image" else "video"
    try:
        result = cloudinary.api.resource(public_id, resource_type=resource, timeout=15)
    except CloudinaryError:
        current_app.logger.exception("Presentation media verification failed")
        abort(502, description="Cloudinary could not verify the upload. Check the connection and retry.")
    duration = result.get("duration")
    valid_duration = isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0
    invalid = (not isinstance(result.get("bytes"), (int, float)) or not 0 < result["bytes"] <= MAX_UPLOAD
               or (kind != "image" and not valid_duration)
               or (kind == "video" and valid_duration and duration > 180)
               or not isinstance(result.get("secure_url"), str)
               or not result["secure_url"].startswith("https://")
               or result.get("resource_type") != resource
               or (kind == "image" and result.get("format") not in ("jpg", "jpeg", "png", "webp"))
               or (kind == "video" and not (result.get("width", 0) > 0 and result.get("height", 0) > 0))
               or (kind == "audio" and (result.get("width", 0) > 0 or result.get("format") not in ("mp3", "wav", "aac", "m4a", "mp4", "ogg", "flac"))))
    if invalid:
        remove_cloudinary_media(public_id, resource)
        abort(400, description="Media could not be verified, is not the selected type, or exceeds the three-minute uploaded-video limit.")
    asset = current_app.PresentationAsset(user_id=current_user.id, name=name,
        kind=kind, url=result["secure_url"], public_id=public_id, duration=duration if valid_duration else None)
    current_app.db.session.add(asset)
    current_app.db.session.commit()
    return response_json(asset.to_dict()), 201


@presentations_bp.delete("/api/assets/<int:asset_id>")
def delete_asset(asset_id):
    body()
    asset = current_app.PresentationAsset.query.filter_by(id=asset_id, user_id=current_user.id).first_or_404()
    for show in current_app.StudioPresentation.query.filter_by(user_id=current_user.id):
        used = show.cues + (show.live.get("show", {}).get("cues", []) if show.live.get("active") else [])
        if any(cue.get("assetId") == asset.id for cue in used):
            abort(409, description="Remove this media from saved timelines and restart or stop their broadcasts before deleting it.")
    if asset.public_id:
        remove_cloudinary_media(asset.public_id, "image" if asset.kind == "image" else "video")
    current_app.db.session.delete(asset)
    current_app.db.session.commit()
    return response_json({"deleted": True})


@presentations_bp.route("/api/shows/<int:show_id>/live", methods=["GET", "PUT"])
def live_state(show_id):
    show = own(show_id)
    now = time.time()
    state = playback(show.live, now)
    if request.method == "GET":
        return response_json({**state, "version": show.live_version})
    data = body()
    if type(data.get("version")) is not int or data["version"] != show.live_version:
        abort(409, description="The controller changed in another tab. Reload before taking control.")
    action = data.get("action")
    live = {**show.live, **state, "at": now, "seen": now}
    if action == "start":
        if not show.cues:
            abort(400, description="Add and save timeline cues before broadcasting.")
        live = {"show": snapshot(show), "position": 0, "playing": True, "active": True, "black": False, "at": now, "seen": now}
    elif action == "stop":
        live.update(active=False, playing=False, position=0)
    elif action == "black":
        live["black"] = not live.get("black", False)
    elif action == "revoke":
        live.update(active=False, playing=False)
    elif action in ("play", "pause", "seek", "next", "previous", "heartbeat"):
        if not live.get("active"):
            abort(400, description="Start a broadcast first.")
        if action in ("play", "pause"):
            live["playing"] = action == "play"
        elif action == "seek":
            end = max(cue["start"] + cue["duration"] for cue in live["show"]["cues"])
            live["position"] = number(data.get("position"), "Playback position", 0, end)
        elif action in ("next", "previous"):
            starts = sorted(cue["start"] for cue in live["show"]["cues"] if cue["lane"] == "picture")
            options = [start for start in starts if start > live["position"] + 0.0001] if action == "next" else [
                start for start in starts if start < live["position"] - 0.05]
            if options:
                live["position"] = options[0] if action == "next" else options[-1]
                live["playing"] = True
            elif action == "next":
                live["playing"] = False
    else:
        abort(400, description="Choose a valid playback action.")
    values = {"live": live, "live_version": show.live_version + 1}
    if action == "revoke":
        values["token"] = secrets.token_urlsafe(32)
    changed = current_app.db.session.execute(update(current_app.StudioPresentation).where(
        current_app.StudioPresentation.id == show.id,
        current_app.StudioPresentation.live_version == data["version"]).values(**values)).rowcount
    if not changed:
        current_app.db.session.rollback()
        abort(409, description="Another controller updated this broadcast. Reload before taking control.")
    current_app.db.session.commit()
    return response_json({**playback(live, now), "version": values["live_version"],
                          "url": url_for("presentations.watch", token=values.get("token", show.token))})


@presentations_bp.get("/watch/<token>/")
def watch(token):
    current_app.StudioPresentation.query.filter_by(token=token).first_or_404()
    response = make_response(render_template("presentations/watch.html",
        state_url=url_for("presentations.watch_state", token=token)))
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})
    return response


@presentations_bp.get("/watch/<token>/state")
def watch_state(token):
    show = current_app.StudioPresentation.query.filter_by(token=token).first_or_404()
    state = playback(show.live, time.time())
    waiting = time.time() - show.live.get("seen", 0) > 8
    if not state["active"] or waiting:
        state.update(show={"title": "", "cues": []}, active=False, playing=False, black=True)
    return response_json({**state, "waiting": waiting})
