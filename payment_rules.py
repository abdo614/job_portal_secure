"""
قواعد صلاحيات الدفع - المرحلة 7
يحتوي على دوال التحقق من صلاحيات الدفع للمستخدمين
مع فصل أسعار التجربة عن الأسعار النهائية ودعم العملات
"""

# ============================================
# إصدار قواعد الدفع
# ============================================
PAYMENT_RULES_VERSION = "1.0.0"

# ============================================
# العملات المدعومة
# ============================================
SUPPORTED_CURRENCIES = {
    'SAR': 'ريال سعودي',
    'AED': 'درهم إماراتي',
    'KWD': 'دينار كويتي',
    'QAR': 'ريال قطري',
    'OMR': 'ريال عماني',
    'BHD': 'دينار بحريني',
    'EGP': 'جنيه مصري',
    'JOD': 'دينار أردني',
    'LBP': 'ليرة لبنانية',
    'MAD': 'درهم مغربي',
    'DZD': 'دينار جزائري',
    'TND': 'دينار تونسي',
    'LYD': 'دينار ليبي',
    'SDG': 'جنيه سوداني',
    'IQD': 'دينار عراقي',
    'YER': 'ريال يمني'
}

# ============================================
# أسعار التجربة (Trial) - تُستخدم أثناء الاختبار
# ============================================
TRIAL_PRICES = {
    'SAR': 500,   # 5.00 ريال سعودي
    'AED': 500,   # 5.00 درهم إماراتي
    'KWD': 500,   # 5.00 دينار كويتي
    'QAR': 500,   # 5.00 ريال قطري
    'OMR': 500,   # 5.00 ريال عماني
    'BHD': 500,   # 5.00 دينار بحريني
    'EGP': 500,   # 5.00 جنيه مصري
    'JOD': 500,   # 5.00 دينار أردني
    'LBP': 500,   # 5.00 ليرة لبنانية
    'MAD': 500,   # 5.00 درهم مغربي
    'DZD': 500,   # 5.00 دينار جزائري
    'TND': 500,   # 5.00 دينار تونسي
    'LYD': 500,   # 5.00 دينار ليبي
    'SDG': 500,   # 5.00 جنيه سوداني
    'IQD': 500,   # 5.00 دينار عراقي
    'YER': 500    # 5.00 ريال يمني
}

# ============================================
# الأسعار النهائية (Final) - تُستخدم بعد التجربة
# ============================================
FINAL_PRICES = {
    'SAR': 5000,  # 50.00 ريال سعودي
    'AED': 5000,  # 50.00 درهم إماراتي
    'KWD': 5000,  # 50.00 دينار كويتي
    'QAR': 5000,  # 50.00 ريال قطري
    'OMR': 5000,  # 50.00 ريال عماني
    'BHD': 5000,  # 50.00 دينار بحريني
    'EGP': 5000,  # 50.00 جنيه مصري
    'JOD': 5000,  # 50.00 دينار أردني
    'LBP': 5000,  # 50.00 ليرة لبنانية
    'MAD': 5000,  # 50.00 درهم مغربي
    'DZD': 5000,  # 50.00 دينار جزائري
    'TND': 5000,  # 50.00 دينار تونسي
    'LYD': 5000,  # 50.00 دينار ليبي
    'SDG': 5000,  # 50.00 جنيه سوداني
    'IQD': 5000,  # 50.00 دينار عراقي
    'YER': 5000   # 50.00 ريال يمني
}

# العملة الافتراضية
DEFAULT_CURRENCY = 'SAR'

# وضع التجربة - يُفعَّل أثناء الاختبار
TRIAL_MODE = True


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


def get_price_table(currency=None):
    """
    الحصول على جدول الأسعار الحالي (تجربة أو نهائي).
    
    Args:
        currency: العملة المطلوبة (اختياري)
        
    Returns:
        dict: جدول الأسعار أو سعر العملة المحددة
    """
    prices = TRIAL_PRICES if TRIAL_MODE else FINAL_PRICES
    
    if currency:
        currency = str(currency).upper()
        if currency not in SUPPORTED_CURRENCIES:
            currency = DEFAULT_CURRENCY
        return {
            'amount': prices.get(currency, prices[DEFAULT_CURRENCY]),
            'currency': currency,
            'formatted': f"{prices.get(currency, prices[DEFAULT_CURRENCY]) / 100:.2f} {currency}"
        }
    
    return prices


def calculate_unlock_price(user=None, currency=None):
    """
    حساب سعر فتح بيانات المتقدم (البريد الإلكتروني والهاتف).
    
    Args:
        user: قاموس بيانات المستخدم (اختياري، يستخدم لتحديد الخصم إن وجد)
        currency: العملة المطلوبة (اختياري، الافتراضي SAR)
        
    Returns:
        dict: يحتوي على 'amount' (السعر بالسنت أو الوحدة) و 'currency' (العملة)
    """
    # تحديد العملة
    if currency:
        currency = str(currency).upper()
        if currency not in SUPPORTED_CURRENCIES:
            currency = DEFAULT_CURRENCY
    else:
        currency = DEFAULT_CURRENCY
    
    # اختيار جدول الأسعار حسب الوضع
    prices = TRIAL_PRICES if TRIAL_MODE else FINAL_PRICES
    base_amount = prices.get(currency, prices[DEFAULT_CURRENCY])
    
    # إذا كان المستخدم مدير، قد يحصل على خصم
    if user and user.get('role') == 'admin':
        base_amount = 0  # المدير مجاناً
    
    return {
        'amount': base_amount,
        'currency': currency,
        'formatted': f"{base_amount / 100:.2f} {currency}",
        'mode': 'trial' if TRIAL_MODE else 'final',
        'rules_version': PAYMENT_RULES_VERSION
    }