"""Pure scheduling constraints. No provider or model calls."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

DEFAULT_PROFILE = {'timezone': 'Asia/Jerusalem', 'work_start': 9, 'work_end': 17,
                   'wake': 7, 'sleep': 22, 'reserve_minutes': 120,
                   'max_commitments': 8, 'buffer_minutes': 15, 'preferences': {}}


def event_interval(event, tz):
    def parse(value):
        if 'dateTime' in value:
            dt = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
            return dt.replace(tzinfo=ZoneInfo(value.get('timeZone', str(tz)))) if dt.tzinfo is None else dt.astimezone(tz)
        return datetime.combine(datetime.fromisoformat(value['date']).date(), time(), tz)
    return parse(event['start']), parse(event['end'])


def compatible(a, b):
    """Only explicitly annotated passive windows may share interruptible activity."""
    if not a or not b:
        return False
    passive, active = (a, b) if a.get('passive') else (b, a)
    return bool(passive.get('passive') and active.get('interruptible')
                and not active.get('continuity')
                and passive.get('location') and passive.get('location') == active.get('location')
                and int(passive.get('attention', 1)) + int(active.get('attention', 1)) <= 1)


def valid_slot(start, achievement, events, profile, now, exclude_id=None):
    tz = ZoneInfo(profile['timezone'])
    start = start.astimezone(tz)
    end = start + timedelta(minutes=achievement['duration'])
    if start <= now or end.date() != start.date():
        return False
    personal = achievement.get('category') == 'personal'
    lower = profile['wake'] if personal else profile['work_start']
    upper = profile['sleep'] if personal else profile['work_end']
    lower = max(lower, profile['wake'], achievement.get('earliest_hour', lower))
    upper = min(upper, profile['sleep'], achievement.get('latest_hour', upper))
    if start < start.replace(hour=lower, minute=0, second=0, microsecond=0) or end > start.replace(hour=upper, minute=0, second=0, microsecond=0):
        return False
    if achievement.get('date') and str(start.date()) != achievement['date']:
        return False
    if achievement.get('deadline') and end > datetime.fromisoformat(achievement['deadline']).astimezone(tz):
        return False
    for window in achievement.get('forbidden_windows', []):
        if (start.hour + start.minute / 60) < window[1] and (end.hour + end.minute / 60) > window[0]:
            return False
    day_start = start.replace(hour=profile['wake'], minute=0, second=0, microsecond=0)
    day_end = start.replace(hour=profile['sleep'], minute=0, second=0, microsecond=0)
    intervals, count = [], 0
    buffer = timedelta(minutes=profile['buffer_minutes'])
    for event in events:
        if event.get('id') == exclude_id or event.get('status') == 'cancelled' or event.get('transparency') == 'transparent':
            continue
        es, ee = event_interval(event, tz)
        if es < day_end and ee > day_start:
            count += 1
            intervals.append((max(es, day_start), min(ee, day_end)))
        resources = event.get('resources')  # trusted structured annotation only, never inferred from title
        if start < ee + buffer and end > es - buffer and not compatible(achievement.get('resources'), resources):
            return False
    if count >= profile['max_commitments']:
        return False
    # Union occupied intervals so overlaps do not double-count capacity.
    occupied = 0
    edge = day_start
    for es, ee in sorted(intervals):
        if ee > edge:
            occupied += (ee - max(es, edge)).total_seconds() / 60
            edge = ee
    available = (day_end - day_start).total_seconds() / 60
    return occupied + achievement['duration'] + 2 * profile['buffer_minutes'] <= available - profile['reserve_minutes']


def propose(achievement, events, profile, now, exclude_id=None):
    tz = ZoneInfo(profile['timezone'])
    now = now.astimezone(tz)
    preferred = achievement.get('preferred_hour', 10)
    slots = []
    for offset in range(8):
        day = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        for minute in range(0, 24 * 60, 15):
            start = day + timedelta(minutes=minute)
            if valid_slot(start, achievement, events, profile, now, exclude_id):
                slots.append((offset * 100 + abs(start.hour + start.minute / 60 - preferred), start))
    if not slots:
        return None
    start = min(slots, key=lambda s: s[0])[1]
    return {'start': start.isoformat(), 'end': (start + timedelta(minutes=achievement['duration'])).isoformat(),
            'reason': 'Fits your hours, avoids calendar conflicts, includes transition time, and leaves protected capacity.'}
