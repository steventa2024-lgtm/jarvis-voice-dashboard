"""
{name} — a small API and static server.

Standard library only: http.server and sqlite3. No pip install, no virtualenv,
no framework. That is a deliberate constraint rather than a limitation — an
MVP that needs a dependency tree before it prints anything is an MVP nobody
runs twice.

    python server.py            serves on 8000
    python server.py 8080       serves on 8080

Then open http://localhost:8000 in a browser.
"""

import http.server
import json
import mimetypes
import os
import socketserver
import sqlite3
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'data.db')
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


# ----------------------------------------------------------------- storage

def connect():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def setup():
    """Create the schema if it is not there. Safe to call on every start."""
    with open(os.path.join(HERE, 'schema.sql'), 'r', encoding='utf-8') as fh:
        sql = fh.read()
    con = connect()
    try:
        con.executescript(sql)
        con.commit()
    finally:
        con.close()


def list_items():
    con = connect()
    try:
        rows = con.execute('SELECT id, title, done, created FROM items '
                           'ORDER BY done, id DESC').fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def add_item(title):
    title = (title or '').strip()
    if not title:
        return None, 'An item needs a title.'
    con = connect()
    try:
        cur = con.execute('INSERT INTO items (title, done) VALUES (?, 0)', (title,))
        con.commit()
        row = con.execute('SELECT id, title, done, created FROM items WHERE id=?',
                          (cur.lastrowid,)).fetchone()
        return dict(row), None
    finally:
        con.close()


def set_done(item_id, done):
    con = connect()
    try:
        con.execute('UPDATE items SET done=? WHERE id=?', (1 if done else 0, item_id))
        con.commit()
        row = con.execute('SELECT id, title, done, created FROM items WHERE id=?',
                          (item_id,)).fetchone()
        return (dict(row), None) if row else (None, 'No such item.')
    finally:
        con.close()


def remove_item(item_id):
    con = connect()
    try:
        cur = con.execute('DELETE FROM items WHERE id=?', (item_id,))
        con.commit()
        return (cur.rowcount > 0), None
    finally:
        con.close()


# ------------------------------------------------------------------ server

class Handler(http.server.SimpleHTTPRequestHandler):

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get('content-length', 0))
            return json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/items':
            return self._json({'ok': True, 'items': list_items()})
        if path.startswith('/api/'):
            return self._json({'ok': False, 'error': 'No such endpoint.'}, 404)
        return self._static(path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/api/items':
            item, err = add_item(self._body().get('title'))
            if err:
                return self._json({'ok': False, 'error': err}, 400)
            return self._json({'ok': True, 'item': item}, 201)
        return self._json({'ok': False, 'error': 'No such endpoint.'}, 404)

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith('/api/items/'):
            try:
                item_id = int(path.rsplit('/', 1)[1])
            except ValueError:
                return self._json({'ok': False, 'error': 'Bad id.'}, 400)
            item, err = set_done(item_id, bool(self._body().get('done')))
            if err:
                return self._json({'ok': False, 'error': err}, 404)
            return self._json({'ok': True, 'item': item})
        return self._json({'ok': False, 'error': 'No such endpoint.'}, 404)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith('/api/items/'):
            try:
                item_id = int(path.rsplit('/', 1)[1])
            except ValueError:
                return self._json({'ok': False, 'error': 'Bad id.'}, 400)
            gone, _ = remove_item(item_id)
            return self._json({'ok': gone})
        return self._json({'ok': False, 'error': 'No such endpoint.'}, 404)

    def _static(self, path):
        """Serve the front end, and refuse anything outside this folder."""
        rel = path.lstrip('/') or 'index.html'
        target = os.path.abspath(os.path.join(HERE, rel))
        if os.path.commonpath([target, HERE]) != HERE or not os.path.isfile(target):
            self.send_error(404, 'Not found')
            return
        ctype, _ = mimetypes.guess_type(target)
        with open(target, 'rb') as fh:
            blob = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype or 'application/octet-stream')
        self.send_header('Content-Length', str(len(blob)))
        # A development server should never hand you yesterday's JavaScript.
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, fmt, *args):
        sys.stderr.write('  %s\n' % (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    setup()
    print()
    print('  {name} on http://localhost:%d' % PORT)
    print('  Ctrl+C to stop.')
    print()
    with Server(('', PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n  stopped.\n')
