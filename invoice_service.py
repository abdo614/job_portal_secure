"""
خدمة الفواتير - المرحلة 12 (Invoice Service Layer)
تغلف منطق الفواتير وتوفر واجهة موحدة
تستخدم: secure_storage للتخزين المشفر
"""

import secrets
from datetime import datetime
from encryption import secure_storage


# ============================================
# دوال مساعدة
# ============================================

def _log_invoice_audit(actor, action, invoice_id, details=None):
    """
    تسجيل عملية في سجل تدقيق الفواتير.
    
    Args:
        actor: من قام بالعملية
        action: نوع العملية
        invoice_id: معرف الفاتورة
        details: تفاصيل إضافية
    """
    try:
        audit_logs = secure_storage.encryption.decrypt_file('invoice_audit') or []
        audit_entry = {
            'actor': actor,
            'action': action,
            'invoiceId': invoice_id,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        audit_logs.append(audit_entry)
        secure_storage.encryption.encrypt_file('invoice_audit', audit_logs)
        return True
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"❌ خطأ في تسجيل سجل تدقيق الفواتير: {str(e)}")
        return False


def _load_invoices():
    """تحميل الفواتير"""
    return secure_storage.encryption.decrypt_file('invoices') or []


def _save_invoices(invoices):
    """حفظ الفواتير"""
    return secure_storage.encryption.encrypt_file('invoices', invoices)


# ============================================
# دوال الخدمة الرئيسية
# ============================================

def generate_invoice_number():
    """
    إنشاء رقم فاتورة فريد.
    
    Returns:
        str: رقم الفاتورة
    """
    try:
        # إنشاء رقم فاتورة بتنسيق: INV-YYYYMMDD-XXXXXX
        date_str = datetime.now().strftime('%Y%m%d')
        random_str = datetime.now().strftime('%H%M%S')
        invoice_number = f"INV-{date_str}-{random_str}"
        return invoice_number
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"❌ خطأ في إنشاء رقم الفاتورة: {str(e)}")
        return None


def create_invoice(payment):
    """
    إنشاء فاتورة من بيانات الدفع.
    
    Args:
        payment: بيانات الدفع (dict)
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        # التحقق من البيانات المطلوبة
        required_fields = ['paymentId', 'employerId', 'employerEmail',
                          'amount', 'currency', 'status']
        missing_fields = [f for f in required_fields if f not in payment]
        
        if missing_fields:
            return {'success': False, 'message': f'حقول مفقودة: {", ".join(missing_fields)}'}, 400
        
        # التحقق من عدم وجود فاتورة مسبقة لنفس الدفع
        invoices = _load_invoices()
        existing_invoice = next((inv for inv in invoices if inv.get('paymentId') == payment.get('paymentId')), None)
        
        if existing_invoice:
            return {
                'success': False, 
                'message': 'الفاتورة موجودة بالفعل',
                'invoiceId': existing_invoice.get('invoiceId')
            }, 400
        
        # إنشاء معرف الفاتورة
        invoice_id = f"inv_{secrets.token_urlsafe(16)}"
        
        # إنشاء رقم الفاتورة
        invoice_number = generate_invoice_number()
        
        if not invoice_number:
            return {'success': False, 'message': 'فشل إنشاء رقم الفاتورة'}, 500
        
        # إنشاء الفاتورة
        invoice = {
            'invoiceId': invoice_id,
            'invoiceNumber': invoice_number,
            'paymentId': payment.get('paymentId'),
            'employerId': payment.get('employerId'),
            'employerEmail': payment.get('employerEmail', ''),
            'applicantId': payment.get('applicantId'),
            'jobId': payment.get('jobId'),
            'amount': payment.get('amount'),
            'amountUnit': payment.get('amountUnit', 'major'),
            'currency': payment.get('currency'),
            'invoiceType': payment.get('invoiceType', 'payment'),
            'formattedPrice': payment.get('formattedPrice', ''),
            'status': payment.get('status', 'pending'),
            'createdAt': datetime.now().isoformat()
        }
        
        # حفظ الفاتورة
        invoices.append(invoice)
        _save_invoices(invoices)
        
        # تسجيل في سجل التدقيق
        _log_invoice_audit(
            actor=payment.get('employerId'),
            action='create_invoice',
            invoice_id=invoice_id,
            details={
                'paymentId': payment.get('paymentId'),
                'amount': payment.get('amount'),
                'currency': payment.get('currency')
            }
        )
        
        return {
            'success': True,
            'message': 'تم إنشاء الفاتورة بنجاح',
            'invoice': invoice
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def get_invoice(invoice_id):
    """
    الحصول على فاتورة بالمعرف.
    
    Args:
        invoice_id: معرف الفاتورة
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        invoices = _load_invoices()
        invoice = next((inv for inv in invoices if inv.get('invoiceId') == invoice_id), None)
        
        if not invoice:
            return {'success': False, 'message': 'الفاتورة غير موجودة'}, 404
        
        return {
            'success': True,
            'invoice': invoice
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def list_invoices(filters=None):
    """
    الحصول على قائمة الفواتير مع إمكانية التصفية.
    
    Args:
        filters: معايير التصفية (dict) - اختياري
            - employerId: تصفية حسب صاحب العمل
            - status: تصفية حسب الحالة
            - paymentId: تصفية حسب معرف الدفع
        
    Returns:
        tuple: (list, status_code)
    """
    try:
        invoices = _load_invoices()
        
        # تطبيق التصفية
        if filters:
            if 'employerId' in filters:
                invoices = [inv for inv in invoices if inv.get('employerId') == filters['employerId']]
            if 'status' in filters:
                invoices = [inv for inv in invoices if inv.get('status') == filters['status']]
            if 'paymentId' in filters:
                invoices = [inv for inv in invoices if inv.get('paymentId') == filters['paymentId']]
        
        return {
            'success': True,
            'invoices': invoices,
            'count': len(invoices)
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def get_invoice_by_payment(payment_id):
    """
    الحصول على فاتورة بالمعرف الدفع.
    
    Args:
        payment_id: معرف الدفع
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        invoices = _load_invoices()
        invoice = next((inv for inv in invoices if inv.get('paymentId') == payment_id), None)
        
        if not invoice:
            return {'success': False, 'message': 'الفاتورة غير موجودة'}, 404
        
        return {
            'success': True,
            'invoice': invoice
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


# ============================================
# تصدير الدوال
# ============================================
__all__ = [
    'create_invoice',
    'get_invoice',
    'list_invoices',
    'get_invoice_by_payment',
    'generate_invoice_number'
]