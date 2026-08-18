# -*- coding: utf-8 -*-
"""إصلاح آمن لبيئة الحسابات التجريبية دون إعادة إنشاء ما حذفه المدير."""
from pathlib import Path
from datetime import datetime, timedelta
import html

from encryption import secure_storage, PasswordManager
from professions import PROFESSIONS

BASE = Path(__file__).resolve().parent
AVATAR_DIR = BASE / 'static' / 'uploads' / 'avatars'
NEWS_DIR = BASE / 'static' / 'uploads' / 'news'
PASSWORD = 'Demo@2026!'

FIRST_NAMES = ["أحمد","محمد","عمر","ياسر","سامي","خالد","طارق","وليد","معاذ","إياد","رامي","مروان","حسام","أنس","زياد","باسم","سيف","مازن","علي","حازم","نور","ليان","سارة","ريم","مريم","دانا","جود","لارا","رنا","هدى","سلمى","فرح","تالا","جنى","آية","نورا","شهد","ميس","لينا","رؤى"]
LAST_NAMES = ["الحمادي","المنصور","العتيبي","الحربي","الزهراني","التميمي","الشهري","الغامدي","الشمري","السالم","الحداد","النجار","الخطيب","الرفاعي","المرزوقي","الأنصاري","العمري","السعدي","القحطاني","الدوسري","الهاشمي","الراشد","المالكي","العباسي","النعيمي","النجدي","المرعي","السليمان","الهادي","الكيلاني"]
LATIN_FIRST = ["ahmad","mohammad","omar","yasser","sami","khaled","tariq","waleed","moath","eyad","rami","marwan","hossam","anas","ziyad","basem","saif","mazen","ali","hazem","noor","layan","sara","reem","maryam","dana","joud","lara","rana","huda","salma","farah","tala","jana","aya","noura","shahd","mais","lina","roua"]
LATIN_LAST = ["almansour","alotaibi","alharbi","alzahrani","altamimi","alshammari","alsalem","alhaddad","alnajjar","alkhatib","alrifai","alansari","alomari","alsaadi","almarzouqi","alhashemi","alrashed","almaliki","alabbasi","alnaimi","alnajdi","almarai","alsulaiman","alhadi","alkilani","alhamadi","aldosari","alqhtani","alghamdi","alshahri"]
LOCATIONS = [("السعودية","الرياض","العليا"),("السعودية","جدة","الروضة"),("السعودية","الدمام","الشاطئ"),("السعودية","الخبر","الراكة"),("مصر","القاهرة","مدينة نصر"),("مصر","الجيزة","الدقي"),("مصر","الإسكندرية","سموحة"),("الإمارات","دبي","الخليج التجاري"),("الإمارات","أبوظبي","الخالدية"),("الإمارات","الشارقة","النهدة"),("الأردن","عمّان","الشميساني"),("قطر","الدوحة","السد"),("الكويت","مدينة الكويت","شرق"),("عُمان","مسقط","الخوير"),("البحرين","المنامة","السيف"),("لبنان","بيروت","الحمرا"),("المغرب","الدار البيضاء","المعاريف"),("المغرب","الرباط","أكدال"),("الجزائر","الجزائر العاصمة","حيدرة"),("تونس","تونس","المنزه")]
CATEGORIES = [("تقنية المعلومات","مهندس برمجيات"),("تقنية المعلومات","محلل بيانات"),("التسويق","أخصائي تسويق رقمي"),("الموارد البشرية","أخصائي موارد بشرية"),("المبيعات","تنفيذي مبيعات"),("المالية","محاسب أول"),("التصميم","مصمم UI/UX"),("اللوجستيات","منسق سلاسل إمداد"),("الهندسة","مهندس مشاريع"),("خدمة العملاء","أخصائي تجربة عميل")]
JOB_TEMPLATES = [("مهندس برمجيات أول","18,000 - 24,000 ريال","Python, FastAPI, PostgreSQL, Docker"),("مطور تطبيقات ويب","12,000 - 18,000 ريال","React, TypeScript, REST API"),("محلل بيانات","11,000 - 16,000 ريال","SQL, Power BI, Python"),("مدير منتج رقمي","16,000 - 23,000 ريال","Product, Agile, Analytics"),("أخصائي تسويق رقمي","9,000 - 14,000 ريال","SEO, Google Ads, Content"),("مصمم UI/UX","10,000 - 15,000 ريال","Figma, Design Systems, Research"),("أخصائي موارد بشرية","8,000 - 13,000 ريال","Recruitment, HRIS, Employee Experience"),("تنفيذي مبيعات B2B","9,000 - 16,000 ريال + عمولة","B2B, CRM, Negotiation"),("مهندس مشاريع","13,000 - 20,000 ريال","Project Management, AutoCAD, Planning"),("منسق سلاسل إمداد","8,000 - 12,000 ريال","Supply Chain, ERP, Excel")]
NEWS_THEMES = [
    ("البحث الذكي","تقنية","بحث وظائف بواجهة حديثة", "🔎"),("السيرة الذاتية","تطوير مهني","سيرة ذاتية احترافية", "CV"),("المقابلات","تطوير مهني","الاستعداد لمقابلة عن بعد", "🎥"),("سوق العمل","سوق العمل","نمو الوظائف الرقمية", "📈"),("المهارات","مهارات","مهارات فرق العمل الحديثة", "🧠"),("الشركات الناشئة","سوق العمل","مواهب تجمع التقنية والأعمال", "🚀"),("العروض الوظيفية","تطوير مهني","اختيار العرض المناسب", "💼"),("رسالة التقديم","تطوير مهني","رسالة تقديم مؤثرة", "✉️"),("العمل المرن","سوق العمل","فرق عمل مرنة وهجينة", "🏠"),("الخبرة المهنية","مهارات","عرض الخبرة والإنجازات", "⭐"),("التعلم المستمر","تطوير مهني","التعلم المستمر", "📚"),("أخبار المنصة","أخبار المنصة","تجربة منصة التوظيف العربية", "🌐")]

def svg_avatar(name, idx, employer=False):
    initials=''.join([x[0] for x in name.split()[:2]]) or 'م'
    hue=(idx*37)%360
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="hsl({hue},55%,28%)"/><stop offset="1" stop-color="hsl({(hue+45)%360},65%,48%)"/></linearGradient></defs><rect width="320" height="320" rx="80" fill="url(#g)"/><circle cx="160" cy="120" r="58" fill="#f1c7a8"/><path d="M100 112c5-55 112-67 121 2-30-20-85-18-121-2z" fill="#2b2530"/><path d="M70 285c8-78 57-112 90-112s82 34 90 112" fill="#f4f7fb" opacity=".96"/><text x="160" y="305" text-anchor="middle" font-family="Arial" font-size="22" font-weight="700" fill="#17364c">{html.escape(initials)}</text></svg>'''

def news_svg(idx, title, icon):
    hue=(idx*31)%360
    safe=html.escape(title[:42])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="hsl({hue},48%,22%)"/><stop offset="1" stop-color="hsl({(hue+55)%360},62%,44%)"/></linearGradient></defs><rect width="1200" height="675" fill="url(#g)"/><circle cx="1000" cy="110" r="170" fill="#fff" opacity=".08"/><circle cx="170" cy="560" r="220" fill="#fff" opacity=".06"/><rect x="90" y="100" width="1020" height="475" rx="38" fill="#fff" opacity=".08"/><text x="600" y="300" text-anchor="middle" font-family="Arial" font-size="92" font-weight="700" fill="#fff">{html.escape(icon)}</text><text x="600" y="410" text-anchor="middle" font-family="Arial" font-size="38" font-weight="700" fill="#fff">{safe}</text><text x="600" y="475" text-anchor="middle" font-family="Arial" font-size="22" fill="#fff" opacity=".85">منصة التوظيف العربية</text></svg>'''

def run_repair():
    users=secure_storage.load_users() or []
    jobs=secure_storage.load_jobs() or []
    news=secure_storage.load_news() or []
    AVATAR_DIR.mkdir(parents=True, exist_ok=True); NEWS_DIR.mkdir(parents=True, exist_ok=True)
    changed_users=False; changed_jobs=False; changed_news=False
    # فقط الحسابات التجريبية الموجودة حالياً: المحذوف منها لا يعاد.
    for u in users:
        uid=str(u.get('id',''))
        if not uid.startswith('demo_seeker_') and not uid.startswith('demo_employer_'):
            continue
        try: idx=int(uid.rsplit('_',1)[1])
        except: continue
        is_emp=uid.startswith('demo_employer_')
        first=FIRST_NAMES[(idx-1)%len(FIRST_NAMES)] if not is_emp else FIRST_NAMES[(idx*2)%len(FIRST_NAMES)]
        last=LAST_NAMES[(idx*3-1)%len(LAST_NAMES)] if not is_emp else LAST_NAMES[(idx*5+2)%len(LAST_NAMES)]
        country,city,neighborhood=LOCATIONS[(idx-1)%len(LOCATIONS)] if not is_emp else LOCATIONS[(idx+5)%len(LOCATIONS)]
        u['firstName']=first; u['lastName']=last; u['emailVerified']=True; u['status']='active'
        u['password']=PasswordManager.hash_password(PASSWORD)
        if not u.get('avatar','').startswith('/static/uploads/avatars/'):
            fn=f'demo_{uid}.svg'; (AVATAR_DIR/fn).write_text(svg_avatar(f'{first} {last}', idx, is_emp), encoding='utf-8'); u['avatar']=f'/static/uploads/avatars/{fn}'
        if not is_emp:
            cat,title=CATEGORIES[(idx-1)%len(CATEGORIES)]
            u['country']=country; u['city']=city; u['neighborhood']=neighborhood; u['category']=cat; u['headline']=title
            u['profession']=PROFESSIONS[(idx*7)%len(PROFESSIONS)]
            u['skills']=JOB_TEMPLATES[(idx-1)%len(JOB_TEMPLATES)][2]
            u['languages']='العربية، الإنجليزية'
            u['certifications']='شهادة مهنية في المجال'
            u['experience']=f'{1+(idx%8)} سنوات من الخبرة العملية في المجال'
            u['bio']=f'{title} بخبرة عملية في {cat}، أبحث عن فرصة مهنية أضيف فيها قيمة واضحة وأطوّر خبرتي ضمن فريق احترافي.'
        changed_users=True
    for j in jobs:
        try: jid=int(j.get('id'))
        except: continue
        # لا نلمس وظائف المستخدمين العاديين، فقط الوظائف المرتبطة بحسابات demo.
        if not str(j.get('employerId','')).startswith('demo_employer_'): continue
        idx=(jid%40) or 40
        loc=LOCATIONS[(idx+4)%len(LOCATIONS)]
        jt=JOB_TEMPLATES[(idx-1)%len(JOB_TEMPLATES)]
        cat,title_cat=CATEGORIES[(idx-1)%len(CATEGORIES)]
        j['country'],j['city'],j['neighborhood']=loc
        j['title']=f'{jt[0]} — {loc[1]}'
        j['category']=cat
        j['profession']=PROFESSIONS[(idx*5)%len(PROFESSIONS)]
        j['tags']=[x.strip() for x in jt[2].split(',')]
        changed_jobs=True
    for idx,n in enumerate(news,1):
        try: nid=int(n.get('id'))
        except: nid=idx
        if not n.get('title'): continue
        # الأخبار التجريبية السابقة تُميّزها العناوين الموجودة في seed؛ تحديث الصورة فقط.
        theme=NEWS_THEMES[(idx-1)%len(NEWS_THEMES)]
        fn=f'demo_news_{idx:02d}.svg'; (NEWS_DIR/fn).write_text(news_svg(idx, theme[2], theme[3]), encoding='utf-8')
        if idx<=12 and (str(n.get('title','')).strip() in {x[0] for x in []} or str(n.get('image','')).startswith('/static/uploads/news/news_')):
            n['image']=f'/static/uploads/news/{fn}'; changed_news=True
    if changed_users: secure_storage.save_users(users)
    if changed_jobs: secure_storage.save_jobs(jobs)
    if changed_news: secure_storage.save_news(news)
    secure_storage.encryption.encrypt_file('demo_repair_v2', {'completed':True,'at':datetime.now().isoformat()})
    return {'users':changed_users,'jobs':changed_jobs,'news':changed_news}
