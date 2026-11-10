// 在页面绘制前应用主题，避免深色模式闪白。必须是外部文件：服务端 CSP 只允许 script-src 'self'。
// fuuko-theme：light | dark | system（未设置时跟随系统）
(function () {
  var mode = 'system';
  try { mode = localStorage.getItem('fuuko-theme') || 'system'; } catch (e) { /* 无痕模式 */ }
  var dark = mode === 'dark' || (mode === 'system' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  if (dark) document.documentElement.dataset.theme = 'dark';
  try { if (localStorage.getItem('fuuko-sidebar') === 'collapsed') document.documentElement.dataset.sidebar = 'collapsed'; } catch (e) { /* 无痕模式 */ }
})();
