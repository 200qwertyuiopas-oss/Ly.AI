'use strict';

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = 'ly-ai-conversations-v3';
const SETTINGS_KEY = 'ly-ai-settings-v1';
const DEFAULT_TITLE = 'محادثة جديدة';
const CONFIG = window.LY_AI_CONFIG || {};
const PROVIDERS = ['google', 'discord', 'github', 'facebook'];

let conversations = readConversations();
let activeConversationId = conversations[0]?.id ?? null;
let isSending = false;
let isActionWorking = false;
let toastTimer = null;
let supabaseClient = null;
let oauthSession = null;
let currentUser = null;
let localAuthenticated = false;
let installPromptEvent = null;
let agentConnected = false;
let currentModel = 'Ollama محلي';
let settings = readSettings();

function newId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function readSettings() {
  try {
    const value = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    return {
      agentUrl: typeof value.agentUrl === 'string' ? value.agentUrl : (CONFIG.localAgentUrl || 'http://127.0.0.1:5000'),
      lightTheme: value.lightTheme === true
    };
  } catch {
    return { agentUrl: CONFIG.localAgentUrl || 'http://127.0.0.1:5000', lightTheme: false };
  }
}

function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

function readConversations() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(stored)) return [];
    return stored.filter((item) => item && typeof item.id === 'string' && Array.isArray(item.messages))
      .map((item) => ({
        id: item.id,
        title: typeof item.title === 'string' ? item.title.slice(0, 80) : DEFAULT_TITLE,
        updatedAt: Number(item.updatedAt) || Date.now(),
        messages: item.messages.filter((message) => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string')
          .slice(-200).map((message) => {
            const clean = { role: message.role, content: message.content.slice(0, 20000) };
            if (message.role === 'assistant' && message.action && typeof message.action === 'object') {
              clean.action = message.action;
              clean.actionStatus = ['pending', 'completed', 'failed', 'rejected', 'expired', 'unknown'].includes(message.actionStatus) ? message.actionStatus : 'pending';
              if (typeof message.actionId === 'string') clean.actionId = message.actionId;
              if (typeof message.actionResult === 'string') clean.actionResult = message.actionResult.slice(0, 6000);
            }
            return clean;
          })
      })).sort((a, b) => b.updatedAt - a.updatedAt);
  } catch { return []; }
}

function saveConversations() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, 50))); }
  catch { showToast('تعذّر حفظ السجل في المتصفح. قد تكون مساحة التخزين ممتلئة.'); }
}

function activeConversation() {
  return conversations.find((item) => item.id === activeConversationId) || null;
}

function ensureConversation() {
  let conversation = activeConversation();
  if (!conversation) {
    conversation = { id: newId(), title: DEFAULT_TITLE, updatedAt: Date.now(), messages: [] };
    conversations.unshift(conversation);
    activeConversationId = conversation.id;
  }
  return conversation;
}

function showToast(message) {
  const toast = $('toast');
  toast.textContent = message;
  toast.hidden = false;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4800);
}

function isLocalFrontend() {
  return ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
}

function validateAgentUrl(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]', '::1'].includes(parsed.hostname)) {
    throw new Error('لأمان جهازك، عنوان المساعد يجب أن يكون http://127.0.0.1:PORT أو http://localhost:PORT فقط.');
  }
  if (parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new Error('اكتب عنوان الخادم فقط، دون مسار أو بيانات دخول.');
  }
  return parsed.origin;
}

function getApiBase() {
  if (isLocalFrontend()) return '';
  try { return validateAgentUrl(settings.agentUrl || CONFIG.localAgentUrl || 'http://127.0.0.1:5000'); }
  catch { return 'http://127.0.0.1:5000'; }
}

function supabaseConfigured() {
  return Boolean(CONFIG.supabaseUrl && CONFIG.supabaseAnonKey && !String(CONFIG.supabaseUrl).includes('YOUR_PROJECT') && !String(CONFIG.supabaseAnonKey).includes('YOUR_'));
}

async function loadSupabaseSdk() {
  if (window.supabase?.createClient) return;
  await new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error('تعذّر تحميل مكتبة تسجيل الدخول من CDN.'));
    document.head.append(script);
  });
  if (!window.supabase?.createClient) throw new Error('لم تتوفر مكتبة Supabase بعد التحميل.');
}

async function initializeSupabase() {
  if (!supabaseConfigured()) {
    $('auth-config-note').hidden = false;
    for (const button of document.querySelectorAll('[data-provider]')) button.disabled = true;
    $('oauth-status').hidden = false;
    $('oauth-status').textContent = 'الدخول الاجتماعي غير مفعّل بعد. راجع قسم إعداد Supabase في README، أو استخدم الدخول المحلي على هذا الجهاز.';
    return;
  }
  try {
    await loadSupabaseSdk();
    supabaseClient = window.supabase.createClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: 'pkce' }
    });
    const { data, error } = await supabaseClient.auth.getSession();
    if (error) throw error;
    oauthSession = data.session || null;
    currentUser = oauthSession?.user || null;
    $('auth-config-note').hidden = true;
    $('oauth-status').hidden = true;
    supabaseClient.auth.onAuthStateChange((_event, session) => {
      oauthSession = session || null;
      currentUser = oauthSession?.user || null;
      if (oauthSession && $('auth-screen').hidden) updateAccountProfile();
    });
  } catch (error) {
    $('oauth-status').hidden = false;
    $('oauth-status').textContent = `تعذّر تهيئة تسجيل الدخول: ${error.message || 'خطأ غير معروف'}`;
  }
}

async function apiFetch(path, options = {}) {
  const base = getApiBase();
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const accessToken = oauthSession?.access_token;
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  const url = `${base}${path}`;
  try {
    return await fetch(url, { ...options, headers, credentials: isLocalFrontend() ? 'same-origin' : 'omit' });
  } catch {
    throw new Error(isLocalFrontend()
      ? 'تعذّر الاتصال بخادم Ly AI. شغّل Ly.bat وتأكد من أن Ollama يعمل.'
      : `تعذّر الاتصال بالمساعد المحلي على ${base}. شغّل Ly AI على هذا الجهاز، ثم تحقق من إعداد LY_AI_ALLOWED_ORIGINS في ملف .env.`);
  }
}

function showAuth() {
  $('auth-screen').hidden = false;
  $('app-shell').hidden = true;
  document.body.classList.remove('sidebar-open');
  $('local-login-section').hidden = !isLocalFrontend();
  $('local-login-toggle').hidden = !isLocalFrontend();
  $('local-login-toggle').setAttribute('aria-expanded', isLocalFrontend() ? 'true' : 'false');
  if (isLocalFrontend()) $('local-login-section').hidden = false;
}

function updateAccountProfile() {
  const metadata = currentUser?.user_metadata || {};
  const name = metadata.full_name || metadata.name || metadata.user_name || metadata.preferred_username || currentUser?.email || 'حساب Ly AI';
  const email = currentUser?.email || (oauthSession ? 'حساب مرتبط' : 'مساعدك الخاص');
  $('account-name').textContent = name;
  $('account-email').textContent = email;
  $('account-avatar').textContent = String(name).trim().slice(0, 1).toUpperCase() || 'L';
  $('settings-account-description').textContent = oauthSession ? `${name} · ${email}` : 'مساحة محلية على هذا الجهاز';
  $('settings-auth-badge').textContent = oauthSession ? 'اجتماعي' : 'محلي';
}

function showApp(modelName) {
  $('auth-screen').hidden = true;
  $('app-shell').hidden = false;
  currentModel = modelName || currentModel || 'Ollama محلي';
  $('account-menu-button').title = oauthSession ? 'الحساب والإعدادات' : 'الإعدادات المحلية';
  updateAccountProfile();
  renderAll();
  window.setTimeout(() => $('message-input').focus(), 80);
}

function updateConnection(isOnline, label, model) {
  agentConnected = isOnline;
  $('connection-pill').classList.toggle('online', isOnline);
  $('connection-pill').classList.toggle('offline', !isOnline);
  $('connection-label').textContent = label || (isOnline ? 'المساعد متصل' : 'غير متصل');
  $('agent-banner').hidden = isOnline;
  if (model) {
    currentModel = model;
  }
}

async function refreshAgentStatus() {
  try {
    const healthResponse = await apiFetch('/api/health', { method: 'GET' });
    if (!healthResponse.ok) throw new Error('الخادم المحلي لم يستجب.');
    const health = await healthResponse.json();
    const sessionResponse = await apiFetch('/api/session', { method: 'GET' });
    const sessionData = await sessionResponse.json().catch(() => ({}));
    if (sessionResponse.ok && sessionData.authenticated) {
      localAuthenticated = true;
      updateConnection(true, 'المساعد متصل', sessionData.model || health.model || currentModel);
    } else if (oauthSession) {
      updateConnection(false, 'يلزم ربط الرفيق', health.model || currentModel);
      $('agent-banner').hidden = false;
    } else {
      updateConnection(false, 'يلزم تسجيل الدخول');
    }
    return sessionResponse.ok && Boolean(sessionData.authenticated);
  } catch (error) {
    updateConnection(false, 'المساعد غير متصل');
    if (oauthSession) $('agent-banner').hidden = false;
    $('settings-agent-status').textContent = error.message || 'تعذّر الاتصال بالمساعد المحلي.';
    return false;
  }
}

async function checkSession() {
  if (oauthSession) {
    showApp('جارٍ الاتصال بالمساعد المحلي…');
    await refreshAgentStatus();
    return;
  }
  if (isLocalFrontend()) {
    try {
      const response = await apiFetch('/api/session', { method: 'GET' });
      const data = await response.json();
      if (response.ok && data.authenticated) {
        localAuthenticated = true;
        showApp(data.model || 'Ollama محلي');
        await refreshAgentStatus();
        return;
      }
    } catch { /* Expected when the local server is not running yet. */ }
  }
  showAuth();
}

function renderHistory() {
  const list = $('conversation-list');
  list.replaceChildren();
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 50);
  $('history-empty').hidden = sorted.length > 0;
  for (const conversation of sorted) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `conversation-item${conversation.id === activeConversationId ? ' active' : ''}`;
    button.title = conversation.title;
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-2 2v-7.5a7.5 7.5 0 1 1 17-2Z'); icon.append(path);
    const title = document.createElement('span'); title.className = 'conversation-title'; title.textContent = conversation.title || DEFAULT_TITLE;
    button.append(icon, title);
    button.addEventListener('click', () => {
      if (isSending) return showToast('انتظر انتهاء الرد الحالي قبل تغيير المحادثة.');
      activeConversationId = conversation.id; renderAll(); closeMobileSidebar();
    });
    list.append(button);
  }
}

function appendMessageContent(target, text) {
  const fencedCode = /```([\w+-]*)\s*\n?([\s\S]*?)```/g;
  let cursor = 0; let match;
  while ((match = fencedCode.exec(text)) !== null) {
    if (match.index > cursor) appendPlainText(target, text.slice(cursor, match.index));
    const pre = document.createElement('pre'); const code = document.createElement('code');
    if (match[1]) code.dataset.language = match[1];
    code.textContent = match[2].replace(/\n$/, ''); pre.append(code); target.append(pre); cursor = fencedCode.lastIndex;
  }
  if (cursor < text.length) appendPlainText(target, text.slice(cursor));
}

function appendPlainText(target, text) {
  const parts = text.split(/(`[^`\n]+`)/g);
  for (const part of parts) {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      const code = document.createElement('code'); code.className = 'inline-code'; code.textContent = part.slice(1, -1); target.append(code);
    } else target.append(document.createTextNode(part));
  }
}

function actionSummary(action) {
  if (!action || typeof action !== 'object') return 'إجراء محلي';
  const apps = { notepad: 'المفكرة', calculator: 'الآلة الحاسبة', explorer: 'مستكشف الملفات', paint: 'الرسام', task_manager: 'إدارة المهام', chrome: 'Google Chrome', edge: 'Microsoft Edge' };
  const folders = { desktop: 'سطح المكتب', downloads: 'التنزيلات', documents: 'المستندات', pictures: 'الصور', music: 'الموسيقى', videos: 'مقاطع الفيديو', home: 'المجلد الشخصي', workspace: 'مساحة عمل Ly AI' };
  const settingsMap = { home: 'الإعدادات الرئيسية', display: 'العرض', windows_update: 'تحديث Windows', network: 'الشبكة والإنترنت', apps: 'التطبيقات' };
  switch (action.type) {
    case 'open_app': return `فتح التطبيق: ${apps[action.app] || action.app || 'غير معروف'}`;
    case 'open_settings': return `فتح إعدادات Windows: ${settingsMap[action.page] || action.page || 'غير معروف'}`;
    case 'open_folder': return `فتح المجلد: ${folders[action.folder] || action.folder || 'غير معروف'}`;
    case 'open_website': return `فتح موقع ويب: ${String(action.url || '')}`;
    case 'web_search': return `فتح بحث ويب عن: ${String(action.query || '')}`;
    case 'list_files': return `عرض أسماء الملفات داخل ${folders[action.folder] || action.folder || 'مجلد'}${action.query ? ` المطابقة لـ «${action.query}»` : ''}`;
    case 'open_file': return `فتح الملف «${String(action.filename || '')}» من ${folders[action.folder] || action.folder || 'مجلد'}`;
    case 'create_note': return `إنشاء ملاحظة داخل Workspace باسم «${String(action.filename || '')}»`;
    case 'create_folder': return `إنشاء مجلد داخل Workspace باسم «${String(action.folder_name || '')}»`;
    default: return 'إجراء محلي غير معروف';
  }
}

function appendActionCard(parent, message) {
  const card = document.createElement('section'); card.className = 'action-card'; card.setAttribute('aria-label', 'إجراء محلي يحتاج إلى تأكيد');
  const top = document.createElement('div'); top.className = 'action-card-top';
  const title = document.createElement('strong'); title.textContent = 'إجراء يحتاج موافقتك';
  const status = document.createElement('span'); status.className = `action-status status-${message.actionStatus || 'pending'}`;
  const labels = { pending: 'بانتظارك', completed: 'تم التنفيذ', failed: 'تعذّر التنفيذ', rejected: 'تم الرفض', expired: 'انتهت الصلاحية', unknown: 'تحقق من النتيجة' };
  status.textContent = labels[message.actionStatus || 'pending'] || 'بانتظارك'; top.append(title, status); card.append(top);
  const summary = document.createElement('p'); summary.className = 'action-summary'; summary.textContent = actionSummary(message.action); card.append(summary);
  if (message.action?.type === 'create_note' && typeof message.action.content === 'string') {
    const previewLabel = document.createElement('div'); previewLabel.className = 'action-preview-label'; previewLabel.textContent = 'معاينة محتوى الملف الذي سيُكتب:';
    const preview = document.createElement('pre'); preview.className = 'action-preview'; preview.textContent = message.action.content; card.append(previewLabel, preview);
  }
  if (message.actionResult) { const result = document.createElement('pre'); result.className = 'action-result'; result.textContent = message.actionResult; card.append(result); }
  if ((message.actionStatus || 'pending') === 'pending' && message.actionId) {
    const warning = document.createElement('p'); warning.className = 'action-safety-note'; warning.textContent = 'لن يُنفّذ سوى هذا الإجراء المحدد، وبعد موافقتك وتأكيد إضافي. لا تُشغّل أوامر طرفية عامة.';
    const buttons = document.createElement('div'); buttons.className = 'action-buttons';
    const approve = document.createElement('button'); approve.type = 'button'; approve.className = 'action-approve'; approve.textContent = 'موافقة وتنفيذ'; approve.disabled = isActionWorking; approve.addEventListener('click', () => handleApproveAction(message));
    const reject = document.createElement('button'); reject.type = 'button'; reject.className = 'action-reject'; reject.textContent = 'رفض'; reject.disabled = isActionWorking; reject.addEventListener('click', () => handleRejectAction(message));
    buttons.append(approve, reject); card.append(warning, buttons);
  }
  parent.append(card);
}

async function handleApproveAction(message) {
  if (isActionWorking || !message.actionId || message.actionStatus !== 'pending') return;
  const actionId = message.actionId;
  if (!window.confirm(`هل توافق على تنفيذ الإجراء التالي؟\n\n${actionSummary(message.action)}\n\nراجع التفاصيل جيدًا. لن يتم تشغيل أوامر نظام عامة.`)) return;
  isActionWorking = true; renderMessages();
  try {
    const response = await apiFetch('/api/actions/execute', { method: 'POST', body: JSON.stringify({ action_id: actionId, confirmed: true }) });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { message.actionStatus = 'expired'; message.actionResult = data.error || 'انتهت الجلسة. أعد تسجيل الدخول.'; message.actionId = ''; }
    else if (response.status === 410) { message.actionStatus = 'expired'; message.actionResult = data.error || 'انتهت صلاحية الإجراء.'; message.actionId = ''; }
    else if (!response.ok) throw new Error(data.error || 'رفض الخادم تنفيذ الإجراء.');
    else { message.actionStatus = data.status || (data.ok ? 'completed' : 'failed'); message.actionResult = data.result || (data.ok ? 'اكتمل الإجراء.' : 'تعذّر تنفيذ الإجراء.'); message.actionId = ''; showToast(data.ok ? 'تم تنفيذ الإجراء الذي وافقت عليه.' : 'لم يكتمل الإجراء. راجع النتيجة.'); }
  } catch (error) {
    message.actionStatus = 'unknown'; message.actionResult = 'تعذّر تأكيد النتيجة بسبب خطأ اتصال. افحص جهازك قبل المحاولة مرة أخرى.'; message.actionId = '';
    showToast(error instanceof Error ? error.message : 'تعذّر الاتصال بالخادم.');
  } finally { isActionWorking = false; saveConversations(); renderAll(); }
}

async function handleRejectAction(message) {
  if (isActionWorking || !message.actionId || message.actionStatus !== 'pending') return;
  isActionWorking = true; renderMessages();
  try {
    const response = await apiFetch('/api/actions/reject', { method: 'POST', body: JSON.stringify({ action_id: message.actionId }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok && response.status !== 410) throw new Error(data.error || 'تعذّر رفض الإجراء.');
    message.actionStatus = 'rejected'; message.actionResult = 'لم يتم تنفيذ الإجراء بناءً على اختيارك.'; message.actionId = ''; showToast('تم رفض الإجراء ولم يتم تنفيذه.');
  } catch (error) { showToast(error instanceof Error ? error.message : 'تعذّر الاتصال بالخادم.'); }
  finally { isActionWorking = false; saveConversations(); renderAll(); }
}

function renderMessages() {
  const conversation = activeConversation(); const messages = conversation?.messages || []; const empty = messages.length === 0;
  $('welcome-panel').hidden = !empty; $('messages').hidden = empty; $('messages').replaceChildren();
  if (empty) return;
  for (const message of messages) {
    const row = document.createElement('article'); row.className = `message-row ${message.role === 'user' ? 'user-row' : 'assistant-row'}`;
    const avatar = document.createElement('div'); avatar.className = 'message-avatar'; avatar.textContent = message.role === 'user' ? 'أنت' : 'Ly'; avatar.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div'); body.className = 'message-body';
    const author = document.createElement('div'); author.className = 'message-author'; author.textContent = message.role === 'user' ? 'أنت' : 'Ly AI';
    const bubble = document.createElement('div'); bubble.className = 'message-bubble'; appendMessageContent(bubble, message.content);
    if (message.role === 'assistant' && message.action && typeof message.action === 'object') appendActionCard(bubble, message);
    body.append(author, bubble); row.append(avatar, body); $('messages').append(row);
  }
  if (isSending) appendTypingIndicator();
  $('messages').scrollTop = $('messages').scrollHeight;
}

function appendTypingIndicator() {
  const row = document.createElement('div'); row.className = 'typing-row'; row.id = 'typing-indicator';
  const avatar = document.createElement('div'); avatar.className = 'message-avatar'; avatar.textContent = 'Ly';
  const dots = document.createElement('span'); dots.className = 'typing-dots'; for (let i = 0; i < 3; i += 1) dots.append(document.createElement('i'));
  const label = document.createElement('span'); label.textContent = 'Ly AI يفكّر في إجابة...'; row.append(avatar, dots, label); $('messages').append(row); $('messages').scrollTop = $('messages').scrollHeight;
}

function renderAll() { renderHistory(); renderMessages(); }
function createNewConversation() {
  if (isSending) return showToast('انتظر انتهاء الرد الحالي قبل بدء محادثة جديدة.');
  const conversation = { id: newId(), title: DEFAULT_TITLE, updatedAt: Date.now(), messages: [] };
  conversations.unshift(conversation); activeConversationId = conversation.id; saveConversations(); renderAll(); closeMobileSidebar(); $('message-input').focus();
}
function resizeComposer() { const input = $('message-input'); input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 180)}px`; }

async function handleSend(prefilledText) {
  if (isSending) return;
  if (!oauthSession && !localAuthenticated) return showToast('سجّل الدخول أولًا للوصول إلى المساعد.');
  if (!agentConnected) {
    const connected = await refreshAgentStatus();
    if (!connected) return showToast('شغّل Ly AI المحلي على هذا الجهاز، ثم جرّب مجددًا.');
  }
  const current = activeConversation();
  if (current?.messages.some((message) => message.actionStatus === 'pending' && message.actionId)) return showToast('يوجد إجراء ينتظر موافقتك أو رفضك. تعامل معه أولًا.');
  const input = $('message-input'); const text = (typeof prefilledText === 'string' ? prefilledText : input.value).trim();
  if (!text) return;
  if (text.length > 8000) return showToast('الرسالة طويلة جدًا. الحد الأقصى 8000 حرف.');
  const conversation = ensureConversation(); conversation.messages.push({ role: 'user', content: text });
  if (conversation.title === DEFAULT_TITLE) conversation.title = text.replace(/\s+/g, ' ').slice(0, 42) || DEFAULT_TITLE;
  conversation.updatedAt = Date.now(); saveConversations(); if (typeof prefilledText !== 'string') input.value = ''; resizeComposer();
  isSending = true; $('send-button').disabled = true; $('send-button').querySelector('span').textContent = 'جارٍ الإرسال'; renderAll();
  try {
    const context = conversation.messages.slice(-20).map(({ role, content }) => ({ role, content }));
    const response = await apiFetch('/api/chat', { method: 'POST', body: JSON.stringify({ messages: context }) });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error(data.error || 'انتهت جلسة الدخول؛ سجّل الدخول مجددًا.');
    if (!response.ok) throw new Error(data.error || 'تعذّر الوصول إلى النموذج المحلي.');
    const assistantMessage = { role: 'assistant', content: data.reply || 'لم يصل رد نصي من النموذج.' };
    if (data.action && typeof data.action === 'object' && typeof data.action_id === 'string') { assistantMessage.action = data.action; assistantMessage.actionId = data.action_id; assistantMessage.actionStatus = 'pending'; }
    conversation.messages.push(assistantMessage);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'حدث خطأ غير معروف.';
    conversation.messages.push({ role: 'assistant', content: `تعذّر إكمال الطلب: ${message}` });
    if (/الاتصال|local agent|المساعد المحلي|CORS|NetworkError/i.test(message)) updateConnection(false, 'المساعد غير متصل');
  } finally {
    isSending = false; $('send-button').disabled = false; $('send-button').querySelector('span').textContent = 'إرسال'; conversation.updatedAt = Date.now(); saveConversations(); if (!$('app-shell').hidden) renderAll(); input.focus();
  }
}

function closeMobileSidebar() { document.body.classList.remove('sidebar-open'); }
function openSettings() {
  $('agent-url-input').value = settings.agentUrl || CONFIG.localAgentUrl || 'http://127.0.0.1:5000';
  $('theme-toggle').checked = !settings.lightTheme;
  $('settings-modal').showModal();
}

async function signInWithProvider(provider) {
  if (!supabaseClient) {
    const message = 'فعّل Supabase أولًا: ضع رابط المشروع وPublishable/anon key في public-config.js، ثم فعّل المزوّد داخل لوحة Supabase.';
    $('oauth-status').textContent = message; $('oauth-status').hidden = false; showToast('تسجيل الدخول الاجتماعي يحتاج إعدادًا لمرة واحدة. راجع README.'); return;
  }
  if (!PROVIDERS.includes(provider)) return;
  const button = document.querySelector(`[data-provider="${provider}"]`); if (button) button.disabled = true;
  $('oauth-status').hidden = false; $('oauth-status').textContent = 'جارٍ تحويلك إلى مزوّد تسجيل الدخول…';
  try {
    const redirectTo = `${window.location.origin}${window.location.pathname}`;
    const { error } = await supabaseClient.auth.signInWithOAuth({ provider, options: { redirectTo, skipBrowserRedirect: false } });
    if (error) throw error;
  } catch (error) {
    $('oauth-status').textContent = `تعذّر بدء تسجيل الدخول: ${error.message || 'تحقق من إعداد المزوّد وروابط العودة.'}`;
    if (button) button.disabled = false;
  }
}

async function handleLogout() {
  if (!window.confirm('هل تريد تسجيل الخروج من Ly AI على هذا المتصفح؟')) return;
  try {
    if (oauthSession && supabaseClient) await supabaseClient.auth.signOut();
    if (isLocalFrontend()) await apiFetch('/api/logout', { method: 'POST', body: JSON.stringify({}) }).catch(() => {});
  } finally {
    oauthSession = null; currentUser = null; localAuthenticated = false; showAuth(); showToast('تم تسجيل الخروج.');
  }
}

async function saveAgentUrl() {
  try {
    const normalized = validateAgentUrl($('agent-url-input').value.trim());
    settings.agentUrl = normalized; saveSettings(); $('settings-agent-status').textContent = `تم حفظ العنوان: ${normalized}. جارٍ اختبار الاتصال…`;
    const ok = await refreshAgentStatus();
    $('settings-agent-status').textContent = ok ? `اتصال ناجح. النموذج: ${currentModel}` : 'لم ينجح الاتصال. تأكد أن Ly AI يعمل محليًا وأن مصدر الموقع مسموح في LY_AI_ALLOWED_ORIGINS.';
  } catch (error) { $('settings-agent-status').textContent = error.message || 'عنوان غير صالح.'; }
}

async function installApp() {
  if (installPromptEvent) {
    installPromptEvent.prompt();
    const choice = await installPromptEvent.userChoice.catch(() => ({ outcome: 'dismissed' }));
    installPromptEvent = null; $('install-button').hidden = true; $('install-top-button').hidden = true;
    if (choice.outcome === 'accepted') showToast('بدأ تثبيت Ly AI. أكمل الخطوات التي يعرضها النظام.');
    return;
  }
  $('install-help-modal').showModal();
}

function initializeInstallSupport() {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault(); installPromptEvent = event; $('install-button').hidden = false; $('install-top-button').hidden = false;
  });
  window.addEventListener('appinstalled', () => { installPromptEvent = null; $('install-button').hidden = true; $('install-top-button').hidden = true; showToast('تم تثبيت Ly AI بنجاح.'); });
  const alreadyInstalled = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  if (alreadyInstalled) {
    $('install-button').hidden = true; $('install-top-button').hidden = true;
  } else if (window.isSecureContext || isLocalFrontend()) {
    window.setTimeout(() => {
      if (!localStorage.getItem('ly-ai-install-nudge-dismissed') && !window.matchMedia('(display-mode: standalone)').matches) {
        $('install-nudge').hidden = false;
      }
    }, 2600);
  }
  $('install-button').addEventListener('click', installApp);
  $('install-top-button').addEventListener('click', installApp);
  $('install-help-button').addEventListener('click', () => $('install-help-modal').showModal());
  $('settings-install-button').addEventListener('click', installApp);
  $('close-install-help').addEventListener('click', () => $('install-help-modal').close());
  $('done-install-help').addEventListener('click', () => $('install-help-modal').close());
  $('install-nudge-yes').addEventListener('click', async () => {
    $('install-nudge').hidden = true;
    await installApp();
  });
  $('install-nudge-no').addEventListener('click', () => {
    $('install-nudge').hidden = true;
    localStorage.setItem('ly-ai-install-nudge-dismissed', '1');
  });
  if ('serviceWorker' in navigator && (window.isSecureContext || isLocalFrontend())) {
    navigator.serviceWorker.register(new URL('./sw.js', window.location.href)).catch(() => {});
  }
}

function initializeSettings() {
  document.body.classList.toggle('light-theme', settings.lightTheme);
  $('theme-toggle').checked = !settings.lightTheme;
  $('settings-button').addEventListener('click', openSettings);
  $('open-settings-nav').addEventListener('click', openSettings);
  $('account-menu-button').addEventListener('click', openSettings);
  $('close-settings-button').addEventListener('click', () => $('settings-modal').close());
  $('done-settings-button').addEventListener('click', () => $('settings-modal').close());
  $('save-agent-url-button').addEventListener('click', saveAgentUrl);
  $('theme-toggle').addEventListener('change', () => {
    settings.lightTheme = !$('theme-toggle').checked; document.body.classList.toggle('light-theme', settings.lightTheme); saveSettings();
  });
  const clearHistory = () => {
    if (!window.confirm('سيتم حذف سجل المحادثات المحفوظ في هذا المتصفح. هل تريد المتابعة؟')) return;
    conversations = []; activeConversationId = null; saveConversations(); renderAll(); showToast('تم مسح سجل المحادثات من هذا المتصفح.');
  };
  $('clear-history-button').addEventListener('click', clearHistory);
  $('settings-clear-history-button').addEventListener('click', clearHistory);
}

function initializeEvents() {
  $('login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!isLocalFrontend()) return showToast('الدخول المحلي متاح من نسخة Ly AI المثبتة على الجهاز. استخدم أحد مزوّدي الدخول للموقع المنشور.');
    const password = $('password-input').value; const errorLabel = $('login-error'); const submit = $('login-form').querySelector('button[type="submit"]');
    submit.disabled = true; errorLabel.hidden = true;
    try {
      const response = await apiFetch('/api/login', { method: 'POST', body: JSON.stringify({ password }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'تعذّر تسجيل الدخول.');
      $('password-input').value = ''; localAuthenticated = true; currentUser = null; showApp(data.model || 'Ollama محلي'); await refreshAgentStatus();
    } catch (error) { errorLabel.textContent = error.message || 'حدث خطأ أثناء تسجيل الدخول.'; errorLabel.hidden = false; $('password-input').select(); }
    finally { submit.disabled = false; }
  });
  $('toggle-password').addEventListener('click', () => {
    const input = $('password-input'); const show = input.type === 'password'; input.type = show ? 'text' : 'password';
    $('toggle-password').setAttribute('aria-label', show ? 'إخفاء كلمة المرور' : 'إظهار كلمة المرور');
  });
  $('local-login-toggle').addEventListener('click', () => {
    const isOpen = $('local-login-toggle').getAttribute('aria-expanded') === 'true';
    $('local-login-toggle').setAttribute('aria-expanded', String(!isOpen)); $('local-login-section').hidden = isOpen;
  });
  $('composer-form').addEventListener('submit', (event) => { event.preventDefault(); handleSend(); });
  $('message-input').addEventListener('input', resizeComposer);
  $('message-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('composer-form').requestSubmit(); } });
  $('new-chat-button').addEventListener('click', createNewConversation);
  $('clear-history-button').addEventListener('keydown', (event) => { if (event.key === 'Enter') event.currentTarget.click(); });
  $('logout-button').addEventListener('click', handleLogout);
  $('mobile-menu-button').addEventListener('click', () => document.body.classList.add('sidebar-open'));
  $('sidebar-close').addEventListener('click', closeMobileSidebar);
  $('sidebar-backdrop').addEventListener('click', closeMobileSidebar);
  $('retry-agent-button').addEventListener('click', refreshAgentStatus);
  document.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => handleSend(button.dataset.prompt)));
  document.querySelectorAll('[data-provider]').forEach((button) => button.addEventListener('click', () => signInWithProvider(button.dataset.provider)));
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); createNewConversation(); }
    if (event.key === 'Escape') closeMobileSidebar();
  });
}

async function boot() {
  initializeSettings(); initializeEvents(); initializeInstallSupport();
  await initializeSupabase();
  await checkSession();
}

boot();
