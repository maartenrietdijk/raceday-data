(function (root) {
  'use strict';
  const settingsKey = 'raceday_statistics';
  const tokenKey = 'raceday_statistics_access';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  function read(storage, key) { try { return storage.getItem(key); } catch { return null; } }
  let settings = {};
  try { settings = JSON.parse(read(root.localStorage, settingsKey) || '{}'); } catch { /* Use defaults. */ }
  const stats = { endpoint: settings.endpoint || 'https://raceday-statistics.gentle-meadow-2bdd.workers.dev', days: [1, 7, 30, 90].includes(settings.days) ? settings.days : 30,
    currency: ['EUR', 'USD', 'GBP'].includes(settings.currency) ? settings.currency : 'EUR',
    token: read(root.localStorage, tokenKey) || read(root.sessionStorage, tokenKey) || '', loading: false, error: '', data: null, nextRefresh: 0, settingsOpen: false };

  function rememberToken() {
    try {
      if (stats.token) root.localStorage.setItem(tokenKey, stats.token);
      else root.localStorage.removeItem(tokenKey);
      root.sessionStorage.removeItem(tokenKey);
    } catch { /* The connection still works when browser storage is unavailable. */ }
  }
  // Preserve access codes from the previous session-only storage.
  if (stats.token) rememberToken();

  function points(chart) {
    if (!chart || !Array.isArray(chart.values)) return [];
    const primary = Math.max(0, (chart.measures || []).findIndex(measure => measure.unit === '$' || measure.unit === chart.yaxis_currency));
    return chart.values.filter(point => Array.isArray(point) ? point.length === 2 :
      (point.measure === undefined || Number(point.measure) === primary) && (point.segment === undefined || point.segment === null || Number(point.segment) === 0))
      .map(point => {
        const rawDate = Array.isArray(point) ? point[0] : point.cohort ?? point.timestamp ?? point.date;
        const rawValue = Array.isArray(point) ? point[1] : point.value;
        const date = typeof rawDate === 'number' ? new Date(rawDate < 1e12 ? rawDate * 1000 : rawDate) : new Date(rawDate);
        return { date, value: rawValue === null || rawValue === undefined ? NaN : Number(rawValue), incomplete: Boolean(point.incomplete) };
      }).filter(point => Number.isFinite(point.value) && Number.isFinite(point.date.getTime()))
      .sort((a, b) => a.date - b.date);
  }
  function format(value, unit, currency = stats.currency, precision) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    if (unit === '$' || ['EUR', 'USD', 'GBP'].includes(unit)) return new Intl.NumberFormat('nl-NL', { style: 'currency', currency, maximumFractionDigits: 2 }).format(value);
    // RevenueCat percentage metrics are already expressed in percentage points.
    return new Intl.NumberFormat('nl-NL', { maximumFractionDigits: precision ?? (unit === '%' ? 2 : 0) }).format(value) + (unit === '%' ? '%' : '');
  }
  function saveSettings() {
    try {
      root.localStorage.setItem(settingsKey, JSON.stringify({ endpoint: stats.endpoint, days: stats.days, currency: stats.currency }));
      rememberToken();
    } catch { /* The connection still works without browser storage. */ }
  }
  function renderIfVisible() { if (typeof state !== 'undefined' && state.activeView === 'statistics') render(); }
  function connectionForm() {
    return `<details class="statistics-connection" ${stats.settingsOpen || !stats.endpoint || !stats.token ? 'open' : ''}>
      <summary>Koppeling instellen</summary>
      <p>Vul de URL van je statistieken-backend en de bijbehorende toegangscode in. Je RevenueCat-sleutel blijft op de server.</p>
      <form onsubmit="event.preventDefault(); RaceDayStatistics.connect(this)">
        <div class="field"><label for="statisticsEndpoint">Backend-URL</label><input id="statisticsEndpoint" name="endpoint" type="url" required placeholder="https://raceday-statistics.…workers.dev" value="${escape(stats.endpoint)}" autocomplete="url"></div>
        <div class="field"><label for="statisticsToken">Toegangscode</label><input id="statisticsToken" name="token" type="password" required value="${escape(stats.token)}" autocomplete="off"><span class="statistics-hint">Lokaal bewaard in deze browser. Ontkoppelen wist de code.</span></div>
        <button class="btn btn-primary" type="submit" ${stats.loading ? 'disabled' : ''}>Koppelen en ophalen</button>
        ${stats.endpoint ? `<button class="btn btn-ghost" type="button" onclick="RaceDayStatistics.disconnect()" ${stats.loading ? 'disabled' : ''}>Ontkoppelen</button>` : ''}
      </form>
      <p class="statistics-hint">Nog geen backend? Volg <a href="REVENUECAT.md" target="_blank" rel="noopener">de installatie-instructies</a>.</p>
    </details>`;
  }
  function metricRows() {
    const metrics = Array.isArray(stats.data?.overview?.metrics) ? stats.data.overview.metrics : [];
    const coreMetrics = [
      ['active_trials', 'Active Trials'], ['active_subscriptions', 'Active Subscriptions'],
      ['mrr', 'MRR'], ['revenue', 'Revenue'], ['new_customers', 'New Customers'],
      ['new_customers_today', 'New Customers vandaag'],
    ];
    return `<dl class="statistics-metrics">${coreMetrics.map(([id, label]) => {
      const metric = (id === 'new_customers_today' ? stats.data?.new_customers_today : metrics.find(item => item.id === id)) || { id, value: null, name: label, description: 'Niet beschikbaar' };
      const period = String(metric.period || '').match(/^P(\d+)D$/);
      const caption = id === 'new_customers_today' ? 'Vandaag (UTC)' : period && Number(period[1]) > 0 ? `Afgelopen ${Number(period[1])} dagen` : period ? 'Huidige stand' : metric.description || '';
      const updated = metric.last_updated_at ? new Date(metric.last_updated_at).toLocaleString('nl-NL') : '';
      const current = metric.value !== null && metric.value !== undefined && (id === 'new_customers_today' || metric.period === 'P0D');
      return `<div><dt>${escape(label)}</dt><dd><span class="statistics-metric-value">${escape(format(metric.value, metric.unit, stats.data.overview?.currency || stats.data.currency))}</span><span class="statistics-period ${current ? 'is-current' : ''}" title="${escape(updated ? 'Bron bijgewerkt: ' + updated : id === 'new_customers_today' ? 'De huidige UTC-dag loopt nog; dit aantal kan nog veranderen.' : '')}">${escape(caption)}</span></dd></div>`;
    }).join('')}</dl>`;
  }
  function revenueChart() {
    const chart = stats.data?.chart;
    const series = points(chart);
    if (!series.length) return '<div class="empty compact"><h3>Geen omzetgegevens</h3><p>Voor deze periode is nog geen omzetgrafiek beschikbaar.</p></div>';
    const currency = chart.yaxis_currency || stats.data.currency;
    const dateLabel = date => date.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', timeZone: 'UTC' });
    return `<div id="statisticsRevenueChart" class="statistics-evil-chart" role="region" aria-label="Dagelijkse omzet in ${escape(currency)}. Gebruik de pijltjestoetsen om dagbedragen te bekijken."><p class="statistics-muted" role="status">Grafiek laden…</p></div><details class="statistics-table"><summary>Bekijk dagbedragen</summary><div><table><thead><tr><th scope="col">Datum</th><th scope="col">Omzet</th><th scope="col">Status</th></tr></thead><tbody>${series.map(point => `<tr><td>${escape(dateLabel(point.date))}</td><td>${escape(format(point.value, '$', currency))}</td><td>${point.incomplete ? 'Onvolledig' : 'Beschikbaar'}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  let chartLoader;
  function mountRevenueChart() {
    const container = document.getElementById('statisticsRevenueChart');
    if (!container) return;
    const chart = stats.data?.chart;
    const series = points(chart);
    const currency = chart?.yaxis_currency || stats.data?.currency || stats.currency;
    if (!chartLoader) {
      chartLoader = root.RaceDayEvilCharts ? Promise.resolve(root.RaceDayEvilCharts) : new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'statistics-chart.js?v=20261001-2';
        script.onload = () => root.RaceDayEvilCharts ? resolve(root.RaceDayEvilCharts) : reject(new Error('Grafiekmodule ontbreekt.'));
        script.onerror = () => { script.remove(); reject(new Error('Grafiekmodule kon niet worden geladen.')); };
        document.head.appendChild(script);
      });
    }
    chartLoader.then(renderer => {
      if (document.getElementById('statisticsRevenueChart') === container) renderer.mount(container, series, currency);
    }).catch(() => {
      chartLoader = null;
      if (document.getElementById('statisticsRevenueChart') === container) {
        container.textContent = 'De grafiek kon niet worden geladen. Controleer of statistics-chart.js is geüpload. De dagbedragen staan in de tabel hieronder.';
      }
    });
  }
  function render() {
    root.RaceDayEvilCharts?.unmount();
    const configured = Boolean(stats.endpoint && stats.token);
    const updated = stats.data?.fetched_at ? new Date(stats.data.fetched_at).toLocaleString('nl-NL') : '';
    document.getElementById('mainContent').innerHTML = `<div class="statistics-page">
      <div class="dashboard-heading"><div><h1 class="dashboard-title">Statistieken</h1><p class="dashboard-sub">Abonnementen, klanten en omzet via RevenueCat.</p></div>
        <button class="btn btn-primary" type="button" onclick="RaceDayStatistics.refresh()" ${stats.loading || !configured ? 'disabled' : ''}>${editorIcon('sync')}<span>${stats.loading ? 'Ophalen…' : 'Verversen'}</span></button></div>
      <div class="statistics-toolbar"><div class="field"><label for="statisticsPeriod">Omzetperiode</label><select id="statisticsPeriod" onchange="RaceDayStatistics.change('days', this.value)" ${stats.loading ? 'disabled' : ''}>${[1, 7, 30, 90].map(days => `<option value="${days}" ${stats.days === days ? 'selected' : ''}>${days === 1 ? 'Vandaag' : `Afgelopen ${days} dagen`}</option>`).join('')}</select></div>
        <div class="field"><label for="statisticsCurrency">Valuta</label><select id="statisticsCurrency" onchange="RaceDayStatistics.change('currency', this.value)" ${stats.loading ? 'disabled' : ''}>${['EUR', 'USD', 'GBP'].map(currency => `<option ${currency === stats.currency ? 'selected' : ''}>${currency}</option>`).join('')}</select></div>
        <p class="statistics-status" role="status" aria-live="polite">${stats.loading ? 'Gegevens ophalen…' : updated ? 'Opgehaald: ' + escape(updated) : 'Nog niet opgehaald'}</p></div>
      ${stats.error ? `<div class="statistics-message" role="alert">${escape(stats.error)}${stats.data ? ' Je ziet de laatst opgehaalde gegevens.' : ''}</div>` : ''}
      ${stats.data?.warnings?.length ? `<div class="statistics-message" role="status">${stats.data.warnings.map(escape).join('<br>')}</div>` : ''}
      ${!configured ? '<section class="statistics-empty"><h2>Koppel RevenueCat</h2><p>Stel hieronder je koppeling in om je cijfers te zien. Daarna worden ze bij het laden van het dashboard automatisch opgehaald.</p></section>' : stats.loading && !stats.data ? '<p class="statistics-muted" aria-busy="true">RevenueCat-statistieken laden…</p>' : stats.data ? `<div class="statistics-layout"><section class="statistics-panel"><h2>Kerncijfers</h2><p class="statistics-muted">De periode staat per cijfer vermeld.</p>${metricRows()}</section><section class="statistics-panel statistics-revenue"><h2>Dagelijkse omzet</h2><p class="statistics-muted">${escape(stats.data.range.start_date)} t/m ${escape(stats.data.range.end_date)} (UTC) · De huidige dag kan nog onvolledig zijn.</p>${revenueChart()}</section></div>` : '<p class="statistics-muted">Klik op Verversen om je cijfers op te halen.</p>'}
      ${connectionForm()}</div>`;
    mountRevenueChart();
  }
  async function refresh() {
    if (stats.loading || !stats.endpoint || !stats.token) return;
    if (Date.now() < stats.nextRefresh) {
      stats.error = `Wacht nog ${Math.ceil((stats.nextRefresh - Date.now()) / 1000)} seconden voordat je opnieuw ververst.`;
      renderIfVisible(); return;
    }
    stats.loading = true; stats.error = ''; renderIfVisible();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const url = new URL(stats.endpoint);
      url.pathname = '/statistics'; url.search = new URLSearchParams({ days: stats.days, currency: stats.currency }).toString();
      const response = await fetch(url, { headers: { Authorization: `Bearer ${stats.token}` }, cache: 'no-store', signal: controller.signal });
      let data;
      try { data = await response.json(); } catch { throw new Error('De backend gaf geen geldig antwoord. Controleer de Backend-URL.'); }
      if (!response.ok) {
        if (response.status === 429) stats.nextRefresh = Date.now() + Math.max(60, Number(response.headers.get('Retry-After')) || 60) * 1000;
        throw new Error(data.error || 'Ophalen mislukt. Probeer opnieuw.');
      }
      if (!data.range || (!data.overview && !data.chart && !data.new_customers_today)) throw new Error('De backend gaf geen statistieken terug.');
      stats.data = data;
      stats.nextRefresh = Date.now() + 15000;
    } catch (error) {
      stats.error = error.name === 'AbortError' ? 'Ophalen duurde te lang. Probeer opnieuw.' : error instanceof TypeError ? 'De backend is niet bereikbaar. Controleer de URL en de toegestane dashboard-origin.' : error.message;
    } finally { clearTimeout(timer); stats.loading = false; renderIfVisible(); }
  }
  function connect(form) {
    if (stats.loading) return;
    try {
      const url = new URL(form.elements.endpoint.value.trim());
      if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || !['/', '/statistics'].includes(url.pathname)) throw new Error('Gebruik de HTTPS-URL van je backend, zonder queryparameters.');
      const token = form.elements.token.value.trim();
      if (!token) throw new Error('Vul je toegangscode in.');
      if (token.startsWith('sk_')) throw new Error('Gebruik de toegangscode van de backend. De geheime RevenueCat-sleutel hoort alleen op de server.');
      stats.endpoint = url.origin; stats.token = token; stats.data = null; stats.nextRefresh = 0; stats.settingsOpen = false;
      saveSettings(); refresh();
    } catch (error) { stats.error = error.message; stats.settingsOpen = true; renderIfVisible(); }
  }
  function change(key, value) {
    if (stats.loading) return;
    if (key === 'days' && [1, 7, 30, 90].includes(Number(value))) stats.days = Number(value);
    else if (key === 'currency' && ['EUR', 'USD', 'GBP'].includes(value)) stats.currency = value;
    else return;
    stats.data = null; stats.error = ''; saveSettings(); renderIfVisible(); refresh();
  }
  function disconnect() { if (stats.loading) return; stats.endpoint = ''; stats.token = ''; stats.data = null; stats.error = ''; saveSettings(); renderIfVisible(); }
  function select() {
    state.activeSeries = null; state.activeView = 'statistics';
    renderSidebar(); renderMain();
    if (root.innerWidth <= 760 && document.querySelector('.sidebar').classList.contains('open')) toggleSidebar();
    if (!stats.data && !stats.error) refresh();
  }
  const api = { render, select, refresh, connect, disconnect, change, points, format };
  root.RaceDayStatistics = api;
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof document !== 'undefined') refresh();
})(typeof window !== 'undefined' ? window : globalThis);
