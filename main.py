from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import threading
import time
import unicodedata
from functools import lru_cache
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import requests
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

try:
    import jwt as pyjwt
except ImportError:  # OAuth bearer verification is disabled until requirements are installed.
    pyjwt = None

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "site"
WORKSPACE_DIR = BASE_DIR / "Workspace"
load_dotenv(BASE_DIR / ".env", override=True)

APP_PASSWORD = os.getenv("LY_AI_PASSWORD", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()
ALLOWED_REMOTE_ORIGINS = {item.strip().rstrip("/") for item in os.getenv("LY_AI_ALLOWED_ORIGINS", "").split(",") if item.strip()}
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b").strip()
try:
    APP_PORT = int(os.getenv("LY_AI_PORT", "5000"))
except ValueError:
    APP_PORT = 5000

app = Flask(__name__)
app.config.update(
    SECRET_KEY=FLASK_SECRET_KEY or None,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=False,  # Local HTTP on loopback only; use HTTPS behind a production server if deployed.
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    MAX_CONTENT_LENGTH=1024 * 1024,
)

# Basic in-memory throttling for login attempts. This app is local-only.
login_failures: dict[str, deque[float]] = defaultdict(deque)
LOGIN_WINDOW_SECONDS = 300
MAX_FAILED_LOGINS = 8

# Pending actions exist only in server memory and expire quickly. The browser never sends
# executable commands or arbitrary process arguments; it can only confirm one server-issued ID.
PENDING_ACTION_TTL_SECONDS = 300
pending_actions: dict[str, dict] = {}
pending_actions_lock = threading.Lock()

SYSTEM_PROMPT = r"""
أنت Ly AI، مساعد محلي باللغة العربية يعمل عبر Ollama. ساعد المستخدم في الأسئلة العادية، أو اقترح إجراءً محليًا عندما يطلب بوضوح فعل شيء على Windows.
أجب دائمًا بكائن JSON صالح فقط، دون Markdown، وبأحد الشكلين:
{"type":"message","reply":"نص الرد للمستخدم"}
أو
{"type":"action","reply":"شرح واضح للإجراء الذي تقترحه ولماذا","action":{"type":"...","...":"..."}}

الأدوات المتاحة فقط (هذه أسماء ثابتة وليست أوامر نظام):
1) {"type":"open_app","app":"notepad|calculator|explorer|paint|task_manager|chrome|edge"}
2) {"type":"open_settings","page":"home|display|windows_update|network|apps"}
3) {"type":"open_folder","folder":"desktop|downloads|documents|pictures|music|videos|home|workspace"}
4) {"type":"open_website","url":"https://example.com"} (رابط ويب عام http أو https فقط)
5) {"type":"web_search","query":"عبارة البحث"}
6) {"type":"list_files","folder":"desktop|downloads|documents|workspace","query":"نص اختياري للترشيح"}
7) {"type":"open_file","folder":"desktop|downloads|documents|workspace","filename":"اسم ملف موجود بامتداد مستند آمن"}
8) {"type":"create_note","filename":"ملاحظات.md","content":"محتوى الملف النصي"} (يُنشأ داخل مجلد Workspace الخاص بالمشروع فقط، والامتداد .txt أو .md)
9) {"type":"create_folder","folder_name":"اسم مجلد جديد"} (داخل Workspace الخاص بالمشروع فقط)

قيود إلزامية:
- لا تقترح غير هذه الأدوات. لا توجد أداة لتشغيل أوامر shell أو PowerShell أو Python أو نصوص أو ملفات تنفيذية.
- لا تحاول تضمين أوامر داخل أسماء الملفات أو الروابط. لا تطلب كلمات مرور أو مفاتيح سرية.
- يُعرض الإجراء للمستخدم ويجب أن يضغط بنفسه «موافقة وتنفيذ» قبل التنفيذ. لا تدّعِ أن الإجراء نُفذ قبل أن يعود الخادم بنتيجة.
- اقترح إجراء واحدًا فقط في كل مرة؛ وإذا كانت المهمة متعددة الخطوات، اشرح الخطة ثم اقترح الخطوة التالية فقط. يتطلب كل إجراء موافقة مستقلة.
- لا تستخدم أي إجراء محلي إذا كان المستخدم يطلب مجرد شرح أو اقتراح. اسأل سؤال توضيح إذا كانت تفاصيل الإجراء غير محددة.
- لا تقترح فتح روابط إلى localhost أو عناوين الشبكات الخاصة، ولا ملفات تنفيذية أو سكربتات.
- عند عدم الحاجة إلى أداة، أجب بشكل طبيعي داخل الحقل reply.
""".strip()

ALLOWED_APPS: dict[str, dict[str, str]] = {
    "notepad": {"label": "المفكرة", "command": "notepad.exe"},
    "calculator": {"label": "الآلة الحاسبة", "command": "calc.exe"},
    "explorer": {"label": "مستكشف الملفات", "command": "explorer.exe"},
    "paint": {"label": "الرسام", "command": "mspaint.exe"},
    "task_manager": {"label": "إدارة المهام", "command": "taskmgr.exe"},
    "chrome": {"label": "Google Chrome", "command": "chrome"},
    "edge": {"label": "Microsoft Edge", "command": "msedge"},
}
ALLOWED_SETTINGS = {
    "home": "ms-settings:",
    "display": "ms-settings:display",
    "windows_update": "ms-settings:windowsupdate",
    "network": "ms-settings:network-status",
    "apps": "ms-settings:appsfeatures",
}
FOLDER_LABELS = {
    "desktop": "سطح المكتب",
    "downloads": "التنزيلات",
    "documents": "المستندات",
    "pictures": "الصور",
    "music": "الموسيقى",
    "videos": "مقاطع الفيديو",
    "home": "المجلد الشخصي",
    "workspace": "مساحة عمل Ly AI",
}
LISTABLE_FOLDERS = {"desktop", "downloads", "documents", "workspace"}
SAFE_DOCUMENT_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".json", ".rtf",
    ".log", ".mp3", ".wav", ".mp4", ".mkv", ".avi",
}
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
}


@lru_cache(maxsize=4)
def _supabase_jwks_client(jwks_url: str):
    if pyjwt is None:
        raise RuntimeError("مكتبة التحقق من تسجيل الدخول غير مثبتة. شغّل setup.bat.")
    return pyjwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)


def _supabase_claims_from_request() -> dict | None:
    """Verify a Supabase access token. Provider secrets never enter the browser."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token or len(token) > 12000:
        raise ValueError("رمز تسجيل الدخول غير صالح.")
    if not SUPABASE_URL:
        raise ValueError("لم يتم ضبط SUPABASE_URL في ملف .env الخاص بالمساعد المحلي.")
    if pyjwt is None:
        raise ValueError("مكتبة PyJWT غير متوفرة. شغّل setup.bat لتثبيت المتطلبات.")

    issuer = f"{SUPABASE_URL}/auth/v1"
    try:
        if SUPABASE_JWT_SECRET:
            # Optional legacy Supabase HS256 signing secret. Keep it only in local .env.
            claims = pyjwt.decode(
                token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated",
                issuer=issuer, options={"require": ["exp", "iss", "aud", "sub"]},
            )
        else:
            jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
            signing_key = _supabase_jwks_client(jwks_url).get_signing_key_from_jwt(token)
            claims = pyjwt.decode(
                token, signing_key.key, algorithms=["ES256", "RS256"], audience="authenticated",
                issuer=issuer, options={"require": ["exp", "iss", "aud", "sub"]},
            )
    except Exception as exc:
        if pyjwt is not None and isinstance(exc, pyjwt.InvalidTokenError):
            raise ValueError("انتهت صلاحية رمز تسجيل الدخول أو تعذّر التحقق منه. سجّل الدخول مجددًا.") from exc
        raise ValueError("تعذّر التحقق من تسجيل الدخول الاجتماعي. تحقق من اتصال الإنترنت وإعدادات Supabase.") from exc
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ValueError("لم يتضمن رمز الدخول هوية مستخدم صالحة.")
    return claims


def _is_trusted_origin(origin: str | None) -> bool:
    if not origin:
        return True
    normalized = origin.rstrip("/")
    if normalized in ALLOWED_REMOTE_ORIGINS:
        return True
    parsed = urlsplit(origin)
    request_host = (request.host or "").lower()
    return parsed.scheme == "http" and parsed.netloc.lower() == request_host and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _client_key() -> str:
    return request.remote_addr or "local"


def _trim_old_login_failures(key: str) -> deque[float]:
    attempts = login_failures[key]
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return attempts


def _prune_pending_actions() -> None:
    now = time.time()
    with pending_actions_lock:
        expired = [key for key, value in pending_actions.items() if value["expires_at"] <= now]
        for key in expired:
            pending_actions.pop(key, None)


def _drop_actions_for_session(session_id: str) -> None:
    with pending_actions_lock:
        stale = [key for key, value in pending_actions.items() if value["session_id"] == session_id]
        for key in stale:
            pending_actions.pop(key, None)


def _register_pending_action(action: dict) -> str:
    _prune_pending_actions()
    session_id = session.get("sid")
    if not session_id:
        raise RuntimeError("جلسة الدخول غير صالحة. سجّل الدخول مرة أخرى.")
    action_id = secrets.token_urlsafe(24)
    with pending_actions_lock:
        # Avoid letting one browser accumulate unbounded unconfirmed actions.
        own_count = sum(1 for item in pending_actions.values() if item["session_id"] == session_id)
        if own_count >= 8:
            raise RuntimeError("لديك عدة إجراءات تنتظر الموافقة. وافق على إجراء معلّق أو ارفضه أولًا.")
        pending_actions[action_id] = {
            "session_id": session_id,
            "action": action,
            "expires_at": time.time() + PENDING_ACTION_TTL_SECONDS,
        }
    return action_id


def _require_authenticated():
    if request.headers.get("Authorization", "").startswith("Bearer "):
        try:
            claims = _supabase_claims_from_request()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 401
        if not claims:
            return jsonify({"error": "رمز تسجيل الدخول غير موجود."}), 401
        # A stable per-user identity scopes pending approvals across cross-origin PWA requests.
        session["authenticated"] = True
        session["auth_method"] = "supabase"
        session["sid"] = "supabase:" + claims["sub"]
        return None
    if not session.get("authenticated"):
        return jsonify({"error": "انتهت الجلسة أو لم تسجّل الدخول. أدخل كلمة المرور للمتابعة."}), 401
    if not session.get("sid"):
        session["sid"] = secrets.token_urlsafe(24)
    return None


def _folder_path(folder_id: str) -> Path:
    home = Path.home()
    paths = {
        "desktop": home / "Desktop",
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
        "home": home,
        "workspace": WORKSPACE_DIR,
    }
    path = paths.get(folder_id)
    if path is None:
        raise ValueError("المجلد المطلوب غير موجود ضمن المجلدات المسموح بها.")
    if folder_id == "workspace":
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        resolved = WORKSPACE_DIR.resolve()
        if not resolved.is_relative_to(BASE_DIR.resolve()):
            raise ValueError("مسار مساحة العمل غير آمن.")
        return resolved
    return path.resolve()


def _safe_windows_name(value: object, *, allowed_extensions: set[str] | None = None) -> str:
    if not isinstance(value, str):
        raise ValueError("اسم الملف غير صالح.")
    name = value.strip()
    if not name or len(name) > 100 or name in {".", ".."} or ".." in name:
        raise ValueError("اسم الملف فارغ أو طويل أو غير صالح.")
    if "/" in name or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("استخدم اسمًا فقط دون مسار مجلد أو رموز غير مسموحة.")
    if name.endswith((" ", ".")) or not re.fullmatch(r"[\w\u0600-\u06FF .()\-]+", name, flags=re.UNICODE):
        raise ValueError("يحتوي الاسم على رموز غير مسموحة.")
    base = name.split(".", 1)[0].upper()
    if base in RESERVED_WINDOWS_NAMES:
        raise ValueError("هذا الاسم محجوز في Windows.")
    suffix = Path(name).suffix.lower()
    if allowed_extensions is not None and suffix not in allowed_extensions:
        raise ValueError("نوع الملف غير مسموح. استخدم مستندًا أو ملفًا نصيًا آمنًا.")
    return name


def _validated_public_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("رابط الموقع غير صالح.")
    url = value.strip()
    if not url or len(url) > 2048:
        raise ValueError("الرابط فارغ أو طويل جدًا.")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            raise ValueError("يُسمح بروابط http وhttps فقط.")
        url = "https://" + url
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https:// ويحتوي على اسم موقع.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("الروابط التي تحتوي على بيانات دخول غير مسموحة.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("منفذ الرابط غير صالح.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("منفذ الرابط غير صالح.")

    host = parsed.hostname.strip(".").lower()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError("لا يمكن فتح عناوين محلية أو داخلية من هذه الأداة.")
    if "%" in host or " " in host or ".." in host:
        raise ValueError("اسم الموقع غير صالح.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        # Browsers may interpret malformed numeric hostnames as non-standard IP forms.
        if re.fullmatch(r"[0-9.]+", host):
            raise ValueError("عنوان IP غير قياسي غير مسموح.") from exc
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as idna_error:
            raise ValueError("اسم الموقع غير صالح.") from idna_error
        labels = ascii_host.split(".")
        if len(ascii_host) > 253 or len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
        ):
            raise ValueError("اسم الموقع غير صالح.") from exc
    else:
        if not ip.is_global:
            raise ValueError("لا يمكن فتح عناوين IP الخاصة أو المحلية.")
    return url



def _normalize_command_text(value: str) -> str:
    """Normalize Arabic/English phrasing for narrow, explicit local shortcuts."""
    text = unicodedata.normalize("NFKC", value).lower().strip()
    text = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)  # Arabic marks/tatweel
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\ufeff", "")
    text = re.sub(r"[،,؛;!؟?]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_direct_action_request(user_text: str) -> dict | None:
    """Return a safe, allowlisted proposal for obvious commands without relying on LLM JSON.

    This is intentionally narrow: the returned object is still validated, stored server-side,
    and must receive the normal one-time user confirmation before any OS action occurs.
    """
    text = _normalize_command_text(user_text)
    if not text or len(text) > 1000:
        return None

    search_prefixes = ("ابحث عن ", "ابحث لي عن ", "search for ", "google search ")
    for prefix in search_prefixes:
        if text.startswith(prefix):
            query = text[len(prefix):].strip()
            if query and len(query) <= 300:
                return {"type": "web_search", "query": query}
            return None

    # Safe, deterministic interpretation of common multi-part Google search requests.
    # It opens a prepared search URL only after the usual approval flow; it never types
    # arbitrary text into forms or clicks unpredictable web-page controls.
    google_search_combo = re.match(
        r"^(?:افتح|شغل|روح الى|اذهب الى|open|go to)\s+(?:قوقل|جوجل|غوغل|google)(?:\s+(?:و|ثم)\s*)(?:اكتب|اكتب لي|اكتبلي|ابحث عن|ابحث لي عن|search for|search)\s+(.+)$",
        text,
    )
    if google_search_combo:
        query = google_search_combo.group(1).strip()
        if query and len(query) <= 300:
            return {"type": "web_search", "query": query}
        return None

    open_prefixes = (
        "من فضلك افتح ", "افتح لي ", "افتحلي ", "افتح ",
        "من فضلك شغل ", "شغل لي ", "شغللي ", "شغل ", "قم بفتح ",
        "اذهب الى ", "روح الى ", "ادخل الى ", "open ", "launch ", "go to ", "visit ",
    )
    target = None
    for prefix in open_prefixes:
        if text.startswith(prefix):
            target = text[len(prefix):].strip()
            break
    if not target:
        return None

    for prefix in ("موقع ", "صفحة ", "the website ", "website ", "site "):
        if target.startswith(prefix):
            target = target[len(prefix):].strip()
            break
    target = re.sub(r"\s+(من فضلك|لو سمحت)$", "", target).strip()

    # Prioritize the browser app when the user names it, not the Google website.
    app_aliases = {
        "جوجل كروم": "chrome", "قوقل كروم": "chrome", "غوغل كروم": "chrome",
        "google chrome": "chrome", "chrome": "chrome", "كروم": "chrome",
        "مايكروسوفت ايدج": "edge", "microsoft edge": "edge", "edge": "edge", "ايدج": "edge", "إيدج": "edge",
        "المفكرة": "notepad", "notepad": "notepad", "notepad.exe": "notepad",
        "الالة الحاسبة": "calculator", "الة حاسبة": "calculator", "الحاسبة": "calculator", "calculator": "calculator", "calc": "calculator",
        "مستكشف الملفات": "explorer", "file explorer": "explorer", "explorer": "explorer",
        "الرسام": "paint", "paint": "paint",
        "ادارة المهام": "task_manager", "مدير المهام": "task_manager", "task manager": "task_manager", "taskmgr": "task_manager",
    }
    if target in app_aliases:
        return {"type": "open_app", "app": app_aliases[target]}

    website_aliases = {
        "قوقل": "https://www.google.com", "جوجل": "https://www.google.com", "غوغل": "https://www.google.com",
        "google": "https://www.google.com", "google.com": "https://www.google.com", "www.google.com": "https://www.google.com",
        "يوتيوب": "https://www.youtube.com", "youtube": "https://www.youtube.com", "youtube.com": "https://www.youtube.com",
        "جيميل": "https://mail.google.com", "gmail": "https://mail.google.com", "gmail.com": "https://mail.google.com",
        "شات جي بي تي": "https://chatgpt.com", "chatgpt": "https://chatgpt.com", "chat gpt": "https://chatgpt.com",
    }
    if target in website_aliases:
        return {"type": "open_website", "url": website_aliases[target]}

    folder_aliases = {
        "سطح المكتب": "desktop", "desktop": "desktop",
        "التنزيلات": "downloads", "التنزيل": "downloads", "downloads": "downloads",
        "المستندات": "documents", "documents": "documents",
        "الصور": "pictures", "pictures": "pictures",
        "الموسيقى": "music", "music": "music",
        "مقاطع الفيديو": "videos", "videos": "videos",
        "مجلد المستخدم": "home", "المجلد الشخصي": "home", "home folder": "home",
        "مساحة عمل ly ai": "workspace", "مساحة عمل ly": "workspace", "مساحة العمل": "workspace",
    }
    if target in folder_aliases:
        return {"type": "open_folder", "folder": folder_aliases[target]}

    # Permit an explicitly named public URL/domain, but never bypass URL validation.
    if re.fullmatch(r"(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9.-]*(?:\.[a-z]{2,})(?:/[^\s]*)?", target):
        try:
            return {"type": "open_website", "url": _validated_public_url(target)}
        except ValueError:
            return None
    return None


def validate_action(raw: object) -> dict:
    """Strictly normalize a model proposal to a small, explicit allowlist."""
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise ValueError("لم يحدّد النموذج نوع إجراء صالحًا.")
    kind = raw["type"]

    if kind == "open_app":
        app_id = raw.get("app")
        if not isinstance(app_id, str) or app_id not in ALLOWED_APPS:
            raise ValueError("التطبيق المطلوب غير موجود ضمن التطبيقات المسموح بها.")
        return {"type": kind, "app": app_id}

    if kind == "open_settings":
        page = raw.get("page", "home")
        if not isinstance(page, str) or page not in ALLOWED_SETTINGS:
            raise ValueError("صفحة إعدادات Windows غير مسموحة.")
        return {"type": kind, "page": page}

    if kind == "open_folder":
        folder = raw.get("folder")
        if not isinstance(folder, str) or folder not in FOLDER_LABELS:
            raise ValueError("المجلد المطلوب غير مسموح.")
        return {"type": kind, "folder": folder}

    if kind == "open_website":
        return {"type": kind, "url": _validated_public_url(raw.get("url"))}

    if kind == "web_search":
        query = raw.get("query")
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 300:
            raise ValueError("عبارة البحث غير صالحة أو طويلة جدًا.")
        return {"type": kind, "query": query.strip()}

    if kind == "list_files":
        folder = raw.get("folder")
        query = raw.get("query", "")
        if not isinstance(folder, str) or folder not in LISTABLE_FOLDERS:
            raise ValueError("يمكن استعراض سطح المكتب والتنزيلات والمستندات ومساحة عمل Ly AI فقط.")
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError("نص البحث عن الملفات غير صالح.")
        return {"type": kind, "folder": folder, "query": query.strip()}

    if kind == "open_file":
        folder = raw.get("folder")
        if not isinstance(folder, str) or folder not in LISTABLE_FOLDERS:
            raise ValueError("يمكن فتح الملفات من المجلدات المسموح بها فقط.")
        filename = _safe_windows_name(raw.get("filename"), allowed_extensions=SAFE_DOCUMENT_EXTENSIONS)
        return {"type": kind, "folder": folder, "filename": filename}

    if kind == "create_note":
        filename = _safe_windows_name(raw.get("filename"), allowed_extensions={".txt", ".md"})
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            raise ValueError("محتوى الملاحظة فارغ أو يتجاوز 12000 حرفًا.")
        return {"type": kind, "filename": filename, "content": content}

    if kind == "create_folder":
        folder_name = _safe_windows_name(raw.get("folder_name"))
        if Path(folder_name).suffix:
            raise ValueError("اكتب اسم المجلد فقط دون امتداد ملف.")
        return {"type": kind, "folder_name": folder_name}

    raise ValueError("هذا النوع من الإجراءات غير مسموح به. لم يتم تنفيذ أي شيء.")


def _ensure_workspace() -> Path:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    workspace = WORKSPACE_DIR.resolve()
    if not workspace.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("مساحة العمل ليست داخل مجلد المشروع.")
    return workspace


def _execute_action(action: dict) -> str:
    """Execute one already-approved, server-stored allowlisted action; never run a shell."""
    if os.name != "nt":
        raise RuntimeError("أدوات التحكم في البرامج والمجلدات مخصصة لنظام Windows.")

    kind = action["type"]
    if kind == "open_app":
        app_id = action["app"]
        command = ALLOWED_APPS[app_id]["command"]
        if app_id in {"chrome", "edge"}:
            candidates = []
            if app_id == "chrome":
                for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
                    if root:
                        candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
            else:
                for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
                    if root:
                        candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            executable = next((str(path) for path in candidates if path.is_file()), None)
            if executable:
                subprocess.Popen([executable], shell=False, close_fds=True)
            else:
                raise RuntimeError(f"لم أجد {ALLOWED_APPS[app_id]['label']} في المسارات المعتادة. ثبّته أو افتحه يدويًا.")
        else:
            # Resolve Windows built-ins to System32 explicitly to avoid PATH/current-directory hijacking.
            windows_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
            executable_path = (windows_root / "System32" / command).resolve()
            if not executable_path.is_file():
                raise RuntimeError(f"لم أجد ملف التطبيق النظامي: {ALLOWED_APPS[app_id]['label']}.")
            subprocess.Popen([str(executable_path)], shell=False, close_fds=True)
        return f"تم إرسال طلب فتح {ALLOWED_APPS[app_id]['label']} إلى Windows."

    if kind == "open_settings":
        os.startfile(ALLOWED_SETTINGS[action["page"]])  # type: ignore[attr-defined]
        return f"تم إرسال طلب فتح إعدادات Windows: {action['page']}."

    if kind == "open_folder":
        folder_id = action["folder"]
        folder = _folder_path(folder_id)
        if not folder.is_dir():
            raise RuntimeError(f"مجلد {FOLDER_LABELS[folder_id]} غير موجود على جهازك.")
        os.startfile(str(folder))  # type: ignore[attr-defined]
        return f"تم إرسال طلب فتح مجلد {FOLDER_LABELS[folder_id]}."

    if kind == "open_website":
        # URL was validated before being put into the pending-action store.
        os.startfile(action["url"])  # type: ignore[attr-defined]
        return f"تم إرسال طلب فتح الموقع: {action['url']}"

    if kind == "web_search":
        url = "https://www.google.com/search?q=" + quote_plus(action["query"])
        os.startfile(url)  # type: ignore[attr-defined]
        return f"تم إرسال طلب فتح بحث الويب عن: {action['query']}"

    if kind == "list_files":
        folder_id = action["folder"]
        folder = _folder_path(folder_id)
        if not folder.is_dir():
            raise RuntimeError(f"مجلد {FOLDER_LABELS[folder_id]} غير موجود.")
        needle = action["query"].casefold()
        # Bound directory scanning so a very large folder cannot stall the local server.
        matches = []
        scanned = 0
        for item in folder.iterdir():
            scanned += 1
            if not item.name.startswith(".") and (not needle or needle in item.name.casefold()):
                matches.append(item)
            if scanned >= 2500 or len(matches) >= 250:
                break
        visible = sorted(matches, key=lambda item: item.name.casefold())[:50]
        if not visible:
            return f"لم أجد ملفات أو مجلدات مطابقة في {FOLDER_LABELS[folder_id]}."
        lines = [f"محتويات {FOLDER_LABELS[folder_id]} (أول {len(visible)} عنصر):"]
        for item in visible:
            try:
                kind_label = "مجلد" if item.is_dir() else "ملف"
            except OSError:
                kind_label = "عنصر"
            lines.append(f"- [{kind_label}] {item.name}")
        return "\n".join(lines)

    if kind == "open_file":
        folder_id = action["folder"]
        base = _folder_path(folder_id).resolve()
        candidate = (base / action["filename"]).resolve(strict=True)
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise RuntimeError("الملف خارج المجلد المسموح به.") from exc
        if not candidate.is_file() or candidate.suffix.lower() not in SAFE_DOCUMENT_EXTENSIONS:
            raise RuntimeError("الملف غير موجود أو نوعه غير مسموح بالفتح.")
        os.startfile(str(candidate))  # type: ignore[attr-defined]
        return f"تم إرسال طلب فتح الملف: {candidate.name}"

    if kind == "create_note":
        workspace = _ensure_workspace()
        target = workspace / action["filename"]
        try:
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(action["content"])
        except FileExistsError as exc:
            raise RuntimeError("يوجد ملف بهذا الاسم بالفعل. لم يتم استبداله؛ اختر اسمًا جديدًا.") from exc
        return f"أُنشئت الملاحظة داخل Workspace باسم: {target.name}. لم يتم تشغيل محتواها."

    if kind == "create_folder":
        workspace = _ensure_workspace()
        target = workspace / action["folder_name"]
        try:
            target.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise RuntimeError("يوجد مجلد بهذا الاسم بالفعل. لم يتم تغيير أي شيء.") from exc
        return f"تم إنشاء المجلد داخل Workspace: {target.name}"

    raise RuntimeError("الإجراء غير متاح.")


@app.before_request
def enforce_local_access_and_origin():
    """Bind the agent to loopback and allow only explicitly listed hosted PWA origins."""
    host = (request.host or "").split(":", 1)[0].strip("[]").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        abort(400, description="يقبل Ly AI الاتصالات المحلية فقط.")

    origin = request.headers.get("Origin")
    if origin and not _is_trusted_origin(origin):
        abort(403, description="تم رفض المصدر غير المصرّح به. أضف عنوان الموقع إلى LY_AI_ALLOWED_ORIGINS إذا كنت تثق به.")

    if request.method == "OPTIONS":
        # The front-end hosted on HTTPS may explicitly connect to this user's loopback agent.
        # Browser PNA support differs by browser; only configured origins get this permission.
        return ("", 204)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
        is_cross_origin = origin.rstrip("/") not in {f"http://{request.host}".rstrip("/"), f"http://localhost:{request.environ.get('SERVER_PORT', '')}".rstrip("/"), f"http://127.0.0.1:{request.environ.get('SERVER_PORT', '')}".rstrip("/")}
        if is_cross_origin and origin.rstrip("/") in ALLOWED_REMOTE_ORIGINS:
            if request.path in {"/api/login", "/api/logout"}:
                abort(403, description="تسجيل الدخول بكلمة المرور المحلية متاح من واجهة Ly AI المحلية فقط.")
            if request.path.startswith("/api/actions/") or request.path == "/api/chat":
                if not request.headers.get("Authorization", "").startswith("Bearer "):
                    abort(401, description="يلزم تسجيل الدخول الاجتماعي عند استخدام الواجهة المنشورة.")


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; script-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; connect-src 'self' https://*.supabase.co https://*.supabase.in "
        "http://127.0.0.1:* http://localhost:*; base-uri 'self'; frame-ancestors 'none'; form-action 'self' https://*.supabase.co"
    )
    origin = request.headers.get("Origin")
    if origin and (origin.rstrip("/") in ALLOWED_REMOTE_ORIGINS or _is_trusted_origin(origin)):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
        response.headers["Access-Control-Max-Age"] = "600"
        if request.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/style.css")
def stylesheet():
    return send_from_directory(WEB_DIR, "style.css", mimetype="text/css")


@app.get("/script.js")
def javascript():
    return send_from_directory(WEB_DIR, "script.js", mimetype="text/javascript")


@app.get("/public-config.js")
def public_config():
    return send_from_directory(WEB_DIR, "public-config.js", mimetype="text/javascript")


@app.get("/manifest.webmanifest")
def web_manifest():
    response = send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(WEB_DIR, "sw.js", mimetype="text/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/icons/<path:filename>")
def app_icons(filename: str):
    return send_from_directory(WEB_DIR / "icons", filename)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(WEB_DIR / "icons", "ly-icon.svg", mimetype="image/svg+xml")


@app.get("/api/health")
def health():
    return jsonify({"app": "Ly AI", "version": "3.0.0", "status": "ok", "model": OLLAMA_MODEL, "auth": "supabase+jwt-or-local-password"})


@app.get("/api/session")
def session_status():
    authenticated = bool(session.get("authenticated"))
    if request.headers.get("Authorization", "").startswith("Bearer "):
        try:
            claims = _supabase_claims_from_request()
            authenticated = bool(claims)
            if claims:
                session["authenticated"] = True
                session["auth_method"] = "supabase"
                session["sid"] = "supabase:" + claims["sub"]
        except ValueError:
            authenticated = False
    elif authenticated and not session.get("sid"):
        session["sid"] = secrets.token_urlsafe(24)
    return jsonify({"authenticated": authenticated, "model": OLLAMA_MODEL, "desktop_control": True, "auth_method": session.get("auth_method", "local") if authenticated else None})


@app.post("/api/login")
def login():
    if not APP_PASSWORD:
        return jsonify({"error": "كلمة مرور الموقع غير مضبوطة. افتح ملف .env واضبط LY_AI_PASSWORD."}), 503

    key = _client_key()
    attempts = _trim_old_login_failures(key)
    if len(attempts) >= MAX_FAILED_LOGINS:
        return jsonify({"error": "محاولات كثيرة. انتظر خمس دقائق ثم حاول مجددًا."}), 429

    data = request.get_json(silent=True) or {}
    supplied = data.get("password", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, APP_PASSWORD):
        attempts.append(time.time())
        return jsonify({"error": "كلمة المرور غير صحيحة. حاول مرة أخرى."}), 401

    old_sid = session.get("sid")
    if old_sid:
        _drop_actions_for_session(old_sid)
    login_failures.pop(key, None)
    session.clear()
    session["authenticated"] = True
    session["auth_method"] = "local"
    session["sid"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"ok": True, "model": OLLAMA_MODEL, "desktop_control": True})


@app.post("/api/logout")
def logout():
    old_sid = session.get("sid")
    if old_sid:
        _drop_actions_for_session(old_sid)
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/chat")
def chat():
    auth_error = _require_authenticated()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    supplied_messages = data.get("messages")
    if not isinstance(supplied_messages, list) or not supplied_messages:
        return jsonify({"error": "لم يتم العثور على رسالة صالحة لإرسالها."}), 400

    messages: list[dict[str, str]] = []
    for item in supplied_messages[-20:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if content and len(content) <= 8000:
            messages.append({"role": role, "content": content})

    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "آخر رسالة يجب أن تكون طلبًا من المستخدم."}), 400

    # Handle clear, common device commands deterministically. The proposal still follows
    # the same server-side validation, per-session pending action, and explicit approval flow.
    direct_action = detect_direct_action_request(messages[-1]["content"])
    if direct_action is not None:
        try:
            action = validate_action(direct_action)
            action_id = _register_pending_action(action)
        except (ValueError, RuntimeError) as exc:
            return jsonify({
                "reply": f"لم أُنفّذ أي شيء لأن الإجراء المقترح غير صالح. {exc}",
                "model": OLLAMA_MODEL,
                "action": None,
            })
        return jsonify({
            "reply": "فهمت طلبك. راجع الإجراء المقترح أدناه، ثم وافق على تنفيذه أو ارفضه.",
            "model": OLLAMA_MODEL,
            "action": action,
            "action_id": action_id,
        })

    ollama_payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 6144, "temperature": 0.2},
    }

    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=ollama_payload, timeout=(5, 240))
        if response.status_code == 404:
            return jsonify({
                "error": f"لم يتم العثور على النموذج '{OLLAMA_MODEL}'. تحقق من الاسم في ملف .env ثم نفّذ: ollama pull {OLLAMA_MODEL}"
            }), 503
        response.raise_for_status()
        result = response.json()
        raw_answer = result.get("message", {}).get("content", "")
        if not isinstance(raw_answer, str) or not raw_answer.strip():
            return jsonify({"error": "استجاب النموذج دون نص. حاول إرسال الطلب مرة أخرى."}), 502

        try:
            decoded = json.loads(raw_answer)
        except json.JSONDecodeError:
            # Fail closed for tool execution: malformed model output is shown as plain text only.
            return jsonify({"reply": raw_answer.strip(), "model": OLLAMA_MODEL, "action": None})

        if not isinstance(decoded, dict):
            # Raw model text is safe to display as text; it is never interpreted as an action.
            return jsonify({"reply": raw_answer.strip(), "model": OLLAMA_MODEL, "action": None})

        # Some smaller local models return common alternate keys despite format=json.
        # Accept text-only variants for display; only a dictionary in the explicit action
        # slot can become an actionable proposal, and validate_action still enforces the allowlist.
        reply_value = decoded.get("reply")
        if not isinstance(reply_value, str):
            for alternate_key in ("response", "message", "content", "text"):
                if isinstance(decoded.get(alternate_key), str):
                    reply_value = decoded[alternate_key]
                    break
        if not isinstance(reply_value, str):
            reply_value = ""

        decoded_type = decoded.get("type")
        if decoded_type == "action" and isinstance(decoded.get("action"), dict):
            if not reply_value.strip():
                reply_value = "هذا هو الإجراء المقترح. راجعه ووافق على تنفيذه أو ارفضه."
        elif not reply_value.strip():
            # If the model produced a JSON object with a different text schema, display it
            # as plain text rather than claiming the response could not be parsed safely.
            return jsonify({"reply": raw_answer.strip(), "model": OLLAMA_MODEL, "action": None})

        reply = reply_value.strip()[:20000]
        if decoded_type != "action":
            return jsonify({"reply": reply, "model": OLLAMA_MODEL, "action": None})

        try:
            action = validate_action(decoded.get("action"))
            action_id = _register_pending_action(action)
        except (ValueError, RuntimeError) as exc:
            return jsonify({
                "reply": (reply + "\n\n" if reply else "") + f"لم أُنفّذ أي شيء؛ الإجراء المقترح غير صالح أو غير متاح. {exc}",
                "model": OLLAMA_MODEL,
                "action": None,
            })
        return jsonify({"reply": reply or "اقترحت إجراءً يحتاج إلى موافقتك.", "model": OLLAMA_MODEL, "action": action, "action_id": action_id})

    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": "تعذّر الاتصال بـ Ollama. تأكد من تثبيت Ollama وتشغيله، ثم جرّب مجددًا. العنوان المتوقع: http://127.0.0.1:11434"
        }), 503
    except requests.exceptions.Timeout:
        return jsonify({"error": "استغرق النموذج وقتًا طويلًا في الرد. جرّب سؤالًا أقصر أو نموذجًا أخف."}), 504
    except requests.exceptions.HTTPError:
        return jsonify({"error": "رفضت خدمة Ollama الطلب. تأكد من وجود النموذج وأن خدمة Ollama تعمل."}), 502
    except (ValueError, requests.exceptions.RequestException):
        return jsonify({"error": "حدث خطأ أثناء قراءة رد Ollama. أعد المحاولة وتحقق من سجل الخادم إذا تكرر الخطأ."}), 502


@app.post("/api/actions/execute")
def execute_pending_action():
    auth_error = _require_authenticated()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    action_id = data.get("action_id")
    if not isinstance(action_id, str) or not action_id or len(action_id) > 100:
        return jsonify({"error": "معرّف الإجراء غير صالح."}), 400
    if data.get("confirmed") is not True:
        return jsonify({"error": "يلزم تأكيد المستخدم قبل تنفيذ الإجراء."}), 400

    _prune_pending_actions()
    session_id = session.get("sid")
    with pending_actions_lock:
        record = pending_actions.get(action_id)
        if not record:
            return jsonify({"error": "انتهت صلاحية الإجراء أو سبق التعامل معه. اطلب اقتراحًا جديدًا."}), 410
        if record["session_id"] != session_id:
            return jsonify({"error": "هذا الإجراء لا ينتمي إلى جلسة الدخول الحالية."}), 403
        # Consume before execution to make the approval one-time and prevent double clicks/replays.
        action = record["action"]
        pending_actions.pop(action_id, None)

    try:
        result = _execute_action(action)
        return jsonify({"ok": True, "status": "completed", "result": result})
    except Exception as exc:
        app.logger.warning("Approved Ly AI action failed (%s): %s", action.get("type"), exc)
        return jsonify({"ok": False, "status": "failed", "result": str(exc) or "تعذّر تنفيذ الإجراء."})


@app.post("/api/actions/reject")
def reject_pending_action():
    auth_error = _require_authenticated()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    action_id = data.get("action_id")
    if not isinstance(action_id, str) or not action_id or len(action_id) > 100:
        return jsonify({"error": "معرّف الإجراء غير صالح."}), 400

    _prune_pending_actions()
    session_id = session.get("sid")
    with pending_actions_lock:
        record = pending_actions.get(action_id)
        if not record:
            return jsonify({"error": "انتهت صلاحية الإجراء أو سبق التعامل معه."}), 410
        if record["session_id"] != session_id:
            return jsonify({"error": "هذا الإجراء لا ينتمي إلى جلسة الدخول الحالية."}), 403
        pending_actions.pop(action_id, None)
    return jsonify({"ok": True, "status": "rejected"})


@app.errorhandler(HTTPException)
def handle_http_error(error: HTTPException):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description or "تعذّر تنفيذ الطلب."}), error.code or 500
    return error


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    app.logger.exception("Unexpected Ly AI error: %s", error)
    if request.path.startswith("/api/"):
        return jsonify({"error": "حدث خطأ غير متوقع في الخادم. راجع logs/server-error.log."}), 500
    return "حدث خطأ غير متوقع في Ly AI.", 500


if __name__ == "__main__":
    if not APP_PASSWORD:
        raise SystemExit("Error: LY_AI_PASSWORD is not configured. Run setup.bat and check .env.")
    if not FLASK_SECRET_KEY or FLASK_SECRET_KEY == "GENERATE_ON_SETUP":
        raise SystemExit("Error: FLASK_SECRET_KEY is not configured. Run setup.bat again.")
    if not 1024 <= APP_PORT <= 65535:
        raise SystemExit("Error: LY_AI_PORT must be between 1024 and 65535.")
    print(f"Ly AI is running locally at http://127.0.0.1:{APP_PORT}")
    print(f"Selected local model: {OLLAMA_MODEL}")
    app.run(host="127.0.0.1", port=APP_PORT, debug=False, use_reloader=False, threaded=True)
