"""Layered presentation authoring and an independently clocked public player."""
import math
import hashlib
import json
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

from clicktrack import _clean_section

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
        if type(item.get("muted", False)) is not bool:
            abort(400, description="Cue mute must be true or false.")
        cue = {"id": cue_id, "lane": lane, "kind": kind, "start": start, "duration": duration,
               "name": text(item.get("name", kind.title()), "Cue name", 120),
               "transition": transition, "fade": number(item.get("fade", 0.8), "Fade duration", 0, duration),
               "fadeOut": number(item.get("fadeOut", 0), "Fade to black duration", 0, duration),
               "advance": advance if lane == "picture" else "auto",
               "offset": number(item.get("offset", 0), "Media in point", 0, 86400),
               "volume": number(item.get("volume", 1 if lane == "audio" else 0), "Volume", 0, 1),
               "muted": item.get("muted", False)}
        if kind in ("image", "video", "audio", "youtube"):
            asset_id = item.get("assetId")
            asset = assets.get(asset_id) if type(asset_id) is int else None
            if asset is None or asset.kind != kind:
                abort(400, description="Select media from your own library.")
            if asset.duration is not None and kind != "image" and cue["offset"] + duration > asset.duration + 0.05:
                abort(400, description=f"{cue['name']}: in point plus duration exceeds the media length.")
            cue["assetId"] = asset.id
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
            "programme": show.programme,
            "music": show.music,
            "url": url_for("presentations.watch", token=show.token),
            "editUrl": url_for("presentations.editor", show_id=show.id),
            "controlUrl": url_for("presentations.control", show_id=show.id)}


def clean_programme(raw, show):
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("sets"), list) or not 1 <= len(raw["sets"]) <= 50:
        abort(400, description="A concert needs between 1 and 50 linked presentations.")
    if any(type(value) is not int for value in raw["sets"]):
        abort(400, description="Choose saved presentations for the concert.")
    image_id = raw.get("placeholderId")
    image = current_app.PresentationAsset.query.filter_by(id=image_id, user_id=show.user_id, kind="image").first() if type(image_id) is int else None
    if image is None:
        abort(400, description="Choose a concert placeholder image from your media library.")
    shows = {item.id: item for item in current_app.StudioPresentation.query.filter_by(user_id=show.user_id)}
    checked = set()
    def visit(show_id, chain):
        if show_id in chain or len(chain) >= 8:
            abort(400, description="Concert links must not form a cycle or nest more than eight levels deep.")
        child = shows.get(show_id)
        if child is None:
            abort(400, description="Every linked presentation must belong to you and still exist.")
        if (show_id, len(chain)) in checked:
            return
        programme = raw if child.id == show.id else child.programme
        if programme:
            for child_id in programme["sets"]:
                visit(child_id, (*chain, show_id))
        checked.add((show_id, len(chain)))
    visit(show.id, ())
    return {"sets": raw["sets"], "placeholderId": image.id}


def music_signature(track):
    return hashlib.sha256(json.dumps(track.sections, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clean_music(raw, show, cues):
    if raw is None:
        return None
    if not isinstance(raw, dict) or type(raw.get("trackId")) is not int:
        abort(400, description="Choose a saved click track from your account.")
    track = current_app.ClickTrack.query.filter_by(id=raw["trackId"], user_id=show.user_id).first()
    if track is None or not track.sections:
        abort(400, description="Choose a non-empty saved click track from your account.")
    signature = music_signature(track)
    if raw.get("signature") != signature:
        abort(409, description="The click track sections changed. Reload the track in Music links and review its event positions before saving.")
    events = raw.get("events")
    holds = raw.get("holds", {})
    if not isinstance(events, list) or len(events) > 500 or not isinstance(holds, dict):
        abort(400, description="Use up to 500 musical events and valid hold options.")
    targets = {cue["id"]: cue for cue in cues if cue["lane"] in ("picture", "text")}
    cleaned, positions, ids = [], set(), set()
    for event in events:
        if not isinstance(event, dict):
            abort(400, description="Each musical event must be an object.")
        event_id = text(event.get("id"), "Musical event ID", 64)
        section, bar, beat = (event.get(key) for key in ("section", "bar", "beat"))
        if type(section) is not int or not 0 <= section < len(track.sections):
            abort(400, description="Choose an existing musical section.")
        music_section = track.sections[section]
        bars = music_section["bars"] if music_section["type"] == "click" else 1
        beats = music_section["num"] if music_section["type"] == "click" else 1
        if type(bar) is not int or type(beat) is not int or not 1 <= bar <= bars or not 1 <= beat <= beats:
            abort(400, description="Musical bars and beats must fit the selected section (beats are meter units).")
        key = (section, bar, beat)
        if key in positions or event_id in ids or event.get("cueId") not in targets:
            abort(400, description="Use unique musical positions and event IDs, linked to a picture or text cue in this presentation.")
        mode = event.get("mode", "run")
        if mode not in ("run", "hold"):
            abort(400, description="Choose run visual timeline or hold visual timeline.")
        positions.add(key)
        ids.add(event_id)
        cue = targets[event["cueId"]]
        media_time = event.get("mediaTime")
        if media_time is not None:
            if cue["kind"] not in ("video", "youtube"):
                abort(400, description="Video-local hit times require a video or YouTube cue.")
            media_time = number(media_time, "Video-local hit time", cue["offset"], cue["offset"] + cue["duration"])
            if media_time >= cue["offset"] + cue["duration"]:
                abort(400, description="Place the hit point before the end of the video cue.")
        cleaned.append({"id": event_id, "section": section, "bar": bar, "beat": beat, "cueId": event["cueId"], "mode": mode,
                        **({"mediaTime": media_time} if media_time is not None else {})})
    for key, value in holds.items():
        if not isinstance(key, str) or not key.isdecimal() or not 0 <= int(key) < len(track.sections) or value not in ("pause", "continue"):
            abort(400, description="Choose pause or continue visuals for each musical hold.")
    return {"trackId": track.id, "signature": signature,
            "events": sorted(cleaned, key=lambda event: (event["section"], event["bar"], event["beat"])),
            "holds": holds}


def snapshot(show):
    assets = {asset.id: asset.to_dict() for asset in current_app.PresentationAsset.query.filter_by(user_id=show.user_id)}
    shows = {item.id: item for item in current_app.StudioPresentation.query.filter_by(user_id=show.user_id)}
    expanded, sections, musical_parts = [], [], []
    def expand(item, start, prefix, chain):
        if item.id in chain or len(chain) >= 8:
            abort(400, description="Concert links form a cycle or exceed eight nested levels.")
        if item.programme:
            if item.music:
                abort(400, description="Music links currently belong to standalone set presentations, not concert programmes.")
            image = assets.get(item.programme["placeholderId"])
            if image is None or image["kind"] != "image":
                abort(400, description=f"{item.title}: select an available concert placeholder image.")
            position = start
            for index, child_id in enumerate(item.programme["sets"]):
                child = shows.get(child_id)
                if child is None:
                    abort(400, description=f"{item.title}: a linked presentation is missing.")
                position = expand(child, position, f"{prefix}{index}/", (*chain, item.id))
                if index < len(item.programme["sets"]) - 1:
                    for cue in expanded:
                        if cue["lane"] == "picture" and abs(cue["start"] + cue["duration"] - position) < 0.002:
                            cue["advance"] = "auto"
                    interval = {"id": f"{prefix}interval-{index}", "name": f"Interval after {child.title}",
                        "lane": "picture", "kind": "image", "assetId": image["id"], "asset": image,
                        "start": position, "duration": 0.1, "transition": "cut", "fade": 0, "fadeOut": 0,
                        "advance": "manual", "offset": 0, "volume": 0, "muted": True}
                    expanded.append(interval)
                    musical_parts.append({"name": interval["name"], "start": position, "end": position + 0.1,
                                          "cueId": interval["id"], "interval": True})
                    position = round(position + 0.1, 3)
            if len(expanded) > 5000 or position > 86400:
                abort(400, description="The expanded concert must fit within 24 hours and 5000 cues.")
            return position
        if not item.cues:
            abort(400, description=f"{item.title}: add and save cues before including it in a concert.")
        sections.append({"name": item.title, "start": start})
        end = max(cue["start"] + cue["duration"] for cue in item.cues)
        for cue in item.cues:
            asset = assets.get(cue.get("assetId"))
            if "assetId" in cue and asset is None:
                abort(400, description=f"{item.title}: a media asset is missing.")
            expanded.append({**cue, "id": prefix + cue["id"], "start": round(start + cue["start"], 3),
                             **({"asset": asset} if asset else {}), "setName": item.title})
        if len(expanded) > 5000 or start + end > 86400:
            abort(400, description="The expanded concert must fit within 24 hours and 5000 cues.")
        binding = clean_music(item.music, item, item.cues) if item.music else None
        if binding and not binding["events"]:
            abort(400, description=f"{item.title}: add at least one musical event before broadcasting.")
        track = current_app.ClickTrack.query.filter_by(id=binding["trackId"], user_id=show.user_id).first() if binding else None
        musical_parts.append({"name": item.title, "start": start, "end": round(start + end, 3),
                              "prefix": prefix, "cueId": prefix + item.cues[0]["id"],
                              "binding": binding, "track": track.to_dict() if track else None})
        return round(start + end, 3)
    expand(show, 0, "", ())
    result = {"title": show.title, "cues": expanded, "sections": sections}
    if any(part.get("binding") for part in musical_parts):
        events, holds, score = [], {}, []
        cues_by_id = {cue["id"]: cue for cue in expanded}
        for part in musical_parts:
            offset = len(score)
            binding = part.get("binding")
            if binding:
                score.extend({**section, "name": (part["name"] + " · " + section["name"])[:120]}
                             if show.programme else dict(section) for section in part["track"]["sections"])
                holds.update({str(offset + int(key)): value for key, value in binding["holds"].items()})
                for event in binding["events"]:
                    cue_id = part["prefix"] + event["cueId"]
                    cue = cues_by_id[cue_id]
                    events.append({**event, "id": part["prefix"] + event["id"], "section": offset + event["section"],
                                   "cueId": cue_id, "end": part["end"],
                                   "position": cue["start"] + (event.get("mediaTime", cue["offset"]) - cue["offset"])})
            else:
                # Non-musical sets run silently until Next; concert intervals hold on the logo.
                score.append(_clean_section({"type": "pause", "name": part["name"], "countIn": 0}))
                interval = part.get("interval", False)
                holds[str(offset)] = "pause" if interval else "continue"
                events.append({"id": "generated-" + part["cueId"], "section": offset, "bar": 1, "beat": 1,
                               "cueId": part["cueId"], "mode": "hold" if interval else "run",
                               "position": part["start"], "end": part["end"]})
            if len(score) > 5000 or len(events) > 5000:
                abort(400, description="The expanded concert must fit within 5000 musical sections and events.")
        # A timed pause needs the following musical section, never the next set's interval.
        for index, section in enumerate(score):
            if section["type"] == "timedPause" and (index + 1 == len(score) or score[index + 1]["type"] != "click"):
                abort(400, description="Each timed musical pause must be followed by a click section within its set.")
        track = ({**musical_parts[0]["track"]} if not show.programme else
                 {"id": f"concert-{show.id}", "title": show.title, "notes": "", "isPublic": False})
        track.update(sections=score, videoOffset=0, videoDraftId=None)
        result["music"] = {"events": events, "holds": holds, "track": track, "session": secrets.token_urlsafe(16)}
    return result


def playback(live, now):
    show = live.get("show", {"title": "", "cues": []})
    position = live.get("position", 0)
    playing = live.get("playing", False)
    target = position + (max(0, now - live.get("at", now)) if playing else 0)
    end = max((cue["start"] + cue["duration"] for cue in show["cues"]), default=0)
    holds = [cue["start"] + cue["duration"] for cue in show["cues"]
             if cue["lane"] == "picture" and cue["advance"] == "manual"
             and cue["start"] + cue["duration"] > position + 0.002]
    boundary = min([end, *holds, live.get("musicBoundary", end)])
    held = playing and target >= boundary
    if held:
        target = max(0, boundary - 0.001)
        playing = False
    return {"show": show, "position": max(0, min(target, end)), "playing": playing,
            "active": live.get("active", False), "black": live.get("black", False), "held": held,
            "placeholder": live.get("placeholder", False), "placeholderAsset": live.get("placeholderAsset"),
            "musicFrozen": live.get("musicFrozen", False), "musicBoundary": live.get("musicBoundary", end)}


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
        programme = clean_programme(data.get("programme", show.programme), show)
        cues = clean_cues(data.get("cues"))
        if programme and cues:
            abort(400, description="Use either a linked concert programme or a standalone cue timeline, not both.")
        music = clean_music(data.get("music", show.music), show, cues)
        if programme and music:
            abort(400, description="Link music inside each set presentation; the concert compiles its linked sets' music automatically.")
        values = {"title": text(data.get("title"), "Title", 255), "cues": cues,
                  "programme": programme,
                  "music": music,
                  "revision": show.revision + 1, "updated_at": datetime.utcnow()}
    elif any(show.id in (parent.programme or {}).get("sets", [])
             for parent in model.query.filter_by(user_id=current_user.id)):
        abort(409, description="Remove this presentation from its concert programmes before deleting it.")
    query = model.query.filter_by(id=show.id, revision=data["revision"])
    changed = query.update(values, synchronize_session=False) if values else query.delete(synchronize_session=False)
    if not changed:
        db.session.rollback()
        abort(409, description="This presentation changed in another tab. Reload before saving.")
    db.session.commit()
    db.session.expire_all()
    return response_json(show_json(own(show_id)) if values else {"deleted": True})


@presentations_bp.get("/api/shows/<int:show_id>/compiled")
def compiled_show(show_id):
    return response_json(snapshot(own(show_id)))


@presentations_bp.get("/api/music-tracks")
def music_tracks():
    return response_json({"tracks": [{**track.to_dict(), "signature": music_signature(track)}
        for track in current_app.ClickTrack.query.filter_by(user_id=current_user.id).order_by(current_app.ClickTrack.title)]})


@presentations_bp.get("/<int:show_id>/music/<session>/")
def music_perform(show_id, session):
    show = own(show_id)
    music = show.live.get("show", {}).get("music")
    if not show.live.get("active") or not music or music["session"] != session:
        abort(404, description="Start a linked presentation broadcast before opening its click track.")
    track = {**music["track"], "videoDraftId": None}
    response = make_response(render_template("clicktrack/index.html", invited_track=track,
        music_bridge={"session": session, "showId": show.id,
                      "events": [{key: event[key] for key in ("id", "section", "bar", "beat")} for event in music["events"]]}))
    response.headers["Cache-Control"] = "no-store"
    return response


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
        if (any(cue.get("assetId") == asset.id for cue in used)
                or (show.live.get("placeholderAsset") or {}).get("id") == asset.id
                or (show.programme or {}).get("placeholderId") == asset.id):
            abort(409, description="Remove this media from saved/running timelines and clear any placeholder selection before deleting it.")
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
        if not show.cues and not show.programme:
            abort(400, description="Add and save timeline cues before broadcasting.")
        placeholder = show.live.get("placeholderAsset")
        if show.programme and not placeholder:
            asset = current_app.PresentationAsset.query.filter_by(id=show.programme["placeholderId"], user_id=show.user_id).first()
            placeholder = asset.to_dict() if asset else None
        compiled = snapshot(show)
        if compiled.get("music") and not compiled["music"]["events"]:
            abort(400, description="Add at least one musical event before starting a linked broadcast.")
        live = {"show": compiled, "position": 0, "playing": not bool(compiled.get("music")), "active": True, "black": False,
                "placeholder": False, "placeholderAsset": placeholder, "at": now, "seen": now}
    elif action == "stop":
        live.update(active=False, playing=False, position=0, placeholder=False)
    elif action == "black":
        live["black"] = not live.get("black", False)
    elif action == "revoke":
        live.update(active=False, playing=False, placeholder=False)
    elif action == "setPlaceholder":
        asset_id = data.get("assetId")
        asset = current_app.PresentationAsset.query.filter_by(id=asset_id, user_id=current_user.id, kind="image").first() if type(asset_id) is int else None
        if asset_id is not None and asset is None:
            abort(400, description="Choose a placeholder image from your own media library.")
        if live.get("placeholder"):
            abort(400, description="Resume the timeline before changing its placeholder image.")
        live["placeholderAsset"] = asset.to_dict() if asset else None
    elif action in ("play", "pause", "seek", "jump", "placeholder", "next", "previous", "heartbeat", "music"):
        if not live.get("active"):
            abort(400, description="Start a broadcast first.")
        if action == "music":
            music = live["show"].get("music")
            if not music or data.get("session") != music["session"]:
                abort(409, description="The musical playback snapshot changed. Restart the linked click-track view.")
            if type(data.get("playing")) is not bool:
                abort(400, description="Musical playback state must be true or false.")
            event_id = data.get("eventId")
            if event_id is not None:
                event = next((event for event in music["events"] if event["id"] == event_id), None)
                if event is None:
                    abort(400, description="Choose an event from the current musical snapshot.")
                cue = next(cue for cue in live["show"]["cues"] if cue["id"] == event["cueId"])
                elapsed = number(data.get("elapsed", 0), "Musical event delay", 0, 86400)
                end = max(cue["start"] + cue["duration"] for cue in live["show"]["cues"])
                live["musicBoundary"] = event.get("end", end)
                live["position"] = min(live["musicBoundary"],
                                       event.get("position", cue["start"]) + (elapsed if event["mode"] == "run" else 0))
                live["musicEvent"] = event_id
                live["placeholder"] = False
            active_event = next((event for event in music["events"] if event["id"] == live.get("musicEvent")), None)
            live["musicFrozen"] = bool(active_event and active_event["mode"] == "hold")
            if data["playing"]:
                live["placeholder"] = False
            live["playing"] = data["playing"] and (active_event is None or active_event["mode"] == "run") and not live.get("placeholder", False)
        elif action in ("play", "pause"):
            live["playing"] = action == "play"
            if action == "play":
                live["placeholder"] = False
        elif action == "placeholder":
            if not live.get("placeholderAsset"):
                abort(400, description="Choose a placeholder image first.")
            live.update(placeholder=True, playing=False, black=False)
        elif action == "jump":
            cue = next((cue for cue in live["show"]["cues"] if cue["id"] == data.get("cueId") and cue["lane"] in ("picture", "text")), None)
            if cue is None:
                abort(400, description="Choose a slide from the running presentation.")
            live.update(position=cue["start"], playing=True, placeholder=False)
        elif action == "seek":
            end = max(cue["start"] + cue["duration"] for cue in live["show"]["cues"])
            live["position"] = number(data.get("position"), "Playback position", 0, end)
            live["placeholder"] = False
        elif action in ("next", "previous"):
            starts = sorted(cue["start"] for cue in live["show"]["cues"] if cue["lane"] == "picture")
            options = [start for start in starts if start > live["position"] + 0.0001] if action == "next" else [
                start for start in starts if start < live["position"] - 0.05]
            if options:
                live["position"] = options[0] if action == "next" else options[-1]
                live["playing"] = True
                live["placeholder"] = False
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
    state["show"] = {key: value for key, value in state["show"].items() if key != "music"}
    waiting = time.time() - show.live.get("seen", 0) > 8
    if not state["active"] or waiting:
        state.update(show={"title": "", "cues": []}, active=False, playing=False, black=True, placeholder=False, placeholderAsset=None)
    return response_json({**state, "waiting": waiting})
