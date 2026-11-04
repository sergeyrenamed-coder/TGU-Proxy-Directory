(() => {
  'use strict';
  const API_URL = 'https://raw.githubusercontent.com/sergeyrenamed-coder/TGU-Proxy-Directory/main/data/proxies.json';
  const $ = (id) => document.getElementById(id);
  const state = { proxies: [], filter: 'all', query: '' };
  $('year').textContent = String(new Date().getFullYear());

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }
  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }
  function notify(message) {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.add('show');
    window.clearTimeout(notify.timer);
    notify.timer = window.setTimeout(() => toast.classList.remove('show'), 1800);
  }
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      notify('Copied to clipboard');
    } catch (_) {
      notify('Clipboard is unavailable in this browser');
    }
  }
  function proxyLink(proxy) {
    if (proxy.type === 'MTProto') {
      return 'tg://proxy?server=' + encodeURIComponent(proxy.server) + '&port=' + encodeURIComponent(proxy.port) + '&secret=' + encodeURIComponent(proxy.secret || '');
    }
    return String(proxy.server) + ':' + String(proxy.port);
  }
  function renderRows() {
    const query = state.query.toLowerCase();
    const filtered = state.proxies.filter((proxy) => {
      const protocolOk = state.filter === 'all' || proxy.type === state.filter;
      const text = [proxy.server, proxy.country, proxy.type, ...(proxy.sources || [])].join(' ').toLowerCase();
      return protocolOk && text.includes(query);
    });
    $('visible-count').textContent = filtered.length + ' / ' + state.proxies.length + ' ENDPOINTS';
    if (!filtered.length) {
      $('proxy-rows').innerHTML = '<tr><td colspan="7" class="empty">No matching endpoints in the current catalog.</td></tr>';
      return;
    }
    $('proxy-rows').innerHTML = filtered.slice(0, 250).map((proxy) => {
      const isSocks = proxy.type === 'SOCKS5';
      const stale = Boolean(proxy.stale);
      const rtt = proxy.rtt_ms == null ? '—' : Math.round(Number(proxy.rtt_ms)) + ' ms';
      const uptime = proxy.uptime == null ? '—' : (Number(proxy.uptime) <= 1 ? (Number(proxy.uptime) * 100).toFixed(0) : Number(proxy.uptime).toFixed(0)) + '%';
      const action = escapeHtml(proxyLink(proxy));
      return '<tr>' +
        '<td><span class="endpoint">' + escapeHtml(proxy.server) + '</span><span class="port">:' + escapeHtml(proxy.port) + '</span></td>' +
        '<td><span class="protocol ' + (isSocks ? 'socks' : '') + '">' + escapeHtml(proxy.type) + '</span></td>' +
        '<td><span class="region">' + escapeHtml(proxy.country || '—') + '</span></td>' +
        '<td><span class="latency">' + escapeHtml(rtt) + '</span></td>' +
        '<td><span class="uptime">' + escapeHtml(uptime) + '</span></td>' +
        '<td><span class="state ' + (stale ? 'stale' : '') + '"><i></i>' + (stale ? 'STALE' : 'IN CATALOG') + '</span></td>' +
        '<td><button class="copy-row" data-copy="' + action + '">COPY ' + (isSocks ? 'ADDRESS' : 'LINK') + '</button></td>' +
      '</tr>';
    }).join('');
    document.querySelectorAll('[data-copy]').forEach((button) => {
      button.addEventListener('click', () => copy(button.getAttribute('data-copy')));
    });
  }
  function renderSources(stats) {
    const sources = Array.isArray(stats.sources) ? stats.sources : [];
    $('source-grid').innerHTML = sources.map((source) => {
      const status = source.status || 'unknown';
      const label = status === 'ok' ? 'ONLINE' : status === 'error' ? 'ERROR' : 'UNKNOWN';
      const error = source.error ? '<div class="source-error">' + escapeHtml(source.error) + '</div>' : '';
      return '<article class="source-card">' +
        '<div class="source-top"><div class="source-name">' + escapeHtml(source.name) + '</div>' +
        '<div class="source-state ' + escapeHtml(status) + '"><i></i>' + label + '</div></div>' +
        '<div class="source-numbers">' +
        '<div class="source-number"><strong>' + Number(source.fetched || 0).toLocaleString() + '</strong><span>FETCHED</span></div>' +
        '<div class="source-number"><strong>' + Number(source.usable || 0).toLocaleString() + '</strong><span>TGU USABLE</span></div>' +
        '<div class="source-number"><strong>' + Number(source.unsupported || 0).toLocaleString() + '</strong><span>SKIPPED</span></div>' +
        '</div>' + error + '</article>';
    }).join('') || '<div class="source-card">No source status available yet.</div>';
  }
  async function load() {
    try {
      const options = { cache: 'no-store' };
      const [proxyResponse, statsResponse] = await Promise.all([
        fetch('./data/proxies.json?ts=' + Date.now(), options),
        fetch('./data/stats.json?ts=' + Date.now(), options)
      ]);
      if (!proxyResponse.ok || !statsResponse.ok) throw new Error('Catalog files are not published yet.');
      const [catalog, stats] = await Promise.all([proxyResponse.json(), statsResponse.json()]);
      state.proxies = Array.isArray(catalog.proxies) ? catalog.proxies : [];
      const counts = catalog.counts || stats.totals || {};
      $('total-count').textContent = Number(counts.total || 0).toLocaleString();
      $('mtproto-count').textContent = Number(counts.mtproto || 0).toLocaleString();
      $('socks-count').textContent = Number(counts.socks5 || 0).toLocaleString();
      $('stale-count').textContent = Number(counts.stale || 0).toLocaleString();
      $('last-updated').textContent = formatDate(catalog.generated_at || stats.generated_at);
      const overall = $('overall-status');
      overall.innerHTML = '<i></i> ' + (stats.overall_status === 'partial' ? 'PARTIAL SOURCE OUTAGE' : 'CATALOG ONLINE');
      if (stats.overall_status === 'partial') overall.style.color = 'var(--warn)';
      renderRows();
      renderSources(stats);
    } catch (error) {
      $('overall-status').innerHTML = '<i></i> CATALOG UNAVAILABLE';
      $('overall-status').style.color = 'var(--danger)';
      $('proxy-rows').innerHTML = '<tr><td colspan="7" class="empty">' + escapeHtml(error.message) + '</td></tr>';
      $('source-grid').innerHTML = '<div class="source-card">Waiting for the first successful GitHub Actions collection run.</div>';
    }
  }
  $('search').addEventListener('input', (event) => {
    state.query = event.target.value || '';
    renderRows();
  });
  document.querySelectorAll('[data-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-filter]').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      state.filter = button.getAttribute('data-filter');
      renderRows();
    });
  });
  $('copy-api').addEventListener('click', () => copy(API_URL));
  load();
})();
