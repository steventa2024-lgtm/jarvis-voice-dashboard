"""Isolated Windows server; no production runtime state or live accounts."""
from pathlib import Path
import os
import sys
sys.dont_write_bytecode=True
port, folder=int(sys.argv[1]),Path(sys.argv[2]).resolve()
app=Path(__file__).resolve().parent.parent/'J.A.R.V.I.S. Dashboard - Copy'
sys.path.insert(0,str(app)); os.chdir(app); sys.argv=['serve.py',str(port),'--no-open']
import serve
import jarvis_tasks
import jarvis_permissions
serve.memory.STORE=str(folder/'memory.json')
serve._task_store=jarvis_tasks.TaskStore(folder/'tasks.db')
serve._permission_store=jarvis_permissions.PermissionStore(folder/'permissions.db',describe=serve.describe_permission)
serve.files.configure(roots=[str(folder)],projects=str(folder),port=port)
raise SystemExit(serve.main())
