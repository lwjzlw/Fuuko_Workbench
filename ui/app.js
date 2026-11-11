import { api, toast, el, tokenUrl, icon, hydrateIcons } from '/_shared/wbkit.js';

const $ = (id) => document.getElementById(id);
const MODE_LABEL = { tab: '打开', window: '新窗口打开', launch: '打开' };
const MODE_HINT = { tab: '在工作台的标签页里打开', window: '用独立窗口打开', launch: '启动这个程序' };
const ICON_LABEL = { window: '窗口', spark: '星光', image: '图片', music: '音乐', book: '书本', tools: '工具',
  grid: '方格', star: '星标', file: '文件', activity: '波形', play: '播放', home: '主页' };
const FALLBACK_CATEGORY = '其他';

let apps = [];
let appsLoaded = false;
let favorites = []; // 常用应用 id，按加入顺序
let categories = []; // [{name, icon, derived?}]，顺序即侧栏顺序（服务端保证每个入口的分类都在里面）
let iconNames = Object.keys(ICON_LABEL);
let windows = []; // 共用窗口程序打开的独立窗口
let windowsLoaded = false;
let manageFilter = 'all'; // all | running | hidden
let manageQuery = '';
let registryState = null;
let registryError = '';
let registryLoading = null;
let registryBusy = false;
let rootsDraft = '';
let rootsDirty = false;
let manifestDraft = '';
let replaceImport = false;
let categoryDraft = '';
let view = null; // 首页当前视图：'fav' | 'all' | 'manage' | 'cat:<分类名>'
const openTabs = new Map(); // app id -> { tab, panel, url, app, timer }
let current = 'home';
let online = true;
const VIEW_KEY = 'fuuko-view';
const THEME_KEY = 'fuuko-theme';
const SIDEBAR_KEY = 'fuuko-sidebar';
const RECENT_KEY = 'fuuko-recent';

const store = {
  get(key, fallback = null) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* 无痕模式 */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* 无痕模式 */ } },
};

hydrateIcons();

// ---------------------------------------------------------------- 确认框
function ask(title, message, okLabel = '确定', danger = false) {
  const dialog = $('confirm-dialog');
  $('confirm-title').textContent = title;
  $('confirm-message').textContent = message;
  $('confirm-ok').textContent = okLabel;
  $('confirm-ok').className = `btn ${danger ? 'danger solid' : 'primary'}`;
  $('confirm-icon').replaceChildren(icon(danger ? 'alert' : 'info'));
  $('confirm-icon').classList.toggle('danger', danger);
  dialog.returnValue = '';
  dialog.showModal();
  $('confirm-cancel').focus();
  return new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), { once: true }));
}

// ---------------------------------------------------------------- 主题（浅色 / 深色 / 跟随系统）
const THEME_ORDER = ['system', 'light', 'dark'];
const THEME_LABEL = { system: '跟随系统', light: '浅色', dark: '深色' };
const THEME_ICON = { system: 'monitor', light: 'sun', dark: 'moon' };
const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
let themeMode = THEME_ORDER.includes(store.get(THEME_KEY)) ? store.get(THEME_KEY) : 'system';

function applyTheme() {
  const dark = themeMode === 'dark' || (themeMode === 'system' && systemDark.matches);
  if (dark) document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  const next = THEME_ORDER[(THEME_ORDER.indexOf(themeMode) + 1) % THEME_ORDER.length];
  $('theme').replaceChildren(icon(THEME_ICON[themeMode]));
  $('theme').title = `主题：${THEME_LABEL[themeMode]}（点击改为${THEME_LABEL[next]}）`;
  $('theme').setAttribute('aria-label', $('theme').title);
}
function cycleTheme() {
  themeMode = THEME_ORDER[(THEME_ORDER.indexOf(themeMode) + 1) % THEME_ORDER.length];
  store.set(THEME_KEY, themeMode);
  applyTheme();
  toast(`主题：${THEME_LABEL[themeMode]}`);
}
$('theme').addEventListener('click', cycleTheme);
systemDark.addEventListener('change', () => { if (themeMode === 'system') applyTheme(); });
applyTheme();

// ---------------------------------------------------------------- 侧栏折叠
function setSidebar(collapsed, remember = true) {
  if (collapsed) document.documentElement.dataset.sidebar = 'collapsed';
  else delete document.documentElement.dataset.sidebar;
  if (remember) store.set(SIDEBAR_KEY, collapsed ? 'collapsed' : 'open');
  const label = collapsed ? '展开侧栏' : '收起侧栏';
  $('sidebar-toggle').replaceChildren(icon('sidebar'));
  $('sidebar-toggle').title = label;
  $('sidebar-toggle').setAttribute('aria-label', label);
  $('sidebar-toggle').setAttribute('aria-expanded', String(!collapsed));
}
const sidebarCollapsed = () => document.documentElement.dataset.sidebar === 'collapsed';
$('sidebar-toggle').addEventListener('click', () => setSidebar(!sidebarCollapsed()));
setSidebar(sidebarCollapsed(), false);

// ---------------------------------------------------------------- 最近使用（只存在本机浏览器里）
let recent = (() => {
  try { const list = JSON.parse(store.get(RECENT_KEY, '[]')); return Array.isArray(list) ? list.filter((r) => r && r.id) : []; }
  catch { return []; }
})();
function touchRecent(id) {
  recent = [{ id, t: Date.now() }, ...recent.filter((r) => r.id !== id)].slice(0, 12);
  store.set(RECENT_KEY, JSON.stringify(recent));
}
function clearRecent() {
  recent = [];
  store.remove(RECENT_KEY);
  toast('已清除最近使用记录');
  render();
}
const recentApps = () => recent.map((r) => ({ app: appById(r.id), t: r.t })).filter((x) => x.app && !x.app.hidden);

function ago(t) {
  const seconds = Math.max(0, (Date.now() - t) / 1000);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  const days = Math.floor(seconds / 86400);
  if (days === 1) return '昨天';
  if (days < 7) return `${days} 天前`;
  const d = new Date(t);
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

function greeting(now = new Date()) {
  const h = now.getHours();
  if (h < 5) return '夜深了'; // 后面接“，名字”，句末提醒放在概况里
  if (h < 9) return '早上好';
  if (h < 12) return '上午好';
  if (h < 14) return '中午好';
  if (h < 18) return '下午好';
  if (h < 23) return '晚上好';
  return '夜深了';
}
const dateLine = (now = new Date()) => `${now.getMonth() + 1} 月 ${now.getDate()} 日 · 星期${'日一二三四五六'[now.getDay()]}`;

// ---------------------------------------------------------------- 视图切换（首页 / 已打开的页签）
const appById = (id) => apps.find((a) => a.id === id);
const categoryIcon = (name) => categories.find((c) => c.name === name)?.icon || 'grid';
function viewLabel() {
  if (view === 'manage') return '管理';
  if (view === 'all') return '全部';
  if (view?.startsWith('cat:')) return view.slice(4);
  return '首页';
}

function show(id) {
  if (id !== 'home' && !openTabs.has(id)) id = 'home';
  const wasHome = current === 'home';
  current = id;
  const home = id === 'home';
  // 从页签回到首页时重画一次，卡片上的“已在标签页”“运行中”保持最新
  if (home && !wasHome && appsLoaded) renderKeepFocus();
  $('panel-home').hidden = !home;
  for (const [appId, entry] of openTabs) {
    const active = appId === id;
    entry.panel.hidden = !active;
    entry.tab.setAttribute('aria-selected', String(active));
    entry.tab.tabIndex = active ? 0 : -1;
  }
  $('topbar-actions').hidden = !home;
  $('tab-actions').hidden = home;
  const tabApp = home ? null : appById(id) || openTabs.get(id).app;
  $('tab-stop').hidden = !(tabApp && canStop(tabApp));
  $('tab-window').disabled = Boolean(tabApp && (tabApp.available === false || !tabApp.open.includes('window')));
  const crumb = home ? viewLabel() : tabApp.name;
  $('breadcrumbs').replaceChildren(el('span', {}, hubName()), el('span', { class: 'crumb-sep' }, '/'), el('strong', {}, crumb));
  for (const link of document.querySelectorAll('.sidebar .nav-item[data-slug]')) {
    link.setAttribute('aria-current', String(home && link.dataset.slug === view));
  }
  document.title = home ? hubName() : `${crumb} · ${hubName()}`;
}

// 页签里的页面：加载时盖一层提示，超过 10 秒还没好就给出补救办法
function loadFrame(entry, url) {
  clearTimeout(entry.timer);
  entry.url = url;
  entry.panel.classList.remove('loaded', 'slow');
  entry.timer = setTimeout(() => entry.panel.classList.add('slow'), 10000);
  entry.panel.querySelector('iframe').src = url;
}

function openInTab(app, url) {
  const existing = openTabs.get(app.id);
  if (existing) {
    if (existing.url !== url) loadFrame(existing, url);
    show(app.id);
    return;
  }
  const tab = el('button', { type: 'button', role: 'tab', id: `tab-${app.id}`, class: 'nav-item open-item',
    'aria-controls': `panel-${app.id}`, 'data-app': app.id, title: `${app.name}（中键关闭）` },
    app.has_icon ? el('img', { src: tokenUrl('/api/icon', { id: app.id }), alt: '' }) : icon(categoryIcon(app.category)),
    el('span', { class: 'label' }, app.name),
    el('span', { class: 'close', 'data-close': app.id, title: '关闭页签（Ctrl+W）', 'aria-hidden': 'true' }, icon('close')));
  const frame = el('iframe', { title: app.name, allow: 'clipboard-read; clipboard-write; autoplay' });
  const panel = el('section', { id: `panel-${app.id}`, class: 'frame-panel', role: 'tabpanel', 'aria-labelledby': tab.id, hidden: true },
    frame,
    el('div', { class: 'frame-loading', 'aria-live': 'polite' },
      el('div', { class: 'loader-mark' }, appIcon(app)),
      el('p', { class: 'loader-text' }, `正在打开 ${app.name}…`),
      el('div', { class: 'loader-slow' },
        el('p', { class: 'muted' }, '比平时久了一些。它可能还在启动，也可以试试：'),
        el('div', { class: 'row' },
          el('button', { type: 'button', class: 'btn small', onclick: () => { const e = openTabs.get(app.id); if (e) loadFrame(e, e.url); } },
            icon('refresh'), '重新载入'),
          app.open.includes('window') ? el('button', { type: 'button', class: 'btn small subtle', onclick: () => openApp(app, 'window') },
            icon('window'), '改用新窗口') : null))));
  const entry = { tab, panel, url, app, timer: null };
  frame.addEventListener('load', () => { clearTimeout(entry.timer); panel.classList.add('loaded'); panel.classList.remove('slow'); });
  $('tablist').append(tab);
  $('panels').append(panel);
  openTabs.set(app.id, entry);
  loadFrame(entry, url);
  $('open-label').hidden = false;
  show(app.id);
  if (view === 'manage') renderManageSoon();
}

function closeTab(id) {
  const entry = openTabs.get(id);
  if (!entry) return;
  clearTimeout(entry.timer);
  entry.tab.remove();
  entry.panel.remove();
  openTabs.delete(id);
  $('open-label').hidden = openTabs.size === 0;
  if (current === id) show('home');
  else if (current === 'home') renderKeepFocus();
}

function closeAllTabs() {
  for (const id of [...openTabs.keys()]) closeTab(id);
}

$('tablist').addEventListener('click', (event) => {
  const close = event.target.closest('[data-close]');
  if (close) { event.stopPropagation(); closeTab(close.dataset.close); return; }
  const tab = event.target.closest('[role="tab"]');
  if (tab) show(tab.dataset.app);
});
// 中键关闭页签，和浏览器习惯一致
$('tablist').addEventListener('mousedown', (event) => { if (event.button === 1) event.preventDefault(); });
$('tablist').addEventListener('auxclick', (event) => {
  const tab = event.target.closest('[role="tab"]');
  if (tab && event.button === 1) { event.preventDefault(); closeTab(tab.dataset.app); }
});
$('tablist').addEventListener('keydown', (event) => {
  const tabs = [...$('tablist').querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (step) { event.preventDefault(); const next = tabs[(index + step + tabs.length) % tabs.length]; show(next.dataset.app); next.focus(); }
  if (event.key === 'Delete') closeTab(tabs[index].dataset.app);
});
document.addEventListener('keydown', (event) => {
  const key = event.key.toLowerCase();
  if (event.ctrlKey && key === 'w' && current !== 'home') { event.preventDefault(); closeTab(current); }
  if (event.ctrlKey && key === 'k') { event.preventDefault(); togglePalette(); }
  if (event.altKey && !event.ctrlKey && /^[0-9]$/.test(event.key)) {
    const ids = [...openTabs.keys()];
    const n = Number(event.key);
    if (n === 0) { event.preventDefault(); show('home'); }
    else if (ids[n - 1]) { event.preventDefault(); show(ids[n - 1]); }
  }
  if (event.key === '/' && !event.ctrlKey && !event.altKey && !isTyping(event.target) && !$('palette').open
      && !$('confirm-dialog').open) { event.preventDefault(); openPalette(); }
});
const isTyping = (node) => node instanceof HTMLElement && (node.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName));

$('tab-close').addEventListener('click', () => closeTab(current));
$('tab-reload').addEventListener('click', () => {
  const entry = openTabs.get(current);
  if (entry) loadFrame(entry, entry.url);
});
$('tab-window').addEventListener('click', () => {
  const entry = openTabs.get(current);
  if (entry) openApp(entry.app, 'window');
});
$('tab-stop').addEventListener('click', (e) => {
  const app = appById(current);
  if (app) stopApp(app, e.currentTarget);
});

// ---------------------------------------------------------------- 打开 / 关闭 / 重启
// 操作进行中：按钮显示转圈和文字；启动较慢时把文字换成“还在启动”，让人知道没有卡住
async function busy(button, text, fn, slowText = '') {
  const label = button ? [...button.childNodes] : [];
  const disabled = button?.disabled;
  let slow = null;
  if (button) {
    button.disabled = true;
    button.classList.add('is-busy');
    button.replaceChildren(el('span', { class: 'spinner', 'aria-hidden': 'true' }), text);
    if (slowText) slow = setTimeout(() => { if (button.isConnected) button.lastChild.textContent = slowText; }, 4000);
  }
  try { return await fn(); } finally {
    clearTimeout(slow);
    if (button && button.isConnected) { button.disabled = disabled; button.classList.remove('is-busy'); button.replaceChildren(...label); }
  }
}

async function openApp(app, mode, button) {
  app = appById(app.id) || app;
  if (app.available === false) { toast(app.error || '这个工作台暂不可用，请在管理页检查登记。', true); return; }
  if (mode === 'tab' && openTabs.has(app.id)) { touchRecent(app.id); show(app.id); return; }
  await busy(button, '正在打开…', async () => {
    try {
      const result = await api('/api/open', { id: app.id, mode });
      touchRecent(app.id);
      if (mode === 'tab') openInTab(app, result.url);
      else toast(mode === 'window' ? `已在新窗口打开 ${app.name}` : `已启动 ${app.name}`);
      setTimeout(() => refreshStatus(true), 1500);
    } catch (err) {
      toast(`${app.name}：${err.message}`, true);
    }
  }, '还在启动，请稍候…');
}

// 打开一个入口的默认方式；已经在标签页里就直接切过去
function quickOpen(app, button) {
  if (openTabs.has(app.id)) { touchRecent(app.id); show(app.id); return; }
  return openApp(app, app.open[0], button);
}

// 关闭前要不要确认：服务类都确认（会连带关掉窗口和页签）；普通程序只有说明文件写了提示才确认
const needsConfirm = (app) => app.stop_type !== 'close' || Boolean(app.stop_confirm);
const confirmText = (app) => app.stop_confirm || '关闭它的服务；显示它的标签页和独立窗口会一并关掉。';

async function stopApp(app, button, force = false) {
  if (app.available === false) { toast(app.error || '这个工作台暂不可用。', true); return; }
  if (!force && needsConfirm(app) && !(await ask(`关闭 ${app.name}？`, confirmText(app), '关闭'))) return;
  let result;
  await busy(button, force ? '正在结束…' : '正在关闭…', async () => {
    try {
      result = await api('/api/stop', { id: app.id, force });
    } catch (err) {
      toast(`${app.name}：${err.message}`, true);
    }
  });
  if (!result) return;
  if (result.stopped) {
    closeTab(app.id);
    toast(result.windows_closed ? `${result.message}，并关掉了 ${result.windows_closed} 个窗口` : result.message);
  } else if (result.needs_force && !force) {
    if (await ask(`强制结束 ${app.name}？`, `${result.message}\n\n强制结束不会等它保存，未完成的任务会中断。`, '强制结束', true)) {
      return stopApp(app, button, true);
    }
  } else {
    toast(`${app.name}：${result.message}`, true);
  }
  await refreshStatus(true);
  if (view === 'manage') loadWindows();
}

async function restartApp(app, button) {
  if (app.available === false) { toast(app.error || '这个工作台暂不可用。', true); return; }
  if (needsConfirm(app) && !(await ask(`重启 ${app.name}？`, `${confirmText(app)}\n关闭后会马上重新启动。`, '重启'))) return;
  await busy(button, '正在重启…', async () => {
    try {
      const result = await api('/api/restart', { id: app.id });
      if (!result.restarted) { toast(`${app.name}：${result.message}`, true); return; }
      toast(result.message);
      const entry = openTabs.get(app.id);
      if (entry && result.url) loadFrame(entry, result.url);
    } catch (err) {
      toast(`${app.name}：${err.message}`, true);
    }
  }, '还在重启，请稍候…');
  await refreshStatus(true);
  if (view === 'manage') loadWindows();
}

// ---------------------------------------------------------------- 首页卡片
function stateNode(status, app = null) {
  if (app?.available === false) return el('span', { class: 'state unavailable', title: app.error || '' }, icon('alert'), '不可用');
  if (!status || status.running === null) return el('span', { class: 'state', 'data-status': '', title: '工作台不检测它的运行状态' }, '外部程序');
  return el('span', { class: `state ${status.running ? 'on' : ''}`, 'data-status': '' },
    el('span', { class: `status-dot ${status.running ? 'on' : ''}`, 'aria-hidden': 'true' }),
    status.running ? '运行中' : '未运行');
}

function appIcon(app, small = false) {
  const box = el('div', { class: `app-icon${small ? ' small' : ''}`, 'aria-hidden': 'true',
    style: app.accent ? `--card-accent:${app.accent}` : null });
  if (app.has_icon) {
    box.append(el('img', { src: tokenUrl('/api/icon', { id: app.id }), alt: '', loading: 'lazy',
      onerror: (e) => e.target.replaceWith(document.createTextNode(app.name.slice(0, 1))) }));
  } else {
    box.append(app.name.slice(0, 1));
  }
  return box;
}

function favButton(app) {
  const on = favorites.includes(app.id);
  return el('button', { type: 'button', class: 'icon-button fav', 'aria-pressed': String(on),
    'aria-label': `${on ? '取消常用' : '设为常用'}：${app.name}`, title: on ? '取消常用' : '设为常用（显示在首页）',
    onclick: () => toggleFavorite(app) }, icon('star'));
}

const canStop = (app) => app.available !== false && app.stoppable && app.status?.running !== false;

function cardActions(app) {
  const inTab = openTabs.has(app.id);
  const modes = inTab ? app.open.filter((m) => m !== 'tab') : app.open;
  return el('div', { class: 'actions', 'data-actions': '' },
    inTab ? el('button', { type: 'button', class: 'btn small primary', 'aria-label': `回到标签页：${app.name}`,
      onclick: () => quickOpen(app) }, icon('corner-left'), '回到标签页') : null,
    ...modes.map((mode, index) =>
      el('button', { type: 'button', class: `btn small ${index === 0 && !inTab ? 'primary' : 'ghost'}`,
        disabled: app.available === false, title: app.available === false ? app.error : MODE_HINT[mode],
        'aria-label': `${MODE_HINT[mode]}：${app.name}`, onclick: (e) => openApp(app, mode, e.currentTarget) },
      index === 0 && !inTab ? null : icon(mode === 'window' ? 'window' : 'play'), MODE_LABEL[mode])),
    canStop(app) && app.status?.running ? el('button', { type: 'button', class: 'icon-button stop danger-hover',
      'aria-label': `关闭：${app.name}`, title: '关闭它的服务', onclick: (e) => stopApp(app, e.currentTarget) }, icon('power')) : null);
}

function card(app) {
  const running = Boolean(app.status?.running);
  return el('article', { class: `app-card${running ? ' is-running' : ''}${app.available === false ? ' is-unavailable' : ''}`,
    'data-app': app.id, style: app.accent ? `--card-accent:${app.accent}` : null },
    el('div', { class: 'head' }, appIcon(app),
      el('div', { class: 'title' }, el('h3', { title: app.name }, app.name),
        el('div', { class: 'meta' }, stateNode(app.status, app),
          openTabs.has(app.id) ? el('span', { class: 'tag' }, '已在标签页') : null)),
      favButton(app)),
    el('p', { class: 'desc', title: app.description || '' }, app.description || '没有填写说明。'),
    app.available === false ? el('p', { class: 'note unavailable' }, app.error || '请在管理页检查工作台位置和说明文件。') : null,
    app.note ? el('p', { class: 'note' }, app.note) : null,
    cardActions(app));
}

async function toggleFavorite(app) {
  const before = favorites;
  const on = !favorites.includes(app.id);
  favorites = on ? [...favorites, app.id] : favorites.filter((id) => id !== app.id);
  const focusId = app.id;
  rerender(focusId);
  try {
    const result = await api('/api/favorites', { ids: favorites });
    favorites = result.favorites;
    toast(on ? `已设为常用：${app.name}，首页可以直接找到它` : `已取消常用：${app.name}`);
  } catch (err) {
    favorites = before;
    toast(`保存常用失败：${err.message}`, true);
  }
  rerender(focusId);
}

// 重新渲染后把焦点还给同一张卡片的星标；卡片已不在当前视图（常用里取消）时放到列表开头
function rerender(focusId) {
  const hadFocus = document.activeElement?.classList.contains('fav');
  renderNav();
  render();
  if (!hadFocus) return;
  const target = document.querySelector(`.app-card[data-app="${CSS.escape(focusId)}"] .fav`)
    || document.querySelector('#groups .app-card .fav');
  target?.focus();
}

const visibleApps = () => apps.filter((a) => !a.hidden);
const runningApps = () => visibleApps().filter((a) => a.status?.running);

function groupsFor(list) {
  return categories.map((c) => ({ ...c, slug: `cat:${c.name}`, items: list.filter((a) => a.category === c.name) }))
    .filter((g) => g.items.length);
}

function navLink(slug, iconName, label, count, countTitle = '') {
  const link = el('a', { class: 'nav-item', href: '#', 'data-slug': slug, title: label,
    'aria-current': String(current === 'home' && view === slug) },
    icon(iconName), el('span', { class: 'label' }, label),
    count === null ? null : el('span', { class: 'nav-count', title: countTitle || null }, String(count)));
  link.addEventListener('click', (e) => { e.preventDefault(); setView(slug); });
  return link;
}

function renderNav() {
  const shown = visibleApps();
  $('category-nav').replaceChildren(
    navLink('fav', 'home', '首页', null),
    navLink('all', 'grid', '全部', shown.length),
    ...groupsFor(shown).map((g) => navLink(g.slug, g.icon, g.name, g.items.length)));
  const running = apps.filter((a) => a.status?.running).length;
  const manage = navLink('manage', 'settings', '管理', running || null, `${running} 项在运行`);
  if (running) manage.querySelector('.nav-count').classList.add('live');
  $('manage-nav').replaceChildren(manage);
}

function favoriteApps() {
  return favorites.map((id) => appById(id)).filter((a) => a && !a.hidden);
}

function validView(slug) {
  return slug === 'fav' || slug === 'all' || slug === 'manage' || groupsFor(visibleApps()).some((g) => g.slug === slug);
}

function setView(slug) {
  view = validView(slug) ? slug : 'all';
  store.set(VIEW_KEY, view);
  show('home');
  render();
  playEnter();
  $('panel-home').scrollTo({ top: 0 });
  if (view === 'manage') { loadWindows(); loadRegistry(); }
}

// 切换视图时卡片轻轻浮上来；状态轮询引起的重画不播放
let enterTimer = null;
function playEnter() {
  const groups = $('groups');
  groups.classList.remove('entering');
  void groups.offsetHeight;
  groups.classList.add('entering');
  clearTimeout(enterTimer);
  enterTimer = setTimeout(() => groups.classList.remove('entering'), 700);
}

function setHeading(eyebrow, title, desc) {
  $('home-eyebrow').textContent = eyebrow;
  $('home-title').textContent = title;
  $('home-desc').textContent = desc;
}

function sectionHeading(id, iconName, title, extra = null, count = null) {
  return el('div', { class: 'section-heading' },
    el('h2', { id }, icon(iconName), title, count === null ? null : el('span', { class: 'count' }, `${count}`)), extra);
}

function groupSection(g) {
  const id = `h-${categories.indexOf(categories.find((c) => c.name === g.name))}`;
  return el('section', { class: 'group', 'aria-labelledby': id },
    sectionHeading(id, g.icon, g.name, null, g.items.length),
    el('div', { class: 'app-grid' }, ...g.items.map(card)));
}

// 首页问候语下的一句话概况
function heroSummary() {
  const shown = visibleApps();
  const running = runningApps().length;
  if (!apps.length) return '一切准备就绪，从添加第一个工作台开始吧。';
  const parts = [`${shown.length} 个入口`];
  parts.push(running ? `${running} 个正在运行` : '现在都没在运行');
  if (openTabs.size) parts.push(`${openTabs.size} 个已在标签页`);
  const late = new Date().getHours();
  return `${parts.join('，')}。${late >= 23 || late < 5 ? '别熬太晚，早点休息。' : ''}`;
}
function paintHero() {
  if (view !== 'fav') return;
  setHeading(dateLine(), `${greeting()}，${profile.name}`, heroSummary());
}

function runningSection() {
  const list = runningApps();
  if (!list.length) return null;
  return el('section', { class: 'group', 'aria-labelledby': 'h-running' },
    sectionHeading('h-running', 'activity', '正在运行', null, list.length),
    el('div', { class: 'chip-row' }, ...list.map((app) => el('div', { class: 'run-chip', style: app.accent ? `--card-accent:${app.accent}` : null },
      appIcon(app, true),
      el('div', { class: 'chip-text' }, el('strong', {}, app.name),
        el('small', {}, openTabs.has(app.id) ? '已在标签页' : '运行中')),
      app.open.length ? el('button', { type: 'button', class: 'btn small ghost', 'aria-label': `打开：${app.name}`,
        onclick: (e) => quickOpen(app, e.currentTarget) }, openTabs.has(app.id) ? '切换' : '打开') : null,
      canStop(app) ? el('button', { type: 'button', class: 'icon-button danger-hover', title: '关闭它的服务',
        'aria-label': `关闭：${app.name}`, onclick: (e) => stopApp(app, e.currentTarget) }, icon('power')) : null))));
}

function recentSection() {
  const list = recentApps().slice(0, 6);
  if (!list.length) return null;
  return el('section', { class: 'group', 'aria-labelledby': 'h-recent' },
    sectionHeading('h-recent', 'clock', '最近使用',
      el('button', { type: 'button', class: 'btn small subtle', title: '只清除这台电脑上的最近使用记录', onclick: clearRecent }, '清除记录')),
    el('div', { class: 'recent-row' }, ...list.map(({ app, t }) =>
      el('button', { type: 'button', class: 'recent-item', title: `${MODE_HINT[app.open[0]] || '打开'}：${app.name}`,
        disabled: app.available === false, style: app.accent ? `--card-accent:${app.accent}` : null,
        onclick: (e) => quickOpen(app, e.currentTarget) },
      appIcon(app, true),
      el('span', { class: 'recent-text' }, el('strong', {}, app.name), el('small', { 'data-ago': String(t) }, ago(t)))))));
}

function welcomeState() {
  return el('div', { class: 'welcome' },
    el('span', { class: 'welcome-icon', 'aria-hidden': 'true' }, icon('grid')),
    el('h2', {}, '这里还是空的'),
    el('p', { class: 'muted' }, '工作台不会自动带入任何东西。想用哪个，就把哪个加进来，一次一个也可以。'),
    el('ol', { class: 'steps' },
      el('li', {}, el('strong', {}, '导入一个工作台'),
        el('span', {}, '在“管理”里粘贴它的 workbench.json 路径，或旧的适配清单（apps 文件夹里的 .json）。')),
      el('li', {}, el('strong', {}, '或者让它自己找'),
        el('span', {}, '把存放工作台的文件夹填进“扫描目录”，带 workbench.json 的会自动登记。')),
      el('li', {}, el('strong', {}, '放到首页'), el('span', {}, '点卡片右上角的星标，常用的就会出现在这里。'))),
    el('button', { type: 'button', class: 'btn primary', onclick: () => setView('manage') }, icon('plus'), '添加第一个工作台'));
}

function hintCard(text, actionLabel, action) {
  return el('div', { class: 'hint-card' }, icon('star'), el('p', {}, text),
    actionLabel ? el('button', { type: 'button', class: 'btn small', onclick: action }, actionLabel, icon('arrow-right')) : null);
}

function homeView() {
  const list = favoriteApps();
  const shown = visibleApps();
  const content = [runningSection(), recentSection()];
  if (!apps.length) return [welcomeState()];
  if (list.length) {
    content.push(el('section', { class: 'group', 'aria-labelledby': 'h-fav' },
      sectionHeading('h-fav', 'star', '常用', shown.length > list.length
        ? el('button', { type: 'button', class: 'btn small subtle', onclick: () => setView('all') }, `查看全部 ${shown.length} 项`, icon('arrow-right'))
        : null, list.length),
      el('div', { class: 'app-grid' }, ...list.map(card))));
  } else if (shown.length) {
    // 还没设常用时，直接把已加入的入口摆在首页，一个个加进来马上就能看到
    const first = shown.slice(0, 8);
    content.push(el('section', { class: 'group', 'aria-labelledby': 'h-added' },
      sectionHeading('h-added', 'grid', '已加入',
        el('div', { class: 'row' },
          shown.length > first.length ? el('button', { type: 'button', class: 'btn small subtle', onclick: () => setView('all') },
            `查看全部 ${shown.length} 项`, icon('arrow-right')) : null,
          el('button', { type: 'button', class: 'btn small subtle', onclick: () => setView('manage') }, icon('plus'), '继续添加')),
        shown.length),
      el('p', { class: 'muted section-hint' }, '点卡片右上角的星标设为常用，首页就只显示常用的那些。'),
      el('div', { class: 'app-grid' }, ...first.map(card))));
  } else {
    content.push(hintCard('所有入口都已隐藏，可以在管理页恢复显示。', '打开管理', () => setView('manage')));
  }
  return content;
}

function render() {
  let content;
  const heroView = view === 'fav';
  $('page-heading').classList.toggle('hero', heroView);
  if (view === 'manage') {
    setHeading('控制', '管理', '扫描和登记本地工作台，管理入口、分类与独立窗口。');
    content = manageView(manageQuery.trim().toLowerCase());
  } else if (view === 'fav') {
    paintHero();
    content = homeView();
  } else if (view === 'all') {
    setHeading('全部入口', '全部', `共 ${visibleApps().length} 项，按分类排列。分类和隐藏可以在“管理”里调整。`);
    content = apps.length ? (visibleApps().length ? groupsFor(visibleApps()).map(groupSection)
      : [el('div', { class: 'empty-state' }, '所有入口都已隐藏，可以在管理页恢复显示。')]) : [welcomeState()];
  } else {
    const g = groupsFor(visibleApps()).find((x) => x.slug === view);
    setHeading('分类', g.name, `${g.items.length} 项`);
    content = [el('div', { class: 'app-grid' }, ...g.items.map(card))];
  }
  $('groups').replaceChildren(...content.filter(Boolean));
  if (current === 'home') {
    $('breadcrumbs').querySelector('strong').textContent = viewLabel();
  }
}

// ---------------------------------------------------------------- 快速打开（Ctrl+K）
let paletteItems = [];
let paletteIndex = 0;

function paletteCommands() {
  const cmds = [
    { title: '首页', sub: '跳转', iconName: 'home', run: () => setView('fav') },
    { title: '全部入口', sub: '跳转', iconName: 'grid', run: () => setView('all') },
    ...groupsFor(visibleApps()).map((g) => ({ title: g.name, sub: `分类 · ${g.items.length} 项`, iconName: g.icon, run: () => setView(g.slug) })),
    { title: '管理', sub: '扫描、登记、分类与窗口', iconName: 'settings', run: () => setView('manage') },
    { title: '扫描本地工作台', sub: '操作', iconName: 'refresh', run: () => scanWorkbenches() },
    { title: '刷新运行状态', sub: '操作', iconName: 'activity', run: async () => { await refreshStatus(true); toast('已刷新状态'); } },
    { title: `切换主题（现在：${THEME_LABEL[themeMode]}）`, sub: '操作', iconName: THEME_ICON[themeMode], run: cycleTheme },
    { title: sidebarCollapsed() ? '展开侧栏' : '收起侧栏', sub: '操作', iconName: 'sidebar', run: () => setSidebar(!sidebarCollapsed()) },
  ];
  if (openTabs.size) cmds.push({ title: `关闭所有标签页（${openTabs.size}）`, sub: '操作', iconName: 'close', run: () => { closeAllTabs(); toast('已关闭所有标签页'); } });
  if (recent.length) cmds.push({ title: '清除最近使用记录', sub: '操作', iconName: 'clock', run: clearRecent });
  return cmds.map((c) => ({ ...c, kind: 'cmd', text: `${c.title} ${c.sub}`.toLowerCase() }));
}

function appEntry(app, sub) {
  return { kind: 'app', app, title: app.name, sub: sub || app.category, run: () => quickOpen(app),
    text: `${app.name} ${app.description || ''} ${app.category} ${app.id}`.toLowerCase() };
}

function paletteEntries(raw) {
  const q = raw.trim().toLowerCase();
  const groups = [];
  if (!q) {
    const seen = new Set();
    const take = (list) => list.filter((a) => a && !seen.has(a.id) && seen.add(a.id));
    const tabs = take([...openTabs.values()].map((e) => appById(e.app.id) || e.app));
    if (tabs.length) groups.push(['已打开', tabs.map((a) => appEntry(a, '已在标签页 · 切换过去'))]);
    const rec = take(recentApps().map((x) => x.app)).slice(0, 5);
    if (rec.length) groups.push(['最近使用', rec.map((a) => appEntry(a, `${a.category} · ${ago(recent.find((r) => r.id === a.id).t)}`))]);
    const fav = take(favoriteApps());
    if (fav.length) groups.push(['常用', fav.map((a) => appEntry(a))]);
    const rest = take(visibleApps());
    if (rest.length) groups.push(['其他入口', rest.map((a) => appEntry(a))]);
    groups.push(['跳转与操作', paletteCommands()]);
  } else {
    const score = (item) => (item.title.toLowerCase().startsWith(q) ? 0 : item.title.toLowerCase().includes(q) ? 1 : 2);
    const found = apps.filter((a) => `${a.name} ${a.description || ''} ${a.category} ${a.id}`.toLowerCase().includes(q))
      .map((a) => appEntry(a, `${a.category}${a.hidden ? ' · 已隐藏' : ''}${openTabs.has(a.id) ? ' · 已在标签页' : ''}`))
      .sort((x, y) => score(x) - score(y));
    if (found.length) groups.push(['入口', found]);
    const cmds = paletteCommands().filter((c) => c.text.includes(q));
    if (cmds.length) groups.push(['跳转与操作', cmds]);
  }
  return groups;
}

function renderPalette() {
  const groups = paletteEntries($('palette-input').value);
  paletteItems = groups.flatMap(([, items]) => items);
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
  const list = $('palette-list');
  if (!paletteItems.length) {
    list.replaceChildren(el('li', { class: 'palette-empty', role: 'presentation' }, '没有找到匹配的入口或操作。'));
    $('palette-input').removeAttribute('aria-activedescendant');
    return;
  }
  let n = 0;
  list.replaceChildren(...groups.flatMap(([title, items]) => [
    el('li', { class: 'palette-group', role: 'presentation' }, title),
    ...items.map((item) => {
      const index = n++;
      const node = el('li', { role: 'option', id: `pal-${index}`, class: 'palette-item', 'aria-selected': String(index === paletteIndex),
        'data-index': String(index) },
        item.kind === 'app' ? appIcon(item.app, true) : el('span', { class: 'palette-icon' }, icon(item.iconName)),
        el('span', { class: 'palette-text' }, el('strong', {}, item.title), el('small', {}, item.sub)),
        item.kind === 'app' && item.app.status?.running ? el('span', { class: 'status-dot on', title: '运行中' }) : null,
        index === paletteIndex ? el('kbd', {}, 'Enter') : null);
      node.addEventListener('mousemove', () => { if (paletteIndex !== index) { paletteIndex = index; paintPaletteSelection(); } });
      node.addEventListener('click', () => runPalette(index));
      return node;
    })]));
  paintPaletteSelection(false);
}

function paintPaletteSelection(scroll = true) {
  for (const node of $('palette-list').querySelectorAll('.palette-item')) {
    const active = Number(node.dataset.index) === paletteIndex;
    node.setAttribute('aria-selected', String(active));
    node.querySelector('kbd')?.remove();
    if (active) {
      node.append(el('kbd', {}, 'Enter'));
      if (scroll) node.scrollIntoView({ block: 'nearest' });
    }
  }
  $('palette-input').setAttribute('aria-activedescendant', `pal-${paletteIndex}`);
}

function runPalette(index) {
  const item = paletteItems[index];
  if (!item) return;
  $('palette').close();
  item.run();
}

function openPalette() {
  if ($('palette').open) return;
  $('palette-input').value = '';
  paletteIndex = 0;
  renderPalette();
  $('palette').showModal();
  $('palette-input').focus();
}
function togglePalette() {
  if ($('palette').open) $('palette').close();
  else if (!$('confirm-dialog').open) openPalette();
}

$('palette-input').addEventListener('input', () => { paletteIndex = 0; renderPalette(); });
$('palette-input').addEventListener('keydown', (event) => {
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (step && paletteItems.length) {
    event.preventDefault();
    paletteIndex = (paletteIndex + step + paletteItems.length) % paletteItems.length;
    paintPaletteSelection();
  }
  if (event.key === 'Enter') { event.preventDefault(); runPalette(paletteIndex); }
});
// 点对话框外面的遮罩也能关掉
$('palette').addEventListener('click', (event) => { if (event.target === $('palette')) $('palette').close(); });
$('quick-open').addEventListener('click', openPalette);

// ---------------------------------------------------------------- 管理
let manageTimer = null;
function renderManageSoon() {
  clearTimeout(manageTimer);
  manageTimer = setTimeout(() => { if (view === 'manage' && current === 'home') renderKeepFocus(); }, 50);
}

// 重新渲染时尽量把焦点留在同一个控件上（按 data-focus 标记找回）
function renderKeepFocus() {
  const active = document.activeElement;
  const key = active?.dataset?.focus;
  const selection = typeof active?.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
  const inputScroll = active?.scrollTop || 0;
  const panelScroll = $('panel-home').scrollTop;
  render();
  if (key) {
    const target = document.querySelector(`[data-focus="${CSS.escape(key)}"]`);
    target?.focus({ preventScroll: true });
    if (selection && target?.setSelectionRange) target.setSelectionRange(...selection);
    if (target) target.scrollTop = inputScroll;
  }
  $('panel-home').scrollTop = panelScroll;
}

function windowsOf(appId) {
  return windows.filter((w) => w.app === appId);
}

// 本地登记表与用户尚未保存的输入分别保存，状态轮询不会覆盖草稿。
function acceptRegistry(data) {
  registryState = data;
  registryError = '';
  if (!rootsDirty) rootsDraft = (data.settings?.scan_roots || []).join('\n');
}

async function loadRegistry() {
  if (registryLoading) return registryLoading;
  if (registryBusy) return;
  registryLoading = (async () => {
    try { acceptRegistry(await api('/api/registry')); }
    catch (err) { registryError = err.message; }
    finally {
      registryLoading = null;
      if (view === 'manage' && current === 'home') renderKeepFocus();
    }
  })();
  return registryLoading;
}

function registrySection() {
  const disabled = registryBusy || !registryState;
  const roots = el('textarea', { id: 'registry-roots', rows: 3, spellcheck: 'false', disabled,
    placeholder: '例如：D:\\Workbenches',
    'data-focus': 'registry-roots', 'aria-describedby': 'registry-roots-note',
    oninput: (event) => {
      rootsDraft = event.target.value;
      rootsDirty = rootsDraft !== (registryState?.settings?.scan_roots || []).join('\n');
      $('registry-roots-note').textContent = rootsDirty ? '目录修改尚未保存。' : '每行一个目录，扫描目录本身及其下一层文件夹。';
      $('registry-roots-note').classList.toggle('dirty', rootsDirty);
    } });
  roots.value = rootsDraft;
  const rootsForm = el('form', { class: 'registry-form', onsubmit: (event) => { event.preventDefault(); saveScanRoots(); } },
    el('div', { class: 'form-title' }, el('span', { class: 'form-icon' }, icon('folder')),
      el('div', {}, el('label', { for: 'registry-roots' }, '扫描目录'), el('small', {}, '工作台会在这些文件夹里查找'))),
    roots,
    el('p', { id: 'registry-roots-note', class: `muted small-note${rootsDirty ? ' dirty' : ''}` },
      rootsDirty ? '目录修改尚未保存。' : '每行一个目录，扫描目录本身及其下一层文件夹。'),
    el('div', { class: 'row' }, el('button', { type: 'submit', class: 'btn small primary', disabled, 'data-focus': 'save-roots' },
      '保存并扫描')));
  const manifest = el('input', { id: 'registry-manifest', type: 'text', value: manifestDraft,
    placeholder: '工作台目录 / workbench.json', spellcheck: 'false', disabled,
    'data-focus': 'registry-manifest', oninput: (event) => { manifestDraft = event.target.value; } });
  const importForm = el('form', { class: 'registry-form', onsubmit: (event) => { event.preventDefault(); importManifest(); } },
    el('div', { class: 'form-title' }, el('span', { class: 'form-icon' }, icon('file')),
      el('div', {}, el('label', { for: 'registry-manifest' }, '导入说明文件'), el('small', {}, '登记扫描目录以外的单个工作台'))),
    manifest,
    el('p', { class: 'muted small-note' }, '粘贴 workbench.json 的完整路径。'),
    el('label', { class: 'registry-replace' },
      el('input', { type: 'checkbox', checked: replaceImport, disabled, 'data-focus': 'replace-import',
        onchange: (event) => { replaceImport = event.target.checked; } }), '替换同标识的已有入口'),
    el('div', { class: 'row' }, el('button', { type: 'submit', class: 'btn small', disabled, 'data-focus': 'import-manifest' },
      icon('plus'), '登记工作台')));

  const issues = (registryState?.errors || []).map((item) => el('div', { class: 'notice registry-issue' },
    el('strong', {}, item.error || '无法读取说明文件'),
    item.source ? el('span', { class: 'registry-path' }, item.source) : null));
  const conflicts = (registryState?.conflicts || []).map((item) => {
    const chosen = item.selected || registryState.entries?.find((entry) => entry.id === item.id)?.source;
    const samePath = (source) => chosen && source.replaceAll('\\', '/').toLowerCase() === chosen.replaceAll('\\', '/').toLowerCase();
    return el('div', { class: 'registry-conflict' },
      el('strong', {}, `发现重复标识：${item.id}`),
      el('p', { class: 'muted small-note' }, '选择要使用的位置；其他目录和数据会保留。'),
      ...item.sources.map((source) => el('div', { class: 'registry-source' },
        el('span', { class: 'registry-path' }, source),
        el('button', { type: 'button', class: 'btn small', disabled: registryBusy || samePath(source),
          'aria-label': `使用此位置：${source}`, onclick: () => chooseManifest(item.id, source) },
        samePath(source) ? '当前使用' : '使用此位置'))));
  });
  const excluded = registryState?.excluded || [];
  return el('section', { class: 'group registry-section', 'aria-labelledby': 'm-registry' },
    sectionHeading('m-registry', 'folder', '本地工作台',
      el('button', { type: 'button', class: 'btn small', disabled, 'data-focus': 'scan-registry',
        onclick: () => scanWorkbenches() }, icon('refresh'), registryBusy ? '正在处理…' : '扫描已保存目录')),
    el('p', { class: 'muted registry-intro' }, '登记后会出现在下方入口列表，点击打开时才启动。'),
    registryError ? el('div', { class: 'notice', role: 'alert' }, `读取登记信息失败：${registryError} `,
      el('button', { type: 'button', class: 'btn small', disabled: registryBusy, onclick: () => loadRegistry() }, '重试')) : null,
    !registryState && !registryError ? el('p', { class: 'muted' }, '正在读取登记信息…') : null,
    el('div', { class: 'registry-forms' }, rootsForm, importForm),
    ...conflicts,
    ...issues,
    excluded.length ? el('details', { class: 'registry-excluded' },
      el('summary', {}, `已取消登记的位置（${excluded.length}）`),
      el('p', { class: 'muted small-note' }, '这些位置不会被自动重新登记。'),
      ...excluded.map((source) => el('div', { class: 'registry-source' }, el('span', { class: 'registry-path' }, source),
        el('button', { type: 'button', class: 'btn small', disabled: registryBusy,
          'aria-label': `重新登记：${source}`, onclick: () => registerManifest(source) }, '重新登记')))) : null);
}

async function registryAction(path, body, message, afterSave) {
  if (registryBusy) return false;
  registryBusy = true;
  renderKeepFocus();
  try {
    // 等待旧的读取完成，避免它随后覆盖刚保存的登记结果。
    if (registryLoading) await registryLoading;
    const before = new Set(apps.map((a) => a.id));
    const data = await api(path, body);
    afterSave?.();
    acceptRegistry(data);
    await loadApps();
    const added = apps.filter((a) => !before.has(a.id)).length;
    if (path === '/api/registry/scan' && !added) {
      const roots = data.settings?.scan_roots || [];
      toast(roots.length ? `没有发现新的工作台。扫描的目录：${roots.join('；')}` : '还没有设置扫描目录，请在“管理”里添加。');
    } else {
      toast(added && path !== '/api/registry/unregister' ? `${message}，新增 ${added} 个入口` : message);
    }
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  } finally {
    registryBusy = false;
    if (current === 'home') renderKeepFocus();
  }
}

function scanWorkbenches() {
  return registryAction('/api/registry/scan', {}, '已扫描本地工作台');
}

function saveScanRoots() {
  const scanRoots = rootsDraft.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return registryAction('/api/registry/settings', { scan_roots: [...new Set(scanRoots)] }, '扫描目录已保存并刷新',
    () => { rootsDirty = false; });
}

function registerManifest(source, replace = false, afterSave) {
  return registryAction('/api/registry/register', { manifest_path: source, replace }, '工作台已登记', afterSave);
}

async function importManifest() {
  const source = manifestDraft.trim();
  if (!source) { $('registry-manifest')?.focus(); toast('请填写 workbench.json 的完整路径。', true); return; }
  if (replaceImport && !(await ask('改用这个工作台位置？', `${source}\n\n如果说明文件的标识已经登记，将改用此位置。原目录和数据会保留。`, '登记并替换'))) return;
  return registerManifest(source, replaceImport, () => { manifestDraft = ''; replaceImport = false; });
}

async function chooseManifest(id, source) {
  if (!(await ask(`使用这份工作台「${id}」？`, `${source}\n\n将把该标识的入口改为此位置。原目录和数据会保留。`, '使用此位置'))) return;
  return registerManifest(source, true);
}

async function unregisterApp(app) {
  if (!(await ask(`取消登记 ${app.name}？`, '移除工作台里的入口，并在后续扫描时跳过此工作台已知的位置。工作台文件和数据会保留；已运行的程序不会停止。', '取消登记'))) return;
  return registryAction('/api/registry/unregister', { id: app.id }, '已取消登记', () => closeTab(app.id));
}

async function revealApp(app) {
  try { await api('/api/reveal', { id: app.id }); }
  catch (err) { toast(`无法打开所在位置：${err.message}`, true); }
}

function manageView(query) {
  const counts = { all: apps.length, running: apps.filter((a) => a.status?.running).length, hidden: apps.filter((a) => a.hidden).length };
  const filters = el('div', { class: 'segmented', role: 'group', 'aria-label': '筛选' },
    ...[['all', '全部'], ['running', '运行中'], ['hidden', '已隐藏']].map(([key, label]) =>
      el('button', { type: 'button', class: 'btn small', 'aria-pressed': String(manageFilter === key), 'data-focus': `filter-${key}`,
        onclick: () => { manageFilter = key; renderKeepFocus(); } }, label, el('span', { class: 'seg-count' }, String(counts[key])))));
  const search = el('label', { class: 'search-field manage-search' }, icon('search'),
    el('input', { type: 'search', placeholder: '按名称、说明或分类筛选', 'aria-label': '筛选入口', value: manageQuery,
      'data-focus': 'manage-filter',
      oninput: (e) => { manageQuery = e.target.value; renderKeepFocus(); },
      onkeydown: (e) => { if (e.key === 'Escape' && manageQuery) { e.preventDefault(); manageQuery = ''; renderKeepFocus(); } } }));
  const tools = el('div', { class: 'row' },
    el('button', { type: 'button', class: 'btn small subtle', disabled: openTabs.size === 0, 'data-focus': 'close-tabs',
      onclick: () => { closeAllTabs(); toast('已关闭所有标签页'); } }, icon('close'), `关闭所有标签页（${openTabs.size}）`),
    el('button', { type: 'button', class: 'btn small subtle', 'data-focus': 'refresh-manage',
      onclick: async () => { await refreshStatus(true); await loadWindows(); toast('已刷新状态'); } }, icon('refresh'), '刷新状态'));

  let list = apps;
  if (manageFilter === 'running') list = list.filter((a) => a.status?.running);
  if (manageFilter === 'hidden') list = list.filter((a) => a.hidden);
  if (query) list = list.filter((a) => `${a.name} ${a.description} ${a.category}`.toLowerCase().includes(query));

  const emptyText = query ? `没有和“${manageQuery.trim()}”匹配的入口。`
    : manageFilter === 'running' ? '现在没有在运行的工作台。'
      : manageFilter === 'hidden' ? '没有隐藏的入口。' : '还没有登记任何入口，先在上面添加扫描目录吧。';
  const table = list.length ? el('table', { class: 'table manage-table' },
    el('caption', { class: 'sr-only' }, '各入口的状态、分类和操作'),
    el('thead', {}, el('tr', {}, ...['入口', '状态', '分类', '首页显示', '操作'].map((h) => el('th', { scope: 'col' }, h)))),
    el('tbody', {}, ...list.map(manageRow)))
    : el('div', { class: 'empty-state compact' }, emptyText);

  return [
    registrySection(),
    el('section', { class: 'group', 'aria-labelledby': 'm-apps' },
      sectionHeading('m-apps', 'grid', '入口', tools, apps.length),
      el('div', { class: 'manage-toolbar' }, filters, search),
      el('div', { class: 'table-wrap' }, table)),
    categorySection(),
    windowSection(),
  ];
}

function manageRow(app) {
  const own = windowsOf(app.id);
  const registration = registryState?.entries?.find((entry) => entry.id === app.id);
  const select = el('select', { 'aria-label': `分类：${app.name}`, 'data-focus': `cat-${app.id}`,
    onchange: (e) => moveApp(app, e.target.value) },
    ...categories.map((c) => el('option', { value: c.name, selected: c.name === app.category }, c.name)));
  const actions = el('div', { class: 'row tight' },
    el('button', { type: 'button', class: 'btn small', 'data-focus': `open-${app.id}`, 'aria-label': `${MODE_HINT[app.open[0]]}：${app.name}`,
      disabled: app.available === false, title: app.available === false ? app.error : MODE_HINT[app.open[0]],
      onclick: (e) => quickOpen(app, e.currentTarget) }, openTabs.has(app.id) ? '切换' : MODE_LABEL[app.open[0]]),
    canStop(app) ? el('button', { type: 'button', class: 'btn small', 'data-focus': `stop-${app.id}`, 'aria-label': `关闭：${app.name}`,
      onclick: (e) => stopApp(app, e.currentTarget) }, icon('power'), '关闭') : null,
    canStop(app) && app.launchable && app.status?.running ? el('button', { type: 'button', class: 'btn small subtle',
      'data-focus': `restart-${app.id}`, 'aria-label': `重启：${app.name}`, onclick: (e) => restartApp(app, e.currentTarget) }, icon('refresh'), '重启') : null,
    el('button', { type: 'button', class: 'icon-button', title: '在资源管理器中显示说明文件',
      'aria-label': `打开所在位置：${app.name}`, onclick: () => revealApp(app) }, icon('folder')),
    el('button', { type: 'button', class: 'btn small subtle danger-hover', disabled: registryBusy,
      'data-focus': `unregister-${app.id}`, 'aria-label': `取消登记：${app.name}`, onclick: () => unregisterApp(app) }, '取消登记'));
  return el('tr', { 'data-app': app.id, class: app.hidden ? 'is-hidden' : null },
    el('th', { scope: 'row' }, el('div', { class: 'entry' }, appIcon(app, true),
      el('div', {}, el('strong', {}, app.name),
        el('small', {}, app.version ? `v${app.version}` : (app.legacy ? '旧版说明文件' : '')),
        registration?.source ? el('small', { class: 'registry-path', title: registration.source }, registration.source) : null,
        app.error ? el('small', { class: 'unavailable' }, app.error) : null,
        app.category !== app.default_category ? el('small', {}, `原分类：${app.default_category}`) : null))),
    el('td', {}, el('div', { class: 'cell-stack' }, stateNode(app.status, app),
      own.length ? el('span', { class: 'badge' }, icon('window'), `${own.length} 个窗口`) : null,
      openTabs.has(app.id) ? el('span', { class: 'badge' }, '已在标签页') : null)),
    el('td', {}, select),
    el('td', {}, el('button', { type: 'button', class: `switch${app.hidden ? '' : ' on'}`, role: 'switch', 'aria-checked': String(!app.hidden),
      'data-focus': `hide-${app.id}`, title: app.hidden ? '已隐藏，点击恢复显示' : '显示中，点击在首页隐藏',
      'aria-label': `在首页显示：${app.name}`, onclick: () => setHidden(app, !app.hidden) },
      el('span', { class: 'switch-track', 'aria-hidden': 'true' }, el('span', { class: 'switch-thumb' })),
      el('span', { class: 'switch-label' }, app.hidden ? '隐藏' : '显示'))),
    el('td', {}, actions));
}

function categorySection() {
  const counts = new Map(categories.map((c) => [c.name, apps.filter((a) => a.category === c.name).length]));
  const rows = categories.map((c, index) => {
    const iconSelect = el('select', { 'aria-label': `图标：${c.name}`, 'data-focus': `icon-${c.name}`,
      onchange: (e) => saveCategories(categories.map((x) => (x.name === c.name ? { ...x, icon: e.target.value } : x))) },
      ...iconNames.map((n) => el('option', { value: n, selected: n === c.icon }, ICON_LABEL[n] || n)));
    const name = el('input', { type: 'text', value: c.name, maxlength: '20', 'aria-label': `分类名：${c.name}`, 'data-focus': `name-${c.name}`,
      title: '改名后按 Enter 保存，Esc 放弃',
      onchange: (e) => renameCategory(c.name, e.target.value, e.target),
      onkeydown: (e) => { if (e.key === 'Enter') e.target.blur(); if (e.key === 'Escape') { e.target.value = c.name; e.target.blur(); } } });
    return el('li', {},
      el('span', { class: 'cat-icon' }, icon(c.icon)), name, iconSelect,
      el('span', { class: 'muted cat-count' }, `${counts.get(c.name) || 0} 项`),
      el('button', { type: 'button', class: 'icon-button', disabled: index === 0, 'aria-label': `上移：${c.name}`, title: '上移',
        'data-focus': `up-${c.name}`, onclick: () => moveCategory(index, -1) }, icon('arrow-up')),
      el('button', { type: 'button', class: 'icon-button', disabled: index === categories.length - 1, 'aria-label': `下移：${c.name}`, title: '下移',
        'data-focus': `down-${c.name}`, onclick: () => moveCategory(index, 1) }, icon('arrow-down')),
      el('button', { type: 'button', class: 'icon-button danger-hover', 'aria-label': `删除分类：${c.name}`, title: '删除分类',
        onclick: () => deleteCategory(c.name) }, icon('trash')));
  });
  const input = el('input', { type: 'text', id: 'new-category', maxlength: '20', placeholder: '新分类名，按 Enter 添加', 'aria-label': '新分类名',
    value: categoryDraft, 'data-focus': 'new-category', oninput: (event) => { categoryDraft = event.target.value; },
    onkeydown: (e) => { if (e.key === 'Enter') addCategory(); } });
  return el('section', { class: 'group', 'aria-labelledby': 'm-cats' },
    sectionHeading('m-cats', 'book', '分类',
      el('span', { class: 'muted small-note' }, '顺序就是侧栏顺序。只改这里的显示，不改各工作台的说明文件。')),
    el('ul', { class: 'list category-list' }, ...rows),
    el('div', { class: 'row add-row' }, input,
      el('button', { type: 'button', class: 'btn small', onclick: addCategory }, icon('plus'), '新增分类')));
}

function windowSection() {
  const rows = windows.map((w) => {
    const app = appById(w.app);
    return el('li', {},
      el('span', { class: 'cat-icon' }, icon('window')),
      el('div', { class: 'grow' }, el('strong', {}, w.title || w.id || '窗口'),
        el('small', { class: 'muted' }, ` ${app ? app.name : '未登记'} · 端口 ${w.port ?? '?'} · PID ${w.pid}`)),
      el('button', { type: 'button', class: 'btn small', 'aria-label': `关闭窗口：${w.title || w.pid}`,
        onclick: (e) => closeWindow(w, e.currentTarget) }, icon('close'), '关闭窗口'));
  });
  return el('section', { class: 'group', 'aria-labelledby': 'm-wins' },
    sectionHeading('m-wins', 'window', '独立窗口',
      el('span', { class: 'muted small-note' }, '用共用窗口程序打开的窗口（不含各工作台自带的窗口）。')),
    windowsLoaded
      ? (rows.length ? el('ul', { class: 'list' }, ...rows) : el('div', { class: 'empty-state compact' }, '没有打开的独立窗口。'))
      : el('div', { class: 'empty-state compact' }, '正在读取…'));
}

async function loadWindows() {
  try {
    windows = (await api('/api/windows')).windows;
  } catch (err) {
    windows = [];
    toast(`读取窗口列表失败：${err.message}`, true);
  }
  windowsLoaded = true;
  if (view === 'manage' && current === 'home') renderKeepFocus();
}

async function closeWindow(w, button) {
  await busy(button, '正在关闭…', async () => {
    try {
      const result = await api('/api/windows/close', { pid: w.pid });
      if (result.closed) toast(`已关闭窗口：${w.title || w.pid}`);
      else toast(result.message, true);
    } catch (err) {
      toast(err.message, true);
    }
  });
  await loadWindows();
}

// 分类与隐藏：整体保存，服务端去掉和说明文件相同的部分
const overrides = () => Object.fromEntries(apps.map((a) => [a.id, { category: a.category, hidden: Boolean(a.hidden) }]));

async function saveLayout(nextCategories, nextOverrides, message) {
  try {
    const result = await api('/api/layout', {
      categories: nextCategories.map(({ name, icon: i }) => ({ name, icon: i })), apps: nextOverrides });
    categories = result.categories;
    for (const app of apps) Object.assign(app, result.apps[app.id] || {});
    if (message) toast(message);
  } catch (err) {
    toast(`保存失败：${err.message}`, true);
  }
  renderNav();
  renderKeepFocus();
}

function saveCategories(next, message) {
  return saveLayout(next, overrides(), message);
}

function moveApp(app, category) {
  const next = overrides();
  next[app.id].category = category;
  return saveLayout(categories, next, `${app.name} 已移到「${category}」`);
}

function setHidden(app, hidden) {
  const next = overrides();
  next[app.id].hidden = hidden;
  return saveLayout(categories, next, hidden ? `首页不再显示 ${app.name}（在“管理”里还能找到）` : `首页恢复显示 ${app.name}`);
}

function cleanName(value) {
  return value.trim().replace(/\s+/g, ' ');
}

function renameCategory(from, value, input) {
  const to = cleanName(value);
  if (!to || to === from) { input.value = from; return; }
  if (categories.some((c) => c.name.toLowerCase() === to.toLowerCase() && c.name !== from)) {
    toast(`已有分类「${to}」`, true); input.value = from; return;
  }
  const next = overrides();
  for (const app of apps) if (app.category === from) next[app.id].category = to;
  input.dataset.focus = `name-${to}`;
  if (view === `cat:${from}`) view = `cat:${to}`;
  return saveLayout(categories.map((c) => (c.name === from ? { ...c, name: to } : c)), next, `已改名为「${to}」`);
}

function moveCategory(index, step) {
  const next = [...categories];
  const [item] = next.splice(index, 1);
  next.splice(index + step, 0, item);
  return saveCategories(next);
}

function addCategory() {
  const input = $('new-category');
  const name = cleanName(input.value);
  if (!name) { input.focus(); return; }
  if (categories.some((c) => c.name.toLowerCase() === name.toLowerCase())) { toast(`已有分类「${name}」`, true); return; }
  categoryDraft = '';
  return saveCategories([...categories, { name, icon: 'grid' }], `已新增分类「${name}」，在入口的分类下拉里就能选它`);
}

async function deleteCategory(name) {
  const inside = apps.filter((a) => a.category === name);
  let next = categories.filter((c) => c.name !== name);
  const target = name === FALLBACK_CATEGORY ? next[0]?.name : FALLBACK_CATEGORY;
  if (inside.length) {
    if (!target) { toast('至少要留一个分类', true); return; }
    if (!(await ask(`删除分类「${name}」？`, `其中 ${inside.length} 项会移到「${target}」。`, '删除', true))) return;
    if (!next.some((c) => c.name === target)) next = [...next, { name: target, icon: 'grid' }];
  }
  const moved = overrides();
  for (const app of inside) moved[app.id].category = target;
  if (view === `cat:${name}`) view = 'manage';
  return saveLayout(next, moved, `已删除分类「${name}」`);
}

// ---------------------------------------------------------------- 名字与头像（连点头像五下，或右键头像）
const DEFAULT_NAME = '风子';
let profile = { name: DEFAULT_NAME, custom_avatar: false, avatar_version: 0 };
const hubName = () => `${profile.name}工作台`;

function applyProfile(next) {
  profile = { ...profile, ...next };
  const src = profile.custom_avatar ? tokenUrl('/api/profile/avatar', { v: profile.avatar_version }) : '/_shared/assets/fuuko-avatar.png';
  $('brand-avatar').src = src;
  $('favicon').href = profile.custom_avatar ? src : '/_shared/assets/fuuko-round.png';
  $('brand-name').textContent = hubName();
  if (appsLoaded) { render(); show(current); }
}

async function loadProfile() {
  try { applyProfile(await api('/api/profile')); } catch { /* 读取失败时保持默认，不影响其他功能 */ }
}

async function saveProfile(body, message) {
  const result = await api('/api/profile', body);
  applyProfile(result);
  toast(message);
}

function openProfileMenu(anchor) {
  const menu = $('profile-menu');
  menu.querySelector('[data-profile="reset-avatar"]').hidden = !profile.custom_avatar;
  menu.querySelector('[data-profile="reset-name"]').hidden = profile.name === DEFAULT_NAME;
  menu.querySelector('.menu-sep').hidden = !profile.custom_avatar && profile.name === DEFAULT_NAME;
  const box = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, box.left)}px`;
  menu.style.top = `${box.bottom + 8}px`;
  menu.hidden = false;
  menu.querySelector('button:not([hidden])').focus();
}
const closeProfileMenu = () => { $('profile-menu').hidden = true; };

// 小对话框：onOk 抛错时把错误显示在框里并保持打开
function profileDialog(title, body, okLabel, onOk) {
  const dialog = $('profile-dialog');
  $('profile-title').textContent = title;
  $('profile-body').replaceChildren(body);
  $('profile-error').textContent = '';
  $('profile-ok').textContent = okLabel;
  dialog.returnValue = '';
  dialog.showModal();
  const input = body.querySelector('input');
  if (input) { input.focus(); input.select(); }
  $('profile-form').onsubmit = async (event) => {
    if (event.submitter?.value !== 'ok') return;
    event.preventDefault();
    $('profile-ok').disabled = true;
    try {
      await onOk();
      dialog.close('ok');
    } catch (err) {
      $('profile-error').textContent = err.message;
    } finally {
      $('profile-ok').disabled = false;
    }
  };
}

function renameDialog() {
  const input = el('input', { type: 'text', maxlength: '12', value: profile.name, 'aria-label': '名字', spellcheck: 'false' });
  const preview = el('span', { class: 'muted' }, `侧栏显示为“${profile.name}工作台”`);
  input.addEventListener('input', () => { preview.textContent = `侧栏显示为“${input.value.trim() || '…'}工作台”`; });
  profileDialog('修改名字', el('div', { class: 'profile-field' },
    el('label', {}, '名字', input), preview,
    el('small', { class: 'muted' }, '会用在侧栏、问候语和窗口标题里，最多 12 个字。')), '保存', async () => {
    const name = input.value.trim().replace(/\s+/g, ' ');
    if (!name) throw new Error('名字不能为空');
    if (name !== profile.name) await saveProfile({ name }, `已改名为“${name}”`);
  });
}

// 选好图片后：取中间的正方形，缩到 512px，先给预览再保存
async function avatarDialog(file) {
  if (!file.type.startsWith('image/')) { toast('请选择图片文件', true); return; }
  if (file.size > 30 << 20) { toast('图片太大了，请换一张 30 MB 以内的', true); return; }
  const url = URL.createObjectURL(file);
  let image;
  try {
    image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('无法读取这张图片'));
      img.src = url;
    });
  } catch (err) { URL.revokeObjectURL(url); toast(err.message, true); return; }
  const size = Math.min(image.naturalWidth, image.naturalHeight);
  const canvas = el('canvas', { width: 512, height: 512, class: 'avatar-preview', 'aria-label': '头像预览' });
  canvas.getContext('2d').drawImage(image, (image.naturalWidth - size) / 2, (image.naturalHeight - size) / 2, size, size, 0, 0, 512, 512);
  URL.revokeObjectURL(url);
  profileDialog('更换头像', el('div', { class: 'profile-avatar' }, canvas,
    el('p', { class: 'muted' }, '取图片中间的正方形，显示时裁成圆形。')), '使用这张', async () => {
    await saveProfile({ avatar: canvas.toDataURL('image/png') }, '头像已更换');
  });
}

function setupProfile() {
  const brand = document.querySelector('.brand');
  let clicks = 0;
  let timer = null;
  brand.addEventListener('click', (e) => {
    e.preventDefault();
    clicks += 1;
    clearTimeout(timer);
    timer = setTimeout(() => { clicks = 0; }, 1200);
    if (clicks >= 5) { clicks = 0; openProfileMenu(brand); return; }
    if (clicks === 1) setView('fav');
  });
  brand.addEventListener('contextmenu', (e) => { e.preventDefault(); openProfileMenu(brand); });
  const menu = $('profile-menu');
  menu.addEventListener('click', async (e) => {
    const item = e.target.closest('[data-profile]');
    if (!item) return;
    closeProfileMenu();
    const action = item.dataset.profile;
    try {
      if (action === 'rename') renameDialog();
      if (action === 'avatar') { $('avatar-file').value = ''; $('avatar-file').click(); }
      if (action === 'reset-avatar') await saveProfile({ reset_avatar: true }, '已恢复默认头像');
      if (action === 'reset-name') await saveProfile({ name: null }, `已恢复默认名字“${DEFAULT_NAME}”`);
    } catch (err) { toast(err.message, true); }
  });
  menu.addEventListener('keydown', (e) => {
    const items = [...menu.querySelectorAll('button:not([hidden])')];
    const index = items.indexOf(document.activeElement);
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step) { e.preventDefault(); items[(index + step + items.length) % items.length].focus(); }
    if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closeProfileMenu(); brand.focus(); }
  });
  document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !menu.contains(e.target) && !brand.contains(e.target)) closeProfileMenu(); });
  window.addEventListener('blur', closeProfileMenu);
  $('avatar-file').addEventListener('change', (e) => { const file = e.target.files[0]; if (file) avatarDialog(file); });
  loadProfile();
}

// ---------------------------------------------------------------- 加载与状态
async function loadApps() {
  const data = await api('/api/apps');
  apps = data.apps;
  favorites = data.favorites || [];
  categories = data.categories || [];
  iconNames = data.icons || iconNames;
  if (!view || !validView(view)) {
    const saved = store.get(VIEW_KEY);
    view = saved && validView(saved) ? saved : 'fav';
  }
  $('errors').replaceChildren(...(data.errors || []).map((e) =>
    el('div', { class: 'notice' }, icon('alert'), el('span', {}, el('strong', {}, '说明文件有误：'), ` ${e.source}：${e.error}`))));
  const first = !appsLoaded;
  appsLoaded = true;
  renderNav();
  render();
  if (first) playEnter();
  show(current);
  setService(true);
  if (view === 'manage') { loadWindows(); if (!registryBusy) loadRegistry(); }
}

function setService(isOnline) {
  const wasOffline = !online;
  online = isOnline;
  $('service-dot').className = `status-dot ${isOnline ? 'on' : 'offline'}`;
  $('service-text').textContent = isOnline ? '服务在线' : '服务未响应';
  $('offline-banner').hidden = isOnline;
  if (isOnline && wasOffline) toast('已重新连上本机服务');
}

// 状态有变化时重画（卡片上的“关闭”按钮、管理页的操作按钮都跟着运行状态变）
async function refreshStatus(force = false) {
  if (!appsLoaded) {
    try { await loadApps(); } catch { setService(false); }
    return;
  }
  try {
    const { status, availability = {} } = await api('/api/status');
    setService(true);
    let changed = force;
    for (const app of apps) {
      const next = status[app.id];
      const available = availability[app.id];
      if (available && (available.available !== app.available || available.error !== app.error)) changed = true;
      if (available) Object.assign(app, available);
      if (next && (next.running !== app.status?.running || next.url !== app.status?.url)) changed = true;
      if (next) app.status = next;
    }
    if (!changed) return;
    renderNav();
    if (current === 'home') renderKeepFocus();
    else show(current);
  } catch { setService(false); }
}

// 在线时每 15 秒刷新一次；断线时每 4 秒重试，连上后马上恢复
function scheduleStatus() {
  setTimeout(async () => {
    if (!document.hidden && !$('confirm-dialog').open && !$('palette').open) await refreshStatus();
    scheduleStatus();
  }, online ? 15000 : 4000);
}

// 问候语和“几分钟前”每分钟更新一次，只改文字，不打断正在进行的操作
setInterval(() => {
  if (current !== 'home' || view !== 'fav') return;
  paintHero();
  for (const node of document.querySelectorAll('[data-ago]')) node.textContent = ago(Number(node.dataset.ago));
}, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus(); });

$('offline-retry').addEventListener('click', (e) => busy(e.currentTarget, '正在重试…', () => refreshStatus(true)));
setupProfile();
$('refresh').addEventListener('click', (e) => busy(e.currentTarget, '正在扫描…', () => scanWorkbenches()));
show('home');
scheduleStatus();
loadApps().catch((err) => { setService(false); toast(`读取应用列表失败：${err.message}`, true); });
