"""
واجهة موحدة لمزودي الدفع - المرحلة 9
تحدد الدوال التي يجب أن ينفذها كل مزود دفع
"""

from abc import ABC, abstractmethod


class BasePaymentProvider(ABC):
    """
    واجهة موحدة لمزودي الدفع.
    كل مزود دفع (Mock أو حقيقي) يجب أن ينفذ هذه الدوال.
    """

    @abstractmethod
    def create_payment(self, amount, currency='SAR', description='', metadata=None):
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
        pass

    @abstractmethod
    def verify_payment(self, payment_id):
        """
        التحقق من حالة عملية دفع.

        Args:
            payment_id: معرف عملية الدفع

        Returns:
            dict: حالة عملية الدفع
        """
        pass

    @abstractmethod
    def refund_payment(self, payment_id):
        """
        استرداد عملية دفع.

        Args:
            payment_id: معرف عملية الدفع

        Returns:
            dict: نتيجة الاسترداد
        """
        pass

    @abstractmethod
    def get_payment_status(self, payment_id):
        """
        الحصول على حالة عملية دفع.

        Args:
            payment_id: معرف عملية الدفع

        Returns:
            dict: حالة عملية الدفع
        """
        pass