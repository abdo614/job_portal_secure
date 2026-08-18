(function () {
  'use strict';

  const DICT = window.__SITE_I18N__ || {};
  const LANGS = window.__SITE_LANGUAGES__ || {};
  const DEFAULT_LANG = window.__SITE_LANG__ || 'ar';
  const SOURCE_ALIASES = {"Search for a Job": "بحث عن وظيفة", "Search for a job": "بحث عن وظيفة", "Job Title, skill or Company": "المسمى الوظيفي، المهارة أو الشركة", "Country / City": "الدولة / المدينة", "More than 1000 jobs available now in all Arab countries": "أكثر من 1000 وظيفة متاحة الآن في جميع الدول العربية", "Available Jobs": "وظائف متاحة", "Registered Companies": "شركات مسجلة", "Job Seeker": "باحث عن عمل", "Arab Country": "دولة عربية", "Why Choose Us?": "لماذا تختارنا؟", "Security & Privacy": "الأمن والخصوصية", "Arab Coverage": "التغطية العربية", "Instant Updates": "تحديثات فورية", "Ongoing Support": "الدعم المستمر", "Welcome to Your Platform": "مرحباً بك في منصتك", "Latest Jobs": "أحدث الوظائف", "View All Jobs": "عرض جميع الوظائف", "Latest News": "آخر الأخبار", "View All News": "عرض جميع الأخبار", "Contact Us": "اتصل بنا", "Quick Links": "روابط سريعة", "FAQ": "الأسئلة الشائعة", "Help": "المساعدة", "Services": "الخدمات"};
  const DIR = Object.fromEntries(Object.entries(LANGS).map(([code, cfg]) => [code, cfg.dir || 'ltr']));
  const NAME = Object.fromEntries(Object.entries(LANGS).map(([code, cfg]) => [code, cfg.name || code]));
  const SOURCE_KEYS = [...new Set(Object.values(DICT).flatMap(value => Object.keys(value || {})))].sort((a, b) => b.length - a.length);
  const FLAGS = { ar: '🇸🇦', en: '🇬🇧', tr: '🇹🇷', fr: '🇫🇷', de: '🇩🇪', es: '🇪🇸' };
  const NATIVE_NAMES = { ar: 'العربية', en: 'English', tr: 'Türkçe', fr: 'Français', de: 'Deutsch', es: 'Español' };
  const originalAttributes = new WeakMap();
  let active = DEFAULT_LANG;
  let observer;
  let applying = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[ch]));
  }

  function dictFor(lang) {
    return DICT[lang] || DICT.ar || {};
  }

  function translateWholeText(text, lang) {
    if (!text || lang === 'ar') return text;
    const dict = dictFor(lang);
    let out = text;
    // First normalize common English UI strings back to their canonical Arabic keys.
    for (const [english, arabic] of Object.entries(SOURCE_ALIASES)) {
      if (out.includes(english)) out = out.split(english).join(arabic);
    }
    for (const key of SOURCE_KEYS) {
      const translated = dict[key];
      if (translated && translated !== key && out.includes(key)) {
        out = out.split(key).join(translated);
      }
    }
    return out;
  }

  function rememberOriginal(el, attr) {
    if (!el) return '';
    let attrs = originalAttributes.get(el);
    if (!attrs) { attrs = {}; originalAttributes.set(el, attrs); }
    if (!Object.prototype.hasOwnProperty.call(attrs, attr)) attrs[attr] = el.getAttribute(attr) || '';
    return attrs[attr];
  }

  function translateElement(el, lang) {
    if (!el || el.nodeType !== 1 || el.closest('[data-no-translate="1"]')) return;
    const dict = dictFor(lang);
    ['title', 'aria-label', 'placeholder'].forEach(attr => {
      if (!el.hasAttribute(attr)) return;
      const original = rememberOriginal(el, attr);
      const translated = lang === 'ar' ? original : (dict[original] || translateWholeText(original, lang));
      if (translated && el.getAttribute(attr) !== translated) el.setAttribute(attr, translated);
    });
  }

  function translateTextNode(node, lang) {
    if (!node || node.nodeType !== Node.TEXT_NODE || !node.nodeValue || !node.parentElement) return;
    if (node.parentElement.closest('[data-no-translate="1"]')) return;
    const parent = node.parentElement;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEXTAREA'].includes(parent.tagName)) return;
    if (!node.__siteI18nOriginal) node.__siteI18nOriginal = node.nodeValue;
    const original = node.__siteI18nOriginal;
    const translated = translateWholeText(original, lang);
    if (node.nodeValue !== translated) node.nodeValue = translated;
  }

  function translateTree(root, lang) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => translateTextNode(node, lang));
    if (root.querySelectorAll) root.querySelectorAll('*').forEach(el => translateElement(el, lang));
    if (root.nodeType === 1) translateElement(root, lang);
  }

  function applyDirection(lang) {
    document.documentElement.lang = lang;
    document.documentElement.dir = DIR[lang] || 'ltr';
    document.body?.setAttribute('dir', DIR[lang] || 'ltr');
  }

  function updateMenu() {
    const root = document.querySelector('.site-language-dropdown');
    if (!root) return;
    const current = root.querySelector('.lang-current');
    if (current) current.textContent = NATIVE_NAMES[active] || NAME[active] || active;
    const icon = root.querySelector('.lang-trigger-icon');
    if (icon) icon.textContent = FLAGS[active] || '🌐';
    root.querySelectorAll('.site-language-option').forEach(option => {
      const isActive = option.dataset.lang === active;
      option.classList.toggle('active', isActive);
      option.setAttribute('aria-selected', String(isActive));
    });
  }

  function buildMenu() {
    if (!document.body) return;
    const slot = document.getElementById('languageControlSlot');
    if (!slot || document.querySelector('#languageControlSlot .site-language-dropdown')) return;
    const root = document.createElement('div');
    root.className = 'site-language-dropdown account-language-dropdown';
    root.setAttribute('data-no-translate', '1');
    const langCodes = Object.keys(LANGS);
    const options = langCodes.map(code => `
      <button type="button" class="site-language-option" data-lang="${escapeHtml(code)}" role="option" aria-selected="false">
        <span class="lang-option-main">
          <span class="lang-flag" aria-hidden="true">${FLAGS[code] || '🌐'}</span>
          <span class="lang-option-copy"><strong>${escapeHtml(NATIVE_NAMES[code] || NAME[code] || code)}</strong><small>${escapeHtml(NAME[code] || code)}</small></span>
        </span>
        <i class="fas fa-check lang-check" aria-hidden="true"></i>
      </button>`).join('');
    root.innerHTML = `
      <button type="button" class="site-language-trigger account-language-trigger" aria-haspopup="listbox" aria-expanded="false">
        <span class="lang-trigger-icon" aria-hidden="true">🌐</span>
        <span class="lang-trigger-copy"><strong>${escapeHtml(NATIVE_NAMES[active] || NAME[active] || active)}</strong></span>
        <i class="fas fa-chevron-down lang-chevron" aria-hidden="true"></i>
      </button>
      <div class="site-language-menu account-language-menu" role="listbox" aria-label="Language">
        <div class="lang-options">${options}</div>
      </div>`;
    slot.appendChild(root);

    const trigger = root.querySelector('.site-language-trigger');
    const close = () => { root.classList.remove('open'); trigger.setAttribute('aria-expanded', 'false'); };
    trigger.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      const open = root.classList.toggle('open');
      trigger.setAttribute('aria-expanded', String(open));
    });
    root.querySelectorAll('.site-language-option').forEach(option => {
      option.addEventListener('click', async event => {
        event.preventDefault(); event.stopPropagation();
        const lang = option.dataset.lang;
        if (!LANGS[lang] || lang === active) { close(); return; }
        option.disabled = true;
        try {
          const response = await fetch('/api/language', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
            body: JSON.stringify({ language: lang })
          });
          if (!response.ok) throw new Error('Language update failed');
          active = lang;
          close();
          window.location.reload();
        } catch (_) {
          option.disabled = false;
          close();
        }
      });
    });
    updateMenu();
  }
  function apply(lang, persist) {
    if (!LANGS[lang]) lang = DEFAULT_LANG;
    active = lang;
    applyDirection(lang);
    translateTree(document.body, lang);
    updateMenu();
    if (persist) {
      fetch('/api/language', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ language: lang })
      }).catch(() => {});
    }
  }

  function startObserver() {
    if (observer || !document.body) return;
    observer = new MutationObserver(mutations => {
      if (applying) return;
      applying = true;
      try {
        for (const mutation of mutations) {
          mutation.addedNodes.forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) translateTextNode(node, active);
            else if (node.nodeType === Node.ELEMENT_NODE) translateTree(node, active);
          });
          if (mutation.type === 'attributes') translateElement(mutation.target, active);
        }
      } finally {
        applying = false;
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['title', 'aria-label', 'placeholder'] });
  }

  window.setSiteLanguage = function (lang) { apply(lang, true); };
  window.getSiteLanguage = function () { return active; };
  window.refreshSiteTranslations = function () { apply(active, false); };

  document.addEventListener('DOMContentLoaded', () => {
    active = LANGS[DEFAULT_LANG] ? DEFAULT_LANG : 'ar';
    buildMenu();
    apply(active, false);
    startObserver();
  });
})();
