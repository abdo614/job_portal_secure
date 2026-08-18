# TELEGRAM_BOT_V5_3_SECURITY_FIX
# TELEGRAM_BOT_V5_SECURITY_FIX
# TELEGRAM_BOT_V3_REGISTRATION_FLOW_FIX
# TELEGRAM_BOT_V2_LOGIN_FIX\n# -*- coding: utf-8 -*-
"""
Telegram Bot adapter for the existing ArabJobs Flask application.

- Uses the SAME encrypted users/jobs/applications/favorites storage as app.py.
- No Telegram token is stored in source code.
- Set TELEGRAM_BOT_TOKEN in the environment before running.
- Designed for polling so it can run without exposing a public Telegram webhook.
"""
import os
import time
import logging
import base64
import html
import secrets
import hashlib
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

# Import the existing application/storage layer. This deliberately reuses the
# project's encryption, password hashing and email verification mechanisms.
from wallet_service import get_wallet_balance, get_wallet_transactions, add_balance, subtract_balance
from payment_pricing import service_prices, usd_cents, user_currency
from error_log import log_error, is_logging_enabled

from app import (
    secure_storage,
    PasswordManager,
    LOCATION_DATA,
    ROLE_LABELS,
    PHONE_COUNTRY_CODES,
    create_email_verification,
    validate_email,
    validate_password,
    sanitize_input,
    normalize_role,
    send_email,
    _load_subscription_plans, _load_subscriptions, _save_subscriptions,
    _load_payment_logs, _save_payment_logs, _get_user_wallet_balance,
    _notification_queue, _save_notification_queue, _notification_preferences,
)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Create a Telegram bot with BotFather and set the token as an environment variable."
    )

API = f"https://api.telegram.org/bot{TOKEN}"
LOG = logging.getLogger("telegram_bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def telegram_audit(chat_id, message, entry_type="telegram_action", cause=""):
    """سجل عمليات وأخطاء Telegram في نفس سجل الإدارة مع المصدر telegram."""
    if not is_logging_enabled():
        return
    try:
        user = linked_user(chat_id)
        actor_id = str(user.get("id")) if user else ""
        actor_role = str(user.get("role", "")) if user else ""
        safe_cause = str(cause or "")[:1000]
        log_error(str(message)[:500], safe_cause, entry_type=entry_type,
                  actor_id=actor_id, actor_role=actor_role, source="telegram")
    except Exception:
        LOG.exception("Could not write Telegram audit entry")


def telegram_error(chat_id, message, exc=None, context=""):
    details = str(context or "")
    if exc:
        details = (details + "; " if details else "") + f"{type(exc).__name__}: {exc}"
    telegram_audit(chat_id, message, entry_type="telegram_error", cause=details)

CATEGORIES = ["تقنية", "هندسة", "طب", "تعليم", "مالية", "تسويق", "إدارة", "خدمة", "قانون", "فنون"]
EDUCATION = ["ثانوي", "معهد", "جامعة", "ماجستير", "دكتوراه", "بدون شهادة"]
WORK_TYPES = ["دوام كامل", "دوام جزئي", "عن بُعد", "عمل حر", "تدريب"]
COUNTRY_PAGE_SIZE = 10
JOB_PAGE_SIZE = 5
APPLICATION_SADAQAH_FREE_LIMIT = 3
JOB_POSTING_SADAQAH_FREE_LIMIT = 3
CONTACT_SADAQAH_FREE_LIMIT = 3

# Conversation state is persisted in the same encrypted storage so a message
# cannot accidentally lose the login/registration step. Passwords are never
# required to survive a restart; only the current flow/step and non-secret data
# are persisted.
STATE_FILE = "telegram_state"
LINK_FILE = "telegram_links"
STATE = {}

# V5 security: keep passwords only in process memory, never in encrypted telegram_state.
SECRET_STATE = {}

# V5 anti-spam/rate-limit: protects the encrypted file storage from rapid updates.
RATE_STATE = {}
RATE_WINDOW_SECONDS = 10
RATE_MAX_ACTIONS = 12
RATE_COOLDOWN_SECONDS = 2

# OTP state is encrypted at rest; OTP values themselves are stored as SHA-256 hashes.
PASSWORD_RESET_FILE = "telegram_password_resets"
PHONE_OTP_FILE = "telegram_phone_otps"
PIN_FILE = "telegram_pins"
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
PIN_LENGTH = 6
PIN_MAX_ATTEMPTS = 5
PIN_LOCK_MINUTES = 15

def load_states():
    try:
        data = secure_storage.encryption.decrypt_file(STATE_FILE) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_states(data):
    try:
        return secure_storage.encryption.encrypt_file(STATE_FILE, data)
    except Exception:
        LOG.exception("Could not save Telegram conversation state")
        return False


def tg(method, payload=None, timeout=35):
    r = requests.post(f"{API}/{method}", json=payload or {}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data.get("result")


def send(chat_id, text, keyboard=None, reply_keyboard=False, one_time_keyboard=False, remove_keyboard=False):
    payload = {"chat_id": chat_id, "text": text}
    if remove_keyboard:
        payload["reply_markup"] = {"remove_keyboard": True}
    elif keyboard:
        if reply_keyboard:
            payload["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": one_time_keyboard}
        else:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", payload)


def answer_callback(callback_id, text=None):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        tg("answerCallbackQuery", payload)
    except Exception:
        pass


def edit(chat_id, message_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("editMessageText", payload)


def btn(text, data):
    return {"text": text, "callback_data": data}


def rows(buttons, n=2):
    return [buttons[i:i+n] for i in range(0, len(buttons), n)]


def load_links():
    try:
        return secure_storage.encryption.decrypt_file(LINK_FILE) or {}
    except Exception:
        return {}


def save_links(data):
    return secure_storage.encryption.encrypt_file(LINK_FILE, data)


def linked_user(telegram_id):
    links = load_links()
    uid = links.get(str(telegram_id))
    if not uid:
        return None
    users = secure_storage.load_users() or []
    return next((u for u in users if str(u.get("id")) == str(uid)), None)


def link_user(telegram_id, user_id):
    links = load_links()
    links[str(telegram_id)] = str(user_id)
    return save_links(links)


def unlink_user(telegram_id):
    links = load_links()
    links.pop(str(telegram_id), None)
    save_links(links)


def set_state(chat_id, **kwargs):
    key = str(chat_id)
    STATE[key] = kwargs
    save_states(STATE)


def get_state(chat_id):
    key = str(chat_id)
    if key not in STATE:
        STATE.update(load_states())
    return STATE.get(key, {})


def clear_state(chat_id):
    key = str(chat_id)
    STATE.pop(key, None)
    SECRET_STATE.pop(key, None)
    save_states(STATE)


def rate_limit(chat_id):
    """Allow normal users to click/type quickly without hammering encrypted storage."""
    key = str(chat_id)
    now = time.monotonic()
    timestamps = [t for t in RATE_STATE.get(key, []) if now - t < RATE_WINDOW_SECONDS]
    if len(timestamps) >= RATE_MAX_ACTIONS:
        RATE_STATE[key] = timestamps
        return False, RATE_COOLDOWN_SECONDS
    timestamps.append(now)
    RATE_STATE[key] = timestamps
    return True, 0


def secret_for(chat_id):
    return SECRET_STATE.setdefault(str(chat_id), {})


def otp_hash(code):
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


def pin_hash(pin, salt=None):
    """Hash a Telegram quick PIN with a per-record salt. Never store the PIN itself."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), salt, 180000)
    return digest.hex(), base64.b64encode(salt).decode("ascii")


def pin_store():
    try:
        data = secure_storage.encryption.decrypt_file(PIN_FILE) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_pin_store(data):
    return secure_storage.encryption.encrypt_file(PIN_FILE, data)


def pin_record(chat_id):
    return pin_store().get(str(chat_id))


def pin_is_configured(chat_id):
    return bool(pin_record(chat_id))


def set_telegram_pin(chat_id, pin):
    if not re.fullmatch(r"\d{%d}" % PIN_LENGTH, str(pin or "")):
        return False
    digest, salt = pin_hash(pin)
    data = pin_store()
    data[str(chat_id)] = {
        "hash": digest,
        "salt": salt,
        "attempts": 0,
        "locked_until": "",
        "updated_at": datetime.now().isoformat(),
    }
    return save_pin_store(data)


def verify_telegram_pin(chat_id, pin):
    data = pin_store()
    item = data.get(str(chat_id))
    if not item:
        return False, "missing"
    locked_until = str(item.get("locked_until", ""))
    if locked_until:
        try:
            if datetime.now() < datetime.fromisoformat(locked_until):
                return False, "locked"
        except Exception:
            item["locked_until"] = ""
    try:
        salt = base64.b64decode(str(item.get("salt", "")))
        digest, _ = pin_hash(pin, salt)
    except Exception:
        return False, "invalid"
    if secrets.compare_digest(str(item.get("hash", "")), digest):
        item["attempts"] = 0
        item["locked_until"] = ""
        data[str(chat_id)] = item
        save_pin_store(data)
        return True, "ok"
    attempts = int(item.get("attempts", 0)) + 1
    item["attempts"] = attempts
    if attempts >= PIN_MAX_ATTEMPTS:
        item["attempts"] = 0
        item["locked_until"] = (datetime.now() + timedelta(minutes=PIN_LOCK_MINUTES)).isoformat()
    data[str(chat_id)] = item
    save_pin_store(data)
    return False, "wrong"


def delete_telegram_pin(chat_id):
    data = pin_store()
    data.pop(str(chat_id), None)
    return save_pin_store(data)


def start_pin_setup(chat_id, after_auth=True):
    set_state(chat_id, flow="pin_setup", step="pin", data={"after_auth": bool(after_auth)})
    send(chat_id, "🔢 أنشئ PIN سريعاً من 6 أرقام للدخول إلى Telegram لاحقاً.\n\nلا تستخدم رقمًا سهل التخمين مثل 123456.", [[btn("⬅️ إلغاء", "home")]])


def start_pin_login(chat_id):
    if not pin_is_configured(chat_id):
        send(chat_id, "🔢 لم يتم إعداد PIN لهذا الحساب بعد. سجّل الدخول بالطريقة العادية أولاً.", [[btn("🔐 تسجيل الدخول", "login")], [btn("📱 دخول بالهاتف", "phone_login")]])
        return
    set_state(chat_id, flow="pin_login", step="pin", data={})
    send(chat_id, "🔐 أدخل PIN المكوّن من 6 أرقام للدخول السريع:", [[btn("📱 دخول بالهاتف", "phone_login")], [btn("⬅️ رجوع", "home")]])


def finish_pin_login(chat_id, pin):
    user = linked_user(chat_id)
    if not user:
        # The Telegram ID is intentionally not enough to create a new account.
        send(chat_id, "🔐 يجب ربط هذا Telegram بحسابك أولاً عبر تسجيل الدخول المعتاد.", [[btn("📱 دخول بالهاتف", "phone_login")], [btn("📧 بالبريد وكلمة المرور", "login_email")]])
        clear_state(chat_id)
        return
    ok, reason = verify_telegram_pin(chat_id, pin)
    if ok:
        clear_state(chat_id)
        telegram_audit(chat_id, "تسجيل دخول سريع باستخدام PIN")
        send(chat_id, f"✅ تم تسجيل الدخول سريعاً.\nمرحباً {user.get('firstName','')} 👋", main_menu(user))
        return
    if reason == "locked":
        send(chat_id, "⛔ تم قفل PIN مؤقتاً بعد محاولات خاطئة كثيرة. استخدم تسجيل الدخول بالهاتف أو البريد.", [[btn("📱 دخول بالهاتف", "phone_login")], [btn("📧 بالبريد", "login_email")]])
    else:
        remaining = max(0, PIN_MAX_ATTEMPTS - int(pin_record(chat_id).get("attempts", 0))) if pin_record(chat_id) else 0
        send(chat_id, f"❌ PIN غير صحيح. المحاولات المتبقية: {remaining}.", [[btn("📱 دخول بالهاتف", "phone_login")]])



def normalize_phone(value):
    """Normalize phone numbers for safe comparison without exposing them in logs."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def find_user_by_phone(phone):
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    users = secure_storage.load_users() or []
    for user in users:
        stored = normalize_phone(user.get("phone", ""))
        if stored and stored == normalized:
            return user
    return None


def phone_belongs_to_other_user(phone, user_id=None):
    """Return the existing user if this normalized phone belongs to another account."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    users = secure_storage.load_users() or []
    for user in users:
        if user_id is not None and str(user.get("id")) == str(user_id):
            continue
        stored = normalize_phone(user.get("phone", ""))
        if stored and stored == normalized:
            return user
    return None


def save_otp_record(filename, key, code, extra=None):
    data = secure_storage.encryption.decrypt_file(filename) or {}
    record = {
        "code_hash": otp_hash(code),
        "expires": (datetime.now() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
        "attempts": 0,
    }
    if extra:
        record.update(extra)
    data[str(key)] = record
    return secure_storage.encryption.encrypt_file(filename, data)


def get_otp_record(filename, key):
    data = secure_storage.encryption.decrypt_file(filename) or {}
    return data, data.get(str(key))


def delete_otp_record(filename, key, data=None):
    data = data if data is not None else (secure_storage.encryption.decrypt_file(filename) or {})
    data.pop(str(key), None)
    return secure_storage.encryption.encrypt_file(filename, data)



def deliver_pending_notifications(chat_id):
    """إرسال الإشعارات المعلقة إلى Telegram للحساب المرتبط فقط."""
    user = linked_user(chat_id)
    if not user:
        return
    uid = str(user.get("id"))
    prefs = _notification_preferences().get(uid, {})
    if prefs and not prefs.get("telegram", True):
        return
    queue = _notification_queue()
    changed = False
    for item in queue:
        if str(item.get("userId")) != uid or item.get("sentTelegram") or not item.get("telegram", True):
            continue
        try:
            text = f"🔔 {item.get('title','إشعار')}\n\n{item.get('message','')}"
            send(chat_id, text)
            item["sentTelegram"] = True
            changed = True
        except Exception as exc:
            telegram_error(chat_id, "تعذر إرسال إشعار Telegram", exc, f"notification_id={item.get('id')}")
            break
    if changed:
        _save_notification_queue(queue)

def welcome(chat_id):
    user = linked_user(chat_id)
    if user:
        role = ROLE_LABELS.get(user.get("role"), "مستخدم")
        text = f"أهلاً {user.get('firstName','')} 👋\n\nحسابك مرتبط بالمنصة.\nالصفة: {role}\n\nاختر ما تريد:"
        send(chat_id, text, main_menu(user))
    else:
        send(
            chat_id,
            "أهلاً بك في منصة التوظيف العربية 👋\n\nاختر ما تريد. سنحاول جعل معظم الخطوات بالضغط على الأزرار بدل الكتابة.",
            [
                [btn("📝 إنشاء حساب", "register"), btn("🔐 تسجيل الدخول", "login")],
                [btn("🔎 تصفح الوظائف", "jobs")],
                [btn("ℹ️ عن المنصة", "about")],
            ],
        )


def main_menu(user=None):
    user = user or {}
    buttons = [
        [btn("🔎 تصفح الوظائف", "jobs"), btn("👤 حسابي", "profile")],
        [btn("❤️ المفضلة", "favorites"), btn("📋 طلباتي", "applications")],
        [btn("💳 محفظتي", "wallet")],
    ]
    if user.get("role") == "employer":
        buttons.append([btn("🏢 لوحة صاحب العمل", "employer")])
    buttons.append([btn("⚙️ إدارة الحساب", "account")])
    buttons.append([btn("🚪 تسجيل الخروج", "logout")])
    return buttons


def registration_start(chat_id):
    SECRET_STATE.pop(str(chat_id), None)
    # Telegram supplies first/last name; use them when available.
    u = STATE.get(str(chat_id), {})
    set_state(chat_id, flow="register", step="role", data={
        "firstName": u.get("firstName", ""),
        "lastName": u.get("lastName", ""),
    })
    send(chat_id, "👤 اختر نوع الحساب:", [
        [btn("🙋 باحث عن عمل", "reg_role:job_seeker")],
        [btn("🏢 صاحب عمل", "reg_role:employer")],
        [btn("⬅️ رجوع", "home")],
    ])


def reg_next(chat_id, step, text, keyboard=None):
    st = get_state(chat_id)
    st["step"] = step
    STATE[str(chat_id)] = st
    send(chat_id, text, keyboard)


def country_keyboard(prefix="reg_country"):
    items = list(LOCATION_DATA.keys())
    return rows([btn(c, f"{prefix}:{c}") for c in items], 2) + [[btn("🌍 أخرى", f"{prefix}:__other__")]]


def city_keyboard(country, prefix="reg_city"):
    cities = list(LOCATION_DATA.get(country, {}).keys())
    return rows([btn(c, f"{prefix}:{c}") for c in cities], 2) + [[btn("⬅️ اختيار دولة أخرى", "reg_back_country")]]


def neighborhood_keyboard(country, city, prefix="reg_neighborhood"):
    neighborhoods = LOCATION_DATA.get(country, {}).get(city, [])
    return rows([btn(n, f"{prefix}:{n}") for n in neighborhoods], 2) + [[btn("⬅️ اختيار مدينة أخرى", "reg_back_city")]]


def category_keyboard(prefix="reg_category"):
    return rows([btn(c, f"{prefix}:{c}") for c in CATEGORIES], 2)


def education_keyboard(prefix="reg_education"):
    return rows([btn(c, f"{prefix}:{c}") for c in EDUCATION], 2)



def bot_create_email_verification(user):
    """Create/send the same 6-digit verification code without Flask request context."""
    code = f"{secrets.randbelow(1000000):06d}"
    token = secrets.token_urlsafe(32)
    data = secure_storage.encryption.decrypt_file("email_verifications") or {}
    data[str(user["id"])] = {
        "token": token,
        "code": code,
        "email": user.get("email", ""),
        "expires": (datetime.now() + timedelta(minutes=5)).isoformat(),
    }
    if not secure_storage.encryption.encrypt_file("email_verifications", data):
        return False

    name = user.get("firstName", "")
    body = f"""مرحباً {name}!

شكراً لتسجيلك في ArabJobs.

رمز تأكيد البريد الإلكتروني الخاص بك هو: {code}

هذا الرمز صالح لمدة 5 دقائق.

إذا لم تقم بإنشاء هذا الحساب، يرجى تجاهل هذه الرسالة.

© 2026 ArabJobs. جميع الحقوق محفوظة.
"""
    html_body = f"""<!doctype html><html lang="ar" dir="rtl"><body>
    <h2>🔐 تفعيل حسابك في ArabJobs</h2>
    <p>مرحباً {name}!</p>
    <p>رمز تأكيد البريد الإلكتروني الخاص بك:</p>
    <div style="font-size:32px;font-weight:bold;letter-spacing:8px">{code}</div>
    <p>هذا الرمز صالح لمدة 5 دقائق.</p>
    </body></html>"""
    return bool(send_email(user.get("email", ""), "🔐 تفعيل حسابك - ArabJobs", body, html_body))


def bot_verify_email_code(chat_id, code):
    st = get_state(chat_id)
    d = st.get("data", {})
    email = str(d.get("email", "")).strip().lower()
    digits_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    code = str(code).strip().translate(digits_map)
    code = "".join(ch for ch in code if ch.isdigit())

    if len(code) != 6:
        send(chat_id, "❌ رمز التحقق يجب أن يتكون من 6 أرقام.\nأرسل الرمز مرة أخرى.", [
            [btn("📩 إعادة إرسال الرمز", "resend_verify")],
            [btn("⬅️ رجوع", "home")],
        ])
        return

    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get("email","")).strip().lower() == email), None)
    if not user:
        clear_state(chat_id)
        send(chat_id, "❌ الحساب غير موجود.", main_menu())
        return

    if user.get("emailVerified"):
        clear_state(chat_id)
        link_user(chat_id, user["id"])
        send(chat_id, "✅ بريدك الإلكتروني مؤكد بالفعل.", main_menu(user))
        return

    data = secure_storage.encryption.decrypt_file("email_verifications") or {}
    item = data.get(str(user.get("id")))
    if not item:
        send(chat_id, "❌ لا يوجد رمز تحقق صالح حاليًا. أرسل رمزًا جديدًا.", [
            [btn("📩 إرسال رمز جديد", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])
        return

    try:
        expires = datetime.fromisoformat(str(item.get("expires")))
    except Exception:
        expires = datetime.min

    if datetime.now() > expires:
        send(chat_id, "⌛ انتهت صلاحية رمز التحقق.", [
            [btn("📩 إرسال رمز جديد", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])
        return

    if str(item.get("code")) != code:
        send(chat_id, "❌ رمز التحقق غير صحيح.\nأرسل الرمز الصحيح مرة أخرى.", [
            [btn("📩 إعادة إرسال الرمز", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])
        return

    user["emailVerified"] = True
    if not secure_storage.save_users(users):
        send(chat_id, "❌ تعذر حفظ تأكيد البريد. حاول مرة أخرى.")
        return

    data.pop(str(user.get("id")), None)
    secure_storage.encryption.encrypt_file("email_verifications", data)
    clear_state(chat_id)
    link_user(chat_id, user["id"])
    send(chat_id, "🎉 تم تأكيد بريدك الإلكتروني بنجاح!\nالآن يمكنك استخدام حسابك بالكامل.", main_menu(user))


def start_email_verification(chat_id, user):
    if user.get("emailVerified"):
        send(chat_id, "✅ بريدك الإلكتروني مؤكد بالفعل.", main_menu(user))
        return
    ok = False
    try:
        ok = bot_create_email_verification(user)
    except Exception:
        LOG.exception("Telegram email verification send failed")
    if ok:
        set_state(chat_id, flow="verify_email", step="verification_code", data={"email": user.get("email","")})
        send(chat_id, "📩 أرسلنا رمز التحقق إلى بريدك الإلكتروني.\n\n🔢 أدخل الرمز المكوّن من 6 أرقام:", [
            [btn("📩 إعادة إرسال الرمز", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])
    else:
        send(chat_id, "❌ تعذر إرسال رسالة التحقق.\nتحقق من إعدادات SMTP في الخادم ثم حاول مرة أخرى.", [
            [btn("📩 المحاولة مرة أخرى", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])


def resend_email_verification(chat_id):
    user = linked_user(chat_id)
    st = get_state(chat_id)
    email = st.get("data", {}).get("email", "") if st else ""
    if not user and email:
        users = secure_storage.load_users() or []
        user = next((u for u in users if str(u.get("email","")).strip().lower() == str(email).strip().lower()), None)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً أو ابدأ التحقق من البريد.", [[btn("🔐 تسجيل الدخول", "login")]])
        return
    start_email_verification(chat_id, user)

def complete_registration(chat_id):
    st = get_state(chat_id)
    d = st.get("data", {})
    required = ["firstName", "lastName", "email", "country", "city", "neighborhood", "phone"]
    if any(not str(d.get(k, "")).strip() for k in required):
        send(chat_id, "❌ ما زالت هناك معلومات ناقصة. نبدأ من جديد.")
        registration_start(chat_id)
        return

    email = d["email"].strip().lower()
    users = secure_storage.load_users() or []
    if any(str(u.get("email", "")).lower() == email for u in users):
        send(chat_id, "❌ هذا البريد الإلكتروني مستخدم بالفعل.\n\nاختر تسجيل الدخول إذا كان الحساب لك.")
        clear_state(chat_id)
        welcome(chat_id)
        return

    password = secret_for(chat_id).get("registration_password", "")
    if not password:
        send(chat_id, "❌ انتهت جلسة كلمة المرور. أعد إدخال كلمة المرور.")
        st["step"] = "password"
        STATE[str(chat_id)] = st
        save_states(STATE)
        return
    ok, msg = validate_password(password)
    if not ok:
        send(chat_id, f"❌ {msg}\n\nأرسل كلمة مرور جديدة تحقق الشروط.")
        st["step"] = "password"
        STATE[str(chat_id)] = st
        return

    role = d.get("role", "job_seeker")
    if role not in ("job_seeker", "employer"):
        send(chat_id, "❌ نوع الحساب غير صالح. اختر نوع الحساب من جديد.")
        registration_start(chat_id)
        return
    if role == "job_seeker" and d.get("education", "") not in EDUCATION:
        send(chat_id, "❌ اختر المستوى التعليمي من القائمة.", education_keyboard())
        st["step"] = "education"
        STATE[str(chat_id)] = st
        save_states(STATE)
        return

    registration_phone = str(d.get("phone", "")).strip()
    if registration_phone:
        other = phone_belongs_to_other_user(registration_phone)
        if other:
            send(chat_id, "❌ رقم الهاتف مرتبط بحساب آخر على المنصة. استخدم رقمًا آخر.", [
                [{"text": "📱 مشاركة رقم هاتف آخر", "request_contact": True}],
                [{"text": "⬅️ إلغاء"}],
            ], reply_keyboard=True, one_time_keyboard=True)
            st["step"] = "phone"
            STATE[str(chat_id)] = st
            save_states(STATE)
            return

    new_user = {
        "id": "user_" + str(int(datetime.now().timestamp() * 1000)),
        "firstName": sanitize_input(d["firstName"]),
        "lastName": sanitize_input(d["lastName"]),
        "email": email,
        "password": PasswordManager.hash_password(password),
        "phone": sanitize_input(d.get("phone", "")),
        "phoneCountryCode": sanitize_input(d.get("phoneCountryCode", "")),
        "category": sanitize_input(d.get("category", "")) if role == "job_seeker" else "",
        "country": d["country"],
        "city": d["city"],
        "neighborhood": d["neighborhood"],
        "birthdate": "",
        "education": sanitize_input(d.get("education", "")) if role == "job_seeker" else "",
        "registeredAt": datetime.now().isoformat(),
        "role": role,
        "status": "active",
        "emailVerified": False,
        "companyName": sanitize_input(d.get("companyName", "")),
        "companyType": sanitize_input(d.get("companyType", "")),
        "companyDescription": sanitize_input(d.get("companyDescription", "")),
        "resume": "",
        "avatar": f"https://ui-avatars.com/api/?name={d['firstName']}+{d['lastName']}&background=1a4a6e&color=fff&size=128",
    }

    users.append(new_user)
    if not secure_storage.save_users(users):
        send(chat_id, "❌ تعذر حفظ الحساب على الخادم. حاول لاحقاً.")
        return

    try:
        verification_sent = bool(bot_create_email_verification(new_user))
    except Exception:
        LOG.exception("email verification creation failed")
        verification_sent = False

    link_user(chat_id, new_user["id"])
    clear_state(chat_id)

    msg = (
        "✅ تم إنشاء حسابك بنجاح!\n\n"
        f"👤 {new_user['firstName']} {new_user['lastName']}\n"
        f"💼 {ROLE_LABELS.get(role, role)}\n"
        f"📍 {new_user['city']}، {new_user['country']}\n\n"
        "📧 يجب تأكيد بريدك الإلكتروني قبل تسجيل الدخول إلى المنصة."
    )
    if verification_sent:
        msg += "\n\nتم إرسال رسالة التحقق إلى بريدك."
    else:
        msg += "\n\nلم يتم إرسال رسالة التحقق حالياً؛ تحقق من إعدادات البريد في الخادم."
    send(chat_id, msg, [
        [btn("📩 تأكيد البريد الآن", "verify_email")],
        [btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")],
    ])


def show_registration_review(chat_id):
    st = get_state(chat_id)
    d = st.get("data", {})
    role = d.get("role", "job_seeker")
    if role not in ("job_seeker", "employer"):
        role = "job_seeker"
    lines = [
        "📋 مراجعة بيانات الحساب",
        "",
        f"👤 الاسم: {d.get('firstName','')} {d.get('lastName','')}",
        f"💼 نوع الحساب: {ROLE_LABELS.get(role, role)}",
        f"📍 الموقع: {d.get('neighborhood','')}، {d.get('city','')}، {d.get('country','')}",
    ]
    if role == "job_seeker":
        lines += [
            f"💼 المجال: {d.get('category','')}",
            f"🎓 التعليم: {d.get('education','')}",
        ]
    else:
        lines += [f"🏢 الشركة/النشاط: {d.get('companyName','')}"]
    lines += [f"📧 البريد: {d.get('email','')}", f"📱 الهاتف: {d.get('phone','') or 'غير محدد'}", "", "هل البيانات صحيحة؟"]
    st["step"] = "review"
    STATE[str(chat_id)] = st
    save_states(STATE)
    send(chat_id, "\n".join(lines), [
        [btn("✅ تأكيد إنشاء الحساب", "reg_confirm")],
        [btn("✏️ تعديل البيانات", "reg_restart")],
        [btn("❌ إلغاء", "home")],
    ])

def start_forgot_password(chat_id):
    clear_state(chat_id)
    set_state(chat_id, flow="forgot_password", step="email", data={})
    send(chat_id, "🔑 استعادة كلمة المرور\n\n📧 اكتب البريد الإلكتروني للحساب:", [[btn("⬅️ رجوع", "home")]])


def send_password_reset_code(chat_id, email):
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get("email", "")).strip().lower() == email), None)

    # Do not reveal whether an email exists.
    if not user:
        clear_state(chat_id)
        send(chat_id, "📩 إذا كان البريد مرتبطاً بحساب، فستصلك رسالة تحتوي على رمز الاستعادة.\n\nيمكنك العودة وتسجيل الدخول بعد ذلك.", [[btn("🔐 تسجيل الدخول", "login"), btn("⬅️ الرئيسية", "home")]])
        return

    code = f"{secrets.randbelow(1000000):06d}"
    if not save_otp_record(PASSWORD_RESET_FILE, user["id"], code, {"email": email}):
        send(chat_id, "❌ تعذر إنشاء رمز الاستعادة. حاول لاحقاً.", [[btn("⬅️ الرئيسية", "home")]])
        return

    body = f"""مرحباً {user.get('firstName','')}!\n\nرمز استعادة كلمة المرور في منصة التوظيف هو: {code}\n\nالرمز صالح لمدة {OTP_TTL_MINUTES} دقائق، ولا تشارك هذا الرمز مع أي شخص."""
    html_body = f"<div dir='rtl'><h2>🔑 استعادة كلمة المرور</h2><p>رمز الاستعادة:</p><h1>{code}</h1><p>صالح لمدة {OTP_TTL_MINUTES} دقائق.</p></div>"
    try:
        ok = bool(send_email(email, "🔑 استعادة كلمة المرور - منصة التوظيف", body, html_body))
    except Exception:
        LOG.exception("password reset email failed")
        ok = False
    if not ok:
        delete_otp_record(PASSWORD_RESET_FILE, user["id"])
        send(chat_id, "❌ تعذر إرسال رسالة الاستعادة. تحقق من إعدادات البريد ثم حاول مرة أخرى.", [[btn("⬅️ الرئيسية", "home")]])
        return

    set_state(chat_id, flow="forgot_password", step="reset_code", data={"email": email, "user_id": user["id"]})
    send(chat_id, "📩 أرسلنا رمز استعادة إلى بريدك الإلكتروني.\n\n🔢 أدخل الرمز المكوّن من 6 أرقام:", [[btn("⬅️ الرئيسية", "home")]])


def verify_password_reset_code(chat_id, code):
    st = get_state(chat_id)
    d = st.get("data", {})
    user_id = d.get("user_id")
    data, item = get_otp_record(PASSWORD_RESET_FILE, user_id)
    if not item:
        send(chat_id, "❌ لا يوجد رمز صالح. ابدأ الاستعادة من جديد.", [[btn("🔑 نسيت كلمة المرور", "forgot_password")]])
        return
    try:
        expires = datetime.fromisoformat(str(item.get("expires")))
    except Exception:
        expires = datetime.min
    if datetime.now() > expires:
        delete_otp_record(PASSWORD_RESET_FILE, user_id, data)
        send(chat_id, "⌛ انتهت صلاحية الرمز. اطلب رمزاً جديداً.", [[btn("🔑 نسيت كلمة المرور", "forgot_password")]])
        return
    try:
        attempts = int(item.get("attempts", 0))
    except Exception:
        attempts = 0
    if attempts >= OTP_MAX_ATTEMPTS:
        delete_otp_record(PASSWORD_RESET_FILE, user_id, data)
        send(chat_id, "⛔ تم تجاوز عدد المحاولات المسموح بها. اطلب رمزاً جديداً.", [[btn("🔑 نسيت كلمة المرور", "forgot_password")]])
        return
    code = str(code).strip()
    item["attempts"] = attempts + 1
    data[str(user_id)] = item
    secure_storage.encryption.encrypt_file(PASSWORD_RESET_FILE, data)
    if not secrets.compare_digest(str(item.get("code_hash", "")), otp_hash(code)):
        send(chat_id, f"❌ الرمز غير صحيح. المحاولات المتبقية: {max(0, OTP_MAX_ATTEMPTS - attempts - 1)}")
        return
    set_state(chat_id, flow="forgot_password", step="new_password", data={"email": d.get("email", ""), "user_id": user_id})
    send(chat_id, "🔑 اكتب كلمة المرور الجديدة.\n\nيجب أن تكون 8 أحرف على الأقل وتحتوي على حرف كبير وصغير ورقم ورمز خاص.")


def finish_password_reset(chat_id):
    st = get_state(chat_id)
    d = st.get("data", {})
    user_id = d.get("user_id")
    secret = secret_for(chat_id)
    password = secret.get("reset_password", "")
    if not password:
        send(chat_id, "❌ انتهت جلسة الاستعادة. ابدأ من جديد.", [[btn("🔑 نسيت كلمة المرور", "forgot_password")]])
        return
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get("id")) == str(user_id)), None)
    if not user:
        clear_state(chat_id)
        send(chat_id, "❌ الحساب غير موجود.", [[btn("⬅️ الرئيسية", "home")]])
        return
    user["password"] = PasswordManager.hash_password(password)
    if not user["password"] or not secure_storage.save_users(users):
        send(chat_id, "❌ تعذر تحديث كلمة المرور. حاول لاحقاً.")
        return
    delete_otp_record(PASSWORD_RESET_FILE, user_id)
    clear_state(chat_id)
    send(chat_id, "✅ تم تغيير كلمة المرور بنجاح. يمكنك الآن تسجيل الدخول.", [[btn("🔐 تسجيل الدخول", "login"), btn("⬅️ الرئيسية", "home")]])


def start_phone_login(chat_id):
    clear_state(chat_id)
    set_state(chat_id, flow="phone_login", step="contact", data={})
    send(chat_id, "📱 تسجيل الدخول برقم الهاتف\n\nاضغط زر «مشاركة رقم الهاتف» لإرسال رقمك من Telegram.\nلن نعرض رقمك في المحادثة ولن نستخدمه إلا للتحقق من الحساب.", [
        [btn("⬅️ رجوع", "home")],
    ])
    try:
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "📱 مشاركة رقم الهاتف للتحقق:",
            "reply_markup": {
                "keyboard": [[{"text": "📱 مشاركة رقم الهاتف", "request_contact": True}], [{"text": "⬅️ إلغاء"}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        })
    except Exception:
        LOG.exception("Could not send phone login keyboard")


def mask_email(value):
    value = str(value or "").strip()
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    if len(local) <= 1:
        local_mask = "*"
    elif len(local) == 2:
        local_mask = local[0] + "*"
    else:
        local_mask = local[0] + "*" * min(5, len(local) - 1)
    return local_mask + "@" + domain


def send_phone_otp_email(email, first_name, code):
    """Send phone-login OTP directly through the project's SMTP settings.

    We intentionally do not depend only on app.send_email here: this gives the
    Telegram bot an explicit SMTP result and a useful server-side error when
    delivery fails. The OTP itself is never written to the logs.
    """
    email = str(email or "").strip()
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port_raw = os.environ.get("SMTP_PORT", "587").strip()
    username = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    mail_from = os.environ.get("MAIL_FROM", username).strip() or username

    try:
        port = int(port_raw)
    except ValueError:
        LOG.error("PHONE OTP SMTP configuration error: SMTP_PORT=%r is invalid", port_raw)
        return False

    if not email or not username or not password:
        LOG.error(
            "PHONE OTP SMTP configuration incomplete: host=%s port=%s user=%s from=%s",
            host, port, mask_email(username), mask_email(mail_from),
        )
        return False

    body = (
        f"مرحباً {first_name}!\n\n"
        "رمز تسجيل الدخول إلى منصة التوظيف عبر رقم الهاتف هو: " + code + "\n\n"
        "الرمز صالح لمدة 10 دقائق. لا تشارك هذا الرمز مع أي شخص.\n\n"
        "إذا لم تطلب تسجيل الدخول، تجاهل هذه الرسالة.\n"
    )
    html_body = (
        "<!doctype html><html lang='ar' dir='rtl'><body style='font-family:Arial,sans-serif'>"
        "<h2>📱 تسجيل الدخول إلى منصة التوظيف</h2>"
        f"<p>مرحباً {html.escape(str(first_name or ''))}!</p>"
        "<p>رمز تسجيل الدخول الخاص بك:</p>"
        f"<div style='font-size:32px;font-weight:bold;letter-spacing:8px'>{code}</div>"
        "<p>صالح لمدة 10 دقائق. لا تشارك الرمز مع أي شخص.</p>"
        "</body></html>"
    )

    msg = EmailMessage()
    msg["Subject"] = "رمز تسجيل الدخول عبر الهاتف - منصة التوظيف"
    msg["From"] = mail_from
    msg["To"] = email
    msg.set_content(body)
    msg.add_alternative(html_body, subtype="html")

    LOG.info("PHONE OTP: sending email to %s via %s:%s", mask_email(email), host, port)
    try:
        with smtplib.SMTP(host, port, timeout=25) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(msg)
        LOG.info("PHONE OTP: SMTP accepted message for %s", mask_email(email))
        return True
    except Exception as exc:
        LOG.exception("PHONE OTP: SMTP delivery failed for %s: %s", mask_email(email), exc)
        return False


def start_phone_otp(chat_id, user, force=False):
    # Do not create another OTP if a valid one is already active unless the user
    # explicitly requested a resend. This prevents duplicate codes and spam.
    existing_data, existing = get_otp_record(PHONE_OTP_FILE, chat_id)
    if existing and str(existing.get("user_id")) == str(user.get("id")) and not force:
        try:
            if datetime.now() <= datetime.fromisoformat(str(existing.get("expires"))):
                set_state(chat_id, flow="phone_login", step="otp", data={"user_id": user["id"]})
                send(chat_id, "📩 رمز الدخول أُرسل بالفعل إلى بريدك الإلكتروني.\n\n🔢 أدخل الرمز المكوّن من 6 أرقام.\nصالح لمدة 10 دقائق.", [
                    [btn("📩 إعادة إرسال الرمز", "phone_resend_otp")],
                    [btn("❌ إلغاء", "home")],
                ])
                return
        except Exception:
            pass

    code = f"{secrets.randbelow(1000000):06d}"
    if not save_otp_record(PHONE_OTP_FILE, chat_id, code, {
        "user_id": user["id"],
        "email": user.get("email", ""),
        "sent_at": datetime.now().isoformat(),
    }):
        send(chat_id, "❌ تعذر بدء التحقق. حاول لاحقاً.")
        return

    email = str(user.get("email", "")).strip()
    sent = send_phone_otp_email(email, user.get("firstName", ""), code)

    # Fallback to the application's existing mail adapter if direct SMTP is
    # unavailable. This keeps compatibility with the existing Flask mail setup.
    if not sent:
        try:
            body = (
                f"مرحباً {user.get('firstName','')}!\n\n"
                "رمز تسجيل الدخول إلى منصة التوظيف عبر رقم الهاتف هو: " + code + "\n\n"
                "الرمز صالح لمدة 10 دقائق. لا تشارك هذا الرمز مع أي شخص.\n"
            )
            html_body = (
                "<div dir='rtl'><h2>📱 تسجيل الدخول إلى منصة التوظيف</h2>"
                f"<p>رمز الدخول: <strong>{code}</strong></p>"
                "<p>صالح لمدة 10 دقائق.</p></div>"
            )
            sent = bool(send_email(email, "رمز تسجيل الدخول عبر الهاتف - منصة التوظيف", body, html_body))
            LOG.info("PHONE OTP: application mail adapter result=%s for %s", sent, mask_email(email))
        except Exception:
            LOG.exception("PHONE OTP: application mail adapter failed for %s", mask_email(email))
            sent = False

    if not sent:
        delete_otp_record(PHONE_OTP_FILE, chat_id)
        clear_state(chat_id)
        send(chat_id, "❌ لم يتم إرسال رمز الدخول. الخادم لم يستطع إرسال البريد الإلكتروني.\nتحقق من إعدادات SMTP ثم حاول مرة أخرى.", [[btn("🔐 تسجيل الدخول", "login")]])
        return

    set_state(chat_id, flow="phone_login", step="otp", data={"user_id": user["id"]})
    send(chat_id, f"📩 أرسلنا رمز الدخول إلى {mask_email(email)}.\n\n🔢 أدخل الرمز المكوّن من 6 أرقام.\nصالح لمدة 10 دقائق.", [
        [btn("📩 إعادة إرسال الرمز", "phone_resend_otp")],
        [btn("❌ إلغاء", "home")],
    ])


def verify_phone_otp(chat_id, code):
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        send(chat_id, "❌ رمز الدخول يجب أن يتكون من 6 أرقام.")
        return
    st = get_state(chat_id)
    user_id = st.get("data", {}).get("user_id")
    data, item = get_otp_record(PHONE_OTP_FILE, chat_id)
    if not item or str(item.get("user_id")) != str(user_id):
        clear_state(chat_id)
        send(chat_id, "❌ لا يوجد تحقق صالح. ابدأ تسجيل الدخول بالهاتف من جديد.", [[btn("📱 دخول بالهاتف", "phone_login")]])
        return
    try:
        expires = datetime.fromisoformat(str(item.get("expires")))
    except Exception:
        expires = datetime.min
    if datetime.now() > expires:
        delete_otp_record(PHONE_OTP_FILE, chat_id, data)
        send(chat_id, "⌛ انتهت صلاحية الرمز.", [[btn("📱 دخول بالهاتف", "phone_login")]])
        return
    attempts = int(item.get("attempts", 0))
    if attempts >= OTP_MAX_ATTEMPTS:
        delete_otp_record(PHONE_OTP_FILE, chat_id, data)
        send(chat_id, "⛔ تم تجاوز عدد المحاولات. ابدأ من جديد.", [[btn("📱 دخول بالهاتف", "phone_login")]])
        return
    item["attempts"] = attempts + 1
    data[str(chat_id)] = item
    secure_storage.encryption.encrypt_file(PHONE_OTP_FILE, data)
    if not secrets.compare_digest(str(item.get("code_hash", "")), otp_hash(str(code).strip())):
        send(chat_id, f"❌ الرمز غير صحيح. المحاولات المتبقية: {max(0, OTP_MAX_ATTEMPTS - attempts - 1)}")
        return
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get("id")) == str(user_id)), None)
    if not user or user.get("status") == "blocked":
        delete_otp_record(PHONE_OTP_FILE, chat_id, data)
        clear_state(chat_id)
        send(chat_id, "⛔ تعذر تسجيل الدخول بهذا الحساب.", [[btn("⬅️ الرئيسية", "home")]])
        return
    if not user.get("emailVerified", False):
        delete_otp_record(PHONE_OTP_FILE, chat_id, data)
        set_state(chat_id, flow="verify_email", step="verification_code", data={"email": user.get("email", "")})
        send(chat_id, "📧 يجب تأكيد البريد الإلكتروني أولاً.", [[btn("📩 إرسال رمز التحقق", "resend_verify")], [btn("⬅️ الرئيسية", "home")]])
        return
    delete_otp_record(PHONE_OTP_FILE, chat_id, data)
    clear_state(chat_id)
    link_user(chat_id, user["id"])
    if not pin_is_configured(chat_id):
        send(chat_id, f"✅ تم تسجيل الدخول برقم الهاتف.\nمرحباً {user.get('firstName','')} 👋", [[btn("🔢 إنشاء PIN للدخول السريع", "pin_setup")], [btn("⬅️ المتابعة بدون PIN", "pin_skip")]])
    else:
        send(chat_id, f"✅ تم تسجيل الدخول برقم الهاتف.\nمرحباً {user.get('firstName','')} 👋", main_menu(user))


def start_login(chat_id):
    clear_state(chat_id)
    set_state(chat_id, flow="login", step="email", data={})
    send(chat_id, "🔐 تسجيل الدخول\n\nاختر طريقة الدخول:", [
        [btn("📧 بالبريد وكلمة المرور", "login_email")],
        [btn("📱 برقم الهاتف", "phone_login")],
        [btn("🔢 دخول سريع بـ PIN", "pin_login")],
        [btn("🔑 نسيت كلمة المرور", "forgot_password")],
        [btn("⬅️ رجوع", "home")],
    ])


def start_email_login(chat_id):
    clear_state(chat_id)
    set_state(chat_id, flow="login", step="email", data={})
    send(chat_id, "🔐 تسجيل الدخول بالبريد\n\n📧 اكتب البريد الإلكتروني للحساب:", [[btn("🔑 نسيت كلمة المرور", "forgot_password")], [btn("📱 دخول بالهاتف", "phone_login"), btn("⬅️ رجوع", "home")]])


def finish_login(chat_id):
    st = get_state(chat_id)
    d = st.get("data", {})
    email = d.get("email", "").strip().lower()
    password = secret_for(chat_id).get("login_password", "")
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get("email", "")).lower() == email), None)
    if not user or not PasswordManager.verify_password(password, user.get("password", "")):
        send(chat_id, "❌ بيانات الدخول غير صحيحة.\n\nحاول مرة أخرى من تسجيل الدخول.")
        clear_state(chat_id)
        return
    if user.get("status") == "blocked":
        send(chat_id, "⛔ هذا الحساب محظور من الإدارة.")
        clear_state(chat_id)
        return
    if not user.get("emailVerified", False):
        set_state(chat_id, flow="verify_email", step="verification_code", data={"email": user.get("email","")})
        send(chat_id, "📧 يجب تأكيد البريد الإلكتروني أولاً.", [
            [btn("📩 إرسال/إعادة إرسال رمز التحقق", "resend_verify")],
            [btn("⬅️ الرئيسية", "home")],
        ])
        return
    link_user(chat_id, user["id"])
    clear_state(chat_id)
    if not pin_is_configured(chat_id):
        send(chat_id, f"✅ تم تسجيل الدخول.\nمرحباً {user.get('firstName','')} 👋", [[btn("🔢 إنشاء PIN للدخول السريع", "pin_setup")], [btn("⬅️ المتابعة بدون PIN", "pin_skip")]])
    else:
        send(chat_id, f"✅ تم تسجيل الدخول.\nمرحباً {user.get('firstName','')} 👋", main_menu(user))


def show_profile(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 يجب تسجيل الدخول أولاً.", [[btn("تسجيل الدخول", "login")]])
        return

    role = normalize_role(user.get("role", "job_seeker"))
    lines = [
        "👤 حسابي",
        "",
        f"الاسم: {user.get('firstName','')} {user.get('lastName','')}",
        f"البريد: {user.get('email','')}",
        f"الهاتف: {user.get('phone','') or 'غير مضاف'}",
        f"الصفة: {ROLE_LABELS.get(role, role)}",
    ]

    if role == "employer":
        lines += [
            f"🏢 الشركة/النشاط: {user.get('companyName','') or 'غير محدد'}",
            f"📝 الوصف: {user.get('companyDescription','') or 'غير مضاف'}",
        ]
    else:
        lines += [
            f"💼 المجال: {user.get('category','') or 'غير محدد'}",
            f"🎓 التعليم: {user.get('education','') or 'غير محدد'}",
        ]

    lines.append(f"📍 الموقع: {user.get('neighborhood','')}، {user.get('city','')}، {user.get('country','')}")

    buttons = []
    if pin_is_configured(chat_id):
        buttons.append([btn("🔢 تغيير PIN", "pin_change"), btn("🗑️ حذف PIN", "pin_delete")])
    else:
        buttons.append([btn("🔢 إنشاء PIN سريع", "pin_setup")])
    if role == "job_seeker":
        buttons.append([btn("✏️ تعديل المجال", "edit_category"), btn("🎓 تعديل التعليم", "edit_education")])
    else:
        buttons.append([btn("🏢 اسم الشركة", "emp_edit_company_name"), btn("🧾 نوع النشاط", "emp_edit_company_type")])
        buttons.append([btn("📝 وصف الشركة", "emp_edit_company_desc"), btn("📱 الهاتف", "edit_phone")])
    buttons.extend([
        [btn("🌍 تعديل الموقع", "edit_country")],
        [btn("⬅️ القائمة الرئيسية", "home")],
    ])
    send(chat_id, "\n".join(lines), buttons)


def show_jobs(chat_id, page=0, category=None, country=None):
    jobs = secure_storage.load_jobs() or []
    if category:
        jobs = [j for j in jobs if str(j.get("category", "")).strip() == category]
    if country:
        jobs = [j for j in jobs if str(j.get("country", "")).strip() == country]
    start = page * JOB_PAGE_SIZE
    subset = jobs[start:start + JOB_PAGE_SIZE]
    if not subset:
        send(chat_id, "🔎 لا توجد وظائف مطابقة حالياً.", [[btn("⬅️ القائمة الرئيسية", "home")]])
        return

    buttons = []
    for j in subset:
        title = str(j.get("title", "وظيفة"))[:50]
        buttons.append([btn(f"💼 {title}", f"job:{j.get('id')}")])
    nav = []
    if start > 0:
        nav.append(btn("⬅️ السابق", f"jobs_page:{page-1}"))
    if start + JOB_PAGE_SIZE < len(jobs):
        nav.append(btn("التالي ➡️", f"jobs_page:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([btn("🎯 تصفية حسب المجال", "job_filter_category"), btn("🌍 تصفية حسب الدولة", "job_filter_country")])
    buttons.append([btn("⬅️ القائمة الرئيسية", "home")])
    send(chat_id, f"🔎 الوظائف\n\nالصفحة {page+1} — اختر وظيفة:", buttons)


def show_job(chat_id, job_id):
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get("id")) == str(job_id)), None)
    if not job:
        send(chat_id, "❌ الوظيفة غير موجودة.", [[btn("⬅️ الوظائف", "jobs")]])
        return
    text = (
        f"💼 {job.get('title','')}\n\n"
        f"🏢 {job.get('company','')}\n"
        f"📍 {job.get('neighborhood','')}، {job.get('city','')}، {job.get('country','')}\n"
        f"💰 {job.get('salary','غير محدد')}\n"
        f"🗂️ المجال: {job.get('category','غير محدد')}\n"
        f"🕐 نوع العمل: {job.get('workType', job.get('type','غير محدد'))}\n\n"
        f"{str(job.get('description',''))[:2500]}"
    )
    send(chat_id, text, [
        [btn("📩 تقديم على الوظيفة", f"apply:{job.get('id')}"), btn("❤️ حفظ", f"fav:{job.get('id')}")],
        [btn("⬅️ الوظائف", "jobs")],
    ])


def telegram_subscription_menu(chat_id):
    user = employer_user(chat_id)
    if not user:
        send(chat_id, "⛔ هذه الخدمة مخصصة لصاحب العمل.", [[btn("⬅️ الرئيسية", "home")]])
        return
    subs = _load_subscriptions()
    active = [x for x in subs if str(x.get("employerId")) == str(user.get("id")) and x.get("status") == "active" and str(x.get("expiresAt", "")) > datetime.now().isoformat()]
    active.sort(key=lambda x: str(x.get("expiresAt", "")), reverse=True)
    lines = ["📦 الاشتراك", ""]
    if active:
        a = active[0]
        lines += [f"✅ الباقة الحالية: {a.get('planName','')}", f"💵 السعر: {a.get('amountUsd',0):.2f} USD", f"📅 تنتهي: {a.get('expiresAt','')[:19].replace('T',' ')}", ""]
    else:
        lines += ["ℹ️ لا يوجد اشتراك نشط حالياً.", ""]
    buttons = []
    for plan in _load_subscription_plans():
        buttons.append([btn(f"📦 {plan.get('name','')} — {float(plan.get('monthlyUsd',0)):.2f} USD", f"sub_buy:{plan.get('id')}" )])
    buttons += [[btn("🧾 فواتيري", "invoices")], [btn("💳 المحفظة", "wallet"), btn("⬅️ اللوحة", "employer")]]
    send(chat_id, "\n".join(lines), buttons)


def telegram_subscribe(chat_id, plan_id):
    user = employer_user(chat_id)
    if not user:
        return
    plan = next((p for p in _load_subscription_plans() if str(p.get("id")) == str(plan_id)), None)
    if not plan:
        send(chat_id, "❌ الباقة غير موجودة.", [[btn("📦 الاشتراكات", "subscriptions")]])
        return
    uid = str(user.get("id")); amount = int(round(float(plan.get("monthlyUsd", 0)) * 100))
    subs = _load_subscriptions()
    active = next((x for x in subs if str(x.get("employerId")) == uid and x.get("status") == "active" and str(x.get("expiresAt", "")) > datetime.now().isoformat()), None)
    if active:
        msg = "هذه الباقة مفعلة بالفعل." if str(active.get("planId")) == str(plan_id) else "لديك اشتراك نشط حالياً. انتظر انتهاءه قبل تغيير الباقة."
        send(chat_id, f"ℹ️ {msg}", [[btn("📦 الاشتراك", "subscriptions")]])
        return
    ref = f"telegram_subscription_{plan_id}_{uid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    prior = next((x for x in subs if str(x.get("paymentReference")) == ref), None)
    if prior:
        send(chat_id, "ℹ️ تم تنفيذ هذه العملية مسبقاً.", [[btn("🧾 فواتيري", "invoices")]])
        return
    bal = _get_user_wallet_balance(uid); available = int(bal.get("available", 0) or 0)
    if available < amount:
        price = service_prices(secure_storage, user)["prices"].get("subscription_usd", {})
        send(chat_id, f"💳 الرصيد غير كافٍ لتفعيل {plan.get('name','الباقة')} ({float(plan.get('monthlyUsd',0)):.2f} USD).\nرصيدك الحالي: {available/100:.2f} USD.", [[btn("💳 المحفظة", "wallet"), btn("📦 الاشتراك", "subscriptions")]])
        return
    debit = subtract_balance(uid, amount, "subscription", reference_id=ref, description=f"اشتراك {plan.get('name','')} الشهري", metadata={"planId": plan_id, "source": "telegram"})
    if not debit.get("success"):
        telegram_error(chat_id, "فشل خصم الاشتراك", context=str(debit))
        send(chat_id, "❌ تعذر خصم رسوم الاشتراك. لم يتم تفعيل الباقة.", [[btn("💳 المحفظة", "wallet")]])
        return
    now = datetime.now(); exp = now + timedelta(days=30)
    item = {"subscriptionId": f"sub_tg_{secrets.token_urlsafe(12)}", "employerId": uid, "planId": plan_id, "planName": plan.get("name"), "amountUsd": float(plan.get("monthlyUsd",0)), "currency": "USD", "status": "active", "startedAt": now.isoformat(), "expiresAt": exp.isoformat(), "paymentReference": ref, "source": "telegram"}
    new_subs = [x for x in subs if not (str(x.get("employerId")) == uid and x.get("status") == "active")]
    new_subs.append(item)
    if not _save_subscriptions(new_subs):
        add_balance(uid, amount, "refund", reference_id=f"{ref}:rollback", description="تعويض تلقائي لفشل حفظ اشتراك Telegram", metadata={"subscriptionRollback": True})
        telegram_error(chat_id, "فشل حفظ الاشتراك وتم رد الرصيد")
        send(chat_id, "❌ تعذر حفظ الاشتراك وتمت إعادة المبلغ إلى المحفظة.", [[btn("📦 الاشتراك", "subscriptions")]])
        return
    payment = {"paymentId": ref, "employerId": uid, "employerEmail": user.get("email",""), "applicantId": None, "jobId": None, "amount": float(plan.get("monthlyUsd",0)), "amountUnit": "major", "currency": "USD", "formattedPrice": f"{float(plan.get('monthlyUsd',0)):.2f} USD", "description": f"اشتراك {plan.get('name','')} الشهري", "status": "paid", "paymentType": "subscription", "invoiceType": "subscription", "createdAt": now.isoformat(), "updatedAt": now.isoformat(), "source": "telegram"}
    logs = _load_payment_logs(); logs.append(payment); _save_payment_logs(logs)
    from invoice_service import create_invoice
    inv_result, inv_code = create_invoice(payment)
    invoice = inv_result.get("invoice") if isinstance(inv_result, dict) and inv_code in (200,201) else None
    telegram_audit(chat_id, f"تم تفعيل اشتراك Telegram: plan={plan_id}; payment={ref}")
    text = f"✅ تم تفعيل {plan.get('name','الباقة')} بنجاح لمدة 30 يوماً.\n💵 {float(plan.get('monthlyUsd',0)):.2f} USD"
    if invoice: text += f"\n🧾 رقم الفاتورة: {invoice.get('invoiceNumber','') }"
    send(chat_id, text, [[btn("📦 اشتراكي", "subscriptions"), btn("🧾 فواتيري", "invoices")], [btn("🏢 اللوحة", "employer")]])


def telegram_invoices(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("🔐 تسجيل الدخول", "login")]])
        return
    from invoice_service import list_invoices
    result, status = list_invoices({"employerId": str(user.get("id"))})
    invoices = result.get("invoices", []) if isinstance(result, dict) and status == 200 else []
    if not invoices:
        send(chat_id, "🧾 لا توجد فواتير حتى الآن.", [[btn("💳 المحفظة", "wallet"), btn("⬅️ الرئيسية", "home")]])
        return
    lines = ["🧾 فواتيري", ""]
    for inv in invoices[:20]:
        lines.append(f"• {inv.get('invoiceNumber','—')} — {inv.get('formattedPrice', inv.get('amount',''))} — {inv.get('status','')}")
    send(chat_id, "\n".join(lines), [[btn("💳 المحفظة", "wallet"), btn("📦 الاشتراك", "subscriptions")], [btn("⬅️ الرئيسية", "home")]])


def telegram_wallet(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("تسجيل الدخول", "login")]])
        return
    result = get_wallet_balance(str(user.get("id")))
    balance = (result.get("balance") or {}) if result.get("success") else {}
    available = float(balance.get("available", 0) or 0) / 100.0
    pricing = service_prices(secure_storage, user)
    currency = pricing.get("currency", "USD")

    attempts = []
    if user.get("role") == "employer":
        used = int(user.get("sadaqahFreeJobPostsUsed", 0) or 0)
        attempts.append(("📢 نشر وظيفة", max(0, JOB_POSTING_SADAQAH_FREE_LIMIT-used), JOB_POSTING_SADAQAH_FREE_LIMIT))
        used = int(user.get("sadaqahFreeUnlocksUsed", 0) or 0)
        attempts.append(("📞 فتح بيانات متقدم", max(0, CONTACT_SADAQAH_FREE_LIMIT-used), CONTACT_SADAQAH_FREE_LIMIT))
    used = int(user.get("sadaqahFreeApplicationsUsed", 0) or 0)
    attempts.append(("📩 التقديم على وظيفة", max(0, APPLICATION_SADAQAH_FREE_LIMIT-used), APPLICATION_SADAQAH_FREE_LIMIT))

    total_left = sum(x[1] for x in attempts)
    lines = [
        "💳 محفظتي",
        "",
        f"💰 الرصيد المتاح: {available:.2f} USD",
        f"🌍 عملة بلدك: {pricing.get('currencyName', currency)} ({currency})",
        "",
        f"🎯 المحاولات المجانية المتبقية: {total_left}",
    ]
    for title, left, limit in attempts:
        lines.append(f"• {title}: {left}/{limit}")
    lines.extend([
        "",
        "💡 بعد انتهاء المحاولات المجانية تُخصم رسوم الخدمة من رصيد المحفظة.",
        "💳 شحن المحفظة الحقيقي غير مفعّل حاليًا على الموقع الرسمي؛ الباقات الحالية تجريبية مثل الموقع.",
    ])
    send(chat_id, "\n".join(lines), [
        [btn("💵 أسعار الخدمات", "wallet_packages"), btn("📜 حركات المحفظة", "wallet_transactions")],
        [btn("📦 الاشتراكات", "subscriptions"), btn("🧾 فواتيري", "invoices")] if user.get("role") == "employer" else [btn("🔎 الوظائف", "jobs")],
        [btn("🏢 لوحة صاحب العمل", "employer")] if user.get("role") == "employer" else [btn("🔎 الوظائف", "jobs")],
        [btn("⬅️ الرئيسية", "home")]
    ])

def telegram_wallet_transactions(chat_id):
    user=linked_user(chat_id)
    if not user:
        send(chat_id,"🔐 سجل الدخول أولاً.",[[btn("تسجيل الدخول","login")]]); return
    result=get_wallet_transactions(str(user.get("id")),limit=20)
    items=result.get("transactions",[]) if result.get("success") else []
    if not items:
        send(chat_id,"📜 لا توجد حركات في المحفظة حتى الآن.",[[btn("⬅️ المحفظة","wallet")]]); return
    lines=["📜 آخر حركات المحفظة\n"]
    for x in items[:20]:
        typ="➕ إضافة" if x.get("type")=="credit" else "➖ خصم"
        amount=float(x.get("amount",0) or 0)/100
        lines.append(f"{typ} {amount:.2f} USD — {x.get('description','') or x.get('transactionType','')}")
    send(chat_id,"\n".join(lines),[[btn("⬅️ المحفظة","wallet"),btn("⬅️ الرئيسية","home")]])

def telegram_wallet_packages(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("تسجيل الدخول", "login")]])
        return
    pricing = service_prices(secure_storage, user)
    rates = __import__("payment_pricing").load_settings(secure_storage)["rates"]
    currency = pricing.get("currency", "USD")
    packages = [5,10,25,50,100]
    lines=["💵 باقات شحن المحفظة التجريبية\n", f"العملة: {pricing.get('currencyName',currency)} ({currency})", "الدفع الحقيقي غير مفعّل حالياً.\n"]
    for usd in packages:
        local = usd * float(rates.get(currency,1))
        formatted = __import__("payment_pricing").format_local(local,currency)
        lines.append(f"• {usd} USD = {formatted}")
    lines.append("\n🧪 الشحن الفعلي حالياً من لوحة الإدارة → المستخدمون → شحن تجريبي.")
    send(chat_id, "\n".join(lines), [[btn("⬅️ المحفظة", "wallet"), btn("⬅️ الرئيسية", "home")]])


def _telegram_sadaqah_pause(chat_id, action, callback_data):
    existing = get_state(chat_id)
    data = dict(existing.get("data") or {})
    data.update({"action": action, "callback": callback_data, "startedAt": datetime.now().isoformat()})
    STATE[str(chat_id)] = {"flow":"sadaqah_pause","step":"wait","data":data}
    save_states(STATE)
    send(chat_id,
         "🤍 وقفة خير قبل متابعة طلبك\n\n"
         "نرجو التوقف 15 ثانية وقراءة سورة الفاتحة والدعاء لوالدي صاحب المنصة.\n\n"
         "سورة الفاتحة:\n"
         "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ\n"
         "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ\n"
         "الرَّحْمَٰنِ الرَّحِيمِ\n"
         "مَالِكِ يَوْمِ الدِّينِ\n"
         "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ\n"
         "اهْدِنَا الصِّرَاطَ الْمُسْتَقِيمَ\n"
         "صِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ",
         [[btn("🤍 انتهيت من وقفة الخير", "sadaqah_done")], [btn("❌ إلغاء", "home")]])


def _telegram_apply_after_pause(chat_id):
    st=get_state(chat_id); data=st.get("data",{})
    try:
        started=datetime.fromisoformat(str(data.get("startedAt")))
    except Exception:
        started=datetime.now()
    if (datetime.now()-started).total_seconds()<15:
        send(chat_id,"⏳ يرجى إكمال 15 ثانية من وقفة الخير أولاً.", [[btn("🤍 متابعة الوقفة", "sadaqah_done")]])
        return
    st["flow"]="apply_confirm"; st["step"]="confirm"; st["data"]={"job_id":data.get("job_id")}
    STATE[str(chat_id)]=st; save_states(STATE)
    confirm_apply(chat_id, skip_pause=True)


def apply_job(chat_id, job_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 يجب إنشاء حساب أو تسجيل الدخول قبل التقديم.", [
            [btn("📝 إنشاء حساب", "register"), btn("🔐 تسجيل الدخول", "login")],
            [btn("⬅️ رجوع", f"job:{job_id}")],
        ])
        return
    if user.get("role") != "job_seeker":
        send(chat_id, "ℹ️ التقديم على الوظائف متاح لحساب الباحث عن عمل.", [[btn("⬅️ الوظيفة", f"job:{job_id}")]])
        return

    apps = secure_storage.load_applications() or {}
    uid = str(user["id"])
    if any(str(a.get("jobId")) == str(job_id) for a in apps.get(uid, [])):
        send(chat_id, "ℹ️ لقد تقدمت لهذه الوظيفة مسبقاً.", [[btn("⬅️ الوظيفة", f"job:{job_id}")]])
        return
    used = int(user.get("sadaqahFreeApplicationsUsed", 0) or 0)
    if used < APPLICATION_SADAQAH_FREE_LIMIT:
        set_state(chat_id, flow="sadaqah_pause", step="wait", data={"action":"apply","job_id":job_id,"startedAt":datetime.now().isoformat()})
        _telegram_sadaqah_pause(chat_id, "apply", "apply_after_pause")
    else:
        set_state(chat_id, flow="apply_confirm", step="confirm", data={"job_id": job_id})
        send(chat_id, "📩 تأكيد التقديم\n\nهل تريد إرسال طلب التوظيف الآن؟", [
            [btn("✅ نعم، تقديم", "apply_confirm_yes"), btn("❌ إلغاء", f"job:{job_id}")],
        ])


def confirm_apply(chat_id, skip_pause=False):
    st = get_state(chat_id)
    job_id = st.get("data", {}).get("job_id")
    user = linked_user(chat_id)
    if not user or not job_id:
        clear_state(chat_id)
        welcome(chat_id)
        return
    apps = secure_storage.load_applications() or {}
    uid = str(user["id"])
    if any(str(a.get("jobId")) == str(job_id) for a in apps.get(uid, [])):
        clear_state(chat_id)
        send(chat_id, "ℹ️ لقد تقدمت مسبقاً لهذه الوظيفة.", [[btn("⬅️ الوظائف", "jobs")]])
        return
    free_used = int(user.get("sadaqahFreeApplicationsUsed", 0) or 0)
    fee = usd_cents(secure_storage, "application_usd")
    if free_used < APPLICATION_SADAQAH_FREE_LIMIT:
        charged = False
    else:
        charged = True
        wallet = get_wallet_balance(uid)
        available = float((wallet.get("balance") or {}).get("available", 0) or 0)
        if available < fee:
            price = service_prices(secure_storage, user)["prices"]["application_usd"]
            send(chat_id, f"💳 انتهت التقديمات المجانية. رسوم التقديم {price['formatted']}. رصيدك غير كافٍ.", [[btn("💳 محفظتي","wallet"),btn("🔎 الوظائف","jobs")]])
            return
        debit = subtract_balance(uid, fee, "application", reference_id=f"application_{job_id}_{uid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}", description="رسوم تقديم على وظيفة")
        if not debit.get("success"):
            send(chat_id, "❌ تعذر خصم رسوم التقديم من المحفظة.")
            return
    apps.setdefault(uid, []).append({
        "jobId": job_id,
        "appliedAt": datetime.now().isoformat(),
        "status": "pending",
    })
    if not secure_storage.save_applications(apps):
        send(chat_id, "❌ تعذر حفظ طلب التوظيف.")
        return
    if free_used < APPLICATION_SADAQAH_FREE_LIMIT:
        users=secure_storage.load_users() or []
        for u in users:
            if str(u.get("id"))==uid:
                u["sadaqahFreeApplicationsUsed"]=free_used+1
                break
        secure_storage.save_users(users)

    # Keep the existing site's employer notification behavior.
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get("id")) == str(job_id)), {})
    employer_email = job.get("employerEmail", "")
    if not employer_email and job.get("employerId"):
        employer = next(
            (u for u in (secure_storage.load_users() or [])
             if str(u.get("id")) == str(job.get("employerId"))),
            None,
        )
        employer_email = employer.get("email", "") if employer else ""
    if employer_email:
        try:
            send_email(
                employer_email,
                "طلب توظيف جديد - منصة التوظيف",
                f"لديك طلب توظيف جديد على وظيفة: {job.get('title','')}\\n"
                f"المتقدم: {user.get('firstName','')} {user.get('lastName','')}\\n"
                f"البريد: {user.get('email','')}\\n"
                f"الموقع: {user.get('neighborhood','')}، {user.get('city','')}، {user.get('country','')}\\n\\n"
                "يرجى الدخول إلى بوابة صاحب العمل لمراجعة الطلب.",
            )
        except Exception:
            LOG.exception("Could not send employer application email")

    clear_state(chat_id)
    send(chat_id, "✅ تم تقديم طلبك بنجاح.\n\nيمكنك متابعة حالته من «📋 طلباتي».", main_menu(user))


def show_applications(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("تسجيل الدخول", "login")]])
        return
    apps = secure_storage.load_applications() or {}
    jobs = secure_storage.load_jobs() or []
    items = apps.get(str(user["id"]), [])
    if not items:
        send(chat_id, "📋 لا توجد طلبات توظيف حتى الآن.", [[btn("🔎 تصفح الوظائف", "jobs"), btn("⬅️ الرئيسية", "home")]])
        return
    lines = ["📋 طلباتي\n"]
    for a in items[-20:]:
        job = next((j for j in jobs if str(j.get("id")) == str(a.get("jobId"))), {})
        status = {
            "pending": "⏳ قيد المراجعة",
            "review": "🔎 قيد الدراسة",
            "accepted": "✅ مقبول",
            "rejected": "❌ مرفوض",
            "withdrawn": "↩️ مسحوب",
        }.get(a.get("status"), a.get("status", "غير معروف"))
        lines.append(f"• {job.get('title','وظيفة')} — {status}")
    send(chat_id, "\n".join(lines), [[btn("⬅️ الرئيسية", "home")]])


def show_favorites(chat_id):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("تسجيل الدخول", "login")]])
        return
    favs = secure_storage.load_favorites() or {}
    ids = {str(x) for x in favs.get(str(user["id"]), [])}
    jobs = [j for j in (secure_storage.load_jobs() or []) if str(j.get("id")) in ids]
    if not jobs:
        send(chat_id, "❤️ لا توجد وظائف محفوظة.", [[btn("🔎 تصفح الوظائف", "jobs"), btn("⬅️ الرئيسية", "home")]])
        return
    keyboard = [[btn(f"💼 {str(j.get('title','وظيفة'))[:50]}", f"job:{j.get('id')}")] for j in jobs[:30]]
    keyboard.append([btn("⬅️ الرئيسية", "home")])
    send(chat_id, "❤️ الوظائف المحفوظة:", keyboard)


def edit_field_menu(chat_id, field):
    if field == "category":
        send(chat_id, "اختر المجال الجديد:", category_keyboard("set_category"))
    elif field == "education":
        send(chat_id, "اختر المستوى التعليمي:", education_keyboard("set_education"))
    elif field == "country":
        send(chat_id, "اختر الدولة:", country_keyboard("set_country"))
    elif field == "phone":
        send(chat_id, "📱 أرسل رقم هاتفك من Telegram بالضغط على زر «مشاركة رقم الهاتف».", [
            [btn("⬅️ رجوع", "profile")]
        ])


def update_user(chat_id, changes):
    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 يجب تسجيل الدخول أولاً.")
        return None
    users = secure_storage.load_users() or []
    target = next((u for u in users if str(u.get("id")) == str(user.get("id"))), None)
    if not target:
        return None

    if "phone" in changes and str(changes.get("phone", "")).strip():
        other = phone_belongs_to_other_user(changes.get("phone"), target.get("id"))
        if other:
            send(chat_id, "❌ هذا الرقم مرتبط بحساب آخر على المنصة. استخدم رقمًا آخر.")
            return None

    target.update(changes)
    if not secure_storage.save_users(users):
        send(chat_id, "❌ تعذر حفظ التعديل.")
        return None
    return target




# ==========================
# V5.4 — Telegram Employer Dashboard
# ==========================
EMPLOYER_STATUS_LABELS = {
    "pending": "⏳ قيد المراجعة",
    "review": "🔎 قيد الدراسة",
    "accepted": "✅ مقبول",
    "rejected": "❌ مرفوض",
    "withdrawn": "↩️ مسحوب",
}


def employer_user(chat_id):
    user = linked_user(chat_id)
    if not user or normalize_role(user.get("role", "")) != "employer":
        send(chat_id, "⛔ هذه اللوحة مخصصة لأصحاب العمل.", [[btn("⬅️ الرئيسية", "home")]])
        return None
    if user.get("status") == "blocked":
        send(chat_id, "⛔ هذا الحساب محظور من الإدارة.", [[btn("⬅️ الرئيسية", "home")]])
        return None
    if not user.get("emailVerified", False):
        send(chat_id, "📧 يجب تأكيد البريد الإلكتروني أولاً.", [[btn("📩 تأكيد البريد", "verify_email")], [btn("⬅️ الرئيسية", "home")]])
        return None
    return user


def employer_jobs_for(user):
    uid = str(user.get("id"))
    return [j for j in (secure_storage.load_jobs() or []) if str(j.get("employerId", "")) == uid]


def employer_applications_for(user):
    jobs = employer_jobs_for(user)
    job_map = {str(j.get("id")): j for j in jobs}
    applications = secure_storage.load_applications() or {}
    users = secure_storage.load_users() or []
    users_map = {str(u.get("id")): u for u in users}
    result = []
    for uid, items in applications.items():
        candidate = users_map.get(str(uid))
        for item in items or []:
            jid = str(item.get("jobId", ""))
            if jid not in job_map:
                continue
            job = job_map[jid]
            result.append({
                "userId": str(uid),
                "jobId": jid,
                "job": job,
                "candidate": candidate or {},
                "application": item,
            })
    result.sort(key=lambda x: str(x["application"].get("appliedAt", "")), reverse=True)
    return result


def employer_dashboard(chat_id):
    user = employer_user(chat_id)
    if not user:
        return
    jobs = employer_jobs_for(user)
    applications = employer_applications_for(user)
    pending = sum(1 for x in applications if x["application"].get("status", "pending") in ("pending", "review"))
    published = sum(1 for j in jobs if str(j.get("status", "published")) == "published")
    text = (
        "🏢 لوحة صاحب العمل\n\n"
        f"👤 {user.get('firstName','')} {user.get('lastName','')}\n"
        f"🏢 {user.get('companyName','') or 'نشاط غير محدد'}\n\n"
        f"📢 الوظائف المنشورة: {published}\n"
        f"📋 إجمالي طلبات التوظيف: {len(applications)}\n"
        f"⏳ طلبات تحتاج مراجعة: {pending}\n\n"
        "اختر ما تريد:"
    )
    send(chat_id, text, [
        [btn("➕ نشر وظيفة", "employer_post"), btn("📢 وظائفِي", "employer_jobs")],
        [btn("👥 المتقدمون", "employer_apps")],
        [btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")],
    ])


def employer_jobs_menu(chat_id):
    user = employer_user(chat_id)
    if not user:
        return
    jobs = employer_jobs_for(user)
    if not jobs:
        send(chat_id, "📢 لا توجد وظائف منشورة من حسابك حتى الآن.", [
            [btn("➕ نشر أول وظيفة", "employer_post")],
            [btn("⬅️ لوحة صاحب العمل", "employer")],
        ])
        return
    keyboard = []
    for job in jobs[:40]:
        title = str(job.get("title", "وظيفة"))[:45]
        keyboard.append([btn(f"💼 {title}", f"emp_job:{job.get('id')}")])
    keyboard.append([btn("➕ نشر وظيفة جديدة", "employer_post")])
    keyboard.append([btn("⬅️ لوحة صاحب العمل", "employer")])
    send(chat_id, f"📢 وظائفِي\n\nلديك {len(jobs)} وظيفة. اختر وظيفة لإدارتها:", keyboard)


def employer_job_details(chat_id, job_id):
    user = employer_user(chat_id)
    if not user:
        return
    job = next((j for j in employer_jobs_for(user) if str(j.get("id")) == str(job_id)), None)
    if not job:
        send(chat_id, "❌ الوظيفة غير موجودة أو لا تملك صلاحية إدارتها.", [[btn("📢 وظائفِي", "employer_jobs")]])
        return
    apps = [x for x in employer_applications_for(user) if str(x["jobId"]) == str(job_id)]
    text = (
        f"💼 {job.get('title','')}\n\n"
        f"🏢 {job.get('company','')}\n"
        f"📍 {job.get('neighborhood','')}، {job.get('city','')}، {job.get('country','')}\n"
        f"💼 المجال: {job.get('category','غير محدد')}\n"
        f"💰 الراتب: {job.get('salary','غير محدد')}\n"
        f"🕐 نوع العمل: {job.get('employmentType', job.get('workType', 'دوام كامل'))}\n"
        f"📅 النشر: {job.get('posted','غير محدد')}\n"
        f"👥 عدد المتقدمين: {len(apps)}\n\n"
        f"📝 {str(job.get('description',''))[:1800]}"
    )
    send(chat_id, text, [
        [btn("👥 المتقدمون", f"emp_job_apps:{job.get('id')}"), btn("✏️ تعديل", f"emp_edit:{job.get('id')}")],
        [btn("🗑️ حذف الوظيفة", f"emp_delete:{job.get('id')}")],
        [btn("⬅️ وظائفِي", "employer_jobs"), btn("🏢 اللوحة", "employer")],
    ])


def employer_delete_job(chat_id, job_id):
    user = employer_user(chat_id)
    if not user:
        return
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get("id")) == str(job_id) and str(j.get("employerId", "")) == str(user.get("id"))), None)
    if not job:
        send(chat_id, "❌ الوظيفة غير موجودة.", [[btn("📢 وظائفِي", "employer_jobs")]])
        return
    send(chat_id, f"⚠️ هل أنت متأكد من حذف الوظيفة «{job.get('title','')}»؟\n\nسيتم حذفها من قائمة الوظائف.", [
        [btn("✅ نعم، احذفها", f"emp_delete_confirm:{job_id}"), btn("❌ إلغاء", f"emp_job:{job_id}")],
    ])


def employer_delete_job_confirm(chat_id, job_id):
    user = employer_user(chat_id)
    if not user:
        return
    jobs = secure_storage.load_jobs() or []
    target = next((j for j in jobs if str(j.get("id")) == str(job_id) and str(j.get("employerId", "")) == str(user.get("id"))), None)
    if not target:
        send(chat_id, "❌ الوظيفة غير موجودة.", [[btn("📢 وظائفِي", "employer_jobs")]])
        return
    jobs = [j for j in jobs if j is not target]
    if not secure_storage.save_jobs(jobs):
        send(chat_id, "❌ تعذر حذف الوظيفة من الخادم.", [[btn("⬅️ الوظيفة", f"emp_job:{job_id}")]])
        return
    send(chat_id, "✅ تم حذف الوظيفة بنجاح.", [[btn("📢 وظائفِي", "employer_jobs"), btn("🏢 اللوحة", "employer")]])


def employer_start_post(chat_id, edit_job_id=None):
    user = employer_user(chat_id)
    if not user:
        return
    data = {"company": user.get("companyName", "") or f"{user.get('firstName','')} {user.get('lastName','')}"}
    if edit_job_id is not None:
        job = next((j for j in employer_jobs_for(user) if str(j.get("id")) == str(edit_job_id)), None)
        if not job:
            send(chat_id, "❌ الوظيفة غير موجودة.", [[btn("📢 وظائفِي", "employer_jobs")]])
            return
        data.update({
            "title": job.get("title", ""), "company": job.get("company", data["company"]),
            "country": job.get("country", ""), "city": job.get("city", ""),
            "neighborhood": job.get("neighborhood", ""), "category": job.get("category", ""),
            "salary": job.get("salary", "غير محدد"),
            "employmentType": job.get("employmentType", job.get("workType", "دوام كامل")),
            "description": job.get("description", ""), "edit_job_id": str(edit_job_id),
        })
    set_state(chat_id, flow="employer_post", step="title", data=data)
    prompt = "✏️ تعديل الوظيفة\n\n" if edit_job_id is not None else "➕ نشر وظيفة جديدة\n\n"
    send(chat_id, prompt + "💼 اكتب المسمى الوظيفي:")


def employer_post_review(chat_id):
    st = get_state(chat_id)
    d = st.get("data", {})
    lines = [
        "📋 مراجعة بيانات الوظيفة", "",
        f"💼 المسمى: {d.get('title','')}",
        f"🏢 الشركة/النشاط: {d.get('company','')}",
        f"📍 الموقع: {d.get('neighborhood','')}، {d.get('city','')}، {d.get('country','')}",
        f"🗂️ المجال: {d.get('category','')}",
        f"💰 الراتب: {d.get('salary','غير محدد')}",
        f"🕐 نوع العمل: {d.get('employmentType','دوام كامل')}",
        f"📝 الوصف: {d.get('description','') or 'بدون وصف'}",
        "", "هل البيانات صحيحة؟",
    ]
    st["step"] = "review"
    STATE[str(chat_id)] = st
    save_states(STATE)
    send(chat_id, "\n".join(lines), [
        [btn("✅ نشر الوظيفة", "emp_post_confirm") if not d.get("edit_job_id") else btn("✅ حفظ التعديلات", "emp_post_confirm")],
        [btn("✏️ إعادة الإدخال", "emp_post_restart"), btn("❌ إلغاء", "employer")],
    ])


def employer_save_post(chat_id):
    user = employer_user(chat_id)
    if not user:
        return
    st = get_state(chat_id)
    d = st.get("data", {})
    title = sanitize_input(str(d.get("title", "")).strip())
    if not title:
        st["step"] = "title"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "❌ المسمى الوظيفي مطلوب. اكتب المسمى:")
        return
    jobs = secure_storage.load_jobs() or []
    edit_id = d.get("edit_job_id")
    if edit_id:
        target = next((j for j in jobs if str(j.get("id")) == str(edit_id) and str(j.get("employerId", "")) == str(user.get("id"))), None)
        if not target:
            clear_state(chat_id)
            send(chat_id, "❌ الوظيفة غير موجودة أو لا تملك صلاحية تعديلها.", [[btn("📢 وظائفِي", "employer_jobs")]])
            return
        for key in ("title", "company", "country", "city", "neighborhood", "category", "salary", "employmentType", "description"):
            target[key] = sanitize_input(str(d.get(key, target.get(key, ""))))
        if not secure_storage.save_jobs(jobs):
            send(chat_id, "❌ تعذر حفظ تعديلات الوظيفة.")
            return
        clear_state(chat_id)
        send(chat_id, "✅ تم تحديث الوظيفة بنجاح.", [[btn("💼 عرض الوظيفة", f"emp_job:{target.get('id')}"), btn("🏢 اللوحة", "employer")]])
        return

    # الفوترة المشتركة مع الخادم: أول 3 عمليات نشر مجانية، ثم خصم USD من المحفظة.
    used = int(user.get("sadaqahFreeJobPostsUsed", 0) or 0)
    fee = usd_cents(secure_storage, "job_posting_usd")
    if used >= JOB_POSTING_SADAQAH_FREE_LIMIT:
        wallet = get_wallet_balance(str(user.get("id")))
        available = float((wallet.get("balance") or {}).get("available", 0) or 0)
        if available < fee:
            price = service_prices(secure_storage, user)["prices"]["job_posting_usd"]
            send(chat_id, f"💳 انتهت الوظائف المجانية. رسوم نشر الوظيفة {price['formatted']} ورصيدك غير كافٍ.", [[btn("💳 محفظتي","wallet"),btn("🏢 اللوحة","employer")]])
            return
        debit = subtract_balance(str(user.get("id")), fee, "job_posting", reference_id=f"job_posting_{user.get('id')}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}", description="رسوم نشر وظيفة")
        if not debit.get("success"):
            send(chat_id,"❌ تعذر خصم رسوم نشر الوظيفة من المحفظة.")
            return
    else:
        # يتم طلب وقفة الخير مرة واحدة قبل النشر المجاني.
        if not st.get("data",{}).get("sadaqahApproved"):
            st["data"]["sadaqahApproved"]=True
            STATE[str(chat_id)]=st; save_states(STATE)
            _telegram_sadaqah_pause(chat_id,"post_job","emp_post_after_pause")
            return

    numeric_ids = []
    for j in jobs:
        try:
            numeric_ids.append(int(j.get("id")))
        except Exception:
            pass
    new_id = (max(numeric_ids) + 1) if numeric_ids else 1
    new_job = {
        "id": new_id,
        "title": title,
        "company": sanitize_input(str(d.get("company") or user.get("companyName") or f"{user.get('firstName','')} {user.get('lastName','')}")),
        "country": sanitize_input(str(d.get("country", ""))),
        "city": sanitize_input(str(d.get("city", ""))),
        "neighborhood": sanitize_input(str(d.get("neighborhood", ""))),
        "category": sanitize_input(str(d.get("category", ""))),
        "salary": sanitize_input(str(d.get("salary", "غير محدد"))),
        "employmentType": sanitize_input(str(d.get("employmentType", "دوام كامل"))),
        "description": sanitize_input(str(d.get("description", ""))),
        "tags": [],
        "employerId": user.get("id"),
        "employerEmail": user.get("email", ""),
        "posted": datetime.now().strftime("%Y-%m-%d"),
        "status": "published",
    }
    jobs.append(new_job)
    if not secure_storage.save_jobs(jobs):
        # إذا كانت العملية مدفوعة، أعد المبلغ عند فشل الحفظ.
        if used >= JOB_POSTING_SADAQAH_FREE_LIMIT:
            add_balance(str(user.get("id")), fee, "bonus", reference_id=f"job_posting_refund_{new_id}", description="إرجاع رسوم نشر الوظيفة بسبب فشل الحفظ")
        send(chat_id, "❌ تعذر نشر الوظيفة على الخادم. حاول لاحقاً.")
        return
    if used < JOB_POSTING_SADAQAH_FREE_LIMIT:
        users=secure_storage.load_users() or []
        for u in users:
            if str(u.get("id"))==str(user.get("id")):
                u["sadaqahFreeJobPostsUsed"]=used+1
                break
        secure_storage.save_users(users)
    clear_state(chat_id)
    send(chat_id, "✅ تم نشر الوظيفة بنجاح!\n\nيمكن للباحثين عن عمل رؤيتها الآن والتقديم عليها.", [
        [btn("💼 عرض الوظيفة", f"emp_job:{new_id}")],
        [btn("➕ نشر وظيفة أخرى", "employer_post"), btn("🏢 اللوحة", "employer")],
    ])


def employer_apps_menu(chat_id, job_id=None):
    user = employer_user(chat_id)
    if not user:
        return
    items = employer_applications_for(user)
    if job_id is not None:
        items = [x for x in items if str(x["jobId"]) == str(job_id)]
    if not items:
        send(chat_id, "👥 لا توجد طلبات توظيف لهذه الوظيفة حالياً." if job_id is not None else "👥 لا توجد طلبات توظيف على وظائفك حتى الآن.", [
            [btn("📢 وظائفِي", "employer_jobs"), btn("🏢 اللوحة", "employer")]
        ])
        return
    keyboard = []
    for item in items[:40]:
        candidate = item["candidate"]
        name = f"{candidate.get('firstName','')} {candidate.get('lastName','')}".strip() or "متقدم"
        title = str(item["job"].get("title", "وظيفة"))[:22]
        status = EMPLOYER_STATUS_LABELS.get(item["application"].get("status", "pending"), "⏳")
        label = f"👤 {name[:25]} — {title} {status[:2]}"
        keyboard.append([btn(label[:55], f"emp_app:{item['jobId']}:{item['userId']}")])
    if job_id is not None:
        keyboard.append([btn("⬅️ الوظيفة", f"emp_job:{job_id}")])
    keyboard.append([btn("⬅️ لوحة صاحب العمل", "employer")])
    send(chat_id, f"👥 المتقدمون\n\nعدد الطلبات: {len(items)}\nاختر متقدماً:", keyboard)


def employer_app_details(chat_id, job_id, user_id):
    user = employer_user(chat_id)
    if not user:
        return
    item = next((x for x in employer_applications_for(user) if str(x["jobId"]) == str(job_id) and str(x["userId"]) == str(user_id)), None)
    if not item:
        send(chat_id, "❌ الطلب غير موجود أو لا تملك صلاحية الوصول إليه.", [[btn("👥 المتقدمون", "employer_apps")]])
        return
    candidate = item["candidate"]
    app_item = item["application"]
    job = item["job"]
    name = f"{candidate.get('firstName','')} {candidate.get('lastName','')}".strip() or "متقدم"
    status = EMPLOYER_STATUS_LABELS.get(app_item.get("status", "pending"), "⏳ قيد المراجعة")
    text = (
        "👤 بيانات المتقدم\n\n"
        f"الاسم: {name}\n"
        f"📍 الموقع: {candidate.get('neighborhood','')}، {candidate.get('city','')}، {candidate.get('country','')}\n"
        f"💼 المجال: {candidate.get('category','') or 'غير محدد'}\n"
        f"🎓 التعليم: {candidate.get('education','') or 'غير محدد'}\n"
        f"💼 الوظيفة: {job.get('title','')}\n"
        f"📌 الحالة: {status}\n"
        f"📅 التقديم: {app_item.get('appliedAt','غير محدد')}\n\n"
        "🔒 بيانات التواصل مخفية حتى تطلب فتحها."
    )
    send(chat_id, text, [
        [btn("📞 فتح بيانات التواصل", f"emp_contact:{job_id}:{user_id}"), btn("🏢 مشاركة بيانات الشركة", f"emp_share:{job_id}:{user_id}")],
        [btn("🔎 قيد الدراسة", f"emp_status:{job_id}:{user_id}:review"), btn("✅ قبول", f"emp_status:{job_id}:{user_id}:accepted")],
        [btn("❌ رفض", f"emp_status:{job_id}:{user_id}:rejected"), btn("⏳ قيد المراجعة", f"emp_status:{job_id}:{user_id}:pending")],
        [btn("⬅️ المتقدمون", f"emp_job_apps:{job_id}")],
    ])


def employer_contact(chat_id, job_id, user_id):
    user = employer_user(chat_id)
    if not user:
        return
    item = next((x for x in employer_applications_for(user) if str(x["jobId"]) == str(job_id) and str(x["userId"]) == str(user_id)), None)
    if not item:
        send(chat_id, "❌ الطلب غير موجود.", [[btn("👥 المتقدمون", "employer_apps")]])
        return
    candidate = item["candidate"]
    app_item = item["application"]
    if not app_item.get("contactUnlocked"):
        used = int(user.get("sadaqahFreeUnlocksUsed", 0) or 0)
        fee = usd_cents(secure_storage, "contact_unlock_usd")
        if used < CONTACT_SADAQAH_FREE_LIMIT:
            if not get_state(chat_id).get("data",{}).get("contactApproved"):
                set_state(chat_id, flow="sadaqah_pause", step="wait", data={
                    "action":"contact_unlock", "job_id":str(job_id), "user_id":str(user_id),
                    "startedAt":datetime.now().isoformat()
                })
                _telegram_sadaqah_pause(chat_id,"contact_unlock","emp_contact_after_pause")
                return
        else:
            wallet=get_wallet_balance(str(user.get("id")))
            available=float((wallet.get("balance") or {}).get("available",0) or 0)
            if available < fee:
                price=service_prices(secure_storage,user)["prices"]["contact_unlock_usd"]
                send(chat_id,f"💳 انتهت فتح البيانات المجانية. الرسوم {price['formatted']} ورصيدك غير كافٍ.",[[btn("💳 محفظتي","wallet"),btn("👥 المتقدمون","employer_apps")]])
                return
            debit=subtract_balance(str(user.get("id")),fee,"contact_unlock",reference_id=f"contact_unlock_{job_id}_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",description="رسوم فتح بيانات المتقدم")
            if not debit.get("success"):
                send(chat_id,"❌ تعذر خصم رسوم فتح بيانات المتقدم.")
                return
        # تسجيل فتح البيانات في نفس سجل الطلب المستخدم من الخادم.
        applications=secure_storage.load_applications() or {}
        for a in applications.get(str(user_id),[]):
            if str(a.get("jobId"))==str(job_id):
                a["contactUnlocked"]=True
                break
        secure_storage.save_applications(applications)
        if used < CONTACT_SADAQAH_FREE_LIMIT:
            users=secure_storage.load_users() or []
            for u in users:
                if str(u.get("id"))==str(user.get("id")):
                    u["sadaqahFreeUnlocksUsed"]=used+1
                    break
            secure_storage.save_users(users)

    text = (
        "🔓 بيانات التواصل\n\n"
        f"👤 {candidate.get('firstName','')} {candidate.get('lastName','')}\n"
        f"📧 البريد: {candidate.get('email','') or 'غير مضاف'}\n"
        f"📱 الهاتف: {candidate.get('phone','') or 'غير مضاف'}"
    )
    send(chat_id, text, [[btn("⬅️ بيانات المتقدم", f"emp_app:{job_id}:{user_id}"), btn("👥 المتقدمون", f"emp_job_apps:{job_id}")]])


def employer_share_company(chat_id, job_id, user_id):
    user = employer_user(chat_id)
    if not user:
        return
    jobs = employer_jobs_for(user)
    if not any(str(j.get("id")) == str(job_id) for j in jobs):
        send(chat_id, "⛔ غير مصرح لك بمشاركة بيانات هذه الوظيفة.", [[btn("⬅️ المتقدمون", f"emp_job_apps:{job_id}")]])
        return
    applications = secure_storage.load_applications() or {}
    entry = next((a for a in applications.get(str(user_id), []) if str(a.get("jobId")) == str(job_id)), None)
    if not entry:
        send(chat_id, "❌ الطلب غير موجود.", [[btn("👥 المتقدمون", f"emp_job_apps:{job_id}")]])
        return
    entry["companyDataShared"] = True
    entry["companyDataSharedAt"] = datetime.now().isoformat()
    if not secure_storage.save_applications(applications):
        send(chat_id, "❌ تعذر حفظ مشاركة بيانات الشركة.")
        return
    telegram_audit(chat_id, f"مشاركة بيانات الشركة مع متقدم: job={job_id}")
    send(chat_id, "✅ تمت مشاركة بيانات المنشأة مع المتقدم بنجاح.", [[btn("👤 بيانات المتقدم", f"emp_app:{job_id}:{user_id}"), btn("👥 المتقدمون", f"emp_job_apps:{job_id}")]])


def employer_update_application_status(chat_id, job_id, user_id, status):
    user = employer_user(chat_id)
    if not user:
        return
    allowed = {"pending", "review", "accepted", "rejected"}
    if status not in allowed:
        send(chat_id, "❌ الحالة غير صحيحة.")
        return
    jobs = employer_jobs_for(user)
    if not any(str(j.get("id")) == str(job_id) for j in jobs):
        send(chat_id, "⛔ غير مصرح لك بتعديل هذا الطلب.")
        return
    applications = secure_storage.load_applications() or {}
    found = False
    for item in applications.get(str(user_id), []):
        if str(item.get("jobId")) == str(job_id):
            item["status"] = status
            item["updatedAt"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        send(chat_id, "❌ الطلب غير موجود.")
        return
    if not secure_storage.save_applications(applications):
        send(chat_id, "❌ تعذر حفظ حالة الطلب.")
        return
    send(chat_id, f"✅ تم تحديث حالة المتقدم إلى: {EMPLOYER_STATUS_LABELS.get(status, status)}", [[btn("👤 بيانات المتقدم", f"emp_app:{job_id}:{user_id}"), btn("👥 المتقدمون", f"emp_job_apps:{job_id}")]])


def handle_text(chat_id, text):
    text = (text or "").strip()
    st = get_state(chat_id)
    step = st.get("step")
    d = st.get("data", {})

    if step == "reset_code":
        verify_password_reset_code(chat_id, text)
        return

    if step == "otp":
        verify_phone_otp(chat_id, text)
        return

    if st.get("flow") == "forgot_password" and step == "email":
        value = text.strip().lower()
        if not validate_email(value):
            send(chat_id, "❌ البريد الإلكتروني غير صحيح. أرسله مرة أخرى.")
            return
        send_password_reset_code(chat_id, value)
        return

    if st.get("flow") == "forgot_password" and step == "new_password":
        ok, msg = validate_password(text)
        if not ok:
            send(chat_id, f"❌ {msg}\nأرسل كلمة مرور أخرى.")
            return
        secret_for(chat_id)["reset_password"] = text
        st["step"] = "confirm_password"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "🔐 أعد كتابة كلمة المرور الجديدة للتأكيد:")
        return

    if st.get("flow") == "forgot_password" and step == "confirm_password":
        if text != secret_for(chat_id).get("reset_password", ""):
            send(chat_id, "❌ كلمتا المرور غير متطابقتين. أعد المحاولة.")
            return
        finish_password_reset(chat_id)
        return

    if st.get("flow") == "pin_setup" and step == "pin":
        if not re.fullmatch(r"\d{6}", text):
            send(chat_id, "❌ يجب أن يتكون PIN من 6 أرقام بالضبط.")
            return
        if text in {"123456", "000000", "111111", "222222", "333333", "444444", "555555", "666666", "777777", "888888", "999999"}:
            send(chat_id, "❌ PIN سهل التخمين. اختر 6 أرقام مختلفة أو غير متسلسلة.")
            return
        secret_for(chat_id)["pin_setup"] = text
        st["step"] = "confirm_pin"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "🔢 أعد إدخال PIN للتأكيد:")
        return

    if st.get("flow") == "pin_setup" and step == "confirm_pin":
        first = secret_for(chat_id).get("pin_setup", "")
        if text != first:
            send(chat_id, "❌ PIN غير متطابق. أعد إدخاله.")
            return
        if not set_telegram_pin(chat_id, text):
            send(chat_id, "❌ تعذر حفظ PIN بشكل آمن. حاول لاحقاً.")
            return
        secret_for(chat_id).pop("pin_setup", None)
        clear_state(chat_id)
        telegram_audit(chat_id, "تم إنشاء PIN آمن للدخول السريع")
        send(chat_id, "✅ تم إنشاء PIN بنجاح. يمكنك استخدام «دخول سريع بـ PIN» لاحقاً.", main_menu(linked_user(chat_id)))
        return

    if st.get("flow") == "pin_login" and step == "pin":
        if not re.fullmatch(r"\d{6}", text):
            send(chat_id, "❌ PIN يجب أن يتكون من 6 أرقام.")
            return
        finish_pin_login(chat_id, text)
        return

    if step == "role":
        role_map = {
            "باحث عن عمل": "job_seeker", "باحث": "job_seeker", "job seeker": "job_seeker",
            "صاحب عمل": "employer", "صاحب": "employer", "employer": "employer",
        }
        role = role_map.get(text.strip().lower())
        if role:
            st.setdefault("data", {})["role"] = role
            st["step"] = "country" if st["data"].get("lastName") else "lastName"
            STATE[str(chat_id)] = st
            save_states(STATE)
            if st["step"] == "lastName":
                send(chat_id, "👤 اكتب اسم العائلة:")
            else:
                send(chat_id, "🌍 اختر الدولة:", country_keyboard())
        else:
            send(chat_id, "👤 اختر «باحث عن عمل» أو «صاحب عمل» من الأزرار، أو اكتب أحدهما.", [
                [btn("🙋 باحث عن عمل", "reg_role:job_seeker")],
                [btn("🏢 صاحب عمل", "reg_role:employer")],
            ])
        return

    if st.get("flow") == "employer_profile" and step in ("companyName", "companyType", "companyDescription"):
        user = linked_user(chat_id)
        if not user or normalize_role(user.get("role", "")) != "employer":
            clear_state(chat_id)
            send(chat_id, "⛔ هذه الخاصية مخصصة لصاحب العمل.", [[btn("⬅️ الرئيسية", "home")]])
            return
        value = sanitize_input(text)
        field_map = {"companyName": "companyName", "companyType": "companyType", "companyDescription": "companyDescription"}
        update_user(chat_id, {field_map[step]: value})
        telegram_audit(chat_id, f"تعديل ملف صاحب العمل: {field_map[step]}")
        clear_state(chat_id)
        show_profile(chat_id)
        return

    if step == "education":
        education_map = {x.strip().lower(): x for x in EDUCATION}
        education = education_map.get(text.strip().lower())
        if education:
            st.setdefault("data", {})["education"] = education
            st["step"] = "email"
            STATE[str(chat_id)] = st
            save_states(STATE)
            send(chat_id, "📧 اكتب بريدك الإلكتروني:")
        else:
            send(chat_id, "🎓 اختر المستوى التعليمي من الأزرار، أو اكتب اسمه كما هو.", education_keyboard())
        return

    # Robust login fallback: if the user sends an email while no state is
    # available, treat it as the login email instead of sending the generic
    # "use the buttons" message.
    if not step and "@" in text and "." in text:
        set_state(chat_id, flow="login", step="email", data={})
        st = get_state(chat_id)
        step = st.get("step")
        d = st.get("data", {})

    if step == "verification_code":
        bot_verify_email_code(chat_id, text)
        return

    if step in ("email", "password", "firstName", "lastName", "companyName", "companyDescription"):
        value = text.strip()
        if not value:
            send(chat_id, "⚠️ أرسل قيمة صحيحة.")
            return
        if step != "password":
            d[step] = value
        st["data"] = d

        if step == "email":
            if not validate_email(value):
                send(chat_id, "❌ البريد الإلكتروني غير صحيح. أرسله مرة أخرى.")
                return
            st["step"] = "password"
            send(chat_id, "🔑 أرسل كلمة المرور.\n\nيجب أن تكون 8 أحرف على الأقل وتحتوي على حرف كبير وصغير ورقم ورمز خاص.")
        elif step == "password":
            ok, msg = validate_password(value)
            if not ok:
                send(chat_id, f"❌ {msg}\nأرسل كلمة مرور أخرى.")
                return
            if st.get("flow") == "login":
                secret_for(chat_id)["login_password"] = value
                finish_login(chat_id)
            else:
                secret_for(chat_id)["registration_password"] = value
                st["step"] = "phone"
                STATE[str(chat_id)] = st
                save_states(STATE)
                send(chat_id, "📱 رقم الهاتف\n\nاضغط زر «مشاركة رقم الهاتف» لإرسال رقم هاتفك لحسابك. لن يتم عرضه علنًا ويُستخدم للتواصل والتحقق من الحساب.", [
                    [{"text": "📱 مشاركة رقم الهاتف", "request_contact": True}],
                    [{"text": "⬅️ إلغاء"}],
                ], reply_keyboard=True, one_time_keyboard=True)
        elif step == "firstName":
            st["step"] = "lastName"
            send(chat_id, "👤 اكتب اسم العائلة:")
        elif step == "lastName":
            st["step"] = "country"
            send(chat_id, "🌍 اختر الدولة:", country_keyboard())
        elif step == "companyName":
            st["step"] = "companyDescription"
            send(chat_id, "📝 اكتب وصفاً مختصراً عن الشركة أو النشاط:")
        elif step == "companyDescription":
            st["step"] = "email"
            send(chat_id, "📧 اكتب بريدك الإلكتروني:")
        STATE[str(chat_id)] = st
        save_states(STATE)
        return

    if st.get("flow") == "employer_post":
        value = text.strip()
        if step == "title":
            if not value:
                send(chat_id, "⚠️ اكتب المسمى الوظيفي.")
                return
            d["title"] = sanitize_input(value)
            st["step"] = "country"
            st["data"] = d
            STATE[str(chat_id)] = st
            save_states(STATE)
            send(chat_id, "🌍 اختر الدولة:", country_keyboard("emp_country"))
            return
        if step == "salary":
            d["salary"] = sanitize_input(value) or "غير محدد"
            st["step"] = "employmentType"
            st["data"] = d
            STATE[str(chat_id)] = st
            save_states(STATE)
            send(chat_id, "🕐 اختر نوع العمل:", rows([btn(x, f"emp_type:{x}") for x in WORK_TYPES], 2))
            return
        if step == "description":
            d["description"] = sanitize_input(value)
            st["data"] = d
            STATE[str(chat_id)] = st
            save_states(STATE)
            employer_post_review(chat_id)
            return

    # If the user sends a normal text while no flow is active, guide them back
    # instead of entering a confusing login loop.
    send(chat_id, "اختر أحد الأزرار من القائمة للمتابعة.", main_menu(linked_user(chat_id)))


def handle_contact(chat_id, contact, sender_id=None):
    phone = contact.get("phone_number", "")
    st = get_state(chat_id)
    if st.get("flow") == "phone_login":
        # Telegram contact sharing must belong to the sender.
        contact_user_id = contact.get("user_id")
        if contact_user_id is not None and sender_id is not None and str(contact_user_id) != str(sender_id):
            send(chat_id, "❌ يجب مشاركة رقم هاتفك أنت باستخدام زر Telegram المخصص.", [[btn("📱 دخول بالهاتف", "phone_login")]])
            return

        # Ignore duplicate contacts while an OTP is already pending.
        if st.get("step") == "otp":
            send(chat_id, "📩 تم إرسال رمز الدخول بالفعل إلى بريدك الإلكتروني. أدخل الرمز المرسل إليك.")
            return

        user = find_user_by_phone(phone)
        # Do not reveal whether a phone number exists in the database.
        if not user:
            clear_state(chat_id)
            send(chat_id, "❌ تعذر تسجيل الدخول بهذا الرقم. تأكد أن الرقم مضاف إلى حسابك في المنصة.", [[btn("📝 إنشاء حساب", "register"), btn("🔐 تسجيل الدخول", "login")]])
            return
        if user.get("status") == "blocked":
            clear_state(chat_id)
            send(chat_id, "⛔ لا يمكن تسجيل الدخول بهذا الحساب.", [[btn("⬅️ الرئيسية", "home")]])
            return
        start_phone_otp(chat_id, user)
        return

    # Registration phone collection via Telegram contact sharing.
    if st.get("flow") == "register" and st.get("step") == "phone":
        contact_user_id = contact.get("user_id")
        if contact_user_id is not None and sender_id is not None and str(contact_user_id) != str(sender_id):
            send(chat_id, "❌ يجب مشاركة رقم هاتفك أنت باستخدام زر Telegram المخصص.")
            return

        if not normalize_phone(phone):
            send(chat_id, "❌ تعذر قراءة رقم الهاتف. أعد مشاركته من زر Telegram.")
            return

        other = phone_belongs_to_other_user(phone)
        if other:
            send(chat_id, "❌ رقم الهاتف مرتبط بحساب آخر على المنصة. جرّب مشاركة رقم هاتف آخر.", remove_keyboard=True)
            send(chat_id, "📱 مشاركة رقم هاتف آخر؟", [
                [{"text": "📱 مشاركة رقم هاتف آخر", "request_contact": True}],
                [{"text": "⬅️ إلغاء"}],
            ], reply_keyboard=True, one_time_keyboard=True)
            return

        st["data"]["phone"] = phone
        st["data"]["phoneCountryCode"] = ""
        st["step"] = "review"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "✅ تم استلام رقم هاتفك.", remove_keyboard=True)
        show_registration_review(chat_id)
        return

    user = linked_user(chat_id)
    if not user:
        send(chat_id, "🔐 سجل الدخول أولاً.")
        return

    if not normalize_phone(phone):
        send(chat_id, "❌ تعذر قراءة رقم الهاتف. أعد مشاركته من زر Telegram.")
        return

    other = phone_belongs_to_other_user(phone, user.get("id"))
    if other:
        send(chat_id, "❌ هذا الرقم مرتبط بحساب آخر على المنصة. استخدم رقمًا آخر.", [
            [btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")]
        ])
        return

    update_user(chat_id, {"phone": phone, "phoneCountryCode": ""})
    send(chat_id, "✅ تم تحديث رقم الهاتف.", [
        [btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")]
    ])


def handle_callback(q):
    data = q.get("data", "")
    message = q.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    callback_id = q.get("id")
    if chat_id is None:
        return
    allowed, wait_seconds = rate_limit(chat_id)
    if not allowed:
        answer_callback(callback_id, "⏳ تمهل قليلاً")
        return
    answer_callback(callback_id)
    telegram_audit(chat_id, f"Telegram callback: {data}")

    if data == "home":
        clear_state(chat_id)
        welcome(chat_id)
        return
    if data == "register":
        registration_start(chat_id)
        return
    if data == "login":
        start_login(chat_id)
        return
    if data == "login_email":
        start_email_login(chat_id)
        return
    if data == "forgot_password":
        start_forgot_password(chat_id)
        return
    if data == "phone_login":
        start_phone_login(chat_id)
        return
    if data == "pin_login":
        start_pin_login(chat_id)
        return
    if data == "pin_setup":
        user = linked_user(chat_id)
        if not user:
            send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("🔐 تسجيل الدخول", "login")]])
            return
        start_pin_setup(chat_id, after_auth=False)
        return
    if data == "pin_skip":
        user = linked_user(chat_id)
        clear_state(chat_id)
        send(chat_id, "تم المتابعة بدون PIN.", main_menu(user))
        return
    if data == "phone_resend_otp":
        st = get_state(chat_id)
        if st.get("flow") != "phone_login" or st.get("step") != "otp":
            send(chat_id, "❌ لا توجد عملية دخول بالهاتف حالية.", [[btn("📱 دخول بالهاتف", "phone_login")]])
            return
        user_id = st.get("data", {}).get("user_id")
        user = next((u for u in (secure_storage.load_users() or []) if str(u.get("id")) == str(user_id)), None)
        if not user:
            clear_state(chat_id)
            send(chat_id, "❌ الحساب غير موجود.", [[btn("🔐 تسجيل الدخول", "login")]])
            return
        _, current = get_otp_record(PHONE_OTP_FILE, chat_id)
        if current and current.get("sent_at"):
            try:
                seconds = (datetime.now() - datetime.fromisoformat(str(current["sent_at"]))).total_seconds()
                if seconds < 60:
                    send(chat_id, f"⏳ انتظر {max(1, int(60 - seconds))} ثانية قبل إعادة إرسال الرمز.")
                    return
            except Exception:
                pass
        delete_otp_record(PHONE_OTP_FILE, chat_id)
        start_phone_otp(chat_id, user, force=True)
        return
    if data == "about":
        send(chat_id, "ℹ️ منصة توظيف عربية تساعد الباحثين عن عمل وأصحاب العمل على الوصول إلى الفرص وإدارتها بسهولة.", [[btn("⬅️ الرئيسية", "home")]])
        return
    if data == "logout":
        unlink_user(chat_id)
        clear_state(chat_id)
        send(chat_id, "🚪 تم تسجيل الخروج من Telegram.", [[btn("🔐 تسجيل الدخول", "login"), btn("📝 إنشاء حساب", "register")]])
        return
    if data == "wallet":
        clear_state(chat_id); telegram_wallet(chat_id); return
    if data == "wallet_packages":
        telegram_wallet_packages(chat_id); return 
    if data == "wallet_transactions":
        telegram_wallet_transactions(chat_id); return
    if data == "subscriptions":
        telegram_subscription_menu(chat_id); return
    if data == "invoices":
        telegram_invoices(chat_id); return
    if data.startswith("sub_buy:"):
        telegram_subscribe(chat_id, data.split(":", 1)[1]); return
    if data == "sadaqah_done":
        st=get_state(chat_id); started=st.get("data",{}).get("startedAt")
        try: elapsed=(datetime.now()-datetime.fromisoformat(str(started))).total_seconds()
        except Exception: elapsed=0
        if elapsed < 15:
            answer_callback(callback_id, f"⏳ بقي {max(1,int(15-elapsed))} ثانية")
            return
        action=st.get("data",{}).get("action")
        if action=="apply":
            _telegram_apply_after_pause(chat_id); return
        if action=="post_job":
            st["flow"]="employer_post"; st["step"]="review"; st["data"]["sadaqahApproved"]=True
            STATE[str(chat_id)]=st; save_states(STATE); employer_post_review(chat_id); return
        if action=="contact_unlock":
            # mark approval then execute the same contact operation
            job_id=st.get("data",{}).get("job_id"); user_id=st.get("data",{}).get("user_id")
            st["flow"]=""; data2=dict(st.get("data") or {}); data2["contactApproved"]=True
            st["data"]=data2; STATE[str(chat_id)]=st; save_states(STATE)
            employer_contact(chat_id,job_id,user_id); return
        clear_state(chat_id); welcome(chat_id); return
    if data == "apply_after_pause":
        return
    if data == "emp_post_after_pause":
        return
    if data == "emp_contact_after_pause":
        return
    if data == "profile":
        clear_state(chat_id)
        show_profile(chat_id)
        return
    if data == "account":
        show_profile(chat_id)
        return
    if data == "pin_change":
        if not linked_user(chat_id):
            send(chat_id, "🔐 سجل الدخول أولاً.")
            return
        delete_telegram_pin(chat_id)
        start_pin_setup(chat_id, after_auth=False)
        return
    if data == "pin_delete":
        if delete_telegram_pin(chat_id):
            telegram_audit(chat_id, "تم حذف PIN للدخول السريع")
            send(chat_id, "🗑️ تم حذف PIN. يمكنك تسجيل الدخول لاحقاً بالطريقة المعتادة.", [[btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")]])
        else:
            send(chat_id, "❌ تعذر حذف PIN. حاول لاحقاً.")
        return
    if data == "applications":
        show_applications(chat_id)
        return
    if data == "favorites":
        show_favorites(chat_id)
        return
    if data == "jobs":
        clear_state(chat_id)
        show_jobs(chat_id)
        return
    if data.startswith("jobs_page:"):
        show_jobs(chat_id, int(data.split(":", 1)[1]))
        return
    if data.startswith("job:"):
        show_job(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("apply:"):
        apply_job(chat_id, data.split(":", 1)[1])
        return
    if data == "apply_confirm_yes":
        confirm_apply(chat_id)
        return
    if data.startswith("fav:"):
        user = linked_user(chat_id)
        if not user:
            send(chat_id, "🔐 سجل الدخول أولاً.")
            return
        jid = data.split(":", 1)[1]
        favs = secure_storage.load_favorites() or {}
        favs.setdefault(str(user["id"]), [])
        if jid not in {str(x) for x in favs[str(user["id"])]}:
            favs[str(user["id"])].append(str(jid))
            secure_storage.save_favorites(favs)
            send(chat_id, "❤️ تمت إضافة الوظيفة إلى المفضلة.", [[btn("⬅️ الوظيفة", f"job:{jid}"), btn("❤️ المفضلة", "favorites")]])
        else:
            send(chat_id, "ℹ️ الوظيفة موجودة بالفعل في المفضلة.", [[btn("⬅️ الوظيفة", f"job:{jid}")]])
        return

    # Employer dashboard callbacks
    if data == "employer":
        employer_dashboard(chat_id)
        return
    if data == "employer_jobs":
        employer_jobs_menu(chat_id)
        return
    if data == "employer_post":
        employer_start_post(chat_id)
        return
    if data == "employer_apps":
        employer_apps_menu(chat_id)
        return
    if data.startswith("emp_job_apps:"):
        employer_apps_menu(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("emp_job:"):
        employer_job_details(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("emp_delete_confirm:"):
        employer_delete_job_confirm(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("emp_delete:"):
        employer_delete_job(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("emp_edit:"):
        employer_start_post(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("emp_country:"):
        country = data.split(":", 1)[1]
        if country == "__other__":
            send(chat_id, "🌍 اختر دولة من القائمة الحالية.", country_keyboard("emp_country"))
            return
        st = get_state(chat_id)
        if st.get("flow") != "employer_post":
            employer_start_post(chat_id)
            return
        st["data"]["country"] = country
        st["step"] = "city"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, f"🏙️ اختر المدينة في {country}:", city_keyboard(country, "emp_city"))
        return
    if data.startswith("emp_city:"):
        city = data.split(":", 1)[1]
        st = get_state(chat_id)
        if st.get("flow") != "employer_post":
            employer_start_post(chat_id)
            return
        country = st.get("data", {}).get("country", "")
        st["data"]["city"] = city
        st["step"] = "neighborhood"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "📍 اختر الحي:", neighborhood_keyboard(country, city, "emp_neighborhood"))
        return
    if data.startswith("emp_neighborhood:"):
        neighborhood = data.split(":", 1)[1]
        st = get_state(chat_id)
        if st.get("flow") != "employer_post":
            employer_start_post(chat_id)
            return
        st["data"]["neighborhood"] = neighborhood
        st["step"] = "category"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "🗂️ اختر المجال:", category_keyboard("emp_category"))
        return
    if data.startswith("emp_category:"):
        category = data.split(":", 1)[1]
        if category not in CATEGORIES:
            send(chat_id, "❌ المجال غير صالح.", category_keyboard("emp_category"))
            return
        st = get_state(chat_id)
        st["data"]["category"] = category
        st["step"] = "salary"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "💰 اكتب الراتب أو المبلغ، أو اختر «غير محدد».", [[btn("غير محدد", "emp_salary:غير محدد")]])
        return
    if data.startswith("emp_salary:"):
        value = data.split(":", 1)[1]
        st = get_state(chat_id)
        if st.get("flow") != "employer_post":
            employer_start_post(chat_id)
            return
        st["data"]["salary"] = value
        st["step"] = "employmentType"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "🕐 اختر نوع العمل:", rows([btn(x, f"emp_type:{x}") for x in WORK_TYPES], 2))
        return
    if data.startswith("emp_type:"):
        value = data.split(":", 1)[1]
        if value not in WORK_TYPES:
            send(chat_id, "❌ نوع العمل غير صالح.", rows([btn(x, f"emp_type:{x}") for x in WORK_TYPES], 2))
            return
        st = get_state(chat_id)
        st["data"]["employmentType"] = value
        st["step"] = "description"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "📝 اكتب وصفاً مختصراً للوظيفة، أو اكتب «بدون وصف».")
        return
    if data == "emp_post_confirm":
        employer_save_post(chat_id)
        return
    if data == "emp_post_restart":
        st = get_state(chat_id)
        edit_id = st.get("data", {}).get("edit_job_id")
        employer_start_post(chat_id, edit_id)
        return
    if data.startswith("emp_app:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            employer_app_details(chat_id, parts[1], parts[2])
        return
    if data.startswith("emp_contact:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            employer_contact(chat_id, parts[1], parts[2])
        return
    if data.startswith("emp_status:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            employer_update_application_status(chat_id, parts[1], parts[2], parts[3])
        return
    if data.startswith("emp_share:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            employer_share_company(chat_id, parts[1], parts[2])
        return

    if data == "emp_edit_company_name":
        if employer_user(chat_id):
            set_state(chat_id, flow="employer_profile", step="companyName", data={})
            send(chat_id, "🏢 اكتب اسم الشركة أو المنشأة:", [[btn("❌ إلغاء", "profile")]])
        return
    if data == "emp_edit_company_type":
        if employer_user(chat_id):
            set_state(chat_id, flow="employer_profile", step="companyType", data={})
            send(chat_id, "🧾 اكتب نوع النشاط:", [[btn("❌ إلغاء", "profile")]])
        return
    if data == "emp_edit_company_desc":
        if employer_user(chat_id):
            set_state(chat_id, flow="employer_profile", step="companyDescription", data={})
            send(chat_id, "📝 اكتب وصف الشركة أو النشاط:", [[btn("❌ إلغاء", "profile")]])
        return

    # Registration callbacks
    if data.startswith("reg_role:"):
        role = data.split(":", 1)[1].strip()
        if role not in ("job_seeker", "employer"):
            send(chat_id, "❌ نوع الحساب غير صالح. اختر من الأزرار.", [
                [btn("🙋 باحث عن عمل", "reg_role:job_seeker")],
                [btn("🏢 صاحب عمل", "reg_role:employer")],
            ])
            return
        st = get_state(chat_id)
        st.setdefault("data", {})["role"] = role
        # If Telegram did not provide a last name, collect it.
        if not st["data"].get("firstName"):
            st["step"] = "firstName"
            send(chat_id, "👤 اكتب اسمك الأول:")
        elif not st["data"].get("lastName"):
            st["step"] = "lastName"
            send(chat_id, "👤 اكتب اسم العائلة:")
        else:
            st["step"] = "country"
            send(chat_id, "🌍 اختر الدولة:", country_keyboard())
        STATE[str(chat_id)] = st
        save_states(STATE)
        return

    if data == "reg_back_country":
        send(chat_id, "🌍 اختر الدولة:", country_keyboard())
        return
    if data == "reg_back_city":
        st = get_state(chat_id)
        send(chat_id, "🏙️ اختر المدينة:", city_keyboard(st["data"].get("country", "")))
        return

    if data.startswith("reg_country:"):
        country = data.split(":", 1)[1]
        if country == "__other__":
            send(chat_id, "🌍 هذه الدولة غير مدعومة بالقائمة حالياً. اختر دولة من القائمة.", country_keyboard())
            return
        st = get_state(chat_id)
        st["data"]["country"] = country
        st["step"] = "city"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, f"🏙️ اختر المدينة في {country}:", city_keyboard(country))
        return

    if data.startswith("reg_city:"):
        city = data.split(":", 1)[1]
        st = get_state(chat_id)
        st["data"]["city"] = city
        st["step"] = "neighborhood"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "📍 اختر الحي:", neighborhood_keyboard(st["data"]["country"], city))
        return

    if data.startswith("reg_neighborhood:"):
        neighborhood = data.split(":", 1)[1]
        st = get_state(chat_id)
        st["data"]["neighborhood"] = neighborhood
        role = st["data"].get("role")
        if role == "job_seeker":
            st["step"] = "category"
            STATE[str(chat_id)] = st
            save_states(STATE)
            send(chat_id, "💼 اختر مجالك المهني:", category_keyboard())
        else:
            st["step"] = "companyName"
            STATE[str(chat_id)] = st
            save_states(STATE)
            send(chat_id, "🏢 اكتب اسم الشركة أو النشاط:")
        return

    if data.startswith("reg_category:"):
        st = get_state(chat_id)
        st["data"]["category"] = data.split(":", 1)[1]
        st["step"] = "education"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "🎓 اختر المستوى التعليمي:", education_keyboard())
        return

    if data.startswith("reg_education:"):
        education = data.split(":", 1)[1].strip()
        if education not in EDUCATION:
            send(chat_id, "❌ المستوى التعليمي غير صالح. اختر من القائمة.", education_keyboard())
            return
        st = get_state(chat_id)
        st.setdefault("data", {})["education"] = education
        st["step"] = "email"
        STATE[str(chat_id)] = st
        save_states(STATE)
        send(chat_id, "📧 اكتب بريدك الإلكتروني:")
        return

    if data == "reg_confirm":
        complete_registration(chat_id)
        return

    if data == "reg_restart":
        st = get_state(chat_id)
        old_data = st.get("data", {})
        set_state(chat_id, flow="register", step="role", data={
            "firstName": old_data.get("firstName", ""),
            "lastName": old_data.get("lastName", ""),
        })
        registration_start(chat_id)
        return

    if data == "resend_verify":
        resend_email_verification(chat_id)
        return

    if data == "verify_email":
        user = linked_user(chat_id)
        if user:
            start_email_verification(chat_id, user)
        else:
            send(chat_id, "🔐 سجل الدخول أولاً.", [[btn("🔐 تسجيل الدخول", "login")]])
        return

    # Profile editing
    if data == "edit_category":
        user = linked_user(chat_id)
        if not user or normalize_role(user.get("role", "job_seeker")) != "job_seeker":
            send(chat_id, "ℹ️ المجال المهني مخصص لحساب الباحث عن عمل.", [[btn("👤 حسابي", "profile")]])
            return
        edit_field_menu(chat_id, "category")
        return
    if data == "edit_education":
        user = linked_user(chat_id)
        if not user or normalize_role(user.get("role", "job_seeker")) != "job_seeker":
            send(chat_id, "ℹ️ المستوى التعليمي مخصص لحساب الباحث عن عمل.", [[btn("👤 حسابي", "profile")]])
            return
        edit_field_menu(chat_id, "education")
        return
    if data == "edit_country":
        send(chat_id, "🌍 اختر الدولة:", country_keyboard("set_country"))
        return
    if data == "edit_phone":
        try:
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "📱 اضغط «مشاركة رقم الهاتف» لإضافة رقمك إلى الحساب.",
                "reply_markup": {
                    "keyboard": [[{"text": "📱 مشاركة رقم الهاتف", "request_contact": True}]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            })
        except Exception:
            LOG.exception("Could not send contact keyboard")
        return
    if data.startswith("set_category:"):
        user = linked_user(chat_id)
        if not user or normalize_role(user.get("role", "job_seeker")) != "job_seeker":
            send(chat_id, "ℹ️ المجال المهني مخصص لحساب الباحث عن عمل.", [[btn("👤 حسابي", "profile")]])
            return
        value = data.split(":", 1)[1].strip()
        if value not in CATEGORIES:
            send(chat_id, "❌ المجال غير صالح. اختر من القائمة.", category_keyboard("set_category"))
            return
        update_user(chat_id, {"category": value})
        show_profile(chat_id)
        return
    if data.startswith("set_education:"):
        user = linked_user(chat_id)
        if not user or normalize_role(user.get("role", "job_seeker")) != "job_seeker":
            send(chat_id, "ℹ️ المستوى التعليمي مخصص لحساب الباحث عن عمل.", [[btn("👤 حسابي", "profile")]])
            return
        value = data.split(":", 1)[1].strip()
        if value not in EDUCATION:
            send(chat_id, "❌ المستوى التعليمي غير صالح. اختر من القائمة.", education_keyboard("set_education"))
            return
        update_user(chat_id, {"education": value})
        show_profile(chat_id)
        return
    if data.startswith("set_country:"):
        country = data.split(":", 1)[1]
        st = get_state(chat_id)
        set_state(chat_id, flow="edit_location", step="city", data={"country": country})
        send(chat_id, "🏙️ اختر المدينة:", city_keyboard(country, "set_city"))
        return
    if data.startswith("set_city:"):
        city = data.split(":", 1)[1]
        st = get_state(chat_id)
        country = st.get("data", {}).get("country", "")
        set_state(chat_id, flow="edit_location", step="neighborhood", data={"country": country, "city": city})
        send(chat_id, "📍 اختر الحي:", neighborhood_keyboard(country, city, "set_neighborhood"))
        return
    if data.startswith("set_neighborhood:"):
        st = get_state(chat_id)
        d = st.get("data", {})
        update_user(chat_id, {
            "country": d.get("country", ""),
            "city": d.get("city", ""),
            "neighborhood": data.split(":", 1)[1],
        })
        clear_state(chat_id)
        show_profile(chat_id)
        return

    # Job filters
    if data == "job_filter_category":
        send(chat_id, "🎯 اختر المجال:", rows([btn(c, f"filter_cat:{c}") for c in CATEGORIES], 2))
        return
    if data.startswith("filter_cat:"):
        show_jobs(chat_id, 0, category=data.split(":", 1)[1])
        return
    if data == "job_filter_country":
        send(chat_id, "🌍 اختر الدولة:", country_keyboard("filter_country"))
        return
    if data.startswith("filter_country:"):
        country = data.split(":", 1)[1]
        if country == "__other__":
            send(chat_id, "اختر دولة من القائمة.", country_keyboard("filter_country"))
        else:
            show_jobs(chat_id, 0, country=country)
        return

    if data == "employer":
        send(chat_id, "🏢 لوحة صاحب العمل\n\nسيتم إضافة نشر الوظائف وإدارة المتقدمين في المرحلة التالية، مع استخدام نفس صلاحيات وحسابات الموقع.", [[btn("👤 حسابي", "profile"), btn("⬅️ الرئيسية", "home")]])
        return

    # Unknown callback
    send(chat_id, "هذا الخيار لم يعد متاحاً. استخدم القائمة الرئيسية.", main_menu(linked_user(chat_id)))


def handle_update(update):
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return

    msg = update.get("message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    if chat_id is None:
        return

    allowed, wait_seconds = rate_limit(chat_id)
    if not allowed:
        send(chat_id, "⏳ أرسلت طلبات بسرعة كبيرة. انتظر لحظات ثم تابع من الأزرار.")
        return

    deliver_pending_notifications(chat_id)

    if msg.get("text") == "/start":
        SECRET_STATE.pop(str(chat_id), None)
        user = msg.get("from", {})
        # Cache Telegram names only for the current registration flow.
        STATE[str(chat_id)] = {
            "flow": "idle",
            "step": None,
            "data": {
                "firstName": sanitize_input(user.get("first_name", "")),
                "lastName": sanitize_input(user.get("last_name", "")),
            },
        }
        save_states(STATE)
        welcome(chat_id)
        return

    if msg.get("text") == "/menu":
        welcome(chat_id)
        return

    if msg.get("text") in ("/cancel", "⬅️ إلغاء"):
        clear_state(chat_id)
        welcome(chat_id)
        return

    if msg.get("contact"):
        handle_contact(chat_id, msg["contact"], msg.get("from", {}).get("id"))
        return

    text = msg.get("text", "")
    if text:
        handle_text(chat_id, text)


def configure_bot():
    # Keep the bot menu compact. This uses only Bot API and requires no extra package.
    commands = [
        {"command": "start", "description": "بدء استخدام المنصة"},
        {"command": "menu", "description": "القائمة الرئيسية"},
        {"command": "cancel", "description": "إلغاء الخطوة الحالية"},
    ]
    try:
        tg("setMyCommands", {"commands": commands})
    except Exception:
        LOG.exception("Could not set bot commands")


def run():
    configure_bot()
    me = tg("getMe")
    LOG.info("Telegram bot started: @%s", me.get("username"))
    offset = 0
    while True:
        try:
            updates = tg("getUpdates", {
                "timeout": 25,
                "offset": offset,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=35)
            for upd in updates or []:
                offset = int(upd["update_id"]) + 1
                try:
                    handle_update(upd)
                except Exception as exc:
                    LOG.exception("Update handling failed")
                    msg = upd.get("message") or {}
                    chat_id = (msg.get("chat") or {}).get("id")
                    if chat_id is None:
                        q = upd.get("callback_query") or {}
                        chat_id = ((q.get("message") or {}).get("chat") or {}).get("id")
                    if chat_id is not None:
                        telegram_error(chat_id, "فشل معالجة تحديث Telegram", exc, f"update_id={upd.get('update_id')}")
        except KeyboardInterrupt:
            LOG.info("Telegram bot stopped.")
            break
        except Exception as exc:
            LOG.exception("Polling error")
            telegram_error(None, "خطأ في اتصال Telegram Polling", exc, "polling")
            time.sleep(3)


if __name__ == "__main__":
    run()
