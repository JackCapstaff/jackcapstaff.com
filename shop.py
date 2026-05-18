import os
import re
import secrets
import smtplib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from email.message import EmailMessage

import requests
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for, abort
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

try:
    import cloudinary.uploader
except Exception:
    cloudinary = None

try:
    import stripe
except Exception:
    stripe = None

try:
    from stripe.error import SignatureVerificationError
except Exception:
    SignatureVerificationError = Exception


shop_bp = Blueprint("shop", __name__)


def _models():
    return (
        current_app.db,
        current_app.Product,
        current_app.ShopOrder,
        current_app.ShopOrderItem,
    )


def _editor_required():
    return current_user.is_authenticated and (current_user.is_admin() or current_user.is_editor())


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[-\s]+", "-", value).strip("-")
    return value or f"product-{secrets.token_hex(3)}"


def _format_money(cents: int, currency: str = "gbp") -> str:
    return f"{currency.upper()} {(cents or 0) / 100:.2f}"


def _parse_price_to_cents(raw_value: str) -> int:
    value = (raw_value or "").strip().replace(",", "")
    if not value:
        return 0
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return 0
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _cart_items():
    cart = session.get("shop_cart", [])
    return cart if isinstance(cart, list) else []


def _save_cart(items):
    session["shop_cart"] = items
    session.modified = True


def _upload_pdf(file_storage):
    if not file_storage or not getattr(file_storage, "filename", None):
        return None

    filename = file_storage.filename.lower()
    if not filename.endswith(".pdf"):
        return None

    if cloudinary is not None and os.environ.get("CLOUDINARY_URL", "").strip():
        try:
            result = cloudinary.uploader.upload(
                file_storage,
                folder="jackcapstaff/shop",
                resource_type="raw",
                use_filename=True,
                unique_filename=True,
            )
            return result.get("secure_url")
        except Exception:
            current_app.logger.exception("Cloudinary PDF upload failed; fallback to local storage")

    uploads_dir = os.path.join(current_app.root_path, "assets", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", file_storage.filename)
    out_name = f"pdf_{secrets.token_hex(8)}_{safe_name}"
    out_path = os.path.join(uploads_dir, out_name)
    file_storage.save(out_path)
    return f"/assets/uploads/{out_name}"


def _download_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="shop-download-v1")


def _build_download_token(order_item_id: int):
    return _download_serializer().dumps({"order_item_id": order_item_id})


def _resolve_stripe_api_key():
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def _resolve_paypal_base_url():
    mode = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
    if mode == "live":
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


def _paypal_access_token():
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    token_url = f"{_resolve_paypal_base_url()}/v1/oauth2/token"
    response = requests.post(
        token_url,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json", "Accept-Language": "en_US"},
        data={"grant_type": "client_credentials"},
        timeout=25,
    )
    if response.status_code >= 400:
        current_app.logger.error("PayPal token request failed: %s", response.text[:400])
        return None

    data = response.json()
    return data.get("access_token")


def _send_email(subject: str, to_email: str, text_body: str, html_body: str):
    api_key = current_app.config.get("BREVO_API_KEY", "").strip()
    from_email = current_app.config.get("BREVO_FROM_EMAIL", "").strip() or current_app.config.get("CONTACT_FROM_EMAIL", "").strip()
    from_name = current_app.config.get("BREVO_FROM_NAME", "").strip() or current_app.config.get("SITE_TITLE", "Jack Capstaff")

    if api_key and from_email:
        payload = {
            "sender": {"name": from_name, "email": from_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if response.status_code < 400:
            return True

    smtp_host = current_app.config.get("SMTP_HOST", "").strip()
    smtp_username = current_app.config.get("SMTP_USERNAME", "").strip()
    smtp_password = current_app.config.get("SMTP_PASSWORD", "").strip()
    smtp_port = int(current_app.config.get("SMTP_PORT", 587))

    if not smtp_host or not from_email:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        use_tls = current_app.config.get("SMTP_USE_TLS", True)
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_username:
                server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception("Failed sending shop email")
        return False


def _send_order_emails(order):
    if not order.customer_email:
        return

    _, _, _, ShopOrderItem = _models()
    items = ShopOrderItem.query.filter_by(order_id=order.id).all()
    download_lines = []
    for item in items:
        if item.delivery_format == "pdf" and item.pdf_file_url_snapshot:
            token = _build_download_token(item.id)
            link = url_for("shop.download_order_item", token=token, _external=True)
            download_lines.append(f"{item.title_snapshot}: {link}")

    items_text = "\n".join([
        f"- {item.title_snapshot} ({item.delivery_format.upper()}) x{item.quantity} - {_format_money(item.line_total_cents, order.currency)}"
        for item in items
    ])
    downloads_text = "\n".join(download_lines) if download_lines else "No digital downloads in this order."

    subject_customer = f"Your order receipt ({order.order_number})"
    text_customer = (
        f"Thank you for your order.\n\nOrder: {order.order_number}\n"
        f"Total: {_format_money(order.total_cents, order.currency)}\n\nItems:\n{items_text}\n\n"
        f"Digital downloads:\n{downloads_text}\n"
    )
    html_customer = (
        f"<p>Thank you for your order.</p><p><strong>Order:</strong> {order.order_number}<br>"
        f"<strong>Total:</strong> {_format_money(order.total_cents, order.currency)}</p>"
        f"<p><strong>Items</strong><br>{items_text.replace(chr(10), '<br>')}</p>"
        f"<p><strong>Digital downloads</strong><br>{downloads_text.replace(chr(10), '<br>')}</p>"
    )
    customer_ok = _send_email(subject_customer, order.customer_email, text_customer, html_customer)

    admin_to = current_app.config.get("CONTACT_TO_EMAIL", "").strip() or "jack@jackcapstaff.com"
    subject_admin = f"New shop order ({order.order_number})"
    shipping_block = "No physical shipping required."
    if order.has_physical_items:
        shipping_block = (
            f"Ship to: {order.shipping_name or ''}\n"
            f"{order.shipping_line1 or ''}\n{order.shipping_line2 or ''}\n"
            f"{order.shipping_city or ''} {order.shipping_postal_code or ''}\n{order.shipping_country or ''}"
        )

    text_admin = (
        f"New paid order received.\n\nOrder: {order.order_number}\n"
        f"Customer: {order.customer_name or ''} <{order.customer_email}>\n"
        f"Total: {_format_money(order.total_cents, order.currency)}\n\nItems:\n{items_text}\n\n{shipping_block}\n"
    )
    html_admin = f"<p>{text_admin.replace(chr(10), '<br>')}</p>"
    admin_ok = _send_email(subject_admin, admin_to, text_admin, html_admin)

    db, _, ShopOrder, _ = _models()
    order.customer_email_sent = bool(customer_ok)
    order.admin_email_sent = bool(admin_ok)
    db.session.commit()


def _cart_with_products():
    _, Product, _, _ = _models()
    cart = _cart_items()
    out = []
    total_cents = 0

    for entry in cart:
        product = Product.query.get(entry.get("product_id"))
        if not product or not product.published:
            continue

        fmt = entry.get("delivery_format")
        quantity = max(1, int(entry.get("quantity") or 1))
        if fmt == "pdf":
            unit = int(product.price_pdf_cents or 0)
        else:
            unit = int(product.price_print_cents or 0)

        if unit <= 0:
            continue

        line_total = unit * quantity
        total_cents += line_total
        out.append({
            "product": product,
            "delivery_format": fmt,
            "quantity": quantity,
            "unit_price_cents": unit,
            "line_total_cents": line_total,
        })

    return out, total_cents


def _create_pending_order_from_cart(customer_email: str):
    items, total_cents = _cart_with_products()
    if not items:
        return None, None, "Your cart is empty."

    db, _, ShopOrder, ShopOrderItem = _models()

    order = ShopOrder(
        order_number=f"JC{datetime.utcnow().strftime('%Y%m%d')}{secrets.randbelow(9000)+1000}",
        status="pending",
        customer_email=customer_email,
        currency="gbp",
        total_cents=total_cents,
        has_physical_items=any(row["delivery_format"] == "print" for row in items),
    )
    db.session.add(order)
    db.session.flush()

    for row in items:
        product = row["product"]
        db.session.add(ShopOrderItem(
            order_id=order.id,
            product_id=product.id,
            title_snapshot=product.title,
            delivery_format=row["delivery_format"],
            quantity=row["quantity"],
            unit_price_cents=row["unit_price_cents"],
            line_total_cents=row["line_total_cents"],
            pdf_file_url_snapshot=product.pdf_file_url,
        ))

    db.session.commit()
    return order, items, None


@shop_bp.app_context_processor
def inject_shop_helpers():
    return {"format_money": _format_money}


@shop_bp.route("/shop")
@shop_bp.route("/Shop")
def shop_index():
    _, Product, _, _ = _models()
    products = Product.query.filter_by(published=True).order_by(Product.sort_order.asc(), Product.created_at.desc()).all()
    return render_template("shop/index.html", products=products)


@shop_bp.route("/shop/<slug>")
def shop_product_detail(slug):
    _, Product, _, _ = _models()
    product = Product.query.filter_by(slug=slug, published=True).first_or_404()
    return render_template("shop/detail.html", product=product)


@shop_bp.route("/shop/cart")
def shop_cart():
    items, total_cents = _cart_with_products()
    return render_template("shop/cart.html", items=items, total_cents=total_cents)


@shop_bp.route("/shop/cart/add", methods=["POST"])
def shop_cart_add():
    _, Product, _, _ = _models()
    product_id = request.form.get("product_id", type=int)
    delivery_format = (request.form.get("delivery_format") or "").strip().lower()
    quantity = max(1, request.form.get("quantity", type=int) or 1)

    product = Product.query.get_or_404(product_id)
    if not product.published:
        abort(404)

    if delivery_format not in {"pdf", "print"}:
        flash("Choose a valid format.", "warning")
        return redirect(url_for("shop.shop_product_detail", slug=product.slug))

    if delivery_format == "pdf" and not product.has_pdf:
        flash("PDF download is not available for this item.", "warning")
        return redirect(url_for("shop.shop_product_detail", slug=product.slug))

    if delivery_format == "print" and not product.has_print:
        flash("Printed copy is not available for this item.", "warning")
        return redirect(url_for("shop.shop_product_detail", slug=product.slug))

    cart = _cart_items()
    for row in cart:
        if row.get("product_id") == product.id and row.get("delivery_format") == delivery_format:
            row["quantity"] = int(row.get("quantity", 1)) + quantity
            _save_cart(cart)
            flash("Added to cart.", "success")
            return redirect(url_for("shop.shop_cart"))

    cart.append({"product_id": product.id, "delivery_format": delivery_format, "quantity": quantity})
    _save_cart(cart)
    flash("Added to cart.", "success")
    return redirect(url_for("shop.shop_cart"))


@shop_bp.route("/shop/cart/remove", methods=["POST"])
def shop_cart_remove():
    product_id = request.form.get("product_id", type=int)
    delivery_format = (request.form.get("delivery_format") or "").strip().lower()

    cart = [
        row for row in _cart_items()
        if not (row.get("product_id") == product_id and row.get("delivery_format") == delivery_format)
    ]
    _save_cart(cart)
    flash("Item removed.", "success")
    return redirect(url_for("shop.shop_cart"))


@shop_bp.route("/shop/checkout/start", methods=["POST"])
def shop_checkout_start():
    payment_method = (request.form.get("payment_method") or "stripe").strip().lower()
    if payment_method == "paypal":
        return shop_checkout_paypal_create()
    return shop_checkout_create()


@shop_bp.route("/shop/checkout/create", methods=["POST"])
def shop_checkout_create():
    if stripe is None:
        flash("Payments are currently unavailable.", "danger")
        return redirect(url_for("shop.shop_cart"))

    api_key = _resolve_stripe_api_key()
    if not api_key:
        flash("Stripe is not configured yet.", "warning")
        return redirect(url_for("shop.shop_cart"))

    stripe.api_key = api_key

    customer_email = (request.form.get("customer_email") or "").strip().lower()
    if not customer_email:
        flash("Please provide your email address for receipt and downloads.", "warning")
        return redirect(url_for("shop.shop_cart"))

    order, items, error_msg = _create_pending_order_from_cart(customer_email)
    if error_msg:
        flash(error_msg, "warning")
        return redirect(url_for("shop.shop_cart"))

    line_items = []
    for row in items:
        product = row["product"]
        label = "PDF Download" if row["delivery_format"] == "pdf" else "Printed Copy"
        line_items.append({
            "price_data": {
                "currency": "gbp",
                "unit_amount": row["unit_price_cents"],
                "product_data": {"name": f"{product.title} ({label})"},
            },
            "quantity": row["quantity"],
        })

    checkout_args = {
        "mode": "payment",
        "line_items": line_items,
        "customer_email": customer_email,
        "success_url": url_for("shop.shop_checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": url_for("shop.shop_cart", _external=True),
        "metadata": {"order_id": str(order.id)},
    }
    if order.has_physical_items:
        checkout_args["shipping_address_collection"] = {"allowed_countries": ["GB", "US", "CA", "AU", "IE", "NZ"]}

    checkout_session = stripe.checkout.Session.create(**checkout_args)

    order.stripe_checkout_session_id = checkout_session.id
    db, _, _, _ = _models()
    db.session.commit()

    return redirect(checkout_session.url)


@shop_bp.route("/shop/checkout/paypal/create", methods=["POST"])
def shop_checkout_paypal_create():
    customer_email = (request.form.get("customer_email") or "").strip().lower()
    if not customer_email:
        flash("Please provide your email address for receipt and downloads.", "warning")
        return redirect(url_for("shop.shop_cart"))

    access_token = _paypal_access_token()
    if not access_token:
        flash("PayPal is not configured yet.", "warning")
        return redirect(url_for("shop.shop_cart"))

    order, items, error_msg = _create_pending_order_from_cart(customer_email)
    if error_msg:
        flash(error_msg, "warning")
        return redirect(url_for("shop.shop_cart"))

    total_value = f"{(order.total_cents or 0) / 100:.2f}"
    paypal_items = []
    for row in items:
        label = "PDF Download" if row["delivery_format"] == "pdf" else "Printed Copy"
        paypal_items.append({
            "name": f"{row['product'].title} ({label})"[:127],
            "quantity": str(row["quantity"]),
            "unit_amount": {"currency_code": "GBP", "value": f"{row['unit_price_cents'] / 100:.2f}"},
        })

    shipping_pref = "GET_FROM_FILE" if order.has_physical_items else "NO_SHIPPING"
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": str(order.id),
            "amount": {
                "currency_code": "GBP",
                "value": total_value,
                "breakdown": {"item_total": {"currency_code": "GBP", "value": total_value}},
            },
            "items": paypal_items,
        }],
        "application_context": {
            "brand_name": current_app.config.get("SITE_TITLE", "Jack Capstaff"),
            "user_action": "PAY_NOW",
            "shipping_preference": shipping_pref,
            "return_url": url_for("shop.shop_checkout_paypal_return", _external=True),
            "cancel_url": url_for("shop.shop_cart", _external=True),
        },
    }

    resp = requests.post(
        f"{_resolve_paypal_base_url()}/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code >= 400:
        current_app.logger.error("PayPal create order failed: %s", resp.text[:500])
        flash("PayPal checkout could not start. Please try again.", "danger")
        return redirect(url_for("shop.shop_cart"))

    order_data = resp.json()
    paypal_order_id = order_data.get("id")
    approval_url = None
    for link in order_data.get("links", []):
        if link.get("rel") == "approve":
            approval_url = link.get("href")
            break

    if not paypal_order_id or not approval_url:
        flash("PayPal checkout could not start. Please try again.", "danger")
        return redirect(url_for("shop.shop_cart"))

    db, _, ShopOrder, _ = _models()
    existing = ShopOrder.query.filter_by(stripe_checkout_session_id=paypal_order_id).first()
    if existing and existing.id != order.id:
        paypal_order_id = f"{paypal_order_id}-{order.id}"
    order.stripe_checkout_session_id = paypal_order_id
    db.session.commit()

    return redirect(approval_url)


@shop_bp.route("/shop/checkout/paypal/return")
def shop_checkout_paypal_return():
    paypal_order_id = (request.args.get("token") or "").strip()
    if not paypal_order_id:
        flash("PayPal checkout did not complete.", "warning")
        return redirect(url_for("shop.shop_cart"))

    access_token = _paypal_access_token()
    if not access_token:
        flash("PayPal is not configured yet.", "warning")
        return redirect(url_for("shop.shop_cart"))

    db, _, ShopOrder, _ = _models()
    order = ShopOrder.query.filter_by(stripe_checkout_session_id=paypal_order_id).first()
    if not order:
        flash("Order could not be found.", "danger")
        return redirect(url_for("shop.shop_cart"))

    capture_resp = requests.post(
        f"{_resolve_paypal_base_url()}/v2/checkout/orders/{paypal_order_id}/capture",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )

    if capture_resp.status_code >= 400:
        current_app.logger.error("PayPal capture failed: %s", capture_resp.text[:500])
        flash("Payment capture failed. Please contact support.", "danger")
        return redirect(url_for("shop.shop_cart"))

    capture_data = capture_resp.json()
    if capture_data.get("status") != "COMPLETED":
        flash("Payment has not completed yet.", "warning")
        return redirect(url_for("shop.shop_cart"))

    purchase_units = capture_data.get("purchase_units", [])
    shipping = (purchase_units[0].get("shipping") if purchase_units else {}) or {}
    shipping_addr = shipping.get("address") or {}
    captures = (((purchase_units[0].get("payments") or {}).get("captures") or [{}])[0]) if purchase_units else {}

    order.status = "paid"
    order.stripe_payment_intent_id = captures.get("id") or order.stripe_payment_intent_id
    amount_val = captures.get("amount", {}).get("value")
    if amount_val:
        order.total_cents = _parse_price_to_cents(amount_val)
    order.currency = (captures.get("amount", {}).get("currency_code") or order.currency or "GBP").lower()
    order.paid_at = datetime.utcnow()
    payer = capture_data.get("payer") or {}
    order.customer_name = (payer.get("name", {}).get("given_name", "") + " " + payer.get("name", {}).get("surname", "")).strip() or order.customer_name
    order.customer_email = payer.get("email_address") or order.customer_email

    if shipping:
        order.shipping_name = shipping.get("name", {}).get("full_name") or order.shipping_name
        order.shipping_line1 = shipping_addr.get("address_line_1")
        order.shipping_line2 = shipping_addr.get("address_line_2")
        order.shipping_city = shipping_addr.get("admin_area_2")
        order.shipping_state = shipping_addr.get("admin_area_1")
        order.shipping_postal_code = shipping_addr.get("postal_code")
        order.shipping_country = shipping_addr.get("country_code")

    db.session.commit()

    if not order.customer_email_sent or not order.admin_email_sent:
        _send_order_emails(order)

    _save_cart([])
    return redirect(url_for("shop.shop_checkout_success", session_id=order.stripe_checkout_session_id))


@shop_bp.route("/shop/checkout/success")
def shop_checkout_success():
    session_id = (request.args.get("session_id") or "").strip()
    _, _, ShopOrder, _ = _models()
    order = None
    if session_id:
        order = ShopOrder.query.filter_by(stripe_checkout_session_id=session_id).first()
        if order and order.status == "paid":
            _save_cart([])
    return render_template("shop/success.html", order=order)


@shop_bp.route("/shop/stripe/webhook", methods=["POST"])
def shop_stripe_webhook():
    if stripe is None:
        return jsonify({"error": "stripe not installed"}), 500

    api_key = _resolve_stripe_api_key()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not api_key or not webhook_secret:
        return jsonify({"error": "stripe webhook not configured"}), 500

    stripe.api_key = api_key

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, SignatureVerificationError):
        return jsonify({"error": "invalid payload"}), 400
    except Exception:
        return jsonify({"error": "invalid webhook"}), 400

    if event.get("type") == "checkout.session.completed":
        session_obj = event["data"]["object"]
        order_id = int(session_obj.get("metadata", {}).get("order_id", 0) or 0)

        db, _, ShopOrder, ShopOrderItem = _models()
        order = ShopOrder.query.get(order_id)
        if order:
            order.status = "paid"
            order.stripe_payment_intent_id = session_obj.get("payment_intent")
            order.total_cents = int(session_obj.get("amount_total") or order.total_cents or 0)
            order.currency = (session_obj.get("currency") or order.currency or "gbp").lower()
            order.paid_at = datetime.utcnow()

            customer_details = session_obj.get("customer_details") or {}
            order.customer_name = customer_details.get("name") or order.customer_name
            order.customer_email = customer_details.get("email") or order.customer_email

            shipping = session_obj.get("shipping_details") or {}
            shipping_addr = shipping.get("address") or {}
            if shipping:
                order.shipping_name = shipping.get("name")
                order.shipping_line1 = shipping_addr.get("line1")
                order.shipping_line2 = shipping_addr.get("line2")
                order.shipping_city = shipping_addr.get("city")
                order.shipping_state = shipping_addr.get("state")
                order.shipping_postal_code = shipping_addr.get("postal_code")
                order.shipping_country = shipping_addr.get("country")

            db.session.commit()

            if not order.customer_email_sent or not order.admin_email_sent:
                _send_order_emails(order)

    return jsonify({"received": True})


@shop_bp.route("/shop/download/<token>")
def download_order_item(token):
    db, _, ShopOrder, ShopOrderItem = _models()

    try:
        data = _download_serializer().loads(token, max_age=60 * 60 * 24 * 7)
        order_item_id = int(data.get("order_item_id", 0))
    except (BadSignature, SignatureExpired, ValueError):
        flash("This download link is invalid or has expired.", "warning")
        return redirect(url_for("shop.shop_index"))

    item = ShopOrderItem.query.get_or_404(order_item_id)
    order = ShopOrder.query.get(item.order_id)
    if not order or order.status != "paid" or item.delivery_format != "pdf" or not item.pdf_file_url_snapshot:
        flash("This download is not available.", "danger")
        return redirect(url_for("shop.shop_index"))

    return redirect(item.pdf_file_url_snapshot)


@shop_bp.route("/admin/shop")
@login_required
def admin_products_list():
    if not _editor_required():
        abort(403)

    _, Product, _, _ = _models()
    page = request.args.get("page", 1, type=int)
    products = Product.query.order_by(Product.sort_order.asc(), Product.created_at.desc()).paginate(page=page, per_page=30)
    return render_template("admin/shop/products_list.html", products=products)


@shop_bp.route("/admin/shop/create", methods=["GET", "POST"])
@login_required
def admin_products_create():
    if not _editor_required():
        abort(403)

    db, Product, _, _ = _models()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Title is required.", "warning")
            return render_template("admin/shop/product_form.html", action="Create")

        slug = _slugify((request.form.get("slug") or "").strip() or title)
        if Product.query.filter_by(slug=slug).first():
            slug = f"{slug}-{secrets.randbelow(10000)}"

        pdf_file_url = (request.form.get("pdf_file_url") or "").strip() or None
        pdf_file = request.files.get("pdf_file")
        uploaded_pdf = _upload_pdf(pdf_file)
        if uploaded_pdf:
            pdf_file_url = uploaded_pdf

        product = Product(
            title=title,
            slug=slug,
            subtitle=(request.form.get("subtitle") or "").strip() or None,
            description=(request.form.get("description") or "").strip() or None,
            cover_image_url=(request.form.get("cover_image_url") or "").strip() or None,
            pdf_file_url=pdf_file_url,
            has_pdf=request.form.get("has_pdf") == "on",
            has_print=request.form.get("has_print") == "on",
            price_pdf_cents=max(0, _parse_price_to_cents(request.form.get("price_pdf") or "0")),
            price_print_cents=max(0, _parse_price_to_cents(request.form.get("price_print") or "0")),
            published=request.form.get("published") == "on",
            sort_order=request.form.get("sort_order", type=int) or 0,
            author_id=current_user.id,
        )
        db.session.add(product)
        db.session.commit()
        flash("Product created.", "success")
        return redirect(url_for("shop.admin_products_list"))

    return render_template("admin/shop/product_form.html", action="Create")


@shop_bp.route("/admin/shop/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def admin_products_edit(product_id):
    if not _editor_required():
        abort(403)

    db, Product, _, _ = _models()
    product = Product.query.get_or_404(product_id)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Title is required.", "warning")
            return render_template("admin/shop/product_form.html", action="Edit", product=product)

        product.title = title
        product.slug = _slugify((request.form.get("slug") or "").strip() or title)
        product.subtitle = (request.form.get("subtitle") or "").strip() or None
        product.description = (request.form.get("description") or "").strip() or None
        product.cover_image_url = (request.form.get("cover_image_url") or "").strip() or None

        pdf_file_url = (request.form.get("pdf_file_url") or "").strip() or product.pdf_file_url
        pdf_file = request.files.get("pdf_file")
        uploaded_pdf = _upload_pdf(pdf_file)
        if uploaded_pdf:
            pdf_file_url = uploaded_pdf
        product.pdf_file_url = pdf_file_url

        product.has_pdf = request.form.get("has_pdf") == "on"
        product.has_print = request.form.get("has_print") == "on"
        product.price_pdf_cents = max(0, _parse_price_to_cents(request.form.get("price_pdf") or "0"))
        product.price_print_cents = max(0, _parse_price_to_cents(request.form.get("price_print") or "0"))
        product.published = request.form.get("published") == "on"
        product.sort_order = request.form.get("sort_order", type=int) or 0

        db.session.commit()
        flash("Product updated.", "success")
        return redirect(url_for("shop.admin_products_list"))

    return render_template("admin/shop/product_form.html", action="Edit", product=product)


@shop_bp.route("/admin/shop/<int:product_id>/delete", methods=["POST"])
@login_required
def admin_products_delete(product_id):
    if not _editor_required():
        abort(403)

    db, Product, _, _ = _models()
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted.", "success")
    return redirect(url_for("shop.admin_products_list"))


@shop_bp.route("/admin/orders")
@login_required
def admin_orders_list():
    if not _editor_required():
        abort(403)

    _, _, ShopOrder, _ = _models()
    page = request.args.get("page", 1, type=int)
    orders = ShopOrder.query.order_by(ShopOrder.created_at.desc()).paginate(page=page, per_page=40)
    return render_template("admin/shop/orders_list.html", orders=orders)


@shop_bp.route("/admin/orders/<int:order_id>")
@login_required
def admin_orders_view(order_id):
    if not _editor_required():
        abort(403)

    _, _, ShopOrder, ShopOrderItem = _models()
    order = ShopOrder.query.get_or_404(order_id)
    items = ShopOrderItem.query.filter_by(order_id=order.id).all()
    return render_template("admin/shop/order_view.html", order=order, items=items)
