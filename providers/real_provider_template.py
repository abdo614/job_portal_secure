"""
مزود دفع حقيقي (قالب) - المرحلة 10
هيكل جاهز لربط بوابة دفع حقيقية (مثل Stripe)
لا يربط أي خدمة خارجية حالياً
يرفض التشغيل بدون مفاتيح API
"""

from providers.base_provider import BasePaymentProvider


class RealPaymentProvider(BasePaymentProvider):
    """
    مزود دفع حقيقي (قالب).
    لا يربط أي خدمة خارجية حالياً.
    يرفض التشغيل بدون مفاتيح API.
    """

    def __init__(self):
        """تهيئة المزود الحقيقي"""
        from payment_config import API_KEY, SECRET_KEY, WEBHOOK_SECRET
        
        # التحقق من وجود المفاتيح
        if not API_KEY or not SECRET_KEY or not WEBHOOK_SECRET:
            raise ValueError(
                "لا يمكن تفعيل مزود الدفع الحقيقي: مفاتيح API مفقودة. "
                "يرجى تعيين PAYMENT_API_KEY, PAYMENT_SECRET_KEY, PAYMENT_WEBHOOK_SECRET "
                "في Environment Variables."
            )
        
        self.api_key = API_KEY
        self.secret_key = SECRET_KEY
        self.webhook_secret = WEBHOOK_SECRET
        self._payments = {}  # تخزين مؤقت (يُستبدل بالاتصال الفعلي لاحقاً)

    def create_payment(self, amount, currency='SAR', description='', metadata=None):
        """
        إنشاء عملية دفع جديدة.
        
        ملاحظة: هذه الدالة غير مفعّلة حالياً.
        يجب ربط بوابة دفع حقيقية (مثل Stripe) لتفعيلها.
        """
        return {
            'success': False,
            'message': 'Provider غير مربوط - يرجى ربط بوابة دفع حقيقية (مثل Stripe)'
        }

    def verify_payment(self, payment_id):
        """
        التحقق من حالة عملية دفع.
        
        ملاحظة: هذه الدالة غير مفعّلة حالياً.
        يجب ربط بوابة دفع حقيقية (مثل Stripe) لتفعيلها.
        """
        return {
            'paymentId': payment_id,
            'status': 'error',
            'verified': False,
            'message': 'Provider غير مربوط'
        }

    def refund_payment(self, payment_id):
        """
        استرداد عملية دفع.
        
        ملاحظة: هذه الدالة غير مفعّلة حالياً.
        يجب ربط بوابة دفع حقيقية (مثل Stripe) لتفعيلها.
        """
        return {
            'paymentId': payment_id,
            'success': False,
            'message': 'Provider غير مربوط'
        }

    def get_payment_status(self, payment_id):
        """
        الحصول على حالة عملية دفع.
        
        ملاحظة: هذه الدالة غير مفعّلة حالياً.
        يجب ربط بوابة دفع حقيقية (مثل Stripe) لتفعيلها.
        """
        return {
            'paymentId': payment_id,
            'status': 'error',
            'message': 'Provider غير مربوط'
        }

    def verify_webhook(self, signature, payload):
        """
        التحقق من توقيع Webhook.
        
        ملاحظة: هذه الدالة غير مفعّلة حالياً.
        يجب ربط بوابة دفع حقيقية (مثل Stripe) لتفعيلها.
        """
        return {
            'verified': False,
            'message': 'Provider غير مربوط'
        }