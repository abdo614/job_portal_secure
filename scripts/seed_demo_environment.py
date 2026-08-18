#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
إنشاء بيئة تجريبية واقعية لمنصة التوظيف.
- 100 حساب تجريبي: 60 باحثاً عن عمل + 40 صاحب عمل.
- كلمة المرور الموحدة: Demo@2026!
- 40 وظيفة منشورة + 12 خبراً + طلبات تقديم ومفضلة ومحافظ.
- التشغيل افتراضي لمرة واحدة فقط. حذف البيانات من لوحة الإدارة لا يعيدها تلقائياً.
- لإعادة بناء البيئة التجريبية استخدم: python scripts/seed_demo_environment.py --force
"""
import argparse, os, random, re, sys
from datetime import datetime, timedelta
from pathlib import Path

# Allow this script to be run directly from the project root with:
#   python scripts/seed_demo_environment.py --force
# When Python executes a script by path, it puts the scripts/ directory
# on sys.path instead of the project root. Add the project root explicitly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from encryption import secure_storage, PasswordManager
from wallet_service import create_wallet
from professions import PROFESSIONS, PROFESSION_GROUPS

BASE = Path(__file__).resolve().parents[1]
SEED_NAME = "demo_seed_state"

PASSWORD = "Demo@2026!"
SEED_VERSION = 2
random.seed(20260817)

FIRST_NAMES = [
    "أحمد","محمد","عمر","ياسر","سامي","خالد","طارق","وليد","معاذ","إياد",
    "رامي","مروان","حسام","أنس","زياد","باسم","سيف","مازن","علي","حازم",
    "نور","ليان","سارة","ريم","مريم","دانا","جود","لارا","رنا","هدى",
    "سلمى","فرح","تالا","جنى","آية","نورا","شهد","ميس","لينا","رؤى"
]
LAST_NAMES = [
    "الحمادي","المنصور","العتيبي","الحربي","الزهراني","التميمي","الشهري","الغامدي",
    "الشمري","السالم","الحداد","النجار","الخطيب","الرفاعي","المرزوقي","الأنصاري",
    "العمري","السعدي","القحطاني","الدوسري","الهاشمي","الراشد","المالكي","العباسي",
    "النعيمي","النجدي","المرعي","السليمان","الهادي","الكيلاني"
]
LATIN_FIRST_NAMES = ["ahmad","mohammad","omar","yasser","sami","khaled","tariq","waleed","moath","eyad","rami","marwan","hossam","anas","ziyad","basem","saif","mazen","ali","hazem","noor","layan","sara","reem","maryam","dana","joud","lara","rana","huda","salma","farah","tala","jana","aya","noura","shahd","mais","lina","roua"]
LATIN_LAST_NAMES = ["almansour","alotaibi","alharbi","alzahrani","altamimi","alshammari","alsalem","alhaddad","alnajjar","alkhatib","alrifai","alansari","alomari","alsaadi","almarzouqi","alhashemi","alrashed","almaliki","alabbasi","alnaimi","alnajdi","almarai","alsulaiman","alhadi","alkilani","alhamadi","aldosari","alqhtani","alghamdi","alshahri"]
LOCATIONS = [
    ("السعودية","الرياض","العليا"),("السعودية","جدة","الروضة"),("السعودية","الدمام","الشاطئ"),
    ("السعودية","الخبر","الراكة"),("مصر","القاهرة","مدينة نصر"),("مصر","الجيزة","الدقي"),
    ("مصر","الإسكندرية","سموحة"),("الإمارات","دبي","الخليج التجاري"),("الإمارات","أبوظبي","الخالدية"),
    ("الإمارات","الشارقة","النهدة"),("الأردن","عمّان","الشميساني"),("قطر","الدوحة","السد"),
    ("الكويت","مدينة الكويت","شرق"),("عُمان","مسقط","الخوير"),("البحرين","المنامة","السيف"),
    ("لبنان","بيروت","الحمرا"),("المغرب","الدار البيضاء","المعاريف"),("المغرب","الرباط","أكدال"),
    ("الجزائر","الجزائر العاصمة","حيدرة"),("تونس","تونس","المنزه")
]
COMPANIES = [
    ("أفق التقنية","تقنية المعلومات","حلول رقمية متخصصة في البرمجيات السحابية والتحول الرقمي للشركات."),
    ("نواة للحلول الذكية","تقنية المعلومات","شركة إقليمية تطور منتجات SaaS وأدوات تحليل البيانات."),
    ("مدار اللوجستية","اللوجستيات","خدمات لوجستية وسلاسل إمداد تربط الأسواق العربية."),
    ("بيوت المستقبل","العقارات","منصة عقارية تركز على التجارب الرقمية وإدارة المشاريع."),
    ("بوصلة المالية","المالية","خدمات مالية رقمية وحلول دفع للشركات الصغيرة والمتوسطة."),
    ("سحابة الأعمال","الاستشارات","استشارات تشغيلية وتقنية لرفع كفاءة المؤسسات."),
    ("مسار الطاقة","الطاقة","مشاريع الطاقة والاستدامة وحلول كفاءة الاستهلاك."),
    ("واجهة الإعلام","الإعلام والتسويق","وكالة إبداعية تقدم التسويق الرقمي وصناعة المحتوى."),
    ("رؤية الصحية","الرعاية الصحية","خدمات تقنية وإدارية للقطاع الصحي."),
    ("إمداد الصناعات","الصناعة","حلول تصنيع وتوريد للمصانع والمنشآت الصناعية."),
    ("رواد التجارة","التجارة الإلكترونية","منصة تجارة إلكترونية تخدم العلامات المحلية."),
    ("جسر التعليم","التعليم","حلول تعليمية رقمية وتدريب مهني مستمر.")
]

GROUP_CATEGORY = {
    'البناء والتشييد':'بناء','الكهرباء والطاقة':'هندسة','السباكة والتكييف':'هندسة','النجارة والديكور':'فنون',
    'الملابس والخياطة':'فنون','الطعام والضيافة':'ضيافة','السيارات والنقل':'نقل','المصانع والإنتاج':'صناعة',
    'الصيانة والخدمات':'خدمة','الزراعة والثروة الحيوانية':'زراعة','الصحة والرعاية':'طب','التعليم والتدريب':'تعليم',
    'التقنية والبرمجيات':'تقنية','التسويق والمبيعات':'تسويق','الإدارة والمال':'إدارة','القانون والإعلام':'قانون',
    'الجمال والعناية':'فنون','السياحة والفنادق':'ضيافة','الحرف والمهن اليدوية':'فنون'
}
GROUP_SKILLS = {
    'التعليم والتدريب':['التدريس','إدارة الصف','التواصل','إعداد المناهج'],
    'السيارات والنقل':['القيادة الآمنة','الصيانة الأساسية','السلامة المرورية'],
    'التقنية والبرمجيات':['Python','SQL','Git','حل المشكلات'],
    'التسويق والمبيعات':['التسويق الرقمي','CRM','التواصل','تحليل البيانات'],
    'الإدارة والمال':['Excel','إدارة الوقت','التقارير','التواصل'],
    'الصحة والرعاية':['سلامة المرضى','التواصل','الإسعافات الأولية'],
    'البناء والتشييد':['السلامة المهنية','AutoCAD','قراءة المخططات'],
    'الكهرباء والطاقة':['السلامة الكهربائية','قراءة المخططات','الصيانة'],
    'السباكة والتكييف':['الصيانة','السلامة المهنية','تشخيص الأعطال'],
    'المصانع والإنتاج':['السلامة المهنية','مراقبة الجودة','تشغيل الآلات'],
    'الزراعة والثروة الحيوانية':['الري','السلامة المهنية','إدارة المحاصيل'],
    'الطعام والضيافة':['سلامة الغذاء','خدمة العملاء','إدارة الوقت'],
    'السياحة والفنادق':['خدمة العملاء','إدارة الحجوزات','التواصل'],
    'القانون والإعلام':['البحث','الكتابة','التواصل'],
    'الجمال والعناية':['خدمة العملاء','العناية الشخصية','التواصل'],
    'النجارة والديكور':['قراءة المخططات','التشطيب','السلامة المهنية'],
    'الملابس والخياطة':['التفصيل','التطريز','مراقبة الجودة'],
    'الصيانة والخدمات':['السلامة المهنية','الصيانة','خدمة العملاء'],
    'الحرف والمهن اليدوية':['الحرف اليدوية','مراقبة الجودة','التصميم']
}
GROUP_CERTS = {
    'التعليم والتدريب':'شهادة تدريس','السيارات والنقل':'رخصة قيادة مهنية','التقنية والبرمجيات':'AWS Certified',
    'التسويق والمبيعات':'Google Analytics','الإدارة والمال':'Microsoft Office Specialist','الصحة والرعاية':'شهادة إسعافات أولية',
    'البناء والتشييد':'شهادة سلامة مهنية','الكهرباء والطاقة':'شهادة سلامة مهنية','السباكة والتكييف':'شهادة سلامة مهنية',
    'المصانع والإنتاج':'شهادة سلامة مهنية','الزراعة والثروة الحيوانية':'شهادة سلامة مهنية','الطعام والضيافة':'شهادة سلامة غذاء',
    'السياحة والفنادق':'خدمة ضيوف معتمدة','القانون والإعلام':'شهادة مهنية','الجمال والعناية':'شهادة مهنية',
    'النجارة والديكور':'شهادة سلامة مهنية','الملابس والخياطة':'شهادة مهنية','الصيانة والخدمات':'شهادة سلامة مهنية','الحرف والمهن اليدوية':'شهادة مهنية'
}

def profession_for_group(group, idx):
    items=PROFESSION_GROUPS.get(group, [])
    return items[idx % len(items)] if items else PROFESSIONS[idx % len(PROFESSIONS)]

def group_for_category(category):
    mapping={
        'تقنية':'التقنية والبرمجيات','هندسة':'الكهرباء والطاقة','طب':'الصحة والرعاية','تعليم':'التعليم والتدريب',
        'مالية':'الإدارة والمال','تسويق':'التسويق والمبيعات','إدارة':'الإدارة والمال','خدمة':'الصيانة والخدمات',
        'قانون':'القانون والإعلام','فنون':'الجمال والعناية','نقل':'السيارات والنقل','بناء':'البناء والتشييد',
        'صناعة':'المصانع والإنتاج','زراعة':'الزراعة والثروة الحيوانية','ضيافة':'الطعام والضيافة'}
    return mapping.get(category,'التقنية والبرمجيات')

CATEGORIES = [
    ("تقنية المعلومات","مهندس برمجيات","تطوير تطبيقات ويب حديثة والعمل ضمن فريق متعدد التخصصات."),
    ("تقنية المعلومات","محلل بيانات","تحليل البيانات وبناء لوحات مؤشرات تساعد الإدارة على اتخاذ القرار."),
    ("التسويق","أخصائي تسويق رقمي","إدارة الحملات الرقمية والمحتوى وتحليل الأداء."),
    ("الموارد البشرية","أخصائي موارد بشرية","إدارة عمليات التوظيف والتطوير وتجربة الموظف."),
    ("المبيعات","تنفيذي مبيعات","بناء علاقات العملاء وإدارة دورة المبيعات وتحقيق المستهدفات."),
    ("المالية","محاسب أول","إعداد التقارير المالية ومتابعة الحسابات والإقفال الشهري."),
    ("التصميم","مصمم UI/UX","تصميم تجارب رقمية سهلة وجذابة وتحويل المتطلبات إلى واجهات."),
    ("اللوجستيات","منسق سلاسل إمداد","متابعة الشحنات والمخزون وتحسين العمليات التشغيلية."),
    ("الهندسة","مهندس مشاريع","إدارة مراحل المشروع والتنسيق مع الفرق والموردين."),
    ("خدمة العملاء","أخصائي تجربة عميل","تحسين رحلة العميل ومعالجة الطلبات وقياس الرضا.")
]
JOB_TEMPLATES = [
    ("مهندس برمجيات أول","دوام كامل","18,000 - 24,000 ريال","Python, FastAPI, PostgreSQL, Docker"),
    ("مطور تطبيقات ويب","دوام كامل","12,000 - 18,000 ريال","React, TypeScript, REST API"),
    ("محلل بيانات","دوام كامل","11,000 - 16,000 ريال","SQL, Power BI, Python"),
    ("مدير منتج رقمي","دوام كامل","16,000 - 23,000 ريال","Product, Agile, Analytics"),
    ("أخصائي تسويق رقمي","دوام كامل","9,000 - 14,000 ريال","SEO, Google Ads, Content"),
    ("مصمم UI/UX","دوام كامل","10,000 - 15,000 ريال","Figma, Design Systems, Research"),
    ("أخصائي موارد بشرية","دوام كامل","8,000 - 13,000 ريال","Recruitment, HRIS, Employee Experience"),
    ("تنفيذي مبيعات B2B","دوام كامل","9,000 - 16,000 ريال + عمولة","B2B, CRM, Negotiation"),
    ("مهندس مشاريع","دوام كامل","13,000 - 20,000 ريال","Project Management, AutoCAD, Planning"),
    ("منسق سلاسل إمداد","دوام كامل","8,000 - 12,000 ريال","Supply Chain, ERP, Excel"),
]

NEWS = [
    ("المنصة تطلق تجربة بحث وظائف أسرع وأكثر ذكاءً","تقنية","تحسينات جديدة تساعد الباحثين عن عمل على الوصول إلى الفرص المناسبة خلال وقت أقل، مع نتائج أكثر دقة حسب المهارات والموقع."),
    ("نصائح عملية لبناء سيرة ذاتية تلفت انتباه مسؤولي التوظيف","تطوير مهني","السيرة الذاتية الجيدة ليست قائمة وظائف فقط؛ تنظيم الإنجازات والمهارات والكلمات المفتاحية المناسبة يصنع فرقاً واضحاً."),
    ("كيف تستعد لمقابلة عمل عن بُعد باحترافية؟","تطوير مهني","من جودة الاتصال إلى اختبار الكاميرا وترتيب الإجابات، إليك خطوات بسيطة تجعل المقابلة عن بُعد أكثر احترافية."),
    ("الوظائف الرقمية تواصل نموها في المنطقة العربية","سوق العمل","تتوسع الشركات في أدوار البرمجة والبيانات والمنتجات الرقمية، ما يفتح مسارات جديدة للمهارات التقنية."),
    ("5 مهارات مطلوبة في فرق العمل الحديثة","مهارات","التواصل، حل المشكلات، التفكير التحليلي، إدارة الوقت والقدرة على التعلم أصبحت عناصر أساسية في معظم الأدوار."),
    ("الشركات الناشئة تبحث عن مواهب تجمع التقنية وفهم الأعمال","سوق العمل","تزايد الطلب على المواهب القادرة على الربط بين المنتج واحتياجات العملاء والنتائج التجارية."),
    ("كيف تختار العرض الوظيفي المناسب؟","تطوير مهني","الراتب مهم، لكنه ليس العامل الوحيد. قارن مسار النمو، بيئة العمل، المسؤوليات، المرونة والمزايا قبل اتخاذ القرار."),
    ("دليل مختصر لكتابة رسالة تقديم مؤثرة","تطوير مهني","رسالة التقديم الجيدة تربط خبرتك باحتياج الوظيفة مباشرة، وتبتعد عن العبارات العامة والمكررة."),
    ("العمل المرن يغيّر طريقة بناء فرق العمل","سوق العمل","العمل الهجين والمرن أصبحا جزءاً من نماذج تشغيل كثيرة، مع تركيز أكبر على النتائج والتواصل الواضح."),
    ("من المهارة إلى الفرصة: كيف تعرض خبرتك بوضوح؟","مهارات","بدلاً من سرد المهام، ركز على النتائج والأثر والأدوات التي استخدمتها والمشكلات التي تمكنت من حلها."),
    ("الاستثمار في التعلم المستمر يرفع جاهزية الباحثين عن عمل","تطوير مهني","خطة تعلم صغيرة ومنتظمة يمكن أن تكون أكثر فاعلية من جمع دورات كثيرة دون تطبيق عملي."),
    ("منصة التوظيف العربية توسع محتواها الإرشادي للباحثين وأصحاب العمل","أخبار المنصة","نواصل تطوير تجربة تجمع بين اكتشاف الفرص، إدارة التقديمات، وبناء حضور مهني أفضل للطرفين.")
]

def next_id(items):
    nums=[]
    for x in items or []:
        try: nums.append(int(x.get("id")))
        except: pass
    return max(nums, default=0)+1

def already_seeded():
    state = secure_storage.encryption.decrypt_file(SEED_NAME) or {}
    return state.get("version") == SEED_VERSION and state.get("completed") is True

def clear_previous_demo():
    state = secure_storage.encryption.decrypt_file(SEED_NAME) or {}
    demo_ids=set(state.get("user_ids",[]))
    job_ids=set(str(x) for x in state.get("job_ids",[]))
    news_ids=set(str(x) for x in state.get("news_ids",[]))
    users=secure_storage.load_users() or []
    jobs=secure_storage.load_jobs() or []
    news=secure_storage.load_news() or []
    users=[u for u in users if str(u.get("id")) not in demo_ids]
    jobs=[j for j in jobs if str(j.get("id")) not in job_ids]
    news=[n for n in news if str(n.get("id")) not in news_ids]
    secure_storage.save_users(users); secure_storage.save_jobs(jobs); secure_storage.save_news(news)
    return users,jobs,news

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="إعادة إنشاء البيانات التجريبية السابقة")
    args=ap.parse_args()
    if already_seeded() and not args.force:
        print("تم إنشاء البيئة التجريبية مسبقاً؛ لن يتم تعديلها حتى لا تعود البيانات التي حذفها المدير.")
        return

    users=secure_storage.load_users() or []
    jobs=secure_storage.load_jobs() or []
    news=secure_storage.load_news() or []
    if args.force:
        users,jobs,news=clear_previous_demo()

    existing_ids={str(u.get("id")) for u in users}
    password_hash=PasswordManager.hash_password(PASSWORD)
    now=datetime.now()

    seeker_ids=[]; employer_ids=[]
    # 60 باحثاً عن عمل
    for idx in range(1,61):
        first=FIRST_NAMES[(idx-1)%len(FIRST_NAMES)]
        last=LAST_NAMES[(idx*3-1)%len(LAST_NAMES)]
        country,city,neighborhood=LOCATIONS[(idx-1)%len(LOCATIONS)]
        category,title,desc=CATEGORIES[(idx-1)%len(CATEGORIES)]
        group=group_for_category(category)
        profession=profession_for_group(group, idx)
        uid=f"demo_seeker_{idx:03d}"
        if uid in existing_ids: continue
        u={
            "id":uid,"username":f"candidate{idx:03d}","firstName":first,"lastName":last,
            "email":f"{LATIN_FIRST_NAMES[(idx-1)%len(LATIN_FIRST_NAMES)]}.{LATIN_LAST_NAMES[(idx*3-1)%len(LATIN_LAST_NAMES)]}.{idx:02d}@demo.arabjobs.test",
            "password":password_hash,"phone":f"+9665{70000000+idx:08d}","phoneCountryCode":"+966",
            "category":category,"country":country,"city":city,"neighborhood":neighborhood,
            "birthdate":f"{1987+(idx%12):04d}-{1+(idx%12):02d}-{5+(idx%20):02d}",
            "education":["بكالوريوس","ماجستير","دبلوم مهني"][idx%3],
            "registeredAt":(now-timedelta(days=idx%45,hours=idx%12)).isoformat(),
            "role":"job_seeker","status":"active","emailVerified":True,
            "headline":title,"profession":profession,"bio":f"{desc} أبحث عن فرصة أستطيع من خلالها تطوير خبرتي وتحقيق أثر واضح ضمن فريق احترافي.",
            "skills":GROUP_SKILLS.get(group, ['التواصل','العمل الجماعي']),
            "languages":"العربية، الإنجليزية",
            "certifications":GROUP_CERTS.get(group, 'شهادة مهنية'),
            "experience":f"{1+(idx%8)} سنوات من الخبرة العملية في المجال",
            "resume":f"السيرة الذاتية لـ {first} {last} — خبرة عملية، مشاريع، مهارات تقنية ومهنية.",
            "avatar":f"/static/uploads/avatars/demo_seeker_{idx:03d}.svg",
            "sadaqahFreeApplicationsUsed":idx%3
        }
        users.append(u); seeker_ids.append(uid)

    # 40 أصحاب عمل
    for idx in range(1,41):
        first=FIRST_NAMES[(idx*2)%len(FIRST_NAMES)]
        last=LAST_NAMES[(idx*5+2)%len(LAST_NAMES)]
        country,city,neighborhood=LOCATIONS[(idx+5)%len(LOCATIONS)]
        company,ctype,cdesc=COMPANIES[(idx-1)%len(COMPANIES)]
        uid=f"demo_employer_{idx:03d}"
        if uid in existing_ids: continue
        email_company=re.sub(r"[^a-z0-9]+","-",company.lower()).strip("-")
        u={
            "id":uid,"username":f"company{idx:03d}","firstName":first,"lastName":last,
            "email":f"talent{idx:02d}@{email_company or 'demo'}.arabjobs.test",
            "password":password_hash,"phone":f"+9665{80000000+idx:08d}","phoneCountryCode":"+966",
            "country":country,"city":city,"neighborhood":neighborhood,
            "category":ctype,"education":"بكالوريوس","registeredAt":(now-timedelta(days=idx%50)).isoformat(),
            "role":"employer","status":"active","emailVerified":True,
            "companyName":company,"companyType":ctype,"companyDescription":cdesc,
            "companyWebsite":f"https://www.{email_company or 'company'}.example",
            "companyFounded":str(2008+(idx%15)),
            "avatar":f"/static/uploads/avatars/demo_employer_{idx:03d}.svg",
            "sadaqahFreeJobPostsUsed":idx%3,"sadaqahFreeUnlocksUsed":idx%3
        }
        users.append(u); employer_ids.append(uid)

    # 40 وظائف مرتبطة بأصحاب العمل
    job_ids=[]
    start_job=next_id(jobs)
    for idx in range(1,41):
        category,title_base,desc_base=CATEGORIES[(idx-1)%len(CATEGORIES)]
        jt=JOB_TEMPLATES[(idx-1)%len(JOB_TEMPLATES)]
        group=group_for_category(category)
        company=COMPANIES[(idx-1)%len(COMPANIES)][0]
        emp_id=employer_ids[(idx-1)%len(employer_ids)]
        emp=next(u for u in users if u["id"]==emp_id)
        country,city,neighborhood=LOCATIONS[(idx+4)%len(LOCATIONS)]
        jid=start_job+idx-1
        created=now-timedelta(days=(idx*2)%35,hours=idx%9)
        job={
            "id":jid,"title":f"{jt[0]} — {city}",
            "company":company,"country":country,"city":city,"neighborhood":neighborhood,
            "category":category,"profession":profession_for_group(group, idx),"salary":jt[2],"employmentType":jt[1],
            "description":f"{desc_base} ستعمل مع فريق متعاون على مشاريع ذات أثر واضح، مع مساحة للتعلم وتحمل المسؤولية.",
            "tags":[x.strip() for x in jt[3].split(",")],
            "employerId":emp_id,"employerEmail":emp["email"],"posted":created.strftime("%Y-%m-%d"),
            "createdAt":created.isoformat(),"status":"published","featured":idx%7==0,
            "remote":idx%4==0
        }
        jobs.append(job); job_ids.append(jid)

    # 12 أخبار
    news_ids=[]
    start_news=next_id(news)
    for idx,(title,cat,content) in enumerate(NEWS,1):
        nid=start_news+idx-1
        created=now-timedelta(days=idx*2)
        news.append({
            "id":nid,"title":title,"category":cat,"content":content,
            "excerpt":content[:145]+"…","date":created.strftime("%Y-%m-%d"),
            "createdAt":created.isoformat(),"status":"منشور",
            "image":f"/static/uploads/news/demo_news_{idx:02d}.svg"
        })
        news_ids.append(nid)

    # محافظ مستقلة لكل الحسابات التجريبية (رصيد رمزي متنوع للاختبار)
    wallets=secure_storage.encryption.decrypt_file("wallets") or []
    wallet_by={str(w.get("employerId")):w for w in wallets if isinstance(w,dict)}
    for idx,uid in enumerate(seeker_ids+employer_ids,1):
        if uid in wallet_by: continue
        amount=0 if uid.startswith("demo_seeker_") else (1500 + (idx%6)*500)
        wallets.append({
            "employerId":uid,"balance":amount,"currency":"SAR",
            "formattedBalance":f"{amount/100:.2f} ر.س","totalEarnings":amount,
            "totalWithdrawn":0,"pendingWithdrawals":0,"availableBalance":amount,
            "status":"active","createdAt":(now-timedelta(days=idx%40)).isoformat(),"updatedAt":now.isoformat()
        })

    # طلبات تقديم واقعية ومفضلة حتى تظهر لوحات الإدارة ممتلئة.
    applications=secure_storage.load_applications() or {}
    favorites=secure_storage.load_favorites() or {}
    statuses=["pending","reviewing","shortlisted","rejected"]
    for idx,uid in enumerate(seeker_ids):
        selected=[jobs[(idx*3+j)%len(jobs)] for j in range(1,4+(idx%3))]
        applications.setdefault(uid,[])
        existing_app_job={str(a.get("jobId")) for a in applications[uid]}
        for j,job in enumerate(selected):
            if str(job["id"]) in existing_app_job: continue
            at=now-timedelta(days=(idx+j)%20,hours=(idx+j)%10)
            applications[uid].append({
                "jobId":job["id"],"appliedAt":at.isoformat(),
                "status":statuses[(idx+j)%len(statuses)],
                "timeline":[{"status":"pending","label":"تم إرسال الطلب","at":at.isoformat()}],
                "paymentRequired":False,"chargedAmountUsdCents":0,
                "coverLetter":f"أتقدم لهذه الفرصة لأن خبرتي في {job['category']} تتوافق مع متطلبات الدور، ويسعدني مناقشة كيف يمكنني إضافة قيمة للفريق.",
                "answers":{},"cvId":"","updatedAt":at.isoformat()
            })
        favorites.setdefault(uid,[])
        favorites[uid]=list(dict.fromkeys(favorites[uid]+[j["id"] for j in selected[:2]]))

    # أصول محلية مستقلة حتى لا تعتمد صور الحسابات والأخبار على خدمات خارجية.
    avatar_dir = BASE / "static" / "uploads" / "avatars"; avatar_dir.mkdir(parents=True, exist_ok=True)
    news_dir = BASE / "static" / "uploads" / "news"; news_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(1,61):
        name=f"{FIRST_NAMES[(idx-1)%len(FIRST_NAMES)]} {LAST_NAMES[(idx*3-1)%len(LAST_NAMES)]}"
        (avatar_dir/f"demo_seeker_{idx:03d}.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" rx="70" fill="#1a4a6e"/><circle cx="160" cy="120" r="58" fill="#f1c7a8"/><path d="M100 112c8-58 112-66 121 2-30-20-85-18-121-2z" fill="#2b2530"/><path d="M70 300c8-85 55-120 90-120s82 35 90 120" fill="#f4f7fb"/><text x="160" y="305" text-anchor="middle" font-family="Arial" font-size="22" fill="#17364c">{name[0]}</text></svg>', encoding="utf-8")
    for idx in range(1,41):
        company=COMPANIES[(idx-1)%len(COMPANIES)][0]
        (avatar_dir/f"demo_employer_{idx:03d}.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"><rect width="320" height="320" rx="70" fill="#0d2b3e"/><circle cx="160" cy="115" r="58" fill="#e8b997"/><path d="M95 115c5-55 125-62 130 5-36-17-93-17-130-5z" fill="#28202a"/><path d="M65 300c10-82 58-115 95-115s84 33 95 115" fill="#dbe8f2"/><text x="160" y="300" text-anchor="middle" font-family="Arial" font-size="20" fill="#17364c">{company[:2]}</text></svg>', encoding="utf-8")
    for idx,(title,cat,content) in enumerate(NEWS,1):
        (news_dir/f"demo_news_{idx:02d}.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><defs><linearGradient id="g" x1="0" x2="1"><stop stop-color="#0d2b3e"/><stop offset="1" stop-color="#2a6a9e"/></linearGradient></defs><rect width="1200" height="675" fill="url(#g)"/><circle cx="1000" cy="120" r="170" fill="#fff" opacity=".08"/><text x="600" y="285" text-anchor="middle" font-family="Arial" font-size="74" font-weight="700" fill="#fff">{cat}</text><text x="600" y="385" text-anchor="middle" font-family="Arial" font-size="34" font-weight="700" fill="#fff">{title[:42]}</text><text x="600" y="455" text-anchor="middle" font-family="Arial" font-size="21" fill="#fff" opacity=".85">منصة التوظيف العربية</text></svg>', encoding="utf-8")

    secure_storage.save_users(users)
    secure_storage.save_jobs(jobs)
    secure_storage.save_news(news)
    secure_storage.save_applications(applications)
    secure_storage.save_favorites(favorites)
    secure_storage.encryption.encrypt_file("wallets",wallets)
    secure_storage.encryption.encrypt_file(SEED_NAME,{
        "version":SEED_VERSION,"completed":True,
        "createdAt":now.isoformat(),"passwordHint":"Demo@2026!",
        "user_ids":seeker_ids+employer_ids,"job_ids":job_ids,"news_ids":news_ids
    })
    print(f"تم إنشاء {len(seeker_ids)+len(employer_ids)} حساباً تجريبياً، {len(job_ids)} وظيفة، {len(news_ids)} خبراً.")
    print("كلمة المرور الموحدة للحسابات التجريبية:", PASSWORD)
    print("لن تعود البيانات المحذوفة تلقائياً؛ إعادة البناء تتطلب --force.")

if __name__=="__main__":
    main()
