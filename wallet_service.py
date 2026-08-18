"""
خدمة المحفظة الإلكترونية - المرحلة 21A
الطبقة الرئيسية لإدارة المحفظة
توفر واجهة موحدة للتعامل مع المحفظة
"""

from datetime import datetime
from wallet_storage import (
    get_wallet, create_wallet, update_wallet,
    create_transaction, get_transactions, get_transaction_by_id,
    credit_wallet, debit_wallet
)
from wallet_rules import (
    validate_balance, validate_credit_amount, validate_debit_amount,
    calculate_new_balance, format_balance
)
import logging

logger = logging.getLogger(__name__)


# ============================================
# دوال المحفظة الرئيسية
# ============================================

def get_wallet_balance(employer_id):
    """
    الحصول على رصيد المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        
    Returns:
        dict: بيانات الرصيد
    """
    try:
        wallet = get_wallet(employer_id)
        if not wallet:
            return {
                'success': False,
                'message': 'المحفظة غير موجودة'
            }
        
        available = float(wallet.get('availableBalance', 0) or 0)
        balance = float(wallet.get('balance', 0) or 0)
        # المحفظة الداخلية موحدة بالسنت الأمريكي؛ لا نغيّر الرصيد المخزن هنا.
        return {
            'success': True,
            'balance': {
                'amount': wallet.get('balance', 0),
                # المحفظة الداخلية موحدة بالدولار (الوحدة الصغرى = سنت).
                'currency': 'USD',
                'formatted': f"{balance / 100.0:.2f} USD",
                'available': wallet.get('availableBalance', 0),
                'totalEarnings': wallet.get('totalEarnings', 0),
                'totalWithdrawn': wallet.get('totalWithdrawn', 0),
                'pendingWithdrawals': wallet.get('pendingWithdrawals', 0)
            }
        }
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على الرصيد: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


def create_employer_wallet(employer_id):
    """
    إنشاء محفظة لصاحب عمل جديد.
    
    Args:
        employer_id: معرف صاحب العمل
        
    Returns:
        dict: نتيجة العملية
    """
    try:
        wallet = create_wallet(employer_id)
        if not wallet:
            return {
                'success': False,
                'message': 'فشل في إنشاء المحفظة'
            }
        
        return {
            'success': True,
            'message': 'تم إنشاء المحفظة بنجاح',
            'wallet': wallet
        }
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء محفظة صاحب العمل: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


# ============================================
# دوال المعاملات
# ============================================

def add_balance(employer_id, amount, transaction_type, reference_id=None,
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
        dict: نتيجة العملية
    """
    try:
        success, result = credit_wallet(
            employer_id=employer_id,
            amount=amount,
            transaction_type=transaction_type,
            reference_id=reference_id,
            invoice_id=invoice_id,
            description=description,
            metadata=metadata
        )
        
        if success:
            return {
                'success': True,
                'message': 'تم إضافة الرصيد بنجاح',
                'transaction': result.get('transaction'),
                'wallet': result.get('wallet')
            }
        else:
            return {
                'success': False,
                'message': result
            }
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة الرصيد: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


def subtract_balance(employer_id, amount, transaction_type, reference_id=None,
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
        dict: نتيجة العملية
    """
    try:
        success, result = debit_wallet(
            employer_id=employer_id,
            amount=amount,
            transaction_type=transaction_type,
            reference_id=reference_id,
            invoice_id=invoice_id,
            description=description,
            metadata=metadata
        )
        
        if success:
            return {
                'success': True,
                'message': 'تم خصم الرصيد بنجاح',
                'transaction': result.get('transaction'),
                'wallet': result.get('wallet')
            }
        else:
            return {
                'success': False,
                'message': result
            }
    except Exception as e:
        logger.error(f"❌ خطأ في خصم الرصيد: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


def get_wallet_transactions(employer_id, limit=50, offset=0, transaction_type=None):
    """
    الحصول على قائمة المعاملات.
    
    Args:
        employer_id: معرف صاحب العمل
        limit: عدد النتائج
        offset: إزاحة
        transaction_type: تصفية حسب النوع (اختياري)
        
    Returns:
        dict: قائمة المعاملات
    """
    try:
        transactions = get_transactions(employer_id, limit, offset, transaction_type)
        
        return {
            'success': True,
            'transactions': transactions,
            'count': len(transactions)
        }
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على المعاملات: {str(e)}")
        return {
            'success': False,
            'message': str(e),
            'transactions': []
        }


def get_transaction_details(transaction_id):
    """
    الحصول على تفاصيل معاملة.
    
    Args:
        transaction_id: معرف المعاملة
        
    Returns:
        dict: بيانات المعاملة
    """
    try:
        transaction = get_transaction_by_id(transaction_id)
        if not transaction:
            return {
                'success': False,
                'message': 'المعاملة غير موجودة'
            }
        
        return {
            'success': True,
            'transaction': transaction
        }
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على تفاصيل المعاملة: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


# ============================================
# دوال المسحوبات (مؤجلة - بدون تنفيذ)
# ============================================

def request_withdrawal(employer_id, amount, bank_details):
    """
    طلب سحب من المحفظة.
    
    ملاحظة: هذه الدالة غير مفعّلة حالياً.
    ستُنفّذ في المرحلة التالية مع نظام السحوبات.
    
    Args:
        employer_id: معرف صاحب العمل
        amount: مبلغ السحب
        bank_details: بيانات الحساب البنكي
        
    Returns:
        dict: نتيجة العملية
    """
    return {
        'success': False,
        'message': 'نظام السحوبات غير متاح حالياً'
    }


def approve_withdrawal(request_id, admin_id):
    """
    الموافقة على طلب سحب.
    
    ملاحظة: هذه الدالة غير مفعّلة حالياً.
    
    Returns:
        dict: نتيجة العملية
    """
    return {
        'success': False,
        'message': 'نظام السحوبات غير متاح حالياً'
    }


def reject_withdrawal(request_id, admin_id, reason):
    """
    رفض طلب سحب.
    
    ملاحظة: هذه الدالة غير مفعّلة حالياً.
    
    Returns:
        dict: نتيجة العملية
    """
    return {
        'success': False,
        'message': 'نظام السحوبات غير متاح حالياً'
    }


# ============================================
# دوال التكامل مع أنظمة الدفع
# ============================================

def on_payment_success(payment_data):
    """
    معالجة نجاح الدفع وإضافة الرصيد للمحفظة.
    
    Args:
        payment_data: بيانات الدفع الناجح
        
    Returns:
        dict: نتيجة العملية
    """
    try:
        employer_id = payment_data.get('employerId')
        amount = payment_data.get('amount')
        payment_id = payment_data.get('paymentId')
        
        if not employer_id or not amount or not payment_id:
            logger.error("بيانات الدفع غير مكتملة")
            return {
                'success': False,
                'message': 'بيانات الدفع غير مكتملة'
            }
        
        # إضافة الرصيد للمحفظة
        result = add_balance(
            employer_id=employer_id,
            amount=amount,
            transaction_type='payment_received',
            reference_id=payment_id,
            description=f'Payment received: {payment_id}',
            metadata={
                'paymentId': payment_id,
                'jobId': payment_data.get('jobId'),
                'applicantId': payment_data.get('applicantId')
            }
        )
        
        if result.get('success'):
            logger.info(f"✅ تم إضافة الرصيد للمحفظة: {employer_id} - {amount}")
        else:
            logger.error(f"❌ فشل في إضافة الرصيد: {result.get('message')}")
        
        return result
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة نجاح الدفع: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


def on_refund_processed(payment_data):
    """
    معالجة الاسترداد وخصم الرصيد من المحفظة.
    
    Args:
        payment_data: بيانات الاسترداد
        
    Returns:
        dict: نتيجة العملية
    """
    try:
        employer_id = payment_data.get('employerId')
        amount = payment_data.get('amount')
        payment_id = payment_data.get('paymentId')
        
        if not employer_id or not amount or not payment_id:
            logger.error("بيانات الاسترداد غير مكتملة")
            return {
                'success': False,
                'message': 'بيانات الاسترداد غير مكتملة'
            }
        
        # خصم الرصيد من المحفظة
        result = subtract_balance(
            employer_id=employer_id,
            amount=amount,
            transaction_type='refund',
            reference_id=payment_id,
            description=f'Refund processed: {payment_id}',
            metadata={
                'paymentId': payment_id,
                'jobId': payment_data.get('jobId'),
                'applicantId': payment_data.get('applicantId')
            }
        )
        
        if result.get('success'):
            logger.info(f"✅ تم خصم الرصيد من المحفظة: {employer_id} - {amount}")
        else:
            logger.error(f"❌ فشل في خصم الرصيد: {result.get('message')}")
        
        return result
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الاسترداد: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


# ============================================
# دوال الإحصائيات
# ============================================

def get_wallet_stats(employer_id):
    """
    الحصول على إحصائيات المحفظة.
    
    Args:
        employer_id: معرف صاحب العمل
        
    Returns:
        dict: الإحصائيات
    """
    try:
        wallet = get_wallet(employer_id)
        if not wallet:
            return {
                'success': False,
                'message': 'المحفظة غير موجودة'
            }
        
        transactions = get_transactions(employer_id, limit=100)
        
        # حساب الإحصائيات
        total_credits = sum(t.get('amount', 0) for t in transactions if t.get('type') == 'credit')
        total_debits = sum(t.get('amount', 0) for t in transactions if t.get('type') == 'debit')
        transaction_count = len(transactions)
        
        return {
            'success': True,
            'stats': {
                'currentBalance': wallet.get('balance', 0),
                'formattedBalance': wallet.get('formattedBalance', '0.00 ر.س'),
                'totalEarnings': wallet.get('totalEarnings', 0),
                'totalWithdrawn': wallet.get('totalWithdrawn', 0),
                'availableBalance': wallet.get('availableBalance', 0),
                'totalCredits': total_credits,
                'totalDebits': total_debits,
                'transactionCount': transaction_count
            }
        }
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على الإحصائيات: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


# ============================================
# تصدير الدوال
# ============================================

__all__ = [
    'get_wallet_balance',
    'create_employer_wallet',
    'add_balance',
    'subtract_balance',
    'get_wallet_transactions',
    'get_transaction_details',
    'request_withdrawal',
    'approve_withdrawal',
    'reject_withdrawal',
    'on_payment_success',
    'on_refund_processed',
    'get_wallet_stats'
]