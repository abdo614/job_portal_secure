/**
 * مكون وقفة الخير - صدقة جارية
 * يظهر قبل العمليات الأساسية في المنصة
 * 
 * العمليات المدعومة:
 * - apply: التقديم على وظيفة
 * - view_applicants: عرض المتقدمين
 * - post_job: نشر وظيفة
 */

const SadaqahPause = (function() {
    'use strict';

    // ============================================
    // النصوص الأساسية
    // ============================================
    const TEXTS = {
        title: 'وقفة خير قبل متابعة طلبك 🤍',
        intro: 'نرجو منك التوقف لمدة 15 ثانية وقراءة سورة الفاتحة والدعاء لوالدي صاحب المنصة:',
        names: [
            'حسين عبداللطيف وزان إدلبي',
            'عائشة محمد طالب وزان'
        ],
        dua: 'نسأل الله أن يحفظهما ويبارك في عمرهما، وأن يرزقهما الصحة والعافية، وأن يجعل هذا العمل صدقة جارية لهما في حياتهما وبعد مماتهما، وأن ينفع به كل من ساهم فيه.',
        countdown: 'العداد:',
        button: 'بارك الله فيك 🤍 — متابعة',
        surahTitle: 'سورة الفاتحة',
        surah: 'بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ\nالْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ\nالرَّحْمَٰنِ الرَّحِيمِ\nمَالِكِ يَوْمِ الدِّينِ\nإِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ\nاهْدِنَا الصِّرَاطَ الْمُسْتَقِيمَ\nصِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ'
    };

    // ============================================
    // حالة المكون
    // ============================================
    let currentAction = null;
    let originalCallback = null;
    let countdownInterval = null;
    let isModalOpen = false;

    // ============================================
    // إنشاء النافذة المنبثقة
    // ============================================
    function createModal() {
        // التحقق من عدم وجود النافذة مسبقاً
        if (document.getElementById('sadaqah-pause-modal')) {
            return document.getElementById('sadaqah-pause-modal');
        }

        const modal = document.createElement('div');
        modal.id = 'sadaqah-pause-modal';
        modal.innerHTML = `
            <div class="sadaqah-overlay"></div>
            <div class="sadaqah-modal">
                <div class="sadaqah-content">
                    <div class="sadaqah-header">
                        <h2>${TEXTS.title}</h2>
                    </div>
                    
                    <div class="sadaqah-body">
                        <p class="sadaqah-intro">${TEXTS.intro}</p>
                        <div class="sadaqah-names">
                            ${TEXTS.names.map(name => `<p class="sadaqah-name">${name}</p>`).join('')}
                        </div>
                        
                        <p class="sadaqah-dua">${TEXTS.dua}</p>
                        
                        <div class="sadaqah-surah">
                            <h3>${TEXTS.surahTitle}</h3>
                            <div class="sadaqah-surah-text">${TEXTS.surah.replace(/\n/g, '<br>')}</div>
                        </div>
                        
                        <div class="sadaqah-countdown">
                            <span class="countdown-label">${TEXTS.countdown}</span>
                            <span id="sadaqah-timer" class="countdown-timer">15 ثانية</span>
                        </div>
                    </div>
                    
                    <div class="sadaqah-footer">
                        <button id="sadaqah-continue-btn" class="sadaqah-continue-btn" disabled>
                            ${TEXTS.button}
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        return modal;
    }

    // ============================================
    // إضافة CSS
    // ============================================
    function injectStyles() {
        if (document.getElementById('sadaqah-pause-styles')) {
            return;
        }

        const styles = document.createElement('style');
        styles.id = 'sadaqah-pause-styles';
        styles.textContent = `
            /* ===== Modal Overlay ===== */
            .sadaqah-overlay {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.7);
                z-index: 999998;
                backdrop-filter: blur(4px);
            }

            /* ===== Modal Container ===== */
            .sadaqah-modal {
                position: fixed;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                z-index: 999999;
                max-width: 700px;
                width: 90%;
                max-height: 90vh;
                overflow-y: auto;
                animation: sadaqahFadeIn 0.4s ease;
            }

            @keyframes sadaqahFadeIn {
                from {
                    opacity: 0;
                    transform: translate(-50%, -50%) scale(0.95);
                }
                to {
                    opacity: 1;
                    transform: translate(-50%, -50%) scale(1);
                }
            }

            /* ===== Modal Content ===== */
            .sadaqah-content {
                background: linear-gradient(135deg, #f8fafc, #ffffff);
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                border: 2px solid #1a4a6e;
            }

            /* ===== Header ===== */
            .sadaqah-header {
                text-align: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 2px solid #e0e6ed;
            }

            .sadaqah-header h2 {
                color: #1a4a6e;
                font-size: 28px;
                font-weight: 700;
                margin: 0;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            /* ===== Body ===== */
            .sadaqah-body {
                margin-bottom: 30px;
            }

            .sadaqah-intro {
                color: #2c3e50;
                font-size: 18px;
                line-height: 1.8;
                text-align: center;
                margin-bottom: 25px;
                font-weight: 600;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            .sadaqah-names {
                text-align: center;
                margin-bottom: 25px;
                padding: 20px;
                background: #f0f4f8;
                border-radius: 12px;
                border-right: 4px solid #1a4a6e;
            }

            .sadaqah-name {
                color: #0d2b3e;
                font-size: 20px;
                font-weight: 700;
                margin: 8px 0;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            .sadaqah-dua {
                color: #2e7d32;
                font-size: 16px;
                line-height: 1.8;
                text-align: center;
                margin-bottom: 30px;
                padding: 15px;
                background: #e8f5e9;
                border-radius: 10px;
                font-style: italic;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            /* ===== Surah Al-Fatiha ===== */
            .sadaqah-surah {
                background: #fff;
                padding: 25px;
                border-radius: 12px;
                margin-bottom: 25px;
                border: 1px solid #e0e6ed;
                text-align: center;
            }

            .sadaqah-surah h3 {
                color: #1a4a6e;
                font-size: 22px;
                margin-bottom: 20px;
                font-weight: 700;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            .sadaqah-surah-text {
                color: #0d2b3e;
                font-size: 24px;
                line-height: 2.2;
                font-weight: 600;
                font-family: 'Traditional Arabic', 'Amiri', 'Tajawal', serif;
                direction: rtl;
                text-align: center;
                padding: 20px;
                background: #f8fafc;
                border-radius: 8px;
            }
            /* ===== Countdown ===== */
            .sadaqah-countdown {
                text-align: center;
                padding: 20px;
                background: #fff3e0;
                border-radius: 12px;
                margin-bottom: 20px;
            }

            .countdown-label {
                color: #f57f17;
                font-size: 16px;
                font-weight: 600;
                display: block;
                margin-bottom: 10px;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
            }

            .countdown-timer {
                display: inline-block;
                color: #fff;
                background: #f57f17;
                padding: 12px 30px;
                border-radius: 50px;
                font-size: 32px;
                font-weight: 800;
                min-width: 80px;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
                box-shadow: 0 4px 12px rgba(245, 127, 23, 0.3);
            }

            /* ===== Continue Button ===== */
            .sadaqah-footer {
                text-align: center;
            }

            .sadaqah-continue-btn {
                background: linear-gradient(135deg, #2e7d32, #1b5e20);
                color: #fff;
                border: none;
                padding: 16px 40px;
                border-radius: 50px;
                font-size: 18px;
                font-weight: 700;
                cursor: pointer;
                transition: all 0.3s ease;
                font-family: 'Tajawal', 'Segoe UI', Tahoma, sans-serif;
                box-shadow: 0 6px 20px rgba(46, 125, 50, 0.3);
            }

            .sadaqah-continue-btn:hover:not(:disabled) {
                background: linear-gradient(135deg, #1b5e20, #0d3b0f);
                transform: translateY(-2px);
                box-shadow: 0 8px 24px rgba(46, 125, 50, 0.4);
            }

            .sadaqah-continue-btn:disabled {
                background: #ccc;
                cursor: not-allowed;
                box-shadow: none;
                opacity: 0.6;
            }

            /* ===== Responsive ===== */
            @media (max-width: 768px) {
                .sadaqah-content {
                    padding: 25px;
                }

                .sadaqah-header h2 {
                    font-size: 22px;
                }

                .sadaqah-intro {
                    font-size: 16px;
                }

                .sadaqah-name {
                    font-size: 18px;
                }

                .sadaqah-surah-text {
                    font-size: 20px;
                }

                .countdown-timer {
                    font-size: 28px;
                    padding: 10px 25px;
                }

                .sadaqah-continue-btn {
                    font-size: 16px;
                    padding: 14px 30px;
                }
            }

            @media (max-width: 480px) {
                .sadaqah-content {
                    padding: 20px;
                }

                .sadaqah-header h2 {
                    font-size: 20px;
                }

                .sadaqah-surah-text {
                    font-size: 18px;
                }
            }
        `;

        document.head.appendChild(styles);
    }

    // ============================================
    // بدء العداد التنازلي
    // ============================================
    function startCountdown() {
        let seconds = 15;
        const timerElement = document.getElementById('sadaqah-timer');
        const continueButton = document.getElementById('sadaqah-continue-btn');

        timerElement.textContent = seconds + ' ثانية';
        continueButton.disabled = true;

        countdownInterval = setInterval(() => {
            seconds--;
            timerElement.textContent = seconds + ' ثانية';

            if (seconds <= 0) {
                clearInterval(countdownInterval);
                countdownInterval = null;
                timerElement.textContent = '0 ثانية';
                continueButton.disabled = false;
            }
        }, 1000);
    }

    // ============================================
    // إغلاق النافذة
    // ============================================
    function closeModal() {
        const modal = document.getElementById('sadaqah-pause-modal');
        if (modal) {
            modal.remove();
        }
        
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        
        isModalOpen = false;
        currentAction = null;
        originalCallback = null;
    }

    // ============================================
    // متابعة العملية
    // ============================================
    function continueAction() {
        if (originalCallback) {
            const callback = originalCallback;
            originalCallback = null;
            closeModal();
            callback();
        }
    }

    // ============================================
    // ربط الأحداث
    // ============================================
    function bindEvents() {
        const continueButton = document.getElementById('sadaqah-continue-btn');
        const overlay = document.querySelector('.sadaqah-overlay');

        // زر المتابعة
        if (continueButton) {
            continueButton.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                continueAction();
            });
        }

        // النقر خارج النافذة (لا يغلقها)
        if (overlay) {
            overlay.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                // لا نفعل شيئاً - لا يمكن الإغلاق بالنقر خارج النافذة
            });
        }

        // منع إغلاق النافذة بمفتاح Escape
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && isModalOpen) {
                e.preventDefault();
                e.stopPropagation();
            }
        }, true);
    }

    // ============================================
    // عرض النافذة
    // ============================================
    function show(actionType, callback) {
        // التحقق من عدم وجود نافذة مفتوحة
        if (isModalOpen) {
            console.warn('نافذة وقفة الخير مفتوحة بالفعل');
            return;
        }

        // التحقق من صحة المعاملات
        if (!['apply', 'view_applicants', 'post_job', 'request_unlock_contact'].includes(actionType)) {
            console.error('نوع العملية غير صالح:', actionType);
            return;
        }

        if (typeof callback !== 'function') {
            console.error('callback يجب أن تكون دالة');
            return;
        }

        // حفظ الحالة
        currentAction = actionType;
        originalCallback = callback;
        isModalOpen = true;

        // إنشاء النافذة
        createModal();
        injectStyles();
        bindEvents();

        // بدء العداد
        startCountdown();
    }

    // ============================================
    // واجهة عامة
    // ============================================
    return {
        show: show,
        close: closeModal
    };
})();

// ============================================
// تصدير للاستخدام العام
// ============================================
if (typeof window !== 'undefined') {
    window.SadaqahPause = SadaqahPause;
}