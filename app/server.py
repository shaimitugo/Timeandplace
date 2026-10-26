import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import google
from .interpretation import Clarification, interpret, correct
from .scheduling import DEFAULT_PROFILE, event_interval, propose, valid_slot
from .storage import connect, init, encode_tokens

APP_URL = os.environ.get('APP_URL', 'http://localhost:8000').rstrip('/')
STATIC = Path(__file__).resolve().parent.parent / 'static'
LOCK = threading.RLock()
QUESTIONS = [
    'Let’s make room for what matters. What timezone are you in? For example Asia/Jerusalem or Europe/London.',
    'What are your usual working hours? Use 9-17, then tell me about fixed commitments and your commute. Existing calendar events will stay protected.',
    'When do you usually wake and sleep? Use 7-22. What family or recurring routines should I understand?',
    'What do you want to protect time for? Tell me about exercise, relationships, learning, health, hobbies, personal time and professional goals, and when you prefer them.',
    'What else makes a day feel realistic—energy patterns, travel, family constraints, or preparations? I will keep this context; tell me explicit time windows when scheduling an activity.'
]


def now_for(profile):
    return datetime.now(ZoneInfo(profile['timezone']))


def row_dict(row):
    return dict(row) if row else None


def message(uid, role, text):
    with connect() as db:
        db.execute('INSERT INTO messages(user_id,role,text) VALUES(?,?,?)', (uid, role, text))


def load_profile(uid):
    with connect() as db:
        row = db.execute('SELECT profile FROM users WHERE id=?', (uid,)).fetchone()
    return json.loads(row['profile'])


def get_events(uid, profile):
    if uid.startswith('demo_'):
        with connect() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM demo_events WHERE user_id=?', (uid,))]
    now = now_for(profile)
    events = google.events(uid, now.replace(hour=0, minute=0, second=0, microsecond=0), now + timedelta(days=9))
    # Reconcile each app-owned event, including items moved outside the current horizon.
    by_id = {e['id']: e for e in events}
    with connect() as db:
        achievements = db.execute("SELECT * FROM achievements WHERE user_id=? AND event_id IS NOT NULL AND status NOT IN ('completed','cancelled')", (uid,)).fetchall()
    for a in achievements:
        event = by_id.get(a['event_id'])
        if event is None:
            try:
                event = google.api(uid, '/events/' + urllib.parse.quote(a['event_id'], safe=''))
            except google.ProviderError as exc:
                if exc.status in (404, 410):
                    event = {'status': 'cancelled'}
                else:
                    raise
        data = json.loads(a['data'])
        if event.get('status') == 'cancelled':
            status = 'cancelled'
        else:
            status = 'scheduled'
            es, ee = event_interval(event, ZoneInfo(profile['timezone']))
            data['duration'] = max(5, int((ee - es).total_seconds() / 60))
            data.update(title=event.get('summary', '').replace(' ⓘ', ''), note=event.get('description', ''),
                        location=event.get('location', ''), scheduled_start=event['start'], scheduled_end=event['end'])
        with connect() as db:
            db.execute('UPDATE achievements SET data=?,etag=?,status=? WHERE id=?', (json.dumps(data), event.get('etag'), status, a['id']))
    return events


def make_proposal(uid, achievement, profile, achievement_id=None):
    events = get_events(uid, profile)
    existing = None
    if achievement_id:
        with connect() as db:
            existing = db.execute('SELECT * FROM achievements WHERE id=? AND user_id=?', (achievement_id, uid)).fetchone()
        if not existing:
            raise Clarification('Achievement not found.')
    slot = propose(achievement, events, profile, now_for(profile), existing['event_id'] if existing else None)
    if not slot:
        raise Clarification('There isn’t enough suitable capacity in the next seven days. Nothing was added or moved. Try another date, a shorter version, or free a commitment in Google Calendar first.')
    aid = achievement_id or secrets.token_hex(16)
    pid = secrets.token_hex(16)
    proposal = dict(slot, achievement=achievement, achievement_id=aid,
                    event_id=existing['event_id'] if existing and existing['event_id'] else secrets.token_hex(16),
                    etag=existing['etag'] if existing else None, reschedule=bool(existing and existing['event_id']), created_at=time.time())
    with connect() as db:
        db.execute("UPDATE proposals SET status='superseded' WHERE user_id=? AND status='pending'", (uid,))
        if not existing:
            db.execute('INSERT INTO achievements VALUES(?,?,?,?,?,?)', (aid, uid, json.dumps(achievement), None, None, 'draft'))
        db.execute('INSERT INTO proposals VALUES(?,?,?,?,?)', (pid, uid, aid, json.dumps(proposal), 'pending'))
    return dict(proposal, id=pid)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # URL query strings may contain OAuth codes. Never log them.
        pass

    def send(self, code, body, content_type='application/json', cookie=None):
        data = json.dumps(body, ensure_ascii=False).encode() if content_type == 'application/json' else body
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def cookie(self, sid):
        return f'sid={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800' + ('; Secure' if APP_URL.startswith('https://') else '')

    def redirect(self, path, cookie=None):
        self.send_response(303)
        self.send_header('Location', path)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()

    def session(self, required=True):
        c = SimpleCookie()
        try:
            c.load(self.headers.get('Cookie', ''))
        except Exception:
            return None
        sid = c.get('sid')
        with connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (sid.value if sid else '', time.time())).fetchone()
        if required and (not row or not row['user_id']):
            raise PermissionError('Please sign in first.')
        return row_dict(row)

    def do_GET(self):
        try:
            self.get()
        except PermissionError as exc:
            self.send(401, {'error': str(exc)})
        except (Clarification, google.ProviderError, ValueError, ZoneInfoNotFoundError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            self.send(500, {'error': 'Something went wrong. Your calendar was not intentionally changed. Retry sync before approving again.'})

    def get(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ['/', '/app.js', '/style.css']:
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            content = {'/': 'text/html', '/app.js': 'application/javascript', '/style.css': 'text/css'}[path]
            return self.send(200, (STATIC / name).read_bytes(), content)
        if path == '/health':
            return self.send(200, {'status': 'ok'})
        if path == '/auth/google':
            if not all(os.environ.get(k) for k in ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET']):
                return self.redirect('/?auth=not_configured')
            sid, state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            with connect() as db:
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (sid, None, secrets.token_urlsafe(32), state, verifier, time.time() + 600))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            query = urllib.parse.urlencode({'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': APP_URL + '/auth/callback',
                'response_type': 'code', 'scope': google.SCOPES, 'state': state, 'access_type': 'offline', 'prompt': 'consent',
                'code_challenge': challenge, 'code_challenge_method': 'S256'})
            return self.redirect('https://accounts.google.com/o/oauth2/v2/auth?' + query, self.cookie(sid))
        if path == '/auth/callback':
            session = self.session(False)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if not session or not session['state'] or not secrets.compare_digest(q.get('state', [''])[0], session['state']):
                raise PermissionError('Invalid or expired Google sign-in state.')
            with connect() as db:
                db.execute('UPDATE sessions SET state=NULL WHERE id=?', (session['id'],))
            if 'error' in q or 'code' not in q:
                return self.redirect('/?auth=cancelled')
            tokens = google.oauth_token({'grant_type': 'authorization_code', 'code': q['code'][0],
                'redirect_uri': APP_URL + '/auth/callback', 'code_verifier': session['verifier']})
            granted = set(tokens.get('scope', '').split())
            if 'https://www.googleapis.com/auth/calendar.events' not in granted:
                raise Clarification('Calendar permission was not granted. Reconnect and allow calendar event access.')
            identity = google.request('https://openidconnect.googleapis.com/v1/userinfo', headers={'Authorization': 'Bearer ' + tokens['access_token']})
            if not identity.get('email_verified') or not identity.get('sub'):
                raise PermissionError('Google identity could not be verified.')
            uid = 'google_' + identity['sub']
            with LOCK, connect() as db:
                owner = db.execute('SELECT id FROM users WHERE google_sub IS NOT NULL LIMIT 1').fetchone()
                if owner and owner['id'] != uid:
                    raise PermissionError('This pilot is already connected to another Google account.')
                db.execute('INSERT OR IGNORE INTO users VALUES(?,?,NULL,?)', (uid, json.dumps(dict(DEFAULT_PROFILE, onboarding_step=0, context=[])), identity['sub']))
            google.save_tokens(uid, tokens)
            sid = secrets.token_urlsafe(32)
            with connect() as db:
                db.execute('DELETE FROM sessions WHERE id=?', (session['id'],))
                db.execute('INSERT INTO sessions VALUES(?,?,?,NULL,NULL,?)', (sid, uid, secrets.token_urlsafe(32), time.time()+604800))
            return self.redirect('/', self.cookie(sid))
        if path == '/api/state':
            s = self.session(False)
            if not s or not s['user_id']:
                return self.send(200, {'authenticated': False, 'google_configured': bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))})
            uid = s['user_id']
            profile = load_profile(uid)
            with connect() as db:
                messages = [dict(r) for r in db.execute('SELECT role,text FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 60', (uid,))][::-1]
                achievements = [dict(r) for r in db.execute("SELECT id,data,event_id,status FROM achievements WHERE user_id=? AND status!='draft'", (uid,))]
                pending = db.execute("SELECT id,data FROM proposals WHERE user_id=? AND status='pending' ORDER BY rowid DESC LIMIT 1", (uid,)).fetchone()
            for a in achievements:
                a['data'] = json.loads(a['data'])
            step = profile.get('onboarding_step', 0)
            return self.send(200, {'authenticated': True, 'demo': uid.startswith('demo_'), 'csrf': s['csrf'], 'profile': profile,
                'question': QUESTIONS[step] if step < len(QUESTIONS) else None, 'messages': messages, 'achievements': achievements,
                'proposal': dict(json.loads(pending['data']), id=pending['id']) if pending else None,
                'interpreter': 'AI interpretation' if os.environ.get('AI_API_KEY') else 'Basic English / Hebrew parser'})
        if path == '/api/sync':
            s = self.session()
            profile = load_profile(s['user_id'])
            events = get_events(s['user_id'], profile)
            now = now_for(profile)
            today = []
            for event in events:
                if event.get('status') == 'cancelled':
                    continue
                start, end = event_interval(event, ZoneInfo(profile['timezone']))
                if start.date() <= now.date() <= end.date() and end > now.replace(hour=0, minute=0, second=0, microsecond=0):
                    today.append(event)
            return self.send(200, {'today_count': len(today), 'synced_at': now.isoformat(), 'today_titles': [e.get('summary', 'Busy') for e in today]})
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 12000:
                return self.send(413, {'error': 'Request too large'})
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise Clarification('Expected a JSON object.')
            # Serialize writes and approvals to prevent duplicate concurrent submissions.
            with LOCK:
                self.post(urllib.parse.urlsplit(self.path).path, data)
        except PermissionError as exc:
            self.send(403, {'error': str(exc)})
        except (Clarification, google.ProviderError, ValueError, KeyError, ZoneInfoNotFoundError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            self.send(500, {'error': 'Something went wrong. Sync before retrying; approval retries use the same event identity.'})

    def post(self, path, data):
        if path == '/api/demo':
            # Block cross-site creation of sessions, including login CSRF.
            if self.headers.get('Origin') != APP_URL:
                raise PermissionError('Origin does not match APP_URL.')
            uid, sid = 'demo_' + secrets.token_hex(16), secrets.token_urlsafe(32)
            with connect() as db:
                db.execute('INSERT INTO users VALUES(?,?,NULL,NULL)', (uid, json.dumps(dict(DEFAULT_PROFILE, onboarding_step=0, context=[]))))
                db.execute('INSERT INTO sessions VALUES(?,?,?,NULL,NULL,?)', (sid, uid, secrets.token_urlsafe(32), time.time()+604800))
                now = now_for(DEFAULT_PROFILE)
                for offset in range(4):
                    start = (now + timedelta(days=offset)).replace(hour=11, minute=0, second=0, microsecond=0)
                    event = {'id': secrets.token_hex(16), 'summary': 'Example: fixed commitment', 'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': (start+timedelta(hours=1)).isoformat()}}
                    db.execute('INSERT INTO demo_events VALUES(?,?,?)', (uid, event['id'], json.dumps(event)))
            return self.send(200, {'ok': True}, cookie=self.cookie(sid))
        s = self.session()
        if not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), s['csrf']):
            raise PermissionError('Session check failed. Refresh the page and try again.')
        uid = s['user_id']
        profile = load_profile(uid)
        if path == '/api/logout':
            with connect() as db:
                db.execute('DELETE FROM sessions WHERE id=?', (s['id'],))
            return self.send(200, {'ok': True}, cookie='sid=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')
        if path == '/api/chat':
            text = str(data.get('text', '')).strip()
            if not text or len(text) > 6000:
                raise Clarification('Please enter a message under 6,000 characters.')
            step = profile.get('onboarding_step', 0)
            if step < len(QUESTIONS):
                if step == 0:
                    ZoneInfo(text)
                    profile['timezone'] = text
                if step in (1, 2):
                    m = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})', text)
                    if not m or not 0 <= int(m[1]) < int(m[2]) <= 23:
                        raise Clarification('Please include a time range like 9-17 (24-hour time). Overnight routines are not supported yet.')
                    keys = ('work_start', 'work_end') if step == 1 else ('wake', 'sleep')
                    profile.update(dict(zip(keys, [int(m[1]), int(m[2])])))
                profile['context'].append({'question': QUESTIONS[step], 'answer': text})
                profile['onboarding_step'] = step + 1
                with connect() as db:
                    db.execute('UPDATE users SET profile=? WHERE id=?', (json.dumps(profile), uid))
                reply = QUESTIONS[step+1] if step+1 < len(QUESTIONS) else 'Your rhythm is saved. Tell me one thing you want to make time for. I’ll propose a slot before changing your calendar. Context is remembered; in basic mode, include specific constraints in the intention.'
                message(uid, 'user', text)
                message(uid, 'assistant', reply)
                return self.send(200, {'reply': reply})
            if data.get('proposal_id'):
                with connect() as db:
                    row = db.execute("SELECT * FROM proposals WHERE id=? AND user_id=? AND status='pending'", (data['proposal_id'], uid)).fetchone()
                if not row:
                    raise Clarification('That proposal is no longer current. Enter your intention again.')
                old = json.loads(row['data'])
                achievement = correct(text, old['achievement'])
                # Scope learned corrections to this exact activity title.
                profile['preferences'][achievement['title']] = {'earliest_hour': achievement['earliest_hour']}
                with connect() as db:
                    db.execute('UPDATE users SET profile=? WHERE id=?', (json.dumps(profile), uid))
                p = make_proposal(uid, achievement, profile, row['achievement_id'])
            else:
                achievement = interpret(text, profile, now_for(profile))
                p = make_proposal(uid, achievement, profile)
            message(uid, 'user', text)
            message(uid, 'assistant', 'Here is a proposed time. Review it and approve when it feels right.')
            return self.send(200, {'proposal': p})
        if path == '/api/dismiss':
            with connect() as db:
                db.execute("UPDATE proposals SET status='dismissed' WHERE id=? AND user_id=? AND status='pending'", (data['id'], uid))
            return self.send(200, {'ok': True})
        if path == '/api/approve':
            with connect() as db:
                row = db.execute('SELECT * FROM proposals WHERE id=? AND user_id=?', (data['id'], uid)).fetchone()
            if not row:
                raise Clarification('Proposal not found.')
            if row['status'] == 'approved':
                return self.send(200, {'ok': True, 'already_approved': True})
            if row['status'] != 'pending':
                raise Clarification('This proposal is no longer current.')
            p = json.loads(row['data'])
            if time.time() - p['created_at'] > 3600:
                raise Clarification('This proposal is over an hour old. Propose a fresh time before approving.')
            events = get_events(uid, profile)
            a = p['achievement']
            start = datetime.fromisoformat(p['start'])
            if not valid_slot(start, a, events, profile, now_for(profile), p['event_id']):
                raise Clarification('Your calendar or capacity changed. Nothing was added or moved. Modify the proposal to find a fresh time.')
            body = {'summary': a['title'] + (' ⓘ' if a.get('note', '').strip() else ''),
                    'description': a.get('note', ''), 'location': a.get('location', ''),
                    'start': {'dateTime': p['start'], 'timeZone': profile['timezone']},
                    'end': {'dateTime': p['end'], 'timeZone': profile['timezone']},
                    'extendedProperties': {'private': {'achievement_id': p['achievement_id']}}}
            if uid.startswith('demo_'):
                event = dict(body, id=p['event_id'], etag='demo')
                with connect() as db:
                    db.execute('INSERT OR REPLACE INTO demo_events VALUES(?,?,?)', (uid, event['id'], json.dumps(event)))
            else:
                event = google.put_event(uid, p['event_id'], body, p['reschedule'], p['etag'])
            a.update(scheduled_start=event['start'], scheduled_end=event['end'])
            with connect() as db:
                db.execute("UPDATE achievements SET data=?,event_id=?,etag=?,status='scheduled' WHERE id=? AND user_id=?", (json.dumps(a), event['id'], event.get('etag'), p['achievement_id'], uid))
                db.execute("UPDATE proposals SET status='approved' WHERE id=?", (row['id'],))
            message(uid, 'assistant', 'Time protected. The existing event was moved.' if p['reschedule'] else 'Time protected. Your achievement is scheduled.' )
            return self.send(200, {'ok': True})
        if path in ['/api/reschedule', '/api/complete']:
            with connect() as db:
                row = db.execute("SELECT * FROM achievements WHERE id=? AND user_id=? AND status='scheduled'", (data['id'], uid)).fetchone()
            if not row:
                raise Clarification('Scheduled achievement not found.')
            if path == '/api/complete':
                with connect() as db:
                    db.execute("UPDATE achievements SET status='completed' WHERE id=?", (row['id'],))
                    db.execute("UPDATE proposals SET status='superseded' WHERE achievement_id=? AND status='pending'", (row['id'],))
                return self.send(200, {'ok': True})
            get_events(uid, profile)
            with connect() as db:
                row = db.execute('SELECT * FROM achievements WHERE id=?', (row['id'],)).fetchone()
            if row['status'] != 'scheduled':
                raise Clarification('This event was cancelled in Google Calendar.')
            a = json.loads(row['data'])
            if a['duration'] > 480:
                raise Clarification('This event is now longer than eight hours. Move it directly in Google Calendar.')
            # A previous requested day is no longer binding when explicitly moving forward.
            a.pop('date', None)
            if data.get('text'):
                constraint = interpret(str(data['text']), profile, now_for(profile))
                for key in ['date', 'earliest_hour', 'preferred_hour', 'latest_hour']:
                    if key in constraint:
                        a[key] = constraint[key]
            p = make_proposal(uid, a, profile, row['id'])
            return self.send(200, {'proposal': p})
        self.send(404, {'error': 'Not found'})


def main():
    init()
    port = int(os.environ.get('PORT', '8000'))
    host = os.environ.get('HOST', '127.0.0.1')
    print(f'Time & Place listening on {host}:{port}', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
