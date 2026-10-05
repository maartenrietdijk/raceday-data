"""Strict readers for the official BSB and Formula DRIFT event timetables.

No guessed clocks, fuzzy event selection, or generic page-wide table fallback.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse


def nodes(node):
    yield node
    for child in node.children:
        yield from nodes(child)


def tree(body):
    from official_schedule_scanner import _NascarHTMLTree
    parser = _NascarHTMLTree()
    parser.feed(body)
    parser.close()
    return list(nodes(parser.root))


def bsb_dates(calendar, event, year, event_url):
    from official_schedule_scanner import SourceError, normalize
    path = urlparse(event_url).path.rstrip('/')
    if not re.fullmatch(r'/calendar/' + str(year) + r'/r' + str(event['roundNumber']) + r'-[a-z-]+', path):
        raise SourceError('BSB: wrong season or round in event URL')
    all_nodes = tree(calendar.body)
    if not any(n.tag == 'h1' and n.text() == f'{year} Calendar' for n in all_nodes):
        raise SourceError('BSB: official calendar does not confirm this season')
    cards = [n for n in all_nodes if 'round-info' in n.attrs.get('class', '').split()
             and any(urlparse(c.attrs.get('href', '')).path.rstrip('/') == path for c in nodes(n) if c.tag == 'a')]
    if len(cards) != 1:
        raise SourceError('BSB: event absent or ambiguous in the official calendar')
    headers = [n.text() for n in nodes(cards[0]) if n.tag == 'h3']
    if len(headers) != 2 or normalize(headers[1]) != normalize(event['raceName']):
        raise SourceError('BSB: official circuit differs from selected event')
    # The publisher uses "31 Jul - 02 Aug" and "16 - 18 Oct".
    m = re.fullmatch(r'(\d{1,2})\s*(\w{3})?\s*-\s*(\d{1,2})\s+(\w{3})', headers[0])
    if not m:
        raise SourceError('BSB: unrecognised official event dates')
    try:
        month = lambda s: datetime.strptime(s, '%b').month
        start = date(year, month(m[2] or m[4]), int(m[1]))
        end = date(year, month(m[4]), int(m[3]))
    except ValueError as exc:
        raise SourceError('BSB: invalid official dates') from exc
    if not 1 <= (end-start).days <= 4:
        raise SourceError('BSB: invalid weekend range')
    return [start + timedelta(days=i) for i in range((end-start).days+1)]


def parse_bsb_schedule(source, calendar, event, year):
    from official_schedule_scanner import SourceError, SourceSession
    days = bsb_dates(calendar, event, year, source.final_url)
    zone = 'Europe/Amsterdam' if urlparse(source.final_url).path.endswith('-assen') else 'Europe/London'
    page_nodes = tree(source.body)
    expected_title = f"R{event['roundNumber']} {event['raceName']}"
    if not any(n.tag == "title" and n.text() == expected_title for n in page_nodes):
        raise SourceError("BSB: page title does not confirm the selected round and circuit")
    result = []
    for n in page_nodes:
        if n.tag != 'li':
            continue
        descendants = list(nodes(n))
        clocks = [c.text() for c in descendants if c.tag == 'span' and 'time' in c.attrs.get('class','').split()]
        weekdays = [c.text() for c in descendants if c.tag == 'strong']
        labels = [c.text() for c in descendants if c.tag == 'div' and c.attrs.get('class') == 'cell auto']
        if not clocks or not weekdays or not labels:
            continue
        label = labels[0]
        m = re.fullmatch(r'(?:ROKiT Oxygen Performance |Omologato )?(Free Practice [123]|Pre Qualifying|Qualifying(?: [12])?|Superpole|Race [123]|Warm up)', label, re.I)
        if not m:
            continue
        name = m[1]
        if name.lower() == 'warm up': name = 'Warm Up'
        if len(clocks) != 1 or len(weekdays) != 1 or len(labels) != 1:
            raise SourceError('BSB: ambiguous schedule row')
        day = [d for d in days if d.strftime('%a') == weekdays[0]]
        if len(day) != 1:
            raise SourceError('BSB: session weekday lies outside the official weekend')
        clock = re.fullmatch(r'([0-2]\d:[0-5]\d) (BST|GMT|CET|CEST)', clocks[0])
        if not clock or int(clock[1][:2]) > 23:
            raise SourceError('BSB: competitive session time is unpublished or invalid')
        if (zone == 'Europe/London') != (clock[2] in {'BST','GMT'}):
            raise SourceError('BSB: clock zone contradicts venue')
        # Assen's page labels summer clocks CET. Use the geographical IANA
        # zone rather than a fixed offset from this informal publisher label.
        result.append(SourceSession(name, day[0].isoformat(), clock[1], zone))
    names = [s.name for s in result]
    required = {'Free Practice 1','Free Practice 2','Free Practice 3','Pre Qualifying','Race 1','Race 2','Race 3','Warm Up'}
    qualifying = {'Qualifying 1','Qualifying 2'} <= set(names) or {'Qualifying','Superpole'} <= set(names)
    if not required <= set(names) or not qualifying or len(names) != len(set(names)):
        raise SourceError('BSB: incomplete or conflicting championship timetable')
    return result


def parse_fd_schedule(source, event, year, zone):
    from official_schedule_scanner import SourceError, SourceSession, normalize, parse_clock
    if not zone:
        raise SourceError('FD: venue timezone is unknown')
    if not re.fullmatch(r'/schedule/' + str(year) + r'/[a-z0-9-]+', urlparse(source.final_url).path.rstrip('/')):
        raise SourceError('FD: wrong season or unsupported event URL')
    all_nodes = tree(source.body)
    headings = [n.text() for n in all_nodes if n.tag == 'h1']
    if len(headings) != 1 or normalize(re.sub(r'\s+pro(?:\s+prospec)?$', '', headings[0], flags=re.I)) != normalize(event['raceName']):
        raise SourceError('FD: official event title differs from selected event')
    if not re.search(r"\bpro\b", headings[0], re.I):
        raise SourceError("FD: event is not confirmed as PRO")
    mixed = bool(re.search(r'\bprospec\b', headings[0], re.I))
    result = []
    for article in all_nodes:
        if article.tag != 'article' or article.attrs.get('role') != 'listitem':
            continue
        day_headers = [n.text() for n in nodes(article) if n.tag == 'h4']
        if len(day_headers) != 1:
            raise SourceError('FD: ambiguous day header')
        try:
            day = datetime.strptime(day_headers[0], '%A, %B %d, %Y').date()
        except ValueError as exc:
            raise SourceError('FD: invalid official schedule date') from exc
        if day.year != year or day.strftime('%A') != day_headers[0].split(',')[0]:
            raise SourceError('FD: schedule weekday or year differs')
        for row in nodes(article):
            if row.tag != 'div' or not any(c.tag == 'dt' for c in row.children): continue
            times = [n.text() for n in row.children if n.tag == 'dt']
            labels = [n.text() for n in row.children if n.tag == 'dd']
            if len(times) != 1 or len(labels) != 1: raise SourceError('FD: ambiguous schedule row')
            label = labels[0].upper()
            if 'PROSPEC' in label: continue
            if mixed and not re.search(r'\bPRO\b', label): continue
            clean = re.sub(r'https?://\S+', '', label, flags=re.I)
            clean = re.sub(r'^LIVESTREAM:\s*', '', clean).strip(' () ')
            clean = re.sub(r'^PRO:\s*|^PRO\s+', '', clean)
            if re.fullmatch(r'(?:OPENING CEREMONY \+ )?(?:MAIN EVENT: )?TOP 32', clean): name = 'Top 32'
            elif re.fullmatch(r'(?:OPENING CEREMONY \+ )?(?:MAIN EVENT: )?TOP 16',clean): name = 'Top 16'
            elif clean == 'QUALIFYING': name = 'Qualifying'
            elif re.fullmatch(r'(?:PRO:\s*)?WARM\s*UP',label): name = 'Warmup'
            elif re.fullmatch(r'(?:PRO:\s*)?PRACTICE',label): name = 'Practice'
            else: continue
            span = re.fullmatch(r'(\d{1,2}:\d{2}[AP]M)\s*-\s*(\d{1,2}:\d{2}[AP]M)', times[0],re.I)
            if not span: raise SourceError('FD: competitive session has no confirmed time range')
            start, end = parse_clock(span[1]), parse_clock(span[2])
            duration = (int(end[:2])*60+int(end[3:]))-(int(start[:2])*60+int(start[3:]))
            if not 0 < duration <= 360: raise SourceError('FD: invalid or overnight session duration')
            result.append(SourceSession(name,day.isoformat(),start,zone,duration))
    result.sort(key=lambda s:(s.date,s.local_time))
    for name in ['Qualifying','Top 32','Top 16']:
        if sum(s.name==name for s in result)!=1:
            raise SourceError('FD: missing or conflicting PRO qualifying / elimination schedule')
    if any(s.get('kind')=='practice' for s in event.get('sessions', [])) and not any(s.name=='Practice' for s in result):
        raise SourceError('FD: missing PRO practice')
    if not any(s.name=='Warmup' for s in result): raise SourceError('FD: missing PRO warmup')
    keys = [(s.name,s.date,s.local_time) for s in result]
    if len(keys)!=len(set(keys)): raise SourceError('FD: duplicated competitive row')
    practice = [s for s in result if s.name=='Practice']
    if len(practice)>1:
        result = [SourceSession(f'Practice {practice.index(s)+1}',s.date,s.local_time,s.timezone,s.duration_minutes) if s.name=='Practice' else s for s in result]
    return result


def specific_kind(source_kind, name, fallback):
    if source_kind == 'bsb-timetable' and name == 'Pre Qualifying': return 'qualifying'
    if source_kind == 'fd-timetable':
        if name == 'Warmup': return 'testing'
        if name in {'Top 32','Top 16'}: return 'race'
    return fallback


def match_fd(official, all_official, existing, available):
    """Duplicate warmups keep their stable calendar slot across UTC date shifts."""
    from official_schedule_scanner import session_alias, normalize_kind
    for slot in existing:
        if re.search(r"\b(cancelled|canceled)\b", slot.get("name", ""), re.I):
            original = re.sub(r"\s*\((?:cancelled|canceled)\)\s*$", "", slot["name"], flags=re.I)
            if session_alias(original) == session_alias(official.name):
                return None, "FD: matching calendar session is cancelled; review manually"
    group = sorted([s for s in all_official if session_alias(s.name)==session_alias(official.name)],key=lambda s:(s.date,s.local_time))
    slots = [s for s in existing if session_alias(s.get('name'))==session_alias(official.name)]
    if len(group)==len(slots) and slots:
        chosen = slots[group.index(official)]
        expected_kind = specific_kind('fd-timetable', official.name, normalize_kind(official.name))
        if chosen.get('kind') != expected_kind:
            return None, 'FD: calendar session kind differs; review manually'
        if chosen.get('id') not in {s.get('id') for s in available}:
            return None, 'FD: calendar slot already matched'
        return chosen, None
    if len(group)>1 or slots:
        return None, 'FD: repeated session count differs; review the calendar structure'
    return None, None
