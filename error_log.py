"""
وحدة تسجيل أخطاء الخادم - منصة التوظيف العربية
تخزين الأخطاء بشكل مشفر AES-256 في data/error_log.enc
"""
import json
import traceback
from datetime import datetime
from encryption import secure_storage

ERROR_LOG_FILE = "error_log"
MAX_LOG_ENTRIES = 500  # الحد الأقصى لعدد السجلات المحفوظة
SETTINGS_FILE = "error_log_settings"


def _load_enabled():
    """تحميل حالة تشغيل سجل الأخطاء مرة واحدة من التخزين المشفر."""
    try:
        data = secure_storage.encryption.decrypt_file(SETTINGS_FILE)
        if isinstance(data, dict) and "enabled" in data:
            return bool(data.get("enabled"))
    except Exception:
        pass
    return True


ERROR_LOG_ENABLED = _load_enabled()


def is_logging_enabled():
    return bool(ERROR_LOG_ENABLED)


def set_logging_enabled(enabled):
    """تغيير حالة التسجيل وحفظها ليستمر الاختيار بعد إعادة تشغيل الخادم."""
    global ERROR_LOG_ENABLED
    enabled = bool(enabled)
    try:
        if not secure_storage.encryption.encrypt_file(SETTINGS_FILE, {"enabled": enabled}):
            return False
        ERROR_LOG_ENABLED = enabled
        return True
    except Exception:
        return False


def _load_log():
    """تحميل سجل الأخطاء من التخزين المشفر"""
    try:
        data = secure_storage.encryption.decrypt_file(ERROR_LOG_FILE)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _save_log(entries):
    """حفظ سجل الأخطاء في التخزين المشفر"""
    try:
        return secure_storage.encryption.encrypt_file(ERROR_LOG_FILE, entries)
    except Exception:
        return False


def log_error(message, cause="", tb=None, entry_type="error", actor_id="", actor_role="", source="official"):

    """
    تسجيل خطأ جديد في السجل.
    
    Args:
        message: وصف الخطأ بالعربية
        cause: سبب المشكلة
        tb: كائن traceback (اختياري)
    """
    try:
        if not ERROR_LOG_ENABLED:
            return False
        entries = _load_log()
        entry = {
            "id": int(datetime.now().timestamp() * 1000),
            "type": str(entry_type or "error")[:40],
            "actor_id": str(actor_id or "")[:120],
            "actor_role": str(actor_role or "")[:40],
            "source": str(source or "official")[:30],
            "message": str(message),
            "cause": str(cause),
            "traceback": tb if tb else "",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        entries.append(entry)
        # الاحتفاظ بآخر MAX_LOG_ENTRIES سجل فقط
        if len(entries) > MAX_LOG_ENTRIES:
            entries = entries[-MAX_LOG_ENTRIES:]
        _save_log(entries)
        return True
    except Exception:
        return False


def get_error_log():
    """استرجاع سجل الأخطاء (الأحدث أولاً)"""
    entries = _load_log()
    # ترتيب تنازلي حسب التاريخ (الأحدث أولاً)
    entries.sort(key=lambda x: x.get("id", 0), reverse=True)
    return entries


def clear_error_log():
    """مسح سجل الأخطاء بالكامل"""
    return _save_log([])