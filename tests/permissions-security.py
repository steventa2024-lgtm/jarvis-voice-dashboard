"""Repository-only security checks. Never print candidate secret values."""
from pathlib import Path
import json
import re
import subprocess
import sys
root=Path(__file__).resolve().parent.parent
def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL).decode('utf8')
names=[p for p in git('ls-files','--cached','--others','--exclude-standard','-z').split('\0') if p]
private=re.compile(r'(?i)(?:\.(?:db|db-wal|db-shm|sqlite|sqlite3|pem|key|bundle)$|(?:google_auth|spotify_auth|jarvis_state|jarvis_jobs|jarvis_lessons|jarvis_minecraft|jarvis_video)\.json$|JarvisPrivateRecovery|UNSANITIZED-PRIVATE-)')
bad=[p for p in names if private.search(p)]
assert not bad,'Private/runtime paths found: '+str(bad)
assert not git('ls-files','--cached','--ignored','--exclude-standard').strip(),'Ignored files became tracked.'
patterns=[re.compile(rb'sk-(?:proj-|ant-|or-)?[A-Za-z0-9_-]{24,}'),re.compile(rb'AKIA[A-Z0-9]{16}'),re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
findings=[]
for name in names:
    p=root/name
    if p.is_file() and p.suffix.lower() in ('.py','.js','.cjs','.md','.json','.html','.css','.example'):
        body=p.read_bytes()
        if any(rx.search(body) for rx in patterns):findings.append(name)
assert not findings,'Possible credential material in files: '+str(findings)
for name in ['jarvis_permissions.db','jarvis_permissions.db-wal','jarvis_permissions.db-shm']:
    assert subprocess.run(['git','-C',str(root),'check-ignore','--quiet','J.A.R.V.I.S. Dashboard - Copy/'+name]).returncode==0
subprocess.run(['git','-C',str(root),'diff','--check'],check=True)
subprocess.run(['git','-C',str(root),'fsck','--full'],check=True,stdout=subprocess.DEVNULL)
print(json.dumps({'private_artifacts':'PASS','ignored_files_untracked':'PASS','credential_patterns':'PASS','permission_runtime_ignored':'PASS','diff_whitespace':'PASS','git_integrity':'PASS','files_scanned':len(names)},indent=2))
