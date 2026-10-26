"""Google OAuth and Calendar transport. Never log provider bodies or credentials."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from .storage import connect, encode_tokens, decode_tokens

BASE = 'https://www.googleapis.com/calendar/v3/calendars/primary'
SCOPES = 'openid email https://www.googleapis.com/auth/calendar.events'


class ProviderError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 412:
            raise ProviderError('This event changed in Google Calendar. Sync and propose the move again.') from exc
        raise ProviderError(f'Google request failed (HTTP {exc.code}). Check access and sync before retrying.', exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError('Google is unreachable. Try again after connectivity is restored.') from exc


def oauth_token(params):
    params.update(client_id=os.environ['GOOGLE_CLIENT_ID'], client_secret=os.environ['GOOGLE_CLIENT_SECRET'])
    return request('https://oauth2.googleapis.com/token', urllib.parse.urlencode(params).encode(),
                   {'Content-Type': 'application/x-www-form-urlencoded'})


def save_tokens(uid, tokens):
    tokens['expires_at'] = time.time() + tokens.get('expires_in', 3600) - 60
    with connect() as db:
        db.execute('UPDATE users SET tokens=? WHERE id=?', (encode_tokens(tokens), uid))


def access_token(uid):
    with connect() as db:
        row = db.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()
    if not row or not row['tokens']:
        raise ProviderError('Connect Google Calendar first.')
    tokens = decode_tokens(row['tokens'])
    if tokens.get('expires_at', 0) < time.time():
        if not tokens.get('refresh_token'):
            raise ProviderError('Reconnect Google to renew calendar access.')
        new = oauth_token({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']})
        tokens.update(new)
        save_tokens(uid, tokens)
    return tokens['access_token']


def api(uid, path, body=None, method='GET', etag=None):
    headers = {'Authorization': 'Bearer ' + access_token(uid)}
    if etag:
        headers['If-Match'] = etag
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    return request(BASE + path, data, headers, method)


def events(uid, start, end):
    result, page = [], None
    while True:
        query = {'timeMin': start.isoformat(), 'timeMax': end.isoformat(),
                 'singleEvents': 'true', 'maxResults': 2500, 'showDeleted': 'false'}
        if page:
            query['pageToken'] = page
        response = api(uid, '/events?' + urllib.parse.urlencode(query))
        result.extend(response.get('items', []))
        page = response.get('nextPageToken')
        if not page:
            return result


def put_event(uid, event_id, body, existing=False, etag=None):
    path = '/events/' + urllib.parse.quote(event_id, safe='')
    if existing:
        return api(uid, path, body, 'PATCH', etag)
    body = dict(body, id=event_id)
    try:
        return api(uid, '/events', body, 'POST')
    except ProviderError:
        # A timed-out insert may have succeeded. Retrieve its stable ID before retrying.
        event = api(uid, path)
        if event.get('extendedProperties', {}).get('private', {}).get('achievement_id') != body.get('extendedProperties', {}).get('private', {}).get('achievement_id'):
            raise ProviderError('Calendar event identity mismatch. Please reconnect and retry.')
        return event
