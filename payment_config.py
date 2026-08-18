"""
إعدادات بوابة الدفع - المرحلة 11 (Production Hardening)
تقرأ جميع الإعدادات من Environment Variables فقط
لا توجد أي إعدادات مكتوبة بشكل ثابت داخل الكود
"""

import os

# ============================================
# مزود الدفع الحالي
# ============================================
# القيم الممكنة: "mock" (حالياً) أو "real" لمزود حقيقي
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")

# ============================================
# بيئة التطبيق
# ============================================
# القيم الممكنة: "development" أو "production"
APP_ENV = os.getenv("APP_ENV", "development")

# ============================================
# وضع الدفع
# ============================================
# القيم الممكنة: "test" أو "live"
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "test")

# ============================================
# السماح بأدوات الاختبار
# ============================================
# القيم الممكنة: "true" أو "false"
# في الإنتاج يجب أن يكون false
ALLOW_TEST_METHODS = os.getenv("ALLOW_TEST_METHODS", "true").lower() == "true"

# ============================================
# مفاتيح API - من Environment Variables فقط
# ============================================
API_KEY = os.getenv("PAYMENT_API_KEY", "")
SECRET_KEY = os.getenv("PAYMENT_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")

# ============================================
# وضع التجربة
# ============================================
# True: يستخدم Mock Gateway
# False: يحاول استخدام مزود حقيقي
MOCK_MODE = PAYMENT_PROVIDER == "mock"

# ============================================
# التحقق من الإعدادات
# ============================================
def is_provider_configured():
    """
    التحقق من أن مزود الدفع مفعّل.
    
    Returns:
        bool: هل المزود مفعّل؟
    """
    if MOCK_MODE:
        return True
    # للمزود الحقيقي: يجب توفر المفاتيح
    return bool(API_KEY and SECRET_KEY and WEBHOOK_SECRET)


def is_production():
    """
    التحقق من أن التطبيق في وضع الإنتاج.
    
    Returns:
        bool: هل التطبيق في وضع الإنتاج؟
    """
    return APP_ENV.lower() == "production"


def is_live_mode():
    """
    التحقق من أن وضع الدفع حي (live).
    
    Returns:
        bool: هل وضع الدفع حي؟
    """
    return PAYMENT_MODE.lower() == "live"


def get_provider_info():
    """
    الحصول على معلومات المزود الحالي (بدون كشف المفاتيح).
    
    Returns:
        dict: معلومات المزود
    """
    return {
        'provider': PAYMENT_PROVIDER,
        'mode': 'mock' if MOCK_MODE else 'real',
        'configured': is_provider_configured(),
        'has_api_key': bool(API_KEY),
        'has_secret_key': bool(SECRET_KEY),
        'has_webhook_secret': bool(WEBHOOK_SECRET),
        'app_env': APP_ENV,
        'payment_mode': PAYMENT_MODE,
        'allow_test_methods': ALLOW_TEST_METHODS
    }


def get_security_info():
    """
    الحصول على معلومات الأمان (للـ logs والمراقبة).
    
    Returns:
        dict: معلومات الأمان
    """
    return {
        'app_env': APP_ENV,
        'payment_mode': PAYMENT_MODE,
        'allow_test_methods': ALLOW_TEST_METHODS,
        'is_production': is_production(),
        'is_live_mode': is_live_mode(),
        'mock_mode': MOCK_MODE
    }