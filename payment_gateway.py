"""
طبقة وسيطة لبوابة الدفع - المرحلة 9
Mock Gateway - لا تتصل بأي خدمة خارجية حالياً
توفر واجهة موحدة للتعامل مع بوابة الدفع الحقيقية لاحقاً
"""

from datetime import datetime

# ============================================
# إصدار البوابة
# ============================================
PAYMENT_GATEWAY_VERSION = "1.0.1"

# ============================================
# إعدادات المزود
# ============================================
from payment_config import MOCK_MODE, PAYMENT_PROVIDER, ALLOW_TEST_METHODS, APP_ENV, is_provider_configured, get_provider_info

# ============================================
# تحميل المزود المناسب
# ============================================
if PAYMENT_PROVIDER == "mock":
    from providers.mock_provider import MockPaymentProvider
    _provider = MockPaymentProvider()
elif PAYMENT_PROVIDER == "real":
    from providers.real_provider_template import RealPaymentProvider
    _provider = RealPaymentProvider()
else:
    _provider = None

# ============================================
# دوال الواجهة الموحدة (تبقى نفس الأسماء)
# ============================================

def create_payment(amount, currency='SAR', description='', metadata=None):
    """
    إنشاء عملية دفع جديدة.
    
    Args:
        amount: المبلغ (بالوحدة الصغرى - سنت/هللة)
        currency: العملة (مثل SAR, AED, KWD)
        description: وصف العملية
        metadata: بيانات إضافية (اختياري)
        
    Returns:
        dict: بيانات عملية الدفع
    """
    if _provider is None:
        return {
            'success': False,
            'message': 'Provider غير مفعّل - يرجى تفعيل MOCK_MODE أو تكوين مزود حقيقي'
        }
    
    return _provider.create_payment(amount, currency, description, metadata)


def verify_payment(payment_id):
    """
    التحقق من حالة عملية دفع.
    
    Args:
        payment_id: معرف عملية الدفع
        
    Returns:
        dict: حالة عملية الدفع
    """
    if _provider is None:
        return {
            'paymentId': payment_id,
            'status': 'error',
            'verified': False,
            'message': 'Provider غير مفعّل'
        }
    
    return _provider.verify_payment(payment_id)


def refund_payment(payment_id):
    """
    استرداد عملية دفع.
    
    Args:
        payment_id: معرف عملية الدفع
        
    Returns:
        dict: نتيجة الاسترداد
    """
    if _provider is None:
        return {
            'paymentId': payment_id,
            'success': False,
            'message': 'Provider غير مفعّل'
        }
    
    return _provider.refund_payment(payment_id)


def get_payment_status(payment_id):
    """
    الحصول على حالة عملية دفع.
    
    Args:
        payment_id: معرف عملية الدفع
        
    Returns:
        dict: حالة عملية الدفع
    """
    if _provider is None:
        return {
            'paymentId': payment_id,
            'status': 'error',
            'message': 'Provider غير مفعّل'
        }
    
    return _provider.get_payment_status(payment_id)


# ============================================
# دوال إضافية (للاستخدام الداخلي)
# ============================================

def get_gateway_info():
    """
    الحصول على معلومات البوابة الحالية.
    
    Returns:
        dict: معلومات البوابة
    """
    return {
        'gateway_version': PAYMENT_GATEWAY_VERSION,
        'provider_info': get_provider_info(),
        'mock_mode': MOCK_MODE
    }


def is_mock_mode():
    """
    التحقق من أن النظام في وضع التجربة (Mock).
    
    Returns:
        bool: هل النظام في وضع Mock؟
    """
    return MOCK_MODE


def is_test_allowed():
    """
    التحقق من أن أدوات الاختبار مسموحة.
    
    Returns:
        bool: هل أدوات الاختبار مسموحة؟
    """
    # في الإنتاج: لا يُسمح بأدوات الاختبار
    if APP_ENV.lower() == "production":
        return False
    # التحقق من ALLOW_TEST_METHODS
    return ALLOW_TEST_METHODS


# ============================================
# تصدير الدوال الرئيسية
# ============================================
__all__ = [
    'create_payment',
    'verify_payment',
    'refund_payment',
    'get_payment_status',
    'get_gateway_info',
    'is_mock_mode',
    'PAYMENT_GATEWAY_VERSION'
]