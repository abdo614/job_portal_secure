# -*- coding: utf-8 -*-
"""إضافة 350 وظيفة تجريبية متنوعة لأصحاب العمل التجريبيين، وإخفاء بيانات المصادر من الأخبار."""
from pathlib import Path
import json, re
from datetime import datetime, timedelta
from cryptography.fernet import Fernet

BASE=Path(__file__).resolve().parents[1]
DATA=BASE/'data'
cipher=Fernet((DATA/'.key').read_bytes())

def load(name):
    return json.loads(cipher.decrypt((DATA/f'{name}.enc').read_bytes()).decode('utf-8'))

def save(name,obj):
    (DATA/f'{name}.enc').write_bytes(cipher.encrypt(json.dumps(obj,ensure_ascii=False).encode('utf-8')))

# 35 وظائف أساسية × 10 مسارات = 350 عنواناً مختلفاً.
ROLES=[
('مهندس برمجيات','تقنية المعلومات',['Python','JavaScript','Git'],'تطوير تطبيقات وخدمات ويب قابلة للتوسع وتحسين جودة الشيفرة.'),
('مطور تطبيقات جوال','تقنية المعلومات',['Flutter','Dart','REST API'],'بناء تطبيقات جوال وتحسين الأداء وتجربة المستخدم عبر المنصات.'),
('مهندس بيانات','تحليل البيانات',['Python','SQL','ETL'],'تصميم خطوط البيانات ومعالجة المصادر وبناء حلول موثوقة للتحليل.'),
('محلل بيانات','تحليل البيانات',['SQL','Power BI','Excel'],'تحليل البيانات وإعداد لوحات مؤشرات وتقارير تساعد الإدارة على اتخاذ القرار.'),
('مهندس تعلم آلي','الذكاء الاصطناعي',['Python','Machine Learning','TensorFlow'],'تطوير نماذج تعلم آلي وتجارب تقييم وتحسين جودة النتائج.'),
('محلل أمن سيبراني','الأمن السيبراني',['SIEM','Networking','Incident Response'],'مراقبة الأحداث الأمنية وتحليل التنبيهات ودعم الاستجابة للحوادث.'),
('مهندس شبكات','البنية التحتية',['Networking','Cisco','TCP/IP'],'إدارة الشبكات ومراقبة الأداء ومعالجة أعطال الاتصال والبنية التحتية.'),
('مسؤول أنظمة','البنية التحتية',['Linux','Windows Server','Virtualization'],'تشغيل الأنظمة والخوادم ومتابعة النسخ الاحتياطي والتحديثات.'),
('مهندس سحابة','الحوسبة السحابية',['AWS','Azure','Docker'],'تصميم وإدارة الخدمات السحابية وتحسين الاعتمادية والتكلفة.'),
('مهندس DevOps','الحوسبة السحابية',['CI/CD','Docker','Kubernetes'],'أتمتة النشر والمراقبة وتحسين دورة حياة تطوير البرمجيات.'),
('مصمم UI/UX','التصميم',['Figma','UX Research','Design Systems'],'تصميم تجارب وواجهات رقمية مبنية على احتياجات المستخدم وبيانات الاستخدام.'),
('مصمم جرافيك','التصميم',['Adobe Illustrator','Photoshop','Branding'],'إنتاج مواد بصرية للحملات والمنصات الرقمية مع الحفاظ على هوية العلامة.'),
('كاتب محتوى','الإعلام والتسويق',['Arabic Writing','SEO','Editing'],'كتابة محتوى واضح وجذاب للمنصات الرقمية والصفحات والحملات.'),
('أخصائي تسويق رقمي','التسويق',['SEO','Google Ads','Analytics'],'تخطيط الحملات الرقمية وقياس الأداء وتحسين الوصول والتحويلات.'),
('مدير حسابات عملاء','المبيعات',['CRM','Communication','Account Management'],'إدارة علاقات العملاء ومتابعة الاحتياجات وتحويلها إلى فرص نمو.'),
('تنفيذي مبيعات','المبيعات',['B2B','Negotiation','CRM'],'تطوير العملاء المحتملين وإدارة دورة البيع وتحقيق الأهداف التجارية.'),
('مندوب مبيعات ميداني','المبيعات',['Field Sales','Negotiation','Reporting'],'زيارة العملاء وبناء العلاقات ومتابعة الطلبات والفرص في السوق.'),
('أخصائي موارد بشرية','الموارد البشرية',['Recruitment','HRIS','Communication'],'دعم عمليات الموارد البشرية والتوظيف والملفات الوظيفية وتجربة الموظف.'),
('أخصائي توظيف','الموارد البشرية',['Sourcing','Interviewing','ATS'],'البحث عن المواهب وفرز المرشحين وتنسيق المقابلات وإدارة خط التوظيف.'),
('محاسب','المالية',['Accounting','Excel','Reporting'],'تسجيل المعاملات وإعداد التسويات والتقارير المالية الدورية.'),
('محلل مالي','المالية',['Financial Modeling','Excel','Forecasting'],'تحليل النتائج وإعداد التوقعات والنماذج والتقارير الداعمة للقرارات.'),
('مدير مشاريع','إدارة المشاريع',['Project Management','Planning','Risk Management'],'تخطيط المشاريع ومتابعة الجداول والموارد والمخاطر والتنسيق بين الفرق.'),
('منسق عمليات','العمليات',['Operations','Excel','Process Improvement'],'تنسيق العمليات اليومية ومتابعة مؤشرات الأداء وتحسين الإجراءات.'),
('منسق سلاسل إمداد','اللوجستيات',['Supply Chain','ERP','Inventory'],'متابعة المشتريات والمخزون والشحنات وتحسين تدفق المواد.'),
('مخطط طلب وتوريد','اللوجستيات',['Demand Planning','Excel','ERP'],'تحليل الطلب والتنسيق مع التوريد للحفاظ على مستويات مخزون مناسبة.'),
('مهندس جودة','الهندسة',['Quality Control','ISO','Reporting'],'متابعة معايير الجودة وتحليل حالات عدم المطابقة واقتراح التحسينات.'),
('مهندس كهرباء','الهندسة',['AutoCAD','Electrical Systems','Safety'],'تنفيذ ومراجعة الأعمال الكهربائية ودعم الاختبارات والتشغيل وفق متطلبات السلامة.'),
('مهندس مدني','الهندسة',['AutoCAD','Civil 3D','Site Management'],'متابعة الأعمال المدنية والمخططات والكميات والتنسيق مع فرق الموقع.'),
('مهندس طاقة متجددة','الطاقة',['Solar','Energy Analysis','Safety'],'دعم تصميم وتنفيذ مشاريع الطاقة المتجددة وتحليل الأداء ومتطلبات السلامة.'),
('أخصائي تجربة عميل','خدمة العملاء',['CRM','Customer Care','Analytics'],'تحسين رحلة العميل ومتابعة الملاحظات ورفع جودة الخدمة.'),
('موظف خدمة عملاء','خدمة العملاء',['Communication','CRM','Problem Solving'],'استقبال استفسارات العملاء ومعالجة الطلبات وتقديم حلول واضحة وسريعة.'),
('أخصائي مشتريات','المشتريات',['Procurement','Negotiation','ERP'],'إدارة طلبات الشراء ومقارنة العروض والتفاوض مع الموردين.'),
('مساعد إداري','الإدارة',['Microsoft Office','Organization','Communication'],'تنظيم المواعيد والمراسلات والملفات ودعم الأعمال الإدارية اليومية.'),
('باحث قانوني','الشؤون القانونية',['Legal Research','Contracts','Writing'],'إعداد البحوث والمذكرات ومراجعة المستندات والعقود تحت إشراف الفريق القانوني.'),
('أخصائي امتثال','الحوكمة والامتثال',['Compliance','Risk','Reporting'],'متابعة الالتزام بالسياسات والإجراءات وتوثيق الملاحظات وخطط المعالجة.'),
]
TRACKS=['أول','في المنتجات الرقمية','للتحول الرقمي','للعمليات التشغيلية','لخدمة الشركات','للمنصات الإلكترونية','للمشاريع الجديدة','للتوسع الإقليمي','للابتكار وتحسين الأداء','للعمل الهجين']
EMPLOYMENT=['دوام كامل','دوام كامل','دوام كامل','دوام جزئي','دوام كامل','عن بُعد','دوام كامل','عقد محدد المدة','دوام كامل','دوام مرن']
SALARIES=['5,000 - 8,000 ريال','7,000 - 11,000 ريال','8,000 - 13,000 ريال','9,000 - 15,000 ريال','10,000 - 16,000 ريال','12,000 - 18,000 ريال','14,000 - 21,000 ريال','16,000 - 24,000 ريال']

STATE='demo_jobs_expansion_20260817'
users=load('users'); jobs=load('jobs'); news=load('news')

# إزالة بيانات المصدر من الأخبار، مع الإبقاء على الخبر نفسه وتاريخه ومحتواه الواقعي.
SOURCE_KEYS={'source','sourceTitle','sourceUrl','sourceDate','sourceVerified','sourceType'}
for item in news:
    for key in SOURCE_KEYS:
        item.pop(key,None)
    for key in ('content','excerpt'):
        text=item.get(key,'')
        text=re.sub(r'\s*هذا الخبر موثق بمصدره الأصلي وتاريخ نشره، وأُضيف إلى قاعدة بيانات المنصة لأغراض العرض والاختبار\.?','',text)
        text=re.sub(r'\s*هذا الخبر موثق بمصدره الأصلي وتاريخ نشره، وأُضيف إلى قاعدة بيانات المنصة لأغراض العرض والاختبار،?','',text)
        item[key]=text.strip()

employers=[u for u in users if u.get('role')=='employer' and str(u.get('id','')).startswith('demo_extra_employer_')]
if len(employers)!=350:
    raise RuntimeError(f'المتوقع 350 صاحب عمل تجريبي، الموجود: {len(employers)}')

existing_demo_jobs={str(j.get('id')) for j in jobs if str(j.get('employerId','')).startswith('demo_extra_employer_')}
if len(existing_demo_jobs)>=350:
    # لا نكرر الوظائف إذا تم تشغيل السكربت مرة أخرى.
    save('news',news)
    print('تمت الإضافة مسبقاً؛ لم تتم إضافة وظائف مكررة.')
    raise SystemExit(0)

numeric_ids=[]
for j in jobs:
    try: numeric_ids.append(int(j.get('id')))
    except: pass
start=max(numeric_ids,default=0)+1
now=datetime.now().astimezone()
new_ids=[]

for idx,emp in enumerate(employers):
    role_idx=idx % len(ROLES)
    track_idx=idx // len(ROLES)
    role,category,tags,base_desc=ROLES[role_idx]
    track=TRACKS[track_idx]
    country=emp.get('country','السعودية'); city=emp.get('city','الرياض'); neighborhood=emp.get('neighborhood','وسط المدينة')
    title=f'{role} {track} — {city}'
    created=now-timedelta(days=(idx*3)%75,hours=idx%11,minutes=idx%50)
    remote=EMPLOYMENT[idx%len(EMPLOYMENT)]=='عن بُعد'
    desc=(f'تبحث {emp.get("companyName","الشركة")} عن {title} للانضمام إلى فريقها. '
          f'{base_desc} تشمل المسؤوليات اليومية التخطيط والتنفيذ والتعاون مع الفرق ورفع التقارير ومتابعة مؤشرات الأداء. '
          f'نبحث عن شخص منظم، مبادر، وقادر على التعلم والعمل ضمن فريق متعدد التخصصات.')
    job={
        'id':start+idx,
        'title':title,
        'company':emp.get('companyName','شركة تجريبية'),
        'country':country,'city':city,'neighborhood':neighborhood,
        'category':category,'profession':role,
        'salary':SALARIES[(idx+role_idx)%len(SALARIES)],
        'employmentType':EMPLOYMENT[idx%len(EMPLOYMENT)],
        'description':desc,
        'tags':tags,
        'employerId':emp['id'],'employerEmail':emp['email'],
        'posted':created.strftime('%Y-%m-%d'),'createdAt':created.isoformat(),
        'status':'published','featured':idx%17==0,'remote':remote
    }
    jobs.append(job); new_ids.append(job['id'])

save('jobs',jobs); save('news',news)
state={'version':1,'completed':True,'createdAt':now.isoformat(),'job_ids':new_ids,'counts':{'jobs_added':len(new_ids),'news_sources_removed':len(news)}}
save(STATE,state)
print(f'أضيفت {len(new_ids)} وظيفة، وأزيلت بيانات المصادر من {len(news)} خبراً.')
