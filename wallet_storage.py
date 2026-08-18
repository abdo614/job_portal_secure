"""
تخزين المحفظة الإلكترونية - المرحلة 21A
يدير التخزين المشفر لبيانات المحفظة والمعاملات
يستخدم نظام التشفير الموجود في المشروع
"""

from datetime import datetime
from encryption import secure_storage
import logging

logger = logging.getLogger(__name__)


# ============================================
# دوال مساعدة
# ============================================

def _log_wallet_audit(actor, action, employer_id, balance_before, balance_after, details=None):
    """
    تسجيل عملية في سجل تدقيق المحفظة.
    
    Args:
        actor: من قام بالعملية (userId أو 'system' أو 'admin')
        action: نوع العملية
        employer_id: معرف صاحب العمل
        balance_before: الرصيد قبل العملية
        balance_after: الرصيد بعد العملية
        details: تفاصيل إضافية (اختياري)
    """
    try:
        audit_logs = secure_storage.encryption.decrypt_file('wallet_audit') or []
        audit_entry = {
            'auditId': f"aud_{datetime.now().strftime('%Y%m%d%H%M%S')}_{actor}",
            'employerId': employer_id,
            'action': action,
            'balanceBefore': balance_before,
            'balanceAfter': balance_after,
            'actor': actor,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        audit_logs.append(audit_entry)
        secure_storage.encryption.encrypt_file('wallet_audit', audit_logs)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تسجيل سجل تدقيق المحفظة: {str(e)}")
        return False


# ============================================
# إدارة المحفظة
# ============================================

def get_wallet(employer_id):
    """
    الحصول على بيانات محفظة صاحب العمل.
    إذا لم تكن المحفظة موجودة، يتم إنشاؤها تلقائياً.
    
    Args:
        employer_id: معرف صاحب العمل
        
    Returns:
        dict: بيانات المحفظة
    """
    try:
        wallets = secure_storage.encryption.decrypt_file('wallets') or []
        wallet = next((w for w in wallets if w.get('employerId') == employer_id), None)
        
        if not wallet:
            # إنشاء محفظة جديدة تلقائياً
            wallet = create_wallet(employer_id)
        
        return wallet
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على المحفظة: {str(e)}")
        return None


def create_wallet(employer_id):
    """
    إنشاء محفظة جديدة لصاحب العمل.
    
    Args:
        employer_id: معرف صاحب العمل
        
    Returns:
        dict: بيانات المحفظة الجديدة
    """
    try:
        wallets = secure_storage.encryption.decrypt_file('wallets') or []
        
        # التحقق من عدم وجود محفظة مسبقة
        existing = next((w for w in wallets if w.get('employerId') == employer_id), None)
        if existing:
            return existing
        
        # إنشاء محفظة جديدة
        now = datetime.now().isoformat()
        wallet = {
            'employerId': employer_id,
            'balance': 0,
            'currency': 'USD',
            'formattedBalance': '0.00 USD',
            'totalEarnings': 0,
            'totalWithdrawn': 0,
            'pendingWithdrawals': 0,
            'availableBalance': 0,
            'status': 'active',
            'createdAt': now,
            'updatedAt': now
        }
        
        wallets.append(wallet)
        secure_storage.encryption.encrypt_file('wallets', wallets)
        
        logger.info(f"✅ تم إنشاء محفظة جديدة لصاحب العمل: {employer_id}")
        return wallet
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء المحفظة: {str(e)}")
        return None


def update_wallet(employer_id, updates):
    """
    تحديث بيانات المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        updates: dict بالبيانات المطلوب تحديثها
        
    Returns:
        dict: المحفظة المحدثة أو None في حالة الفشل
    """
    try:
        wallets = secure_storage.encryption.decrypt_file('wallets') or []
        wallet_index = next((i for i, w in enumerate(wallets) if w.get('employerId') == employer_id), None)
        
        if wallet_index is None:
            logger.error(f"المحفظة غير موجودة: {employer_id}")
            return None
        
        # تحديث البيانات
        wallets[wallet_index].update(updates)
        wallets[wallet_index]['updatedAt'] = datetime.now().isoformat()
        
        # حفظ التغييرات
        secure_storage.encryption.encrypt_file('wallets', wallets)
        
        return wallets[wallet_index]
    except Exception as e:
        logger.error(f"❌ خطأ في تحديث المحفظة: {str(e)}")
        return None


# ============================================
# إدارة المعاملات
# ============================================

def create_transaction(employer_id, transaction_type, amount, balance_before, balance_after, 
                       reference_type=None, reference_id=None, invoice_id=None, description=None, metadata=None):
    """
    إنشاء معاملة جديدة في المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        transaction_type: نوع المعاملة (payment_received, refund, withdrawal, etc.)
        amount: مبلغ المعاملة
        balance_before: الرصيد قبل المعاملة
        balance_after: الرصيد بعد المعاملة
        reference_type: نوع المرجع (payment, refund, withdrawal, invoice)
        reference_id: معرف المرجع
        invoice_id: معرف الفاتورة (اختياري)
        description: وصف المعاملة (اختياري)
        metadata: بيانات إضافية (اختياري)
        
    Returns:
        dict: بيانات المعاملة أو None في حالة الفشل
    """
    try:
        from wallet_config import TRANSACTION_TYPES, WALLET_DECIMAL_PLACES
        
        # التحقق من صحة نوع المعاملة
        if transaction_type not in TRANSACTION_TYPES:
            logger.error(f"نوع معاملة غير صالح: {transaction_type}")
            return None
        
        txn_info = TRANSACTION_TYPES[transaction_type]
        
        # إنشاء معرف المعاملة
        transaction_id = f"txn_{datetime.now().strftime('%Y%m%d%H%M%S')}_{employer_id}"
        
        # تنسيق المبلغ
        actual_amount = amount / (10 ** WALLET_DECIMAL_PLACES)
        formatted_amount = f"{actual_amount:.2f} USD"
        
        actual_before = balance_before / (10 ** WALLET_DECIMAL_PLACES)
        actual_after = balance_after / (10 ** WALLET_DECIMAL_PLACES)
        
        # إنشاء المعاملة
        transaction = {
            'transactionId': transaction_id,
            'employerId': employer_id,
            'type': txn_info['type'],
            'amount': amount,
            'currency': 'USD',
            'formattedAmount': formatted_amount,
            'balanceBefore': balance_before,
            'balanceAfter': balance_after,
            'formattedBalanceBefore': f"{actual_before:.2f} USD",
            'formattedBalanceAfter': f"{actual_after:.2f} USD",
            'status': 'completed',
            'referenceType': reference_type,
            'referenceId': reference_id,
            'invoiceId': invoice_id,
            'description': description or txn_info.get('description_ar', ''),
            'metadata': metadata or {},
            'createdAt': datetime.now().isoformat(),
            'updatedAt': datetime.now().isoformat()
        }
        
        # حفظ المعاملة
        transactions = secure_storage.encryption.decrypt_file('wallet_transactions') or []
        transactions.append(transaction)
        secure_storage.encryption.encrypt_file('wallet_transactions', transactions)
        
        # تسجيل في سجل التدقيق
        _log_wallet_audit(
            actor='system',
            action=f'transaction_{transaction_type}',
            employer_id=employer_id,
            balance_before=balance_before,
            balance_after=balance_after,
            details={
                'transactionId': transaction_id,
                'amount': amount,
                'type': transaction_type,
                'referenceId': reference_id
            }
        )
        
        logger.info(f"✅ تم إنشاء معاملة: {transaction_id} - {transaction_type} - {formatted_amount}")
        return transaction
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء المعاملة: {str(e)}")
        return None


def get_transactions(employer_id, limit=50, offset=0, transaction_type=None):
    """
    الحصول على قائمة المعاملات لصاحب العمل.
    
    Args:
        employer_id: معرف صاحب العمل
        limit: عدد النتائج
        offset: إزاحة للترقيم
        transaction_type: تصفية حسب النوع (اختياري)
        
    Returns:
        list: قائمة المعاملات
    """
    try:
        transactions = secure_storage.encryption.decrypt_file('wallet_transactions') or []
        
        # تصفية حسب صاحب العمل
        filtered = [t for t in transactions if t.get('employerId') == employer_id]
        
        # تصفية حسب النوع
        if transaction_type:
            from wallet_config import TRANSACTION_TYPES
            txn_info = TRANSACTION_TYPES.get(transaction_type, {})
            txn_direction = txn_info.get('type')
            filtered = [t for t in filtered if t.get('type') == txn_direction]
        
        # ترتيب حسب التاريخ (الأحدث أولاً)
        filtered.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        
        # تطبيق الحد والإزاحة
        return filtered[offset:offset + limit]
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على المعاملات: {str(e)}")
        return []


def get_transaction_by_id(transaction_id):
    """
    الحصول على معاملة بالمعرف.
    
    Args:
        transaction_id: معرف المعاملة
        
    Returns:
        dict: بيانات المعاملة أو None
    """
    try:
        transactions = secure_storage.encryption.decrypt_file('wallet_transactions') or []
        return next((t for t in transactions if t.get('transactionId') == transaction_id), None)
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على المعاملة: {str(e)}")
        return None


# ============================================
# دوال الرصيد
# ============================================

def credit_wallet(employer_id, amount, transaction_type, reference_id=None, 
                  invoice_id=None, description=None, metadata=None):
    """
    إضافة رصيد إلى المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        amount: المبلغ (بالوحدة الصغرى)
        transaction_type: نوع المعاملة
        reference_id: معرف المرجع (اختياري)
        invoice_id: معرف الفاتورة (اختياري)
        description: وصف المعاملة (اختياري)
        metadata: بيانات إضافية (اختياري)
        
    Returns:
        tuple: (success, result)
    """
    try:
        from wallet_rules import validate_credit_amount, calculate_new_balance
        
        # التحقق من صحة المبلغ
        is_valid, error = validate_credit_amount(amount)
        if not is_valid:
            return False, error
        
        # الحصول على المحفظة
        wallet = get_wallet(employer_id)
        if not wallet:
            return False, "المحفظة غير موجودة"
        
        current_balance = wallet.get('balance', 0)
        
        # حساب الرصيد الجديد
        new_balance, error = calculate_new_balance(current_balance, amount, transaction_type)
        if new_balance is None:
            return False, error
        
        # إنشاء المعاملة
        transaction = create_transaction(
            employer_id=employer_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=current_balance,
            balance_after=new_balance,
            reference_type=reference_id.split('_')[0] if reference_id else None,
            reference_id=reference_id,
            invoice_id=invoice_id,
            description=description,
            metadata=metadata
        )
        
        if not transaction:
            return False, "فشل في إنشاء المعاملة"
        
        # تحديث المحفظة
        updates = {
            'balance': new_balance,
            'availableBalance': new_balance - wallet.get('pendingWithdrawals', 0),
            'totalEarnings': wallet.get('totalEarnings', 0) + amount,
            'formattedBalance': f"{new_balance / 100:.2f} USD"
        }
        
        updated_wallet = update_wallet(employer_id, updates)
        if not updated_wallet:
            return False, "فشل في تحديث المحفظة"
        
        return True, {
            'success': True,
            'transaction': transaction,
            'wallet': updated_wallet
        }
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة الرصيد: {str(e)}")
        return False, str(e)


def debit_wallet(employer_id, amount, transaction_type, reference_id=None,
                 invoice_id=None, description=None, metadata=None):
    """
    خصم رصيد من المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        amount: المبلغ (بالوحدة الصغرى)
        transaction_type: نوع المعاملة
        reference_id: معرف المرجع (اختياري)
        invoice_id: معرف الفاتورة (اختياري)
        description: وصف المعاملة (اختياري)
        metadata: بيانات إضافية (اختياري)
        
    Returns:
        tuple: (success, result)
    """
    try:
        from wallet_rules import validate_debit_amount, calculate_new_balance
        
        # الحصول على المحفظة
        wallet = get_wallet(employer_id)
        if not wallet:
            return False, "المحفظة غير موجودة"
        
        current_balance = wallet.get('balance', 0)
        
        # التحقق من صحة المبلغ
        is_valid, error = validate_debit_amount(amount, current_balance)
        if not is_valid:
            return False, error
        
        # حساب الرصيد الجديد
        new_balance, error = calculate_new_balance(current_balance, amount, transaction_type)
        if new_balance is None:
            return False, error
        
        # إنشاء المعاملة
        transaction = create_transaction(
            employer_id=employer_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=current_balance,
            balance_after=new_balance,
            reference_type=reference_id.split('_')[0] if reference_id else None,
            reference_id=reference_id,
            invoice_id=invoice_id,
            description=description,
            metadata=metadata
        )
        
        if not transaction:
            return False, "فشل في إنشاء المعاملة"
        
        # تحديث المحفظة
        updates = {
            'balance': new_balance,
            'availableBalance': new_balance - wallet.get('pendingWithdrawals', 0),
            'formattedBalance': f"{new_balance / 100:.2f} USD"
        }
        
        # تحديث إجمالي المسحوب إذا كانت المعاملة سحب
        if transaction_type == 'withdrawal':
            updates['totalWithdrawn'] = wallet.get('totalWithdrawn', 0) + amount
        
        updated_wallet = update_wallet(employer_id, updates)
        if not updated_wallet:
            return False, "فشل في تحديث المحفظة"
        
        return True, {
            'success': True,
            'transaction': transaction,
            'wallet': updated_wallet
        }
    except Exception as e:
        logger.error(f"❌ خطأ في خصم الرصيد: {str(e)}")
        return False, str(e)