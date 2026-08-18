"""
مزود الدفع التجريبي (Mock).
يحاكي بوابة الدفع الحقيقية بدون اتصال خارجي.
"""

import secrets
from datetime import datetime

from providers.base_provider import BasePaymentProvider
from payment_config import ALLOW_TEST_METHODS

MOCK_PROVIDER_VERSION = "1.0.1"
MOCK_WEBHOOK_SECRET = "mock_webhook_secret_phase9"

PAYMENT_STATES = ["created", "pending", "paid", "failed", "cancelled", "refunded"]
ALLOWED_TRANSITIONS = {
    "created": ["pending", "cancelled", "failed"],
    "pending": ["paid", "failed", "cancelled"],
    "paid": ["refunded"],
    "failed": ["pending"],
    "cancelled": [],
    "refunded": [],
}


def can_transition(from_state, to_state):
    return to_state in ALLOWED_TRANSITIONS.get(from_state, [])


class MockPaymentProvider(BasePaymentProvider):
    def __init__(self):
        self._payments = {}
        self._processed_events = {}

    def create_payment(self, amount, currency="SAR", description="", metadata=None):
        payment_id = f"pay_{secrets.token_urlsafe(16)}"
        while payment_id in self._payments:
            payment_id = f"pay_{secrets.token_urlsafe(16)}"
        now = datetime.now().isoformat()
        payment = {
            "paymentId": payment_id,
            "amount": amount,
            "currency": str(currency).upper(),
            "description": description,
            "metadata": metadata or {},
            "status": "created",
            "createdAt": now,
            "updatedAt": now,
            "provider": "mock",
            "provider_version": MOCK_PROVIDER_VERSION,
        }
        self._payments[payment_id] = payment
        return payment

    def _transition_payment(self, payment, to_state):
        current = payment.get("status")
        if to_state not in PAYMENT_STATES:
            return False, f"حالة غير صالحة: {to_state}"
        if not can_transition(current, to_state):
            return False, f"لا يمكن الانتقال من {current} إلى {to_state}"
        payment["status"] = to_state
        payment["updatedAt"] = datetime.now().isoformat()
        return True, "تم التحديث بنجاح"

    def verify_payment(self, payment_id):
        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "status": "not_found", "verified": False}
        return {
            "paymentId": payment_id,
            "status": payment.get("status"),
            "amount": payment.get("amount"),
            "currency": payment.get("currency"),
            "verified": payment.get("status") == "paid",
        }

    def refund_payment(self, payment_id):
        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "success": False, "message": "الدفع غير موجود"}
        success, message = self._transition_payment(payment, "refunded")
        return {
            "paymentId": payment_id,
            "success": success,
            "message": message,
            "status": payment.get("status") if success else None,
        }

    def get_payment_status(self, payment_id):
        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "status": "not_found"}
        return {
            "paymentId": payment_id,
            "status": payment.get("status"),
            "amount": payment.get("amount"),
            "currency": payment.get("currency"),
            "description": payment.get("description"),
            "createdAt": payment.get("createdAt"),
            "updatedAt": payment.get("updatedAt"),
        }

    def mark_payment_pending(self, payment_id):
        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "success": False, "message": "الدفع غير موجود"}
        success, message = self._transition_payment(payment, "pending")
        return {
            "paymentId": payment_id,
            "success": success,
            "message": message,
            "status": payment.get("status") if success else None,
        }

    def mark_payment_failed(self, payment_id):
        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "success": False, "message": "الدفع غير موجود"}
        success, message = self._transition_payment(payment, "failed")
        return {
            "paymentId": payment_id,
            "success": success,
            "message": message,
            "status": payment.get("status") if success else None,
        }

    def simulate_payment_success(self, payment_id):
        """إكمال دفع تجريبي بأمان عبر المسار created -> pending -> paid."""
        from payment_config import ALLOW_TEST_METHODS as CURRENT_ALLOW_TEST
        if not CURRENT_ALLOW_TEST:
            return {"paymentId": payment_id, "success": False, "message": "دوال الاختبار معطّلة في بيئة الإنتاج"}

        payment = self._payments.get(payment_id)
        if not payment:
            return {"paymentId": payment_id, "success": False, "message": "الدفع غير موجود"}

        # إنشاء الدفع يبدأ بحالة created. المسار السابق كان يحاول القفز مباشرة
        # إلى paid، وهو انتقال غير مسموح به في ALLOWED_TRANSITIONS، لذلك كان
        # /test-complete يعيد 400. نمر الآن بالحالة pending أولاً.
        if payment.get("status") == "created":
            ok, message = self._transition_payment(payment, "pending")
            if not ok:
                return {"paymentId": payment_id, "success": False, "message": message}

        success, message = self._transition_payment(payment, "paid")
        if not success:
            return {"paymentId": payment_id, "success": False, "message": message}
        return {"paymentId": payment_id, "success": True, "status": "paid"}

    def verify_webhook_signature(self, signature, payload):
        return signature == MOCK_WEBHOOK_SECRET

    def is_event_processed(self, event_id):
        return event_id in self._processed_events

    def mark_event_processed(self, event_id):
        self._processed_events[event_id] = {
            "processed": True,
            "timestamp": datetime.now().isoformat(),
        }
