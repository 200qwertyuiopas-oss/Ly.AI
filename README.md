# Ly AI Studio 3.0

واجهة محادثة عربية حديثة قابلة للتثبيت كتطبيق PWA، مع تسجيل دخول اجتماعي اختياري، ورفيق Python محلي يربط الواجهة بنموذج Ollama وأدوات Windows محدودة تتطلب موافقة المستخدم قبل **كل إجراء**.

> **مهم قبل النشر:** GitHub Pages يستضيف ملفات الويب الثابتة فقط ولا يشغّل Python أو Ollama. الموقع المنشور هو الواجهة وتسجيل الدخول؛ لاستخدام نموذجك المحلي والتحكم في Windows يجب أن يشغّل الشخص رفيق Ly AI على جهازه نفسه. إن أردت نموذجًا مركزيًا يعمل لكل زوار الموقع، فستحتاج خادمًا خلفيًا مستضافًا ونموذجًا يعمل على ذلك الخادم.

## مكوّنات المشروع

- `site/` — ملفات الواجهة العامة التي تُنشر على GitHub Pages: HTML/CSS/JS، إعدادات المتصفح العامة، manifest، service worker والأيقونات.
- `main.py` — خادم Flask محلي، مصادقة كلمة المرور أو التحقق من رموز Supabase، واتصال Ollama وأدوات Windows المسموح بها.
- `Ly.bat` و`launcher.ps1` — بدء الرفيق المحلي وفتح Chrome على Windows.
- `setup.bat` و`initialize_config.py` — إعداد بيئة Python وإنشاء ملف `.env` محلي.
- `.env.example` — نموذج إعدادات بلا أسرار حقيقية. الملف `.env` الفعلي مستثنى من Git.
- `.github/workflows/pages.yml` — نشر مجلد `site/` تلقائيًا عند الدفع إلى الفرع `main`.
- `tests/` — اختبارات أمان وإجراءات Windows.

## 1. التشغيل المحلي على Windows

### المتطلبات

1. Windows 10 أو 11.
2. Python 3.10 أو أحدث: <https://www.python.org/downloads/windows/>.
3. Ollama: <https://ollama.com/download/windows>.
4. Google Chrome أو متصفح Windows الافتراضي.

### التثبيت

1. فك ضغط المشروع إلى مسار عادي، مثل `C:\Ly_AI_Studio`.
2. انقر مرتين على `setup.bat` وانتظر ظهور `Setup finished.`. ينشئ بيئة `.venv` ويثبت مكتبات Python ويولّد كلمة مرور ومفتاح جلسة في `.env`.
3. نزّل النموذج الذي تستخدمه بالفعل أو نموذجًا محليًا آخر. النموذج الافتراضي في `.env.example` هو:

   ```powershell
   ollama pull qwen2.5-coder:3b
   ```

4. تأكد من وجود النموذج:

   ```powershell
   ollama list
   ```

5. انقر مرتين على `Ly.bat` وأدخل كلمة المرور التي أنشأها الإعداد. ستفتح الواجهة المحلية على `http://127.0.0.1:5000`.

إذا عدّلت `OLLAMA_MODEL` أو `.env` والخادم يعمل، أوقف الخادم وأعد تشغيل `Ly.bat` حتى يقرأ الإعدادات الجديدة.

## 2. تفعيل Google وDiscord وGitHub وFacebook

الدخول الاجتماعي اختياري، ويستخدم [Supabase Auth](https://supabase.com/docs/guides/auth/social-login). عليك إعداد مشروع Supabase واحد، ثم إضافة بيانات كل مزوّد من لوحته. إنشاء أزرار الواجهة وحده لا يكفي لتفعيل OAuth.

### أ. إعداد Supabase

1. أنشئ مشروعًا من <https://supabase.com/dashboard>.
2. من إعدادات المشروع انسخ **Project URL** و**Publishable/anon key**.
3. افتح `site/public-config.js` واملأ القيم التالية فقط:

   ```js
   window.LY_AI_CONFIG = Object.freeze({
     supabaseUrl: "https://YOUR_PROJECT_REF.supabase.co",
     supabaseAnonKey: "YOUR_SUPABASE_PUBLISHABLE_OR_ANON_KEY",
     localAgentUrl: "http://127.0.0.1:5000",
     version: "3.0.0"
   });
   ```

   عنوان المشروع ومفتاح **publishable/anon** هما إعدادات للمتصفح، وسيظهران للزوار. لا تضع مفتاح `service_role` أو سر عميل OAuth أو كلمة المرور أو JWT secret هنا.

4. في Supabase افتح **Authentication → URL Configuration** واضبط Site URL ورابط/روابط العودة المسموح بها. أضف عنوان Pages الذي سيظهر بعد النشر ورابط التطوير المحلي `http://127.0.0.1:5000/` عند الحاجة. يجب أن يطابق redirect URL العنوان الذي تستخدمه بالضبط.
5. افتح **Authentication → Providers** وفعّل كل مزوّد تريد استعماله. تُدخل أسرار مزوّدي OAuth في لوحة Supabase فقط، وليس في GitHub أو ملفات `site/`.

### ب. عناوين callback لدى مزوّدي الدخول

ستعرض صفحة المزوّد في Supabase عنوان callback الخاص بمشروعك، ويكون عادةً بالشكل:

`https://YOUR_PROJECT_REF.supabase.co/auth/v1/callback`

استخدم العنوان الذي تعرضه لوحة Supabase حرفيًا عند إعداد كل مزوّد:

- **Google:** أنشئ OAuth Client من نوع Web application في Google Cloud، وأضف عنوان موقعك ضمن Authorized JavaScript origins، وعنوان Supabase callback ضمن Authorized redirect URIs.
- **GitHub:** أنشئ OAuth App، وضع عنوان موقعك في Homepage URL وعنوان Supabase callback في Authorization callback URL.
- **Discord:** أنشئ Application في Discord Developer Portal، وأضف Supabase callback تحت OAuth2 Redirects.
- **Facebook:** أنشئ App في Meta for Developers، فعّل Facebook Login، وأضف Supabase callback إلى Valid OAuth Redirect URIs، واضبط الأذونات المطلوبة للبريد إذا كنت ستطلبه.

قد تتطلب بعض المزوّدات إعدادات موافقة أو نشرًا أو مراجعة حسب حسابك. راجع الوثائق الرسمية لكل مزوّد؛ لا يمكن للمشروع إنشاء هذه المفاتيح نيابةً عنك.

### ج. التحقق من الدخول على الرفيق المحلي

الواجهة ترسل رمز الوصول الذي تمنحه Supabase إلى `main.py`. يحاول الخادم التحقق منه باستخدام JWKS لمفاتيح التوقيع غير المتماثلة. إذا كان مشروع Supabase لديك لا يزال يستخدم توقيع HS256 القديم، فضع `SUPABASE_JWT_SECRET` في `.env` المحلي فقط (من إعدادات Supabase)؛ سيستخدم الخادم هذا السر للتحقق من JWT. لا ترفعه أبدًا إلى GitHub.

## 3. نشر الواجهة على GitHub Pages

يحتوي المشروع على GitHub Actions workflow ينشر `site/` فقط، ولا يرفع `.env` أو كود الرفيق المحلي ضمن artifact الخاص بالموقع.

1. أنشئ مستودع GitHub جديدًا. اجعل المستودع عامًا إذا كانت خطة حسابك تتطلب ذلك لـ Pages، أو استخدم إعدادًا يوافق خطة حسابك.
2. انسخ محتويات هذا المجلد إلى جذر المستودع وارفعها إلى الفرع `main`.
3. افتح **Settings → Pages** واجعل مصدر النشر **GitHub Actions**.
4. انتظر نجاح العملية في تبويب **Actions**. سيعرض GitHub عنوان Pages مثل `https://YOURNAME.github.io/REPOSITORY/`.
5. ارجع إلى إعداد URL في Supabase وأضف رابط Pages الكامل ضمن redirect URLs، مع `/` في آخره إذا كان هذا هو المسار المستخدم.
6. اختبر تسجيل الدخول من الرابط المنشور. لتعمل محادثة Ollama على جهاز زائر، يجب أن يشغّل ذلك الزائر الرفيق المحلي بنفسه؛ لا يمكن لصفحة GitHub أن تصل تلقائيًا إلى ملفات جهازه أو تتحكم فيه.

### ربط نطاق خاص مثل `lyai.example.com`

1. اشترِ نطاقًا أو استخدم نطاقًا تملكه.
2. من مستودع GitHub افتح **Settings → Pages → Custom domain** وأدخل اسم النطاق.
3. اضبط DNS وفق التعليمات التي يعرضها GitHub لنوع النطاق (CNAME للنطاق الفرعي عادةً، وإعدادات DNS مختلفة للنطاق الأساسي).
4. بعد اعتماد النطاق، أنشئ ملفًا باسم `site/CNAME` يحتوي على اسم النطاق وحده، مثل `lyai.example.com`، ثم ادفع التغيير إلى `main`.
5. فعّل **Enforce HTTPS** عندما يصبح الخيار متاحًا، وحدّث Site URL وRedirect URLs في Supabase.

لا تُنشئ ملف `CNAME` بقيمة المثال نفسها؛ استبدلها بنطاقك الحقيقي. وثائق Pages: <https://docs.github.com/en/pages>.

## 4. ربط الموقع المنشور بالرفيق المحلي

إذا أردت أن تستخدم واجهة GitHub Pages نموذج Ollama الموجود على **نفس جهاز الزائر**، يلزم السماح لمصدر الموقع المحدد في `.env` المحلي. مثلًا:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
LY_AI_ALLOWED_ORIGINS=https://YOURNAME.github.io,https://lyai.example.com
```

اكتب **الأصل فقط** دون `/REPOSITORY/` أو مسار الصفحة. أضف دومينك الحقيقي فقط، ولا تستخدم `*`. الإعداد فارغ افتراضيًا عمدًا لحماية الرفيق المحلي من طلبات مواقع أخرى. يجب إعادة تشغيل `Ly.bat` بعد تعديل `.env`.

في `site/public-config.js`، عدّل `localAgentUrl` فقط إذا غيّرت منفذ الرفيق. يجب أن يبقى عنوانًا loopback مثل `http://127.0.0.1:5000`، وليس عنوانًا عامًا. بعض المتصفحات قد تعرض تحذيرًا أو تمنع اتصال HTTPS بالمساعد المحلي بسبب حماية الشبكة الخاصة؛ ذلك يختلف بين المتصفحات والإصدارات. لا تعطّل حماية المتصفح عالميًا لتجاوز ذلك.

## 5. تثبيت Ly AI على الكمبيوتر أو الهاتف

Ly AI هو **PWA**؛ أي واجهة ويب قابلة للتثبيت، وليس ملف APK أو EXE أصليًا. على المواقع المنشورة عبر HTTPS يستطيع المتصفح عرض خيار التثبيت، ويعرض Ly AI دعوة داخلية بعد الزيارة الأولى. يظهر تأكيد النظام، ويمكن للمستخدم قبول التثبيت أو رفضه.

- **Chrome/Edge على الكمبيوتر:** اضغط «تثبيت» إذا ظهر، أو اختر تثبيت الصفحة كتطبيق من قائمة المتصفح.
- **Android:** افتح رابط Pages في Chrome ثم اختر «تثبيت التطبيق» أو «إضافة إلى الشاشة الرئيسية».
- **iPhone/iPad:** افتح الموقع في Safari، واضغط مشاركة، ثم «إضافة إلى الشاشة الرئيسية»؛ لا تدعم كل المتصفحات مطالبة التثبيت البرمجية نفسها.

تثبيت الواجهة لا يثبّت Python أو Ollama تلقائيًا. يجب إعداد الرفيق المحلي بشكل منفصل إذا أردت الذكاء الاصطناعي والتحكم في Windows. مراجع PWA: <https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable>.

## 6. إعدادات وأمان

- `.env` — الإعدادات السرية المحلية. مستثنى في `.gitignore`؛ تأكد دائمًا من أن `git status` لا يظهره قبل الدفع.
- `site/public-config.js` — يحتوي فقط على Supabase URL والمفتاح العام وURL الرفيق. أي قيمة فيه مرئية للزوار بطبيعتها.
- أسرار Google/Discord/GitHub/Facebook تحفظ في لوحة مزوّد OAuth وSupabase، لا في المستودع.
- خادم Python مرتبط بـ `127.0.0.1` فقط. لا تفتحه على الشبكة ولا تستخدمه كخادم إنتاج عام.
- إجراءات Windows من قائمة سماح محدودة؛ كل إجراء يحتاج بطاقة موافقة وتأكيدًا إضافيًا، ولا توجد أداة عامة لتشغيل shell أو PowerShell أو ملفات تنفيذية من رد النموذج.
- سجل المحادثات في هذه النسخة محفوظ في `localStorage` بالمتصفح؛ مسحه من الإعدادات يحذفه من ذلك المتصفح فقط.
- النموذج الصغير قد يخطئ أو لا يفهم بعض الطلبات. لا يستطيع هذا المشروع ضمان تنفيذ «كل شيء»؛ يجب أن تبقى العمليات الحساسة أو المالية أو الأمنية خارج التنفيذ التلقائي.
- لا تحفظ بيانات شخصية حساسة داخل المحادثات إن كان الجهاز أو المتصفح مشتركًا.

## 7. اختبارات المشروع

بعد تثبيت المتطلبات، افتح Terminal في مجلد المشروع وشغّل:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

لفحص JavaScript إذا كان Node.js مثبتًا:

```powershell
node --check .\site\script.js
```

الاختبارات لا تغني عن اختبار تسجيل الدخول مع إعدادات مزوّدي OAuth الفعلية، ولا عن تجربة إجراءات Windows على جهازك.

## استكشاف الأخطاء

- `لم يتم العثور على النموذج`: نفّذ `ollama list` وتأكد أن `OLLAMA_MODEL` في `.env` يطابق الاسم الموجود.
- فشل social login: راجع Provider في Supabase، callback URL، Site URL، Redirect URLs، و`site/public-config.js`.
- الدخول الاجتماعي يعمل لكن المساعد غير متصل: شغّل `Ly.bat` على الجهاز نفسه وتأكد من `LY_AI_ALLOWED_ORIGINS` ورقم المنفذ.
- تعذّر التحقق من رمز الدخول: تأكد من `SUPABASE_URL`. إن كان المشروع يستخدم HS256 القديم، اضبط `SUPABASE_JWT_SECRET` في `.env` المحلي فقط.
- خطأ Python: افحص `logs/server-error.log` ثم شغّل `setup.bat` إذا كانت المكتبات ناقصة.
