"""
Alexa custom skill endpoint for the hidden Kitchen recipe app.

The Flask app itself is the skill's HTTPS backend (no AWS Lambda required).
Alexa POSTs skill requests to /kitchen/alexa; we read recipes straight from the
existing database and reply with voice + Echo Show visuals (APL). Editing of
recipes stays entirely in the existing /kitchen/manage web UI.

Security: incoming requests are verified per Amazon's requirements:
  1. Skill (application) ID must match ALEXA_SKILL_ID (if configured).
  2. Request timestamp must be within 150 seconds.
  3. Request signature is verified against the SignatureCertChainUrl certificate
     using the modern `cryptography` library (set ALEXA_VERIFY_SIGNATURE=0 to
     disable, e.g. for local testing).
"""
import os
import re
import json
import base64
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Blueprint, current_app, request, jsonify

alexa_bp = Blueprint("alexa", __name__, url_prefix="/kitchen/alexa")

APL_TOKEN = "kitchenStep"
MEAL_PLAN_INVOCATION = "family cookbook"

# module-level cert cache: {url: pem_bytes}
_CERT_CACHE = {}

# in-memory ring buffer of recent rejection diagnostics (no secrets stored)
_DIAG = []


def _record_diag(reason, extra=None):
    import time as _t
    entry = {"at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()), "reason": reason}
    if extra:
        entry.update(extra)
    _DIAG.append(entry)
    del _DIAG[:-15]



# --------------------------------------------------------------------------- #
# Request verification
# --------------------------------------------------------------------------- #
def _verify_enabled():
    return os.environ.get("ALEXA_VERIFY_SIGNATURE", "1").strip() not in {"0", "false", "False", ""}


def _valid_cert_url(url):
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme.lower() != "https":
        return False
    if p.hostname is None or p.hostname.lower() != "s3.amazonaws.com":
        return False
    if p.port not in (None, 443):
        return False
    # normalise path and require /echo.api/ prefix
    path = os.path.normpath(p.path)
    return path.startswith("/echo.api/")


def _fetch_cert_chain(url):
    if url in _CERT_CACHE:
        return _CERT_CACHE[url]
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    pem = resp.content
    _CERT_CACHE[url] = pem
    return pem


def _verify_signature(cert_url, signature_b64, body_bytes):
    """Raise ValueError if the request signature is invalid."""
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509.verification import PolicyBuilder, Store
    import certifi

    if not _valid_cert_url(cert_url):
        raise ValueError("Invalid SignatureCertChainUrl")

    pem = _fetch_cert_chain(cert_url)
    certs = x509.load_pem_x509_certificates(pem)
    if not certs:
        raise ValueError("Empty certificate chain")
    leaf, intermediates = certs[0], certs[1:]

    # Validate the certificate chain up to a trusted public root and confirm the
    # leaf is issued for echo-api.amazon.com (Amazon's documented requirement).
    with open(certifi.where(), "rb") as fh:
        store = Store(x509.load_pem_x509_certificates(fh.read()))
    verifier = PolicyBuilder().store(store).build_server_verifier(
        x509.DNSName("echo-api.amazon.com")
    )
    verifier.verify(leaf, intermediates)  # raises on failure

    # Verify the request body signature (Signature-256 = RSA-SHA256).
    signature = base64.b64decode(signature_b64)
    leaf.public_key().verify(
        signature, body_bytes, padding.PKCS1v15(), hashes.SHA256()
    )


def _verify_timestamp(envelope):
    ts = (((envelope or {}).get("request") or {}).get("timestamp"))
    if not ts:
        raise ValueError("Missing timestamp")
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Bad timestamp")
    now = datetime.now(timezone.utc)
    if abs((now - when).total_seconds()) > 150:
        raise ValueError("Stale timestamp")


def _verify_request(envelope, body_bytes):
    """Full verification. Returns None if OK, or an error string."""
    # Skill/application ID check (cheap, always on when configured)
    expected = os.environ.get("ALEXA_SKILL_ID", "").strip()
    if expected:
        got = (((envelope.get("session") or {}).get("application") or {}).get("applicationId")
               or ((envelope.get("context") or {}).get("System") or {}).get("application", {}).get("applicationId"))
        if got != expected:
            return "Application ID mismatch"

    try:
        _verify_timestamp(envelope)
    except ValueError as e:
        # The timestamp check is part of request-integrity verification, so it is
        # skipped together with the signature when verification is disabled for
        # local testing (ALEXA_VERIFY_SIGNATURE=0). It is always enforced in prod.
        if _verify_enabled():
            return str(e)

    if _verify_enabled():
        cert_url = request.headers.get("SignatureCertChainUrl", "")
        signature = request.headers.get("Signature-256", "") or request.headers.get("Signature", "")
        if not cert_url or not signature:
            return "Missing signature headers"
        try:
            _verify_signature(cert_url, signature, body_bytes)
        except Exception as e:  # noqa: BLE001 - any verification failure = reject
            return f"Signature verification failed: {e}"
    return None


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
def _all_recipes():
    Recipe = current_app.Recipe
    return Recipe.query.filter_by(published=True).order_by(
        Recipe.meal_plan, Recipe.day_number, Recipe.title
    ).all()


def _find_by_day(day):
    Recipe = current_app.Recipe
    return Recipe.query.filter_by(published=True, day_number=day).first()


def _find_by_name(name):
    if not name:
        return None
    Recipe = current_app.Recipe
    norm = _norm(name)
    recipes = _all_recipes()
    # exact-ish first, then contains either direction
    for r in recipes:
        if _norm(r.title) == norm:
            return r
    for r in recipes:
        rt = _norm(r.title)
        if norm in rt or rt in norm:
            return r
    # token overlap fallback
    want = set(norm.split())
    best, best_score = None, 0
    for r in recipes:
        score = len(want & set(_norm(r.title).split()))
        if score > best_score:
            best, best_score = r, score
    return best if best_score else None


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _get_by_slug(slug):
    if not slug:
        return None
    return current_app.Recipe.query.filter_by(slug=slug, published=True).first()


# --------------------------------------------------------------------------- #
# Speech helpers
# --------------------------------------------------------------------------- #
def _join(items):
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _iso_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    if s:
        out += f"{s}S"
    return out if out != "PT" else "PT0S"


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #
def _resp(speech, reprompt=None, end=False, attributes=None, directives=None, card=None):
    response = {
        "shouldEndSession": end,
    }
    if speech is not None:
        response["outputSpeech"] = {"type": "PlainText", "text": speech}
    if reprompt:
        response["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    if directives:
        response["directives"] = directives
    if card:
        response["card"] = card
    return jsonify({
        "version": "1.0",
        "sessionAttributes": attributes or {},
        "response": response,
    })


def _supports_apl(envelope):
    try:
        return "Alexa.Presentation.APL" in (
            envelope["context"]["System"]["device"]["supportedInterfaces"]
        )
    except (KeyError, TypeError):
        return False


def _timer_directive_via_api(envelope, seconds, label):
    """Best-effort: create a native Alexa timer using the Timers API.

    Returns (ok: bool, needs_permission: bool).
    """
    try:
        sys = envelope["context"]["System"]
        api_endpoint = sys["apiEndpoint"]
        token = sys["apiAccessToken"]
    except (KeyError, TypeError):
        return False, False

    body = {
        "duration": _iso_duration(seconds),
        "timerLabel": label[:64] if label else "Kitchen timer",
        "creationBehavior": {"displayExperience": {"visibility": "VISIBLE"}},
        "triggeringBehavior": {
            "operation": {
                "type": "ANNOUNCE",
                "textToAnnounce": [{"locale": "en-GB", "text": f"{label} is done"}],
            },
            "notificationConfig": {"playAudible": True},
        },
    }
    try:
        r = requests.post(
            f"{api_endpoint}/v1/alerts/timers",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=6,
        )
        if r.status_code in (401, 403):
            return False, True
        return (200 <= r.status_code < 300), False
    except requests.RequestException:
        return False, False


# --------------------------------------------------------------------------- #
# Intent handlers
# --------------------------------------------------------------------------- #
def _launch(envelope):
    recipes = _all_recipes()
    n = len(recipes)
    if not n:
        return _resp("There are no recipes yet. Add some in the kitchen web app first.", end=True)
    speech = (f"Welcome to the kitchen. There are {n} recipes. "
              "Tap a recipe on the screen, or say, open day one, or, cook the chicken tikka curry.")
    reprompt = "Which recipe would you like? Try, open day one, or tap one on screen."
    directives = [_apl_home_directive(recipes)] if _supports_apl(envelope) else None
    return _resp(speech, reprompt=reprompt, attributes={}, directives=directives)


def _list_recipes(envelope):
    recipes = _all_recipes()
    if not recipes:
        return _resp("There are no recipes yet.", end=True)
    names = []
    for r in recipes:
        if r.day_number:
            names.append(f"day {r.day_number}, {r.title}")
        else:
            names.append(r.title)
    speech = "On the menu: " + _join(names) + ". Which would you like?"
    return _resp(speech, reprompt="Which recipe? Try, open day one.")


def _slot(intent, name):
    try:
        return intent["slots"][name]["value"]
    except (KeyError, TypeError):
        return None


def _open_recipe(recipe, envelope):
    attrs = {"slug": recipe.slug, "step": 0}
    equip = _join(recipe.equipment)
    parts = [recipe.title + "."]
    if recipe.servings:
        parts.append(f"Serves {recipe.servings}.")
    if equip:
        parts.append(f"You'll need: {equip}.")
    parts.append("Tap Cook or say start cooking to begin.")
    speech = " ".join(parts)
    directives = None
    if _supports_apl(envelope):
        lines = recipe.equipment or ["Nothing special needed â€” tap Cook to begin."]
        directives = [_apl_list_directive(recipe, "equip", "Equipment", lines)]
    return _resp(speech, reprompt="Say start cooking to begin.", attributes=attrs,
                 directives=directives)


def _open_by_day(envelope, intent):
    val = _slot(intent, "day")
    try:
        day = int(val)
    except (TypeError, ValueError):
        return _resp("Which day number would you like? For example, open day one.",
                     reprompt="Which day? Try, open day one.")
    recipe = _find_by_day(day)
    if not recipe:
        return _resp(f"I couldn't find a recipe for day {day}. Say, what's on the menu, to hear the list.",
                     reprompt="Which recipe? Try, open day one.")
    return _open_recipe(recipe, envelope)


def _open_by_name(envelope, intent):
    name = _slot(intent, "recipeName")
    recipe = _find_by_name(name)
    if not recipe:
        return _resp("I couldn't find that recipe. Say, what's on the menu, to hear the list.",
                     reprompt="Which recipe? Try, open day one.")
    return _open_recipe(recipe, envelope)


def _current(attrs):
    recipe = _get_by_slug(attrs.get("slug"))
    step = attrs.get("step", 0) or 0
    return recipe, step


def _say_step(recipe, step, envelope, timer_override=None, speak=True):
    steps = recipe.steps
    step = max(0, min(step, len(steps) - 1))
    s = steps[step]
    attrs = {"slug": recipe.slug, "step": step}
    reprompt = None
    if speak:
        speech = f"Step {step + 1}. {s['text']}"
        if s.get("timer_label"):
            speech += f" You can say start the timer for {s['timer_label']}."
        if step == len(steps) - 1:
            speech += " That's the last step. Enjoy!"
        reprompt = "Say next, repeat, or start the timer."
    else:
        speech = None
    directives = None
    if _supports_apl(envelope):
        directives = [_apl_step_directive(recipe, step, timer_override=timer_override)]
    return _resp(speech, reprompt=reprompt, attributes=attrs, directives=directives)


def _start_cooking(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Which recipe would you like to cook? Try, open day one.",
                     reprompt="Which recipe? Try, open day one.")
    return _say_step(recipe, 0, envelope)


def _next(envelope, attrs):
    recipe, step = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if step + 1 >= len(recipe.steps):
        return _say_step(recipe, step, envelope)
    return _say_step(recipe, step + 1, envelope)


def _previous(envelope, attrs):
    recipe, step = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    return _say_step(recipe, max(0, step - 1), envelope)


def _repeat_step(envelope, attrs):
    recipe, step = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    return _say_step(recipe, step, envelope)


def _where_am_i(envelope, attrs):
    recipe, step = _current(attrs)
    if not recipe:
        return _resp("You haven't opened a recipe yet. Try, open day one.", reprompt="Try, open day one.")
    return _resp(f"You're on step {step + 1} of {len(recipe.steps)} of {recipe.title}.",
                 reprompt="Say next, previous, or repeat.", attributes=attrs)


def _equipment(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if not recipe.equipment:
        return _resp("No equipment is listed for this recipe.", attributes=attrs)
    directives = [_apl_list_directive(recipe, "equip", "Equipment", recipe.equipment)] \
        if _supports_apl(envelope) else None
    return _resp("You'll need: " + _join(recipe.equipment) + ".",
                 reprompt="Say start cooking to begin.", attributes=attrs, directives=directives)


def _ingredients(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if not recipe.ingredients:
        return _resp("No separate ingredients are listed; they're mentioned within the steps.",
                     attributes=attrs)
    directives = [_apl_list_directive(recipe, "prep", "Ingredients", recipe.ingredients)] \
        if _supports_apl(envelope) else None
    return _resp("Ingredients: " + _join(recipe.ingredients) + ".",
                 reprompt="Say start cooking to begin.", attributes=attrs, directives=directives)


def _prep(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if not recipe.prep:
        return _resp("There are no separate prep notes for this recipe.", attributes=attrs)
    directives = [_apl_list_directive(recipe, "prep", "Prep", recipe.prep)] \
        if _supports_apl(envelope) else None
    return _resp("Prep: " + _join(recipe.prep) + ".",
                 reprompt="Say start cooking to begin.", attributes=attrs, directives=directives)


def _start_timer(envelope, attrs):
    recipe, step = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    s = recipe.steps[step]
    secs = s.get("timer_seconds")
    if not secs:
        return _resp("This step doesn't have a timer. Say next to continue.",
                     reprompt="Say next, or repeat.", attributes=attrs)
    label = f"Step {step + 1}"
    ok, needs_perm = _timer_directive_via_api(envelope, secs, label)
    if ok:
        speech = f"Timer set for {s.get('timer_label') or _iso_duration(secs)}."
    elif needs_perm:
        speech = ("To let me set timers, please enable the timers permission for this skill "
                  "in the Alexa app. For now, you can say, Alexa, set a timer for "
                  f"{s.get('timer_label')}.")
    else:
        speech = f"Please say, Alexa, set a timer for {s.get('timer_label')}."
    directives = [_apl_step_directive(recipe, step)] if _supports_apl(envelope) else None
    return _resp(speech, reprompt="Say next when you're ready.", attributes=attrs, directives=directives)


def _help(envelope, attrs):
    speech = ("You can say: open day one, or, cook the chicken tikka curry. "
              "Then say: start cooking, next, previous, repeat, what equipment do I need, "
              "or, start the timer. Say stop to exit.")
    return _resp(speech, reprompt="Which recipe would you like? Try, open day one.", attributes=attrs)


def _as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _user_event(envelope):
    """Handle a touch (SendEvent) from the Echo Show APL screen.

    All state travels in the button's ``arguments`` list, so touch handling is
    stateless and independent of session attributes:
      ["menu"]                         -> home grid
      ["overview", slug]               -> recipe landing (Equipment tab)
      ["equip", slug]                  -> Equipment list
      ["prep", slug]                   -> Ingredients + Prep list
      ["open", slug]                   -> start cooking at step 0
      ["next"|"prev", slug, step]      -> step navigation
      ["tadj", slug, step, secs, delta]-> adjust this step's timer, re-render
      ["tstart", slug, step, secs]     -> start a native timer for `secs`
    """
    req = envelope.get("request", {}) or {}
    args = req.get("arguments") or []
    if not args:
        return _launch(envelope)
    action = args[0]
    if action == "menu":
        return _launch(envelope)

    slug = args[1] if len(args) > 1 else None
    recipe = _get_by_slug(slug) if slug else None
    if not recipe:
        return _launch(envelope)

    step = _as_int(args[2]) if len(args) > 2 else 0
    attrs = {"slug": recipe.slug, "step": step}

    if action == "overview":
        return _open_recipe(recipe, envelope)

    if action == "equip":
        lines = recipe.equipment or ["Nothing special needed."]
        directives = [_apl_list_directive(recipe, "equip", "Equipment", lines)] \
            if _supports_apl(envelope) else None
        return _resp(None, attributes={"slug": recipe.slug, "step": step}, directives=directives)

    if action == "prep":
        lines = []
        if recipe.ingredients:
            lines.append("Ingredients")
            lines += recipe.ingredients
        if recipe.prep:
            lines.append("Prep")
            lines += recipe.prep
        if not lines:
            lines = ["No prep needed â€” tap Cook to begin."]
        directives = [_apl_list_directive(recipe, "prep", "Prep", lines)] \
            if _supports_apl(envelope) else None
        return _resp(None, attributes={"slug": recipe.slug, "step": step}, directives=directives)

    if action == "open":
        return _say_step(recipe, 0, envelope)

    if action == "next":
        return _say_step(recipe, min(step + 1, len(recipe.steps) - 1), envelope)

    if action == "prev":
        return _say_step(recipe, max(step - 1, 0), envelope)

    if action == "tadj":
        cur = _as_int(args[3]) if len(args) > 3 else 0
        delta = _as_int(args[4]) if len(args) > 4 else 0
        return _say_step(recipe, step, envelope, timer_override=max(0, cur + delta), speak=False)

    if action == "tstart":
        secs = _as_int(args[3]) if len(args) > 3 else (recipe.steps[step].get("timer_seconds") or 0)
        if secs <= 0:
            return _say_step(recipe, step, envelope, speak=False)
        ok, needs_perm = _timer_directive_via_api(envelope, secs, f"{recipe.title} Â· step {step + 1}")
        if ok:
            speech = f"Timer set for {_iso_duration(secs).replace('PT', '').replace('M', ' minutes ').replace('S', ' seconds').replace('H', ' hours ').strip()}."
        elif needs_perm:
            speech = ("To let me set timers, enable the timers permission for this skill in the "
                      "Alexa app. For now you can say, Alexa, set a timer.")
        else:
            speech = "Please say, Alexa, set a timer."
        directives = [_apl_step_directive(recipe, step, timer_override=secs)] \
            if _supports_apl(envelope) else None
        return _resp(speech, reprompt="Say next when you're ready.",
                     attributes=attrs, directives=directives)

    return _launch(envelope)


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def _handle(envelope):
    req = envelope.get("request", {})
    rtype = req.get("type")
    attrs = envelope.get("session", {}).get("attributes", {}) or {}

    if rtype == "LaunchRequest":
        return _launch(envelope)

    if rtype == "SessionEndedRequest":
        return _resp("", end=True)

    if rtype == "Alexa.Presentation.APL.UserEvent":
        return _user_event(envelope)

    if rtype == "IntentRequest":
        intent = req.get("intent", {})
        name = intent.get("name", "")
        handlers = {
            "OpenRecipeByDayIntent": lambda: _open_by_day(envelope, intent),
            "OpenRecipeByNameIntent": lambda: _open_by_name(envelope, intent),
            "ListRecipesIntent": lambda: _list_recipes(envelope),
            "StartCookingIntent": lambda: _start_cooking(envelope, attrs),
            "EquipmentIntent": lambda: _equipment(envelope, attrs),
            "IngredientsIntent": lambda: _ingredients(envelope, attrs),
            "PrepIntent": lambda: _prep(envelope, attrs),
            "StartTimerIntent": lambda: _start_timer(envelope, attrs),
            "WhereAmIIntent": lambda: _where_am_i(envelope, attrs),
            "AMAZON.NextIntent": lambda: _next(envelope, attrs),
            "AMAZON.PreviousIntent": lambda: _previous(envelope, attrs),
            "AMAZON.RepeatIntent": lambda: _repeat_step(envelope, attrs),
            "AMAZON.HelpIntent": lambda: _help(envelope, attrs),
            "AMAZON.FallbackIntent": lambda: _help(envelope, attrs),
            "AMAZON.StopIntent": lambda: _resp("Goodbye.", end=True),
            "AMAZON.CancelIntent": lambda: _resp("Goodbye.", end=True),
            "AMAZON.NavigateHomeIntent": lambda: _launch(envelope),
        }
        handler = handlers.get(name)
        if handler:
            return handler()
        return _help(envelope, attrs)

    return _resp("Sorry, I didn't understand that.", reprompt="Try, open day one.")


@alexa_bp.route("", methods=["POST"])
@alexa_bp.route("/", methods=["POST"])
def alexa_endpoint():
    body_bytes = request.get_data()
    try:
        envelope = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "bad request"}), 400

    error = _verify_request(envelope, body_bytes)
    if error:
        current_app.logger.warning("Alexa request rejected: %s", error)
        _record_diag(error, {
            "had_cert_url": bool(request.headers.get("SignatureCertChainUrl")),
            "had_signature": bool(request.headers.get("Signature-256") or request.headers.get("Signature")),
            "verify_enabled": _verify_enabled(),
        })
        return jsonify({"error": "unauthorized"}), 400

    try:
        import time as _t
        _t0 = _t.time()
        resp = _handle(envelope)
        try:
            payload = resp.get_json(silent=True) if hasattr(resp, "get_json") else None
        except Exception:
            payload = None
        rtype = (envelope.get("request") or {}).get("type")
        rname = ((envelope.get("request") or {}).get("intent") or {}).get("name")
        dev = (((envelope.get("context") or {}).get("System") or {}).get("device") or {})
        has_apl_iface = "Alexa.Presentation.APL" in (dev.get("supportedInterfaces") or {})
        directives = ((payload or {}).get("response") or {}).get("directives") or []
        sent_apl = any((d.get("type", "").startswith("Alexa.Presentation.APL")) for d in directives)
        _record_diag("ok", {
            "rtype": rtype, "intent": rname,
            "device_supports_apl": has_apl_iface,
            "sent_apl": sent_apl,
            "ms": int((_t.time() - _t0) * 1000),
        })
        return resp
    except Exception as e:  # noqa: BLE001 - never 500 to Alexa; speak a graceful error
        current_app.logger.exception("Alexa handler error")
        _record_diag("handler_exception", {"err": repr(e)[:300]})
        return _resp("Sorry, something went wrong in the kitchen. Please try again.", end=True)


@alexa_bp.route("/_diag", methods=["GET"])
def alexa_diag():
    """Return recent request-rejection diagnostics.

    Guarded by the ALEXA_DEBUG_TOKEN env var: if it is unset the route 404s, so
    it is inert unless you deliberately enable it. The recorded reasons contain
    no secrets (just verification error strings). Access with ?t=<token>.
    """
    token = os.environ.get("ALEXA_DEBUG_TOKEN", "").strip()
    if not token or request.args.get("t", "") != token:
        return jsonify({"error": "not found"}), 404
    return jsonify({"recent": _DIAG})


# --------------------------------------------------------------------------- #
# APL documents (server-rendered inline; no static hosting or datasources).
#
# Every screen is built fresh per request with literal values and literal touch
# arguments, so there is no data-binding to reason about and the Echo Show can
# drive the whole flow by touch (each tap POSTs an Alexa.Presentation.APL.User-
# Event back to this endpoint, handled by _user_event()).
# --------------------------------------------------------------------------- #
_BG = "#0b0f14"
_PANEL = "#161d27"
_PANEL2 = "#1d2734"
_LINE = "#263141"
_ACCENT = "#ff7a3c"
_ACCENT_INK = "#1a1008"
_GREEN = "#36c17a"
_GREEN_INK = "#04240f"
_TEXT = "#f3f6fa"
_MUTED = "#9fb0c3"


def _fmt_mmss(secs):
    secs = max(0, int(secs or 0))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _spacer(px, vertical=False):
    key = "height" if vertical else "width"
    return {"type": "Container", key: f"{int(px)}dp"}


def _btn(text, args, bg=_LINE, color=_TEXT, grow=0, font="22dp", pad=12):
    """A touchable pill button that fires a SendEvent with literal `args`."""
    return {
        "type": "TouchWrapper",
        "grow": grow,
        "onPress": [{"type": "SendEvent", "arguments": args}],
        "item": {
            "type": "Frame",
            "backgroundColor": bg,
            "borderRadius": "14dp",
            "item": {
                "type": "Container",
                "direction": "row",
                "justifyContent": "center",
                "alignItems": "center",
                "paddingTop": f"{pad}dp",
                "paddingBottom": f"{pad}dp",
                "paddingLeft": "14dp",
                "paddingRight": "14dp",
                "items": [{
                    "type": "Text",
                    "text": text,
                    "fontSize": font,
                    "color": color,
                    "fontWeight": "700",
                    "textAlign": "center",
                    "maxLines": 1,
                }],
            },
        },
    }


def _render(items, token=APL_TOKEN):
    """Wrap a list of components in a full-screen dark document + directive."""
    doc = {
        "type": "APL",
        "version": "2022.1",
        "theme": "dark",
        "mainTemplate": {
            "items": [{
                "type": "Frame",
                "width": "100vw",
                "height": "100vh",
                "backgroundColor": _BG,
                "item": {
                    "type": "Container",
                    "width": "100%",
                    "height": "100%",
                    "direction": "column",
                    "paddingLeft": "4vw",
                    "paddingRight": "4vw",
                    "paddingTop": "3vh",
                    "paddingBottom": "3vh",
                    "items": items,
                },
            }],
        },
    }
    return {
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": token,
        "document": doc,
    }


def _tabbar(recipe, active):
    """Menu / Equipment / Prep / Cook tab row (mirrors the web app tabs)."""
    def tab(label, key, args):
        on = (key == active)
        return _btn(
            label, args,
            bg=(_ACCENT if on else _PANEL2),
            color=(_ACCENT_INK if on else _MUTED),
            grow=1, font="19dp", pad=10,
        )
    return {
        "type": "Container",
        "direction": "row",
        "width": "100%",
        "items": [
            tab("\u2261 Menu", "menu", ["menu"]),
            _spacer(8),
            tab("\U0001f9f0 Equip", "equip", ["equip", recipe.slug]),
            _spacer(8),
            tab("\U0001f52a Prep", "prep", ["prep", recipe.slug]),
            _spacer(8),
            tab("\U0001f525 Cook", "cook", ["open", recipe.slug]),
        ],
    }


def _home_card(r):
    return {
        "type": "TouchWrapper",
        "width": "100%",
        "onPress": [{"type": "SendEvent", "arguments": ["overview", r.slug]}],
        "item": {
            "type": "Frame",
            "backgroundColor": _PANEL,
            "borderRadius": "18dp",
            "borderColor": _LINE,
            "borderWidth": "1dp",
            "height": "156dp",
            "item": {
                "type": "Container",
                "width": "100%",
                "height": "100%",
                "direction": "column",
                "paddingLeft": "18dp",
                "paddingRight": "18dp",
                "paddingTop": "14dp",
                "paddingBottom": "14dp",
                "items": [
                    {"type": "Text",
                     "text": (f"DAY {r.day_number}" if r.day_number else "RECIPE"),
                     "fontSize": "15dp", "color": _ACCENT, "fontWeight": "800",
                     "maxLines": 1},
                    {"type": "Text", "text": r.title, "fontSize": "24dp",
                     "color": _TEXT, "fontWeight": "700", "grow": 1, "maxLines": 3},
                    {"type": "Text", "text": (r.servings or ""), "fontSize": "15dp",
                     "color": _MUTED, "maxLines": 1},
                ],
            },
        },
    }


def _apl_home_directive(recipes):
    """Scrollable two-column grid of tappable recipe cards."""
    cards = [_home_card(r) for r in recipes]
    rows = []
    for i in range(0, len(cards), 2):
        pair = [{"type": "Container", "grow": 1, "items": [cards[i]]}, _spacer(14)]
        if i + 1 < len(cards):
            pair.append({"type": "Container", "grow": 1, "items": [cards[i + 1]]})
        else:
            pair.append({"type": "Container", "grow": 1})
        rows.append({"type": "Container", "direction": "row", "width": "100%", "items": pair})
        rows.append(_spacer(14, vertical=True))
    header = [
        {"type": "Text", "text": "Family Kitchen", "fontSize": "34dp",
         "color": _ACCENT, "fontWeight": "800", "maxLines": 1},
        {"type": "Text", "text": "Tap a recipe to open it", "fontSize": "20dp",
         "color": _MUTED, "maxLines": 1, "paddingBottom": "1vh"},
    ]
    scroll = {
        "type": "ScrollView",
        "grow": 1,
        "width": "100%",
        "item": {"type": "Container", "width": "100%", "direction": "column", "items": rows},
    }
    return _render(header + [scroll])


def _apl_list_directive(recipe, active, heading, lines):
    """Equipment / Prep style list view with the tab bar and a Start button."""
    items = [
        _tabbar(recipe, active),
        _spacer(10, vertical=True),
        {"type": "Text", "text": recipe.title, "fontSize": "22dp", "color": _MUTED, "maxLines": 1},
        {"type": "Text", "text": heading, "fontSize": "26dp", "color": _ACCENT,
         "fontWeight": "800", "maxLines": 1, "paddingBottom": "1vh"},
    ]
    list_items = []
    for ln in lines:
        list_items.append({
            "type": "Frame", "backgroundColor": _PANEL, "borderRadius": "12dp",
            "borderColor": _LINE, "borderWidth": "1dp", "width": "100%",
            "item": {"type": "Container", "paddingLeft": "16dp", "paddingRight": "16dp",
                     "paddingTop": "12dp", "paddingBottom": "12dp",
                     "items": [{"type": "Text", "text": ln, "fontSize": "22dp",
                                "color": _TEXT, "maxLines": 4}]},
        })
        list_items.append(_spacer(8, vertical=True))
    items.append({
        "type": "ScrollView", "grow": 1, "width": "100%",
        "item": {"type": "Container", "width": "100%", "items": list_items},
    })
    items.append(_spacer(8, vertical=True))
    items.append(_btn("\U0001f525 Start cooking", ["open", recipe.slug],
                      bg=_ACCENT, color=_ACCENT_INK, font="24dp", pad=14))
    return _render(items)


def _dots(n, cur):
    dots = []
    for i in range(n):
        if i == cur:
            c = _ACCENT
        elif i < cur:
            c = _GREEN
        else:
            c = _LINE
        dots.append({"type": "Frame", "width": "12dp", "height": "12dp",
                     "borderRadius": "6dp", "backgroundColor": c})
        dots.append(_spacer(6))
    return {"type": "Container", "direction": "row", "justifyContent": "center",
            "wrap": "wrap", "items": dots}


def _apl_step_directive(recipe, step, timer_override=None):
    """Interactive cook view: big step text, editable timer, prev/next, dots."""
    steps = recipe.steps
    n = len(steps)
    step = max(0, min(step, n - 1))
    s = steps[step]
    base_secs = s.get("timer_seconds") or 0
    secs = timer_override if timer_override is not None else base_secs
    has_timer = bool(base_secs) or bool(timer_override)

    items = [
        _tabbar(recipe, "cook"),
        _spacer(10, vertical=True),
        {"type": "Container", "direction": "row", "width": "100%", "alignItems": "center",
         "items": [
             {"type": "Text", "text": recipe.title, "fontSize": "20dp", "color": _MUTED,
              "grow": 1, "maxLines": 1},
             {"type": "Text", "text": f"Step {step + 1} of {n}", "fontSize": "18dp",
              "color": _ACCENT, "fontWeight": "800", "maxLines": 1},
         ]},
        _spacer(8, vertical=True),
        {"type": "Frame", "grow": 1, "width": "100%", "backgroundColor": _PANEL,
         "borderRadius": "18dp", "borderColor": _ACCENT, "borderWidth": "1dp",
         "item": {"type": "Container", "width": "100%", "height": "100%",
                  "justifyContent": "center", "paddingLeft": "22dp", "paddingRight": "22dp",
                  "paddingTop": "16dp", "paddingBottom": "16dp",
                  "items": [{"type": "Text", "text": s["text"], "fontSize": "34dp",
                             "color": _TEXT, "textAlign": "center", "maxLines": 8}]}},
    ]

    if has_timer:
        items.append(_spacer(10, vertical=True))
        items.append({
            "type": "Container", "direction": "row", "width": "100%",
            "justifyContent": "center", "alignItems": "center",
            "items": [
                _btn("\u2212", ["tadj", recipe.slug, step, secs, -60],
                     bg=_PANEL2, font="30dp", pad=6),
                _spacer(12),
                {"type": "Frame", "backgroundColor": _PANEL2, "borderRadius": "12dp",
                 "item": {"type": "Container", "paddingLeft": "22dp", "paddingRight": "22dp",
                          "paddingTop": "8dp", "paddingBottom": "8dp",
                          "items": [{"type": "Text", "text": _fmt_mmss(secs), "fontSize": "30dp",
                                     "color": _TEXT, "fontWeight": "800", "maxLines": 1}]}},
                _spacer(12),
                _btn("+", ["tadj", recipe.slug, step, secs, 60], bg=_PANEL2, font="30dp", pad=6),
                _spacer(16),
                _btn("\u23f1 Start", ["tstart", recipe.slug, step, secs],
                     bg=_GREEN, color=_GREEN_INK, font="22dp"),
            ],
        })

    items.append(_spacer(12, vertical=True))
    prev_btn = (_btn("\u25c0 Prev", ["prev", recipe.slug, step], bg=_PANEL2, grow=1)
                if step > 0 else {"type": "Container", "grow": 1})
    next_label = "Done \u2713" if step == n - 1 else "Next \u25b6"
    items.append({
        "type": "Container", "direction": "row", "width": "100%",
        "items": [
            prev_btn,
            _spacer(14),
            _btn(next_label, ["next", recipe.slug, step], bg=_ACCENT, color=_ACCENT_INK, grow=1),
        ],
    })
    items.append(_spacer(10, vertical=True))
    items.append(_dots(n, step))
    return _render(items)
