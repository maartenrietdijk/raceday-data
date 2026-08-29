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
  const CONTENT_BOTTOM = 1194;
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
  const FLAG_ROOT = 'instagram-assets/flags/4x3';
  const FLAG_DATA_URL_CACHE = new Map();
  const LOGO_SCALE_STORAGE_KEY = 'raceday_instagram_logo_scales';
  let logoScaleStorageWarningShown = false;

  const instagramState = {
    allSessions: [], selectedIds: new Set(), slides: [], slideIndex: 0,
    images: new Map(), warnings: [], weekend: null, sourceWarningCount: 0,
    assetLoadComplete: false, mode: 'sessions', selectedDay: '', displayItems: [],
    title: 'Upcoming races', showTitle: true, showDate: true, showTopMeta: true,
    seriesOrder: [], draggedSeriesId: '', logoScales: {}, controlTab: 'sessions',
  };

  function loadLogoScales() {
    if (window.matchMedia('(max-width: 760px)').matches) {
      return {};
    }
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
    if (window.matchMedia('(max-width: 760px)').matches) {
      return;
    }
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
    return zonedWallTimeToUtc(date, time, SERIES_TIME_ZONES[seriesId] || DISPLAY_ZONE);
  }

  function localDateKey(date) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: DISPLAY_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(date);
  }

  function localTimeInfo(date) {
    const time = new Intl.DateTimeFormat('en-GB', {
      timeZone: DISPLAY_ZONE, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
    const zoneName = new Intl.DateTimeFormat('en-GB', {
      timeZone: DISPLAY_ZONE, timeZoneName: 'short',
    }).formatToParts(date).find(part => part.type === 'timeZoneName')?.value || '';
    return { time, zone: /GMT\+2|CEST/i.test(zoneName) ? 'CEST' : /GMT\+1|CET/i.test(zoneName) ? 'CET' : zoneName };
  }

  function defaultEnabled(kind) {
    if (DEFAULT_ON.has(kind)) return true;
    if (DEFAULT_OFF.has(kind)) return false;
    return true; // Unknown kinds remain visible and selected, never silently lost.
  }

  function collectWeekendSessions(weekend = weekendRangeFor()) {
    const sessions = [];
    const warnings = [];
    (state.series || []).forEach(series => {
      (state.data[series.id] || []).forEach((round, roundIndex) => {
        const roundDayKeys = (round.sessions || []).map(roundSession => {
          const roundInstant = sessionInstant(roundSession, series.id);
          return roundInstant ? localDateKey(roundInstant) : rawSessionDate(roundSession);
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
          const dayKey = instant ? localDateKey(instant) : date;
          if (dayKey < weekend.start || dayKey >= weekend.endExclusive) return;
          const timeInfo = instant ? localTimeInfo(instant) : { time: 'TIME TBC', zone: '' };
          const uid = `${series.id}:${round.id || roundIndex}:${session.id || sessionIndex}`;
          sessions.push({
            uid, seriesId: series.id, seriesName: series.name || formatSeriesName(series.id),
            round, session, roundIndex, sessionIndex, dayKey, instant, isTbc,
            time: timeInfo.time, zone: timeInfo.zone, kind: session.kind || 'unknown',
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
    const base = LABELS[item.kind] || String(item.session.name || item.kind || 'SESSION').toUpperCase();
    const name = String(item.session.name || '').trim();
    const number = name.match(/(?:^|\s)(\d{1,2})(?:\s|$)/)?.[1];
    if (number && ['practice', 'qualifying', 'race', 'stage'].includes(item.kind)) return `${base} ${number}`;
    return compactLabel(base);
  }

  function compactLabel(value) {
    const cleaned = String(value || 'SESSION').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toUpperCase();
    return cleaned.length <= 21 ? cleaned : `${cleaned.slice(0, 18).trim()}…`;
  }

  // ── Slide distribution ────────────────────────────────────────────────────

  function hasMinimalHeader() {
    return !instagramState.showTitle && !instagramState.showDate && !instagramState.showTopMeta;
  }

  function contentTop() {
    if (hasMinimalHeader()) return MINIMAL_CONTENT_TOP;
    return instagramState.showTopMeta ? DEFAULT_CONTENT_TOP : COMPACT_CONTENT_TOP;
  }

  function maxSessionsPerSlide() {
    if (hasMinimalHeader()) return MINIMAL_MAX_SESSIONS_PER_SLIDE;
    return instagramState.showTopMeta ? DEFAULT_MAX_SESSIONS_PER_SLIDE : COMPACT_MAX_SESSIONS_PER_SLIDE;
  }

  function headerDividerY() {
    if (hasMinimalHeader()) return 132;
    return instagramState.showTopMeta ? 260 : 236;
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
        const availableHeight = CONTENT_BOTTOM - contentTop() - slide.height - gap - GROUP_HEADER_HEIGHT - GROUP_BOTTOM_PADDING;
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

  function buildOverviewSlides(items) {
    const slides = [];
    const capacity = maxSessionsPerSlide();
    for (let index = 0; index < items.length; index += capacity) {
      slides.push({ groups: [{
        dayKey: instagramState.weekend.start,
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

  async function preloadAssets(items) {
    const configs = items.map(item => window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId]).filter(Boolean);
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
    const headerRange = ['day', 'dayNoTimes'].includes(instagramState.mode)
      ? { start: instagramState.selectedDay, end: instagramState.selectedDay }
      : instagramState.weekend;
    ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
    drawBrandIcon(ctx, 68, 56, 58, 15);
    ctx.fillStyle = '#ffffff'; ctx.font = '650 25px Inter, sans-serif';
    ctx.fillText('RaceDay', 143, 93);
    if (instagramState.showTopMeta) {
      ctx.fillStyle = '#ff3045'; ctx.font = '650 18px Inter, sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(`Race week ${isoWeek(instagramState.weekend.start)}`, 1008, 81);
      if (totalSlides > 1) {
        ctx.fillStyle = '#7f7f87'; ctx.font = '550 16px Inter, sans-serif';
        ctx.fillText(`${slideNumber} of ${totalSlides}`, 1008, 111);
      }
    }
    ctx.textAlign = 'left'; ctx.fillStyle = '#ffffff';
    const postTitle = instagramState.title || 'Upcoming races';
    let titleSize = 64;
    ctx.font = `700 ${titleSize}px Inter, sans-serif`;
    while (titleSize > 44 && ctx.measureText(postTitle).width > 720) {
      titleSize -= 1;
      ctx.font = `700 ${titleSize}px Inter, sans-serif`;
    }
    if (instagramState.showTitle) ctx.fillText(truncateText(ctx, postTitle, 720), 68, 185);
    if (instagramState.showDate) {
      ctx.fillStyle = '#a6a6ad'; ctx.font = '500 22px Inter, sans-serif';
      ctx.fillText(formatHeaderDates(headerRange), 72, 226);
    }
    const selectedZones = [...new Set(instagramState.displayItems
      .filter(item => instagramState.selectedIds.has(item.uid) && !item.isTbc && item.zone)
      .map(item => item.zone))];
    if (!['overview', 'dayNoTimes'].includes(instagramState.mode)) {
      const zoneLabel = selectedZones.length ? selectedZones.join(' / ') : localTimeInfo(new Date(`${headerRange.start}T12:00:00Z`)).zone;
      const zoneText = `All times are ${zoneLabel}`;
      const badgeHeight = 40, badgePadding = 15, clockSize = 16, badgeGap = 10;
      ctx.font = '550 17px Inter, sans-serif';
      const badgeWidth = Math.ceil(ctx.measureText(zoneText).width + (badgePadding * 2) + clockSize + badgeGap);
      const badgeX = 1008 - badgeWidth, badgeY = instagramState.showTopMeta ? 199 : 65;
      const badgeFill = ctx.createLinearGradient(badgeX, badgeY, badgeX, badgeY + badgeHeight);
      badgeFill.addColorStop(0, 'rgba(28,25,27,.92)'); badgeFill.addColorStop(1, 'rgba(13,12,14,.92)');
      fillRoundRect(ctx, badgeX, badgeY, badgeWidth, badgeHeight, PANEL_RADIUS, badgeFill);
      ctx.strokeStyle = 'rgba(255,255,255,.14)'; ctx.lineWidth = 1;
      roundedPath(ctx, badgeX + .5, badgeY + .5, badgeWidth - 1, badgeHeight - 1, PANEL_RADIUS - .5); ctx.stroke();
      drawClockIcon(ctx, badgeX + badgePadding + clockSize / 2, badgeY + badgeHeight / 2, clockSize);
      ctx.fillStyle = '#c5c5cb'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(zoneText, badgeX + badgePadding + clockSize + badgeGap, badgeY + badgeHeight / 2 + .5);
      ctx.textBaseline = 'alphabetic';
    }
    ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fillRect(68, headerDividerY(), 944, 1);
  }

  function drawLogo(ctx, item, x, y, width, height) {
    const config = window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId];
    const image = config ? instagramState.images.get(config.src) : null;
    ctx.save();
    ctx.beginPath(); ctx.rect(x, y, width, height); ctx.clip();
    if (image) {
      const size = Math.min(config.maxWidth || width, width) * (config.scale || 1) * logoScaleFor(item.seriesId);
      ctx.filter = config.mono ? 'grayscale(1) brightness(0) invert(1)' : 'none';
      ctx.globalAlpha = .95;
      ctx.drawImage(image, x + width / 2 - size / 2 + (config.x || 0), y + height / 2 - size / 2 + (config.y || 0), size, size);
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
    drawLogo(ctx, item, logoX, logoY, logoW, logoH);
    const copyX = rowX + 162;
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#f7f7f8'; ctx.font = '650 24px Inter, sans-serif';
    const eventTitle = truncateText(ctx, item.eventName, item.overview ? 555 : 338);
    ctx.fillText(eventTitle, copyX, y + 46);
    drawFlag(ctx, item.countryCode, copyX + ctx.measureText(eventTitle).width + 12, y + 25, 32, 24);
    ctx.fillStyle = '#8e8e96'; ctx.font = '500 16px Inter, sans-serif';
    const subline = item.circuitName && item.circuitName !== item.eventName ? `${item.seriesName} · ${item.circuitName}` : item.seriesName;
    ctx.fillText(truncateText(ctx, subline, 390), copyX, y + 70);
    const label = item.overview ? '' : sessionLabel(item), labelX = x + 592, labelW = 178, labelH = 48;
    const labelY = y + (rowHeight - labelH) / 2;
    const dayWithoutTimes = instagramState.mode === 'dayNoTimes';
    if (!item.overview && !dayWithoutTimes) {
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
    let rightFontSize = item.overview ? 19 : dayWithoutTimes ? 15 : item.isTbc ? 17 : 28;
    ctx.font = `${dayWithoutTimes ? 650 : 700} ${rightFontSize}px Inter, sans-serif`;
    while (rightFontSize > 11 && ctx.measureText(rightLabel).width > timeW - 18) {
      rightFontSize -= 1;
      ctx.font = `${dayWithoutTimes ? 650 : 700} ${rightFontSize}px Inter, sans-serif`;
    }
    ctx.fillText(rightLabel, timeX + timeW/2, y + rowHeight / 2 + 1);
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
    const dayLabel = `${group.overview ? 'THIS WEEKEND' : heading.day.toUpperCase()}${group.continuation ? ' · CONTINUED' : ''}`;
    const headingX = contentX;
    const headingY = y + 35;
    ctx.fillText(dayLabel, headingX, headingY);
    const dateX = headingX + ctx.measureText(dayLabel).width + 20;
    ctx.fillStyle = '#929299'; ctx.font = '550 20px Inter, sans-serif'; ctx.textAlign = 'left';
    ctx.fillText(group.overview ? formatCompactDateRange(instagramState.weekend.start, instagramState.weekend.end).toUpperCase() : heading.date.toUpperCase(), dateX, headingY);
    let rowY = y + headerHeight;
    group.items.forEach(item => { drawSessionRow(ctx, item, rowY, rowHeight); rowY += rowHeight; });
    return rowY + bottomPadding;
  }

  function drawFooter(ctx, slideNumber, totalSlides) {
    ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fillRect(68, 1236, 944, 1);
    drawBrandIcon(ctx, 68, 1266, 48, 13);
    ctx.fillStyle = '#f5f5f7'; ctx.font = '650 26px Inter, sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillText('RaceDay', 132, 1300);
    drawStoreBadge(ctx, 646, 1264, 174, 50, 'apple');
    drawStoreBadge(ctx, 832, 1264, 173, 50, 'google');
    if (totalSlides > 1) { ctx.fillStyle='#77777f';ctx.font='550 14px Inter, sans-serif';ctx.textAlign='center';ctx.fillText(`${slideNumber} of ${totalSlides}`, 530, 1297); }
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
      let y = contentTop();
      slide.groups.forEach((group, groupIndex) => { if (groupIndex) y += GROUP_GAP; y = drawDayGroup(ctx, group, y, ROW_HEIGHT); });
    }
    drawFooter(ctx, index + 1, totalSlides);
    updateNavigation();
  }

  // ── Preview UI and PNG export ─────────────────────────────────────────────

  function orderedItems(items) {
    const order = new Map(instagramState.seriesOrder.map((id, index) => [id, index]));
    return [...items].sort((a, b) => {
      if (instagramState.mode !== 'overview') {
        const dayDifference = a.dayKey.localeCompare(b.dayKey);
        if (dayDifference) return dayDifference;
      }
      // Schedules that show times must be chronological across all series.
      // Sessions without a confirmed time stay at the end of their day.
      if (!['overview', 'dayNoTimes'].includes(instagramState.mode)) {
        const timeDifference = (a.instant?.getTime() ?? Number.MAX_SAFE_INTEGER) -
          (b.instant?.getTime() ?? Number.MAX_SAFE_INTEGER);
        if (timeDifference) return timeDifference;
      }
      const seriesDifference = (order.get(a.seriesId) ?? 9999) - (order.get(b.seriesId) ?? 9999);
      if (seriesDifference) return seriesDifference;
      if (instagramState.mode === 'overview') {
        const eventDifference = a.eventStart.localeCompare(b.eventStart);
        if (eventDifference) return eventDifference;
      }
      return a.eventName.localeCompare(b.eventName) || a.uid.localeCompare(b.uid);
    });
  }

  function renderSeriesOrder() {
    const list = document.getElementById('instagramSeriesOrder');
    if (!list) return;
    const names = new Map(instagramState.allSessions.map(item => [item.seriesId, item.seriesName]));
    const ids = instagramState.seriesOrder.filter(id => names.has(id));
    list.innerHTML = ids.map((id, index) => `<div class="instagram-series-order-item" data-series-id="${esc(id)}"
        ondragover="event.preventDefault()" ondrop="dropInstagramSeries(event, this.dataset.seriesId)">
      <div class="instagram-series-order-main">
        <span class="instagram-series-order-handle" draggable="true" aria-label="Sleep ${esc(names.get(id))}"
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
    const index = instagramState.seriesOrder.indexOf(seriesId);
    const next = index + Number(direction);
    if (index < 0 || next < 0 || next >= instagramState.seriesOrder.length) return;
    [instagramState.seriesOrder[index], instagramState.seriesOrder[next]] = [instagramState.seriesOrder[next], instagramState.seriesOrder[index]];
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
    const sourceIndex = instagramState.seriesOrder.indexOf(sourceId);
    const targetIndex = instagramState.seriesOrder.indexOf(targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;
    instagramState.seriesOrder.splice(sourceIndex, 1);
    instagramState.seriesOrder.splice(targetIndex, 0, sourceId);
    instagramState.draggedSeriesId = '';
    applySeriesOrderChange();
  }

  function rebuildWarnings() {
    const selected = instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid));
    const missingLogos = [...new Set(selected.filter(item => {
      const config = window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId];
      return !config || (instagramState.assetLoadComplete && !instagramState.images.has(config.src));
    }).map(item => item.seriesName))];
    const selectedFlagSources = [...new Set(selected.map(item => flagSrc(item.countryCode)).filter(Boolean))];
    const loadedFlagCount = selectedFlagSources.filter(src => instagramState.images.has(src)).length;
    const missingCountries = selected.filter(item => {
      const src = flagSrc(item.countryCode);
      return !src || (instagramState.assetLoadComplete && !instagramState.images.has(src));
    }).length;
    const messages = [];
    if (!instagramState.allSessions.length) messages.push('Geen sessies gevonden voor dit weekend. Synchroniseer eerst de kalender.');
    if (instagramState.sourceWarningCount) messages.push(`${instagramState.sourceWarningCount} sessie(s) zonder geldige datum zijn overgeslagen.`);
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
      const day = instagramState.mode !== 'overview' && previousDay !== item.dayKey ? `<div class="instagram-day-label">${heading.day} · ${heading.date}</div>` : '';
      previousDay = item.dayKey;
      const primary = item.overview ? item.eventName : `${item.eventName} · ${sessionLabel(item)}`;
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
    const selected = orderedItems(instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid)));
    instagramState.slides = instagramState.mode === 'overview' ? buildOverviewSlides(selected) : buildSlides(selected);
    instagramState.slideIndex = Math.min(instagramState.slideIndex, Math.max(0, instagramState.slides.length - 1));
    rebuildWarnings(); renderSlide();
    const selectedLabel = document.getElementById('instagramSelectionSummary');
    const noun = instagramState.mode === 'overview' ? 'events' : 'sessies';
    if (selectedLabel) selectedLabel.textContent = `${selected.length} van ${instagramState.displayItems.length} ${noun} geselecteerd · ${Math.max(1, instagramState.slides.length)} slide${instagramState.slides.length === 1 ? '' : 's'}`;
  }

  function refreshDisplayItems() {
    if (instagramState.mode === 'overview') {
      instagramState.displayItems = buildOverviewItems(instagramState.allSessions);
      instagramState.selectedIds = new Set(instagramState.displayItems.map(item => item.uid));
    } else {
      instagramState.displayItems = ['day', 'dayNoTimes'].includes(instagramState.mode)
        ? instagramState.allSessions.filter(item => item.dayKey === instagramState.selectedDay)
        : instagramState.allSessions;
      instagramState.selectedIds = new Set(instagramState.displayItems.filter(item => item.enabledByDefault).map(item => item.uid));
    }
    instagramState.slideIndex = 0;
    const formatControls = document.querySelector('.instagram-format-controls');
    formatControls?.classList.toggle('day-mode', ['day', 'dayNoTimes'].includes(instagramState.mode));
    renderSessionControls(); rebuildSlides();
  }

  function setInstagramMode(mode) {
    instagramState.mode = ['sessions', 'day', 'dayNoTimes', 'overview'].includes(mode) ? mode : 'sessions';
    refreshDisplayItems();
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
    if (part === 'title') instagramState.showTitle = !instagramState.showTitle;
    else if (part === 'date') instagramState.showDate = !instagramState.showDate;
    else if (part === 'topMeta') instagramState.showTopMeta = !instagramState.showTopMeta;
    else return;
    updateHeaderVisibilityControls();
    rebuildSlides();
  }

  function updateHeaderVisibilityControls() {
    const titleButton = document.getElementById('instagramTitleVisibility');
    const dateButton = document.getElementById('instagramDateVisibility');
    const topMetaButton = document.getElementById('instagramTopMetaVisibility');
    [[titleButton, instagramState.showTitle], [dateButton, instagramState.showDate], [topMetaButton, instagramState.showTopMeta]].forEach(([button, visible]) => {
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
    const result = collectWeekendSessions(instagramState.weekend);
    instagramState.allSessions = result.sessions;
    instagramState.sourceWarningCount = result.warnings.length;
    instagramState.assetLoadComplete = false;
    instagramState.mode = 'sessions';
    instagramState.title = 'Upcoming races';
    instagramState.showTitle = true;
    instagramState.showDate = true;
    instagramState.showTopMeta = true;
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
    renderSeriesOrder(); renderLogoScaleControls(); setInstagramControlTab('sessions'); renderSessionControls(); rebuildWarnings();
    await Promise.all([
      preloadAssets(result.sessions),
      document.fonts.load('700 70px Inter'),
      document.fonts.ready,
    ]);
    instagramState.assetLoadComplete = true;
    rebuildSlides();
  }

  function closeInstagramGenerator() {
    document.getElementById('instagramModal')?.classList.remove('show');
    document.body.style.overflow = '';
  }

  function toggleInstagramSession(uid, enabled) {
    if (enabled) instagramState.selectedIds.add(uid); else instagramState.selectedIds.delete(uid);
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
    const selected = instagramState.displayItems.filter(item => instagramState.selectedIds.has(item.uid));
    await preloadAssets(selected);
  }

  async function downloadInstagramPng(allSlides = false) {
    const button = document.getElementById(allSlides ? 'instagramDownloadAll' : 'instagramDownload');
    if (button) button.disabled = true;
    const originalIndex = instagramState.slideIndex;
    try {
      await prepareExport();
      const indexes = allSlides ? instagramState.slides.map((_, index) => index) : [instagramState.slideIndex];
      for (const index of indexes) {
        instagramState.slideIndex = index; renderSlide(index);
        await new Promise(requestAnimationFrame);
        const canvas = document.getElementById('instagramCanvas');
        if (canvas.width !== WIDTH || canvas.height !== HEIGHT) throw new Error('Exportformaat is niet 1080 × 1350');
        const blob = await canvasBlob(canvas);
        downloadBlob(blob, `raceday-week-${isoWeek(instagramState.weekend.start)}-${String(index + 1).padStart(2, '0')}.png`);
      }
      showStatus(`✓ ${indexes.length} PNG${indexes.length === 1 ? '' : '’s'} van 1080 × 1350 gedownload`, 'success');
    } catch (error) {
      showStatus(`Export mislukt: ${error.message}`, 'error');
    } finally {
      instagramState.slideIndex = originalIndex; renderSlide(originalIndex);
      if (button) button.disabled = false;
    }
  }

  window.openInstagramGenerator = openInstagramGenerator;
  window.closeInstagramGenerator = closeInstagramGenerator;
  window.toggleInstagramSession = toggleInstagramSession;
  window.setInstagramMode = setInstagramMode;
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
    collectWeekendSessions, buildSlides, buildOverviewItems, sessionInstant, localTimeInfo, sessionLabel,
    weekendRangeFor, renderSlide, state: instagramState, WIDTH, HEIGHT,
  };
})();
