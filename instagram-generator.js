/* RaceDay Instagram weekend generator — fixed 1080×1350 canvas template. */
(() => {
  'use strict';

  const WIDTH = 1080;
  const HEIGHT = 1350;
  const DISPLAY_ZONE = 'Europe/Amsterdam';
  const MAX_SESSIONS_PER_SLIDE = 6;
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
  const VALID_FLAG_CODES = new Set(['NL','FR','IT','DE','BE','AT','ES','HU','BG','RO','IE','LU','PL','MC','ID','RU','UA','EE','LV','LT','AE','BH','QA','JP','BR','US','GB','AU','NZ','CA','MX','AR','ZA','FI','SE','NO','DK','CH','CZ','SK','SI','HR','PT','GR','TR','SA','CN','KR','TH','MY','SG','AZ','IN']);

  const instagramState = {
    allSessions: [], selectedIds: new Set(), slides: [], slideIndex: 0,
    images: new Map(), warnings: [], weekend: null, sourceWarningCount: 0,
    assetLoadComplete: false,
  };

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

  function buildSlides(selected) {
    if (!selected.length) return [];
    const groups = [];
    selected.forEach(item => {
      const last = groups[groups.length - 1];
      if (last?.dayKey === item.dayKey) last.items.push(item);
      else groups.push({ dayKey: item.dayKey, items: [item], continuation: false });
    });
    const chunks = groups.flatMap(group => {
      const result = [];
      for (let index = 0; index < group.items.length; index += MAX_SESSIONS_PER_SLIDE) {
        result.push({ dayKey: group.dayKey, items: group.items.slice(index, index + MAX_SESSIONS_PER_SLIDE), continuation: index > 0 });
      }
      return result;
    });
    const slides = [];
    let slide = { groups: [], count: 0 };
    chunks.forEach(group => {
      if (slide.count && slide.count + group.items.length > MAX_SESSIONS_PER_SLIDE) {
        slides.push(slide);
        slide = { groups: [], count: 0 };
      }
      slide.groups.push(group);
      slide.count += group.items.length;
    });
    if (slide.count) slides.push(slide);
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

  async function preloadAssets(items) {
    const configs = items.map(item => window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId]).filter(Boolean);
    const brandIcon = window.RACEDAY_INSTAGRAM_BRAND?.icon;
    await Promise.all([...configs.map(config => loadImage(config.src)), loadImage(brandIcon)]);
  }

  function drawFlag(ctx, code, x, y, width = 38, height = 25) {
    ctx.save();
    roundedPath(ctx, x, y, width, height, 3);
    ctx.clip();
    const horizontal = colors => colors.forEach((color, index) => {
      ctx.fillStyle = color; ctx.fillRect(x, y + height * index / colors.length, width, height / colors.length + 1);
    });
    const vertical = colors => colors.forEach((color, index) => {
      ctx.fillStyle = color; ctx.fillRect(x + width * index / colors.length, y, width / colors.length + 1, height);
    });
    if (!VALID_FLAG_CODES.has(code)) {
      ctx.fillStyle = '#3b3b43'; ctx.fillRect(x, y, width, height);
      ctx.fillStyle = '#a4a4ad'; ctx.font = '700 11px Inter, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(code || '—', x + width / 2, y + height / 2 + .5);
      ctx.restore();
      ctx.strokeStyle = '#62626c'; ctx.lineWidth = 1; roundedPath(ctx, x, y, width, height, 3); ctx.stroke();
      return false;
    }
    const horizontalMap = {
      NL:['#ae1c28','#fff','#21468b'], DE:['#000','#dd0000','#ffce00'], BE:['#000','#ffd90c','#ef3340'],
      AT:['#ed2939','#fff','#ed2939'], HU:['#ce2939','#fff','#477050'], BG:['#fff','#00966e','#d62612'],
      RU:['#fff','#0039a6','#d52b1e'], UA:['#0057b7','#ffd700'], EE:['#4891d9','#000','#fff'],
      LV:['#9e3039','#fff','#9e3039'], LT:['#fdb913','#006a44','#c1272d'], LU:['#ed2939','#fff','#00a1de'],
      PL:['#fff','#dc143c'], ID:['#ce1126','#fff'], MC:['#ce1126','#fff'], AE:['#00732f','#fff','#000'],
      BH:['#fff','#ce1126'], QA:['#fff','#8a1538'], JP:['#fff'], UA:['#0057b7','#ffd700'],
      AR:['#74acdf','#fff','#74acdf'], FI:['#fff'], SE:['#006aa7'], NO:['#ba0c2f'], DK:['#c60c30'],
      CH:['#d52b1e'], CZ:['#fff','#d7141a'], SK:['#fff','#0b4ea2','#ee1c25'], SI:['#fff','#005da4','#ed1c24'],
      HR:['#ff0000','#fff','#171796'], PT:['#046a38'], GR:['#0d5eaf','#fff','#0d5eaf','#fff','#0d5eaf'],
      TR:['#e30a17'], SA:['#006c35'], CN:['#de2910'], KR:['#fff'], TH:['#a51931','#f4f5f8','#2d2a4a','#f4f5f8','#a51931'],
      MY:['#cc0001','#fff','#cc0001','#fff','#cc0001'], SG:['#ef3340','#fff'], AZ:['#00b5e2','#ef3340','#509e2f'],
      IN:['#ff9933','#fff','#138808'], ZA:['#007749'], BR:['#009c3b'], US:['#b22234','#fff','#b22234','#fff','#b22234','#fff','#b22234'],
      GB:['#012169'], AU:['#012169'], NZ:['#012169'], CA:['#fff'], MX:['#fff'],
    };
    const verticalMap = { FR:['#0055a4','#fff','#ef4135'], IT:['#009246','#fff','#ce2b37'], IE:['#169b62','#fff','#ff883e'], RO:['#002b7f','#fcd116','#ce1126'], ES:['#aa151b','#f1bf00','#aa151b'] };
    if (verticalMap[code]) vertical(verticalMap[code]); else horizontal(horizontalMap[code] || ['#2c2c33','#777']);
    ctx.fillStyle = '#fff';
    if (code === 'JP') { ctx.fillStyle = '#bc002d'; ctx.beginPath(); ctx.arc(x + width/2, y + height/2, height*.26, 0, Math.PI*2); ctx.fill(); }
    if (['FI','SE','NO','DK'].includes(code)) drawNordicCross(ctx, code, x, y, width, height);
    if (code === 'CH') { ctx.fillRect(x+width*.43,y+height*.22,width*.14,height*.56); ctx.fillRect(x+width*.29,y+height*.41,width*.42,height*.18); }
    if (code === 'BR') { ctx.fillStyle='#ffdf00'; diamond(ctx,x+width*.5,y+height*.5,width*.34,height*.38); ctx.fill(); ctx.fillStyle='#002776'; ctx.beginPath();ctx.arc(x+width*.5,y+height*.5,height*.2,0,Math.PI*2);ctx.fill(); }
    if (code === 'CA') { ctx.fillStyle='#d80621';ctx.fillRect(x,y,width*.24,height);ctx.fillRect(x+width*.76,y,width*.24,height);ctx.fillRect(x+width*.47,y+height*.27,width*.06,height*.48); }
    if (code === 'MX') { ctx.fillStyle='#006847';ctx.fillRect(x,y,width/3,height);ctx.fillStyle='#ce1126';ctx.fillRect(x+width*2/3,y,width/3,height); }
    if (['GB','AU','NZ'].includes(code)) drawUnionJack(ctx,x,y,width*(code==='GB'?1:.52),height*(code==='GB'?1:.55));
    if (code === 'US') { ctx.fillStyle='#3c3b6e';ctx.fillRect(x,y,width*.46,height*.54); }
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,255,255,.24)'; ctx.lineWidth = 1; roundedPath(ctx, x, y, width, height, 3); ctx.stroke();
    return true;
  }

  function drawCircularFlag(ctx, code, x, y, size = 28) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x + size / 2, y + size / 2, size / 2, 0, Math.PI * 2);
    ctx.clip();
    drawFlag(ctx, code, x, y, size, size);
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,255,255,.28)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x + size / 2, y + size / 2, size / 2 - .75, 0, Math.PI * 2);
    ctx.stroke();
  }

  function drawNordicCross(ctx, code, x, y, w, h) {
    const colors = { FI:['#003580',null], SE:['#fecc00',null], DK:['#fff',null], NO:['#fff','#00205b'] }[code];
    ctx.fillStyle=colors[0]; ctx.fillRect(x+w*.29,y,w*.14,h);ctx.fillRect(x,y+h*.42,w,h*.18);
    if(colors[1]){ctx.fillStyle=colors[1];ctx.fillRect(x+w*.325,y,w*.07,h);ctx.fillRect(x,y+h*.465,w,h*.08);}
  }
  function drawUnionJack(ctx,x,y,w,h){ctx.fillStyle='#fff';ctx.fillRect(x+w*.43,y,w*.14,h);ctx.fillRect(x,y+h*.42,w,h*.18);ctx.strokeStyle='#fff';ctx.lineWidth=5;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+w,y+h);ctx.moveTo(x+w,y);ctx.lineTo(x,y+h);ctx.stroke();ctx.strokeStyle='#c8102e';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#c8102e';ctx.fillRect(x+w*.47,y,w*.07,h);ctx.fillRect(x,y+h*.47,w,h*.09);}
  function diamond(ctx,cx,cy,rx,ry){ctx.beginPath();ctx.moveTo(cx,cy-ry);ctx.lineTo(cx+rx,cy);ctx.lineTo(cx,cy+ry);ctx.lineTo(cx-rx,cy);ctx.closePath();}

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
    const sameMonth = start.getUTCMonth() === end.getUTCMonth();
    const month = new Intl.DateTimeFormat('en-GB', { month: 'long', timeZone: 'UTC' }).format(start);
    if (sameMonth) return `${start.getUTCDate()}–${end.getUTCDate()} ${month} ${start.getUTCFullYear()}`;
    const startLabel = new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'long', timeZone:'UTC' }).format(start);
    const endLabel = new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'long', year:'numeric', timeZone:'UTC' }).format(end);
    return `${startLabel} – ${endLabel}`;
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
    const gradient = ctx.createLinearGradient(0, 0, WIDTH, HEIGHT);
    gradient.addColorStop(0, '#29040b'); gradient.addColorStop(.34, '#160207'); gradient.addColorStop(.72, '#09090b'); gradient.addColorStop(1, '#260309');
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, WIDTH, HEIGHT);
    const topGlow = ctx.createRadialGradient(920, 30, 30, 920, 30, 620);
    topGlow.addColorStop(0, 'rgba(255,0,35,.38)'); topGlow.addColorStop(1, 'rgba(255,0,35,0)');
    ctx.fillStyle = topGlow; ctx.fillRect(300, 0, 780, 650);
    const bottomGlow = ctx.createRadialGradient(130, 1360, 10, 130, 1360, 520);
    bottomGlow.addColorStop(0, 'rgba(190,0,25,.32)'); bottomGlow.addColorStop(1, 'rgba(190,0,25,0)');
    ctx.fillStyle = bottomGlow; ctx.fillRect(0, 820, 700, 530);
  }

  function drawBrandIcon(ctx, x, y, size, radius = 16) {
    const src = window.RACEDAY_INSTAGRAM_BRAND?.icon;
    const image = src ? instagramState.images.get(src) : null;
    ctx.save(); roundedPath(ctx, x, y, size, size, radius); ctx.clip();
    if (image) ctx.drawImage(image, x, y, size, size);
    else { ctx.fillStyle = '#8d0012'; ctx.fillRect(x, y, size, size); ctx.fillStyle = '#fff'; ctx.font = `750 ${size * .42}px Inter, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('R', x + size/2, y + size/2); }
    ctx.restore();
  }

  function drawHeader(ctx, slideNumber, totalSlides) {
    ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
    drawBrandIcon(ctx, 68, 56, 58, 15);
    ctx.fillStyle = '#ffffff'; ctx.font = '650 25px Inter, sans-serif';
    ctx.fillText('RaceDay', 143, 93);
    ctx.fillStyle = '#ff3045'; ctx.font = '650 18px Inter, sans-serif'; ctx.textAlign = 'right';
    ctx.fillText(`Race week ${isoWeek(instagramState.weekend.start)}`, 1008, 81);
    if (totalSlides > 1) {
      ctx.fillStyle = '#7f7f87'; ctx.font = '550 16px Inter, sans-serif';
      ctx.fillText(`${slideNumber} of ${totalSlides}`, 1008, 111);
    }
    ctx.textAlign = 'left'; ctx.fillStyle = '#ffffff'; ctx.font = '700 70px Inter, sans-serif';
    ctx.fillText('Upcoming races', 68, 198);
    ctx.fillStyle = '#a6a6ad'; ctx.font = '500 24px Inter, sans-serif';
    ctx.fillText(formatHeaderDates(instagramState.weekend), 72, 244);
    const selectedZones = [...new Set(instagramState.allSessions
      .filter(item => instagramState.selectedIds.has(item.uid) && !item.isTbc && item.zone)
      .map(item => item.zone))];
    const zoneLabel = selectedZones.length ? selectedZones.join(' / ') : localTimeInfo(new Date(`${instagramState.weekend.start}T12:00:00Z`)).zone;
    ctx.fillStyle = '#8f8f96'; ctx.font = '550 18px Inter, sans-serif'; ctx.textAlign = 'right';
    ctx.fillText(`All times are ${zoneLabel}`, 1008, 244);
    ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fillRect(68, 284, 944, 1);
  }

  function drawLogo(ctx, item, x, y, width, height) {
    const config = window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId];
    const image = config ? instagramState.images.get(config.src) : null;
    fillRoundRect(ctx, x, y, width, height, 14, 'rgba(255,255,255,.055)');
    ctx.save(); roundedPath(ctx, x, y, width, height, 14); ctx.clip();
    if (image) {
      const size = Math.min(config.maxWidth || width, width) * (config.scale || 1);
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
    fillRoundRect(ctx, rowX, y + 7, rowWidth, rowHeight - 14, 12, '#202023');
    const logoW = 122, logoH = 70, logoX = rowX + 20, logoY = y + (rowHeight - logoH) / 2;
    drawLogo(ctx, item, logoX, logoY, logoW, logoH);
    const copyX = rowX + 162;
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#f7f7f8'; ctx.font = '650 24px Inter, sans-serif';
    const eventTitle = truncateText(ctx, item.eventName, 338);
    ctx.fillText(eventTitle, copyX, y + 52);
    drawCircularFlag(ctx, item.countryCode, copyX + ctx.measureText(eventTitle).width + 12, y + 29, 28);
    ctx.fillStyle = '#8e8e96'; ctx.font = '500 16px Inter, sans-serif';
    const subline = item.circuitName && item.circuitName !== item.eventName ? `${item.seriesName} · ${item.circuitName}` : item.seriesName;
    ctx.fillText(truncateText(ctx, subline, 390), copyX, y + 77);
    const label = sessionLabel(item), labelX = x + 592, labelW = 178, labelH = 52;
    const labelY = y + (rowHeight - labelH) / 2;
    fillRoundRect(ctx, labelX, labelY, labelW, labelH, 9, 'rgba(255,255,255,.018)');
    ctx.strokeStyle = 'rgba(255,255,255,.22)'; ctx.lineWidth = 1.5;
    roundedPath(ctx, labelX + .75, labelY + .75, labelW - 1.5, labelH - 1.5, 8.25); ctx.stroke();
    let labelFontSize = label.length > 17 ? 14 : 16;
    ctx.font = `650 ${labelFontSize}px Inter, sans-serif`;
    while (labelFontSize > 12 && ctx.measureText(label).width > labelW - 22) {
      labelFontSize -= 1;
      ctx.font = `650 ${labelFontSize}px Inter, sans-serif`;
    }
    ctx.fillStyle = '#b8b8bf'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(label, labelX + labelW/2, y + rowHeight/2 + 1);
    const timeX = x + 790, timeW = 146;
    fillRoundRect(ctx, timeX, labelY, timeW, labelH, 9, '#000000');
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = item.isTbc ? '650 17px Inter, sans-serif' : '700 28px Inter, sans-serif';
    ctx.fillText(item.time, timeX + timeW/2, y + rowHeight / 2 + 1);
  }

  function drawDayGroup(ctx, group, y, rowHeight) {
    const x = 52, width = 976, headerHeight = 64;
    const totalHeight = headerHeight + group.items.length * rowHeight;
    ctx.save(); roundedPath(ctx, x, y, width, totalHeight, 22); ctx.clip();
    const panelGradient = ctx.createLinearGradient(x, y, x + width, y + totalHeight);
    panelGradient.addColorStop(0, '#1d0a0e'); panelGradient.addColorStop(1, '#0d0d0f');
    ctx.fillStyle = panelGradient; ctx.fillRect(x, y, width, totalHeight);
    ctx.fillStyle = '#231014'; ctx.fillRect(x, y, width, headerHeight);
    const heading = dayHeading(group.dayKey);
    ctx.fillStyle = '#f7f7f8'; ctx.font = '650 24px Inter, sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    const dayLabel = `${heading.day.toUpperCase()}${group.continuation ? ' · CONTINUED' : ''}`;
    ctx.fillText(dayLabel, x + 24, y + headerHeight/2 + 1);
    const dateX = x + 24 + ctx.measureText(dayLabel).width + 20;
    ctx.fillStyle = '#929299'; ctx.font = '550 20px Inter, sans-serif'; ctx.textAlign = 'left';
    ctx.fillText(heading.date.toUpperCase(), dateX, y + headerHeight/2 + 1);
    let rowY = y + headerHeight;
    group.items.forEach(item => { drawSessionRow(ctx, item, rowY, rowHeight); rowY += rowHeight; });
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,88,105,.18)'; ctx.lineWidth = 1; roundedPath(ctx, x + .5, y + .5, width - 1, totalHeight - 1, 22); ctx.stroke();
    return rowY;
  }

  function drawFooter(ctx, slideNumber, totalSlides) {
    ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fillRect(68, 1236, 944, 1);
    drawBrandIcon(ctx, 68, 1266, 48, 13);
    ctx.fillStyle = '#f5f5f7'; ctx.font = '650 26px Inter, sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillText('RaceDay', 132, 1300);
    if (totalSlides > 1) { ctx.fillStyle='#77777f';ctx.font='550 15px Inter, sans-serif';ctx.textAlign='right';ctx.fillText(`${slideNumber} of ${totalSlides}`, 1010, 1297); }
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
      const rowHeight = 108;
      let y = 316;
      slide.groups.forEach((group, groupIndex) => { if (groupIndex) y += 14; y = drawDayGroup(ctx, group, y, rowHeight); });
    }
    drawFooter(ctx, index + 1, totalSlides);
    updateNavigation();
  }

  // ── Preview UI and PNG export ─────────────────────────────────────────────

  function rebuildWarnings() {
    const selected = instagramState.allSessions.filter(item => instagramState.selectedIds.has(item.uid));
    const missingLogos = [...new Set(selected.filter(item => {
      const config = window.RACEDAY_INSTAGRAM_LOGOS?.[item.seriesId];
      return !config || (instagramState.assetLoadComplete && !instagramState.images.has(config.src));
    }).map(item => item.seriesName))];
    const missingCountries = selected.filter(item => !VALID_FLAG_CODES.has(item.countryCode)).length;
    const messages = [];
    if (!instagramState.allSessions.length) messages.push('Geen sessies gevonden voor dit weekend. Synchroniseer eerst de kalender.');
    if (instagramState.sourceWarningCount) messages.push(`${instagramState.sourceWarningCount} sessie(s) zonder geldige datum zijn overgeslagen.`);
    if (missingLogos.length) messages.push(`Tekstfallback voor ontbrekend logo: ${missingLogos.join(', ')}.`);
    if (missingCountries) messages.push(`${missingCountries} sessie(s) gebruiken een neutrale vlagfallback.`);
    instagramState.warnings = messages;
    const warning = document.getElementById('instagramWarning');
    if (warning) { warning.textContent = messages.join(' '); warning.classList.toggle('show', Boolean(messages.length)); }
  }

  function renderSessionControls() {
    const list = document.getElementById('instagramSessionList');
    if (!list) return;
    if (!instagramState.allSessions.length) { list.innerHTML = '<div class="empty compact"><h3>Geen sessies</h3><p>Synchroniseer de kalender en probeer opnieuw.</p></div>'; return; }
    let previousDay = '';
    list.innerHTML = instagramState.allSessions.map(item => {
      const heading = dayHeading(item.dayKey);
      const day = previousDay !== item.dayKey ? `<div class="instagram-day-label">${heading.day} · ${heading.date}</div>` : '';
      previousDay = item.dayKey;
      return `${day}<label class="instagram-session-toggle">
        <input type="checkbox" data-instagram-uid="${esc(item.uid)}" ${instagramState.selectedIds.has(item.uid) ? 'checked' : ''} onchange="toggleInstagramSession(this.dataset.instagramUid, this.checked)">
        <span class="instagram-session-copy"><strong>${esc(item.eventName)} · ${esc(sessionLabel(item))}</strong><span>${esc(item.seriesName)}</span></span>
        <span class="instagram-session-time">${esc(item.time)}</span>
      </label>`;
    }).join('');
  }

  function rebuildSlides() {
    const selected = instagramState.allSessions.filter(item => instagramState.selectedIds.has(item.uid));
    instagramState.slides = buildSlides(selected);
    instagramState.slideIndex = Math.min(instagramState.slideIndex, Math.max(0, instagramState.slides.length - 1));
    rebuildWarnings(); renderSlide();
    const selectedLabel = document.getElementById('instagramSelectionSummary');
    if (selectedLabel) selectedLabel.textContent = `${selected.length} van ${instagramState.allSessions.length} sessies geselecteerd · ${Math.max(1, instagramState.slides.length)} slide${instagramState.slides.length === 1 ? '' : 's'}`;
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
    instagramState.weekend = weekendRangeFor();
    const result = collectWeekendSessions(instagramState.weekend);
    instagramState.allSessions = result.sessions;
    instagramState.sourceWarningCount = result.warnings.length;
    instagramState.assetLoadComplete = false;
    instagramState.selectedIds = new Set(result.sessions.filter(item => item.enabledByDefault).map(item => item.uid));
    instagramState.slideIndex = 0;
    const modal = document.getElementById('instagramModal');
    modal?.classList.add('show'); document.body.style.overflow = 'hidden';
    renderSessionControls(); rebuildWarnings();
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
    const selected = instagramState.allSessions.filter(item => instagramState.selectedIds.has(item.uid));
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
  window.navigateInstagramSlide = navigateInstagramSlide;
  window.downloadInstagramPng = downloadInstagramPng;
  window.RaceDayInstagram = {
    collectWeekendSessions, buildSlides, sessionInstant, localTimeInfo, sessionLabel,
    weekendRangeFor, renderSlide, state: instagramState, WIDTH, HEIGHT,
  };
})();
