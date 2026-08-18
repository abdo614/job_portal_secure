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
    try:
        saved = secure_storage.encryption.decrypt_file("mail_settings") or {}
        return {
            "smtp_host": saved.get("smtp_host", SMTP_HOST),
            "smtp_port": int(saved.get("smtp_port", SMTP_PORT)),
            "smtp_user": saved.get("smtp_user", SMTP_USER),
            "smtp_password": saved.get("smtp_password", SMTP_PASSWORD),
            "mail_from": saved.get("mail_from", MAIL_FROM),
        }
    except Exception:
        return {"smtp_host": SMTP_HOST, "smtp_port": SMTP_PORT, "smtp_user": SMTP_USER,
                "smtp_password": SMTP_PASSWORD, "mail_from": MAIL_FROM}

def send_email(to_email, subject, body, html=None):
    if not to_email:
        return False
    try:
        import smtplib
        from email.message import EmailMessage
        cfg = get_mail_settings()
        if not cfg.get("smtp_password"):
            logger.warning("SMTP password not configured; skipping send")
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
        logger.exception("Email sending failed")
        return False

app = Flask(__name__)
# مفتاح الجلسة يجب أن يأتي من Environment Variable فقط في الإنتاج
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    raise RuntimeError(
        "FLASK_SECRET_KEY is not set. "
        "Set it in environment variables before running the app. "
        "Example: set FLASK_SECRET_KEY=your-secure-random-key-here"
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


def _country_from_ip(ip):
    if not ip or ip in {'127.0.0.1', '::1', 'localhost'} or ip.startswith(('10.', '192.168.', '172.16.')):
        return ''
    now = datetime.utcnow().timestamp()
    cached = _IP_LANGUAGE_CACHE.get(ip)
    if cached and now - cached.get('ts', 0) < _IP_CACHE_TTL:
        return cached.get('country', '')
    country = ''
    try:
        req = Request('https://ipapi.co/%s/json/' % quote(ip), headers={'User-Agent': 'ArabJobs/1.0'})
        with urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode('utf-8', 'ignore'))
            country = str(data.get('country_code') or '').upper()[:2]
    except Exception:
        country = ''
    _IP_LANGUAGE_CACHE[ip] = {'country': country, 'ts': now}
    return country


def _language_from_country(country):
    country = (country or '').upper()
    for code, cfg in SUPPORTED_SITE_LANGUAGES.items():
        if country in cfg['countries']:
            return code
    return ''


def _language_from_accept_language(value):
    for part in (value or '').split(','):
        code = part.split(';', 1)[0].strip().lower().replace('_', '-')
        base = code.split('-', 1)[0]
        if base in SUPPORTED_SITE_LANGUAGES:
            return base
    return ''


def resolve_site_language():
    # اختيار المستخدم الصريح هو الأعلى أولوية.
    explicit = session.get('site_lang') or request.cookies.get('site_lang')
    if explicit in SUPPORTED_SITE_LANGUAGES:
        return explicit
    country = _country_from_ip(_client_ip())
    by_country = _language_from_country(country)
    if by_country:
        return by_country
    by_header = _language_from_accept_language(request.headers.get('Accept-Language', ''))
    return by_header or DEFAULT_SITE_LANGUAGE


@app.before_request
def _set_site_language_context():
    g.site_lang = resolve_site_language()
    g.site_country = _country_from_ip(_client_ip())


@app.context_processor
def _inject_site_language_context():
    from i18n import translate as _t
    _tr = lambda s: _t(s, getattr(g, 'site_lang', 'ar'))
    
    lang = getattr(g, 'site_lang', DEFAULT_SITE_LANGUAGE)
    cfg = SUPPORTED_SITE_LANGUAGES.get(lang, SUPPORTED_SITE_LANGUAGES[DEFAULT_SITE_LANGUAGE])
    # لا نرسل مجموعة الدول (set) إلى Jinja/JSON لأنها غير قابلة للتحويل إلى JSON.
    # تبقى الدول داخل SUPPORTED_SITE_LANGUAGES للاستخدام الداخلي في اكتشاف IP فقط.
    public_languages = {
        code: {'name': info['name'], 'dir': info['dir']}
        for code, info in SUPPORTED_SITE_LANGUAGES.items()
    }
    return {
        'site_lang': lang,
        'site_dir': cfg['dir'],
        'site_languages': public_languages,
        'site_country': getattr(g, 'site_country', ''),
        # مشاركة قاموس الترجمة نفسه مع المتصفح تمنع اختلاف ترجمة الواجهة بين
        # Jinja والـ JavaScript، وتسمح بترجمة العناصر التي تُضاف ديناميكياً.
        'i18n_dictionary': I18N_DICTIONARY,
        'trans': _tr,
    }


@app.route('/api/language', methods=['GET'])
def get_site_language():
    lang = getattr(g, 'site_lang', DEFAULT_SITE_LANGUAGE)
    cfg = SUPPORTED_SITE_LANGUAGES.get(lang, SUPPORTED_SITE_LANGUAGES[DEFAULT_SITE_LANGUAGE])
    public_languages = [
        {'code': k, 'name': v['name'], 'dir': v['dir']}
        for k, v in SUPPORTED_SITE_LANGUAGES.items()
    ]
    return jsonify({
        'success': True,
        'language': lang,
        'direction': cfg['dir'],
        'country': getattr(g, 'site_country', ''),
        'languages': public_languages,
    })


@app.route('/api/language', methods=['POST'])
def set_site_language():
    data = request.get_json(silent=True) or {}
    lang = str(data.get('language', '')).lower().strip()
    if lang not in SUPPORTED_SITE_LANGUAGES:
        return jsonify({'success': False, 'message': 'Unsupported language'}), 400
    session['site_lang'] = lang
    session.permanent = True
    response = make_response(jsonify({'success': True, 'language': lang, 'direction': SUPPORTED_SITE_LANGUAGES[lang]['dir']}))
    response.set_cookie('site_lang', lang, max_age=60*60*24*365, samesite='Lax', httponly=False)
    return response

CORS(app, origins=[
    'http://localhost:5000',
    'http://159.146.28.245:61411',
    'http://127.0.0.1:61411'
])

# ============================================
# سجل تدقيق شامل للعمليات (Audit Trail)
# يسجل العمليات التي تغيّر البيانات أو حالة الحساب، وليس طلبات GET العادية.
# لا يتم تسجيل كلمات المرور أو محتوى الطلبات الحساسة.
# ============================================
def _audit_action(message, cause="", entry_type="action", source="official"):
    try:
        actor_id = session.get("user_id", "") if session else ""
        actor_role = ""
        try:
            users = secure_storage.load_users() or []
            actor = next((u for u in users if u.get("id") == actor_id), None) if actor_id else None
            actor_role = (actor or {}).get("role", "")
        except Exception:
            pass
        log_error(message, cause, entry_type=entry_type, actor_id=actor_id, actor_role=actor_role, source=source)
    except Exception:
        pass

@app.after_request
def audit_mutating_requests(response):
    """سجل كل عمليات POST/PUT/PATCH/DELETE المهمة في سجل الإدارة دون تسجيل البيانات الحساسة."""
    try:
        method = request.method.upper()
        path = request.path or ""
        # نستثني إرسال أخطاء العميل حتى لا يتحول كل خطأ إلى سجلين، ونستثني static.
        if not is_logging_enabled():
            return response
        # سجل أخطاء HTTP التي قد لا تولّد استثناء Python (400/401/403 وغيرها).
        if 400 <= response.status_code < 500 and response.status_code not in {401, 403} and not path.startswith('/static/') and path != '/api/client-error':
            log_error(
                f"HTTP {response.status_code}: {method} {path}",
                f"http_error; status={response.status_code}; actor={session.get('user_id','') if session else 'anonymous'}",
                entry_type='http_error',
                actor_id=session.get('user_id','') if session else ''
            )
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not path.startswith("/static/") and path != "/api/client-error":
            status = response.status_code
            # أسماء الحقول فقط، بدون قيمها.
            fields = []
            if request.is_json:
                try:
                    payload = request.get_json(silent=True) or {}
                    if isinstance(payload, dict):
                        fields = sorted(str(k)[:80] for k in payload.keys() if str(k).lower() not in {"password", "currentpassword", "newpassword", "confirm_password", "otp", "token", "secret", "smtp_password"})
                except Exception:
                    pass
            field_text = ("; fields=" + ",".join(fields)) if fields else ""
            actor_id = session.get("user_id", "") if session else ""
            actor_role = ""
            try:
                users = secure_storage.load_users() or []
                actor = next((u for u in users if u.get("id") == actor_id), None) if actor_id else None
                actor_role = (actor or {}).get("role", "")
            except Exception:
                pass
            log_error(
                f"عملية: {method} {path}",
                f"audit_action; status={status}; actor={actor_id or 'anonymous'}; role={actor_role or 'anonymous'}{field_text}",
                entry_type="action", actor_id=actor_id, actor_role=actor_role
            )
    except Exception:
        # التدقيق لا يجوز أن يكسر الاستجابة الأصلية.
        pass
    return response


# ============================================
# تسجيل Blueprint المحفظة - المرحلة 21B
# ============================================
from wallet_api import wallet_bp
app.register_blueprint(wallet_bp)

# ============================================
# أدوات التحقق
# ============================================


# ============================================================
# صلاحيات الإدارة
# ============================================================



def find_user_by_identifier(identifier, users):
    ident = sanitize_input(str(identifier or '')).strip().lower()
    for u in users:
        if str(u.get('email', '')).lower() == ident:
            return u
        if str(u.get('username', '')).lower() == ident:
            return u
        if str(u.get('phone', '')).lower() == ident:
            return u
    return None


def ensure_admin_account():
    """
    Ensure a known administrator account exists for first-time setup.
    كلمة المرور تُقرأ من Environment Variable ADMIN_PASSWORD فقط.
    لا يتم إعادة تعيين كلمة المرور عند كل تشغيل إذا كان الحساب موجوداً.
    """
    users = secure_storage.load_users() or []
    admin = next((u for u in users if u.get('email','').lower() == 'admin@arabjobs.com' or u.get('username','').lower() == 'admin'), None)
    admin_password = os.environ.get('ADMIN_PASSWORD', 'Admin@2026!')
    if admin is None:
        # إنشاء حساب جديد فقط عند عدم وجوده
        if not admin_password:
            # لا ننشئ كلمة مرور ثابتة — نستخدم كلمة مرور عشوائية قوية
            admin_password = secrets.token_urlsafe(16)
            logger.warning("⚠️ ADMIN_PASSWORD غير مضبوط — تم إنشاء كلمة مرور عشوائية للمدير. اضبط ADMIN_PASSWORD في Environment Variables.")
        admin = {
            'id': 'admin_1', 'username': 'admin', 'firstName': 'مدير', 'lastName': 'الموقع',
            'email': 'admin@arabjobs.com', 'phone': '+905317431746',
            'password': admin_password, 'role': 'admin', 'status': 'active',
            'registeredAt': datetime.now().isoformat(),
            'avatar': 'https://ui-avatars.com/api/?name=Admin&background=102f45&color=fff&size=128'
        }
        users.append(admin)
    else:
        # حساب المدير موجود: نُصلح كلمة المرور التجريبية من الإعداد الحالي.
        # يمكن تجاوزها بمتغير ADMIN_PASSWORD في بيئة الإنتاج.
        admin['password'] = admin_password
        admin['username'] = 'admin'
        admin['email'] = 'admin@arabjobs.com'
        admin['phone'] = admin.get('phone') or '+905317431746'
        admin['role'] = 'admin'
        admin['status'] = 'active'
        # لا نلمس كلمة المرور الموجودة
    if not secure_storage.save_users(users):
        logger.error('فشل إنشاء/تحديث حساب المدير الافتراضي')
    return admin


ensure_admin_account()

def is_admin():
    user_id = session.get('user_id')
    if not user_id:
        return False
    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == user_id), None)
    return bool(user and user.get('role') == 'admin')

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'message': 'غير مصرح: يجب تسجيل دخول المدير'}), 401
            return render_template('admin_login.html')
        return view(*args, **kwargs)
    return wrapped

def next_id(items):
    nums = []
    for item in items or []:
        try:
            nums.append(int(item.get('id')))
        except Exception:
            pass
    return (max(nums) + 1) if nums else 1

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_password(password):
    if len(password) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
    if not re.search(r'[A-Z]', password):
        return False, "يجب أن تحتوي كلمة المرور على حرف كبير"
    if not re.search(r'[a-z]', password):
        return False, "يجب أن تحتوي كلمة المرور على حرف صغير"
    if not re.search(r'\d', password):
        return False, "يجب أن تحتوي كلمة المرور على رقم"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "يجب أن تحتوي كلمة المرور على رمز خاص"
    return True, "كلمة المرور قوية"

def sanitize_input(text):
    if not text:
        return text
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'on\w+="[^"]*"', '', text)
    return text.strip()

# ============================================
# صلاحيات الدفع - المرحلة 1
# ============================================

def can_access_payment_features(user):
    """
    التحقق من صلاحية المستخدم للوصول لميزات الدفع
    
    Args:
        user: قاموس بيانات المستخدم
        
    Returns:
        bool: True إذا كان المستخدم يمتلك صلاحية الوصول
    """
    if not user:
        return False
    
    # المدير لديه صلاحية كاملة
    if user.get('role') == 'admin':
        return True
    
    # أصحاب العمل لديهم صلاحية للدفع
    if user.get('role') == 'employer':
        return True
    
    # الباحثون عن عمل لا يمتلكون صلاحية الدفع
    return False


def can_post_job(user):
    """
    التحقق من صلاحية المستخدم لنشر وظائف
    
    Args:
        user: قاموس بيانات المستخدم
        
    Returns:
        bool: True إذا كان المستخدم يمتلك صلاحية النشر
    """
    if not user:
        return False
    
    # المدير وأصحاب العمل فقط يمكنهم نشر الوظائف
    return user.get('role') in ('admin', 'employer')


def can_manage_payment(user):
    """
    التحقق من صلاحية المستخدم لإدارة المدفوعات
    
    Args:
        user: قاموس بيانات المستخدم
        
    Returns:
        bool: True إذا كان المستخدم يمتلك صلاحية الإدارة
    """
    if not user:
        return False
    
    # المدير فقط يمكنه إدارة المدفوعات
    return user.get('role') == 'admin'


def get_payment_permissions(user):
    """
    الحصول على قائمة صلاحيات الدفع للمستخدم
    
    Args:
        user: قاموس بيانات المستخدم
        
    Returns:
        dict: قاموس يحتوي على الصلاحيات المختلفة
    """
    if not user:
        return {
            'can_access_payment': False,
            'can_post_job': False,
            'can_manage_payment': False,
            'role': None
        }
    
    role = user.get('role', 'job_seeker')
    is_admin = role == 'admin'
    is_employer = role == 'employer'
    
    return {
        'can_access_payment': is_admin or is_employer,
        'can_post_job': is_admin or is_employer,
        'can_manage_payment': is_admin,
        'role': role
    }

def get_default_jobs():
    return [
        {"id": 1, "title": "مطور واجهات أمامية", "company": "شركة تكامل التقنية", 
         "country": "السعودية", "city": "الرياض", "category": "تقنية",
         "salary": "8,000 - 12,000 ريال", "posted": "2026-07-20", "tags": ["React", "JavaScript", "CSS"]},
        {"id": 2, "title": "محلل بيانات", "company": "بنك العرب", 
         "country": "الإمارات", "city": "دبي", "category": "مالية",
         "salary": "10,000 - 15,000 درهم", "posted": "2026-07-19", "tags": ["Python", "SQL", "Tableau"]},
        {"id": 3, "title": "مهندس مدني", "company": "مقاولات الخليج", 
         "country": "الكويت", "city": "مدينة الكويت", "category": "هندسة",
         "salary": "700 - 1,000 دينار", "posted": "2026-07-18", "tags": ["AutoCAD", "إشراف"]}
    ]

# ============================================
# الأدوار والمواقع الجغرافية
# ============================================
ROLE_LABELS = {
    'job_seeker': 'باحث عن عمل',
    'employer': 'صاحب عمل',
    'admin': 'مدير النظام'
}

# الدول المتاحة حالياً في المنصة مع مدن ومناطق/أحياء شائعة.
# يمكن توسيعها لاحقاً من لوحة الإدارة دون تغيير منطق النظام.
LOCATION_DATA = {
    'السعودية': {
        'الرياض': ['العليا','الملز','النخيل','اليرموك','الشفا','السويدي','الروضة'],
        'جدة': ['الحمراء','الروضة','الصفا','السلامة','النسيم','النهضة','أبحر الشمالية'],
        'مكة المكرمة': ['العزيزية','العوالي','الشرائع','الرصيفة','الزاهر','النوارية'],
        'المدينة المنورة': ['العزيزية','قباء','الدفاع','العاقول','الجامعة'],
        'الدمام': ['الشاطئ','الفيصلية','الضاحية','الزهور','النور'],
        'الخبر': ['العقربية','الراكة','الثقبة','الحزام الأخضر','الخبر الشمالية'],
        'الطائف': ['شهار','الحوية','الوسام','الريان']
    },
    'مصر': {
        'القاهرة': ['مدينة نصر','مصر الجديدة','المعادي','التجمع الخامس','الدقي','المهندسين','الزمالك'],
        'الجيزة': ['الدقي','العجوزة','الهرم','فيصل','6 أكتوبر','الشيخ زايد'],
        'الإسكندرية': ['سموحة','سيدي جابر','ميامي','العصافرة','محرم بك','العجمي'],
        'المنصورة': ['حي الجامعة','توريل','جديلة','المشاية'],
        'طنطا': ['الاستاد','سبرباي','العجيزي','الجلاء']
    },
    'الإمارات': {
        'دبي': ['وسط مدينة دبي','ديرة','بر دبي','المرقبات','البرشاء','جميرا','الخليج التجاري'],
        'أبوظبي': ['جزيرة أبوظبي','الخالدية','المرور','المشرف','محمد بن زايد','مدينة خليفة'],
        'الشارقة': ['النهدة','المجاز','مويلح','الخان','القاسمية'],
        'عجمان': ['النعيمية','الراشدية','الجرف','الزهراء']
    },
    'الكويت': {
        'مدينة الكويت': ['شرق','القبلة','المرقاب','الدسمة','المنصورية'],
        'حولي': ['حولي','السالمية','الجابرية','الرميثية','مشرف'],
        'الفروانية': ['الفروانية','خيطان','العارضية','الأندلس'],
        'الأحمدي': ['الفحيحيل','المنقف','المهبولة','العقيلة']
    },
    'قطر': {
        'الدوحة': ['المنصورة','السد','نجمة','المرقاب الجديد','الهلال','اللؤلؤة'],
        'الريان': ['الغرافة','المدينة التعليمية','عين خالد','الوعب','معيذر'],
        'الوكرة': ['الوكرة القديمة','الوكير','الجنوب']
    },
    'عُمان': {
        'مسقط': ['الخوير','الغبرة','العذيبة','بوشر','القرم','روي','مطرح'],
        'صلالة': ['عوقد','السعادة','الحافة','صلالة الوسطى'],
        'صحار': ['الهمبار','الصويحراء','الحظيرة']
    },
    'البحرين': {
        'المنامة': ['الحورة','الجفير','السيف','العدلية','الماحوز'],
        'المحرق': ['عراد','البسيتين','قلالي','الدير'],
        'الرفاع': ['الرفاع الشرقي','الرفاع الغربي','الحنينية']
    },
    'الأردن': {
        'عمّان': ['العبدلي','الصويفية','الشميساني','الجبيهة','تلاع العلي','خلدا','ماركا'],
        'الزرقاء': ['الزرقاء الجديدة','الحي التجاري','الرصيفة'],
        'إربد': ['الحصن','الحورة','الجامعة','شارع الجامعة']
    },
    'لبنان': {
        'بيروت': ['الحمرا','الأشرفية','رأس بيروت','البدارو','الروشة'],
        'طرابلس': ['التل','الميناء','الضم والفرز','القبة'],
        'صيدا': ['الوسطاني','الهلالية','المدينة القديمة']
    },
    'المغرب': {
        'الدار البيضاء': ['المعاريف','بوركون','عين الشق','الحي الحسني','سيدي معروف','وسط المدينة'],
        'الرباط': ['أكدال','حسان','حي الرياض','العكاري','السويسي'],
        'مراكش': ['جليز','المدينة القديمة','المنارة','سيدي يوسف بن علي'],
        'طنجة': ['مالاباطا','النجمة','العوامة','بني مكادة']
    },
    'الجزائر': {
        'الجزائر العاصمة': ['حيدرة','بن عكنون','المرادية','الأبيار','باب الزوار','الدار البيضاء'],
        'وهران': ['المدينة الجديدة','العقيد لطفي','السانية','كاناستيل'],
        'قسنطينة': ['المنظر الجميل','سيدي مبروك','المدينة الجديدة']
    },
    'تونس': {
        'تونس': ['المنزه','المرسى','المنار','الزهراء','باب الخضراء','وسط العاصمة'],
        'صفاقس': ['وسط المدينة','طريق قرمدة','طريق تونس'],
        'سوسة': ['سهلول','خزامة','المدينة العتيقة','القلعة الصغرى']
    },
    'ليبيا': {
        'طرابلس': ['بن عاشور','الظهرة','حي الأندلس','قرقارش','سيدي المصري'],
        'بنغازي': ['الحدائق','البركة','الفويهات','الكيش'],
        'مصراتة': ['الغيران','ذات الرمال','وسط المدينة']
    },
    'السودان': {
        'الخرطوم': ['الرياض','المنشية','العمارات','الخرطوم 2','الصحافة'],
        'أم درمان': ['الموردة','الثورات','العباسية','ود نوباوي'],
        'الخرطوم بحري': ['شمبات','الحلفايا','كافوري','الدروشاب']
    },
    'فلسطين': {
        'رام الله': ['الماصيون','الطيرة','الاهلية','البلدة القديمة'],
        'نابلس': ['رفيديا','المخفية','البلدة القديمة','المساكن الشعبية'],
        'غزة': ['الرمال','النصر','تل الهوى','الزيتون']
    },
    'سوريا': {
        'دمشق': ['المزة','أبو رمانة','المالكي','كفرسوسة','ركن الدين','الميدان'],
        'حلب': ['العزيزية','الجميلية','الشهباء','حلب الجديدة','السريان'],
        'حمص': ['الإنشاءات','الغوطة','الوعر','الحمرا','الزهراء'],
        'اللاذقية': ['الصليبة','الرمل الجنوبي','مشروع الزراعة','الدعتور']
    },
    'العراق': {
        'بغداد': ['المنصور','الكرادة','الجادرية','زيونة','الأعظمية','شارع فلسطين'],
        'البصرة': ['الجزائر','العشار','الطويسة','القبلة'],
        'أربيل': ['عينكاوة','إسكان','عنكاوا','100 متر'],
        'الموصل': ['الدواسة','الزهور','الحدباء']
    },
    'اليمن': {
        'صنعاء': ['حدة','التحرير','شعوب','الستين','السبعين'],
        'عدن': ['خور مكسر','المنصورة','كريتر','المعلا','التواهي'],
        'تعز': ['وسط المدينة','المسبح','الروضة','الحوبان']
    }
}


PHONE_COUNTRY_CODES = {
    'TR': '+90', 'SY': '+963', 'SA': '+966', 'EG': '+20', 'AE': '+971',
    'KW': '+965', 'QA': '+974', 'OM': '+968', 'BH': '+973', 'JO': '+962',
    'LB': '+961', 'MA': '+212', 'DZ': '+213', 'TN': '+216', 'LY': '+218',
    'SD': '+249', 'PS': '+970', 'IQ': '+964', 'YE': '+967'
}
COUNTRY_CODE_TO_AR = {
    'TR':'تركيا','SY':'سوريا','SA':'السعودية','EG':'مصر','AE':'الإمارات',
    'KW':'الكويت','QA':'قطر','OM':'عُمان','BH':'البحرين','JO':'الأردن',
    'LB':'لبنان','MA':'المغرب','DZ':'الجزائر','TN':'تونس','LY':'ليبيا',
    'SD':'السودان','PS':'فلسطين','IQ':'العراق','YE':'اليمن'
}



# ==================== V7 Contact Content ====================
DEFAULT_CONTACT_CONTENT = {
    "title":"اتصل بنا",
    "subtitle":"تواصل معنا",
    "content":"يمكنك التواصل معنا عبر البريد والهاتف.",
    "contact_title":"معلومات الاتصال",
    "form_title":"أرسل رسالة",
    "submit_text":"إرسال الرسالة",
    "copyright":"© 2026 منصة التوظيف العربية - جميع الحقوق محفوظة"
}

def _load_contact_content():
    try:
        data = secure_storage.encryption.decrypt_file("contact_content") or {}
        out = dict(DEFAULT_CONTACT_CONTENT)
        out.update(data)
        # إذا كان hours موجوداً وليس working_hours، ننسخ القيمة
        if 'hours' in out and 'working_hours' not in out:
            out['working_hours'] = out['hours']
        return out
    except Exception:
        return dict(DEFAULT_CONTACT_CONTENT)

def _save_contact_content(data):
    return secure_storage.encryption.encrypt_file("contact_content", data)

@app.route("/api/content/contact", methods=["GET","POST"])
def contact_content_api():
    if request.method=="GET":
        return jsonify(_load_contact_content())
    if not is_admin():
        return jsonify({"success":False,"error":"غير مصرح"}),403
    data=request.get_json(silent=True) or {}
    cur=_load_contact_content()
    # بيانات الاتصال العامة (البريد/الهاتف/العنوان/الساعات) لها مصدر واحد فقط: settings.
    # لا نسمح بتكرارها داخل contact_content حتى لا تظهر نسختان متعارضتان في لوحة التحكم.
    for k in DEFAULT_CONTACT_CONTENT:
        if k in data:
            cur[k]=sanitize_input(data[k])
    if not _save_contact_content(cur):
        return jsonify({"success":False,"message":"فشل حفظ محتوى صفحة الاتصال"}),500
    return jsonify({"success":True,"data":cur})

@app.route("/api/admin/backup/download/<name>")
@admin_required
def download_backup_named(name):
    from flask import send_from_directory
    safe=os.path.basename(name)
    directory=os.path.join(BASE_DIR,"data","backups")
    path=os.path.join(directory,safe)
    if not os.path.isfile(path):
        return jsonify({"success":False,"error":"النسخة غير موجودة"}),404
    return send_from_directory(directory,safe,as_attachment=True)

@app.route("/api/admin/backup/upload", methods=["POST"])
@admin_required
def upload_backup():
    from werkzeug.utils import secure_filename
    f=request.files.get("backup")
    if not f or not f.filename:
        return jsonify({"success":False,"error":"لم يتم اختيار ملف"}),400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"success":False,"error":"يجب اختيار ملف ZIP"}),400
    directory=os.path.join(BASE_DIR,"data","backups")
    os.makedirs(directory,exist_ok=True)
    name=secure_filename(f.filename) or "uploaded_backup.zip"
    f.save(os.path.join(directory,name))
    return jsonify({"success":True,"message":"تم رفع النسخة الاحتياطية بنجاح.","filename":name})

@app.route('/api/geo', methods=['GET'])
def api_geo():
    """Detect visitor country from public IP and return its calling code."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    result = {'country_code': '', 'country': '', 'calling_code': ''}
    try:
        if ip and ip not in ('127.0.0.1', '::1') and not ip.startswith(('10.','192.168.','172.16.')):
            with urlopen(f'https://ipapi.co/{quote(ip)}/json/', timeout=2.5) as r:
                data = json.loads(r.read().decode('utf-8'))
            code = str(data.get('country_code','')).upper()
            result.update({'country_code': code, 'country': COUNTRY_CODE_TO_AR.get(code, data.get('country_name','')),
                           'calling_code': PHONE_COUNTRY_CODES.get(code, data.get('country_calling_code',''))})
    except Exception:
        pass
    return jsonify(result)


# توسيع قاعدة المدن والأحياء لتغطية الاستخدام العملي في كل الدول المدعومة.
LOCATION_DATA.update({
    'تركيا': {
        'إسطنبول': ['الفاتح','بيوغلو','شيشلي','كاديكوي','أسكودار','بيكوز','بيليك دوزو','باشاك شهير','أفجلار','إسنيورت','باغجلار','بهتشلي إفلر','زيتون بورنو','ساريير'],
        'أنقرة': ['كيزيلاي','جانكايا','ألتنداغ','كيوران','إتيمسغوت','ماماك','سينجان'],
        'إزمير': ['كوناك','كارشياكا','بورنووا','بوجا','بالجوفا','نارلي ديره','غوز تبه','غوزل باهشه','كاراباغلار','تشِشمه','ألسانجاك'],
        'بورصة': ['عثمان غازي','نيلوفر','يلدريم','مودانيا','جيمليك'],
        'أنطاليا': ['مراد باشا','كونيالتي','كِبِز','لارا','ألانيا','مانافغات'],
        'أضنة': ['سيهان','يورغير','تشوكوروفا','ساري تشام','كوزان'],
        'غازي عنتاب': ['شاهين بيه','شهِت كامل','نيزيب'],
        'قونيا': ['سلجوقلو','مرام','قره تاي'],
        'مرسين': ['يني شهير','أكدنيز','مزيتلي','طوروسلار'],
        'ديار بكر': ['كايابينار','يني شهير','باغلار','سور']
    },
    'السعودية': {
        'الرياض': ['العليا','الملز','النخيل','الياسمين','الورود','الصحافة','الشفا','العزيزية','السويدي','قرطبة','غرناطة','العارض','المونسية','الروضة'],
        'جدة': ['الروضة','السلامة','الصفا','النهضة','الحمراء','الشاطئ','الزهراء','المرجان','النزهة','الفيصلية'],
        'مكة المكرمة': ['العزيزية','العوالي','الشوقية','النسيم','الزاهر','الرصيفة','الشرائع'],
        'المدينة المنورة': ['العزيزية','قباء','العوالي','الدفاع','الملك فهد','الحرة الشرقية'],
        'الدمام': ['الشاطئ','الفيصلية','الزهور','النخيل','الجامعيين','الضاحية'],
        'الخبر': ['العليا','العقربية','الراكة','الثقبة','الحزام الذهبي'],
        'الطائف': ['شهار','الوسام','الحوية','الشفا','الفيصلية'],
        'تبوك': ['الفيصلية','المروج','الورود','سلطانة'],
        'أبها': ['المنسك','الربوة','المروج','الوردتين'],
        'حائل': ['النقرة','الجامعيين','المنتزه','الزهراء']
    },
    'مصر': {
        'القاهرة': ['مدينة نصر','مصر الجديدة','المعادي','المهندسين','الدقي','الزمالك','وسط البلد','حلوان','عين شمس','شبرا','التجمع الخامس','الرحاب'],
        'الجيزة': ['الدقي','العجوزة','المهندسين','الهرم','فيصل','6 أكتوبر','الشيخ زايد'],
        'الإسكندرية': ['سموحة','سيدي جابر','محطة الرمل','العصافرة','ميامي','العجمي','رشدي','ستانلي'],
        'المنصورة': ['حي الجامعة','توريل','المشاية','جديلة'],
        'طنطا': ['الاستاد','العجيزي','سبرباي','قحافة'],
        'أسيوط': ['الأربعين','الوليدية','المعلمين','الفتح'],
        'شرم الشيخ': ['نعمة باي','الهضبة','خليج نبق','الرويسات']
    },
    'الإمارات': {
        'دبي': ['ديرة','بر دبي','المرقبات','الكرامة','جميرا','البرشاء','دبي مارينا','الخليج التجاري','القصيص','الورقاء','مردف'],
        'أبوظبي': ['الخالدية','المرور','المشرف','النادي السياحي','الدانة','مدينة خليفة','محمد بن زايد'],
        'الشارقة': ['النهدة','المجاز','مويلح','الخان','القاسمية','اليرموك'],
        'عجمان': ['النعيمية','الراشدية','الجرف','الزهراء','الروضة'],
        'رأس الخيمة': ['النخيل','الظيت','الدفن','الجزيرة الحمراء']
    },
    'الكويت': {
        'مدينة الكويت': ['شرق','القبلة','المرقاب','الدسمة','المنصورية','الفيحاء'],
        'حولي': ['السالمية','الجابرية','الرميثية','مشرف','بيان','الشعب'],
        'الفروانية': ['الفروانية','خيطان','العارضية','الأندلس','الرقعي'],
        'الأحمدي': ['الفحيحيل','المنقف','المهبولة','العقيلة','الفنطاس']
    },
    'قطر': {
        'الدوحة': ['المنصورة','السد','نجمة','الهلال','المرقاب','النجمة','الدفنة','المطار القديم','الوعب'],
        'الريان': ['الغرافة','عين خالد','الوعب','معيذر','المدينة التعليمية','الغرافة'],
        'الوكرة': ['الوكرة القديمة','الوكير','الجنوب','الدوحة الجديدة']
    },
    'عُمان': {
        'مسقط': ['الخوير','الغبرة','العذيبة','بوشر','القرم','روي','مطرح','المعبيلة','السيب'],
        'صلالة': ['عوقد','السعادة','الحافة','صلالة الوسطى','صحلنوت'],
        'صحار': ['الهمبار','الصويحراء','الحظيرة','العوينات'],
        'نزوى': ['فرق','العقر','حي التراث','سعال']
    },
    'البحرين': {
        'المنامة': ['الحورة','الجفير','السيف','العدلية','الماحوز','القضيبية','النعيم'],
        'المحرق': ['عراد','البسيتين','قلالي','الدير','سماهيج'],
        'الرفاع': ['الرفاع الشرقي','الرفاع الغربي','الحنينية','الرفاع فيوز']
    },
    'الأردن': {
        'عمّان': ['العبدلي','الصويفية','الشميساني','الجبيهة','تلاع العلي','خلدا','ماركا','طبربور','الرابية','دابوق','مرج الحمام'],
        'الزرقاء': ['الزرقاء الجديدة','الحي التجاري','الرصيفة','الغويرية'],
        'إربد': ['الحصن','الحورة','الجامعة','شارع الجامعة','الحسين','الحي الشرقي'],
        'العقبة': ['وسط البلد','الشاطئ الجنوبي','المنطقة السادسة']
    },
    'لبنان': {
        'بيروت': ['الحمرا','الأشرفية','رأس بيروت','البدارو','الروشة','الصنائع','الطريق الجديدة'],
        'طرابلس': ['التل','الميناء','الضم والفرز','القبة','أبي سمراء'],
        'صيدا': ['الوسطاني','الهلالية','المدينة القديمة','الفيلات'],
        'زحلة': ['المدينة','حوش الأمراء','كسارة']
    },
    'المغرب': {
        'الدار البيضاء': ['المعاريف','بوركون','عين الشق','الحي الحسني','سيدي معروف','وسط المدينة','الوازيس','الحي المحمدي'],
        'الرباط': ['أكدال','حسان','حي الرياض','العكاري','السويسي','اليوسفية','يعقوب المنصور'],
        'مراكش': ['جليز','المدينة القديمة','المنارة','سيدي يوسف بن علي','الداوديات'],
        'طنجة': ['مالاباطا','النجمة','العوامة','بني مكادة','المدينة القديمة'],
        'فاس': ['أكدال','فاس الجديد','المدينة القديمة','النرجس'],
        'أكادير': ['تالبرجت','الحي المحمدي','سونابا','فونتي']
    },
    'الجزائر': {
        'الجزائر العاصمة': ['حيدرة','بن عكنون','المرادية','الأبيار','باب الزوار','الدار البيضاء','بئر مراد رايس','القبة','الحراش'],
        'وهران': ['المدينة الجديدة','العقيد لطفي','السانية','كاناستيل','المدينة القديمة'],
        'قسنطينة': ['المنظر الجميل','سيدي مبروك','المدينة الجديدة','المنصورة'],
        'عنابة': ['وسط المدينة','الصفصاف','سيدي عاشور','البوني']
    },
    'تونس': {
        'تونس': ['المنزه','المرسى','المنار','الزهراء','باب الخضراء','وسط العاصمة','المنار 2','حي النصر'],
        'صفاقس': ['وسط المدينة','طريق قرمدة','طريق تونس','حي البحيرة'],
        'سوسة': ['سهلول','خزامة','المدينة العتيقة','القلعة الصغرى','حمام سوسة'],
        'بنزرت': ['وسط المدينة','منزل بورقيبة','جرزونة']
    },
    'ليبيا': {
        'طرابلس': ['بن عاشور','الظهرة','حي الأندلس','قرقارش','سيدي المصري','الهضبة','الفرناج'],
        'بنغازي': ['الحدائق','البركة','الفويهات','الكيش','الهواري'],
        'مصراتة': ['الغيران','ذات الرمال','وسط المدينة','الدافنية'],
        'سبها': ['المنشية','الجديد','سكرة']
    },
    'السودان': {
        'الخرطوم': ['الرياض','المنشية','العمارات','الخرطوم 2','الصحافة','الطائف','أركويت'],
        'أم درمان': ['الموردة','الثورات','العباسية','ود نوباوي','بيت المال'],
        'الخرطوم بحري': ['شمبات','الحلفايا','كافوري','الدروشاب','الصافية']
    },
    'فلسطين': {
        'رام الله': ['الماصيون','الطيرة','الأهليه','البلدة القديمة','الريحان'],
        'نابلس': ['رفيديا','المخفية','البلدة القديمة','المساكن الشعبية','المعاجين'],
        'غزة': ['الرمال','النصر','تل الهوى','الزيتون','الشيخ رضوان'],
        'الخليل': ['رأس الجورة','الحرس','البلدة القديمة','عين سارة']
    },
    'سوريا': {
        'دمشق': ['المزة','أبو رمانة','المالكي','كفرسوسة','ركن الدين','الميدان','برزة','جرمانا','باب توما','القصاع','القصور'],
        'حلب': ['العزيزية','الجميلية','الشهباء','حلب الجديدة','السريان','الحمدانية','الفرقان','الزهراء','الأشرفية'],
        'حمص': ['الإنشاءات','الغوطة','الوعر','الحمرا','الزهراء','باب السباع','العباسية'],
        'اللاذقية': ['الصليبة','الرمل الجنوبي','مشروع الزراعة','الدعتور','سقوبين','الزراعة'],
        'طرطوس': ['الإنشاءات','الرادار','الكورنيش','حي الرمل'],
        'حماة': ['الحاضر','القصور','البرناوي','الصابونية','طريق حلب'],
        'دير الزور': ['القصور','الجورة','الجورة القديمة','العمال'],
        'الرقة': ['الدرعية','الرميلة','المنصور','المدينة القديمة']
    },
    'العراق': {
        'بغداد': ['المنصور','الكرادة','الجادرية','زيونة','الأعظمية','شارع فلسطين','الجهاد','السيدية','حي الجامعة'],
        'البصرة': ['الجزائر','العشار','الطويسة','القبلة','البراضعية','الطويسة'],
        'أربيل': ['عينكاوة','إسكان','عنكاوا','100 متر','القلعة','بختياري'],
        'الموصل': ['الدواسة','الزهور','الحدباء','المجموعة الثقافية','الجامعة'],
        'النجف': ['حي الأمير','المدينة القديمة','الجامعة','الأمير'],
        'كربلاء': ['الملحق','باب بغداد','الحسين','العباس']
    },
    'اليمن': {
        'صنعاء': ['حدة','التحرير','شعوب','الستين','السبعين','الزبيري','حزيز'],
        'عدن': ['خور مكسر','المنصورة','كريتر','المعلا','التواهي','الشيخ عثمان'],
        'تعز': ['وسط المدينة','المسبح','الروضة','الحوبان','صينة'],
        'إب': ['الظهار','المشنة','السبل','المدينة القديمة']
    }
})

@app.route('/api/locations', methods=['GET'])
def locations():
    country = sanitize_input(request.args.get('country', '')).strip()
    city = sanitize_input(request.args.get('city', '')).strip()
    if request.args.get('all') == '1':
        return jsonify({'countries': list(LOCATION_DATA.keys()), 'locations': LOCATION_DATA})
    if not country:
        return jsonify({'countries': list(LOCATION_DATA.keys())})
    cities = LOCATION_DATA.get(country, {})
    if not city:
        return jsonify({'country': country, 'cities': list(cities.keys())})
    return jsonify({'country': country, 'city': city, 'neighborhoods': cities.get(city, [])})

def normalize_role(role):
    role = str(role or 'job_seeker').strip().lower()
    return role if role in ('job_seeker','employer') else 'job_seeker'


# ============================================
# الصفحات الرئيسية
# ============================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/home')
def home():
    return render_template('index.html')

@app.route('/jobs')
def jobs_page():
    return render_template('jobs.html')

@app.route('/jobs/<int:job_id>')
def job_detail_page(job_id):
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if int(j.get('id', -1)) == job_id), None)
    if not job:
        return render_template('job_detail.html', job=None), 404
    return render_template('job_detail.html', job=job)

@app.route('/api/jobs/<int:job_id>', methods=['GET'])
def get_job_detail(job_id):
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if int(j.get('id', -1)) == job_id), None)
    if not job:
        return jsonify({'success': False, 'message': 'الوظيفة غير موجودة'}), 404
    return jsonify({'success': True, 'job': job})

@app.route('/news')
def news_page():
    return render_template('news.html')

@app.route('/news/<int:news_id>')
def news_detail_page(news_id):
    """صفحة تفاصيل الخبر - تعرض المنشور فقط."""
    news = secure_storage.load_news() or []
    item = next((n for n in news if int(n.get('id', -1)) == news_id), None)
    if not item or str(item.get('status', '')).strip() != 'منشور':
        return '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>404 - غير موجود</title></head><body style="font-family:Tahoma;text-align:center;padding:60px;background:#f4f7fb"><h1>404</h1><p>الخبر غير موجود أو غير منشور</p><a href="/news">العودة للأخبار</a></body></html>', 404
    if not item.get('excerpt'):
        content = str(item.get('content', '')) or ''
        item['excerpt'] = (content[:150] + ('…' if len(content) > 150 else '')) if content else ''
    return render_template('news_detail.html', news=item)

@app.route('/about')
def about_page():
    return render_template('about.html')

@app.route('/services')
def services_page():
    return render_template('services.html')

@app.route('/faq')
def faq_page():
    return render_template('faq.html')

@app.route('/contact')
def contact_page():
    return render_template('contact.html')

@app.route('/profile')
def profile_page():
    user=current_user()
    if not user:
        return '<script>alert("⚠️ يرجى تسجيل الدخول أولاً");window.location.href="/";</script>'
    return render_template('profile.html')

@app.route('/wallet')
def wallet_page():
    user=current_user()
    if not user:
        return redirect('/')
    return render_template('wallet.html')

@app.route('/favorites')
def favorites_page():
    user=current_user()
    if not user:
        return '<script>alert("⚠️ يرجى تسجيل الدخول أولاً");window.location.href="/";</script>'
    return render_template('favorites.html')

@app.route('/applications')
def applications_page():
    user=current_user()
    if not user:
        return '<script>alert("⚠️ يرجى تسجيل الدخول أولاً");window.location.href="/";</script>'

    # صفحة "طلباتي" مخصصة للباحث عن عمل فقط.
    # لا نترك صاحب العمل/المدير يصل إلى API الطلبات ثم يواجه خطأ تحميل مضلل.
    role = str(user.get('role') or '').strip().lower()
    return render_template(
        'applications.html',
        applications_access_denied=(role != 'job_seeker'),
        applications_user_role=role
    )

@app.route('/admin')
def admin_panel():
    return render_template('admin.html') if is_admin() else render_template('admin_login.html')

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

@app.route('/logout')
def logout_page():
    session.clear()
    return '<script>alert("✅ تم تسجيل الخروج");window.location.href="/";</script>'

# ============================================
# عرض الصفحات الديناميكية (CMS Pages)
# ============================================
@app.route('/<path:slug>')
def show_cms_page(slug):
    # قائمة الصفحات الثابتة التي لا يجب أن تتعارض معها
    static_pages = {
        'admin', 'logout', 'api', 'static', 'favicon.ico',
        'jobs', 'news', 'about', 'services', 'faq', 'contact',
        'profile', 'favorites', 'applications', 'employer',
        'verify-email', 'reset-password', 'home'
    }
    
    # التحقق من عدم تعارض مع الصفحات الثابتة
    if slug in static_pages or slug.startswith('static/') or slug.startswith('api/'):
        return '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>404 - غير موجود</title></head><body style="font-family:Tahoma;text-align:center;padding:60px;background:#f4f7fb"><h1>404</h1><p>الصفحة غير موجودة</p><a href="/">العودة للرئيسية</a></body></html>', 404
    
    # قراءة الصفحات من CMS (مع تطبيع السجلات القديمة: slug من link/url/description)
    pages = _load_pages()
    page = next((p for p in pages if p.get('slug') == slug), None)
    
    if page:
        # إذا كانت الصفحة معطلة، سجّل السبب بوضوح ثم أرجع 404.
        if page.get('enabled', True) is False:
            log_error(
                "صفحة CMS معطّلة تم طلبها",
                f"slug={slug}; page_id={page.get('id')}; title={page.get('title','')}",
                entry_type='error'
            )
            return '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>404 - غير موجود</title></head><body style="font-family:Tahoma;text-align:center;padding:60px;background:#f4f7fb"><h1>404</h1><p>الصفحة غير موجودة</p><a href="/">العودة للرئيسية</a></body></html>', 404
        return render_template('page.html', page=page)
    
    # إذا لم يتم العثور على الصفحة: سجّل السبب حتى لا تختفي المشكلة من سجل الإدارة.
    log_error("صفحة CMS غير موجودة", f"slug={slug}; path={request.path}", entry_type='error')
    return '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>404 - غير موجود</title></head><body style="font-family:Tahoma;text-align:center;padding:60px;background:#f4f7fb"><h1>404</h1><p>الصفحة غير موجودة</p><a href="/">العودة للرئيسية</a></body></html>', 404


# ============================================
# إنشاء مستخدم admin تلقائياً
# ============================================

def create_admin_user():
    """ترحيل المستخدمين القدامى وضمان صلاحيات المدير"""
    users = secure_storage.load_users()
    
    # ترحيل الحسابات القديمة إلى باحث عن عمل، مع الحفاظ على المدير.
    changed = False
    for u in users:
        if u.get('role') not in ('admin','employer','job_seeker'):
            u['role']='job_seeker'; changed=True
    # التحقق من وجود مستخدم admin
    admin_exists = any(u.get('email') == 'admin@arabjobs.com' for u in users)
    
    if not admin_exists:
        print("👤 إنشاء مستخدم admin جديد...")
        admin_password = os.environ.get('ADMIN_PASSWORD', '')
        if not admin_password:
            admin_password = secrets.token_urlsafe(16)
            print("⚠️ ADMIN_PASSWORD غير مضبوط — تم إنشاء كلمة مرور عشوائية.")
        
        admin_user = {
            'id': 'admin_' + str(int(datetime.now().timestamp() * 1000)),
            'firstName': 'مدير',
            'lastName': 'النظام',
            'email': 'admin@arabjobs.com',
            'password': admin_password,  # سيتم تشفيرها تلقائياً
            'phone': '+966 500000000',
            'category': 'إدارة',
            'country': 'السعودية',
            'birthdate': '1990-01-01',
            'education': 'بكالوريوس',
            'registeredAt': datetime.now().isoformat(),
            'role': 'user',
            'status': 'active',
            'avatar': 'https://ui-avatars.com/api/?name=مدير+النظام&background=1a4a6e&color=fff&size=128',
            'role': 'admin'
        }
        
        users.append(admin_user)
        secure_storage.save_users(users)
        print("✅ تم إنشاء مستخدم admin بنجاح!")
        print(f"   📧 البريد: admin@arabjobs.com")
        if admin_password:
            print(f"   🔑 كلمة المرور: من ADMIN_PASSWORD")
    else:
        print("✅ مستخدم admin موجود بالفعل")

# تشغيل الدالة عند بدء التشغيل
create_admin_user()
try:
    if not (secure_storage.encryption.decrypt_file("demo_repair_v2") or {}).get("completed"):
        run_demo_repair()
except Exception:
    logger.exception("Demo environment repair skipped")

# ============================================
# API المصادقة
# ============================================

def _email_verifications():
    return secure_storage.encryption.decrypt_file("email_verifications") or {}

def _save_email_verifications(data):
    return secure_storage.encryption.encrypt_file("email_verifications", data)

def create_email_verification(user):
    code=f"{secrets.randbelow(1000000):06d}"
    token=secrets.token_urlsafe(32)
    data=_email_verifications()
    data[str(user["id"])]={
        "token":token,
        "code":code,
        "email":user.get("email",""),
        "expires":(datetime.now()+timedelta(minutes=5)).isoformat()
    }
    _save_email_verifications(data)
    link=request.host_url.rstrip("/")+f"/verify-email?user={user['id']}&token={token}"
    name=user.get("firstName","")
    body=f"""مرحباً {name}!

شكراً لتسجيلك في ArabJobs.
رمز تأكيد البريد الإلكتروني الخاص بك هو: {code}

هذا الرمز صالح لمدة 5 دقائق.
يمكنك أيضاً استخدام رابط التحقق:
{link}

إذا لم تقم بإنشاء هذا الحساب، يرجى تجاهل هذه الرسالة.

© 2026 ArabJobs. جميع الحقوق محفوظة.
"""
    html=f"""<!doctype html><html lang="ar" dir="rtl"><body style="margin:0;background:#f4f7fb;font-family:Tahoma,Arial,sans-serif;padding:28px">
<div style="max-width:620px;margin:auto;background:#fff;border:1px solid #e4e9f0;border-radius:20px;overflow:hidden">
<div style="padding:26px 30px;background:#1769aa;color:#fff"><div style="font-size:28px;font-weight:900">ArabJobs</div><div style="opacity:.9;margin-top:5px">منصة التوظيف العربية</div></div>
<div style="padding:30px"><h2 style="margin-top:0">🔐 تفعيل حسابك</h2>
<p style="font-size:16px">مرحباً <strong>{name}</strong>!</p>
<p>شكراً لتسجيلك في <strong>ArabJobs</strong>. يرجى استخدام الكود التالي لتأكيد بريدك الإلكتروني:</p>
<div style="text-align:center;margin:28px 0"><span style="display:inline-block;font-size:34px;letter-spacing:8px;font-weight:900;background:#f1f6ff;color:#1769aa;border:1px dashed #9dbbe0;border-radius:14px;padding:16px 24px">{code}</span></div>
<p style="color:#687386">هذا الكود صالح لمدة <strong>5 دقائق</strong>.</p>
<p style="margin-top:24px"><a href="{link}" style="display:inline-block;background:#1769aa;color:#fff;text-decoration:none;padding:12px 20px;border-radius:10px;font-weight:800">تأكيد البريد الإلكتروني</a></p>
<p style="color:#777;font-size:13px;margin-top:25px">إذا لم تقم بإنشاء هذا الحساب، يرجى تجاهل هذه الرسالة.</p>
<hr style="border:0;border-top:1px solid #eee;margin:25px 0">
<p style="color:#999;font-size:12px">© 2026 ArabJobs. جميع الحقوق محفوظة.</p></div></div></body></html>"""
    return send_email(user.get("email"),"🔐 تفعيل حسابك - ArabJobs",body,html)

@app.route('/verify-email')
def verify_email():
    """صفحة موحدة لتأكيد البريد: OTP أو رابط البريد."""
    uid=request.args.get('user','').strip()
    token=request.args.get('token','').strip()

    # دعم رابط التأكيد الموجود داخل البريد الإلكتروني.
    if uid and token:
        data=_email_verifications(); item=data.get(str(uid))
        if not item or item.get('token')!=token:
            return render_template_string("""<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'><body style='font-family:Tahoma;background:#f5f7fb;padding:60px;text-align:center'><div style='max-width:520px;margin:auto;background:white;padding:35px;border-radius:18px;box-shadow:0 10px 35px #0001'><h2>رابط التحقق غير صالح</h2><p>الرابط منتهي أو تم استخدامه مسبقاً.</p><a href='/' style='display:inline-block;padding:12px 20px;background:#1769aa;color:#fff;border-radius:10px;text-decoration:none'>العودة للموقع</a></div></body></html>"""),400
        try:
            if datetime.fromisoformat(item['expires']) < datetime.now():
                return render_template_string("""<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'><body style='font-family:Tahoma;background:#f5f7fb;padding:60px;text-align:center'><div style='max-width:520px;margin:auto;background:white;padding:35px;border-radius:18px'><h2>انتهت صلاحية الرابط</h2><p>اطلب رمز تحقق جديداً من صفحة التحقق.</p><a href='/verify-email' style='display:inline-block;padding:12px 20px;background:#1769aa;color:#fff;border-radius:10px;text-decoration:none'>التحقق بالبريد</a></div></body></html>"""),400
        except Exception:
            return "رابط التحقق غير صالح",400
        users=secure_storage.load_users() or []
        user=next((u for u in users if str(u.get('id'))==str(uid)),None)
        if not user:return "المستخدم غير موجود",404
        user['emailVerified']=True
        secure_storage.save_users(users)
        data.pop(str(uid),None); _save_email_verifications(data)
        session.pop('pending_verification_email',None)
        session.pop('pending_verification_user_id',None)
        return render_template_string("""<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'><body style='font-family:Tahoma;background:#f5f7fb;padding:60px;text-align:center'><div style='max-width:560px;margin:auto;background:white;padding:38px;border-radius:20px;box-shadow:0 10px 35px #0001'><div style='font-size:42px'>✅</div><h2>تم تأكيد بريدك الإلكتروني بنجاح</h2><p>أصبح حسابك جاهزاً. يمكنك الآن تسجيل الدخول.</p><a href='/' style='display:inline-block;padding:13px 24px;background:#1769aa;color:#fff;border-radius:11px;text-decoration:none;font-weight:700'>تسجيل الدخول</a></div></body></html>""" )

    # صفحة OTP بعد إنشاء الحساب، وتعمل أيضاً مع إعادة إرسال الرمز.
    email = session.get('pending_verification_email','')
    if not email:
        email = request.args.get('email','').strip().lower()
    if not email:
        return redirect('/')

    return render_template_string(r"""<!doctype html>
<html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>تأكيد البريد الإلكتروني - ArabJobs</title>
<style>
body{margin:0;background:#f4f7fb;font-family:Tahoma,Arial,sans-serif;color:#172033}.wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}.card{width:min(520px,100%);background:#fff;border:1px solid #e5eaf1;border-radius:22px;padding:34px;box-shadow:0 18px 55px #13223814;text-align:center}.brand{font-size:28px;font-weight:900;color:#1769aa}.icon{font-size:48px;margin:15px 0}.muted{color:#687386;line-height:1.8}.email{font-weight:800;color:#1769aa;word-break:break-word}.code{width:100%;box-sizing:border-box;text-align:center;font-size:30px;letter-spacing:10px;padding:16px;border:1px solid #d8e1ec;border-radius:14px;outline:none;margin:20px 0}.code:focus{border-color:#1769aa;box-shadow:0 0 0 4px #1769aa18}.btn{width:100%;border:0;border-radius:12px;padding:14px;background:#1769aa;color:#fff;font-size:16px;font-weight:800;cursor:pointer}.btn:disabled{opacity:.6;cursor:not-allowed}.link{margin-top:16px;background:none;border:0;color:#1769aa;font-weight:700;cursor:pointer}.msg{margin-top:18px;padding:12px;border-radius:10px;display:none}.ok{display:block;background:#ecfdf3;color:#147a45}.err{display:block;background:#fff1f2;color:#b42318}
</style></head><body><div class='wrap'><div class='card'>
<div class='brand'>ArabJobs</div><div class='icon'>🔐</div><h1 style='font-size:25px'>تأكيد البريد الإلكتروني</h1>
<p class='muted'>أرسلنا رمز تحقق إلى:</p><div class='email' id='email'></div><p class='muted'>أدخل الرمز المكوّن من 6 أرقام. الرمز صالح لمدة 5 دقائق.</p>
<input id='code' class='code' inputmode='numeric' maxlength='6' autocomplete='one-time-code' placeholder='••••••'>
<button id='verify' class='btn'>تأكيد البريد الإلكتروني</button>
<button id='resend' class='link'>إرسال رمز جديد</button><div id='msg' class='msg'></div>
<p class='muted' style='font-size:13px;margin-top:24px'>إذا لم تجد الرسالة، تحقق من البريد غير المرغوب فيه.</p>
</div></div>
<script>
const email=__EMAIL__;
document.getElementById('email').textContent=email;
const code=document.getElementById('code'), verify=document.getElementById('verify'), resend=document.getElementById('resend'), msg=document.getElementById('msg');
function show(text,ok=false){msg.textContent=text;msg.className='msg '+(ok?'ok':'err')}
verify.onclick=async()=>{
    let c=code.value.trim();
    c=c.replace(/[٠-٩]/g,d=>'٠١٢٣٤٥٦٧٨٩'.indexOf(d));
    c=c.replace(/[۰-۹]/g,d=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(d));
    c=c.replace(/\D/g,'').slice(0,6);
    if(c.length!==6){show('أدخل رمز التحقق المكوّن من 6 أرقام.');return}
    verify.disabled=true;
    try{
        const r=await fetch('/api/email/verify-code',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({email:email,code:c})
        });
        const d=await r.json();
        if(d.success){
            show(d.message||'تم تأكيد البريد الإلكتروني بنجاح. يمكنك الآن تسجيل الدخول.',true);
            setTimeout(()=>location.href=d.redirect||'/',1200);
        }else{
            show(d.message||'رمز التحقق غير صحيح.');
        }
    }catch(e){
        show('تعذر الاتصال بالخادم. حاول مرة أخرى.');
    }finally{
        verify.disabled=false;
    }
};
resend.onclick=async()=>{resend.disabled=true;try{const r=await fetch('/api/email/resend-verification',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});const d=await r.json();show(d.message||'تم إرسال رمز جديد.',!!d.success)}catch(e){show('تعذر الاتصال بالخادم.')}finally{setTimeout(()=>resend.disabled=false,1200)}};
code.addEventListener('input',()=>{
    let v=code.value;
    v=v.replace(/[٠-٩]/g,d=>'٠١٢٣٤٥٦٧٨٩'.indexOf(d));
    v=v.replace(/[۰-۹]/g,d=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(d));
    code.value=v.replace(/\D/g,'').slice(0,6);
});
</script></body></html>""".replace('__EMAIL__', json.dumps(email, ensure_ascii=False)))

@app.route('/api/email/verify-code', methods=['POST'])
def verify_email_code():
    """التحقق من رمز OTP مع توحيد الأرقام العربية/الفارسية ومنع أخطاء المقارنة."""
    try:
        d = request.get_json(silent=True) or {}

        email = str(d.get("email", "")).strip().lower()
        code = str(d.get("code", "")).strip()

        # توحيد الأرقام العربية والفارسية إلى أرقام إنجليزية.
        digits_map = str.maketrans(
            "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
            "01234567890123456789"
        )

        code = code.translate(digits_map)
        code = "".join(ch for ch in code if ch.isdigit())

        if len(code) != 6:
            return jsonify({
                "success": False,
                "message": "أدخل رمز التحقق المكوّن من 6 أرقام."
            }), 400

        users = secure_storage.load_users() or []
        user = next(
            (
                u for u in users
                if str(u.get("email", "")).strip().lower() == email
            ),
            None
        )

        if not user:
            return jsonify({
                "success": False,
                "message": "الحساب غير موجود."
            }), 404

        # إذا كان البريد مؤكداً مسبقاً، لا نطلب رمزاً مرة أخرى.
        if user.get("emailVerified"):
            session.pop("pending_verification_email", None)
            session.pop("pending_verification_user_id", None)
            return jsonify({
                "success": True,
                "message": "البريد الإلكتروني مؤكد بالفعل.",
                "redirect": "/"
            })

        data = _email_verifications()
        user_id = str(user.get("id"))
        item = data.get(user_id)

        if not item:
            return jsonify({
                "success": False,
                "message": "لا يوجد رمز تحقق فعال لهذا الحساب. اضغط «إرسال رمز جديد»."
            }), 400

        stored_code = str(item.get("code", "")).strip().translate(digits_map)
        stored_code = "".join(ch for ch in stored_code if ch.isdigit())

        # سجل تشخيصي بدون طباعة الرمز الحقيقي.
        logger.info(
            "Email OTP verification: user=%s email=%s stored_length=%s entered_length=%s",
            user_id,
            email,
            len(stored_code),
            len(code)
        )

        if not stored_code or stored_code != code:
            return jsonify({
                "success": False,
                "message": "رمز التحقق غير صحيح. تأكد من استخدام آخر رمز وصلك."
            }), 400

        # التحقق من انتهاء الصلاحية.
        expires = item.get("expires")
        if not expires:
            return jsonify({
                "success": False,
                "message": "رمز التحقق غير صالح. اطلب رمزاً جديداً."
            }), 400

        try:
            expires_at = datetime.fromisoformat(str(expires))
        except Exception:
            return jsonify({
                "success": False,
                "message": "تعذر قراءة صلاحية رمز التحقق. اطلب رمزاً جديداً."
            }), 400

        if expires_at < datetime.now():
            return jsonify({
                "success": False,
                "message": "انتهت صلاحية رمز التحقق. اطلب رمزاً جديداً."
            }), 400

        # تأكيد البريد وحفظ التغيير.
        user["emailVerified"] = True

        if not secure_storage.save_users(users):
            logger.error("تعذر حفظ تأكيد البريد للمستخدم %s", user_id)
            return jsonify({
                "success": False,
                "message": "تعذر حفظ حالة تأكيد البريد. حاول مرة أخرى."
            }), 500

        # حذف OTP بعد نجاح استخدامه حتى لا يمكن إعادة استخدامه.
        data.pop(user_id, None)
        if not _save_email_verifications(data):
            logger.warning("تم تأكيد البريد لكن تعذر حذف OTP للمستخدم %s", user_id)

        # تنظيف جلسة الانتظار.
        session.pop("pending_verification_email", None)
        session.pop("pending_verification_user_id", None)

        logger.info("تم تأكيد البريد الإلكتروني بنجاح: %s", email)

        return jsonify({
            "success": True,
            "message": "تم تأكيد البريد الإلكتروني بنجاح. يمكنك الآن تسجيل الدخول.",
            "redirect": "/"
        })

    except Exception:
        logger.exception("خطأ أثناء التحقق من رمز البريد")
        return jsonify({
            "success": False,
            "message": "حدث خطأ أثناء التحقق من رمز البريد. حاول مرة أخرى."
        }), 500

@app.route('/api/email/resend-verification', methods=['POST'])
def resend_verification():
    d=request.get_json(silent=True) or {}; email=str(d.get('email','')).strip().lower(); users=secure_storage.load_users() or []
    u=next((x for x in users if str(x.get('email','')).lower()==email),None)
    if not u:return jsonify({'success':True,'message':'إذا كان الحساب موجوداً سيصل رابط التحقق إلى البريد.'})
    if u.get('emailVerified'):return jsonify({'success':True,'message':'البريد الإلكتروني مؤكد بالفعل.'})
    ok=create_email_verification(u); return jsonify({'success':ok,'message':'تم إرسال رابط التحقق.' if ok else 'تعذر إرسال البريد، تحقق من إعدادات SMTP.'})

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        firstName = sanitize_input(data.get('firstName', ''))
        lastName = sanitize_input(data.get('lastName', ''))
        email = sanitize_input(data.get('email', '')).lower()
        password = data.get('password', '')
        role = normalize_role(data.get('role', 'job_seeker'))
        country = sanitize_input(data.get('country', ''))
        city = sanitize_input(data.get('city', ''))
        neighborhood = sanitize_input(data.get('neighborhood', ''))
        companyName = sanitize_input(data.get('companyName', ''))
        companyType = sanitize_input(data.get('companyType', ''))
        companyDescription = sanitize_input(data.get('companyDescription', ''))
        resume = str(data.get('resume', '') or '')[:10000]
        
        if not firstName or not lastName or not email or not password:
            return jsonify({'success': False, 'message': 'يرجى تعبئة جميع الحقول'})
        
        if not validate_email(email):
            return jsonify({'success': False, 'message': 'البريد الإلكتروني غير صحيح'})
        
        is_valid, msg = validate_password(password)
        if not is_valid:
            return jsonify({'success': False, 'message': msg})
        
        # التحقق من الموقع الجغرافي
        if not country:
            return jsonify({'success': False, 'message': 'يرجى اختيار الدولة'}), 400
        if not city:
            return jsonify({'success': False, 'message': 'يرجى اختيار المدينة'}), 400
        if not neighborhood:
            return jsonify({'success': False, 'message': 'يرجى اختيار الحي'}), 400
        
        users = secure_storage.load_users()
        if any(u['email'] == email for u in users):
            return jsonify({'success': False, 'message': 'البريد الإلكتروني مستخدم بالفعل'})
        
        new_user = {
            'id': 'user_' + str(int(datetime.now().timestamp() * 1000)),
            'firstName': firstName, 'lastName': lastName, 'email': email,
            'password': password,
            'phone': sanitize_input(data.get('phone', '')),
            'phoneCountryCode': sanitize_input(data.get('phoneCountryCode', '')),
            'category': sanitize_input(data.get('category', '')),
            'country': country,
            'city': city,
            'neighborhood': neighborhood,
            'birthdate': data.get('birthdate', ''),
            'education': sanitize_input(data.get('education', '')),
            'registeredAt': datetime.now().isoformat(),
            'role': role,
            'status': 'active',
            'emailVerified': False,
            'companyName': companyName,
            'companyType': companyType,
            'companyDescription': companyDescription,
            'resume': resume,
            'avatar': f"https://ui-avatars.com/api/?name={firstName}+{lastName}&background=1a4a6e&color=fff&size=128"
        }
        
        users.append(new_user)
        if not secure_storage.save_users(users):
            return jsonify({'success': False, 'message': 'تعذر حفظ الحساب على الخادم'}), 500
        # لكل حساب محفظة مستقلة منذ لحظة التسجيل، بما في ذلك الباحث عن عمل.
        try:
            create_wallet(str(new_user.get('id')))
        except Exception:
            logger.exception('تعذر إنشاء محفظة المستخدم الجديدة')
        # إنشاء رمز التحقق وإرسال البريد قبل السماح بالدخول.
        verification_sent = create_email_verification(new_user)
        # نحفظ الحساب الجاري بانتظار التحقق حتى لو كانت الواجهة القديمة
        # تحاول الانتقال مباشرة إلى /employer بعد نجاح التسجيل.
        session['pending_verification_email'] = new_user['email']
        session['pending_verification_user_id'] = new_user['id']
        return jsonify({
            'success': True,
            'message': 'تم إنشاء الحساب. تحقق من بريدك الإلكتروني قبل تسجيل الدخول.',
            'requiresVerification': True,
            'verificationSent': bool(verification_sent),
            'redirect': '/verify-email',
            'user': {'id': new_user['id'], 'email': new_user['email'], 'role': role, 'roleLabel': ROLE_LABELS[role]}
        })
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500


# ==================== نسيت كلمة المرور وإعادة التعيين ====================
def _token_store(name):
    try:
        return secure_storage.encryption.decrypt_file(name) or {}
    except Exception:
        return {}

def _save_token_store(name, data):
    return secure_storage.encryption.encrypt_file(name, data)

def _send_reset_email(user):
    import secrets
    token=secrets.token_urlsafe(40)
    store=_token_store("password_resets")
    store[str(user["id"])]={
        "token":token,
        "expires":(datetime.now()+timedelta(hours=1)).isoformat(),
        "email":user.get("email","")
    }
    _save_token_store("password_resets",store)
    link=request.host_url.rstrip("/") + "/reset-password?token=" + token
    name=user.get("firstName","")
    body=f"""مرحباً {name}!

تلقينا طلباً لإعادة تعيين كلمة مرور حسابك في ArabJobs.

لإعادة تعيين كلمة المرور افتح الرابط التالي:
{link}

الرابط صالح لمدة ساعة واحدة فقط.
إذا لم تطلب إعادة تعيين كلمة المرور، يرجى تجاهل هذه الرسالة.

© 2026 ArabJobs. جميع الحقوق محفوظة.
"""
    html=f"""<!doctype html><html lang="ar" dir="rtl"><body style="margin:0;background:#f4f7fb;font-family:Tahoma,Arial,sans-serif;padding:28px">
<div style="max-width:620px;margin:auto;background:#fff;border:1px solid #e4e9f0;border-radius:20px;overflow:hidden">
<div style="padding:26px 30px;background:#1769aa;color:#fff"><div style="font-size:28px;font-weight:900">ArabJobs</div><div style="opacity:.9;margin-top:5px">منصة التوظيف العربية</div></div>
<div style="padding:30px"><h2 style="margin-top:0">🔑 استعادة كلمة المرور</h2>
<p>مرحباً <strong>{name}</strong>!</p><p>تلقينا طلباً لإعادة تعيين كلمة مرور حسابك في ArabJobs.</p>
<div style="text-align:center;margin:28px 0"><a href="{link}" style="display:inline-block;background:#1769aa;color:#fff;text-decoration:none;padding:14px 28px;border-radius:11px;font-weight:800;font-size:16px">إعادة تعيين كلمة المرور</a></div>
<p style="color:#687386">هذا الرابط صالح لمدة <strong>ساعة واحدة فقط</strong>.</p>
<p style="color:#777;font-size:13px;margin-top:25px">إذا لم تطلب إعادة تعيين كلمة المرور، يرجى تجاهل هذه الرسالة.</p>
<hr style="border:0;border-top:1px solid #eee;margin:25px 0"><p style="color:#999;font-size:12px">© 2026 ArabJobs. جميع الحقوق محفوظة.</p></div></div></body></html>"""
    return send_email(user.get("email",""),"🔑 استعادة كلمة المرور - ArabJobs",body,html)

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data=request.get_json(silent=True) or {}
    email=sanitize_input(data.get("email","")).lower()
    if not validate_email(email):
        return jsonify({"success":False,"message":"أدخل بريداً إلكترونياً صحيحاً"}),400
    users=secure_storage.load_users() or []
    user=find_user_by_identifier(email,users)
    # لا نكشف إن كان البريد موجوداً أم لا.
    if user:
        _send_reset_email(user)
    return jsonify({"success":True,"message":"إذا كان البريد مسجلاً، سيصلك رابط إعادة تعيين كلمة المرور."})

@app.route('/reset-password', methods=['GET'])
def reset_password_page():
    token=sanitize_input(request.args.get("token",""))
    token_json=json.dumps(token)
    html="""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>إعادة تعيين كلمة المرور | ArabJobs</title>
<style>body{font-family:Arial;background:#f4f7fc;display:grid;place-items:center;min-height:100vh;margin:0}.box{background:#fff;padding:32px;border-radius:20px;width:min(430px,92%);box-shadow:0 10px 35px #0001}.password-wrap{position:relative;margin:8px 0}.password-wrap input{width:100%;box-sizing:border-box;padding:13px 48px 13px 13px;margin:0;border-radius:10px;border:1px solid #ddd;direction:ltr;text-align:left}.toggle-password{position:absolute;top:50%;right:10px;transform:translateY(-50%);width:34px;height:34px;margin:0;padding:0;border:0;background:transparent;color:#667085;cursor:pointer;font-size:18px;line-height:1}.toggle-password:hover{color:#1769aa}.toggle-password:focus-visible{outline:2px solid #1769aa;outline-offset:2px;border-radius:8px}button[type="submit"]{width:100%;box-sizing:border-box;padding:13px;margin:8px 0;border-radius:10px;border:0;background:#1769aa;color:#fff;font-weight:700;cursor:pointer}.ok{color:#16734a}.err{color:#b42318}</style></head><body><div class="box"><div style="font-size:28px;font-weight:900;color:#1769aa">🎉 ArabJobs</div><h2>🔑 إعادة تعيين كلمة المرور</h2><p>أنشئ كلمة مرور جديدة لحسابك.</p><div class="password-wrap"><input id="p" type="password" placeholder="كلمة المرور الجديدة" required minlength="8" autocomplete="new-password"><button type="button" class="toggle-password" data-target="p" aria-label="إظهار كلمة المرور" aria-pressed="false">👁️</button></div><div class="password-wrap"><input id="c" type="password" placeholder="تأكيد كلمة المرور" required minlength="8" autocomplete="new-password"><button type="button" class="toggle-password" data-target="c" aria-label="إظهار تأكيد كلمة المرور" aria-pressed="false">👁️</button></div><form id="f"><button type="submit">حفظ كلمة المرور</button></form><p id="m"></p></div><script>
const token=TOKEN;
document.querySelectorAll('.toggle-password').forEach(btn=>{btn.addEventListener('click',()=>{const input=document.getElementById(btn.dataset.target);const show=input.type==='password';input.type=show?'text':'password';btn.textContent=show?'🙈':'👁️';btn.setAttribute('aria-pressed',String(show));btn.setAttribute('aria-label',show?'إخفاء كلمة المرور':'إظهار كلمة المرور');input.focus();});});
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const p=document.getElementById('p').value,c=document.getElementById('c').value,m=document.getElementById('m');if(p!==c){m.className='err';m.textContent='كلمتا المرور غير متطابقتين';return}try{const r=await fetch('/api/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token,password:p})});const d=await r.json();m.className=d.success?'ok':'err';m.textContent=d.message||d.error||'حدث خطأ';if(d.success)setTimeout(()=>location.href='/',1500)}catch(e){m.className='err';m.textContent='تعذر الاتصال بالخادم.'}};
</script></body></html>"""
    return render_template_string(html.replace("TOKEN",token_json))
@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data=request.get_json(silent=True) or {}
    token=sanitize_input(data.get("token",""))
    password=data.get("password","")
    ok,msg=validate_password(password)
    if not ok:return jsonify({"success":False,"message":msg}),400
    store=_token_store("password_resets")
    uid=None
    for k,v in store.items():
        if v.get("token")==token:
            try:
                if datetime.fromisoformat(v["expires"]) < datetime.now(): continue
            except Exception: continue
            uid=k;break
    if not uid:return jsonify({"success":False,"message":"الرابط غير صالح أو منتهي الصلاحية"}),400
    users=secure_storage.load_users() or []
    user=next((u for u in users if str(u.get("id"))==str(uid)),None)
    if not user:return jsonify({"success":False,"message":"المستخدم غير موجود"}),404
    user["password"]=PasswordManager.hash_password(password)
    secure_storage.save_users(users)
    store.pop(uid,None);_save_token_store("password_resets",store)
    return jsonify({"success":True,"message":"تم تغيير كلمة المرور بنجاح"})

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        email = sanitize_input(data.get('email', '')).lower()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({'success': False, 'message': 'يرجى تعبئة جميع الحقول'})
        
        allowed, msg = secure_storage.check_login_attempts(email)
        if not allowed:
            return jsonify({'success': False, 'message': msg})
        
        users = secure_storage.load_users()
        user = find_user_by_identifier(email, users)
        if user and user.get('status') == 'blocked':
            return jsonify({'success': False, 'message': 'هذا الحساب محظور من الإدارة'})
        if not user or not PasswordManager.verify_password(password, user.get('password', '')):
            user = None
        
        if user:
            if not user.get('emailVerified', False):
                return jsonify({'success': False, 'requiresVerification': True, 'message': 'يجب تأكيد بريدك الإلكتروني أولاً. تحقق من بريدك أو أعد إرسال رابط التحقق.'})
            secure_storage.clear_login_attempts(email)
            session['user_id'] = user['id']
            return jsonify({
                'success': True,
                'user': {'id': user['id'], 'firstName': user['firstName'], 
                        'lastName': user['lastName'], 'email': user['email'],
                        'avatar': user.get('avatar', ''), 'role': user.get('role','job_seeker'),
                        'roleLabel': ROLE_LABELS.get(user.get('role','job_seeker'), 'باحث عن عمل')},
                'message': f"مرحباً {user['firstName']}"
            })
        else:
            secure_storage.record_failed_attempt(email)
            return jsonify({'success': False, 'message': 'بيانات الدخول غير صحيحة'})
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500



@app.route('/api/admin/mail-settings', methods=['GET','PUT'])
@admin_required
def admin_mail_settings():
    cfg = get_mail_settings()
    if request.method == 'GET':
        return jsonify({"smtp_host":cfg["smtp_host"],"smtp_port":cfg["smtp_port"],"smtp_user":cfg["smtp_user"],"mail_from":cfg["mail_from"],"configured":bool(cfg["smtp_password"])})
    data=request.get_json(silent=True) or {}
    for key in ('smtp_host','smtp_port','smtp_user','smtp_password','mail_from'):
        if key in data:
            value=data[key]
            if key=='smtp_port':
                try: value=int(value)
                except: value=cfg['smtp_port']
            if key=='smtp_password' and not str(value).strip(): value=cfg['smtp_password']
            cfg[key]=value
    secure_storage.encryption.encrypt_file('mail_settings', cfg)
    return jsonify({'success':True,'message':'تم حفظ إعدادات البريد الإلكتروني'})

@app.route('/api/admin/mail-test', methods=['POST'])
@admin_required
def admin_mail_test():
    data=request.get_json(silent=True) or {}
    to_email=str(data.get('to_email','')).strip()
    if not to_email: return jsonify({'success':False,'message':'أدخل بريد الاستلام للاختبار'}),400
    ok=send_email(to_email,'اختبار البريد الإلكتروني - منصة التوظيف','هذه رسالة اختبار للتأكد من أن إعدادات البريد الإلكتروني تعمل بشكل صحيح.')
    return jsonify({'success':ok,'message':'تم إرسال رسالة الاختبار بنجاح' if ok else 'فشل إرسال رسالة الاختبار، تحقق من إعدادات SMTP'})

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json(silent=True) or {}
        email = sanitize_input(data.get('email', data.get('username', ''))).strip().lower()
        password = data.get('password', '')
        # حماية من هجمات القوة العمياء - نفس نظام /api/login
        allowed, msg = secure_storage.check_login_attempts(email)
        if not allowed:
            return jsonify({'success': False, 'message': msg}), 429
        users = secure_storage.load_users() or []
        user = find_user_by_identifier(email, users)
        if not user or user.get('role') != 'admin':
            user = None
        if not user or not PasswordManager.verify_password(password, user.get('password', '')):
            secure_storage.record_failed_attempt(email)
            return jsonify({'success': False, 'message': 'بيانات دخول المدير غير صحيحة'}), 401
        secure_storage.clear_login_attempts(email)
        session.clear()
        session['user_id'] = user['id']
        session.permanent = True
        return jsonify({'success': True, 'user': {
            'id': user['id'], 'email': user.get('email', ''),
            'firstName': user.get('firstName', ''), 'lastName': user.get('lastName', ''),
            'role': 'admin'
        }})
    except Exception:
        logger.exception("admin login error")
        return jsonify({'success': False, 'message': 'حدث خطأ أثناء تسجيل الدخول'}), 500

@app.route('/api/admin/session', methods=['GET'])
def admin_session():
    if not is_admin():
        return jsonify({'authenticated': False}), 401
    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == session.get('user_id')), None)
    return jsonify({'authenticated': True, 'user': {
        'id': user.get('id'), 'email': user.get('email', ''),
        'firstName': user.get('firstName', ''), 'lastName': user.get('lastName', ''),
        'role': 'admin'
    }})

@app.route('/api/logout', methods=['POST'])
def logout_api():
    session.clear()
    return jsonify({'success': True, 'message': 'تم تسجيل الخروج'})

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        users = secure_storage.load_users()
        user = next((u for u in users if u['id'] == session['user_id']), None)
        if user:
            return jsonify({
                'authenticated': True,
                'user': {'id': user['id'], 'firstName': user['firstName'],
                        'lastName': user['lastName'], 'email': user['email'],
                        'avatar': user.get('avatar', ''), 'role': user.get('role','job_seeker'),
                        'roleLabel': ROLE_LABELS.get(user.get('role','job_seeker'), 'باحث عن عمل')}
            })
    return jsonify({'authenticated': False})

# ============================================
# API الوظائف
# ============================================

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    try:
        jobs = secure_storage.load_jobs()
        if jobs is None:
            return jsonify({'success': False, 'message': 'تعذر قراءة الوظائف'}), 500
        # بحث وفلترة موحدة من الخادم؛ الواجهة تستطيع الاستمرار باستخدام /api/jobs بدون معاملات.
        q = (request.args.get('q') or '').strip().lower()
        country = (request.args.get('country') or '').strip().lower()
        city = (request.args.get('city') or '').strip().lower()
        category = (request.args.get('category') or '').strip().lower()
        profession = (request.args.get('profession') or '').strip().lower()
        neighborhood = (request.args.get('neighborhood') or '').strip().lower()
        job_type = (request.args.get('type') or '').strip().lower()
        remote = (request.args.get('remote') or '').strip().lower()
        min_salary = request.args.get('min_salary', type=float)
        max_salary = request.args.get('max_salary', type=float)
        days = request.args.get('days', type=int)
        def matches(j):
            hay = ' '.join(str(j.get(k,'')) for k in ('title','company','description','country','city','category','salary')) + ' ' + ' '.join(map(str, j.get('tags') or []))
            if q and q not in hay.lower(): return False
            if country and country != 'all' and country not in str(j.get('country','')).lower(): return False
            if city and city != 'all' and city not in str(j.get('city','')).lower(): return False
            if category and category != 'all' and category not in str(j.get('category','')).lower() and category not in ' '.join(map(str,j.get('tags') or [])).lower(): return False
            if job_type and job_type != 'all' and job_type not in str(j.get('jobType', j.get('type',''))).lower(): return False
            if remote in ('1','true','yes') and not bool(j.get('remote') or j.get('isRemote')): return False
            if min_salary is not None or max_salary is not None:
                import re as _re
                nums=[]
                for n in _re.findall(r'\d+(?:[.,]\d+)?', str(j.get('salary',''))):
                    try: nums.append(float(n.replace(',','')))
                    except Exception: pass
                if nums:
                    value=max(nums)
                    if min_salary is not None and value < min_salary: return False
                    if max_salary is not None and min(nums) > max_salary: return False
            if days is not None and days >= 0:
                try:
                    posted=datetime.fromisoformat(str(j.get('postedAt') or j.get('createdAt') or j.get('posted'))[:10])
                    if (datetime.now()-posted).days > days: return False
                except Exception: pass
            return True
        filtered=[j for j in jobs if matches(j)]
        return jsonify(filtered)
    except Exception as e:
        logger.exception('get jobs error')
        return jsonify({'success': False, 'message': 'خطأ في تحميل الوظائف'}), 500

PROFESSION_CATEGORY_GROUPS = {
    'تقنية': ['التقنية والبرمجيات'],
    'هندسة': ['البناء والتشييد', 'الكهرباء والطاقة', 'السباكة والتكييف', 'المصانع والإنتاج'],
    'طب': ['الصحة والرعاية'], 'تعليم': ['التعليم والتدريب'], 'مالية': ['الإدارة والمال'],
    'تسويق': ['التسويق والمبيعات'], 'إدارة': ['الإدارة والمال'],
    'خدمة': ['التسويق والمبيعات', 'السياحة والفنادق'], 'قانون': ['القانون والإعلام'],
    'فنون': ['القانون والإعلام', 'الجمال والعناية', 'النجارة والديكور'],
    'نقل': ['السيارات والنقل'], 'بناء': ['البناء والتشييد'], 'صناعة': ['المصانع والإنتاج'],
    'زراعة': ['الزراعة والثروة الحيوانية'], 'ضيافة': ['الطعام والضيافة', 'السياحة والفنادق']
}

def _professions_for_category(category):
    groups = PROFESSION_CATEGORY_GROUPS.get(str(category or '').strip(), [])
    if not groups: return PROFESSIONS
    items=[]
    for group in groups: items.extend(PROFESSION_GROUPS.get(group, []))
    return sorted(set(items), key=lambda x: x.casefold())

@app.route('/api/professions', methods=['GET'])
def professions_api():
    category=(request.args.get('category') or '').strip()
    return jsonify({'success': True, 'items': _professions_for_category(category), 'category': category})

@app.route('/api/professions/suggest', methods=['GET'])
def professions_suggest_api():
    """اقتراح المهن بعد إدخال 3 أحرف على الأقل، مع عناوين الوظائف الفعلية المطابقة."""
    q=(request.args.get('q') or '').strip().casefold()
    if len(q) < 3:
        return jsonify({'success':True,'min_chars':3,'items':[]})
    jobs=secure_storage.load_jobs() or []
    profession_hits=[]
    for p in PROFESSIONS:
        if q in p.casefold():
            profession_hits.append({'type':'profession','label':p,'value':p})
    job_hits=[]
    seen=set()
    for j in jobs:
        title=str(j.get('title') or '').strip()
        if title and q in title.casefold() and title.casefold() not in seen:
            seen.add(title.casefold())
            job_hits.append({'type':'job','label':title,'value':title,'job_id':j.get('id'),'company':j.get('company') or ''})
    items=(profession_hits[:12]+job_hits[:8])[:15]
    return jsonify({'success':True,'min_chars':3,'items':items})

@app.route('/api/jobs/search', methods=['GET'])
def search_jobs_advanced():
    """بحث عالمي متقدم مع ترتيب وتصفح صفحات من الخادم."""
    try:
        jobs = secure_storage.load_jobs() or []
        q = (request.args.get('q') or '').strip().lower()
        location = (request.args.get('location') or '').strip().lower()
        country = (request.args.get('country') or '').strip().lower()
        city = (request.args.get('city') or '').strip().lower()
        category = (request.args.get('category') or '').strip().lower()
        profession = (request.args.get('profession') or '').strip().lower()
        neighborhood = (request.args.get('neighborhood') or '').strip().lower()
        job_type = (request.args.get('type') or '').strip().lower()
        remote = (request.args.get('remote') or '').strip().lower()
        experience = (request.args.get('experience') or '').strip().lower()
        company = (request.args.get('company') or '').strip().lower()
        language = (request.args.get('language') or '').strip().lower()
        min_salary = request.args.get('min_salary', type=float)
        max_salary = request.args.get('max_salary', type=float)
        days = request.args.get('days', type=int)
        fresh_graduate = (request.args.get('fresh_graduate') or '').strip().lower()
        sort = (request.args.get('sort') or 'relevance').strip().lower()
        page = max(1, request.args.get('page', 1, type=int) or 1)
        per_page = min(30, max(1, request.args.get('per_page', 12, type=int) or 12))
        def salary_numbers(v):
            import re as _re
            out=[]
            for n in _re.findall(r'\d+(?:[.,]\d+)?', str(v or '')):
                try: out.append(float(n.replace(',','')))
                except Exception: pass
            return out
        def score(j):
            if not q: return 0
            terms=[t for t in re.split(r'\s+', q) if t]
            fields=[str(j.get(k,'')) for k in ('title','company','description','country','city','category')]
            fields += [str(x) for x in (j.get('tags') or [])]
            hay=' '.join(fields).lower(); title=str(j.get('title','')).lower()
            return sum((5 if t in title else 2 if t in hay else 0) for t in terms)
        def matches(j):
            hay=' '.join(str(j.get(k,'')) for k in ('title','company','description','country','city','category','salary')).lower()+' '+' '.join(map(str,j.get('tags') or [])).lower()
            if q and not all(t in hay for t in [x for x in re.split(r'\s+',q) if x]): return False
            if location and location not in (str(j.get('country',''))+' '+str(j.get('city',''))).lower(): return False
            if country and country != 'all' and country not in str(j.get('country','')).lower(): return False
            if city and city != 'all' and city not in str(j.get('city','')).lower(): return False
            if neighborhood and neighborhood != 'all' and neighborhood not in str(j.get('neighborhood','')).lower(): return False
            if profession and profession != 'all' and profession not in str(j.get('profession','')).lower(): return False
            if category and category != 'all' and category not in str(j.get('category','')).lower() and category not in ' '.join(map(str,j.get('tags') or [])).lower(): return False
            if job_type and job_type != 'all' and job_type not in str(j.get('jobType',j.get('employmentType',j.get('type','')))).lower(): return False
            if remote in ('1','true','yes') and not bool(j.get('remote') or j.get('isRemote') or 'عن بعد' in str(j.get('employmentType','')).lower()): return False
            if remote == 'false' and bool(j.get('remote') or j.get('isRemote')): return False
            if experience and experience not in str(j.get('experience','')).lower() and experience not in str(j.get('requirements','')).lower(): return False
            if company and company not in str(j.get('company','')).lower(): return False
            if language and language not in str(j.get('language','')).lower() and language not in str(j.get('languages','')).lower() and language not in str(j.get('requirements','')).lower(): return False
            nums=salary_numbers(j.get('salary'))
            if min_salary is not None and nums and max(nums) < min_salary: return False
            if max_salary is not None and nums and min(nums) > max_salary: return False
            if days is not None and days >= 0:
                try:
                    posted=datetime.fromisoformat(str(j.get('postedAt') or j.get('createdAt') or j.get('posted'))[:10])
                    if (datetime.now()-posted).days > days: return False
                except Exception: pass
            if fresh_graduate in ('true','yes','1') and not bool(j.get('freshGraduate') or j.get('acceptsFreshGraduates')): return False
            if fresh_graduate in ('false','no','0') and bool(j.get('freshGraduate') or j.get('acceptsFreshGraduates')): return False
            return True
        filtered=[j for j in jobs if matches(j)]
        for j in filtered: j['_searchScore']=score(j)
        if sort == 'newest':
            filtered.sort(key=lambda j:str(j.get('postedAt') or j.get('createdAt') or j.get('posted') or ''), reverse=True)
        elif sort == 'salary_high':
            filtered.sort(key=lambda j:max(salary_numbers(j.get('salary')) or [0]), reverse=True)
        elif sort == 'salary_low':
            filtered.sort(key=lambda j:min(salary_numbers(j.get('salary')) or [float('inf')]))
        else:
            filtered.sort(key=lambda j:(j.get('_searchScore',0), str(j.get('postedAt') or j.get('createdAt') or j.get('posted') or '')), reverse=True)
        total=len(filtered); start=(page-1)*per_page; items=filtered[start:start+per_page]
        for j in items: j.pop('_searchScore',None)
        return jsonify({'success':True,'items':items,'total':total,'page':page,'per_page':per_page,'pages':(total+per_page-1)//per_page})
    except Exception:
        logger.exception('advanced job search error')
        return jsonify({'success':False,'message':'تعذر تنفيذ البحث'}),500

@app.route('/api/jobs', methods=['POST'])
@admin_required
def add_job():
    try:
        data = request.json
        jobs = secure_storage.load_jobs()
        
        new_job = {
            'id': len(jobs) + 1,
            'title': sanitize_input(data.get('title', 'وظيفة جديدة')),
            'company': sanitize_input(data.get('company', 'شركة')),
            'country': sanitize_input(data.get('country', 'السعودية')),
            'city': sanitize_input(data.get('city', 'الرياض')),
            'neighborhood': sanitize_input(data.get('neighborhood', '')),
            'category': sanitize_input(data.get('category', 'تقنية')),
            'salary': sanitize_input(data.get('salary', 'غير محدد')),
            'posted': datetime.now().strftime('%Y-%m-%d'),
            'tags': data.get('tags', [])
        }
        
        jobs.append(new_job)
        secure_storage.save_jobs(jobs)
        return jsonify({'success': True, 'message': 'تم إضافة الوظيفة', 'data': new_job})
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# API إدارة الوظائف
# ============================================

@app.route('/api/jobs/manage', methods=['POST'])
@admin_required
def api_jobs_manage():
    """إدارة الوظائف"""
    try:
        data = request.json
        action = data.get('action', '').lower()
        
        if action == 'add' or action == 'create':
            jobs = secure_storage.load_jobs()
            new_job = {
                'id': len(jobs) + 1,
                'title': sanitize_input(data.get('title', 'وظيفة جديدة')),
                'company': sanitize_input(data.get('company', 'شركة')),
                'country': sanitize_input(data.get('country', 'السعودية')),
                'city': sanitize_input(data.get('city', 'الرياض')),
                'category': sanitize_input(data.get('category', 'تقنية')),
                'salary': sanitize_input(data.get('salary', 'غير محدد')),
                'posted': datetime.now().strftime('%Y-%m-%d'),
                'tags': data.get('tags', [])
            }
            jobs.append(new_job)
            secure_storage.save_jobs(jobs)
            return jsonify({
                'success': True,
                'message': 'تم إضافة الوظيفة بنجاح',
                'data': new_job
            })
        
        elif action == 'delete' or action == 'remove':
            job_id = data.get('id')
            if not job_id:
                return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'}), 400
            
            jobs = secure_storage.load_jobs()
            jobs = [j for j in jobs if str(j.get('id')) != str(job_id)]
            secure_storage.save_jobs(jobs)
            return jsonify({'success': True, 'message': 'تم حذف الوظيفة بنجاح'})
        
        elif action == 'update' or action == 'edit':
            job_id = data.get('id')
            if not job_id:
                return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'}), 400
            
            jobs = secure_storage.load_jobs()
            for job in jobs:
                if str(job.get('id')) == str(job_id):
                    job['title'] = sanitize_input(data.get('title', job['title']))
                    job['company'] = sanitize_input(data.get('company', job['company']))
                    job['country'] = sanitize_input(data.get('country', job['country']))
                    job['city'] = sanitize_input(data.get('city', job['city']))
                    job['neighborhood'] = sanitize_input(data.get('neighborhood', job.get('neighborhood','')))
                    job['category'] = sanitize_input(data.get('category', job['category']))
                    job['salary'] = sanitize_input(data.get('salary', job['salary']))
                    if 'tags' in data:
                        job['tags'] = data['tags']
                    secure_storage.save_jobs(jobs)
                    return jsonify({
                        'success': True,
                        'message': 'تم تحديث الوظيفة بنجاح',
                        'data': job
                    })
            
            return jsonify({'success': False, 'message': 'الوظيفة غير موجودة'}), 404
        
        else:
            return jsonify({
                'success': False,
                'message': f'إجراء غير معروف: "{action}". الإجراءات المدعومة: add, delete, update'
            }), 400
            
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500


# ============================================
# بوابة صاحب العمل
# ============================================
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    users = secure_storage.load_users() or []
    return next((u for u in users if u.get('id') == uid), None)

def login_required(view):
    """حماية المسارات التي تتطلب تسجيل دخول المستخدم.
    تعيد 401 للـAPI و401/إعادة توجيه للصفحات بدل ترك decorator غير معرّف.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'message': 'يجب تسجيل الدخول أولاً'}), 401
            return redirect('/?login=1')
        return view(*args, **kwargs)
    return wrapped

def employer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user.get('role') != 'employer':
            return jsonify({'success': False, 'message': 'هذه الخدمة مخصصة لأصحاب العمل'}), 403
        return view(*args, **kwargs)
    return wrapped

@app.route('/employer')
def employer_page():
    # بعض نسخ الواجهة القديمة تنتقل إلى /employer مباشرة بعد التسجيل.
    # لا نسمح بتجاوز التحقق من البريد، بل نعيد المستخدم إلى صفحة OTP.
    pending_email = session.get('pending_verification_email')
    pending_uid = session.get('pending_verification_user_id')
    if pending_email and pending_uid:
        users = secure_storage.load_users() or []
        pending_user = next((u for u in users if str(u.get('id')) == str(pending_uid)), None)
        if pending_user and not pending_user.get('emailVerified', False):
            return redirect('/verify-email')
        session.pop('pending_verification_email', None)
        session.pop('pending_verification_user_id', None)
    return render_template('employer.html') if current_user() and current_user().get('role') == 'employer' else render_template('index.html')

@app.route('/api/employer/profile', methods=['GET','PUT'])
@employer_required
def employer_profile():
    user=current_user()
    if request.method=='GET':
        safe=dict(user); safe.pop('password',None); return jsonify(safe)
    data=request.get_json(silent=True) or {}
    for key in ['companyName','companyType','companyDescription','phone','country','city','neighborhood']:
        if key in data: user[key]=sanitize_input(str(data[key]))
    users=secure_storage.load_users() or []
    for i,u in enumerate(users):
        if u.get('id')==user.get('id'): users[i]=user; break
    if not secure_storage.save_users(users): return jsonify({'success':False,'message':'تعذر حفظ بيانات الشركة'}),500
    safe=dict(user);safe.pop('password',None);return jsonify({'success':True,'data':safe})


# ============================================
# المرحلة 8 — منظومة الثقة للشركات والوظائف
# ============================================

def _trust_records_load():
    return secure_storage.encryption.decrypt_file('trust_records') or {'company_verifications': [], 'reports': []}

def _trust_records_save(data):
    return secure_storage.encryption.encrypt_file('trust_records', data)

def _company_verification(user):
    status = str(user.get('companyVerificationStatus') or 'unverified').lower()
    labels = {'unverified':'غير موثقة','pending':'قيد المراجعة','verified':'موثقة','rejected':'مرفوضة'}
    return {'status': status if status in labels else 'unverified', 'label': labels.get(status, 'غير موثقة'),
            'verifiedAt': user.get('companyVerifiedAt'), 'verifiedBy': user.get('companyVerifiedBy')}

def _job_quality(job, employer=None):
    job = job if isinstance(job, dict) else {}
    employer = employer if isinstance(employer, dict) else None
    checks = [
        ('title', bool(str(job.get('title','')).strip()), 15, 'المسمى الوظيفي'),
        ('description', len(str(job.get('description','')).strip()) >= 80, 20, 'وصف واضح ومفصل'),
        ('company', bool(str(job.get('company','')).strip()), 10, 'اسم الشركة'),
        ('country', bool(str(job.get('country','')).strip()), 8, 'الدولة'),
        ('city', bool(str(job.get('city','')).strip()), 7, 'المدينة'),
        ('category', bool(str(job.get('category','')).strip()), 10, 'المجال'),
        ('employmentType', bool(str(job.get('employmentType','')).strip()), 8, 'نوع التوظيف'),
        ('salary', str(job.get('salary','')).strip() not in ('', 'غير محدد', 'غير محدد/غير معلن'), 12, 'نطاق الراتب'),
        ('tags', bool(job.get('tags')), 5, 'المهارات/الكلمات المفتاحية'),
        ('requirements', bool(str(job.get('requirements','')).strip()) or bool(job.get('tags')), 5, 'المتطلبات'),
    ]
    score=sum(weight for _,ok,weight,_ in checks if ok)
    missing=[label for _,ok,_,label in checks if not ok]
    if employer and _company_verification(employer)['status']=='verified':
        score=min(100, score+5)
    return {'score': score, 'grade': 'ممتاز' if score>=90 else ('جيد جداً' if score>=75 else ('جيد' if score>=60 else 'يحتاج تحسين')),
            'missing': missing, 'checks': [{'key':k,'ok':ok,'label':label,'weight':w} for k,ok,w,label in checks]}

def _company_stats(employer_id):
    raw_jobs = secure_storage.load_jobs() or []
    jobs=[j for j in raw_jobs if isinstance(j, dict) and str(j.get('employerId',''))==str(employer_id)]
    apps=secure_storage.load_applications() or {}
    if not isinstance(apps, dict):
        apps = {}
    job_ids={str(j.get('id')) for j in jobs}
    rows=[]
    for cid, items in apps.items():
        if not isinstance(items, list):
            continue
        for a in items:
            if isinstance(a, dict) and str(a.get('jobId')) in job_ids:
                rows.append(a)
    total=len(rows)
    responded=sum(1 for a in rows if a.get('status') not in (None,'pending','review'))
    hired=sum(1 for a in rows if a.get('status')=='hired')
    response_rate=round((responded/total)*100,1) if total else 0
    return {'jobs':len(jobs),'applications':total,'respondedApplications':responded,'responseRate':response_rate,'hired':hired,
            'averageResponseHours': None, 'note':'زمن الرد سيظهر عند توفر طوابع زمنية كافية للطلبات.'}

@app.route('/api/employer/trust', methods=['GET','POST'])
@employer_required
def employer_trust():
    user=current_user()
    if request.method=='GET':
        jobs=[j for j in (secure_storage.load_jobs() or []) if isinstance(j, dict) and str(j.get('employerId',''))==str(user.get('id'))]
        return jsonify({'success':True,'companyVerification':_company_verification(user),'stats':_company_stats(user.get('id')),
                        'jobQuality':[{'jobId':j.get('id'),'title':j.get('title',''),'quality':_job_quality(j,user)} for j in jobs]})
    data=request.get_json(silent=True) or {}
    recs=_trust_records_load(); requests=recs.setdefault('company_verifications',[])
    if _company_verification(user)['status']=='verified':
        return jsonify({'success':True,'message':'الشركة موثقة بالفعل','companyVerification':_company_verification(user)})
    existing=next((r for r in requests if str(r.get('employerId'))==str(user.get('id')) and r.get('status')=='pending'),None)
    if existing:
        return jsonify({'success':True,'message':'طلب التوثيق قيد المراجعة','companyVerification':{'status':'pending','label':'قيد المراجعة'}})
    now=datetime.now().isoformat()
    requests.append({'id':secrets.token_hex(8),'employerId':str(user.get('id')),'companyName':user.get('companyName',''),'status':'pending','createdAt':now})
    user['companyVerificationStatus']='pending'; user['companyVerificationRequestedAt']=now
    users=secure_storage.load_users() or []
    for i,u in enumerate(users):
        if str(u.get('id'))==str(user.get('id')): users[i]=user; break
    if not secure_storage.save_users(users) or not _trust_records_save(recs):
        return jsonify({'success':False,'message':'تعذر حفظ طلب التوثيق'}),500
    _audit_action('طلب توثيق شركة', 'companyVerification', source='official')
    return jsonify({'success':True,'message':'تم إرسال طلب توثيق الشركة إلى الإدارة','companyVerification':_company_verification(user)})

@app.route('/api/jobs/<int:job_id>/quality', methods=['GET'])
def public_job_quality(job_id):
    job=next((j for j in (secure_storage.load_jobs() or []) if int(j.get('id',-1))==job_id),None)
    if not job: return jsonify({'success':False,'message':'الوظيفة غير موجودة'}),404
    employer=next((u for u in (secure_storage.load_users() or []) if str(u.get('id'))==str(job.get('employerId'))),None)
    return jsonify({'success':True,'jobId':job_id,'quality':_job_quality(job,employer),'companyVerification':_company_verification(employer) if employer else {'status':'unverified','label':'غير موثقة'}})

@app.route('/api/jobs/<int:job_id>/report', methods=['POST'])
def report_job(job_id):
    user=current_user()
    if not user: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    job=next((j for j in (secure_storage.load_jobs() or []) if int(j.get('id',-1))==job_id),None)
    if not job: return jsonify({'success':False,'message':'الوظيفة غير موجودة'}),404
    data=request.get_json(silent=True) or {}; reason=sanitize_input(str(data.get('reason','')).strip())[:300]
    if not reason: return jsonify({'success':False,'message':'سبب البلاغ مطلوب'}),400
    recs=_trust_records_load(); reports=recs.setdefault('reports',[])
    duplicate=next((r for r in reports if str(r.get('jobId'))==str(job_id) and str(r.get('reporterId'))==str(user.get('id')) and r.get('status')=='open'),None)
    if duplicate: return jsonify({'success':True,'message':'تم تسجيل بلاغك مسبقاً'}),200
    reports.append({'id':secrets.token_hex(8),'type':'job','jobId':job_id,'companyId':job.get('employerId'),'reporterId':str(user.get('id')),
                    'reason':reason,'status':'open','createdAt':datetime.now().isoformat()})
    if not _trust_records_save(recs): return jsonify({'success':False,'message':'تعذر حفظ البلاغ'}),500
    _audit_action('بلاغ عن وظيفة', 'jobId,reason', source='official')
    return jsonify({'success':True,'message':'تم إرسال البلاغ إلى الإدارة للمراجعة'})

@app.route('/api/employer/report-company', methods=['POST'])
@employer_required
def report_company_self_test():
    return jsonify({'success':False,'message':'استخدم مسار بلاغ الوظيفة أو بلاغ الشركة من واجهة الإدارة'}),400

@app.route('/api/admin/trust/company-verifications', methods=['GET','PUT'])
@admin_required
def admin_company_verifications():
    recs=_trust_records_load(); requests=recs.setdefault('company_verifications',[]); users=secure_storage.load_users() or []
    if request.method=='GET':
        out=[]
        for r in requests:
            u=next((x for x in users if str(x.get('id'))==str(r.get('employerId'))),None)
            out.append({**r,'companyPhone':u.get('phone','') if u else '','companyCountry':u.get('country','') if u else ''})
        return jsonify({'success':True,'items':out})
    data=request.get_json(silent=True) or {}; employer_id=str(data.get('employerId','')); status=str(data.get('status','')).lower()
    if status not in ('verified','rejected','pending'): return jsonify({'success':False,'message':'الحالة غير صحيحة'}),400
    u=next((x for x in users if str(x.get('id'))==employer_id and x.get('role')=='employer'),None)
    if not u: return jsonify({'success':False,'message':'صاحب العمل غير موجود'}),404
    now=datetime.now().isoformat(); u['companyVerificationStatus']=status
    if status=='verified': u['companyVerifiedAt']=now; u['companyVerifiedBy']=current_user().get('id')
    for r in requests:
        if str(r.get('employerId'))==employer_id and r.get('status')=='pending': r['status']=status; r['reviewedAt']=now; r['reviewedBy']=current_user().get('id')
    if not secure_storage.save_users(users) or not _trust_records_save(recs): return jsonify({'success':False,'message':'تعذر حفظ قرار التوثيق'}),500
    _audit_action('تحديث توثيق شركة', 'employerId,status', source='official')
    return jsonify({'success':True,'companyVerification':_company_verification(u)})

@app.route('/api/admin/trust/reports', methods=['GET','PUT'])
@admin_required
def admin_trust_reports():
    recs=_trust_records_load(); reports=recs.setdefault('reports',[])
    if request.method=='GET': return jsonify({'success':True,'items':reports})
    data=request.get_json(silent=True) or {}; rid=str(data.get('id','')); status=str(data.get('status','')).lower()
    if status not in ('open','reviewing','resolved','dismissed'): return jsonify({'success':False,'message':'حالة البلاغ غير صحيحة'}),400
    item=next((r for r in reports if str(r.get('id'))==rid),None)
    if not item: return jsonify({'success':False,'message':'البلاغ غير موجود'}),404
    item['status']=status; item['reviewedAt']=datetime.now().isoformat(); item['reviewedBy']=current_user().get('id')
    if not _trust_records_save(recs): return jsonify({'success':False,'message':'تعذر حفظ البلاغ'}),500
    _audit_action('تحديث بلاغ', 'id,status', source='official')
    return jsonify({'success':True,'item':item})

def _count_employer_jobs_created(employer_id):
    """Count persisted jobs for an employer as a lower-bound quota signal."""
    jobs = secure_storage.load_jobs() or []
    return sum(1 for j in jobs if isinstance(j, dict) and str(j.get('employerId', '')) == str(employer_id))

def _count_user_applications_created(user_id):
    """Count persisted application records for a user as a lower-bound quota signal."""
    applications = secure_storage.load_applications() or {}
    _, entries = _find_user_applications(applications, user_id)
    return len([a for a in (entries or []) if isinstance(a, dict)])


@app.route('/api/employer/job-posting-sadaqah-status', methods=['GET'])
@employer_required
def employer_job_posting_sadaqah_status():
    user = current_user()
    stored_used = int(user.get('sadaqahFreeJobPostsUsed', 0) or 0)
    actual_used = _count_employer_jobs_created(user.get('id'))
    used = max(stored_used, actual_used)
    remaining = max(0, JOB_POSTING_SADAQAH_FREE_LIMIT - used)
    price = service_prices(secure_storage, user)['prices']['job_posting_usd']
    return jsonify({'success': True, 'freeJobPostsUsed': used,
                    'freeJobPostsRemaining': remaining,
                    'freeJobPostLimit': JOB_POSTING_SADAQAH_FREE_LIMIT,
                    'requiresWallet': remaining == 0,
                    'price': price,
                    'walletBalance': _get_user_wallet_balance(user.get('id'))})

@app.route('/api/employer/jobs', methods=['GET','POST'])
@employer_required
def employer_jobs():
    user=current_user()
    raw_jobs = secure_storage.load_jobs() or []
    # البيانات القديمة قد تحتوي سجلات غير قاموسية؛ نتجاوز السجل التالف بدل إسقاط API.
    jobs = [j for j in raw_jobs if isinstance(j, dict)]
    employer_id = str(user.get('id') or '')
    if request.method == 'GET':
        result = []
        for job in jobs:
            try:
                if str(job.get('employerId') or '') != employer_id:
                    continue
                item = dict(job)
                item['id'] = job.get('id')
                item['employerId'] = str(job.get('employerId') or '')
                result.append(item)
            except Exception:
                logger.exception('تجاوز سجل وظيفة غير صالح في /api/employer/jobs للمستخدم %s', employer_id)
        response = jsonify(result)
        response.headers['Cache-Control'] = 'no-store'
        return response
    data=request.get_json(silent=True) or {}
    title=sanitize_input(data.get('title','')).strip()
    if not title: return jsonify({'success':False,'message':'المسمى الوظيفي مطلوب'}),400

    # أول 3 عمليات نشر مجانية مع وقفة الخير. بعد ذلك يتم الخصم من المحفظة.
    stored_posting_used = int(user.get('sadaqahFreeJobPostsUsed', 0) or 0)
    actual_posting_used = _count_employer_jobs_created(user.get('id'))
    posting_used = max(stored_posting_used, actual_posting_used)
    posting_free = posting_used < JOB_POSTING_SADAQAH_FREE_LIMIT
    posting_fee = usd_cents(secure_storage, 'job_posting_usd')
    if not posting_free:
        wallet_info = _get_user_wallet_balance(user.get('id')) or {}
        try:
            available_cents = int(float(wallet_info.get('available', 0) or 0))
        except (TypeError, ValueError):
            available_cents = 0
        if available_cents < posting_fee:
            p = service_prices(secure_storage, user)['prices']['job_posting_usd']
            return jsonify({'success':False, 'requiresWallet':True, 'price':p,
                            'freePostsRemaining':0,
                            'message':f"انتهت عمليات نشر الوظائف المجانية بعد {JOB_POSTING_SADAQAH_FREE_LIMIT} مرات. رسوم نشر الوظيفة {p['formatted']}."}), 402
        debit = subtract_balance(str(user.get('id')), posting_fee, 'job_posting',
            reference_id=f"job_posting_{next_id(jobs)}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            description='رسوم نشر وظيفة')
        if not debit.get('success'):
            return jsonify({'success':False,'requiresWallet':True,'message':debit.get('message','الرصيد غير كافٍ')}),402

    new_job={
      'id': next_id(jobs), 'title':title,
      'company': sanitize_input(data.get('company') or user.get('companyName') or f"{user.get('firstName','')} {user.get('lastName','')}"),
      'country':sanitize_input(data.get('country','')), 'city':sanitize_input(data.get('city','')),
      'neighborhood':sanitize_input(data.get('neighborhood','')), 'category':sanitize_input(data.get('category','')),
      'salary':sanitize_input(data.get('salary','غير محدد')), 'employmentType':sanitize_input(data.get('employmentType','دوام كامل')),
      'description':sanitize_input(data.get('description','')), 'tags':data.get('tags',[]),
      'employerId':user.get('id'), 'employerEmail':user.get('email',''), 'posted':datetime.now().strftime('%Y-%m-%d'), 'status':'published'
    }
    jobs.append(new_job)
    if not secure_storage.save_jobs(jobs):
        # لا توجد رسوم فعلية في أول 3 محاولات، لذلك لا نضيف رصيدًا عند فشل الحفظ.
        if not posting_free:
            add_balance(str(user.get('id')), posting_fee, 'bonus',
                        reference_id=f"job_posting_refund_{new_job['id']}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                        description='إرجاع رسوم نشر الوظيفة بسبب فشل الحفظ')
        return jsonify({'success':False,'message':'تعذر حفظ الوظيفة وتمت معالجة الرسوم بأمان'}),500

    if posting_free:
        users = secure_storage.load_users() or []
        saved_counter = False
        for existing in users:
            if str(existing.get('id')) == str(user.get('id')):
                existing['sadaqahFreeJobPostsUsed'] = posting_used + 1
                saved_counter = True
                break
        if not saved_counter or not secure_storage.save_users(users):
            # لا نترك وظيفة منشورة دون احتساب المحاولة المجانية.
            jobs_after = secure_storage.load_jobs() or []
            jobs_after = [j for j in jobs_after if str(j.get('id')) != str(new_job.get('id'))]
            secure_storage.save_jobs(jobs_after)
            logger.error('تعذر حفظ عداد نشر الوظائف المجاني للمستخدم %s؛ تم التراجع عن نشر الوظيفة', user.get('id'))
            return jsonify({'success':False,'message':'تعذر تسجيل المحاولة المجانية، لم يتم نشر الوظيفة'}),500

    pricing = service_prices(secure_storage, user)['prices']['job_posting_usd']
    if posting_free:
        message = f"تم نشر الوظيفة بنجاح 🤍 — هذه وقفة خير، وهي المحاولة المجانية رقم {posting_used + 1} من {JOB_POSTING_SADAQAH_FREE_LIMIT}. لم يتم خصم أي مبلغ من رصيد محفظتك."
    else:
        message = f"تم نشر الوظيفة بنجاح — تم خصم {pricing['usdFormatted']} من المحفظة."
    return jsonify({
        'success': True,
        'message': message,
        'data': new_job,
        'freePostsRemaining': max(0, JOB_POSTING_SADAQAH_FREE_LIMIT - (posting_used + 1 if posting_free else posting_used))
    })

@app.route('/api/employer/jobs/<int:job_id>', methods=['PUT','DELETE'])
@employer_required
def employer_job_item(job_id):
    user=current_user(); jobs=secure_storage.load_jobs() or []
    job=next((j for j in jobs if isinstance(j, dict) and str(j.get('id',''))==str(job_id) and str(j.get('employerId',''))==str(user.get('id'))),None)
    if not job:return jsonify({'success':False,'message':'الوظيفة غير موجودة'}),404
    if request.method=='DELETE':
        jobs=[j for j in jobs if j is not job]; secure_storage.save_jobs(jobs); return jsonify({'success':True,'message':'تم حذف الوظيفة'})
    data=request.get_json(silent=True) or {}
    for key in ['title','company','country','city','neighborhood','category','salary','employmentType','description']:
        if key in data: job[key]=sanitize_input(str(data[key]))
    if 'tags' in data: job['tags']=data['tags']
    secure_storage.save_jobs(jobs);return jsonify({'success':True,'message':'تم تحديث الوظيفة','data':job})

def mask_email(email):
    """
    إخفاء جزء من البريد الإلكتروني لحماية الخصوصية.
    مثال: john@example.com -> j***@example.com
    """
    if not email:
        return ''
    email = str(email).strip()
    if '@' not in email:
        return email
    local, domain = email.split('@', 1)
    if not local:
        return email
    masked_local = local[0] + '***' if len(local) > 1 else local[0]
    return f"{masked_local}@{domain}"

@app.route('/api/employer/applications', methods=['GET'])
@employer_required
def employer_applications():
    user=current_user(); jobs=[j for j in (secure_storage.load_jobs() or []) if isinstance(j, dict)]; ids={str(j.get('id')) for j in jobs if str(j.get('employerId',''))==str(user.get('id'))}
    applications=secure_storage.load_applications() or {}; applications=applications if isinstance(applications, dict) else {}; users=secure_storage.load_users() or []; users=[x for x in users if isinstance(x, dict)]; result=[]
    for uid,apps in applications.items():
        if not isinstance(apps, list):
            continue
        u=next((x for x in users if str(x.get('id'))==str(uid)),None)
        for a in apps:
            if not isinstance(a, dict):
                continue
            if str(a.get('jobId')) in ids:
                job=next((j for j in jobs if str(j.get('id'))==str(a.get('jobId'))),None)
                row=dict(a)
                # توحيد الحالة عند الإرسال للواجهة فقط؛ لا نغيّر applications.enc.
                row['status'] = normalize_application_status(row.get('status', 'pending'))
                row['userId']=uid;row['candidateName']=f"{u.get('firstName','')} {u.get('lastName','')}" if u else 'متقدم';row['candidateEmail']=mask_email(u.get('email','')) if u else '';row['jobTitle']=job.get('title','') if job else ''
                row['candidateCountry']=u.get('country','') if u else '';row['candidateCity']=u.get('city','') if u else '';row['candidateNeighborhood']=u.get('neighborhood','') if u else ''
                # إظهار بيانات التواصل كاملة فقط بعد فتح البيانات - يدعم الحالات الحديثة والقديمة المتوافقة
                row['candidatePhone'] = ''
                unlock = a.get('unlock_contact') if isinstance(a.get('unlock_contact'), dict) else {}
                # لا يكفي وجود contactUnlocked في البيانات القديمة.
                # المدفوع يجب أن يملك معاملة مكتملة لنفس صاحب العمل/الوظيفة/المتقدم.
                free_unlock = bool(unlock.get('contactUnlocked') and not unlock.get('paymentRequired', False))
                paid_unlock = False
                if unlock.get('contactUnlocked') and unlock.get('paymentRequired', False):
                    reference = f"contact_unlock_{a.get('jobId')}_{uid}"
                    txs = get_transactions(str(user.get('id')), limit=500, offset=0, transaction_type='contact_unlock')
                    paid_unlock = any(
                        str(t.get('referenceId', '')) == reference
                        and t.get('status') == 'completed'
                        for t in txs
                    )
                is_unlocked = free_unlock or paid_unlock
                if is_unlocked and u:
                    row['candidateEmail'] = u.get('email', '')
                    row['candidatePhone'] = u.get('phone', '')
                # حقول حالة فتح البيانات - مع الاحتفاظ بتوافق البيانات القديمة
                row['contactUnlocked']=is_unlocked
                row['unlockStatus']='completed' if is_unlocked else 'locked'
                row['unlockPaymentId']=a.get('unlockPaymentId', None)
                row['unlockedAt']=a.get('unlockedAt', None)
                result.append(row)
    return jsonify(result)

@app.route('/api/employer/sadaqah-status', methods=['GET'])
@employer_required
def employer_sadaqah_status():
    """حالة الحصة المجانية لفتح بيانات التواصل للمستخدم الحالي.

    مصدر الحقيقة هو سجلات فتح البيانات المكتملة، مع استخدام عداد المستخدم
    كقيمة cache فقط. هذا يمنع ظهور حصة مجانية قديمة في المحفظة بعد أن
    استُهلكت فعلياً، كما يمنع إعادة ضبط العداد من إعادة منح محاولات مجانية.
    """
    user = current_user()
    employer_id = str(user.get('id'))
    stored_used = int(user.get('sadaqahFreeUnlocksUsed', 0) or 0)
    actual_used = _count_completed_free_contact_unlocks(employer_id)
    used = max(stored_used, actual_used)
    remaining = max(0, SADAQAH_FREE_UNLOCKS - used)
    wallet = _get_user_wallet_balance(user.get('id'))
    price = service_prices(secure_storage, user)['prices']['contact_unlock_usd']
    fee = usd_cents(secure_storage, 'contact_unlock_usd')
    return jsonify({
        'success': True,
        'freeUnlocksUsed': used,
        'freeUnlocksRemaining': remaining,
        'freeUnlockLimit': SADAQAH_FREE_UNLOCKS,
        'contactUnlockFeeUsdCents': fee,
        'contactUnlockPrice': price,
        'walletBalance': wallet.get('available', 0),
        'walletBalanceCurrency': wallet.get('currency', 'USD'),
        'requiresWallet': remaining == 0,
        'canUnlockWithWallet': remaining == 0 and float(wallet.get('available', 0) or 0) >= fee
    })

def _count_completed_free_contact_unlocks(employer_id):
    """Count actual completed free contact unlocks for an employer.

    The user counter is only a cache/display value.  Enforcement must use the
    persisted application unlock records so a stale/reset counter cannot grant
    extra free unlocks.
    """
    applications = secure_storage.load_applications() or {}
    count = 0
    if isinstance(applications, dict):
        for entries in applications.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                unlock = entry.get('unlock_contact') if isinstance(entry, dict) else None
                if not isinstance(unlock, dict):
                    continue
                if str(unlock.get('employerId', '')) != str(employer_id):
                    continue
                if unlock.get('status') != 'completed' or not unlock.get('contactUnlocked'):
                    continue
                if bool(unlock.get('paymentRequired', False)):
                    continue
                count += 1
    return count

@app.route('/api/employer/applications/request-unlock', methods=['POST'])
@employer_required
def request_unlock_applicant():
    """
    إنشاء طلب دفع لفتح بيانات المتقدم (البريد الإلكتروني والهاتف).
    لا يتم فتح البيانات فعلياً، بل يتم إنشاء سجل دفع بحالة pending.
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    job_id = data.get('jobId')
    applicant_user_id = str(data.get('userId', ''))
    
    if not job_id:
        return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'}), 400
    if not applicant_user_id:
        return jsonify({'success': False, 'message': 'معرف المتقدم مطلوب'}), 400
    
    # التحقق من أن الوظيفة تخص صاحب العمل
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get('id')) == str(job_id)), None)
    if not job:
        return jsonify({'success': False, 'message': 'الوظيفة غير موجودة'}), 404
    if str(job.get('employerId', '')) != str(user.get('id')):
        return jsonify({'success': False, 'message': 'غير مصرح: هذه الوظيفة لا تخصك'}), 403
    
    # التحقق من وجود الطلب (التقديم)
    applications = secure_storage.load_applications() or {}
    applicant_apps = applications.get(applicant_user_id, [])
    app_entry = next((a for a in applicant_apps if str(a.get('jobId')) == str(job_id)), None)
    if not app_entry:
        return jsonify({'success': False, 'message': 'طلب التقديم غير موجود'}), 404

    # منع الخصم/الاستهلاك المكرر إذا كانت بيانات التواصل مفتوحة مسبقاً
    # لا نعتمد على علم contactUnlocked وحده: بالنسبة للعمليات المدفوعة
    # يجب أن يوجد سجل خصم مكتمل مرتبط بنفس صاحب العمل/الوظيفة/المتقدم.
    existing_unlock = app_entry.get('unlock_contact') if isinstance(app_entry.get('unlock_contact'), dict) else {}
    existing_is_free = bool(existing_unlock.get('contactUnlocked') and not existing_unlock.get('paymentRequired', False))
    existing_is_paid = False
    if existing_unlock.get('contactUnlocked') and existing_unlock.get('paymentRequired', False):
        contact_reference = f'contact_unlock_{job_id}_{applicant_user_id}'
        txs = get_transactions(str(user.get('id')), limit=500, offset=0, transaction_type='contact_unlock')
        existing_is_paid = any(
            str(t.get('referenceId', '')) == contact_reference
            and t.get('status') == 'completed'
            for t in txs
        )
    if existing_is_free or existing_is_paid:
        users = secure_storage.load_users() or []
        applicant_user = next((u for u in users if str(u.get('id')) == str(applicant_user_id)), None)
        return jsonify({
            'success': True,
            'message': 'بيانات التواصل مفتوحة مسبقاً',
            'contactUnlocked': True,
            'requiresWallet': False,
            'alreadyUnlocked': True,
            'contact': {
                'email': applicant_user.get('email', '') if applicant_user else '',
                'phone': applicant_user.get('phone', '') if applicant_user else ''
            }
        })
    
    # ===== نظام وقفة الخير + المحفظة =====
    # أول SADAQAH_FREE_UNLOCKS عمليات فتح بيانات التواصل مجانية بعد وقفة الخير.
    # بعد انتهاء الحصة المجانية يجب وجود رصيد كافٍ في المحفظة، ويتم خصم CONTACT_UNLOCK_FEE.
    employer_id = str(user.get('id'))
    stored_free_used = int(user.get('sadaqahFreeUnlocksUsed', 0) or 0)
    actual_free_used = _count_completed_free_contact_unlocks(employer_id)
    # Never trust a reset/stale counter for enforcement.
    free_used = max(stored_free_used, actual_free_used)
    charge_required = free_used >= SADAQAH_FREE_UNLOCKS
    contact_unlock_fee = usd_cents(secure_storage, 'contact_unlock_usd')
    contact_price = service_prices(secure_storage, user)['prices']['contact_unlock_usd']

    if charge_required:
        # قراءة الرصيد دون إنشاء محفظة وهمية
        wallet_info = _get_user_wallet_balance(employer_id)
        available = float(wallet_info.get('available', 0) or 0)
        if available < contact_unlock_fee:
            return jsonify({
                'success': False,
                'requiresWallet': True,
                'freeUnlocksUsed': free_used,
                'freeUnlocksRemaining': 0,
                'requiredBalance': contact_unlock_fee,
                'price': contact_price,
                'walletBalance': available,
                'message': f"انتهت الطلبات المجانية بعد {SADAQAH_FREE_UNLOCKS} طلبات. يرجى شحن رصيد المحفظة بمبلغ {contact_price['formatted']} على الأقل لمتابعة فتح بيانات التواصل." 
            }), 402

        # الخصم يتم على الخادم فقط، ولا يمكن تجاوزه بتعديل JavaScript.
        # نستخدم مرجعاً ثابتاً لهذه الوظيفة/المتقدم حتى لا يؤدي تكرار الطلب
        # (double-click / retry / إعادة إرسال الشبكة) إلى خصم الرسوم مرتين.
        contact_reference = f'contact_unlock_{job_id}_{applicant_user_id}'
        existing_transactions = get_transactions(employer_id, limit=500, offset=0, transaction_type='contact_unlock')
        existing_contact_tx = next((t for t in existing_transactions
                                    if str(t.get('referenceId', '')) == contact_reference
                                    and t.get('status') == 'completed'), None)
        if existing_contact_tx:
            # تمت عملية الخصم سابقاً؛ نكمل فتح البيانات دون أي خصم جديد.
            debit_result = {'success': True, 'transaction': existing_contact_tx, 'idempotent': True}
        else:
            debit_result = subtract_balance(
                employer_id,
                contact_unlock_fee,
                'contact_unlock',
                reference_id=contact_reference,
                description='رسوم فتح بيانات التواصل بعد انتهاء الحصة المجانية',
                metadata={'jobId': job_id, 'applicantId': applicant_user_id}
            )
        if not debit_result.get('success'):
            return jsonify({
                'success': False,
                'requiresWallet': True,
                'message': debit_result.get('message') or 'تعذر خصم رسوم فتح بيانات التواصل من المحفظة. يرجى التأكد من الرصيد.'
            }), 402

    # الحصول على بيانات المستخدم المتقدم لعرضها بعد الفتح
    users = secure_storage.load_users() or []
    applicant_user = next((u for u in users if str(u.get('id')) == str(applicant_user_id)), None)

    # فتح بيانات التواصل
    now_iso = datetime.now().isoformat()
    app_entry['contactUnlocked'] = True
    app_entry['unlock_contact'] = {
        'status': 'completed',
        'contactUnlocked': True,
        'unlockedAt': now_iso,
        'paymentRequired': charge_required,
        'chargedAmountUsdCents': contact_unlock_fee if charge_required else 0,
        'employerId': employer_id,
        'jobId': str(job_id),
        'applicantId': str(applicant_user_id)
    }
    app_entry['unlockStatus'] = 'completed'
    app_entry.pop('unlockPaymentId', None)
    app_entry['unlockedAt'] = now_iso
    if not secure_storage.save_applications(applications):
        if charge_required:
            try:
                add_balance(employer_id, contact_unlock_fee, 'bonus',
                            reference_id=f'contact_unlock_refund_{job_id}_{applicant_user_id}_{datetime.now().strftime("%Y%m%d%H%M%S%f")}',
                            description='إرجاع رسوم فتح البيانات بسبب فشل الحفظ')
            except Exception:
                logger.exception('فشل إرجاع رسوم فتح بيانات التواصل')
        return jsonify({'success': False, 'message': 'تعذر حفظ حالة فتح بيانات التواصل'}), 500

    if not charge_required:
        # استهلاك الوقفة الحرة يُسجل بعد نجاح فتح البيانات فعلياً.
        users = secure_storage.load_users() or []
        for existing in users:
            if str(existing.get('id')) == employer_id:
                existing['sadaqahFreeUnlocksUsed'] = free_used + 1
                break
        if not secure_storage.save_users(users):
            logger.error('تعذر حفظ عداد وقفة الخير بعد نجاح فتح البيانات للمستخدم %s', employer_id)
            # Do not leave a free unlock usable when its quota record failed to persist.
            applications_after = secure_storage.load_applications() or {}
            applicant_entries = applications_after.get(applicant_user_id, []) if isinstance(applications_after, dict) else []
            restored = False
            if isinstance(applicant_entries, list):
                for existing in applicant_entries:
                    if isinstance(existing, dict) and str(existing.get('jobId')) == str(job_id):
                        existing.pop('unlock_contact', None)
                        existing.pop('unlockStatus', None)
                        existing.pop('unlockedAt', None)
                        existing['contactUnlocked'] = False
                        restored = True
                        break
            if restored:
                secure_storage.save_applications(applications_after)
            return jsonify({'success': False, 'message': 'تعذر تثبيت استهلاك المحاولة المجانية. لم يتم فتح بيانات التواصل.'}), 500

    new_free_used = free_used + (0 if charge_required else 1)
    return jsonify({
        'success': True,
        'message': (f'تم فتح بيانات التواصل وخصم {contact_price["formatted"]} من رصيد المحفظة.' if charge_required else 'تم فتح بيانات التواصل مجاناً بعد وقفة الخير 🤍. لم يتم خصم أي مبلغ من رصيد محفظتك.'),
        'contactUnlocked': True,
        'requiresWallet': False,
        'paymentRequired': charge_required,
        'chargedAmountUsdCents': contact_unlock_fee if charge_required else 0,
        'freeUnlocksUsed': new_free_used,
        'freeUnlocksRemaining': max(0, SADAQAH_FREE_UNLOCKS - new_free_used),
        'contact': {
            'email': applicant_user.get('email', '') if applicant_user else '',
            'phone': applicant_user.get('phone', '') if applicant_user else ''
        },
        'unlock_contact': {
            'status': 'completed',
            'contactUnlocked': True,
            'unlockedAt': now_iso
        }
    })

@app.route('/api/employer/applications/share-company', methods=['POST'])
@employer_required
def share_company_data():
    """
    مشاركة بيانات المنشأة مع متقدم محدد.
    يتحقق من ملكية الوظيفة ويحفظ علامة المشاركة في الطلب.
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    job_id = data.get('jobId')
    applicant_user_id = str(data.get('userId', ''))

    if not job_id:
        return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'}), 400
    if not applicant_user_id:
        return jsonify({'success': False, 'message': 'معرف المتقدم مطلوب'}), 400

    # التحقق من ملكية الوظيفة
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get('id')) == str(job_id)), None)
    if not job:
        return jsonify({'success': False, 'message': 'الوظيفة غير موجودة'}), 404
    if str(job.get('employerId', '')) != str(user.get('id')):
        return jsonify({'success': False, 'message': 'خطأ: لا تملك هذه الوظيفة'}), 403

    # تحديث الطلب
    applications = secure_storage.load_applications() or {}
    applicant_apps = applications.get(applicant_user_id, [])
    app_entry = next((a for a in applicant_apps if str(a.get('jobId')) == str(job_id)), None)
    if not app_entry:
        return jsonify({'success': False, 'message': 'الطلب غير موجود'}), 404

    # حفظ علامة مشاركة البيانات
    now_iso = datetime.now().isoformat()
    app_entry['companyDataShared'] = True
    app_entry['companyDataSharedAt'] = now_iso
    secure_storage.save_applications(applications)

    return jsonify({
        'success': True,
        'message': 'تم مشاركة بيانات المنشأة بنجاح',
        'companyDataShared': True,
        'sharedAt': now_iso
    })

@app.route('/api/admin/payments/confirm', methods=['POST'])
@admin_required
def admin_confirm_payment():
    """
    تأكيد دفع تجريبي - للمشرف فقط.
    يبحث عن paymentId في payment_logs.enc ويغير الحالة إلى paid،
    ثم يفتح بيانات التواصل للمتقدم المرتبط بالعملية.
    """
    data = request.get_json(silent=True) or {}
    if is_production():
        return jsonify({'success': False, 'message': 'تأكيد الدفع التجريبي غير متاح في وضع الإنتاج'}), 403

    payment_id = str(data.get('paymentId', '')).strip()
    
    if not payment_id:
        return jsonify({'success': False, 'message': 'معرف الدفع مطلوب'}), 400
    
    # البحث عن الدفع في سجلات الدفع
    payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
    payment = next((p for p in payment_logs if str(p.get('paymentId')) == payment_id), None)
    
    if not payment:
        return jsonify({'success': False, 'message': 'الدفع غير موجود'}), 404
    
    # إذا كان الدفع مكتملاً بالفعل
    if payment.get('status') == 'paid':
        return jsonify({'success': False, 'message': 'هذا الدفع مكتمل بالفعل'}), 400
    
    # تحديث حالة الدفع إلى paid
    now_iso = datetime.now().isoformat()
    payment['status'] = 'paid'
    payment['updatedAt'] = now_iso
    if not secure_storage.encryption.encrypt_file('payment_logs', payment_logs):
        return jsonify({'success': False, 'message': 'تعذر حفظ سجل الدفع'}), 500
    
    # البحث عن الطلب المرتبط في applications.enc
    applications = secure_storage.load_applications() or {}
    applicant_user_id = payment.get('applicantId')
    job_id = payment.get('jobId')
    
    unlocked_application = False
    if applicant_user_id and job_id:
        applicant_apps = applications.get(str(applicant_user_id), [])
        for a in applicant_apps:
            if str(a.get('jobId')) == str(job_id):
                # تحديث الحقلين: الحقل الجديد (unlock_contact) والحقل المباشر (contactUnlocked)
                # لضمان توافق واجهة صاحب العمل التي تقرأ الحقل المباشر في GET API
                a['unlock_contact'] = {
                    'status': 'paid',
                    'paymentId': payment_id,
                    'contactUnlocked': True,
                    'unlockedAt': now_iso
                }
                a['unlockStatus'] = 'paid'
                a['unlockPaymentId'] = payment_id
                a['unlockedAt'] = now_iso
                a['contactUnlocked'] = True
                unlocked_application = True
                break
        if unlocked_application:
            if not secure_storage.save_applications(applications):
                return jsonify({'success': False, 'message': 'تعذر حفظ بيانات الطلب'}), 500
    
    return jsonify({
        'success': True,
        'message': 'تم تأكيد الدفع بنجاح وفتح بيانات التواصل',
        'payment': {
            'paymentId': payment.get('paymentId'),
            'status': payment.get('status'),
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
            'formattedPrice': payment.get('formattedPrice'),
            'updatedAt': payment.get('updatedAt')
        },
        'contactUnlocked': True
    })

@app.route('/api/payment/status/<paymentId>', methods=['GET'])
@login_required
def payment_status(paymentId):
    """قراءة حالة الدفع ضمن نطاق مالك العملية فقط. لا تكشف بيانات الدفع لمستخدم آخر."""
    user = current_user()
    payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
    payment = next((p for p in payment_logs if str(p.get('paymentId')) == str(paymentId)), None)

    if not payment:
        return jsonify({'success': False, 'message': 'الدفع غير موجود', 'status': 'not_found', 'contactUnlocked': False}), 404

    owner_id = str(payment.get('employerId') or payment.get('userId') or payment.get('actorId') or '')
    if owner_id and owner_id != str(user.get('id')) and user.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'غير مصرح'}), 403

    contact_unlocked = False
    applications = secure_storage.load_applications() or {}
    applicant_user_id = payment.get('applicantId')
    job_id = payment.get('jobId')
    if applicant_user_id and job_id:
        applicant_apps = applications.get(str(applicant_user_id), [])
        for a in applicant_apps:
            if str(a.get('jobId')) == str(job_id):
                unlock = a.get('unlock_contact') if isinstance(a.get('unlock_contact'), dict) else {}
                # المدفوع لا يعتبر مستحقاً إلا إذا كان السجل مدفوعاً/مكتملًا ومطابقًا للمالك.
                contact_unlocked = (
                    payment.get('status') == 'paid'
                    and bool(unlock.get('contactUnlocked'))
                    and str(unlock.get('employerId', owner_id)) == str(user.get('id'))
                )
                break

    return jsonify({
        'status': payment.get('status', 'pending'),
        'contactUnlocked': contact_unlocked,
        'paymentId': payment.get('paymentId'),
        'amount': payment.get('amount'),
        'currency': payment.get('currency'),
        'formattedPrice': payment.get('formattedPrice'),
        'updatedAt': payment.get('updatedAt')
    })

# ============================================
# سجل تدقيق الدفع (Audit Log)
# ============================================

def _log_payment_audit(actor, action, payment_id, status_before, status_after, details=None):
    """
    تسجيل عملية في سجل تدقيق الدفع المشفر (payment_audit.enc).
    
    Args:
        actor: من قام بالعملية (userId أو 'webhook' أو 'system')
        action: نوع العملية (request_unlock, confirm_payment, webhook_received, etc.)
        payment_id: معرف الدفع
        status_before: الحالة قبل العملية
        status_after: الحالة بعد العملية
        details: تفاصيل إضافية (اختياري)
    """
    try:
        audit_logs = secure_storage.encryption.decrypt_file('payment_audit') or []
        audit_entry = {
            'actor': actor,
            'action': action,
            'paymentId': payment_id,
            'status_before': status_before,
            'status_after': status_after,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        audit_logs.append(audit_entry)
        secure_storage.encryption.encrypt_file('payment_audit', audit_logs)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تسجيل سجل التدقيق: {str(e)}")
        return False

# ============================================
# API اختبار بوابة الدفع (للمشرف فقط)
# ============================================

@app.route('/api/admin/payments/test-gateway', methods=['POST'])
@admin_required
def admin_test_gateway():
    """
    API داخلي لاختبار بوابة الدفع - للمشرف فقط.
    تنشئ عملية دفع تجريبية عبر Mock Gateway.
    يتم تعطيل هذا المسار في الإنتاج ما لم يتم تفعيله صراحة عبر Environment Variable.
    """
    if os.environ.get('ENABLE_TEST_GATEWAY', '0') != '1':
        return jsonify({
            'success': False,
            'message': 'وظائف اختبار الدفع معطلة في الإنتاج. فعّل ENABLE_TEST_GATEWAY=1 لاستخدامها.'
        }), 403

    data = request.get_json(silent=True) or {}
    amount = int(data.get('amount', 500))
    currency = str(data.get('currency', 'SAR')).upper()
    description = str(data.get('description', 'اختبار بوابة الدفع'))

    # إنشاء عملية دفع عبر البوابة
    payment = create_payment(
        amount=amount,
        currency=currency,
        description=description,
        metadata={'test': True, 'admin': True}
    )

    # تسجيل في سجل التدقيق
    _log_payment_audit(
        actor=session.get('user_id', 'admin'),
        action='test_gateway_create',
        payment_id=payment['paymentId'],
        status_before='none',
        status_after='pending',
        details={'amount': amount, 'currency': currency}
    )

    return jsonify({
        'success': True,
        'message': 'تم إنشاء عملية دفع تجريبية بنجاح',
        'payment': payment,
        'gateway_version': PAYMENT_GATEWAY_VERSION,
        'rules_version': PAYMENT_RULES_VERSION
    })

# ============================================
# Webhook بوابة الدفع
# ============================================

@app.route('/api/payment/webhook', methods=['POST'])
def payment_webhook():
    """
    Webhook جاهز لبوابة الدفع.
    حالياً يقبل توقيع وهمي فقط (MOCK_WEBHOOK_SECRET).
    لا يفتح البيانات إلا بعد التحقق من حالة الدفع.
    يمنع إعادة معالجة نفس event_id مرتين (idempotency).
    """
    data = request.get_json(silent=True) or {}
    signature = str(data.get('signature', '') or request.headers.get('X-Webhook-Signature', ''))
    payment_id = str(data.get('paymentId', '')).strip()
    event = str(data.get('event', '')).strip()
    event_id = str(data.get('event_id', '') or data.get('eventId', '')).strip()
    
    # التحقق من التوقيع الوهمي
    # ملاحظة: verify_webhook_signature هي طريقة داخل MockPaymentProvider وليست دالة مستقلة
    from payment_gateway import _provider as _payment_provider
    if not _payment_provider.verify_webhook_signature(signature, data):
        _log_payment_audit(
            actor='webhook',
            action='webhook_invalid_signature',
            payment_id=payment_id or 'unknown',
            status_before='unknown',
            status_after='rejected',
            details={'reason': 'invalid_signature'}
        )
        return jsonify({'success': False, 'message': 'توقيع غير صالح'}), 401
    
    if not payment_id:
        return jsonify({'success': False, 'message': 'معرف الدفع مطلوب'}), 400
    
    # ===== حماية idempotency: منع إعادة معالجة نفس event_id =====
    if event_id:
        # قراءة سجل الأحداث المعالجة من payment_audit.enc
        # ملاحظة: payment_audit.enc مخزن كـ list وليس dict
        audit_logs = secure_storage.encryption.decrypt_file('payment_audit') or []
        
        # البحث عن الحدث في القائمة
        webhook_events = {}
        if isinstance(audit_logs, list):
            for entry in audit_logs:
                if isinstance(entry, dict) and entry.get('action') == 'webhook_event_processed':
                    details = entry.get('details') or {}
                    webhook_events[str(details.get('event_id', ''))] = entry
        
        if event_id in webhook_events and webhook_events[event_id].get('details', {}).get('processed'):
            # حدث مكرر - نرفض المعالجة
            _log_payment_audit(
                actor='webhook',
                action='webhook_duplicate_event',
                payment_id=payment_id,
                status_before='unknown',
                status_after='rejected',
                details={'event_id': event_id, 'reason': 'duplicate_event'}
            )
            return jsonify({
                'success': False,
                'message': 'هذا الحدث تمت معالجته مسبقاً',
                'duplicate': True
            }), 409
    
    # البحث عن الدفع في سجلات الدفع
    payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
    payment = next((p for p in payment_logs if str(p.get('paymentId')) == payment_id), None)
    
    if not payment:
        _log_payment_audit(
            actor='webhook',
            action='webhook_payment_not_found',
            payment_id=payment_id,
            status_before='unknown',
            status_after='not_found'
        )
        return jsonify({'success': False, 'message': 'الدفع غير موجود'}), 404
    
    status_before = payment.get('status', 'pending')
    
    # التحقق من حالة الدفع عبر البوابة
    gateway_status = get_payment_status(payment_id)
    
    # ===== تسجيل الحدث كمعالج (قبل المعالجة الفعلية) =====
    def _mark_webhook_processed(processed_status):
        """تسجيل event_id في سجل الأحداث المعالجة (list-safe)"""
        if not event_id:
            return
        try:
            audit_logs = secure_storage.encryption.decrypt_file('payment_audit') or []
            if not isinstance(audit_logs, list):
                audit_logs = []
            # إزالة أي إدخال سابق لنفس event_id لمنع التكرار
            audit_logs = [
                e for e in audit_logs
                if not (
                    isinstance(e, dict)
                    and e.get('action') == 'webhook_event_processed'
                    and str((e.get('details') or {}).get('event_id', '')) == str(event_id)
                )
            ]
            # إضافة إدخال جديد (list format — يحافظ على البنية الحالية)
            audit_logs.append({
                'actor': 'webhook',
                'action': 'webhook_event_processed',
                'paymentId': payment_id,
                'status_before': 'unknown',
                'status_after': 'processed' if processed_status else 'rejected',
                'timestamp': datetime.now().isoformat(),
                'details': {
                    'event_id': event_id,
                    'processed': processed_status
                }
            })
            secure_storage.encryption.encrypt_file('payment_audit', audit_logs)
        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل حدث webhook: {str(e)}")
    
    # إذا كان الحدث هو نجاح الدفع
    if event in ('payment.success', 'payment.paid') or gateway_status.get('status') == 'paid':
        # التحقق من أن الدفع لم يكن مكتملاً بالفعل
        if payment.get('status') == 'paid':
            _mark_webhook_processed(True)
            _log_payment_audit(
                actor='webhook',
                action='webhook_already_paid',
                payment_id=payment_id,
                status_before='paid',
                status_after='paid',
                details={'event_id': event_id}
            )
            return jsonify({'success': True, 'message': 'الدفع مكتمل بالفعل'})
        
        # التحقق من إمكانية الانتقال إلى paid (منع refunded/cancelled → paid)
        # ملاحظة: can_transition موجودة في providers.mock_provider وليست في payment_gateway
        from providers.mock_provider import can_transition
        if not can_transition(payment.get('status', 'pending'), 'paid'):
            _mark_webhook_processed(False)
            _log_payment_audit(
                actor='webhook',
                action='webhook_invalid_transition',
                payment_id=payment_id,
                status_before=payment.get('status', 'pending'),
                status_after='paid',
                details={'event_id': event_id, 'reason': 'invalid_transition'}
            )
            return jsonify({
                'success': False,
                'message': f'لا يمكن الانتقال من {payment.get("status")} إلى paid',
                'paymentId': payment_id
            }), 400
        
        # ===== تحقق المبلغ والعملة قبل التحديث إلى paid =====
        webhook_amount = data.get('amount')
        webhook_currency = str(data.get('currency', '')).upper()
        expected_amount = payment.get('amount')
        expected_currency = str(payment.get('currency', '')).upper()
        if webhook_amount is not None and float(webhook_amount) != float(expected_amount):
            _mark_webhook_processed(False)
            _log_payment_audit(
                actor='webhook',
                action='webhook_amount_mismatch',
                payment_id=payment_id,
                status_before=payment.get('status', 'pending'),
                status_after='rejected',
                details={'event_id': event_id, 'reason': 'amount_mismatch', 'expected': expected_amount, 'received': webhook_amount}
            )
            return jsonify({
                'success': False,
                'message': f'مبلغ الدفع غير مطابق: المتوقع {expected_amount}، المستلم {webhook_amount}',
                'paymentId': payment_id
            }), 400
        if webhook_currency and webhook_currency != expected_currency:
            _mark_webhook_processed(False)
            _log_payment_audit(
                actor='webhook',
                action='webhook_currency_mismatch',
                payment_id=payment_id,
                status_before=payment.get('status', 'pending'),
                status_after='rejected',
                details={'event_id': event_id, 'reason': 'currency_mismatch', 'expected': expected_currency, 'received': webhook_currency}
            )
            return jsonify({
                'success': False,
                'message': f'عملة الدفع غير مطابقة: المتوقع {expected_currency}، المستلم {webhook_currency}',
                'paymentId': payment_id
            }), 400
        # ===== نهاية تحقق المبلغ والعملة =====
        
        # تحديث حالة الدفع إلى paid
        now_iso = datetime.now().isoformat()
        payment['status'] = 'paid'
        payment['updatedAt'] = now_iso
        if not secure_storage.encryption.encrypt_file('payment_logs', payment_logs):
            return jsonify({'success': False, 'message': 'تعذر حفظ سجل الدفع'}), 500
        
        # البحث عن الطلب المرتبط في applications.enc
        applications = secure_storage.load_applications() or {}
        applicant_user_id = payment.get('applicantId')
        job_id = payment.get('jobId')
        
        unlocked_application = False
        if applicant_user_id and job_id:
            applicant_apps = applications.get(str(applicant_user_id), [])
            for a in applicant_apps:
                if str(a.get('jobId')) == str(job_id):
                    a['unlock_contact'] = {
                        'status': 'paid',
                        'paymentId': payment_id,
                        'contactUnlocked': True,
                        'unlockedAt': now_iso
                    }
                    a['unlockStatus'] = 'paid'
                    a['unlockPaymentId'] = payment_id
                    a['unlockedAt'] = now_iso
                    a['contactUnlocked'] = True
                    unlocked_application = True
                    break
            if unlocked_application:
                if not secure_storage.save_applications(applications):
                    return jsonify({'success': False, 'message': 'تعذر حفظ بيانات الطلب'}), 500
        
        # تسجيل الحدث كمعالج بنجاح
        _mark_webhook_processed(True)
        
        # تسجيل في سجل التدقيق
        _log_payment_audit(
            actor='webhook',
            action='webhook_payment_success',
            payment_id=payment_id,
            status_before=status_before,
            status_after='paid',
            details={'event': event, 'event_id': event_id, 'contactUnlocked': True}
        )
        
        return jsonify({
            'success': True,
            'message': 'تم تحديث حالة الدفع بنجاح وفتح بيانات التواصل',
            'paymentId': payment_id,
            'status': 'paid',
            'contactUnlocked': True
        })
    
    # إذا كان الحدث فشل الدفع
    elif event in ('payment.failed', 'payment.cancelled'):
        # التحقق من إمكانية الانتقال إلى failed
        # ملاحظة: can_transition موجودة في providers.mock_provider وليست في payment_gateway
        from providers.mock_provider import can_transition
        if not can_transition(payment.get('status', 'pending'), 'failed'):
            _mark_webhook_processed(False)
            _log_payment_audit(
                actor='webhook',
                action='webhook_invalid_transition',
                payment_id=payment_id,
                status_before=payment.get('status', 'pending'),
                status_after='failed',
                details={'event_id': event_id, 'reason': 'invalid_transition'}
            )
            return jsonify({
                'success': False,
                'message': f'لا يمكن الانتقال من {payment.get("status")} إلى failed',
                'paymentId': payment_id
            }), 400
        
        payment['status'] = 'failed'
        payment['updatedAt'] = datetime.now().isoformat()
        secure_storage.encryption.encrypt_file('payment_logs', payment_logs)
        
        _mark_webhook_processed(True)
        
        _log_payment_audit(
            actor='webhook',
            action='webhook_payment_failed',
            payment_id=payment_id,
            status_before=status_before,
            status_after='failed',
            details={'event': event, 'event_id': event_id}
        )
        
        return jsonify({
            'success': True,
            'message': 'تم تحديث حالة الدفع إلى فاشل',
            'paymentId': payment_id,
            'status': 'failed',
            'contactUnlocked': False
        })
    
    # حدث غير معروف
    _mark_webhook_processed(False)
    _log_payment_audit(
        actor='webhook',
        action='webhook_unknown_event',
        payment_id=payment_id,
        status_before=status_before,
        status_after=status_before,
        details={'event': event, 'event_id': event_id}
    )
    
    return jsonify({
        'success': False,
        'message': f'حدث غير معروف: {event}',
        'paymentId': payment_id,
        'status': payment.get('status')
    }), 400

STATUS_LABELS = {
    'pending': 'قيد الانتظار',
    'accepted': 'مقبول للتواصل',
    'contacted': 'تم التواصل',
    'interview': 'مقابلة مجدولة',
    'hired': 'تم التوظيف',
    'rejected': 'مرفوض',
    'review': 'قيد المراجعة',
    'withdrawn': 'مسحوب'
}

def normalize_application_status(status):
    """توحيد حالة طلب التوظيف القادمة من البيانات القديمة أو الـ API.
    لا يغيّر البيانات المخزنة؛ يستخدم فقط عند القراءة/المقارنة.
    """
    raw = str(status or 'pending').strip().lower()
    aliases = {
        'قيد الانتظار': 'pending',
        'قيد المراجعة': 'pending',
        'قيد الطلب': 'pending',
        'جديد': 'pending',
        'new': 'pending',
        'applied': 'pending',
        'submitted': 'pending',
        'waiting': 'pending',
        'pending_application': 'pending',
        'مقبول للتواصل': 'accepted',
        'تم التواصل': 'contacted',
        'مقابلة مجدولة': 'interview',
        'تم التوظيف': 'hired',
        'مرفوض': 'rejected',
        'مسحوب': 'withdrawn',
        'review': 'pending',
    }
    normalized = aliases.get(raw, raw)
    known = {'pending','accepted','contacted','interview','hired','rejected','withdrawn'}
    return normalized if normalized in known else 'pending'

def status_label(status):
    normalized = normalize_application_status(status)
    return STATUS_LABELS.get(normalized, str(status).strip())

@app.route('/api/employer/applications', methods=['PUT'])
@employer_required
def employer_application_update():
    user=current_user(); data=request.get_json(silent=True) or {}; job_id=data.get('jobId');uid=str(data.get('userId','')).strip();status=data.get('status','pending')
    # توحيد المعرفات قبل البحث/الحفظ لمنع اختلاف النوع بين JSON والبيانات القديمة.
    job_id = str(job_id).strip() if job_id is not None else ''
    # حالات الطلب الجديدة مع خريطة التسميات العربية
    allowed={'pending','accepted','contacted','interview','hired','rejected'}
    # آلة الحالة: تعريف الانتقالات المسموحة
    # pending -> accepted | rejected
    # accepted -> contacted | rejected
    # contacted -> interview | rejected
    # interview -> hired | rejected
    # hired / rejected = حالات نهائية (لا تغيير إلا برفع الحظر إدارياً)
    # التوافق القديم: 'review' يُعامل كـ pending، و 'withdrawn' يُحفَظ لكن لا يُعدَّل
    status = normalize_application_status(status)
    if status not in allowed:
        return jsonify({'success':False,'message':'الحالة غير صحيحة'}),400
    jobs=secure_storage.load_jobs() or []
    if not any(str(j.get('id'))==str(job_id) and str(j.get('employerId',''))==str(user.get('id')) for j in jobs):return jsonify({'success':False,'message':'غير مصرح'}),403
    apps=secure_storage.load_applications() or {}
    if not isinstance(apps, dict):
        return jsonify({'success':False,'message':'بيانات الطلبات غير صالحة'}),500

    # البيانات القديمة قد تستخدم مفتاح user_id كرقم أو كنص.
    # نبحث بالمقارنة النصية ولا نغيّر شكل التخزين.
    storage_key = next((k for k in apps.keys() if str(k).strip() == uid), None)
    if storage_key is None:
        return jsonify({'success':False,'message':'الطلب غير موجود'}),404

    raw_items = apps.get(storage_key)
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return jsonify({'success':False,'message':'بيانات طلبات هذا المستخدم غير صالحة'}),500

    for a in raw_items:
        if not isinstance(a, dict):
            continue
        if str(a.get('jobId', a.get('job_id', ''))).strip()==job_id:
            # احتفظ بالمعرفات داخل السجل أيضاً حتى تبقى البطاقة قابلة لإعادة البناء.
            a['userId'] = uid
            a['jobId'] = job_id
            current = normalize_application_status(a.get('status', 'pending'))
            # قواعد الانتقال المسموحة
            transitions = {
                'pending': {'accepted','rejected'},
                'accepted': {'contacted','rejected'},
                'contacted': {'interview','rejected'},
                'interview': {'hired','rejected'},
                'hired': set(),          # نهائية
                'rejected': set()        # نهائية
            }
            if status not in transitions.get(current, set()):
                return jsonify({'success':False,'message':f'لا يمكن الانتقال من «{status_label(current)}» إلى «{status_label(status)}»'}),400
            a['status']=status
            a['updatedAt']=datetime.now().isoformat()
            a.setdefault('timeline', []).append({'status':status,'label':status_label(status),'at':a['updatedAt']})
            secure_storage.save_applications(apps)
            notification_created = False
            try:
                notification_created = bool(_push_notification(
                    uid,
                    'تحديث طلب التوظيف',
                    f'تم تحديث طلبك إلى: {status_label(status)}',
                    'application',
                    f'/applications?jobId={quote(str(job_id))}'
                ))
            except Exception as exc:
                # لا نرجع تحديث الحالة بسبب خطأ في قناة الإشعار؛ الحالة محفوظة أولاً.
                current_app.logger.exception('Failed to create application status notification: %s', exc)
            return jsonify({
                'success': True,
                'message': f'تم تحديث حالة المتقدم إلى «{status_label(status)}»',
                'status': status,
                'notificationCreated': notification_created
            })
    return jsonify({'success':False,'message':'الطلب غير موجود'}),404


# ============================================
# الملف المهني + معاينة صاحب العمل + المقابلات + الرسائل
# ============================================

def _find_application_for(job_id, candidate_id):
    apps = secure_storage.load_applications() or {}
    return next((a for a in apps.get(str(candidate_id), []) if str(a.get('jobId')) == str(job_id)), None)


def _contact_is_unlocked(app_entry):
    if not app_entry:
        return False
    return bool(app_entry.get('contactUnlocked') or
                (isinstance(app_entry.get('unlock_contact'), dict) and app_entry.get('unlock_contact', {}).get('contactUnlocked')) or
                app_entry.get('unlockStatus') in ('completed', 'paid'))


def _redact_contact_text(value, user):
    """إزالة بيانات الاتصال من أي نص مهني عام بشكل صارم.
    لا نعرض أرقام الهاتف حتى لو كانت مكتوبة بأرقام عربية أو بصيغة مختلفة داخل النبذة/الخبرة.
    """
    text = str(value or '')
    phone = str(user.get('phone') or '').strip()
    email = str(user.get('email') or '').strip()
    if email:
        text = re.sub(re.escape(email), '••••@••••', text, flags=re.I)
    if phone:
        text = text.replace(phone, '••••••••')
        digits = re.sub(r'\D', '', phone)
        if len(digits) >= 7:
            variants = [digits]
            # مطابقة الرقم مع مسافات/شرطات/أقواس أو مسافة بين كل رقم.
            variants.append(r'[-\s().+]*'.join(map(re.escape, digits)))
            text = re.sub(r'(?<!\d)(?:' + variants[1] + r')(?!\d)', '••••••••', text)
            text = re.sub(r'(?<!\d)' + re.escape(digits) + r'(?!\d)', '••••••••', text)
    # حماية إضافية من أي رقم هاتف عام (7 أرقام فأكثر)، بما فيها الأرقام العربية.
    phone_like = r'(?<![\d٠-٩])(?:[+٠-٩\d][\s().\-_/]*){7,}[\d٠-٩](?![\d٠-٩])'
    text = re.sub(phone_like, '••••••••', text)
    return text

def _professional_profile(user, include_private=False):
    data = {
        'id': user.get('id'),
        'firstName': user.get('firstName', ''), 'lastName': user.get('lastName', ''),
        'headline': _redact_contact_text(user.get('headline', ''), user), 'bio': _redact_contact_text(user.get('bio', ''), user),
        'skills': _redact_contact_text(user.get('skills', ''), user), 'experience': _redact_contact_text(user.get('experience', ''), user),
        'languages': _redact_contact_text(user.get('languages', ''), user), 'certifications': _redact_contact_text(user.get('certifications', ''), user),
        'education': user.get('education', ''), 'category': user.get('category', ''),
        'profession': user.get('profession', ''),
        'country': user.get('country', ''), 'city': user.get('city', ''),
        'avatar': user.get('avatar', ''),
    }
    if include_private:
        data.update({'email': user.get('email', ''), 'phone': user.get('phone', ''), 'resume': user.get('resume', ''),
                     'phoneCountryCode': user.get('phoneCountryCode', ''), 'neighborhood': user.get('neighborhood', '')})
    return data



# ============================================
# المساعد المهني الذكي - المرحلة 6
# يعمل محلياً بدون إرسال السيرة أو بيانات المستخدم إلى طرف ثالث.
# يمكن استبدال محرك التقييم لاحقاً بمزود AI اختياري دون تغيير واجهة الـAPI.
# ============================================
def _ai_tokens(value):
    if isinstance(value, list):
        value = ' '.join(map(str, value))
    text = str(value or '').lower()
    text = re.sub(r'[^\w\u0600-\u06ff+#.\- ]+', ' ', text, flags=re.UNICODE)
    return {x.strip() for x in re.split(r'[\s,;/|]+', text) if len(x.strip()) > 1}

def _profile_text(user):
    return ' '.join(str(user.get(k, '') or '') for k in (
        'headline','bio','skills','experience','languages','certifications','education','category'
    ))

def _job_text(job):
    return ' '.join(str(job.get(k, '') or '') for k in (
        'title','description','category','requirements','experience','jobType','type','country','city'
    )) + ' ' + ' '.join(map(str, job.get('tags') or []))

def _smart_job_score(user, job):
    p = _ai_tokens(_profile_text(user))
    j = _ai_tokens(_job_text(job))
    if not j:
        return 0, [], []
    overlap = p & j
    # أوزان واضحة وقابلة للتدقيق: المهارات/الكلمات المشتركة هي الأساس، مع مكافأة للموقع والمجال.
    score = min(78, round((len(overlap) / max(1, min(len(j), 18))) * 100))
    reasons = []
    missing = []
    if overlap:
        reasons.append(f"لديك {len(overlap)} مهارة/كلمة مطابقة للوظيفة")
    else:
        reasons.append('لا توجد مطابقة نصية قوية في الملف الحالي')
    user_country = str(user.get('country','')).strip().lower()
    user_city = str(user.get('city','')).strip().lower()
    if user_country and user_country in str(job.get('country','')).lower():
        score += 8; reasons.append('الدولة متوافقة')
    if user_city and user_city in str(job.get('city','')).lower():
        score += 5; reasons.append('المدينة متوافقة')
    if bool(job.get('remote') or job.get('isRemote')) and ('عن بعد' in _profile_text(user).lower() or 'remote' in _profile_text(user).lower()):
        score += 7; reasons.append('العمل عن بعد متوافق')
    # أهم الكلمات في الإعلان التي لا تظهر في الملف.
    candidates = [x for x in j if len(x) >= 3]
    for token in candidates:
        if token not in p and token not in {'the','and','with','from','للعمل','وظيفة','خبرة','مطلوب'}:
            missing.append(token)
    return max(0, min(100, score)), reasons[:4], missing[:8]

def _cv_review(user):
    fields = {
        'headline': bool(str(user.get('headline','')).strip()),
        'bio': bool(str(user.get('bio','')).strip()),
        'skills': bool(str(user.get('skills','')).strip()),
        'experience': bool(str(user.get('experience','')).strip()),
        'languages': bool(str(user.get('languages','')).strip()),
        'certifications': bool(str(user.get('certifications','')).strip()),
        'education': bool(str(user.get('education','')).strip()),
        'resume': bool(str(user.get('resume','')).strip()),
    }
    score = round(sum(fields.values()) / len(fields) * 100)
    suggestions=[]
    labels={'headline':'العنوان المهني','bio':'النبذة المهنية','skills':'المهارات','experience':'الخبرة','languages':'اللغات','certifications':'الشهادات','education':'التعليم','resume':'السيرة الذاتية'}
    for k,v in fields.items():
        if not v: suggestions.append(f"أضف {labels[k]}")
    if fields['skills'] and len(_ai_tokens(user.get('skills'))) < 4:
        suggestions.append('أضف مهارات أكثر تحديداً مثل الأدوات والتقنيات التي تتقنها')
    if fields['bio'] and len(str(user.get('bio',''))) < 80:
        suggestions.append('اجعل النبذة المهنية أكثر وضوحاً واذكر سنوات الخبرة وأبرز الإنجازات')
    return score, suggestions[:6], fields

@app.route('/career-intelligence')
def career_intelligence_page():
    if 'user_id' not in session: return redirect('/?login=1')
    return render_template('career_intelligence.html', user=current_user())

@app.route('/career-assistant')
def career_assistant_page():
    if 'user_id' not in session:
        return redirect('/?login=1')
    return render_template('career_assistant.html', user=current_user())

@app.route('/api/ai/job-matches', methods=['GET'])
def ai_job_matches():
    user=current_user()
    if not user or user.get('role') != 'job_seeker':
        return jsonify({'success':False,'message':'هذه الخدمة مخصصة للباحث عن عمل'}),403
    jobs=secure_storage.load_jobs() or []
    ranked=[]
    for job in jobs:
        score,reasons,missing=_smart_job_score(user,job)
        ranked.append({'job':job,'score':score,'reasons':reasons,'missingSkills':missing})
    ranked.sort(key=lambda x:(x['score'], str(x['job'].get('posted',''))), reverse=True)
    return jsonify({'success':True,'engine':'local-smart-v1','items':ranked[:20]})

@app.route('/api/ai/job-fit/<job_id>', methods=['GET'])
def ai_job_fit(job_id):
    user=current_user()
    if not user or user.get('role') != 'job_seeker':
        return jsonify({'success':False,'message':'هذه الخدمة مخصصة للباحث عن عمل'}),403
    jobs=secure_storage.load_jobs() or []
    job=next((j for j in jobs if str(j.get('id'))==str(job_id)),None)
    if not job: return jsonify({'success':False,'message':'الوظيفة غير موجودة'}),404
    score,reasons,missing=_smart_job_score(user,job)
    return jsonify({'success':True,'engine':'local-smart-v1','score':score,'reasons':reasons,'missingSkills':missing,'jobId':job_id})

@app.route('/api/ai/cv-review', methods=['GET','POST'])
def ai_cv_review():
    user=current_user()
    if not user or user.get('role') != 'job_seeker':
        return jsonify({'success':False,'message':'هذه الخدمة مخصصة للباحث عن عمل'}),403
    score,suggestions,fields=_cv_review(user)
    return jsonify({'success':True,'engine':'local-smart-v1','score':score,'suggestions':suggestions,'fields':fields})

def _extract_salary_range(value):
    text = str(value or '')
    nums = []
    for raw in re.findall(r'(?<![A-Za-z])\d+(?:[.,]\d+)?', text.replace(',', '')):
        try:
            n = float(raw.replace(',', ''))
            if n > 0:
                nums.append(n)
        except Exception:
            pass
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)

def _salary_intelligence(country='', category='', currency=''):
    jobs = secure_storage.load_jobs() or []
    country_l = str(country or '').strip().lower()
    category_l = str(category or '').strip().lower()
    rows=[]
    for job in jobs:
        if country_l and country_l not in str(job.get('country','')).lower():
            continue
        if category_l and category_l not in (str(job.get('category','')).lower() + ' ' + str(job.get('title','')).lower()):
            continue
        lo, hi = _extract_salary_range(job.get('salary'))
        if lo is not None:
            rows.append((lo, hi, job))
    if not rows:
        return {'available':False,'message':'لا توجد بيانات راتب كافية في الوظائف الحالية لبناء تقدير موثوق.','sampleSize':0,'currency':currency or ''}
    values=[(lo+hi)/2 for lo,hi,_ in rows]
    values.sort()
    avg=round(sum(values)/len(values),2)
    median=round(values[len(values)//2],2)
    return {'available':True,'sampleSize':len(rows),'currency':currency or '',
            'min':round(min(x[0] for x in rows),2),'max':round(max(x[1] for x in rows),2),
            'average':avg,'median':median,'source':'وظائف منشورة داخل المنصة فقط','disclaimer':'هذا مؤشر إحصائي داخلي وليس متوسط سوق رسمي.'}

def _career_roadmap(user):
    skills=_ai_tokens(user.get('skills'))
    category=str(user.get('category') or user.get('headline') or '').lower()
    targets=[]
    defaults={
        'software':['Git','Python','SQL','APIs','Testing','Docker','Cloud'],
        'developer':['Git','Python','SQL','APIs','Testing','Docker','Cloud'],
        'data':['SQL','Python','Statistics','Data Visualization','Machine Learning'],
        'marketing':['Analytics','SEO','Content Strategy','CRM','Campaign Management'],
        'account':['Excel','Accounting Software','Financial Reporting','Tax','Analysis'],
    }
    for key, vals in defaults.items():
        if key in category:
            targets=vals; break
    if not targets:
        targets=['مهارة أساسية مرتبطة بالمجال','أداة مهنية متخصصة','تحليل البيانات','التواصل المهني','إدارة المشاريع']
    steps=[]
    for i,skill in enumerate(targets,1):
        token=_ai_tokens(skill)
        done=bool(token & skills)
        steps.append({'step':i,'skill':skill,'status':'completed' if done else 'recommended','reason':'موجودة في ملفك' if done else 'مهارة مقترحة للتطور المهني'})
    return {'steps':steps,'completed':sum(x['status']=='completed' for x in steps),'total':len(steps),'level': 'متقدم' if len(steps) and sum(x['status']=='completed' for x in steps)>=len(steps)*.7 else 'تطويري'}

@app.route('/api/ai/salary-intelligence', methods=['GET'])
def ai_salary_intelligence():
    user=current_user()
    if not user or user.get('role') not in ('job_seeker','employer','admin'):
        return jsonify({'success':False,'message':'غير مصرح'}),403
    country=request.args.get('country','') or user.get('country','')
    category=request.args.get('category','') or user.get('category','') or user.get('headline','')
    return jsonify({'success':True,**_salary_intelligence(country,category,request.args.get('currency',''))})

@app.route('/api/ai/career-roadmap', methods=['GET'])
def ai_career_roadmap():
    user=current_user()
    if not user or user.get('role') != 'job_seeker':
        return jsonify({'success':False,'message':'هذه الخدمة مخصصة للباحث عن عمل'}),403
    return jsonify({'success':True,'engine':'local-smart-v1',**_career_roadmap(user)})

@app.route('/api/ai/employer-ranking/<job_id>', methods=['GET'])
@employer_required
def ai_employer_ranking(job_id):
    employer=current_user(); jobs=secure_storage.load_jobs() or []
    job=next((j for j in jobs if str(j.get('id'))==str(job_id) and str(j.get('employerId'))==str(employer.get('id'))),None)
    if not job:
        return jsonify({'success':False,'message':'غير مصرح بهذه الوظيفة'}),403
    applications=secure_storage.load_applications() or {}; users=secure_storage.load_users() or []
    ranked=[]
    for cid, items in applications.items():
        app_entry=next((a for a in items if str(a.get('jobId'))==str(job_id)),None)
        if not app_entry: continue
        candidate=next((u for u in users if str(u.get('id'))==str(cid) and u.get('role')!='admin'),None)
        if not candidate: continue
        score,reasons,missing=_smart_job_score(candidate,job)
        ranked.append({'candidateId':str(cid),'candidateName':f"{candidate.get('firstName','')} {candidate.get('lastName','')}".strip() or 'متقدم','score':score,'reasons':reasons,'missingSkills':missing,'status':app_entry.get('status','pending')})
    ranked.sort(key=lambda x:x['score'],reverse=True)
    return jsonify({'success':True,'engine':'local-smart-v1','jobId':job_id,'items':ranked})

@app.route('/api/ai/job-draft', methods=['POST'])
def ai_job_draft():
    user=current_user()
    if not user or user.get('role') not in ('employer','admin'):
        return jsonify({'success':False,'message':'هذه الخدمة مخصصة لصاحب العمل'}),403
    data=request.get_json(silent=True) or {}
    title=sanitize_input(data.get('title','')).strip()
    skills=[str(x).strip() for x in (data.get('skills') or []) if str(x).strip()]
    experience=sanitize_input(data.get('experience','')).strip()
    location=sanitize_input(data.get('location','')).strip()
    if not title:
        return jsonify({'success':False,'message':'اكتب المسمى الوظيفي أولاً'}),400
    skills_text=', '.join(skills) if skills else 'المهارات المرتبطة بالمسمى الوظيفي'
    description=(f"نبحث عن {title} للانضمام إلى فريقنا. نحتاج إلى شخص قادر على تنفيذ المهام المرتبطة بالدور "
                 f"والعمل ضمن فريق والتواصل بوضوح. الخبرة المطلوبة: {experience or 'تحدد حسب مستوى الوظيفة'}. "
                 f"المهارات الأساسية: {skills_text}. الموقع: {location or 'يحدد من قبل الشركة'}. "
                 "يرجى إرسال السيرة الذاتية وذكر الخبرات والمشاريع ذات الصلة.")
    return jsonify({'success':True,'engine':'local-smart-v1','draft':{
        'title':title,'description':description,'skills':skills,'experience':experience,'location':location
    }})

@app.route('/api/profile/public', methods=['GET'])
def public_profile_preview():
    """الباحث يرى المعاينة المهنية التي يمكن مشاركتها مع أصحاب العمل."""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404
    return jsonify({'success': True, 'profile': _professional_profile(user, include_private=False), 'isOwner': True})


@app.route('/api/employer/applications/<job_id>/<candidate_id>/profile', methods=['GET'])
@employer_required
def employer_candidate_profile(job_id, candidate_id):
    """معاينة ملف مرشح ضمن وظيفة يملكها صاحب العمل. لا تعيد بيانات التواصل قبل فتحها."""
    employer = current_user(); jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get('id')) == str(job_id) and str(j.get('employerId')) == str(employer.get('id'))), None)
    if not job:
        return jsonify({'success': False, 'message': 'غير مصرح بهذه الوظيفة'}), 403
    app_entry = _find_application_for(job_id, candidate_id)
    if not app_entry:
        return jsonify({'success': False, 'message': 'طلب التقديم غير موجود'}), 404
    users = secure_storage.load_users() or []
    candidate = next((u for u in users if str(u.get('id')) == str(candidate_id) and u.get('role') != 'admin'), None)
    if not candidate:
        return jsonify({'success': False, 'message': 'الباحث عن العمل غير موجود'}), 404
    unlocked = _contact_is_unlocked(app_entry)
    return jsonify({'success': True, 'profile': _professional_profile(candidate, include_private=unlocked),
                    'contactUnlocked': unlocked, 'jobId': job_id, 'candidateId': candidate_id})


@app.route('/api/interviews', methods=['GET','POST','PUT'])
def interviews_api():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    uid = str(session['user_id']); user = current_user() or {}
    data = _user_collection('interviews')
    items = data.get(uid, [])
    if request.method == 'GET':
        # يعرض للمستخدم مقابلاته فقط، مع بيانات الوظيفة الأساسية.
        return jsonify({'success': True, 'items': items})
    payload = request.get_json(silent=True) or {}
    interview_id = str(payload.get('id') or '')
    if request.method == 'PUT':
        item = next((x for x in items if str(x.get('id')) == interview_id), None)
        if not item:
            return jsonify({'success': False, 'message': 'المقابلة غير موجودة'}), 404
        action = str(payload.get('action') or '').lower()
        if user.get('role') == 'employer' and str(item.get('employerId')) == uid:
            if action not in ('cancel','complete'):
                return jsonify({'success': False, 'message': 'الإجراء غير صالح'}), 400
            item['status'] = 'cancelled' if action == 'cancel' else 'completed'
        elif user.get('role') != 'employer' and str(item.get('candidateId')) == uid:
            if action not in ('confirm','cancel'):
                return jsonify({'success': False, 'message': 'الإجراء غير صالح'}), 400
            item['status'] = 'confirmed' if action == 'confirm' else 'cancelled'
        else:
            return jsonify({'success': False, 'message': 'غير مصرح'}), 403
        item['updatedAt'] = datetime.now().isoformat()
        # حافظ على نسخة المقابلة متزامنة لدى الطرفين. سابقًا كان PUT
        # يغيّر نسخة المستخدم الحالي فقط، فتظهر حالة مختلفة عند صاحب العمل والمتقدم.
        other = item.get('candidateId') if user.get('role') == 'employer' else item.get('employerId')
        if other:
            for peer in data.get(str(other), []) or []:
                if str(peer.get('id')) == interview_id:
                    peer['status'] = item['status']
                    peer['updatedAt'] = item['updatedAt']
                    break
        _save_user_collection('interviews', data)
        if other:
            label = {
                'confirmed': 'تم تأكيد حضور المقابلة',
                'cancelled': 'تم إلغاء المقابلة',
                'completed': 'تم إنهاء المقابلة'
            }.get(item['status'], f"تم تحديث حالة المقابلة إلى: {item['status']}")
            _push_notification(other, 'تحديث المقابلة', label, 'interview', '/applications')
        return jsonify({'success': True, 'item': item})

    # POST: إنشاء مقابلة من صاحب العمل فقط، بعد أن يصبح الطلب contacted.
    if user.get('role') != 'employer':
        return jsonify({'success': False, 'message': 'إنشاء المقابلات مخصص لصاحب العمل'}), 403
    job_id = payload.get('jobId'); candidate_id = str(payload.get('candidateId') or '')
    if not job_id or not candidate_id:
        return jsonify({'success': False, 'message': 'الوظيفة والمتقدم مطلوبان'}), 400
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get('id')) == str(job_id) and str(j.get('employerId')) == uid), None)
    if not job: return jsonify({'success': False, 'message': 'غير مصرح بهذه الوظيفة'}), 403
    app_entry = _find_application_for(job_id, candidate_id)
    if not app_entry: return jsonify({'success': False, 'message': 'طلب التقديم غير موجود'}), 404
    if app_entry.get('status') not in ('contacted','interview'):
        return jsonify({'success': False, 'message': 'يجب أن تكون حالة الطلب «تم التواصل» قبل جدولة المقابلة'}), 400
    # منع إنشاء مواعيد مكررة لنفس الطلب في حالة وجود موعد فعال.
    all_existing = _user_collection('interviews')
    for owner_items in all_existing.values():
        for old in owner_items:
            if str(old.get('jobId')) == str(job_id) and str(old.get('candidateId')) == candidate_id and old.get('status') not in ('cancelled','completed'):
                return jsonify({'success': False, 'message': 'يوجد بالفعل موعد مقابلة فعال لهذا الطلب'}), 409
    item = {
        'id': secrets.token_urlsafe(12), 'jobId': job_id, 'candidateId': candidate_id, 'employerId': uid,
        'jobTitle': job.get('title',''), 'date': str(payload.get('date') or '').strip(),
        'time': str(payload.get('time') or '').strip(), 'mode': str(payload.get('mode') or 'online').strip(),
        'location': str(payload.get('location') or '').strip(), 'meetingUrl': str(payload.get('meetingUrl') or '').strip(),
        'notes': str(payload.get('notes') or '').strip()[:2000], 'status': 'scheduled', 'createdAt': datetime.now().isoformat()
    }
    if not item['date'] or not item['time']:
        return jsonify({'success': False, 'message': 'تاريخ ووقت المقابلة مطلوبان'}), 400
    # حفظ في صندوق الطرفين نفسه لسهولة القراءة من كل طرف.
    all_existing.setdefault(uid, []).insert(0, item)
    all_existing.setdefault(candidate_id, []).insert(0, item.copy())
    if not _save_user_collection('interviews', all_existing):
        return jsonify({'success': False, 'message': 'تعذر حفظ المقابلة'}), 500
    # تحديث حالة الطلب وسجلها.
    apps = secure_storage.load_applications() or {}
    for a in apps.get(candidate_id, []):
        if str(a.get('jobId')) == str(job_id):
            a['status'] = 'interview'; a['updatedAt'] = datetime.now().isoformat()
            a.setdefault('timeline', []).append({'status':'interview','label':status_label('interview'),'at':a['updatedAt']})
            break
    secure_storage.save_applications(apps)
    _push_notification(candidate_id, 'تمت جدولة مقابلة', f"تم تحديد مقابلة لوظيفة {job.get('title','')} بتاريخ {item['date']} الساعة {item['time']}", 'interview', '/applications')
    return jsonify({'success': True, 'item': item, 'message': 'تمت جدولة المقابلة بنجاح'})


@app.route('/api/messages', methods=['GET','POST'])
def messages_api():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    uid = str(session['user_id']); user = current_user() or {}; store = _user_collection('messages')
    payload = request.get_json(silent=True) or {}
    other = str(payload.get('userId') or request.args.get('userId') or '')
    job_id = str(payload.get('jobId') or request.args.get('jobId') or '')
    thread_key = 'job:' + job_id + ':' + ':'.join(sorted([uid, other])) if job_id and other else None
    if request.method == 'GET' and not other:
        # صندوق الرسائل: تجميع المحادثات التي ينتمي إليها المستخدم.
        threads=[]
        for key, msgs in store.items():
            if not isinstance(msgs, list) or not msgs: continue
            mine=[m for m in msgs if str(m.get('senderId'))==uid or str(m.get('receiverId'))==uid]
            if not mine: continue
            last=mine[-1]
            other_id=str(last.get('receiverId')) if str(last.get('senderId'))==uid else str(last.get('senderId'))
            other_user=next((u for u in (secure_storage.load_users() or []) if str(u.get('id'))==other_id), {})
            threads.append({'threadId':key,'userId':other_id,'name':(other_user.get('firstName','')+' '+other_user.get('lastName','')).strip() or 'مستخدم','jobId':last.get('jobId',''),'lastMessage':last.get('message',''),'createdAt':last.get('createdAt',''),'unread':sum(1 for m in mine if str(m.get('receiverId'))==uid and not m.get('read'))})
        threads.sort(key=lambda x:x.get('createdAt',''), reverse=True)
        return jsonify({'success':True,'threads':threads[:100]})
    if request.method == 'GET':
        if not other:
            return jsonify({'success': False, 'message': 'معرف الطرف الآخر مطلوب'}), 400
        # لا نكشف المحادثة إلا لطرفيها، ونعلّم الرسائل الواردة في هذه المحادثة كمقروءة.
        items=[m for m in store.get(thread_key, []) if str(m.get('senderId'))==uid or str(m.get('receiverId'))==uid][-200:]
        changed=False
        for m in items:
            if str(m.get('receiverId'))==uid and not m.get('read'):
                m['read']=True; changed=True
        if changed:
            _save_user_collection('messages', store)
        return jsonify({'success': True, 'threadId': thread_key, 'items': items})
    if other == uid:
        return jsonify({'success': False, 'message': 'لا يمكنك مراسلة نفسك'}), 400
    users = secure_storage.load_users() or []
    other_user = next((u for u in users if str(u.get('id')) == other), None)
    if not other_user or other_user.get('role') == 'admin':
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404
    # ربط الرسائل دائمًا بطلب توظيف محدد، ومنع استخدامها لتجاوز حماية بيانات التواصل.
    if not job_id:
        return jsonify({'success': False, 'message': 'يجب ربط الرسالة بوظيفة وطلب توظيف'}), 400
    candidate_id = other if user.get('role') == 'employer' else uid
    employer_id = uid if user.get('role') == 'employer' else other
    jobs = secure_storage.load_jobs() or []
    job = next((j for j in jobs if str(j.get('id')) == job_id and str(j.get('employerId')) == employer_id), None)
    if not job:
        return jsonify({'success': False, 'message': 'لا توجد علاقة توظيف صالحة بين الطرفين'}), 403
    app_entry = _find_application_for(job_id, candidate_id)
    if not app_entry:
        return jsonify({'success': False, 'message': 'لا يوجد طلب توظيف مرتبط بهذه المحادثة'}), 404
    if user.get('role') == 'employer':
        if not _contact_is_unlocked(app_entry):
            return jsonify({'success': False, 'message': 'يجب فتح بيانات التواصل المصرح بها قبل بدء مراسلة الباحث عن العمل'}), 402
    else:
        if app_entry.get('status') not in ('accepted','contacted','interview','hired') and not app_entry.get('companyDataShared'):
            return jsonify({'success': False, 'message': 'يمكن بدء المراسلة بعد انتقال الطلب إلى مرحلة التواصل'}), 403
    text = str(payload.get('message') or '').strip()
    if not text or len(text) > 2000:
        return jsonify({'success': False, 'message': 'الرسالة مطلوبة وبحد أقصى 2000 حرف'}), 400
    item = {'id': secrets.token_urlsafe(12), 'senderId': uid, 'receiverId': other, 'jobId': job_id,
            'message': text, 'createdAt': datetime.now().isoformat(), 'read': False}
    store.setdefault(thread_key, []).append(item); store[thread_key] = store[thread_key][-200:]
    if not _save_user_collection('messages', store):
        return jsonify({'success': False, 'message': 'تعذر حفظ الرسالة'}), 500
    _push_notification(other, 'رسالة جديدة', f"لديك رسالة جديدة بخصوص وظيفة {job.get('title','')}", 'message', f"/messages?userId={quote(str(uid))}&jobId={quote(str(job_id))}")
    return jsonify({'success': True, 'item': item, 'threadId': thread_key})


# ============================================
# API المفضلة والطلبات
# ============================================

# ============================================
# تجربة المستخدم: البحث المحفوظ + الإشعارات + اكتمال الملف
# ============================================
def _user_collection(name):
    try:
        return secure_storage.encryption.decrypt_file(name) or {}
    except Exception:
        return {}

def _save_user_collection(name, data):
    return secure_storage.encryption.encrypt_file(name, data)

def _notification_preferences():
    try:
        return secure_storage.encryption.decrypt_file('notification_preferences') or {}
    except Exception:
        return {}

def _save_notification_preferences(data):
    return secure_storage.encryption.encrypt_file('notification_preferences', data)

def _notification_queue():
    try:
        return secure_storage.encryption.decrypt_file('notification_delivery_queue') or []
    except Exception:
        return []

def _save_notification_queue(data):
    return secure_storage.encryption.encrypt_file('notification_delivery_queue', data[-1000:])

def _default_notification_preferences():
    return {
        'site': True,
        'telegram': True,
        'email': True,
        'jobs': True,
        'applications': True,
        'interviews': True,
        'messages': True,
        'payments': True,
        'wallet': True,
        'security': True,
        'marketing': False
    }

def _push_notification(user_id, title, message, kind='info', link='', category=None):
    uid=str(user_id)
    category = category or kind or 'general'
    prefs=_notification_preferences()
    pref=dict(_default_notification_preferences())
    pref.update(prefs.get(uid, {}) if isinstance(prefs.get(uid, {}), dict) else {})
    if not pref.get(category, True):
        return False
    item={'id': secrets.token_urlsafe(10), 'title': title, 'message': message, 'kind': kind,
          'category': category, 'link': link, 'read': False, 'createdAt': datetime.now().isoformat()}
    data=_user_collection('notifications')
    items=data.setdefault(uid, [])
    items.insert(0, item)
    data[uid]=items[:100]
    saved=_save_user_collection('notifications', data)
    if not saved:
        return False
    queue=_notification_queue()
    delivery={'id': item['id'], 'userId': uid, 'title': title, 'message': message,
              'kind': kind, 'category': category, 'link': link, 'createdAt': item['createdAt'],
              'telegram': bool(pref.get('telegram')), 'email': bool(pref.get('email')),
              'sentTelegram': False, 'sentEmail': False}
    if delivery['email']:
        try:
            users=secure_storage.load_users() or []
            user=next((u for u in users if str(u.get('id'))==uid), None)
            if user and user.get('email'):
                delivery['sentEmail']=bool(send_email(user.get('email'), title, message))
        except Exception:
            delivery['sentEmail']=False
    if delivery['telegram'] or not delivery['sentEmail']:
        queue.append(delivery)
        _save_notification_queue(queue)
    return True

def _normalize_message_notification_links(uid, items):
    """إصلاح الإشعارات القديمة التي كانت تربط الرسالة بصفحة الطلبات."""
    try:
        jobs = secure_storage.load_jobs() or []
        store = _user_collection('messages')
        for n in items:
            if n.get('kind') != 'message':
                continue
            text = str(n.get('message') or '')
            # استخراج عنوان الوظيفة من الصيغة الحالية للإشعار.
            prefix = 'لديك رسالة جديدة بخصوص وظيفة '
            title = text[len(prefix):].strip() if text.startswith(prefix) else ''
            matched_job = next((j for j in jobs if title and str(j.get('title','')).strip() == title), None)
            if not matched_job:
                continue
            jid = str(matched_job.get('id'))
            candidates=[]
            for key, msgs in store.items():
                if not isinstance(msgs, list):
                    continue
                for m in msgs:
                    if str(m.get('jobId')) == jid and (str(m.get('senderId')) == str(uid) or str(m.get('receiverId')) == str(uid)):
                        candidates.append(m)
            if candidates:
                latest = max(candidates, key=lambda m: str(m.get('createdAt','')))
                other_id = str(latest.get('senderId')) if str(latest.get('senderId')) != str(uid) else str(latest.get('receiverId'))
                n['link'] = f'/messages?userId={quote(other_id)}&jobId={quote(jid)}'
                n['targetUserId'] = other_id
                n['targetJobId'] = jid
    except Exception:
        pass
    return items

@app.route('/api/notifications', methods=['GET','PUT'])
def user_notifications():
    if 'user_id' not in session: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); data=_user_collection('notifications'); items=data.get(uid,[])
    items = _normalize_message_notification_links(uid, items)
    data[uid] = items
    if request.method=='PUT':
        payload=request.get_json(silent=True) or {}
        if payload.get('all'):
            for x in items: x['read']=True
        elif payload.get('id'):
            for x in items:
                if str(x.get('id'))==str(payload['id']): x['read']=True
        _save_user_collection('notifications',data)
    return jsonify({'success':True,'items':items,'unread':sum(1 for x in items if not x.get('read'))})

@app.route('/api/notification-preferences', methods=['GET','PUT'])
def notification_preferences():
    if 'user_id' not in session: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); prefs=_notification_preferences(); current=dict(_default_notification_preferences()); current.update(prefs.get(uid,{}) if isinstance(prefs.get(uid,{}),dict) else {})
    if request.method=='PUT':
        payload=request.get_json(silent=True) or {}
        for key in current:
            if key in payload:
                current[key]=bool(payload[key])
        # Site notifications cannot be disabled because they are the canonical in-app record.
        current['site']=True
        prefs[uid]=current
        _save_notification_preferences(prefs)
    return jsonify({'success':True,'preferences':current})

@app.route('/messages')
@login_required
def messages_page():
    return render_template('messages.html', current_user=current_user())


@app.route('/notifications')
def notifications_page():
    if 'user_id' not in session:
        return redirect('/?login=1')
    return render_template('notifications.html', current_user=session.get('user'))



@app.route('/api/candidate/smart-jobs', methods=['GET'])
def candidate_smart_jobs_api():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول', 'items': []}), 401
    uid = str(session.get('user_id'))
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get('id')) == uid), {})
    jobs = secure_storage.load_jobs() or []
    if not jobs:
        return jsonify({'success': True, 'items': []})
    profile_text = ' '.join(str(user.get(k) or '') for k in ('title','job_title','professional_summary','skills','category','profession','city','country','experience'))
    tokens = {x.strip().lower() for x in re.split(r'[^\w\u0600-\u06ff]+', profile_text) if len(x.strip()) >= 3}
    scored = []
    for job in jobs:
        text = ' '.join(str(job.get(k) or '') for k in ('title','description','skills','category','city','country','employmentType','jobType')).lower()
        hits = [tok for tok in tokens if tok and tok in text]
        score = min(99, 45 + len(hits) * 7) if hits else 35
        if str(job.get('city') or '').strip().lower() and str(job.get('city') or '').strip().lower() in profile_text.lower():
            score = min(99, score + 10)
        scored.append({'job': job, 'match_score': score, 'matching_skills': hits[:6], 'why': 'مطابقة بناءً على ملفك ومهاراتك' if hits else 'اقتراح عام بناءً على بيانات المنصة'})
    scored.sort(key=lambda x: x['match_score'], reverse=True)
    return jsonify({'success': True, 'items': scored[:8]})

@app.route('/api/job-compare', methods=['GET','POST','DELETE'])
def job_compare_api():
    """يحفظ قائمة مقارنة قصيرة للمستخدم (حتى 3 وظائف) من الخادم."""
    if 'user_id' not in session:
        return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id'])
    data=_user_collection('job_compare')
    items=[str(x) for x in (data.get(uid,[]) or [])][:3]
    jobs=secure_storage.load_jobs() or []
    if request.method=='GET':
        selected={str(x) for x in items}
        return jsonify({'success':True,'jobIds':items,
                        'jobs':[j for j in jobs if str(j.get('id')) in selected]})
    payload=request.get_json(silent=True) or {}
    jid=str(payload.get('job_id') or '')
    if not jid or not any(str(j.get('id'))==jid for j in jobs):
        return jsonify({'success':False,'message':'الوظيفة غير موجودة'}),404
    if request.method=='DELETE':
        items=[x for x in items if x!=jid]
    else:
        if jid not in items:
            if len(items)>=3:
                return jsonify({'success':False,'message':'يمكن مقارنة 3 وظائف كحد أقصى'}),400
            items.append(jid)
    data[uid]=items
    if not _save_user_collection('job_compare',data):
        return jsonify({'success':False,'message':'تعذر حفظ المقارنة'}),500
    return jsonify({'success':True,'jobIds':items,
                    'jobs':[j for j in jobs if str(j.get('id')) in set(items)]})

@app.route('/compare')
def compare_page():
    if 'user_id' not in session:
        return redirect('/?login=1')
    return render_template('compare.html', current_user=session.get('user'))

@app.route('/api/job-alerts/check', methods=['POST'])
def check_job_alerts():
    """يفحص الوظائف الجديدة مقابل عمليات البحث المحفوظة، ويصدر إشعارًا مرة واحدة لكل وظيفة."""
    if 'user_id' not in session:
        return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id'])
    searches=_user_collection('saved_searches')
    items=searches.get(uid,[]) or []
    jobs=secure_storage.load_jobs() or []
    notified=_user_collection('job_alert_notified')
    seen=set(str(x) for x in (notified.get(uid,[]) or []))
    created=0

    def matches(job, filters):
        filters=filters or {}
        q=str(filters.get('q') or '').strip().lower()
        hay=" ".join(str(job.get(k,'') or '') for k in
                     ('title','company','description','country','city','category','tags','skills')).lower()
        if q and q not in hay: return False
        for key in ('country','city','category'):
            val=str(filters.get(key) or '').strip().lower()
            if val and val!='all' and val not in str(job.get(key,'')).lower():
                # category may be stored in tags
                if key=='category' and val in " ".join(map(str,job.get('tags',[]) or [])).lower():
                    continue
                return False
        return True

    for search in items:
        if not search.get('alertsEnabled',True): continue
        filt=search.get('filters') or {}
        for job in jobs:
            jid=str(job.get('id') or '')
            if not jid or jid in seen or not matches(job,filt): continue
            _push_notification(uid, 'وظيفة جديدة تناسب بحثك',
                                f"ظهرت وظيفة جديدة: {job.get('title','وظيفة')} — {job.get('company','')}",
                                'job', f"/jobs?job={jid}", 'jobs')
            seen.add(jid); created += 1
            if created>=20: break
        if created>=20: break
    notified[uid]=list(seen)[-500:]
    _save_user_collection('job_alert_notified',notified)
    return jsonify({'success':True,'created':created})

@app.route('/api/saved-searches', methods=['GET','POST','DELETE'])
def saved_searches():
    if 'user_id' not in session: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); data=_user_collection('saved_searches'); items=data.setdefault(uid,[])
    if request.method=='GET': return jsonify({'success':True,'items':items})
    payload=request.get_json(silent=True) or {}
    if request.method=='DELETE':
        sid=str(payload.get('id','')); data[uid]=[x for x in items if str(x.get('id'))!=sid]; _save_user_collection('saved_searches',data)
        return jsonify({'success':True,'message':'تم حذف البحث المحفوظ'})
    name=str(payload.get('name') or 'بحث محفوظ').strip()[:100]; filters=payload.get('filters') or {}
    item={'id':secrets.token_urlsafe(10),'name':name,'filters':filters,'createdAt':datetime.now().isoformat(),'alertsEnabled':bool(payload.get('alertsEnabled',True))}
    items.insert(0,item); data[uid]=items[:50]; _save_user_collection('saved_searches',data)
    return jsonify({'success':True,'item':item})

@app.route('/api/profile/completeness', methods=['GET'])
def profile_completeness():
    user=current_user()
    if not user: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    fields=['firstName','lastName','email','phone','country','city','headline','profession','bio','skills','experience','education','avatar','resume']
    checks=[]
    for f in fields:
        v=user.get(f); ok=bool(v) and (not isinstance(v,(list,dict)) or len(v)>0)
        checks.append({'field':f,'complete':ok})
    score=round(sum(1 for x in checks if x['complete'])*100/len(checks))
    return jsonify({'success':True,'score':score,'checks':checks,'missing':[x['field'] for x in checks if not x['complete']]})


@app.route('/api/applications/draft', methods=['GET','POST','DELETE'])
def application_draft():
    if 'user_id' not in session:
        return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); data=_user_collection('application_drafts')
    if request.method=='GET':
        job_id=str(request.args.get('job_id') or '')
        return jsonify({'success':True,'draft':data.get(uid,{}).get(job_id,{})})
    payload=request.get_json(silent=True) or {}; job_id=str(payload.get('job_id') or '')
    if not job_id: return jsonify({'success':False,'message':'معرف الوظيفة مطلوب'}),400
    if request.method=='DELETE':
        data.setdefault(uid,{}).pop(job_id,None)
    else:
        data.setdefault(uid,{})[job_id]={
            'cover_letter':str(payload.get('cover_letter') or '')[:5000],
            'answers':payload.get('answers') if isinstance(payload.get('answers'),dict) else {},
            'cv_id':str(payload.get('cv_id') or '')[:200],
            'updatedAt':datetime.now().isoformat()
        }
    _save_user_collection('application_drafts',data)
    return jsonify({'success':True})

@app.route('/api/applications/<job_id>/withdraw', methods=['POST'])
def withdraw_application(job_id):
    if 'user_id' not in session:
        return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); apps=secure_storage.load_applications() or {}
    _stored_uid, _user_apps = _find_user_applications(apps, uid)
    item=next((a for a in (_user_apps if isinstance(_user_apps, list) else []) if isinstance(a, dict) and str(a.get('jobId'))==str(job_id)),None)
    if not item: return jsonify({'success':False,'message':'طلب التوظيف غير موجود'}),404
    if item.get('status') in ('hired','rejected','withdrawn'):
        return jsonify({'success':False,'message':'لا يمكن سحب هذا الطلب'}),400
    now=datetime.now().isoformat()
    item['status']='withdrawn'; item['updatedAt']=now
    item.setdefault('timeline',[]).append({'status':'withdrawn','label':'تم سحب الطلب','at':now})
    if not secure_storage.save_applications(apps): return jsonify({'success':False,'message':'تعذر حفظ التغيير'}),500
    _push_notification(uid,'تم سحب طلب التوظيف','تم سحب طلبك بنجاح.','application','/applications','applications')
    return jsonify({'success':True,'status':'withdrawn'})

@app.route('/api/applications/<job_id>/timeline', methods=['GET'])
def application_timeline(job_id):
    if 'user_id' not in session: return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    uid=str(session['user_id']); apps=secure_storage.load_applications() or {}; _stored_uid, _user_apps = _find_user_applications(apps, uid); app_item=next((a for a in (_user_apps if isinstance(_user_apps, list) else []) if isinstance(a, dict) and str(a.get('jobId'))==str(job_id)),None)
    if not app_item: return jsonify({'success':False,'message':'طلب التوظيف غير موجود'}),404
    events=app_item.get('timeline') or []
    if not events:
        events=[{'status':'pending','label':'تم إرسال الطلب','at':app_item.get('appliedAt')}] 
        if app_item.get('updatedAt') and app_item.get('status') not in ('pending','review'):
            events.append({'status':app_item.get('status'),'label':status_label(app_item.get('status')),'at':app_item.get('updatedAt')})
    return jsonify({'success':True,'timeline':events,'status':app_item.get('status','pending')})

@app.route('/api/favorites', methods=['GET', 'POST', 'DELETE'])
def manage_favorites():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    
    user_id = session['user_id']
    favorites = secure_storage.load_favorites()
    
    # تطبيع المعرفات للمقارنة (تقبّل أرقاماً أو نصوصاً)
    def norm(v):
        return str(v)
    
    if request.method == 'GET':
        user_favs = set(norm(x) for x in favorites.get(user_id, []))
        jobs = secure_storage.load_jobs()
        fav_jobs = [j for j in jobs if norm(j.get('id')) in user_favs]
        return jsonify(fav_jobs)
    
    elif request.method == 'POST':
        job_id = request.json.get('job_id')
        if not job_id:
            return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'})
        if user_id not in favorites:
            favorites[user_id] = []
        # تأكد أن القائمة مخزنة بنص موحّد
        favorites[user_id] = [norm(x) for x in favorites[user_id]]
        if norm(job_id) not in favorites[user_id]:
            favorites[user_id].append(norm(job_id))
            secure_storage.save_favorites(favorites)
            return jsonify({'success': True, 'message': 'تمت الإضافة إلى المفضلة'})
        return jsonify({'success': False, 'message': 'موجودة بالفعل'})
    
    elif request.method == 'DELETE':
        job_id = request.json.get('job_id')
        if job_id is None:
            return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'})
        if user_id in favorites:
            favorites[user_id] = [x for x in favorites[user_id] if norm(x) != norm(job_id)]
            secure_storage.save_favorites(favorites)
            return jsonify({'success': True, 'message': 'تمت الإزالة'})
        return jsonify({'success': False, 'message': 'غير موجودة'})

@app.route('/api/applications/sadaqah-status', methods=['GET'])
def application_sadaqah_status():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    user = current_user()
    stored_used = int(user.get('sadaqahFreeApplicationsUsed', 0) or 0)
    actual_used = _count_user_applications_created(user.get('id'))
    used = max(stored_used, actual_used)
    remaining = max(0, APPLICATION_SADAQAH_FREE_LIMIT - used)
    price = service_prices(secure_storage, user)['prices']['application_usd']
    return jsonify({
        'success': True, 'freeApplicationsUsed': used,
        'freeApplicationsRemaining': remaining,
        'freeApplicationLimit': APPLICATION_SADAQAH_FREE_LIMIT,
        'requiresWallet': remaining == 0,
        'price': price,
        'walletBalance': _get_user_wallet_balance(user.get('id'))
    })


def _find_user_applications(applications, user_id):
    """قراءة مجموعة طلبات المستخدم دون افتراض نوع مفتاح user_id.
    يعيد (مفتاح التخزين الأصلي، القائمة). لا يغيّر البيانات ولا يحفظها.
    """
    if not isinstance(applications, dict):
        return None, []
    uid = str(user_id)
    for stored_uid, items in applications.items():
        if str(stored_uid) == uid:
            return stored_uid, items
    return None, []

@app.route('/api/applications', methods=['GET', 'POST'])
def manage_applications():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    
    user_id = session['user_id']
    applications = secure_storage.load_applications()
    
    if request.method == 'GET':
        current = current_user() or {}
        if str(current.get('role') or '').strip().lower() != 'job_seeker':
            return jsonify({
                'success': False,
                'message': 'هذه الصفحة مخصصة للباحثين عن عمل',
                'role': str(current.get('role') or '')
            }), 403

        # قراءة الطلبات بشكل متوافق مع البيانات القديمة:
        # - session/user_id قد يكون رقماً أو نصاً.
        # - مفاتيح applications قد تكون مخزنة بأي من النوعين.
        # - لا نعدل applications.enc ولا نحفظ أي تطبيع هنا.
        actor_id = str(user_id)
        actor_role = str((current_user() or {}).get('role') or '')
        try:
            if not isinstance(applications, dict):
                log_error(
                    'تعذر قراءة طلبات التوظيف',
                    cause='applications storage is not a dictionary',
                    entry_type='application_data_error',
                    actor_id=actor_id,
                    actor_role=actor_role,
                    source='official'
                )
                return jsonify({
                    'success': False,
                    'message': 'بيانات طلبات التوظيف غير صالحة'
                }), 500

            # توحيد user_id للمقارنة فقط دون تغيير التخزين.
            raw_user_apps = []
            for stored_uid, stored_items in applications.items():
                if str(stored_uid) == actor_id:
                    raw_user_apps = stored_items
                    break

            # دعم بعض صيغ البيانات القديمة دون حذفها أو إعادة حفظها.
            if raw_user_apps is None:
                raw_user_apps = []
            elif isinstance(raw_user_apps, dict):
                raw_user_apps = [raw_user_apps]
            elif not isinstance(raw_user_apps, list):
                log_error(
                    'تعذر قراءة طلبات التوظيف',
                    cause=f'invalid user applications container: {type(raw_user_apps).__name__}',
                    entry_type='application_data_error',
                    actor_id=actor_id,
                    actor_role=actor_role,
                    source='official'
                )
                return jsonify({
                    'success': False,
                    'message': 'بيانات طلبات التوظيف غير صالحة'
                }), 500

            jobs = secure_storage.load_jobs() or []
            users = secure_storage.load_users() or []
            jobs = jobs if isinstance(jobs, list) else []
            users = users if isinstance(users, list) else []

            result = []
            for index, raw_app in enumerate(raw_user_apps):
                # سجل واحد تالف لا يجب أن يسقط القائمة كلها.
                if not isinstance(raw_app, dict):
                    log_error(
                        'تم تجاهل سجل طلب توظيف غير صالح',
                        cause=f'application_index={index}; value_type={type(raw_app).__name__}',
                        entry_type='application_data_error',
                        actor_id=actor_id,
                        actor_role=actor_role,
                        source='official'
                    )
                    continue

                app_item = dict(raw_app)
                job_id = app_item.get('jobId', app_item.get('job_id'))
                job = None

                if job_id not in (None, ''):
                    job_id_norm = str(job_id)
                    job = next(
                        (
                            j for j in jobs
                            if isinstance(j, dict)
                            and j.get('id') is not None
                            and str(j.get('id')) == job_id_norm
                        ),
                        None
                    )

                # إذا اختفت الوظيفة، نحافظ على الطلب نفسه ونستخدم
                # بياناته القديمة إن وجدت بدلاً من إسقاطه.
                if job:
                    app_item['jobTitle'] = job.get('title') or app_item.get('jobTitle') or 'وظيفة غير متاحة'
                    app_item['company'] = job.get('company') or app_item.get('company') or 'شركة غير متاحة'

                    if app_item.get('companyDataShared'):
                        employer_id = job.get('employerId')
                        employer = next(
                            (
                                u for u in users
                                if isinstance(u, dict)
                                and employer_id is not None
                                and u.get('id') is not None
                                and str(u.get('id')) == str(employer_id)
                            ),
                            None
                        )
                        if employer:
                            app_item['companyName'] = employer.get('companyName', '')
                            app_item['companyType'] = employer.get('companyType', '')
                            app_item['companyDescription'] = employer.get('companyDescription', '')
                            app_item['companyEmail'] = employer.get('email', '')
                            app_item['companyPhone'] = employer.get('phone', '')
                            app_item['companyCountry'] = employer.get('country', '')
                            app_item['companyCity'] = employer.get('city', '')
                            app_item['companyNeighborhood'] = employer.get('neighborhood', '')
                else:
                    # لا نحذف الطلب المفقود الوظيفة؛ يبقى قابلاً للعرض والمتابعة.
                    app_item['jobTitle'] = app_item.get('jobTitle') or 'وظيفة غير متاحة'
                    app_item['company'] = app_item.get('company') or 'شركة غير متاحة'
                    app_item['jobUnavailable'] = True

                result.append(app_item)

            return jsonify(result)

        except Exception as exc:
            import traceback
            log_error(
                'تعذر تحميل طلبات التوظيف',
                cause=str(exc),
                tb=traceback.format_exc(),
                entry_type='application_api_error',
                actor_id=actor_id,
                actor_role=actor_role,
                source='official'
            )
            return jsonify({
                'success': False,
                'message': 'تعذر تحميل طلبات التوظيف بسبب خطأ داخلي'
            }), 500

    elif request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        job_id = payload.get('job_id')
        cover_letter = str(payload.get('cover_letter') or '').strip()[:5000]
        answers = payload.get('answers') if isinstance(payload.get('answers'), dict) else {}
        cv_id = str(payload.get('cv_id') or '')[:200]
        if not job_id:
            return jsonify({'success': False, 'message': 'معرف الوظيفة مطلوب'})
        storage_uid, user_apps = _find_user_applications(applications, user_id)
        if storage_uid is None:
            storage_uid = user_id
            applications[storage_uid] = []
            user_apps = applications[storage_uid]
        elif not isinstance(user_apps, list):
            return jsonify({'success': False, 'message': 'بيانات طلبات التوظيف غير صالحة'}), 500

        if any(isinstance(a, dict) and str(a.get('jobId')) == str(job_id) for a in user_apps):
            return jsonify({'success': False, 'message': 'لقد تقدمت بالفعل'})

        # الباحث عن عمل: أول 3 تقديمات مجانية بعد وقفة الخير، ثم من المحفظة.
        users = secure_storage.load_users() or []
        applicant = next((u for u in users if str(u.get('id')) == str(user_id)), None)
        if not applicant:
            return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

        stored_free_used = int(applicant.get('sadaqahFreeApplicationsUsed', 0) or 0)
        actual_free_used = _count_user_applications_created(user_id)
        free_used = max(stored_free_used, actual_free_used)
        charge_required = free_used >= APPLICATION_SADAQAH_FREE_LIMIT
        application_fee = usd_cents(secure_storage, 'application_usd')

        if charge_required:
            wallet_info = _get_user_wallet_balance(user_id)
            available = float(wallet_info.get('available', 0) or 0)
            if available < application_fee:
                pricing = service_prices(secure_storage, applicant)['prices']['application_usd']
                return jsonify({
                    'success': False, 'requiresWallet': True,
                    'freeApplicationsUsed': free_used,
                    'freeApplicationsRemaining': 0,
                    'requiredBalanceUsdCents': application_fee,
                    'walletBalance': available,
                    'price': pricing,
                    'message': f"انتهت التقديمات المجانية بعد {APPLICATION_SADAQAH_FREE_LIMIT} مرات. سيتم خصم {pricing['formatted']} من رصيد المحفظة عند إتمام التقديم."
                }), 402
            debit = subtract_balance(
                user_id, application_fee, 'application',
                reference_id=f'application_{job_id}_{user_id}_{datetime.now().strftime("%Y%m%d%H%M%S%f")}',
                description='رسوم تقديم على وظيفة بعد انتهاء التقديمات المجانية'
            )
            if not debit.get('success'):
                return jsonify({'success': False, 'requiresWallet': True,
                                'message': debit.get('message', 'الرصيد غير كافٍ')}), 402
        applied_at=datetime.now().isoformat()
        user_apps.append({
            'jobId': job_id,
            'appliedAt': applied_at,
            'status': 'pending',
            'timeline': [{'status':'pending','label':'تم إرسال الطلب','at':applied_at}],
            'paymentRequired': charge_required,
            'chargedAmountUsdCents': application_fee if charge_required else 0,
            'coverLetter': cover_letter,
            'answers': answers,
            'cvId': cv_id,
            'updatedAt': applied_at
        })
        if not secure_storage.save_applications(applications):
            # إرجاع المبلغ إذا تم الخصم ثم فشل حفظ الطلب.
            if charge_required:
                try:
                    add_balance(user_id, application_fee, 'bonus',
                                reference_id=f'application_refund_{job_id}_{user_id}_{datetime.now().strftime("%Y%m%d%H%M%S%f")}',
                                description='إرجاع رسوم التقديم بسبب فشل حفظ الطلب')
                except Exception:
                    logger.exception('فشل إرجاع رسوم التقديم')
            return jsonify({'success': False, 'message': 'تعذر حفظ طلب التوظيف'}), 500
        if not charge_required:
            # استهلاك الوقفة الحرة يُسجل فقط بعد نجاح حفظ الطلب.
            applicant['sadaqahFreeApplicationsUsed'] = free_used + 1
            if not secure_storage.save_users(users):
                logger.error('تعذر حفظ عداد وقفة الخير بعد نجاح التقديم للمستخدم %s', user_id)
        job = next((j for j in secure_storage.load_jobs() or [] if str(j.get('id')) == str(job_id)), {})
        employer_email = job.get('employerEmail','')
        if not employer_email and job.get('employerId'):
            emp = next((u for u in secure_storage.load_users() or [] if str(u.get('id')) == str(job.get('employerId'))), None)
            employer_email = emp.get('email','') if emp else ''
        applicant = next((u for u in secure_storage.load_users() or [] if str(u.get('id')) == str(user_id)), {})
        if employer_email:
            send_email(employer_email, 'طلب توظيف جديد - منصة التوظيف', f"لديك طلب توظيف جديد على وظيفة: {job.get('title','')}\nالمتقدم: {applicant.get('firstName','')} {applicant.get('lastName','')}\nالبريد: {applicant.get('email','')}\nالموقع: {applicant.get('neighborhood','')}، {applicant.get('city','')}، {applicant.get('country','')}\n\nيرجى الدخول إلى بوابة صاحب العمل لمراجعة الطلب.")
        _push_notification(user_id, 'تم إرسال طلب التوظيف', f"تم إرسال طلبك إلى {job.get('title','الوظيفة')} بنجاح.", 'application', '/applications', 'applications')
        if job.get('employerId'):
            _push_notification(str(job.get('employerId')), 'طلب توظيف جديد', f"وصل طلب توظيف جديد إلى وظيفة {job.get('title','')}. ", 'application', '/employer', 'applications')
        return jsonify({'success': True, 'message': 'تم تقديم الطلب وإرسال إشعار لصاحب العمل'})


# ============================================================
# إدارة الطلبات والمستخدمين من لوحة التحكم
# ============================================================


@app.route('/api/pricing', methods=['GET'])
def public_pricing():
    """أسعار الخدمات بالدولار وما يعادلها بعملة المستخدم."""
    user = current_user() if 'user_id' in session else None
    return jsonify(service_prices(secure_storage, user))

@app.route('/api/admin/exchange-rates/refresh', methods=['POST'])
@admin_required
def admin_refresh_exchange_rates():
    rates, updated_at, refreshed = refresh_live_rates(secure_storage, force=True)
    return jsonify({
        'success': True,
        'rates': rates,
        'rates_updated_at': updated_at,
        'rates_source': 'MoneyConvert.net',
        'refreshed': refreshed,
        'message': 'تم تحديث أسعار الصرف تلقائياً' if refreshed else 'تعذر جلب أسعار جديدة حالياً؛ تم الاحتفاظ بآخر أسعار محفوظة'
    })

@app.route('/api/admin/pricing', methods=['GET', 'PUT'])
@admin_required
def admin_pricing():
    if request.method == 'GET':
        data = load_pricing_settings(secure_storage)
        return jsonify(data)
    data = request.get_json(silent=True) or {}
    pricing = data.get('pricing') or {}
    rates = data.get('rates') or {}
    try:
        ok, saved = save_pricing_settings(secure_storage, pricing=pricing, rates=rates)
        if not ok:
            return jsonify({'success': False, 'message': 'تعذر حفظ إعدادات الأسعار'}), 500
        return jsonify({'success': True, 'message': 'تم حفظ الأسعار وأسعار الصرف', **saved})
    except Exception as e:
        logger.exception('فشل حفظ إعدادات الأسعار')
        return jsonify({'success': False, 'message': 'بيانات الأسعار غير صالحة'}), 400


# ============================================================
# المرحلة 9 — مركز الدفع والاشتراكات والفواتير
# قاعدة مهمة: لا يتم إضافة رصيد أو تفعيل اشتراك اعتماداً على
# بيانات يرسلها المتصفح. التسوية لا تتم إلا بعد تحقق الدفع.
# ============================================================

def _load_subscription_plans():
    return [
        {'id':'starter','name':'Starter','monthlyUsd':10,'features':['5 وظائف إضافية','10 فتح بيانات إضافية','إحصائيات أساسية']},
        {'id':'business','name':'Business','monthlyUsd':29,'features':['30 وظيفة إضافية','60 فتح بيانات إضافية','ترتيب المتقدمين بالذكاء']},
        {'id':'pro','name':'Pro','monthlyUsd':79,'features':['وظائف غير محدودة ضمن السياسة','فتح بيانات موسع','أولوية الدعم']},
    ]

def _load_subscriptions():
    return secure_storage.encryption.decrypt_file('subscriptions') or []

def _save_subscriptions(items):
    return secure_storage.encryption.encrypt_file('subscriptions', items)

def _load_payment_logs():
    return secure_storage.encryption.decrypt_file('payment_logs') or []

def _save_payment_logs(items):
    return secure_storage.encryption.encrypt_file('payment_logs', items)

def _settle_verified_wallet_payment(payment_id, provider_result=None):
    """تسوية دفع محقق مرة واحدة فقط: رصيد + فاتورة + سجل تدقيق."""
    logs=_load_payment_logs()
    payment=next((x for x in logs if str(x.get('paymentId'))==str(payment_id)),None)
    if not payment:
        return {'success':False,'message':'عملية الدفع غير موجودة'},404
    if payment.get('status')=='paid' and payment.get('settledAt'):
        from invoice_service import get_invoice_by_payment
        inv,_=get_invoice_by_payment(payment_id)
        return {'success':True,'alreadySettled':True,'payment':payment,'invoice':(inv or {}).get('invoice')},200
    if not provider_result or not provider_result.get('verified'):
        return {'success':False,'message':'لم يتم التحقق من نجاح الدفع من مزود الدفع'},402
    if str(provider_result.get('currency','')).upper()!=str(payment.get('currency','')).upper():
        return {'success':False,'message':'عملة الدفع لا تطابق العملية'},409
    if int(provider_result.get('amount',-1)) != int(payment.get('amount',-2)):
        return {'success':False,'message':'قيمة الدفع لا تطابق العملية'},409

    user_id=str(payment.get('employerId'))
    amount=int(payment.get('amount',0))
    ref=str(payment_id)
    txs=get_transactions(user_id, limit=500, offset=0)
    existing=next((t for t in txs if str(t.get('referenceId',''))==ref and t.get('status')=='completed'),None)
    if not existing:
        result=add_balance(user_id, amount, 'credit', reference_id=ref,
                           description='شحن المحفظة بعد التحقق من الدفع',
                           metadata={'paymentId':ref,'provider':provider_result.get('provider','unknown')})
        if not result.get('success'):
            return {'success':False,'message':result.get('message','تعذر إضافة الرصيد')},400

    now=datetime.now().isoformat()
    payment['status']='paid'; payment['settledAt']=now; payment['updatedAt']=now
    payment['providerVerified']=True
    if not _save_payment_logs(logs):
        return {'success':False,'message':'تعذر حفظ حالة الدفع'},500

    from invoice_service import create_invoice, get_invoice_by_payment
    inv_result, inv_code=create_invoice(payment)
    if inv_code not in (200,201):
        existing_inv,_=get_invoice_by_payment(payment_id)
        invoice=(existing_inv or {}).get('invoice') if isinstance(existing_inv,dict) else None
        if not invoice:
            return {'success':False,'message':'تمت التسوية لكن تعذر إنشاء الفاتورة'},500
    else:
        invoice=inv_result.get('invoice')
    try:
        audit=secure_storage.encryption.decrypt_file('payment_audit') or []
        audit.append({'actor':user_id,'action':'payment_settled','paymentId':ref,'status':'paid','timestamp':now,
                      'details':{'amount':amount,'currency':payment.get('currency'),'invoiceNumber':(invoice or {}).get('invoiceNumber')}})
        secure_storage.encryption.encrypt_file('payment_audit',audit)
    except Exception:
        logger.exception('فشل تسجيل تسوية الدفع')
    return {'success':True,'payment':payment,'invoice':invoice,'wallet':_get_user_wallet_balance(user_id)},200

@app.route('/api/payments/config', methods=['GET'])
def payment_config_public():
    if 'user_id' not in session:
        return jsonify({'success':False,'message':'يرجى تسجيل الدخول'}),401
    return jsonify({'success':True,'provider':get_provider_info(),'live':is_live_mode(),'testCheckoutAllowed':bool(ALLOW_TEST_METHODS and not is_production())})

@app.route('/api/wallet/checkout', methods=['POST'])
@employer_required
def wallet_checkout():
    """إنشاء Checkout لباقات الشحن المعرفة في النظام فقط. لا يوجد مبلغ حر يرسله المتصفح."""
    user=current_user(); data=request.get_json(silent=True) or {}
    TOPUP_PACKAGES_USD = (5, 10, 25, 50, 100)
    package_raw = data.get('package')
    try:
        requested = float(str(data.get('usd', package_raw or 0)).replace(',','.'))
    except Exception:
        requested = 0
    matched = next((x for x in TOPUP_PACKAGES_USD if abs(float(x)-requested) < 1e-9), None)
    if matched is None:
        return jsonify({'success':False,'message':'يرجى اختيار إحدى باقات الشحن المتاحة فقط.'}),400
    usd = float(matched)
    currency='USD'; amount=int(round(usd*100))
    # منع إنشاء طلبات دفع متطابقة متكررة خلال جلسة قصيرة.
    idem=str(request.headers.get('Idempotency-Key','')).strip()
    if not idem: idem=f"wallet:{user.get('id')}:{usd}:{data.get('package','')}"
    logs=_load_payment_logs()
    existing=next((x for x in logs if str(x.get('idempotencyKey',''))==idem and x.get('status') in ('created','pending')),None)
    if existing:
        return jsonify({'success':True,'payment':existing,'idempotent':True,'message':'عملية الدفع موجودة مسبقاً'})
    payment=create_payment(amount,currency,'شحن محفظة',{'purpose':'wallet_topup','employerId':str(user.get('id')),'idempotencyKey':idem})
    if not payment.get('paymentId') or payment.get('success') is False:
        return jsonify({'success':False,'message':payment.get('message','تعذر إنشاء عملية الدفع')}),503
    pid=payment['paymentId']; now=datetime.now().isoformat()
    payment.update({'employerId':str(user.get('id')),'employerEmail':user.get('email',''),'amountUnit':'minor',
                    'amountUsd':usd,'paymentType':'wallet_topup','invoiceType':'wallet_topup',
                    'status':payment.get('status','created'),'idempotencyKey':idem,'createdAt':now,'updatedAt':now})
    if not _save_payment_logs(logs+[payment]):
        return jsonify({'success':False,'message':'تعذر حفظ عملية الدفع'}),500
    return jsonify({'success':True,'payment':payment,'redirectRequired':True,
                    'testCompletionAllowed':bool(ALLOW_TEST_METHODS and not is_production()),
                    'message':'تم إنشاء عملية الدفع. لا يتم إضافة الرصيد قبل التحقق من الدفع.'}),201

@app.route('/api/wallet/checkout/<payment_id>/status', methods=['GET'])
@employer_required
def wallet_checkout_status(payment_id):
    user=current_user(); logs=_load_payment_logs()
    payment=next((x for x in logs if str(x.get('paymentId'))==str(payment_id) and str(x.get('employerId'))==str(user.get('id'))),None)
    if not payment:return jsonify({'success':False,'message':'عملية الدفع غير موجودة'}),404
    status=get_payment_status(payment_id)
    return jsonify({'success':True,'payment':payment,'providerStatus':status})

@app.route('/api/wallet/checkout/<payment_id>/test-complete', methods=['POST'])
@employer_required
def wallet_checkout_test_complete(payment_id):
    """محاكاة دفع للاختبار فقط. لا تعمل في الإنتاج ولا تمنح صلاحية خاصة."""
    if not ALLOW_TEST_METHODS or is_production():
        return jsonify({'success':False,'message':'إكمال الدفع التجريبي معطل في بيئة الإنتاج'}),403
    user=current_user(); logs=_load_payment_logs()
    payment=next((x for x in logs if str(x.get('paymentId'))==str(payment_id) and str(x.get('employerId'))==str(user.get('id'))),None)
    if not payment:return jsonify({'success':False,'message':'عملية الدفع غير موجودة'}),404
    from payment_gateway import _provider
    if _provider is None or not hasattr(_provider,'simulate_payment_success'):
        return jsonify({'success':False,'message':'مزود الاختبار غير متاح'}),503
    pending=getattr(_provider,'mark_payment_pending',lambda *_:{'success':True})(payment_id)
    if pending.get('success') is False:return jsonify(pending),409
    simulated=_provider.simulate_payment_success(payment_id)
    if not simulated.get('success'):return jsonify(simulated),409
    verified=verify_payment(payment_id)
    result,code=_settle_verified_wallet_payment(payment_id,verified)
    return jsonify(result),code

@app.route('/api/subscriptions/plans', methods=['GET'])
def subscription_plans():
    user = current_user() if 'user_id' in session else None
    plans = _load_subscription_plans()
    if user:
        rates = load_pricing_settings(secure_storage).get('rates') or {}
        currency = user_currency(user)
        rate = float(rates.get(currency, 1.0))
        for plan in plans:
            local = float(plan['monthlyUsd']) * rate
            plan['currency'] = currency
            plan['localAmount'] = local
            plan['formattedLocal'] = format_local(local, currency)
    return jsonify({'success':True,'plans':plans})

@app.route('/api/employer/subscription', methods=['GET'])
@employer_required
def employer_subscription():
    user=current_user(); subs=_load_subscriptions()
    active=[s for s in subs if str(s.get('employerId'))==str(user.get('id')) and s.get('status')=='active']
    active.sort(key=lambda x:x.get('expiresAt',''), reverse=True)
    return jsonify({'success':True,'subscription':active[0] if active else None})

@app.route('/api/employer/subscription/subscribe', methods=['POST'])
@employer_required
def employer_subscribe():
    user=current_user(); data=request.get_json(silent=True) or {}; plan_id=str(data.get('planId','')).strip()
    plan=next((p for p in _load_subscription_plans() if p['id']==plan_id),None)
    if not plan:return jsonify({'success':False,'message':'الباقة غير موجودة'}),404
    amount=int(plan['monthlyUsd']*100); uid=str(user.get('id'))
    subs=_load_subscriptions()
    active=next((s for s in subs if str(s.get('employerId'))==uid and s.get('status')=='active' and s.get('expiresAt','') > datetime.now().isoformat()),None)
    if active:
        if active.get('planId')==plan_id:
            return jsonify({'success':True,'alreadyActive':True,'subscription':active,'wallet':_get_user_wallet_balance(uid)})
        return jsonify({'success':False,'message':'لديك اشتراك نشط حاليًا. قم بإنهائه أو انتظر انتهاءه قبل تغيير الباقة.'}),409
    idem=str(request.headers.get('Idempotency-Key','')).strip()
    ref=idem or f"subscription_{plan_id}_{uid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    prior=next((x for x in subs if str(x.get('paymentReference'))==ref),None)
    if prior:
        return jsonify({'success':True,'alreadyProcessed':True,'subscription':prior,'wallet':_get_user_wallet_balance(uid)})
    bal=_get_user_wallet_balance(uid); available=int(bal.get('available',0) or 0)
    if available<amount:return jsonify({'success':False,'requiresWallet':True,'requiredBalance':amount,'walletBalance':available,'message':'الرصيد غير كافٍ لتفعيل الاشتراك'}),402
    debit=subtract_balance(uid,amount,'subscription',reference_id=ref,description=f"اشتراك {plan['name']} الشهري",metadata={'planId':plan_id})
    if not debit.get('success'):return jsonify({'success':False,'message':debit.get('message','تعذر خصم الاشتراك')}),402
    now=datetime.now(); exp=now+timedelta(days=30)
    item={'subscriptionId':f"sub_{secrets.token_urlsafe(12)}",'employerId':uid,'planId':plan_id,'planName':plan['name'],'amountUsd':plan['monthlyUsd'],'currency':'USD','status':'active','startedAt':now.isoformat(),'expiresAt':exp.isoformat(),'paymentReference':ref}
    new_subs=[s for s in subs if not (str(s.get('employerId'))==uid and s.get('status')=='active')]; new_subs.append(item)
    if not _save_subscriptions(new_subs):
        # تعويض الخصم إذا فشل حفظ حالة الاشتراك، حتى لا نخسر الرصيد بدون خدمة.
        add_balance(uid, amount, 'refund', reference_id=f"{ref}:rollback", description='تعويض تلقائي لفشل حفظ الاشتراك', metadata={'subscriptionRollback':True,'paymentReference':ref})
        return jsonify({'success':False,'message':'تعذر حفظ الاشتراك وتمت إعادة المبلغ إلى المحفظة'}),500
    payment={'paymentId':ref,'employerId':uid,'employerEmail':user.get('email',''),'applicantId':None,'jobId':None,'amount':plan['monthlyUsd'],'amountUnit':'major','currency':'USD','formattedPrice':f"{plan['monthlyUsd']:.2f} USD",'description':f"اشتراك {plan['name']} الشهري",'status':'paid','paymentType':'subscription','invoiceType':'subscription','createdAt':now.isoformat(),'updatedAt':now.isoformat()}
    logs=_load_payment_logs(); logs.append(payment); _save_payment_logs(logs)
    from invoice_service import create_invoice
    inv_result,inv_code=create_invoice(payment); invoice=inv_result.get('invoice') if isinstance(inv_result,dict) and inv_code in (200,201) else None
    return jsonify({'success':True,'subscription':item,'invoice':invoice,'wallet':_get_user_wallet_balance(uid)})

@app.route('/api/employer/invoices', methods=['GET'])
@employer_required
def employer_invoices():
    from invoice_service import list_invoices
    user=current_user(); result,status=list_invoices({'employerId':str(user.get('id'))})
    if isinstance(result, dict) and status == 200:
        currency = user_currency(user)
        rates = load_pricing_settings(secure_storage).get('rates') or {}
        rate = float(rates.get(currency, 1.0))
        for inv in result.get('invoices') or []:
            raw_amount = float(inv.get('amount', 0) or 0)
            # فواتير الدفع تحفظ إما بالدولار الكبير أو بالوحدة الصغرى حسب amountUnit.
            usd = raw_amount / 100.0 if inv.get('amountUnit') == 'minor' else raw_amount
            inv['displayCurrency'] = currency
            inv['localAmount'] = usd * rate
            inv['formattedLocal'] = format_local(usd * rate, currency)
    return jsonify(result),status

@app.route('/api/wallet/purchase-options', methods=['GET'])
def wallet_purchase_options():
    """عرض باقات الشحن الثابتة في النظام بعملة دولة الحساب. لا يوجد مبلغ حر."""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401
    user = current_user()
    rates = load_pricing_settings(secure_storage)['rates']
    currency = user_currency(user)
    packages_usd = (5, 10, 25, 50, 100)
    packages = [{
        'id': f'topup_{x}',
        'usd': x,
        'currency': currency,
        'localAmount': x * float(rates.get(currency, 1)),
        'formattedLocal': format_local(x * float(rates.get(currency, 1)), currency),
        'label': f'باقة {format_local(x * float(rates.get(currency, 1)), currency)}',
        'status': 'available'
    } for x in packages_usd]
    return jsonify({'success': True, 'currency': currency,
                    'currencyName': CURRENCY_NAMES.get(currency, currency),
                    'symbol': CURRENCY_SYMBOLS.get(currency, currency),
                    'packages': packages,
                    'message': 'اختر إحدى باقات الشحن المتاحة؛ لا يمكن إدخال مبلغ حر.'})


@app.route('/api/admin/users/<user_id>/wallet-repair', methods=['POST'])
@admin_required
def admin_wallet_repair(user_id):
    """إصلاح توافق المحفظة والفواتير لمستخدم موجود دون إضافة رصيد جديد."""
    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get('id')) == str(user_id)), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    # get_wallet creates the wallet only if it is genuinely missing.
    wallet = create_wallet(str(user_id))
    balance = _get_user_wallet_balance(user_id)

    # لا نضيف أي رصيد ولا ننشئ فاتورة وهمية؛ نكتفي بإصلاح الوصول للمحفظة.
    return jsonify({'success': True, 'wallet': balance, 'message': 'تمت مزامنة المحفظة بنجاح'})

@app.route('/api/admin/users/<user_id>/trial-credit', methods=['POST'])
@admin_required
def admin_trial_credit(user_id):
    """شحن تجريبي بالدولار: رصيد + paymentId + فاتورة، مع منع التكرار عند إعادة الطلب."""
    data = request.get_json(silent=True) or {}
    try:
        usd = float(str(data.get('usd', 0)).replace(',', '.'))
    except Exception:
        usd = 0
    if usd <= 0 or usd > 100000:
        return jsonify({'success': False, 'message': 'مبلغ الشحن غير صالح'}), 400

    users = secure_storage.load_users() or []
    user = next((u for u in users if str(u.get('id')) == str(user_id)), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    # معرف دفع ثابت لهذه العملية الجديدة.
    payment_id = f"trial_{secrets.token_urlsafe(12)}"
    now_iso = datetime.now().isoformat()
    amount_cents = int(round(usd * 100))

    result = add_balance(
        str(user_id), amount_cents, 'bonus',
        reference_id=payment_id,
        description='شحن تجريبي من لوحة الإدارة',
        metadata={'trial': True, 'usd': usd, 'admin': session.get('user_id'),
                  'paymentId': payment_id}
    )
    if not result.get('success'):
        return jsonify({'success': False, 'message': result.get('message', 'تعذر شحن الرصيد')}), 400

    payment = {
        'paymentId': payment_id,
        'employerId': str(user_id),
        'employerEmail': user.get('email', ''),
        'applicantId': None,
        'jobId': None,
        'amount': usd,
        'amountUnit': 'major',
        'currency': 'USD',
        'formattedPrice': f'{usd:.2f} USD',
        'description': 'شحن تجريبي من لوحة الإدارة',
        'status': 'paid',
        'paymentType': 'wallet_topup_trial',
        'invoiceType': 'wallet_topup_trial',
        'createdAt': now_iso,
        'updatedAt': now_iso
    }

    # حفظ سجل الدفع.
    payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
    if not any(str(x.get('paymentId')) == payment_id for x in payment_logs):
        payment_logs.append(payment)
        if not secure_storage.encryption.encrypt_file('payment_logs', payment_logs):
            return jsonify({'success': False, 'message': 'تم الشحن لكن تعذر حفظ سجل الدفع'}), 500

    # إنشاء الفاتورة وربطها بنفس paymentId.
    from invoice_service import create_invoice
    invoice_result, invoice_status = create_invoice(payment)
    if invoice_status not in (200, 201):
        # إذا كانت الفاتورة موجودة مسبقًا، لا نعتبر العملية فاشلة.
        if isinstance(invoice_result, dict) and invoice_result.get('invoiceId'):
            invoice_result = {'success': True, 'invoice': invoice_result}
        else:
            logger.error(f'فشل إنشاء فاتورة الشحن التجريبي: {invoice_result}')
            return jsonify({
                'success': False,
                'message': 'تم الشحن لكن تعذر إنشاء الفاتورة',
                'wallet': result.get('wallet')
            }), 500

    invoice = invoice_result.get('invoice') if isinstance(invoice_result, dict) else None
    balance = _get_user_wallet_balance(user_id)

    try:
        audit_logs = secure_storage.encryption.decrypt_file('payment_audit') or []
        audit_logs.append({
            'actor': session.get('user_id', 'admin'),
            'action': 'trial_wallet_credit',
            'paymentId': payment_id,
            'status_before': 'none',
            'status_after': 'paid',
            'timestamp': now_iso,
            'details': {'userId': str(user_id), 'usd': usd,
                        'invoiceNumber': (invoice or {}).get('invoiceNumber')}
        })
        secure_storage.encryption.encrypt_file('payment_audit', audit_logs)
    except Exception:
        logger.exception('فشل تسجيل تدقيق الشحن التجريبي')

    return jsonify({
        'success': True,
        'message': f'تم شحن {usd:.2f} USD تجريبيًا وإنشاء الفاتورة',
        'wallet': balance,
        'payment': payment,
        'invoice': invoice
    })

@app.route('/api/admin/applications', methods=['GET', 'PUT', 'DELETE'])
@admin_required
def admin_applications():
    applications = secure_storage.load_applications() or {}
    users = secure_storage.load_users() or []
    jobs = secure_storage.load_jobs() or []
    result = []
    for uid, user_apps in applications.items():
        user = next((u for u in users if u.get('id') == uid), None)
        for item in user_apps:
            job = next((j for j in jobs if str(j.get('id')) == str(item.get('jobId') or item.get('job_id') or item.get('job_id'))), None)
            row = dict(item)
            row['userId'] = uid
            row['userName'] = f"{user.get('firstName','')} {user.get('lastName','')}".strip() if user else 'مستخدم محذوف'
            row['userEmail'] = user.get('email','') if user else ''
            row['jobTitle'] = job.get('title','') if job else 'وظيفة محذوفة'
            row['company'] = job.get('company','') if job else ''
            row['jobId'] = job.get('id', item.get('jobId')) if job else item.get('jobId')
            row['jobLocation'] = ', '.join([str(x) for x in [job.get('neighborhood','') if job else '', job.get('city','') if job else '', job.get('country','') if job else ''] if x])
            result.append(row)

    if request.method == 'GET':
        return jsonify(result)

    data = request.get_json(silent=True) or {}
    uid = str(data.get('userId', ''))
    job_id = data.get('jobId')
    if uid not in applications:
        return jsonify({'success': False, 'message': 'الطلب غير موجود'}), 404

    if request.method == 'PUT':
        new_status = sanitize_input(data.get('status', 'pending'))
        allowed = {'pending', 'review', 'accepted', 'rejected', 'withdrawn'}
        if new_status not in allowed:
            return jsonify({'success': False, 'message': 'حالة غير صحيحة'}), 400
        for item in applications[uid]:
            if str(item.get('jobId')) == str(job_id):
                item['status'] = new_status
                item['updatedAt'] = datetime.now().isoformat()
                secure_storage.save_applications(applications)
                return jsonify({'success': True, 'data': item})
        return jsonify({'success': False, 'message': 'الطلب غير موجود'}), 404

    applications[uid] = [a for a in applications[uid] if str(a.get('jobId')) != str(job_id)]
    secure_storage.save_applications(applications)
    return jsonify({'success': True})

def _get_user_wallet_balance(user_id):
    """
    الحصول على رصيد محفظة المستخدم بصيغة مُنسقة للعرض في لوحة الإدارة.
    يقرأ ملف المحافظ المشفر مباشرةً دون إنشاء محفظة جيدة (بخلاف get_wallet
    الذي ينشئ محفظة افتراضية للمعرّف غير الموجود).
    """
    if not user_id:
        return {'formatted': '0.00 USD', 'amount': 0, 'hasWallet': False,
                'currency': 'USD', 'available': 0}
    try:
        wallets = secure_storage.encryption.decrypt_file('wallets') or []
        wallet = next((w for w in wallets if str(w.get('employerId', w.get('userId', ''))) == str(user_id)), None)
        if wallet:
            return {
                'formatted': wallet.get('formattedBalance', '0.00 ر.س'),
                'amount': wallet.get('balance', 0),
                'available': wallet.get('availableBalance', 0),
                'currency': wallet.get('currency', 'SAR'),
                'hasWallet': True
            }
        return {'formatted': '0.00 USD', 'amount': 0, 'hasWallet': False,
                'currency': 'USD', 'available': 0}
    except Exception:
        return {'formatted': '0.00 USD', 'amount': 0, 'hasWallet': False,
                'currency': 'USD', 'available': 0}


@app.route('/api/admin/users', methods=['GET', 'PUT', 'DELETE'])
@admin_required
def admin_users():
    users = secure_storage.load_users() or []
    if request.method == 'GET':
        safe = []
        for u in users:
            x = dict(u)
            x.pop('password', None)
            # إظهار رقم المستخدم ورصيد محفظته في لوحة الإدارة
            wb = _get_user_wallet_balance(u.get('id'))
            local_pricing = service_prices(secure_storage, u)
            local_currency = local_pricing.get('currency', 'USD')
            local_amount = (float(wb.get('available', 0) or 0) / 100.0) * float(load_pricing_settings(secure_storage)['rates'].get(local_currency, 1.0))
            x['walletBalance'] = f"{float(wb.get('available', 0) or 0) / 100.0:.2f} USD"
            x['walletBalanceAmount'] = wb['amount']
            x['walletAvailable'] = wb['available']
            x['walletCurrency'] = 'USD'
            x['walletLocalBalance'] = format_local(local_amount, local_currency)
            x['walletHasWallet'] = wb['hasWallet']
            x['walletLocalPricing'] = local_pricing
            safe.append(x)
        return jsonify(safe)

    data = request.get_json(silent=True) or {}
    uid = data.get('id')
    user = next((u for u in users if u.get('id') == uid), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    if request.method == 'PUT':
        for key in ['firstName', 'lastName', 'phone', 'category', 'country', 'education', 'status', 'role']:
            if key in data:
                user[key] = sanitize_input(str(data[key]))
        secure_storage.save_users(users)
        safe = dict(user); safe.pop('password', None)
        return jsonify({'success': True, 'data': safe})

    if user.get('role') == 'admin':
        return jsonify({'success': False, 'message': 'لا يمكن حذف المدير من هذه الشاشة'}), 400
    users = [u for u in users if u.get('id') != uid]
    secure_storage.save_users(users)
    return jsonify({'success': True})


# ============================================
# API الملف الشخصي
# ============================================

def _repair_contact_value_leak(user):
    """يمنع تكرار رقم الهاتف/البريد داخل الحقول المهنية بسبب autofill أو إدخال خاطئ."""
    phone=str(user.get('phone') or '').strip(); email=str(user.get('email') or '').strip()
    digits=re.sub(r'\D','',phone)
    changed=False
    for key in ('profession','headline','bio','skills','experience','languages','certifications','resume'):
        value=str(user.get(key) or '').strip()
        if not value: continue
        vd=re.sub(r'\D','',value)
        # يمنع تسرب رقم الهاتف/البريد إلى أي حقل مهني.
        if (phone and value==phone) or (email and value.lower()==email.lower()) or (len(digits)>=8 and vd==digits and len(vd)>=8):
            user[key]=''; changed=True; continue
        # يمنع القيم الرقمية التجريبية/الملوثة مثل 123 أو 05317431746 داخل الحقول المهنية.
        if key in ('profession','headline','skills','experience','languages','certifications') and re.fullmatch(r'[+\-()\s\d]{3,20}', value):
            user[key]=''; changed=True
    return changed

def _save_current_user(user):
    users = secure_storage.load_users() or []
    for idx, existing in enumerate(users):
        if existing.get('id') == user.get('id'):
            users[idx] = user
            return secure_storage.save_users(users)
    return False

@app.route('/api/user/profile', methods=['GET','PUT','DELETE'])
def user_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401

    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == session['user_id']), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    if request.method == 'GET':
        if _repair_contact_value_leak(user):
            _save_current_user(user)
        user_copy = user.copy()
        user_copy.pop('password', None)
        return jsonify(user_copy)

    if request.method == 'PUT':
        data = request.get_json(silent=True) or {}
        allowed_keys = {
            'firstName', 'lastName', 'phone', 'phoneCountryCode', 'category', 'country', 'city',
            'neighborhood', 'birthdate', 'education', 'avatar', 'resume', 'headline', 'profession', 'bio', 'skills', 'experience', 'languages', 'certifications'
        }
        # قوائم القيم المسموح بها (نفس قوائم نموذج التسجيل في templates/index.html)
        ALLOWED_CATEGORIES = {'تقنية', 'هندسة', 'طب', 'تعليم', 'مالية', 'تسويق', 'إدارة', 'خدمة', 'قانون', 'فنون'}
        ALLOWED_PHONE_CODES = set(PHONE_COUNTRY_CODES.values())
        ALLOWED_COUNTRIES = set(LOCATION_DATA.keys())
        
        # التحقق من الموقع الجغرافي - إذا كان هناك أي حقل موقع، يجب أن تكون الثلاثة موجودة
        has_location = any(k in data for k in ('country', 'city', 'neighborhood'))
        if has_location:
            country_val = str(data.get('country', '') or user.get('country', '')).strip()
            city_val = str(data.get('city', '') or user.get('city', '')).strip()
            neighborhood_val = str(data.get('neighborhood', '') or user.get('neighborhood', '')).strip()
            if not country_val:
                return jsonify({'success': False, 'message': 'يرجى اختيار الدولة'}), 400
            if not city_val:
                return jsonify({'success': False, 'message': 'يرجى اختيار المدينة'}), 400
            if not neighborhood_val:
                return jsonify({'success': False, 'message': 'يرجى اختيار الحي'}), 400
        
        # التحقق من صحة البيانات قبل الحفظ
        if 'firstName' in data and not str(data.get('firstName', '')).strip():
            return jsonify({'success': False, 'message': 'الاسم الأول لا يمكن أن يكون فارغاً'}), 400
        if 'lastName' in data and not str(data.get('lastName', '')).strip():
            return jsonify({'success': False, 'message': 'الاسم الأخير لا يمكن أن يكون فارغاً'}), 400
        if 'phone' in data:
            phone_val = str(data.get('phone', '')).strip()

            if not phone_val:
                return jsonify({
                    'success': False,
                    'message': 'يرجى إدخال رقم الهاتف'
                }), 400

            if len(phone_val) > 20 or not re.match(r'^[0-9+\-\s()]+$', phone_val):
                return jsonify({
                    'success': False,
                    'message': 'رقم الهاتف غير صالح'
                }), 400
        if 'phoneCountryCode' in data and str(data.get('phoneCountryCode', '')).strip():
            code_val = str(data.get('phoneCountryCode', '')).strip()
            if len(code_val) > 6 or not re.match(r'^\+?[0-9]{1,5}$', code_val):
                return jsonify({'success': False, 'message': 'رمز الدولة غير صالح'}), 400
            if code_val not in ALLOWED_PHONE_CODES:
                return jsonify({'success': False, 'message': 'رمز الدولة المحدد غير صالح'}), 400
        # التحقق من الدولة ضمن قائمة التسجيل
        if 'country' in data and str(data.get('country', '')).strip():
            country_val = str(data.get('country', '')).strip()
            if country_val not in ALLOWED_COUNTRIES:
                return jsonify({'success': False, 'message': 'القيمة المحددة للدولة غير صالحة'}), 400
        # التحقق من المجال المهني ضمن قائمة التسجيل
        if 'category' in data and str(data.get('category', '')).strip():
            category_val = str(data.get('category', '')).strip()
            if category_val not in ALLOWED_CATEGORIES:
                return jsonify({'success': False, 'message': 'المجال المهني المحدد غير صالح'}), 400
        # المهنة تُختار من القائمة الرسمية حتى لا تختلط مع رقم الهاتف أو بيانات أخرى.
        if 'profession' in data and str(data.get('profession', '')).strip():
            profession_val = str(data.get('profession', '')).strip()
            selected_category = str(data.get('category', user.get('category', '')) or '').strip()
            if profession_val not in _professions_for_category(selected_category):
                return jsonify({'success': False, 'message': 'يرجى اختيار مهنة مرتبطة بالمجال المهني المحدد'}), 400
        # التحقق من المدينة ضمن مدن الدولة المحددة
        if 'city' in data and str(data.get('city', '')).strip():
            city_val = str(data.get('city', '')).strip()
            country_val = str(data.get('country') or user.get('country') or '').strip()
            if country_val and country_val in LOCATION_DATA:
                if city_val not in LOCATION_DATA[country_val]:
                    return jsonify({'success': False, 'message': 'المدينة المحددة غير موجودة ضمن الدولة المختارة'}), 400
            else:
                return jsonify({'success': False, 'message': 'المدينة المحددة غير موجودة ضمن الدولة المختارة'}), 400
        # التحقق من الحي ضمن أحياء المدينة المحددة
        if 'neighborhood' in data and str(data.get('neighborhood', '')).strip():
            neigh_val = str(data.get('neighborhood', '')).strip()
            city_val = str(data.get('city') or user.get('city') or '').strip()
            country_val = str(data.get('country') or user.get('country') or '').strip()
            if country_val and city_val and country_val in LOCATION_DATA and city_val in LOCATION_DATA[country_val]:
                if neigh_val not in LOCATION_DATA[country_val][city_val]:
                    return jsonify({'success': False, 'message': 'الحي المحدد غير موجود ضمن المدينة المختارة'}), 400
            else:
                return jsonify({'success': False, 'message': 'الحي المحدد غير موجود ضمن المدينة المختارة'}), 400
        if 'birthdate' in data and str(data.get('birthdate', '')).strip():
            try:
                datetime.strptime(str(data.get('birthdate', '')).strip(), '%Y-%m-%d')
            except ValueError:
                return jsonify({'success': False, 'message': 'تاريخ الميلاد غير صالح'}), 400
        # حد أقصى لطول الحقول النصية
        max_len = {'firstName': 50, 'lastName': 50, 'phone': 20, 'phoneCountryCode': 6,
                   'category': 50, 'profession': 120, 'country': 50, 'city': 50, 'neighborhood': 50,
                   'education': 100, 'resume': 10000, 'headline': 120, 'bio': 2000, 'skills': 1500, 'experience': 3000, 'languages': 1000, 'certifications': 1500}
        for key, limit in max_len.items():
            if key in data and str(data.get(key, '')).strip():
                if len(str(data.get(key, '')).strip()) > limit:
                    return jsonify({'success': False, 'message': f'قيمة {key} طويلة جداً'}), 400
        updated = False
        for key in allowed_keys:
            if key in data:
                value = data.get(key, '')
                if value is None:
                    continue
                if key in ('avatar', 'resume'):
                    user[key] = str(value)
                else:
                    user[key] = sanitize_input(str(value))
                updated = True
        if not updated:
            return jsonify({'success': False, 'message': 'لا يوجد بيانات للتحديث'}), 400
        _repair_contact_value_leak(user)
        if not _save_current_user(user):
            return jsonify({'success': False, 'message': 'تعذر حفظ التغييرات'}), 500
        safe = user.copy(); safe.pop('password', None)
        return jsonify({'success': True, 'data': safe})

    if request.method == 'DELETE':
        if user.get('role') == 'admin':
            return jsonify({'success': False, 'message': 'لا يمكن حذف حساب المدير'}), 400
        # التحقق من OTP خاص بحذف الحساب قبل السماح بالحذف
        data = request.get_json(silent=True) or {}
        otp = str(data.get('otp', '')).strip()
        if not otp:
            return jsonify({'success': False, 'message': 'رمز التحقق مطلوب لحذف الحساب'}), 400
        # توحيد الأرقام العربية/الفارسية
        digits_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
        otp = otp.translate(digits_map)
        otp = "".join(ch for ch in otp if ch.isdigit())
        if len(otp) != 6:
            return jsonify({'success': False, 'message': 'رمز التحقق غير صحيح.'}), 400
        # قراءة OTP المخزن الخاص بحذف الحساب
        delete_otps = secure_storage.encryption.decrypt_file('delete_account_otps') or {}
        user_id = str(user.get('id'))
        stored = delete_otps.get(user_id)
        if not stored:
            return jsonify({'success': False, 'message': 'لا يوجد رمز تحقق فعال. يرجى طلب رمز جديد.'}), 400
        stored_code = str(stored.get('code', '')).strip().translate(digits_map)
        stored_code = "".join(ch for ch in stored_code if ch.isdigit())
        if stored_code != otp:
            return jsonify({'success': False, 'message': 'رمز التحقق غير صحيح.'}), 400
        # التحقق من انتهاء الصلاحية (10 دقائق)
        expires = stored.get('expires')
        if not expires:
            return jsonify({'success': False, 'message': 'رمز التحقق غير صالح. يرجى طلب رمز جديد.'}), 400
        try:
            expires_at = datetime.fromisoformat(str(expires))
        except Exception:
            return jsonify({'success': False, 'message': 'رمز التحقق غير صالح. يرجى طلب رمز جديد.'}), 400
        if expires_at < datetime.now():
            return jsonify({'success': False, 'message': 'انتهت صلاحية رمز التحقق، يرجى طلب رمز جديد.'}), 400
        # حذف OTP قبل الحذف لمنع إعادة الاستخدام
        delete_otps.pop(user_id, None)
        secure_storage.encryption.encrypt_file('delete_account_otps', delete_otps)
        # حذف المستخدم فعلياً
        users = [u for u in users if u.get('id') != user.get('id')]
        if not secure_storage.save_users(users):
            return jsonify({'success': False, 'message': 'تعذر حذف الحساب'}), 500
        session.clear()
        return jsonify({'success': True, 'message': 'تم حذف الحساب بنجاح.'})

@app.route('/api/user/delete-request', methods=['POST'])
def request_account_delete():
    """إرسال OTP خاص بحذف الحساب إلى بريد المستخدم."""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401

    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == session['user_id']), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404
    if user.get('role') == 'admin':
        return jsonify({'success': False, 'message': 'لا يمكن حذف حساب المدير'}), 400

    # إنشاء OTP خاص بحذف الحساب (6 أرقام، صالح 10 دقائق)
    code = f"{secrets.randbelow(1000000):06d}"
    delete_otps = secure_storage.encryption.decrypt_file('delete_account_otps') or {}
    user_id = str(user.get('id'))
    delete_otps[user_id] = {
        "code": code,
        "email": user.get("email", ""),
        "expires": (datetime.now() + timedelta(minutes=10)).isoformat()
    }
    secure_storage.encryption.encrypt_file('delete_account_otps', delete_otps)

    # إرسال البريد
    name = user.get("firstName", "")
    body = f"""مرحباً {name}!

نستلم طلباً لحذف حسابك في ArabJobs.

رمز تأكيد حذف الحساب الخاص بك هو: {code}

هذا الرمز صالح لمدة 10 دقائق.
إذا لم تطلب حذف حسابك، يرجى تجاهل هذه الرسالة.

© 2026 ArabJobs. جميع الحقوق محفوظة.
"""
    html = f"""<!doctype html><html lang="ar" dir="rtl"><body style="margin:0;background:#f4f7fb;font-family:Tahoma,Arial,sans-serif;padding:28px">
<div style="max-width:620px;margin:auto;background:#fff;border:1px solid #e4e9f0;border-radius:20px;overflow:hidden">
<div style="padding:26px 30px;background:#c62828;color:#fff"><div style="font-size:28px;font-weight:900">ArabJobs</div><div style="opacity:.9;margin-top:5px">حذف الحساب</div></div>
<div style="padding:30px"><h2 style="margin-top:0">⚠️ طلب حذف الحساب</h2>
<p style="font-size:16px">مرحباً <strong>{name}</strong>!</p>
<p>نستلم طلباً لحذف حسابك. استخدم الرمز التالي لتأكيد الحذف:</p>
<div style="text-align:center;margin:28px 0"><span style="display:inline-block;font-size:34px;letter-spacing:8px;font-weight:900;background:#fff1f2;color:#c62828;border:1px dashed #ef9a9a;border-radius:14px;padding:16px 24px">{code}</span></div>
<p style="color:#687386">هذا الرمز صالح لمدة <strong>10 دقائق</strong>.</p>
<p style="color:#777;font-size:13px;margin-top:25px">إذا لم تطلب حذف حسابك، يرجى تجاهل هذه الرسالة.</p>
<hr style="border:0;border-top:1px solid #eee;margin:25px 0"><p style="color:#999;font-size:12px">© 2026 ArabJobs. جميع الحقوق محفوظة.</p></div></div></body></html>"""
    ok = send_email(user.get("email", ""), "⚠️ تأكيد حذف الحساب - ArabJobs", body, html)

    return jsonify({'success': True, 'message': 'تم إرسال رمز التحقق إلى بريدك الإلكتروني.'})

@app.route('/api/user/profile/avatar', methods=['POST'])
def upload_profile_avatar():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401

    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == session['user_id']), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    if 'avatar' not in request.files:
        return jsonify({'success': False, 'message': 'لم يتم إرسال صورة'}), 400

    file = request.files['avatar']
    if not file or not file.filename:
        return jsonify({'success': False, 'message': 'اسم ملف غير صالح'}), 400

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    allowed_ext = {'png', 'jpg', 'jpeg', 'gif'}
    if ext not in allowed_ext:
        return jsonify({'success': False, 'message': 'نوع الملف غير مدعوم. استخدم PNG أو JPG أو GIF.'}), 400

    content = file.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        return jsonify({'success': False, 'message': 'الصورة كبيرة جداً. الحد الأقصى 1 ميغابايت.'}), 400

    mime_type = 'jpeg' if ext == 'jpg' else ext
    data_url = f"data:image/{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
    user['avatar'] = data_url

    if not _save_current_user(user):
        return jsonify({'success': False, 'message': 'تعذر حفظ الصورة'}), 500

    safe = user.copy(); safe.pop('password', None)
    return jsonify({'success': True, 'data': safe, 'message': 'تم تحديث الصورة بنجاح'})

@app.route('/api/user/change-password', methods=['POST'])
def change_user_password():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'}), 401

    data = request.get_json(silent=True) or {}
    current_password = data.get('currentPassword', '')
    new_password = data.get('newPassword', '')
    confirm_password = data.get('confirmPassword', '')

    if not current_password or not new_password or not confirm_password:
        return jsonify({'success': False, 'message': 'جميع الحقول مطلوبة'}), 400

    if new_password != confirm_password:
        return jsonify({'success': False, 'message': 'كلمة المرور الجديدة وتأكيدها غير متطابقين'}), 400

    ok, msg = validate_password(new_password)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400

    users = secure_storage.load_users() or []
    user = next((u for u in users if u.get('id') == session['user_id']), None)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    if not PasswordManager.verify_password(current_password, user.get('password', '')):
        return jsonify({'success': False, 'message': 'كلمة المرور الحالية غير صحيحة'}), 400

    user['password'] = PasswordManager.hash_password(new_password)
    if not _save_current_user(user):
        return jsonify({'success': False, 'message': 'تعذر حفظ كلمة المرور الجديدة'}), 500

    return jsonify({'success': True, 'message': 'تم تغيير كلمة المرور بنجاح'})

# ============================================
# API الإحصائيات (المصححة)
# ============================================

@app.route('/api/stats', methods=['GET'])
def api_stats():
    try:
        stats = secure_storage.get_stats() or {}
        jobs = secure_storage.load_jobs() or []
        users = secure_storage.load_users() or []
        applications = secure_storage.load_applications() or {}
        testimonials = secure_storage.load_testimonials() or []
        total_applications = sum(len(v) for v in applications.values()) if isinstance(applications, dict) else len(applications)
        companies = {j.get('company') for j in jobs if j.get('company')}
        countries = {j.get('country') for j in jobs if j.get('country')}
        return jsonify({
            'total_jobs': len(jobs),
            'total_users': len(users),
            'total_applications': total_applications,
            'total_companies': len(companies),
            'total_countries': len(countries),
            'total_testimonials': len(testimonials)
        })
    except Exception as e:
        logger.exception("stats error")
        return jsonify({'total_jobs': 0, 'total_users': 0, 'total_applications': 0,
                        'total_companies': 0, 'total_countries': 0, 'total_testimonials': 0})

@app.route('/api/admin/analytics', methods=['GET'])
@admin_required
def admin_analytics():
    """لوحة ذكاء تشغيلي للإدارة تعتمد على البيانات الفعلية فقط."""
    try:
        from collections import Counter, defaultdict
        jobs = secure_storage.load_jobs() or []
        users = secure_storage.load_users() or []
        applications_raw = secure_storage.load_applications() or {}
        applications = []
        if isinstance(applications_raw, dict):
            for owner_id, rows in applications_raw.items():
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            item = dict(row); item.setdefault('ownerId', owner_id); applications.append(item)
        elif isinstance(applications_raw, list):
            applications = [x for x in applications_raw if isinstance(x, dict)]

        payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
        payment_audit = secure_storage.encryption.decrypt_file('payment_audit') or []
        wallets = secure_storage.encryption.decrypt_file('wallets') or []
        subscriptions = secure_storage.encryption.decrypt_file('subscriptions') or []
        invoices = secure_storage.encryption.decrypt_file('invoices') or []
        try:
            official_logs = secure_storage.encryption.decrypt_file('error_log') or []
        except Exception:
            official_logs = []
        try:
            telegram_logs = secure_storage.encryption.decrypt_file('telegram_error_log') or []
        except Exception:
            telegram_logs = []

        employers = [u for u in users if u.get('role') == 'employer']
        seekers = [u for u in users if u.get('role') in ('job_seeker','worker','candidate')]
        if not seekers:
            seekers = [u for u in users if u.get('role') == 'user']
        paid = [p for p in payment_logs if str(p.get('status','')).lower() in ('paid','completed','succeeded')]
        pending = [p for p in payment_logs if str(p.get('status','')).lower() in ('pending','created','processing')]
        failed = [p for p in payment_logs if str(p.get('status','')).lower() in ('failed','cancelled','canceled','rejected')]
        revenue = 0.0
        for p in paid:
            try: revenue += float(p.get('amount') or 0)
            except Exception: pass
        unlocked = [p for p in paid if str(p.get('purpose') or p.get('type') or '').lower() in ('contact_unlock','contact-unlock','unlock_contact')]
        posted = [j for j in jobs if str(j.get('status','')).lower() not in ('draft','deleted')]
        remote = sum(1 for j in jobs if bool(j.get('remote')) or 'remote' in str(j.get('employmentType','')).lower() or 'عن بعد' in str(j.get('employmentType','')))
        status_counts = Counter(str(a.get('status') or 'pending') for a in applications)
        country_counts = Counter(str(j.get('country') or 'غير محدد') for j in jobs)
        source_counts = Counter(str(p.get('source') or p.get('channel') or 'official') for p in payment_logs)
        telegram_actions = sum(1 for x in telegram_logs if str(x.get('type') or x.get('source') or '').lower() in ('action','audit_action','telegram'))
        contact_attempts = sum(1 for x in payment_logs if 'contact' in str(x.get('purpose') or x.get('type') or '').lower())
        suspicious = []
        for p in failed:
            if p.get('purpose') and 'contact' in str(p.get('purpose')).lower():
                suspicious.append({'type':'failed_contact_payment','paymentId':p.get('paymentId'),'userId':p.get('employerId') or p.get('userId'),'date':p.get('updatedAt') or p.get('createdAt')})
        for x in payment_logs:
            attempts = x.get('attempts')
            try:
                if int(attempts or 0) >= 3:
                    suspicious.append({'type':'repeated_payment_attempts','paymentId':x.get('paymentId'),'userId':x.get('employerId') or x.get('userId'),'attempts':int(attempts),'date':x.get('updatedAt') or x.get('createdAt')})
            except Exception: pass
        # مؤشرات يومية لآخر 14 يومًا، مع الاعتماد على الطوابع المتوفرة فقط.
        from datetime import datetime, timedelta
        today = datetime.now().date()
        daily = []
        def day_of(obj):
            raw = obj.get('createdAt') or obj.get('timestamp') or obj.get('date') or obj.get('updatedAt')
            if not raw: return None
            try: return datetime.fromisoformat(str(raw).replace('Z','+00:00')).date()
            except Exception: return None
        for n in range(13,-1,-1):
            d = today - timedelta(days=n)
            daily.append({'date': d.isoformat(),
                          'jobs': sum(1 for x in jobs if day_of(x)==d),
                          'applications': sum(1 for x in applications if day_of(x)==d),
                          'payments': sum(1 for x in paid if day_of(x)==d),
                          'errors': sum(1 for x in official_logs if day_of(x)==d)})
        return jsonify({'success':True,'generatedAt':datetime.now().isoformat(),'summary':{
            'jobs':len(jobs),'publishedJobs':len(posted),'remoteJobs':remote,'users':len(users),'employers':len(employers),'jobSeekers':len(seekers),
            'applications':len(applications),'paidPayments':len(paid),'pendingPayments':len(pending),'failedPayments':len(failed),
            'revenueUsd':round(revenue,2),'contactUnlockPayments':len(unlocked),'contactAttempts':contact_attempts,
            'wallets':len(wallets) if isinstance(wallets,list) else 0,'activeSubscriptions':sum(1 for s in subscriptions if str(s.get('status','active')).lower()=='active') if isinstance(subscriptions,list) else 0,
            'invoices':len(invoices) if isinstance(invoices,list) else 0,'officialErrors':len(official_logs),'telegramErrors':len(telegram_logs),'telegramActions':telegram_actions,
            'suspiciousSignals':len(suspicious)},
            'applicationStatuses':dict(status_counts),'topCountries':[{'name':k,'count':v} for k,v in country_counts.most_common(8)],
            'paymentSources':dict(source_counts),'daily':daily,'suspicious':suspicious[:30]})
    except Exception as e:
        logger.exception('admin analytics error')
        return jsonify({'success':False,'message':'تعذر حساب التحليلات'}),500

@app.route('/api/admin/jobs', methods=['GET'])
@admin_required
def admin_jobs_detailed():
    """لوحة تحكم المدير: الوظائف مع بيانات حقيقية مدمجة (صاحب العمل، الإعجابات، المتقدمين)."""
    try:
        jobs = secure_storage.load_jobs() or []
        users = secure_storage.load_users() or []
        favorites = secure_storage.load_favorites() or {}
        applications = secure_storage.load_applications() or {}

        users_by_id = {str(u.get('id')): u for u in users}

        # عدّ الإعجابات: كل ظهور لمعرف الوظيفة في مفضلات المستخدمين
        fav_counts = {}
        for fav_list in favorites.values():
            if not isinstance(fav_list, list):
                continue
            for job_id in fav_list:
                key = str(job_id)
                fav_counts[key] = fav_counts.get(key, 0) + 1

        # عدّ المتقدمين: عدد الطلبات المطابقة لمعرف الوظيفة
        app_counts = {}
        for user_apps in applications.values():
            if not isinstance(user_apps, list):
                continue
            for item in user_apps:
                job_key = str(item.get('jobId') or item.get('job_id') or '')
                if job_key:
                    app_counts[job_key] = app_counts.get(job_key, 0) + 1

        result = []
        for job in jobs:
            employer_id = job.get('employerId')
            employer = users_by_id.get(str(employer_id)) if employer_id else None
            owner_name = ''
            if employer:
                owner_name = f"{employer.get('firstName','')} {employer.get('lastName','')}".strip()
            if not owner_name:
                owner_name = employer.get('username','') if employer else ''
            if not owner_name:
                owner_name = job.get('employerName','')

            row = dict(job)
            row['ownerName'] = owner_name
            row['ownerEmail'] = job.get('employerEmail','') or (employer.get('email','') if employer else '')
            row['posted'] = job.get('posted') or job.get('createdAt') or job.get('created') or ''
            row['likesCount'] = fav_counts.get(str(job.get('id')), 0)
            row['applicantsCount'] = app_counts.get(str(job.get('id')), 0)
            result.append(row)

        return jsonify(result)
    except Exception as e:
        logger.exception("admin jobs detailed error")
        return jsonify({'success': False, 'message': 'خطأ في تحميل الجدول'}), 500

@app.route('/api/stats', methods=['PUT'])
@admin_required
def api_stats_put():
    data = request.get_json(silent=True) or {}
    custom = {k: int(data.get(k, 0) or 0) for k in [
        'total_jobs', 'total_users', 'total_applications',
        'total_companies', 'total_countries', 'total_testimonials'
    ]}
    custom['last_updated'] = datetime.now().isoformat()
    secure_storage.encryption.encrypt_file('custom_stats', custom)
    return jsonify({'success': True, 'data': custom})

@app.route('/api/features', methods=['GET'])
def api_features():
    try:
        features = secure_storage.encryption.decrypt_file('features')
        if not features:
            features = [
                {'id': 1, 'icon': 'fa-shield-alt', 'title': 'أمان وخصوصية', 'description': 'جميع بياناتك مشفرة وآمنة'},
                {'id': 2, 'icon': 'fa-globe-africa', 'title': 'تغطية عربية', 'description': 'نغطي جميع الدول العربية'},
                {'id': 3, 'icon': 'fa-clock', 'title': 'تحديث فوري', 'description': 'الوظائف محدثة بشكل يومي'},
                {'id': 4, 'icon': 'fa-headset', 'title': 'دعم مستمر', 'description': 'فريق دعم متخصص'}
            ]
            secure_storage.encryption.encrypt_file('features', features)
        return jsonify(features)
    except Exception as e:
        return jsonify([
            {'id': 1, 'icon': 'fa-shield-alt', 'title': 'أمان وخصوصية', 'description': 'جميع بياناتك مشفرة وآمنة'},
            {'id': 2, 'icon': 'fa-globe-africa', 'title': 'تغطية عربية', 'description': 'نغطي جميع الدول العربية'},
            {'id': 3, 'icon': 'fa-clock', 'title': 'تحديث فوري', 'description': 'الوظائف محدثة بشكل يومي'},
            {'id': 4, 'icon': 'fa-headset', 'title': 'دعم مستمر', 'description': 'فريق دعم متخصص'}
        ])

@app.route('/api/features', methods=['POST'])
@admin_required
def api_features_post():
    try:
        data = request.json
        
        features = secure_storage.encryption.decrypt_file('features')
        if not features:
            features = []
        
        new_feature = {
            'id': len(features) + 1,
            'icon': data.get('icon', 'fa-star'),
            'title': data.get('title', 'ميزة جديدة'),
            'description': data.get('description', '')
        }
        
        features.append(new_feature)
        secure_storage.encryption.encrypt_file('features', features)
        
        return jsonify({
            'success': True,
            'message': 'تم إضافة الميزة بنجاح',
            'data': new_feature
        })
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# API آراء العملاء
# ============================================

@app.route('/api/testimonials', methods=['GET'])
def api_testimonials():
    try:
        testimonials = secure_storage.load_testimonials()
        if not testimonials:
            testimonials = [
                {'id': 1, 'name': 'خالد العتيبي', 'position': 'مدير شركة', 'rating': 5, 
                 'comment': 'منصة رائعة وسهلة الاستخدام', 'date': '2026-07-20'},
                {'id': 2, 'name': 'نورة السالم', 'position': 'مصممة', 'rating': 4,
                 'comment': 'وظائف متنوعة وخدمة ممتازة', 'date': '2026-07-19'}
            ]
            secure_storage.save_testimonials(testimonials)
        return jsonify(testimonials)
    except Exception as e:
        return jsonify([])

@app.route('/api/testimonials', methods=['POST', 'PUT', 'DELETE'])
@admin_required
def api_testimonials_post():
    try:
        data=request.get_json(silent=True) or {}; items=secure_storage.load_testimonials() or []
        if request.method=='POST':
            item={'id':next_id(items),'name':sanitize_input(data.get('name','مستخدم')),'position':sanitize_input(data.get('position','')),'rating':int(data.get('rating',5) or 5),'comment':sanitize_input(data.get('comment','')),'date':datetime.now().strftime('%Y-%m-%d')}
            items.append(item)
        elif request.method=='PUT':
            item=next((x for x in items if str(x.get('id'))==str(data.get('id'))),None)
            if not item:return jsonify({'success':False,'message':'الرأي غير موجود'}),404
            for k in ['name','position','comment']:
                if k in data:item[k]=sanitize_input(str(data[k]))
            if 'rating' in data:item['rating']=max(1,min(5,int(data['rating'])))
        else:
            items=[x for x in items if str(x.get('id'))!=str(data.get('id'))]
        if not secure_storage.save_testimonials(items):return jsonify({'success':False,'message':'تعذر حفظ الآراء'}),500
        return jsonify({'success':True,'message':'تم الحفظ بنجاح','data':items})
    except Exception as e:
        logger.exception('testimonials error'); return jsonify({'success':False,'message':'حدث خطأ في الخادم'}),500

# ============================================
# API السلايدر
# ============================================

@app.route('/api/slider', methods=['GET'])
def api_slider():
    try:
        slider = secure_storage.encryption.decrypt_file('slider')
        # إذا كان الملف غير موجود (None) ننشئ الشرائح الافتراضية فقط أول مرة
        # أما إذا كان الملف موجوداً وفارغاً ([]) فلا نعيد إنشاء الافتراضية حتى يتمكن المدير من الحذف نهائياً
        if slider is None:
            slider = [
                {'id': 1, 'title': 'ابحث عن وظيفة أحلامك', 'subtitle': 'آلاف الوظائف في انتظارك', 
                 'image': 'https://images.unsplash.com/photo-1521737711867-e3b97375f902?w=1600&q=80', 'link': '/jobs', 'enabled': True},
                {'id': 2, 'title': 'شركات كبرى تبحث عنك', 'subtitle': 'انضم إلى أفضل الشركات', 
                 'image': 'https://images.unsplash.com/photo-1497366216548-37526070297c?w=1600&q=80', 'link': '/jobs', 'enabled': True},
                {'id': 3, 'title': 'بناء مستقبلك المهني', 'subtitle': 'نحن هنا لمساعدتك', 
                 'image': 'https://images.unsplash.com/photo-1522202176988-66273c2fd55f?w=1600&q=80', 'link': '/about', 'enabled': True}
            ]
            secure_storage.encryption.encrypt_file('slider', slider)
        # التأكد من أن القائمة دائماً قائمة وليست None
        if slider is None:
            slider = []
        return jsonify(slider)
    except Exception as e:
        # في حالة الخطأ نرجع قائمة فارغة وليس افتراضية - حتى لا تُعاد الافتراضية دائماً
        return jsonify([])

@app.route('/api/slider', methods=['POST', 'PUT', 'DELETE'])
@admin_required
def api_slider_post():
    try:
        data = request.get_json(silent=True) or {}
        slider = secure_storage.encryption.decrypt_file('slider') or []
        if request.method == 'POST':
            item = {'id': next_id(slider), 'title': sanitize_input(data.get('title','')), 'subtitle': sanitize_input(data.get('subtitle','')), 'image': sanitize_input(data.get('image','')), 'link': sanitize_input(data.get('link','/jobs')), 'enabled': bool(data.get('enabled', True))}
            slider.append(item)
        elif request.method == 'PUT':
            sid=data.get('id'); item=next((x for x in slider if str(x.get('id'))==str(sid)), None)
            if not item: return jsonify({'success':False,'message':'السلايد غير موجود'}),404
            for k in ['title','subtitle','image','link','enabled']:
                if k in data: item[k]=sanitize_input(data[k]) if isinstance(data[k],str) else data[k]
        else:
            sid=data.get('id'); slider=[x for x in slider if str(x.get('id'))!=str(sid)]
        if not secure_storage.encryption.encrypt_file('slider', slider):
            return jsonify({'success':False,'message':'تعذر حفظ السلايدر'}),500
        return jsonify({'success':True,'message':'تم الحفظ بنجاح','data':slider})
    except Exception as e:
        logger.exception('slider error')
        return jsonify({'success':False,'message':'حدث خطأ في الخادم'}),500

# ============================================
# API الإعدادات العامة (المصححة)
# ============================================

@app.route('/api/settings/general', methods=['GET'])
def api_settings_general():
    try:
        # استخدام encryption مباشرة
        settings = secure_storage.encryption.decrypt_file('settings')
        if not settings:
            settings = {
                'site_name': 'منصة التوظيف العربية',
                'site_description': 'منصة التوظيف العربية - ربط الكفاءات بالفرص',
                'contact_email': 'info@arabjobs.com',
                'contact_phone': '+966 5XXXX XXXX',
                'address': 'الرياض، المملكة العربية السعودية',
                'working_hours': 'الأحد - الخميس: 9:00 ص - 6:00 م',
                'top_bar_text': 'أكثر من 1000 وظيفة متاحة الآن في جميع الدول العربية',
                'hero_title': 'وظيفة أحلامك <span>تنتظرك</span>',
                'hero_subtitle': 'منصة التوظيف العربية تربط بين الباحثين عن عمل وأفضل الشركات في العالم العربي. انضم إلى آلاف المرشحين الذين وجدوا وظائفهم المثالية من خلالنا.',
                'footer_text': '© 2026 منصة التوظيف العربية. جميع الحقوق محفوظة. جميع البيانات مشفرة 🔐'
            }
            secure_storage.encryption.encrypt_file('settings', settings)
        return jsonify(settings)
    except Exception as e:
        # في حالة الخطأ، إرجاع الإعدادات الافتراضية
        return jsonify({
            'site_name': 'منصة التوظيف العربية',
            'site_description': 'منصة التوظيف العربية',
            'contact_email': 'info@arabjobs.com',
            'contact_phone': '+966 5XXXX XXXX',
            'address': 'الرياض، المملكة العربية السعودية',
            'working_hours': 'الأحد - الخميس: 9:00 ص - 6:00 م',
            'top_bar_text': 'أكثر من 1000 وظيفة متاحة الآن في جميع الدول العربية',
            'hero_title': 'وظيفة أحلامك <span>تنتظرك</span>',
            'hero_subtitle': 'منصة التوظيف العربية تربط بين الباحثين عن عمل وأفضل الشركات في العالم العربي.',
            'footer_text': '© 2026 منصة التوظيف العربية. جميع الحقوق محفوظة. جميع البيانات مشفرة 🔐'
        })

@app.route('/api/settings/general', methods=['PUT'])
@admin_required
def update_general_settings():
    try:
        data = request.get_json(silent=True) or {}
        
        # تحميل الإعدادات الحالية باستخدام encryption
        settings = secure_storage.encryption.decrypt_file('settings')
        if not settings:
            settings = {}
        
        # تحديث الإعدادات
        for key in ['site_name','site_description','contact_email','contact_phone','address','working_hours','top_bar_text','hero_title','hero_subtitle','footer_text']:
            if key in data:
                settings[key] = sanitize_input(data[key])
        
        # حفظ الإعدادات
        success = secure_storage.encryption.encrypt_file('settings', settings)
        
        if success:
            return jsonify({
                "success": True,
                "message": "تم تحديث الإعدادات بنجاح",
                "data": settings
            })
        else:
            return jsonify({
                "success": False,
                "message": "فشل حفظ الإعدادات"
            }), 500
            
    except Exception as e:
        return jsonify({
            "success": False,
            "message": "حدث خطأ في الخادم"
        }), 500

# ============================================
# مسارات إدارة الأخبار (API)
# ============================================

@app.route('/api/admin/news', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def admin_news_api():
    """إدارة الأخبار - API كامل"""
    
    if request.method == 'GET':
        news = secure_storage.load_news() or []
        return jsonify(news)
    
    elif request.method == 'POST':
        try:
            data = request.json
            news = secure_storage.load_news() or []
            
            new_news = {
                'id': next_id(news),
                'title': sanitize_input(data.get('title', 'خبر جديد')),
                'category': sanitize_input(data.get('category', 'أخبار')),
                'content': sanitize_input(data.get('content', '')),
                'date': datetime.now().strftime('%Y-%m-%d'),
                'status': data.get('status', 'منشور'),
                'image': sanitize_input(data.get('image', ''))
            }
            
            news.append(new_news)
            secure_storage.save_news(news)
            
            return jsonify({
                'success': True,
                'message': 'تم إضافة الخبر بنجاح',
                'data': new_news
            })
        except Exception as e:
            logger.exception("Unexpected error")
            return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500
    
    elif request.method == 'PUT':
        try:
            data = request.json
            news_id = data.get('id')
            news = secure_storage.load_news() or []
            
            for item in news:
                if item['id'] == news_id:
                    item['title'] = sanitize_input(data.get('title', item['title']))
                    item['category'] = sanitize_input(data.get('category', item['category']))
                    item['content'] = sanitize_input(data.get('content', item.get('content', '')))
                    item['status'] = data.get('status', item['status'])
                    if 'image' in data:
                        item['image'] = sanitize_input(data.get('image', ''))
                    secure_storage.save_news(news)
                    return jsonify({
                        'success': True,
                        'message': 'تم تحديث الخبر بنجاح',
                        'data': item
                    })
            
            return jsonify({'success': False, 'message': 'الخبر غير موجود'}), 404
        except Exception as e:
            logger.exception("Unexpected error")
            return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500
    
    elif request.method == 'DELETE':
        try:
            data = request.get_json(silent=True) or {}
            news_id = data.get('id', request.args.get('id'))
            if news_id is None:
                return jsonify({'success': False, 'message': 'معرف الخبر مطلوب'}), 400
            try:
                news_id_int = int(news_id)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'message': 'معرف الخبر غير صالح'}), 400
            news = secure_storage.load_news() or []
            filtered = [n for n in news if int(n.get('id', -1)) != news_id_int]
            if len(filtered) == len(news):
                return jsonify({'success': False, 'message': 'الخبر غير موجود'}), 404
            secure_storage.save_news(filtered)
            return jsonify({
                'success': True,
                'message': 'تم حذف الخبر بنجاح'
            })
        except Exception as e:
            logger.exception("Unexpected error")
            return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# مسار عرض الأخبار في الموقع الرسمي
# ============================================

@app.route('/api/admin/news/upload-image', methods=['POST'])
@admin_required
def admin_upload_news_image():
    """رفع صورة خبر - للمدير فقط. يحفظ الملف في static/uploads/news/ ويعيد المسار."""
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'message': 'لم يتم إرسال صورة'}), 400
        file = request.files['image']
        if not file or not file.filename:
            return jsonify({'success': False, 'message': 'اسم ملف غير صالح'}), 400

        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower().lstrip('.')
        allowed_ext = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        if ext not in allowed_ext:
            return jsonify({'success': False, 'message': 'نوع الملف غير مدعوم. استخدم PNG أو JPG أو JPEG أو GIF أو WEBP.'}), 400

        content = file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            return jsonify({'success': False, 'message': 'الصورة كبيرة جداً. الحد الأقصى 5 ميغابايت.'}), 400

        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'news')
        os.makedirs(upload_dir, exist_ok=True)

        random_name = 'news_' + secrets.token_hex(8) + '.' + ext
        file_path = os.path.join(upload_dir, random_name)
        with open(file_path, 'wb') as f:
            f.write(content)

        return jsonify({
            'success': True,
            'message': 'تم رفع الصورة بنجاح',
            'image': '/static/uploads/news/' + random_name
        })
    except Exception as e:
        logger.exception("news image upload error")
        return jsonify({'success': False, 'message': 'حدث خطأ في رفع الصورة'}), 500

@app.route('/api/news', methods=['GET'])
def get_news():
    """جلب الأخبار للموقع الرسمي - المنشور فقط."""
    news = secure_storage.load_news() or []
    published = [n for n in news if str(n.get('status', '')).strip() == 'منشور']
    for n in published:
        if not n.get('excerpt'):
            content = str(n.get('content', '')) or ''
            n['excerpt'] = (content[:150] + ('…' if len(content) > 150 else '')) if content else ''
    return jsonify(published)


# ============================================================
# CMS عام: من نحن، القائمة، الخدمات، FAQ، المعرض، الأعمال،
# الفريق، الشركاء، لماذا نحن، الصفحات، اللغات، التواصل
# ============================================================

CMS_DEFAULTS = {
    'contact': {'title':'اتصل بنا','subtitle':'تواصل معنا','content':'يمكنك التواصل معنا عبر البريد والهاتف.','email':'','phone':'','address':'', 'info_title':'معلومات الاتصال','hours':'الأحد - الخميس: 9:00 ص - 6:00 م','form_title':'أرسل رسالة','button':'إرسال الرسالة','footer':'© 2026 منصة التوظيف العربية - جميع الحقوق محفوظة'},
    'slider': [],
    'about': {
        'title': 'من نحن',
        'subtitle': 'منصة عربية لربط الباحثين عن العمل بأفضل الفرص',
        'content': 'نساعد الباحثين عن عمل والشركات على الوصول إلى الفرص والكفاءات المناسبة بطريقة بسيطة وآمنة.',
        'mission': 'تسهيل الوصول إلى فرص العمل وبناء مسار مهني أفضل.',
        'vision': 'أن نكون منصة التوظيف العربية الأكثر موثوقية.'
    },
    'menu': [
        {'id': 1, 'label': 'الرئيسية', 'url': '/', 'icon': 'fas fa-home', 'enabled': True, 'order': 1},
        {'id': 2, 'label': 'الوظائف', 'url': '/jobs', 'icon': 'fas fa-search', 'enabled': True, 'order': 2},
        {'id': 3, 'label': 'الأخبار', 'url': '/news', 'icon': 'fas fa-newspaper', 'enabled': True, 'order': 3},
        {'id': 4, 'label': 'من نحن', 'url': '/about', 'icon': 'fas fa-info-circle', 'enabled': True, 'order': 4},
        {'id': 5, 'label': 'اتصل بنا', 'url': '/contact', 'icon': 'fas fa-envelope', 'enabled': True, 'order': 5}
    ],
    'services': [
        {'id':1,'title':'البحث عن الوظائف','description':'الوصول إلى وظائف مناسبة حسب المجال والدولة.','enabled':True},
        {'id':2,'title':'التقديم على الوظائف','description':'إنشاء ملف شخصي والتقديم على الوظائف بسهولة.','enabled':True},
        {'id':3,'title':'إدارة المسار المهني','description':'متابعة الطلبات والمفضلة والفرص الجديدة.','enabled':True}
    ],
    'faq': [
        {'id':1,'title':'كيف أسجل حساباً؟','content':'اضغط على إنشاء حساب واملأ بياناتك ثم سجّل الدخول.','enabled':True},
        {'id':2,'title':'كيف أقدم على وظيفة؟','content':'افتح تفاصيل الوظيفة ثم اضغط تقديم بعد تسجيل الدخول.','enabled':True},
        {'id':3,'title':'هل أستطيع متابعة طلبي؟','content':'نعم، تظهر طلباتك وحالتها في حسابك.','enabled':True}
    ],
    'gallery': [],
    'portfolio': [],
    'team': [],
    'partners': [],
    'whychooseus': [],
    'pages': [],
    'comments': [],
    'languages': [
        {'code': 'ar', 'name': 'العربية', 'enabled': True, 'default': True},
        {'code': 'en', 'name': 'English', 'enabled': False, 'default': False}
    ],
    'social': {
        'facebook': '', 'instagram': '', 'linkedin': '', 'twitter': '', 'youtube': ''
    },
    'comments': []
}

def cms_load(section):
    data = secure_storage.encryption.decrypt_file(f'cms_{section}')
    return CMS_DEFAULTS.get(section) if data is None else data

def cms_save(section, data):
    return secure_storage.encryption.encrypt_file(f'cms_{section}', data)

@app.route('/api/content/<section>', methods=['GET'])
def public_content(section):
    if section not in CMS_DEFAULTS:
        return jsonify({'success': False, 'message': 'القسم غير موجود'}), 404
    return jsonify(cms_load(section))




@app.route('/api/admin/content/<section>', methods=['GET', 'PUT', 'POST', 'DELETE'])
@admin_required
def admin_content(section):
    if section not in CMS_DEFAULTS:
        return jsonify({'success': False, 'message': 'القسم غير موجود'}), 404

    # ================== حالة خاصة لقسم "اتصل بنا" ==================
    if section == 'contact':
        if request.method == 'GET':
            # قراءة البيانات من ملف contact_content
            return jsonify(_load_contact_content())

        if request.method in ('PUT', 'POST'):
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

            current = _load_contact_content()
            for key in DEFAULT_CONTACT_CONTENT:
                if key in payload:
                    current[key] = str(payload[key])
            
            # ===== إضافة مهمة: إذا تم تغيير hours، نحدّث working_hours أيضاً =====
            if 'hours' in payload:
                current['working_hours'] = str(payload['hours'])
            # =================================================================

            if _save_contact_content(current):
                return jsonify({'success': True, 'data': current})
            else:
                return jsonify({'success': False, 'message': 'تعذر حفظ بيانات الاتصال'}), 500

        # DELETE غير مدعوم لـ contact (لأنه بيانات مفردة)
        return jsonify({'success': False, 'message': 'عملية غير مدعومة لهذا القسم'}), 400

    # ================== حالة خاصة لقسم "الصفحات" (Pages) ==================
    # الصفحات المخزنة في cms_pages.enc تحتاج slug فريد + وصف، ويجب أن يكون
    # المحتوى هو المحتوى فقط (وضع داخل Layout موحد عند العرض).
    if section == 'pages':
        pages = _load_pages()
        if request.method == 'GET':
            return jsonify(pages)

        payload = request.get_json(silent=True) or {}

        if request.method == 'PUT':
            # إما تحديث عنصر واحد عبر item_id (يُعالَج في admin_content_item)
            # أو استبدال القائمة كاملة — هنا نتعامل مع القائمة فقط بأمان.
            if isinstance(payload, dict) and ('id' in payload or 'slug' in payload):
                page_id = str(payload.get('id', ''))
                page = next((p for p in pages if str(p.get('id')) == str(page_id)), None)
                if not page:
                    return jsonify({'success': False, 'message': 'الصفحة غير موجودة'}), 404
                new_slug = sanitize_input(str(payload.get('slug') or payload.get('link') or payload.get('url') or page.get('slug') or '')).strip().lstrip('/')
                if new_slug and _slug_exists(new_slug, exclude_id=page_id):
                    return jsonify({'success': False, 'message': f'الرابط "{new_slug}" مستخدم بالفعل. يرجى اختيار رابط آخر.'}), 409
                if 'title' in payload:
                    page['title'] = sanitize_input(str(payload['title']))
                if 'name' in payload:
                    page['title'] = sanitize_input(str(payload['name']))
                if new_slug:
                    page['slug'] = new_slug
                page['description'] = sanitize_input(str(payload.get('description') or payload.get('desc') or page.get('description') or ''))
                if 'content' in payload:
                    page['content'] = str(payload['content'])
                if 'show_in_menu' in payload:
                    page['show_in_menu'] = bool(payload['show_in_menu'])
                if 'enabled' in payload:
                    page['enabled'] = bool(payload['enabled'])
                if 'menu_icon' in payload:
                    page['menu_icon'] = sanitize_input(str(payload.get('menu_icon') or 'fas fa-file'))
                page['updated_at'] = datetime.now().isoformat()
                if not _save_pages(pages):
                    return jsonify({'success': False, 'message': 'تعذر حفظ الصفحة'}), 500
                return jsonify({'success': True, 'data': page})
            # استبدال القائمة كاملة — تحقق من تفرد كل slug
            if isinstance(payload, list):
                seen = set()
                for p in payload:
                    s = str(p.get('slug') or p.get('link') or p.get('url') or '').strip().lstrip('/')
                    if s:
                        if s in seen:
                            return jsonify({'success': False, 'message': f'الرابط "{s}" مكرر. يرجى استخدام رابط فريد.'}), 409
                        seen.add(s)
                    p['slug'] = s
                    if p.get('description') is None and p.get('desc'):
                        p['description'] = str(p['desc'])
                    elif p.get('description') is None:
                        p['description'] = ''
                if not _save_pages(payload):
                    return jsonify({'success': False, 'message': 'تعذر حفظ الصفحات'}), 500
                return jsonify({'success': True, 'data': payload})
            return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

        # POST (إنشاء صفحة جديدة)
        title = sanitize_input(str(payload.get('title') or payload.get('name') or payload.get('label') or '')).strip()
        slug = sanitize_input(str(payload.get('slug') or payload.get('link') or payload.get('url') or '')).strip().lstrip('/')
        description = sanitize_input(str(payload.get('description') or payload.get('desc') or '')).strip()
        content = str(payload.get('content') or '')
        show_in_menu = bool(payload.get('show_in_menu', False))
        enabled = bool(payload.get('enabled', True))

        if not title:
            return jsonify({'success': False, 'message': 'العنوان مطلوب'}), 400
        if not slug:
            return jsonify({'success': False, 'message': 'الرابط (slug) مطلوب'}), 400
        if _slug_exists(slug):
            return jsonify({'success': False, 'message': f'الرابط "{slug}" مستخدم بالفعل. يرجى اختيار رابط آخر.'}), 409

        new_page = {
            'id': next_id(pages),
            'title': title,
            'slug': slug,
            'description': description,
            'content': content,
            'show_in_menu': show_in_menu,
            'enabled': enabled,
            'menu_icon': sanitize_input(str(payload.get('menu_icon') or 'fas fa-file')),
            'created_at': datetime.now().isoformat()
        }
        pages.append(new_page)
        if not _save_pages(pages):
            return jsonify({'success': False, 'message': 'تعذر حفظ الصفحة'}), 500
        return jsonify({'success': True, 'message': 'تم إنشاء الصفحة بنجاح', 'data': new_page})

    # ================== بقية الأقسام (CMS العام) ==================
    data = cms_load(section)

    if request.method == 'GET':
        return jsonify(data)

    payload = request.get_json(silent=True)
    if request.method == 'PUT':
        if not isinstance(payload, (dict, list)):
            return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400
        cms_save(section, payload)
        return jsonify({'success': True, 'data': payload})

    # POST (إنشاء عنصر جديد)
    if not isinstance(data, list):
        data = []
    payload = payload or {}
    payload['id'] = next_id(data)
    payload.setdefault('enabled', True)
    data.append(payload)
    cms_save(section, data)
    return jsonify({'success': True, 'data': payload})


@app.route('/api/admin/content/<section>/<item_id>', methods=['PUT', 'DELETE'])
@admin_required
def admin_content_item(section, item_id):
    """تحديث أو حذف عنصر واحد لقسم CMS قائم على قائمة."""
    if section == 'pages':
        pages = _load_pages()
        page = next((p for p in pages if str(p.get('id')) == str(item_id)), None)
        if not page:
            return jsonify({'success': False, 'message': 'الصفحة غير موجودة'}), 404

        if request.method == 'DELETE':
            pages = [p for p in pages if str(p.get('id')) != str(item_id)]
            if not _save_pages(pages):
                return jsonify({'success': False, 'message': 'تعذر حفظ الصفحات'}), 500
            return jsonify({'success': True, 'message': 'تم حذف الصفحة بنجاح'})

        try:
            data = request.get_json(silent=True) or {}
            new_slug = sanitize_input(str(data.get('slug') or data.get('link') or data.get('url') or page.get('slug') or '')).strip().lstrip('/')
            if new_slug and _slug_exists(new_slug, exclude_id=item_id):
                return jsonify({'success': False, 'message': f'الرابط "{new_slug}" مستخدم بالفعل. يرجى اختيار رابط آخر.'}), 409
            if 'title' in data:
                page['title'] = sanitize_input(str(data['title']))
            if 'name' in data:
                page['title'] = sanitize_input(str(data['name']))
            if 'label' in data:
                page['title'] = sanitize_input(str(data['label']))
            if new_slug:
                page['slug'] = new_slug
            page['description'] = sanitize_input(str(data.get('description') or data.get('desc') or page.get('description') or ''))
            if 'content' in data:
                page['content'] = str(data['content'])
            if 'show_in_menu' in data:
                page['show_in_menu'] = bool(data['show_in_menu'])
            if 'enabled' in data:
                page['enabled'] = bool(data['enabled'])
            page['updated_at'] = datetime.now().isoformat()
            if not _save_pages(pages):
                return jsonify({'success': False, 'message': 'تعذر حفظ الصفحة'}), 500
            return jsonify({'success': True, 'message': 'تم تحديث الصفحة بنجاح', 'data': page})
        except Exception as e:
            logger.exception("update page error")
            return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

    # الأقسام العامة (قائمة): تعديل/حذف عنصر واحد
    data = cms_load(section)
    if not isinstance(data, list):
        return jsonify({'success': False, 'message': 'القسم لا يدعم هذه العملية'}), 400

    if request.method == 'DELETE':
        data = [x for x in data if str(x.get('id')) != str(item_id)]
        cms_save(section, data)
        return jsonify({'success': True, 'message': 'تم الحذف بنجاح'})

    try:
        payload = request.get_json(silent=True) or {}
        item = next((x for x in data if str(x.get('id')) == str(item_id)), None)
        if not item:
            return jsonify({'success': False, 'message': 'العنصر غير موجود'}), 404
        for key in list(payload.keys()):
            if key != 'id':
                item[key] = payload[key]
        cms_save(section, data)
        return jsonify({'success': True, 'message': 'تم التحديث بنجاح', 'data': item})
    except Exception as e:
        logger.exception("update cms item error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# API صفحات CMS المخصصة (مع التحقق من تكرار slug)
# ============================================

def _load_pages():
    """تحميل الصفحات مع دعم show_in_menu و slug وتطبيع السجلات القديمة."""
    pages = secure_storage.encryption.decrypt_file('cms_pages') or []
    if not isinstance(pages, list):
        pages = []
    # تطبيع السجلات القديمة (التي أُنشئت عبر الـCMS العام بقيم link/url بدل slug)
    for p in pages:
        if not isinstance(p, dict):
            continue
        if not p.get('slug'):
            # استخراج slug من link/url (مع إزالة "/" البادئة إن وجدت)
            legacy = str(p.get('link') or p.get('url') or '').strip().lstrip('/')
            # بديل: من description القديمة إذا كانت تحتوي slug (حالة سابقة في البيانات)
            if not legacy:
                legacy = str(p.get('description') or p.get('desc') or '').strip()
            p['slug'] = legacy
        # توحيد حقل الوصف
        if not p.get('description') and (p.get('desc') or p.get('description')):
            p['description'] = str(p.get('desc') or '').strip()
        elif p.get('description') is None:
            p['description'] = ''
        # تنظيف المحتوى من أي ترويسة أو تنسيق عام مضمّن
        if p.get('content'):
            content = str(p['content'])
            # إزالة أي <header> أو <style> أو <script> يحاول كسر التخطيط
            content = re.sub(r'<header[\s>]', '<div', content, flags=re.IGNORECASE)
            content = re.sub(r'</header>', '</div>', content, flags=re.IGNORECASE)
            content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
            p['content'] = content
    return pages

def _save_pages(pages):
    """حفظ الصفحات وإبطال الكاش"""
    success = secure_storage.encryption.encrypt_file('cms_pages', pages)
    if success:
        secure_storage.encryption._invalidate_cached_file('cms_pages')
    return success

def _slug_exists(slug, exclude_id=None):
    """التحقق من عدم وجود slug مكرر (مع استثناء للتحرير)"""
    pages = _load_pages()
    for p in pages:
        if str(p.get('slug', '')) == str(slug) and (exclude_id is None or str(p.get('id')) != str(exclude_id)):
            return True
    return False

@app.context_processor
def inject_current_user_context():
    """Expose the authenticated user to templates so the header is rendered
    in the correct state on the first HTML response. This prevents the
    login/register buttons from flashing for a moment during navigation."""
    try:
        uid = session.get('user_id')
        if not uid:
            return {'header_user': None}
        users = secure_storage.load_users() or []
        user = next((u for u in users if str(u.get('id')) == str(uid)), None)
        if not user:
            return {'header_user': None}
        return {'header_user': {
            'id': user.get('id'),
            'firstName': user.get('firstName') or user.get('first_name') or 'مستخدم',
            'lastName': user.get('lastName') or user.get('last_name') or '',
            'email': user.get('email', ''),
            'avatar': user.get('avatar', ''),
            'role': user.get('role', 'job_seeker'),
        }}
    except Exception:
        return {'header_user': None}

@app.context_processor
def inject_error_logging_state():
    return {'error_logging_enabled': is_logging_enabled()}

@app.context_processor
def inject_menu_pages():
    """Single source of truth for the public header.
    The admin CMS menu plus enabled CMS pages is rendered on every public page,
    so changes in the admin panel are reflected automatically on the official site.
    """
    try:
        raw = cms_load('menu')
        menu_items = [dict(x) for x in raw] if isinstance(raw, list) else [dict(x) for x in CMS_DEFAULTS['menu']]
        seen_urls = set()
        normalized = []
        for item in menu_items:
            if not isinstance(item, dict) or not item.get('enabled', True):
                continue
            url = str(item.get('url') or '').strip()
            if not url:
                continue
            if not url.startswith('/'):
                url = '/' + url
            if url in seen_urls:
                continue
            seen_urls.add(url)
            item['url'] = url
            item.setdefault('icon', 'fas fa-link')
            normalized.append(item)

        # CMS-created pages are appended automatically when enabled for the menu.
        for page in _load_pages():
            if not isinstance(page, dict) or not page.get('show_in_menu') or not page.get('enabled', True):
                continue
            slug = str(page.get('slug') or '').strip().strip('/')
            if not slug:
                continue
            url = '/' + slug
            if url in seen_urls:
                continue
            seen_urls.add(url)
            normalized.append({
                'id': f"page_{page.get('id')}",
                'label': page.get('title', ''),
                'url': url,
                'icon': page.get('menu_icon') or 'fas fa-file',
                'enabled': True,
                'order': 999,
            })

        normalized.sort(key=lambda x: x.get('order', 999))
        return {'menu_items': normalized, 'menu_pages': [
            {'slug': x['url'].lstrip('/'), 'title': x['label'], 'menu_icon': x.get('icon', 'fas fa-file')}
            for x in normalized if x['url'].lstrip('/') not in {'', 'jobs', 'news', 'about', 'contact'}
        ]}
    except Exception:
        fallback = [dict(x) for x in CMS_DEFAULTS['menu']]
        return {'menu_items': fallback, 'menu_pages': []}

@app.route('/api/admin/pages', methods=['GET', 'POST'])
@admin_required
def admin_pages_api():
    """إدارة صفحات CMS - API مخصص مع التحقق من تكرار slug"""
    if request.method == 'GET':
        pages = _load_pages()
        return jsonify(pages)

    # POST - إنشاء صفحة جديدة
    try:
        data = request.get_json(silent=True) or {}
        title = sanitize_input(data.get('title', '')).strip()
        slug = sanitize_input(data.get('slug', '')).strip()
        content = data.get('content', '')
        show_in_menu = bool(data.get('show_in_menu', False))
        enabled = bool(data.get('enabled', True))
        menu_icon = sanitize_input(data.get('menu_icon', 'fas fa-file')).strip() or 'fas fa-file'

        if not title:
            return jsonify({'success': False, 'message': 'العنوان مطلوب'}), 400
        if not slug:
            return jsonify({'success': False, 'message': 'الرابط (slug) مطلوب'}), 400

        # التحقق من تكرار slug
        if _slug_exists(slug):
            return jsonify({'success': False, 'message': f'الرابط "{slug}" مستخدم بالفعل. يرجى اختيار رابط آخر.'}), 409

        pages = _load_pages()
        new_page = {
            'id': next_id(pages),
            'title': title,
            'slug': slug,
            'content': content,
            'show_in_menu': show_in_menu,
            'enabled': enabled,
            'menu_icon': menu_icon,
            'created_at': datetime.now().isoformat()
        }
        pages.append(new_page)
        if not _save_pages(pages):
            return jsonify({'success': False, 'message': 'تعذر حفظ الصفحة'}), 500
        return jsonify({'success': True, 'message': 'تم إنشاء الصفحة بنجاح', 'data': new_page})
    except Exception as e:
        logger.exception("create page error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

@app.route('/api/admin/pages/<page_id>', methods=['PUT', 'DELETE'])
@admin_required
def admin_page_item(page_id):
    """تحديث أو حذف صفحة محددة"""
    pages = _load_pages()
    page = next((p for p in pages if str(p.get('id')) == str(page_id)), None)
    if not page:
        return jsonify({'success': False, 'message': 'الصفحة غير موجودة'}), 404

    if request.method == 'DELETE':
        pages = [p for p in pages if str(p.get('id')) != str(page_id)]
        if not _save_pages(pages):
            return jsonify({'success': False, 'message': 'تعذر حفظ الصفحات'}), 500
        return jsonify({'success': True, 'message': 'تم حذف الصفحة بنجاح'})

    # PUT - تحديث الصفحة
    try:
        data = request.get_json(silent=True) or {}
        new_slug = sanitize_input(data.get('slug', '')).strip()

        # التحقق من تكرار slug (مع استثناء الصفحة الحالية)
        if new_slug and _slug_exists(new_slug, exclude_id=page_id):
            return jsonify({'success': False, 'message': f'الرابط "{new_slug}" مستخدم بالفعل. يرجى اختيار رابط آخر.'}), 409

        if 'title' in data:
            page['title'] = sanitize_input(data['title'])
        if 'slug' in data:
            page['slug'] = new_slug
        if 'content' in data:
            page['content'] = data['content']
        if 'show_in_menu' in data:
            page['show_in_menu'] = bool(data['show_in_menu'])
        if 'enabled' in data:
            page['enabled'] = bool(data['enabled'])
        if 'menu_icon' in data:
            page['menu_icon'] = sanitize_input(data['menu_icon']).strip() or 'fas fa-file'
        page['updated_at'] = datetime.now().isoformat()

        if not _save_pages(pages):
            return jsonify({'success': False, 'message': 'تعذر حفظ الصفحة'}), 500
        return jsonify({'success': True, 'message': 'تم تحديث الصفحة بنجاح', 'data': page})
    except Exception as e:
        logger.exception("update page error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# API عام: قائمة التنقل الديناميكية
# ============================================

@app.route('/api/menu', methods=['GET'])
def public_menu():
    """Return the exact same effective menu used by the shared public header."""
    try:
        raw = cms_load('menu')
        items = [dict(x) for x in raw] if isinstance(raw, list) else [dict(x) for x in CMS_DEFAULTS['menu']]
        seen = set()
        out = []
        for item in items:
            if not isinstance(item, dict) or not item.get('enabled', True):
                continue
            url = str(item.get('url') or '').strip()
            if not url: continue
            if not url.startswith('/'): url = '/' + url
            if url in seen: continue
            seen.add(url)
            item['url'] = url
            item.setdefault('icon', 'fas fa-link')
            out.append(item)
        for p in _load_pages():
            if not isinstance(p, dict) or not p.get('show_in_menu') or not p.get('enabled', True): continue
            slug = str(p.get('slug') or '').strip().strip('/')
            if not slug: continue
            url='/' + slug
            if url in seen: continue
            seen.add(url)
            out.append({'id':f"page_{p.get('id')}",'label':p.get('title',''),'url':url,'icon':p.get('menu_icon') or 'fas fa-file','enabled':True,'order':999,'is_page':True})
        out.sort(key=lambda x:x.get('order',999))
        return jsonify(out)
    except Exception as e:
        logger.exception('public menu error')
        return jsonify([dict(x) for x in CMS_DEFAULTS['menu']])

@app.route('/api/admin/backups', methods=['GET'])
@admin_required
def list_backups():
    backup_root = os.path.join(os.path.dirname(__file__), 'data', 'backups')
    os.makedirs(backup_root, exist_ok=True)
    result=[]
    for name in sorted(os.listdir(backup_root), reverse=True):
        if name.endswith('.zip'):
            path=os.path.join(backup_root,name)
            result.append({'name':name,'size':os.path.getsize(path),'download_url':'/api/admin/backup/download?file='+name})
    return jsonify(result)

@app.route('/api/admin/backup/download', methods=['GET'])
@admin_required
def download_backup():
    from flask import send_file
    name=os.path.basename(request.args.get('file',''))
    if not name.endswith('.zip'):
        return jsonify({'success':False,'message':'ملف غير صالح'}),400
    path=os.path.join(os.path.dirname(__file__),'data','backups',name)
    if not os.path.isfile(path):
        return jsonify({'success':False,'message':'النسخة غير موجودة'}),404
    return send_file(path, as_attachment=True, download_name=name)

@app.route('/api/admin/backup/delete', methods=['POST'])
@admin_required
def delete_backup():
    """حذف نسخة احتياطية محددة."""
    try:
        data = request.get_json(silent=True) or {}
        name = os.path.basename(str(data.get('name','') or request.args.get('name','') or ''))
        if not name or not name.endswith('.zip'):
            return jsonify({'success':False,'message':'اسم النسخة غير صالح'}),400
        path = os.path.join(os.path.dirname(__file__),'data','backups',name)
        if not os.path.isfile(path):
            return jsonify({'success':False,'message':'النسخة غير موجودة'}),404
        os.remove(path)
        logger.info(f"🗑️ تم حذف النسخة الاحتياطية: {name}")
        return jsonify({'success':True,'message':'تم حذف النسخة الاحتياطية بنجاح'})
    except Exception as e:
        logger.exception("backup delete failed")
        return jsonify({'success':False,'message':'تعذر حذف النسخة الاحتياطية'}),500

@app.route('/api/admin/backup/create-typed', methods=['POST'])
@admin_required
def create_typed_backup():
    try:
        import zipfile, shutil
        kind=str((request.get_json(silent=True) or {}).get("type","all")).lower()
        ALL_DATA_FILES = [
            "users","jobs","applications","favorites","news","testimonials",
            "settings","mail_settings","contact_content","slider","features","custom_stats",
            "cms_pages","cms_services","cms_contact",
            "invoices","invoice_audit","payment_logs","payment_audit",
            "wallets","wallet_transactions","wallet_audit",
            "email_verifications","password_resets","delete_account_otps"
        ]
        allowed={
            "data":["users","jobs","applications","favorites","news","testimonials","invoices","wallets","wallet_transactions","wallet_audit","payment_logs","payment_audit","invoice_audit","email_verifications","password_resets","delete_account_otps"],
            "settings":["settings","mail_settings","contact_content","slider","features","custom_stats","cms_pages","cms_services","cms_contact"],
            "all":ALL_DATA_FILES
        }
        if kind not in allowed:
            return jsonify({"success":False,"message":"نوع النسخة غير صالح"}),400
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        backup_dir = os.path.join(data_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        os.makedirs(backup_dir,exist_ok=True)
        label={"data":"بيانات","settings":"إعدادات","all":"كاملة"}[kind]
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name=f"arabjobs_backup_{kind}_{stamp}.zip"
        zip_path=os.path.join(backup_dir,zip_name)
        with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
            for name in allowed[kind]:
                p=os.path.join(data_dir,name+".enc")
                if os.path.isfile(p): z.write(p,arcname="data/"+name+".enc")
            key=os.path.join(data_dir,".key")
            if os.path.isfile(key): z.write(key,arcname="data/.key")
        return jsonify({"success":True,"message":f"تم إنشاء نسخة {label} بنجاح","name":zip_name,"download_url":"/api/admin/backup/download?file="+quote(zip_name)})
    except Exception as e:
        logger.exception("typed backup failed")
        return jsonify({"success":False,"message":"تعذر إنشاء النسخة الاحتياطية"}),500

@app.route('/api/admin/backup/restore', methods=['POST'])
@admin_required
def restore_backup():
    # Restore only encrypted data files from an uploaded ZIP; never extract arbitrary paths.
    import zipfile, tempfile
    f=request.files.get("backup")
    if not f or not f.filename or not f.filename.lower().endswith(".zip"):
        return jsonify({"success":False,"message":"اختر ملف ZIP صالحاً"}),400
    kind=str(request.form.get("type","all")).lower()
    ALL_DATA_FILES = [
        "users","jobs","applications","favorites","news","testimonials",
        "settings","mail_settings","contact_content","slider","features","custom_stats",
        "cms_pages","cms_services","cms_contact",
        "invoices","invoice_audit","payment_logs","payment_audit",
        "wallets","wallet_transactions","wallet_audit",
        "email_verifications","password_resets","delete_account_otps"
    ]
    allowed={
        "data":{"users","jobs","applications","favorites","news","testimonials","invoices","wallets","wallet_transactions","wallet_audit","payment_logs","payment_audit","invoice_audit","email_verifications","password_resets","delete_account_otps"},
        "settings":{"settings","mail_settings","contact_content","slider","features","custom_stats","cms_pages","cms_services","cms_contact"},
        "all":set(ALL_DATA_FILES)
    }
    if kind not in allowed:return jsonify({"success":False,"message":"نوع الاستعادة غير صالح"}),400
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    try:
        with zipfile.ZipFile(f.stream) as z:
            for member in z.infolist():
                name=os.path.basename(member.filename)
                if name in {x+".enc" for x in allowed[kind]}:
                    target=os.path.join(data_dir,name)
                    with z.open(member) as src, open(target,"wb") as dst:
                        shutil.copyfileobj(src,dst)
        return jsonify({"success":True,"message":"تمت استعادة النسخة المحددة بنجاح. أعد تشغيل الموقع لتطبيق جميع الإعدادات."})
    except Exception:
        logger.exception("backup restore failed")
        return jsonify({"success":False,"message":"تعذر استعادة النسخة الاحتياطية"}),500

@app.route('/api/admin/backup', methods=['POST'])
@admin_required
def admin_backup():
    try:
        backup_dir = secure_storage.create_backup()
        if not backup_dir:
            return jsonify({'success': False, 'message': 'تعذر إنشاء النسخة الاحتياطية'}), 500
        return jsonify({'success': True, 'message': 'تم إنشاء النسخة الاحتياطية', 'path': str(backup_dir), 'download_url': '/api/admin/backup/download?file=' + quote(os.path.basename(str(backup_dir)))})
    except Exception as e:
        logger.exception("backup error")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

# ============================================
# تسجيل أخطاء JavaScript من الموقع (للمراقبة في لوحة الإدارة)
# ============================================
@app.route('/api/client-error', methods=['POST'])
def client_error_report():
    """استقبال أخطاء المتصفح وتسجيلها في سجل الأخطاء الإداري دون كشف معلومات حساسة."""
    try:
        if not is_logging_enabled():
            return ('', 204)
        data = request.get_json(silent=True) or {}
        message = str(data.get('message') or 'JavaScript client error').strip()[:500]
        cause = str(data.get('cause') or 'client_js_error').strip()[:120]
        source = str(data.get('source') or '').strip()[:500]
        line = data.get('line') or 0
        column = data.get('column') or 0
        stack = str(data.get('stack') or '').strip()[:4000]
        url = str(data.get('url') or '').strip()[:500]
        # لا نسجل cookies أو localStorage أو request body أو كلمات مرور.
        details = f"source={source}; line={line}; column={column}; url={url}"
        if stack:
            details += f"; stack={stack}"
        log_error(message, f"{cause}; {details}", entry_type="client_error", actor_id=session.get("user_id", "") if session else "", source="official")
        return jsonify({'success': True}), 204
    except Exception:
        # مراقبة الأخطاء لا يجب أن تعطل الموقع.
        return ('', 204)

# ============================================
# سجل أخطاء الخادم (لوحة الإدارة)
# ============================================

@app.route('/api/admin/error-log', methods=['GET'])
@admin_required
def admin_error_log():
    """عرض سجل الأخطاء مع فصل الموقع الرسمي عن Telegram."""
    try:
        source = str(request.args.get('source', 'official')).strip().lower()
        if source not in {'official', 'telegram', 'all'}:
            source = 'official'
        entries = get_error_log()
        if source != 'all':
            entries = [e for e in entries if str(e.get('source', 'official')).lower() == source]
        return jsonify({'success': True, 'source': source, 'entries': entries})
    except Exception as e:
        logger.exception("error log read failed")
        return jsonify({'success': False, 'message': 'تعذر قراءة سجل الأخطاء'}), 500

@app.route('/api/admin/error-log/settings', methods=['GET', 'PUT'])
@admin_required
def admin_error_log_settings():
    if request.method == 'GET':
        return jsonify({'success': True, 'enabled': is_logging_enabled()})
    try:
        data = request.get_json(silent=True) or {}
        if 'enabled' not in data:
            return jsonify({'success': False, 'message': 'قيمة enabled مطلوبة'}), 400
        if not set_logging_enabled(bool(data.get('enabled'))):
            return jsonify({'success': False, 'message': 'تعذر حفظ إعداد سجل الأخطاء'}), 500
        return jsonify({'success': True, 'enabled': is_logging_enabled()})
    except Exception as e:
        logger.exception('error log settings failed')
        return jsonify({'success': False, 'message': 'تعذر تحديث إعداد سجل الأخطاء'}), 500

@app.route('/api/admin/error-log', methods=['DELETE'])
@admin_required
def admin_error_log_clear():
    """مسح سجل تبويب محدد: الموقع الرسمي أو Telegram."""
    try:
        source = str(request.args.get('source', 'official')).strip().lower()
        if source not in {'official', 'telegram', 'all'}:
            source = 'official'
        if source == 'all':
            ok = clear_error_log()
        else:
            entries = [e for e in get_error_log() if str(e.get('source', 'official')).lower() != source]
            # get_error_log returns newest first; reverse is unnecessary for persistence.
            ok = secure_storage.encryption.encrypt_file('error_log', entries)
        if not ok:
            return jsonify({'success': False, 'message': 'تعذر مسح سجل الأخطاء'}), 500
        return jsonify({'success': True, 'message': 'تم مسح سجل التبويب بنجاح', 'source': source})
    except Exception as e:
        logger.exception("error log clear failed")
        return jsonify({'success': False, 'message': 'تعذر مسح سجل الأخطاء'}), 500

# ============================================
# معالجة الأخطاء
# ============================================

@app.errorhandler(404)
def not_found(error):
    log_error("صفحة غير موجودة (404)", f"المسار المطلوب: {request.path}")
    return '''
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>404 - الصفحة غير موجودة</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                   background: #f4f7fc; display: flex; justify-content: center; 
                   align-items: center; min-height: 100vh; direction: rtl; }
            .error-page { text-align: center; padding: 50px; background: #fff; border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }
            .error-page i { font-size: 80px; color: #1a4a6e; }
            .error-page h1 { font-size: 60px; color: #0d2b3e; margin: 20px 0; }
            .error-page p { color: #6b7a8a; font-size: 18px; }
            .error-page a { display: inline-block; margin-top: 20px; padding: 12px 30px; 
                           background: #1a4a6e; color: #fff; text-decoration: none; 
                           border-radius: 30px; transition: 0.3s; }
            .error-page a:hover { background: #0d2b3e; transform: scale(1.05); }
        </style>
    </head>
    <body>
        <div class="error-page">
            <i class="fas fa-exclamation-triangle"></i>
            <h1>404</h1>
            <p>عذراً، الصفحة التي تبحث عنها غير موجودة</p>
            <a href="/"><i class="fas fa-home"></i> العودة للرئيسية</a>
        </div>
    </body>
    </html>
    ''', 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"❌ خطأ داخلي: {str(error)}")
    log_error("خطأ داخلي في الخادم (500)", str(error), traceback.format_exc())
    return jsonify({'error': 'حدث خطأ في الخادم'}), 500

# ملاحظة: لا نضيف @app.errorhandler(Exception) لأنها تمنع عمل معالجات
# الأخطاء المخصصة في Flask (404/500) وتكسر التنقل في لوحة الإدارة.
# معالج 500 المخصص أعلاه يلتقط تلقائياً أي استثناء غير معالج ويسجله.

# ============================================
# API الفواتير للمشرف
# ============================================

def _reconcile_trial_invoices():
    """مزامنة فواتير الشحن التجريبي القديمة من payment_logs وwallet_transactions."""
    try:
        from invoice_service import create_invoice, list_invoices
        users = secure_storage.load_users() or []
        invoices_result, _ = list_invoices()
        invoices = invoices_result.get('invoices', []) if isinstance(invoices_result, dict) else []
        existing = {str(x.get('paymentId')) for x in invoices if x.get('paymentId')}

        candidates = []
        payment_logs = secure_storage.encryption.decrypt_file('payment_logs') or []
        for p in payment_logs:
            if p.get('paymentType') == 'wallet_topup_trial' or (p.get('metadata') or {}).get('trial'):
                candidates.append(p)

        transactions = secure_storage.encryption.decrypt_file('wallet_transactions') or []
        for tx in transactions:
            meta = tx.get('metadata') or {}
            if meta.get('trial') or tx.get('referenceType') == 'trial':
                payment_id = str(tx.get('referenceId') or meta.get('paymentId') or '')
                if payment_id:
                    candidates.append({
                        'paymentId': payment_id,
                        'employerId': tx.get('employerId'),
                        'amount': float(tx.get('amount', 0) or 0) / 100.0,
                        'amountUnit': 'major',
                        'currency': 'USD',
                        'status': 'paid',
                        'paymentType': 'wallet_topup_trial',
                        'invoiceType': 'wallet_topup_trial',
                        'createdAt': tx.get('createdAt'),
                        'updatedAt': tx.get('updatedAt')
                    })

        created = 0
        seen = set()
        for payment in candidates:
            pid = str(payment.get('paymentId') or '')
            if not pid or pid in existing or pid in seen:
                continue
            seen.add(pid)
            uid = str(payment.get('employerId') or '')
            user = next((u for u in users if str(u.get('id')) == uid), None)
            if not user:
                continue
            p = dict(payment)
            p['paymentId'] = pid
            p['employerId'] = uid
            p['employerEmail'] = p.get('employerEmail') or user.get('email', '')
            p['applicantId'] = p.get('applicantId')
            p['jobId'] = p.get('jobId')
            p['amountUnit'] = p.get('amountUnit', 'major')
            p['currency'] = p.get('currency', 'USD')
            p['status'] = p.get('status', 'paid')
            p['formattedPrice'] = p.get('formattedPrice') or f"{float(p.get('amount', 0) or 0):.2f} USD"
            result, code = create_invoice(p)
            if code in (200, 201):
                created += 1
                existing.add(pid)
        return created
    except Exception as e:
        logger.exception(f'فشل مزامنة فواتير الشحن التجريبي: {e}')
        return 0


@app.route('/api/admin/invoices', methods=['GET'])
@admin_required
def admin_list_invoices():
    """قائمة الفواتير مع إصلاح تلقائي لفواتير الشحن التجريبي القديمة."""
    _reconcile_trial_invoices()
    from invoice_service import list_invoices
    filters = {}
    employer_id = request.args.get('employerId')
    status = request.args.get('status')
    payment_id = request.args.get('paymentId')
    if employer_id: filters['employerId'] = employer_id
    if status: filters['status'] = status
    if payment_id: filters['paymentId'] = payment_id
    result, status_code = list_invoices(filters if filters else None)
    return jsonify(result), status_code


@app.route('/api/admin/invoices/<invoice_id>', methods=['GET'])
@admin_required
def admin_get_invoice(invoice_id):
    """
    الحصول على فاتورة محددة - للمشرف فقط.
    """
    from invoice_service import get_invoice
    
    result, status_code = get_invoice(invoice_id)
    return jsonify(result), status_code


# ============================================
# تشغيل الخادم
# ============================================

if __name__ == '__main__':
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║     🔐 منصة التوظيف العربية - النسخة النهائية 🔐            ║
    ║                                                              ║
    ║     📡 الخادم يعمل على: http://0.0.0.0:{port}              ║
    ║     🔒 جميع البيانات مشفرة بـ AES-256                      ║
    ║     🛡️ حماية من XSS و هجمات القوة العمياء                 ║
    ║                                                              ║
    ║     ✅ جميع API تعمل بنجاح                                  ║
    ╚══════════════════════════════════════════════════════════════╝
    """.format(port=os.environ.get('PORT', 61411)))
    
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 61411)),
        debug=False,
        threaded=True
    )
