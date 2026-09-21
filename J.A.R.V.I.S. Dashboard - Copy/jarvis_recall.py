"""
Document memory and conversation recall for J.A.R.V.I.S.

What this is for
----------------
Web search answers what the world knows. This answers what *you* know: the
folder of contracts, notes, manuals and code on your own drive, plus every
conversation you have already had with him.

How it works, briefly: files are split into overlapping chunks, each chunk is
turned into a vector by a local embedding model, and a question is answered by
finding the chunks whose vectors point in the same direction. No text leaves
the machine — embedding runs in Ollama alongside everything else.

Storage is SQLite rather than JSON because embeddings are large. A 768-float
vector is 3 kB as JSON and 3 kB as raw float32 bytes, but ten thousand of them
is a 60 MB text file that has to be parsed in full on every query, against a
30 MB database that can be read in slices. The rest of this project stores JSON
because the data is tiny; this is the one place where it would not have held.
"""

import json
import os
import re
import sqlite3
import struct
import threading
import time
import urllib.request
import zipfile

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jarvis_recall.db')
EMBED_MODEL = 'nomic-embed-text'
OLLAMA = 'http://localhost:11434'

CHUNK = 1400          # characters per chunk; roughly a long paragraph
OVERLAP = 200         # so a sentence spanning a boundary is still findable
MAX_FILE = 2_000_000  # skip anything larger; it is a binary or a log

TEXT_EXT = {
    '.txt', '.md', '.markdown', '.rst', '.log', '.csv', '.json', '.yaml', '.yml',
    '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.java', '.c', '.h',
    '.cpp', '.cs', '.go', '.rs', '.rb', '.php', '.sh', '.bat', '.ps1', '.sql',
    '.ini', '.cfg', '.toml', '.env', '.xml',
}

_lock = threading.Lock()
_indexing = {'running': False, 'done': 0, 'total': 0, 'file': '', 'error': ''}


# ------------------------------------------------------------------ database

_schema_done = False


def _db():
    """One connection per caller.

    WAL is set once rather than per-connection: switching journal mode takes an
    exclusive lock, so doing it on every connect made a status query collide
    with the indexing thread and fail outright. busy_timeout covers the rest —
    the writer holds the file only in short bursts.
    """
    global _schema_done
    con = sqlite3.connect(DB, timeout=30)
    con.execute('PRAGMA busy_timeout=15000')

    if _schema_done:
        return con

    try:
        con.execute('PRAGMA journal_mode=WAL')
    except sqlite3.OperationalError:
        pass                      # already WAL, or another connection is mid-write

    con.execute("""CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY,
        kind TEXT,            -- 'doc' or 'chat'
        source TEXT,          -- file path, or a conversation marker
        ord INTEGER,          -- position within the source
        text TEXT,
        vec BLOB,
        mtime REAL,
        at REAL
    )""")
    con.execute('CREATE INDEX IF NOT EXISTS idx_source ON chunks(source)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_kind ON chunks(kind)')
    con.commit()
    _schema_done = True
    return con


def _pack(vec):
    return struct.pack('%df' % len(vec), *vec)


def _unpack(blob):
    return struct.unpack('%df' % (len(blob) // 4), blob)


# ----------------------------------------------------------------- embedding

BATCH = 16


def embed_many(texts):
    """Embed a list in one request.

    Measured on this machine: the singular /api/embeddings endpoint costs about
    2s per chunk regardless of size, which is per-call overhead rather than
    compute. The batch endpoint amortises that across the whole list and comes
    in around 2.4x faster. Worth the extra code path for an index that may run
    over thousands of chunks.
    """
    if not texts:
        return []
    body = json.dumps({'model': EMBED_MODEL, 'input': texts}).encode()
    req = urllib.request.Request(OLLAMA + '/api/embed', data=body)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            got = json.loads(r.read().decode('utf-8')).get('embeddings') or []
        if len(got) == len(texts):
            return got
    except Exception:
        pass                      # older Ollama without /api/embed
    return [embed(t) for t in texts]


def embed(text):
    body = json.dumps({'model': EMBED_MODEL, 'prompt': text}).encode()
    req = urllib.request.Request(OLLAMA + '/api/embeddings', data=body)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode('utf-8')).get('embedding') or []


def embed_ready():
    try:
        v = embed('ping')
        return (True, len(v)) if v else (False, 0)
    except Exception:
        return (False, 0)


# ------------------------------------------------------------------ reading

def _read_docx(path):
    """A .docx is a zip of XML. Pulling the paragraph text out needs no library,
    which keeps this dependency-free for the commonest office format."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read('word/document.xml').decode('utf-8', 'replace')
        xml = re.sub(r'</w:p>', '\n', xml)
        return re.sub(r'<[^>]+>', '', xml)
    except Exception:
        return ''


def _read_pdf(path):
    try:
        import pypdf
    except ImportError:
        return None                      # signals "cannot read this kind"
    try:
        out = []
        reader = pypdf.PdfReader(path)
        for page in reader.pages[:200]:
            out.append(page.extract_text() or '')
        return '\n'.join(out)
    except Exception:
        return ''


def read_text(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if os.path.getsize(path) > MAX_FILE:
            return ''
    except OSError:
        return ''

    if ext == '.pdf':
        return _read_pdf(path)
    if ext == '.docx':
        return _read_docx(path)
    if ext in TEXT_EXT:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                return fh.read()
        except Exception:
            return ''
    return ''


def chunk_text(text):
    text = re.sub(r'\n{3,}', '\n\n', text or '').strip()
    if not text:
        return []
    out = []
    i = 0
    while i < len(text):
        piece = text[i:i + CHUNK]
        # prefer to break at a paragraph or sentence rather than mid-word
        if i + CHUNK < len(text):
            for sep in ('\n\n', '\n', '. '):
                cut = piece.rfind(sep)
                if cut > CHUNK * 0.5:
                    piece = piece[:cut + len(sep)]
                    break
        piece = piece.strip()
        if len(piece) > 40:
            out.append(piece)

        # Advance must always be substantial. Trimming to a sentence boundary
        # can leave a piece shorter than OVERLAP, and a naive
        # `len(piece) - OVERLAP` then goes negative - clamped to 1, that walks
        # the file one character at a time and turns a 15 kB file into 175
        # chunks instead of 11.
        i += max(CHUNK // 2, len(piece) - OVERLAP)
    return out


# ------------------------------------------------------------------ indexing

SKIP_DIRS = {'node_modules', '.git', '__pycache__', 'venv', '.venv', 'dist',
             'build', '.next', 'target', '.idea', '.vscode', 'AppData'}


def _walk(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in TEXT_EXT or ext in ('.pdf', '.docx'):
                yield os.path.join(dirpath, fn)


def index_folder(root, rebuild=False):
    """Walk a folder and embed anything readable.

    Unchanged files are skipped by modification time, so re-running this after
    adding a few documents costs seconds rather than re-embedding everything.
    """
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        return {'ok': False, 'error': 'No such folder: %s' % root}

    ok, dim = embed_ready()
    if not ok:
        return {'ok': False, 'error': 'The embedding model is not available. Run: '
                                      'ollama pull ' + EMBED_MODEL}

    with _lock:
        if _indexing['running']:
            return {'ok': False, 'error': 'An index is already running.'}
        _indexing.update({'running': True, 'done': 0, 'total': 0, 'file': '', 'error': ''})

    def work():
        con = _db()
        try:
            if rebuild:
                con.execute("DELETE FROM chunks WHERE kind='doc' AND source LIKE ?",
                            (root + '%',))
                con.commit()

            files = list(_walk(root))
            _indexing['total'] = len(files)
            unreadable_pdf = 0

            for path in files:
                _indexing['file'] = os.path.basename(path)
                try:
                    mtime = os.path.getmtime(path)
                    row = con.execute(
                        'SELECT mtime FROM chunks WHERE source=? LIMIT 1', (path,)
                    ).fetchone()
                    if row and abs(row[0] - mtime) < 1:
                        _indexing['done'] += 1
                        continue        # unchanged since last time

                    text = read_text(path)
                    if text is None:
                        unreadable_pdf += 1
                        _indexing['done'] += 1
                        continue
                    pieces = chunk_text(text)
                    if not pieces:
                        _indexing['done'] += 1
                        continue

                    con.execute('DELETE FROM chunks WHERE source=?', (path,))
                    written = 0
                    for start in range(0, len(pieces), BATCH):
                        group = pieces[start:start + BATCH]
                        vecs = embed_many(group)
                        for k, (piece, vec) in enumerate(zip(group, vecs)):
                            if not vec:
                                continue
                            con.execute(
                                'INSERT INTO chunks (kind, source, ord, text, vec, mtime, at) '
                                'VALUES (?,?,?,?,?,?,?)',
                                ('doc', path, start + k, piece, _pack(vec), mtime, time.time()))
                            written += 1
                    con.commit()
                except Exception as err:
                    _indexing['error'] = '%s: %s' % (os.path.basename(path), err)
                _indexing['done'] += 1

            if unreadable_pdf:
                _indexing['error'] = ('%d PDF(s) skipped — install pypdf to read them '
                                      '(pip install pypdf)' % unreadable_pdf)
        finally:
            con.close()
            _indexing['running'] = False

    threading.Thread(target=work, daemon=True).start()
    return {'ok': True, 'started': root}


def index_status():
    d = dict(_indexing)
    con = _db()
    try:
        d['chunks'] = con.execute("SELECT COUNT(*) FROM chunks WHERE kind='doc'").fetchone()[0]
        d['files'] = con.execute(
            "SELECT COUNT(DISTINCT source) FROM chunks WHERE kind='doc'").fetchone()[0]
        d['chats'] = con.execute("SELECT COUNT(*) FROM chunks WHERE kind='chat'").fetchone()[0]
    finally:
        con.close()
    d['ok'] = True
    return d


def forget_folder(root):
    root = os.path.abspath(os.path.expanduser(root))
    con = _db()
    try:
        n = con.execute("DELETE FROM chunks WHERE kind='doc' AND source LIKE ?",
                        (root + '%',)).rowcount
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'summary': 'Removed %d chunks under %s.' % (n, root)}


# -------------------------------------------------------- conversation recall

def remember_exchange(user_text, reply_text):
    """Store one exchange so it can be found months later.

    Only the pair is kept, not the whole transcript: a question with its answer
    is the unit that is actually useful to retrieve."""
    body = ('You asked: %s\n\nI answered: %s' % (user_text or '', reply_text or '')).strip()
    if len(body) < 60:
        return {'ok': True, 'skipped': 'too short to be worth recalling'}
    try:
        vec = embed(body[:4000])
        if not vec:
            return {'ok': False, 'error': 'no embedding'}
    except Exception as err:
        return {'ok': False, 'error': str(err)}

    con = _db()
    try:
        con.execute('INSERT INTO chunks (kind, source, ord, text, vec, mtime, at) '
                    'VALUES (?,?,?,?,?,?,?)',
                    ('chat', time.strftime('%Y-%m-%d %H:%M'), 0, body[:4000],
                     _pack(vec), 0, time.time()))
        con.commit()
    finally:
        con.close()
    return {'ok': True}


# ------------------------------------------------------------------- search

def search(query, kind=None, n=6):
    if not (query or '').strip():
        return {'ok': False, 'error': 'No query.'}
    try:
        qv = embed(query)
    except Exception as err:
        return {'ok': False, 'error': 'Embedding failed: %s. Is Ollama running?' % err}
    if not qv:
        return {'ok': False, 'error': 'The embedding model returned nothing. Run: '
                                      'ollama pull ' + EMBED_MODEL}

    import numpy as np
    q = np.array(qv, dtype='float32')
    qn = np.linalg.norm(q) or 1.0

    con = _db()
    try:
        sql = 'SELECT source, text, vec, at FROM chunks'
        args = ()
        if kind:
            sql += ' WHERE kind=?'
            args = (kind,)
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()

    if not rows:
        return {'ok': True, 'summary': 'Nothing has been indexed yet.'}

    scored = []
    for source, text, blob, at in rows:
        v = np.frombuffer(blob, dtype='float32')
        if v.shape != q.shape:
            continue                       # embedded with a different model
        sim = float(q.dot(v) / (qn * (np.linalg.norm(v) or 1.0)))
        scored.append((sim, source, text, at))

    scored.sort(key=lambda r: -r[0])
    top = [r for r in scored[:n] if r[0] > 0.35]
    if not top:
        return {'ok': True, 'summary': 'Nothing indexed looks relevant to that.'}

    out = []
    for sim, source, text, at in top:
        where = source if len(source) < 90 else '…' + source[-86:]
        out.append('[%.0f%% — %s]\n%s' % (sim * 100, where, text[:900]))
    return {'ok': True, 'hits': len(top), 'summary': '\n\n'.join(out)}
