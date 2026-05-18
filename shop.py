import os
import re
import secrets
import smtplib
import json
import csv
import base64
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from email.message import EmailMessage
from io import BytesIO, StringIO

import requests
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for, abort, make_response, send_file
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as reportlab_canvas
from reportlab.lib.utils import ImageReader

from PIL import Image, ImageDraw, ImageFont

try:
    from pypdf import PdfReader, PdfWriter
except Exception:
    PdfReader = None
    PdfWriter = None

try:
    import pypdfium2 as pdfium
except Exception:
    pdfium = None

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


def _clean_optional_text(value):
    text = (value or "").strip()
    if not text:
        return None
    if text.lower() in {"none", "null", "n/a", "na"}:
        return None
    return text


def _upload_cover_image(file_storage):
    if not file_storage or not getattr(file_storage, "filename", None):
        return None

    filename = (file_storage.filename or "").lower()
    if not any(filename.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        return None

    if cloudinary is not None and os.environ.get("CLOUDINARY_URL", "").strip():
        try:
            result = cloudinary.uploader.upload(
                file_storage,
                folder="jackcapstaff/shop/covers",
                resource_type="image",
                use_filename=True,
                unique_filename=True,
            )
            return result.get("secure_url")
        except Exception:
            current_app.logger.exception("Cloudinary image upload failed; fallback to local storage")

    uploads_dir = os.path.join(current_app.root_path, "assets", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", file_storage.filename)
    out_name = f"cover_{secrets.token_hex(8)}_{safe_name}"
    out_path = os.path.join(uploads_dir, out_name)
    file_storage.save(out_path)
    return f"/assets/uploads/{out_name}"


def _upload_cover_image_bytes(image_bytes: bytes, slug_hint: str):
    if not image_bytes:
        return None

    if cloudinary is not None and os.environ.get("CLOUDINARY_URL", "").strip():
        try:
            result = cloudinary.uploader.upload(
                BytesIO(image_bytes),
                folder="jackcapstaff/shop/covers",
                public_id=f"{slug_hint}-cover-{secrets.token_hex(4)}",
                resource_type="image",
                format="jpg",
                overwrite=False,
            )
            return result.get("secure_url")
        except Exception:
            current_app.logger.exception("Cloudinary generated cover upload failed; fallback to local storage")

    uploads_dir = os.path.join(current_app.root_path, "assets", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    out_name = f"cover_{slug_hint}_{secrets.token_hex(6)}.jpg"
    out_path = os.path.join(uploads_dir, out_name)
    with open(out_path, "wb") as fh:
        fh.write(image_bytes)
    return f"/assets/uploads/{out_name}"


def _generate_cover_from_pdf(pdf_file_url: str, slug_hint: str):
    if not pdf_file_url:
        return None

    try:
        source_pdf = _read_pdf_bytes(pdf_file_url)
        cover_jpg = _render_pdf_page_to_jpeg(source_pdf, page_index=0, max_width=1200)
        return _upload_cover_image_bytes(cover_jpg, slug_hint=slug_hint)
    except Exception:
        current_app.logger.exception("Failed generating/uploading cover from PDF")
        return None


def _safe_pdf_name(value: str, suffix: str = "") -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "document")).strip("-")
    base = base or "document"
    if suffix:
        base = f"{base}-{suffix}"
    return f"{base}.pdf"


def _resolve_local_upload_path(file_url: str):
    if not file_url or not file_url.startswith("/assets/uploads/"):
        return None
    rel = file_url.lstrip("/").replace("/", os.sep)
    candidate = os.path.abspath(os.path.join(current_app.root_path, rel))
    uploads_root = os.path.abspath(os.path.join(current_app.root_path, "assets", "uploads"))
    if not candidate.startswith(uploads_root + os.sep) and candidate != uploads_root:
        return None
    if not os.path.exists(candidate):
        return None
    return candidate


def _read_pdf_bytes(file_url: str):
    local_path = _resolve_local_upload_path(file_url)
    if local_path:
        with open(local_path, "rb") as fh:
            return fh.read()

    if isinstance(file_url, str) and file_url.lower().startswith(("http://", "https://")):
        resp = requests.get(file_url, timeout=30)
        resp.raise_for_status()
        return resp.content

    raise ValueError("Unsupported PDF source URL")


def _apply_preview_watermark(image: Image.Image) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    width, height = base.size

    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(30, int(width * 0.055)))
        footer_font = ImageFont.truetype("DejaVuSans.ttf", max(13, int(width * 0.013)))
    except Exception:
        title_font = ImageFont.load_default()
        footer_font = ImageFont.load_default()

    # Draw a subtle diagonal stamp layer and rotate it into place.
    stamp = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    stamp_draw = ImageDraw.Draw(stamp)
    stamp_draw.text((int(width * 0.20), int(height * 0.45)), "PERUSAL COPY", font=title_font, fill=(90, 90, 90, 88))
    stamp_draw.text((int(width * 0.18), int(height * 0.62)), "PERUSAL COPY", font=title_font, fill=(90, 90, 90, 74))
    stamp = stamp.rotate(-26, expand=False, resample=Image.BICUBIC)
    overlay = Image.alpha_composite(overlay, stamp)

    draw = ImageDraw.Draw(overlay)
    draw.rectangle([(0, height - 34), (width, height)], fill=(255, 255, 255, 140))
    draw.text(
        (width // 2, height - 17),
        "PERUSAL COPY • Preview only • Not licensed for performance",
        font=footer_font,
        anchor="mm",
        fill=(70, 70, 70, 220),
    )

    return Image.alpha_composite(base, overlay).convert("RGB")


def _render_pdf_page_to_jpeg(pdf_bytes: bytes, page_index: int, max_width: int = 1200):
    if pdfium is None:
        raise RuntimeError("PDF rendering requires pypdfium2")

    document = pdfium.PdfDocument(pdf_bytes)
    if len(document) == 0 or page_index < 0 or page_index >= len(document):
        raise ValueError("PDF page out of range")

    rendered = document[page_index].render(scale=2)
    image = rendered.to_pil().convert("RGB")
    if image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)

    output = BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def _build_perusal_preview_pdf(pdf_bytes: bytes, max_pages: int = 20):
    if pdfium is None:
        raise RuntimeError("PDF preview requires pypdfium2")

    document = pdfium.PdfDocument(pdf_bytes)
    total_pages = len(document)
    even_page_indexes = [idx for idx in range(total_pages) if (idx + 1) % 2 == 0]
    if not even_page_indexes and total_pages > 0:
        even_page_indexes = [0]

    selected_indexes = even_page_indexes[:max(1, max_pages)]
    output = BytesIO()
    preview_canvas = reportlab_canvas.Canvas(output)

    for idx in selected_indexes:
        rendered = document[idx].render(scale=2)
        image = rendered.to_pil().convert("RGB")
        image = _apply_preview_watermark(image)

        page_width = max(100, int(image.width / 2))
        page_height = max(100, int(image.height / 2))
        preview_canvas.setPageSize((page_width, page_height))
        preview_canvas.drawImage(ImageReader(image), 0, 0, width=page_width, height=page_height, preserveAspectRatio=False)
        preview_canvas.showPage()

    preview_canvas.save()
    return output.getvalue(), len(selected_indexes), total_pages


def _build_authorized_copy_pdf(pdf_bytes: bytes, authorized_for: str):
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError("PDF personalization requires pypdf")

    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()
    footer_text = f"This copy is authorised for use by {authorized_for}".strip()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        overlay_stream = BytesIO()
        overlay_canvas = reportlab_canvas.Canvas(overlay_stream, pagesize=(width, height))
        overlay_canvas.setFont("Helvetica", 7)
        overlay_canvas.setFillColorRGB(0.45, 0.45, 0.45)
        overlay_canvas.drawCentredString(width / 2, 8, footer_text)
        overlay_canvas.save()

        overlay_stream.seek(0)
        overlay_page = PdfReader(overlay_stream).pages[0]
        page.merge_page(overlay_page)
        writer.add_page(page)

    out_stream = BytesIO()
    writer.write(out_stream)
    return out_stream.getvalue()


def _serve_pdf_from_url(file_url: str, download_name: str, as_attachment: bool):
    local_path = _resolve_local_upload_path(file_url)
    if local_path:
        return send_file(local_path, mimetype="application/pdf", as_attachment=as_attachment, download_name=download_name)

    if isinstance(file_url, str) and file_url.lower().startswith(("http://", "https://")):
        pdf_bytes = _read_pdf_bytes(file_url)
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        disposition = "attachment" if as_attachment else "inline"
        response.headers["Content-Disposition"] = f'{disposition}; filename="{download_name}"'
        return response

    raise ValueError("Unsupported PDF source URL")


def _seller_invoice_details():
    lines = [
        os.environ.get("SHOP_SELLER_NAME", "Jack Capstaff").strip(),
        os.environ.get("SHOP_SELLER_LINE1", "").strip(),
        os.environ.get("SHOP_SELLER_LINE2", "").strip(),
        os.environ.get("SHOP_SELLER_CITY", "").strip(),
        os.environ.get("SHOP_SELLER_POSTCODE", "").strip(),
        os.environ.get("SHOP_SELLER_COUNTRY", "").strip(),
        os.environ.get("SHOP_SELLER_EMAIL", current_app.config.get("CONTACT_FROM_EMAIL", "")).strip(),
    ]
    return [line for line in lines if line]


def _build_invoice_pdf(order, items):
    buffer = BytesIO()
    pdf = reportlab_canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 54

    seller_lines = _seller_invoice_details()
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(44, y, "Invoice")
    y -= 26

    pdf.setFont("Helvetica", 10)
    pdf.drawString(44, y, f"Invoice number: INV-{order.order_number}")
    y -= 14
    pdf.drawString(44, y, f"Order number: {order.order_number}")
    y -= 14
    pdf.drawString(44, y, f"Issue date: {datetime.utcnow().strftime('%Y-%m-%d')}")
    y -= 20

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(44, y, "Seller")
    y -= 14
    pdf.setFont("Helvetica", 10)
    for line in seller_lines:
        pdf.drawString(44, y, line)
        y -= 12

    y -= 8
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(320, height - 114, "Bill to")
    pdf.setFont("Helvetica", 10)
    customer_lines = [
        order.customer_name or "",
        order.customer_email or "",
        order.shipping_line1 or "",
        order.shipping_line2 or "",
        " ".join([part for part in [order.shipping_city, order.shipping_postal_code] if part]),
        order.shipping_country or "",
    ]
    bill_y = height - 128
    for line in [ln for ln in customer_lines if ln]:
        pdf.drawString(320, bill_y, line)
        bill_y -= 12

    y = min(y, bill_y) - 18
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(44, y, "Description")
    pdf.drawString(330, y, "Qty")
    pdf.drawString(380, y, "Unit")
    pdf.drawString(470, y, "Total")
    y -= 12
    pdf.line(44, y, 548, y)
    y -= 14

    subtotal_cents = 0
    for item in items:
        if y < 100:
            pdf.showPage()
            y = height - 60
        subtotal_cents += int(item.line_total_cents or 0)
        label = f"{item.title_snapshot} ({(item.delivery_format or '').upper()})"
        pdf.setFont("Helvetica", 10)
        pdf.drawString(44, y, label[:52])
        pdf.drawRightString(360, y, str(item.quantity or 1))
        pdf.drawRightString(450, y, _format_money(item.unit_price_cents, order.currency))
        pdf.drawRightString(548, y, _format_money(item.line_total_cents, order.currency))
        y -= 14

    y -= 8
    pdf.line(44, y, 548, y)
    y -= 18
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(500, y, "Total")
    pdf.drawRightString(548, y, _format_money(subtotal_cents, order.currency))

    y -= 24
    pdf.setFont("Helvetica", 9)
    pdf.drawString(44, y, f"Payment status: {order.status}")
    y -= 12
    if order.paid_at:
        pdf.drawString(44, y, f"Paid at: {order.paid_at.strftime('%Y-%m-%d %H:%M UTC')}")

    pdf.save()
    buffer.seek(0)
    return buffer.read()


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


def _send_email(subject: str, to_email: str, text_body: str, html_body: str, attachments=None):
    api_key = current_app.config.get("BREVO_API_KEY", "").strip()
    from_email = current_app.config.get("BREVO_FROM_EMAIL", "").strip() or current_app.config.get("CONTACT_FROM_EMAIL", "").strip()
    from_name = current_app.config.get("BREVO_FROM_NAME", "").strip() or current_app.config.get("SITE_TITLE", "Jack Capstaff")

    if api_key and from_email:
        try:
            payload = {
                "sender": {"name": from_name, "email": from_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body,
            }
            if attachments:
                payload["attachment"] = [
                    {
                        "name": (att.get("filename") or "attachment"),
                        "content": base64.b64encode(att.get("data") or b"").decode("ascii"),
                    }
                    for att in attachments
                    if att.get("data")
                ]
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
        except Exception:
            current_app.logger.exception("Brevo email send failed; attempting SMTP fallback")

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
    if attachments:
        for att in attachments:
            data = att.get("data")
            if not data:
                continue
            msg.add_attachment(
                data,
                maintype="application",
                subtype="pdf",
                filename=att.get("filename") or "attachment.pdf",
            )

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
    download_limit = max(1, int(os.environ.get("SHOP_DOWNLOAD_MAX_USES", "3")))
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
    invoice_bytes = _build_invoice_pdf(order, items)
    invoice_attachment = [{"filename": _safe_pdf_name(order.order_number, "invoice"), "data": invoice_bytes}]

    raw_name = (order.customer_name or "").strip()
    first_name = raw_name.split()[0] if raw_name else "there"
    greeting_text = f"Dear {first_name},"

    signature_name = os.environ.get("SHOP_EMAIL_SIGNATURE_NAME", "Jack Capstaff").strip() or "Jack Capstaff"
    signature_role = os.environ.get("SHOP_EMAIL_SIGNATURE_ROLE", "Music Director").strip() or "Music Director"
    signature_phone = os.environ.get("SHOP_EMAIL_SIGNATURE_PHONE", "07805 165 842").strip() or "07805 165 842"
    signature_email = (
        os.environ.get("SHOP_EMAIL_SIGNATURE_EMAIL", "").strip()
        or os.environ.get("SHOP_SELLER_EMAIL", "").strip()
        or "jack@jackcapstaff.com"
    )
    signature_website = os.environ.get("SHOP_EMAIL_SIGNATURE_WEBSITE", "www.jackcapstaff.com").strip() or "www.jackcapstaff.com"
    signature_image_url = os.environ.get("SHOP_EMAIL_SIGNATURE_IMAGE_URL", "").strip()

    signoff_text = (
        "Kind regards,\n\n"
        f"{signature_name}\n"
        f"{signature_role}\n"
        f"M {signature_phone}\n"
        f"E {signature_email}\n"
        f"W {signature_website}"
    )

    signoff_html = (
        "<p>Kind regards,</p>"
        f"<p><strong>{signature_name}</strong><br>{signature_role}<br>"
        f"M {signature_phone}<br>"
        f"E <a href=\"mailto:{signature_email}\">{signature_email}</a><br>"
        f"W <a href=\"https://{signature_website.replace('https://', '').replace('http://', '')}\">{signature_website}</a></p>"
    )
    if signature_image_url:
        signoff_html += (
            f"<p><img src=\"{signature_image_url}\" alt=\"{signature_name} signature\" "
            "style=\"max-width:260px;height:auto;display:block;\"></p>"
        )

    subject_customer = f"Your order receipt ({order.order_number})"
    text_customer = (
        f"{greeting_text}\n\n"
        "Thank you for your order.\n\n"
        f"Order: {order.order_number}\n"
        f"Total: {_format_money(order.total_cents, order.currency)}\n\nItems:\n{items_text}\n\n"
        f"Digital downloads:\n{downloads_text}\n\n"
        f"Security note: download links expire and each PDF can be downloaded up to {download_limit} times.\n\n"
        f"{signoff_text}\n"
    )
    html_customer = (
        f"<p>{greeting_text}</p>"
        f"<p>Thank you for your order.</p><p><strong>Order:</strong> {order.order_number}<br>"
        f"<strong>Total:</strong> {_format_money(order.total_cents, order.currency)}</p>"
        f"<p><strong>Items</strong><br>{items_text.replace(chr(10), '<br>')}</p>"
        f"<p><strong>Digital downloads</strong><br>{downloads_text.replace(chr(10), '<br>')}</p>"
        f"<p><em>Security note: download links expire and each PDF can be downloaded up to {download_limit} times.</em></p>"
        f"{signoff_html}"
    )
    customer_ok = _send_email(subject_customer, order.customer_email, text_customer, html_customer, attachments=invoice_attachment)

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
    admin_ok = _send_email(subject_admin, admin_to, text_admin, html_admin, attachments=invoice_attachment)

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
    default_download_limit = max(1, int(os.environ.get("SHOP_DOWNLOAD_MAX_USES", "3")))

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
            download_access_limit=default_download_limit if row["delivery_format"] == "pdf" else 0,
            download_access_count=0,
        ))

    db.session.commit()
    return order, items, None


@shop_bp.app_context_processor
def inject_shop_helpers():
    cart_count = sum(item.get("quantity", 1) for item in _cart_items())
    return {"format_money": _format_money, "cart_item_count": cart_count}


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


@shop_bp.route("/shop/<slug>/cover.jpg")
def shop_product_cover_image(slug):
    _, Product, _, _ = _models()
    product = Product.query.filter_by(slug=slug, published=True).first_or_404()
    if not product.has_pdf or not product.pdf_file_url:
        abort(404)

    try:
        source_pdf = _read_pdf_bytes(product.pdf_file_url)
        cover_bytes = _render_pdf_page_to_jpeg(source_pdf, page_index=0, max_width=1200)
        response = make_response(cover_bytes)
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response
    except Exception:
        current_app.logger.exception("Failed generating product cover image from PDF")
        abort(404)


@shop_bp.route("/shop/<slug>/preview.pdf")
def shop_product_preview(slug):
    _, Product, _, _ = _models()
    product = Product.query.filter_by(slug=slug, published=True).first_or_404()
    if not product.has_pdf or not product.pdf_file_url:
        flash("Preview is not available for this item.", "warning")
        return redirect(url_for("shop.shop_product_detail", slug=slug))

    try:
        source_pdf = _read_pdf_bytes(product.pdf_file_url)
        preview_count = int(os.environ.get("SHOP_PREVIEW_MAX_PAGES", "20"))
        preview_bytes, shown_pages, total_pages = _build_perusal_preview_pdf(source_pdf, max_pages=preview_count)
        response = make_response(preview_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'inline; filename="{_safe_pdf_name(product.title, "preview")}"'
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["X-Preview-Pages"] = str(shown_pages)
        response.headers["X-Source-Pages"] = str(total_pages)
        return response
    except Exception:
        current_app.logger.exception("Failed generating product preview PDF")
        flash("Preview is temporarily unavailable.", "warning")
        return redirect(url_for("shop.shop_product_detail", slug=slug))


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

    try:
        checkout_session = stripe.checkout.Session.create(**checkout_args)
    except stripe.error.InvalidRequestError as e:
        msg = str(e)
        if "minimum" in msg.lower() or "at least" in msg.lower():
            flash("Sorry, the order total is below the minimum allowed for card payments (£0.30). Please adjust your cart.", "danger")
        else:
            flash("There was a problem starting checkout. Please try again or contact us.", "danger")
        return redirect(url_for("shop.shop_cart"))
    except stripe.error.StripeError:
        flash("Payment service unavailable. Please try again shortly or contact us.", "danger")
        return redirect(url_for("shop.shop_cart"))

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
    payment_confirmed = False
    if session_id:
        order = ShopOrder.query.filter_by(stripe_checkout_session_id=session_id).first()
        if order and order.status == "paid":
            payment_confirmed = True
            _save_cart([])
    return render_template("shop/success.html", order=order, payment_confirmed=payment_confirmed)


@shop_bp.route("/shop/stripe/webhook", methods=["POST"])
@shop_bp.route("/shop/checkout/stripe/webhook", methods=["POST"])
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

    # Stripe SDK returns StripeObject instances; parse verified JSON for stable dict access.
    try:
        event_data = json.loads(payload)
    except ValueError:
        return jsonify({"error": "invalid payload"}), 400

    if event_data.get("type") == "checkout.session.completed":
        session_obj = ((event_data.get("data") or {}).get("object") or {})
        try:
            order_id = int(session_obj.get("metadata", {}).get("order_id", 0) or 0)
        except (TypeError, ValueError):
            order_id = 0

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
                try:
                    _send_order_emails(order)
                except Exception:
                    current_app.logger.exception("Order email processing failed in Stripe webhook")

    return jsonify({"received": True})


@shop_bp.route("/shop/download/<token>")
def download_order_item(token):
    db, _, ShopOrder, ShopOrderItem = _models()
    link_ttl_hours = max(1, int(os.environ.get("SHOP_DOWNLOAD_LINK_TTL_HOURS", "72")))

    try:
        data = _download_serializer().loads(token, max_age=60 * 60 * link_ttl_hours)
        order_item_id = int(data.get("order_item_id", 0))
    except (BadSignature, SignatureExpired, ValueError):
        flash("This download link is invalid or has expired.", "warning")
        return redirect(url_for("shop.shop_index"))

    item = ShopOrderItem.query.get_or_404(order_item_id)
    order = ShopOrder.query.get(item.order_id)
    if not order or order.status != "paid" or item.delivery_format != "pdf" or not item.pdf_file_url_snapshot:
        flash("This download is not available.", "danger")
        return redirect(url_for("shop.shop_index"))

    configured_default_limit = max(1, int(os.environ.get("SHOP_DOWNLOAD_MAX_USES", "3")))
    effective_limit = int(item.download_access_limit or configured_default_limit)
    current_downloads = int(item.download_access_count or 0)
    if current_downloads >= effective_limit:
        flash("This secure download link has reached its access limit. Please contact support if you need it reissued.", "warning")
        return redirect(url_for("shop.shop_index"))

    download_name = _safe_pdf_name(item.title_snapshot)
    try:
        authorized_for = (order.customer_name or order.customer_email or "the purchaser").strip()
        source_pdf = _read_pdf_bytes(item.pdf_file_url_snapshot)
        personalized_pdf = _build_authorized_copy_pdf(source_pdf, authorized_for=authorized_for)

        response = make_response(personalized_pdf)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'

        item.download_access_count = current_downloads + 1
        now = datetime.utcnow()
        if not item.first_downloaded_at:
            item.first_downloaded_at = now
        item.last_downloaded_at = now
        db.session.commit()
        return response
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed serving order PDF download")
        flash("Download is temporarily unavailable.", "warning")
        return redirect(url_for("shop.shop_index"))


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

        pdf_file_url = _clean_optional_text(request.form.get("pdf_file_url"))
        pdf_file = request.files.get("pdf_file")
        uploaded_pdf = _upload_pdf(pdf_file)
        if uploaded_pdf:
            pdf_file_url = uploaded_pdf

        cover_image_url = _clean_optional_text(request.form.get("cover_image_url"))
        uploaded_cover = _upload_cover_image(request.files.get("cover_image_file"))
        if uploaded_cover:
            cover_image_url = uploaded_cover
        elif not cover_image_url and pdf_file_url:
            cover_image_url = _generate_cover_from_pdf(pdf_file_url, slug_hint=slug)

        product = Product(
            title=title,
            slug=slug,
            subtitle=_clean_optional_text(request.form.get("subtitle")),
            description=_clean_optional_text(request.form.get("description")),
            cover_image_url=cover_image_url,
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
        product.subtitle = _clean_optional_text(request.form.get("subtitle"))
        product.description = _clean_optional_text(request.form.get("description"))

        cover_image_url = _clean_optional_text(request.form.get("cover_image_url"))
        uploaded_cover = _upload_cover_image(request.files.get("cover_image_file"))
        if uploaded_cover:
            cover_image_url = uploaded_cover

        pdf_file_url = _clean_optional_text(request.form.get("pdf_file_url")) or product.pdf_file_url
        pdf_file = request.files.get("pdf_file")
        uploaded_pdf = _upload_pdf(pdf_file)
        if uploaded_pdf:
            pdf_file_url = uploaded_pdf
        product.pdf_file_url = pdf_file_url

        if not cover_image_url and product.pdf_file_url:
            cover_image_url = _generate_cover_from_pdf(product.pdf_file_url, slug_hint=product.slug)
        product.cover_image_url = cover_image_url

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


@shop_bp.route("/admin/shop/<int:product_id>/regen-cover", methods=["POST"])
@login_required
def admin_products_regen_cover(product_id):
    if not _editor_required():
        abort(403)

    db, Product, _, _ = _models()
    product = Product.query.get_or_404(product_id)
    if not product.pdf_file_url:
        flash("No PDF uploaded — cannot generate cover.", "warning")
        return redirect(url_for("shop.admin_products_edit", product_id=product_id))
    try:
        _generate_and_store_cover_from_pdf(product, db)
        flash("Cover image regenerated from PDF page 1.", "success")
    except Exception as e:
        flash(f"Cover generation failed: {e}", "danger")
    return redirect(url_for("shop.admin_products_edit", product_id=product_id))


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


@shop_bp.route("/admin/orders/export.csv")
@login_required
def admin_orders_export_csv():
    if not _editor_required():
        abort(403)

    _, _, ShopOrder, ShopOrderItem = _models()
    orders = ShopOrder.query.filter_by(status="paid").order_by(ShopOrder.paid_at.desc(), ShopOrder.created_at.desc()).all()

    csv_buffer = StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow([
        "order_number",
        "created_at_utc",
        "paid_at_utc",
        "customer_name",
        "customer_email",
        "currency",
        "order_total",
        "payment_reference",
        "has_physical_items",
        "shipping_country",
        "item_title",
        "item_format",
        "item_quantity",
        "item_unit_price",
        "item_line_total",
    ])

    for order in orders:
        items = ShopOrderItem.query.filter_by(order_id=order.id).all()
        if not items:
            writer.writerow([
                order.order_number,
                order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
                order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else "",
                order.customer_name or "",
                order.customer_email or "",
                (order.currency or "gbp").upper(),
                _format_money(order.total_cents, order.currency),
                order.stripe_payment_intent_id or order.stripe_checkout_session_id or "",
                "yes" if order.has_physical_items else "no",
                order.shipping_country or "",
                "",
                "",
                "",
                "",
                "",
            ])
            continue

        for item in items:
            writer.writerow([
                order.order_number,
                order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
                order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else "",
                order.customer_name or "",
                order.customer_email or "",
                (order.currency or "gbp").upper(),
                _format_money(order.total_cents, order.currency),
                order.stripe_payment_intent_id or order.stripe_checkout_session_id or "",
                "yes" if order.has_physical_items else "no",
                order.shipping_country or "",
                item.title_snapshot,
                item.delivery_format,
                item.quantity,
                _format_money(item.unit_price_cents, order.currency),
                _format_money(item.line_total_cents, order.currency),
            ])

    response = make_response(csv_buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="sales-log-{datetime.utcnow().strftime("%Y%m%d")}.csv"'
    return response


@shop_bp.route("/admin/orders/<int:order_id>")
@login_required
def admin_orders_view(order_id):
    if not _editor_required():
        abort(403)

    _, _, ShopOrder, ShopOrderItem = _models()
    order = ShopOrder.query.get_or_404(order_id)
    items = ShopOrderItem.query.filter_by(order_id=order.id).all()
    return render_template("admin/shop/order_view.html", order=order, items=items)
