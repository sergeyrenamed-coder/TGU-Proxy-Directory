(() => {
  "use strict";
  const API = "https://raw.githubusercontent.com/sergeyrenamed-coder/TGU-Proxy-Directory/main/data/proxies.json";
  const $ = id => document.getElementById(id);
  const state = { proxies: [], filter: "all", query: "", ruOnly: false, ru: null };
  $("year").textContent = String(new Date().getFullYear());

  const safe = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[c]);
  const number = value => Number(value || 0).toLocaleString("ru-RU");
  const date = value => {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ru-RU", { day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit" });
  };
  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toast.timeout);
    toast.timeout = setTimeout(() => node.classList.remove("show"), 1800);
  }
  function clipboard(value) {
    navigator.clipboard?.writeText(value).then(() => toast("Скопировано")).catch(() => toast("Не удалось скопировать"));
  }
  function proxyLink(proxy) {
    if (proxy.type === "MTProto") {
      return "tg://proxy?server=" + encodeURIComponent(proxy.server) + "&port=" + encodeURIComponent(proxy.port) + "&secret=" + encodeURIComponent(proxy.secret || "");
    }
    return String(proxy.server) + ":" + String(proxy.port);
  }
  function isRuFresh(health) {
    const checked = Date.parse(health?.generated_at || "");
    return health?.status === "active" && Number.isFinite(checked) &&
      checked <= Date.now() && Date.now() - checked <= 24 * 60 * 60 * 1000;
  }
  function ruMap() {
    if (!isRuFresh(state.ru)) return new Map();
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return new Map((state.ru.checks || [])
      .filter(item => Date.parse(item.checked_at) >= cutoff)
      .map(item => [item.proxy_id, item]));
  }
  function renderProxies() {
    const ru = ruMap();
    const query = state.query.toLowerCase();
    const visible = state.proxies.filter(p => {
      if (state.filter !== "all" && p.type !== state.filter) return false;
      if (state.ruOnly && ru.get(p.id)?.status !== "working") return false;
      return [p.server, p.country, p.type, ...(p.sources || [])].join(" ").toLowerCase().includes(query);
    });
    $("visible-count").textContent = number(visible.length) + " / " + number(state.proxies.length) + " ENDPOINTS · ПОКАЗАНО " + number(Math.min(200,visible.length));
    if (!visible.length) {
      $("proxy-rows").innerHTML = '<tr><td colspan="6" class="empty-cell">По заданным условиям прокси не найдены.</td></tr>';
      return;
    }
    $("proxy-rows").innerHTML = visible.slice(0,200).map(p => {
      const proof = ru.get(p.id);
      const rtt = p.rtt_ms == null ? "—" : Math.round(Number(p.rtt_ms)) + " ms";
      const ruText = proof?.status === "working" ? "● RU VERIFIED" : proof?.status === "failed" ? "○ RU FAILED" : "—";
      return '<tr><td><span class="hostname">' + safe(p.server) + '</span><span class="port">:' + safe(p.port) + '</span></td>' +
        '<td><span class="protocol">' + safe(p.type) + '</span></td>' +
        '<td class="dim">' + safe(p.country || "—") + '</td>' +
        '<td class="dim">' + safe(rtt) + '</td>' +
        '<td><span class="ru-tag ' + (proof?.status === "working" ? "yes" : "") + '">' + ruText + '</span></td>' +
        '<td><button class="copy-link" type="button" data-copy="' + safe(proxyLink(p)) + '">COPY ↗</button></td></tr>';
    }).join("");
  }
  function renderRU(health) {
    const active = isRuFresh(health);
    const totals = health?.totals || {};
    $("ru-working-count").textContent = active ? number(totals.working) : "—";
    $("ru-checked").textContent = active ? number(totals.checked) : "—";
    $("ru-coverage").textContent = active ? Number(totals.coverage_percent || 0).toFixed(1) + "%" : "—";
    $("ru-updated").textContent = active ? date(health.generated_at) : "—";
    $("ru-state").textContent = active ? "● RU CLOUD RESULTS AVAILABLE" :
      health?.status === "active" ? "○ RU CHECKER: ДАННЫЕ УСТАРЕЛИ" : "○ RU CHECKER НЕ НАСТРОЕН";
    const byId = new Map(state.proxies.map(p => [p.id, p]));
    const matches = active ? (health.checks || [])
      .filter(x => x.status === "working" && byId.has(x.proxy_id) &&
        Date.parse(x.checked_at) >= Date.now() - 24 * 60 * 60 * 1000)
      .sort((a,b) => (a.latency_ms ?? 999999) - (b.latency_ms ?? 999999))
      .slice(0, 5) : [];
    $("ru-list").innerHTML = matches.length ? matches.map((item,i) => {
      const proxy = byId.get(item.proxy_id);
      return '<div class="result-item"><span class="result-rank">0' + (i+1) + '</span>' +
        '<div class="result-host">' + safe(proxy.server) + ':' + safe(proxy.port) +
        '<span class="result-meta">' + safe(proxy.type) + ' · ' + safe(date(item.checked_at)) + '</span></div>' +
        '<strong class="result-rtt">' + safe(item.latency_ms) + ' ms</strong></div>';
    }).join("") : '<div class="empty-panel">' +
      (active ? "За последние 24 часа подтверждённых из РФ прокси пока нет." :
        "После настройки Yandex Cloud Functions здесь появятся реальные результаты RU-проверки.") + '</div>';
  }
  function renderSources(stats) {
    const list = Array.isArray(stats.sources) ? stats.sources : [];
    $("source-grid").innerHTML = list.length ? list.map(source => {
      const online = source.status === "ok";
      return '<article class="source-card"><div class="source-title"><span>' + safe(source.name) + '</span>' +
        '<span class="source-status ' + (online ? "" : "error") + '">' + (online ? "● ONLINE" : "○ ERROR") + '</span></div>' +
        '<div class="source-numbers"><div><strong>' + number(source.fetched) + '</strong><span>FETCHED</span></div>' +
        '<div><strong>' + number(source.usable) + '</strong><span>USABLE</span></div>' +
        '<div><strong>' + number(source.unsupported) + '</strong><span>SKIPPED</span></div></div>' +
        (source.error ? '<p class="source-error">' + safe(source.error) + '</p>' : '') + '</article>';
    }).join("") : '<div class="source-card">Пока нет сведений об источниках.</div>';
  }
  async function load() {
    try {
      const stamp = "?v=" + Date.now();
      const opt = { cache:"no-store" };
      const [c,s,r] = await Promise.all([
        fetch("./data/proxies.json"+stamp,opt),
        fetch("./data/stats.json"+stamp,opt),
        fetch("./data/ru-health.json"+stamp,opt).catch(() => null)
      ]);
      if (!c.ok || !s.ok) throw new Error("Основной каталог недоступен.");
      const [catalog,stats] = await Promise.all([c.json(),s.json()]);
      let health = null;
      if (r?.ok) health = await r.json().catch(() => null);
      state.proxies = Array.isArray(catalog.proxies) ? catalog.proxies : [];
      // RU records are deliberately separate from the global catalog.
      state.ru = health;
      const counts = catalog.counts || stats.totals || {};
      $("total-count").textContent = number(counts.total);
      $("mtproto-count").textContent = number(counts.mtproto);
      $("socks-count").textContent = number(counts.socks5);
      $("last-updated").textContent = date(catalog.generated_at || stats.generated_at);
      $("overall-status").textContent = stats.overall_status === "partial" ? "○ ЧАСТИЧНЫЙ СБОЙ ИСТОЧНИКОВ" : "● КАТАЛОГ ДОСТУПЕН";
      renderRU(health);
      renderProxies();
      renderSources(stats);
    } catch (error) {
      $("overall-status").textContent = "○ КАТАЛОГ НЕДОСТУПЕН";
      $("proxy-rows").innerHTML = '<tr><td colspan="6" class="empty-cell">' + safe(error.message) + '</td></tr>';
      $("ru-state").textContent = "○ НЕТ ДАННЫХ";
    }
  }
  $("search").addEventListener("input", e => { state.query = e.target.value || ""; renderProxies(); });
  document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll("[data-filter]").forEach(x => x.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    renderProxies();
  }));
  $("ru-only").addEventListener("click", e => {
    state.ruOnly = !state.ruOnly;
    e.currentTarget.classList.toggle("active",state.ruOnly);
    e.currentTarget.setAttribute("aria-pressed",String(state.ruOnly));
    renderProxies();
  });
  $("proxy-rows").addEventListener("click", e => {
    const button = e.target.closest("[data-copy]");
    if (button) clipboard(button.getAttribute("data-copy"));
  });
  $("copy-api").addEventListener("click", () => clipboard(API));
  load();
})();
