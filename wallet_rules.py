"""
قواعد منطق المحفظة الإلكترونية - المرحلة 21A
يحتوي على جميع القواعد والتحققات الخاصة بالمحفظة
"""

from wallet_config import (
    MIN_WITHDRAWAL_AMOUNT, MAX_WITHDRAWAL_AMOUNT,
    WITHDRAWAL_FEE_PERCENTAGE, WITHDRAWAL_FEE_PERCENTAGE_LOW,
    MIN_BALANCE, MAX_BALANCE, MAX_TRANSACTION_AMOUNT,
    MAX_DAILY_WITHDRAWAL, MAX_MONTHLY_WITHDRAWAL
)

# ============================================
# قواعد التحقق من الرصيد
# ============================================

def validate_balance(balance):
    """
    التحقق من صحة الرصيد.
    
    Args:
        balance: الرصيد الحالي
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if balance < MIN_BALANCE:
        return False, f"الرصيد لا يمكن أن يكون أقل من {MIN_BALANCE}"
    
    if balance > MAX_BALANCE:
        return False, f"الرصيد يتجاوز الحد الأقصى {MAX_BALANCE}"
    
    return True, "OK"


def validate_credit_amount(amount):
    """
    التحقق من صحة مبلغ الإضافة.
    
    Args:
        amount: المبلغ المراد إضافته
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if amount <= 0:
        return False, "مبلغ الإضافة يجب أن يكون أكبر من صفر"
    
    if amount > MAX_TRANSACTION_AMOUNT:
        return False, f"مبلغ الإضافة يتجاوز الحد الأقصى {MAX_TRANSACTION_AMOUNT}"
    
    return True, "OK"


def validate_debit_amount(amount, current_balance):
    """
    التحقق من صحة مبلغ الخصم.
    
    Args:
        amount: المبلغ المراد خصمه
        current_balance: الرصيد الحالي
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if amount <= 0:
        return False, "مبلغ الخصم يجب أن يكون أكبر من صفر"
    
    if amount > MAX_TRANSACTION_AMOUNT:
        return False, f"مبلغ الخصم يتجاوز الحد الأقصى {MAX_TRANSACTION_AMOUNT}"
    
    if amount > current_balance:
        return False, "الرصيد غير كافٍ"
    
    return True, "OK"


# ============================================
# قواعد السحب
# ============================================

def can_withdraw(employer_id, amount, wallet_data, withdrawal_history=None):
    """
    التحقق من إمكانية تنفيذ عملية السحب.
    
    Args:
        employer_id: معرف صاحب العمل
        amount: مبلغ السحب
        wallet_data: بيانات المحفظة
        withdrawal_history: سجل السحوبات (اختياري)
        
    Returns:
        tuple: (can_withdraw, error_message)
    """
    # التحقق من الرصيد المتاح
    available_balance = wallet_data.get('availableBalance', 0)
    if amount > available_balance:
        return False, "الرصيد المتاح غير كافٍ"
    
    # التحقق من الحد الأدنى للسحب
    if amount < MIN_WITHDRAWAL_AMOUNT:
        return False, f"الحد الأدنى للسحب هو {MIN_WITHDRAWAL_AMOUNT}"
    
    # التحقق من الحد الأقصى للسحب
    if amount > MAX_WITHDRAWAL_AMOUNT:
        return False, f"الحد الأقصى للسحب هو {MAX_WITHDRAWAL_AMOUNT}"
    
    # التحقق من عدم وجود طلبات سحب معلقة
    pending_withdrawals = wallet_data.get('pendingWithdrawals', 0)
    if pending_withdrawals > 0:
        return False, "لديك طلبات سحب معلقة. يرجى انتظار الموافقة عليها"
    
    # التحقق من الحد اليومي (إذا كان هناك سجل)
    if withdrawal_history:
        from datetime import datetime
        today = datetime.now().date()
        daily_withdrawn = sum(
            w.get('amount', 0) for w in withdrawal_history
            if datetime.fromisoformat(w.get('createdAt', '')).date() == today
            and w.get('status') == 'completed'
        )
        
        if daily_withdrawn + amount > MAX_DAILY_WITHDRAWAL:
            return False, f"تجاوزت الحد اليومي للسحب ({MAX_DAILY_WITHDRAWAL})"
    
    return True, "OK"


def calculate_withdrawal_fee(amount):
    """
    حساب رسوم السحب.
    
    Args:
        amount: مبلغ السحب
        
    Returns:
        float: قيمة الرسوم
    """
    if amount >= 1000:  # 10 SAR or more
        return int(amount * WITHDRAWAL_FEE_PERCENTAGE)
    else:
        return int(amount * WITHDRAWAL_FEE_PERCENTAGE_LOW)


def calculate_net_withdrawal(amount):
    """
    حساب المبلغ الصافي بعد خصم الرسوم.
    
    Args:
        amount: مبلغ السحب
        
    Returns:
        tuple: (net_amount, fee)
    """
    fee = calculate_withdrawal_fee(amount)
    net_amount = amount - fee
    return net_amount, fee


# ============================================
# قواعد التحقق من المعاملات
# ============================================

def validate_transaction_type(transaction_type):
    """
    التحقق من صحة نوع المعاملة.
    
    Args:
        transaction_type: نوع المعاملة
        
    Returns:
        bool: هل النوع صحيح؟
    """
    from wallet_config import TRANSACTION_TYPES
    return transaction_type in TRANSACTION_TYPES


def get_transaction_info(transaction_type):
    """
    الحصول على معلومات نوع المعاملة.
    
    Args:
        transaction_type: نوع المعاملة
        
    Returns:
        dict: معلومات المعاملة
    """
    from wallet_config import TRANSACTION_TYPES
    return TRANSACTION_TYPES.get(transaction_type, {})


def is_auto_process_transaction(transaction_type):
    """
    التحقق مما إذا كانت المعاملة تتم تلقائياً.
    
    Args:
        transaction_type: نوع المعاملة
        
    Returns:
        bool: هل تتم تلقائياً؟
    """
    info = get_transaction_info(transaction_type)
    return info.get('auto_process', False)


# ============================================
# قواعد حساب الرصيد
# ============================================

def calculate_new_balance(current_balance, amount, transaction_type):
    """
    حساب الرصيد الجديد بعد المعاملة.
    
    Args:
        current_balance: الرصيد الحالي
        amount: مبلغ المعاملة
        transaction_type: نوع المعاملة
        
    Returns:
        tuple: (new_balance, error_message)
    """
    from wallet_config import TRANSACTION_TYPES
    
    if transaction_type not in TRANSACTION_TYPES:
        return None, "نوع معاملة غير صالح"
    
    txn_info = TRANSACTION_TYPES[transaction_type]
    txn_direction = txn_info.get('type')
    
    if txn_direction == 'credit':
        new_balance = current_balance + amount
    elif txn_direction == 'debit':
        new_balance = current_balance - amount
    else:  # credit_debit (adjustment)
        # للتعديلات، نحدد الاتجاه يدوياً
        new_balance = current_balance + amount  # أو نطرح بناءً على السياق
    
    # التحقق من صحة الرصيد الجديد
    is_valid, error = validate_balance(new_balance)
    if not is_valid:
        return None, error
    
    return new_balance, "OK"


# ============================================
# قواعد الأمان
# ============================================

def detect_suspicious_activity(employer_id, amount, transaction_type, transaction_history):
    """
    الكشف عن النشاط المشبوه.
    
    Args:
        employer_id: معرف صاحب العمل
        amount: مبلغ المعاملة
        transaction_type: نوع المعاملة
        transaction_history: سجل المعاملات
        
    Returns:
        tuple: (is_suspicious, reason)
    """
    from datetime import datetime, timedelta
    
    # قاعدة 1: مبلغ كبير جداً
    if amount > 50000:  # 500 SAR
        return True, "مبلغ كبير غير معتاد"
    
    # قاعدة 2: عدة معاملات كبيرة في فترة قصيرة
    one_hour_ago = datetime.now() - timedelta(hours=1)
    recent_large = sum(
        t.get('amount', 0) for t in transaction_history
        if datetime.fromisoformat(t.get('createdAt', '')) > one_hour_ago
        and t.get('amount', 0) > 10000
    )
    
    if recent_large > 100000:  # 1000 SAR in one hour
        return True, "عدة معاملات كبيرة في فترة قصيرة"
    
    # قاعدة 3: محاولة سحب كامل الرصيد
    if transaction_type == 'withdrawal':
        # نتحقق من ذلك في مكان آخر
        pass
    
    return False, "OK"


# ============================================
# قواعد التنسيق
# ============================================

def format_balance(amount, currency='SAR', symbol='ر.س'):
    """
    تنسيق الرصيد للعرض.
    
    Args:
        amount: المبلغ (بالوحدة الصغرى)
        currency: رمز العملة
        symbol: رمز العملة المعروض
        
    Returns:
        str: الرصيد المنسق
    """
    from wallet_config import WALLET_DECIMAL_PLACES
    actual_amount = amount / (10 ** WALLET_DECIMAL_PLACES)
    return f"{actual_amount:.2f} {symbol}"


def parse_balance(formatted_balance):
    """
    تحليل الرصيد المنسق إلى وحدة صغرى.
    
    Args:
        formatted_balance: الرصيد المنسق (مثل "15.00 SAR")
        
    Returns:
        int: الرصيد بالوحدة الصغرى
    """
    from wallet_config import WALLET_DECIMAL_PLACES
    try:
        parts = formatted_balance.split()
        amount = float(parts[0])
        return int(amount * (10 ** WALLET_DECIMAL_PLACES))
    except:
        return 0