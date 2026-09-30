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
    brand.innerHTML = '<span class="logo">🔤</span><b>' + esc(BRAND) + "</b>";
    header.insertBefore(brand, header.firstChild);

    // GitHub 开源仓库链接（左上角品牌旁，预留入口）
    var gh = document.createElement("a");
    gh.className = "ghlink";
    gh.href = "https://github.com/lhg-skills/srt-craft";
    gh.target = "_blank";
    gh.rel = "noopener";
    gh.title = "GitHub 开源仓库";
    gh.innerHTML = "⭐ GitHub";
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
    btn.textContent = theme === "dark" ? "☀️" : "🌙";
    btn.onclick = function () {
      theme = theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      btn.textContent = theme === "dark" ? "☀️" : "🌙";
      try { localStorage.setItem("jy_theme", theme); } catch (e) {}
    };
    header.appendChild(btn);
  });
})();
