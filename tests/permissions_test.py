"""Deterministic broker contracts; every runtime store is disposable."""
import concurrent.futures
import contextlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'J.A.R.V.I.S. Dashboard - Copy'))
from jarvis_permissions import PermissionStore, classify, MAP, matches, target

def action(tool='files',name='write',**args):
    return {'kind':'route','tool':tool,'input':dict(action=name,**args)}

class Broker(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'permissions.db'; self.now=1700000000
        self.store=PermissionStore(self.path,clock=lambda:self.now); self.ui=self.store.session()
        self.store.set_context(self.ui,{'user_text':'Fix my fixture project','background':False,'mission_id':'mission','task_run_id':''})
        self.write=action(project='PulseUI',path='src/App.tsx',content='safe fixture')
    def tearDown(self): self.temp.cleanup()
    def command(self,op,**data): return self.store.command(dict(action=op,**data),self.ui)
    def approve(self,call=None,choice='allow_once'):
        call=call or self.write; check=self.store.check(call,self.ui); self.assertEqual(check['decision'],'ASK')
        ident=check['approval']['id']; self.store.resolve(ident,choice,self.ui); return self.store.recover(ident,self.ui)
    def policy(self,cap='WRITE_FILE',effect='ALLOW',scope='pulseui',**data):
        return self.command('create_policy',capability=cap,effect=effect,scope_type='target',scope_value=scope,**data)
    def test_low_read_allow_and_audit(self):
        call=action(name='read',project='PulseUI',path='a.txt'); result=self.store.check(call,self.ui); self.assertEqual(result['decision'],'ALLOW'); self.assertTrue(self.store.consume(call,result['receipt'],self.ui)); self.assertEqual(self.command('history')['events'][0]['source'],'receipt_consumed')
    def test_ask_does_not_authorize_until_ui_resolution(self):
        result=self.store.check(self.write,self.ui); self.assertEqual(result['decision'],'ASK'); self.assertFalse(self.store.consume(self.write,None,self.ui)); self.assertEqual(result['approval']['risk'],'high')
    def test_allow_once_exact_action(self):
        approved=self.approve(); self.assertEqual(approved['action'],self.write); self.assertTrue(self.store.consume(self.write,approved['receipt'],self.ui))
    def test_fingerprint_target_mismatch(self):
        approved=self.approve(); other=action(project='Other',path='src/App.tsx',content='safe fixture'); self.assertFalse(self.store.consume(other,approved['receipt'],self.ui)); self.assertTrue(self.store.consume(self.write,approved['receipt'],self.ui))
    def test_fingerprint_content_mismatch(self):
        approved=self.approve(); other=action(project='PulseUI',path='src/App.tsx',content='changed arguments'); self.assertFalse(self.store.consume(other,approved['receipt'],self.ui))
    def test_receipt_single_use(self):
        approved=self.approve(); self.assertTrue(self.store.consume(self.write,approved['receipt'],self.ui)); self.assertFalse(self.store.consume(self.write,approved['receipt'],self.ui))
    def test_two_workers_only_one_consumes(self):
        token=self.approve()['receipt']
        with concurrent.futures.ThreadPoolExecutor(2) as pool: outcomes=list(pool.map(lambda _:self.store.consume(self.write,token,self.ui),range(2)))
        self.assertEqual(outcomes.count(True),1)
    def test_receipt_expiration(self):
        token=self.approve()['receipt']; self.now+=61; self.assertFalse(self.store.consume(self.write,token,self.ui))
    def test_approval_expiration(self):
        result=self.store.check(self.write,self.ui); self.now+=601
        with self.assertRaises(ValueError): self.store.resolve(result['approval']['id'],'allow_once',self.ui)
        self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY')
    def test_session_project_grant_and_other_project(self):
        self.approve(choice='allow_session'); self.assertEqual(self.store.check(action(project='PulseUI',path='b.txt',content='b'),self.ui)['decision'],'ALLOW'); self.assertEqual(self.store.check(action(project='PulseUI-evil',path='b.txt',content='b'),self.ui)['decision'],'ASK')
    def test_session_does_not_survive_page_or_server_restart(self):
        self.policy(persistence='session'); second=self.store.session(); self.assertEqual(self.store.check(self.write,second)['decision'],'ASK'); restarted=PermissionStore(self.path,clock=lambda:self.now); self.assertEqual(restarted.check(self.write,restarted.session())['decision'],'ASK')
    def test_persistent_target_grant_after_restart(self):
        self.policy(cap='CONTROL_APP',scope='spotify'); restarted=PermissionStore(self.path,clock=lambda:self.now); ui=restarted.session(); restarted.set_context(ui,{'background':True}); self.assertEqual(restarted.check(action(tool='control_interface',name='open_app',query='NotSpotify'),ui)['decision'],'ASK'); self.assertEqual(restarted.check(action(tool='control_interface',name='play_pause'),ui)['decision'],'ASK'); self.policy(cap='CONTROL_SPOTIFY',scope='spotify'); again=PermissionStore(self.path,clock=lambda:self.now); ui=again.session(); again.set_context(ui,{'background':True}); self.assertEqual(again.check(action(tool='spotify',name='pause'),ui)['decision'],'ALLOW')
    def test_explicit_deny_overrides_session_allow(self):
        self.policy(persistence='session'); self.policy(effect='DENY',scope='pulseui/src'); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY'); self.assertEqual(self.command('list')['approvals'],[])
    def test_global_deny_overrides_specific_allow(self):
        self.policy(); self.command('create_policy',capability='WRITE_FILE',effect='DENY',scope_type='global'); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY')
    def test_revoke_invalidates_unused_receipts(self):
        policy=self.policy(); receipt=self.store.check(self.write,self.ui)['receipt']; self.command('delete_policy',id=policy['id']); self.assertFalse(self.store.consume(self.write,receipt,self.ui)); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'ASK')
    def test_change_allow_to_ask_and_deny(self):
        policy=self.policy(); changed=self.command('update_policy',id=policy['id'],effect='ASK'); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'ASK'); self.command('update_policy',id=changed['id'],effect='DENY'); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY')
    def test_unknown_and_unavailable_fail_safe(self):
        self.assertEqual(self.store.check(action(tool='mystery',name='surprise'),self.ui)['decision'],'DENY'); self.assertNotIn('delete',MAP['files'])
        for cap in ['DELETE_FILE','INSTALL_PACKAGE','PAYMENT','PURCHASE','SEND_EMAIL']:
            with self.assertRaises(ValueError): self.policy(cap=cap)
    def test_background_write_requires_policy_and_approval(self):
        self.store.set_context(self.ui,{'background':True,'run_policy':'reviewed_writes','mission_id':'mission','task_run_id':'run'})
        self.assertEqual(self.store.check(self.write,self.ui)['decision'],'ASK'); approved=self.approve(); self.assertTrue(self.store.consume(self.write,approved['receipt'],self.ui)); self.policy(); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'ALLOW')
    def test_read_only_background_boundary_cannot_be_overridden(self):
        self.policy(); self.store.set_context(self.ui,{'background':True,'run_policy':'read_only'}); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY')
    def test_background_task_management_never_allowed(self):
        self.store.set_context(self.ui,{'background':True}); self.assertEqual(self.store.check(action(tool='tasks',name='create'),self.ui)['decision'],'DENY')
    def test_explicit_launch_and_lock_intent(self):
        self.store.set_context(self.ui,{'user_text':'Open Spotify','background':False}); self.assertEqual(self.store.check(action(tool='control_interface',name='open_app',query='spotify'),self.ui)['decision'],'ALLOW')
        self.store.set_context(self.ui,{'user_text':'lock my computer','background':False}); self.assertEqual(self.store.check(action(tool='control_interface',name='lock_screen'),self.ui)['decision'],'ALLOW')
        self.store.set_context(self.ui,{'user_text':'go to sleep','background':False}); self.assertEqual(self.store.check(action(tool='control_interface',name='lock_screen'),self.ui)['decision'],'ASK')
    def test_denial_antispam_same_fingerprint(self):
        result=self.store.check(self.write,self.ui); self.store.resolve(result['approval']['id'],'deny_once',self.ui); self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY'); self.assertEqual(self.command('list')['approvals'],[])
    def test_pending_approval_and_exact_recovery_survive_restart(self):
        result=self.store.check(self.write,self.ui); restarted=PermissionStore(self.path,clock=lambda:self.now); ui=restarted.session(); restarted.set_context(ui,self.store.context(self.ui)); self.assertEqual(restarted.command({'action':'list'},ui)['approvals'][0]['id'],result['approval']['id']); restarted.resolve(result['approval']['id'],'allow_once',ui); recovered=restarted.recover(result['approval']['id'],ui); self.assertEqual(recovered['action'],self.write)
    def test_mission_and_run_binding(self):
        approved=self.approve(); self.store.set_context(self.ui,{'mission_id':'another','background':False}); self.assertFalse(self.store.consume(self.write,approved['receipt'],self.ui))
    def test_sensitive_arguments_never_persist_or_log(self):
        sensitive=action(project='PulseUI',path='secret.txt',content='password=fixture-secret'); result=self.store.check(sensitive,self.ui); self.store.resolve(result['approval']['id'],'allow_once',self.ui)
        with self.assertRaises(ValueError): self.store.recover(result['approval']['id'],self.ui)
        recovered=self.store.recover(result['approval']['id'],self.ui,sensitive); self.assertTrue(self.store.consume(sensitive,recovered['receipt'],self.ui))
        with contextlib.closing(sqlite3.connect(self.path)) as db: dump='\n'.join(db.iterdump())
        self.assertNotIn('fixture-secret',dump)
        json_secret=action(project='PulseUI',path='settings.json',content='{"password": "fixture-json-secret"}')
        result=self.store.check(json_secret,self.ui); self.store.resolve(result['approval']['id'],'allow_once',self.ui)
        with self.assertRaises(ValueError): self.store.recover(result['approval']['id'],self.ui)
        with contextlib.closing(sqlite3.connect(self.path)) as db: dump='\n'.join(db.iterdump())
        self.assertNotIn('fixture-json-secret',dump)
    def test_scope_boundaries_and_normalization(self):
        self.assertEqual(target('PulseUI\\src/../main.py'),'pulseui/main.py'); self.assertFalse(matches('pulseui-evil/a','pulseui','target')); self.assertFalse(matches('pulseui/../other/a','pulseui','exact'))
    def test_invalid_schema_and_inputs_preserve_data(self):
        with self.assertRaises(ValueError): self.policy(effect='MAYBE')
        with self.assertRaises(ValueError): self.command('check',proposed={'tool':'files','input':{},'policy':'allow'})
        with self.assertRaises(ValueError): self.store.resolve('missing','allow_once',self.ui)
        with contextlib.closing(sqlite3.connect(self.path)) as db: db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError): PermissionStore(self.path)
    def test_no_global_high_risk_allow(self):
        with self.assertRaises(ValueError): self.command('create_policy',capability='WRITE_FILE',effect='ALLOW',scope_type='global')
    def test_internal_review_actions_are_not_model_tools(self):
        result=self.command('classify',proposed={'kind':'tool','tool':'files','input':{'action':'apply','id':'fixture'}})
        self.assertFalse(result['known'])
    def test_named_tests_and_code_execution_are_distinct(self):
        self.assertEqual(classify(action(name='run',what='pytest',project='PulseUI'))['capability'],'RUN_TESTS')
        self.assertEqual(classify(action(name='run',what='python',entry='main.py',project='PulseUI'))['capability'],'EXECUTE_CODE')
        self.assertEqual(self.store.check(action(name='run',what='python',project='PulseUI'),self.ui)['decision'],'ASK')
    def test_read_routes_are_centrally_mapped(self):
        from jarvis_permissions import read_action
        self.assertEqual(classify(read_action('/api/screenshot'))['capability'],'READ_SCREEN')
        self.assertEqual(classify(read_action('/preview-file',{'path':['PulseUI/a.png']}))['capability'],'READ_FILE')
    def test_durable_snapshot_permission_reference_is_bounded(self):
        from jarvis_tasks import checkpoint
        snap={'version':1,'id':'mission','status':'waiting_approval','currentStep':0,'steps':[{'id':'s','status':'pending'}],'evidence':[],'pendingPermissions':['approval-1']}
        self.assertEqual(json.loads(checkpoint(snap))['pendingPermissions'],['approval-1'])
        with self.assertRaises(ValueError): checkpoint(dict(snap,pendingPermissions=['a']*11))
    def test_resolved_root_change_invalidates_approval_and_receipt(self):
        original=self.store.describe; root=['root-a']
        self.store.describe=lambda call:dict(original(call),target=root[0]+'/'+original(call)['target'])
        approved=self.approve(); ident=self.command('list')['approvals'][0]['id']; root[0]='root-b'
        self.assertFalse(self.store.consume(self.write,approved['receipt'],self.ui))
        with self.assertRaises(ValueError): self.store.recover(ident,self.ui)
    def test_denial_once_does_not_become_permanent_in_a_new_trusted_turn(self):
        self.store.set_context(self.ui,{'background':False,'turn_id':'turn-a'})
        denied=self.store.check(self.write,self.ui);self.store.resolve(denied['approval']['id'],'deny_once',self.ui)
        self.assertEqual(self.store.check(self.write,self.ui)['decision'],'DENY')
        self.store.set_context(self.ui,{'background':False,'turn_id':'turn-b'})
        self.assertEqual(self.store.check(self.write,self.ui)['decision'],'ASK')
    def test_permission_database_is_not_a_file_tool_target(self):
        call=action(name='write',path=str(self.path),content='untrusted policy data')
        self.policy(scope=str(self.path))
        self.assertEqual(self.store.check(call,self.ui)['decision'],'DENY')
    def test_trusted_ui_task_management_is_isolated_from_worker_context(self):
        self.store.set_context(self.ui,{'background':True,'run_policy':'reviewed_writes','task_run_id':'run','mission_id':'mission'})
        child=self.store.command({'action':'session','isolate':True},self.ui)['session']
        self.store.set_context(child,{'background':False})
        call=action(tool='tasks',name='cancel_run',run_id='run')
        self.assertEqual(self.store.check(call,self.ui)['decision'],'DENY')
        allowed=self.store.check(call,child);self.assertEqual(allowed['decision'],'ALLOW')
        self.assertTrue(self.store.consume(call,allowed['receipt'],child))
        self.assertTrue(self.store.context(self.ui)['background'])
        self.assertEqual(self.store.scope_session(child),self.store.scope_session(self.ui))
    def test_all_model_action_enums_are_classified(self):
        import re
        source=(Path(__file__).resolve().parent.parent/'J.A.R.V.I.S. Dashboard - Copy/js/brain.js').read_text(encoding='utf8')
        found=0
        for tool in MAP:
            if tool in ('memory','apply','tasks'): continue
            match=re.search(r"name: '"+tool+r"'[\s\S]*?input_schema:[\s\S]*?action: \{[\s\S]*?enum: \[([^]]+)\]",source)
            if match:
                for name in re.findall(r"'([^']+)'",match[1]):
                    self.assertIn(name,MAP[tool],tool+':'+name); found+=1
        # Integration schemas moved intact to validated manifests in Phase 4.
        for manifest_path in (Path(__file__).resolve().parent.parent/'J.A.R.V.I.S. Dashboard - Copy/skills').glob('*/skill.json'):
            manifest=json.loads(manifest_path.read_text(encoding='utf8'))
            for tool in manifest['tools']:
                if tool['name'] not in MAP or tool['name'] in ('tasks',): continue
                for name in tool['input_schema']['properties'].get('action',{}).get('enum',[]):
                    self.assertIn(name,MAP[tool['name']],tool['name']+':'+name);found+=1
        self.assertGreater(found,80)

if __name__=='__main__': unittest.main(verbosity=2)
