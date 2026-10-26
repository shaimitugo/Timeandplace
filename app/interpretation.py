"""Interpretation adapters. Output is always validated independently of the adapter."""
import json
import os
import re
import urllib.request
from datetime import timedelta


class Clarification(ValueError):
    pass


def validate(a):
    if not isinstance(a.get('title'), str) or not a['title'].strip():
        raise Clarification('What would you like to achieve?')
    if not isinstance(a.get('duration'), int) or not 5 <= a['duration'] <= 480:
        raise Clarification('Please choose a duration between 5 minutes and 8 hours.')
    a['title'] = a['title'].strip().replace('ⓘ', '').strip()[:160]
    a['note'] = str(a.get('note', '')).strip()[:4000]
    if a.get('category') not in ['personal', 'work']:
        a['category'] = 'personal'
    for key in ['earliest_hour', 'latest_hour', 'preferred_hour']:
        if key in a and (not isinstance(a[key], int) or not 0 <= a[key] <= 23):
            raise Clarification('Please specify a valid hour.')
    if a.get('earliest_hour', 0) >= a.get('latest_hour', 23):
        raise Clarification('That time window is empty. What other time would work?')
    if a.get('date'):
        from datetime import date
        date.fromisoformat(a['date'])
    return a


def interpret(text, profile, now):
    if len(text) > 6000:
        raise Clarification('Please keep this intention under 6,000 characters.')
    if os.environ.get('AI_API_KEY'):
        return validate(ai_interpret(text, profile, now))
    low = text.lower()
    if re.search(r'\b(before|deadline|by)\b|דדליין|לפני|עד לתאריך', low):
        raise Clarification('Please give a single date and an after-hour window for this basic parser. Deadline interpretation needs AI mode; nothing has been scheduled.')
    if re.search(r'\d{1,2}:(?!00)\d{2}', low):
        raise Clarification('Basic mode currently supports whole-hour constraints, for example after 16:00. Nothing was scheduled.')
    if re.search(r'\b(every|weekly|daily)\b|כל יום|כל שבוע', low):
        raise Clarification('Recurring achievements are not supported yet. Choose one occurrence to schedule.')
    parts = re.split(r'\n|\bnotes?:|הערות:', text, maxsplit=1, flags=re.I)
    title = parts[0].strip()
    note = parts[1].strip() if len(parts) > 1 else ''
    a = {'title': title, 'note': note, 'duration': 30, 'category': 'personal', 'location': ''}
    if any(word in low for word in ['meeting', 'client', 'project', 'work', 'לקוח', 'פגישה', 'פרויקט']):
        a['category'] = 'work'
    if any(word in low for word in ['call', 'להתקשר', 'לדבר']):
        a['duration'] = 20
    if any(word in low for word in ['exercise', 'gym', 'אימון', 'כושר']):
        a['duration'] = 60
    m = re.search(r'(\d+)\s*(minutes?|mins?|דקות|דק)', low)
    if m:
        a['duration'] = int(m[1])
    elif re.search(r'\b(an? hour|1 hour)\b|שעה', low):
        a['duration'] = 60
    if 'tomorrow' in low or 'מחר' in low:
        a['date'] = str((now + timedelta(days=1)).date())
    elif 'today' in low or 'היום' in low:
        a['date'] = str(now.date())
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', low)
    if m:
        a['date'] = m[1]
    m = re.search(r'(?:after|אחרי|עד)\s*(\d{1,2})(?::\d\d)?', low)
    if m:
        a['earliest_hour'] = int(m[1])
    if any(word in low for word in ['evening', 'בערב']):
        a['earliest_hour'] = 18
        a['preferred_hour'] = 19
    if any(word in low for word in ['morning', 'בבוקר']):
        a['preferred_hour'] = 9
        a['latest_hour'] = 12
    if re.search(r'\bat\s+\d|בשעה\s*\d', low):
        raise Clarification('For now, use an after-hour window such as after 16:00; exact-time scheduling is not supported in basic mode.')
    if 'at home' in low or 'בבית' in low:
        a['location'] = 'Home'
    a['title'] = re.sub(r'\b(today|tomorrow)\b|מחר|היום', '', a['title'], flags=re.I).strip(' ,.-')
    learned = profile.get('preferences', {}).get(a['title'], {})
    for key, value in learned.items():
        a.setdefault(key, value)
    return validate(a)


def correct(text, achievement):
    a = dict(achievement)
    low = text.lower()
    if re.search(r'\d{1,2}:(?!00)\d{2}', low):
        raise Clarification('Please use a whole-hour constraint in this version, such as after 16:00.')
    m = re.search(r'(?:after|until|אחרי|עד)\s*(\d{1,2})(?::00)?', low)
    if m:
        a['earliest_hour'] = int(m[1])
    else:
        m = re.search(r'(?:at|בשעה|ב[־-]?)\s*(\d{1,2})(?::00)?', low)
        if m:
            a['earliest_hour'] = int(m[1])
            a['preferred_hour'] = int(m[1])
        else:
            raise Clarification('I can revise the time with “after 16:00” / “אחרי 16:00”. For other changes, enter a new intention.')
    return validate(a)


def ai_interpret(text, profile, now):
    schema = {'type': 'object', 'properties': {
        'title': {'type': 'string'}, 'note': {'type': 'string'},
        'duration': {'type': 'integer'}, 'category': {'type': 'string', 'enum': ['personal', 'work']},
        'location': {'type': 'string'}, 'date': {'type': ['string', 'null']},
        'earliest_hour': {'type': 'integer'}, 'latest_hour': {'type': 'integer'},
        'preferred_hour': {'type': 'integer'}, 'clarification': {'type': ['string', 'null']}},
        'required': ['title', 'note', 'duration', 'category', 'location', 'date', 'earliest_hour', 'latest_hour', 'preferred_hour', 'clarification'],
        'additionalProperties': False}
    payload = {'model': os.environ.get('AI_MODEL', 'gpt-4o-mini'),
        'messages': [{'role': 'system', 'content': 'Extract exactly one achievement. Never claim to schedule. Ask clarification for recurrence, ambiguous dates, deadlines or multiple activities. Duration 5..480 minutes. Keep user language and meaningful notes. Date YYYY-MM-DD or null. Conservative defaults: personal, 30 minutes, earliest 7 latest 22 preferred 10. Current local time: ' + now.isoformat() + '. Profile: ' + json.dumps(profile)},
                     {'role': 'user', 'content': text}],
        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'achievement', 'strict': True, 'schema': schema}}}
    req = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['AI_API_KEY'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        a = json.loads(result['choices'][0]['message']['content'])
    except Exception as exc:
        raise Clarification('The language service is unavailable. Please try again; nothing was scheduled.') from exc
    if a.pop('clarification', None):
        raise Clarification('Please clarify the date, duration and single activity. Recurring scheduling is not supported in this version.')
    return a
