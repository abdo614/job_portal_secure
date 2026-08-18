"""
API المحفظة الإلكترونية - المرحلة 21B
يحتوي على نقاط النهاية (endpoints) للوصول إلى المحفظة
محمي بصلاحيات - كل صاحب عمل يرى محفظته فقط
"""

from flask import Blueprint, request, jsonify, session
from wallet_service import get_wallet_balance, get_wallet_transactions, get_wallet_stats, get_transaction_details
from encryption import secure_storage
from payment_pricing import user_currency, format_local, load_settings as load_pricing_settings
import logging

logger = logging.getLogger(__name__)

# إنشاء Blueprint للمحفظة
wallet_bp = Blueprint('wallet', __name__)


# ============================================
# دوال مساعدة للتحقق من الصلاحيات
# ============================================

def get_current_user_id():
    """
    الحصول على معرف المستخدم الحالي من الجلسة.
    
    Returns:
        str: معرف المستخدم أو None
    """
    user_id = session.get('user_id')
    if not user_id:
        return None
    
    # تحميل المستخدم من التخزين المشفر
    try:
        users = secure_storage.load_users() or []
        user = next((u for u in users if str(u.get('id')) == str(user_id)), None)
        return user.get('id') if user else None
    except Exception:
        return None


def require_wallet_user():
    """
    التحقق من أن المستخدم الحالي يملك حساباً صالحاً للوصول إلى المحفظة (باحث عن عمل أو صاحب عمل أو مدير).
    
    Returns:
        tuple: (is_employer, user_id, error_response)
    """
    user_id = session.get('user_id')
    
    if not user_id:
        return False, None, (jsonify({
            'success': False,
            'message': 'يجب تسجيل الدخول أولاً'
        }), 401)
    
    # تحميل المستخدم من التخزين المشفر
    try:
        users = secure_storage.load_users() or []
    except Exception as exc:
        logger.exception('فشل تحميل المستخدمين أثناء التحقق من المحفظة: %s', exc)
        return False, None, (jsonify({'success': False, 'message': 'تعذر تحميل بيانات الحساب مؤقتًا، حاول تحديث الصفحة'}), 503)

    user = next((u for u in users if str(u.get('id')) == str(user_id)), None)
    
    if not user:
        return False, None, (jsonify({
            'success': False,
            'message': 'المستخدم غير موجود'
        }), 401)
    
    if user.get('role') not in ('user', 'employer', 'job_seeker', 'admin'):
        return False, None, (jsonify({
            'success': False,
            'message': 'غير مصرح: لا يمكن الوصول إلى المحفظة بهذا الحساب'
        }), 403)
    
    return True, user.get('id'), None


# توافق مع الاسم القديم
require_employer = require_wallet_user

# ============================================
# نقاط النهاية (Endpoints)
# ============================================

@wallet_bp.route('/api/wallet/balance', methods=['GET'])
def get_balance():
    """
    الحصول على رصيد المحفظة.
    
    الصلاحيات المطلوبة: مستخدم مسجل (باحث عن عمل أو صاحب عمل أو مدير)
    يُرجع: بيانات الرصيد
    
    الاستجابة الناجحة:
    {
        "success": true,
        "balance": {
            "amount": 1000,
            "currency": "SAR",
            "formatted": "10.00 ر.س",
            "available": 1000,
            "totalEarnings": 1000,
            "totalWithdrawn": 0,
            "pendingWithdrawals": 0
        }
    }
    """
    try:
        # التحقق من الصلاحيات
        is_employer, employer_id, error_response = require_wallet_user()
        if not is_employer:
            return error_response
        
        # الحصول على الرصيد
        result = get_wallet_balance(employer_id)
        
        if result.get('success'):
            # الرصيد الداخلي محفوظ بالدولار (سنت)، لكن العرض للمستخدم يكون
            # بعملة دولته المسجلة في الحساب. لا نغيّر القيمة المحاسبية الداخلية.
            users = secure_storage.load_users() or []
            user = next((u for u in users if str(u.get('id')) == str(employer_id)), {})
            currency = user_currency(user)
            settings = load_pricing_settings(secure_storage)
            rates = settings.get('rates') or {}
            usd_amount = float(result['balance'].get('available', 0) or 0) / 100.0
            local_amount = usd_amount * float(rates.get(currency, 1.0))
            result['balance']['currency'] = currency
            result['balance']['localAmount'] = local_amount
            result['balance']['formattedLocal'] = format_local(local_amount, currency)
            result['balance']['internalCurrency'] = 'USD'
            result['balance']['internalFormatted'] = f"{usd_amount:.2f} USD"
            return jsonify(result), 200
        else:
            return jsonify(result), 404
    
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على الرصيد: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'حدث خطأ في الخادم'
        }), 500


@wallet_bp.route('/api/wallet/transactions', methods=['GET'])
def get_transactions():
    """
    الحصول على سجل المعاملات.
    
    الصلاحيات المطلوبة: مستخدم مسجل (باحث عن عمل أو صاحب عمل أو مدير)
    المعاملات:
    - limit: عدد النتائج (افتراضي 50، أقصى 100)
    - offset: إزاحة للترقيم (افتراضي 0)
    - type: تصفية حسب النوع (اختياري)
    
    الاستجابة الناجحة:
    {
        "success": true,
        "transactions": [...],
        "count": 10
    }
    """
    try:
        # التحقق من الصلاحيات
        is_employer, employer_id, error_response = require_wallet_user()
        if not is_employer:
            return error_response
        
        # الحصول على المعاملات
        limit = min(int(request.args.get('limit', 50)), 100)
        offset = int(request.args.get('offset', 0))
        transaction_type = request.args.get('type')
        
        result = get_wallet_transactions(employer_id, limit, offset, transaction_type)
        
        if result.get('success'):
            users = secure_storage.load_users() or []
            user = next((u for u in users if str(u.get('id')) == str(employer_id)), {})
            currency = user_currency(user)
            rates = load_pricing_settings(secure_storage).get('rates') or {}
            rate = float(rates.get(currency, 1.0))
            for tx in result.get('transactions') or []:
                usd_amount = float(tx.get('amount', 0) or 0) / 100.0
                usd_before = float(tx.get('balanceBefore', 0) or 0) / 100.0
                usd_after = float(tx.get('balanceAfter', 0) or 0) / 100.0
                tx['displayCurrency'] = currency
                tx['localAmount'] = usd_amount * rate
                tx['formattedLocal'] = format_local(usd_amount * rate, currency)
                tx['formattedBalanceBeforeLocal'] = format_local(usd_before * rate, currency)
                tx['formattedBalanceAfterLocal'] = format_local(usd_after * rate, currency)
            result['currency'] = currency
            result['currencyName'] = __import__('payment_pricing').CURRENCY_NAMES.get(currency, currency)
            result['symbol'] = __import__('payment_pricing').CURRENCY_SYMBOLS.get(currency, currency)
            return jsonify(result), 200
        else:
            return jsonify(result), 500
    
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على المعاملات: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'حدث خطأ في الخادم'
        }), 500


@wallet_bp.route('/api/wallet/stats', methods=['GET'])
def get_stats():
    """
    الحصول على إحصائيات المحفظة.
    
    الصلاحيات المطلوبة: مستخدم مسجل (باحث عن عمل أو صاحب عمل أو مدير)
    
    الاستجابة الناجحة:
    {
        "success": true,
        "stats": {
            "currentBalance": 1000,
            "formattedBalance": "10.00 ر.س",
            "totalEarnings": 1000,
            "totalWithdrawn": 0,
            "availableBalance": 1000,
            "totalCredits": 1000,
            "totalDebits": 0,
            "transactionCount": 1
        }
    }
    """
    try:
        # التحقق من الصلاحيات
        is_employer, employer_id, error_response = require_wallet_user()
        if not is_employer:
            return error_response
        
        # الحصول على الإحصائيات
        result = get_wallet_stats(employer_id)
        
        if result.get('success'):
            return jsonify(result), 200
        else:
            return jsonify(result), 404
    
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على الإحصائيات: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'حدث خطأ في الخادم'
        }), 500


@wallet_bp.route('/api/wallet/transactions/<transaction_id>', methods=['GET'])
def get_transaction_details(transaction_id):
    """
    الحصول على تفاصيل معاملة محددة.
    
    الصلاحيات المطلوبة: مستخدم مسجل (باحث عن عمل أو صاحب عمل أو مدير)
    
    الاستجابة الناجحة:
    {
        "success": true,
        "transaction": {...}
    }
    """
    try:
        # التحقق من الصلاحيات
        is_employer, employer_id, error_response = require_wallet_user()
        if not is_employer:
            return error_response
        
        # الحصول على تفاصيل المعاملة
        result = get_transaction_details(transaction_id)
        
        if not result.get('success'):
            return jsonify(result), 404
        
        # التحقق من أن المعاملة تخص صاحب العمل الحالي
        transaction = result.get('transaction', {})
        if transaction.get('employerId') != employer_id:
            return jsonify({
                'success': False,
                'message': 'غير مصرح: هذه المعاملة لا تخصك'
            }), 403
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"❌ خطأ في الحصول على تفاصيل المعاملة: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'حدث خطأ في الخادم'
        }), 500


# ============================================
# تصدير الدوال
# ============================================

__all__ = [
    'wallet_bp',
    'get_balance',
    'get_transactions',
    'get_stats',
    'get_transaction_details'
]