(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.RaceDayUpdates = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  const fields = new Set(['name', 'kind', 'date', 'timeLocal', 'durationMinutes']);
  const kinds = new Set(['practice', 'qualifying', 'hyperpole', 'sprintQualifying', 'sprintRace', 'featureRace', 'shakedown', 'stage', 'testing', 'race']);
  const clone = value => JSON.parse(JSON.stringify(value));
  function fail(message) { throw new Error(message); }
  function object(value) { return value && typeof value === 'object' && !Array.isArray(value); }
  function validate(values, season) {
    if (!object(values) || !Object.keys(values).length) fail('Een sessie-update moet velden bevatten.');
    for (const [key, value] of Object.entries(values)) {
      if (!fields.has(key)) fail(`Onbekend sessieveld: ${key}`);
      if (key === 'name' && (typeof value !== 'string' || !value.trim())) fail('Sessienaam ontbreekt.');
      if (key === 'kind' && !kinds.has(value)) fail(`Onbekend sessietype: ${value}`);
      if (key === 'durationMinutes' && (!Number.isInteger(value) || value < 1 || value > 1440)) fail('Duur moet 1–1440 minuten zijn.');
      if (key === 'timeLocal' && value !== null && (typeof value !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value))) fail('Ongeldige starttijd.');
      if (key === 'date' && (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0,10) !== value || Number(value.slice(0,4)) !== season)) fail('Ongeldige datum of verkeerd seizoen.');
    }
  }
  // Pure planning: validation of the whole file completes before editor state changes.
  function plan(calendarFiles, file, zoneForSeries) {
    if (!object(file) || file.format !== 'raceday-session-update' || file.version !== 1 || !Array.isArray(file.updates) || !file.updates.length) fail('Geen geldig RaceDay-updatebestand (versie 1).');
    const calendars = clone(calendarFiles), changes = [], targets = new Set();
    for (const update of file.updates) {
      if (!object(update) || typeof update.seriesId !== 'string' || !/^[a-z0-9_]+$/.test(update.seriesId) || !Number.isInteger(update.season)) fail('Serie en seizoen ontbreken.');
      const filename = `${update.seriesId}_${update.season}.json`;
      const rounds = calendars[filename];
      if (!Array.isArray(rounds)) fail(`Laad eerst de seizoenskalender ${filename}.`);
      if (typeof update.timeZone !== 'string' || update.timeZone !== zoneForSeries(update.seriesId)) fail(`Gebruik de invoertijdzone ${zoneForSeries(update.seriesId)} voor ${filename}.`);
      const matches = rounds.filter(round => round.id === update.eventId);
      if (matches.length !== 1 || matches[0].seriesId !== update.seriesId) fail(`Event-ID niet uniek of niet gevonden: ${update.eventId}`);
      const round = matches[0];
      if (!Array.isArray(update.sessions) || !update.sessions.length) fail('Sessies ontbreken.');
      for (const patch of update.sessions) {
        if (!object(patch) || typeof patch.id !== 'string' || !patch.id.startsWith(`${round.id}-`) || !object(patch.set)) fail('Ongeldige sessie-ID of velden.');
        const target = `${filename}:${patch.id}`;
        if (targets.has(target)) fail(`Dubbele sessie-update: ${patch.id}`);
        targets.add(target);
        validate(patch.set, update.season);
        const existing = round.sessions.filter(session => session.id === patch.id);
        if (existing.length > 1) fail(`Dubbele sessie-ID: ${patch.id}`);
        let session = existing[0];
        if (!session) {
          if (patch.create !== true) fail(`Sessie-ID niet gevonden: ${patch.id}`);
          if (rounds.some(r => r.sessions.some(s => s.id === patch.id))) fail(`Sessie-ID bestaat bij een ander event: ${patch.id}`);
          for (const field of fields) if (!(field in patch.set)) fail(`Nieuwe sessie mist ${field}.`);
          session = { id: patch.id }; round.sessions.push(session);
          changes.push(`${round.raceName}: + ${patch.set.name} (${patch.id})`);
        }
        for (const [field, value] of Object.entries(patch.set)) {
          const before = field === 'date' ? session._date || session.date : field === 'timeLocal' ? (session._tbcMode ? null : session._time || session.timeLocal) : session[field];
          if (before !== value) changes.push(`${patch.set.name || session.name || patch.id}: ${field}: ${before ?? '—'} → ${value ?? 'TBC'}`);
          session[field] = value;
          if (field === 'date') { delete session._date; delete session.dateUTC; delete session.tbcDate; }
          if (field === 'timeLocal') { delete session._time; delete session.timeUTC; delete session.dateUTC; session._tbcMode = value === null; if (value) session._lastConfirmedTime = value; else delete session._lastConfirmedTime; }
        }
      }
      round.sessions.sort((a,b) => `${a._date || a.date || ''}T${a._time || a.timeLocal || '99:99'}`.localeCompare(`${b._date || b.date || ''}T${b._time || b.timeLocal || '99:99'}`));
    }
    return { calendars, changes };
  }
  return { plan };
});
