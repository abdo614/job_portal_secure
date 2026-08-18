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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
# يمكن الآن توجيه التخزين إلى Persistent Disk على Render عبر DATA_DIR.
# لا نغير المسار الافتراضي حتى لا نفقد البيانات الموجودة أثناء الترقية.
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE_DIR / 'data'))).expanduser()


class EncryptionManager:
    """مدير التشفير الرئيسي - يستخدم AES-256"""
    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)
        self._file_cache = {}
        self._file_cache_ttl = 2.0
        self._cache_lock = threading.RLock()
        logger.info("🔐 تم تهيئة مدير التشفير بنجاح | DATA_DIR=%s", DATA_DIR)

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
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file = DATA_DIR / '.key'
        env_key = os.environ.get('ENCRYPTION_KEY', '').strip()
        if env_key:
            try:
                key = env_key.encode('utf-8')
                Fernet(key)
                logger.info("🔑 تم تحميل مفتاح التشفير من متغير البيئة")
                return key
            except Exception as e:
                logger.error(f"⚠️ مفتاح التشفير من البيئة غير صالح: {str(e)}")
        if key_file.exists():
            return key_file.read_bytes()
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        logger.warning("⚠️ تم إنشاء مفتاح تشفير جديد؛ في الإنتاج يجب ضبط ENCRYPTION_KEY")
        return key

    def encrypt_data(self, data):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False)
        elif not isinstance(data, str):
            data = str(data)
        return self.cipher.encrypt(data.encode('utf-8'))

    def decrypt_data(self, encrypted_data):
        try:
            decrypted = self.cipher.decrypt(encrypted_data)
            try:
                return json.loads(decrypted.decode('utf-8'))
            except Exception:
                return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"❌ خطأ في فك التشفير: {str(e)}")
            return None

    def encrypt_file(self, filename, data):
        try:
            encrypted = self.encrypt_data(data)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            target = DATA_DIR / f'{filename}.enc'
            temp = DATA_DIR / f'.{filename}.enc.tmp'
            temp.write_bytes(encrypted)
            os.replace(temp, target)
            self._set_cached_file(filename, data)
            logger.info(f"✅ تم تشفير وحفظ ملف: {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الملف المشفر {filename}: {str(e)}")
            return False

    def decrypt_file(self, filename):
        cached = self._get_cached_file(filename)
        if cached is not None:
            return cached
        try:
            encrypted = (DATA_DIR / f'{filename}.enc').read_bytes()
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
    @staticmethod
    def hash_password(password: str) -> str:
        try:
            return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
        except Exception as e:
            logger.error(f"❌ خطأ في تشفير كلمة المرور: {str(e)}")
            return None

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من كلمة المرور: {str(e)}")
            return False

    @staticmethod
    def is_password_strong(password: str) -> tuple:
        if len(password) < 8: return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
        if not any(c.isupper() for c in password): return False, "يجب أن تحتوي كلمة المرور على حرف كبير"
        if not any(c.islower() for c in password): return False, "يجب أن تحتوي كلمة المرور على حرف صغير"
        if not any(c.isdigit() for c in password): return False, "يجب أن تحتوي كلمة المرور على رقم"
        if not any(c in "!@#$%^&*(),.?\":{}|<>" for c in password): return False, "يجب أن تحتوي كلمة المرور على رمز خاص"
        return True, "كلمة المرور قوية"


class SecureStorage:
    def __init__(self):
        self.encryption = EncryptionManager()
        self.password_manager = PasswordManager()
        self.login_attempts = {}
        self.max_attempts = 5
        self.lock_time = 300
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
            if entry: self._cache.pop(key, None)
            return None

    def _set_cached(self, key, data):
        with self._cache_lock:
            self._cache[key] = {'data': data, 'timestamp': time.time()}

    def _invalidate_cache(self, *keys):
        with self._cache_lock:
            for key in keys: self._cache.pop(key, None)
