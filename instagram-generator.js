/* RaceDay Instagram weekend generator — fixed 1080×1350 canvas template. */
(() => {
  'use strict';

  const WIDTH = 1080;
  const HEIGHT = 1350;
  const DISPLAY_ZONE = 'Europe/Amsterdam';
  const DEFAULT_MAX_SESSIONS_PER_SLIDE = 8;
  const COMPACT_MAX_SESSIONS_PER_SLIDE = 9;
  const MINIMAL_MAX_SESSIONS_PER_SLIDE = 10;
  const DEFAULT_CONTENT_TOP = 286;
  const COMPACT_CONTENT_TOP = 246;
  const MINIMAL_CONTENT_TOP = 150;
  const HIDDEN_LOGO_OFFSET = 96;
  const CONTENT_BOTTOM = 1174;
  const OVERVIEW_ROW_HEIGHT = 115;
  const ROW_HEIGHT = 96;
  const GROUP_HEADER_HEIGHT = 64;
  const GROUP_BOTTOM_PADDING = 14;
  const GROUP_GAP = 24;
  const STORE_BADGES = {
    apple: 'instagram-assets/branding/app-store-badge.svg',
    google: 'instagram-assets/branding/google-play-badge.svg',
  };
  const PANEL_RADIUS = 10;
  const BACKGROUND_LAYER_OPACITY = .8;
  const DEFAULT_ON = new Set(['race', 'featureRace', 'sprintRace', 'qualifying', 'sprintQualifying', 'hyperpole']);
  const DEFAULT_OFF = new Set(['practice', 'testing', 'shakedown']);
  const LABELS = {
    practice: 'PRACTICE', qualifying: 'QUALIFYING', hyperpole: 'HYPERPOLE',
    sprintQualifying: 'SPRINT QUALIFYING', sprintRace: 'SPRINT',
    featureRace: 'FEATURE RACE', race: 'RACE', testing: 'TEST',
    shakedown: 'SHAKEDOWN', stage: 'STAGE',
  };
  const SERIES_TIME_ZONES = {
    nascar: 'America/New_York', nascar_oreilly: 'America/New_York',
    nascar_trucks: 'America/New_York', indycar: 'America/New_York',
    indynxt: 'America/New_York', imsa: 'America/New_York',
    supercars: 'Australia/Sydney',
  };
  // Supplement the scanner registry for venues whose calendar needs no source scan.
  const ADDITIONAL_EVENT_TIME_ZONES = {
    'marina bay': 'Asia/Singapore', singapore: 'Asia/Singapore',
    mandalika: 'Asia/Makassar', 'phillip island': 'Australia/Melbourne',
    buriram: 'Asia/Bangkok', 'chang international': 'Asia/Bangkok',
    zandvoort: 'Europe/Amsterdam', catalunya: 'Europe/Madrid', barcelona: 'Europe/Madrid',
    portimao: 'Europe/Lisbon', valencia: 'Europe/Madrid', sakhir: 'Asia/Bahrain',
  };
  const FLAG_ROOT = 'instagram-assets/flags/4x3';
  const FLAG_DATA_URL_CACHE = new Map();
  const LOGO_SCALE_STORAGE_KEY = 'raceday_instagram_logo_scales';
  let logoScaleStorageWarningShown = false;

  const instagramState = {
    month: '', weekends: [], selectedWeeks: new Set(), allSessions: [], selectedIds: new Set(), slides: [], slideIndex: 0,
    images: new Map(), warnings: [], weekend: null, sourceWarningCount: 0,
    assetLoadComplete: false, mode: 'overview', selectedDay: '', displayItems: [],
    title: 'Upcoming races', showLogo: false, showTitle: false, showDate: false, showTopMeta: false,
    seriesOrder: [], draggedSeriesId: '', logoScales: {}, controlTab: 'sessions',
    timeZone: DISPLAY_ZONE, selectedSeries: '', selectedEvent: '',
    timeZonePlacement: 'groups', titleAlignment: 'left', scheduleAlignment: 'center', showFooter: true,
    headerLogoScale: 1, showRowLogos: true,
  };

  function loadLogoScales() {
    try {
      const stored = JSON.parse(localStorage.getItem(LOGO_SCALE_STORAGE_KEY) || '{}');
      return Object.fromEntries(Object.entries(stored).flatMap(([seriesId, value]) => {
        const scale = Number(value);
        return Number.isFinite(scale) && scale >= .45 && scale <= 1.25 ? [[seriesId, scale]] : [];
      }));
    } catch (_) {
      return {};
    }
  }

  function saveLogoScales() {
    try {
      localStorage.setItem(LOGO_SCALE_STORAGE_KEY, JSON.stringify(instagramState.logoScales));
    } catch (error) {
      console.warn('Logoformaten konden niet lokaal worden bewaard:', error);
      if (!logoScaleStorageWarningShown) {
        logoScaleStorageWarningShown = true;
        window.showStatus?.('Het logoformaat blijft actief, maar kon niet op dit apparaat worden onthouden.', 'error');
      }
    }
  }

  function logoScaleFor(seriesId) {
    return instagramState.logoScales[seriesId] || 1;
  }

  // ── Weekend and session selection ─────────────────────────────────────────

  function amsterdamDateParts(date = new Date()) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: DISPLAY_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(date);
    const value = key => parts.find(part => part.type === key)?.value;
    return { year: +value('year'), month: +value('month'), day: +value('day') };
  }

  function dateKeyFromParts(parts) {
    return `${parts.year}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`;
  }

  function addUtcDays(dateKey, days) {
    const [year, month, day] = dateKey.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day + days, 12));
    return date.toISOString().slice(0, 10);
  }

  function weekendRangeFor(date = new Date()) {
    const current = dateKeyFromParts(amsterdamDateParts(date));
    const [year, month, day] = current.split('-').map(Number);
    const weekday = new Date(Date.UTC(year, month - 1, day, 12)).getUTCDay();
    const fridayOffset = weekday === 0 ? -2 : weekday === 6 ? -1 : 5 - weekday;
    const start = addUtcDays(current, fridayOffset);
    return { start, endExclusive: addUtcDays(start, 3), end: addUtcDays(start, 2) };
  }

  function weekendsForMonth(month) {
    if (!/^\d{4}-\d{2}$/.test(month) || +month.slice(5) < 1 || +month.slice(5) > 12) return [];
    const first = `${month}-01`;
    const weekday = new Date(`${first}T12:00:00Z`).getUTCDay();
    let start = addUtcDays(first, -((weekday + 2) % 7));
    const weekends = [];
    while (start.slice(0, 7) <= month) {
      const end = addUtcDays(start, 2);
      if (end >= first) weekends.push({ start, end, endExclusive: addUtcDays(start, 3) });
      start = addUtcDays(start, 7);
    }
    return weekends;
  }

  function renderWeekControls() {
    const month = document.getElementById('instagramMonth');
    if (month) month.value = instagramState.month;
    const picker = document.getElementById('instagramWeekend');
    if (picker) {
      picker.innerHTML = instagramState.weekends.map(week => `<option value="${week.start}">${esc(formatCompactDateRange(week.start, week.end))} · Week ${isoWeek(week.start)}</option>`).join('');
      picker.value = instagramState.weekend.start;
    }
    const isMonth = instagramState.mode === 'monthOverview';
    document.getElementById('instagramWeekendControls')?.toggleAttribute('hidden', isMonth);
    const list = document.getElementById('instagramWeekList');
    if (list) {
      list.hidden = !isMonth;
      list.innerHTML = instagramState.weekends.map(week => `<label class="instagram-session-toggle">
        <input type="checkbox" data-week="${week.start}" ${instagramState.selectedWeeks.has(week.start) ? 'checked' : ''} onchange="toggleInstagramWeek(this.dataset.week, this.checked)">
        <span class="instagram-session-copy"><strong>${esc(formatCompactDateRange(week.start, week.end))}</strong><span>Week ${isoWeek(week.start)}</span></span>
      </label>`).join('');
    }
  }

  async function loadSelectedPeriod(preserveSelection = false) {
    const previousSelection = new Set(instagramState.selectedIds);
    const previousItems = new Set(instagramState.displayItems.map(item => item.uid));
    const periods = instagramState.mode === 'monthOverview' ? instagramState.weekends : [instagramState.weekend];
    const results = periods.map(week => collectWeekendSessions(week, instagramState.mode === 'seriesWeekend'));
    instagramState.allSessions = results.flatMap(result => result.sessions);
    const knownSeries = new Set(instagramState.seriesOrder);
    (state.series || []).forEach(series => { if (!knownSeries.has(series.id)) instagramState.seriesOrder.push(series.id); });
    instagramState.sourceWarningCount = Math.max(0, ...results.map(result => result.warnings.length));
    const days = [...new Set(instagramState.allSessions.map(item => item.dayKey))];
    if (!days.includes(instagramState.selectedDay)) instagramState.selectedDay = days[0] || instagramState.weekend.start;
    const daySelect = document.getElementById('instagramDay');
    if (daySelect) {
      daySelect.innerHTML = days.map(day => `<option value="${day}">${esc(dayHeading(day).day)} · ${esc(dayHeading(day).date)}</option>`).join('');
      daySelect.value = instagramState.selectedDay;
    }
    renderWeekControls();
    renderSeriesEventControls();
    refreshDisplayItems();
    if (preserveSelection) {
      instagramState.selectedIds = new Set(instagramState.displayItems.filter(item =>
        previousItems.has(item.uid) ? previousSelection.has(item.uid) : instagramState.selectedIds.has(item.uid)
      ).map(item => item.uid));
      renderSessionControls();
    }
    renderLogoScaleControls();
    await preloadAssets(instagramState.allSessions);
    rebuildSlides();
  }

  function setInstagramMonth(month) {
    const weeks = weekendsForMonth(month);
    if (!weeks.length) return;
    instagramState.month = month;
    instagramState.weekends = weeks;
    instagramState.selectedWeeks = new Set(weeks.map(week => week.start));
    instagramState.weekend = weeks.find(week => week.start === instagramState.weekend.start) || weeks[0];
    loadSelectedPeriod();
  }

  function setInstagramWeekend(start) {
    const week = instagramState.weekends.find(week => week.start === start);
    if (!week) return;
    instagramState.weekend = week;
    loadSelectedPeriod();
  }

  function toggleInstagramWeek(start, checked) {
    if (checked) instagramState.selectedWeeks.add(start);
    else instagramState.selectedWeeks.delete(start);
    renderSeriesOrder();
    rebuildSlides();
  }

  function rawSessionDate(session) {
    const value = session?._date || session?.date || session?.tbcDate ||
      (typeof session?.dateUTC === 'string' ? session.dateUTC.split('T')[0] : '');
    return /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value : null;
  }

  function rawSessionTime(session) {
    const value = session?._time || session?.timeLocal || session?.timeUTC ||
      (typeof session?.dateUTC === 'string' && session.dateUTC.includes('T')
        ? session.dateUTC.split('T')[1].slice(0, 5) : '');
    return /^([01]\d|2[0-3]):[0-5]\d$/.test(value || '') ? value : null;
  }

  function offsetAt(timestamp, timeZone) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).formatToParts(new Date(timestamp));
    const get = type => Number(parts.find(part => part.type === type)?.value);
    const hour = get('hour') === 24 ? 0 : get('hour');
    return Date.UTC(get('year'), get('month') - 1, get('day'), hour, get('minute'), get('second')) - timestamp;
  }

  function zonedWallTimeToUtc(dateKey, time, timeZone) {
    const [year, month, day] = dateKey.split('-').map(Number);
    const [hour, minute] = time.split(':').map(Number);
    const wall = Date.UTC(year, month - 1, day, hour, minute, 0);
    let result = wall - offsetAt(wall, timeZone);
    result = wall - offsetAt(result, timeZone);
    const date = new Date(result);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function sessionInstant(session, seriesId) {
    const date = rawSessionDate(session);
    const time = rawSessionTime(session);
    if (!date || !time || session?._tbcMode || session?.tbc === true) return null;
    if (session.timeUTC || (session.dateUTC && !session.timeLocal && !session._time)) {
      const instant = new Date(`${date}T${time}:00Z`);
      return Number.isNaN(instant.getTime()) ? null : instant;
    }
    return zonedWallTimeToUtc(date, time, sourceTimeZone(seriesId));
  }

  function sourceTimeZone(seriesId) {
    return window.RACEDAY_INSTAGRAM_TIME_ZONES?.editorTimeZones?.[seriesId] || SERIES_TIME_ZONES[seriesId] || DISPLAY_ZONE;
  }

  function eventTimeZone(round) {
    const explicit = round.timeZone || round.timezone;
    if (explicit) {
      try { new Intl.DateTimeFormat('en', { timeZone: explicit }); return explicit; } catch (_) { /* Try the circuit registry. */ }
    }
    const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const text = normalize(`${round.raceName || ''} ${round.circuitName || ''} ${round.city || ''}`);
    const matches = Object.entries({ ...ADDITIONAL_EVENT_TIME_ZONES, ...window.RACEDAY_INSTAGRAM_TIME_ZONES?.eventTimeZones })
      .filter(([name]) => text.includes(normalize(name)))
      .sort((a, b) => normalize(b[0]).length - normalize(a[0]).length);
    return matches[0]?.[1] || null;
  }

  function displayTimeZone(round, seriesId) {
    if (instagramState.timeZone === 'deviceLocal') return Intl.DateTimeFormat().resolvedOptions().timeZone || DISPLAY_ZONE;
    return instagramState.timeZone === 'eventLocal'
      ? eventTimeZone(round) || sourceTimeZone(seriesId)
      : instagramState.timeZone;
  }

  function sessionEndDateKey(session, seriesId, timeZone = DISPLAY_ZONE) {
    const instant = sessionInstant(session, seriesId);
    const duration = Number(session?.durationMinutes);
    if (!instant || !Number.isFinite(duration) || duration <= 0) return null;
    return localDateKey(new Date(instant.getTime() + duration * 60000), timeZone);
  }

  function localDateKey(date, timeZone = DISPLAY_ZONE) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(date);
  }

  function localTimeInfo(date, timeZone = DISPLAY_ZONE) {
    const time = new Intl.DateTimeFormat('en-GB', {
      timeZone, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
    const zoneName = new Intl.DateTimeFormat('en-GB', {
      timeZone, timeZoneName: 'short',
    }).formatToParts(date).find(part => part.type === 'timeZoneName')?.value || '';
    const amsterdam = timeZone === DISPLAY_ZONE;
    return { time, zone: amsterdam && /GMT\+2|CEST/i.test(zoneName) ? 'CEST' : amsterdam && /GMT\+1|CET/i.test(zoneName) ? 'CET' : zoneName };
  }

  function defaultEnabled(kind) {
    if (DEFAULT_ON.has(kind)) return true;
    if (DEFAULT_OFF.has(kind)) return false;
    return true; // Unknown kinds remain visible and selected, never silently lost.
  }

  function collectWeekendSessions(weekend = weekendRangeFor(), fullEvents = false) {
    const sessions = [];
    const warnings = [];
    (state.series || []).forEach(series => {
      (state.data[series.id] || []).forEach((round, roundIndex) => {
        const timeZone = displayTimeZone(round, series.id);
        const roundDayKeys = (round.sessions || []).flatMap(roundSession => {
          const roundInstant = sessionInstant(roundSession, series.id);
          const startDay = roundInstant ? localDateKey(roundInstant, timeZone) : rawSessionDate(roundSession);
          // Only long races extend the overview to their finishing day.
          const isLongRace = ['race', 'featureRace', 'sprintRace'].includes(roundSession.kind)
            && Number(roundSession.durationMinutes) > 300;
          const endDay = isLongRace ? sessionEndDateKey(roundSession, series.id, timeZone) : null;
          return endDay && endDay !== startDay ? [startDay, endDay] : [startDay];
        }).filter(Boolean).sort();
        const eventStart = roundDayKeys[0] || weekend.start;
        const eventEnd = roundDayKeys[roundDayKeys.length - 1] || eventStart;
        (round.sessions || []).forEach((session, sessionIndex) => {
          const date = rawSessionDate(session);
          if (!date) {
            warnings.push(`${series.name}: session without a valid date`);
            return;
          }
          const instant = sessionInstant(session, series.id);
          const isTbc = !instant;
          const dayKey = instant ? localDateKey(instant, timeZone) : date;
          if (fullEvents ? eventStart >= weekend.endExclusive || eventEnd < weekend.start : dayKey < weekend.start || dayKey >= weekend.endExclusive) return;
          const timeInfo = instant ? localTimeInfo(instant, timeZone) : { time: 'TIME TBC', zone: '' };
          const uid = `${series.id}:${round.id || roundIndex}:${session.id || sessionIndex}`;
          sessions.push({
            uid, seriesId: series.id, seriesName: series.name || formatSeriesName(series.id),
            round, session, roundIndex, sessionIndex, dayKey, instant, isTbc,
            time: timeInfo.time, zone: timeInfo.zone, kind: session.kind || 'unknown',
            timeZone, missingEventZone: instagramState.timeZone === 'eventLocal' && !eventTimeZone(round),
            countryCode: String(round.countryCode || '').trim().toUpperCase(),
            eventName: round.raceName || round.circuitName || round.city || 'Event name TBC',
            circuitName: round.circuitName || round.city || '',
            eventUid: `${series.id}:${round.id || roundIndex}`,
            eventStart, eventEnd,
            enabledByDefault: defaultEnabled(session.kind),
          });
        });
      });
    });
    sessions.sort((a, b) => a.dayKey.localeCompare(b.dayKey) ||
      ((a.instant?.getTime() ?? Number.MAX_SAFE_INTEGER) - (b.instant?.getTime() ?? Number.MAX_SAFE_INTEGER)) ||
      a.seriesName.localeCompare(b.seriesName));
    return { sessions, warnings };
  }

  // ── English label mapping ─────────────────────────────────────────────────

  function sessionLabel(item) {
    if (item.kind === 'race') return 'RACE';
    const base = LABELS[item.kind] || String(item.session.name || item.kind || 'SESSION').toUpperCase();
    const name = String(item.session.name || '').trim();
    const number = name.match(/(?:^|\s)(\d{1,2})(?:\s|$)/)?.[1];
    if (number && ['practice', 'qualifying', 'stage'].includes(item.kind)) return `${base} ${number}`;
    return compactLabel(base);
  }

  function compactLabel(value) {
    const cleaned = String(value || 'SESSION').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toUpperCase();
    return cleaned.length <= 21 ? cleaned : `${cleaned.slice(0, 18).trim()}…`;
  }

  function sessionTitle(item) {
    return item.kind === 'race' ? 'RACE' : String(item.session.name || '').trim() || sessionLabel(item);
  }

  // ── Slide distribution ────────────────────────────────────────────────────

  function hasMinimalHeader() {
    return !instagramState.showTitle && !instagramState.showDate && !instagramState.showTopMeta;
  }

  function contentTop() {
    if (instagramState.mode === 'seriesWeekend') return Math.max(200, 52 + seriesHeaderHeight());
    const base = hasMinimalHeader()
      ? MINIMAL_CONTENT_TOP
      : instagramState.showTopMeta ? DEFAULT_CONTENT_TOP : COMPACT_CONTENT_TOP;
    const top = instagramState.showLogo ? base : base - HIDDEN_LOGO_OFFSET;
    const content = showsTimes() ? Math.max(132, top) : top;
    return content + (showsTimes() && instagramState.timeZonePlacement === 'header' ? 58 : 0);
  }

  function showsTimes() {
    return !['overview', 'monthOverview', 'dayNoTimes'].includes(instagramState.mode);
  }

  function contentBottom() {
    return instagramState.showFooter ? CONTENT_BOTTOM : HEIGHT - 54;
  }

  function scheduleTop(totalHeight) {
    const remaining = Math.max(0, contentBottom() - contentTop() - totalHeight);
    const factor = instagramState.scheduleAlignment === 'top' ? 0 : instagramState.scheduleAlignment === 'bottom' ? 1 : .5;
    return contentTop() + remaining * factor;
  }

  function maxSessionsPerSlide() {
    const base = hasMinimalHeader()
      ? MINIMAL_MAX_SESSIONS_PER_SLIDE
      : instagramState.showTopMeta ? DEFAULT_MAX_SESSIONS_PER_SLIDE : COMPACT_MAX_SESSIONS_PER_SLIDE;
    return base + (instagramState.showLogo ? 0 : 1) + (instagramState.showFooter ? 0 : 1);
  }

  function headerDividerY() {
    const base = hasMinimalHeader() ? 132 : instagramState.showTopMeta ? 260 : 236;
    return instagramState.showLogo ? base : base - HIDDEN_LOGO_OFFSET;
  }

  function buildSlides(selected) {
    if (!selected.length) return [];
    const groups = [];
    selected.forEach(item => {
      const last = groups[groups.length - 1];
      if (last?.dayKey === item.dayKey) last.items.push(item);
      else groups.push({ dayKey: item.dayKey, items: [item], continuation: false });
    });
    const slides = [];
    let slide = { groups: [], count: 0, height: 0 };
    groups.forEach(group => {
      let itemIndex = 0;
      while (itemIndex < group.items.length) {
        const gap = slide.groups.length ? GROUP_GAP : 0;
        const availableHeight = contentBottom() - contentTop() - slide.height - gap - GROUP_HEADER_HEIGHT - GROUP_BOTTOM_PADDING;
        const availableCount = Math.min(
          maxSessionsPerSlide() - slide.count,
          Math.max(0, Math.floor(availableHeight / ROW_HEIGHT)),
        );
        if (!availableCount) {
          if (slide.count) slides.push(slide);
          slide = { groups: [], count: 0, height: 0 };
          continue;
        }
        const items = group.items.slice(itemIndex, itemIndex + availableCount);
        const chunk = { dayKey: group.dayKey, items, continuation: itemIndex > 0 };
        slide.groups.push(chunk);
        slide.count += items.length;
        slide.height += gap + GROUP_HEADER_HEIGHT + GROUP_BOTTOM_PADDING + items.length * ROW_HEIGHT;
        itemIndex += items.length;
        if (itemIndex < group.items.length) {
          slides.push(slide);
          slide = { groups: [], count: 0, height: 0 };
        }
      }
    });
    if (slide.count) slides.push(slide);
    return slides;
  }

  function buildOverviewItems(sessions) {
    const events = new Map();
    sessions.forEach(item => {
      if (!events.has(item.eventUid)) {
        events.set(item.eventUid, {
          ...item,
          uid: `event:${item.eventUid}`,
          overview: true,
          dateRange: formatCompactDateRange(item.eventStart, item.eventEnd),
        });
      }
    });
    return [...events.values()].sort((a, b) => a.eventStart.localeCompare(b.eventStart) || a.seriesName.localeCompare(b.seriesName));
  }

  function buildOverviewSlides(items, weekend = instagramState.weekend) {
    const slides = [];
    const capacity = Math.min(maxSessionsPerSlide(), Math.floor((contentBottom() - contentTop() - GROUP_HEADER_HEIGHT - GROUP_BOTTOM_PADDING) / OVERVIEW_ROW_HEIGHT));
    for (let index = 0; index < items.length; index += capacity) {
      slides.push({ groups: [{
        dayKey: weekend.start,
        weekend,
        items: items.slice(index, index + capacity),
        continuation: index > 0,
        overview: true,
      }], count: Math.min(capacity, items.length - index) });
    }
    return slides;
  }

  // ── Local logo and flag resolution ────────────────────────────────────────

  function loadImage(src) {
    if (!src) return Promise.resolve(null);
    if (instagramState.images.has(src)) return Promise.resolve(instagramState.images.get(src));
    return new Promise(resolve => {
      const image = new Image();
      image.onload = () => { instagramState.images.set(src, image); resolve(image); };
      image.onerror = () => resolve(null);
      image.src = src;
    });
  }

  function flagSrc(code) {
    const value = String(code || '').trim().toLowerCase();
    if (!/^[a-z]{2}$/.test(value)) return '';
    const bundledSvg = window.RACEDAY_INSTAGRAM_FLAG_SVGS?.[value];
    if (bundledSvg) {
      if (!FLAG_DATA_URL_CACHE.has(value)) {
        FLAG_DATA_URL_CACHE.set(value, `data:image/svg+xml;charset=utf-8,${encodeURIComponent(bundledSvg)}`);
      }
      return FLAG_DATA_URL_CACHE.get(value);
    }
    return `${FLAG_ROOT}/${value}.svg`;
  }

  function logoConfigFor(item) {
    const logos = window.RACEDAY_INSTAGRAM_LOGOS || {};
    if (logos[item.seriesId]) return logos[item.seriesId];
    // Custom series IDs are chosen when adding a series in the editor.
    const isFormulaDrift = [item.seriesId, item.seriesName].some(value =>
      /^(fd|formuladrift)$/.test(String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '')));
    return isFormulaDrift ? logos.formula_drift : undefined;
  }

  async function preloadAssets(items) {
    const configs = items.map(logoConfigFor).filter(Boolean);
    const flags = [...new Set(items.map(item => flagSrc(item.countryCode)).filter(Boolean))];
    const brandIcon = window.RACEDAY_INSTAGRAM_BRAND?.icon;
    await Promise.all([
      ...configs.map(config => loadImage(config.src)),
      ...flags.map(loadImage),
      loadImage(brandIcon),
      ...Object.values(STORE_BADGES).map(loadImage),
    ]);
  }

  function drawFlag(ctx, code, x, y, width = 32, height = 24) {
    ctx.save();
    roundedPath(ctx, x, y, width, height, 3);
    ctx.clip();
    const image = instagramState.images.get(flagSrc(code));
    if (!image) {
      ctx.fillStyle = '#3b3b43'; ctx.fillRect(x, y, width, height);
      ctx.fillStyle = '#a4a4ad'; ctx.font = '700 11px Inter, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(code || '—', x + width / 2, y + height / 2 + .5);
      ctx.restore();
      ctx.strokeStyle = '#62626c'; ctx.lineWidth = 1; roundedPath(ctx, x, y, width, height, 3); ctx.stroke();
      return false;
    }
    ctx.drawImage(image, x, y, width, height);
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,255,255,.24)'; ctx.lineWidth = 1; roundedPath(ctx, x, y, width, height, 3); ctx.stroke();
    return true;
  }

  // ── Fixed canvas template ─────────────────────────────────────────────────

  function roundedPath(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r); ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r); ctx.closePath();
  }

  function fillRoundRect(ctx, x, y, width, height, radius, fill) {
    roundedPath(ctx, x, y, width, height, radius); ctx.fillStyle = fill; ctx.fill();
  }

  function truncateText(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    let result = String(text);
    while (result.length && ctx.measureText(`${result}…`).width > maxWidth) result = result.slice(0, -1);
    return `${result.trim()}…`;
  }

  function formatHeaderDates(weekend) {
    const parse = value => new Date(`${value}T12:00:00Z`);
    const start = parse(weekend.start), end = parse(weekend.end);
    if (weekend.start === weekend.end) {
      return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(start);
    }
    const sameMonth = start.getUTCMonth() === end.getUTCMonth();
    const month = new Intl.DateTimeFormat('en-GB', { month: 'long', timeZone: 'UTC' }).format(start);
    if (sameMonth) return `${start.getUTCDate()}–${end.getUTCDate()} ${month} ${start.getUTCFullYear()}`;
    const startLabel = new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'long', timeZone:'UTC' }).format(start);
    const endLabel = new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'long', year:'numeric', timeZone:'UTC' }).format(end);
    return `${startLabel} – ${endLabel}`;
  }

  function formatCompactDateRange(startKey, endKey) {
    const parse = value => new Date(`${value}T12:00:00Z`);
    const start = parse(startKey), end = parse(endKey);
    const monthFormat = new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' });
    const startMonth = monthFormat.format(start), endMonth = monthFormat.format(end);
    if (startKey === endKey) return `${start.getUTCDate()} ${startMonth}`;
    if (start.getUTCFullYear() === end.getUTCFullYear() && start.getUTCMonth() === end.getUTCMonth()) {
      return `${start.getUTCDate()}–${end.getUTCDate()} ${endMonth}`;
    }
    return `${start.getUTCDate()} ${startMonth}–${end.getUTCDate()} ${endMonth}`;
  }

  function isoWeek(dateKey) {
    const [year, month, day] = dateKey.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    const weekday = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() + 4 - weekday);
    const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
    return Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
  }

  function dayHeading(dayKey) {
    const date = new Date(`${dayKey}T12:00:00Z`);
    return {
      day: new Intl.DateTimeFormat('en-GB', { weekday: 'long', timeZone: 'UTC' }).format(date),
      date: new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' }).format(date),
    };
  }

  function drawBackground(ctx) {
    const base = ctx.createLinearGradient(0, 0, WIDTH, HEIGHT);
    base.addColorStop(0, '#080405'); base.addColorStop(.34, '#050506');
    base.addColorStop(.72, '#060506'); base.addColorStop(1, '#100207');
    ctx.fillStyle = base; ctx.fillRect(0, 0, WIDTH, HEIGHT);

    ctx.save();
    ctx.globalAlpha = BACKGROUND_LAYER_OPACITY;
    ctx.globalCompositeOperation = 'soft-light';
    const colorWash = ctx.createLinearGradient(0, HEIGHT, WIDTH, 0);
    colorWash.addColorStop(0, 'rgba(92,0,15,.34)');
    colorWash.addColorStop(.46, 'rgba(24,8,11,.08)');
    colorWash.addColorStop(1, 'rgba(185,0,29,.48)');
    ctx.fillStyle = colorWash; ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.restore();

    ctx.save();
    ctx.globalAlpha = BACKGROUND_LAYER_OPACITY;
    ctx.globalCompositeOperation = 'screen';
    const topGlow = ctx.createRadialGradient(1060, 40, 20, 1060, 40, 720);
    topGlow.addColorStop(0, 'rgba(142,0,24,.34)'); topGlow.addColorStop(.48, 'rgba(92,0,16,.14)');
    topGlow.addColorStop(1, 'rgba(86,0,14,0)');
    ctx.fillStyle = topGlow; ctx.fillRect(290, 0, 790, 720);
    const bottomGlow = ctx.createRadialGradient(0, 1320, 10, 0, 1320, 720);
    bottomGlow.addColorStop(0, 'rgba(174,0,29,.42)');
    bottomGlow.addColorStop(.48, 'rgba(105,0,19,.18)');
    bottomGlow.addColorStop(1, 'rgba(80,0,13,0)');
    ctx.fillStyle = bottomGlow; ctx.fillRect(0, 660, 760, 690);
    ctx.restore();

    ctx.save();
    ctx.globalAlpha = BACKGROUND_LAYER_OPACITY;
    ctx.globalCompositeOperation = 'multiply';
    const vignette = ctx.createRadialGradient(WIDTH * .52, HEIGHT * .42, 260, WIDTH * .52, HEIGHT * .42, 880);
    vignette.addColorStop(0, 'rgba(0,0,0,0)');
    vignette.addColorStop(.7, 'rgba(0,0,0,.08)');
    vignette.addColorStop(1, 'rgba(0,0,0,.38)');
    ctx.fillStyle = vignette; ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.restore();
  }

  function drawBrandIcon(ctx, x, y, size, radius = 16) {
    const src = window.RACEDAY_INSTAGRAM_BRAND?.icon;
    const image = src ? instagramState.images.get(src) : null;
    ctx.save(); roundedPath(ctx, x, y, size, size, radius); ctx.clip();
    if (image) ctx.drawImage(image, x, y, size, size);
    else { ctx.fillStyle = '#8d0012'; ctx.fillRect(x, y, size, size); ctx.fillStyle = '#fff'; ctx.font = `750 ${size * .42}px Inter, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('R', x + size/2, y + size/2); }
    ctx.restore();
  }

  function drawClockIcon(ctx, centerX, centerY, size = 16) {
    ctx.save();
    ctx.strokeStyle = '#ff4054';
    ctx.lineWidth = 1.8;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.arc(centerX, centerY, size / 2, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.lineTo(centerX, centerY - size * .26);
    ctx.moveTo(centerX, centerY);
    ctx.lineTo(centerX + size * .21, centerY + size * .13);
    ctx.stroke();
    ctx.restore();
  }

  function drawHeader(ctx, slideNumber, totalSlides) {
    if (instagramState.mode === 'seriesWeekend') return;
    const headerRange = ['day', 'dayNoTimes'].includes(instagramState.mode)
      ? { start: instagramState.selectedDay, end: instagramState.selectedDay }
      : instagramState.slides[slideNumber - 1]?.groups[0]?.weekend || instagramState.weekend;
    ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
    if (instagramState.showLogo) {
      drawBrandIcon(ctx, 68, 56, 58, 15);
      ctx.fillStyle = '#ffffff'; ctx.font = '650 25px Inter, sans-serif';
      ctx.fillText('RaceDay', 143, 93);
    }
    if (instagramState.showTopMeta) {
      ctx.fillStyle = '#ff3045'; ctx.font = '650 18px Inter, sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(`Race week ${isoWeek(headerRange.start)}`, 1008, 81);
      if (totalSlides > 1) {
        ctx.fillStyle = '#7f7f87'; ctx.font = '550 16px Inter, sans-serif';
        ctx.fillText(`${slideNumber} of ${totalSlides}`, 1008, 111);
      }
    }
    ctx.textAlign = instagramState.titleAlignment; ctx.fillStyle = '#ffffff';
    const titleX = instagramState.titleAlignment === 'center' ? 540 : instagramState.titleAlignment === 'right' ? 1012 : 68;
    const postTitle = instagramState.title || 'Upcoming races';
    const compactOffset = instagramState.showLogo ? 0 : HIDDEN_LOGO_OFFSET;
    let titleSize = 64;
    ctx.font = `700 ${titleSize}px Inter, sans-serif`;
    while (titleSize > 44 && ctx.measureText(postTitle).width > 720) {
      titleSize -= 1;
      ctx.font = `700 ${titleSize}px Inter, sans-serif`;
    }
    if (instagramState.showTitle) ctx.fillText(truncateText(ctx, postTitle, 720), titleX, 185 - compactOffset);
    if (instagramState.showDate) {
      ctx.fillStyle = '#a6a6ad'; ctx.font = '500 22px Inter, sans-serif';
      ctx.fillText(formatHeaderDates(headerRange), titleX, 226 - compactOffset);
    }
    ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fillRect(68, headerDividerY(), 944, 1);
  }

  // Alpha compositing also works in iOS Safari, where canvas filters may be unavailable.
  const whiteLogoCache = new Map();
  function whiteLogo(image) {
    if (whiteLogoCache.has(image)) return whiteLogoCache.get(image);
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth || image.width;
    canvas.height = image.naturalHeight || image.height;
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0);
    context.globalCompositeOperation = 'source-in';
    context.fillStyle = '#fff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    whiteLogoCache.set(image, canvas);
    return canvas;
  }

  function drawLogo(ctx, item, x, y, width, height) {
    const config = logoConfigFor(item);
    const image = config ? instagramState.images.get(config.src) : null;
    ctx.save();
    ctx.beginPath(); ctx.rect(x, y, width, height); ctx.clip();
    if (image) {
      const size = Math.min(config.maxWidth || width, width) * (config.scale || 1) * logoScaleFor(item.seriesId);
      const logo = config.mono ? whiteLogo(image) : image;
      ctx.globalAlpha = .95;
      ctx.drawImage(logo, x + width / 2 - size / 2 + (config.x || 0), y + height / 2 - size / 2 + (config.y || 0), size, size);
    } else {
      ctx.fillStyle = '#f4f4f6'; ctx.font = '700 19px Inter, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(truncateText(ctx, item.seriesName.toUpperCase(), width - 14), x + width / 2, y + height / 2);
    }
    ctx.restore(); ctx.filter = 'none'; ctx.globalAlpha = 1;
  }

  function drawSessionRow(ctx, item, y, rowHeight) {
    const x = 52, width = 976;
    const rowX = x + 20, rowWidth = width - 40;
    fillRoundRect(ctx, rowX, y + 6, rowWidth, rowHeight - 12, PANEL_RADIUS, '#202023');
    const logoW = 122, logoH = 60, logoX = rowX + 20, logoY = y + (rowHeight - logoH) / 2;
    if (instagramState.showRowLogos) drawLogo(ctx, item, logoX, logoY, logoW, logoH);
    const copyX = rowX + (instagramState.showRowLogos ? 162 : 20);
    const extraCopyWidth = instagramState.showRowLogos ? 0 : 142;
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    const textOffset = (rowHeight - ROW_HEIGHT) / 2;
    const seriesWeekend = instagramState.mode === 'seriesWeekend';
    ctx.fillStyle = '#f7f7f8'; ctx.font = `650 ${item.overview ? 28 : 24}px Inter, sans-serif`;
    // Reserve the flag and gap before the fixed date/session column.
    const eventTitle = truncateText(ctx, seriesWeekend ? sessionTitle(item) : item.eventName, (seriesWeekend ? 570 : item.overview ? 542 : 338) + extraCopyWidth);
    ctx.fillText(eventTitle, copyX, y + 44 + textOffset);
    if (!seriesWeekend) drawFlag(ctx, item.countryCode, copyX + ctx.measureText(eventTitle).width + 12, y + 23 + textOffset, 32, 24);
    ctx.fillStyle = '#b5b5bd'; ctx.font = `500 ${item.overview ? 20 : 18}px Inter, sans-serif`;
    const subline = item.circuitName && item.circuitName !== item.eventName ? `${item.seriesName} · ${item.circuitName}` : item.seriesName;
    ctx.fillText(truncateText(ctx, subline, (seriesWeekend ? 570 : item.overview ? 580 : 390) + extraCopyWidth), copyX, y + 72 + textOffset);
    const label = item.overview ? '' : sessionLabel(item), labelX = x + 592, labelW = 178, labelH = 48;
    const labelY = y + (rowHeight - labelH) / 2;
    const dayWithoutTimes = instagramState.mode === 'dayNoTimes';
    if (!item.overview && !dayWithoutTimes && !seriesWeekend) {
      fillRoundRect(ctx, labelX, labelY, labelW, labelH, PANEL_RADIUS, 'rgba(255,255,255,.018)');
      ctx.strokeStyle = 'rgba(255,255,255,.22)'; ctx.lineWidth = 1.5;
      roundedPath(ctx, labelX + .75, labelY + .75, labelW - 1.5, labelH - 1.5, PANEL_RADIUS - .75); ctx.stroke();
      let labelFontSize = label.length > 17 ? 14 : 16;
      ctx.font = `650 ${labelFontSize}px Inter, sans-serif`;
      while (labelFontSize > 12 && ctx.measureText(label).width > labelW - 22) {
        labelFontSize -= 1; ctx.font = `650 ${labelFontSize}px Inter, sans-serif`;
      }
      ctx.fillStyle = '#b8b8bf'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(label, labelX + labelW/2, y + rowHeight/2 + 1);
    }
    const timeX = x + 790, timeW = 146;
    fillRoundRect(ctx, timeX, labelY, timeW, labelH, PANEL_RADIUS, '#000000');
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const rightLabel = item.overview ? item.dateRange : dayWithoutTimes ? label : item.time;
    let rightFontSize = item.overview ? 22 : dayWithoutTimes ? 15 : item.isTbc ? 17 : 28;
    ctx.font = `${dayWithoutTimes ? 650 : 700} ${rightFontSize}px Inter, sans-serif`;
    while (rightFontSize > 11 && ctx.measureText(rightLabel).width > timeW - 18) {
      rightFontSize -= 1;
      ctx.font = `${dayWithoutTimes ? 650 : 700} ${rightFontSize}px Inter, sans-serif`;
    }
    ctx.fillText(rightLabel, timeX + timeW/2, y + rowHeight / 2 + 1);
  }

  const logoInkBounds = new WeakMap();
  function inkBounds(image) {
    if (logoInkBounds.has(image)) return logoInkBounds.get(image);
    const canvas = document.createElement('canvas');
    const width = image.naturalWidth || image.width, height = image.naturalHeight || image.height;
    const rasterScale = Math.max(1, 512 / Math.max(width, height));
    canvas.width = Math.round(width * rasterScale);
    canvas.height = Math.round(height * rasterScale);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    let bounds = { x: 0, y: 0, width: canvas.width, height: canvas.height };
    try {
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let left = canvas.width, right = -1, top = canvas.height, bottom = -1;
      for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
        if (pixels[(y * canvas.width + x) * 4 + 3] > 8) {
          left = Math.min(left, x); right = Math.max(right, x);
          top = Math.min(top, y); bottom = Math.max(bottom, y);
        }
      }
      if (right >= left) bounds = { x: left, y: top, width: right - left + 1, height: bottom - top + 1 };
    } catch (_) { /* Keep the original image bounds for an external asset. */ }
    bounds.bitmap = canvas;
    logoInkBounds.set(image, bounds);
    return bounds;
  }

  function eventLogoSize(item) {
    const config = item ? logoConfigFor(item) : null;
    const image = config ? instagramState.images.get(config.src) : null;
    const multiplier = instagramState.headerLogoScale;
    if (!image) return { width: 132 * multiplier, height: 76 * multiplier };
    const bounds = inkBounds(image);
    const scale = Math.min(132 / bounds.width, 76 / bounds.height) * multiplier;
    return { width: bounds.width * scale, height: bounds.height * scale, config, image, bounds };
  }

  function seriesHeaderHeight() {
    const item = instagramState.displayItems.find(item => instagramState.selectedIds.has(item.uid)) || instagramState.displayItems[0];
    return Math.max(138, eventLogoSize(item).height + 32);
  }

  function drawEventLogo(ctx, item, centerY) {
    const size = eventLogoSize(item);
    if (!size.image) {
      drawLogo(ctx, item, 52, centerY - size.height / 2, size.width, size.height);
    } else {
      const { config, image, bounds, width, height } = size;
      // Crop asset padding so the visible mark stays on the panel's left edge.
      ctx.drawImage(config.mono ? whiteLogo(bounds.bitmap) : bounds.bitmap, bounds.x, bounds.y, bounds.width, bounds.height,
        52, centerY - height / 2, width, height);
    }
    return size.width;
  }

  function timeZoneText(items) {
    const zones = [...new Set(items.filter(item => !item.isTbc && item.zone).map(item => item.zone))];
    const first = items[0];
    const zone = zones.join(' / ') || localTimeInfo(new Date(`${first?.dayKey || instagramState.weekend.start}T12:00:00Z`), first?.timeZone || DISPLAY_ZONE).zone;
    const local = instagramState.timeZone === 'deviceLocal' ||
      (instagramState.timeZone === 'eventLocal' && items.every(item => !item.missingEventZone));
    return local ? `Local time (${zone})` : `All times are ${zone}`;
  }

  function timeZoneBadgeWidth(ctx, items) {
    ctx.font = '550 16px Inter, sans-serif';
    return Math.min(420, Math.ceil(ctx.measureText(timeZoneText(items)).width + 49));
  }

  function drawSeriesEventHeader(ctx, item, items, y) {
    ctx.save();
    const centerY = y + (seriesHeaderHeight() - 32) / 2;
    const logoWidth = drawEventLogo(ctx, item, centerY);
    const headerBadge = instagramState.timeZonePlacement === 'header';
    const badgeWidth = headerBadge ? timeZoneBadgeWidth(ctx, items) : 0;
    const left = 52 + logoWidth + 30, right = 1028 - (headerBadge ? badgeWidth + 24 : 0), width = right - left;
    const flagWidth = 40, flagHeight = 30, flagGap = 12, flagSpace = flagWidth + flagGap;
    ctx.fillStyle = '#fff'; ctx.font = '700 34px Inter, sans-serif';
    ctx.textBaseline = 'middle';
    const words = item.eventName.split(/\s+/);
    let first = '';
    while (words.length && ctx.measureText(`${first} ${words[0]}`.trim()).width <= width - flagSpace) {
      first = `${first} ${words.shift()}`.trim();
    }
    if (!first) first = words.shift();
    const lines = [truncateText(ctx, first, width - flagSpace)];
    if (words.length) lines.push(truncateText(ctx, words.join(' '), width - flagSpace));
    const firstY = centerY - (lines.length > 1 ? 34 : 15);
    ctx.textAlign = instagramState.titleAlignment;
    const alignment = ctx.textAlign;
    lines.forEach((line, index) => {
      const isLast = index === lines.length - 1;
      const reserve = isLast ? flagSpace : 0;
      const textWidth = ctx.measureText(line).width;
      const x = alignment === 'center' ? (left + right - reserve) / 2 : alignment === 'right' ? right - reserve : left;
      const lineY = firstY + index * 38;
      ctx.fillText(line, x, lineY);
      if (isLast) {
        const textEnd = alignment === 'center' ? x + textWidth / 2 : alignment === 'right' ? x : x + textWidth;
        drawFlag(ctx, item.countryCode, textEnd + flagGap, lineY - flagHeight / 2, flagWidth, flagHeight);
      }
    });
    ctx.fillStyle = '#b5b5bd'; ctx.font = '500 21px Inter, sans-serif';
    const x = alignment === 'center' ? (left + right) / 2 : alignment === 'right' ? right : left;
    const range = { start: item.eventStart, end: item.eventEnd };
    ctx.fillText(truncateText(ctx, formatHeaderDates(range), width), x, firstY + (lines.length - 1) * 38 + 30);
    if (headerBadge) drawTimeZoneBadge(ctx, items, 1028, firstY);
    ctx.restore();
  }

  function drawTimeZoneBadge(ctx, items, right, centerY) {
    ctx.save();
    const text = timeZoneText(items);
    const height = 36, padding = 12, iconSize = 16, gap = 9;
    const width = timeZoneBadgeWidth(ctx, items);
    const x = right - width, y = centerY - height / 2;
    fillRoundRect(ctx, x, y, width, height, PANEL_RADIUS, '#202023');
    ctx.strokeStyle = 'rgba(255,255,255,.14)'; ctx.lineWidth = 1;
    roundedPath(ctx, x + .5, y + .5, width - 1, height - 1, PANEL_RADIUS - .5); ctx.stroke();
    drawClockIcon(ctx, x + padding + iconSize / 2, centerY, iconSize);
    let size = 16;
    while (size > 11 && ctx.measureText(text).width > width - 49) {
      ctx.font = `550 ${--size}px Inter, sans-serif`;
    }
    ctx.fillStyle = '#c5c5cb'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(truncateText(ctx, text, width - 49), right - padding, centerY + .5);
    ctx.restore();
  }

  function drawDayGroup(ctx, group, y, rowHeight) {
    const x = 52, width = 976, headerHeight = 64, bottomPadding = 14;
    const totalHeight = headerHeight + group.items.length * rowHeight + bottomPadding;
    const contentX = x + 20, contentWidth = width - 40;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.28)'; ctx.shadowBlur = 22; ctx.shadowOffsetY = 9;
    const panelGradient = ctx.createLinearGradient(x, y, x, y + totalHeight);
    panelGradient.addColorStop(0, 'rgba(29,26,29,.94)');
    panelGradient.addColorStop(.16, 'rgba(21,19,22,.93)');
    panelGradient.addColorStop(.62, 'rgba(12,11,14,.91)');
    panelGradient.addColorStop(1, 'rgba(8,7,10,.88)');
    fillRoundRect(ctx, x, y, width, totalHeight, PANEL_RADIUS, panelGradient);
    ctx.restore();
    const insideBorder = ctx.createLinearGradient(x, y, x + width, y + totalHeight);
    insideBorder.addColorStop(0, 'rgba(255,255,255,.17)');
    insideBorder.addColorStop(.48, 'rgba(255,255,255,.085)');
    insideBorder.addColorStop(1, 'rgba(174,62,76,.12)');
    ctx.strokeStyle = insideBorder; ctx.lineWidth = 1.25;
    roundedPath(ctx, x + .75, y + .75, width - 1.5, totalHeight - 1.5, PANEL_RADIUS - .75); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,.025)'; ctx.lineWidth = 1;
    roundedPath(ctx, x + 2.25, y + 2.25, width - 4.5, totalHeight - 4.5, PANEL_RADIUS - 2.25); ctx.stroke();
    const heading = dayHeading(group.dayKey);
    ctx.fillStyle = '#f7f7f8'; ctx.font = '650 24px Inter, sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    const monthLabel = new Intl.DateTimeFormat('en-GB', { month: 'long', timeZone: 'UTC' }).format(new Date(`${group.dayKey}T12:00:00Z`)).toUpperCase();
    const dayLabel = `${group.overview ? monthLabel : heading.day.toUpperCase()}${group.continuation ? ' · CONTINUED' : ''}`;
    const headingX = contentX;
    const headingY = y + 35;
    ctx.fillText(dayLabel, headingX, headingY);
    const dateX = group.overview ? contentX + contentWidth : headingX + ctx.measureText(dayLabel).width + 20;
    ctx.fillStyle = '#929299'; ctx.font = '550 20px Inter, sans-serif'; ctx.textAlign = group.overview ? 'right' : 'left';
    ctx.fillText(group.overview ? `Week ${isoWeek(group.dayKey)}` : heading.date.toUpperCase(), dateX, headingY);
    if (!group.overview && showsTimes() && instagramState.timeZonePlacement === 'groups') drawTimeZoneBadge(ctx, group.items, contentX + contentWidth, headingY);
    let rowY = y + headerHeight;
    group.items.forEach(item => { drawSessionRow(ctx, item, rowY, rowHeight); rowY += rowHeight; });
    return rowY + bottomPadding;
  }

  function drawFooter(ctx, slideNumber, totalSlides) {
    ctx.fillStyle = 'rgba(255,255,255,.16)'; ctx.fillRect(68, 1200, 944, 1);
    drawBrandIcon(ctx, 68, 1228, 64, 16);
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#ffffff'; ctx.font = '700 32px Inter, sans-serif';
    ctx.fillText('Download the RaceDay App!', 150, 1254);
    ctx.fillStyle = '#c4c4cb'; ctx.font = '500 23px Inter, sans-serif';
    ctx.fillText('Your Personal Racing Calendar.', 150, 1290);
    drawStoreBadge(ctx, 652, 1240, 174, 50, 'apple');
    drawStoreBadge(ctx, 838, 1240, 173, 50, 'google');
    if (totalSlides > 1) {
      ctx.fillStyle = '#b5b5bd'; ctx.font = '550 18px Inter, sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(`${slideNumber} / ${totalSlides}`, 1012, 1324);
    }
  }

  function drawStoreBadge(ctx, x, y, width, height, store) {
    const image = instagramState.images.get(STORE_BADGES[store]);
    if (image) ctx.drawImage(image, x, y, width, height);
  }

  function renderSlide(index = instagramState.slideIndex) {
    const canvas = document.getElementById('instagramCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.clearRect(0, 0, WIDTH, HEIGHT); drawBackground(ctx);
    const slide = instagramState.slides[index];
    const totalSlides = Math.max(1, instagramState.slides.length);
    drawHeader(ctx, index + 1, totalSlides);
    if (!slide) {
      ctx.fillStyle = '#fff';ctx.font='650 38px Inter, sans-serif';ctx.textAlign='center';ctx.fillText('No sessions selected', WIDTH/2, 650);
      ctx.fillStyle='#92929d';ctx.font='500 22px Inter, sans-serif';ctx.fillText('Select at least one session to create a post.', WIDTH/2, 692);
    } else {
      const rowHeights = slide.groups.map(group => group.overview ? OVERVIEW_ROW_HEIGHT : ROW_HEIGHT);
      const totalHeight = slide.groups.reduce((height, group, index) => height
        + GROUP_HEADER_HEIGHT + group.items.length * rowHeights[index] + GROUP_BOTTOM_PADDING
        + (index ? GROUP_GAP : 0), 0);
      let y = scheduleTop(totalHeight);
      const items = slide.groups.flatMap(group => group.items);
      if (instagramState.mode === 'seriesWeekend') drawSeriesEventHeader(ctx, items[0], items, y - seriesHeaderHeight());
      else if (showsTimes() && instagramState.timeZonePlacement === 'header') drawTimeZoneBadge(ctx, items, 1028, contentTop() - 30);
      slide.groups.forEach((group, index) => {
        if (index) y += GROUP_GAP;
        y = drawDayGroup(ctx, group, y, rowHeights[index]);
      });
    }
    if (instagramState.showFooter) drawFooter(ctx, index + 1, totalSlides);
    updateNavigation();
  }

  // ── Preview UI and PNG export ─────────────────────────────────────────────

  function orderedItems(items) {
    const order = new Map(instagramState.seriesOrder.map((id, index) => [id, index]));
    return [...items].sort((a, b) => {
      if (instagramState.mode === 'monthOverview') {
        const difference = a.weekStart.localeCompare(b.weekStart);
        if (difference) return difference;
      }
      if (!['overview', 'monthOverview'].includes(instagramState.mode)) {
        const dayDifference = a.dayKey.localeCompare(b.dayKey);
        if (dayDifference) return dayDifference;
      }
      // Schedules that show times must be chronological across all series.
      // Sessions without a confirmed time stay at the end of their day.
      if (!['overview', 'monthOverview', 'dayNoTimes'].includes(instagramState.mode)) {
        const timeDifference = (a.instant?.getTime() ?? Number.MAX_SAFE_INTEGER) -
          (b.instant?.getTime() ?? Number.MAX_SAFE_INTEGER);
        if (timeDifference) return timeDifference;
      }
      const seriesDifference = (order.get(a.seriesId) ?? 9999) - (order.get(b.seriesId) ?? 9999);
      if (seriesDifference) return seriesDifference;
      if (['overview', 'monthOverview'].includes(instagramState.mode)) {
        const eventDifference = a.eventStart.localeCompare(b.eventStart);
        if (eventDifference) return eventDifference;
      }
      return a.eventName.localeCompare(b.eventName) || a.uid.localeCompare(b.uid);
    });
  }

  function selectedSeriesOrder() {
    const selectedSeries = new Set(instagramState.displayItems
      .filter(item => instagramState.selectedIds.has(item.uid) && (instagramState.mode !== 'monthOverview' || instagramState.selectedWeeks.has(item.weekStart)))
      .map(item => item.seriesId));
    return instagramState.seriesOrder.filter(id => selectedSeries.has(id));
  }

  function applyVisibleSeriesOrder(ids) {
    const visible = new Set(ids);
    let next = 0;
    instagramState.seriesOrder = instagramState.seriesOrder.map(id => visible.has(id) ? ids[next++] : id);
  }

  function renderSeriesOrder() {
    const list = document.getElementById('instagramSeriesOrder');
    if (!list) return;
    const names = new Map(instagramState.allSessions.map(item => [item.seriesId, item.seriesName]));
    const ids = selectedSeriesOrder().filter(id => names.has(id));
    list.innerHTML = ids.map((id, index) => `<div class="instagram-series-order-item" data-series-id="${esc(id)}"
        ondragover="event.preventDefault()" ondrop="dropInstagramSeries(event, this.dataset.seriesId)">
      <div class="instagram-series-order-main">
        <span class="instagram-series-order-handle" draggable="${editorDragEnabled()}" aria-label="Sleep ${esc(names.get(id))}"
          ondragstart="startInstagramSeriesDrag(event, this.closest('[data-series-id]').dataset.seriesId)"
          ondragend="endInstagramSeriesDrag(event)">••</span>
        <span class="instagram-series-order-name">${esc(names.get(id))}</span>
        <button type="button" ${index === 0 ? 'disabled' : ''} onclick="moveInstagramSeries(this.closest('[data-series-id]').dataset.seriesId, -1)" aria-label="Verplaats ${esc(names.get(id))} omhoog">↑</button>
        <button type="button" ${index === ids.length - 1 ? 'disabled' : ''} onclick="moveInstagramSeries(this.closest('[data-series-id]').dataset.seriesId, 1)" aria-label="Verplaats ${esc(names.get(id))} omlaag">↓</button>
      </div>
    </div>`).join('');
  }

  function renderLogoScaleControls() {
    const list = document.getElementById('instagramLogoScaleList');
    if (!list) return;
    const names = new Map(instagramState.allSessions.map(item => [item.seriesId, item.seriesName]));
    list.innerHTML = instagramState.seriesOrder.filter(id => names.has(id)).map(id => {
      const scalePercent = Math.round(logoScaleFor(id) * 100);
      return `<div class="instagram-logo-scale-item" data-series-id="${esc(id)}">
        <div class="instagram-logo-scale-title">
          <strong>${esc(names.get(id))}</strong>
          <output>${scalePercent}%</output>
        </div>
        <div class="instagram-logo-scale-control">
          <input type="range" min="45" max="125" step="5" value="${scalePercent}" data-series-id="${esc(id)}"
            oninput="setInstagramLogoScale(this.dataset.seriesId, this.value, this)" aria-label="Logogrootte ${esc(names.get(id))}">
          <button type="button" onclick="resetInstagramLogoScale(this.closest('[data-series-id]').dataset.seriesId)" aria-label="Herstel logogrootte ${esc(names.get(id))}" title="Herstel naar 100%">↺</button>
        </div>
      </div>`;
    }).join('');
  }

  function applySeriesOrderChange() {
    renderSeriesOrder();
    renderLogoScaleControls();
    renderSessionControls();
    rebuildSlides();
  }

  function moveInstagramSeries(seriesId, direction) {
    const ids = selectedSeriesOrder();
    const index = ids.indexOf(seriesId);
    const next = index + Number(direction);
    if (index < 0 || next < 0 || next >= ids.length) return;
    [ids[index], ids[next]] = [ids[next], ids[index]];
    applyVisibleSeriesOrder(ids);
    applySeriesOrderChange();
  }

  function setInstagramLogoScale(seriesId, percent, input) {
    const scale = Math.max(.45, Math.min(1.25, Number(percent) / 100));
    if (!Number.isFinite(scale)) return;
    if (Math.abs(scale - 1) < .001) delete instagramState.logoScales[seriesId];
    else instagramState.logoScales[seriesId] = scale;
    saveLogoScales();
    const output = input?.closest('.instagram-logo-scale-item')?.querySelector('output');
    if (output) output.value = `${Math.round(scale * 100)}%`;
    renderSlide();
  }

  function resetInstagramLogoScale(seriesId) {
    delete instagramState.logoScales[seriesId];
    saveLogoScales();
    renderLogoScaleControls();
    renderSlide();
  }

  function setInstagramControlTab(tab) {
    instagramState.controlTab = tab === 'logos' ? 'logos' : 'sessions';
    const showLogos = instagramState.controlTab === 'logos';
    document.getElementById('instagramSessionsPanel')?.classList.toggle('active', !showLogos);
    document.getElementById('instagramLogosPanel')?.classList.toggle('active', showLogos);
    const sessionsTab = document.getElementById('instagramSessionsTab');
    const logosTab = document.getElementById('instagramLogosTab');
    sessionsTab?.classList.toggle('active', !showLogos);
    logosTab?.classList.toggle('active', showLogos);
    sessionsTab?.setAttribute('aria-selected', String(!showLogos));
    logosTab?.setAttribute('aria-selected', String(showLogos));
  }

  function startInstagramSeriesDrag(event, seriesId) {
    if (!editorDragEnabled()) { event.preventDefault(); return; }
    instagramState.draggedSeriesId = seriesId;
    event.currentTarget?.closest('.instagram-series-order-item')?.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', seriesId);
  }

  function endInstagramSeriesDrag(event) {
    event.currentTarget?.closest('.instagram-series-order-item')?.classList.remove('dragging');
    instagramState.draggedSeriesId = '';
  }

  function dropInstagramSeries(event, targetId) {
    event.preventDefault();
    const sourceId = instagramState.draggedSeriesId || event.dataTransfer.getData('text/plain');
    if (!sourceId || sourceId === targetId) return;
    const ids = selectedSeriesOrder();
    const sourceIndex = ids.indexOf(sourceId);
    const targetIndex = ids.indexOf(targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;
    ids.splice(sourceIndex, 1);
    ids.splice(targetIndex, 0, sourceId);
    applyVisibleSeriesOrder(ids);
    instagramState.draggedSeriesId = '';
    applySeriesOrderChange();
  }

  function rebuildWarnings() {
    const selected = instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid) && (instagramState.mode !== 'monthOverview' || instagramState.selectedWeeks.has(item.weekStart)));
    const missingLogos = [...new Set(selected.filter(item => {
      const config = logoConfigFor(item);
      return !config || (instagramState.assetLoadComplete && !instagramState.images.has(config.src));
    }).map(item => item.seriesName))];
    const selectedFlagSources = [...new Set(selected.map(item => flagSrc(item.countryCode)).filter(Boolean))];
    const loadedFlagCount = selectedFlagSources.filter(src => instagramState.images.has(src)).length;
    const missingCountries = selected.filter(item => {
      const src = flagSrc(item.countryCode);
      return !src || (instagramState.assetLoadComplete && !instagramState.images.has(src));
    }).length;
    const messages = [];
    if (!instagramState.allSessions.length) messages.push('Geen sessies gevonden voor deze periode. Synchroniseer eerst de kalender.');
    if (instagramState.sourceWarningCount) messages.push(`${instagramState.sourceWarningCount} sessie(s) zonder geldige datum zijn overgeslagen.`);
    const missingEventZones = [...new Set(selected.filter(item => item.missingEventZone).map(item => item.eventName))];
    if (missingEventZones.length) messages.push(`Circuittijdzone onbekend voor ${missingEventZones.join(', ')}. Voor deze events wordt de opgeslagen kalendertijdzone gebruikt; kies eventueel zelf de juiste tijdzone.`);
    if (missingLogos.length) messages.push(`Tekstfallback voor ontbrekend logo: ${missingLogos.join(', ')}.`);
    if (instagramState.assetLoadComplete && selectedFlagSources.length && loadedFlagCount === 0) {
      messages.push('De lokale vlagassets ontbreken. Upload instagram-assets/flag-bundle.js naar GitHub.');
    } else if (missingCountries) {
      messages.push(`${missingCountries} sessie(s) gebruiken een neutrale vlagfallback.`);
    }
    instagramState.warnings = messages;
    const warning = document.getElementById('instagramWarning');
    if (warning) { warning.textContent = messages.join(' '); warning.classList.toggle('show', Boolean(messages.length)); }
  }

  function renderSessionControls() {
    const list = document.getElementById('instagramSessionList');
    if (!list) return;
    if (!instagramState.displayItems.length) { list.innerHTML = '<div class="empty compact"><h3>Geen gegevens</h3><p>Voor deze keuze zijn geen races gevonden.</p></div>'; return; }
    let previousDay = '';
    list.innerHTML = orderedItems(instagramState.displayItems).map(item => {
      const heading = dayHeading(item.dayKey);
      const day = instagramState.mode === 'monthOverview' ? (previousDay !== item.weekStart ? `<div class="instagram-day-label">${esc(formatCompactDateRange(item.weekStart, addUtcDays(item.weekStart, 2)))} · Week ${isoWeek(item.weekStart)}</div>` : '') : !['overview', 'monthOverview'].includes(instagramState.mode) && previousDay !== item.dayKey ? `<div class="instagram-day-label">${heading.day} · ${heading.date}</div>` : '';
      previousDay = instagramState.mode === 'monthOverview' ? item.weekStart : item.dayKey;
      const primary = item.overview ? item.eventName : instagramState.mode === 'seriesWeekend' ? sessionTitle(item) : `${item.eventName} · ${sessionLabel(item)}`;
      const secondary = item.overview ? item.seriesName : item.seriesName;
      const trailing = item.overview ? item.dateRange : instagramState.mode === 'dayNoTimes' ? sessionLabel(item) : item.time;
      return `${day}<label class="instagram-session-toggle">
        <input type="checkbox" data-instagram-uid="${esc(item.uid)}" ${instagramState.selectedIds.has(item.uid) ? 'checked' : ''} onchange="toggleInstagramSession(this.dataset.instagramUid, this.checked)">
        <span class="instagram-session-copy"><strong>${esc(primary)}</strong><span>${esc(secondary)}</span></span>
        <span class="instagram-session-time">${esc(trailing)}</span>
      </label>`;
    }).join('');
  }

  function rebuildSlides() {
    const selected = orderedItems(instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid) && (instagramState.mode !== 'monthOverview' || instagramState.selectedWeeks.has(item.weekStart))));
    instagramState.slides = instagramState.mode === 'monthOverview'
      ? instagramState.weekends.filter(week => instagramState.selectedWeeks.has(week.start)).flatMap(week => buildOverviewSlides(selected.filter(item => item.weekStart === week.start), week))
      : instagramState.mode === 'overview' ? buildOverviewSlides(selected) : buildSlides(selected);
    instagramState.slideIndex = Math.min(instagramState.slideIndex, Math.max(0, instagramState.slides.length - 1));
    rebuildWarnings(); renderSlide();
    const selectedLabel = document.getElementById('instagramSelectionSummary');
    const noun = ['overview', 'monthOverview'].includes(instagramState.mode) ? 'events' : 'sessies';
    if (selectedLabel) selectedLabel.textContent = `${selected.length} van ${instagramState.displayItems.length} ${noun} geselecteerd · ${Math.max(1, instagramState.slides.length)} slide${instagramState.slides.length === 1 ? '' : 's'}`;
  }

  function refreshDisplayItems() {
    if (['overview', 'monthOverview'].includes(instagramState.mode)) {
      instagramState.displayItems = instagramState.mode === 'monthOverview'
        ? instagramState.weekends.flatMap(week => buildOverviewItems(instagramState.allSessions.filter(item => item.dayKey >= week.start && item.dayKey < week.endExclusive)).map(item => ({ ...item, uid: `${week.start}:${item.uid}`, weekStart: week.start })))
        : buildOverviewItems(instagramState.allSessions);
      instagramState.selectedIds = new Set(instagramState.displayItems.map(item => item.uid));
    } else {
      instagramState.displayItems = instagramState.mode === 'seriesWeekend'
        ? instagramState.allSessions.filter(item => item.seriesId === instagramState.selectedSeries && item.eventUid === instagramState.selectedEvent)
        : ['day', 'dayNoTimes'].includes(instagramState.mode)
        ? instagramState.allSessions.filter(item => item.dayKey === instagramState.selectedDay)
        : instagramState.allSessions;
      instagramState.selectedIds = new Set(instagramState.displayItems.filter(item => instagramState.mode === 'seriesWeekend' || item.enabledByDefault).map(item => item.uid));
    }
    instagramState.slideIndex = 0;
    const formatControls = document.querySelector('.instagram-format-controls');
    formatControls?.classList.toggle('day-mode', ['day', 'dayNoTimes'].includes(instagramState.mode));
    document.getElementById('instagramGeneralHeaderControls')?.toggleAttribute('hidden', instagramState.mode === 'seriesWeekend');
    document.querySelector('.instagram-series-order-wrap')?.toggleAttribute('hidden', instagramState.mode === 'seriesWeekend');
    document.getElementById('instagramTimeZoneControls')?.toggleAttribute('hidden', ['overview', 'monthOverview', 'dayNoTimes'].includes(instagramState.mode));
    renderSeriesOrder(); renderSessionControls(); rebuildSlides();
  }

  function setInstagramMode(mode) {
    instagramState.mode = ['sessions', 'seriesWeekend', 'day', 'dayNoTimes', 'overview', 'monthOverview'].includes(mode) ? mode : 'sessions';
    const picker = document.getElementById('instagramMode');
    if (picker) picker.value = instagramState.mode;
    return loadSelectedPeriod();
  }

  function renderSeriesEventControls() {
    const wrapper = document.getElementById('instagramSeriesEventControls');
    if (wrapper) wrapper.hidden = instagramState.mode !== 'seriesWeekend';
    const names = new Map(instagramState.allSessions.map(item => [item.seriesId, item.seriesName]));
    if (!names.has(instagramState.selectedSeries)) {
      instagramState.selectedSeries = names.has(state.activeSeries) ? state.activeSeries : names.keys().next().value || '';
    }
    const events = new Map(instagramState.allSessions.filter(item => item.seriesId === instagramState.selectedSeries).map(item => [item.eventUid, item]));
    if (!events.has(instagramState.selectedEvent)) instagramState.selectedEvent = events.keys().next().value || '';
    const seriesPicker = document.getElementById('instagramSeries');
    if (seriesPicker) {
      seriesPicker.innerHTML = names.size ? [...names].map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join('') : '<option value="">Geen series in dit weekend</option>';
      seriesPicker.value = instagramState.selectedSeries;
      seriesPicker.disabled = !names.size;
    }
    const eventPicker = document.getElementById('instagramEvent');
    if (eventPicker) {
      eventPicker.innerHTML = events.size ? [...events].map(([id, item]) => `<option value="${esc(id)}">${esc(item.eventName)}</option>`).join('') : '<option value="">Geen events in dit weekend</option>';
      eventPicker.value = instagramState.selectedEvent;
      eventPicker.disabled = !events.size;
    }
  }

  function setInstagramSeries(seriesId) {
    instagramState.selectedSeries = seriesId;
    instagramState.selectedEvent = '';
    renderSeriesEventControls();
    refreshDisplayItems();
  }

  function setInstagramEvent(eventUid) {
    if (!instagramState.allSessions.some(item => item.eventUid === eventUid && item.seriesId === instagramState.selectedSeries)) return;
    instagramState.selectedEvent = eventUid;
    refreshDisplayItems();
  }

  function renderTimeZoneControls() {
    const picker = document.getElementById('instagramTimeZone');
    if (!picker) return;
    const deviceZone = Intl.DateTimeFormat().resolvedOptions().timeZone || DISPLAY_ZONE;
    const zones = [...new Set([DISPLAY_ZONE, deviceZone, 'UTC', ...(Intl.supportedValuesOf?.('timeZone') || [
      'Europe/London', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Asia/Tokyo', 'Australia/Sydney',
    ])])];
    picker.innerHTML = `<option value="eventLocal">Lokale tijd van het circuit</option><option value="deviceLocal">Mijn lokale tijd · ${esc(deviceZone)}</option>` + zones.map(zone =>
      `<option value="${esc(zone)}">${esc(zone)}</option>`
    ).join('');
    picker.value = instagramState.timeZone;
  }

  function setInstagramTimeZone(timeZone) {
    if (!['eventLocal', 'deviceLocal'].includes(timeZone)) {
      try { new Intl.DateTimeFormat('en', { timeZone }); } catch (_) { return; }
    }
    instagramState.timeZone = timeZone;
    const picker = document.getElementById('instagramTimeZone');
    if (picker) picker.value = timeZone;
    return loadSelectedPeriod(true);
  }

  function setInstagramLayout(part, value) {
    const options = {
      timeZonePlacement: ['groups', 'header'], titleAlignment: ['left', 'center', 'right'],
      scheduleAlignment: ['top', 'center', 'bottom'],
    };
    if (['showFooter', 'showRowLogos'].includes(part)) instagramState[part] = Boolean(value);
    else if (options[part]?.includes(value)) instagramState[part] = value;
    else return;
    updateLayoutControls();
    rebuildSlides();
  }

  function updateLayoutControls() {
    const ids = { timeZonePlacement: 'instagramTimeZonePlacement', titleAlignment: 'instagramTitleAlignment', scheduleAlignment: 'instagramScheduleAlignment' };
    Object.entries(ids).forEach(([part, id]) => {
      const input = document.getElementById(id);
      if (input) input.value = instagramState[part];
    });
    const footer = document.getElementById('instagramFooterVisibility');
    if (footer) footer.checked = instagramState.showFooter;
    const rowLogos = document.getElementById('instagramRowLogoVisibility');
    if (rowLogos) rowLogos.checked = instagramState.showRowLogos;
    const headerLogoSize = document.getElementById('instagramHeaderLogoSize');
    if (headerLogoSize) headerLogoSize.value = Math.round(instagramState.headerLogoScale * 100);
    const output = document.getElementById('instagramHeaderLogoSizeValue');
    if (output) output.value = `${Math.round(instagramState.headerLogoScale * 100)}%`;
  }

  function setInstagramHeaderLogoSize(percent) {
    const value = Number(percent);
    if (!Number.isFinite(value)) return;
    instagramState.headerLogoScale = Math.max(.5, Math.min(2, value / 100));
    updateLayoutControls();
    rebuildSlides();
  }

  function setInstagramDay(dayKey) {
    instagramState.selectedDay = dayKey;
    if (['day', 'dayNoTimes'].includes(instagramState.mode)) refreshDisplayItems();
  }

  function setInstagramTitle(value) {
    instagramState.title = String(value || '').trimStart().slice(0, 42) || 'Upcoming races';
    renderSlide();
  }

  function toggleInstagramHeaderPart(part) {
    if (part === 'logo') instagramState.showLogo = !instagramState.showLogo;
    else if (part === 'title') instagramState.showTitle = !instagramState.showTitle;
    else if (part === 'date') instagramState.showDate = !instagramState.showDate;
    else if (part === 'topMeta') instagramState.showTopMeta = !instagramState.showTopMeta;
    else return;
    updateHeaderVisibilityControls();
    rebuildSlides();
  }

  function updateHeaderVisibilityControls() {
    const logoButton = document.getElementById('instagramLogoVisibility');
    const titleButton = document.getElementById('instagramTitleVisibility');
    const dateButton = document.getElementById('instagramDateVisibility');
    const topMetaButton = document.getElementById('instagramTopMetaVisibility');
    [[logoButton, instagramState.showLogo], [titleButton, instagramState.showTitle], [dateButton, instagramState.showDate], [topMetaButton, instagramState.showTopMeta]].forEach(([button, visible]) => {
      button?.classList.toggle('active', visible);
      button?.setAttribute('aria-pressed', String(visible));
    });
  }

  function updateNavigation() {
    const total = Math.max(1, instagramState.slides.length);
    const label = document.getElementById('instagramSlideLabel');
    if (label) label.textContent = `${instagramState.slideIndex + 1} / ${total}`;
    const previous = document.getElementById('instagramPrevious');
    const next = document.getElementById('instagramNext');
    if (previous) previous.disabled = instagramState.slideIndex <= 0;
    if (next) next.disabled = instagramState.slideIndex >= total - 1;
  }

  async function openInstagramGenerator() {
    instagramState.logoScales = loadLogoScales();
    instagramState.controlTab = 'sessions';
    instagramState.weekend = weekendRangeFor();
    instagramState.month = instagramState.weekend.start.slice(0, 7);
    instagramState.weekends = weekendsForMonth(instagramState.month);
    instagramState.selectedWeeks = new Set(instagramState.weekends.map(week => week.start));
    renderWeekControls();
    const result = collectWeekendSessions(instagramState.weekend);
    instagramState.allSessions = result.sessions;
    instagramState.sourceWarningCount = result.warnings.length;
    instagramState.assetLoadComplete = false;
    instagramState.mode = 'overview';
    renderTimeZoneControls();
    updateLayoutControls();
    renderSeriesEventControls();
    renderWeekControls();
    instagramState.title = 'Upcoming races';
    instagramState.showLogo = false;
    instagramState.showTitle = false;
    instagramState.showDate = false;
    instagramState.showTopMeta = false;
    const presentSeries = new Set(result.sessions.map(item => item.seriesId));
    instagramState.seriesOrder = (state.series || []).map(series => series.id).filter(id => presentSeries.has(id));
    instagramState.draggedSeriesId = '';
    instagramState.selectedDay = result.sessions.find(item => item.enabledByDefault)?.dayKey || result.sessions[0]?.dayKey || instagramState.weekend.start;
    instagramState.displayItems = result.sessions;
    instagramState.selectedIds = new Set(result.sessions.filter(item => item.enabledByDefault).map(item => item.uid));
    instagramState.slideIndex = 0;
    const modal = document.getElementById('instagramModal');
    modal?.classList.add('show'); document.body.style.overflow = 'hidden';
    const modeSelect = document.getElementById('instagramMode');
    if (modeSelect) modeSelect.value = instagramState.mode;
    const titleInput = document.getElementById('instagramPostTitle');
    if (titleInput) titleInput.value = instagramState.title;
    updateHeaderVisibilityControls();
    const daySelect = document.getElementById('instagramDay');
    if (daySelect) {
      const days = [...new Set(result.sessions.map(item => item.dayKey))];
      daySelect.innerHTML = days.map(dayKey => {
        const heading = dayHeading(dayKey);
        return `<option value="${esc(dayKey)}">${esc(heading.day)} · ${esc(heading.date)}</option>`;
      }).join('');
      daySelect.value = instagramState.selectedDay;
    }
    document.querySelector('.instagram-format-controls')?.classList.remove('day-mode');
    refreshDisplayItems(); renderLogoScaleControls(); setInstagramControlTab('sessions');
    await Promise.all([
      preloadAssets(result.sessions),
      document.fonts.load('700 70px Inter'),
      document.fonts.ready,
    ]);
    instagramState.assetLoadComplete = true;
    rebuildSlides();
  }

  function closeInstagramGenerator() {
    clearExportFiles();
    document.getElementById('instagramModal')?.classList.remove('show');
    document.body.style.overflow = '';
  }

  function toggleInstagramSession(uid, enabled) {
    if (enabled) instagramState.selectedIds.add(uid); else instagramState.selectedIds.delete(uid);
    renderSeriesOrder();
    rebuildSlides();
  }

  function navigateInstagramSlide(offset) {
    const next = instagramState.slideIndex + offset;
    if (next < 0 || next >= Math.max(1, instagramState.slides.length)) return;
    instagramState.slideIndex = next; renderSlide();
  }

  function canvasBlob(canvas) {
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG kon niet worden opgebouwd')), 'image/png'));
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2500);
  }

  async function prepareExport() {
    if (!instagramState.slides.length) throw new Error('Selecteer minimaal één sessie');
    showStatus('Assets en lettertypen voorbereiden…', 'loading');
    await document.fonts.load('700 70px Inter');
    await document.fonts.ready;
    const selected = instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid) && (instagramState.mode !== 'monthOverview' || instagramState.selectedWeeks.has(item.weekStart)));
    await preloadAssets(selected);
  }

  let exportBusy = false;
  let exportUrls = [];
  function clearExportFiles() {
    document.getElementById('instagramExportResults')?.remove();
    exportUrls.forEach(url => URL.revokeObjectURL(url));
    exportUrls = [];
  }

  function showExportFiles(files) {
    clearExportFiles();
    const panel = document.createElement('section');
    panel.id = 'instagramExportResults';
    panel.className = 'instagram-export-results';
    panel.setAttribute('aria-label', 'PNG’s opslaan en delen');
    panel.innerHTML = `<div class="instagram-export-heading"><strong>${files.length} PNG${files.length === 1 ? '' : '’s'} gereed</strong><button type="button" aria-label="Sluit exportresultaat">×</button></div><p>Deel naar een app of bewaar de afbeeldingen. Je kunt een afbeelding ook ingedrukt houden om deze op te slaan.</p>`;
    panel.querySelector('button').onclick = clearExportFiles;
    if (navigator.canShare?.({ files })) {
      const share = document.createElement('button');
      share.className = 'btn btn-primary'; share.textContent = 'Deel of bewaar PNG’s';
      share.onclick = async () => {
        try { await navigator.share({ files }); }
        catch (error) { if (error.name !== 'AbortError') showStatus('Delen lukt niet. Gebruik de downloadlinks hieronder.', 'error'); }
      };
      panel.appendChild(share);
    }
    files.forEach((file, index) => {
      const url = URL.createObjectURL(file); exportUrls.push(url);
      const link = document.createElement('a');
      link.href = url; link.download = file.name; link.className = 'instagram-export-file';
      const image = document.createElement('img'); image.src = url; image.alt = `Instagram-post ${index + 1}`;
      link.append(image, document.createTextNode(`Download PNG ${index + 1}`)); panel.appendChild(link);
    });
    document.querySelector('.instagram-workspace').prepend(panel);
    panel.scrollIntoView({ block: 'start' });
    panel.querySelector('button').focus({ preventScroll: true });
  }

  async function downloadInstagramPng(allSlides = false) {
    if (exportBusy) return;
    exportBusy = true;
    const buttons = ['instagramDownloadAll', 'instagramDownload'].map(id => document.getElementById(id));
    buttons.forEach(button => { if (button) button.disabled = true; });
    const originalIndex = instagramState.slideIndex;
    try {
      await prepareExport();
      const indexes = allSlides ? instagramState.slides.map((_, index) => index) : [instagramState.slideIndex];
      const files = [];
      for (const index of indexes) {
        instagramState.slideIndex = index; renderSlide(index);
        await new Promise(requestAnimationFrame);
        const canvas = document.getElementById('instagramCanvas');
        if (canvas.width !== WIDTH || canvas.height !== HEIGHT) throw new Error('Exportformaat is niet 1080 × 1350');
        const blob = await canvasBlob(canvas);
        files.push(new File([blob], `raceday-week-${isoWeek(instagramState.slides[index]?.groups[0]?.dayKey || instagramState.weekend.start)}-${String(index + 1).padStart(2, '0')}.png`, { type: 'image/png' }));
      }
      if (window.matchMedia('(max-width: 900px), (pointer: coarse)').matches) {
        showExportFiles(files);
      } else {
        files.forEach(file => downloadBlob(file, file.name));
      }
      showStatus(`✓ ${indexes.length} PNG${indexes.length === 1 ? '' : '’s'} van 1080 × 1350 gereed`, 'success');
    } catch (error) {
      showStatus(`Export mislukt: ${error.message}`, 'error');
    } finally {
      instagramState.slideIndex = originalIndex; renderSlide(originalIndex);
      exportBusy = false;
      buttons.forEach(button => { if (button) button.disabled = false; });
    }
  }

  window.openInstagramGenerator = openInstagramGenerator;
  window.closeInstagramGenerator = closeInstagramGenerator;
  window.toggleInstagramSession = toggleInstagramSession;
  window.setInstagramMode = setInstagramMode;
  window.setInstagramSeries = setInstagramSeries;
  window.setInstagramEvent = setInstagramEvent;
  window.setInstagramTimeZone = setInstagramTimeZone;
  window.setInstagramLayout = setInstagramLayout;
  window.setInstagramHeaderLogoSize = setInstagramHeaderLogoSize;
  window.setInstagramMonth = setInstagramMonth;
  window.setInstagramWeekend = setInstagramWeekend;
  window.toggleInstagramWeek = toggleInstagramWeek;
  window.setInstagramDay = setInstagramDay;
  window.setInstagramTitle = setInstagramTitle;
  window.toggleInstagramHeaderPart = toggleInstagramHeaderPart;
  window.setInstagramLogoScale = setInstagramLogoScale;
  window.resetInstagramLogoScale = resetInstagramLogoScale;
  window.setInstagramControlTab = setInstagramControlTab;
  window.moveInstagramSeries = moveInstagramSeries;
  window.startInstagramSeriesDrag = startInstagramSeriesDrag;
  window.endInstagramSeriesDrag = endInstagramSeriesDrag;
  window.dropInstagramSeries = dropInstagramSeries;
  window.navigateInstagramSlide = navigateInstagramSlide;
  window.downloadInstagramPng = downloadInstagramPng;
  window.RaceDayInstagram = {
    collectWeekendSessions, buildSlides, buildOverviewItems, sessionInstant, sessionEndDateKey, localTimeInfo, localDateKey, sessionLabel,
    eventTimeZone, displayTimeZone, sessionTitle, timeZoneText, contentBottom, scheduleTop,
    weekendsForMonth, buildOverviewSlides, isoWeek, weekendRangeFor, renderSlide, contentTop, maxSessionsPerSlide, selectedSeriesOrder,
    state: instagramState, WIDTH, HEIGHT,
  };
})();
