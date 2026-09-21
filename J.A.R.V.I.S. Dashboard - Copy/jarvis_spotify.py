"""
Spotify Web API for J.A.R.V.I.S.

Why this lives in the server rather than the page
-------------------------------------------------
Spotify does not accept `localhost` as a redirect URI — only an explicit
loopback literal such as `http://127.0.0.1:8123/callback`. The dashboard is
served from `http://localhost:8123`, so the OAuth callback necessarily lands on
a *different origin* from the page, and browser storage is per-origin. Trying
to hold the tokens in the page would mean writing them somewhere the page
cannot read back.

So the server owns the whole flow. It performs the PKCE exchange, keeps the
refresh token in a file next to itself, and exposes a small verb API to the
page. The browser never sees a token, which is also the safer arrangement: a
refresh token in localStorage is readable by any script that gets injected into
the page, whereas this one never crosses the wire.

Media keys still exist and still work. They are blind toggles that drive
whatever happens to be playing; this is the opposite — it can start a *named*
track, report what is actually playing, and set an exact volume.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_HOST = 'https://accounts.spotify.com'
API = 'https://api.spotify.com/v1'

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'spotify_auth.json')

SCOPES = ' '.join([
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing',
    'playlist-read-private',
    'playlist-read-collaborative',
])

_lock = threading.Lock()
_pending = {}          # state -> code_verifier, for in-flight logins


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


def set_client_id(client_id):
    with _lock:
        data = _read()
        data['client_id'] = (client_id or '').strip()
        _write(data)
    return {'ok': True}


def forget():
    with _lock:
        data = _read()
        _write({'client_id': data.get('client_id', '')})   # keep the app id
    return {'ok': True, 'disconnected': True}


# ------------------------------------------------------------------- request

def _api(method, path, token, body=None, params=None):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    if data is not None:
        req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if not raw:
                return {}, r.status
            return json.loads(raw.decode('utf-8')), r.status
    except urllib.error.HTTPError as err:
        raw = err.read().decode('utf-8', 'replace')
        try:
            return json.loads(raw), err.code
        except Exception:
            return {'error': {'message': raw[:200]}}, err.code


# ---------------------------------------------------------------------- auth

def login_url(redirect_uri, client_id=None):
    """Build the authorize URL for the PKCE flow.

    PKCE rather than the classic code flow because there is no confidential
    place to keep a client secret here — the whole point is that this runs on
    a desktop the user controls.
    """
    with _lock:
        data = _read()
        cid = (client_id or data.get('client_id') or '').strip()
        if cid:
            data['client_id'] = cid
            _write(data)

    if not cid:
        return None, ('No Spotify client ID yet. Create an app at '
                      'developer.spotify.com/dashboard, add '
                      'http://127.0.0.1:8123/callback as a Redirect URI, then paste the '
                      'Client ID into the Spotify box on the J.A.R.V.I.S. settings panel.')

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip('=')
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(16)

    _pending[state] = {'verifier': verifier, 'redirect': redirect_uri, 'at': time.time()}
    # forget anything that was abandoned more than ten minutes ago
    for k in [k for k, v in _pending.items() if time.time() - v['at'] > 600]:
        _pending.pop(k, None)

    q = urllib.parse.urlencode({
        'client_id': cid,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': SCOPES,
        'code_challenge_method': 'S256',
        'code_challenge': challenge,
        'state': state,
    })
    return AUTH_HOST + '/authorize?' + q, None


def _token_request(fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(AUTH_HOST + '/api/token', data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8')), None
    except urllib.error.HTTPError as err:
        raw = err.read().decode('utf-8', 'replace')
        return None, 'Spotify rejected the token request (%s): %s' % (err.code, raw[:200])
    except Exception as err:
        return None, 'Could not reach Spotify: %s' % err


def handle_callback(code, state):
    entry = _pending.pop(state, None)
    if not entry:
        return {'ok': False, 'error': 'That login link has expired or was already used. '
                                      'Press Connect again.'}

    data = _read()
    cid = data.get('client_id', '')
    tok, err = _token_request({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': entry['redirect'],
        'client_id': cid,
        'code_verifier': entry['verifier'],
    })
    if err:
        return {'ok': False, 'error': err}

    with _lock:
        data = _read()
        data['access_token'] = tok.get('access_token', '')
        data['refresh_token'] = tok.get('refresh_token', data.get('refresh_token', ''))
        data['expires_at'] = time.time() + int(tok.get('expires_in', 3600)) - 60
        _write(data)

    me, status = _api('GET', '/me', data['access_token'])
    if status == 200:
        with _lock:
            data = _read()
            data['user'] = me.get('display_name') or me.get('id') or ''
            data['product'] = me.get('product', '')
            _write(data)

    return {'ok': True, 'user': _read().get('user', '')}


def _token():
    """A valid access token, refreshed if it has aged out."""
    data = _read()
    if not data.get('refresh_token') and not data.get('access_token'):
        return None, 'Spotify is not linked to J.A.R.V.I.S. yet. This is NOT about signing into the Spotify app — that is already fine. The link is a separate one-time setup in J.A.R.V.I.S. itself: open the gear icon at the top right of THIS dashboard, find the Spotify section, paste a Client ID from developer.spotify.com/dashboard and press Connect. Tell the user exactly that, and do not suggest logging into Spotify or changing anything inside the Spotify application.'

    if data.get('access_token') and time.time() < data.get('expires_at', 0):
        return data['access_token'], None

    if not data.get('refresh_token'):
        return None, ('The Spotify link expired. Reopen the gear icon on the J.A.R.V.I.S. '
                      'dashboard, Spotify section, and press Connect again.')

    tok, err = _token_request({
        'grant_type': 'refresh_token',
        'refresh_token': data['refresh_token'],
        'client_id': data.get('client_id', ''),
    })
    if err:
        return None, err

    with _lock:
        data = _read()
        data['access_token'] = tok.get('access_token', '')
        if tok.get('refresh_token'):
            data['refresh_token'] = tok['refresh_token']
        data['expires_at'] = time.time() + int(tok.get('expires_in', 3600)) - 60
        _write(data)
    return data['access_token'], None


def status():
    data = _read()
    return {
        'ok': True,
        'has_client_id': bool(data.get('client_id')),
        'connected': bool(data.get('refresh_token') or data.get('access_token')),
        'user': data.get('user', ''),
        'product': data.get('product', ''),
    }


# -------------------------------------------------------------------- verbs

# Injected by serve.py so this module can start the desktop client without
# importing the server back (which would be circular).
_launcher = None


def set_launcher(fn):
    global _launcher
    _launcher = fn


def _devices(token):
    devs, st = _api('GET', '/me/player/devices', token)
    if st != 200:
        return None
    return devs.get('devices') or []


def _active_device(token):
    """Spotify refuses to start playback with no active device.

    Two separate traps live here. A closed Spotify is not a device at all, so
    the list comes back empty; and a freshly opened client is *available* but
    not *active*, so the first play of a session 404s unless playback is
    transferred to it. Between them these are the most confusing failures in
    this API, so both are handled rather than reported.
    """
    items = _devices(token)
    if items is None:
        return None, 'Could not list your Spotify devices.'

    if not items and _launcher:
        # Nothing registered — almost always because the app is not running.
        # Start it and give it a moment to appear rather than telling the user
        # to go and do it themselves.
        _launcher('spotify')
        for _ in range(12):                 # up to ~12s; a cold start is slow
            time.sleep(1.0)
            items = _devices(token) or []
            if items:
                break

    if not items:
        return None, ('Spotify is not running as a playable device. It was started, but it '
                      'has not registered yet — open Spotify, play anything once, and it '
                      'will stay available after that.')

    for d in items:
        if d.get('is_active'):
            return d, None
    return items[0], None


def _ensure_playback_target(token):
    dev, err = _active_device(token)
    if err:
        return None, err
    if not dev.get('is_active'):
        _api('PUT', '/me/player', token, body={'device_ids': [dev['id']], 'play': False})
        time.sleep(0.4)
    return dev, None


def _now_playing(token):
    data, st = _api('GET', '/me/player', token)
    if st == 204 or not data:
        return {'ok': True, 'playing': False, 'summary': 'Nothing is playing.'}
    item = data.get('item') or {}
    name = item.get('name', '')
    artists = ', '.join(a.get('name', '') for a in (item.get('artists') or []))
    is_playing = bool(data.get('is_playing'))
    where = (data.get('device') or {}).get('name', '')
    if not name:
        return {'ok': True, 'playing': is_playing, 'summary': 'Nothing is playing.'}
    return {
        'ok': True,
        'playing': is_playing,
        'track': name,
        'artist': artists,
        'device': where,
        'volume': (data.get('device') or {}).get('volume_percent'),
        'summary': ('%s — %s' % (name, artists) if artists else name)
                   + (' (paused)' if not is_playing else '')
                   + (' on %s' % where if where else ''),
    }


def _search_and_play(token, query, kind):
    dev, err = _ensure_playback_target(token)
    if err:
        return {'ok': False, 'error': err}

    res, st = _api('GET', '/search', token,
                   params={'q': query, 'type': kind, 'limit': 5})
    if st != 200:
        return {'ok': False, 'error': 'Search failed: %s'
                                      % (res.get('error', {}).get('message', st))}

    bucket = {'track': 'tracks', 'playlist': 'playlists', 'album': 'albums',
              'artist': 'artists'}[kind]
    items = [i for i in ((res.get(bucket) or {}).get('items') or []) if i]
    if not items:
        return {'ok': False, 'error': 'Nothing on Spotify matched "%s".' % query}

    top = items[0]
    body = {}
    if kind == 'track':
        body['uris'] = [top['uri']]
    else:
        body['context_uri'] = top['uri']

    out, st = _api('PUT', '/me/player/play', token, body=body,
                   params={'device_id': dev['id']})
    if st not in (200, 202, 204):
        msg = (out or {}).get('error', {}).get('message', 'status %s' % st)
        if st == 403:
            msg += (' — starting playback through the API needs Spotify Premium.')
        return {'ok': False, 'error': 'Could not start playback: %s' % msg}

    label = top.get('name', query)
    if kind == 'track':
        who = ', '.join(a.get('name', '') for a in (top.get('artists') or []))
        label += (' — ' + who) if who else ''
    return {'ok': True, 'started': label, 'kind': kind, 'device': dev.get('name', '')}


def command(action, query=None, value=None):
    token, err = _token()
    if not token:
        return {'ok': False, 'error': err}

    simple = {
        'pause':    ('PUT', '/me/player/pause'),
        'resume':   ('PUT', '/me/player/play'),
        'next':     ('POST', '/me/player/next'),
        'previous': ('POST', '/me/player/previous'),
    }

    if action == 'current':
        return _now_playing(token)

    if action == 'devices':
        devs, st = _api('GET', '/me/player/devices', token)
        if st != 200:
            return {'ok': False, 'error': 'Could not list devices.'}
        return {'ok': True, 'devices': [
            {'name': d.get('name'), 'type': d.get('type'), 'active': d.get('is_active')}
            for d in (devs.get('devices') or [])]}

    if action in ('play_track', 'play_playlist', 'play_album', 'play_artist'):
        if not query:
            return {'ok': False, 'error': 'No search terms supplied.'}
        kind = action.split('_', 1)[1]
        return _search_and_play(token, query, kind)

    if action == 'volume':
        try:
            pct = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'Volume must be a number from 0 to 100.'}
        out, st = _api('PUT', '/me/player/volume', token,
                       params={'volume_percent': pct})
        if st not in (200, 202, 204):
            return {'ok': False, 'error': 'Could not set volume (status %s).' % st}
        return {'ok': True, 'volume': pct}

    if action in simple:
        method, path = simple[action]
        if action == 'resume':
            dev, derr = _ensure_playback_target(token)
            if derr:
                return {'ok': False, 'error': derr}
        out, st = _api(method, path, token)
        if st in (200, 202, 204):
            return {'ok': True, 'did': action}
        msg = (out or {}).get('error', {}).get('message', 'status %s' % st)
        if st == 404:
            msg = ('No active Spotify device. Open Spotify and play something once so it '
                   'registers.')
        if st == 403:
            msg += ' — this usually means the account is not Premium.'
        return {'ok': False, 'error': msg}

    return {'ok': False, 'error': 'Unknown Spotify action "%s".' % action}
