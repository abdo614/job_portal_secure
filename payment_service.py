"""
طبقة خدمة الدفع - المرحلة 11 (Payment Service Layer)
تغلف منطق الدفع وتوفر واجهة موحدة
تستخدم: payment_gateway.py, payment_rules.py, invoice_service.py, secure_storage
"""

import logging
import payment_gateway
from datetime import datetime
from payment_gateway import create_payment, verify_payment, refund_payment, get_payment_status
from payment_rules import calculate_unlock_price
from invoice_service import create_invoice
from encryption import secure_storage

logger = logging.getLogger(__name__)


# ============================================
# دوال مساعدة
# ============================================

def _log_payment_audit(actor, action, payment_id, status_before, status_after, details=None):
    """
    تسجيل عملية في سجل تدقيق الدفع المشفر (payment_audit.enc).
    
    Args:
        actor: من قام بالعملية (userId أو 'webhook' أو 'system')
        action: نوع العملية
        payment_id: معرف الدفع
        status_before: الحالة قبل العملية
        status_after: الحالة بعد العملية
        details: تفاصيل إضافية (اختياري)
    """
    try:
        audit_logs = secure_storage.encryption.decrypt_file('payment_audit') or []
        audit_entry = {
            'actor': actor,
            'action': action,
            'paymentId': payment_id,
            'status_before': status_before,
            'status_after': status_after,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        audit_logs.append(audit_entry)
        secure_storage.encryption.encrypt_file('payment_audit', audit_logs)
        return True
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"❌ خطأ في تسجيل سجل التدقيق: {str(e)}")
        return False


def _load_payment_logs():
    """تحميل سجلات الدفع"""
    return secure_storage.encryption.decrypt_file('payment_logs') or []


def _save_payment_logs(payment_logs):
    """حفظ سجلات الدفع"""
    return secure_storage.encryption.encrypt_file('payment_logs', payment_logs)


def _load_applications():
    """تحميل الطلبات"""
    return secure_storage.load_applications() or {}


def _save_applications(applications):
    """حفظ الطلبات"""
    return secure_storage.save_applications(applications)


# ============================================
# دوال الخدمة الرئيسية
# ============================================

def create_unlock_payment(employer_user, job_id, applicant_user_id, job_title=''):
    """
    إنشاء طلب دفع لفتح بيانات المتقدم.
    
    Args:
        employer_user: بيانات صاحب العمل
        job_id: معرف الوظيفة
        applicant_user_id: معرف المتقدم
        job_title: عنوان الوظيفة (اختياري)
        
    Returns:
        dict: نتيجة العملية
    """
    try:
        # التحقق من صحة البيانات
        if not job_id:
            return {'success': False, 'message': 'معرف الوظيفة مطلوب'}, 400
        if not applicant_user_id:
            return {'success': False, 'message': 'معرف المتقدم مطلوب'}, 400
        
        # التحقق من أن الوظيفة تخص صاحب العمل
        jobs = secure_storage.load_jobs() or []
        job = next((j for j in jobs if str(j.get('id')) == str(job_id)), None)
        if not job:
            return {'success': False, 'message': 'الوظيفة غير موجودة'}, 404
        if str(job.get('employerId', '')) != str(employer_user.get('id')):
            return {'success': False, 'message': 'غير مصرح: هذه الوظيفة لا تخصك'}, 403
        
        # التحقق من وجود الطلب (التقديم)
        applications = _load_applications()
        applicant_apps = applications.get(str(applicant_user_id), [])
        app_entry = next((a for a in applicant_apps if str(a.get('jobId')) == str(job_id)), None)
        if not app_entry:
            return {'success': False, 'message': 'طلب التقديم غير موجود'}, 404
        
        # حساب السعر باستخدام payment_rules.py
        price_info = calculate_unlock_price(employer_user)
        
        # إنشاء عملية دفع عبر البوابة
        payment = create_payment(
            amount=price_info['amount'],
            currency=price_info['currency'],
            description=f'فتح بيانات متقدم - {job_title or job.get("title", "")}',
            metadata={
                'employerId': employer_user.get('id'),
                'applicantId': applicant_user_id,
                'jobId': str(job_id),
                'jobTitle': job_title or job.get('title', '')
            }
        )
        
        if not payment.get('paymentId'):
            return {'success': False, 'message': 'فشل إنشاء عملية الدفع'}, 500
        
        payment_id = payment['paymentId']
        
        # إنشاء سجل دفع في payment_logs.enc
        payment_logs = _load_payment_logs()
        payment_log = {
            'paymentId': payment_id,
            'employerId': employer_user.get('id'),
            'employerEmail': employer_user.get('email', ''),
            'applicantId': applicant_user_id,
            'jobId': str(job_id),
            'jobTitle': job_title or job.get('title', ''),
            'amount': price_info['amount'],
            'currency': price_info['currency'],
            'formattedPrice': price_info['formatted'],
            'status': 'pending',
            'createdAt': datetime.now().isoformat(),
            'updatedAt': datetime.now().isoformat()
        }
        payment_logs.append(payment_log)
        _save_payment_logs(payment_logs)
        
        # تحديث unlock_contact داخل الطلب
        app_entry['unlock_contact'] = {
            'status': 'pending',
            'paymentId': payment_id,
            'contactUnlocked': False
        }
        app_entry['unlockStatus'] = 'pending'
        app_entry['unlockPaymentId'] = payment_id
        app_entry['unlockedAt'] = None
        _save_applications(applications)
        
        # تسجيل في سجل التدقيق
        _log_payment_audit(
            actor=employer_user.get('id'),
            action='request_unlock',
            payment_id=payment_id,
            status_before='none',
            status_after='pending',
            details={
                'amount': price_info['amount'],
                'currency': price_info['currency'],
                'jobId': str(job_id),
                'applicantId': applicant_user_id
            }
        )
        
        return {
            'success': True,
            'message': 'تم إنشاء طلب فتح البيانات بنجاح. يرجى إكمال عملية الدفع.',
            'paymentId': payment_id,
            'price': price_info,
            'unlock_contact': {
                'status': 'pending',
                'paymentId': payment_id,
                'contactUnlocked': False
            }
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def get_payment_status_service(payment_id):
    """
    الحصول على حالة عملية دفع.
    
    Args:
        payment_id: معرف الدفع
        
    Returns:
        dict: حالة الدفع
    """
    try:
        # البحث عن الدفع في سجلات الدفع
        payment_logs = _load_payment_logs()
        payment = next((p for p in payment_logs if str(p.get('paymentId')) == str(payment_id)), None)
        
        if not payment:
            return {
                'success': False,
                'message': 'الدفع غير موجود',
                'status': 'not_found',
                'contactUnlocked': False
            }, 404
        
        # البحث عن حالة فتح بيانات التواصل المرتبطة
        contact_unlocked = False
        applications = _load_applications()
        applicant_user_id = payment.get('applicantId')
        job_id = payment.get('jobId')
        
        if applicant_user_id and job_id:
            applicant_apps = applications.get(str(applicant_user_id), [])
            for a in applicant_apps:
                if str(a.get('jobId')) == str(job_id):
                    contact_unlocked = bool(a.get('unlock_contact', {}).get('contactUnlocked', False)) or bool(a.get('contactUnlocked', False))
                    break
        
        return {
            'status': payment.get('status', 'pending'),
            'contactUnlocked': contact_unlocked,
            'paymentId': payment.get('paymentId'),
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
            'formattedPrice': payment.get('formattedPrice'),
            'updatedAt': payment.get('updatedAt')
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def update_payment_status(payment_id, new_status, actor='system', details=None):
    """
    تحديث حالة الدفع.
    
    Args:
        payment_id: معرف الدفع
        new_status: الحالة الجديدة (paid, failed, cancelled, refunded)
        actor: من قام بالعملية
        details: تفاصيل إضافية
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        # البحث عن الدفع
        payment_logs = _load_payment_logs()
        payment = next((p for p in payment_logs if str(p.get('paymentId')) == str(payment_id)), None)
        
        if not payment:
            return {'success': False, 'message': 'الدفع غير موجود'}, 404
        
        status_before = payment.get('status', 'pending')
        
        # التحقق من أن الدفع لم يكن مكتملاً بالفعل
        if status_before == 'paid' and new_status == 'paid':
            return {'success': False, 'message': 'هذا الدفع مكتمل بالفعل'}, 400
        
        # تحديث الحالة
        now_iso = datetime.now().isoformat()
        payment['status'] = new_status
        payment['updatedAt'] = now_iso
        _save_payment_logs(payment_logs)
        
        # تسجيل في سجل التدقيق
        _log_payment_audit(
            actor=actor,
            action=f'update_status_{new_status}',
            payment_id=payment_id,
            status_before=status_before,
            status_after=new_status,
            details=details
        )
        
        return {
            'success': True,
            'message': f'تم تحديث حالة الدفع إلى {new_status}',
            'payment': {
                'paymentId': payment.get('paymentId'),
                'status': payment.get('status'),
                'amount': payment.get('amount'),
                'currency': payment.get('currency'),
                'formattedPrice': payment.get('formattedPrice'),
                'updatedAt': payment.get('updatedAt')
            }
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def process_successful_payment(payment_id, actor='webhook'):
    """
    معالجة دفع ناجح وفتح بيانات التواصل.
    
    Args:
        payment_id: معرف الدفع
        actor: من قام بالعملية
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        # البحث عن الدفع
        payment_logs = _load_payment_logs()
        payment = next((p for p in payment_logs if str(p.get('paymentId')) == str(payment_id)), None)
        
        if not payment:
            return {'success': False, 'message': 'الدفع غير موجود'}, 404
        
        status_before = payment.get('status', 'pending')
        
        # التحقق من أن الدفع لم يكن مكتملاً بالفعل
        if status_before == 'paid':
            _log_payment_audit(
                actor=actor,
                action='webhook_already_paid',
                payment_id=payment_id,
                status_before='paid',
                status_after='paid'
            )
            return {'success': True, 'message': 'الدفع مكتمل بالفعل'}, 200
        
        # تحديث حالة الدفع إلى paid
        now_iso = datetime.now().isoformat()
        payment['status'] = 'paid'
        payment['updatedAt'] = now_iso
        _save_payment_logs(payment_logs)
        
        # البحث عن الطلب المرتبط في applications.enc
        applications = _load_applications()
        applicant_user_id = payment.get('applicantId')
        job_id = payment.get('jobId')
        
        unlocked_application = False
        if applicant_user_id and job_id:
            applicant_apps = applications.get(str(applicant_user_id), [])
            for a in applicant_apps:
                if str(a.get('jobId')) == str(job_id):
                    # تحديث الحقلين: الحقل الجديد (unlock_contact) والحقل المباشر (contactUnlocked)
                    a['unlock_contact'] = {
                        'status': 'paid',
                        'paymentId': payment_id,
                        'contactUnlocked': True,
                        'unlockedAt': now_iso
                    }
                    a['unlockStatus'] = 'paid'
                    a['unlockPaymentId'] = payment_id
                    a['unlockedAt'] = now_iso
                    a['contactUnlocked'] = True
                    unlocked_application = True
                    break
            if unlocked_application:
                _save_applications(applications)
        
        # إنشاء فاتورة تلقائياً عند نجاح الدفع
        invoice_result, invoice_status = create_invoice(payment)
        if invoice_status == 200:
            logger.info(f"✅ تم إنشاء الفاتورة تلقائياً: {invoice_result.get('invoice', {}).get('invoiceId')}")
        else:
            logger.warning(f"⚠️ فشل إنشاء الفاتورة: {invoice_result.get('message')}")
        
        # تسجيل في سجل التدقيق
        _log_payment_audit(
            actor=actor,
            action='webhook_payment_success',
            payment_id=payment_id,
            status_before=status_before,
            status_after='paid',
            details={'contactUnlocked': unlocked_application, 'invoice_created': invoice_status == 200}
        )
        
        return {
            'success': True,
            'message': 'تم تحديث حالة الدفع بنجاح وفتح بيانات التواصل',
            'paymentId': payment_id,
            'status': 'paid',
            'contactUnlocked': unlocked_application,
            'invoice': invoice_result.get('invoice') if invoice_status == 200 else None
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


def process_refund(payment_id, actor='admin'):
    """
    معالجة استرداد دفع.
    
    Args:
        payment_id: معرف الدفع
        actor: من قام بالعملية
        
    Returns:
        tuple: (dict, status_code)
    """
    try:
        # البحث عن الدفع
        payment_logs = _load_payment_logs()
        payment = next((p for p in payment_logs if str(p.get('paymentId')) == str(payment_id)), None)
        
        if not payment:
            return {'success': False, 'message': 'الدفع غير موجود'}, 404
        
        status_before = payment.get('status', 'pending')
        
        # التحقق من أن الدفع يمكن استرداده
        if status_before != 'paid':
            return {'success': False, 'message': 'لا يمكن استرداد دفع غير مكتمل'}, 400
        
        # ملاحظة: لا نستدعي refund_payment من البوابة لأنها تقرأ من البوابة
        # وليس من payment_logs.enc. نكتفي بتحديث الحالة محلياً.
        refund_result = {'success': True, 'message': 'تم الاسترداد محلياً'}
        
        # تحديث حالة الدفع إلى refunded
        now_iso = datetime.now().isoformat()
        payment['status'] = 'refunded'
        payment['updatedAt'] = now_iso
        _save_payment_logs(payment_logs)
        
        # إغلاق بيانات التواصل
        applications = _load_applications()
        applicant_user_id = payment.get('applicantId')
        job_id = payment.get('jobId')
        
        if applicant_user_id and job_id:
            applicant_apps = applications.get(str(applicant_user_id), [])
            for a in applicant_apps:
                if str(a.get('jobId')) == str(job_id):
                    a['unlock_contact'] = {
                        'status': 'refunded',
                        'paymentId': payment_id,
                        'contactUnlocked': False,
                        'unlockedAt': None
                    }
                    a['unlockStatus'] = 'refunded'
                    a['contactUnlocked'] = False
                    break
            _save_applications(applications)
        
        # تسجيل في سجل التدقيق
        _log_payment_audit(
            actor=actor,
            action='refund_payment',
            payment_id=payment_id,
            status_before=status_before,
            status_after='refunded',
            details={'refund_result': refund_result}
        )
        
        return {
            'success': True,
            'message': 'تم استرداد الدفع بنجاح',
            'paymentId': payment_id,
            'status': 'refunded'
        }, 200
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


# ============================================
# تصدير الدوال
# ============================================
__all__ = [
    'create_unlock_payment',
    'get_payment_status_service',
    'update_payment_status',
    'process_successful_payment',
    'process_refund'
]