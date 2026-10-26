import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app import storage, server, google


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_root = storage.ROOT
        from pathlib import Path
        storage.ROOT = Path(cls.temp.name)
        storage.init()
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.old_url = server.APP_URL
        server.APP_URL = cls.base
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        storage.ROOT = cls.old_root
        server.APP_URL = cls.old_url
        cls.temp.cleanup()

    def setUp(self):
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ''

    def call(self, path, data=None, csrf=True):
        headers = {'Content-Type': 'application/json', 'Origin': self.base}
        if csrf:
            headers['X-CSRF-Token'] = self.csrf
        request = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with self.client.open(request) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def demo(self):
        self.assertEqual(self.call('/api/demo', {})[0], 200)
        status, state = self.call('/api/state')
        self.csrf = state['csrf']
        return state

    def onboard(self):
        self.demo()
        for text in ['Asia/Jerusalem', '9-17; family dinner at 19', '7-22; school run', 'Exercise and relationships', 'Keep breathing room']:
            self.assertEqual(self.call('/api/chat', {'text': text})[0], 200)

    def propose(self):
        status, p = self.call('/api/chat', {'text': 'Call brother tomorrow\nAsk about the sample'})
        self.assertEqual(status, 200, p)
        return p['proposal']

    def test_auth_required(self):
        self.assertEqual(self.call('/api/sync')[0], 401)
        self.assertEqual(self.call('/api/chat', {'text': 'Call'})[0], 403)

    def test_csrf_required(self):
        self.demo()
        self.assertEqual(self.call('/api/chat', {'text': 'Asia/Jerusalem'}, csrf=False)[0], 403)

    def test_full_flow_and_reschedule_same_event(self):
        self.onboard()
        p = self.propose()
        # No event exists until explicit approval.
        with storage.connect() as db:
            self.assertIsNone(db.execute('SELECT event_id FROM achievements WHERE id=?', (p['achievement_id'],)).fetchone()['event_id'])
        status, correction = self.call('/api/chat', {'text': 'הוא בעבודה עד 16:00', 'proposal_id': p['id']})
        self.assertEqual(status, 200, correction)
        newer = correction['proposal']
        self.assertGreaterEqual(int(newer['start'][11:13]), 16)
        self.assertEqual(self.call('/api/approve', {'id': p['id']})[0], 400)
        self.assertEqual(self.call('/api/approve', {'id': newer['id']})[0], 200)
        self.assertEqual(self.call('/api/approve', {'id': newer['id']})[0], 200)
        with storage.connect() as db:
            events = db.execute('SELECT * FROM demo_events WHERE id=?', (newer['event_id'],)).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]['data'])['description'], 'Ask about the sample')
        status, moved = self.call('/api/reschedule', {'id': p['achievement_id'], 'text': 'tomorrow after 18:00'})
        self.assertEqual(status, 200, moved)
        self.assertEqual(moved['proposal']['event_id'], newer['event_id'])
        self.assertEqual(self.call('/api/approve', {'id': moved['proposal']['id']})[0], 200)
        with storage.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_events WHERE id=?', (newer['event_id'],)).fetchone()[0], 1)
            event = json.loads(db.execute('SELECT data FROM demo_events WHERE id=?', (newer['event_id'],)).fetchone()[0])
        self.assertEqual(event['description'], 'Ask about the sample')
        self.assertTrue(event['summary'].endswith('ⓘ'))
        self.assertEqual(self.call('/api/complete', {'id': p['achievement_id']})[0], 200)

    def test_approval_rechecks_conflicts(self):
        self.onboard()
        p = self.propose()
        with storage.connect() as db:
            uid = db.execute('SELECT user_id FROM proposals WHERE id=?', (p['id'],)).fetchone()[0]
            e = {'id': 'new-conflict', 'start': {'dateTime': p['start']}, 'end': {'dateTime': p['end']}}
            db.execute('INSERT INTO demo_events VALUES(?,?,?)', (uid, e['id'], json.dumps(e)))
        self.assertEqual(self.call('/api/approve', {'id': p['id']})[0], 400)

    def test_demo_sessions_are_isolated(self):
        first = self.demo()
        self.call('/api/chat', {'text': 'Asia/Jerusalem'})
        self.demo()
        _, second = self.call('/api/state')
        self.assertEqual(second['profile']['onboarding_step'], 0)
        self.assertNotEqual(first['csrf'], second['csrf'])

    def test_logout(self):
        self.demo()
        self.assertEqual(self.call('/api/logout', {})[0], 200)
        self.assertFalse(self.call('/api/state')[1]['authenticated'])

    def test_invalid_oauth_state(self):
        self.assertEqual(self.call('/auth/callback?state=wrong&code=fake')[0], 401)

    def test_dismiss(self):
        self.onboard()
        p = self.propose()
        self.assertEqual(self.call('/api/dismiss', {'id': p['id']})[0], 200)
        self.assertEqual(self.call('/api/approve', {'id': p['id']})[0], 400)

    def test_note_indicator_only_meaningful_note(self):
        self.onboard()
        status, response = self.call('/api/chat', {'text': 'Call brother tomorrow'})
        p = response['proposal']
        self.assertEqual(self.call('/api/approve', {'id': p['id']})[0], 200)
        with storage.connect() as db:
            event = json.loads(db.execute('SELECT data FROM demo_events WHERE id=?', (p['event_id'],)).fetchone()[0])
        self.assertNotIn('ⓘ', event['summary'])


class GoogleTransportTests(unittest.TestCase):
    def test_reschedule_is_patch_with_etag(self):
        with patch('app.google.api', return_value={'id': 'existing'}) as api:
            google.put_event('user', 'existing', {'summary': 'Call ⓘ', 'description': 'Notes'}, True, 'v1')
            self.assertEqual(api.call_args.args[1], '/events/existing')
            self.assertEqual(api.call_args.args[3:], ('PATCH', 'v1'))

    def test_insert_retry_recovers_existing_identity(self):
        body = {'extendedProperties': {'private': {'achievement_id': 'a'}}}
        with patch('app.google.api', side_effect=[google.ProviderError('Timeout'), dict(body, id='stable')]) as api:
            self.assertEqual(google.put_event('u', 'stable', body)['id'], 'stable')
            self.assertEqual(api.call_count, 2)

    def test_pagination(self):
        now = server.now_for(server.DEFAULT_PROFILE)
        with patch('app.google.api', side_effect=[{'items': [{'id': 'one'}], 'nextPageToken': 'next'}, {'items': [{'id': 'two'}]}]) as api:
            self.assertEqual(len(google.events('u', now, now + timedelta(days=1))), 2)
            self.assertIn('pageToken=next', api.call_args.args[1])

    def test_refresh_preserves_refresh_token(self):
        tokens = {'access_token': 'old', 'refresh_token': 'refresh', 'expires_at': 0}
        class DB:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, *args): return self
            def fetchone(self): return {'tokens': 'encrypted'}
        with patch('app.google.connect', return_value=DB()), patch('app.google.decode_tokens', return_value=tokens), patch('app.google.oauth_token', return_value={'access_token': 'new'}), patch('app.google.save_tokens') as save:
            self.assertEqual(google.access_token('u'), 'new')
            self.assertEqual(save.call_args.args[1]['refresh_token'], 'refresh')

class OAuthAndSyncTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = storage.ROOT
        storage.ROOT = Path(self.temp.name)
        storage.init()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.old_url = server.APP_URL
        server.APP_URL = 'http://localhost:' + str(self.http.server_port)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        storage.ROOT = self.old_root
        server.APP_URL = self.old_url
        self.temp.cleanup()

    def test_oauth_pkce_callback_rotation_and_encrypted_tokens(self):
        import http.client
        import urllib.parse
        def get(path, cookie=None):
            client = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
            client.request('GET', path, headers={'Cookie': cookie} if cookie else {})
            response = client.getresponse()
            result = (response.status, dict(response.getheaders()), response.read())
            client.close()
            return result
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': 'test-client', 'GOOGLE_CLIENT_SECRET': 'test-secret'}):
            code, headers, _ = get('/auth/google')
            self.assertEqual(code, 303)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['Location']).query)
            self.assertEqual(q['code_challenge_method'], ['S256'])
            self.assertEqual(q['redirect_uri'], [server.APP_URL + '/auth/callback'])
            old_cookie = headers['Set-Cookie'].split(';')[0]
            tokens = {'access_token': 'private-access', 'refresh_token': 'private-refresh', 'scope': google.SCOPES, 'expires_in': 3600}
            with patch('app.google.oauth_token', return_value=tokens), patch('app.google.request', return_value={'sub': 'pilot', 'email_verified': True}):
                callback = '/auth/callback?' + urllib.parse.urlencode({'state': q['state'][0], 'code': 'fake-code'})
                code, headers, _ = get(callback, old_cookie)
                self.assertEqual(code, 303)
                self.assertNotEqual(headers['Set-Cookie'].split(';')[0], old_cookie)
                self.assertEqual(get(callback, old_cookie)[0], 401)
            with storage.connect() as db:
                encrypted = db.execute('SELECT tokens FROM users WHERE id=?', ('google_pilot',)).fetchone()[0]
                self.assertNotIn('private-access', encrypted)
                self.assertEqual(storage.decode_tokens(encrypted)['refresh_token'], 'private-refresh')

    def test_external_edits_and_deletions_reconcile(self):
        uid = 'google_test'
        p = dict(server.DEFAULT_PROFILE)
        now = server.now_for(p)
        a = {'title': 'Old', 'duration': 30, 'note': 'Old notes'}
        with storage.connect() as db:
            db.execute('INSERT INTO users VALUES(?,?,NULL,?)', (uid, json.dumps(p), 'test'))
            db.execute('INSERT INTO achievements VALUES(?,?,?,?,?,?)', ('a', uid, json.dumps(a), 'google-id', 'old-tag', 'scheduled'))
        changed = {'id': 'google-id', 'etag': 'new-tag', 'summary': 'Externally changed ⓘ', 'description': 'Keep this note',
                   'start': {'dateTime': now.isoformat()}, 'end': {'dateTime': (now+timedelta(minutes=60)).isoformat()}}
        with patch('app.google.events', return_value=[changed]):
            server.get_events(uid, p)
        with storage.connect() as db:
            row = db.execute('SELECT * FROM achievements WHERE id=?', ('a',)).fetchone()
            self.assertEqual(json.loads(row['data'])['duration'], 60)
            self.assertEqual(json.loads(row['data'])['note'], 'Keep this note')
            self.assertEqual(row['etag'], 'new-tag')
        with patch('app.google.events', return_value=[]), patch('app.google.api', side_effect=google.ProviderError('Gone', 410)):
            server.get_events(uid, p)
        with storage.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM achievements WHERE id=?', ('a',)).fetchone()[0], 'cancelled')

    def test_transient_sync_error_does_not_delete(self):
        uid = 'google_test'
        p = dict(server.DEFAULT_PROFILE)
        with storage.connect() as db:
            db.execute('INSERT INTO users VALUES(?,?,NULL,?)', (uid, json.dumps(p), 'test'))
            db.execute('INSERT INTO achievements VALUES(?,?,?,?,?,?)', ('a', uid, '{}', 'google-id', 'old', 'scheduled'))
        with patch('app.google.events', return_value=[]), patch('app.google.api', side_effect=google.ProviderError('Network', 503)):
            with self.assertRaises(google.ProviderError):
                server.get_events(uid, p)
        with storage.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM achievements WHERE id=?', ('a',)).fetchone()[0], 'scheduled')
