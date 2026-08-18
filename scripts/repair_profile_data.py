# -*- coding: utf-8 -*-
"""إصلاح بيانات ملفات Demo القديمة دون إنشاء/حذف أي حساب أو وظيفة أو خبر."""
from pathlib import Path
from datetime import datetime
import sys, re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from encryption import secure_storage
from professions import PROFESSION_GROUPS

GROUP_CATEGORY={
 'البناء والتشييد':'بناء','الكهرباء والطاقة':'هندسة','السباكة والتكييف':'هندسة','النجارة والديكور':'فنون',
 'الملابس والخياطة':'فنون','الطعام والضيافة':'ضيافة','السيارات والنقل':'نقل','المصانع والإنتاج':'صناعة',
 'الصيانة والخدمات':'خدمة','الزراعة والثروة الحيوانية':'زراعة','الصحة والرعاية':'طب','التعليم والتدريب':'تعليم',
 'التقنية والبرمجيات':'تقنية','التسويق والمبيعات':'تسويق','الإدارة والمال':'إدارة','القانون والإعلام':'قانون',
 'الجمال والعناية':'فنون','السياحة والفنادق':'ضيافة','الحرف والمهن اليدوية':'فنون'
}
GROUP_SKILLS={
 'التعليم والتدريب':['التدريس','إدارة الصف','التواصل','إعداد المناهج'], 'السيارات والنقل':['القيادة الآمنة','الصيانة الأساسية','السلامة المرورية'],
 'التقنية والبرمجيات':['Python','SQL','Git','حل المشكلات'], 'التسويق والمبيعات':['التسويق الرقمي','CRM','التواصل','تحليل البيانات'],
 'الإدارة والمال':['Excel','إدارة الوقت','التقارير','التواصل'], 'الصحة والرعاية':['سلامة المرضى','التواصل','الإسعافات الأولية'],
 'البناء والتشييد':['السلامة المهنية','AutoCAD','قراءة المخططات'], 'الكهرباء والطاقة':['السلامة الكهربائية','قراءة المخططات','الصيانة'],
 'السباكة والتكييف':['الصيانة','السلامة المهنية','تشخيص الأعطال'], 'المصانع والإنتاج':['السلامة المهنية','مراقبة الجودة','تشغيل الآلات'],
 'الزراعة والثروة الحيوانية':['الري','السلامة المهنية','إدارة المحاصيل'], 'الطعام والضيافة':['سلامة الغذاء','خدمة العملاء','إدارة الوقت'],
 'السياحة والفنادق':['خدمة العملاء','إدارة الحجوزات','التواصل'], 'القانون والإعلام':['البحث','الكتابة','التواصل'],
 'الجمال والعناية':['خدمة العملاء','العناية الشخصية','التواصل'], 'النجارة والديكور':['قراءة المخططات','التشطيب','السلامة المهنية'],
 'الملابس والخياطة':['التفصيل','التطريز','مراقبة الجودة'], 'الصيانة والخدمات':['السلامة المهنية','الصيانة','خدمة العملاء'],
 'الحرف والمهن اليدوية':['الحرف اليدوية','مراقبة الجودة','التصميم']}
GROUP_CERTS={
 'التعليم والتدريب':'شهادة تدريس','السيارات والنقل':'رخصة قيادة مهنية','التقنية والبرمجيات':'AWS Certified','التسويق والمبيعات':'Google Analytics',
 'الإدارة والمال':'Microsoft Office Specialist','الصحة والرعاية':'شهادة إسعافات أولية','البناء والتشييد':'شهادة سلامة مهنية',
 'الكهرباء والطاقة':'شهادة سلامة مهنية','السباكة والتكييف':'شهادة سلامة مهنية','المصانع والإنتاج':'شهادة سلامة مهنية',
 'الزراعة والثروة الحيوانية':'شهادة سلامة مهنية','الطعام والضيافة':'شهادة سلامة غذاء','السياحة والفنادق':'شهادة خدمة ضيوف',
 'القانون والإعلام':'شهادة مهنية','الجمال والعناية':'شهادة مهنية','النجارة والديكور':'شهادة سلامة مهنية','الملابس والخياطة':'شهادة مهنية',
 'الصيانة والخدمات':'شهادة سلامة مهنية','الحرف والمهن اليدوية':'شهادة مهنية'}
CATEGORY_GROUP={'تقنية':'التقنية والبرمجيات','هندسة':'الكهرباء والطاقة','طب':'الصحة والرعاية','تعليم':'التعليم والتدريب','مالية':'الإدارة والمال','تسويق':'التسويق والمبيعات','إدارة':'الإدارة والمال','خدمة':'الصيانة والخدمات','قانون':'القانون والإعلام','فنون':'الجمال والعناية','نقل':'السيارات والنقل','بناء':'البناء والتشييد','صناعة':'المصانع والإنتاج','زراعة':'الزراعة والثروة الحيوانية','ضيافة':'الطعام والضيافة'}

def find_group(profession):
    for group, items in PROFESSION_GROUPS.items():
        if profession in items: return group
    return None

def group_for_category(category): return CATEGORY_GROUP.get(str(category or '').strip(),'التقنية والبرمجيات')

def clean_phone_leaks(u):
    phone=str(u.get('phone') or '').strip(); email=str(u.get('email') or '').strip(); digits=re.sub(r'\D','',phone)
    changed=False
    for key in ('profession','headline','bio','skills','experience','languages','certifications','resume'):
        value=str(u.get(key) or '').strip(); vd=re.sub(r'\D','',value)
        if (phone and value==phone) or (email and value.lower()==email.lower()) or (len(digits)>=8 and vd==digits and len(vd)>=8):
            u[key]=''; changed=True
        elif key in ('profession','headline','skills','experience','languages','certifications') and re.fullmatch(r'[+\-()\s\d]{3,20}',value):
            u[key]=''; changed=True
    return changed

users=secure_storage.load_users() or []
changed=0
for u in users:
    if not str(u.get('id','')).startswith('demo_seeker_'):
        continue
    before=repr({k:u.get(k) for k in ('category','profession','skills','languages','certifications','bio','resume','birthdate')})
    clean_phone_leaks(u)
    group=find_group(u.get('profession','')) or group_for_category(u.get('category',''))
    # كل حساب Demo يجب أن يملك مهنة متوافقة مع مجاله.
    items=PROFESSION_GROUPS.get(group,[])
    if u.get('profession') not in items:
        idx=int(re.sub(r'\D','',str(u.get('id'))) or 1)
        u['profession']=items[idx % len(items)] if items else u.get('profession','')
    u['category']=GROUP_CATEGORY.get(group,u.get('category',''))
    u['skills']=GROUP_SKILLS.get(group,u.get('skills') or ['التواصل','العمل الجماعي'])
    u['languages']='العربية، الإنجليزية'
    u['certifications']=GROUP_CERTS.get(group,'شهادة مهنية')
    if u.get('birthdate'):
        try:
            d=datetime.fromisoformat(str(u['birthdate'])[:10])
            if d.date() > datetime.now().date():
                u['birthdate']='1995-05-15'
        except Exception: pass
    if repr({k:u.get(k) for k in ('category','profession','skills','languages','certifications','bio','resume','birthdate')}) != before:
        changed+=1

if not secure_storage.save_users(users): raise SystemExit('تعذر حفظ بيانات المستخدمين')
print(f'تم إصلاح {changed} حساب Demo. لم يتم إنشاء أو حذف أي حساب أو وظيفة أو خبر.')
