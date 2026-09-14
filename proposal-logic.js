(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.RaceDayProposals = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const REVIEWABLE = new Set(['open', 'requires_review']);
  const ACTIVE = new Set(['open', 'requires_review', 'unresolved']);

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
    return (items || []).filter(item => REVIEWABLE.has(item.status)).length;
  }

  function filtered(items, filters = {}) {
    return (items || []).filter(item => {
      if (filters.seriesId && item.seriesId !== filters.seriesId) return false;
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

  function matchingCalendarSession(calendarFiles, proposal) {
    const rounds = calendarFiles?.[proposal?.calendarFile];
    const round = rounds?.find(item => item.id === proposal?.eventId);
    if (!round || !proposal?.proposed) return null;
    const exactId = (round.sessions || []).find(item => item.id === (proposal.sessionId || proposal.proposed.sessionId));
    if (exactId) return exactId;
    return (round.sessions || []).find(item =>
      String(item.name || '').toLowerCase() === String(proposal.proposed.name || proposal.sessionName || '').toLowerCase() &&
      item.kind === proposal.proposed.kind &&
      (item._date || item.date || '') === proposal.proposed.date &&
      (item._time || item.timeLocal || '') === proposal.proposed.timeLocal
    ) || null;
  }

  function isFulfilled(calendarFiles, proposal) {
    const session = matchingCalendarSession(calendarFiles, proposal);
    if (!session || !proposal?.proposed) return false;
    return (session._date || session.date || '') === proposal.proposed.date &&
      (session._time || session.timeLocal || '') === proposal.proposed.timeLocal;
  }

  function reconcile(items, calendarFiles) {
    const proposals = items || [];
    proposals.forEach(item => {
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
      const leftKey = `${left?._date || left?.date || left?.tbcDate || '9999-99-99'}T${left?._time || left?.timeLocal || '99:99'}`;
      const rightKey = `${right?._date || right?.date || right?.tbcDate || '9999-99-99'}T${right?._time || right?.timeLocal || '99:99'}`;
      return leftKey.localeCompare(rightKey) || String(left?.name || '').localeCompare(String(right?.name || ''), 'nl');
    });
  }

  function apply(calendarFiles, proposal) {
    if (proposal.status !== 'open' || !proposal.proposed) {
      throw new Error('Alleen een betrouwbaar open voorstel kan worden geaccepteerd.');
    }
    const { round } = findTarget(calendarFiles, proposal);
    if (!Array.isArray(round.sessions)) round.sessions = [];
    if (proposal.proposalType === 'new-session') {
      const existing = round.sessions.find(item => item.id === proposal.proposed.sessionId);
      if (existing) {
        const sameValues = existing.name === proposal.proposed.name &&
          existing.kind === proposal.proposed.kind &&
          existing.date === proposal.proposed.date &&
          existing.timeLocal === proposal.proposed.timeLocal;
        if (sameValues) return { type: 'noop', sessionId: existing.id };
        throw new Error('Er bestaat al een andere conceptsessie met deze ID. Controleer het event handmatig.');
      }
      const created = {
        id: proposal.proposed.sessionId,
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
    const index = round.sessions.findIndex(item => item.id === proposal.sessionId);
    if (index < 0) throw new Error('Sessie niet gevonden in de conceptkalender.');
    const session = round.sessions[index];
    const before = JSON.parse(JSON.stringify(session));
    session.date = proposal.proposed.date;
    session.timeLocal = proposal.proposed.timeLocal;
    session.durationMinutes = proposal.proposed.durationMinutes || session.durationMinutes;
    session._date = proposal.proposed.date;
    session._time = proposal.proposed.timeLocal;
    session._tbcMode = false;
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

  return { REVIEWABLE, ACTIVE, openCount, filtered, grouped, proposalDate, proposalTime, compare, sameTarget, sessionTarget, isFulfilled, reconcile, sortCalendarSessions, apply, undo };
});
