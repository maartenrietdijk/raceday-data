(function (root) {
  'use strict';
  function normalizeSourceUrl(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    let url;
    try { url = new URL(text); } catch (_) { throw new Error('Vul een volledige HTTPS-link naar de officiële eventpagina in.'); }
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443') ||
        !url.hostname.includes('.') || /^\d+(?:\.\d+){3}$/.test(url.hostname) || url.hostname.includes(':') ||
        /(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(url.hostname)) {
      throw new Error('Gebruik een openbare HTTPS-link zonder inloggegevens.');
    }
    url.hash = '';
    return url.href;
  }
  function mergeLinks(rounds, links, strict = true) {
    if (!Array.isArray(rounds)) throw new Error('De kalender op GitHub heeft een ongeldige structuur.');
    if (strict && Object.keys(links).some(id => rounds.filter(round => round.id === id).length !== 1)) {
      throw new Error('Een event ontbreekt op GitHub of heeft een dubbele ID. Publiceer de kalender eerst via Agenda.');
    }
    return rounds.map(round => {
      if (!Object.prototype.hasOwnProperty.call(links, round.id)) return round;
      const next = { ...round };
      const url = normalizeSourceUrl(links[round.id]);
      if (url) next.officialScheduleUrl = url;
      else delete next.officialScheduleUrl;
      return next;
    });
  }
  function offsetMinutes(zone, instant) {
    const fields = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(instant).filter(item => item.type !== 'literal').map(item => [item.type, item.value]));
    return Math.round((Date.UTC(+fields.year, +fields.month - 1, +fields.day, +fields.hour, +fields.minute, +fields.second) - instant.getTime()) / 60000);
  }
  function timezoneInfo(zone, instant = new Date()) {
    const delta = offsetMinutes(zone, instant) - offsetMinutes('Europe/Amsterdam', instant);
    const hours = Math.floor(Math.abs(delta) / 60), minutes = Math.abs(delta) % 60;
    const amount = [hours ? `${hours} uur` : '', minutes ? `${minutes} minuten` : ''].filter(Boolean).join(' en ');
    const clock = z => new Intl.DateTimeFormat('nl-NL', { timeZone: z, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(instant);
    return { zone, delta, localTime: clock(zone), nlTime: clock('Europe/Amsterdam'), difference: delta === 0 ? 'Gelijk aan Nederland' : `${amount} ${delta > 0 ? 'voor' : 'achter'} op Nederland` };
  }
  const api = { normalizeSourceUrl, mergeLinks, timezoneInfo };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RaceDayLinks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
