"""
Click track / programmable metronome.

  /clicktrack/                    performance + editor page (login required)
  /clicktrack/api/tracks          GET list, POST create        (login required)
  /clicktrack/api/tracks/<id>     PUT update, DELETE           (login required)

Tracks are saved to the signed-in user's account, with a browser-local cache.
"""
import math
import re
from functools import wraps

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

clicktrack_bp = Blueprint("clicktrack", __name__, url_prefix="/clicktrack")

MAX_TRACKS_PER_USER = 200
MAX_SECTIONS = 500

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
