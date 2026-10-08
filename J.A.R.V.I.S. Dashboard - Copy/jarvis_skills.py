"""Trusted built-in discovery. Manifests are bounded data, never executable code."""
from pathlib import Path
from collections import Counter
import copy
import json
import os
import re
import threading

import jarvis_permissions as permissions

SCHEMA = 1
IDENT = re.compile(r'[a-z][a-z0-9_]{0,47}\Z')
VERSION = re.compile(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-zA-Z0-9.-]{1,30})?\Z')
# Trusted routing identities and canonical ordering; adding code requires developer review.
ADAPTERS = {
 'tasks': ('tasks',), 'spotify': ('spotify',), 'knowledge': ('lookup',),
 'google': ('google',), 'memory': ('reminders',), 'vision': ('see_screen',),
 'recall': ('recall',), 'files': ('files',), 'preview': ('see_preview',),
 'video': ('video',), 'jobs': ('jobs',), 'job_hunt': ('job_hunt',),
 'minecraft': ('minecraft',), 'lessons': ('lessons',), 'translate': ('translate',),
 'desktop': ('desktop',), 'web': ('web_search', 'web_fetch')}
CAPABILITIES = {'search','fetch','launch','spotify','knowledge','google','memory','vision','recall','files','jobs','lessons','video','hunt','minecraft','desktop','tasks'}
REQUIREMENTS = {'vision_model','web_search','foreground','task_module'}
CORE = {'control_interface','remember','forget'}
HOSTED = {'web_search': {'type':'web_search_20260209','name':'web_search','max_uses':8}, 'web_fetch':{'type':'web_fetch_20260209','name':'web_fetch','max_uses':5}}

def object_fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value)-set(required)-set(optional):
        raise ValueError('Missing or unsupported manifest fields.')

def bounded(value, limit, pattern=None):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or (pattern and not pattern.fullmatch(value)):
        raise ValueError('Invalid or oversized manifest text.')
    if permissions.SENSITIVE.search(value):
        raise ValueError('Credential-like manifest data is forbidden.')

def schema(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth>8 or budget[0]>256:
        raise ValueError('Schema exceeds bounded complexity.')
    object_fields(value, {'type'}, {'properties','required','enum','items','description'})
    kind=value['type']
    if kind not in ('object','string','number','integer','array','boolean'):
        raise ValueError('Unsupported schema type.')
    if 'description' in value: bounded(value['description'],8192)
    if 'properties' in value:
        props=value['properties']
        if kind!='object' or not isinstance(props,dict) or len(props)>64: raise ValueError('Invalid properties.')
        for key,node in props.items():
            if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',key): raise ValueError('Invalid property name.')
            schema(node,depth+1,budget)
    if 'required' in value:
        req=value['required']
        if kind!='object' or not isinstance(req,list) or len(req)>64 or any(not isinstance(k,str) or k not in value.get('properties',{}) for k in req) or len(set(req))!=len(req): raise ValueError('Invalid required properties.')
    if 'items' in value:
        if kind!='array': raise ValueError('Items on non-array schema.')
        schema(value['items'],depth+1,budget)
    if 'enum' in value:
        enum=value['enum']
        if not isinstance(enum,list) or not 1<=len(enum)<=64 or any(not isinstance(v,(str,int,float,bool)) or (isinstance(v,str) and len(v)>256) for v in enum): raise ValueError('Invalid enum.')
        if len(set(json.dumps(v) for v in enum))!=len(enum): raise ValueError('Duplicate enum.')
        for entry in enum:
            valid = ((kind=='string' and isinstance(entry,str)) or (kind=='boolean' and type(entry) is bool) or
                     (kind=='integer' and type(entry) is int) or (kind=='number' and type(entry) in (int,float)))
            if not valid: raise ValueError('Enum value does not match schema type.')
            if isinstance(entry,str) and permissions.SENSITIVE.search(entry): raise ValueError('Credential-like enum value.')

def validate(value):
    object_fields(value, {'schema_version','id','name','version','description','tools','runtime','permissions','availability'}, {'provider_tools'})
    if type(value['schema_version']) is not int or value['schema_version']!=SCHEMA: raise ValueError('Unsupported manifest schema version.')
    bounded(value['id'],48,IDENT); bounded(value['name'],80); bounded(value['version'],48,VERSION); bounded(value['description'],8192)
    if value['id'] in ('permissions','agent'): raise ValueError('Required infrastructure cannot be registered as a disableable skill.')
    object_fields(value['runtime'], {'kind','adapter'})
    adapter=value['runtime']['adapter']
    if value['runtime']['kind']!='client_adapter' or adapter not in ADAPTERS: raise ValueError('Unknown trusted runtime/adapter.')
    tools=value['tools']
    if not isinstance(tools,list) or not 1<=len(tools)<=2: raise ValueError('Invalid tool count.')
    for tool in tools:
        object_fields(tool,{'name','description','input_schema'})
        bounded(tool['name'],48,IDENT); bounded(tool['description'],8192); schema(tool['input_schema'])
        if tool['input_schema']['type']!='object': raise ValueError('Tool schema must be object.')
    names=[tool['name'] for tool in tools]
    if tuple(names)!=ADAPTERS[adapter] or any(n in CORE for n in names): raise ValueError('Adapter/tool binding is not trusted.')
    declared=value['permissions']
    if not isinstance(declared,dict) or set(declared)!=set(names): raise ValueError('Permission tool mapping mismatch.')
    for tool in tools:
        props=tool['input_schema'].get('properties',{})
        selector='action' if 'action' in props else 'source' if tool['name']=='translate' else None
        actions=props[selector].get('enum',[]) if selector else ['*']
        mapping=declared[tool['name']]
        if not isinstance(mapping,dict) or set(mapping)!=set(actions): raise ValueError('Permission action mapping mismatch.')
        for action,cap in mapping.items():
            args={selector:action} if selector else {}
            authoritative=permissions.classify({'kind':'tool','tool':tool['name'],'input':args})['capability']
            if cap not in permissions.RISKS or cap!=authoritative: raise ValueError('Permission metadata differs from broker.')
    availability=value['availability']
    object_fields(availability,{'capabilities','mode','client_requirements'})
    for field,known in [('capabilities',CAPABILITIES),('client_requirements',REQUIREMENTS)]:
        data=availability[field]
        if not isinstance(data,list) or len(data)>4 or any(not isinstance(v,str) or v not in known for v in data) or len(set(data))!=len(data): raise ValueError('Unknown availability metadata.')
    if availability['mode'] not in ('all','any'): raise ValueError('Unknown availability mode.')
    if 'provider_tools' in value and (adapter!='web' or value['provider_tools']!={'anthropic':HOSTED}): raise ValueError('Unsupported provider-native tool metadata.')
    return copy.deepcopy(value)

def parse(raw):
    def pairs(items):
        out={}
        for key,value in items:
            if key in out: raise ValueError('Duplicate JSON field.')
            out[key]=value
        return out
    if len(raw)>65536: raise ValueError('Manifest exceeds 64 KiB.')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON value.')))

class SkillRegistry:
    def __init__(self, root, state_path, probe=None):
        self.root=Path(root).resolve(); self.state_path=Path(state_path); self.probe=probe or (lambda m:('available','Trusted adapter installed.'))
        self.lock=threading.RLock(); self.skills=[]; self.invalid=[]; self.generation=0; self.state={}; self.state_error=None
        if self.state_path.exists():
            try:
                with self.state_path.open('rb') as handle: saved=parse(handle.read(65537))
                object_fields(saved,{'schema_version','enabled'})
                if saved['schema_version']!=1 or not isinstance(saved['enabled'],dict) or len(saved['enabled'])>100 or any(not isinstance(k,str) or not IDENT.fullmatch(k) or type(v) is not bool for k,v in saved['enabled'].items()): raise ValueError('Invalid enabled state.')
                self.state=saved['enabled']
            except (ValueError,OSError): self.state_error='Invalid runtime state; skills fail closed until trusted recovery.'
        self.refresh()

    def refresh(self):
        with self.lock:
            candidates=[]; invalid=[]
            if self.root.is_dir():
                for folder in sorted(self.root.iterdir(),key=lambda p:p.name):
                    if not folder.is_dir(): continue
                    manifest=folder/'skill.json'
                    try:
                        if folder.is_symlink() or folder.is_junction() or manifest.is_symlink() or not folder.resolve().is_relative_to(self.root) or not IDENT.fullmatch(folder.name): raise ValueError('Untrusted manifest location.')
                        with manifest.open('rb') as handle: raw=handle.read(65537)
                        data=validate(parse(raw)); candidates.append((folder.name,data))
                    except (OSError,ValueError,TypeError,RecursionError): invalid.append({'id':permissions.clean(folder.name,48),'status':'invalid','reason':'Manifest failed strict validation.','enabled':False})
            ids=Counter(m['id'] for _,m in candidates); names=Counter(t['name'] for _,m in candidates for t in m['tools'])
            valid=[]
            for folder,m in candidates:
                if ids[m['id']]>1 or any(names[t['name']]>1 for t in m['tools']):
                    invalid.append({'id':folder,'status':'invalid','reason':'Duplicate skill ID or tool name; all conflicts excluded.','enabled':False}); continue
                enabled=self.state.get(m['id'],True) and not self.state_error
                try:
                    status,reason=self.probe(m)
                    if status not in ('available','unavailable','degraded'): raise ValueError('Invalid probe.')
                except Exception: status,reason='unavailable','Availability probe failed.'
                valid.append(dict(m,enabled=bool(enabled),status=status if enabled else 'disabled',reason=reason if enabled else self.state_error or 'Disabled by user.',diagnostics={'manifest':'valid','schema':'valid','permissions':'broker-equivalent','adapter':'registered'}))
            by_adapter={m['runtime']['adapter']:m for m in valid}
            preview=by_adapter.get('preview'); files=by_adapter.get('files')
            if preview and preview['enabled'] and (not files or not files['enabled'] or files['status'] not in ('available','degraded')):
                preview.update(status='unavailable',reason='Required Files skill is disabled or unavailable.')
            order={adapter:i for i,adapter in enumerate(ADAPTERS)}
            self.skills=sorted(valid,key=lambda m:(order[m['runtime']['adapter']],m['id']));self.invalid=sorted(invalid,key=lambda m:m['id']);self.generation+=1
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({'ok':True,'schema_version':1,'generation':self.generation,'skills':self.skills,'invalid':self.invalid,'infrastructure':[{'id':'permissions','name':'Permission Broker','status':'required','disableable':False},{'id':'agent','name':'Agent Core','status':'required','disableable':False}], 'health':{'loaded':len(self.skills),'available':sum(s['status']=='available' for s in self.skills),'degraded':sum(s['status']=='degraded' for s in self.skills),'invalid':len(self.invalid)}})

    def enabled_tool(self,name):
        with self.lock:
            if name in CORE: return True
            return any(s['enabled'] and s['status'] in ('available','degraded') and any(t['name']==name for t in s['tools']) for s in self.skills)

    def route_enabled(self,action):
        tool=action['tool']; args=action.get('input',{})
        if tool=='memory':
            return self.enabled_tool('reminders') if args.get('action') in ('set','list','cancel','recall') else True  # Core memory/notice infrastructure remains required.
        if tool=='open': return True  # Core interface control.
        if tool=='files' and args.get('action')=='configure': return True  # Trusted existing settings, still broker protected.
        if tool=='recall' and args.get('action')=='log_exchange': return True
        if tool=='apply': return self.enabled_tool('job_hunt' if args.get('action')=='sign_in' else 'jobs')
        if tool=='see_screen': return self.enabled_tool('see_screen') or self.enabled_tool('translate')
        return self.enabled_tool(tool)

    def protected_resource(self,resource):
        try:
            path=Path(resource).resolve()
            return path.is_relative_to(self.root) or path==self.state_path.resolve() or path==Path(str(self.state_path)+'.tmp').resolve()
        except (ValueError,OSError): return True

    def command(self,data):
        object_fields(data,{'action'},{'id','enabled'})
        op=data['action']
        if op in ('list','status'): return self.snapshot()
        if op=='get':
            result=next((s for s in self.snapshot()['skills'] if s['id']==data.get('id')),None)
            if result is None: raise ValueError('Unknown skill.')
            return {'ok':True,'skill':result}
        if op=='refresh': return self.refresh()
        if op=='set_enabled':
            with self.lock:
                ident=data.get('id')
                if type(data.get('enabled')) is not bool or not any(s['id']==ident for s in self.skills) or self.state_error: raise ValueError('Unknown skill or invalid enabled state.')
                updated=dict(self.state,**{ident:data['enabled']})
                self.state_path.parent.mkdir(parents=True,exist_ok=True)
                tmp=Path(str(self.state_path)+'.tmp')
                with tmp.open('w',encoding='utf8') as f:
                    json.dump({'schema_version':1,'enabled':updated},f,sort_keys=True); f.flush(); os.fsync(f.fileno())
                os.replace(tmp,self.state_path);self.state=updated
                return self.refresh()
        raise ValueError('Unsupported skill command.')
