# Jawdah Cloud v38 Deploy Ready

هذه نسخة نشر احترافية من نظام جودة الانطلاقة، مهيأة لتعمل كرابط أونلاين بدل التشغيل المحلي.

## ماذا تحتوي؟

- Backend Python + SQLite.
- واجهة Dashboard متصلة بالـ API.
- تسجيل دخول وصلاحيات.
- قاعدة بيانات حقيقية داخل ملف SQLite.
- Backup JSON و CSV Export.
- Dockerfile.
- docker-compose.yml.
- Procfile.
- render.yaml.
- إعدادات بيئة جاهزة.

## بيانات الدخول الافتراضية

- admin / admin123
- accountant / 1234
- operations / 1234
- maintenance / 1234
- viewer / 1234

غيّر كلمات المرور بعد أول دخول.

## تشغيل محلي بدون ملف BAT

من داخل المجلد:

```bash
python server.py
```

ثم افتح:

```text
http://127.0.0.1:8765
```

## تشغيل عبر Docker

```bash
docker compose up --build
```

ثم افتح:

```text
http://127.0.0.1:8765
```

## النشر على استضافة

ارفع هذا المجلد كما هو على منصة تدعم Python أو Docker.

أهم أمر تشغيل:

```bash
python server.py
```

أهم متغيرات البيئة:

```text
JAWDAH_HOST=0.0.0.0
JAWDAH_DATA_DIR=/var/data
```

المنصة عادة توفر متغير PORT تلقائياً، والنظام يقرأه تلقائياً.

## نقطة فحص الصحة

بعد النشر افتح:

```text
/api/health
```

لازم تظهر نتيجة مثل:

```json
{"ok": true, "status": "healthy"}
```

## ملاحظة مهمة عن قاعدة البيانات

SQLite تحتاج مسار تخزين دائم. لا تنشرها على استضافة تمسح ملفات التطبيق عند إعادة التشغيل إلا إذا فعلت Persistent Disk أو Volume.

## المرحلة التالية بعد النشر

- ربط دومين.
- تفعيل SSL.
- تغيير كلمات المرور.
- تنزيل Backup بعد إدخال البيانات.
- لاحقاً يمكن نقل SQLite إلى PostgreSQL إذا احتجت فريق كبير وعدة مستخدمين بنفس الوقت.
