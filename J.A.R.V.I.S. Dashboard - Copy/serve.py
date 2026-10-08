#!/usr/bin/env python3
"""
J.A.R.V.I.S. static server.

Identical to `python -m http.server` with two differences that matter:

  * It tells the browser not to cache anything. Plain http.server sends no
    cache headers at all, so Chrome caches heuristically — you edit a file,
    reload, and silently get the previous version. That costs far more time
    than the bandwidth it saves on a dashboard served from localhost.

  * It opens the browser for you, at localhost. That is not cosmetic: the
    microphone and the API call both require a secure context, which means
    https or localhost. Reaching the page as a file:// path or over a plain
    http LAN address breaks voice input.

Usage:  python serve.py [port] [--no-open]
"""

import http.server
import socketserver
import sys
import webbrowser
import threading
import io

# ===========================================================================
#  Search and fetch, proxied through this server.
#
#  A browser cannot call a search engine directly — CORS forbids it, and no
#  search API sets the headers that would allow it. But this server is the
#  page's own origin, so a request to /api/search is same-origin and simply
#  works. That is the whole trick, and it is what lets web search run on ANY
#  model provider rather than only on Anthropic, whose search executes
#  server-side on their end.
#
#  Backends, in order of preference:
#    * Brave    — set BRAVE_API_KEY. 2,000 queries/month free, reliable JSON.
#    * DuckDuckGo HTML — no key at all, but it is scraped markup, so it will
#      break whenever they change their template. Good enough as a default.
# ===========================================================================

import html as _html
import ipaddress
import mimetypes
import json as _json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

FETCH_LIMIT = 240_000        # bytes pulled off the wire
TEXT_LIMIT = 6_000           # characters handed back to the model


def _get(url, headers=None, data=None, timeout=12):
    req = urllib.request.Request(url, data=data)
    req.add_header('User-Agent', UA)
    req.add_header('Accept-Language', 'en-US,en;q=0.9')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(FETCH_LIMIT)
        charset = r.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset, 'replace'), r.geturl()


def _strip_html(doc):
    doc = re.sub(r'(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>', ' ', doc)
    doc = re.sub(r'(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>', '\n', doc)
    doc = re.sub(r'(?s)<[^>]+>', ' ', doc)
    doc = _html.unescape(doc)
    doc = re.sub(r'[ \t\r\f\v]+', ' ', doc)
    doc = re.sub(r'\n\s*\n+', '\n\n', doc)
    return doc.strip()


def _search_brave(q, key, n=6):
    url = 'https://api.search.brave.com/res/v1/web/search?' + urllib.parse.urlencode(
        {'q': q, 'count': n})
    body, _ = _get(url, headers={'Accept': 'application/json', 'X-Subscription-Token': key})
    data = _json.loads(body)
    out = []
    for item in (data.get('web', {}).get('results') or [])[:n]:
        out.append({
            'title': item.get('title', ''),
            'url': item.get('url', ''),
            'snippet': _strip_html(item.get('description', '') or '')
        })
    return out


def _search_ddg(q, n=6):
    body, _ = _get('https://html.duckduckgo.com/html/',
                   data=urllib.parse.urlencode({'q': q}).encode())

    out = []
    for m in re.finditer(
            r'(?is)<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body):
        href, title = m.group(1), _strip_html(m.group(2))

        # DuckDuckGo wraps results in a redirector; the real target is in uddg=
        if 'uddg=' in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = (qs.get('uddg') or [href])[0]
        if href.startswith('//'):
            href = 'https:' + href
        if not href.startswith('http'):
            continue

        out.append({'title': title, 'url': href, 'snippet': ''})
        if len(out) >= n:
            break

    snippets = [_strip_html(s) for s in re.findall(
        r'(?is)<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', body)]
    for i, s in enumerate(snippets[:len(out)]):
        out[i]['snippet'] = s

    return out


def _search_google(q, key, cx, n=6):
    """Google Programmable Search. 100 queries a day free.

    Two identifiers rather than one: an API key, and a Search Engine ID for the
    programmable engine itself. Both come from different consoles, which is the
    usual reason this is set up wrong."""
    url = 'https://www.googleapis.com/customsearch/v1?' + urllib.parse.urlencode(
        {'key': key, 'cx': cx, 'q': q, 'num': min(n, 10)})

    # Google explains a rejection properly, in the body of the response. urlopen
    # raises before anyone reads it, so the whole diagnosis was being discarded
    # and surfaced as a bare "HTTP Error 400: Bad Request" - which is true, and
    # says nothing about which of the two identifiers is wrong.
    try:
        body, _ = _get(url, headers={'Accept': 'application/json'})
    except urllib.error.HTTPError as err:
        detail = ''
        try:
            payload = _json.loads(err.read().decode('utf-8', 'replace'))
            detail = ((payload.get('error') or {}).get('message') or '').strip()
        except Exception:
            pass

        if not detail:
            raise RuntimeError('HTTP %s from Google' % err.code)

        low = detail.lower()
        if ('does not have the access' in low or 'has not been used in project' in low
                or 'is disabled' in low or 'accessnotconfigured' in low):
            # The commonest one by far, and the only one the user cannot fix by
            # retyping something: creating a key does NOT enable the API it is
            # for. Point at the exact page rather than describing it.
            hint = ('the key is fine - Custom Search JSON API is simply not enabled on '
                    'its Cloud project. Enable it at '
                    'console.cloud.google.com/apis/library/customsearch.googleapis.com '
                    '(pick the same project the key came from), then give it a minute')
        elif 'api key not valid' in low or 'api_key_invalid' in low:
            hint = 'the API key is wrong, or Custom Search API is not enabled for it'
        elif 'invalid argument' in low or 'invalid value' in low:
            hint = 'the Search Engine ID (cx) is wrong - it comes from '                    'programmablesearchengine.google.com, not the Cloud console'
        elif 'quota' in low or 'rate limit' in low:
            hint = 'the free 100-a-day allowance is spent'
        else:
            hint = ''
        raise RuntimeError(detail + (' - ' + hint if hint else ''))

    data = _json.loads(body)
    out = []
    for item in (data.get('items') or [])[:n]:
        out.append({'title': item.get('title', ''),
                    'url': item.get('link', ''),
                    'snippet': _strip_html(item.get('snippet', '') or '')})
    return out


def _search_mojeek(q, n=6):
    """Mojeek runs its own crawler and does not gate scrapers behind a captcha,
    which is what makes it usable with no key at all. Markup is stable:
    <a class="title" href="URL">TITLE</a> followed by <p class="s">SNIPPET</p>.
    """
    body, _ = _get('https://www.mojeek.com/search?' + urllib.parse.urlencode({'q': q}))

    out = []
    for m in re.finditer(
            r'(?is)<a class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<!--re-->|$)',
            body):
        href, title, tail = m.group(1), _strip_html(m.group(2)), m.group(3)
        if not href.startswith('http'):
            continue
        snip = ''
        sm = re.search(r'(?is)<p class="s">(.*?)</p>', tail)
        if sm:
            snip = _strip_html(sm.group(1))
        out.append({'title': title, 'url': href, 'snippet': snip})
        if len(out) >= n:
            break
    return out


# A model often re-asks the same thing within one turn, and a repeated search
# costs a full network round trip for an answer we already had. Short-lived so
# it can never serve a stale figure for something time-sensitive.
_SEARCH_CACHE = {}
SEARCH_TTL = 180


def do_search(q, key=None, google_key=None, google_cx=None):
    hit = _SEARCH_CACHE.get(q)
    if hit and (time.time() - hit[0]) < SEARCH_TTL:
        out = dict(hit[1])
        out['cached'] = True
        return out
    result = _do_search_uncached(q, key, google_key, google_cx)
    if result.get('ok'):
        _SEARCH_CACHE[q] = (time.time(), result)
        # keep the table small; this is a convenience, not a store
        if len(_SEARCH_CACHE) > 60:
            for k in sorted(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])[:20]:
                _SEARCH_CACHE.pop(k, None)
    return result


def _do_search_uncached(q, key=None, google_key=None, google_cx=None):
    """Try each backend in turn and report which one answered.

    Order matters. Brave is the only one with a contract behind it, so it wins
    when a key exists. Mojeek is the keyless default because it crawls its own
    index and does not challenge scrapers. DuckDuckGo is last: it now serves an
    anomaly page to this kind of request, so it is kept only as a long shot.
    """
    attempts = []

    key = (key or '').strip() or os.environ.get('BRAVE_API_KEY', '').strip()
    gkey = (google_key or '').strip() or os.environ.get('GOOGLE_SEARCH_KEY', '').strip()
    gcx = (google_cx or '').strip() or os.environ.get('GOOGLE_SEARCH_CX', '').strip()

    backends = []
    if gkey and gcx:
        # Best results of the lot when configured, but only 100 queries a day.
        backends.append(('google', lambda: _search_google(q, gkey, gcx)))
    if key:
        backends.append(('brave', lambda: _search_brave(q, key)))
    backends.append(('mojeek', lambda: _search_mojeek(q)))
    backends.append(('duckduckgo', lambda: _search_ddg(q)))

    for name, fn in backends:
        try:
            hits = fn()
            if hits:
                out = {'ok': True, 'engine': name, 'query': q, 'results': hits}
                # A configured backend that failed before this one succeeded was
                # previously thrown away, so a dead Google key looked exactly
                # like working search. Carry the failures with the win.
                if attempts:
                    out['skipped'] = attempts
                return out
            if name == 'google':
                # Google does not throttle a paid-quota API - an empty result
                # means the Programmable Search Engine matched nothing, which
                # for an engine limited to a site list is most general queries.
                attempts.append('google: no results - the engine answered but matched '
                                'nothing. A Programmable Search Engine restricted to a '
                                'site list only ever returns those sites; set it to '
                                '"Search the entire web but emphasize included sites" '
                                'to cover the open web')
            else:
                attempts.append('%s: no results (this engine throttles scrapers, '
                                'so it usually means rate-limited)' % name)
        except Exception as err:
            attempts.append('%s: %s' % (name, str(err)[:80]))

    return {'ok': False,
            'error': 'Every search backend failed (' + '; '.join(attempts) + '). '
                     'Setting BRAVE_API_KEY makes this reliable.'}


def _is_public(host):
    """Refuse anything on the local network.

    /api/fetch takes a URL from the model and retrieves it with this server's
    own network access. Without this check a prompt injection on a web page
    could walk the LAN or hit a cloud metadata endpoint through us.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return False
    return True


def _main_region(doc):
    """Narrow to the article body before stripping tags.

    Otherwise the character budget goes on site chrome — on Wikipedia the first
    thousand characters are the language list and the donate banner, and the
    model never reaches the actual subject.
    """
    for pattern in (r'(?is)<main[^>]*>(.*?)</main>',
                    r'(?is)<article[^>]*>(.*?)</article>',
                    r'(?is)<div[^>]+id="mw-content-text"[^>]*>(.*)',
                    r'(?is)<div[^>]+(?:id|class)="[^"]*(?:content|post|entry)[^"]*"[^>]*>(.*)'):
        m = re.search(pattern, doc)
        if m and len(m.group(1)) > 500:
            return m.group(1)
    return doc


def do_fetch(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return {'ok': False, 'error': 'Only http and https URLs can be fetched.'}
    if not parsed.hostname or not _is_public(parsed.hostname):
        return {'ok': False, 'error': 'Refused: that host is on a private or local network.'}
    try:
        body, final = _get(url, timeout=15)
    except urllib.error.HTTPError as err:
        return {'ok': False, 'error': 'HTTP %s fetching that page.' % err.code}
    except Exception as err:
        return {'ok': False, 'error': 'Could not fetch that page: %s' % err}

    title = ''
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', body)
    if m:
        title = _strip_html(m.group(1))

    text = _strip_html(_main_region(body))
    truncated = len(text) > TEXT_LIMIT
    return {'ok': True, 'url': final, 'title': title,
            'truncated': truncated, 'text': text[:TEXT_LIMIT]}


def _is_loopback(addr):
    try:
        return ipaddress.ip_address(addr).is_loopback
    except Exception:
        return False


def llm_proxy(base, key, payload, timeout=300):
    """Forward one chat-completions call upstream and hand back the raw stream.

    This exists because some providers serve no CORS headers at all — Ollama
    Cloud answers the preflight with 405 — which makes them flatly unreachable
    from a browser no matter how the request is built. This server has no such
    restriction, so it makes the call and relays the bytes.
    """
    url = base.rstrip('/') + '/chat/completions'
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', UA)
    if key:
        req.add_header('Authorization', 'Bearer ' + key)
    return urllib.request.urlopen(req, timeout=timeout)


# ===========================================================================
#  Launching things on this machine.
#
#  A web page cannot start a native application, and window.open() from an
#  async model reply gets swallowed by the popup blocker because there was no
#  user gesture behind it. Both problems disappear here: this process is an
#  ordinary desktop program and can simply ask the OS to open something.
#
#  What may be launched is deliberately NOT arbitrary. The model never supplies
#  a command line — it supplies a name, matched against shortcuts already in
#  the Start Menu. That is the software the user chose to install, and nothing
#  outside that list can be started.
# ===========================================================================

import difflib
import subprocess
import time

import jarvis_spotify as spotify
import jarvis_knowledge as knowledge
import jarvis_google as google
import jarvis_memory as memory
import jarvis_recall as recall
import jarvis_files as files
import jarvis_desktop as desktop
import jarvis_jobs as jobs
import jarvis_lessons as lessons
import jarvis_apply as apply_stage
import job_hunt_skill as hunt
import jarvis_minecraft as minecraft
import jarvis_video as video

_APP_CACHE = {}
_APP_CACHE_AT = 0
APP_CACHE_TTL = 300          # seconds; installs are rare, rescans are not free

# Launching one of these by voice is never what "open X" meant, and some of
# them are destructive to trigger by accident.
_APP_DENY = ('uninstall', 'setup', 'installer', 'repair', 'readme',
             'license', 'documentation', 'help', 'remove ')


def _start_menu_roots():
    roots = []
    for var, tail in (('APPDATA', r'Microsoft\Windows\Start Menu\Programs'),
                      ('PROGRAMDATA', r'Microsoft\Windows\Start Menu\Programs')):
        base = os.environ.get(var)
        if base:
            path = os.path.join(base, tail)
            if os.path.isdir(path):
                roots.append(path)
    return roots


def discover_apps(force=False):
    """Friendly name -> shortcut path, taken from the Start Menu."""
    global _APP_CACHE, _APP_CACHE_AT
    now = time.time()
    if _APP_CACHE and not force and (now - _APP_CACHE_AT) < APP_CACHE_TTL:
        return _APP_CACHE

    apps = {}
    for root in _start_menu_roots():
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.lower().endswith(('.lnk', '.url')):
                    continue
                name = os.path.splitext(fn)[0].strip()
                low = name.lower()
                if any(bad in low for bad in _APP_DENY):
                    continue
                apps.setdefault(low, os.path.join(dirpath, fn))

    _APP_CACHE = apps
    _APP_CACHE_AT = now
    return apps


# Words that carry no identity — they are how people phrase a request, not
# part of any program's name.
_FILLER = {'open', 'launch', 'start', 'run', 'play', 'the', 'a', 'my', 'app',
           'application', 'program', 'please', 'up', 'on', 'pc', 'computer'}


def match_app(query):
    """Resolve what the user said to one installed application.

    Returns (name, path) on a hit, or (None, [suggestions]) on a miss.

    This is deliberately forgiving because the input is dictated speech. It has
    to survive both extra words ("open spotify please") and outright mishearing
    ("spotifi"), neither of which a plain substring test handles.
    """
    apps = discover_apps()
    raw = (query or '').strip().lower()
    if not raw:
        return None, []

    def pick(pool):
        # Shortest sensible match wins: "Spotify" over "Spotify Web Helper".
        best = min(pool, key=len)
        return best, apps[best]

    if raw in apps:
        return raw, apps[raw]

    # 1. the whole phrase, as a prefix then as a substring
    for pool in ([k for k in apps if k.startswith(raw)],
                 [k for k in apps if raw in k]):
        if pool:
            return pick(pool)

    # 2. drop the filler and try again — "open spotify please" -> "spotify"
    words = [w for w in re.split(r'\W+', raw) if w and w not in _FILLER]
    core = ' '.join(words)
    if core and core != raw:
        if core in apps:
            return core, apps[core]
        for pool in ([k for k in apps if k.startswith(core)],
                     [k for k in apps if core in k]):
            if pool:
                return pick(pool)

    # 3. any single meaningful word
    for w in sorted(words, key=len, reverse=True):
        if len(w) < 3:
            continue
        pool = [k for k in apps if w in k]
        if pool:
            return pick(pool)

    # 4. give up on exactness and go phonetic-ish: "spotifi" -> "spotify".
    #    Only now, so a real match is never beaten by a fuzzy one.
    for candidate in ([core] if core else []) + words + [raw]:
        if len(candidate) < 4:
            continue
        close = difflib.get_close_matches(candidate, list(apps.keys()), n=1, cutoff=0.72)
        if close:
            return close[0], apps[close[0]]

    return None, sorted(apps.keys())[:40]


def launch_app(query):
    name, found = match_app(query)
    if name is None:
        return {'ok': False,
                'error': 'No installed application matches "%s".' % query,
                'available_sample': found}
    try:
        if hasattr(os, 'startfile'):
            os.startfile(found)                        # Windows
        else:
            subprocess.Popen(['xdg-open', found])      # anything else
        return {'ok': True, 'launched': name}
    except Exception as err:
        return {'ok': False, 'error': 'Could not launch %s: %s' % (name, err)}


# ===========================================================================
#  Media and volume keys.
#
#  Every media application on Windows — Spotify, a browser tab, VLC, the
#  built-in player — listens for the same global media keys. Pressing one is
#  therefore the general answer to "play", "skip" and "turn it down", without
#  needing to integrate with any of them individually.
#
#  This sends a FIXED SET of virtual keys, chosen by name from the table below.
#  It is not a general keystroke injector: no caller can ask for an arbitrary
#  key, so this cannot be turned into a way to type into whatever happens to
#  be focused.
# ===========================================================================

_VK = {
    'play_pause':  0xB3,
    'next':        0xB0,
    'previous':    0xB1,
    'stop':        0xB2,
    'volume_up':   0xAF,
    'volume_down': 0xAE,
    'mute':        0xAD,
}

KEYEVENTF_KEYUP = 0x0002


def press_key(name, repeat=1):
    if name not in _VK:
        return {'ok': False, 'error': 'Unknown key "%s". Known: %s'
                                      % (name, ', '.join(sorted(_VK)))}
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except Exception as err:
        return {'ok': False, 'error': 'Media keys are Windows-only here (%s).' % err}

    code = _VK[name]
    try:
        for _ in range(max(1, min(int(repeat), 10))):
            user32.keybd_event(code, 0, 0, 0)                 # down
            user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)   # up
            time.sleep(0.03)
        return {'ok': True, 'pressed': name, 'times': max(1, min(int(repeat), 10))}
    except Exception as err:
        return {'ok': False, 'error': 'Could not send %s: %s' % (name, err)}


def lock_screen():
    try:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return {'ok': True, 'locked': True}
    except Exception as err:
        return {'ok': False, 'error': 'Could not lock the workstation: %s' % err}


def open_url_here(url):
    """Open a URL in the real default browser, not a popup the page can't spawn."""
    if not url.startswith(('http://', 'https://')):
        return {'ok': False, 'error': 'Only http and https URLs can be opened.'}
    try:
        webbrowser.open(url)
        return {'ok': True, 'opened': url}
    except Exception as err:
        return {'ok': False, 'error': 'Could not open that URL: %s' % err}


# Let the Spotify bridge start the desktop client when no device is registered.
# Passed as a callback rather than imported, so the two modules stay one-way.
spotify.set_launcher(lambda name: launch_app(name))


# ===========================================================================
#  Screen capture.
#
#  A browser cannot see the desktop — getDisplayMedia would need a permission
#  prompt and a user gesture for every single look, which is unusable for
#  "what does this error say". This process has no such restriction.
#
#  The image is downscaled hard before it leaves here. A 4K screenshot is
#  enormous as base64 and costs a fortune in image tokens for no gain: a vision
#  model reads a 1280px-wide frame as well as a 3840px one.
# ===========================================================================

SHOT_WIDTH = 1280
SHOT_QUALITY = 70


def grab_screen():
    try:
        from PIL import ImageGrab
    except ImportError:
        return {'ok': False, 'error': 'Screen capture needs Pillow. Install it with: '
                                      'pip install pillow'}
    try:
        import base64 as _b64
        import io as _io

        img = ImageGrab.grab()
        w, h = img.size
        if w > SHOT_WIDTH:
            img = img.resize((SHOT_WIDTH, int(h * SHOT_WIDTH / w)))
        if img.mode != 'RGB':
            img = img.convert('RGB')

        buf = _io.BytesIO()
        img.save(buf, format='JPEG', quality=SHOT_QUALITY)
        raw = buf.getvalue()
        return {'ok': True,
                'width': img.size[0], 'height': img.size[1],
                'bytes': len(raw),
                'data_url': 'data:image/jpeg;base64,' + _b64.b64encode(raw).decode()}
    except Exception as err:
        return {'ok': False, 'error': 'Could not capture the screen: %s' % err}


# Files that must never be served, however they are asked for.
PRIVATE = (
    'spotify_auth.json', 'google_auth.json', 'jarvis_state.json',
    '.gitignore', 'launch.json', 'jarvis_skills_state.json', 'jarvis_skills_state.json.tmp',
)
PRIVATE_EXT = ('.py', '.db', '.db-wal', '.db-shm', '.env', '.pem', '.key')


def _is_private(route):
    name = os.path.basename(urllib.parse.unquote(route or '')).lower()
    if not name:
        return False
    if name in PRIVATE:
        return True
    if name.endswith(PRIVATE_EXT):
        return True
    if name.endswith('.json') and name.startswith(('spotify', 'google', 'jarvis')):
        return True
    return False


ARGS = [a for a in sys.argv[1:] if not a.startswith('-')]
PORT = int(ARGS[0]) if ARGS else 8123
OPEN_BROWSER = '--no-open' not in sys.argv

# The headless render in jarvis_files points a browser back at /preview/, so it
# has to know which port answers. Told once, here, rather than guessed there.
files.configure(port=PORT)


class Handler(http.server.SimpleHTTPRequestHandler):


    # ------------------------------------------------------------ tool API
    def _html_page(self, title, message, ok=True):
        """A plain confirmation page for the OAuth round trip.

        The callback lands on 127.0.0.1, not on the dashboard's own origin, so
        this tab cannot talk to the app. It just has to tell the user what
        happened and that they may close it.
        """
        accent = '#35d6ff' if ok else '#ff5f6b'
        body = ("""<!doctype html><meta charset="utf-8">
<title>%s</title>
<style>
 body{background:#05070c;color:#c9d4e2;font:15px/1.6 system-ui,sans-serif;
      display:grid;place-items:center;height:100vh;margin:0}
 .card{max-width:34rem;padding:2.2rem 2.6rem;border:1px solid #1b2432;
       border-left:2px solid %s;border-radius:10px;background:#0a0f18}
 h1{font-size:14px;letter-spacing:.14em;text-transform:uppercase;color:%s;margin:0 0 .8rem}
 b{color:#eaf2ff}
</style>
<div class="card"><h1>%s</h1><p>%s</p></div>""" % (title, accent, accent, title, message))

        raw = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json_out(self, obj, code=200):
        body = _json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _permission_gate(self, route):
        """Single server boundary, before any protected handler executes."""
        import jarvis_permissions as permissions
        if route not in permissions.ROUTES and route not in ('/api/permissions/command','/api/skills/command'):
            return False
        host = urllib.parse.urlparse('http://' + self.headers.get('Host', '')).hostname
        origin = self.headers.get('Origin')
        if not _is_loopback(self.client_address[0]) or host not in ('localhost', '127.0.0.1', '::1') or (origin and urllib.parse.urlparse(origin).netloc != self.headers.get('Host')):
            self._json_out({'ok': False, 'error': 'Permission requests require the local origin.'}, 403)
            return True
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1048576 or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ValueError('Bounded application/json required.')
            raw = self.rfile.read(length)
            data = _json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Object required.')
            self.rfile = io.BytesIO(raw)  # Original handlers consume the exact same bytes.
            store = permission_store()
            session = self.headers.get('X-Jarvis-UI')
            if route == '/api/skills/command':
                if session not in store.sessions:
                    raise ValueError('Trusted UI session required for skill management.')
                self._json_out(skill_registry().command(data))
                return True
            if route == '/api/permissions/command':
                if data.get('action') == 'context' and data.get('context', {}).get('background'):
                    run = task_store().command({'action': 'get_run', 'run_id': data['context'].get('task_run_id')})
                    if not run.get('ok'):
                        raise ValueError('Unknown durable run.')
                    task = task_store().command({'action': 'get', 'task': run['run']['task_id']})
                    data['context']['run_policy'] = task['task']['run_policy']
                if data.get('action') == 'create_policy' and data.get('capability') in ('READ_FILE','READ_PROJECT','WRITE_FILE','CREATE_FILE','MODIFY_PROJECT','EXECUTE_CODE','RUN_TESTS') and data.get('scope_type', 'target') != 'global':
                    value = data.get('scope_value', '')
                    if isinstance(value, str) and value and not os.path.isabs(value):
                        data['scope_value'] = os.path.realpath(os.path.join(files._config['projects'], value))
                result = store.command(data, session)
                self._json_out(result, 200 if result.get('ok') else 403)
                return True
            tool = permissions.ROUTES[route]
            if tool == 'tasks' and data.get('action') in ('due','claim','check_claim','heartbeat','complete','fail','interrupt','get_run'):
                return False  # Fenced Phase 2 runtime mechanics; never model tools.
            proposed = {'kind': 'route', 'tool': tool, 'input': data}
            if not skill_action_allowed(proposed):
                self._json_out({'ok': False, 'error': 'FAILED - Skill is disabled, invalid or unavailable.'}, 403)
                return True
            receipt = self.headers.get('X-Jarvis-Authorization')
            if receipt:
                allowed = store.consume(proposed, receipt, session)
                result = {'decision': 'ALLOW' if allowed else 'DENY', 'reason': 'invalid_authorization_receipt'}
            else:
                result = store.check(proposed, session)
                # Harmless legacy reads remain functional; protected actions never execute without a receipt.
                allowed = result['decision'] == 'ALLOW' and store.consume(proposed, result.get('receipt'), session)
            if not allowed:
                self._json_out({'ok': False, 'error': 'PERMISSION_' + result['decision'] + ': ' + result.get('reason', 'protected action'), 'permission': {k: v for k, v in result.items() if k != 'receipt'}}, 403)
                return True
            return False
        except (ValueError, TypeError, KeyError):
            self._json_out({'ok': False, 'error': 'Invalid or stale permission request.'}, 400)
            return True
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return True
        except Exception:
            self._json_out({'ok': False, 'error': 'Permission broker unavailable; action stopped.'}, 503)
            return True

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if self._permission_gate(parsed.path):
            return

        if parsed.path == '/api/tasks/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out({'ok': False, 'error': 'Tasks are local only.'}, 403)
            host = urllib.parse.urlparse('http://' + self.headers.get('Host', '')).hostname
            if host not in ('localhost', '127.0.0.1', '::1'):
                return self._json_out({'ok': False, 'error': 'Task requests require a loopback host.'}, 403)
            origin = self.headers.get('Origin')
            if origin and (urllib.parse.urlparse(origin).scheme not in ('http', 'https') or urllib.parse.urlparse(origin).netloc != self.headers.get('Host')):
                return self._json_out({'ok': False, 'error': 'Task requests must use this origin.'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536 or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Send bounded application/json task commands.')
                data = _json.loads(self.rfile.read(length).decode('utf-8'))
                result = task_store().command(data)
                return self._json_out(result, 200 if result.get('ok') else 400)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return  # A disconnected worker is recovered by its durable lease.
            except (ValueError, UnicodeError):
                return self._json_out({'ok': False, 'error': 'Invalid task request.'}, 400)
            except Exception:
                return self._json_out({'ok': False, 'error': 'Task storage is unavailable. Ordinary chat remains available.'}, 503)

        if parsed.path == '/api/extract':
            # PDFs and .docx cannot be read in the browser. The document indexer
            # already knows how, so an upload is written to a temp file, read
            # with the same code, and thrown away.
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)

            import tempfile
            ctype = self.headers.get('content-type', '')
            length = int(self.headers.get('content-length', 0))
            if length > 30 * 1024 * 1024:
                return self._json_out({'ok': False, 'error': 'That file is too large.'}, 413)

            raw = self.rfile.read(length)
            m = re.search(r'boundary=([^;]+)', ctype)
            if not m:
                return self._json_out({'ok': False, 'error': 'Malformed upload.'}, 400)

            boundary = ('--' + m.group(1).strip('"')).encode()
            name, blob = '', b''
            for part in raw.split(boundary):
                head, sep, body = part.partition(b'\r\n\r\n')
                if not sep or b'filename=' not in head:
                    continue
                fm = re.search(rb'filename="([^"]*)"', head)
                name = (fm.group(1).decode('utf-8', 'replace') if fm else 'upload')
                blob = body.rstrip(b'\r\n-')
                break

            if not blob:
                return self._json_out({'ok': False, 'error': 'No file in the upload.'}, 400)

            ext = os.path.splitext(name)[1] or '.bin'
            tmp = os.path.join(tempfile.gettempdir(), 'jarvis_upload' + ext)
            try:
                with open(tmp, 'wb') as fh:
                    fh.write(blob)
                text = recall.read_text(tmp)
                if text is None:
                    return self._json_out({'ok': False, 'error':
                        'That is a PDF and pypdf is not installed (pip install pypdf).'})
                if not (text or '').strip():
                    return self._json_out({'ok': False, 'error':
                        'No readable text was found in %s.' % name})
                return self._json_out({'ok': True, 'name': name, 'text': text[:60000]})
            except Exception as err:
                return self._json_out({'ok': False, 'error': str(err)})
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        if parsed.path == '/api/desktop/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            return self._json_out(desktop.command(body.get('action', ''), body.get('text')))

        if parsed.path in ('/api/recall/command', '/api/files/command'):
            # Both of these touch the disk, so neither answers the network.
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}

            if parsed.path == '/api/recall/command':
                act = body.get('action', '')
                if act == 'index':
                    return self._json_out(recall.index_folder(
                        body.get('folder', ''), bool(body.get('rebuild'))))
                if act == 'forget':
                    return self._json_out(recall.forget_folder(body.get('folder', '')))
                if act == 'search':
                    return self._json_out(recall.search(
                        body.get('query', ''), body.get('kind') or None))
                if act == 'log_exchange':
                    return self._json_out(recall.remember_exchange(
                        body.get('user', ''), body.get('reply', '')))
                return self._json_out({'ok': False, 'error': 'Unknown recall action.'}, 400)

            if body.get('configure'):
                return self._json_out(files.configure(
                    body.get('roots'), body.get('projects')))
            # `action` is passed positionally, so it must not also appear in the
            # splat — Python raises "multiple values for argument" and the
            # handler dies mid-response, which the browser reports only as a
            # bare "Failed to fetch".
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(files.command(body.get('action', ''), **rest))

        if parsed.path == '/api/video/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(video.command(body.get('action', ''), **rest))

        if parsed.path == '/api/apply/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(apply_stage.command(body.get('action', ''), **rest))

        if parsed.path == '/api/lessons/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(lessons.command(body.get('action', ''), **rest))

        if parsed.path == '/api/jobs/command':
            # Touches the disk and the network on his behalf, so it answers
            # only to this machine, like everything else that does.
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(jobs.command(body.get('action', ''), **rest))

        if parsed.path == '/api/minecraft/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(minecraft.command(body.get('action', ''), **rest))

        if parsed.path == '/api/hunt/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            rest = {k: v for k, v in body.items() if k != 'action'}
            return self._json_out(hunt.command(body.get('action', ''), **rest))

        if parsed.path == '/api/memory/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            if body.get('clear_memories'):
                return self._json_out(memory.clear_memories())
            # An episode carries more than the one text field command() takes,
            # so it gets its own door rather than a stringly-typed payload.
            if body.get('action') == 'episode':
                return self._json_out(memory.record_episode(
                    body.get('request'), body.get('actions'),
                    body.get('outcome') or 'ok', body.get('detail')))
            return self._json_out(memory.command(
                body.get('action', ''), body.get('text'), body.get('when')))

        if parsed.path == '/api/google/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            if body.get('client_id') is not None or body.get('client_secret') is not None:
                return self._json_out(google.set_credentials(
                    body.get('client_id'), body.get('client_secret')))
            if body.get('disconnect'):
                return self._json_out(google.forget())
            return self._json_out(google.command(body.get('action', ''), body.get('query')))

        if parsed.path == '/api/spotify/command':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Only allowed from this machine.'}, 403)
            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            if body.get('client_id') is not None:
                return self._json_out(spotify.set_client_id(body['client_id']))
            if body.get('disconnect'):
                return self._json_out(spotify.forget())
            return self._json_out(spotify.command(
                body.get('action', ''), body.get('query'), body.get('value')))

        if parsed.path == '/api/open':
            # Starting programs is the most consequential thing this server
            # does, so it answers only to the machine it runs on.
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Launching is only allowed from this machine.'}, 403)

            length = int(self.headers.get('content-length', 0))
            try:
                body = _json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}

            if body.get('app'):
                return self._json_out(launch_app(body['app']))
            if body.get('url'):
                return self._json_out(open_url_here(body['url']))
            if body.get('settings'):
                # Windows exposes its settings pages as a URI scheme, which is
                # the only way to drop the user straight onto the voice
                # installer instead of describing where to click.
                try:
                    os.startfile('ms-settings:%s' % body['settings'])
                    return self._json_out({'ok': True, 'opened': body['settings']})
                except Exception as err:
                    return self._json_out({'ok': False, 'error': str(err)})
            if body.get('key'):
                return self._json_out(press_key(body['key'], body.get('repeat', 1)))
            if body.get('lock'):
                return self._json_out(lock_screen())
            return self._json_out(
                {'ok': False, 'error': 'Supply app, url, key or lock.'}, 400)

        if parsed.path != '/api/llm':
            self.send_error(404, 'Not Found')
            return

        # An open relay is exactly what this would be if anyone on the network
        # could drive it, so it answers only to the machine it runs on.
        if not _is_loopback(self.client_address[0]):
            return self._json_out(
                {'error': 'The model proxy only accepts requests from this machine.'}, 403)

        base = self.headers.get('X-Upstream-Base', '').strip()
        key = self.headers.get('X-Upstream-Key', '').strip()
        if not base.startswith(('http://', 'https://')):
            return self._json_out({'error': 'Missing or invalid X-Upstream-Base.'}, 400)

        length = int(self.headers.get('content-length', 0))
        payload = self.rfile.read(length)

        try:
            up = llm_proxy(base, key, payload)
        except urllib.error.HTTPError as err:
            body = err.read()
            self.send_response(err.code)
            self.send_header('Content-Type', err.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except Exception as err:
            return self._json_out({'error': 'Upstream unreachable: %s' % err}, 502)

        # Relay verbatim. No Content-Length: the body is a stream and the
        # client reads until close.
        self.send_response(200)
        self.send_header('Content-Type', up.headers.get('Content-Type', 'application/json'))
        self.end_headers()
        try:
            while True:
                chunk = up.read(512)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError):
            pass          # the browser aborted the turn; nothing to clean up
        finally:
            up.close()

    def _serve_project(self, route):
        """Serve a project folder for the live preview.

        Kept separate from the dashboard's own static root: the preview shows
        what he is building, and mixing the two would let a generated page
        reach the dashboard's files."""
        rest = route[len('/preview/'):]
        project, _, rel = rest.partition('/')
        if not project:
            return self._json_out({'ok': False, 'error': 'No project named.'}, 400)

        root = os.path.join(files._config['projects'], project)
        if not os.path.isdir(root):
            return self._html_page('No such project',
                                   'Nothing called <b>%s</b> in the projects folder.'
                                   % project, ok=False)

        target = os.path.abspath(os.path.join(root, rel or 'index.html'))
        if os.path.isdir(target):
            target = os.path.join(target, 'index.html')
        if not files._within(target, [root]) or not os.path.isfile(target):
            return self._html_page('Not found',
                                   'No <b>%s</b> in that project yet.' % (rel or 'index.html'),
                                   ok=False)

        ctype, _ = mimetypes.guess_type(target)
        try:
            with open(target, 'rb') as fh:
                blob = fh.read()
        except Exception as err:
            return self._json_out({'ok': False, 'error': str(err)}, 500)

        self.send_response(200)
        self.send_header('Content-Type', ctype or 'application/octet-stream')
        self.send_header('Content-Length', str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        args = urllib.parse.parse_qs(parsed.query)
        if route == '/api/skills' or route == '/api/skills/status' or route.startswith('/api/skills/'):
            try:
                registry = skill_registry()
                if route in ('/api/skills','/api/skills/status'):
                    return self._json_out(registry.snapshot())
                if route.startswith('/api/skills/tool/'):
                    name = urllib.parse.unquote(route[len('/api/skills/tool/'):])
                    if not registry.enabled_tool(name):
                        return self._json_out({'ok':False,'error':'FAILED - Skill is disabled, invalid or unavailable.'},403)
                    skill = next(s for s in registry.snapshot()['skills'] if any(t['name']==name for t in s['tools']))
                    return self._json_out({'ok':True,'skill':skill})
                return self._json_out(registry.command({'action':'get','id':route[len('/api/skills/'):]}))
            except (ValueError, StopIteration):
                return self._json_out({'ok':False,'error':'Unknown skill.'},404)
            except Exception:
                return self._json_out({'ok':False,'error':'Skill registry unavailable.'},503)
        import jarvis_permissions
        proposed = jarvis_permissions.read_action(route, args)
        if proposed:
            try:
                store = permission_store()
                session = self.headers.get('X-Jarvis-UI')
                if route != '/api/memory/due' and not skill_action_allowed(proposed):
                    return self._json_out({'ok':False,'error':'FAILED - Skill is disabled, invalid or unavailable.'},403)
                receipt = self.headers.get('X-Jarvis-Authorization')
                if receipt:
                    allowed = store.consume(proposed, receipt, session)
                else:
                    decision = store.check(proposed, session)
                    allowed = decision['decision'] == 'ALLOW' and store.consume(proposed, decision.get('receipt'), session)
                if not allowed:
                    return self._json_out({'ok': False, 'error': 'PERMISSION_DENIED: protected read.'}, 403)
            except Exception:
                return self._json_out({'ok': False, 'error': 'Permission broker unavailable; read stopped.'}, 503)

        if route == '/api/health':
            # Deliberately does NOT advertise `jarvis`. That flag means "this
            # server holds the API key and proxies chat", which it does not —
            # claiming it would send every conversation to a route that is
            # not implemented here.
            return self._json_out(server_capabilities())

        # ---- Spotify OAuth lives on the server; see jarvis_spotify.py ----
        if route == '/api/screenshot':
            if not _is_loopback(self.client_address[0]):
                return self._json_out(
                    {'ok': False, 'error': 'Screen capture is only allowed from this machine.'}, 403)
            return self._json_out(grab_screen())

        if route == '/preview-file':
            # One still image, by absolute path, so the media bay can show the
            # frame it just produced instead of describing it.
            #
            # Deliberately narrow: this machine only, images only, and only
            # under the folders the file layer already considers readable. It
            # is a viewer, not a second way out of the sandbox.
            if not _is_loopback(self.client_address[0]):
                return self._json_out({'ok': False, 'error': 'Local only.'}, 403)

            want = os.path.abspath((args.get('path') or [''])[0])
            if not want or not files._readable(want) or not os.path.isfile(want):
                self.send_error(404, 'Not found')
                return None
            if os.path.splitext(want)[1].lower() not in (
                    '.jpg', '.jpeg', '.png', '.gif', '.webp'):
                self.send_error(415, 'Images only')
                return None

            ctype, _ = mimetypes.guess_type(want)
            with open(want, 'rb') as fh:
                blob = fh.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype or 'image/jpeg')
            self.send_header('Content-Length', str(len(blob)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(blob)
            return None

        if route == '/frame':
            # A wrapper that holds one page in an iframe of an exact width, so
            # a narrow render gets a real narrow layout viewport. Served from
            # here rather than a temp file because a file:// parent does not
            # share Chrome's virtual-time clock with an http child — the shot
            # then lands before any entrance animation has run, and half the
            # page appears to be missing. See render() in jarvis_files.
            src = (args.get('src') or [''])[0]
            if not src.startswith('/preview/'):
                return self._json_out({'ok': False, 'error': 'Only previews.'}, 400)
            try:
                fw = max(120, min(2000, int((args.get('w') or ['390'])[0])))
                fh_ = max(120, min(6000, int((args.get('h') or ['760'])[0])))
            except ValueError:
                return self._json_out({'ok': False, 'error': 'Bad size.'}, 400)

            body = ('<!doctype html><meta charset="utf-8"><title>frame</title>'
                    '<style>html,body{margin:0;padding:0;background:#fff;'
                    'overflow:hidden}iframe{border:0;display:block;width:%dpx;'
                    'height:%dpx}</style><iframe src="%s" scrolling="no"></iframe>'
                    % (fw, fh_, _html.escape(src, quote=True))).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return None

        if route.startswith('/preview/'):
            return self._serve_project(route)

        if route == '/api/recall/status':
            return self._json_out(recall.index_status())

        if route == '/api/files/capabilities':
            return self._json_out(files.command('capabilities'))

        if route == '/api/memory/due':
            # Polled by the page; anything that came due while the dashboard
            # was shut still surfaces here on the next poll.
            return self._json_out(memory.due_reminders())

        if route == '/api/memory/all':
            return self._json_out({
                'ok': True,
                'memories': memory.memories_for_prompt(),
                'reminders': memory.list_reminders().get('items', []),
            })

        if route == '/api/google/status':
            return self._json_out(google.status())

        if route == '/api/google/login':
            redirect = 'http://127.0.0.1:%d/google/callback' % PORT
            url, err = google.login_url(redirect,
                                        (args.get('client_id') or [''])[0],
                                        (args.get('client_secret') or [''])[0])
            if err:
                return self._json_out({'ok': False, 'error': err}, 400)
            self.send_response(302)
            self.send_header('Location', url)
            self.end_headers()
            return None

        if route == '/google/callback':
            denied = (args.get('error') or [''])[0]
            if denied:
                # access_denied on a Testing-mode consent screen almost always
                # means the account was never added as a test user, which the
                # bare error code does nothing to convey.
                if denied == 'access_denied':
                    return self._html_page(
                        'Google blocked this sign-in',
                        'Your OAuth consent screen is in <b>Testing</b> mode and this '
                        'Google account is not on its test-user list.<br><br>'
                        'Fix it in the Cloud console: <b>APIs &amp; Services</b> &rarr; '
                        '<b>OAuth consent screen</b> &rarr; <b>Audience</b> (older consoles '
                        'call it <b>Test users</b>) &rarr; <b>Add users</b> &rarr; enter the '
                        'same Gmail address you are signing in with &rarr; <b>Save</b>. '
                        'Then press Connect again.<br><br>'
                        'You do not need to publish the app or pass Google verification '
                        'for personal use.', ok=False)
                return self._html_page('Google declined',
                                       'Authorisation was refused: ' + denied, ok=False)
            r = google.handle_callback((args.get('code') or [''])[0],
                                       (args.get('state') or [''])[0])
            if r.get('ok'):
                return self._html_page('Google connected',
                    'Signed in as <b>%s</b>. Close this tab and go back to J.A.R.V.I.S.'
                    % (r.get('user') or 'your account'), ok=True)
            return self._html_page('Could not connect', r.get('error', 'Unknown error'),
                                   ok=False)

        if route == '/api/spotify/status':
            return self._json_out(spotify.status())

        if route == '/api/spotify/login':
            # Spotify forbids `localhost` as a redirect target, so the callback
            # is always addressed to the loopback literal even though the page
            # itself is served from localhost.
            redirect = 'http://127.0.0.1:%d/callback' % PORT
            url, err = spotify.login_url(redirect, (args.get('client_id') or [''])[0])
            if err:
                return self._json_out({'ok': False, 'error': err}, 400)
            self.send_response(302)
            self.send_header('Location', url)
            self.end_headers()
            return None

        if route == '/callback':
            code = (args.get('code') or [''])[0]
            state = (args.get('state') or [''])[0]
            denied = (args.get('error') or [''])[0]

            if denied:
                return self._html_page('Spotify declined', 'Authorisation was refused: '
                                       + denied, ok=False)
            result = spotify.handle_callback(code, state)
            if result.get('ok'):
                return self._html_page(
                    'Spotify connected',
                    'Signed in as <b>%s</b>. You can close this tab and go back to '
                    'J.A.R.V.I.S.' % (result.get('user') or 'your account'), ok=True)
            return self._html_page('Could not connect', result.get('error', 'Unknown error'),
                                   ok=False)

        if route == '/api/knowledge':
            return self._json_out(knowledge.lookup(
                (args.get('source') or [''])[0],
                (args.get('q') or [''])[0]))

        if route == '/api/apps':
            names = sorted(discover_apps(force=('refresh' in args)).keys())
            return self._json_out({'ok': True, 'count': len(names), 'apps': names})

        if route == '/api/search':
            q = (args.get('q') or [''])[0].strip()
            if not q:
                return self._json_out({'ok': False, 'error': 'No query supplied.'}, 400)
            return self._json_out(do_search(
                q,
                self.headers.get('X-Search-Key', ''),
                self.headers.get('X-Google-Key', ''),
                self.headers.get('X-Google-CX', '')))

        if route == '/api/fetch':
            u = (args.get('url') or [''])[0].strip()
            if not u:
                return self._json_out({'ok': False, 'error': 'No url supplied.'}, 400)
            return self._json_out(do_fetch(u))

        # Everything below is served straight off disk, so anything private
        # sitting in this folder has to be refused explicitly. The tokens are
        # the ones that matter: without this they are one fetch away, and the
        # preview iframe would put that fetch inside generated code.
        if _is_private(route):
            self.send_error(403, 'Forbidden')
            return

        return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, fmt, *args):
        # Only report failures. api/health 404s are expected — that is the
        # dashboard probing for an optional server-side key proxy.
        try:
            status = str(args[1])
        except (IndexError, TypeError):
            return
        if status.startswith(('4', '5')) and 'api/health' not in str(args[0]):
            super().log_message(fmt, *args)


class Server(socketserver.ThreadingTCPServer):
    # Threading is not an optimisation here, it is correctness. The dashboard
    # pulls a dozen files at once plus a health probe; on a single-threaded
    # server one stalled connection blocks every request behind it and the
    # page hangs half-loaded.
    allow_reuse_address = True          # survives a quick restart
    daemon_threads = True               # Ctrl+C does not wait on open sockets


def server_capabilities():
    return {'search': True, 'fetch': True, 'llm': True,
                                   'launch': True, 'spotify': True,
                                   'knowledge': True, 'google': True, 'memory': True, 'vision': True,
                                   'recall': True, 'files': True, 'jobs': True, 'lessons': True,
                                   'apply': True, 'video': True, 'hunt': True, 'minecraft': True,
                                   'desktop': True, 'tasks': True}


def skill_probe(manifest):
    values = [bool(server_capabilities().get(name)) for name in manifest['availability']['capabilities']]
    present = any(values) if manifest['availability']['mode']=='any' else all(values)
    if not present:
        return 'unavailable', 'Required server capability is absent.'
    adapter = manifest['runtime']['adapter']
    if adapter in ('spotify','google'):
        connected = (spotify if adapter=='spotify' else google).status().get('connected', False)
        if not connected:
            return 'degraded', 'Not connected; existing setup/failure workflow retained.'
    if adapter=='video' and (not video.PIXABAY_KEY or not os.path.isfile(video.VOICE)):
        return 'degraded', 'Video credentials/voice may need configuration; existing workflow retained.'
    return 'available', 'Trusted adapter installed.'


_skill_registry = None
_skill_init_lock = threading.Lock()


def skill_registry():
    global _skill_registry
    with _skill_init_lock:
        if _skill_registry is None:
            import jarvis_skills
            base = os.path.dirname(__file__)
            _skill_registry = jarvis_skills.SkillRegistry(os.path.join(base,'skills'),os.path.join(base,'jarvis_skills_state.json'),probe=skill_probe)
        return _skill_registry


def skill_action_allowed(action):
    if action['tool']=='open':
        return True  # Required core interface.
    registry = skill_registry()
    if action['tool']=='files':
        description = describe_permission(action)
        if registry.protected_resource(description['target']):
            return False  # Model/file tools cannot mutate registry manifests or enabled state.
    return registry.route_enabled(action)


def describe_permission(action):
    """Resolve held proposals from server state, never caller-provided scope."""
    import jarvis_permissions
    args = action.get('input', {}) if isinstance(action, dict) else {}
    if isinstance(action, dict) and action.get('tool') == 'files' and args.get('action') == 'apply':
        pending = files._PENDING.get(str(args.get('id', '')))
        if not pending:
            return dict(jarvis_permissions.classify(action), capability='UNKNOWN')
        resolved = dict(action, input=dict(args, project=pending['project'], path=pending['path']))
    else:
        resolved = action
    result = jarvis_permissions.classify(resolved)
    project = resolved.get('input', {}).get('project') if isinstance(resolved, dict) else None
    if result['tool'] == 'files':
        project = project or resolved.get('input', {}).get('name')
        if project:
            scope = os.path.realpath(os.path.join(files._config['projects'], str(project)))
            path = resolved.get('input', {}).get('path', '')
            result['target'] = jarvis_permissions.target(os.path.realpath(os.path.join(scope, str(path))))
            result['project_scope'] = jarvis_permissions.target(scope)
            result['project_name'] = project
        elif resolved.get('input', {}).get('path'):
            result['target'] = jarvis_permissions.target(os.path.realpath(str(resolved['input']['path'])))
        if args.get('action') == 'apply':
            import hashlib
            result['content_hash'] = hashlib.sha256(pending['content'].encode('utf-8')).hexdigest()
    return result


_permission_store = None
_permission_init_lock = threading.Lock()


def permission_store():
    global _permission_store
    with _permission_init_lock:
        if _permission_store is None:
            import jarvis_permissions
            _permission_store = jarvis_permissions.PermissionStore(os.path.join(os.path.dirname(__file__), 'jarvis_permissions.db'), describe=describe_permission)
        return _permission_store


# A fixed private runtime path; clients never select a database.
_task_store = None
_task_init_lock = threading.Lock()


def task_store():
    global _task_store
    with _task_init_lock:
        if _task_store is None:
            import jarvis_tasks
            _task_store = jarvis_tasks.TaskStore(os.path.join(os.path.dirname(__file__), 'jarvis_tasks.db'))
        return _task_store


def main():
    try:
        httpd = Server(('', PORT), Handler)
    except OSError as err:
        print()
        print('  Could not bind port %d: %s' % (PORT, err))
        print('  Something else is using it. Try:  python serve.py 8124')
        print()
        return 1

    url = 'http://localhost:%d' % PORT
    print()
    print('  J.A.R.V.I.S. serving on %s' % url)
    print('  Ctrl+C to stop.')
    print()

    try:
        health = skill_registry().snapshot()['health']
        print('  Skills loaded: %d; available: %d; degraded: %d; invalid: %d' % (health['loaded'],health['available'],health['degraded'],health['invalid']))
    except Exception:
        print('  Skill registry unavailable; normal chat remains available.')
    scheduler = None
    try:
        import jarvis_tasks
        scheduler = jarvis_tasks.Scheduler(task_store())
        scheduler.start()
    except Exception:
        print('  Task storage unavailable; ordinary dashboard routes remain available.')
    if OPEN_BROWSER:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n  stopped.\n')
    finally:
        if scheduler:
            scheduler.stop()
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
