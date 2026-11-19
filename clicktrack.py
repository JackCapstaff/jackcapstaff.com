"""
Click track / programmable metronome.

  /clicktrack/                    performance + editor page (login required)
  /clicktrack/api/tracks          GET list, POST create        (login required)
  /clicktrack/api/tracks/<id>     PUT update, DELETE           (login required)
  /clicktrack/api/video-drafts  GET list, POST create         (login required)
  /clicktrack/api/video-drafts/<id> GET, PUT, DELETE           (login required)

Tracks are saved to the signed-in user's account, with a browser-local cache.
"""
import math
import re
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request, send_from_directory
from flask_login import current_user, login_required

clicktrack_bp = Blueprint("clicktrack", __name__, url_prefix="/clicktrack")

MAX_TRACKS_PER_USER = 200
MAX_SECTIONS = 500
MAX_VIDEO_DRAFTS = 50
YOUTUBE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")

SECTION_TYPES = {"click", "pause"}
ADVANCE_MODES = {"auto", "manual"}
CLICK_MODES = {"beats", "units", "sub2", "sub3", "sub4", "silent"}
VALID_DENOMINATORS = {1, 2, 4, 8, 16, 32}
BEAT_UNIT_RE = re.compile(r"^(auto|[1-9]/(1|2|4|8|16|32))$")
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
        "beatUnit": beat_unit,
        "num": num,
        "den": den,
        "grouping": grouping,
        "bars": _clamp_int(raw.get("bars"), 4, 1, 9999),
        "advance": raw.get("advance") if raw.get("advance") in ADVANCE_MODES else "auto",
        "click": raw.get("click") if raw.get("click") in CLICK_MODES else "beats",
        "unitPitch": _clamp_float(raw.get("unitPitch"), 0, -24, 24),
        "unitVelocity": _clamp_int(raw.get("unitVelocity"), 53, 0, 127),
        "subPitch": _clamp_float(raw.get("subPitch"), 0, -24, 24),
        "subVelocity": _clamp_int(raw.get("subVelocity"), 32, 0, 127),
        "countIn": _clamp_int(raw.get("countIn"), 0, 0, 8),
        "startBar": _clamp_int(raw.get("startBar"), None, 1, 99999) if raw.get("startBar") not in (None, "") else None,
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
    track.title = title
    track.notes = _text(data.get("notes"), 2000)
    if "videoOffset" in data:
        offset = data["videoOffset"]
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not 0 <= offset <= 86400:
            abort(400, description="Video offset must be between 0 and 86400 seconds.")
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


@clicktrack_bp.route("/")
@login_required
def clicktrack_index():
    return render_template("clicktrack/index.html")


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
    tracks = ClickTrack.query.filter_by(user_id=current_user.id).order_by(ClickTrack.title).all()
    return jsonify({"tracks": [t.to_dict() for t in tracks]})


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
    for raw in markers:
        if not isinstance(raw, dict):
            abort(400, description="Invalid hit point.")
        time = raw.get("time")
        previous = cleaned[-1]["time"] if cleaned else -1
        if (type(time) not in (int, float) or not 0 <= time <= 86400
                or time != round(time, 3) or time <= previous or (not cleaned and time != 0)):
            abort(400, description="Hit points must start at zero and have increasing times to millisecond precision.")
        shape = _text(raw.get("shape"), 8)
        unit = _text(raw.get("unit"), 8)
        den = _text(raw.get("den"), 2)
        if shape not in ("steady", "accel", "rall") or not BEAT_UNIT_RE.fullmatch(unit) or den not in {
            str(v) for v in VALID_DENOMINATORS
        }:
            abort(400, description="Invalid hit point meter, beat unit or tempo shape.")
        cleaned.append({
            "time": time, "shape": shape, "unit": unit, "den": den,
            "bar": _text(raw.get("bar"), 32), "num": _text(raw.get("num"), 32),
            "bpm": _text(raw.get("bpm"), 32), "dialogue": _text(raw.get("dialogue"), 500),
        })
    draft.video_id = video_id
    draft.title = _text(data.get("title"), 255) or "YouTube video " + video_id
    draft.markers = cleaned


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


@clicktrack_bp.route("/api/video-drafts/<int:draft_id>", methods=["GET", "PUT", "DELETE"])
@_api_login_required
def api_video_draft(draft_id):
    draft = current_app.ClickTrackVideoDraft.query.filter_by(id=draft_id, user_id=current_user.id).first_or_404()
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
