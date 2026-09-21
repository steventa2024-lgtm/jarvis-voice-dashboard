"""
File operations, media, and project scaffolding for J.A.R.V.I.S.

The safety shape of this module
-------------------------------
This is the part of the system that touches your disk, so the boundaries are
explicit rather than implied:

  * **Reading and searching** are allowed anywhere under a configured set of
    roots. Not the whole filesystem — pointing an assistant at C:\\ invites it
    to read credentials it has no business seeing.

  * **Writing** happens only inside the projects folder. Scaffolding a new app
    is the use case; editing arbitrary files on request is not, and the two are
    kept apart deliberately.

  * **Running commands** is a fixed allowlist — git, npm, python and friends,
    named individually. The model never supplies a command line. There is no
    argument that would make `rm -rf` reachable from here, which is the point.

  * **Running a project** is a second, separate allowlist: four named jobs, in
    the projects folder only, with no stdin, a timeout, and the process tree
    killed at the end of it. Nothing that serves is on it.

  * **Looking at a project** renders one of its pages in a headless browser and
    hands back an image. It reads the same files the preview does and nothing
    else — in particular it is not a view of the desktop.

Deleting is not implemented at all. If he cannot delete, he cannot delete the
wrong thing, and the cost of that restriction is one manual step in the rare
case you actually wanted it.
"""

import difflib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HOME = os.path.expanduser('~')

# Where reading and searching are permitted. Sensible defaults; the dashboard
# can extend them.
DEFAULT_ROOTS = [
    os.path.join(HOME, 'Documents'),
    os.path.join(HOME, 'Downloads'),
    os.path.join(HOME, 'Desktop'),
    os.path.join(HOME, 'Pictures'),
    os.path.join(HOME, 'Videos'),
]

PROJECTS = os.path.join(HOME, 'JarvisProjects')

SKIP_DIRS = {'node_modules', '.git', '__pycache__', 'venv', '.venv', 'dist',
             'build', '.next', 'target', 'AppData', 'Windows', 'Program Files'}

READ_LIMIT = 40_000        # characters returned from a single file

# `port` is which port serve.py answers on. This module needs it to point a
# headless browser back at the preview route — see render().
_config = {'roots': list(DEFAULT_ROOTS), 'projects': PROJECTS, 'port': 8123}


def configure(roots=None, projects=None, port=None):
    if roots is not None:
        _config['roots'] = [os.path.abspath(os.path.expanduser(r))
                            for r in roots if r and r.strip()]
    if projects:
        _config['projects'] = os.path.abspath(os.path.expanduser(projects))
    if port:
        _config['port'] = int(port)
    return {'ok': True, 'roots': _config['roots'], 'projects': _config['projects']}


def settings():
    return {'ok': True, 'roots': _config['roots'], 'projects': _config['projects']}


def _within(path, roots):
    p = os.path.abspath(path)
    for r in roots:
        try:
            if os.path.commonpath([p, os.path.abspath(r)]) == os.path.abspath(r):
                return True
        except ValueError:
            continue          # different drives; commonpath raises rather than lying
    return False


def _readable(path):
    return _within(path, _config['roots'] + [_config['projects']])


def _writable(path):
    """Writing is confined to the projects folder, full stop."""
    return _within(path, [_config['projects']])


# ------------------------------------------------------------------- finding

def find(pattern, limit=40):
    """Search filenames across the permitted roots."""
    pat = (pattern or '').strip().lower()
    if not pat:
        return {'ok': False, 'error': 'No search term.'}

    words = [w for w in re.split(r'\s+', pat) if w]
    hits = []
    for root in _config['roots'] + [_config['projects']]:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            for fn in files:
                low = fn.lower()
                if all(w in low for w in words):
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    hits.append({'path': full, 'size': st.st_size, 'mtime': st.st_mtime})
            if len(hits) >= limit * 3:
                break

    hits.sort(key=lambda h: -h['mtime'])
    hits = hits[:limit]
    if not hits:
        return {'ok': True, 'summary': 'Nothing matching "%s" under the folders I can see '
                                       '(%s).' % (pattern, ', '.join(_config['roots']))}

    lines = ['%s  (%s, %s)' % (h['path'],
                               _human(h['size']),
                               time.strftime('%Y-%m-%d', time.localtime(h['mtime'])))
             for h in hits]
    return {'ok': True, 'count': len(hits), 'summary': '\n'.join(lines)}


def _human(n):
    for unit in ('B', 'kB', 'MB', 'GB'):
        if n < 1024:
            return '%.0f %s' % (n, unit)
        n /= 1024.0
    return '%.1f TB' % n


def read(path, project=None):
    # Reading back a file he just wrote is the commonest read there is, and the
    # shape that fits it is the one write already uses: a project and a path
    # relative to it. Without this the only way in is an absolute path, which
    # he has to have been told.
    if project:
        root, target, rel, refusal = _resolve(project, path)
        if refusal:
            return refusal
        p = target
    else:
        p = os.path.abspath(os.path.expanduser(path or ''))

    if not _readable(p):
        # Listing only the roots here said the projects folder was off limits,
        # which is false — and being told it could not read its own work is
        # enough to make him abandon the task.
        return {'ok': False, 'error': 'That path is outside the folders I am allowed to '
                                      'read. Permitted: '
                                      + ', '.join(_config['roots'] + [_config['projects']])
                                      + '. For a file inside a project, pass the project '
                                        'name and a path relative to it.'}
    if not os.path.isfile(p):
        return {'ok': False, 'error': 'No such file: %s' % p}
    try:
        import jarvis_recall as recall
        text = recall.read_text(p)
    except Exception:
        text = None

    if text is None:
        return {'ok': False, 'error': 'That is a PDF and pypdf is not installed '
                                      '(pip install pypdf).'}
    if not text:
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read(READ_LIMIT)
        except Exception as err:
            return {'ok': False, 'error': 'Could not read it: %s' % err}
    if not text.strip():
        return {'ok': False, 'error': 'That file has no readable text in it.'}

    clipped = len(text) > READ_LIMIT
    return {'ok': True, 'path': p, 'truncated': clipped,
            'summary': text[:READ_LIMIT] + ('\n\n[truncated]' if clipped else '')}


# --------------------------------------------------------------------- media

def _have(exe):
    return shutil.which(exe) is not None


_FFMPEG = []


def _ffmpeg():
    """Find ffmpeg whether or not it is on this process's PATH.

    winget adds it to the user PATH, but a process started before the install
    has already captured the old one — and that is precisely the moment
    somebody tries again, having just run the command the error message told
    them to run. Telling them it is still missing when they can see it in
    Explorer is how an error message loses its credibility.
    """
    if _FFMPEG:
        return _FFMPEG[0]

    found = shutil.which('ffmpeg')
    if not found:
        import glob as _glob
        local = os.environ.get('LOCALAPPDATA', os.path.join(HOME, 'AppData', 'Local'))
        patterns = [
            os.path.join(local, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*',
                         '*', 'bin', 'ffmpeg.exe'),
            os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                         'ffmpeg', 'bin', 'ffmpeg.exe'),
            '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg',
        ]
        for pattern in patterns:
            hits = _glob.glob(pattern)
            if hits:
                found = hits[0]
                break

    if found:
        _FFMPEG.append(found)
    return found


def transcribe(path, model='base'):
    """Speech to text, locally, via Whisper."""
    p = os.path.abspath(os.path.expanduser(path or ''))
    if not _readable(p):
        return {'ok': False, 'error': 'That file is outside the folders I can read.'}
    if not os.path.isfile(p):
        return {'ok': False, 'error': 'No such file: %s' % p}

    try:
        import whisper
    except ImportError:
        return {'ok': False, 'error': 'Whisper is not installed (pip install openai-whisper).'}

    try:
        m = whisper.load_model(model)
        result = m.transcribe(p, fp16=False)
        text = (result.get('text') or '').strip()
        if not text:
            return {'ok': True, 'summary': 'No speech was found in that file.'}
        return {'ok': True, 'path': p, 'chars': len(text),
                'summary': text[:12000] + ('\n\n[truncated]' if len(text) > 12000 else '')}
    except Exception as err:
        return {'ok': False, 'error': 'Transcription failed: %s' % err}


FFMPEG_JOBS = {
    'to_mp3':    ['-vn', '-acodec', 'libmp3lame', '-q:a', '2'],
    'to_mp4':    ['-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-c:a', 'aac'],
    'to_wav':    ['-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1'],
    'compress':  ['-c:v', 'libx264', '-preset', 'slow', '-crf', '28', '-c:a', 'aac', '-b:a', '128k'],
    'audio_only': ['-vn', '-acodec', 'copy'],
    'mute':      ['-an', '-c:v', 'copy'],

    # Cutting a clip to shape for somewhere it has to go. Each one is a named
    # job for the same reason as everything else here: ffmpeg's argument
    # surface is enormous and includes writing wherever it likes.
    # Reframing needs scale-to-COVER and then crop. Scaling by width alone
    # gives a 1080x608 frame, and cropping 1920 of height out of 608 is not a
    # tall video, it is ffmpeg exiting -22. force_original_aspect_ratio makes
    # the frame at least as large as the target in both directions first.
    'vertical':  ['-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,'
                         'crop=1080:1920', '-c:v', 'libx264', '-preset', 'medium',
                  '-crf', '23', '-c:a', 'aac', '-b:a', '160k'],            # 9:16
    'square':    ['-vf', 'scale=1080:1080:force_original_aspect_ratio=increase,'
                         'crop=1080:1080', '-c:v', 'libx264', '-preset', 'medium',
                  '-crf', '23', '-c:a', 'aac', '-b:a', '160k'],            # 1:1
    # The same shapes without losing the edges of the picture: fit inside and
    # fill the rest with a blurred copy of the frame rather than black bars.
    'vertical_fit': ['-vf', 'split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,'
                            'crop=1080:1920,gblur=sigma=28[bg];'
                            '[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];'
                            '[bg][fg]overlay=(W-w)/2:(H-h)/2',
                     '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                     '-c:a', 'aac', '-b:a', '160k'],
    'web':       ['-vf', 'scale=1280:-2', '-c:v', 'libx264', '-preset', 'medium',
                  '-crf', '26', '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '128k'],
    'silent_web': ['-vf', 'scale=1280:-2', '-c:v', 'libx264', '-preset', 'medium',
                   '-crf', '28', '-movflags', '+faststart', '-an'],   # hero backgrounds
    'to_gif':    ['-vf', 'fps=12,scale=640:-1:flags=lanczos'],
    'half_speed':   ['-filter:v', 'setpts=2.0*PTS', '-filter:a', 'atempo=0.5'],
    'double_speed': ['-filter:v', 'setpts=0.5*PTS', '-filter:a', 'atempo=2.0'],
}


def media(action, path, out=None, start=None, duration=None):
    """A small, named set of ffmpeg jobs.

    Named rather than free-form for the same reason as the command allowlist:
    ffmpeg's argument surface is enormous and includes writing to arbitrary
    paths.
    """
    exe = _ffmpeg()
    if not exe:
        return {'ok': False, 'error': 'ffmpeg is not installed. Install it with: '
                                      'winget install Gyan.FFmpeg'}

    p = os.path.abspath(os.path.expanduser(path or ''))
    if not _readable(p) or not os.path.isfile(p):
        return {'ok': False, 'error': 'Cannot read %s' % p}

    stem, ext = os.path.splitext(p)
    if action == 'trim':
        args = []
        if start:
            args += ['-ss', str(start)]
        if duration:
            args += ['-t', str(duration)]
        args += ['-c', 'copy']
        target = out or (stem + '_trimmed' + ext)
    elif action == 'thumbnail':
        args = ['-ss', str(start or 1), '-vframes', '1']
        target = out or (stem + '.jpg')
    elif action in FFMPEG_JOBS:
        args = FFMPEG_JOBS[action]
        newext = {'to_mp3': '.mp3', 'to_wav': '.wav', 'to_mp4': '.mp4',
                  'to_gif': '.gif', 'web': '.mp4', 'silent_web': '.mp4',
                  'vertical': '.mp4', 'square': '.mp4', 'vertical_fit': '.mp4'}.get(action, ext)
        target = out or (stem + '_' + action + newext)
    else:
        return {'ok': False, 'error': 'Unknown media action "%s". Known: %s, trim, '
                                      'thumbnail, join'
                                      % (action, ', '.join(sorted(FFMPEG_JOBS)))}

    target = os.path.abspath(os.path.expanduser(target))
    cmd = [exe, '-y', '-i', p] + args + [target]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            tail = (r.stderr or '')[-300:]
            return {'ok': False, 'error': 'ffmpeg failed: %s' % tail}
        size = os.path.getsize(target) if os.path.exists(target) else 0
        return {'ok': True, 'output': target,
                'summary': 'Wrote %s (%s).' % (target, _human(size))}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'ffmpeg took longer than 30 minutes and was stopped.'}
    except Exception as err:
        return {'ok': False, 'error': str(err)}


# ---------------------------------------------------------------- scaffolding

# Every command he may run, named individually. Nothing here can delete, and
# nothing takes a shell string - arguments are built by this module, not by the
# model.
ALLOWED = {
    'git_init':      ['git', 'init'],
    'npm_init':      ['npm', 'init', '-y'],
    'npm_install':   ['npm', 'install'],
    'pip_install':   [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'],
    'venv':          [sys.executable, '-m', 'venv', '.venv'],
}

MEDIA_EXT = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wmv', '.flv',
             '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'}


def projects():
    """Every project, with enough to decide what to do with it.

    The dashboard has never had a way to see what exists — the only route in
    was to ask him and hope. A list nobody has to request is the difference
    between a folder and a workspace.
    """
    root = _config['projects']
    if not os.path.isdir(root):
        return {'ok': True, 'projects': [], 'root': root,
                'summary': 'No projects folder yet.'}

    rows = []
    for name in sorted(os.listdir(root)):
        here = os.path.join(root, name)
        if not os.path.isdir(here) or name.startswith('.'):
            continue

        count, size, newest, pages = 0, 0, 0, []
        for dirpath, dirs, files in os.walk(here):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            for fn in files:
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                count += 1
                size += st.st_size
                newest = max(newest, st.st_mtime)
                if fn.lower().endswith(('.html', '.htm')):
                    pages.append(os.path.relpath(full, here).replace(chr(92), '/'))

        # What can actually be done with it, so the interface can show only
        # the buttons that will work.
        runner = None
        for what in ('python', 'node'):
            for entry in RUNNERS[what]['try']:
                if os.path.isfile(os.path.join(here, *entry.split('/'))):
                    runner = (what, entry)
                    break
            if runner:
                break

        ok, out = _git(here, 'rev-list', '--count', 'HEAD')
        rows.append({
            'name': name, 'files': count, 'bytes': size, 'mtime': newest,
            'pages': sorted(pages)[:8],
            'page': ('index.html' if 'index.html' in pages else (pages[0] if pages else None)),
            'runner': runner[0] if runner else None,
            'entry': runner[1] if runner else None,
            'commits': int(out) if ok and out.strip().isdigit() else 0,
        })

    rows.sort(key=lambda r: -r['mtime'])
    return {'ok': True, 'root': root, 'projects': rows,
            'summary': ('\n'.join('%s — %d files, %s, %s'
                                  % (r['name'], r['files'], _human(r['bytes']),
                                     time.strftime('%d %b %H:%M', time.localtime(r['mtime'])))
                                  for r in rows) or 'No projects yet.')}


def media_files(limit=60):
    """Video and audio under the readable roots, newest first."""
    hits = []
    for root in _config['roots']:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in MEDIA_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                hits.append({'path': full, 'name': fn, 'bytes': st.st_size,
                             'human': _human(st.st_size), 'mtime': st.st_mtime,
                             'where': os.path.basename(dirpath)})
            if len(hits) > limit * 6:
                break

    hits.sort(key=lambda h: -h['mtime'])
    return {'ok': True, 'files': hits[:limit], 'count': len(hits),
            'ffmpeg': bool(_ffmpeg())}


def join(paths, out=None):
    """Stitch several clips into one, in the order given.

    Re-encoded rather than stream-copied. Copying is faster and works only
    when every input shares a codec, resolution and frame rate — which clips
    from a phone, a screen recorder and a download never do, and the failure
    is a file that plays for four seconds and then goes black. Slower and
    correct beats faster and haunted.
    """
    exe = _ffmpeg()
    if not exe:
        return {'ok': False, 'error': 'ffmpeg is not installed. Install it with: '
                                      'winget install Gyan.FFmpeg'}

    items = [p for p in (paths or []) if str(p).strip()]
    if len(items) < 2:
        return {'ok': False, 'error': 'Joining needs at least two files.'}

    resolved = []
    for p in items:
        full = os.path.abspath(os.path.expanduser(str(p)))
        if not _readable(full):
            return {'ok': False, 'error': 'That file is outside the folders I can '
                                          'read: %s' % full}
        if not os.path.isfile(full):
            return {'ok': False, 'error': 'No such file: %s' % full}
        resolved.append(full)

    stem, ext = os.path.splitext(resolved[0])
    target = os.path.abspath(os.path.expanduser(out or (stem + '_joined' + (ext or '.mp4'))))

    listing = os.path.join(tempfile.gettempdir(), 'jarvis_join_%d.txt' % os.getpid())
    try:
        with open(listing, 'w', encoding='utf-8', newline='\n') as fh:
            for full in resolved:
                # the concat demuxer's own escaping: single quotes are closed,
                # escaped and reopened
                fh.write("file '%s'\n" % full.replace("'", "'\\''"))

        cmd = [exe, '-y', '-f', 'concat', '-safe', '0', '-i', listing,
               '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
               '-c:a', 'aac', '-b:a', '160k', target]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            return {'ok': False, 'error': 'ffmpeg could not join them: %s'
                                          % (r.stderr or '')[-300:]}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'Joining took over an hour and was stopped.'}
    except Exception as err:
        return {'ok': False, 'error': str(err)}
    finally:
        try:
            os.remove(listing)
        except OSError:
            pass

    size = os.path.getsize(target) if os.path.exists(target) else 0
    return {'ok': True, 'output': target,
            'summary': 'Joined %d clips into %s (%s).'
                       % (len(resolved), target, _human(size))}


# ---------------------------------------------------------------- templates

#  Templates live on disk, in templates/<kind>/, rather than as string
#  literals in this file. A landing page worth generating is a few hundred
#  lines of HTML and CSS, and that is unreadable as an escaped Python string —
#  you cannot open it in a browser, cannot lint it, and cannot hand-edit it
#  without counting quotes.
#
#  templates/_base/ holds the shared design foundation. Kinds in USES_BASE get
#  it laid down first and their own files on top, so a template overrides any
#  part of it by simply shipping its own copy.
#
#  The dict below survives as the fallback for a checkout with no templates
#  directory. Deliberately minimal: enough to prove scaffolding works, not
#  enough to be worth maintaining twice.

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
USES_BASE = {'web', 'site', 'mvp'}


def kinds():
    """Every project type available, from disk and from the fallback."""
    found = set(TEMPLATES)
    if os.path.isdir(TEMPLATE_DIR):
        found |= {d for d in os.listdir(TEMPLATE_DIR)
                  if not d.startswith('_')
                  and os.path.isdir(os.path.join(TEMPLATE_DIR, d))}
    return sorted(found)


def _copy_tree(src, dest, name):
    """Copy a template folder, substituting the project name as it goes.

    Text files are rewritten so {name} lands everywhere; anything that will
    not decode as UTF-8 is copied byte for byte, which is what makes it safe
    to put an image or a font in a template."""
    written = []
    for dirpath, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, src)
        out_dir = dest if rel == '.' else os.path.join(dest, rel)
        os.makedirs(out_dir, exist_ok=True)
        for fn in sorted(files):
            source = os.path.join(dirpath, fn)
            target = os.path.join(out_dir, fn)
            try:
                with open(source, 'r', encoding='utf-8') as fh:
                    body = fh.read()
                with open(target, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(body.replace('{name}', name))
            except UnicodeDecodeError:
                shutil.copyfile(source, target)
            written.append(os.path.relpath(target, dest).replace(chr(92), '/'))
    return written


def _lay_template(kind, root, name):
    src = os.path.join(TEMPLATE_DIR, kind)
    if not os.path.isdir(src):
        out = []
        for fname, body in TEMPLATES.get(kind, {}).items():
            with open(os.path.join(root, fname), 'w', encoding='utf-8',
                      newline='\n') as fh:
                fh.write(body.replace('{name}', name))
            out.append(fname)
        return out

    written = []
    base = os.path.join(TEMPLATE_DIR, '_base')
    if kind in USES_BASE and os.path.isdir(base):
        written += _copy_tree(base, root, name)
    for f in _copy_tree(src, root, name):
        if f not in written:
            written.append(f)
    return written


TEMPLATES = {
    'web': {
        'index.html': '<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n'
                      '  <title>{name}</title>\n  <link rel="stylesheet" href="style.css">\n'
                      '</head>\n<body>\n  <h1>{name}</h1>\n  <script src="app.js"></script>\n'
                      '</body>\n</html>\n',
        'style.css': ':root { color-scheme: dark; }\nbody { font-family: system-ui; '
                     'margin: 3rem auto; max-width: 46rem; }\n',
        'app.js': "console.log('{name} ready');\n",
        'README.md': '# {name}\n\nOpen index.html.\n',
    },
    'python': {
        'main.py': 'def main():\n    print("{name}")\n\n\nif __name__ == "__main__":\n'
                   '    main()\n',
        'requirements.txt': '',
        'README.md': '# {name}\n\n```bash\npython main.py\n```\n',
        '.gitignore': '__pycache__/\n.venv/\n*.pyc\n',
    },
    'node': {
        'index.js': "console.log('{name}');\n",
        'README.md': '# {name}\n\n```bash\nnode index.js\n```\n',
        '.gitignore': 'node_modules/\n',
    },
}


def scaffold(name, kind='web', steps=None):
    """Create a project folder from a template and run allowlisted setup steps."""
    clean = re.sub(r'[^A-Za-z0-9 _-]', '', (name or '').strip())
    clean = re.sub(r'\s+', '-', clean).strip('-')
    if not clean:
        return {'ok': False, 'error': 'That is not a usable project name.'}
    if kind not in kinds():
        return {'ok': False, 'error': 'Unknown project type "%s". Known: %s'
                                      % (kind, ', '.join(kinds()))}

    root = os.path.join(_config['projects'], clean)
    if os.path.exists(root):
        return {'ok': False, 'error': 'A project called %s already exists at %s.'
                                      % (clean, root)}
    if not _writable(root):
        return {'ok': False, 'error': 'Refusing to write outside the projects folder.'}

    os.makedirs(root, exist_ok=True)
    written = _lay_template(kind, root, clean)

    ran = []
    for step in (steps or []):
        if step not in ALLOWED:
            ran.append('%s — refused, not on the allowlist' % step)
            continue

        # npm is npm.cmd on Windows and subprocess will not find it by bare
        # name, which surfaced as "npm is not installed" on machines that had
        # it. Resolving the path first is not a widening: it is the same
        # allowlisted command, still with no shell.
        argv = list(ALLOWED[step])
        if not os.path.isabs(argv[0]):
            found = _exe(argv[0])
            if not found:
                ran.append('%s — that tool is not installed' % step)
                continue
            argv[0] = found

        try:
            r = subprocess.run(argv, cwd=root, capture_output=True,
                               text=True, timeout=300, shell=False)
            ran.append('%s — %s' % (step, 'ok' if r.returncode == 0
                                    else 'failed: ' + (r.stderr or '')[-120:]))
        except FileNotFoundError:
            ran.append('%s — that tool is not installed' % step)
        except subprocess.TimeoutExpired:
            ran.append('%s — timed out' % step)
        except Exception as err:
            ran.append('%s — %s' % (step, err))

    return {'ok': True, 'path': root,
            'summary': 'Created %s at %s with %s.%s'
                       % (clean, root, ', '.join(written),
                          (' Steps: ' + '; '.join(ran)) if ran else '')}


# --------------------------------------------------------------- writing

MAX_WRITE = 400_000        # a source file, not a dataset


def _git(root, *args):
    """Run one git command in a project. Failures are reported, never raised —
    a project without git is still a working project."""
    try:
        r = subprocess.run(['git'] + list(args), cwd=root, capture_output=True,
                           text=True, timeout=60)
        return r.returncode == 0, (r.stdout or r.stderr or '').strip()
    except Exception as err:
        return False, str(err)


def snapshot(root, message):
    """Commit the current state so any change can be undone.

    Every write is committed. That is the whole safety model for letting him
    edit files: nothing is ever lost, because the previous state is one
    `git checkout` away."""
    if not os.path.isdir(os.path.join(root, '.git')):
        ok, _ = _git(root, 'init')
        if not ok:
            return False, 'git is not available'
        _git(root, 'add', '-A')
        _git(root, '-c', 'user.email=jarvis@local', '-c', 'user.name=J.A.R.V.I.S.',
             'commit', '-m', 'initial state')

    _git(root, 'add', '-A')
    ok, out = _git(root, '-c', 'user.email=jarvis@local', '-c', 'user.name=J.A.R.V.I.S.',
                   'commit', '-m', message)
    if not ok and 'nothing to commit' in out.lower():
        return True, 'no change'
    return ok, out


def _project_name(project):
    """Fold whatever was said into the folder name it must mean."""
    name = re.sub(r'[^A-Za-z0-9 _.-]', '', (project or '').strip())
    return re.sub(r'\s+', '-', name).strip('-')


def _resolve(project, path):
    """Turn a project and a relative path into a real target, or a refusal.

    Shared by write and propose so the two cannot disagree. A proposal that
    passed here and then failed at write time would be the worst kind of bug:
    approved, and then silently not applied."""
    name = _project_name(project)
    if not name:
        return None, None, None, {'ok': False, 'error': 'No project named.'}

    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return None, None, None, {'ok': False, 'error':
                                  'No project called %s. Scaffold it first.' % name}

    rel = (path or '').strip().replace(chr(92), '/').lstrip('/')

    # An absolute path is the commonest mistake here, and a bare refusal makes
    # the model try three more of them. Say what shape is wanted instead.
    if re.match(r'^[A-Za-z]:/', rel) or rel.startswith('//'):
        return None, None, None, {'ok': False, 'error':
                'Paths are RELATIVE to the project, not absolute. You gave "%s". '
                'Write "index.html" or "src/app.js" — the project folder is chosen by '
                'the project argument, and everything lands inside it. Nothing can be '
                'written outside %s.' % (path, _config['projects'])}

    if not rel or '..' in rel.split('/'):
        return None, None, None, {'ok': False, 'error':
                'Paths are relative to the project and cannot climb out of it. '
                'Use something like "index.html" or "css/site.css".'}

    target = os.path.abspath(os.path.join(root, rel))
    if not _within(target, [root]):
        return None, None, None, {'ok': False, 'error':
                                  'Refusing to write outside the project folder.'}

    return root, target, rel, None


SHRINK_FLOOR = 30          # lines below which a rewrite is not worth guarding
SHRINK_KEEP = 0.40         # a write keeping less than this of a file is presumed accidental


def _shrinks(old, new):
    """(old_lines, new_lines) when a write would delete most of a file that
    already exists, otherwise None.

    This exists because it has already happened twice. "Fix footer clipping"
    replaced a 267-line stylesheet with 18 lines; "add dark theme forcing" did
    the same to another project. Both times the entire design foundation went
    with it and nothing said a word, because a write that lands and commits
    looks exactly like a write that was correct.

    The failure always has the same shape: the file gets rewritten from memory
    instead of edited, and whatever was not remembered is simply gone. So the
    question is not "is this smaller" — plenty of good edits are — but "did
    most of it survive".

    Lines and bytes both have to collapse. Requiring both is what keeps a
    reformat that joins many lines into few from reading as a deletion."""
    old_lines = old.count(chr(10)) + 1
    if old_lines < SHRINK_FLOOR or not old.strip():
        return None

    new_lines = new.count(chr(10)) + 1
    if new_lines >= old_lines * SHRINK_KEEP or len(new) >= len(old) * SHRINK_KEEP:
        return None
    return old_lines, new_lines


def _shrink_refusal(rel, old, new):
    """The same test, worded for the model that tried it."""
    sizes = _shrinks(old, new)
    if not sizes:
        return None
    old_lines, new_lines = sizes

    gap = chr(10) * 2
    return (('Refusing to write %s: that would cut it from %d lines to %d, deleting '
             '%d%% of a file that already exists. Nothing was written and the file on '
             'disk is untouched.' + gap +
             'This is the shape of an accidental rewrite — the file was written from '
             'memory rather than edited, and everything not remembered is gone. Read '
             '%s first, then send the whole file back with your change applied to it.'
             + gap +
             'If you genuinely mean to cut it down this far, say so and it will be '
             'held for the user to approve, which is the only way past this.')
            % (rel, old_lines, new_lines,
               round(100 - (new_lines * 100.0 / old_lines)), rel))


def write(project, path, content, why=None, force=False):
    """Create or replace one file inside a project.

    `force` is not reachable from the model. It is set when a human has read
    the diff and approved it, which is the only thing that should be able to
    authorise a write the shrink guard refuses."""
    root, target, rel, refusal = _resolve(project, path)
    if refusal:
        return refusal

    body = content or ''
    if len(body) > MAX_WRITE:
        return {'ok': False, 'error': 'That file is too large to write in one go.'}

    existed = os.path.isfile(target)

    if existed and not force:
        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as fh:
                said = _shrink_refusal(rel, fh.read(), body)
        except Exception:
            said = None            # unreadable, probably binary; not this guard's business
        if said:
            return {'ok': False, 'shrink': True, 'error': said}

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(body)

    committed, detail = snapshot(root, why or (('update ' if existed else 'add ') + rel))
    # `project` matters to an approval applied after the turn moved on: by then
    # nothing else still knows which preview to refresh.
    return {'ok': True, 'path': target, 'existed': existed,
            'project': os.path.basename(root), 'rel': rel,
            'summary': '%s %s (%d lines).%s'
                       % ('Rewrote' if existed else 'Created', rel,
                          body.count(chr(10)) + 1,
                          '' if committed else ' Not snapshotted: ' + detail)}


def list_project(project):
    name = _project_name(project)
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}

    rows = []
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in sorted(fnames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(chr(92), '/')
            try:
                rows.append('%s  (%s)' % (rel, _human(os.path.getsize(full))))
            except OSError:
                continue
    if not rows:
        return {'ok': True, 'summary': '%s is empty.' % name}
    return {'ok': True, 'root': root, 'summary': '\n'.join(rows[:200])}


def history(project, limit=12):
    name = _project_name(project)
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}
    ok, out = _git(root, 'log', '--oneline', '-n', str(limit))
    if not ok:
        return {'ok': True, 'summary': 'No history yet.'}
    return {'ok': True, 'summary': out or 'No commits yet.'}


def revert(project, steps=1):
    """Undo the last change or changes."""
    name = _project_name(project)
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}

    try:
        n = max(1, min(20, int(steps)))
    except (TypeError, ValueError):
        n = 1

    ok, out = _git(root, 'reset', '--hard', 'HEAD~%d' % n)
    if not ok:
        return {'ok': False, 'error': 'Could not undo: %s' % out[:160]}
    return {'ok': True, 'summary': 'Rolled %s back %d change(s).' % (name, n)}



# ------------------------------------------------------------- proposals

#  Diff review.
#
#  A write lands immediately and is snapshotted, which makes it reversible but
#  not reviewable — by the time you read the result the old file is already
#  gone. A proposal is the same write held in memory with its diff attached,
#  so a change can be read before it exists.
#
#  Nothing here touches the disk. If the server restarts mid-review the
#  proposal evaporates, which is the correct failure: an unapproved change
#  should not survive anything.

_PENDING = {}
PENDING_TTL = 1800         # seconds; a proposal nobody answered is stale
MAX_PENDING = 24
DIFF_LINES = 400           # a diff longer than this is not being read anyway


def _prune_pending():
    cutoff = time.time() - PENDING_TTL
    for key in [k for k, v in _PENDING.items() if v['at'] < cutoff]:
        _PENDING.pop(key, None)
    if len(_PENDING) > MAX_PENDING:
        oldest = sorted(_PENDING.items(), key=lambda kv: kv[1]['at'])
        for key, _ in oldest[:len(_PENDING) - MAX_PENDING]:
            _PENDING.pop(key, None)


def propose(project, path, content, why=None):
    """Work out what a write would change, without doing it."""
    root, target, rel, refusal = _resolve(project, path)
    if refusal:
        return refusal

    body = content or ''
    if len(body) > MAX_WRITE:
        return {'ok': False, 'error': 'That file is too large to write in one go.'}

    existed = os.path.isfile(target)
    old = ''
    if existed:
        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as fh:
                old = fh.read()
        except Exception as err:
            return {'ok': False, 'error': 'Could not read the current %s: %s' % (rel, err)}

    # Approving a no-op wastes the only thing this costs, which is the user's
    # attention.
    if existed and old == body:
        return {'ok': True, 'unchanged': True, 'path': rel,
                'summary': '%s already contains exactly that. Nothing to change.' % rel}

    lines = list(difflib.unified_diff(
        old.splitlines(), body.splitlines(),
        fromfile=rel + '  (on disk)', tofile=rel + '  (proposed)',
        lineterm='', n=3))

    added = sum(1 for ln in lines[2:] if ln.startswith('+'))
    removed = sum(1 for ln in lines[2:] if ln.startswith('-'))
    if len(lines) > DIFF_LINES:
        hidden = len(lines) - DIFF_LINES
        lines = lines[:DIFF_LINES] + ['', '[diff truncated, %d more lines]' % hidden]

    _prune_pending()
    change_id = '%x%s' % (int(time.time()), os.urandom(4).hex())
    _PENDING[change_id] = {'project': project, 'path': path, 'content': body,
                           'why': why, 'at': time.time()}

    # +13/-262 is a true summary of a catastrophe and reads like an edit. Both
    # disasters were approved from a card that showed exactly those numbers,
    # so the card gets told, in words, what is about to be lost.
    sizes = _shrinks(old, body) if existed else None

    out = {'ok': True, 'id': change_id, 'path': rel, 'existed': existed,
           'added': added, 'removed': removed, 'diff': chr(10).join(lines),
           'summary': 'Proposed %s to %s: +%d/-%d, awaiting approval.'
                      % ('an edit' if existed else 'a new file', rel, added, removed)}
    if sizes:
        out['warn'] = ('This replaces %s wholesale — %d lines become %d. If that is '
                       'not what you asked for, reject it.' % (rel, sizes[0], sizes[1]))
    return out


def apply_change(change_id):
    """Carry out a proposal the user approved."""
    _prune_pending()
    change = _PENDING.pop(str(change_id or ''), None)
    if not change:
        return {'ok': False, 'error':
                'That proposed change is no longer held. It was applied already, '
                'discarded, or it expired. Propose the write again.'}
    return write(change['project'], change['path'], change['content'],
                 change['why'], force=True)


def discard(change_id):
    change = _PENDING.pop(str(change_id or ''), None)
    if not change:
        return {'ok': True, 'summary': 'Nothing was pending under that id.'}
    return {'ok': True, 'summary': 'Discarded the proposed change to %s. The file on '
                                   'disk is untouched.' % change['path']}


# --------------------------------------------------------------- looking

#  Photographing a project the way a browser draws it.
#
#  The desktop capture in serve.py cannot do this job. It takes whatever is in
#  front — the dashboard, the preview pane at whatever size it happens to be,
#  and everything else on the screen — so what reaches the vision model is
#  mostly not the page. Shooting the page alone gives a known viewport, works
#  whether or not the preview pane is open or covered, and shows nothing of
#  the desktop.
#
#  Headless Chromium does it in about a second and a half. Edge ships with
#  Windows, so this costs no new dependency, which is why it wins over driving
#  a real browser window or adding Playwright.

SHOT_WIDTH = 1280          # a vision model reads this as well as it reads 4K
SHOT_QUALITY = 74
SHOT_NARROWEST, SHOT_WIDEST = 320, 1920
MIN_WINDOW = 520        # narrower than Chrome will open a top-level window
SHOT_TALLEST = 4000

_BROWSER = []              # resolved once; empty means not looked for yet
_SHOT_LOCK = threading.Lock()


def _browser():
    """Find a Chromium that can screenshot a page without opening a window."""
    if _BROWSER:
        return _BROWSER[0]

    found = []
    for exe in ('msedge', 'chrome', 'chromium', 'brave'):
        hit = shutil.which(exe)
        if hit:
            found.append(hit)

    pf = os.environ.get('ProgramFiles', 'C:' + chr(92) + 'Program Files')
    pf86 = os.environ.get('ProgramFiles(x86)', pf + ' (x86)')
    local = os.environ.get('LOCALAPPDATA', os.path.join(HOME, 'AppData', 'Local'))
    for base in (pf86, pf, local):
        for rest in ('Microsoft/Edge/Application/msedge.exe',
                     'Google/Chrome/Application/chrome.exe',
                     'BraveSoftware/Brave-Browser/Application/brave.exe',
                     'Chromium/Application/chrome.exe'):
            candidate = os.path.join(base, *rest.split('/'))
            if os.path.isfile(candidate):
                found.append(candidate)

    # Not Windows. Here for the day this runs somewhere else.
    for candidate in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                      '/usr/bin/google-chrome', '/usr/bin/chromium-browser'):
        if os.path.isfile(candidate):
            found.append(candidate)

    if not found:
        return None
    _BROWSER.append(found[0])
    return found[0]


def render(project, path=None, width=None, height=None):
    """Screenshot a project page. Returns a data URL.

    The caller sends the image to a vision model; this module does not talk to
    models, and does not need to know which one."""
    name = _project_name(project)
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}

    rel = (path or 'index.html').strip().replace(chr(92), '/').lstrip('/')
    if '..' in rel.split('/'):
        return {'ok': False, 'error': 'That path climbs out of the project.'}

    target = os.path.abspath(os.path.join(root, rel))
    if os.path.isdir(target):
        rel = rel.rstrip('/') + '/index.html'
        target = os.path.join(target, 'index.html')
    if not _within(target, [root]) or not os.path.isfile(target):
        pages = [os.path.relpath(os.path.join(d, f), root).replace(chr(92), '/')
                 for d, dirs, fs in os.walk(root)
                 for f in fs if f.lower().endswith(('.html', '.htm'))][:12]
        return {'ok': False, 'error':
                'There is no %s in %s. %s'
                % (rel, name, ('Pages here: ' + ', '.join(pages)) if pages
                   else 'It has no HTML page to look at yet.')}

    exe = _browser()
    if not exe:
        return {'ok': False, 'error':
                'No Chromium browser was found to render the page with. Edge, Chrome '
                'or Brave will all do it; installing any one of them makes this work.'}

    try:
        w = max(SHOT_NARROWEST, min(SHOT_WIDEST, int(width or SHOT_WIDTH)))
    except (TypeError, ValueError):
        w = SHOT_WIDTH
    try:
        h = max(240, min(SHOT_TALLEST, int(height or 900)))
    except (TypeError, ValueError):
        h = 900

    url = 'http://127.0.0.1:%d/preview/%s/%s' % (
        _config['port'], urllib.parse.quote(name), urllib.parse.quote(rel))

    #  Narrow widths have to go through a frame.
    #
    #  Chrome will not open a top-level window below about 500px on Windows.
    #  Ask for --window-size=390 and you get a 390px *image* of a page that
    #  laid itself out at 492 — so a phone preview showed a cropped desktop
    #  layout, and every overflow it appeared to reveal was an artefact of the
    #  crop. Worse than useless: it would have had him fixing bugs that were
    #  not there.
    #
    #  The minimum applies to windows, not to frames. So the page goes in an
    #  iframe of exactly the requested width inside a legal-sized window, and
    #  the image is cropped back down afterwards. The iframe gets a true
    #  narrow layout viewport, which is the thing that was actually wanted.
    framed = w < MIN_WINDOW
    shoot_w, shoot_h = (max(w, MIN_WINDOW), h) if framed else (w, h)
    if framed:
        url = ('http://127.0.0.1:%d/frame?src=%s&w=%d&h=%d'
               % (_config['port'],
                  urllib.parse.quote('/preview/%s/%s'
                                     % (urllib.parse.quote(name),
                                        urllib.parse.quote(rel)), safe=''),
                  w, h))

    # A profile of its own. Run headless against the default one and Chromium
    # hands off to the browser the user already has open, then exits without
    # ever taking the picture.
    profile = os.path.join(tempfile.gettempdir(), 'jarvis-shot-profile')
    out = os.path.join(tempfile.gettempdir(), 'jarvis-shot-%d.png' % os.getpid())

    cmd = [exe, '--headless=new', '--disable-gpu', '--no-first-run',
           '--no-default-browser-check', '--hide-scrollbars',
           '--disable-extensions', '--mute-audio',
           '--user-data-dir=' + profile,
           '--window-size=%d,%d' % (shoot_w, shoot_h),
           # Let scripts, fonts and entrance animations settle before the
           # shutter. A framed render needs considerably longer: the wrapper
           # has to load before the iframe inside it even starts, and a shot
           # taken too early catches every fade-in at opacity zero — which
           # reads as "half the page is missing" rather than "not ready yet".
           '--virtual-time-budget=%d' % (9000 if framed else 3500),
           '--screenshot=' + out, url]

    with _SHOT_LOCK:                              # one profile, one shot at a time
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': 'The browser took too long to render that page.'}
        except Exception as err:
            return {'ok': False, 'error': 'Could not run the browser: %s' % err}

        if not os.path.isfile(out):
            tail = (r.stderr or r.stdout or '')[-200:]
            return {'ok': False, 'error': 'The browser produced no image. %s' % tail}
        try:
            with open(out, 'rb') as fh:
                blob = fh.read()
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    mime = 'image/png'
    try:
        import io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(blob))
        if framed:
            # the frame sits at the top-left; everything right of it is padding
            img = img.crop((0, 0, min(w, img.size[0]), min(h, img.size[1])))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        buf = _io.BytesIO()
        img.save(buf, format='JPEG', quality=SHOT_QUALITY)
        blob, mime = buf.getvalue(), 'image/jpeg'
        w, h = img.size
    except Exception:
        pass          # PNG is larger but perfectly readable; not worth failing over

    import base64 as _b64
    return {'ok': True, 'project': name, 'path': rel, 'url': url,
            'width': w, 'height': h, 'bytes': len(blob),
            'data_url': 'data:%s;base64,%s' % (mime, _b64.b64encode(blob).decode()),
            'summary': 'Rendered %s/%s at %dx%d.' % (name, rel, w, h)}


# -------------------------------------------------------------- checking

#  The deterministic half of the build check.
#
#  Rendering a page and asking a vision model how it looks catches design
#  faults and almost nothing else. It cannot see a class that was never
#  defined — a page of browser defaults reads back as "plain" rather than as
#  "the stylesheet is not being used" — and it cannot see a broken image at
#  all, because a description of a screenshot describes what is there, not the
#  hole where a photograph should have been.
#
#  Measured on bean_and_brew: eleven classes used, two defined, four dead
#  images and a 404 on every page load. The vision pass reported none of it.
#
#  So there is no model in this one. It reads the markup, reads the CSS that
#  the markup actually links, and reports what does not line up. Same input,
#  same answer, every time.

# Placeholder services every model trained before they died still reaches for.
# via.placeholder.com no longer opens a connection at all.
DEAD_IMAGE_HOSTS = {
    'via.placeholder.com', 'placeholder.com', 'placehold.it',
    'placeimg.com', 'lorempixel.com',
}

DEFAULT_ACCENT = '#4f7cff'      # unchanged from templates/_base/style.css
NET_CHECKS = 10                 # remote URLs verified per pass
NET_TIMEOUT = 5
NAMED_LIMIT = 14                # undefined classes listed before summarising

_CLASS_IN_SELECTOR = re.compile(r'\.((?:\\.|[A-Za-z0-9_-])+)')


def _css_selector_classes(text):
    """Class names in *selector* position within a stylesheet.

    Comments and string literals go first, so a brace inside either cannot be
    mistaken for a block. Then a depth walk: whatever accumulates before a `{`
    is a selector, whatever follows a `;` inside a block is a declaration and
    is thrown away. That distinction is the whole point — `content: ".card"`
    defines no class, and counting it would make this check lie in the one
    direction that hides the bug it exists to find."""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', ' ', text)
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", ' ', text)

    found, buf, depth = set(), [], 0
    for ch in text:
        if ch == '{':
            sel = ''.join(buf).strip()
            if sel and not sel.startswith('@'):
                found.update(_CLASS_IN_SELECTOR.findall(sel))
            buf, depth = [], depth + 1
        elif ch == '}':
            buf, depth = [], max(0, depth - 1)
        elif ch == ';' and depth:
            buf = []
        else:
            buf.append(ch)

    # Tailwind escapes its own names in the compiled file: .md\:flex, .w-1\/2.
    return {c.replace('\\', '') for c in found}


class _Markup(HTMLParser):
    """What one page claims about itself: the classes it wears, and the files
    it asks the browser to go and fetch."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.classes = set()
        self.inline_css = []
        self.sheets = []        # hrefs of linked stylesheets, in order
        self.refs = []          # (kind, url) — everything worth resolving
        self.images = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        self.classes.update((attr.get('class') or '').split())

        if tag == 'style':
            self._in_style = True
        elif tag == 'link':
            rel, href = (attr.get('rel') or '').lower(), attr.get('href')
            if href and 'stylesheet' in rel:
                self.sheets.append(href)
                self.refs.append(('stylesheet', href))
            elif href and 'icon' in rel:
                self.refs.append(('icon', href))
        elif tag == 'script' and attr.get('src'):
            self.refs.append(('script', attr['src']))
        elif tag in ('img', 'source'):
            src = attr.get('src') or ''
            if not src and attr.get('srcset'):
                src = attr['srcset'].split(',')[0].strip().split(' ')[0]
            if src:
                self.images.append(src)
                self.refs.append(('image', src))
        elif tag == 'a':
            href = attr.get('href') or ''
            if href and not href.startswith('#'):
                self.refs.append(('link', href))

    def handle_endtag(self, tag):
        if tag == 'style':
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.inline_css.append(data)


def _suggest(used, pool):
    """The class he probably meant.

    difflib on its own is not enough: `btn-primary` against `btn` scores 0.43
    and `nav-links` against `site-nav` scores 0.47, so any sane cutoff rejects
    exactly the two suggestions worth making. Hyphen-separated tokens are what
    carry the intent, so those are compared first and difflib is the
    tie-break."""
    want = set(used.lower().split('-'))
    best, best_key = None, (0, 0.0)
    for cand in pool:
        shared = len(want & set(cand.lower().split('-')))
        ratio = difflib.SequenceMatcher(None, used.lower(), cand.lower()).ratio()
        if not shared and ratio < 0.72:
            continue
        if (shared, ratio) > best_key:
            best, best_key = cand, (shared, ratio)
    return best


def _reachable(url):
    """Whether a remote file actually serves. Returns (ok, note)."""
    agent = {'User-Agent': 'Mozilla/5.0 (J.A.R.V.I.S. build check)'}
    try:
        req = urllib.request.Request(url, method='HEAD', headers=agent)
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            return True, 'HTTP %d' % resp.status
    except urllib.error.HTTPError as err:
        # Plenty of CDNs refuse HEAD and serve GET perfectly well. Asking for a
        # single byte settles it without pulling the whole image down.
        if err.code in (403, 405, 501):
            try:
                head = dict(agent, **{'Range': 'bytes=0-0'})
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers=head),
                        timeout=NET_TIMEOUT) as resp:
                    return True, 'HTTP %d' % resp.status
            except Exception:
                pass
        return False, 'HTTP %d' % err.code
    except Exception as err:
        return False, err.__class__.__name__


def _is_remote(url):
    return url.startswith('//') or bool(re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', url))


def _project_tree(root):
    """Every file in the project, project-relative, forward slashes."""
    files = set()
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            rel = os.path.relpath(os.path.join(base, name), root)
            files.add(rel.replace(os.sep, '/'))
    return files


def check(project):
    """Read a built project and report what is provably wrong with it.

    Four faults, every one of them invisible in a screenshot: classes no
    stylesheet on the page defines, local files referenced and absent, images
    that cannot load, and an accent left at the template default."""
    name = _project_name(project)
    if not name:
        return {'ok': False, 'error': 'No project named.'}
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}

    on_disk = _project_tree(root)
    pages = sorted(p for p in on_disk if p.lower().endswith(('.html', '.htm')))
    if not pages:
        return {'ok': True, 'problems': 0, 'pages': 0,
                'summary': 'No HTML in %s, so there is nothing here to check.' % name}

    sheets = {}          # css path -> classes it defines, read once per project
    remote = {}          # url -> (ok, note), so a repeated image costs one call
    budget = NET_CHECKS
    reports, problems = [], 0
    undefined_seen = False

    for page in pages:
        try:
            with open(os.path.join(root, page), 'r', encoding='utf-8',
                      errors='replace') as fh:
                markup = fh.read()
        except Exception as err:
            reports.append('%s\n  · could not be read: %s' % (page, err))
            problems += 1
            continue

        scan = _Markup()
        try:
            scan.feed(markup)
        except Exception:
            pass                 # malformed markup still yields whatever parsed

        here = posixpath.dirname(page)

        def resolve(url, _here=here):
            """A referenced URL as a project-relative path, or None when it is
            not ours to check."""
            if _is_remote(url):
                return None
            clean = url.split('#')[0].split('?')[0]
            if not clean:
                return None
            joined = posixpath.normpath(posixpath.join(_here, clean))
            return joined.lstrip('/') if joined != '.' else None

        # ---- what styles this page, and therefore what it may wear ----
        defined, suggestable = set(), set()
        for href in scan.sheets:
            rel = resolve(href)
            if rel is None or rel not in on_disk:
                continue
            if rel not in sheets:
                try:
                    with open(os.path.join(root, rel), 'r', encoding='utf-8',
                              errors='replace') as fh:
                        sheets[rel] = _css_selector_classes(fh.read())
                except Exception:
                    sheets[rel] = set()
            defined |= sheets[rel]
            # Tailwind is two thousand generated utilities. Offering one of
            # those as "did you mean" is noise, and the foundation is what he
            # is meant to reach for anyway.
            if 'tailwind' not in rel.lower():
                suggestable |= sheets[rel]

        for block in scan.inline_css:
            own = _css_selector_classes(block)
            defined |= own
            suggestable |= own

        notes = []

        # ---- classes nothing defines ----
        missing = sorted(c for c in scan.classes if c not in defined)
        if missing:
            problems += len(missing)
            undefined_seen = True
            named = []
            for cls in missing[:NAMED_LIMIT]:
                hint = _suggest(cls, suggestable)
                named.append('.%s%s' % (cls, (' → .%s?' % hint) if hint else ''))
            more = '' if len(missing) <= NAMED_LIMIT else \
                   '   (+%d more)' % (len(missing) - NAMED_LIMIT)
            notes.append('· %d class%s used that no stylesheet on this page defines:'
                         '\n      %s%s'
                         % (len(missing), '' if len(missing) == 1 else 'es',
                            '   '.join(named), more))

        # ---- files it asks for and does not have ----
        absent = []
        for kind, url in scan.refs:
            rel = resolve(url)
            if rel is None or rel in on_disk:
                continue
            if kind == 'link' and not rel.lower().endswith(('.html', '.htm')):
                continue         # a directory, a download, something not ours
            if (kind, url) not in absent:
                absent.append((kind, url))
        if absent:
            problems += len(absent)
            notes.append('· referenced and not in the project: '
                         + ', '.join('%s (%s)' % (url, kind) for kind, url in absent[:8]))

        # ---- images that will not draw ----
        dead, unverified = [], []
        for src in dict.fromkeys(scan.images):
            if not _is_remote(src):
                continue
            full = 'https:' + src if src.startswith('//') else src
            if not full.lower().startswith(('http://', 'https://')):
                continue                        # data: and the like are fine
            host = (urllib.parse.urlparse(full).hostname or '').lower()
            if host.startswith('www.'):
                host = host[4:]
            if host in DEAD_IMAGE_HOSTS:
                dead.append((src, 'that service is gone'))
                continue
            if full not in remote:
                if budget <= 0:
                    unverified.append(src)
                    continue
                budget -= 1
                remote[full] = _reachable(full)
            ok, note = remote[full]
            if not ok:
                dead.append((src, note))
        if dead:
            # Counted by <img> rather than by URL: four cards all pointing at
            # the same dead placeholder is four holes on the page, and saying
            # "1 image" invites him to fix one of them.
            broken = sum(scan.images.count(url) for url, _ in dead)
            problems += broken
            notes.append('· %d image%s will not load: %s'
                         % (broken, '' if broken == 1 else 's',
                            ', '.join(
                                '%s%s — %s'
                                % (url, '' if scan.images.count(url) == 1
                                        else ' (x%d)' % scan.images.count(url), why)
                                for url, why in dead[:6])))
        if unverified:
            notes.append('· %d further remote image%s not checked, budget spent'
                         % (len(unverified), '' if len(unverified) == 1 else 's'))

        if notes:
            reports.append(page + '\n  ' + '\n  '.join(notes))

    # A total network failure looks exactly like every image being dead, and
    # saying so would be a lie on a machine that is merely offline.
    if remote and not any(ok for ok, _ in remote.values()):
        reports.append('No remote host answered at all. This machine may be offline, '
                       'so treat the image failures above as unconfirmed.')

    # ---- the one design decision that is always his ----
    accent_note = ''
    for css in sorted(p for p in on_disk
                      if p.lower().endswith('.css') and 'tailwind' not in p.lower()):
        try:
            with open(os.path.join(root, css), 'r', encoding='utf-8',
                      errors='replace') as fh:
                found = re.search(r'--accent\s*:\s*([^;}]+)', fh.read())
        except Exception:
            continue
        if found and found.group(1).strip().lower() == DEFAULT_ACCENT:
            accent_note = ('--accent in %s is still the template default %s. One line '
                           'retints the entire page — pick something the subject '
                           'warrants.' % (css, DEFAULT_ACCENT))
        break

    if not reports and not accent_note:
        return {'ok': True, 'problems': 0, 'pages': len(pages),
                'summary': 'Checked %d page%s in %s: every class is defined, every '
                           'referenced file exists, and every image loads.'
                           % (len(pages), '' if len(pages) == 1 else 's', name)}

    if problems:
        body = ['%s — %d problem%s across %d page%s.'
                % (name, problems, '' if problems == 1 else 's',
                   len(pages), '' if len(pages) == 1 else 's')]
    else:
        body = ['%s — %d page%s, nothing broken. One thing to settle:'
                % (name, len(pages), '' if len(pages) == 1 else 's')]
    body += reports
    if accent_note:
        body.append('· ' + accent_note)
    # Only worth saying when there is a miss to explain; on a clean page it is
    # a lecture about a problem he does not have.
    if undefined_seen:
        body.append('An undefined class falls through to browser defaults, which is '
                    'what makes a page look unstyled. Either use the foundation class '
                    'named beside it, or add a real rule for yours to style.css built '
                    'from the tokens rather than from hardcoded pixels.')

    return {'ok': True, 'problems': problems, 'pages': len(pages),
            'summary': '\n\n'.join(body)}


# --------------------------------------------------------------- running

#  Running a project.
#
#  Same principle as ALLOWED: named jobs, never a command line. A run may take
#  one argument — a file inside the project — and that is not a widening of
#  what is reachable, because the only files in there are ones this module
#  wrote.
#
#  Three things make an unattended run safe to sit through. No stdin, so a
#  script waiting for input fails at once instead of hanging until the
#  timeout. A timeout. And killing the whole process tree rather than the
#  child alone, because npm test is npm, which is node, which is the runner —
#  kill the first and two are still going.
#
#  Deliberately absent: anything that serves. `npm run dev` never exits, so it
#  can only ever be killed at the timeout, and a task card that always ends
#  the same way teaches nothing.

RUN_TIMEOUT = 90
RUN_TIMEOUT_MAX = 180
RUN_OUTPUT = 6000

RUNNERS = {
    'python':   {'exts': ('.py',),
                 'try': ['main.py', 'app.py', 'run.py', 'src/main.py']},
    'node':     {'exts': ('.js', '.mjs'),
                 'try': ['index.js', 'main.js', 'server.js', 'src/index.js']},
    'npm_test': {'exts': (), 'try': []},
    'pytest':   {'exts': (), 'try': []},
}


def _exe(name):
    """Resolve a launcher to a real path.

    On Windows npm is npm.cmd, and handing subprocess the bare name raises
    FileNotFoundError — which reads as "npm is not installed" when it is."""
    return shutil.which(name)


def _project_python(root):
    """A project's own virtualenv if it made one, otherwise this interpreter."""
    for rel in ('.venv/Scripts/python.exe', '.venv/bin/python'):
        candidate = os.path.join(root, *rel.split('/'))
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def run(project, what, entry=None, timeout=None):
    name = _project_name(project)
    root = os.path.join(_config['projects'], name)
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No project called %s.' % name}

    job = RUNNERS.get(what)
    if not job:
        return {'ok': False, 'error':
                'I can only run: %s. Nothing else is reachable from here, and there '
                'is no way to hand me a command line.' % ', '.join(sorted(RUNNERS))}

    try:
        secs = max(5, min(RUN_TIMEOUT_MAX, int(timeout or RUN_TIMEOUT)))
    except (TypeError, ValueError):
        secs = RUN_TIMEOUT

    target_rel = None
    if job['exts']:
        wanted = (entry or '').strip().replace(chr(92), '/').lstrip('/')
        if wanted:
            if '..' in wanted.split('/') or not wanted.endswith(job['exts']):
                return {'ok': False, 'error':
                        'The file to run must sit inside the project and end in %s. '
                        'You gave "%s".' % (' or '.join(job['exts']), entry)}
            full = os.path.abspath(os.path.join(root, wanted))
            if not _within(full, [root]) or not os.path.isfile(full):
                return {'ok': False, 'error': 'There is no %s in %s.' % (wanted, name)}
            target_rel = wanted
        else:
            for guess in job['try']:
                if os.path.isfile(os.path.join(root, *guess.split('/'))):
                    target_rel = guess
                    break
            if not target_rel:
                return {'ok': False, 'error':
                        'I could not tell what to run: %s has none of %s. Name the file '
                        'in the entry argument.' % (name, ', '.join(job['try']))}

    if what == 'python':
        argv = [_project_python(root), '-u', target_rel]
    elif what == 'node':
        node = _exe('node')
        if not node:
            return {'ok': False, 'error': 'Node is not installed on this machine.'}
        argv = [node, target_rel]
    elif what == 'npm_test':
        npm = _exe('npm')
        if not npm:
            return {'ok': False, 'error': 'npm is not installed on this machine.'}
        if not os.path.isfile(os.path.join(root, 'package.json')):
            return {'ok': False, 'error':
                    '%s has no package.json, so there is no test script to run.' % name}
        argv = [npm, 'test']
    else:
        argv = [_project_python(root), '-m', 'pytest', '-q']

    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'          # or a run killed at the timeout reports nothing
    env['PYTHONIOENCODING'] = 'utf-8'
    env['NO_COLOR'] = '1'                  # escape codes are noise to a reader and a model
    env['FORCE_COLOR'] = '0'

    started = time.time()
    try:
        proc = subprocess.Popen(
            argv, cwd=root, env=env,
            stdin=subprocess.DEVNULL,      # a script asking for input fails, never hangs
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    except FileNotFoundError:
        return {'ok': False, 'error': 'Could not start %s — it is not installed.' % what}
    except Exception as err:
        return {'ok': False, 'error': 'Could not start it: %s' % err}

    killed = False
    try:
        out, _ = proc.communicate(timeout=secs)
    except subprocess.TimeoutExpired:
        killed = True
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            out = ''

    took = time.time() - started
    body = (out or '').strip()
    if len(body) > RUN_OUTPUT:
        body = '[earlier output dropped]\n' + body[-RUN_OUTPUT:]

    label = target_rel or what.replace('_', ' ')
    if killed:
        head = ('%s was still running after %d seconds, so I stopped it. What it printed '
                'before that is below. A program that does not exit is not the same as '
                'one that failed.' % (label, secs))
    elif proc.returncode == 0:
        head = '%s finished cleanly in %.1fs, exit code 0.' % (label, took)
    else:
        head = ('%s exited %d after %.1fs — it did NOT succeed. The output below is why.'
                % (label, proc.returncode, took))

    return {'ok': True, 'exit': proc.returncode, 'killed': killed,
            'output': body, 'ran': label,
            'summary': head + (('\n\n' + body) if body else '\n\nIt printed nothing.')}


def _kill_tree(proc):
    """Kill the child and everything it spawned."""
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=15)
            return
        except Exception:
            pass          # fall through to the blunt instrument
    try:
        proc.kill()
    except Exception:
        pass


# -------------------------------------------------------------- dispatch

def command(action, **kw):
    if action == 'find':
        return find(kw.get('query'))
    if action == 'read':
        return read(kw.get('path'), kw.get('project'))
    if action == 'transcribe':
        return transcribe(kw.get('path'))
    if action == 'media':
        return media(kw.get('media_action') or 'to_mp3', kw.get('path'),
                     kw.get('out'), kw.get('start'), kw.get('duration'))
    if action == 'join':
        return join(kw.get('paths'), kw.get('out'))
    if action == 'projects':
        return projects()
    if action == 'media_files':
        return media_files()
    if action == 'write':
        return write(kw.get('project'), kw.get('path'), kw.get('content'), kw.get('why'))
    if action == 'propose':
        return propose(kw.get('project'), kw.get('path'), kw.get('content'), kw.get('why'))
    if action == 'apply':
        return apply_change(kw.get('id'))
    if action == 'discard':
        return discard(kw.get('id'))
    if action == 'check':
        return check(kw.get('project'))
    if action == 'render':
        return render(kw.get('project'), kw.get('path'), kw.get('width'), kw.get('height'))
    if action == 'run':
        return run(kw.get('project'), kw.get('what'), kw.get('entry'), kw.get('timeout'))
    if action == 'list_project':
        return list_project(kw.get('project'))
    if action == 'history':
        return history(kw.get('project'))
    if action == 'revert':
        return revert(kw.get('project'), kw.get('steps') or 1)
    if action == 'scaffold':
        return scaffold(kw.get('name'), kw.get('kind') or 'web', kw.get('steps'))
    if action == 'capabilities':
        return {'ok': True, 'summary': json.dumps({
            'roots': _config['roots'],
            'projects': _config['projects'],
            'ffmpeg': bool(_ffmpeg()),
            'media_jobs': sorted(FFMPEG_JOBS) + ['trim', 'thumbnail', 'join'],
            'templates': kinds(),
            'allowed_steps': sorted(ALLOWED),
            'runners': sorted(RUNNERS),
            'can_render': bool(_browser()),
        }, indent=1)}
    return {'ok': False, 'error': 'Unknown action "%s".' % action}
