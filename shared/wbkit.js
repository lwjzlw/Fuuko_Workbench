// 风子工作台共用前端小助手：令牌请求、任务轮询、页签、提示。
// 页面里放 <meta name="wb-token" content="{{WB_TOKEN}}">，服务端渲染时替换成本次令牌。
const token = document.querySelector('meta[name="wb-token"]')?.content || '';

// 心跳：让服务知道窗口还开着（关窗一段时间后服务自行退出）
setInterval(() => { fetch('/api/health', { cache: 'no-store' }).catch(() => {}); }, 60000);

export async function api(path, body) {
  const options = { headers: { 'X-WB-Token': token } };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ ok: false, error: `HTTP ${response.status}` }));
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// 带令牌的 GET 地址（用于 <audio src> 这类不能加请求头的地方）
export function tokenUrl(path, params = {}) {
  const query = new URLSearchParams({ ...params, token });
  return `${path}?${query}`;
}

export async function pick(kind = 'file', options = {}) {
  const { paths } = await api('/api/pick', { kind, ...options });
  return paths;
}

// 轮询任务，onUpdate(snapshot) 每次刷新都调用；返回最终快照
export async function watchJob(id, onUpdate, interval = 700) {
  for (;;) {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    onUpdate?.(job);
    if (job.state !== 'running') return job;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

let toastTimer;
export function toast(message, isError = false) {
  let node = document.getElementById('wb-toast');
  if (!node) {
    node = document.createElement('div');
    node.id = 'wb-toast';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    document.body.append(node);
  }
  node.className = isError ? 'toast error' : 'toast';
  node.textContent = message;
  node.hidden = false;
  // 连续提示时重新播放进入动画，让人注意到内容换了
  node.style.animation = 'none';
  void node.offsetHeight;
  node.style.animation = '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 8000 : 3500);
}

// 页签：容器内 [role=tab][aria-controls] 与对应 [role=tabpanel]，支持左右方向键
export function setupTabs(tablist, onChange) {
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  const select = (tab, focus = false) => {
    for (const other of tabs) {
      const active = other === tab;
      other.setAttribute('aria-selected', String(active));
      other.tabIndex = active ? 0 : -1;
      document.getElementById(other.getAttribute('aria-controls')).hidden = !active;
    }
    if (focus) tab.focus();
    onChange?.(tab.getAttribute('aria-controls'));
  };
  tablist.addEventListener('click', (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (tab) select(tab);
  });
  tablist.addEventListener('keydown', (event) => {
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (step) { event.preventDefault(); select(tabs[(index + step + tabs.length) % tabs.length], true); }
  });
  select(tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0]);
  return select;
}

export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === false || value == null) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.flat().filter((c) => c != null));
  return node;
}

// 共用线条图标（与 Gal 工作台同一套画法）：icon('search') 返回 <svg class="icon">
export const ICON_PATHS = {
  home: 'M3 11l9-7 9 7 M5 10v10h14V10 M10 20v-6h4v6',
  star: 'm12 3 3 6 6 1-4 5 1 6-6-3-6 3 1-6-4-5 6-1z',
  image: 'M3 3h18v18H3z M3 16l6-6 7 7 3-3 2 2 M15 7h.01',
  music: 'M9 18V5l12-2v13 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
  book: 'M12 6c-3-3-7-3-10-2v15c4-1 7-1 10 2 3-3 6-3 10-2V4c-3-1-7-1-10 2z M12 6v15',
  tools: 'M14 6a5 5 0 0 0-6 6L3 17l4 4 5-5a5 5 0 0 0 6-6l-3 3-4-4z',
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  search: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
  refresh: 'M20 7a9 9 0 1 0 1 8 M20 2v5h-5',
  window: 'M3 5h18v14H3z M3 9h18 M14 13h4v3',
  play: 'M7 4l13 8-13 8z',
  close: 'M6 6l12 12 M6 18 18 6',
  moon: 'M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M4 12H2 M22 12h-2 M5 5l1.5 1.5 M17.5 17.5 19 19 M5 19l1.5-1.5 M17.5 6.5 19 5',
  file: 'M5 3h9l5 5v13H5z M14 3v6h5',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
  power: 'M12 3v8 M6.3 6.3a8 8 0 1 0 11.4 0',
  settings: 'M4 6h9 M17 6h3 M4 12h3 M11 12h9 M4 18h11 M19 18h1 M15 4v4 M9 10v4 M17 16v4',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  'eye-off': 'M3 3l18 18 M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3 3.6 M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7a9.6 9.6 0 0 0 5.4-1.6 M9.9 9.9a3 3 0 0 0 4.2 4.2',
  'arrow-up': 'M12 19V5 M6 11l6-6 6 6',
  'arrow-down': 'M12 5v14 M6 13l6 6 6-6',
  plus: 'M12 5v14 M5 12h14',
  trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13',
  sidebar: 'M3 4h18v16H3z M9 4v16',
  monitor: 'M3 4h18v12H3z M8 20h8 M12 16v4',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
  alert: 'M12 3 2 20h20z M12 10v4 M12 17h.01',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 11v5 M12 8h.01',
  check: 'M5 12l5 5 9-10',
  'arrow-right': 'M5 12h14 M13 6l6 6-6 6',
  folder: 'M3 6h6l2 2h10v11H3z',
  'corner-left': 'M9 14 4 9l5-5 M4 9h11a5 5 0 0 1 0 10h-3',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.file);
  svg.append(path);
  return svg;
}

// 把页面里的 <span data-icon="x"></span> 换成图标
export function hydrateIcons(root = document) {
  for (const node of root.querySelectorAll('[data-icon]')) node.replaceWith(icon(node.dataset.icon));
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, '0')}`;
}
