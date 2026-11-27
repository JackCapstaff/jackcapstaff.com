"""
Performance Studio / programmable metronome.

The canonical prefix is /live; /clicktrack remains a compatibility alias.
New public broadcasts use /live/watch/YYYY-MM-DD/ (Europe/London date).
Only admins can create or broadcast; readers need no login.

  /clicktrack/                    performance + editor page (login required)
  /clicktrack/watch/<token>/       public live video          (no login)
  /clicktrack/watch/<token>/state  public playback state      (no login)
  /clicktrack/api/presentations    POST public live link      (login required)
  /clicktrack/api/presentations/<id> PUT state, DELETE revoke (owner only)
  /clicktrack/api/tracks          GET list, POST create        (login required)
  /clicktrack/api/tracks/<id>     PUT update, DELETE           (login required)
  /clicktrack/api/tracks/restore-backup POST linked backup     (login required)
  /clicktrack/api/tracks/<id>/video-editor PUT unified edit    (login required)
  /clicktrack/api/tracks/<id>/video-link PUT link draft       (login required)
  /clicktrack/api/tracks/from-video-draft POST linked track    (login required)
  /clicktrack/api/video-drafts  GET list, POST create         (login required)
  /clicktrack/api/video-drafts/<id>/unlinked PUT draft edit    (login required)
  /clicktrack/api/video-drafts/<id> GET, PUT, DELETE           (login required)

Tracks are saved to the signed-in user's account, with a browser-local cache.
"""
import math
import re
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

clicktrack_bp = Blueprint("clicktrack", __name__, url_prefix="/live")

MAX_TRACKS_PER_USER = 200
MAX_SECTIONS = 500
MAX_VIDEO_DRAFTS = 50
YOUTUBE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
PRESENTATION_LIFETIME = 12 * 60 * 60
PRESENTATION_STALE_AFTER = 5

SECTION_TYPES = {"click", "pause", "timedPause"}
ADVANCE_MODES = {"auto", "manual"}
CLICK_MODES = {"beats", "units", "sub2", "sub3", "sub4", "silent"}
VALID_DENOMINATORS = {1, 2, 4, 8, 16, 32}
BEAT_UNIT_RE = re.compile(r"^(auto|[1-9]/(1|2|4|8|16|32))$")
MODULATION_NOTE_RE = re.compile(r"^1/(1|2|4|8|16):(straight|dotted|triplet|quintuplet|septuplet)$")
GROUPING_RE = re.compile(r"^\s*\d+(\s*\+\s*\d+)*\s*$")


def _models():
    return current_app.db, current_app.ClickTrack


def _api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Login required."}), 401
        return view(*args, **kwargs)
    return wrapped


@clicktrack_bp.before_request
def restrict_track_editing():
    endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
    if endpoint in ("api_create_presentation", "api_publish_presentation") and current_user.is_authenticated and current_user.role != "admin":
        return jsonify({"error": "Only administrators can create or broadcast public presentations."}), 403
    if not request.path.startswith(("/clicktrack/api/tracks", "/clicktrack/api/video-drafts", "/live/api/tracks", "/live/api/video-drafts")):
        return
    editing = request.method not in ("GET", "HEAD", "OPTIONS")
    listing_drafts = endpoint == "api_video_drafts"
    if (editing or listing_drafts) and current_user.is_authenticated and current_user.role != "admin":
        return jsonify({"error": "Only administrators can edit performance tracks and video drafts."}), 403


def _published_tracks():
    _, model = _models()
    user = current_app.User
    return model.query.join(user, model.user_id == user.id).filter(model.is_public.is_(True), user.role == "admin")


def _clamp_int(value, default, lo, hi):
    try:
        v = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, v))


def _clamp_float(value, default, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, round(v, 2)))


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _clamp_float_ms(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(86400, number)) if math.isfinite(number) else 0


def _clean_section(raw):
    raw = raw if isinstance(raw, dict) else {}
    num = _clamp_int(raw.get("num"), 4, 1, 32)
    den = _clamp_int(raw.get("den"), 4, 1, 32)
    if den not in VALID_DENOMINATORS:
        den = 4

    grouping = _text(raw.get("grouping"), 60)
    if grouping:
        if not GROUPING_RE.match(grouping):
            grouping = ""
        else:
            parts = [int(p) for p in grouping.replace(" ", "").split("+")]
            grouping = "+".join(str(p) for p in parts) if all(parts) and sum(parts) == num else ""

    bpm_end = raw.get("bpmEnd")
    bpm_end = None if bpm_end in (None, "") else _clamp_float(bpm_end, None, 10, 400)

    beat_unit = _text(raw.get("beatUnit"), 8) or "auto"
    if not BEAT_UNIT_RE.match(beat_unit):
        beat_unit = "auto"

    section_type = raw.get("type") if raw.get("type") in SECTION_TYPES else "click"
    return {
        "type": section_type,
        "name": _text(raw.get("name"), 120),
        "notes": _text(raw.get("notes"), 500),
        "calcStart": _text(raw.get("calcStart"), 32),
        "calcEnd": _text(raw.get("calcEnd"), 32),
        "bpm": _clamp_float(raw.get("bpm"), 120, 10, 400),
        "bpmEnd": bpm_end,
        "rampMode": "linearTime" if raw.get("rampMode") == "linearTime" else "linearBeat",
        "pauseDuration": round(_clamp_float_ms(raw.get("pauseDuration")), 3),
        "pauseSubdivision": _clamp_int(raw.get("pauseSubdivision"), 1, 1, 4),
        "beatUnit": beat_unit,
        "num": num,
        "den": den,
        "grouping": grouping,
        "bars": _clamp_int(raw.get("bars"), 4, 1, 9999),
        "advance": raw.get("advance") if raw.get("advance") in ADVANCE_MODES else "auto",
        "click": raw.get("click") if raw.get("click") in CLICK_MODES else (
            "units" if den >= 8 and num > 3 and num % 3 == 0 else "beats"
        ),
        "compoundClickDefaulted": raw.get("compoundClickDefaulted") is True,
        "unitPitch": _clamp_float(raw.get("unitPitch"), 0, -24, 24),
        "unitVelocity": _clamp_int(raw.get("unitVelocity"), 53, 0, 127),
        "subPitch": _clamp_float(raw.get("subPitch"), 0, -24, 24),
        "subVelocity": _clamp_int(raw.get("subVelocity"), 32, 0, 127),
        "countIn": None if raw.get("countIn") in (None, "") else _clamp_int(raw.get("countIn"), 0, 0, 8),
        "startBar": _clamp_int(raw.get("startBar"), None, 1, 99999) if raw.get("startBar") not in (None, "") else None,
        "hitPoint": _text(raw.get("hitPoint"), 17),
        "figure": _text(raw.get("figure"), 32),
        "visualCue": raw.get("visualCue") is True,
        "visualEndCue": raw.get("visualEndCue") is True,
    }


def _apply_payload(track, data):
    title = _text(data.get("title"), 255)
    if not title:
        abort(400, description="Title is required.")
    sections = data.get("sections")
    if not isinstance(sections, list):
        abort(400, description="Sections must be a list.")
    if len(sections) > MAX_SECTIONS:
        abort(400, description=f"A track can have at most {MAX_SECTIONS} sections.")
    for section in sections:
        if isinstance(section, dict) and section.get("type") == "timedPause":
            duration = section.get("pauseDuration")
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 0.001 <= duration <= 86400:
                abort(400, description="A timed pause duration must be between 0.001 and 86400 seconds.")
    track.title = title
    track.notes = _text(data.get("notes"), 2000)
    if "videoOffset" in data:
        offset = data["videoOffset"]
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or not -86400 <= offset <= 86400:
            abort(400, description="Video offset must be between -86400 and 86400 seconds.")
        track.video_offset = offset
    if "videoDraftId" in data:
        draft_id = data["videoDraftId"]
        if draft_id is not None and (
            type(draft_id) is not int or not 1 <= draft_id <= 2147483647
            or not current_app.ClickTrackVideoDraft.query.filter_by(id=draft_id, user_id=track.user_id).first()
        ):
            abort(400, description="Choose a saved video draft from your account.")
        track.video_draft_id = draft_id
    track.sections = [_clean_section(s) for s in sections]


def _json_body():
    data = request.get_json(silent=True)
    # Requiring a JSON content type also forces a CORS preflight for cross-site writes.
    if not request.is_json or not isinstance(data, dict):
        abort(400, description="Expected a JSON object.")
    return data


def _own_track_or_404(track_id):
    _, ClickTrack = _models()
    track = ClickTrack.query.filter_by(id=track_id, user_id=current_user.id).first()
    if track is None:
        abort(404)
    return track


@clicktrack_bp.errorhandler(400)
def _bad_request(err):
    return jsonify({"error": getattr(err, "description", "Bad request")}), 400


@clicktrack_bp.errorhandler(409)
def _conflict(err):
    return jsonify({"error": getattr(err, "description", "Conflict")}), 409


@clicktrack_bp.route("/")
@login_required
def clicktrack_index():
    return render_template("clicktrack/index.html")


@clicktrack_bp.get("/home/")
@login_required
def performance_home():
    from presentationstudio import available_shows
    return render_template("performance/home.html",
                           has_media_access=current_user.role == "admin" or bool(available_shows()))


@clicktrack_bp.get("/invite/<int:track_id>/")
def performance_invite(track_id):
    track = _published_tracks().filter(current_app.ClickTrack.id == track_id).first_or_404()
    if not current_user.is_authenticated:
        destination = url_for("clicktrack.performance_invite", track_id=track_id)
        return _presentation_response(make_response(render_template(
            "clicktrack/invite.html", destination=destination,
        )))
    return _presentation_response(make_response(render_template(
        "clicktrack/index.html", invited_track=track.to_dict(),
    )))


@clicktrack_bp.get("/presentation/")
@login_required
def presentation():
    response = make_response(render_template("clicktrack/presentation.html"))
    response.headers["Cache-Control"] = "no-store"
    return response


def _presentation_response(response):
    response.headers.update({
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
    })
    return response


def _public_video_state(data):
    video_id = data.get("videoId")
    if not isinstance(video_id, str) or not YOUTUBE_ID_RE.fullmatch(video_id):
        abort(400, description="A saved YouTube video is required for public sharing.")
    drafts = current_app.ClickTrackVideoDraft
    available = drafts.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not available:
        abort(400, description="Save and link this video draft before sharing.")
    position = data.get("time")
    if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position) or not 0 <= position <= 86400:
        abort(400, description="Enter a valid presentation video position.")
    if any(type(data.get(field)) is not bool for field in ("playing", "blank")):
        abort(400, description="Presentation playing and blank states must be boolean.")
    offset = data.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or not -10 <= offset <= 10:
        abort(400, description="Audience timing offset must be between -10 and +10 seconds.")
    audio = data.get("audio", False)
    if type(audio) is not bool:
        abort(400, description="Audience film audio must be boolean.")
    return {"videoId": video_id, "time": position, "playing": data["playing"], "blank": data["blank"], "offset": round(offset, 3), "audio": audio}


@clicktrack_bp.post("/api/presentations")
@_api_login_required
def api_create_presentation():
    data = _json_body()
    state = _public_video_state(data)
    db, _ = _models()
    model = current_app.ClickTrackPresentation
    now = time.time()
    date = datetime.fromtimestamp(now, ZoneInfo("Europe/London")).date().isoformat()
    existing = model.query.filter_by(token=date).first()
    if existing is not None and existing.expires_at > now:
        if data.get("replaceId") != existing.id or type(data.get("replaceId")) is not int:
            return _presentation_response(jsonify({
                "error": "A public broadcast already exists for " + date + ". Replace it?",
                "replaceId": existing.id, "date": date,
            })), 409
        db.session.delete(existing)
        db.session.flush()
    model.query.filter(model.expires_at <= now).delete(synchronize_session=False)
    if model.query.filter_by(user_id=current_user.id).count() >= 3:
        db.session.rollback()
        abort(400, description="Stop an existing public presentation before creating another.")
    live = model(user_id=current_user.id, token=date, state=state,
                 updated_at=now, expires_at=now + PRESENTATION_LIFETIME)
    db.session.add(live)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = model.query.filter_by(token=date).first()
        if existing is None:
            raise
        return _presentation_response(jsonify({
            "error": "Another admin started today's broadcast. Confirm replacement to continue.",
            "replaceId": existing.id, "date": date,
        })), 409
    return _presentation_response(jsonify({
        "id": live.id, "url": url_for("clicktrack.public_presentation", token=live.token),
    })), 201


@clicktrack_bp.route("/api/presentations/<int:presentation_id>", methods=["PUT", "DELETE"])
@_api_login_required
def api_publish_presentation(presentation_id):
    db, _ = _models()
    model = current_app.ClickTrackPresentation
    live = model.query.filter_by(id=presentation_id, user_id=current_user.id).first_or_404()
    if request.method == "DELETE":
        db.session.delete(live)
        db.session.commit()
        return _presentation_response(jsonify({"stopped": True}))
    if live.expires_at <= time.time():
        abort(410, description="This presentation link has expired. Create a new link.")
    live.state = _public_video_state(_json_body())
    live.updated_at = time.time()
    db.session.commit()
    return _presentation_response(jsonify({"published": True}))


@clicktrack_bp.get("/watch/<token>/")
def public_presentation(token):
    model = current_app.ClickTrackPresentation
    model.query.filter(model.token == token, model.expires_at > time.time()).first_or_404()
    return _presentation_response(make_response(render_template(
        "clicktrack/presentation.html",
        public_state_url=url_for("clicktrack.public_presentation_state", token=token),
    )))


@clicktrack_bp.get("/watch/<token>/state")
def public_presentation_state(token):
    model = current_app.ClickTrackPresentation
    now = time.time()
    live = model.query.filter(model.token == token, model.expires_at > now).first()
    if live is None:
        return _presentation_response(jsonify({"error": "This public presentation has ended or expired."})), 404
    age = max(0, now - live.updated_at)
    state = live.state
    waiting = age > PRESENTATION_STALE_AFTER
    playing = state["playing"] and not waiting and not state["blank"]
    offset = state.get("offset", 0)
    return _presentation_response(jsonify({
        "source": {"kind": "youtube", "id": state["videoId"], "key": state["videoId"]},
        "time": max(0, min(86400, state["time"] + (age + offset if playing else 0))),
        "offset": offset,
        "muted": not state.get("audio", False),
        "playing": playing,
        "blank": state["blank"] or waiting, "waiting": waiting,
    }))


@clicktrack_bp.get("/offline/")
def offline_performance():
    assets = Path(current_app.static_folder)
    response = make_response(render_template(
        "clicktrack/index.html", offline=True,
        offline_css=(assets / "css" / "clicktrack.css").read_text(encoding="utf-8"),
        offline_js=(assets / "js" / "clicktrack.js").read_text(encoding="utf-8"),
    ))
    response.headers.update({"Cache-Control": "no-store", "X-Clicktrack-Offline-Shell": "1"})
    return response


@clicktrack_bp.get("/offline/worker.js")
def offline_worker():
    return send_from_directory(current_app.static_folder, "js/clicktrack-offline-worker.js", max_age=0)


@clicktrack_bp.route("/api/tracks", methods=["GET"])
@_api_login_required
def api_list_tracks():
    _, ClickTrack = _models()
    query = ClickTrack.query.filter_by(user_id=current_user.id) if current_user.role == "admin" else _published_tracks()
    tracks = query.order_by(ClickTrack.title).all()
    return jsonify({"tracks": [t.to_dict() for t in tracks]})


@clicktrack_bp.put("/api/tracks/<int:track_id>/publication")
@_api_login_required
def api_publish_track(track_id):
    track = _own_track_or_404(track_id)
    public = _json_body().get("isPublic")
    if type(public) is not bool:
        abort(400, description="Public visibility must be true or false.")
    track.is_public = public
    current_app.db.session.commit()
    return jsonify(track.to_dict())


@clicktrack_bp.route("/api/tracks", methods=["POST"])
@_api_login_required
def api_create_track():
    db, ClickTrack = _models()
    data = _json_body()
    if ClickTrack.query.filter_by(user_id=current_user.id).count() >= MAX_TRACKS_PER_USER:
        abort(400, description="Track limit reached.")
    track = ClickTrack(user_id=current_user.id)
    _apply_payload(track, data)
    db.session.add(track)
    db.session.commit()
    return jsonify(track.to_dict()), 201


@clicktrack_bp.route("/api/tracks/<int:track_id>", methods=["PUT"])
@_api_login_required
def api_update_track(track_id):
    db, _ = _models()
    track = _own_track_or_404(track_id)
    _apply_payload(track, _json_body())
    db.session.commit()
    return jsonify(track.to_dict())


@clicktrack_bp.route("/api/tracks/<int:track_id>", methods=["DELETE"])
@_api_login_required
def api_delete_track(track_id):
    db, _ = _models()
    track = _own_track_or_404(track_id)
    db.session.delete(track)
    db.session.commit()
    return jsonify({"ok": True})


def _apply_video_draft(draft, data):
    video_id = data.get("videoId")
    if not isinstance(video_id, str) or not YOUTUBE_ID_RE.fullmatch(video_id):
        abort(400, description="A valid YouTube video ID is required.")
    markers = data.get("markers")
    if not isinstance(markers, list) or not 1 <= len(markers) <= MAX_SECTIONS + 2:
        abort(400, description="A draft must have between 1 and 502 hit points.")
    cleaned = []
    labels = set()
    for raw in markers:
        if not isinstance(raw, dict):
            abort(400, description="Invalid hit point.")
        time = raw.get("time")
        previous = cleaned[-1]["time"] if cleaned else None
        if (type(time) not in (int, float) or not math.isfinite(time) or not -86400 <= time <= 86400
                or time != round(time, 3) or (previous is not None and time <= previous)):
            abort(400, description="Hit points must have increasing times within 24 hours of video zero, to millisecond precision.")
        shape = _text(raw.get("shape"), 10)
        unit = _text(raw.get("unit"), 8)
        den = _text(raw.get("den"), 2)
        if shape not in ("steady", "accel", "rall", "timedPause") or not BEAT_UNIT_RE.fullmatch(unit) or den not in {
            str(v) for v in VALID_DENOMINATORS
        }:
            abort(400, description="Invalid hit point meter, beat unit or tempo shape.")
        cleaned.append({
            "time": time, "shape": shape, "unit": unit, "den": den,
            "bar": _text(raw.get("bar"), 32), "num": _text(raw.get("num"), 32),
            "bpm": _text(raw.get("bpm"), 32), "dialogue": _text(raw.get("dialogue"), 500),
            "figure": _text(raw.get("figure"), 32),
            "visualCue": raw.get("visualCue") is True,
        })
        if "rampAnchor" in raw:
            if raw["rampAnchor"] not in ("start", "end", "previous", "next"):
                abort(400, description="Invalid ramp tempo anchor.")
            cleaned[-1]["rampAnchor"] = raw["rampAnchor"]
        if "endBpm" in raw:
            cleaned[-1]["endBpm"] = _text(raw["endBpm"], 32)
        if "modulationOld" in raw or "modulationNew" in raw:
            old, new = raw.get("modulationOld"), raw.get("modulationNew")
            if not isinstance(old, str) or not isinstance(new, str) or not MODULATION_NOTE_RE.fullmatch(old) or not MODULATION_NOTE_RE.fullmatch(new):
                abort(400, description="Choose valid note values on both sides of the metric modulation.")
            cleaned[-1]["modulationOld"] = old
            cleaned[-1]["modulationNew"] = new
        if "skip" in raw:
            skip = raw["skip"]
            if not isinstance(skip, (int, str)) or isinstance(skip, bool) or not re.fullmatch(r"\d{1,4}", str(skip)) or int(skip) > 9998:
                abort(400, description="Skipped score bars must be a whole number from 0 to 9998.")
            cleaned[-1]["skip"] = str(int(skip))
        if "label" in raw:
            label = raw["label"]
            if not isinstance(label, str) or not re.fullmatch(r"[0-9]{1,9}[A-Za-z]{0,8}", label.strip()):
                abort(400, description="Enter a hit point number, optionally followed by letters (e.g. 16A).")
            label = label.strip().upper()
            if label in labels:
                abort(400, description="Hit point numbers must be unique.")
            labels.add(label)
            cleaned[-1]["label"] = label
    if not any(marker["time"] == 0 for marker in cleaned):
        abort(400, description="A video draft must include a hit point at video 0:00.")
    draft.video_id = video_id
    draft.title = _text(data.get("title"), 255) or "YouTube video " + video_id
    draft.markers = cleaned


@clicktrack_bp.post("/api/tracks/restore-backup")
@_api_login_required
def api_restore_backup():
    db, ClickTrack = _models()
    data = _json_body()
    if data.get("format") != "jackcapstaff-clicktrack" or data.get("version") != 2:
        abort(400, description="Unsupported linked backup format.")
    draft_data = data.get("videoDraft")
    if not isinstance(draft_data, dict):
        abort(400, description="The backup must contain a YouTube draft.")
    if ClickTrack.query.filter_by(user_id=current_user.id).count() >= MAX_TRACKS_PER_USER:
        abort(400, description="Track limit reached.")
    if current_app.ClickTrackVideoDraft.query.filter_by(user_id=current_user.id).count() >= MAX_VIDEO_DRAFTS:
        abort(400, description="Draft limit reached. Delete an unused draft before restoring another.")
    track = ClickTrack(user_id=current_user.id)
    draft = current_app.ClickTrackVideoDraft(user_id=current_user.id)
    try:
        _apply_payload(track, {key: value for key, value in data.items() if key != "videoDraftId"})
        _apply_video_draft(draft, draft_data)
        db.session.add(draft)
        db.session.flush()
        track.video_draft_id = draft.id
        db.session.add(track)
        db.session.commit()
    except (HTTPException, SQLAlchemyError):
        db.session.rollback()
        raise
    return jsonify(track.to_dict()), 201


@clicktrack_bp.put("/api/tracks/<int:track_id>/video-editor")
@_api_login_required
def api_save_video_editor(track_id):
    db, ClickTrack = _models()
    data = _json_body()
    track = ClickTrack.query.filter_by(id=track_id, user_id=current_user.id).with_for_update().first_or_404()
    draft_data = data.get("videoDraft")
    if not isinstance(draft_data, dict):
        abort(400, description="A YouTube draft is required.")
    if "expectedDraft" not in data:
        abort(400, description="The previous draft state is required.")
    if "expectedTrack" not in data:
        abort(400, description="The previous track state is required.")
    track_data = data.get("track")
    if track_data is not None and not isinstance(track_data, dict):
        abort(400, description="Track must be an object or null.")
    model = current_app.ClickTrackVideoDraft
    try:
        if track.to_dict() != data["expectedTrack"]:
            abort(409, description="The track changed. Reload and compare before saving.")
        draft = None
        if track.video_draft_id is not None:
            draft = model.query.filter_by(id=track.video_draft_id, user_id=current_user.id).with_for_update().first()
            if draft is None:
                abort(409, description="The linked video draft is no longer available. Reload before saving.")
        if (draft.to_dict() if draft else None) != data["expectedDraft"]:
            abort(409, description="The linked video draft changed. Reload and compare before saving.")
        if track_data is not None:
            _apply_payload(track, {key: value for key, value in track_data.items() if key != "videoDraftId"})
        shared = draft is not None and ClickTrack.query.filter(
            ClickTrack.user_id == current_user.id,
            ClickTrack.video_draft_id == draft.id,
            ClickTrack.id != track.id,
        ).first() is not None
        if draft is None or shared:
            if model.query.filter_by(user_id=current_user.id).count() >= MAX_VIDEO_DRAFTS:
                abort(400, description="Draft limit reached. Delete an unused draft before saving.")
            draft = model(user_id=current_user.id)
            db.session.add(draft)
        _apply_video_draft(draft, draft_data)
        db.session.flush()
        track.video_draft_id = draft.id
        db.session.commit()
    except (HTTPException, SQLAlchemyError):
        db.session.rollback()
        raise
    return jsonify({"track": track.to_dict(), "videoDraft": draft.to_dict()})


@clicktrack_bp.put("/api/tracks/<int:track_id>/video-link")
@_api_login_required
def api_link_video_draft(track_id):
    db, ClickTrack = _models()
    data = _json_body()
    expected_draft = data.get("expectedDraft")
    if "expectedTrack" not in data or not isinstance(expected_draft, dict):
        abort(400, description="The previous track and draft states are required.")
    track = ClickTrack.query.filter_by(id=track_id, user_id=current_user.id).with_for_update().first_or_404()
    try:
        if track.to_dict() != data["expectedTrack"] or track.video_draft_id is not None:
            abort(409, description="The track changed. Reload and compare before linking.")
        draft = current_app.ClickTrackVideoDraft.query.filter_by(
            id=expected_draft.get("id"), user_id=current_user.id
        ).with_for_update().first()
        if draft is None or draft.to_dict() != expected_draft:
            abort(409, description="The video draft changed. Reload and compare before linking.")
        track.video_draft_id = draft.id
        db.session.commit()
    except (HTTPException, SQLAlchemyError):
        db.session.rollback()
        raise
    return jsonify(track.to_dict())


@clicktrack_bp.post("/api/tracks/from-video-draft")
@_api_login_required
def api_create_from_video_draft():
    db, ClickTrack = _models()
    model = current_app.ClickTrackVideoDraft
    data = _json_body()
    track_data, draft_data, expected = data.get("track"), data.get("videoDraft"), data.get("expectedDraft")
    if not isinstance(track_data, dict) or not isinstance(draft_data, dict):
        abort(400, description="A track and YouTube draft are required.")
    if "expectedDraft" not in data or (expected is not None and not isinstance(expected, dict)):
        abort(400, description="The previous draft state is required.")
    if ClickTrack.query.filter_by(user_id=current_user.id).count() >= MAX_TRACKS_PER_USER:
        abort(400, description="Track limit reached.")
    try:
        draft = None
        if expected is not None:
            draft = model.query.filter_by(id=expected.get("id"), user_id=current_user.id).with_for_update().first()
            if draft is None or draft.to_dict() != expected:
                abort(409, description="The video draft changed. Reload and compare before saving.")
            if ClickTrack.query.filter_by(user_id=current_user.id, video_draft_id=draft.id).first():
                draft = None
        if draft is None:
            if model.query.filter_by(user_id=current_user.id).count() >= MAX_VIDEO_DRAFTS:
                abort(400, description="Draft limit reached. Delete an unused draft before creating a track.")
            draft = model(user_id=current_user.id)
            db.session.add(draft)
        track = ClickTrack(user_id=current_user.id)
        _apply_payload(track, {key: value for key, value in track_data.items() if key != "videoDraftId"})
        _apply_video_draft(draft, draft_data)
        db.session.flush()
        track.video_draft_id = draft.id
        db.session.add(track)
        db.session.commit()
    except (HTTPException, SQLAlchemyError):
        db.session.rollback()
        raise
    return jsonify({"track": track.to_dict(), "videoDraft": draft.to_dict()}), 201


@clicktrack_bp.route("/api/video-drafts", methods=["GET", "POST"])
@_api_login_required
def api_video_drafts():
    model = current_app.ClickTrackVideoDraft
    own = model.query.filter_by(user_id=current_user.id)
    if request.method == "GET":
        rows = own.with_entities(model.id, model.title).order_by(model.updated_at.desc()).all()
        return jsonify({"drafts": [{"id": row.id, "title": row.title} for row in rows]})
    if own.count() >= MAX_VIDEO_DRAFTS:
        abort(400, description="Draft limit reached. Delete an unused draft before creating another.")
    draft = model(user_id=current_user.id)
    _apply_video_draft(draft, _json_body())
    current_app.db.session.add(draft)
    current_app.db.session.commit()
    return jsonify(draft.to_dict()), 201


@clicktrack_bp.put("/api/video-drafts/<int:draft_id>/unlinked")
@_api_login_required
def api_update_unlinked_video_draft(draft_id):
    db, ClickTrack = _models()
    data = _json_body()
    if "expectedDraft" not in data or not isinstance(data.get("videoDraft"), dict):
        abort(400, description="The previous draft state and a YouTube draft are required.")
    draft = current_app.ClickTrackVideoDraft.query.filter_by(
        id=draft_id, user_id=current_user.id
    ).with_for_update().first_or_404()
    try:
        if draft.to_dict() != data.get("expectedDraft"):
            abort(409, description="The video draft changed. Reload and compare before saving.")
        if ClickTrack.query.filter_by(user_id=current_user.id, video_draft_id=draft.id).first():
            if current_app.ClickTrackVideoDraft.query.filter_by(user_id=current_user.id).count() >= MAX_VIDEO_DRAFTS:
                abort(400, description="Draft limit reached. Delete an unused draft before saving.")
            draft = current_app.ClickTrackVideoDraft(user_id=current_user.id)
            db.session.add(draft)
        _apply_video_draft(draft, data.get("videoDraft"))
        db.session.commit()
    except (HTTPException, SQLAlchemyError):
        db.session.rollback()
        raise
    return jsonify(draft.to_dict())


@clicktrack_bp.route("/api/video-drafts/<int:draft_id>", methods=["GET", "PUT", "DELETE"])
@_api_login_required
def api_video_draft(draft_id):
    model = current_app.ClickTrackVideoDraft
    if current_user.role == "admin" and (
        request.method != "GET" or model.query.filter_by(id=draft_id, user_id=current_user.id).first()
    ):
        draft = model.query.filter_by(id=draft_id, user_id=current_user.id).first_or_404()
    else:
        if not _published_tracks().filter(current_app.ClickTrack.video_draft_id == draft_id).first():
            abort(404)
        draft = model.query.filter_by(id=draft_id).first_or_404()
    if request.method == "DELETE":
        current_app.ClickTrack.query.filter_by(user_id=current_user.id, video_draft_id=draft.id).update(
            {"video_draft_id": None}
        )
        current_app.db.session.delete(draft)
        current_app.db.session.commit()
        return jsonify({"ok": True})
    if request.method == "PUT":
        _apply_video_draft(draft, _json_body())
        current_app.db.session.commit()
    return jsonify(draft.to_dict())
