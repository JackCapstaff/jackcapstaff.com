from flask import Flask, render_template, request, redirect, url_for, jsonify
import json
import os
from datetime import datetime

# Import the portable engine extracted from the original Tk app
from print_engine import line_breakdown, calculate_totals, get_pages_per_sheet


# Default settings mapping used by the engine when no overrides are provided
DEFAULT_SETTINGS = {
    "cost_a4": 0.05,
    "cost_a3": 0.12,
    "ink_cost_a4": 0.03,
    "ink_cost_a3": 0.035,
    "photo_paper_surcharge": 0.15,
    "acetate_cost": 0.60,
    "labour_per_job": 0.25,
    "markup_multiplier": 1.25,
    "binding_costs": {"None": 0.0, "Staple": 0.10, "Plastic Comb": 0.40, "Wire Comb": 0.60},
    "binding_labour": {"None": 0.0, "Staple": 0.30, "Plastic Comb": 1.00, "Wire Comb": 2.50},
    "bw_cover_costs": {"Card 300gsm": 1.10, "Card 450gsm": 1.15, "Card 600gsm": 1.20},
    "colour_cover_costs": {"Card 300gsm": 1.20, "Card 450gsm": 1.30, "Card 600gsm": 1.40},
    "paper_grade_surcharge": {
        "80gsm": {"A4": 0.0, "A3": 0.0},
        "100gsm": {"A4": 0.01, "A3": 0.02},
        "110gsm": {"A4": 0.02, "A3": 0.04},
        "120gsm": {"A4": 0.03, "A3": 0.06},
    }
}

app = Flask(__name__)
ORDERS_FILE = os.path.join(os.path.dirname(__file__), "orders.json")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def calculate_cost(qty: int, pages: int, color_sides: int, paper_type: str, binding: str, lamination: bool) -> dict:
    # Simple pricing stub — replace with your real pricing rules
    base_paper_cost = 0.01 if paper_type == "standard" else 0.03
    color_cost_per_side = 0.03 if color_sides > 0 else 0.0
    binding_cost = {"none": 0.0, "staple": 0.50, "spine": 1.50}.get(binding, 0.0)
    lamination_cost = 0.10 if lamination else 0.0

    pages_cost = pages * base_paper_cost
    color_cost = pages * color_sides * color_cost_per_side
    unit_price = pages_cost + color_cost + binding_cost + lamination_cost

    # volume discount example
    if qty >= 500:
        unit_price *= 0.6
    elif qty >= 200:
        unit_price *= 0.75
    elif qty >= 50:
        unit_price *= 0.9

    total = round(unit_price * qty, 2)
    return {
        "qty": qty,
        "pages": pages,
        "color_sides": color_sides,
        "paper_type": paper_type,
        "binding": binding,
        "lamination": lamination,
        "unit_price": round(unit_price, 4),
        "total": total,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/quote", methods=["POST"])
def quote():
    form = request.form
    # basic validation
    try:
        qty = int(form.get("qty", "1"))
        pages = int(form.get("pages", "1"))
    except ValueError:
        return "Invalid input", 400

    # Build settings from defaults and optional overrides
    settings = DEFAULT_SETTINGS.copy()
    if form.get("markup_multiplier"):
        try:
            settings["markup_multiplier"] = float(form.get("markup_multiplier"))
        except Exception:
            pass

    # handle uploaded files (multiple allowed)
    files = request.files.getlist("files")
    items = []
    if files:
        for f in files:
            if not f or f.filename == "":
                continue
            # save uploaded pdf
            try:
                from werkzeug.utils import secure_filename
            except Exception:
                secure_name = f.filename
            else:
                secure_name = secure_filename(f.filename)
            save_path = os.path.join(UPLOAD_FOLDER, secure_name)
            f.save(save_path)
            item = {
                "file": save_path,
                "file_name": secure_name,
                "pages": int(form.get("pages", pages)),
                "qty": int(form.get("qty", qty)),
                "type": form.get("print_type", "A4 Double-sided"),
                "binding": form.get("binding", "None").title(),
                "front_cover": form.get("front_cover", "None"),
                "back_cover": form.get("back_cover", "None"),
                "acetate": form.get("acetate", "None"),
                "paper_type": "Photo" if form.get("paper_type", "standard") == "premium" else "Standard",
                "paper_grade": form.get("paper_grade", "120gsm"),
            }
            items.append(item)
    else:
        # fallback: build a single pseudo-item (no file)
        item = {
            "file": None,
            "file_name": form.get("file_name", "web_quote"),
            "pages": int(form.get("pages", pages)),
            "qty": int(form.get("qty", qty)),
            "type": form.get("print_type", "A4 Double-sided"),
            "binding": form.get("binding", "None").title(),
            "front_cover": form.get("front_cover", "None"),
            "back_cover": form.get("back_cover", "None"),
            "acetate": form.get("acetate", "None"),
            "paper_type": "Photo" if form.get("paper_type", "standard") == "premium" else "Standard",
            "paper_grade": form.get("paper_grade", "120gsm"),
        }
        items.append(item)

    totals = calculate_totals(items, settings)
    return render_template("quote.html", totals=totals, form=form)


@app.route('/api/quote_json', methods=['POST'])
def quote_json():
    """POST JSON { items: [...], settings: {...} } -> computed breakdowns and totals."""
    payload = request.get_json(force=True)
    items = payload.get('items') if isinstance(payload.get('items'), list) else []
    settings = payload.get('settings', {}) or {}
    # merge provided settings onto defaults
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings)
    totals = calculate_totals(items, merged)
    return jsonify(totals)


@app.route("/submit-order", methods=["POST"])
def submit_order():
    data = request.form.to_dict()
    quote_data = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "customer": {
            "name": data.get("name"),
            "email": data.get("email"),
        },
        "order": {
            "qty": int(data.get("qty", 0)),
            "pages": int(data.get("pages", 0)),
            "color_sides": int(data.get("color_sides", 0)),
            "paper_type": data.get("paper_type"),
            "binding": data.get("binding"),
            "lamination": data.get("lamination") == "yes",
        },
        "pricing": {
            "unit_price": float(data.get("unit_price", 0)),
            "total": float(data.get("total", 0)),
        },
    }

    os.makedirs(os.path.dirname(ORDERS_FILE), exist_ok=True)
    # append as JSON lines
    with open(ORDERS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(quote_data) + "\n")

    return render_template("thankyou.html", order=quote_data)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
