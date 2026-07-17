"""
Hidden "Kitchen" recipe/menu app (mounted at /kitchen).

- Display/cook screens are open (obscure, unlisted URL) and optimised for the
  Alexa Show: big touch targets, Equipment -> Prep -> Cook flow, and
  pre-programmed countdown timers with "Start timer" buttons.
- Add / edit / upload / delete screens require an admin login.

Recipe list/step data is stored as JSON-in-Text on the Recipe model so it works
on SQLite (local) and PostgreSQL (production) with no extra dependency.
"""
import io
import re
import json
import zipfile
import unicodedata
from datetime import datetime
from html import unescape

from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, url_for, abort, jsonify, Response,
)
from flask_login import current_user, login_required

recipes_bp = Blueprint(
    "recipes", __name__,
    url_prefix="/kitchen",
    template_folder="templates",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _models():
    return current_app.db, current_app.Recipe


def _admin_only():
    """Guard used inside routes that mutate data."""
    if not current_user.is_authenticated:
        return redirect(url_for("login", next=request.path))
    if not current_user.is_admin():
        flash("You need an admin account to manage recipes.", "danger")
        return redirect(url_for("recipes.kitchen_index"))
    return None


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "recipe"


def _unique_slug(Recipe, base, exclude_id=None):
    slug = base
    i = 2
    while True:
        q = Recipe.query.filter_by(slug=slug)
        if exclude_id is not None:
            q = q.filter(Recipe.id != exclude_id)
        if not q.first():
            return slug
        slug = f"{base}-{i}"
        i += 1


# --- timer parsing --------------------------------------------------------- #
_DURATION_RE = re.compile(
    r"(\d+)\s*(?:[\u2013\u2014\-]\s*(\d+))?\s*(hour|hours|hr|hrs|minute|minutes|min|mins|second|seconds|sec|secs)",
    re.IGNORECASE,
)


def parse_timer_seconds(text):
    """Return (seconds, human_label) for the first duration found, else (None, None).

    For a range like "25-30 minutes" the upper bound is used.
    """
    if not text:
        return None, None
    m = _DURATION_RE.search(text)
    if not m:
        return None, None
    low, high, unit = m.group(1), m.group(2), m.group(3).lower()
    value = int(high) if high else int(low)
    if unit.startswith("h"):
        seconds = value * 3600
        unit_word = "hour" if value == 1 else "hours"
    elif unit.startswith("m") and unit.startswith("min"):
        seconds = value * 60
        unit_word = "minute" if value == 1 else "minutes"
    elif unit.startswith("s"):
        seconds = value
        unit_word = "second" if value == 1 else "seconds"
    else:  # 'm' fallthrough shouldn't happen
        seconds = value * 60
        unit_word = "minutes"
    label = f"{value} {unit_word}"
    return seconds, label


def build_steps(step_texts):
    """Turn a list of step strings into step dicts with auto timers."""
    steps = []
    for txt in step_texts:
        txt = (txt or "").strip()
        if not txt:
            continue
        secs, label = parse_timer_seconds(txt)
        steps.append({
            "text": txt,
            "timer_seconds": secs,
            "timer_label": label,
        })
    return steps


# --- equipment inference --------------------------------------------------- #
_EQUIP_RULES = [
    (re.compile(r"slow cooker", re.I), "Slow cooker"),
    (re.compile(r"\boven\b|roast|bake|\b\d{3}\s*[\u00b0]?c\b|grill", re.I), "Oven"),
    (re.compile(r"stir[ -]?fry|\bwok\b", re.I), "Wok or large frying pan"),
    (re.compile(r"steam", re.I), "Steamer or saucepan"),
    (re.compile(r"noodle|spaghetti|orzo|bulgur|\brice\b|pasta|boil|packet|stock", re.I), "Saucepan (for boiling)"),
    (re.compile(r"large pan|\bpan\b|\bfry\b|brown|simmer|saut", re.I), "Large pan"),
    (re.compile(r"pizza|\bbase\b", re.I), "Baking tray"),
]


def infer_equipment(step_texts):
    found = []
    joined = " ".join(step_texts)
    for rx, name in _EQUIP_RULES:
        if rx.search(joined) and name not in found:
            found.append(name)
    # sensible always-there basics
    for basic in ("Chopping board & knife", "Wooden spoon", "Measuring spoons"):
        if basic not in found:
            found.append(basic)
    return found


# --------------------------------------------------------------------------- #
# .docx parsing (no external dependency)
# --------------------------------------------------------------------------- #
def _docx_paragraphs(file_bytes):
    """Extract paragraph strings from a .docx byte stream."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    # paragraph boundaries
    xml = re.sub(r"<w:p[ >]", "\n", xml)
    # line breaks / tabs inside a paragraph
    xml = re.sub(r"<w:(br|tab)\b[^>]*/?>", " ", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = unescape(text)
    lines = [ln.strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


_DAY_RE = re.compile(r"^Day\s*\d+\s*[\u2013\u2014\-]\s*(.+)$", re.I)


def parse_docx_recipes(file_bytes):
    """Parse a booklet in the '14-Day Family Meal Plan' style into recipe dicts.

    Recognises 'Day N - Title', a 'Baby:' note, a 'Method' heading followed by
    numbered/plain step lines until the next day or a blank section.
    """
    paras = _docx_paragraphs(file_bytes)
    recipes = []
    current = None
    mode = None  # None | 'method'
    day_counter = 0

    def flush():
        nonlocal current
        if current and current["steps_raw"]:
            recipes.append(current)
        current = None

    for line in paras:
        m = _DAY_RE.match(line)
        if m:
            flush()
            day_counter += 1
            current = {
                "title": m.group(1).strip(),
                "day_number": day_counter,
                "baby_note": "",
                "steps_raw": [],
            }
            mode = None
            continue
        if current is None:
            continue
        low = line.lower()
        if low.startswith("baby:"):
            current["baby_note"] = line.split(":", 1)[1].strip()
            continue
        if low == "method" or low.startswith("method"):
            mode = "method"
            continue
        if mode == "method":
            # strip a leading list number like "1. " / "1) "
            cleaned = re.sub(r"^\s*\d+[.)]\s*", "", line)
            current["steps_raw"].append(cleaned)

    flush()
    return recipes


# --------------------------------------------------------------------------- #
# Built-in 14-Day Family Meal Plan seed data
# --------------------------------------------------------------------------- #
_BABY = ("Remove a portion before adding extra chilli or salt. Cut into appropriate "
         "bite-sized pieces and add yogurt, olive oil or avocado if extra calories are needed.")

SEED_RECIPES = [
    ("Hidden Veg Beef & Lentil Bolognese", [
        "Heat 1 tbsp olive oil in a large pan over a medium heat (2 minutes).",
        "Brown 500g lean beef mince for 5\u20137 minutes.",
        "Add 1 diced onion, 2 grated carrots and 1 grated courgette. Cook for 8 minutes until softened.",
        "Stir in 500g passata and 1 drained tin of lentils. Simmer gently for 25\u201330 minutes.",
        "Meanwhile cook 300g wholewheat spaghetti according to the packet (10\u201312 minutes).",
        "Serve topped with a little Parmesan. Remove the baby's portion before seasoning further.",
    ]),
    ("Chicken Tikka Curry", [
        "Dice 500g chicken and fry for 5 minutes.",
        "Add diced onion and cook another 5 minutes.",
        "Stir in 2 tbsp tikka seasoning and cook 1 minute.",
        "Add 1 tin chopped tomatoes and simmer for 20 minutes.",
        "Stir through 3 tbsp Greek yogurt off the heat.",
        "Serve with cooked rice.",
    ]),
    ("Creamy Chicken Orzo", [
        "Brown 400g diced chicken for 6 minutes.",
        "Add 250g orzo and 700ml chicken stock.",
        "Simmer 10\u201312 minutes stirring occasionally.",
        "Add peas and spinach for 3 minutes.",
        "Finish with 3 tbsp light cream cheese.",
    ]),
    ("Loaded Burrito Bowls", [
        "Cook seasoned mince for 8\u201310 minutes.",
        "Warm black beans and sweetcorn (3 minutes).",
        "Reheat rice.",
        "Assemble with lettuce, salsa and Greek yogurt.",
    ]),
    ("Sticky Chicken Noodles", [
        "Cook noodles.",
        "Stir fry chicken 6 minutes.",
        "Add broccoli and carrots for 5 minutes.",
        "Mix soy, honey, garlic and ginger then toss together for 2 minutes.",
    ]),
    ("Naked Burgers & Loaded Potatoes", [
        "Shape burgers and chill 10 minutes.",
        "Roast potato wedges at 200\u00b0C for 35\u201340 minutes.",
        "Pan fry or grill burgers for 4\u20135 minutes per side.",
        "Serve with salad and yogurt burger sauce.",
    ]),
    ("Thai Coconut Chicken Curry", [
        "Fry curry paste 1 minute.",
        "Add chicken and brown 5 minutes.",
        "Pour in coconut milk.",
        "Add peppers and green beans.",
        "Simmer 20 minutes and serve with rice.",
    ]),
    ("Beef Kofta & Bulgur Bowls", [
        "Mix mince with garlic, cumin and paprika.",
        "Shape into koftas.",
        "Bake at 200\u00b0C for 18\u201320 minutes.",
        "Serve over bulgur with salad and yogurt.",
    ]),
    ("Homemade Pizza", [
        "Heat oven to maximum (220\u2013250\u00b0C).",
        "Spread passata over base.",
        "Top with mozzarella and toppings.",
        "Bake 10\u201312 minutes.",
    ]),
    ("Crispy White Fish Bowls", [
        "Season fish.",
        "Bake at 200\u00b0C for 15\u201318 minutes.",
        "Steam broccoli and carrots for 6\u20138 minutes.",
        "Serve with bulgur or rice.",
    ]),
    ("Honey Garlic Chicken Stir Fry", [
        "Cook chicken 6 minutes.",
        "Add vegetables for 5 minutes.",
        "Add sauce and cooked rice/noodles.",
        "Cook 2 minutes more.",
    ]),
    ("Slow Cooker Chilli", [
        "Brown mince and onions 8 minutes.",
        "Transfer to slow cooker with beans and tomatoes.",
        "Cook LOW 6\u20138 hours or HIGH 4 hours.",
        "Serve with rice.",
    ]),
    ("Pesto Chicken & Roasted Veg", [
        "Roast chicken thighs and chopped vegetables at 200\u00b0C for 35\u201340 minutes.",
        "Toss with pesto before serving.",
    ]),
    ("Takeaway Curry", [
        "Choose a tomato-based curry such as Bhuna, Jalfrezi, Madras or Rogan Josh.",
        "Share one portion of rice between two adults if needed to maintain your calorie target.",
        "Skip starters and naan for a lighter meal.",
    ]),
]


def _seed_plan(db, Recipe, replace=False):
    """Insert the built-in 14-day plan. Returns (added, skipped)."""
    plan_name = "14-Day Family Meal Plan"
    if replace:
        Recipe.query.filter_by(meal_plan=plan_name).delete()
        db.session.commit()
    added, skipped = 0, 0
    for idx, (title, step_texts) in enumerate(SEED_RECIPES, start=1):
        base = slugify(title)
        if Recipe.query.filter_by(slug=base).first():
            skipped += 1
            continue
        r = Recipe(
            slug=_unique_slug(Recipe, base),
            title=title,
            meal_plan=plan_name,
            day_number=idx,
            servings="2 adults + 1 baby",
            baby_note=_BABY,
            published=True,
        )
        r.steps = build_steps(step_texts)
        r.equipment = infer_equipment(step_texts)
        r.ingredients = []
        r.prep = []
        db.session.add(r)
        added += 1
    db.session.commit()
    return added, skipped


# --------------------------------------------------------------------------- #
# form parsing
# --------------------------------------------------------------------------- #
def _lines(raw):
    return [ln.strip() for ln in (raw or "").replace("\r\n", "\n").split("\n") if ln.strip()]


def _apply_form(recipe, Recipe, form):
    recipe.title = (form.get("title") or "").strip() or "Untitled recipe"
    recipe.meal_plan = (form.get("meal_plan") or "").strip() or None
    day = (form.get("day_number") or "").strip()
    recipe.day_number = int(day) if day.isdigit() else None
    recipe.servings = (form.get("servings") or "").strip() or None
    recipe.baby_note = (form.get("baby_note") or "").strip() or None
    recipe.published = form.get("published") == "on"
    recipe.equipment = _lines(form.get("equipment"))
    recipe.ingredients = _lines(form.get("ingredients"))
    recipe.prep = _lines(form.get("prep"))

    # Steps: paired text[] + timer[] (mm:ss or minutes). If timer blank, auto-detect.
    texts = form.getlist("step_text")
    timers = form.getlist("step_timer")
    steps = []
    for i, txt in enumerate(texts):
        txt = (txt or "").strip()
        if not txt:
            continue
        raw_timer = (timers[i] if i < len(timers) else "").strip()
        secs, label = _timer_from_input(raw_timer)
        if secs is None:
            secs, label = parse_timer_seconds(txt)
        steps.append({"text": txt, "timer_seconds": secs, "timer_label": label})
    recipe.steps = steps


def _timer_from_input(raw):
    """Accept 'mm:ss', 'mm', or '' for a manual timer override."""
    if not raw:
        return None, None
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            mins = int(parts[0] or 0)
            secs = int(parts[1] or 0)
        except ValueError:
            return None, None
        total = mins * 60 + secs
    elif raw.isdigit():
        total = int(raw) * 60
    else:
        return None, None
    if total <= 0:
        return None, None
    return total, _human_seconds(total)


def _human_seconds(total):
    if total % 3600 == 0:
        h = total // 3600
        return f"{h} hour" if h == 1 else f"{h} hours"
    if total >= 60:
        m, s = divmod(total, 60)
        if s == 0:
            return f"{m} minute" if m == 1 else f"{m} minutes"
        return f"{m}m {s}s"
    return f"{total} seconds"


# --------------------------------------------------------------------------- #
# Public (open) routes
# --------------------------------------------------------------------------- #
@recipes_bp.route("/")
def kitchen_index():
    db, Recipe = _models()
    recipes = Recipe.query.filter_by(published=True).order_by(
        Recipe.meal_plan, Recipe.day_number, Recipe.title
    ).all()
    can_manage = current_user.is_authenticated and current_user.is_admin()
    return render_template("kitchen/index.html", recipes=recipes, can_manage=can_manage)


@recipes_bp.route("/r/<slug>")
def kitchen_recipe(slug):
    db, Recipe = _models()
    recipe = Recipe.query.filter_by(slug=slug).first_or_404()
    can_manage = current_user.is_authenticated and current_user.is_admin()
    return render_template("kitchen/recipe.html", recipe=recipe, can_manage=can_manage,
                           human_seconds=_human_seconds)


# --------------------------------------------------------------------------- #
# JSON API (read-only, public) — used by the Alexa skill and any external view
# --------------------------------------------------------------------------- #
@recipes_bp.route("/api/recipes")
def api_recipes():
    db, Recipe = _models()
    recipes = Recipe.query.filter_by(published=True).order_by(
        Recipe.meal_plan, Recipe.day_number, Recipe.title
    ).all()
    return jsonify({
        "count": len(recipes),
        "recipes": [
            {"slug": r.slug, "title": r.title, "day_number": r.day_number,
             "meal_plan": r.meal_plan, "servings": r.servings,
             "steps": len(r.steps), "has_timers": r.has_timers()}
            for r in recipes
        ],
    })


@recipes_bp.route("/api/recipe/<slug>")
def api_recipe(slug):
    db, Recipe = _models()
    recipe = Recipe.query.filter_by(slug=slug, published=True).first_or_404()
    return jsonify(recipe.to_dict())


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #
@recipes_bp.route("/manage")
@login_required
def manage_index():
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    recipes = Recipe.query.order_by(Recipe.meal_plan, Recipe.day_number, Recipe.title).all()
    return render_template("kitchen/manage.html", recipes=recipes)


@recipes_bp.route("/manage/new", methods=["GET", "POST"])
@login_required
def manage_new():
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    if request.method == "POST":
        recipe = Recipe()
        _apply_form(recipe, Recipe, request.form)
        recipe.slug = _unique_slug(Recipe, slugify(recipe.title))
        db.session.add(recipe)
        db.session.commit()
        flash("Recipe created.", "success")
        return redirect(url_for("recipes.kitchen_recipe", slug=recipe.slug))
    return render_template("kitchen/form.html", recipe=None, mode="new")


@recipes_bp.route("/manage/<slug>/edit", methods=["GET", "POST"])
@login_required
def manage_edit(slug):
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    recipe = Recipe.query.filter_by(slug=slug).first_or_404()
    if request.method == "POST":
        _apply_form(recipe, Recipe, request.form)
        # keep slug stable unless title changed and slug now clashes; regenerate if requested
        if request.form.get("regenerate_slug") == "on":
            recipe.slug = _unique_slug(Recipe, slugify(recipe.title), exclude_id=recipe.id)
        db.session.commit()
        flash("Recipe updated.", "success")
        return redirect(url_for("recipes.kitchen_recipe", slug=recipe.slug))
    return render_template("kitchen/form.html", recipe=recipe, mode="edit")


@recipes_bp.route("/manage/<slug>/delete", methods=["POST"])
@login_required
def manage_delete(slug):
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    recipe = Recipe.query.filter_by(slug=slug).first_or_404()
    db.session.delete(recipe)
    db.session.commit()
    flash("Recipe deleted.", "success")
    return redirect(url_for("recipes.manage_index"))


@recipes_bp.route("/manage/seed", methods=["POST"])
@login_required
def manage_seed():
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    replace = request.form.get("replace") == "on"
    added, skipped = _seed_plan(db, Recipe, replace=replace)
    flash(f"Built-in 14-day plan imported: {added} added, {skipped} skipped.", "success")
    return redirect(url_for("recipes.manage_index"))


@recipes_bp.route("/manage/import", methods=["POST"])
@login_required
def manage_import():
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    file = request.files.get("docx")
    if not file or not file.filename:
        flash("Please choose a .docx file to upload.", "danger")
        return redirect(url_for("recipes.manage_index"))
    if not file.filename.lower().endswith(".docx"):
        flash("Only .docx files are supported.", "danger")
        return redirect(url_for("recipes.manage_index"))
    try:
        parsed = parse_docx_recipes(file.read())
    except Exception as e:
        flash(f"Could not read that .docx: {e}", "danger")
        return redirect(url_for("recipes.manage_index"))
    if not parsed:
        flash("No recipes found. Expected 'Day N - Title' headings followed by a 'Method' list.", "warning")
        return redirect(url_for("recipes.manage_index"))

    plan_name = (request.form.get("plan_name") or "Imported plan").strip() or "Imported plan"
    added, skipped = 0, 0
    for p in parsed:
        base = slugify(p["title"])
        if Recipe.query.filter_by(slug=base).first():
            skipped += 1
            continue
        r = Recipe(
            slug=_unique_slug(Recipe, base),
            title=p["title"],
            meal_plan=plan_name,
            day_number=p.get("day_number"),
            baby_note=p.get("baby_note") or None,
            published=True,
        )
        r.steps = build_steps(p["steps_raw"])
        r.equipment = infer_equipment(p["steps_raw"])
        r.ingredients = []
        r.prep = []
        db.session.add(r)
        added += 1
    db.session.commit()
    flash(f"Imported from {file.filename}: {added} added, {skipped} skipped (duplicate).", "success")
    return redirect(url_for("recipes.manage_index"))


@recipes_bp.route("/manage/export")
@login_required
def manage_export():
    guard = _admin_only()
    if guard:
        return guard
    db, Recipe = _models()
    data = [r.to_dict() for r in Recipe.query.order_by(Recipe.meal_plan, Recipe.day_number).all()]
    payload = json.dumps({"exported_at": datetime.utcnow().isoformat(), "recipes": data}, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=recipes-export.json"},
    )
