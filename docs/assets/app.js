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
    // In RU-only mode show fastest verified connections first, not feed order.
    if (state.ruOnly) visible.sort((a, b) =>
      (ru.get(a.id)?.latency_ms ?? Infinity) - (ru.get(b.id)?.latency_ms ?? Infinity));
    $("visible-count").textContent = number(visible.length) + " / " + number(state.proxies.length) + " ENDPOINTS · ПОКАЗАНО " + number(Math.min(200,visible.length));
    if (!visible.length) {
      $("proxy-rows").innerHTML = '<tr><td colspan="6" class="empty-cell">По заданным условиям прокси не найдены.</td></tr>';
      return;
    }
    $("proxy-rows").innerHTML = visible.slice(0,200).map(p => {
      const proof = ru.get(p.id);
      const rtt = p.rtt_ms == null ? "—" : Math.round(Number(p.rtt_ms)) + " ms";
      const ruText = proof?.status === "working" ? "● RU VERIFIED · " + Math.round(Number(proof.latency_ms)) + " ms" : proof?.status === "failed" ? "○ RU FAILED" : "—";
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
    const byId = new Map(state.proxies.map(p => [p.id, p]));
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    // Catalog changes between RU runs. Count only records present in the
    // currently published global catalog and actually fresh for this visitor.
    const currentChecks = active && Array.isArray(health?.checks) ? health.checks
      .filter(x => byId.has(x.proxy_id) && Date.parse(x.checked_at) >= cutoff &&
        Date.parse(x.checked_at) <= Date.now()) : [];
    const verified = currentChecks.filter(x => x.status === "working");
    const coverage = state.proxies.length ? 100 * currentChecks.length / state.proxies.length : 0;
    $("ru-working-count").textContent = active ? number(verified.length) : "—";
    $("ru-checked").textContent = active ? number(currentChecks.length) : "—";
    $("ru-coverage").textContent = active ? coverage.toFixed(1) + "%" : "—";
    $("ru-updated").textContent = active ? date(health.generated_at) : "—";
    $("ru-state").textContent = active ?
      (currentChecks.length ? "● RU CLOUD RESULTS AVAILABLE" : "○ НЕТ ПРОВЕРОК ДЛЯ ТЕКУЩЕГО КАТАЛОГА") :
      health?.status === "active" ? "○ RU CHECKER: ДАННЫЕ УСТАРЕЛИ" : "○ RU CHECKER НЕ НАСТРОЕН";
    const matches = verified
      .sort((a,b) => (a.latency_ms ?? 999999) - (b.latency_ms ?? 999999))
      .slice(0, 5);
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

  // RNMD Music is a separate experimental namespace. The proxy collector,
  // metrics and filters are intentionally unchanged.
  const musicState = {
    providers: [], tracks: [], query: "", loaded: false, loading: false, error: null
  };
  const musicMode = () => state.query.trim().toLowerCase() === "music";
  const trustedLink = value => {
    try {
      const url = new URL(String(value || ""));
      return url.protocol === "https:" ? url.href : "";
    } catch (_) { return ""; }
  };
  const playableTrack = track => {
    if (!track || track.stream_verified !== true) return "";
    if (!["by", "by-sa", "by-nd", "by-nc", "by-nc-sa", "by-nc-nd", "cc0", "pdm"]
        .includes(String(track.license || "").toLowerCase())) return "";
    try {
      const license = new URL(String(track.license_url || ""));
      if (license.protocol !== "https:" || license.hostname !== "creativecommons.org") return "";
      const media = new URL(String(track.audio_url || ""));
      const host = media.hostname.toLowerCase();
      if (media.protocol !== "https:" || !(host === "jamendo.com" || host.endsWith(".jamendo.com"))) return "";
      return media.href;
    } catch (_) { return ""; }
  };
  const musicLabel = status => ({
    candidate: "◌ НА ПРОВЕРКЕ",
    needs_client_id: "◌ НУЖЕН CLIENT ID",
    integration_pending: "◌ ИССЛЕДОВАНИЕ API",
    verified: "● ПРОВЕРЕНО"
  }[status] || "◌ НЕ ПОДКЛЮЧЁН");
  function renderMusic() {
    const term = musicState.query.trim().toLowerCase();
    const providers = musicState.providers.filter(p =>
      [p.name,p.content,p.id,...(p.languages || [])].join(" ").toLowerCase().includes(term));
    const tracks = musicState.tracks.filter(t =>
      [t.title,t.artist,t.provider,t.language].join(" ").toLowerCase().includes(term));
    $("music-count").textContent = "ИСТОЧНИКИ " + number(musicState.providers.length);
    $("music-tracks-count").textContent = "ИНДЕКС " + number(musicState.tracks.length) + " ТРЕКОВ";
    $("music-status").textContent = musicState.error
      ? "Не удалось загрузить музыкальный каталог: " + musicState.error
      : musicState.loading
        ? "Загрузка музыкальной директории…"
        : "Пилотный режим. Статусы не означают доступность без VPN и не гарантируют права на запись.";
    $("music-provider-grid").innerHTML = providers.length ? providers.map(p => {
      const docs = trustedLink(p.api_docs);
      return '<article class="source-card music-provider-card">' +
        '<div class="source-title"><span>' + safe(p.name) + '</span>' +
        '<span class="source-status ' + (p.status === "verified" ? "" : "error") +
        '">' + safe(musicLabel(p.status)) + '</span></div>' +
        '<p>' + safe(p.content || "") + '</p>' +
        '<div class="music-provider-meta">RU ACCESS: ' + (p.network_ru_verified ? "CHECKED" : "NOT CHECKED") +
        ' · STREAM: ' + (p.streaming_verified ? "CHECKED" : "NOT CHECKED") + '</div>' +
        (docs ? '<a class="under-link" href="' + safe(docs) +
          '" target="_blank" rel="noopener noreferrer">API DOCS ↗</a>' : '') +
        '</article>';
    }).join("") : '<div class="empty-panel">Нет источников по запросу.</div>';
    $("music-track-list").innerHTML = tracks.length ? tracks.slice(0,80).map(t => {
      const source = trustedLink(t.source_page);
      const playable = playableTrack(t);
      const license = trustedLink(t.license_url);
      return '<article class="music-track"><div><strong>' + safe(t.title) + '</strong>' +
        '<span>' + safe(t.artist || "Неизвестный исполнитель") + ' · ' +
        safe(t.provider || "—") + '</span></div>' +
        '<span class="music-license">CC ' + safe(t.license || "НЕИЗВЕСТНО") + '</span>' +
        (license ? '<a class="under-link" href="' + safe(license) +
          '" target="_blank" rel="noopener noreferrer">ЛИЦЕНЗИЯ ↗</a>' : '') +
        (source ? '<a class="under-link" target="_blank" rel="noopener noreferrer" href="' +
          safe(source) + '">ОРИГИНАЛ ↗</a>' : '') +
        (playable ? '<button class="music-play" type="button" data-music-id="' +
          safe(t.id) + '">СЛУШАТЬ ▶</button>' : '') + '</article>';
    }).join("") : '<div class="empty-panel">Публичный музыкальный индекс пока пуст. ' +
      'Источники готовы к экспериментальной проверке, но мы не выдаём тестовые данные за настоящие треки.</div>';
  }
  async function loadMusic(force = false) {
    if (musicState.loading || (musicState.loaded && !force)) return;
    musicState.loading = true;
    musicState.error = null;
    renderMusic();
    try {
      const stamp = "?v=" + Date.now();
      const [p,t] = await Promise.all([
        fetch("./data/music/providers.json"+stamp, { cache:"no-store" }),
        fetch("./data/music/tracks.json"+stamp, { cache:"no-store" })
      ]);
      if (!p.ok || !t.ok) throw new Error("HTTP " + (p.ok ? t.status : p.status));
      const providers = await p.json();
      const tracks = await t.json();
      if (!Array.isArray(providers.providers) || !Array.isArray(tracks.tracks))
        throw new Error("Неверная структура JSON");
      musicState.providers = providers.providers;
      musicState.tracks = tracks.tracks;
      musicState.loaded = true;
    } catch (error) {
      musicState.error = error.message || "Неизвестная ошибка";
    } finally {
      musicState.loading = false;
      renderMusic();
    }
  }
  function syncDirectoryMode() {
    const active = musicMode();
    if (!active) {
      $("music-player").pause();
      $("music-player").removeAttribute("src");
      $("music-player-panel").hidden = true;
    }
    $("proxy-filters").hidden = active;
    $("proxy-results").hidden = active;
    $("music-directory").hidden = !active;
    $("pool-head").textContent = active ? "RNMD Music Directory" : "Proxy Endpoints";
    if (active) loadMusic();
    else renderProxies();
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
  $("search").addEventListener("input", e => {
    state.query = e.target.value || "";
    syncDirectoryMode();
  });
  $("music-search").addEventListener("input", e => {
    musicState.query = e.target.value || "";
    renderMusic();
  });
  $("music-reload").addEventListener("click", () => loadMusic(true));
  $("music-track-list").addEventListener("click", event => {
    const button = event.target.closest("[data-music-id]");
    if (!button) return;
    const track = musicState.tracks.find(item => item.id === button.dataset.musicId);
    const url = playableTrack(track);
    if (!url) return;
    const audio = $("music-player");
    audio.pause();
    audio.src = url;
    $("music-playing-title").textContent = track.title;
    $("music-playing-attribution").textContent = track.artist + " · CC " +
      track.license + " · " + (track.provider || "Openverse");
    $("music-playback-status").textContent =
      "Поток проверен с GitHub Actions. Доступность из РФ не подтверждена; права независимо не проверены.";
    $("music-player-panel").hidden = false;
    audio.load();
    audio.play().catch(() => {
      $("music-playback-status").textContent = "Не удалось начать воспроизведение. Попробуй запустить вручную или открыть оригинал.";
    });
  });
  $("music-player").addEventListener("error", () => {
    $("music-playback-status").textContent =
      "Аудиопоток недоступен с этого устройства. Открой оригинал записи или попробуй позже.";
  });
  $("music-player-close").addEventListener("click", () => {
    const audio = $("music-player");
    audio.pause();
    audio.removeAttribute("src");
    $("music-player-panel").hidden = true;
  });
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
