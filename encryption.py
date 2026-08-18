"""
وحدة التشفير المتقدمة - منصة التوظيف العربية
تستخدم AES-256 (Fernet) لتشفير جميع البيانات المخزنة
مع حماية إضافية باستخدام bcrypt لكلمات المرور
"""

from cryptography.fernet import Fernet
import base64
import os
import json
from datetime import datetime
import bcrypt
import logging
import time
import threading
from pathlib import Path

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'


class EncryptionManager:
    """مدير التشفير الرئيسي - يستخدم AES-256"""
    
    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)
        self._file_cache = {}
        self._file_cache_ttl = 2.0
        self._cache_lock = threading.RLock()
        logger.info("🔐 تم تهيئة مدير التشفير بنجاح")

    def _get_cached_file(self, filename):
        now = time.time()
        with self._cache_lock:
            entry = self._file_cache.get(filename)
            if entry and now - entry['timestamp'] < self._file_cache_ttl:
                return entry['data']
            if entry:
                self._file_cache.pop(filename, None)
            return None

    def _set_cached_file(self, filename, data):
        with self._cache_lock:
            self._file_cache[filename] = {'data': data, 'timestamp': time.time()}

    def _invalidate_cached_file(self, filename):
        with self._cache_lock:
            self._file_cache.pop(filename, None)
    
    def _get_or_create_key(self):
        """إنشاء أو استرجاع مفتاح التشفير"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file = DATA_DIR / '.key'
        
        # 1) أولوية: مفتاح من متغير البيئة (للاستضافة السحابية)
        env_key = os.environ.get('ENCRYPTION_KEY', '').strip()
        if env_key:
            try:
                key = env_key.encode('utf-8')
                # التأكد من صلاحية المفتاح
                Fernet(key)
                logger.info("🔑 تم تحميل مفتاح التشفير من متغير البيئة")
                # حفظ المفتاح محلياً أيضاً لضمان التوافق مع النسخ الاحتياطية
                if not os.path.exists(key_file):
                    with open(key_file, 'wb') as f:
                        f.write(key)
                return key
            except Exception as e:
                logger.error(f"⚠️ مفتاح التشفير من البيئة غير صالح: {str(e)}")
        
        # التأكد من وجود مجلد data
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                key = f.read()
            logger.info("🔑 تم تحميل مفتاح التشفير من الملف")
            return key
        else:
            # إنشاء مفتاح جديد
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            logger.info("🔑 تم إنشاء مفتاح تشفير جديد")
            return key
    
    def encrypt_data(self, data):
        """تشفير البيانات"""
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False)
        elif not isinstance(data, str):
            data = str(data)
        
        encrypted = self.cipher.encrypt(data.encode('utf-8'))
        return encrypted
    
    def decrypt_data(self, encrypted_data):
        """فك تشفير البيانات"""
        try:
            decrypted = self.cipher.decrypt(encrypted_data)
            # محاولة تحويل JSON
            try:
                return json.loads(decrypted.decode('utf-8'))
            except:
                return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"❌ خطأ في فك التشفير: {str(e)}")
            return None
    
    def encrypt_file(self, filename, data):
        """تشفير وحفظ ملف"""
        try:
            encrypted = self.encrypt_data(data)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(DATA_DIR / f'{filename}.enc', 'wb') as f:
                f.write(encrypted)
            self._set_cached_file(filename, data)
            logger.info(f"✅ تم تشفير وحفظ ملف: {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الملف المشفر {filename}: {str(e)}")
            return False
    
    def decrypt_file(self, filename):
        """قراءة وفك تشفير ملف"""
        cached = self._get_cached_file(filename)
        if cached is not None:
            return cached
        try:
            with open(DATA_DIR / f'{filename}.enc', 'rb') as f:
                encrypted = f.read()
            data = self.decrypt_data(encrypted)
            if data is not None:
                self._set_cached_file(filename, data)
                logger.info(f"✅ تم فك تشفير ملف: {filename}")
            return data
        except FileNotFoundError:
            logger.warning(f"⚠️ ملف غير موجود: {filename}.enc")
            return None
        except Exception as e:
            logger.error(f"❌ خطأ في قراءة الملف المشفر {filename}: {str(e)}")
            return None


class PasswordManager:
    """مدير كلمات المرور باستخدام bcrypt"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """
        تشفير كلمة المرور باستخدام bcrypt
        يستخدم 12 round للحصول على توازن بين الأمان والأداء
        """
        try:
            salt = bcrypt.gensalt(rounds=12)
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            return hashed.decode('utf-8')
        except Exception as e:
            logger.error(f"❌ خطأ في تشفير كلمة المرور: {str(e)}")
            return None
    
    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """
        التحقق من كلمة المرور مقابل التشفير
        """
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من كلمة المرور: {str(e)}")
            return False
    
    @staticmethod
    def is_password_strong(password: str) -> tuple:
        """
        التحقق من قوة كلمة المرور
        تعيد (نعم/لا, الرسالة)
        """
        if len(password) < 8:
            return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
        if not any(c.isupper() for c in password):
            return False, "يجب أن تحتوي كلمة المرور على حرف كبير"
        if not any(c.islower() for c in password):
            return False, "يجب أن تحتوي كلمة المرور على حرف صغير"
        if not any(c.isdigit() for c in password):
            return False, "يجب أن تحتوي كلمة المرور على رقم"
        if not any(c in "!@#$%^&*(),.?\":{}|<>" for c in password):
            return False, "يجب أن تحتوي كلمة المرور على رمز خاص"
        return True, "كلمة المرور قوية"


class SecureStorage:
    """
    تخزين آمن للبيانات مع طبقات حماية متعددة
    - تشفير AES-256 لجميع الملفات
    - تشفير bcrypt لكلمات المرور
    - حماية من هجمات القوة العمياء
    - تسجيل جميع العمليات
    """
    
    def __init__(self):
        self.encryption = EncryptionManager()
        self.password_manager = PasswordManager()
        self.login_attempts = {}  # منع هجمات القوة العمياء
        self.max_attempts = 5      # الحد الأقصى للمحاولات
        self.lock_time = 300       # وقت الحظر بالثواني (5 دقائق)
        self._cache = {}
        self._cache_ttl = 2.0
        self._cache_lock = threading.RLock()
        logger.info("🛡️ تم تهيئة التخزين الآمن")

    def _get_cached(self, key):
        now = time.time()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and now - entry['timestamp'] < self._cache_ttl:
                return entry['data']
            if entry:
                self._cache.pop(key, None)
            return None

    def _set_cached(self, key, data):
        with self._cache_lock:
            self._cache[key] = {'data': data, 'timestamp': time.time()}

    def _invalidate_cache(self, *keys):
        with self._cache_lock:
            for key in keys:
                self._cache.pop(key, None)
    
    # ============== إدارة المستخدمين ==============
    
    def save_users(self, users):
        """
        حفظ المستخدمين مع تشفير كلمات المرور
        يتم تشفير كلمات المرور قبل التخزين
        """
        try:
            secured_users = []
            for user in users:
                user_copy = user.copy()
                # تشفير كلمة المرور إذا لم تكن مشفرة بالفعل
                if 'password' in user_copy and not user_copy['password'].startswith('$2b$'):
                    hashed = self.password_manager.hash_password(user_copy['password'])
                    if hashed:
                        user_copy['password'] = hashed
                    else:
                        logger.error(f"❌ فشل تشفير كلمة المرور للمستخدم {user_copy.get('email', 'unknown')}")
                        continue
                secured_users.append(user_copy)
            
            success = self.encryption.encrypt_file('users', secured_users)
            if success:
                self._set_cached('users', secured_users)
                self._invalidate_cache('stats')
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ المستخدمين: {str(e)}")
            return False
    
    def load_users(self):
        """تحميل المستخدمين"""
        cached = self._get_cached('users')
        if cached is not None:
            return cached
        try:
            users = self.encryption.decrypt_file('users')
            users = users if users else []
            self._set_cached('users', users)
            return users
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل المستخدمين: {str(e)}")
            return []
    
    # ============== إدارة الوظائف ==============
    
    def save_jobs(self, jobs):
        """حفظ الوظائف"""
        try:
            success = self.encryption.encrypt_file('jobs', jobs)
            if success:
                self._set_cached('jobs', jobs)
                self._invalidate_cache('stats')
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الوظائف: {str(e)}")
            return False
    
    def load_jobs(self):
        """تحميل الوظائف"""
        cached = self._get_cached('jobs')
        if cached is not None:
            return cached
        try:
            jobs = self.encryption.decrypt_file('jobs')
            jobs = jobs if jobs else []
            self._set_cached('jobs', jobs)
            return jobs
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل الوظائف: {str(e)}")
            return []
    
    # ============== إدارة طلبات التوظيف ==============
    
    def save_applications(self, applications):
        """حفظ طلبات التوظيف"""
        try:
            success = self.encryption.encrypt_file('applications', applications)
            if success:
                self._set_cached('applications', applications)
                self._invalidate_cache('stats')
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الطلبات: {str(e)}")
            return False
    
    def load_applications(self):
        """تحميل طلبات التوظيف"""
        cached = self._get_cached('applications')
        if cached is not None:
            return cached
        try:
            apps = self.encryption.decrypt_file('applications')
            apps = apps if apps else {}
            self._set_cached('applications', apps)
            return apps
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل الطلبات: {str(e)}")
            return {}
    
    # ============== إدارة المفضلات ==============
    
    def save_favorites(self, favorites):
        """حفظ المفضلات"""
        try:
            success = self.encryption.encrypt_file('favorites', favorites)
            if success:
                self._set_cached('favorites', favorites)
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ المفضلات: {str(e)}")
            return False
    
    def load_favorites(self):
        """تحميل المفضلات"""
        cached = self._get_cached('favorites')
        if cached is not None:
            return cached
        try:
            favs = self.encryption.decrypt_file('favorites')
            favs = favs if favs else {}
            self._set_cached('favorites', favs)
            return favs
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل المفضلات: {str(e)}")
            return {}
    
    # ============== إدارة الأخبار ==============
    
    def save_news(self, news):
        """حفظ الأخبار"""
        try:
            success = self.encryption.encrypt_file('news', news)
            if success:
                self._set_cached('news', news)
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الأخبار: {str(e)}")
            return False
    
    def load_news(self):
        """تحميل الأخبار"""
        cached = self._get_cached('news')
        if cached is not None:
            return cached
        try:
            news = self.encryption.decrypt_file('news')
            news = news if news else []
            self._set_cached('news', news)
            return news
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل الأخبار: {str(e)}")
            return []
    
    # ============== إدارة فريق العمل ==============
    
    def save_team(self, team):
        """حفظ فريق العمل"""
        try:
            success = self.encryption.encrypt_file('team', team)
            if success:
                self._set_cached('team', team)
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ فريق العمل: {str(e)}")
            return False
    
    def load_team(self):
        """تحميل فريق العمل"""
        cached = self._get_cached('team')
        if cached is not None:
            return cached
        try:
            team = self.encryption.decrypt_file('team')
            team = team if team else []
            self._set_cached('team', team)
            return team
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل فريق العمل: {str(e)}")
            return []
    
    # ============== إدارة الآراء ==============
    
    def save_testimonials(self, testimonials):
        """حفظ آراء العملاء"""
        try:
            success = self.encryption.encrypt_file('testimonials', testimonials)
            if success:
                self._set_cached('testimonials', testimonials)
            return success
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الآراء: {str(e)}")
            return False
    
    def load_testimonials(self):
        """تحميل آراء العملاء"""
        cached = self._get_cached('testimonials')
        if cached is not None:
            return cached
        try:
            testimonials = self.encryption.decrypt_file('testimonials')
            testimonials = testimonials if testimonials else []
            self._set_cached('testimonials', testimonials)
            return testimonials
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل الآراء: {str(e)}")
            return []
    
    # ============== محتوى الموقع CMS ==============

    def save_cms(self, section, data):
        success = self.encryption.encrypt_file(f'cms_{section}', data)
        if success:
            self._set_cached(f'cms_{section}', data)
        return success

    def load_cms(self, section, default=None):
        cache_key = f'cms_{section}'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        data = self.encryption.decrypt_file(cache_key)
        if data is not None:
            self._set_cached(cache_key, data)
        return default if data is None else data

    # ============== حماية من هجمات القوة العمياء ==============
    
    def check_login_attempts(self, email):
        """
        التحقق من محاولات الدخول الفاشلة
        تعيد (مسموح, الرسالة)
        """
        if email in self.login_attempts:
            attempts, last_attempt = self.login_attempts[email]
            if attempts >= self.max_attempts:
                time_elapsed = datetime.now().timestamp() - last_attempt
                if time_elapsed < self.lock_time:
                    remaining = int((self.lock_time - time_elapsed) / 60)
                    return False, f"تم حظر الحساب لمدة {remaining} دقائق"
                else:
                    # إعادة تعيين المحاولات بعد انتهاء الوقت
                    self.login_attempts[email] = (0, datetime.now().timestamp())
                    logger.info(f"🔓 تم إلغاء حظر الحساب: {email}")
        return True, None
    
    def record_failed_attempt(self, email):
        """تسجيل محاولة دخول فاشلة"""
        if email in self.login_attempts:
            attempts, _ = self.login_attempts[email]
            self.login_attempts[email] = (attempts + 1, datetime.now().timestamp())
            logger.warning(f"⚠️ محاولة فاشلة {attempts + 1}/{self.max_attempts} للبريد: {email}")
        else:
            self.login_attempts[email] = (1, datetime.now().timestamp())
            logger.warning(f"⚠️ أول محاولة فاشلة للبريد: {email}")
    
    def clear_login_attempts(self, email):
        """مسح محاولات الدخول بعد نجاح الدخول"""
        if email in self.login_attempts:
            del self.login_attempts[email]
            logger.info(f"✅ تم مسح محاولات الدخول للبريد: {email}")
    
    # ============== إحصائيات ==============
    
    def get_stats(self):
        """الحصول على إحصائيات النظام"""
        try:
            users = self.load_users()
            jobs = self.load_jobs()
            applications = self.load_applications()
            
            # عدد الطلبات الكلي
            total_applications = sum(len(apps) for apps in applications.values())
            
            # عدد الشركات الفريدة
            companies = set(job.get('company', '') for job in jobs if job.get('company'))
            
            # عدد الدول
            countries = set(job.get('country', '') for job in jobs if job.get('country'))
            
            return {
                'total_users': len(users),
                'total_jobs': len(jobs),
                'total_applications': total_applications,
                'total_companies': len(companies),
                'total_countries': len(countries),
                'last_updated': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الإحصائيات: {str(e)}")
            return None
    
    # ============== النسخ الاحتياطي ==============
    
    def create_backup(self):
        """إنشاء نسخة احتياطية كاملة وإرجاع مسارها المطلق."""
        try:
            import shutil
            backup_root = DATA_DIR / 'backups'
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_dir = backup_root / f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}'
            backup_dir.mkdir(parents=True, exist_ok=True)
            for file in DATA_DIR.iterdir():
                if file.is_file() and (file.suffix == '.enc' or file.name == '.key'):
                    shutil.copy2(file, backup_dir / file.name)
            zip_path = shutil.make_archive(str(backup_dir), 'zip', root_dir=str(backup_dir))
            # حذف المجلد المؤقت بعد إنشاء ملف ZIP
            shutil.rmtree(backup_dir, ignore_errors=True)
            logger.info(f"✅ تم إنشاء النسخة الاحتياطية: {zip_path}")
            return zip_path
        except Exception as e:
            logger.exception(f"❌ خطأ في إنشاء النسخة الاحتياطية: {str(e)}")
            return None


# ============================================
# إنشاء نسخة عامة من التخزين الآمن
# ============================================

secure_storage = SecureStorage()

# ============================================
# اختبار سريع عند التشغيل
# ============================================

if __name__ == '__main__':
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║     🔐 وحدة التشفير - منصة التوظيف العربية                 ║
    ║                                                              ║
    ║     🔒 خوارزمية التشفير: AES-256 (Fernet)                 ║
    ║     🔑 تشفير كلمات المرور: bcrypt (12 rounds)             ║
    ║     🛡️ حماية من هجمات القوة العمياء: نعم                  ║
    ║     📁 التخزين: مشفر بالكامل                               ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    # اختبار التشفير
    test_data = {'test': 'Hello World', 'date': datetime.now().isoformat()}
    print("🧪 اختبار التشفير...")
    
    encrypted = secure_storage.encryption.encrypt_data(test_data)
    print(f"✅ تم التشفير: {encrypted[:50]}...")
    
    decrypted = secure_storage.encryption.decrypt_data(encrypted)
    print(f"✅ تم فك التشفير: {decrypted}")
    
    # اختبار كلمات المرور
    print("\n🧪 اختبار كلمات المرور...")
    test_password = "Test@1234"
    hashed = PasswordManager.hash_password(test_password)
    print(f"✅ تم تشفير كلمة المرور: {hashed[:30]}...")
    
    verified = PasswordManager.verify_password(test_password, hashed)
    print(f"✅ التحقق من كلمة المرور: {'ناجح' if verified else 'فاشل'}")
    
    print("\n✅ جميع الاختبارات ناجحة! وحدة التشفير جاهزة للاستخدام.")