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
MEAL_PLAN_INVOCATION = "family kitchen"

# module-level cert cache: {url: pem_bytes}
_CERT_CACHE = {}


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
        "outputSpeech": {"type": "PlainText", "text": speech},
        "shouldEndSession": end,
    }
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


def _apl_directive(recipe, step_index=None, heading=None, lines=None, subtitle=None):
    """Build an APL RenderDocument directive for the Echo Show."""
    if step_index is not None:
        steps = recipe.steps
        s = steps[step_index]
        heading = f"Step {step_index + 1} of {len(steps)}"
        primary = s["text"]
        subtitle = recipe.title
        timer_label = s.get("timer_label") or ""
        nxt = steps[step_index + 1]["text"] if step_index + 1 < len(steps) else ""
    else:
        primary = "\n".join(f"• {ln}" for ln in (lines or []))
        timer_label = ""
        nxt = ""

    return {
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": APL_TOKEN,
        "document": _APL_DOC,
        "datasources": {
            "data": {
                "heading": heading or "",
                "subtitle": subtitle or "",
                "primary": primary,
                "timer": (f"Timer: {timer_label}" if timer_label else ""),
                "nextUp": (f"Next: {nxt}" if nxt else ""),
            }
        },
    }


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
              "You can say, for example, open day one, or, cook the chicken tikka curry. "
              "You can also say, what's on the menu.")
    reprompt = "Which recipe would you like? Try, open day one."
    directives = None
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
    parts.append("Say start cooking to begin, or ask what equipment or ingredients you need.")
    speech = " ".join(parts)
    directives = []
    if _supports_apl(envelope):
        directives.append(_apl_directive(
            recipe, heading=recipe.title,
            subtitle=(f"Serves {recipe.servings}" if recipe.servings else ""),
            lines=recipe.equipment or ["Say 'start cooking' to begin"],
        ))
    return _resp(speech, reprompt="Say start cooking to begin.", attributes=attrs,
                 directives=directives or None)


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


def _say_step(recipe, step, envelope):
    steps = recipe.steps
    step = max(0, min(step, len(steps) - 1))
    s = steps[step]
    attrs = {"slug": recipe.slug, "step": step}
    speech = f"Step {step + 1}. {s['text']}"
    if s.get("timer_label"):
        speech += f" You can say start the timer for {s['timer_label']}."
    if step == len(steps) - 1:
        speech += " That's the last step. Enjoy!"
    directives = []
    if _supports_apl(envelope):
        directives.append(_apl_directive(recipe, step_index=step))
    reprompt = "Say next, repeat, or start the timer."
    return _resp(speech, reprompt=reprompt, attributes=attrs, directives=directives or None)


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
    directives = [_apl_directive(recipe, heading="Equipment", subtitle=recipe.title,
                                 lines=recipe.equipment)] if _supports_apl(envelope) else None
    return _resp("You'll need: " + _join(recipe.equipment) + ".",
                 reprompt="Say start cooking to begin.", attributes=attrs, directives=directives)


def _ingredients(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if not recipe.ingredients:
        return _resp("No separate ingredients are listed; they're mentioned within the steps.",
                     attributes=attrs)
    directives = [_apl_directive(recipe, heading="Ingredients", subtitle=recipe.title,
                                 lines=recipe.ingredients)] if _supports_apl(envelope) else None
    return _resp("Ingredients: " + _join(recipe.ingredients) + ".",
                 reprompt="Say start cooking to begin.", attributes=attrs, directives=directives)


def _prep(envelope, attrs):
    recipe, _ = _current(attrs)
    if not recipe:
        return _resp("Open a recipe first. Try, open day one.", reprompt="Try, open day one.")
    if not recipe.prep:
        return _resp("There are no separate prep notes for this recipe.", attributes=attrs)
    directives = [_apl_directive(recipe, heading="Prep", subtitle=recipe.title,
                                 lines=recipe.prep)] if _supports_apl(envelope) else None
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
    directives = [_apl_directive(recipe, step_index=step)] if _supports_apl(envelope) else None
    return _resp(speech, reprompt="Say next when you're ready.", attributes=attrs, directives=directives)


def _help(envelope, attrs):
    speech = ("You can say: open day one, or, cook the chicken tikka curry. "
              "Then say: start cooking, next, previous, repeat, what equipment do I need, "
              "or, start the timer. Say stop to exit.")
    return _resp(speech, reprompt="Which recipe would you like? Try, open day one.", attributes=attrs)


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
        return jsonify({"error": "unauthorized"}), 400

    try:
        return _handle(envelope)
    except Exception as e:  # noqa: BLE001 - never 500 to Alexa; speak a graceful error
        current_app.logger.exception("Alexa handler error")
        return _resp("Sorry, something went wrong in the kitchen. Please try again.", end=True)


# --------------------------------------------------------------------------- #
# APL document (kept inline so no static hosting is needed)
# --------------------------------------------------------------------------- #
_APL_DOC = {
    "type": "APL",
    "version": "2022.1",
    "mainTemplate": {
        "parameters": ["data"],
        "items": [
            {
                "type": "Container",
                "width": "100vw",
                "height": "100vh",
                "paddingLeft": "5vw",
                "paddingRight": "5vw",
                "paddingTop": "5vh",
                "paddingBottom": "5vh",
                "direction": "column",
                "items": [
                    {
                        "type": "Text",
                        "text": "${data.heading}",
                        "fontSize": "28dp",
                        "color": "#ff7a3c",
                        "fontWeight": "700",
                        "maxLines": 1
                    },
                    {
                        "type": "Text",
                        "text": "${data.subtitle}",
                        "fontSize": "20dp",
                        "color": "#9fb0c3",
                        "maxLines": 1,
                        "paddingBottom": "2vh"
                    },
                    {
                        "type": "Text",
                        "text": "${data.primary}",
                        "fontSize": "44dp",
                        "color": "#f3f6fa",
                        "fontWeight": "500",
                        "grow": 1,
                        "maxLines": 8
                    },
                    {
                        "type": "Text",
                        "text": "${data.timer}",
                        "fontSize": "26dp",
                        "color": "#36c17a",
                        "fontWeight": "700",
                        "maxLines": 1
                    },
                    {
                        "type": "Text",
                        "text": "${data.nextUp}",
                        "fontSize": "22dp",
                        "color": "#9fb0c3",
                        "maxLines": 2
                    }
                ]
            }
        ]
    }
}
