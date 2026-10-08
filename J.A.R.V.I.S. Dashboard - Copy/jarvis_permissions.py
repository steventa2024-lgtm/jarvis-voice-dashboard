"""Mk VIII deterministic authorization. Policy never relaxes tool sandboxes.

Only the HTTP/UI adapter resolves approvals. Models receive no policy tools.
Pending arguments are bounded recovery data, never audit data or reasoning.
"""
import contextlib
import hashlib
import json
import ntpath
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import uuid

SCHEMA = 1
TTL = 600
RECEIPT_TTL = 60
RISKS = {}
for tier, names in {
    'low': 'READ_FILE READ_PROJECT BROWSER_READ READ_CLIPBOARD READ_SCREEN READ_EMAIL READ_CALENDAR READ_SPOTIFY READ_MEMORY READ_TASK READ_STATE',
    'medium': 'CREATE_FILE RUN_TESTS LAUNCH_APP CONTROL_APP OPEN_URL WRITE_CLIPBOARD CONTROL_SPOTIFY CREATE_TASK MODIFY_TASK CANCEL_TASK MODIFY_MEMORY MODIFY_STATE',
    'high': 'WRITE_FILE MODIFY_PROJECT EXECUTE_CODE BROWSER_INTERACT SUBMIT_FORM CONTROL_DESKTOP MODIFY_CALENDAR SEND_EMAIL DOWNLOAD_FILE UPLOAD_FILE SEND_MESSAGE',
    'critical': 'DELETE_FILE INSTALL_PACKAGE PURCHASE PAYMENT SYSTEM_SHUTDOWN SECURITY_CHANGE SYSTEM_LOCK CLEAR_DATA',
}.items():
    for name in names.split():
        RISKS[name] = tier
UNAVAILABLE = {'DELETE_FILE', 'INSTALL_PACKAGE', 'PURCHASE', 'PAYMENT', 'SYSTEM_SHUTDOWN', 'SEND_EMAIL', 'SEND_MESSAGE', 'MODIFY_CALENDAR', 'SUBMIT_FORM'}
# Medium calendar/task management is interactive only unless explicitly granted.
BACKGROUND_DEFAULT = {'READ_FILE', 'READ_PROJECT', 'BROWSER_READ', 'READ_EMAIL', 'READ_CALENDAR', 'READ_SPOTIFY', 'READ_MEMORY', 'READ_TASK', 'READ_STATE', 'RUN_TESTS'}
ONCE_ONLY = {'SYSTEM_LOCK', 'CLEAR_DATA', 'SECURITY_CHANGE'} | UNAVAILABLE
SERVER_TOOLS = {'files', 'desktop', 'google', 'spotify', 'recall', 'video', 'jobs', 'job_hunt', 'minecraft', 'lessons', 'tasks', 'reminders'}
SERVER_INTERFACE_ACTIONS = {'open_app','open_url','search_the_web_in_a_tab','play_on_youtube','play_pause','next_track','previous_track','stop_media','volume_up','volume_down','mute','lock_screen'}
ROUTES = {f'/api/{name}/command': name for name in ['files', 'desktop', 'google', 'spotify', 'recall', 'video', 'jobs', 'minecraft', 'lessons', 'tasks', 'memory', 'apply']}
ROUTES.update({'/api/hunt/command': 'job_hunt', '/api/open': 'open'})

MAP = {
 'files': {
  **dict.fromkeys('find read transcribe'.split(), 'READ_FILE'),
  **dict.fromkeys('capabilities list list_project projects media_files history diff status propose discard'.split(), 'READ_PROJECT'),
  'write': 'WRITE_FILE', 'apply': 'WRITE_FILE', 'scaffold': 'CREATE_FILE', 'revert': 'MODIFY_PROJECT', 'rollback': 'MODIFY_PROJECT',
  'run': 'RUN_TESTS', 'check': 'RUN_TESTS', 'render': 'RUN_TESTS', 'media': 'CREATE_FILE', 'join': 'CREATE_FILE', 'configure': 'SECURITY_CHANGE'},
 'desktop': {'clipboard': 'READ_CLIPBOARD', 'active_window': 'READ_STATE', 'copy': 'WRITE_CLIPBOARD'},
 'google': {'agenda': 'READ_CALENDAR', 'mail': 'READ_EMAIL', 'search_mail': 'READ_EMAIL'},
 'spotify': {**dict.fromkeys('current devices'.split(), 'READ_SPOTIFY'), **dict.fromkeys('play_track play_album play_artist play_playlist pause resume next previous volume'.split(), 'CONTROL_SPOTIFY')},
 'recall': {'search': 'READ_MEMORY', 'search_chats': 'READ_MEMORY', 'status': 'READ_MEMORY', 'index': 'MODIFY_MEMORY', 'forget': 'CLEAR_DATA', 'log_exchange': 'MODIFY_MEMORY'},
 'video': {'list': 'READ_STATE', 'review': 'READ_STATE', 'publish_packet': 'READ_STATE', 'render': 'CREATE_FILE'},
 'jobs': {**dict.fromkeys('list shortlist analyse'.split(), 'READ_STATE'), **dict.fromkeys('scan tailor decide watchlist profile'.split(), 'MODIFY_STATE'), 'prepare': 'BROWSER_INTERACT'},
 'job_hunt': {'pending': 'READ_STATE', **dict.fromkeys('start decline stop'.split(), 'MODIFY_STATE'), 'choose': 'BROWSER_INTERACT', 'sign_in': 'OPEN_URL'},
 'minecraft': {'where': 'READ_STATE', 'status': 'READ_STATE', 'plan': 'READ_STATE', 'build': 'CONTROL_APP', 'add': 'CONTROL_APP', 'clear': 'CLEAR_DATA'},
 'lessons': {'progress': 'READ_STATE', 'due': 'READ_STATE', **dict.fromkeys('start add record asl'.split(), 'MODIFY_STATE'), 'forget': 'CLEAR_DATA'},
 'tasks': {**dict.fromkeys('list get history'.split(), 'READ_TASK'), 'create': 'CREATE_TASK', **dict.fromkeys('pause resume run_now resume_run restart_run'.split(), 'MODIFY_TASK'), 'cancel': 'CANCEL_TASK', 'cancel_run': 'CANCEL_TASK'},
 'memory': {**dict.fromkeys('list recall brief precedents relevant precedent episodes notices'.split(), 'READ_MEMORY'), **dict.fromkeys('remember set cancel episode consolidate reindex notice notice_seen notices_seen'.split(), 'MODIFY_MEMORY'), 'forget': 'CLEAR_DATA', 'clear': 'CLEAR_DATA', 'notices_clear': 'CLEAR_DATA'},
 'control_interface': {**dict.fromkeys('play_pause next_track previous_track stop_media volume_up volume_down mute'.split(), 'CONTROL_APP'), 'open_app': 'LAUNCH_APP', **dict.fromkeys('open_url search_the_web_in_a_tab play_on_youtube'.split(), 'OPEN_URL'), 'lock_screen': 'SYSTEM_LOCK', 'clear_conversation': 'CLEAR_DATA', 'clear_log': 'CLEAR_DATA', **dict.fromkeys('set_accent set_timer set_units stop_speaking fullscreen'.split(), 'MODIFY_STATE'), 'refresh_weather': 'READ_STATE'},
 'apply': {'shortlist': 'READ_STATE', 'analyse': 'READ_STATE', 'tailor': 'CREATE_FILE', 'prepare': 'BROWSER_INTERACT', 'close_forms': 'CONTROL_APP', 'sign_in': 'OPEN_URL'},
}
CLIENT_MAP = {'web_search': 'BROWSER_READ', 'web_fetch': 'BROWSER_READ', 'lookup': 'BROWSER_READ', 'see_screen': 'READ_SCREEN', 'see_preview': 'READ_PROJECT', 'remember': 'MODIFY_MEMORY', 'forget': 'CLEAR_DATA'}
def read_action(path, query=None):
    query = query or {}
    fields = {k: v[0] if isinstance(v, list) and v else v for k,v in query.items()}
    calls = {'/api/screenshot': ('see_screen', {}), '/api/search': ('web_search', fields), '/api/fetch': ('web_fetch', fields), '/api/knowledge': ('lookup', fields),
      '/api/memory/all': ('memory', {'action':'relevant'}), '/api/memory/due': ('memory', {'action':'list'}), '/api/recall/status': ('recall', {'action':'status'}),
      '/api/files/capabilities': ('files', {'action':'capabilities'}), '/api/google/status': ('google', {'action':'agenda'}), '/api/spotify/status': ('spotify', {'action':'current'}),
      '/api/apps': ('desktop', {'action':'active_window'}), '/preview-file': ('files', dict(fields, action='read'))}
    if path.startswith('/preview/'):
        calls[path] = ('files', {'action':'list_project', 'project':urllib.parse.unquote(path.split('/')[2])})
    call = calls.get(path)
    return {'kind':'route_read','tool':call[0],'input':call[1]} if call else None
SENSITIVE = re.compile(r'''(?i)(?:sk-(?:proj-|ant-|or-)?[a-z0-9_-]{16,}|bearer\s+\S+|(?:api[_-]?key|password|access_token|refresh_token|client_secret|authorization)\s*["']?\s*[=:]\s*["']?\s*\S+)''')

def clean(value, limit=240):
    return SENSITIVE.sub('[redacted]', str(value or ''))[:limit]

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def target(value):
    text = str(value or 'local').strip()
    if '://' in text:
        u = urllib.parse.urlsplit(text)
        return urllib.parse.urlunsplit((u.scheme.lower(), (u.hostname or '').lower() + (':' + str(u.port) if u.port else ''), u.path or '/', u.query, ''))
    # Windows semantics on all platforms; boundary matching below never uses startswith alone.
    return ntpath.normpath(text.replace('/', '\\')).replace('\\', '/').casefold()

def matches(resource, scope, kind):
    return kind == 'global' or resource == scope or (kind == 'target' and resource.startswith(scope.rstrip('/') + '/'))

def classify(action):
    if not isinstance(action, dict) or set(action) - {'kind', 'tool', 'input'}:
        raise ValueError('Invalid action envelope.')
    tool, args = action.get('tool'), action.get('input', {})
    if not isinstance(tool, str) or not isinstance(args, dict) or len(canonical(args)) > 262144:
        raise ValueError('Invalid or oversized action.')
    name = args.get('action', '')
    if tool == 'open':
        capability = 'LAUNCH_APP' if args.get('app') else 'OPEN_URL' if args.get('url') or args.get('settings') else 'SYSTEM_LOCK' if args.get('lock') else 'CONTROL_APP' if args.get('key') else None
    elif tool == 'translate':
        capability = {'screen': 'READ_SCREEN', 'clipboard': 'READ_CLIPBOARD'}.get(args.get('source'))
    elif tool == 'reminders':
        capability = {'set': 'MODIFY_MEMORY', 'list': 'READ_MEMORY', 'cancel': 'MODIFY_MEMORY', 'recall': 'READ_MEMORY'}.get(name)
    else:
        capability = MAP.get(tool, {}).get(name) or CLIENT_MAP.get(tool)
    if tool == 'files' and args.get('configure'):
        capability = 'SECURITY_CHANGE'
    if tool == 'files' and name == 'run' and args.get('what') not in ('pytest', 'npm_test'):
        capability = 'EXECUTE_CODE'
    if tool in ('google', 'spotify') and any(k in args for k in ('client_id', 'client_secret', 'disconnect')):
        capability = 'SECURITY_CHANGE'
    if tool == 'memory' and args.get('clear_memories'):
        capability = 'CLEAR_DATA'
    project = args.get('project') or (args.get('name') if tool == 'files' else None)
    value = (str(project) + '/' + str(args.get('path', ''))) if project else args.get('path') or args.get('app') or args.get('url') or args.get('folder') or args.get('task') or args.get('run_id') or args.get('id') or tool
    if tool == 'control_interface' and name == 'open_app':
        value = args.get('query')
    if tool == 'spotify':
        value = 'spotify'
    result = {'capability': capability or 'UNKNOWN', 'risk': RISKS.get(capability, 'critical'), 'target': target(value), 'tool': tool, 'action': str(name)}
    if tool == 'files' and project:
        result['project_scope'] = target(project)
    return result

class PermissionStore:
    def __init__(self, path, clock=time.time, describe=None):
        self.path, self.clock = str(path), clock
        self.describe = describe or classify
        self.sessions = {}
        self.lock = threading.RLock()
        with self.db() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA):
                raise ValueError('Unsupported permission schema.')
            for ddl in [
              'CREATE TABLE IF NOT EXISTS permission_policies(id TEXT PRIMARY KEY,capability TEXT,effect TEXT,scope_type TEXT,scope_value TEXT,session_id TEXT,created_at REAL)',
              'CREATE TABLE IF NOT EXISTS approval_requests(id TEXT PRIMARY KEY,fingerprint TEXT,action_json TEXT,context_json TEXT,capability TEXT,target TEXT,risk TEXT,status TEXT,created_at REAL,expires_at REAL,resolved_at REAL)',
              'CREATE TABLE IF NOT EXISTS authorization_receipts(token_hash TEXT PRIMARY KEY,approval_id TEXT,fingerprint TEXT,context_key TEXT,expires_at REAL,used INTEGER DEFAULT 0)',
              'CREATE TABLE IF NOT EXISTS permission_events(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,decision TEXT,capability TEXT,target TEXT,source TEXT,mission_id TEXT,run_id TEXT,policy_id TEXT)',
            ]:
                db.execute(ddl)
            # Session records from a previous process are never permanent grants.
            db.execute('DELETE FROM permission_policies WHERE session_id IS NOT NULL')
            db.execute('UPDATE authorization_receipts SET used=1')
            db.execute('PRAGMA user_version=1')

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, parent=None):
        key = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[key] = {'user_text': '', 'background': False, 'turn_id': str(uuid.uuid4()), 'grant_session': self.scope_session(parent) if parent else key}
        return key

    def scope_session(self, session):
        return self.sessions.get(session, {}).get('grant_session', session)

    def set_context(self, session, context):
        if session not in self.sessions or not isinstance(context, dict):
            raise ValueError('Invalid UI session.')
        allowed = {'user_text', 'background', 'mission_id', 'task_run_id', 'run_policy', 'turn_id'}
        if set(context) - allowed or not isinstance(context.get('background', False), bool):
            raise ValueError('Invalid trusted context.')
        with self.lock:
            prior_turn = self.sessions[session]['turn_id']
            grant_session = self.scope_session(session)
            self.sessions[session] = {k: clean(v, 1200 if k == 'user_text' else 160) for k, v in context.items() if k != 'background'}
            self.sessions[session].setdefault('turn_id', prior_turn)
            self.sessions[session]['grant_session'] = grant_session
            self.sessions[session]['background'] = context.get('background', False)

    def context(self, session):
        with self.lock:
            return {k:v for k,v in self.sessions.get(session, {'background': False, 'user_text': ''}).items() if k != 'grant_session'}

    def fingerprint(self, action, context):
        item = self.describe(action)
        binding = {k: item.get(k) for k in ('capability', 'target', 'project_scope', 'content_hash')}
        return hashlib.sha256(canonical({'action': action, 'resource': binding, 'mission': context.get('mission_id'), 'run': context.get('task_run_id'), 'background': bool(context.get('background')), 'turn': context.get('turn_id') if not context.get('mission_id') and not context.get('task_run_id') else None}).encode()).hexdigest()

    def audit(self, db, decision, item, source, context, policy=None):
        db.execute('INSERT INTO permission_events(at,decision,capability,target,source,mission_id,run_id,policy_id) VALUES(?,?,?,?,?,?,?,?)', (self.clock(), decision, item['capability'], clean(item['target']), clean(source), clean(context.get('mission_id'), 100), clean(context.get('task_run_id'), 100), policy))
        db.execute('DELETE FROM permission_events WHERE id NOT IN (SELECT id FROM permission_events ORDER BY id DESC LIMIT 1000)')

    def expire(self, db):
        db.execute("UPDATE approval_requests SET status='expired' WHERE status='pending' AND expires_at<=?", (self.clock(),))
        db.execute('DELETE FROM authorization_receipts WHERE expires_at<?', (self.clock()-3600,))
        db.execute("DELETE FROM approval_requests WHERE status NOT IN ('pending','approved') AND created_at<?", (self.clock()-30*86400,))

    def default(self, item, context):
        cap = item['capability']
        protected_store = target(self.path)
        if item['target'] in (protected_store, protected_store+'-wal', protected_store+'-shm'):
            return 'DENY', 'permission_store_is_not_a_tool_target'
        if cap == 'UNKNOWN' or cap in UNAVAILABLE:
            return 'DENY', 'unknown_or_unavailable_capability'
        if context.get('background'):
            if item['tool'] in ('memory', 'recall') and item['action'] in ('episode', 'log_exchange', 'notice_seen', 'notices_seen'):
                return 'ALLOW', 'existing_internal_memory_bookkeeping'
            if cap in ('CREATE_TASK', 'MODIFY_TASK', 'CANCEL_TASK'):
                return 'DENY', 'scheduled_work_cannot_manage_tasks'
            if cap in ('WRITE_FILE', 'CREATE_FILE', 'MODIFY_PROJECT') and context.get('run_policy') != 'reviewed_writes':
                return 'DENY', 'read_only_task_boundary'
            return ('ALLOW', 'safe_background_observation') if cap in BACKGROUND_DEFAULT else ('ASK', 'background_requires_explicit_grant')
        text = context.get('user_text', '').lower().strip()
        # Only lock/clear direct phrases authorize these once. Tool data never enters this text.
        if cap == 'SYSTEM_LOCK' and re.fullmatch(r'(?:jarvis[, ]+)?(?:please )?lock (?:my |the )?(?:pc|computer|screen|workstation)[.!]?', text):
            return 'ALLOW', 'explicit_user_request'
        if cap == 'CLEAR_DATA' and item['tool'] == 'control_interface' and item['action'] == 'clear_conversation' and re.fullmatch(r'(?:jarvis[, ]+)?(?:please )?clear (?:the |my )?conversation[.!]?', text):
            return 'ALLOW', 'explicit_user_request'
        return ('ALLOW', 'default_'+item['risk']) if item['risk'] in ('low', 'medium') else ('ASK', 'protected_'+item['risk'])

    def policy(self, db, item, context, session):
        base = self.default(item, context)
        if base[0] == 'DENY':
            return *base, None
        rows = [r for r in db.execute('SELECT * FROM permission_policies WHERE capability=?', (item['capability'],)) if (r['session_id'] is None or r['session_id'] == self.scope_session(session)) and matches(item['target'], r['scope_value'], r['scope_type'])]
        # Explicit denials override every grant; exact > project > global for ASK/ALLOW.
        rows.sort(key=lambda r: (r['effect'] == 'DENY', r['scope_type'] != 'global', len(r['scope_value']), r['session_id'] is not None, r['created_at']), reverse=True)
        if rows:
            r = rows[0]
            return r['effect'], 'user_policy', r['id']
        return *base, None

    def mint(self, db, fp, context, approval=None):
        token = secrets.token_urlsafe(32)
        key = canonical({'run': context.get('task_run_id'), 'mission': context.get('mission_id'), 'background': bool(context.get('background'))})
        db.execute('INSERT INTO authorization_receipts VALUES(?,?,?,?,?,0)', (hashlib.sha256(token.encode()).hexdigest(), approval, fp, key, self.clock()+RECEIPT_TTL))
        return token

    def check(self, action, session=None):
        item, context = self.describe(action), self.context(session)
        fp = self.fingerprint(action, context)
        with self.db() as db:
            self.expire(db)
            decision, source, policy_id = self.policy(db, item, context, session)
            result = dict(item, decision=decision, reason=source)
            if decision == 'ALLOW':
                result['receipt'] = self.mint(db, fp, context)
            elif decision == 'ASK':
                old = db.execute('SELECT * FROM approval_requests WHERE fingerprint=? ORDER BY created_at DESC LIMIT 1', (fp,)).fetchone()
                if old and old['status'] in ('denied', 'expired', 'cancelled'):
                    result.update(decision='DENY', reason='action_already_resolved_or_expired')
                elif old and old['status'] in ('pending', 'approved'):
                    result['approval'] = self.view_approval(old)
                else:
                    raw = canonical(action)
                    # Credentials never enter recovery storage. Sensitive configuration must be retried in its existing settings UI.
                    sensitive = SENSITIVE.search(raw) or any(SENSITIVE.search(str(v)) for v in action.get('input', {}).values()) or any(k in action.get('input', {}) for k in ('client_secret', 'client_id'))
                    if sensitive:
                        # Fingerprint binds the secret arguments, while the DB contains only placeholders.
                        safe = dict(action, input={k: '[redacted]' if k in ('content','text','client_id','client_secret') or SENSITIVE.search(str(v)) else v for k,v in action['input'].items()})
                        safe['input']['_requires_original'] = True
                        raw = canonical(safe)
                    ident = str(uuid.uuid4())
                    db.execute('INSERT INTO approval_requests VALUES(?,?,?,?,?,?,?,?,?,?,NULL)', (ident, fp, raw, canonical(context), item['capability'], clean(item['target']), item['risk'], 'pending', self.clock(), self.clock()+TTL))
                    result['approval'] = self.view_approval(db.execute('SELECT * FROM approval_requests WHERE id=?', (ident,)).fetchone())
            self.audit(db, result['decision'], item, result['reason'], context, policy_id)
            return dict(result, ok=True)

    def view_approval(self, row):
        data = dict(row)
        context = json.loads(data.pop('context_json'))
        proposed = json.loads(data.pop('action_json'))
        item = self.describe(proposed)
        data['grant_scope'] = clean(item.get('project_scope') or item['target'])
        data.pop('fingerprint')
        data.update(mission_id=context.get('mission_id'), task_run_id=context.get('task_run_id'), background=context.get('background', False))
        data['choices'] = ['allow_once', 'deny_once', 'always_deny']
        if data['capability'] not in ONCE_ONLY:
            data['choices'][1:1] = ['allow_session', 'allow_target']
            if data['risk'] == 'low':
                data['choices'].insert(3, 'allow_always')
        return data

    def resolve(self, ident, choice, session):
        if session not in self.sessions:
            raise ValueError('Trusted UI session required.')
        with self.db() as db:
            self.expire(db)
            row = db.execute('SELECT * FROM approval_requests WHERE id=?', (ident,)).fetchone()
            if not row or row['status'] != 'pending' or choice not in self.view_approval(row)['choices']:
                raise ValueError('Approval is stale, resolved, or choice is unavailable.')
            context, action = json.loads(row['context_json']), json.loads(row['action_json'])
            item = self.describe(action)
            effect, _, _ = self.policy(db, item, context, session)
            if effect == 'DENY' and choice.startswith('allow'):
                raise ValueError('Current policy denies this action.')
            allow = choice.startswith('allow')
            if choice in ('allow_session', 'allow_target', 'allow_always', 'always_deny'):
                scope = item['target']
                # A project-level file grant uses the trusted normalized project argument.
                if item.get('project_scope'):
                    scope = item['project_scope']
                self._policy(db, item['capability'], 'ALLOW' if allow else 'DENY', 'global' if choice == 'allow_always' else 'target', '' if choice == 'allow_always' else scope, self.scope_session(session) if choice == 'allow_session' else None)
            state = 'approved' if allow else 'denied'
            db.execute('UPDATE approval_requests SET status=?,resolved_at=? WHERE id=?', (state, self.clock(), ident))
            self.audit(db, 'ALLOW' if allow else 'DENY', item, 'approval_'+choice, context)
            return {'ok': True, 'decision': 'ALLOW' if allow else 'DENY', 'approval_id': ident}

    def _policy(self, db, cap, effect, kind, scope, session=None):
        if cap not in RISKS or effect not in ('ALLOW', 'ASK', 'DENY') or kind not in ('global', 'target', 'exact'):
            raise ValueError('Invalid policy.')
        if effect == 'ALLOW' and (cap in UNAVAILABLE or cap in ONCE_ONLY or (kind == 'global' and RISKS[cap] != 'low')):
            raise ValueError('This capability cannot receive that persistent grant.')
        if kind != 'global' and (not isinstance(scope, str) or not scope.strip() or len(scope) > 300 or SENSITIVE.search(scope)):
            raise ValueError('Provide a bounded target scope.')
        ident = str(uuid.uuid4())
        db.execute('INSERT INTO permission_policies VALUES(?,?,?,?,?,?,?)', (ident, cap, effect, kind, target(scope) if kind != 'global' else '', session, self.clock()))
        db.execute('UPDATE authorization_receipts SET used=1')
        return ident

    def recover(self, ident, session, proposed=None):
        with self.db() as db:
            self.expire(db)
            row = db.execute('SELECT * FROM approval_requests WHERE id=?', (ident,)).fetchone()
            if not row or row['status'] != 'approved' or row['expires_at'] <= self.clock():
                raise ValueError('No unexpired approved action to resume.')
            action, original = json.loads(row['action_json']), json.loads(row['context_json'])
            current = self.context(session)
            if action['input'].get('_requires_original'):
                if not proposed or self.fingerprint(proposed, original) != row['fingerprint']:
                    raise ValueError('Sensitive arguments were not persisted; re-enter the exact original action.')
                action = proposed
            if self.fingerprint(action, original) != row['fingerprint']:
                raise ValueError('Target or held action changed after approval; request approval again.')
            if any(original.get(k) != current.get(k) for k in ('task_run_id', 'mission_id')) or bool(original.get('background')) != bool(current.get('background')):
                raise ValueError('Resume in the original mission/run context.')
            if self.policy(db, self.describe(action), current, session)[0] == 'DENY':
                raise ValueError('Current policy denies this action.')
            db.execute('UPDATE authorization_receipts SET used=1 WHERE approval_id=?', (ident,))
            return {'ok': True, 'action': action, 'classification': self.describe(action), 'receipt': self.mint(db, self.fingerprint(action, current), current, ident)}

    def consume(self, action, token, session=None):
        context, item = self.context(session), self.describe(action)
        with self.db() as db:
            self.expire(db)
            row = db.execute('SELECT * FROM authorization_receipts WHERE token_hash=?', (hashlib.sha256(str(token or '').encode()).hexdigest(),)).fetchone()
            if not row or row['used'] or row['expires_at'] <= self.clock() or row['fingerprint'] != self.fingerprint(action, context):
                self.audit(db, 'DENY', item, 'invalid_expired_replayed_or_mismatched_receipt', context)
                return False
            if self.policy(db, item, context, session)[0] == 'DENY':
                return False
            db.execute('UPDATE authorization_receipts SET used=1 WHERE token_hash=?', (row['token_hash'],))
            if row['approval_id']:
                db.execute("UPDATE approval_requests SET status='executed' WHERE id=? AND status='approved'", (row['approval_id'],))
            self.audit(db, 'ALLOW', item, 'receipt_consumed', context)
            return True

    def command(self, data, session=None):
        if not isinstance(data, dict):
            raise ValueError('Object required.')
        op = data.get('action')
        if op == 'session':
            if data.get('isolate') and session not in self.sessions:
                raise ValueError('Existing page session required for an isolated UI channel.')
            return {'ok': True, 'session': self.session(session if data.get('isolate') else None), 'routes': ROUTES}
        if session not in self.sessions:
            raise ValueError('UI session required.')
        if op == 'context':
            self.set_context(session, data.get('context', {}))
            return {'ok': True}
        if op == 'check':
            return self.check(data.get('proposed'), session)
        if op == 'classify':
            item = self.describe(data.get('proposed'))
            known = item['capability'] != 'UNKNOWN'
            proposed = data.get('proposed') or {}
            if proposed.get('kind') == 'tool' and item['tool'] == 'files' and item['action'] in ('apply','propose','discard','configure','render','check','projects','media_files','status','rollback','list','diff'):
                known = False  # Internal file/review operations are not model tools.
            if proposed.get('kind') == 'tool' and item['tool'] == 'files' and set(proposed.get('input', {})) & {'configure','roots','projects','force','id'}:
                known = False
            return {'ok': True, 'known': known, 'server': item['tool'] in SERVER_TOOLS or (item['tool'] == 'control_interface' and item['action'] in SERVER_INTERFACE_ACTIONS)}
        if op == 'preflight':
            item = self.describe(data.get('proposed'))
            context = self.context(session)
            with self.db() as db:
                decision, source, policy_id = self.policy(db, item, context, session)
                if decision == 'DENY':
                    self.audit(db, decision, item, source, context, policy_id)
            return {'ok': True, 'decision': decision, 'reason': source, 'capability': item['capability']}
        if op == 'route_action':
            return {'ok': True, 'proposed': read_action(data.get('path',''),data.get('query',{}))}
        if op == 'consume':
            return {'ok': self.consume(data.get('proposed'), data.get('receipt'), session)}
        if op == 'resolve_approval':
            return self.resolve(data.get('id'), data.get('choice'), session)
        if op == 'recover':
            return self.recover(data.get('id'), session, data.get('proposed'))
        with self.db() as db:
            self.expire(db)
            if op == 'list':
                return {'ok': True, 'policies': [dict(r) for r in db.execute('SELECT * FROM permission_policies WHERE session_id IS NULL OR session_id=? ORDER BY created_at DESC LIMIT 200', (self.scope_session(session),))], 'approvals': [self.view_approval(r) for r in db.execute("SELECT * FROM approval_requests WHERE status IN ('pending','approved') ORDER BY created_at DESC LIMIT 60")], 'expired': [{'id':r['id'], **{k:v for k,v in json.loads(r['context_json']).items() if k in ('mission_id','task_run_id')}} for r in db.execute("SELECT id,context_json FROM approval_requests WHERE status='expired' ORDER BY created_at DESC LIMIT 60")], 'capabilities': RISKS}
            if op == 'history':
                return {'ok': True, 'events': [dict(r) for r in db.execute('SELECT * FROM permission_events ORDER BY id DESC LIMIT 100')]}
            if op in ('create_policy', 'update_policy'):
                if op == 'update_policy':
                    old = db.execute('SELECT * FROM permission_policies WHERE id=?', (data.get('id'),)).fetchone()
                    if not old:
                        raise ValueError('Policy not found.')
                    data = dict(data, capability=old['capability'], scope_type=old['scope_type'], scope_value=old['scope_value'])
                    db.execute('DELETE FROM permission_policies WHERE id=?', (old['id'],))
                ident = self._policy(db, data.get('capability'), data.get('effect'), data.get('scope_type', 'target'), data.get('scope_value'), self.scope_session(session) if data.get('persistence') == 'session' else None)
                self.audit(db, data.get('effect'), {'capability': data.get('capability'), 'target': data.get('scope_value') or 'global'}, 'policy_changed', {}, ident)
                return {'ok': True, 'id': ident}
            if op in ('delete_policy', 'clear_session'):
                if op == 'delete_policy':
                    db.execute('DELETE FROM permission_policies WHERE id=?', (data.get('id'),))
                else:
                    db.execute('DELETE FROM permission_policies WHERE session_id=?', (self.scope_session(session),))
                db.execute('UPDATE authorization_receipts SET used=1')
                self.audit(db, 'DENY', {'capability': 'POLICY', 'target': 'local'}, op, {})
                return {'ok': True}
        raise ValueError('Unknown permission UI command.')
