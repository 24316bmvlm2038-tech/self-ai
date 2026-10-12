'use strict';

/* ======================================================================
   Helpers
   ====================================================================== */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child);
  }
  return node;
}

const SVG = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

const ICONS = {
  logo: `<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#4d6bfe"/><path d="M9 19.5c3 3.2 11 3.2 14-1.6" stroke="#fff" stroke-width="2.8" stroke-linecap="round" fill="none"/><circle cx="11.5" cy="12.5" r="2" fill="#fff"/><circle cx="20.5" cy="12.5" r="2" fill="#fff"/></svg>`,
  edit: SVG('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
  tasks: SVG('<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2"/>'),
  folder: SVG('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'),
  file: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6"/>'),
  menu: SVG('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  x: SVG('<path d="M18 6 6 18M6 6l12 12"/>'),
  send: SVG('<path d="M12 19V5M5 12l7-7 7 7"/>'),
  stop: '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/></svg>',
  clip: SVG('<path d="m21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>'),
  search: SVG('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
  globe: SVG('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  download: SVG('<path d="M12 4v12M6 10l6 6 6-6M4 20h16"/>'),
  upload: SVG('<path d="M12 20V8M6 14l6-6 6 6M4 4h16"/>'),
  trash: SVG('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  pencil: SVG('<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
  brain: SVG('<path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3Z"/><path d="M15 3a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3 3 3 0 0 1-3-3"/>'),
  tool: SVG('<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8M3 21l6-6"/><path d="M14.7 6.3 17 4l3 3-2.3 2.3"/>'),
  clock: SVG('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  play: SVG('<path d="M7 4v16l13-8Z"/>'),
  pause: SVG('<path d="M8 5v14M16 5v14"/>'),
  refresh: SVG('<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>'),
  write: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M9 15l2 2 4-4"/>'),
  read: SVG('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'),
  code: SVG('<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>'),
  sparkle: SVG('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'),
  back: SVG('<path d="M15 18l-6-6 6-6"/>'),
  chip: SVG('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
  bulb: SVG('<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2Z"/>'),
  check: SVG('<path d="M20 6 9 17l-5-5"/>'),
};

function icon(name, cls = 'icon') {
  const span = el('span', { class: cls });
  span.innerHTML = ICONS[name] || ICONS.tool; // trusted static markup
  return span;
}

function hydrateIcons(root = document) {
  $$('[data-icon]', root).forEach((node) => {
    if (!node.querySelector('svg')) node.insertAdjacentHTML('afterbegin', ICONS[node.dataset.icon] || '');
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function safeJson(text) {
  try { return JSON.parse(text || '{}'); } catch { return null; }
}

// Tool arguments as readable "key: value" lines; multi-line strings (file contents) shown verbatim.
function formatArgs(text) {
  const parsed = safeJson(text);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return text;
  return Object.entries(parsed)
    .map(([key, value]) => (typeof value === 'string' && value.includes('\n') ? `${key}:\n${value}` : `${key}: ${JSON.stringify(value)}`))
    .join('\n');
}

function plainSnippet(markdown) {
  return markdown.replace(/[*`#>]+/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\s+/g, ' ').trim();
}

function shortUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.length > 40 ? u.pathname.slice(0, 40) + '…' : u.pathname;
    return u.hostname.replace(/^www\./, '') + (path === '/' ? '' : path);
  } catch {
    return url;
  }
}

function humanSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function relativeTime(ts) {
  if (!ts) return '';
  const diff = ts * 1000 - Date.now();
  const mins = Math.round(Math.abs(diff) / 60000);
  const label = mins < 1 ? 'less than a minute' : mins < 60 ? `${mins} min` : mins < 1440 ? `${Math.round(mins / 60)} h` : `${Math.round(mins / 1440)} d`;
  return diff >= 0 ? `in ${label}` : `${label} ago`;
}

function fileUrl(path, download = false) {
  return `/api/files/raw?path=${encodeURIComponent(path)}${download ? '&download=true' : ''}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-SelfAI': '1', ...(options.headers || {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(message) {
  const node = el('div', { class: 'error-box', text: message, style: 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:100;box-shadow:var(--shadow);max-width:90vw' });
  document.body.append(node);
  setTimeout(() => node.remove(), 5000);
}

/* ======================================================================
   Markdown
   ====================================================================== */

if (window.marked) marked.setOptions({ gfm: true, breaks: false });
if (window.DOMPurify) {
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A' && node.getAttribute('href')) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    }
  });
}

function markdownToHtml(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text || ''));
  return `<p>${escapeHtml(text || '').replace(/\n/g, '<br>')}</p>`;
}

function renderMarkdown(node, text) {
  node.innerHTML = markdownToHtml(text);
  $$('pre > code', node).forEach((code) => {
    if (window.hljs) {
      try { hljs.highlightElement(code); } catch { /* unknown language */ }
    }
    const pre = code.parentElement;
    const btn = el('button', { class: 'copy-btn', type: 'button', text: 'Copy' });
    btn.addEventListener('click', () => {
      navigator.clipboard?.writeText(code.innerText);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    });
    pre.append(btn);
  });
}

function markdownBlock(text, cls = 'markdown') {
  const node = el('div', { class: cls });
  renderMarkdown(node, text);
  return node;
}

/* ======================================================================
   State
   ====================================================================== */

const state = {
  status: null,
  view: 'chat',
  convId: null,
  conversations: [],
  streaming: new Set(), // conversation ids with an active stream in this tab
  model: null,
  attachments: [],
  stick: true,
  tasks: [],
  openTaskId: null,
  filesPath: '.',
  thinking: false,
  models: null,
  timers: {},
};

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

/* ======================================================================
   Tool call rendering (shared by chat and task logs)
   ====================================================================== */

const TOOL_ICONS = {
  web_search: 'search', fetch_url: 'globe', download_file: 'download', write_file: 'write', read_file: 'read',
  list_files: 'folder', delete_file: 'trash', create_task: 'tasks', list_tasks: 'tasks', cancel_task: 'tasks',
};

function toolLabel(name, args) {
  const a = args || {};
  const b = (text) => el('b', { text });
  switch (name) {
    case 'web_search': return ['Searched the web for ', b(`“${a.query || ''}”`)];
    case 'fetch_url': return ['Read ', b(shortUrl(a.url || ''))];
    case 'download_file': return ['Downloaded ', b(shortUrl(a.url || '')), ' → ', b(a.path || '')];
    case 'write_file': return [a.append ? 'Appended to ' : 'Wrote ', b(a.path || '')];
    case 'read_file': return ['Read file ', b(a.path || '')];
    case 'list_files': return ['Listed files in ', b(a.path || '.')];
    case 'delete_file': return ['Deleted ', b(a.path || '')];
    case 'create_task': return ['Started background task ', b(`“${a.title || ''}”`)];
    case 'list_tasks': return ['Checked background tasks'];
    case 'cancel_task': return ['Cancelled task ', b(a.task_id || '')];
    default: return [b(name)];
  }
}

function createToolView(name, argsText) {
  const args = safeJson(argsText);
  const status = el('span', { class: 'tool-status running' });
  const result = el('pre', { class: 'tool-result', text: 'Running…' });
  const summary = el('summary', {}, status, icon(TOOL_ICONS[name] || 'tool'), el('span', { class: 'tool-label' }, toolLabel(name, args)));
  const details = el(
    'details',
    { class: 'tool' },
    summary,
    el('div', { class: 'tool-body' },
      el('div', { class: 'tool-section-title', text: 'Input' }),
      el('pre', { text: formatArgs(argsText) }),
      el('div', { class: 'tool-section-title', text: 'Result' }),
      result),
  );
  return {
    details,
    setResult(ok, content) {
      status.className = `tool-status ${ok ? 'ok' : 'error'}`;
      result.textContent = content;
      if (ok && (name === 'write_file' || name === 'download_file') && args && args.path && !summary.querySelector('.file-link')) {
        summary.append(el('a', { class: 'file-link', href: fileUrl(args.path), target: '_blank', rel: 'noopener', text: 'Open', onclick: (e) => e.stopPropagation() }));
      }
    },
    setInterrupted() {
      status.className = 'tool-status interrupted';
      result.textContent = '(interrupted)';
    },
  };
}

/* ======================================================================
   Chat view
   ====================================================================== */

const messagesEl = () => $('#messages');

function scrollToBottom(force = false, node = null) {
  if (node && !node.isConnected) return;
  const box = messagesEl();
  if (force || state.stick) box.scrollTop = box.scrollHeight;
}

class AssistantView {
  constructor(container) {
    this.body = el('div', { class: 'msg-body' });
    this.root = el('div', { class: 'msg assistant' }, icon('logo', 'avatar'), this.body);
    container.append(this.root);
    this.segment = null;
    this.reasoning = null;
    this.tools = new Map();
    this.typing = null;
  }

  append(node) {
    if (this.typing) this.body.insertBefore(node, this.typing);
    else this.body.append(node);
  }

  showTyping() {
    if (this.typing) return;
    this.typing = el('div', { class: 'typing' }, el('span'), el('span'), el('span'));
    this.body.append(this.typing);
  }

  hideTyping() {
    this.typing?.remove();
    this.typing = null;
  }

  addReasoning(text) {
    if (!this.reasoning) {
      const label = el('span', { text: 'Thinking…' });
      const content = el('div', { class: 'reasoning-text' });
      const details = el('details', { class: 'reasoning' }, el('summary', {}, icon('brain'), label), content);
      this.append(details);
      this.reasoning = { label, content, text: '' };
      this.segment = null;
    }
    this.reasoning.text += text;
    this.reasoning.content.textContent = this.reasoning.text;
  }

  endReasoning() {
    if (this.reasoning) {
      this.reasoning.label.textContent = 'Thought process';
      this.reasoning = null;
    }
  }

  addText(text, immediate = false) {
    this.endReasoning();
    if (!this.segment) {
      const node = el('div', { class: 'markdown' });
      this.append(node);
      this.segment = { node, text: '', frame: null };
    }
    const seg = this.segment;
    seg.text += text;
    if (immediate) {
      renderMarkdown(seg.node, seg.text);
      return;
    }
    if (seg.frame) return;
    seg.frame = requestAnimationFrame(() => {
      seg.frame = null;
      renderMarkdown(seg.node, seg.text);
      scrollToBottom(false, seg.node);
    });
  }

  addToolCall(id, name, args) {
    this.endReasoning();
    this.segment = null;
    const view = createToolView(name, args);
    this.append(view.details);
    this.tools.set(id, view);
  }

  addToolResult(id, ok, content) {
    this.tools.get(id)?.setResult(ok, content);
    this.tools.delete(id);
  }

  markUnfinishedTools() {
    for (const view of this.tools.values()) view.setInterrupted();
    this.tools.clear();
  }

  addError(message) {
    this.segment = null;
    this.append(el('div', { class: 'error-box', text: message }));
  }

  addNotice(message) {
    this.segment = null;
    this.append(el('div', { class: 'notice', text: message }));
  }
}

function userBubble(text) {
  return el('div', { class: 'msg user' }, el('div', { class: 'bubble', text }));
}

const SUGGESTIONS = [
  { icon: 'globe', title: 'What happened in tech today?', sub: 'Search the web and summarize with sources', prompt: 'Search the web for the most important technology news from today and give me a short summary with sources.' },
  { icon: 'write', title: 'Write a research report', sub: 'Saved as a Markdown file', prompt: 'Research the current state of solar battery storage (costs, leading companies, trends) and write a well-structured report to reports/solar-storage.md.' },
  { icon: 'code', title: 'Build a small web app', sub: 'Creates the files in your workspace', prompt: 'Create a single-file HTML/JS pomodoro timer app with a clean design and save it to apps/pomodoro.html.' },
  { icon: 'clock', title: 'Run a recurring background job', sub: 'The AI keeps working on its own', prompt: 'Create a background task that runs every 60 minutes: check the top stories on Hacker News and append any new interesting ones (title, link, one-line summary) to notes/hn-digest.md.' },
];

function renderEmptyState() {
  const box = messagesEl();
  box.innerHTML = '';
  const grid = el('div', { class: 'suggestions' });
  for (const s of SUGGESTIONS) {
    grid.append(el('button', {
      class: 'suggestion', type: 'button',
      onclick: () => { const input = $('#input'); input.value = s.prompt; autoGrow(); updateSendButton(); input.focus(); },
    }, icon(s.icon), el('span', {}, s.title, el('small', { text: s.sub }))));
  }
  box.append(el('div', { class: 'empty' },
    el('h1', { text: 'What can I help with?' }),
    el('p', { text: 'Chat, research the web, create files, or hand off a background task.' }),
    grid));
}

function renderConversation(conv) {
  const box = messagesEl();
  box.innerHTML = '';
  if (!conv.messages.length && !conv.running) {
    renderEmptyState();
    return;
  }
  let view = null;
  const views = [];
  for (const m of conv.messages) {
    if (m.role === 'user') {
      box.append(userBubble(m.content || ''));
      view = null;
      continue;
    }
    if (!view) {
      view = new AssistantView(box);
      views.push(view);
    }
    if (m.role === 'assistant') {
      if (m.reasoning) {
        view.addReasoning(m.reasoning);
        view.endReasoning();
      }
      if (m.content) view.addText(m.content, true);
      for (const tc of m.tool_calls || []) view.addToolCall(tc.id, tc.function.name, tc.function.arguments);
    } else if (m.role === 'tool') {
      view.addToolResult(m.tool_call_id, !(m.content || '').startsWith('Error:'), m.content || '');
    }
  }
  if (conv.running) {
    if (!view) view = new AssistantView(box);
    view.addNotice('Still working on this reply…');
    view.showTyping();
  } else {
    views.forEach((v) => v.markUnfinishedTools());
  }
  state.stick = true;
  scrollToBottom(true);
}

async function openConversation(id, { updateHash = true } = {}) {
  setView('chat', { updateHash: false });
  state.convId = id;
  if (updateHash) history.replaceState(null, '', `#c/${id}`);
  renderConversationList();
  closeSidebar();
  try {
    const conv = await api(`/api/conversations/${id}`);
    if (state.convId !== id) return;
    $('#view-title').textContent = conv.title;
    renderConversation(conv);
    clearTimeout(state.timers.conv);
    if (conv.running || state.streaming.has(id)) {
      state.timers.conv = setTimeout(() => state.convId === id && openConversation(id, { updateHash: false }), 2000);
    }
  } catch (err) {
    toast(`Could not open chat: ${err.message}`);
    newChat();
  }
  updateSendButton();
}

function newChat() {
  state.convId = null;
  clearTimeout(state.timers.conv);
  history.replaceState(null, '', '#');
  setView('chat', { updateHash: false });
  $('#view-title').textContent = 'Self-AI';
  renderEmptyState();
  renderConversationList();
  updateSendButton();
  closeSidebar();
  $('#input').focus();
}

async function loadConversations() {
  try {
    state.conversations = await api('/api/conversations');
    renderConversationList();
  } catch (err) {
    console.error(err);
  }
}

function dateGroup(ts) {
  const d = new Date(ts * 1000);
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const day = 86400000;
  if (d >= start) return 'Today';
  if (d >= start - day) return 'Yesterday';
  if (d >= start - 7 * day) return 'Previous 7 days';
  if (d >= start - 30 * day) return 'Previous 30 days';
  return 'Older';
}

function renderConversationList() {
  const box = $('#conversations');
  box.innerHTML = '';
  let group = null;
  for (const c of state.conversations) {
    const g = dateGroup(c.updated_at);
    if (g !== group) {
      group = g;
      box.append(el('div', { class: 'conv-group', text: g }));
    }
    const actions = el('span', { class: 'conv-actions' },
      el('button', { title: 'Rename', type: 'button', onclick: (e) => { e.stopPropagation(); renameConversation(c); } }, icon('pencil')),
      el('button', { title: 'Delete', type: 'button', onclick: (e) => { e.stopPropagation(); deleteConversation(c); } }, icon('trash')));
    box.append(el('div', {
      class: `conv${c.id === state.convId && state.view === 'chat' ? ' active' : ''}`,
      title: c.title,
      onclick: () => openConversation(c.id),
    },
    c.running || state.streaming.has(c.id) ? el('span', { class: 'tool-status running' }) : null,
    el('span', { class: 'conv-title', text: c.title }),
    actions));
  }
}

async function renameConversation(c) {
  const title = prompt('Rename chat', c.title);
  if (!title || !title.trim()) return;
  try {
    await api(`/api/conversations/${c.id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim() }) });
    if (c.id === state.convId) $('#view-title').textContent = title.trim();
    loadConversations();
  } catch (err) { toast(err.message); }
}

async function deleteConversation(c) {
  if (!confirm(`Delete “${c.title}”?`)) return;
  try {
    await api(`/api/conversations/${c.id}`, { method: 'DELETE' });
    if (c.id === state.convId) newChat();
    loadConversations();
  } catch (err) { toast(err.message); }
}

/* ---------- composer ---------- */

function autoGrow() {
  const input = $('#input');
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}

function isStreamingHere() {
  return state.convId != null && state.streaming.has(state.convId);
}

function updateSendButton() {
  const btn = $('#send-btn');
  const streaming = isStreamingHere();
  btn.innerHTML = streaming ? ICONS.stop : ICONS.send;
  btn.title = streaming ? 'Stop generating' : 'Send';
  btn.disabled = !streaming && !$('#input').value.trim() && !state.attachments.length;
}

function renderAttachments() {
  const box = $('#attachments');
  box.innerHTML = '';
  state.attachments.forEach((a, i) => {
    box.append(el('span', { class: 'chip' }, icon('file'), a.uploading ? `${a.name} (uploading…)` : a.path,
      el('button', { type: 'button', title: 'Remove', onclick: () => { state.attachments.splice(i, 1); renderAttachments(); updateSendButton(); } }, icon('x'))));
  });
}

async function uploadFile(file, dir) {
  const path = `${dir.replace(/\/$/, '')}/${file.name}`.replace(/^\.\//, '');
  const res = await fetch(`/api/files/raw?path=${encodeURIComponent(path)}`, { method: 'PUT', body: file, headers: { 'X-SelfAI': '1' } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(`Upload of ${file.name} failed: ${detail}`);
  }
  return res.json();
}

async function attachFiles(files) {
  for (const file of files) {
    const entry = { name: file.name, path: null, uploading: true };
    state.attachments.push(entry);
    renderAttachments();
    try {
      const saved = await uploadFile(file, 'uploads');
      entry.path = saved.path;
      entry.uploading = false;
    } catch (err) {
      state.attachments.splice(state.attachments.indexOf(entry), 1);
      toast(err.message);
    }
    renderAttachments();
    updateSendButton();
  }
}

async function onSubmit(event) {
  event?.preventDefault();
  if (isStreamingHere()) {
    stopGenerating();
    return;
  }
  const input = $('#input');
  let text = input.value.trim();
  if (state.attachments.some((a) => a.uploading)) {
    toast('Please wait for uploads to finish.');
    return;
  }
  const files = state.attachments.filter((a) => a.path).map((a) => a.path);
  if (!text && !files.length) return;
  if (files.length) {
    const note = `[Attached files in the workspace: ${files.join(', ')}]`;
    text = text ? `${text}\n\n${note}` : `${note}\nPlease take a look at the attached files.`;
  }
  input.value = '';
  state.attachments = [];
  renderAttachments();
  autoGrow();
  await sendMessage(text);
}

async function sendMessage(text) {
  let convId = state.convId;
  try {
    if (!convId) {
      const conv = await api('/api/conversations', { method: 'POST', body: '{}' });
      convId = conv.id;
      state.convId = convId;
      history.replaceState(null, '', `#c/${convId}`);
    }
  } catch (err) {
    toast(`Could not create chat: ${err.message}`);
    return;
  }

  const box = messagesEl();
  if (box.querySelector('.empty')) box.innerHTML = '';
  box.append(userBubble(text));
  const view = new AssistantView(box);
  view.showTyping();
  state.stick = true;
  scrollToBottom(true);

  state.streaming.add(convId);
  updateSendButton();
  loadConversations();

  try {
    const res = await fetch(`/api/conversations/${convId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-SelfAI': '1' },
      body: JSON.stringify({ content: text, model: state.model, think: canThink() ? state.thinking : undefined }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleStreamEvent(raw, view);
      }
    }
  } catch (err) {
    view.addError(err.message || String(err));
  } finally {
    view.hideTyping();
    view.markUnfinishedTools();
    state.streaming.delete(convId);
    updateSendButton();
    loadConversations();
    if (state.convId === convId) {
      api(`/api/conversations/${convId}`).then((c) => { if (state.convId === convId) $('#view-title').textContent = c.title; }).catch(() => {});
    }
  }
}

function handleStreamEvent(raw, view) {
  let eventName = 'message';
  let data = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (eventName === 'end' || !data) return;
  let ev;
  try { ev = JSON.parse(data); } catch { return; }
  switch (ev.type) {
    case 'token': view.addText(ev.text); break;
    case 'reasoning': view.addReasoning(ev.text); break;
    case 'tool_call': view.addToolCall(ev.id, ev.name, ev.arguments); if (ev.name === 'create_task') refreshTasksSoon(); break;
    case 'tool_result':
      view.addToolResult(ev.id, ev.ok, ev.content);
      if (['write_file', 'download_file', 'delete_file'].includes(ev.name) && state.view === 'files') loadFiles();
      break;
    case 'notice': view.addNotice(ev.text); break;
    case 'error': view.addError(ev.error); break;
    case 'stopped': view.addNotice('Stopped.'); break;
    default: break;
  }
  scrollToBottom(false, view.root);
}

async function stopGenerating() {
  if (!state.convId) return;
  try {
    await api(`/api/conversations/${state.convId}/stop`, { method: 'POST' });
  } catch (err) { toast(err.message); }
}

/* ======================================================================
   Tasks view
   ====================================================================== */

function refreshTasksSoon() {
  setTimeout(loadTasks, 800);
}

async function loadTasks() {
  try {
    state.tasks = await api('/api/tasks');
  } catch (err) {
    console.error(err);
    return;
  }
  updateTaskBadge();
  if (state.view === 'tasks') {
    renderTaskList();
    if (state.openTaskId) loadTaskDetail(state.openTaskId);
  }
}

function updateTaskBadge() {
  const running = state.tasks.filter((t) => t.status === 'running').length;
  const badge = $('#tasks-badge');
  badge.textContent = running;
  badge.classList.toggle('hidden', running === 0);
  badge.title = `${running} running`;
}

function scheduleText(t) {
  const parts = [];
  if (t.interval_minutes) parts.push(`every ${t.interval_minutes} min`);
  if (t.status === 'pending' && t.next_run_at) parts.push(`next run ${relativeTime(t.next_run_at)}`);
  if (t.run_count) parts.push(`${t.run_count} run${t.run_count === 1 ? '' : 's'}`);
  if (t.last_run_status && t.interval_minutes) parts.push(`last: ${t.last_run_status}`);
  parts.push(t.created_by === 'user' ? 'created by you' : 'created by the AI');
  return parts.join(' · ');
}

function renderTaskList() {
  const box = $('#task-list');
  box.innerHTML = '';
  if (!state.tasks.length) {
    box.append(el('div', { class: 'empty-list', text: 'No background tasks yet. Create one above, or ask the AI in chat to do something in the background.' }));
    return;
  }
  for (const t of state.tasks) {
    box.append(el('div', { class: 'card task-card', onclick: () => openTask(t.id) },
      el('div', { class: 'head' },
        t.status === 'running' ? el('span', { class: 'tool-status running' }) : null,
        el('span', { class: 'title', text: t.title }),
        el('span', { class: `status ${t.status}`, text: t.status })),
      el('div', { class: 'meta', text: scheduleText(t) }),
      t.last_result ? el('div', { class: 'result', text: plainSnippet(t.last_result) }) : null));
  }
}

async function createTaskFromForm(event) {
  event.preventDefault();
  const goal = $('#task-goal').value.trim();
  if (!goal) return;
  const body = {
    title: $('#task-title').value.trim(),
    goal,
    delay_minutes: Number($('#task-delay').value) || 0,
    interval_minutes: $('#task-repeat').checked ? Number($('#task-interval').value) || 60 : null,
  };
  try {
    const task = await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) });
    $('#task-form').reset();
    await loadTasks();
    openTask(task.id);
  } catch (err) { toast(err.message); }
}

function openTask(id) {
  state.openTaskId = id;
  state.lastTaskRender = null;
  history.replaceState(null, '', `#tasks/${id}`);
  loadTaskDetail(id);
}

function closeTask() {
  if (!state.openTaskId) return;
  state.openTaskId = null;
  state.lastTaskRender = null;
  $('#task-drawer').classList.add('hidden');
  if (state.view === 'tasks') history.replaceState(null, '', '#tasks');
}

async function taskAction(id, action) {
  try {
    if (action === 'delete') {
      if (!confirm('Delete this task and its history?')) return;
      await api(`/api/tasks/${id}`, { method: 'DELETE' });
      closeTask();
    } else {
      await api(`/api/tasks/${id}/${action}`, { method: 'POST' });
    }
    await loadTasks();
  } catch (err) { toast(err.message); }
}

function renderEvents(events) {
  const out = [];
  const pending = [];
  for (const ev of events) {
    const d = ev.data;
    if (ev.type === 'tool_call') {
      const view = createToolView(d.name, d.arguments);
      pending.push(view);
      out.push(view.details);
    } else if (ev.type === 'tool_result') {
      const view = pending.shift();
      view?.setResult(d.ok, d.content);
    } else if (ev.type === 'thought') {
      out.push(markdownBlock(d.content, 'markdown thought'));
    } else if (ev.type === 'notice') {
      out.push(el('div', { class: 'notice', text: d.content }));
    } else if (ev.type === 'error') {
      out.push(el('div', { class: 'error-box', text: d.content }));
    } else if (ev.type === 'final') {
      out.push(el('div', { class: 'final' }, el('div', { class: 'final-title', text: 'Final report' }), markdownBlock(d.content)));
    }
  }
  return { nodes: out, unfinished: pending };
}

async function loadTaskDetail(id) {
  let task;
  try {
    task = await api(`/api/tasks/${id}`);
  } catch (err) {
    closeTask();
    toast(err.message);
    return;
  }
  if (state.openTaskId !== id) return;
  const signature = JSON.stringify(task);
  if (state.lastTaskRender === signature) return; // nothing changed; keep open/closed sections as they are
  state.lastTaskRender = signature;

  const drawer = $('#task-drawer');
  const openKeys = new Set($$('details[open][data-key]', drawer).map((d) => d.dataset.key));
  const firstRender = drawer.classList.contains('hidden') || drawer.dataset.taskId !== id;
  const scroll = $('.drawer-body', drawer)?.scrollTop || 0;
  drawer.dataset.taskId = id;
  drawer.classList.remove('hidden');
  drawer.innerHTML = '';

  const btn = (label, iconName, action, cls = 'btn') =>
    el('button', { class: cls, type: 'button', onclick: () => taskAction(id, action) }, icon(iconName), label);
  const actions = el('div', { class: 'drawer-actions' });
  if (task.status !== 'running') actions.append(btn(task.run_count ? 'Run again now' : 'Run now', 'play', 'run'));
  if (task.status === 'pending' || task.status === 'running') actions.append(btn('Pause', 'pause', 'pause'));
  if (task.status === 'paused') actions.append(btn('Resume', 'play', 'resume'));
  if (['pending', 'running', 'paused'].includes(task.status)) actions.append(btn('Cancel', 'x', 'cancel'));
  actions.append(btn('Delete', 'trash', 'delete', 'btn danger'));

  const body = el('div', { class: 'drawer-body' },
    el('div', { class: 'meta muted', style: 'margin-bottom:12px;font-size:13px' },
      el('span', { class: `status ${task.status}`, text: task.status }), '  ', scheduleText(task)),
    actions,
    el('div', { class: 'section-title', text: 'Goal' }),
    el('div', { class: 'goal', text: task.goal }),
    el('div', { class: 'section-title', text: 'Runs' }));

  if (!task.runs.length) body.append(el('div', { class: 'muted', text: task.next_run_at ? `Starts ${relativeTime(task.next_run_at)}.` : 'Not scheduled.' }));
  const totalRuns = task.run_count + (task.runs[0]?.status === 'running' ? 1 : 0);
  task.runs.forEach((run, i) => {
    const key = `run-${run.id}`;
    const details = el('details', { class: 'run', 'data-key': key },
      el('summary', {},
        run.status === 'running' ? el('span', { class: 'tool-status running' }) : el('span', { class: `status ${run.status}`, text: run.status }),
        el('span', { style: 'flex:1', text: `Run #${totalRuns - i}` }),
        el('span', { class: 'muted', style: 'font-size:13px', text: `${formatTime(run.started_at)}${run.finished_at ? ` – ${formatTime(run.finished_at)}` : ''}` })));
    const runBody = el('div', { class: 'run-body' });
    if (run.events) {
      const { nodes, unfinished } = renderEvents(run.events);
      nodes.forEach((n, j) => { if (n.tagName === 'DETAILS') n.dataset.key = `${key}-${j}`; });
      runBody.append(...nodes);
      if (run.status !== 'running') unfinished.forEach((v) => v.setInterrupted());
      if (run.status === 'running') runBody.append(el('div', { class: 'typing' }, el('span'), el('span'), el('span')));
      if (!nodes.length && run.status !== 'running') runBody.append(el('div', { class: 'muted', text: run.result || 'No activity recorded.' }));
    } else if (run.result) {
      runBody.append(markdownBlock(run.result));
    }
    details.append(runBody);
    if (openKeys.has(key) || (firstRender && i === 0)) details.open = true;
    body.append(details);
  });
  $$('details[data-key]', body).forEach((d) => { if (openKeys.has(d.dataset.key)) d.open = true; });

  drawer.append(
    el('div', { class: 'drawer-head' },
      el('button', { class: 'icon-btn', type: 'button', title: 'Close', onclick: closeTask }, icon('back')),
      el('h3', { text: task.title })),
    body);
  body.scrollTop = firstRender ? 0 : scroll;
}

/* ======================================================================
   Files view
   ====================================================================== */

const TEXT_EXT = new Set(['txt', 'md', 'markdown', 'py', 'js', 'ts', 'json', 'csv', 'tsv', 'html', 'htm', 'css', 'xml', 'yml', 'yaml', 'toml', 'ini', 'log', 'sh', 'sql', 'java', 'c', 'cpp', 'h', 'go', 'rs', 'rb', 'php', 'jsx', 'tsx', 'svg', 'env', 'cfg']);
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif']);

async function loadFiles(path = state.filesPath) {
  let data;
  try {
    data = await api(`/api/files?path=${encodeURIComponent(path)}`);
  } catch (err) {
    if (path !== '.') return loadFiles('.');
    toast(err.message);
    return;
  }
  state.filesPath = data.path;
  renderBreadcrumb(data.path);
  const list = $('#file-list');
  list.innerHTML = '';
  if (data.path !== '.') {
    const parent = data.path.split('/').slice(0, -1).join('/') || '.';
    list.append(el('div', { class: 'file-row dir', onclick: () => loadFiles(parent) }, icon('back'), el('span', { class: 'name', text: '..' })));
  }
  if (!data.entries.length) {
    list.append(el('div', { class: 'empty-list', text: data.path === '.' ? 'The workspace is empty. Files the AI creates will appear here.' : 'This folder is empty.' }));
  }
  for (const entry of data.entries) {
    list.append(el('div', { class: `file-row${entry.is_dir ? ' dir' : ''}`, onclick: () => (entry.is_dir ? loadFiles(entry.path) : previewFile(entry)) },
      icon(entry.is_dir ? 'folder' : 'file'),
      el('span', { class: 'name', text: entry.name }),
      el('span', { class: 'date', text: formatTime(entry.modified) }),
      el('span', { class: 'size', text: entry.is_dir ? '' : humanSize(entry.size) })));
  }
}

function renderBreadcrumb(path) {
  const box = $('#breadcrumb');
  box.innerHTML = '';
  box.append(el('button', { type: 'button', onclick: () => loadFiles('.') , text: 'Workspace' }));
  if (path === '.') return;
  let acc = '';
  for (const part of path.split('/')) {
    acc = acc ? `${acc}/${part}` : part;
    const target = acc;
    box.append(el('span', { class: 'sep', text: '/' }), el('button', { type: 'button', text: part, onclick: () => loadFiles(target) }));
  }
}

function closeFilePreview() {
  $('#file-drawer').classList.add('hidden');
}

async function previewFile(entry) {
  const drawer = $('#file-drawer');
  const ext = (entry.name.split('.').pop() || '').toLowerCase();
  const content = el('div', { class: 'drawer-body' });
  drawer.innerHTML = '';
  drawer.append(
    el('div', { class: 'drawer-head' },
      el('button', { class: 'icon-btn', type: 'button', title: 'Close', onclick: closeFilePreview }, icon('back')),
      el('h3', { text: entry.path }),
      el('a', { class: 'btn', href: fileUrl(entry.path), target: '_blank', rel: 'noopener' }, icon('globe'), 'Open'),
      el('a', { class: 'btn', href: fileUrl(entry.path, true) }, icon('download'), 'Download'),
      el('button', { class: 'btn danger', type: 'button', title: 'Delete', onclick: () => deleteFile(entry) }, icon('trash'))),
    content);
  drawer.classList.remove('hidden');

  if (IMAGE_EXT.has(ext)) {
    content.append(el('img', { class: 'preview-img', src: fileUrl(entry.path), alt: entry.name }));
  } else if (ext === 'pdf') {
    content.append(el('iframe', { class: 'preview-frame', src: fileUrl(entry.path), title: entry.name }));
  } else if (TEXT_EXT.has(ext) || entry.size < 200 * 1024) {
    if (entry.size > 2 * 1024 * 1024) {
      content.append(el('div', { class: 'muted', text: 'This file is too large to preview. Use Open or Download.' }));
      return;
    }
    try {
      const res = await fetch(fileUrl(entry.path));
      const text = await res.text();
      if (ext === 'md' || ext === 'markdown') {
        content.append(markdownBlock(text));
      } else if (ext === 'html' || ext === 'htm') {
        content.append(el('iframe', { class: 'preview-frame', src: fileUrl(entry.path), sandbox: 'allow-scripts', title: entry.name }));
      } else {
        const code = el('code', { class: `language-${ext}`, text });
        content.append(el('pre', {}, code));
        if (window.hljs && text.length < 300000) { try { hljs.highlightElement(code); } catch { /* ignore */ } }
      }
    } catch (err) {
      content.append(el('div', { class: 'error-box', text: err.message }));
    }
  } else {
    content.append(el('div', { class: 'muted', text: 'No preview available for this file type. Use Open or Download.' }));
  }
}

async function deleteFile(entry) {
  if (!confirm(`Delete ${entry.path}?`)) return;
  try {
    await api(`/api/files?path=${encodeURIComponent(entry.path)}`, { method: 'DELETE' });
    closeFilePreview();
    loadFiles();
  } catch (err) { toast(err.message); }
}

async function uploadToCurrentFolder(files) {
  for (const file of files) {
    try { await uploadFile(file, state.filesPath); } catch (err) { toast(err.message); }
  }
  loadFiles();
}

/* ======================================================================
   Navigation, status, startup
   ====================================================================== */

function setView(view, { updateHash = true } = {}) {
  state.view = view;
  for (const name of ['chat', 'tasks', 'files', 'models']) $(`#view-${name}`).classList.toggle('hidden', name !== view);
  $$('.views .nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('#model-picker').classList.toggle('hidden', view !== 'chat');
  clearInterval(state.timers.view);
  clearTimeout(state.timers.models);
  if (view === 'models') {
    $('#view-title').textContent = 'Models';
    if (updateHash) history.replaceState(null, '', '#models');
    loadModels();
  } else if (view === 'tasks') {
    $('#view-title').textContent = 'Background tasks';
    if (updateHash) history.replaceState(null, '', '#tasks');
    loadTasks();
    state.timers.view = setInterval(loadTasks, 3000);
  } else if (view === 'files') {
    $('#view-title').textContent = 'Files';
    if (updateHash) history.replaceState(null, '', '#files');
    loadFiles();
    state.timers.view = setInterval(() => loadFiles(), 10000);
  } else {
    $('#view-title').textContent = state.conversations.find((c) => c.id === state.convId)?.title || 'Self-AI';
  }
  renderConversationList();
  closeSidebar();
}

function openSidebar() {
  $('#sidebar').classList.add('open');
  $('#scrim').classList.add('open');
}
function closeSidebar() {
  $('#sidebar').classList.remove('open');
  $('#scrim').classList.remove('open');
}

function canThink() {
  const s = state.status;
  return !!(s && s.provider === 'ollama' && (s.capabilities || []).includes('thinking'));
}

function updateThinkButton() {
  const btn = $('#think-btn');
  btn.classList.toggle('hidden', !canThink());
  btn.classList.toggle('on', state.thinking);
  btn.setAttribute('aria-pressed', String(state.thinking));
}

// Escape text, then show `backticked` parts as code.
function richText(text) {
  return escapeHtml(text).replace(/`([^`]+)`/g, '<code>$1</code>');
}

async function loadStatus() {
  try {
    state.status = await api('/api/status');
  } catch {
    return;
  }
  const s = state.status;
  const local = s.provider === 'ollama';
  state.model = s.model;

  const select = $('#model-select');
  const options = s.models.length ? s.models : [];
  if (select.dataset.options !== JSON.stringify(options)) {
    select.dataset.options = JSON.stringify(options);
    select.innerHTML = '';
    for (const m of options) select.append(el('option', { value: m, text: m }));
    if (!options.length) select.append(el('option', { value: '', text: local ? 'No model yet' : '—', disabled: true }));
  }
  select.value = options.includes(s.model) ? s.model : '';
  $('#models-nav').classList.toggle('hidden', !local);
  updateThinkButton();

  const foot = $('#status-line');
  foot.innerHTML = '';
  const label = s.ready ? `${local ? 'On this device' : 'DeepSeek API'} · ${s.model}` : 'Not ready';
  foot.append(
    el('div', {}, el('span', { class: `dot${s.ready ? '' : ' bad'}` }), label),
    el('div', { text: `Search: ${s.search_provider} · v${s.version}` }));

  const banner = $('#setup-banner');
  banner.classList.toggle('hidden', s.ready);
  banner.innerHTML = '';
  if (!s.ready) {
    const msg = el('span');
    msg.innerHTML = richText(s.problem || 'The AI is not ready.');
    banner.append(msg);
    if (s.problem_code === 'no_models' || s.problem_code === 'model_missing') {
      banner.append(' ', el('button', { type: 'button', class: 'btn', onclick: () => setView('models') }, icon('download'), 'Download a free model'));
    }
  }
}

async function chooseModel(name) {
  if (!name) return;
  try {
    await api('/api/models/active', { method: 'POST', body: JSON.stringify({ name }) });
  } catch (err) { toast(err.message); }
  await loadStatus();
  if (state.view === 'models') loadModels();
}

/* ======================================================================
   Models view (free local models via Ollama)
   ====================================================================== */

function capabilityBadges(caps) {
  if (!caps) return [];
  const labels = { tools: 'web & files', thinking: 'can think', vision: 'images', audio: 'audio' };
  return caps.filter((c) => labels[c]).map((c) => el('span', { class: 'pill', text: labels[c] }));
}

function progressBar(pull) {
  const pct = pull.total ? Math.floor((pull.completed / pull.total) * 100) : 0;
  const detail = pull.total ? `${pct}% · ${humanSize(pull.completed)} of ${humanSize(pull.total)}` : pull.status;
  return el('div', { class: 'progress-wrap' },
    el('div', { class: 'progress' }, el('div', { class: 'progress-fill', style: `width:${pct}%` })),
    el('div', { class: 'muted progress-text', text: detail }));
}

function modelCard(m, data) {
  const pull = data.pulls[m.name];
  const pulling = pull && !pull.done;
  const active = data.active === m.name;
  // Devices report a bit less RAM than advertised, so allow some slack.
  const tooBig = data.ram_gb && m.min_ram_gb && data.ram_gb < m.min_ram_gb * 0.85;
  const head = el('div', { class: 'head' },
    el('span', { class: 'title', text: m.title || m.name }),
    m.name === data.suggested && !m.installed ? el('span', { class: 'pill accent', text: 'Best for this device' }) : null,
    active ? el('span', { class: 'pill ok', text: 'In use' }) : null);
  const meta = [m.name];
  if (m.size_gb) meta.push(`≈${m.size_gb} GB download`);
  else if (m.size) meta.push(humanSize(m.size));
  if (m.min_ram_gb) meta.push(`needs ${m.min_ram_gb} GB+ RAM`);
  const actions = el('div', { class: 'model-actions' });
  if (pulling) {
    actions.append(progressBar(pull));
  } else if (m.installed) {
    if (!active) actions.append(el('button', { class: 'primary', type: 'button', onclick: () => chooseModel(m.name) }, 'Use this model'));
    actions.append(el('button', { class: 'btn danger', type: 'button', title: 'Delete from this device', onclick: () => removeModel(m.name) }, icon('trash'), 'Delete'));
  } else {
    actions.append(el('button', { class: 'primary', type: 'button', onclick: () => pullModel(m.name) }, icon('download'), 'Download'));
  }
  return el('div', { class: `card model-card${active ? ' active' : ''}` },
    head,
    m.description ? el('div', { class: 'desc', text: m.description }) : null,
    el('div', { class: 'meta muted', text: meta.join(' · ') }),
    m.capabilities ? el('div', { class: 'pills' }, capabilityBadges(m.capabilities)) : null,
    m.capabilities && !m.capabilities.includes('tools') ? el('div', { class: 'warn', text: "This model can't use tools, so it can't browse the web or create files." }) : null,
    tooBig && !m.installed ? el('div', { class: 'warn', text: `This device has ${data.ram_gb} GB RAM, so this model may not fit.` }) : null,
    pull && pull.error ? el('div', { class: 'error-box', text: `Download failed: ${pull.error}` }) : null,
    actions);
}

async function loadModels() {
  clearTimeout(state.timers.models);
  let data;
  try {
    data = await api('/api/models');
  } catch (err) {
    toast(err.message);
    return;
  }
  const finished = state.models && Object.entries(data.pulls).some(([name, p]) => p.done && !state.models.pulls[name]?.done);
  state.models = data;
  if (state.view !== 'models') return;

  const suggested = data.recommended.find((r) => r.name === data.suggested);
  $('#models-device').textContent = data.ram_gb
    ? `This ${data.android ? 'phone' : 'device'} has ${data.ram_gb} GB RAM. Recommended: ${suggested ? suggested.title : data.suggested}.`
    : '';
  const errorBox = $('#models-error');
  errorBox.innerHTML = '';
  if (data.error) {
    const msg = el('div', { class: 'error-box' });
    msg.innerHTML = richText(data.error);
    errorBox.append(msg);
  }
  const recommended = $('#models-recommended');
  recommended.innerHTML = '';
  const installedByName = Object.fromEntries(data.installed.map((m) => [m.name, m]));
  for (const r of data.recommended) {
    const inst = installedByName[r.name];
    recommended.append(modelCard({ ...r, capabilities: inst?.capabilities, size: inst?.size }, data));
  }
  const recNames = new Set(data.recommended.map((r) => r.name));
  const others = data.installed.filter((m) => !recNames.has(m.name));
  const pendingOthers = Object.keys(data.pulls).filter((n) => !recNames.has(n) && !installedByName[n]);
  $('#models-other-wrap').classList.toggle('hidden', !others.length && !pendingOthers.length);
  const other = $('#models-other');
  other.innerHTML = '';
  for (const m of others) other.append(modelCard({ ...m, installed: true }, data));
  for (const n of pendingOthers) other.append(modelCard({ name: n, installed: false }, data));

  if (finished) loadStatus();
  const busy = Object.values(data.pulls).some((p) => !p.done);
  state.timers.models = setTimeout(loadModels, busy ? 1000 : 5000);
}

async function pullModel(name) {
  try {
    await api('/api/models/pull', { method: 'POST', body: JSON.stringify({ name }) });
  } catch (err) { toast(err.message); }
  loadModels();
}

async function removeModel(name) {
  if (!confirm(`Delete ${name} from this device? You can download it again later.`)) return;
  try {
    await api(`/api/models?name=${encodeURIComponent(name)}`, { method: 'DELETE' });
  } catch (err) { toast(err.message); }
  await loadStatus();
  loadModels();
}

function route() {
  const hash = location.hash.replace(/^#/, '');
  if (hash.startsWith('c/')) openConversation(hash.slice(2), { updateHash: false });
  else if (hash.startsWith('tasks')) {
    setView('tasks', { updateHash: false });
    const id = hash.split('/')[1];
    if (id) openTask(id);
  } else if (hash === 'files') setView('files', { updateHash: false });
  else if (hash === 'models') setView('models', { updateHash: false });
  else newChat();
}

function init() {
  hydrateIcons();
  $('#new-chat').addEventListener('click', newChat);
  $$('.views .nav-btn').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
  $('#open-sidebar').addEventListener('click', openSidebar);
  $('#close-sidebar').addEventListener('click', closeSidebar);
  $('#scrim').addEventListener('click', closeSidebar);
  $('#model-select').addEventListener('change', (e) => chooseModel(e.target.value));
  state.thinking = storageGet('selfai-think') === '1';
  $('#think-btn').addEventListener('click', () => {
    state.thinking = !state.thinking;
    storageSet('selfai-think', state.thinking ? '1' : '0');
    updateThinkButton();
  });
  $('#custom-model-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const name = $('#custom-model').value.trim();
    if (name) { pullModel(name); $('#custom-model').value = ''; }
  });

  const input = $('#input');
  input.addEventListener('input', () => { autoGrow(); updateSendButton(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!isStreamingHere()) onSubmit();
    }
  });
  $('#composer').addEventListener('submit', onSubmit);
  $('#attach-btn').addEventListener('click', () => $('#attach-input').click());
  $('#attach-input').addEventListener('change', (e) => { attachFiles([...e.target.files]); e.target.value = ''; });
  messagesEl().addEventListener('scroll', () => {
    const box = messagesEl();
    state.stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  });
  const chatView = $('#view-chat');
  chatView.addEventListener('dragover', (e) => { e.preventDefault(); chatView.classList.add('drop-hint'); });
  chatView.addEventListener('dragleave', () => chatView.classList.remove('drop-hint'));
  chatView.addEventListener('drop', (e) => { e.preventDefault(); chatView.classList.remove('drop-hint'); attachFiles([...e.dataTransfer.files]); });

  $('#task-form').addEventListener('submit', createTaskFromForm);
  $('#files-refresh').addEventListener('click', () => loadFiles());
  $('#files-upload-btn').addEventListener('click', () => $('#files-upload').click());
  $('#files-upload').addEventListener('change', (e) => { uploadToCurrentFolder([...e.target.files]); e.target.value = ''; });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeTask(); closeFilePreview(); closeSidebar(); }
  });

  loadStatus();
  loadConversations().then(route);
  loadTasks();
  setInterval(() => { loadTasks(); loadStatus(); if (state.view === 'chat') loadConversations(); }, 15000);
}

init();
