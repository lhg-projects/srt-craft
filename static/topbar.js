/* topbar.js -- 共享顶栏 + 明暗主题注入（无构建步骤，页面 <head> 引入一次）
   用法: <script src="/static/topbar.js" data-brand="SRT 字幕校正"></script>
   单页应用：品牌 + 健康点 + 主题切换（页面自有控件保留在 header 内） */
(function () {
  var BRAND = (document.currentScript && document.currentScript.dataset.brand) || "剪映工具箱";

  // 主题：localStorage 优先，默认跟随系统；渲染前设置避免闪白
  var theme = "dark";
  try {
    theme = localStorage.getItem("jy_theme")
      || (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  } catch (e) {}
  document.documentElement.dataset.theme = theme;
  var link = document.createElement("link");
  link.rel = "stylesheet"; link.href = "/static/theme.css";
  document.head.appendChild(link);
  // 内联 SVG 图标 sprite（feather 风格线性图标）：全站 <use> 引用，
  // 取代 emoji（emoji 字形随平台漂移、尺寸不受 font-size 约束）
  var sprite = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  sprite.setAttribute("aria-hidden", "true");
  sprite.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
  var STROKE = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  sprite.innerHTML =
    '<symbol id="i-gear" viewBox="0 0 24 24" ' + STROKE + '><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></symbol>' +
    '<symbol id="i-mic" viewBox="0 0 24 24" ' + STROKE + '><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></symbol>' +
    '<symbol id="i-volume" viewBox="0 0 24 24" ' + STROKE + '><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></symbol>' +
    '<symbol id="i-cpu" viewBox="0 0 24 24" ' + STROKE + '><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></symbol>' +
    '<symbol id="i-download" viewBox="0 0 24 24" ' + STROKE + '><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></symbol>' +
    '<symbol id="i-headphones" viewBox="0 0 24 24" ' + STROKE + '><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></symbol>' +
    '<symbol id="i-box" viewBox="0 0 24 24" ' + STROKE + '><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></symbol>' +
    '<symbol id="i-eye" viewBox="0 0 24 24" ' + STROKE + '><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></symbol>' +
    '<symbol id="i-zap" viewBox="0 0 24 24" ' + STROKE + '><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></symbol>' +
    '<symbol id="i-refresh" viewBox="0 0 24 24" ' + STROKE + '><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></symbol>' +
    '<symbol id="i-star" viewBox="0 0 24 24" ' + STROKE + '><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></symbol>' +
    '<symbol id="i-sun" viewBox="0 0 24 24" ' + STROKE + '><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></symbol>' +
    '<symbol id="i-moon" viewBox="0 0 24 24" ' + STROKE + '><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></symbol>' +
    '<symbol id="i-type" viewBox="0 0 24 24" ' + STROKE + '><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></symbol>';
  document.head.appendChild(sprite);
  // 标签页角标（favicon），与品牌一致；SVG 现代浏览器全支持
  var fav = document.createElement("link");
  fav.rel = "icon"; fav.type = "image/svg+xml"; fav.href = "/static/favicon.svg";
  document.head.appendChild(fav);

  function esc(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  window.addEventListener("DOMContentLoaded", function () {
    var header = document.querySelector("header");
    if (!header) return;
    header.classList.add("topbar");

    // 品牌区
    var brand = document.createElement("span");
    brand.className = "brand";
    brand.innerHTML = '<span class="logo"><svg class="ic" aria-hidden="true"><use href="#i-type"/></svg></span><b>' + esc(BRAND) + "</b>";
    header.insertBefore(brand, header.firstChild);

    // GitHub 开源仓库链接（左上角品牌旁；窄屏下 .ghlabel 由 CSS 隐藏只留图标）
    var gh = document.createElement("a");
    gh.className = "ghlink";
    gh.href = "https://github.com/lhg-projects/srt-craft";
    gh.target = "_blank";
    gh.rel = "noopener";
    gh.title = "GitHub 开源仓库";
    gh.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-star"/></svg><span class="ghlabel"> GitHub</span>';
    header.appendChild(gh);

    // 健康指示点
    var spacer = document.createElement("span");
    spacer.className = "spacer";
    header.appendChild(spacer);
    var dot = document.createElement("span");
    dot.className = "healddot";
    dot.title = "草稿格式检查中…";
    header.appendChild(dot);
    fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
      dot.className = "healddot" + (h.ok ? (h.jianying_running ? " warn" : "") : " err");
      dot.title = (h.message || "") + (h.jianying_running ? "（剪映运行中）" : "");
    }).catch(function () { dot.className = "healddot err"; dot.title = "服务不可达"; });

    // 主题切换
    var btn = document.createElement("button");
    btn.className = "themetoggle";
    btn.type = "button";
    btn.title = "切换明暗主题";
    var ICON_SUN = '<svg class="ic" aria-hidden="true"><use href="#i-sun"/></svg>';
    var ICON_MOON = '<svg class="ic" aria-hidden="true"><use href="#i-moon"/></svg>';
    btn.innerHTML = theme === "dark" ? ICON_SUN : ICON_MOON;
    btn.onclick = function () {
      theme = theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      btn.innerHTML = theme === "dark" ? ICON_SUN : ICON_MOON;
      try { localStorage.setItem("jy_theme", theme); } catch (e) {}
    };
    header.appendChild(btn);
  });
})();
