import tarfile, io as archive_io, ast, contextlib, functools, hashlib, http.client, importlib.util, io, json, os, subprocess, sys, tempfile, threading, types, urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
sys.dont_write_bytecode=True
root=Path(__file__).resolve().parent.parent
app_name='J.A.R.V.I.S. Dashboard - Copy'

class Scripts(HTMLParser):
 def __init__(self): super().__init__(); self.active=False; self.module=False; self.parts=[]; self.blocks=[]
 def handle_starttag(self,tag,attrs):
  if tag!='script': return
  a=dict(attrs); t=a.get('type','').lower()
  self.active='src' not in a and t in ('','text/javascript','application/javascript','module'); self.module=t=='module'; self.parts=[]
 def handle_data(self,data):
  if self.active: self.parts.append(data)
 def handle_endtag(self,tag):
  if tag=='script' and self.active:
   code=''.join(self.parts)
   if code.strip(): self.blocks.append((code,self.module))
   self.active=False

def http_snapshot(repo):
 app=repo/app_name
 os.chdir(app); sys.path.insert(0,str(app)); sys.argv=['serve.py','--no-open']
 os.environ['PIXABAY_API_KEY']='EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY'
 with mock.patch('urllib.request.urlopen',side_effect=AssertionError('Live network prohibited')):
  spec=importlib.util.spec_from_file_location('serve',app/'serve.py'); server_module=importlib.util.module_from_spec(spec); spec.loader.exec_module(server_module)
  class Quiet(server_module.Handler):
   def log_message(self,*args): pass
  handler=functools.partial(Quiet,directory=str(app))
  server=server_module.Server(('127.0.0.1',0),handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
  routes=['/','/index.html','/style.css','/script.js']+['/js/'+n+'.js' for n in ['app','brain','core','face','orb','palette','starfield','telemetry','voice','watch']]+['/templates/mvp/index.html','/templates/mvp/app.js','/templates/_base/base.js','/templates/node/index.js','/api/health','/jarvis_video.py','/google_auth.json','/spotify_auth.json','/jarvis_recall.db','/.env','/private.pem']
  result=[]
  try:
   for i,route in enumerate(routes):
    conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5); conn.request('GET',route); response=conn.getresponse(); body=response.read(); conn.close()
    assert response.status==(200 if i<19 else 403), (route,response.status)
    assert response.getheader('Cache-Control')=='no-store, no-cache, must-revalidate'
    result.append({'route':route,'status':response.status,'sha256':hashlib.sha256(body).hexdigest(),'content_type':response.getheader('Content-Type')})
  finally: server.shutdown(); server.server_close(); thread.join()
 assert len(result)==25
 return result

if len(sys.argv)>1 and sys.argv[1]=='http-worker':
 repo=Path(sys.argv[2]); dest=Path(sys.argv[3]); dest.write_text(json.dumps(http_snapshot(repo),indent=2)); raise SystemExit(0)

repo=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root
# Export the immutable approved safety commit, never change the baseline.
base_temp=tempfile.TemporaryDirectory(prefix='jarvis-phase1-baseline-')
baseline=Path(base_temp.name)
archive=subprocess.check_output(['git','-C',str(repo),'archive','7703ef0692a90bf945f79887fbdf20a9ee9b2828'])
with tarfile.open(fileobj=archive_io.BytesIO(archive),mode='r:') as bundle: bundle.extractall(baseline,filter='data')
tracked=subprocess.check_output(['git','-C',str(repo),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
files=[repo/p for p in tracked if p]
python_files=[p for p in files if p.suffix=='.py']; js_files=[p for p in files if p.suffix=='.js']; harness_files=[p for p in files if p.suffix=='.cjs']
for p in python_files: compile(p.read_bytes(),str(p),'exec')
inline_count=0
with tempfile.TemporaryDirectory(prefix='jarvis-regression-') as temp_dir:
 temp=Path(temp_dir)
 for p in js_files+harness_files: subprocess.run(['node','--check',str(p)],check=True,capture_output=True)
 for p in files:
  if p.suffix=='.html':
   parser=Scripts(); parser.feed(p.read_text(encoding='utf8'))
   for code,is_module in parser.blocks:
    inline_count+=1; target=temp/('inline-'+str(inline_count)+('.mjs' if is_module else '.js')); target.write_text(code,encoding='utf8'); subprocess.run(['node','--check',str(target)],check=True,capture_output=True)
 snapshots=[]
 for checkout in [baseline,repo]:
  output=temp/(checkout.name+'-http.json')
  subprocess.run([sys.executable,str(Path(__file__).resolve()),'http-worker',str(checkout),str(output)],check=True,capture_output=True)
  snapshots.append(json.loads(output.read_text()))
 changed_routes=set()
 for before,after in zip(*snapshots):
  assert {k:v for k,v in before.items() if k!='sha256'}=={k:v for k,v in after.items() if k!='sha256'}, 'HTTP route/status/cache/type regression'
  if before['sha256']!=after['sha256']: changed_routes.add(before['route'])
 assert changed_routes=={'/','/index.html','/style.css','/js/app.js','/js/brain.js'}, changed_routes

 def load_video(checkout,key):
  m=types.ModuleType('video_under_test'); m.__file__=str(checkout/app_name/'jarvis_video.py')
  env={} if key is None else {'PIXABAY_API_KEY':key}
  with mock.patch.dict(os.environ,env,clear=True): exec(compile(Path(m.__file__).read_bytes(),m.__file__,'exec'),m.__dict__)
  return m

 candidate=load_video(repo,None)
 missing_checks=0
 for key in [None,'','   ']:
  m=load_video(repo,key)
  with mock.patch.object(m.os,'makedirs',side_effect=AssertionError('Filesystem side effect prohibited')), mock.patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('Network side effect prohibited')),mock.patch.object(m.subprocess,'run',side_effect=AssertionError('Tool side effect prohibited')):
   result=m.render('Coffee',[{'query':'coffee beans','say':'A cup of coffee.'}]); assert result['ok'] is False and 'PIXABAY_API_KEY' in result['error'] and 'restart' in result['error']; missing_checks+=1
   try: m._stock('coffee',str(temp/'should-not-exist.mp4'))
   except RuntimeError as e: assert 'PIXABAY_API_KEY' in str(e); missing_checks+=1
   else: raise AssertionError('Stock request accepted missing configuration')
 assert load_video(repo,'  EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY  ').PIXABAY_KEY=='EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY'

 def configured_render(checkout):
  m=load_video(checkout,'EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY')
  folder=temp/'render-comparison'; folder.mkdir(exist_ok=True)
  voice=temp/'test-voice.onnx'; voice.write_bytes(b'offline test stub')
  m.VOICE=str(voice); m.VIDEO_DIR=str(folder); m.STATE=str(temp/'test-video-state.json')
  cmds=[]
  def stock(query,path): Path(path).write_bytes(b'offline stock'); return {'credit':'Offline fixture','w':1920,'h':1080}
  def narrate(text,path): Path(path).write_bytes(b'offline narration'); return 2.0
  def ff(args): cmds.append(args); Path(args[-1]).write_bytes(b'offline rendered fixture')
  with mock.patch.object(m,'_stock',side_effect=stock),mock.patch.object(m,'_narrate',side_effect=narrate),mock.patch.object(m,'_ff',side_effect=ff),mock.patch.object(m,'_duration',return_value=2.6),mock.patch.object(m.time,'time',return_value=1700000000):
   result=m.render('Offline coffee',[{'query':'coffee beans','say':'A cup of coffee.'}],tags=['coffee']); assert result['ok'] is True
  player=(folder/'offline-coffee/index.html').read_bytes()
  return result,cmds,player,m.review(),m.publish_packet()
 baseline_render=configured_render(baseline); candidate_render=configured_render(repo)
 assert baseline_render==candidate_render, 'Configured render/listing/review/publish behavior differs'

 def stock_fetch(checkout):
  m=load_video(checkout,'EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY'); calls=[]
  fixture={'hits':[{'tags':'coffee beans','user':'Fixture','pageURL':'https://example.invalid/fixture','videos':{'large':{'url':'https://example.invalid/offline.mp4','width':1920,'height':1080}}}]}
  def open_url(request,**kw):
   url=request if isinstance(request,str) else request.full_url; calls.append((url,kw))
   if 'pixabay.com/api/videos/' in url:
    query=urllib.parse.parse_qs(urllib.parse.urlparse(url).query); assert query['key']==['EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY'] and query['q']==['coffee beans']; return io.BytesIO(json.dumps(fixture).encode())
   assert url=='https://example.invalid/offline.mp4'; return io.BytesIO(b'offline video bytes')
  output=temp/'stock.mp4'
  with mock.patch.object(m.urllib.request,'urlopen',side_effect=open_url): result=m._stock('coffee beans',str(output))
  return result,output.read_bytes(),calls
 assert stock_fetch(baseline)==stock_fetch(repo)
 old=load_video(baseline,'EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY'); new=load_video(repo,'EXAMPLE_KEY_FOR_OFFLINE_TEST_ONLY')
 for query,hits in [('coffee beans',[{'tags':'coffee beans'},{'tags':'watermelon picnic'}]),('the and',[]),('watermelon picnic',[{'tags':'picnic table'},{'tags':'watermelon'}])]: assert old._rank(query,hits)==new._rank(query,hits)

assert len(python_files)==18 and len(js_files)==16 and len(harness_files)==2 and inline_count==8, 'Unexpected source counts'
base_files=subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only','7703ef0692a90bf945f79887fbdf20a9ee9b2828']).decode().splitlines()
changed_existing=[p for p in base_files if (baseline/p).read_bytes()!=(repo/p).read_bytes()]
assert set(changed_existing)=={app_name+'/'+p for p in ['index.html','style.css','js/app.js','js/brain.js']},changed_existing
# Video and every other Python/voice/reactor/provider definition source is preserved.
assert (baseline/app_name/'jarvis_video.py').read_bytes()==(repo/app_name/'jarvis_video.py').read_bytes()
report={'python_syntax_existing':17,'python_test_runner_syntax':1,'javascript_syntax':len(js_files),'test_harness_syntax':len(harness_files),'inline_javascript':inline_count,'http_checks':25,'intended_http_body_changes':sorted(changed_routes),'missing_key_side_effect_checks':missing_checks,'offline_pixabay_configured_fetch_render_comparison':'PASS','existing_changed_source_files':changed_existing,'preservation':'PASS','live_integrations_tested':False,'pass':True}
print(json.dumps(report,indent=2))
base_temp.cleanup()
