(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.RaceDayProposals = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const REVIEWABLE = new Set(['open', 'requires_review']);

  function openCount(items) {
    return (items || []).filter(item => REVIEWABLE.has(item.status)).length;
  }

  function filtered(items, filters = {}) {
    return (items || []).filter(item => {
      if (filters.seriesId && item.seriesId !== filters.seriesId) return false;
      if (filters.status && item.status !== filters.status) return false;
      return true;
    });
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

  function apply(calendarFiles, proposal) {
    if (proposal.status !== 'open' || !proposal.proposed) {
      throw new Error('Alleen een betrouwbaar open voorstel kan worden geaccepteerd.');
    }
    const { round } = findTarget(calendarFiles, proposal);
    if (!Array.isArray(round.sessions)) round.sessions = [];
    if (proposal.proposalType === 'new-session') {
      if (round.sessions.some(item => item.id === proposal.proposed.sessionId)) {
        throw new Error('Deze conceptsessie bestaat al.');
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
    return { type: 'time-update', sessionId: session.id, before };
  }

  function undo(calendarFiles, proposal, undoRecord) {
    const { round } = findTarget(calendarFiles, proposal);
    if (!undoRecord) throw new Error('Geen ongedaan-maakgegevens beschikbaar.');
    if (undoRecord.type === 'new-session') {
      const index = round.sessions.findIndex(item => item.id === undoRecord.sessionId);
      if (index >= 0) round.sessions.splice(index, 1);
      return;
    }
    const index = round.sessions.findIndex(item => item.id === undoRecord.sessionId);
    if (index < 0) throw new Error('Gewijzigde sessie niet gevonden.');
    round.sessions[index] = JSON.parse(JSON.stringify(undoRecord.before));
  }

  return { REVIEWABLE, openCount, filtered, grouped, apply, undo };
});
