"""
Google Calendar and Gmail for J.A.R.V.I.S.

Read-only, deliberately
-----------------------
The scopes here are `calendar.readonly` and `gmail.readonly`. Knowing the day
is what makes an assistant useful; sending mail on someone's behalf is a
different risk entirely, and not one worth taking for "what does my day look
like". Write access can be added later as an explicit, separate decision.

Same architecture as the Spotify bridge and for the same reason: Google only
permits loopback redirect URIs for this kind of client, so the callback cannot
land on the dashboard's own origin. The server therefore owns the OAuth
exchange and holds the tokens in a file, and the browser never sees one.

Unlike Spotify, Google's desktop client type requires the client *secret* in
the token exchange even when PKCE is used — so both halves are asked for, and
both stay server-side.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
CAL = 'https://www.googleapis.com/calendar/v3'
GMAIL = 'https://gmail.googleapis.com/gmail/v1'

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'google_auth.json')

SCOPES = ' '.join([
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/gmail.readonly',
    'openid', 'email',
])

_lock = threading.Lock()
_pending = {}


# --------------------------------------------------------------- token store

def _read():
    try:
        with open(STORE, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write(data):
    tmp = STORE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, STORE)


def set_credentials(client_id, client_secret):
    with _lock:
        d = _read()
        if client_id is not None:
            d['client_id'] = (client_id or '').strip()
        if client_secret is not None:
            d['client_secret'] = (client_secret or '').strip()
        _write(d)
    return {'ok': True}


def forget():
    with _lock:
        d = _read()
        _write({'client_id': d.get('client_id', ''),
                'client_secret': d.get('client_secret', '')})
    return {'ok': True, 'disconnected': True}


def status():
    d = _read()
    return {
        'ok': True,
        'has_credentials': bool(d.get('client_id') and d.get('client_secret')),
        'connected': bool(d.get('refresh_token')),
        'user': d.get('email', ''),
    }


# ---------------------------------------------------------------------- auth

def login_url(redirect_uri, client_id=None, client_secret=None):
    with _lock:
        d = _read()
        if client_id:
            d['client_id'] = client_id.strip()
        if client_secret:
            d['client_secret'] = client_secret.strip()
        _write(d)
        cid = d.get('client_id', '')
        sec = d.get('client_secret', '')

    if not cid or not sec:
        return None, ('Google needs both a Client ID and a Client secret. Create an OAuth '
                      'client at console.cloud.google.com, type "Web application", and add '
                      + redirect_uri + ' as an Authorised redirect URI.')

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip('=')
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(16)
    _pending[state] = {'verifier': verifier, 'redirect': redirect_uri, 'at': time.time()}
    for k in [k for k, v in _pending.items() if time.time() - v['at'] > 600]:
        _pending.pop(k, None)

    q = urllib.parse.urlencode({
        'client_id': cid,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPES,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': state,
        # Without these Google hands back no refresh token on repeat consents,
        # and the connection silently dies an hour later.
        'access_type': 'offline',
        'prompt': 'consent',
    })
    return AUTH + '?' + q, None


def _post_token(fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(TOKEN, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8')), None
    except urllib.error.HTTPError as err:
        raw = err.read().decode('utf-8', 'replace')
        return None, 'Google rejected the token request (%s): %s' % (err.code, raw[:250])
    except Exception as err:
        return None, 'Could not reach Google: %s' % err


def handle_callback(code, state):
    entry = _pending.pop(state, None)
    if not entry:
        return {'ok': False, 'error': 'That login link expired or was already used. '
                                      'Press Connect again.'}
    d = _read()
    tok, err = _post_token({
        'code': code,
        'client_id': d.get('client_id', ''),
        'client_secret': d.get('client_secret', ''),
        'redirect_uri': entry['redirect'],
        'grant_type': 'authorization_code',
        'code_verifier': entry['verifier'],
    })
    if err:
        return {'ok': False, 'error': err}

    with _lock:
        d = _read()
        d['access_token'] = tok.get('access_token', '')
        if tok.get('refresh_token'):
            d['refresh_token'] = tok['refresh_token']
        d['expires_at'] = time.time() + int(tok.get('expires_in', 3600)) - 60
        # the id_token carries the address; no extra call needed
        idt = tok.get('id_token', '')
        if idt and idt.count('.') == 2:
            try:
                payload = idt.split('.')[1]
                payload += '=' * (-len(payload) % 4)
                d['email'] = json.loads(base64.urlsafe_b64decode(payload)).get('email', '')
            except Exception:
                pass
        _write(d)

    if not _read().get('refresh_token'):
        return {'ok': False, 'error': 'Google returned no refresh token. Remove this app '
                                      'from your Google account permissions and connect again.'}
    return {'ok': True, 'user': _read().get('email', '')}


def _token():
    d = _read()
    if not d.get('refresh_token'):
        return None, ('Google is not linked to J.A.R.V.I.S. yet. Open the gear icon on THIS '
                      'dashboard, find the Google section, and press Connect. This is a '
                      'one-time setup and is nothing to do with being signed into Gmail in '
                      'the browser.')
    if d.get('access_token') and time.time() < d.get('expires_at', 0):
        return d['access_token'], None

    tok, err = _post_token({
        'refresh_token': d['refresh_token'],
        'client_id': d.get('client_id', ''),
        'client_secret': d.get('client_secret', ''),
        'grant_type': 'refresh_token',
    })
    if err:
        return None, err
    with _lock:
        d = _read()
        d['access_token'] = tok.get('access_token', '')
        d['expires_at'] = time.time() + int(tok.get('expires_in', 3600)) - 60
        _write(d)
    return d['access_token'], None


def _get(url, token, params=None):
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8')), 200
    except urllib.error.HTTPError as err:
        raw = err.read().decode('utf-8', 'replace')
        try:
            return json.loads(raw), err.code
        except Exception:
            return {'error': {'message': raw[:200]}}, err.code


# ------------------------------------------------------------------ calendar

def _when(ev):
    start = ev.get('start') or {}
    if start.get('dateTime'):
        try:
            t = start['dateTime'][11:16]
            day = start['dateTime'][:10]
            return day, t
        except Exception:
            return start['dateTime'], ''
    return start.get('date', ''), 'all day'


def agenda(q=None, days=1):
    token, err = _token()
    if not token:
        return {'ok': False, 'error': err}

    m = re.search(r'(\d+)\s*day', (q or ''), re.I)
    if m:
        days = max(1, min(14, int(m.group(1))))
    elif re.search(r'\bweek\b', (q or ''), re.I):
        days = 7
    elif re.search(r'\btomorrow\b', (q or ''), re.I):
        days = 2

    now = time.time()
    data, st = _get(CAL + '/calendars/primary/events', token, {
        'timeMin': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
        'timeMax': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now + days * 86400)),
        'singleEvents': 'true',
        'orderBy': 'startTime',
        'maxResults': 20,
    })
    if st != 200:
        return {'ok': False, 'error': 'Calendar refused: %s'
                                      % (data.get('error', {}).get('message', st))}

    items = data.get('items') or []
    if not items:
        return {'ok': True, 'summary': 'Nothing in the calendar for the next %d day(s).' % days}

    lines = []
    for ev in items:
        day, at = _when(ev)
        who = ev.get('summary', '(no title)')
        where = ev.get('location', '')
        lines.append('%s %s — %s%s' % (day, at, who, (' @ ' + where) if where else ''))
    return {'ok': True, 'source': 'Google Calendar', 'count': len(items),
            'summary': '\n'.join(lines)}


# --------------------------------------------------------------------- gmail

def _headers_of(msg):
    out = {}
    for h in ((msg.get('payload') or {}).get('headers') or []):
        out[h.get('name', '').lower()] = h.get('value', '')
    return out


def mail(q=None, unread_only=True, limit=8):
    token, err = _token()
    if not token:
        return {'ok': False, 'error': err}

    query = (q or '').strip()
    if not query:
        query = 'is:unread' if unread_only else 'in:inbox'

    data, st = _get(GMAIL + '/users/me/messages', token,
                    {'q': query, 'maxResults': limit})
    if st != 200:
        return {'ok': False, 'error': 'Gmail refused: %s'
                                      % (data.get('error', {}).get('message', st))}

    ids = [m['id'] for m in (data.get('messages') or [])]
    if not ids:
        return {'ok': True, 'summary': 'Nothing matching "%s".' % query}

    rows = []
    for mid in ids:
        # A list of pairs, not a dict: metadataHeaders repeats, and a dict
        # would silently drop everything but the last one.
        msg, ms = _get(GMAIL + '/users/me/messages/' + mid, token, [
            ('format', 'metadata'),
            ('metadataHeaders', 'From'),
            ('metadataHeaders', 'Subject'),
            ('metadataHeaders', 'Date'),
        ])
        if ms != 200:
            continue
        h = _headers_of(msg)
        frm = re.sub(r'\s*<[^>]+>', '', h.get('from', '')).strip('" ')
        subj = h.get('subject', '') or msg.get('snippet', '')[:60]
        rows.append('%s — %s' % (frm or 'unknown', subj))

    if not rows:
        return {'ok': True, 'summary': 'Nothing matching "%s".' % query}
    return {'ok': True, 'source': 'Gmail', 'count': len(rows),
            'summary': '\n'.join(rows)}


def command(action, query=None):
    if action in ('agenda', 'calendar', 'schedule'):
        return agenda(query)
    if action in ('mail', 'unread', 'inbox'):
        return mail(query)
    if action == 'search_mail':
        return mail(query, unread_only=False)
    return {'ok': False, 'error': 'Unknown Google action "%s".' % action}
