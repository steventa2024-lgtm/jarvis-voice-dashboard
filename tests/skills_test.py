"""Deterministic manifest, isolation, enable-state and permission-equivalence checks."""
from pathlib import Path
import copy,json,shutil,sys,tempfile,unittest
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent.parent; APP=ROOT/'J.A.R.V.I.S. Dashboard - Copy'
sys.path.insert(0,str(APP))
from jarvis_skills import SkillRegistry,validate,parse,ADAPTERS
from jarvis_permissions import PermissionStore,classify

class Skills(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name);self.root=self.folder/'skills';shutil.copytree(APP/'skills',self.root);self.state=self.folder/'state.json'
    def tearDown(self):self.tmp.cleanup()
    def registry(self,**kw):return SkillRegistry(self.root,self.state,**kw)
    def manifest(self,ident='spotify'):return json.loads((self.root/ident/'skill.json').read_text(encoding='utf8'))
    def write(self,m,folder='spotify'):(self.root/folder/'skill.json').write_text(json.dumps(m),encoding='utf8')
    def invalid(self,mutate):
        m=self.manifest();mutate(m);self.write(m);r=self.registry();self.assertEqual(len(r.snapshot()['invalid']),1);self.assertFalse(r.enabled_tool('spotify'));self.assertTrue(r.enabled_tool('files'))
    def test_all_builtins_valid_and_discovered(self):
        r=self.registry();self.assertEqual(r.snapshot()['health']['loaded'],17);self.assertEqual(len(r.snapshot()['invalid']),0);self.assertEqual(sum(len(s['tools']) for s in r.snapshot()['skills']),18)
    def test_exact_schema_snapshot_equivalence(self):
        baseline=json.loads((ROOT/'tests/fixtures/phase3-tools.json').read_text(encoding='utf8')); expected={t['name']:t for value in baseline.values() for t in (value if isinstance(value,list) else [value])}
        for s in self.registry().snapshot()['skills']:
            for tool in s['tools']:self.assertEqual(tool,expected[tool['name']])
    def test_path_traversal_adapter(self):self.invalid(lambda m:m['runtime'].update(adapter='../../evil.py'))
    def test_unknown_adapter(self):self.invalid(lambda m:m['runtime'].update(adapter='os.system'))
    def test_unknown_runtime(self):self.invalid(lambda m:m['runtime'].update(kind='mcp'))
    def test_unknown_fields_and_risk_override(self):self.invalid(lambda m:m.update(risk='low'))
    def test_unsupported_version(self):self.invalid(lambda m:m.update(schema_version=2))
    def test_huge_description(self):self.invalid(lambda m:m.update(description='x'*8193))
    def test_deep_schema(self):
        def change(m):
            node={'type':'string'}
            for _ in range(10):node={'type':'array','items':node}
            m['tools'][0]['input_schema']['properties']['extra']=node
        self.invalid(change)
    def test_huge_schema(self):self.invalid(lambda m:m['tools'][0]['input_schema'].update(properties={str(i):{'type':'string'} for i in range(100)}))
    def test_invalid_permission(self):self.invalid(lambda m:m['permissions']['spotify'].update(pause='FAKE_ALLOW'))
    def test_cannot_lower_existing_permission(self):self.invalid(lambda m:m['permissions']['spotify'].update(pause='READ_SPOTIFY'))
    def test_invalid_json(self):
        (self.root/'spotify/skill.json').write_text('{broken');r=self.registry();self.assertEqual(len(r.snapshot()['invalid']),1);self.assertTrue(r.enabled_tool('files'))
    def test_duplicate_json_fields(self):
        with self.assertRaises(ValueError):parse('{"id":"one","id":"two"}')
    def test_duplicate_id_excludes_all_conflicts(self):
        m=self.manifest('google');m['id']='spotify';self.write(m,'google');r=self.registry();self.assertEqual(len(r.snapshot()['invalid']),2);self.assertFalse(r.enabled_tool('spotify'));self.assertFalse(r.enabled_tool('google'));self.assertTrue(r.enabled_tool('files'))
    def test_duplicate_tool_excludes_all_conflicts(self):
        folder=self.root/'other';folder.mkdir();m=self.manifest();m['id']='other';self.write(m,'other');r=self.registry();self.assertEqual(len(r.snapshot()['invalid']),2);self.assertFalse(r.enabled_tool('spotify'))
    def test_arbitrary_schema_execution_fields(self):self.invalid(lambda m:m['runtime'].update(handler='os.system'))
    def test_invalid_capability_probe(self):self.invalid(lambda m:m['availability'].update(capabilities=['arbitrary_url']))
    def test_required_infrastructure_not_disableable(self):
        r=self.registry()
        for ident in ['permissions','agent']:
            with self.assertRaises(ValueError):r.command({'action':'set_enabled','id':ident,'enabled':False})
        self.assertTrue(r.enabled_tool('remember'))
    def test_reminder_route_disable_preserves_required_memory_and_notices(self):
        r=self.registry();r.command({'action':'set_enabled','id':'memory','enabled':False})
        for action in ('set','list','cancel','recall'):
            self.assertFalse(r.route_enabled({'tool':'memory','input':{'action':action}}))
        for action in ('remember','forget','episode','notice','notices','notice_seen','relevant','brief','precedent'):
            self.assertTrue(r.route_enabled({'tool':'memory','input':{'action':action}}))
    def test_enable_disable_survives_restart_without_manifest_edit(self):
        before=(self.root/'spotify/skill.json').read_bytes();r=self.registry();r.command({'action':'set_enabled','id':'spotify','enabled':False});self.assertFalse(r.enabled_tool('spotify'));r=self.registry();self.assertFalse(r.enabled_tool('spotify'));self.assertEqual(before,(self.root/'spotify/skill.json').read_bytes());r.command({'action':'set_enabled','id':'spotify','enabled':True});self.assertTrue(r.enabled_tool('spotify'))
    def test_invalid_state_fails_closed(self):
        self.state.write_text('{bad');r=self.registry();self.assertFalse(r.enabled_tool('files'));self.assertTrue(r.enabled_tool('remember'))
    def test_stable_order_and_copy_isolation(self):
        r=self.registry();first=[s['id'] for s in r.snapshot()['skills']];r.refresh();self.assertEqual(first,[s['id'] for s in r.snapshot()['skills']]);snap=r.snapshot();snap['skills'][0]['enabled']=False;self.assertTrue(r.enabled_tool('tasks'))
    def test_cached_probes_are_not_called_per_lookup(self):
        calls=[];r=self.registry(probe=lambda m:(calls.append(m['id']) or 'available','Safe test probe'));initial=len(calls)
        for _ in range(10):r.snapshot();r.enabled_tool('files')
        self.assertEqual(len(calls),initial);r.refresh();self.assertEqual(len(calls),initial*2)
    def test_unavailable_and_failed_probe_isolated(self):
        r=self.registry(probe=lambda m:('unavailable','Missing bridge') if m['id']=='spotify' else ('degraded','Setup needed'));self.assertFalse(r.enabled_tool('spotify'));self.assertTrue(r.enabled_tool('files'))
    def test_registry_metadata_cannot_mutate_risk_policy(self):
        before=copy.deepcopy(__import__('jarvis_permissions').RISKS);self.registry();self.assertEqual(before,__import__('jarvis_permissions').RISKS)
    def test_all_declared_actions_match_existing_broker(self):
        for s in self.registry().snapshot()['skills']:
            for t in s['tools']:
                selector='action' if 'action' in t['input_schema']['properties'] else 'source' if t['name']=='translate' else None
                for action,cap in s['permissions'][t['name']].items():self.assertEqual(cap,classify({'tool':t['name'],'input':{selector:action} if selector else {}})['capability'])
    def test_protected_registry_resources(self):
        r=self.registry();self.assertTrue(r.protected_resource(self.root/'spotify/skill.json'));self.assertTrue(r.protected_resource(self.state));self.assertFalse(r.protected_resource(self.folder/'safe-project/main.py'))
    def test_only_declared_command_fields(self):
        with self.assertRaises(ValueError):self.registry().command({'action':'list','path':'arbitrary'})
    def test_invalid_enum_type_is_isolated(self):self.invalid(lambda m:m['tools'][0]['input_schema']['properties'].update(extra={'type':'string','enum':[12]}))
    def test_oversized_file_is_bounded_and_isolated(self):
        (self.root/'spotify/skill.json').write_bytes(b' '*65537);r=self.registry();self.assertEqual(len(r.snapshot()['invalid']),1);self.assertTrue(r.enabled_tool('files'))
    def test_symlink_locations_are_rejected(self):
        from unittest.mock import patch
        with patch.object(Path,'is_symlink',lambda path:path.name=='spotify'):
            r=self.registry();self.assertFalse(r.enabled_tool('spotify'));self.assertTrue(r.enabled_tool('files'))
    def test_secret_like_manifest_data_is_rejected(self):self.invalid(lambda m:m.update(description='password=synthetic-fixture-value'))
    def test_disabled_file_dependency_hides_preview(self):
        r=self.registry();r.command({'action':'set_enabled','id':'files','enabled':False});self.assertFalse(r.enabled_tool('see_preview'));r.command({'action':'set_enabled','id':'files','enabled':True});self.assertTrue(r.enabled_tool('see_preview'))

if __name__=='__main__':unittest.main(verbosity=2)
