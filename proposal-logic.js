(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.RaceDayProposals = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const REVIEWABLE = new Set(['open', 'requires_review']);
  const ACTIVE = new Set(['open', 'requires_review', 'unresolved']);

  function sourceConfirmed(item) {
    return item?.seriesId !== 'f1' || item?.source?.confirmationPolicy === 'f1-published-schedule-v1';
  }

  function proposalDate(item) {
    return item?.proposed?.date || item?.sourceTime?.date || item?.current?.date || '';
  }

  function proposalTime(item) {
    return item?.proposed?.timeLocal || item?.sourceTime?.time || item?.current?.timeLocal || '';
  }

  function compare(left, right) {
    const leftKey = `${proposalDate(left) || '9999-99-99'}T${proposalTime(left) || '99:99'}`;
    const rightKey = `${proposalDate(right) || '9999-99-99'}T${proposalTime(right) || '99:99'}`;
    return leftKey.localeCompare(rightKey) ||
      String(left?.seriesName || left?.seriesId || '').localeCompare(String(right?.seriesName || right?.seriesId || ''), 'nl') ||
      String(left?.eventName || '').localeCompare(String(right?.eventName || ''), 'nl') ||
      String(left?.sessionName || '').localeCompare(String(right?.sessionName || ''), 'nl');
  }

  function sessionTarget(item) {
    const session = item?.sessionId || item?.proposed?.sessionId || '';
    const semantic = `${item?.proposed?.kind || ''}:${item?.proposed?.name || item?.sessionName || ''}`.toLowerCase();
    return `${item?.calendarFile || ''}:${item?.eventId || ''}:${session || semantic}`;
  }

  function sameTarget(left, right) {
    if (!left || !right) return false;
    const leftSession = left.proposed?.sessionId || left.sessionId || '';
    const rightSession = right.proposed?.sessionId || right.sessionId || '';
    return left.calendarFile === right.calendarFile &&
      left.eventId === right.eventId &&
      leftSession === rightSession &&
      (left.proposed?.date || '') === (right.proposed?.date || '') &&
      (left.proposed?.timeLocal || '') === (right.proposed?.timeLocal || '') &&
      (left.proposed?.name || left.sessionName || '') === (right.proposed?.name || right.sessionName || '');
  }

  function openCount(items) {
    return (items || []).filter(item => REVIEWABLE.has(item.status) && sourceConfirmed(item)).length;
  }

  function filtered(items, filters = {}) {
    return (items || []).filter(item => {
      if (filters.seriesId && item.seriesId !== filters.seriesId) return false;
      if (filters.eventId && item.eventId !== filters.eventId) return false;
      if (ACTIVE.has(item.status) && !sourceConfirmed(item)) return false;
      if (filters.status === 'active' && !ACTIVE.has(item.status)) return false;
      if (filters.status && filters.status !== 'active' && item.status !== filters.status) return false;
      const itemDate = proposalDate(item);
      if (filters.year && !itemDate.startsWith(`${filters.year}-`)) return false;
      if (filters.periodDays) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(itemDate)) return false;
        const today = new Date(filters.now || Date.now());
        today.setHours(0, 0, 0, 0);
        const end = new Date(today);
        end.setDate(today.getDate() + Number(filters.periodDays));
        const eventDate = new Date(`${itemDate}T00:00:00`);
        if (eventDate < today || eventDate >= end) return false;
      }
      return true;
    }).sort(compare);
  }

  function grouped(items, filters = {}) {
    return filtered(items, filters).reduce((groups, item) => {
      const key = `${item.calendarFile || ''}:${item.eventId || item.eventName || ''}`;
      if (!groups[key]) groups[key] = {
        key,
        calendarFile: item.calendarFile,
        eventId: item.eventId,
        eventName: item.eventName,
        seriesId: item.seriesId,
        seriesName: item.seriesName,
        proposals: [],
      };
      groups[key].proposals.push(item);
      return groups;
    }, {});
  }

  function findTarget(calendarFiles, proposal) {
    const rounds = calendarFiles?.[proposal.calendarFile];
    const round = rounds?.find(item => item.id === proposal.eventId);
    if (!round) throw new Error('Event niet gevonden in de conceptkalender.');
    return { rounds, round };
  }

  function sessionAlias(name) {
    return String(name || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
      .replace(/^free practice\b/, 'practice')
      .replace(/\bwarm up\b/g, 'warmup')
      .replace(/\b(?:top 10 shootout|ttso)\b/g, 'top ten shootout')
      .replace(/^(race|qualifying|top ten shootout) 1$/, '$1');
  }

  function sessionFamily(name) {
    const value = sessionAlias(name);
    return (value.match(/\b(?:warmup|shootout|superpole|hyperpole|night|bronze|pre qualifying|final|high line|cancelled|canceled|resumption|rescheduled|gt[234]|lmgt3|lmp[23]|hypercar|pro am|group [a-z]|top \d+|fast \d+)\b/g) || []).sort().join('|');
  }

  function matchingCalendarSession(calendarFiles, proposal) {
    const rounds = calendarFiles?.[proposal?.calendarFile];
    const round = rounds?.find(item => item.id === proposal?.eventId);
    if (!round || !proposal?.proposed) return null;
    const exactIds = (round.sessions || []).filter(item => item.id === (proposal.sessionId || proposal.proposed.sessionId));
    if (exactIds.length > 1) return null;
    if (exactIds.length === 1) return exactIds[0];
    const matches = (round.sessions || []).filter(item =>
      sessionAlias(item.name) === sessionAlias(proposal.proposed.name || proposal.sessionName) &&
      item.kind === proposal.proposed.kind &&
      (item._date || item.date || '') === proposal.proposed.date &&
      (item._time || item.timeLocal || item.timeUTC || '') === proposal.proposed.timeLocal
    );
    return matches.length === 1 ? matches[0] : null;
  }

  function isFulfilled(calendarFiles, proposal) {
    const session = matchingCalendarSession(calendarFiles, proposal);
    if (!session || session._tbcMode || !proposal?.proposed) return false;
    return (session._date || session.date || '') === proposal.proposed.date &&
      (session._time || session.timeLocal || session.timeUTC || '') === proposal.proposed.timeLocal &&
      sessionAlias(session.name) === sessionAlias(proposal.proposed.name) && session.kind === proposal.proposed.kind &&
      (!['sf', 'supergt', 'f1academy', 'elms', 'fd'].includes(proposal.seriesId) ||
        proposal.proposed.durationMinutes == null || session.durationMinutes === proposal.proposed.durationMinutes);
  }

  function mergeLocalDecisions(remote, localItems, calendarFiles) {
    const localByFingerprint = new Map((localItems || []).map(item => [item.fingerprint, item]));
    return (remote || []).map(item => {
      const local = localByFingerprint.get(item.fingerprint);
      if (!local || !['accepted', 'rejected'].includes(local.status)) return item;
      // A failed old batch may have saved its decision without its calendar
      // edit. Keep acceptance only when the local calendar actually contains it.
      if (local.status === 'accepted' && !isFulfilled(calendarFiles, item)) return item;
      return { ...item, status: local.status, ...(local.decisionAt ? { decisionAt: local.decisionAt } : {}) };
    });
  }

  function reconcile(items, calendarFiles) {
    const proposals = items || [];
    proposals.forEach(item => {
      if (ACTIVE.has(item.status) && !sourceConfirmed(item)) {
        item.status = 'superseded';
        item.reason = 'F1-bron opnieuw controleren: eerdere interne tijdwaarden waren niet bevestigd in het gepubliceerde tijdschema.';
        return;
      }
      if (REVIEWABLE.has(item.status) && isFulfilled(calendarFiles, item)) {
        item.status = 'accepted';
        item.reconciled = true;
        item.decisionAt ||= new Date().toISOString();
      }
    });
    const newestByTarget = new Map();
    [...proposals].sort((a, b) => {
      const checked = String(a?.source?.checkedAt || '').localeCompare(String(b?.source?.checkedAt || ''));
      return checked || String(a?.fingerprint || '').localeCompare(String(b?.fingerprint || ''));
    }).forEach(item => {
      if (ACTIVE.has(item.status)) newestByTarget.set(sessionTarget(item), item);
    });
    proposals.forEach(item => {
      const newest = newestByTarget.get(sessionTarget(item));
      if (newest && newest !== item && ACTIVE.has(item.status)) {
        item.status = 'superseded';
        item.supersededBy = newest.fingerprint;
        item.supersededAt = newest?.source?.checkedAt || new Date().toISOString();
      }
    });
    return proposals;
  }

  function sortCalendarSessions(sessions) {
    return (sessions || []).sort((left, right) => {
      const leftKey = `${left?._date || left?.date || left?.tbcDate || '9999-99-99'}T${left?._time || left?.timeLocal || left?.timeUTC || '99:99'}`;
      const rightKey = `${right?._date || right?.date || right?.tbcDate || '9999-99-99'}T${right?._time || right?.timeLocal || right?.timeUTC || '99:99'}`;
      return leftKey.localeCompare(rightKey) || String(left?.name || '').localeCompare(String(right?.name || ''), 'nl');
    });
  }

  function apply(calendarFiles, proposal) {
    if (proposal.status !== 'open' || !proposal.proposed || proposal.stale || !sourceConfirmed(proposal)) {
      throw new Error('Alleen een betrouwbaar open voorstel kan worden geaccepteerd.');
    }
    const { round } = findTarget(calendarFiles, proposal);
    if (isFulfilled(calendarFiles, proposal)) return { type: 'noop', sessionId: matchingCalendarSession(calendarFiles, proposal).id };
    if (!Array.isArray(round.sessions)) round.sessions = [];
    if (proposal.proposalType === 'new-session') {
      const sameValues = item => !item._tbcMode && sessionAlias(item.name) === sessionAlias(proposal.proposed.name) &&
        item.kind === proposal.proposed.kind &&
        (item._date || item.date) === proposal.proposed.date &&
        (item._time || item.timeLocal) === proposal.proposed.timeLocal;
      const fulfilled = round.sessions.find(sameValues);
      if (fulfilled) return { type: 'noop', sessionId: fulfilled.id };
      const sameSession = round.sessions.find(item => sessionAlias(item.name) === sessionAlias(proposal.proposed.name) &&
        item.kind === proposal.proposed.kind && (item._date || item.date) === proposal.proposed.date);
      if (sameSession) throw new Error(`De nieuwe sessie ${proposal.proposed.name} bestaat al met een andere tijd. Controleer je concept of vernieuw de broncontrole.`);
      let createdId = proposal.proposed.sessionId;
      if (!createdId) throw new Error('Het voorstel bevat geen sessie-ID. Vernieuw de broncontrole.');
      let suffix = 2;
      while (round.sessions.some(item => item.id === createdId)) createdId = `${proposal.proposed.sessionId}-${suffix++}`;
      const created = {
        id: createdId,
        name: proposal.proposed.name,
        kind: proposal.proposed.kind,
        date: proposal.proposed.date,
        timeLocal: proposal.proposed.timeLocal,
        durationMinutes: proposal.proposed.durationMinutes,
      };
      round.sessions.push(created);
      sortCalendarSessions(round.sessions);
      return { type: 'new-session', sessionId: created.id };
    }
    if (round.sessions.filter(item => item.id === proposal.sessionId).length > 1) throw new Error('Meerdere conceptsessies hebben hetzelfde ID. Controleer de kalender.');
    let index = round.sessions.findIndex(item => item.id === proposal.sessionId);
    if (index < 0) {
      const current = proposal.current;
      const expectedKind = current?.kind || proposal.proposed.kind;
      const candidates = current && Object.hasOwn(current, 'date') && Object.hasOwn(current, 'timeLocal')
        ? round.sessions.map((item, index) => ({ item, index })).filter(({ item }) =>
          item.kind === expectedKind &&
          (item._date || item.date || item.tbcDate || null) === (current.date || null) &&
          (item._time || item.timeLocal || item.timeUTC || null) === (current.timeLocal || null) &&
          (!current.name || item.name === current.name))
        : [];
      if (candidates.length !== 1) throw new Error(candidates.length > 1
        ? 'Meerdere conceptsessies passen bij dit voorstel. Controleer de sessies en vernieuw de broncontrole.'
        : 'Sessie niet gevonden: de conceptkalender wijkt af van de gescande kalender. Synchroniseer de kalender of publiceer je concept en vernieuw de broncontrole.');
      index = candidates[0].index;
    }
    const session = round.sessions[index];
    if (sessionFamily(session.name) !== sessionFamily(proposal.proposed.name)) {
      throw new Error('Het voorstel past bij een ander sessietype of een andere klasse. Vernieuw de broncontrole en controleer de koppeling.');
    }
    const current = proposal.current;
    const currentTime = session._tbcMode ? null : (session._time || session.timeLocal || session.timeUTC || null);
    if (!current || !Object.hasOwn(current, 'date') || !Object.hasOwn(current, 'timeLocal') ||
        (session._date || session.date || null) !== (current.date || null) ||
        currentTime !== (current.timeLocal || null) ||
        (current.name && session.name !== current.name) ||
        (current.kind && session.kind !== current.kind) ||
        (Object.hasOwn(current, 'durationMinutes') && session.durationMinutes !== current.durationMinutes)) {
      throw new Error('De conceptsessie is gewijzigd sinds de broncontrole. Vernieuw de broncontrole voordat je dit voorstel accepteert.');
    }
    const before = JSON.parse(JSON.stringify(session));
    session.name = proposal.proposed.name || session.name;
    session.kind = proposal.proposed.kind || session.kind;
    session.date = proposal.proposed.date;
    session.timeLocal = proposal.proposed.timeLocal;
    session.durationMinutes = proposal.proposed.durationMinutes || session.durationMinutes;
    session._date = proposal.proposed.date;
    session._time = proposal.proposed.timeLocal;
    session._tbcMode = false;
    delete session.timeUTC;
    delete session.dateUTC;
    delete session.tbcDate;
    sortCalendarSessions(round.sessions);
    return { type: 'time-update', sessionId: session.id, before };
  }

  function undo(calendarFiles, proposal, undoRecord) {
    const { round } = findTarget(calendarFiles, proposal);
    if (!undoRecord) throw new Error('Geen ongedaan-maakgegevens beschikbaar.');
    if (undoRecord.type === 'noop') return;
    if (undoRecord.type === 'new-session') {
      const index = round.sessions.findIndex(item => item.id === undoRecord.sessionId);
      if (index >= 0) round.sessions.splice(index, 1);
      sortCalendarSessions(round.sessions);
      return;
    }
    const index = round.sessions.findIndex(item => item.id === undoRecord.sessionId);
    if (index < 0) throw new Error('Gewijzigde sessie niet gevonden.');
    round.sessions[index] = JSON.parse(JSON.stringify(undoRecord.before));
    sortCalendarSessions(round.sessions);
  }

  return { sourceConfirmed, mergeLocalDecisions, REVIEWABLE, ACTIVE, openCount, filtered, grouped, proposalDate, proposalTime, compare, sameTarget, sessionTarget, isFulfilled, reconcile, sortCalendarSessions, apply, undo };
});
