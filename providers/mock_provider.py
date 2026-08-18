"""
مزود دفع تجريبي (Mock) - المرحلة 9
ينقل كود Mock الحالي من payment_gateway.py
لا يتصل بأي خدمة خارجية
"""

import secrets
from datetime import datetime

from providers.base_provider import BasePaymentProvider

# ============================================
# إصدار المزود
# ============================================
MOCK_PROVIDER_VERSION = "1.0.0"

# ============================================
# حالات الدفع
# ============================================
PAYMENT_STATES = ['created', 'pending', 'paid', 'failed', 'cancelled', 'refunded']

# الانتقالات المسموح بها بين الحالات
ALLOWED_TRANSITIONS = {
    'created': ['pending', 'cancelled', 'failed'],
    'pending': ['paid', 'failed', 'cancelled'],
    'paid': ['refunded'],
    'failed': ['pending'],  # يمكن إعادة المحاولة
    'cancelled': [],        # لا يمكن تغييره بدون عملية جديدة
    'refunded': []          # نهائي - لا يمكن الرجوع إلى paid
}

# ============================================
# توقيع Webhook وهمي
# ============================================
MOCK_WEBHOOK_SECRET = "mock_webhook_secret_phase9"

# ============================================
# السماح بدوال الاختبار (يُقرأ من Environment Variables)
# ============================================
from payment_config import ALLOW_TEST_METHODS


def can_transition(from_state, to_state):
    """
    التحقق من إمكانية الانتقال بين حالتين وفق القواعد.
    """
    allowed = ALLOWED_TRANSITIONS.get(from_state, [])
    return to_state in allowed


class MockPaymentProvider(BasePaymentProvider):
    """
    مزود دفع تجريبي (Mock Provider).
    يحاكي سلوك بوابة الدفع الحقيقية دون الاتصال بأي خدمة خارجية.
    """

    def __init__(self):
        """تهيئة المزود"""
        self._payments = {}  # تخزين مؤقت في الذاكرة
        self._processed_events = {}  # تتبع الأحداث المعالجة (idempotency)

    def create_payment(self, amount, currency='SAR', description='', metadata=None):
        """
        إنشاء عملية دفع جديدة.
        """
        # توليد paymentId فريد
        payment_id = f"pay_{secrets.token_urlsafe(16)}"
        
        # حماية idempotency: التأكد من عدم وجود نفس paymentId
        while payment_id in self._payments:
            payment_id = f"pay_{secrets.token_urlsafe(16)}"
        
        now = datetime.now().isoformat()
        payment = {
            'paymentId': payment_id,
            'amount': amount,
            'currency': str(currency).upper(),
            'description': description,
            'metadata': metadata or {},
            'status': 'created',
            'createdAt': now,
            'updatedAt': now,
            'provider': 'mock',
            'provider_version': MOCK_PROVIDER_VERSION
        }
        
        self._payments[payment_id] = payment
        return payment

    def _transition_payment(self, payment, to_state):
        """
        تغيير حالة الدفع مع التحقق من الانتقال المسموح.
        """
        current_state = payment.get('status')
        
        if to_state not in PAYMENT_STATES:
            return False, f'حالة غير صالحة: {to_state}'
        
        if not can_transition(current_state, to_state):
            return False, f'لا يمكن الانتقال من {current_state} إلى {to_state}'
        
        payment['status'] = to_state
        payment['updatedAt'] = datetime.now().isoformat()
        return True, 'تم التحديث بنجاح'

    def verify_payment(self, payment_id):
        """
        التحقق من حالة عملية دفع.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'status': 'not_found',
                'verified': False
            }
        
        return {
            'paymentId': payment_id,
            'status': payment.get('status'),
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
            'verified': payment.get('status') == 'paid'
        }

    def refund_payment(self, payment_id):
        """
        استرداد عملية دفع.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': 'الدفع غير موجود'
            }
        
        success, message = self._transition_payment(payment, 'refunded')
        
        if not success:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': message
            }
        
        return {
            'paymentId': payment_id,
            'success': True,
            'message': 'تم الاسترداد بنجاح',
            'status': 'refunded'
        }

    def get_payment_status(self, payment_id):
        """
        الحصول على حالة عملية دفع.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'status': 'not_found'
            }
        
        return {
            'paymentId': payment_id,
            'status': payment.get('status'),
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
            'description': payment.get('description'),
            'createdAt': payment.get('createdAt'),
            'updatedAt': payment.get('updatedAt')
        }

    def mark_payment_pending(self, payment_id):
        """
        تحويل الدفع من created إلى pending.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': 'الدفع غير موجود'
            }
        
        success, message = self._transition_payment(payment, 'pending')
        
        return {
            'paymentId': payment_id,
            'success': success,
            'message': message,
            'status': payment.get('status') if success else None
        }

    def mark_payment_failed(self, payment_id):
        """
        تحديد فشل الدفع.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': 'الدفع غير موجود'
            }
        
        success, message = self._transition_payment(payment, 'failed')
        
        return {
            'paymentId': payment_id,
            'success': success,
            'message': message,
            'status': payment.get('status') if success else None
        }

    def simulate_payment_success(self, payment_id):
        """
        محاكاة نجاح عملية دفع (لأغراض الاختبار فقط).
        محمية: لا تعمل إذا كان ALLOW_TEST_METHODS = False (الإنتاج).
        """
        # التحقق من القيمة الحالية (وليس القيمة عند التحميل)
        from payment_config import ALLOW_TEST_METHODS as CURRENT_ALLOW_TEST
        
        if not CURRENT_ALLOW_TEST:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': 'دوال الاختبار معطّلة في بيئة الإنتاج'
            }
        
        payment = self._payments.get(payment_id)
        if not payment:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': 'الدفع غير موجود'
            }
        
        success, message = self._transition_payment(payment, 'paid')
        
        if not success:
            return {
                'paymentId': payment_id,
                'success': False,
                'message': message
            }
        
        return {
            'paymentId': payment_id,
            'success': True,
            'status': 'paid'
        }

    def verify_webhook_signature(self, signature, payload):
        """
        التحقق من توقيع Webhook (وهمي حالياً).
        """
        return signature == MOCK_WEBHOOK_SECRET

    # ============================================
    # نظام idempotency للأحداث
    # ============================================

    def is_event_processed(self, event_id):
        """
        التحقق مما إذا كان حدث webhook قد تمت معالجته بالفعل.
        """
        return event_id in self._processed_events

    def mark_event_processed(self, event_id):
        """
        تسجيل حدث كمعالج (لضمان عدم معالجته مرتين).
        """
        self._processed_events[event_id] = {
            'processed': True,
            'timestamp': datetime.now().isoformat()
        }