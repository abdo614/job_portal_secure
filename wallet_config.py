"""
إعدادات المحفظة الإلكترونية - المرحلة 21A
يحتوي على جميع الإعدادات والثوابت الخاصة بالمحفظة
"""

import os

# ============================================
# إعدادات العملة
# ============================================
WALLET_CURRENCY = os.getenv('WALLET_CURRENCY', 'SAR')
WALLET_CURRENCY_SYMBOL = 'ر.س'
WALLET_DECIMAL_PLACES = 2

# ============================================
# رسوم نشر الوظائق - المرحلة 22A
# ============================================
JOB_POSTING_FEE = 500  # 5.00 ريال سعودي (بالوحدة الصغرى)
JOB_POSTING_FEE_CURRENCY = 'SAR'
JOB_POSTING_FEE_FORMATTED = '5.00 ر.س'
# إطار زمني لمنع الخصم المكرر (بالثواني)
JOB_POSTING_IDEMPOTENCY_WINDOW = 60

# وقفة الخير: أول 3 عمليات فتح بيانات التواصل مجانية، ثم تُستخدم المحفظة
SADAQAH_FREE_UNLOCKS = int(os.getenv('SADAQAH_FREE_UNLOCKS', '3'))
CONTACT_UNLOCK_FEE = int(os.getenv('CONTACT_UNLOCK_FEE', '500'))  # 5.00 ر.س بالوحدة الصغرى


# ============================================
# حدود السحب
# ============================================
MIN_WITHDRAWAL_AMOUNT = 100  # 1 SAR (بالوحدة الصغرى)
MAX_WITHDRAWAL_AMOUNT = 100000  # 1000 SAR
WITHDRAWAL_FEE_PERCENTAGE = 0.01  # 1% for amounts >= 1000
WITHDRAWAL_FEE_PERCENTAGE_LOW = 0.02  # 2% for amounts < 1000

# ============================================
# حدود الرصيد
# ============================================
MIN_BALANCE = 0
MAX_BALANCE = 1000000000  # حد أعلى موسع يدعم أرصدة تجريبية مستقبلية

# ============================================
# حدود المعاملات
# ============================================
MAX_DAILY_WITHDRAWAL = 50000  # 500 SAR
MAX_MONTHLY_WITHDRAWAL = 500000  # 5000 SAR
MAX_TRANSACTION_AMOUNT = 100000000  # حد أعلى موسع للشحن التجريبي ورسوم الخدمات

# ============================================
# إعدادات الأمان
# ============================================
REQUIRE_ADMIN_APPROVAL_FOR_WITHDRAWAL = True
AUTO_APPROVE_THRESHOLD = 1000  # Auto-approve withdrawals under 10 SAR

# ============================================
# إعدادات الإشعارات
# ============================================
SEND_WALLET_NOTIFICATIONS = True
SEND_WITHDRAWAL_NOTIFICATIONS = True
SEND_BALANCE_ALERTS = True
BALANCE_ALERT_THRESHOLD = 100  # Alert when balance drops below 1 SAR

# ============================================
# أنواع المعاملات
# ============================================
TRANSACTION_TYPES = {
    'payment_received': {
        'type': 'credit',
        'description_ar': 'دفعة مستلمة',
        'description_en': 'Payment received',
        'auto_process': True
    },
    'refund': {
        'type': 'debit',
        'description_ar': 'استرداد',
        'description_en': 'Refund',
        'auto_process': True
    },
    'withdrawal': {
        'type': 'debit',
        'description_ar': 'سحب',
        'description_en': 'Withdrawal',
        'auto_process': False
    },
    'withdrawal_fee': {
        'type': 'debit',
        'description_ar': 'رسوم سحب',
        'description_en': 'Withdrawal fee',
        'auto_process': True
    },
    'adjustment': {
        'type': 'credit_debit',
        'description_ar': 'تعديل',
        'description_en': 'Adjustment',
        'auto_process': False
    },
    'bonus': {
        'type': 'credit',
        'description_ar': 'مكافأة',
        'description_en': 'Bonus',
        'auto_process': False
    },
    'penalty': {
        'type': 'debit',
        'description_ar': 'غرامة',
        'description_en': 'Penalty',
        'auto_process': False
    },
    'application': {'type': 'debit', 'description_ar': 'رسوم التقديم على وظيفة', 'description_en': 'Application fee', 'auto_process': True},
    'contact_unlock': {'type': 'debit', 'description_ar': 'رسوم فتح بيانات المتقدم', 'description_en': 'Contact unlock fee', 'auto_process': True},
    'job_posting': {
        'type': 'debit',
        'description_ar': 'رسوم نشر وظيقة',
        'description_en': 'Job posting fee',
        'auto_process': True
    }
}

# ============================================
# حالات المحفظة
# ============================================
WALLET_STATUSES = {
    'active': 'نشطة',
    'suspended': 'معلقة',
    'closed': 'مغلقة'
}

# ============================================
# حالات المعاملات
# ============================================
TRANSACTION_STATUSES = {
    'pending': 'قيد الانتظار',
    'completed': 'مكتملة',
    'failed': 'فاشلة',
    'cancelled': 'ملغاة',
    'rejected': 'مرفوضة'
}

# ============================================
# حالات طلبات السحب
# ============================================
WITHDRAWAL_STATUSES = {
    'pending': 'قيد المراجعة',
    'approved': 'موافق عليه',
    'rejected': 'مرفوض',
    'completed': 'مكتمل',
    'cancelled': 'ملغى'
}
