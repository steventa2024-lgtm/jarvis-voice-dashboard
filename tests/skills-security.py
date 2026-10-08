"""Phase 4 artifact checks; print filenames/counts only, never candidate values."""
from pathlib import Path
import json,re,subprocess,sys
root=Path(__file__).resolve().parent.parent;app=root/'J.A.R.V.I.S. Dashboard - Copy'
sys.dont_write_bytecode=True;sys.path.insert(0,str(app));import jarvis_skills
subprocess.run([sys.executable,str(root/'tests/permissions-security.py')],check=True)
for name in ['jarvis_skills_state.json','jarvis_skills_state.json.tmp']:
    assert subprocess.run(['git','-C',str(root),'check-ignore','--quiet','J.A.R.V.I.S. Dashboard - Copy/'+name]).returncode==0
tracked=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
assert not any(Path(p).name in ('jarvis_skills_state.json','jarvis_skills_state.json.tmp') for p in tracked)
manifests=list((app/'skills').glob('*/skill.json'));assert len(manifests)==17
for p in manifests:jarvis_skills.validate(jarvis_skills.parse(p.read_bytes()))
for p in [app/'jarvis_skills.py',app/'js/skills.js',app/'js/skill-adapters.js']:
    assert not re.search(r'\b(?:eval|exec)\s*\(|new\s+Function\s*\(',p.read_text(encoding='utf8')),'Dynamic execution in registry code.'
print(json.dumps({'manifest_validation':'PASS','manifest_count':17,'runtime_state_ignored_untracked':'PASS','no_manifest_code_evaluation':'PASS','private_credentials_git_scan':'PASS'},indent=2))
