"""
خادم منصة التوظيف العربية - الإصدار 10
IP: 159.146.28.245, Port: 61411
جميع البيانات مشفرة بـ AES-256
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from flask import Flask, request, jsonify, session, render_template, redirect, render_template_string, g, make_response
from flask_cors import CORS
from i18n import translate as i18n_translate, I18N_DICT as I18N_DICTIONARY, get_direction
from encryption import secure_storage, PasswordManager, BASE_DIR
from payment_rules import calculate_unlock_price, PAYMENT_RULES_VERSION, SUPPORTED_CURRENCIES, get_price_table
from payment_gateway import create_payment, verify_payment, refund_payment, get_payment_status, PAYMENT_GATEWAY_VERSION
from providers.mock_provider import MOCK_WEBHOOK_SECRET
from payment_config import ALLOW_TEST_METHODS, is_production, is_live_mode, get_provider_info
from wallet_service import subtract_balance, add_balance, create_wallet, get_transactions
from error_log import log_error, get_error_log, clear_error_log, is_logging_enabled, set_logging_enabled
from wallet_config import JOB_POSTING_FEE, JOB_POSTING_IDEMPOTENCY_WINDOW, CONTACT_UNLOCK_FEE, SADAQAH_FREE_UNLOCKS
from payment_pricing import load_settings as load_pricing_settings, save_settings as save_pricing_settings, service_prices, usd_cents, user_currency, format_local, CURRENCY_NAMES, CURRENCY_SYMBOLS, COUNTRY_CURRENCIES, refresh_live_rates
import secrets
APPLICATION_SADAQAH_FREE_LIMIT = 3
JOB_POSTING_SADAQAH_FREE_LIMIT = 3
import re
import base64
import traceback
from datetime import datetime, timedelta
import logging
import json
import os
from professions import PROFESSIONS, PROFESSION_GROUPS
from demo_repair import run_repair as run_demo_repair
import shutil
from functools import wraps

from urllib.request import urlopen, Request

from urllib.parse import quote
from werkzeug.utils import secure_filename

# ============================================
# إعدادات التسجيل
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# البريد الإلكتروني: الإعدادات من متغيرات البيئة فقط، بدون تخزين كلمة مرور التطبيق داخل الكود.
# Mail settings and helpers are defined later; import BASE_DIR above for file paths


# ============================================
# تهيئة التطبيق
# ============================================


# Email defaults (prefer environment variables; saved settings may override)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER or "")


def get_mail_settings():
    """Return mail settings, with production environment variables taking precedence.

    Mailgun settings must not be shadowed by the legacy encrypted SMTP settings.
    The encrypted settings are retained only as a local/development fallback.
    """
    try:
        saved = secure_storage.encryption.decrypt_file("mail_settings") or {}
    except Exception:
        saved = {}

    # Environment variables are authoritative for Mailgun in production.
    mailgun_api_key = os.environ.get("MAILGUN_API_KEY", "").strip()
    mailgun_domain = os.environ.get("MAILGUN_DOMAIN", "").strip()
    mailgun_base_url = os.environ.get("MAILGUN_BASE_URL", "https://api.mailgun.net").strip().rstrip("/")
    env_mail_from = os.environ.get("MAIL_FROM", "").strip()

    return {
        "smtp_host": os.environ.get("SMTP_HOST", saved.get("smtp_host", SMTP_HOST)),
        "smtp_port": int(os.environ.get("SMTP_PORT", saved.get("smtp_port", SMTP_PORT))),
        "smtp_user": os.environ.get("SMTP_USER", saved.get("smtp_user", SMTP_USER)),
        "smtp_password": os.environ.get("SMTP_PASSWORD", saved.get("smtp_password", SMTP_PASSWORD)),
        "mail_from": env_mail_from or saved.get("mail_from", MAIL_FROM),
        "mailgun_api_key": mailgun_api_key,
        "mailgun_domain": mailgun_domain,
        "mailgun_base_url": mailgun_base_url,
    }


def send_email(to_email, subject, body, html=None):
    """Send email through Mailgun HTTPS API when configured, with SMTP fallback for local use.

    Render production should use Mailgun because outbound SMTP connectivity is not
    available in the current deployment environment.
    """
    if not to_email:
        return False

    cfg = get_mail_settings()
    mailgun_api_key = cfg.get("mailgun_api_key")
    mailgun_domain = cfg.get("mailgun_domain")

    # Production path: Mailgun HTTPS API (no SMTP socket is opened).
    if mailgun_api_key and mailgun_domain:
        try:
            import requests

            endpoint = (
                f"{cfg.get('mailgun_base_url', 'https://api.mailgun.net').rstrip('/')}/"
                f"v3/{mailgun_domain}/messages"
            )
            from_address = cfg.get("mail_from") or f"postmaster@{mailgun_domain}"
            data = {
                "from": from_address,
                "to": to_email,
                "subject": subject,
                "text": body,
            }
            if html:
                data["html"] = html

            response = requests.post(
                endpoint,
                auth=("api", mailgun_api_key),
                data=data,
                timeout=20,
            )

            if 200 <= response.status_code < 300:
                logger.info("Email sent successfully via Mailgun to %s", to_email)
                return True

            # Do not log the API key or the full response if it could contain sensitive data.
            logger.error(
                "Mailgun email sending failed: HTTP %s - %s",
                response.status_code,
                response.text[:500],
            )
            return False
        except Exception:
            logger.exception("Mailgun email sending failed")
            return False

    # Local/development fallback only when Mailgun is not configured.
    try:
        import smtplib
        from email.message import EmailMessage

        if not cfg.get("smtp_password"):
            logger.warning("No Mailgun configuration and SMTP password not configured; skipping send")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("mail_from") or cfg.get("smtp_user")
        msg["To"] = to_email
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20) as s:
            s.starttls()
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.send_message(msg)
        return True
    except Exception:
        logger.exception("SMTP email sending failed")
        return False

app = Flask(__name__)
# مفتاح الجلسة من Environment Variable في الإنتاج.
# في حالة عدم توفر المفتاح (نسخة التطوير مثلاً) يولّد النظام مفتاحاً
# عشوائياً لهذا التشغيل فقط ولا يحفظه. يُنصح بشدة بضبطه في بيئة الإنتاج.
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    app.secret_key = secrets.token_hex(32)
    logger.warning(
        "⚠️ FLASK_SECRET_KEY غير مضبوط — تم توليد مفتاح جلسة عشوائي مؤقت. "
        "اضبط FLASK_SECRET_KEY في Environment Variables لبيئة إنتاج مستقرة."
    )
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_HTTPS', '0') == '1'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=86400)


# ============================================
# 🌍 نظام اللغة العالمي — IP + اختيار المستخدم
# ============================================
SUPPORTED_SITE_LANGUAGES = {
    'ar': {'name': 'العربية', 'dir': 'rtl', 'countries': {'SA','AE','QA','KW','BH','OM','JO','EG','MA','DZ','TN','LY','IQ','PS','LB','YE','SD','SY','MR','DJ','KM','SO'}},
    'en': {'name': 'English', 'dir': 'ltr', 'countries': {'US','GB','CA','AU','NZ','IE','IN','PH','SG','ZA'}},
    'tr': {'name': 'Türkçe', 'dir': 'ltr', 'countries': {'TR','CY'}},
    'fr': {'name': 'Français', 'dir': 'ltr', 'countries': {'FR','BE','CH','LU','MC'}},
    'de': {'name': 'Deutsch', 'dir': 'ltr', 'countries': {'DE','AT','CH'}},
    'es': {'name': 'Español', 'dir': 'ltr', 'countries': {'ES','MX','AR','CL','CO','PE','UY','EC'}},
}
DEFAULT_SITE_LANGUAGE = 'ar'
_IP_LANGUAGE_CACHE = {}
_IP_CACHE_TTL = 6 * 60 * 60


def _client_ip():
    # لا نثق بـ X-Forwarded-For إلا إذا كان التطبيق خلف proxy موثوق.
    # يمكن تفعيل ذلك لاحقاً عبر إعداد PROXY_TRUSTED=1.
    if os.environ.get('PROXY_TRUSTED') == '1':
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.remote_addr or ''
