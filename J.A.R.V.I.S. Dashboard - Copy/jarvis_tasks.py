"""Mk VIII durable objectives. Scheduling only; the browser remains the brain."""
import contextlib
import datetime as dt
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid

import jarvis_memory as memory

SCHEMA_VERSION = 1
LEASE_SECONDS = 90
ACTIVE_RUNS = ('queued', 'claimed', 'running', 'waiting_approval', 'interrupted')
OWNED_RUNS = ('claimed', 'running', 'waiting_approval')
TERMINAL_RUNS = ('completed', 'failed', 'cancelled')
SECRET = re.compile(r'\b(?:sk-(?:ant-|proj-)?[\w-]{16,}|gh[pousr]_\w{20,}|AIza[\w-]{25,})\b|(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|password|client[_ -]?secret)\s*[:=]\s*[^\s,;]+', re.I)


def compact(value, limit=600):
    return SECRET.sub('[redacted]', str(value or ''))[:limit]


def checked_text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('%s must be nonempty text, at most %s characters.' % (name, limit))
    if SECRET.search(value) or re.search(r'</?(?:think|analysis|reasoning)>', value, re.I):
        raise ValueError('%s cannot contain credentials or private reasoning.' % name)
    return value.strip()


def numeric(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid %s.' % name)
    return value


def wall(day, hour, minute):
    result = time.mktime((day.year, day.month, day.day, hour, minute, 0, -1, -1, -1))
    local = time.localtime(result)
    if (local.tm_year, local.tm_mon, local.tm_mday, local.tm_hour, local.tm_min) != (day.year, day.month, day.day, hour, minute):
        return None  # A nonexistent DST wall time is skipped, never silently shifted.
    return result


def next_occurrence(schedule, after):
    if schedule['type'] == 'once':
        return schedule['at'] if schedule['at'] > after else None
    if schedule['type'] == 'interval':
        return schedule['anchor'] + max(1, math.floor((after - schedule['anchor']) / schedule['seconds']) + 1) * schedule['seconds']
    day = dt.date(*time.localtime(after)[:3])
    for offset in range(9):
        candidate_day = day + dt.timedelta(days=offset)
        candidate = wall(candidate_day, schedule['hour'], schedule['minute'])
        if candidate_day.weekday() in schedule['days'] and candidate is not None and candidate > after:
            return candidate
    raise ValueError('No next local calendar occurrence found.')


def latest_due(schedule, first, now):
    if schedule['type'] == 'interval':
        return first + math.floor((now - first) / schedule['seconds']) * schedule['seconds']
    if schedule['type'] == 'calendar':
        day = dt.date(*time.localtime(now)[:3])
        for offset in range(9):
            candidate_day = day - dt.timedelta(days=offset)
            candidate = wall(candidate_day, schedule['hour'], schedule['minute'])
            if candidate_day.weekday() in schedule['days'] and candidate is not None and first <= candidate <= now:
                return candidate
    return first


def parse_schedule(value, now):
    """Small explicit grammar using the existing reminder parser for relative times."""
    if isinstance(value, dict):
        kind = value.get('type')
        fields = {'once': {'type', 'at'}, 'interval': {'type', 'seconds'}, 'calendar': {'type', 'hour', 'minute', 'days'}}
        if kind not in fields or set(value) != fields[kind]:
            raise ValueError('Invalid schedule fields.')
        if kind == 'once':
            schedule = {'type': kind, 'at': numeric(value['at'], 'future timestamp', now + .001, now + 10 * 366 * 86400)}
        elif kind == 'interval':
            schedule = {'type': kind, 'seconds': numeric(value['seconds'], 'interval seconds', 60, 604800), 'anchor': now}
        else:
            hour = numeric(value['hour'], 'hour', 0, 23)
            minute = numeric(value['minute'], 'minute', 0, 59)
            days = value['days']
            if int(hour) != hour or int(minute) != minute or not isinstance(days, list) or not days or len(days) > 7 or any(type(d) is not int or not 0 <= d <= 6 for d in days):
                raise ValueError('Invalid calendar schedule.')
            schedule = {'type': kind, 'hour': int(hour), 'minute': int(minute), 'days': sorted(set(days))}
    else:
        phrase = checked_text(value, 'schedule', 200).lower().strip().rstrip('.')
        interval = re.fullmatch(r'every (\d+) (minutes?|hours?)', phrase)
        recurring = re.fullmatch(r'every (day|morning|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday) at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', phrase)
        once = re.fullmatch(r'(?:(tonight|tomorrow|today) )?at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', phrase)
        if interval:
            seconds = int(interval[1]) * (3600 if interval[2].startswith('hour') else 60)
            return parse_schedule({'type': 'interval', 'seconds': seconds}, now)
        if recurring or once:
            match = recurring or once
            label, hour, minute, suffix = match.groups()
            if suffix is None and minute is None and label not in ('morning', 'tonight'):
                raise ValueError('Specify AM/PM or a 24-hour time such as 20:00.')
            hour, minute = int(hour), int(minute or 0)
            suffix = suffix or ('am' if label == 'morning' else 'pm' if label == 'tonight' else None)
            if minute > 59 or (suffix and not 1 <= hour <= 12) or (not suffix and hour > 23):
                raise ValueError('Invalid local clock time.')
            if suffix:
                hour = hour % 12 + (12 if suffix == 'pm' else 0)
            if recurring:
                weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
                days = list(range(5)) if label == 'weekday' else list(range(7)) if label in ('day', 'morning') else [weekdays.index(label)]
                return parse_schedule({'type': 'calendar', 'hour': hour, 'minute': minute, 'days': days}, now)
            day = dt.date(*time.localtime(now)[:3]) + dt.timedelta(days=1 if label == 'tomorrow' else 0)
            when = wall(day, hour, minute)
            if when is not None and when <= now and label is None:
                when = wall(day + dt.timedelta(days=1), hour, minute)
            if when is None or when <= now:
                raise ValueError('That local time is past or does not exist. Specify another time.')
            return parse_schedule({'type': 'once', 'at': when}, now)
        if re.fullmatch(r'in \d+(?:\.\d+)? (seconds?|minutes?|hours?|days?|weeks?)', phrase):
            when, error = memory.parse_when(phrase, now=now)
            if error:
                raise ValueError(error)
            return parse_schedule({'type': 'once', 'at': when}, now)
        try:
            return parse_schedule({'type': 'once', 'at': dt.datetime.fromisoformat(value.strip()).timestamp()}, now)
        except (ValueError, TypeError):
            raise ValueError('Use a future ISO timestamp, in 2 hours, tomorrow at 9am, every morning at 8, every weekday at 7am, or every 2 hours.') from None
    if schedule['type'] == 'calendar':
        schedule['label'] = '%s at %02d:%02d (PC local time)' % ('Days ' + ','.join(str(x) for x in schedule['days']), schedule['hour'], schedule['minute'])
    elif schedule['type'] == 'interval':
        schedule['label'] = 'Every %s seconds' % schedule['seconds']
    else:
        schedule['label'] = time.strftime('%Y-%m-%d %H:%M (PC local time)', time.localtime(schedule['at']))
    return schedule


def checkpoint(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('id'), str):
        raise ValueError('Invalid Phase 1 mission checkpoint.')
    allowed = {'version', 'id', 'title', 'objective', 'createdAt', 'updatedAt', 'status', 'currentStep', 'steps', 'evidenceSequence', 'evidence', 'failures', 'revisions', 'corrections', 'outcome', 'durable', 'pendingPermissions'}
    if set(value) - allowed or value.get('status') not in ('planning', 'ready', 'executing', 'verifying', 'waiting', 'waiting_approval', 'blocked', 'completed', 'failed', 'cancelled'):
        raise ValueError('Invalid mission fields or state.')
    pending_permissions = value.get('pendingPermissions', [])
    if not isinstance(pending_permissions, list) or len(pending_permissions) > 10 or any(not isinstance(p, str) or len(p) > 80 for p in pending_permissions):
        raise ValueError('Invalid bounded permission references.')
    steps, evidence = value.get('steps'), value.get('evidence')
    if not isinstance(steps, list) or len(steps) > 10 or not isinstance(evidence, list) or len(evidence) > 60:
        raise ValueError('Checkpoint exceeds bounded Phase 1 state.')
    if type(value.get('currentStep')) is not int or not 0 <= value['currentStep'] <= len(steps):
        raise ValueError('Invalid current step.')
    step_ids = [s.get('id') for s in steps if isinstance(s, dict)]
    if len(step_ids) != len(steps) or len(set(step_ids)) != len(steps) or any(not isinstance(s, str) or not s for s in step_ids):
        raise ValueError('Checkpoint step IDs must be unique.')
    ids = {e.get('id') for e in evidence if isinstance(e, dict)}
    if len(ids) != len(evidence) or any(not isinstance(e, str) or not e for e in ids):
        raise ValueError('Checkpoint evidence IDs must be unique.')
    for step in steps:
        if not isinstance(step, dict) or step.get('status') not in ('pending', 'running', 'verifying', 'completed', 'failed', 'blocked', 'skipped'):
            raise ValueError('Invalid checkpoint step.')
        if step['status'] == 'completed' and (not step.get('resultSummary') or not step.get('evidence') or any(e not in ids for e in step['evidence'])):
            raise ValueError('Completed step is missing evidence.')
    def scrub(item):
        if isinstance(item, str):
            return compact(item, 3000)
        if isinstance(item, list):
            return [scrub(x) for x in item]
        if isinstance(item, dict):
            if any(re.fullmatch(r'(?:thinking|reasoning|analysis|api_?key|access_?token|refresh_?token|authorization|password)', k, re.I) for k in item):
                raise ValueError('Private fields are not accepted.')
            return {k: scrub(v) for k, v in item.items()}
        return item
    encoded = json.dumps(scrub(value), separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > 60000:
        raise ValueError('Checkpoint too large; retain concise evidence only.')
    return encoded


class TaskStore:
    def __init__(self, path, clock=time.time, notice=memory.add_notice):
        self.path, self.clock, self.notice = os.fspath(path), clock, notice
        self.delivery_lock = threading.Lock()
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, SCHEMA_VERSION):
                raise ValueError('Unsupported task schema version; no data was changed.')
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, enabled INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','paused','cancelled')),
 schedule_type TEXT NOT NULL, schedule_data TEXT NOT NULL, next_run_at REAL,
 last_run_at REAL, run_policy TEXT NOT NULL, created_by TEXT NOT NULL,
 creation_key TEXT UNIQUE, timezone TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), status TEXT NOT NULL
 CHECK(status IN ('queued','claimed','running','waiting_approval','interrupted','completed','failed','cancelled')),
 occurrence_key TEXT UNIQUE NOT NULL, scheduled_at REAL NOT NULL, queued_at REAL NOT NULL,
 claimed_at REAL, started_at REAL, finished_at REAL, claimed_by TEXT, lease_until REAL,
 claim_key TEXT, mission_id TEXT, mission_snapshot TEXT, result_summary TEXT, failure_summary TEXT,
 attempt INTEGER NOT NULL DEFAULT 0, resume_requested INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_task_run ON task_runs(task_id)
 WHERE status IN ('queued','claimed','running','waiting_approval','interrupted');
CREATE TABLE IF NOT EXISTS task_events (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), run_id TEXT REFERENCES task_runs(id),
 kind TEXT NOT NULL, at REAL NOT NULL, summary TEXT NOT NULL, notice_sent INTEGER NOT NULL DEFAULT 0,
 UNIQUE(run_id,kind));
PRAGMA user_version=1; COMMIT;''')

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def event(self, db, task_id, run_id, kind, summary):
        db.execute('INSERT OR IGNORE INTO task_events(task_id,run_id,kind,at,summary) VALUES(?,?,?,?,?)', (task_id, run_id, kind, self.clock(), compact(summary)))

    def task(self, db, selector):
        selector = checked_text(selector, 'task ID or exact title', 160)
        rows = db.execute('SELECT * FROM tasks WHERE id=? OR lower(title)=lower(?)', (selector, selector)).fetchall()
        if len(rows) != 1:
            raise ValueError('Task not found.' if not rows else 'Task title is ambiguous. Choose a task ID.')
        return rows[0]

    @staticmethod
    def view(row, private=False):
        if row is None:
            return None
        result = dict(row)
        if 'schedule_data' in result:
            result['schedule'] = json.loads(result.pop('schedule_data'))
        if 'mission_snapshot' in result:
            raw = result.pop('mission_snapshot')
            if private:
                result['mission_snapshot'] = json.loads(raw) if raw else None
            result.pop('claim_key', None)
        return result

    def queue(self, db, task, occurrence, key):
        existing = db.execute("SELECT * FROM task_runs WHERE task_id=? AND status IN ('queued','claimed','running','waiting_approval','interrupted')", (task['id'],)).fetchone()
        if existing:
            return existing
        db.execute("INSERT OR IGNORE INTO task_runs(id,task_id,status,occurrence_key,scheduled_at,queued_at) VALUES(?,?,'queued',?,?,?)", (str(uuid.uuid4()), task['id'], key, occurrence, self.clock()))
        return db.execute('SELECT * FROM task_runs WHERE occurrence_key=?', (key,)).fetchone()

    def tick(self):
        now = self.clock()
        with self.transaction() as db:
            for run in db.execute("SELECT * FROM task_runs WHERE status IN ('claimed','running','waiting_approval') AND lease_until<=?", (now,)).fetchall():
                db.execute("UPDATE task_runs SET status='interrupted',lease_until=NULL,failure_summary=? WHERE id=?", ('Worker lease expired; explicit resume or restart required.', run['id']))
                self.event(db, run['task_id'], run['id'], 'interrupted', 'Worker lease expired; explicit resume or restart required.')
            for task in db.execute("SELECT * FROM tasks WHERE status='active' AND enabled=1 AND next_run_at<=?", (now,)).fetchall():
                schedule = json.loads(task['schedule_data'])
                occurrence = latest_due(schedule, task['next_run_at'], now)
                self.queue(db, task, occurrence, '%s:scheduled:%.3f' % (task['id'], occurrence))
                db.execute('UPDATE tasks SET next_run_at=?,updated_at=? WHERE id=?', (next_occurrence(schedule, now), now, task['id']))
        self.flush_notices()

    def flush_notices(self):
        # The durable event is a delivery marker, not a second notification store.
        with self.delivery_lock:
            with self.connect() as db:
                pending = db.execute("SELECT e.*,t.title FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE e.notice_sent=0 AND e.run_id IS NOT NULL AND e.kind IN ('completed','failed','interrupted','waiting_approval','cancelled') ORDER BY e.id").fetchall()
            for event in pending:
                try:
                    self.notice('%s: %s. %s' % (event['title'], event['kind'].replace('_', ' '), event['summary']), kind='task', source='task:' + event['task_id'] + ':run:' + event['run_id'], dedupe_key='task:' + event['run_id'] + ':' + event['kind'])
                    with self.transaction() as db:
                        db.execute('UPDATE task_events SET notice_sent=1 WHERE id=?', (event['id'],))
                except (OSError, ValueError):
                    break  # Retry delivery later without replaying execution.

    def owner(self, db, data):
        run = db.execute('SELECT * FROM task_runs WHERE id=?', (checked_text(data.get('run_id'), 'run ID', 80),)).fetchone()
        if not run or run['claimed_by'] != data.get('worker_id') or run['claim_key'] != data.get('claim_key'):
            raise ValueError('Run is not owned by this worker claim.')
        if run['status'] in TERMINAL_RUNS:
            return run
        if run['status'] not in OWNED_RUNS or run['lease_until'] <= self.clock():
            raise ValueError('Run lease expired or was interrupted. Explicit resume is required.')
        return run

    def command(self, data):
        try:
            result = self._command(data)
            self.flush_notices()
            return {'ok': True, **result}
        except (ValueError, TypeError, OverflowError, sqlite3.IntegrityError) as error:
            return {'ok': False, 'error': compact(error)}

    def _command(self, data):
        fields = {
            'create': {'title', 'objective', 'schedule', 'run_policy', 'request_key'},
            'list': set(), 'get': {'task'}, 'pause': {'task'}, 'resume': {'task'}, 'cancel': {'task'},
            'run_now': {'task', 'request_key'}, 'history': {'task'}, 'due': set(),
            'claim': {'worker_id', 'run_id'}, 'get_run': {'run_id'},
            'cancel_run': {'run_id'}, 'resume_run': {'run_id'}, 'restart_run': {'run_id', 'request_key'},
            'heartbeat': {'run_id', 'worker_id', 'claim_key', 'mission', 'status'},
            'check_claim': {'run_id', 'worker_id', 'claim_key'},
            'complete': {'run_id', 'worker_id', 'claim_key', 'mission', 'summary'},
            'fail': {'run_id', 'worker_id', 'claim_key', 'mission', 'summary'},
            'interrupt': {'run_id', 'worker_id', 'claim_key', 'mission', 'summary'}}
        if not isinstance(data, dict) or data.get('action') not in fields or set(data) - fields[data['action']] - {'action'}:
            raise ValueError('Unknown action or unsupported task fields.')
        action, now = data['action'], self.clock()
        if action in ('due', 'claim', 'list', 'get_run'):
            self.tick()
        with self.transaction() as db:
            if action == 'create':
                title = checked_text(data.get('title'), 'title', 160)
                objective = checked_text(data.get('objective'), 'trusted user objective', 2400)
                schedule = parse_schedule(data.get('schedule'), now)
                policy = data.get('run_policy', 'read_only')
                if policy not in ('read_only', 'reviewed_writes'):
                    raise ValueError('Run policy must be read_only or reviewed_writes.')
                key = checked_text(data['request_key'], 'request key', 120) if data.get('request_key') else None
                prior = db.execute('SELECT * FROM tasks WHERE creation_key=?', (key,)).fetchone() if key else None
                if prior:
                    if prior['title'] != title or prior['objective'] != objective or prior['run_policy'] != policy:
                        raise ValueError('Request key already belongs to a different task.')
                    return {'task': self.view(prior)}
                task_id = str(uuid.uuid4())
                first = schedule['at'] if schedule['type'] == 'once' else next_occurrence(schedule, now)
                db.execute("INSERT INTO tasks(id,title,objective,created_at,updated_at,enabled,status,schedule_type,schedule_data,next_run_at,run_policy,created_by,creation_key,timezone) VALUES(?,?,?,?,?,1,'active',?,?,?,?,?,?,?)", (task_id, title, objective, now, now, schedule['type'], json.dumps(schedule), first, policy, 'user', key, '/'.join(time.tzname)))
                self.event(db, task_id, None, 'created', 'Trusted objective scheduled.')
                return {'task': self.view(self.task(db, task_id))}
            if action == 'list':
                return {'tasks': [self.view(t) for t in db.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200')], 'runs': [self.view(r) for r in db.execute('SELECT * FROM task_runs ORDER BY queued_at DESC LIMIT 100')]}
            if action in ('get', 'pause', 'resume', 'cancel', 'run_now', 'history'):
                task = self.task(db, data.get('task'))
                if action in ('pause', 'resume', 'cancel'):
                    if task['status'] == 'cancelled' and action != 'cancel':
                        raise ValueError('Cancelled tasks cannot be resumed; create a new task.')
                    state = {'pause': 'paused', 'resume': 'active', 'cancel': 'cancelled'}[action]
                    next_at = task['next_run_at']
                    if action == 'resume' and task['schedule_type'] != 'once':
                        next_at = next_occurrence(json.loads(task['schedule_data']), now)
                    db.execute('UPDATE tasks SET status=?,enabled=?,next_run_at=?,updated_at=? WHERE id=?', (state, int(state == 'active'), next_at, now, task['id']))
                    self.event(db, task['id'], None, state, 'Future scheduling changed; existing runs were not cancelled.')
                if action == 'run_now':
                    if task['status'] == 'cancelled':
                        raise ValueError('Task is cancelled.')
                    # Materialize a due occurrence first, avoiding a near-schedule duplicate.
                    if task['status'] == 'active' and task['next_run_at'] is not None and task['next_run_at'] <= now:
                        schedule = json.loads(task['schedule_data'])
                        occurrence = latest_due(schedule, task['next_run_at'], now)
                        self.queue(db, task, occurrence, '%s:scheduled:%.3f' % (task['id'], occurrence))
                        db.execute('UPDATE tasks SET next_run_at=? WHERE id=?', (next_occurrence(schedule, now), task['id']))
                    key = checked_text(data['request_key'], 'request key', 120) if data.get('request_key') else str(uuid.uuid4())
                    return {'run': self.view(self.queue(db, task, now, task['id'] + ':manual:' + key))}
                if action == 'history':
                    return {'runs': [self.view(r) for r in db.execute('SELECT * FROM task_runs WHERE task_id=? ORDER BY queued_at DESC LIMIT 60', (task['id'],))]}
                return {'task': self.view(self.task(db, task['id']))}
            if action == 'due':
                return {'runs': [self.view(r) for r in db.execute("SELECT * FROM task_runs WHERE status='queued' ORDER BY queued_at LIMIT 20")]}
            if action == 'claim':
                worker = checked_text(data.get('worker_id'), 'worker ID', 120)
                run = db.execute("SELECT * FROM task_runs WHERE status='queued' AND (? IS NULL OR id=?) ORDER BY queued_at LIMIT 1", (data.get('run_id'), data.get('run_id'))).fetchone()
                if not run:
                    return {'run': None}
                claim_key = str(uuid.uuid4())
                db.execute("UPDATE task_runs SET status='claimed',claimed_by=?,claim_key=?,claimed_at=?,lease_until=?,attempt=attempt+1 WHERE id=? AND status='queued'", (worker, claim_key, now, now + LEASE_SECONDS, run['id']))
                result = self.view(db.execute('SELECT * FROM task_runs WHERE id=?', (run['id'],)).fetchone(), private=True)
                result['claim_key'] = claim_key
                return {'run': result, 'task': self.view(self.task(db, run['task_id']))}
            if action in ('get_run', 'cancel_run', 'resume_run', 'restart_run'):
                run = db.execute('SELECT * FROM task_runs WHERE id=?', (checked_text(data.get('run_id'), 'run ID', 80),)).fetchone()
                if not run:
                    raise ValueError('Run not found.')
                if action == 'cancel_run' and run['status'] not in TERMINAL_RUNS:
                    db.execute("UPDATE task_runs SET status='cancelled',finished_at=?,lease_until=NULL,failure_summary='Cancelled by user.' WHERE id=?", (now, run['id']))
                    self.event(db, run['task_id'], run['id'], 'cancelled', 'Cancelled by user.')
                if action == 'resume_run':
                    if run['status'] not in ('interrupted', 'failed') or not run['mission_snapshot'] or not json.loads(run['mission_snapshot']).get('steps'):
                        raise ValueError('No resumable checkpoint. Explicitly restart this run instead.')
                    db.execute("UPDATE task_runs SET status='queued',claimed_by=NULL,claim_key=NULL,lease_until=NULL,finished_at=NULL,resume_requested=1 WHERE id=?", (run['id'],))
                if action == 'restart_run':
                    if run['status'] not in TERMINAL_RUNS + ('interrupted',):
                        raise ValueError('Cancel the active run before restarting.')
                    if run['status'] == 'interrupted':
                        db.execute("UPDATE task_runs SET status='cancelled',finished_at=? WHERE id=?", (now, run['id']))
                    task = self.task(db, run['task_id'])
                    key = checked_text(data['request_key'], 'request key', 120) if data.get('request_key') else str(uuid.uuid4())
                    return {'run': self.view(self.queue(db, task, now, task['id'] + ':restart:' + key))}
                return {'run': self.view(db.execute('SELECT * FROM task_runs WHERE id=?', (run['id'],)).fetchone(), private=True)}
            run = self.owner(db, data)
            if run['status'] in TERMINAL_RUNS:
                return {'run': self.view(run), 'duplicate': True}
            if action == 'check_claim':
                return {'run': self.view(run)}
            submitted = data.get('mission')
            if isinstance(submitted, dict):
                submitted = dict(submitted, durable={'taskId': run['task_id'], 'runId': run['id']})
            saved = checkpoint(submitted)
            mission = json.loads(saved) if saved else None
            if mission and run['mission_id'] and run['mission_id'] != mission['id']:
                raise ValueError('Mission identity cannot change within a resumed run.')
            if action == 'heartbeat':
                state = data.get('status', 'running')
                if state not in ('running', 'waiting_approval'):
                    raise ValueError('Invalid worker state.')
                db.execute('UPDATE task_runs SET status=?,lease_until=?,started_at=COALESCE(started_at,?),mission_id=COALESCE(?,mission_id),mission_snapshot=COALESCE(?,mission_snapshot) WHERE id=?', (state, now + LEASE_SECONDS, now, mission['id'] if mission else None, saved, run['id']))
                if state == 'waiting_approval':
                    self.event(db, run['task_id'], run['id'], state, 'Waiting for existing write approval. No write was auto-approved.')
            else:
                state = {'complete': 'completed', 'fail': 'failed', 'interrupt': 'interrupted'}[action]
                if state == 'completed' and (not mission or mission['status'] != 'completed' or not mission['steps'] or any(s['status'] != 'completed' for s in mission['steps'])):
                    raise ValueError('Completion requires a verified Phase 1 receipt.')
                summary = compact(data.get('summary') or (mission or {}).get('outcome') or 'No verified outcome.', 1200)
                db.execute('UPDATE task_runs SET status=?,finished_at=?,lease_until=NULL,mission_id=COALESCE(?,mission_id),mission_snapshot=COALESCE(?,mission_snapshot),result_summary=?,failure_summary=? WHERE id=?', (state, now, mission['id'] if mission else None, saved, summary if state == 'completed' else None, summary if state != 'completed' else None, run['id']))
                db.execute('UPDATE tasks SET last_run_at=?,updated_at=? WHERE id=?', (now, now, run['task_id']))
                self.event(db, run['task_id'], run['id'], state, summary)
            return {'run': self.view(db.execute('SELECT * FROM task_runs WHERE id=?', (run['id'],)).fetchone())}


class Scheduler:
    def __init__(self, store):
        self.store, self.stopped = store, threading.Event()
        self.thread = threading.Thread(target=self.loop, name='jarvis-task-scheduler', daemon=True)

    def start(self):
        self.thread.start()

    def loop(self):
        while not self.stopped.is_set():
            try:
                self.store.tick()
            except (OSError, sqlite3.Error, ValueError):
                pass  # Ordinary chat remains available; later ticks retry storage.
            self.stopped.wait(10)

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=6)
