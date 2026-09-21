"""Video: render from stock and narration, draft the listing, stop before publish.

The whole pipeline is free and local. Pixabay supplies 1080p-4K footage, Piper
narrates offline with no key and no network, ffmpeg assembles. Measured at
roughly 18 seconds of wall clock for a 16-second 1080p video, which is fast
enough that a render is not a background job you forget about.

What this does NOT do, deliberately:

  * It never uploads. publish_packet() prepares a folder and opens YouTube
    Studio; the file is attached and the fields are filled by hand, by Zero.
    There is no YouTube API call anywhere in this module.

  * It never invents. A description is written from the beats that were
    actually rendered, so it cannot promise footage the video does not contain.

Generation of synthetic footage is a separate question and is not here. This is
the free path: real stock, real narration, real 1080p.
"""

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import wave

PROJECTS = os.path.join(os.path.expanduser('~'), 'JarvisProjects')
VIDEO_DIR = os.path.join(PROJECTS, 'videos')
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jarvis_video.json')

# Piper voice. Downloaded once; the module says so plainly if it is missing
# rather than failing inside ffmpeg three steps later.
VOICE = os.path.join(os.path.expanduser('~'), 'OpenMontage', 'voices',
                     'en_US-lessac-medium.onnx')

PIXABAY_KEY = os.environ.get('PIXABAY_API_KEY', '')

TARGET_W, TARGET_H, FPS = 1920, 1080, 30
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'


# ---------------------------------------------------------------------------
#  state
# ---------------------------------------------------------------------------

def _read():
    try:
        with open(STATE, encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    d.setdefault('videos', [])
    return d


def _write(d):
    with open(STATE, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1)


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')[:50] or 'video'


# ---------------------------------------------------------------------------
#  footage + narration
# ---------------------------------------------------------------------------

_STOP = {'the', 'and', 'with', 'from', 'into', 'onto', 'over', 'under', 'a', 'an',
         'of', 'on', 'in', 'at', 'to', 'up', 'close', 'shot', 'view', 'footage'}


def _rank(query, hits):
    """Best hit for a query, by tag overlap.

    Taking hits[0] is what produced a watermelon video with no watermelon in
    it: Pixabay ORs a multi-word query, so "watermelon on picnic table" happily
    returns a picnic. Score against the tags it hands back and require the
    subject to actually be there."""
    terms = [w for w in re.findall(r'[a-z]+', query.lower()) if w not in _STOP]
    if not terms:
        return hits

    # The subject is the RAREST term, not the first one. "children eating
    # watermelon outdoors" starts with "children", and requiring only that
    # returned a child in a ball pit - correct by the rule, useless as footage.
    # The distinctive word is the one fewest clips carry.
    freq = {}
    for t in terms:
        freq[t] = sum(1 for h in hits if t in (h.get('tags') or '').lower())
    subject_term = min(terms, key=lambda t: (freq[t], -len(t)))

    scored = []
    for h in hits:
        tags = (h.get('tags') or '').lower()
        hit_count = sum(1 for t in terms if t in tags)
        subject = subject_term in tags
        scored.append((subject, hit_count, h))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [h for subject, n, h in scored if subject] or [h for _, _, h in scored]


def _stock(query, out_path):
    url = 'https://pixabay.com/api/videos/?' + urllib.parse.urlencode(
        {'key': PIXABAY_KEY, 'q': query, 'per_page': 20, 'safesearch': 'true'})
    data = json.load(urllib.request.urlopen(url, timeout=30))
    for hit in _rank(query, data.get('hits') or []):
        vids = hit.get('videos') or {}
        for size in ('large', 'medium', 'small'):
            v = vids.get(size) or {}
            if v.get('url') and (v.get('width') or 0) >= 1280:
                # The CDN refuses urllib's default agent even though the API
                # call that produced this URL succeeded.
                req = urllib.request.Request(v['url'], headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=180) as r, open(out_path, 'wb') as fh:
                    while True:
                        buf = r.read(1 << 16)
                        if not buf:
                            break
                        fh.write(buf)
                return {'w': v.get('width'), 'h': v.get('height'),
                        'credit': hit.get('user'), 'page': hit.get('pageURL'),
                        'tags': hit.get('tags')}
    return None


def _narrate(text, out_path):
    from piper import PiperVoice
    voice = PiperVoice.load(VOICE)
    with wave.open(out_path, 'wb') as wav:
        voice.synthesize_wav(text, wav)
    with wave.open(out_path) as w:
        return w.getnframes() / float(w.getframerate())


def _ff(args):
    r = subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error'] + args,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])


# ---------------------------------------------------------------------------
#  render
# ---------------------------------------------------------------------------

# The narration field has been called several things by models trying to guess
# it. Accept them all: being strict here bought nothing except a model that
# tried "line" five times because an error message told it to.
_SAY_KEYS = ('say', 'line', 'text', 'narration', 'voiceover', 'script')


def render(title, beats, tags=None, description=None):
    """beats: [{'query': 'coffee beans', 'say': 'Every cup starts...'}, ...]"""
    if not os.path.isfile(VOICE):
        return {'ok': False, 'error':
                'No Piper voice at %s. Download en_US-lessac-medium.onnx from '
                'huggingface.co/rhasspy/piper-voices.' % VOICE}
    if not beats:
        return {'ok': False, 'error': 'No beats given - a video needs at least one.'}

    vid = 'vid:%d' % int(time.time())
    folder = os.path.join(VIDEO_DIR, _slug(title))
    os.makedirs(folder, exist_ok=True)

    started = time.time()
    segments, credits, used = [], [], []

    for i, beat in enumerate(beats[:8]):
        if not isinstance(beat, dict):
            return {'ok': False, 'error':
                    'Beat %d is not an object. Each beat is {"query": "...", "say": "..."}.'
                    % (i + 1)}

        query = (beat.get('query') or beat.get('search') or beat.get('footage') or '').strip()
        say = ''
        for k in _SAY_KEYS:
            if (beat.get(k) or '').strip():
                say = beat[k].strip()
                break

        if not query or not say:
            # Name the fields exactly. The previous wording said "a line", which
            # is not a field, and the model duly sent one.
            return {'ok': False, 'error':
                    'Beat %d is missing something. Each beat needs "query" (what stock '
                    'footage to search for) and "say" (the narration spoken over it). '
                    'Got keys: %s' % (i + 1, ', '.join(sorted(beat.keys())) or 'none')}

        raw = os.path.join(folder, 'raw%d.mp4' % i)
        info = _stock(query, raw)
        if not info:
            return {'ok': False, 'error': 'No usable footage for "%s".' % query}
        if info.get('credit'):
            credits.append(info['credit'])

        vo = os.path.join(folder, 'vo%d.wav' % i)
        secs = _narrate(say, vo)

        seg = os.path.join(folder, 'seg%d.mp4' % i)
        _ff(['-t', '%.2f' % (secs + 0.6), '-i', raw, '-i', vo,
             '-vf', ('scale=%d:%d:force_original_aspect_ratio=decrease,'
                     'pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=%d,setsar=1'
                     % (TARGET_W, TARGET_H, TARGET_W, TARGET_H, FPS)),
             '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
             '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
             '-shortest', seg])
        segments.append(seg)
        used.append({'query': query, 'say': say, 'seconds': round(secs, 1)})

    listing = os.path.join(folder, 'segments.txt')
    with open(listing, 'w', encoding='utf-8') as fh:
        for s in segments:
            fh.write("file '%s'\n" % s.replace('\\', '/'))

    final = os.path.join(folder, 'final-1080p.mp4')
    _ff(['-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', final])

    # The preview pane serves a project's index.html. Without one there is no
    # preview button at all - which is how a finished video ended up somewhere
    # Zero could not watch it. Give every render a player page.
    _write_player(folder, os.path.basename(final), title)

    # The raws and per-beat segments are scaffolding. Left behind they turned
    # one afternoon of renders into 207 MB across 38 files.
    for junk in segments + [listing]:
        try:
            os.remove(junk)
        except Exception:
            pass
    for i in range(len(beats[:8])):
        for name in ('raw%d.mp4' % i, 'vo%d.wav' % i):
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass

    dur = _duration(final)
    record = {
        'id': vid, 'title': title, 'folder': folder, 'file': final,
        'beats': used, 'credits': sorted(set(credits)),
        'seconds': dur, 'mb': round(os.path.getsize(final) / 1e6, 1),
        'render_seconds': round(time.time() - started, 1),
        'state': 'rendered', 'made': time.time(),
    }
    record.update(_listing_for(record, tags=tags, description=description))

    d = _read()
    d['videos'] = [v for v in d['videos'] if v.get('id') != vid] + [record]
    _write(d)
    return {'ok': True, 'video': record,
            'summary': ('Rendered "%s" - %.0fs at 1080p, %.1f MB, in %.0fs. '
                        'Draft listing is written. NOT uploaded.'
                        % (title, dur, record['mb'], record['render_seconds']))}


PLAYER = """<!doctype html>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
 html,body{margin:0;height:100%%;background:#04060a;color:#dde5ef;
   font:14px/1.5 system-ui,sans-serif;display:grid;place-items:center}
 .wrap{width:min(100%%,1200px);padding:18px}
 video{width:100%%;border-radius:10px;border:1px solid rgba(150,180,210,.16);background:#000}
 h1{font-size:15px;letter-spacing:.06em;margin:0 0 12px;color:#35d6ff}
 p{margin:12px 0 0;font-size:12px;color:#5c6b7e}
</style>
<div class="wrap">
  <h1>%(title)s</h1>
  <video src="%(file)s" controls autoplay muted playsinline></video>
  <p>Not uploaded. Rendered locally from Pixabay footage and Piper narration.</p>
</div>
"""


def _write_player(folder, filename, title):
    esc = (title or 'Video').replace('&', '&amp;').replace('<', '&lt;')
    with open(os.path.join(folder, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(PLAYER % {'title': esc, 'file': filename})


def _duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 1)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
#  the listing - written from what was actually rendered
# ---------------------------------------------------------------------------

def _listing_for(record, tags=None, description=None):
    """Title, description and tags built from the beats that exist.

    Deliberately mechanical. A model writing this freely would promise things
    the footage does not show; assembling it from the narration cannot."""
    lines = [b['say'] for b in record['beats']]
    # His own words if he wrote them; the narration stitched together if not.
    body = (description or '').strip() or ' '.join(lines)

    chapters, at = [], 0.0
    for b in record['beats']:
        chapters.append('%02d:%02d  %s' % (int(at // 60), int(at % 60),
                                           b['query'].title()))
        at += b['seconds'] + 0.6

    desc = [body, '', 'Chapters:'] + chapters
    if record['credits']:
        desc += ['', 'Footage: Pixabay (%s)' % ', '.join(record['credits'][:6]),
                 'Narration: Piper, generated locally.']

    # Tags he wrote, if he wrote any. The word-frequency fallback below is
    # honestly poor - "almost, entirely, which" are not tags - and exists only
    # so a render never ships with none at all.
    chosen = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    if not chosen:
        words = re.findall(r'[a-z]{4,}', body.lower())
        stop = {'this', 'that', 'with', 'from', 'they', 'them', 'then', 'than',
                'have', 'been', 'were', 'will', 'your', 'into', 'when', 'what',
                'almost', 'entirely', 'which', 'where', 'about', 'their'}
        seen = set()
        for w in words:
            if w in stop or w in seen:
                continue
            seen.add(w)
            chosen.append(w)
            if len(chosen) >= 12:
                break
    tags = chosen[:15]

    return {'yt_title': record['title'][:100],
            'yt_description': '\n'.join(desc)[:4900],
            'yt_tags': tags}


# ---------------------------------------------------------------------------
#  hand it over - and stop
# ---------------------------------------------------------------------------

def review(video_id=None):
    """The packet for the review card. Nothing is sent anywhere."""
    d = _read()
    vids = d['videos']
    if not vids:
        return {'ok': False, 'error': 'No videos rendered yet.'}
    v = next((x for x in vids if x['id'] == video_id), None) if video_id else vids[-1]
    if not v:
        return {'ok': False, 'error': 'No video with id %s.' % video_id}
    return {'ok': True, 'video': v}


def publish_packet(video_id=None):
    """Open YouTube Studio's upload page and say where the file is.

    This is where the automation stops, on purpose and by your own rule: he
    finds, drafts and opens; you press publish. There is no upload call in this
    module and adding one should be a deliberate decision, not a refactor."""
    got = review(video_id)
    if not got['ok']:
        return got
    v = got['video']
    if not os.path.isfile(v['file']):
        return {'ok': False, 'error': 'The rendered file is gone: %s' % v['file']}

    d = _read()
    for x in d['videos']:
        if x['id'] == v['id']:
            x['state'] = 'awaiting-review'
    _write(d)

    return {
        'ok': True,
        # Canonical upload entry point - it redirects to whichever account is
        # signed in. A hardcoded channel id would only ever be right by luck.
        'open_url': 'https://www.youtube.com/upload',
        'file': v['file'],
        'folder': v['folder'],
        'title': v['yt_title'],
        'description': v['yt_description'],
        'tags': v['yt_tags'],
        'summary': ('Ready for review: "%s" (%.0fs, %.1f MB). YouTube Studio is open '
                    'and the file is at %s. NOTHING HAS BEEN UPLOADED - drag the file '
                    'in, check the description, and press Publish yourself.'
                    % (v['yt_title'], v['seconds'], v['mb'], v['file'])),
    }


def list_videos(limit=20):
    d = _read()
    return {'ok': True, 'count': len(d['videos']),
            'videos': list(reversed(d['videos']))[:limit]}


def command(action, **kw):
    if action == 'render':
        return render(kw.get('title') or 'Untitled', kw.get('beats') or [],
                      tags=kw.get('tags'), description=kw.get('description'))
    if action == 'list':
        return list_videos(int(kw.get('limit') or 20))
    if action == 'review':
        return review(kw.get('id'))
    if action == 'publish_packet':
        return publish_packet(kw.get('id'))
    return {'ok': False, 'error': 'Unknown action "%s". Known: render, list, '
                                  'review, publish_packet.' % action}
