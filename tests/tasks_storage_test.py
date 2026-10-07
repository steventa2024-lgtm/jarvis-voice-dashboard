"""Deterministic storage/schedule/lease/notice tests; no live integrations."""
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'J.A.R.V.I.S. Dashboard - Copy'))
import jarvis_tasks as tasks
import jarvis_memory as memory


class Tasks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jarvis-task-test-')
        self.addCleanup(self.temp.cleanup)
        self.now = tasks.wall(dt.date(2026, 10, 7), 7, 0)
        self.notices = []
        self.store = tasks.TaskStore(Path(self.temp.name) / 'tasks.db', clock=lambda: self.now,
                                     notice=lambda text, **kw: self.notices.append((text, kw)))

    def command(self, action, **data):
        result = self.store.command({'action': action, **data})
        self.assertTrue(result['ok'], result)
        return result

    def create(self, **extra):
        return self.command('create', title='Morning Brief', objective='Read calendar and weather; summarize observed results.',
                            schedule=extra.pop('schedule', 'every morning at 8'), **extra)['task']

    def claim(self):
        task = self.create(schedule='in 2 hours')
        self.now += 7200
        run = self.command('claim', worker_id='worker-a')['run']
        self.assertEqual(run['task_id'], task['id'])
        return {k: v for k, v in {'run_id': run['id'], 'worker_id': 'worker-a', 'claim_key': run['claim_key']}.items()}

    def mission(self, state='completed'):
        return {'version': 1, 'id': 'mission-fixture', 'title': 'Brief', 'objective': 'Read and summarize', 'status': state,
                'currentStep': 2 if state == 'completed' else 1,
                'durable': {'taskId': 'test', 'runId': 'test'},
                'steps': [{'id': 'read', 'title': 'Read', 'status': 'completed', 'resultSummary': 'Actual read', 'evidence': ['e1']},
                          {'id': 'summarize', 'title': 'Summarize', 'status': 'completed' if state == 'completed' else 'running', 'resultSummary': 'Observed output' if state == 'completed' else '', 'evidence': ['e1'] if state == 'completed' else []}],
                'evidence': [{'id': 'e1', 'tool': 'google:agenda', 'success': True, 'summary': 'Observed calendar'}]}

    def test_create_list_get_reopen_and_schema_idempotent(self):
        task = self.create(request_key='stable-request')
        self.assertEqual(self.create(request_key='stable-request')['id'], task['id'])
        self.store = tasks.TaskStore(self.store.path, clock=lambda: self.now, notice=self.store.notice)
        self.assertEqual(self.command('get', task='Morning Brief')['task']['id'], task['id'])
        self.assertEqual(len(self.command('list')['tasks']), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_pause_resume_cancel_only_future(self):
        task = self.create()
        self.command('run_now', task=task['id'])
        self.command('pause', task=task['id'])
        self.now += 2 * 86400
        self.command('due')
        self.assertEqual(len(self.command('history', task=task['id'])['runs']), 1)
        resumed = self.command('resume', task=task['id'])['task']
        self.assertGreater(resumed['next_run_at'], self.now)
        self.command('cancel', task=task['id'])
        self.assertEqual(self.command('history', task=task['id'])['runs'][0]['status'], 'queued')
        self.assertFalse(self.store.command({'action': 'resume', 'task': task['id']})['ok'])

    def test_one_time_is_materialized_once(self):
        task = self.create(schedule={'type': 'once', 'at': self.now + 10})
        self.now += 10
        for _ in range(5):
            self.command('due')
        self.assertEqual(len(self.command('history', task=task['id'])['runs']), 1)
        self.assertIsNone(self.command('get', task=task['id'])['task']['next_run_at'])

    def test_daily_calendar_no_completion_drift(self):
        task = self.create()
        self.now += 3 * 3600
        run = self.command('claim', worker_id='a')['run']
        next_at = self.command('get', task=task['id'])['task']['next_run_at']
        self.assertEqual(time.localtime(next_at).tm_hour, 8)
        self.now += 20
        self.command('complete', run_id=run['id'], worker_id='a', claim_key=run['claim_key'], mission=self.mission(), summary='Actual checks complete')
        self.assertEqual(self.command('get', task=task['id'])['task']['next_run_at'], next_at)

    def test_iso_utc_timestamp_preserves_explicit_zone(self):
        value = dt.datetime.fromtimestamp(self.now + 3600, dt.timezone.utc).isoformat().replace('+00:00', 'Z')
        self.assertEqual(tasks.parse_schedule(value, self.now)['at'], self.now + 3600)
    def test_interval_and_weekday_occurrences(self):
        interval = tasks.parse_schedule('every 2 hours', self.now)
        self.assertEqual(tasks.next_occurrence(interval, self.now + 7300), self.now + 14400)
        for phrase, expected in [('every Monday at 9am', [0]), ('every weekday at 7am', list(range(5)) )]:
            schedule = tasks.parse_schedule(phrase, self.now)
            self.assertEqual(schedule['days'], expected)
            self.assertIn(time.localtime(tasks.next_occurrence(schedule, self.now)).tm_wday, expected)

    def test_relative_tomorrow_tonight_and_time_validation(self):
        self.assertEqual(tasks.parse_schedule('in 2 hours', self.now)['at'], self.now + 7200)
        self.assertEqual(time.localtime(tasks.parse_schedule('tonight at 8', self.now)['at']).tm_hour, 20)
        tomorrow = time.localtime(tasks.parse_schedule('tomorrow at 9am', self.now)['at'])
        self.assertEqual(tomorrow.tm_mday, 8)
        for phrase in ['every day at 8', 'tomorrow at 25:00', 'tomorrow at 13pm', 'every day at 8:70am', 'someday', 'every 0 hours']:
            with self.assertRaises(ValueError, msg=phrase):
                tasks.parse_schedule(phrase, self.now)

    def test_missed_occurrences_coalesce_and_restart_preserves(self):
        task = self.create()
        self.now += 60 * 86400
        self.store.tick()
        history = self.command('history', task=task['id'])['runs']
        self.assertEqual(len(history), 1)
        self.assertLess(self.now - history[0]['scheduled_at'], 86400)
        self.store = tasks.TaskStore(self.store.path, clock=lambda: self.now, notice=self.store.notice)
        self.assertEqual(self.command('history', task=task['id'])['runs'], history)

    def test_two_workers_atomic_claim(self):
        self.create(schedule={'type': 'once', 'at': self.now + 1})
        self.now += 1
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda worker: self.store.command({'action': 'claim', 'worker_id': worker}), ['a', 'b']))
        self.assertTrue(all(result['ok'] for result in responses), responses)
        self.assertEqual(sum(result['run'] is not None for result in responses), 1)

    def test_run_now_near_due_has_one_run_and_schedule_unchanged(self):
        task = self.create()
        first = self.command('run_now', task=task['id'], request_key='manual')['run']
        self.assertEqual(self.command('get', task=task['id'])['task']['next_run_at'], task['next_run_at'])
        self.now += 3600
        for _ in range(4):
            self.assertEqual(self.command('run_now', task=task['id'])['run']['id'], first['id'])
        self.assertEqual(len(self.command('history', task=task['id'])['runs']), 1)

    def test_expiry_is_interrupted_and_never_reclaimed(self):
        owner = self.claim()
        self.command('heartbeat', **owner, mission=self.mission('executing'))
        self.now += tasks.LEASE_SECONDS + 1
        self.command('due')
        self.assertEqual(self.command('get_run', run_id=owner['run_id'])['run']['status'], 'interrupted')
        self.assertIsNone(self.command('claim', worker_id='b')['run'])
        self.assertFalse(self.store.command({'action': 'complete', **owner, 'mission': self.mission()})['ok'])

    def test_explicit_resume_keeps_mission_and_fences_old_claim(self):
        owner = self.claim()
        mission = self.mission('executing')
        self.command('heartbeat', **owner, mission=mission)
        self.command('interrupt', **owner, summary='Closed browser')
        self.command('resume_run', run_id=owner['run_id'])
        resumed = self.command('claim', worker_id='b')['run']
        self.assertEqual(resumed['mission_snapshot']['steps'][0]['status'], 'completed')
        self.assertEqual(resumed['mission_id'], mission['id'])
        self.assertNotEqual(resumed['claim_key'], owner['claim_key'])
        self.assertFalse(self.store.command({'action': 'heartbeat', **owner})['ok'])

    def test_resume_without_checkpoint_requires_explicit_restart(self):
        owner = self.claim()
        self.command('interrupt', **owner)
        self.assertFalse(self.store.command({'action': 'resume_run', 'run_id': owner['run_id']})['ok'])
        restarted = self.command('restart_run', run_id=owner['run_id'], request_key='retry')['run']
        self.assertNotEqual(restarted['id'], owner['run_id'])

    def test_heartbeat_approval_and_rejection(self):
        owner = self.claim()
        self.now += 20
        self.command('heartbeat', **owner, status='waiting_approval', mission=self.mission('waiting_approval'))
        run = self.command('get_run', run_id=owner['run_id'])['run']
        self.assertEqual(run['status'], 'waiting_approval')
        self.assertEqual(run['lease_until'], self.now + tasks.LEASE_SECONDS)
        self.assertEqual(len(self.notices), 1)
        self.command('heartbeat', **owner, status='waiting_approval')
        self.assertEqual(len(self.notices), 1)
        self.command('fail', **owner, mission=self.mission('blocked'), summary='Write rejected; nothing applied.')
        self.assertEqual(self.command('get_run', run_id=owner['run_id'])['run']['status'], 'failed')

    def test_completion_exactly_once_and_compact_history(self):
        owner = self.claim()
        self.command('complete', **owner, mission=self.mission(), summary='Calendar checked; brief ready.')
        self.assertTrue(self.command('complete', **owner, mission=self.mission())['duplicate'])
        self.assertEqual(len(self.notices), 1)
        run = self.command('list')['runs'][0]
        self.assertNotIn('mission_snapshot', run)
        self.assertNotIn('claim_key', run)
        self.assertEqual(run['status'], 'completed')

    def test_unverified_completion_is_refused(self):
        owner = self.claim()
        bad = self.mission(); bad['steps'][0]['evidence'] = []
        self.assertFalse(self.store.command({'action': 'complete', **owner, 'mission': bad})['ok'])
        self.assertFalse(self.store.command({'action': 'complete', **owner, 'mission': self.mission('executing')})['ok'])

    def test_cancel_run_stops_claim_without_cancelling_schedule(self):
        owner = self.claim()
        self.command('cancel_run', run_id=owner['run_id'])
        self.assertEqual(self.command('check_claim', **owner)['run']['status'], 'cancelled')
        self.assertEqual(self.command('list')['tasks'][0]['status'], 'active')
        self.assertEqual(self.command('heartbeat', **owner)['run']['status'], 'cancelled')

    def test_invalid_fields_injection_ambiguity_and_redaction(self):
        for data in [[], {'action': 'sql'}, {'action': 'list', 'db_path': 'elsewhere'}, {'action': 'create', 'title': 'x', 'objective': 'password=secret', 'schedule': 'in 2 hours'}]:
            self.assertFalse(self.store.command(data)['ok'])
        self.create(); self.create()
        self.assertFalse(self.store.command({'action': 'get', 'task': 'Morning Brief'})['ok'])
        self.assertEqual(tasks.compact('password=secret'), '[redacted]')

    def test_real_notice_dedup_even_seen_after_delivery_crash(self):
        old = memory.STORE
        memory.STORE = str(Path(self.temp.name) / 'memory.json')
        self.addCleanup(setattr, memory, 'STORE', old)
        self.store.notice = memory.add_notice
        owner = self.claim()
        self.command('complete', **owner, mission=self.mission(), summary='Ready')
        memory.mark_seen()
        with self.store.transaction() as db:
            db.execute('UPDATE task_events SET notice_sent=0')
        self.store.flush_notices()
        self.assertEqual(len(memory.list_notices()['notices']), 1)

    def test_scheduler_clean_shutdown_and_no_replay(self):
        scheduler = tasks.Scheduler(self.store)
        scheduler.start(); scheduler.stop()
        self.assertFalse(scheduler.thread.is_alive())

    def test_nonexistent_calendar_time_is_skipped(self):
        schedule = {'type': 'calendar', 'hour': 8, 'minute': 0, 'days': list(range(7))}
        actual_wall = tasks.wall
        today = dt.date(*time.localtime(self.now)[:3])
        with patch.object(tasks, 'wall', side_effect=lambda day, h, m: None if day == today else actual_wall(day, h, m)):
            result = tasks.next_occurrence(schedule, self.now)
        self.assertEqual(time.localtime(result).tm_mday, today.day + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
