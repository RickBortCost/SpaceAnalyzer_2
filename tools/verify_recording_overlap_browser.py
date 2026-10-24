"""Verify a new recording during a blocked previous FDA save in real Chromium.

Development-only dependencies: source/requirements.txt, python-snap7==2.0.2,
and Playwright. Supply a locally installed Chromium; canvas and CSP are real.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source/tests'))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from test_source_sessions import Sources,app
from recordings import Recorder
from unittest.mock import patch


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--chromium',required=True)
 parser.add_argument('--report',type=Path,required=True)
 parser.add_argument('--screenshot',type=Path,required=True)
 args=parser.parse_args()
 args.report.parent.mkdir(parents=True,exist_ok=True)
 args.screenshot.parent.mkdir(parents=True,exist_ok=True)
 gate=threading.Event();real_run=Recorder._run
 def blocked(rec):
  if rec.metadata.get('session_name')=='Slow previous':gate.wait(90)
  return real_run(rec)
 writer_patch=patch.object(Recorder,'_run',blocked);writer_patch.start()
 @app.app.post('/test/finalizers/release')
 def release_finalizer():gate.set();return {'ok':True}
 Sources.setUpClass()
 case=Sources('test_all_nonempty_protocol_subsets_and_catalogs');case.setUp()
 http=make_server('127.0.0.1',0,app.app,threaded=True)
 threading.Thread(target=http.serve_forever,daemon=True).start()
 stop=threading.Event()
 def publish():
  i=0
  while not stop.wait(.025):
   i+=1;case.mqtt.publish('plant/motor',{'load':7+i%10},qos=1)
 thread=threading.Thread(target=publish,daemon=True);thread.start()
 report={'version':app.APP_VERSION,'browser':'Chromium','canvas_stubbed':False,'hardware_tested':False,'checks':[],'result':'FAIL'}
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
   page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
   report['browser_version']=browser.version
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   def wait(expression,arg=None,timeout=30):
    # Poll through DevTools evaluation without relaxing the application's CSP.
    predicate=expression if '=>' in expression else '()=>('+expression+')'
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
     if page.evaluate(predicate,arg):return
     time.sleep(.05)
    raise AssertionError('Browser condition timed out: '+expression)

   base=f'http://127.0.0.1:{http.server_port}'
   page.goto(base);wait('!!window.opcuaConfigReady');page.evaluate('()=>window.opcuaConfigReady')
   page.locator('#nav-connection').click()
   page.locator('#mqtt-parallel').check()
   cfg=case.cfg
   page.evaluate('''cfg=>{
    for(const p of ['opcua','ads','mqtt','s7']){document.getElementById('enable-'+p).checked=true;document.getElementById('period-'+p).value=cfg.source_intervals[p];}
    for(const key of ['endpoint','ads_tcp_port','s7_host','s7_tcp_port','mqtt_port'])document.getElementById(key).value=cfg[key];
    document.getElementById('security').value='None';document.getElementById('security_mode').value='None';document.getElementById('sample_ms').value='20';document.getElementById('buffer_mode').value='time';document.getElementById('window_seconds').value='10';
    document.getElementById('mqtt_host').value='127.0.0.1';document.getElementById('mqtt_topics').value='plant/#';window.s7Tags=cfg.s7_tags;
   }''',cfg)
   page.locator('#connect').click()
   wait("!connectionBusy&&Object.values(window.sourceSessionsStatus||{}).length===4&&Object.values(window.sourceSessionsStatus).every(s=>s.state==='connected')")
   print('PASS four browser sessions connected',flush=True)
   page.locator('.address-drawer > summary').click()
   page.locator('details.secondary-controls').filter(has=page.locator('#manual-node')).locator('summary').click()
   for protocol,nid in case.ids.items():
    page.locator('#browse-source').select_option(protocol)
    wait('p=>currentSource()===p',arg=protocol)
    page.locator('#manual-node').fill(nid);page.locator('#add-node').click()
    wait('id=>selected.has(id)',arg=nid)
   wait("liveCharts.active.trend.domain&&Object.values(liveCharts.active.trend.series).every(a=>a.length>5)")
   def start(fmt,name):
    page.locator('#record').click();page.locator('input[name="record-format"][value="'+fmt+'"]').check()
    page.locator('#record-session').fill(name);page.locator('#record-start').click()
    wait("name=>recordState.active&&recordState.session.session_name===name",arg=name)
   start('das','Slow previous');wait('recordState.cycles_received>80')
   first=page.evaluate('recordState.recording_id');page.locator('#record-stop').click()
   wait("recordState.finalizing_count===1&&!recordState.selection_locked&&!document.getElementById('record').disabled")
   start('csv','New acquisition');wait('recordState.cycles_received>80&&recordState.finalizing_count===1')
   assert page.evaluate('recordState.active')
   assert page.evaluate('recordState.recording_id')!=first
   page.locator('#recording-jobs > summary').click()
   assert 'Slow previous' in page.locator('#recording-jobs-list').inner_text()
   page.screenshot(path=str(args.screenshot))
   report['checks'].append('Actual Stop and Record controls start a new CSV acquisition while the old FDA disk writer is blocked; independent states visible in the background-save panel')
   page.evaluate("async()=>request('/test/finalizers/release',{})")
   wait("id=>recordState.active&&recordState.finalizations.some(j=>j.recording_id===id&&!j.busy&&!j.error)",arg=first)
   report['checks'].append('Previous FDA conversion finishes in a separate process while the new acquisition continues')
   page.locator('#record-stop').click();wait('!recordState.busy')
   jobs=page.evaluate('recordState.finalizations')
   assert len(jobs)==2 and all(j['state']=='stopped' and j['rows_written']>40 for j in jobs),jobs
   assert all(j['primary']['integrity']=='complete' and j['primary']['durable_samples']==j['rows_written'] for j in jobs),jobs
   replay=page.evaluate("async()=>{const out=[];for(const j of recordState.finalizations){const r=(await request('/api/recordings/data',{source:'repository',name:j.files[0].name,max_points:1000})).recording;out.push({format:j.format,rows:j.rows_written,numeric:r.numeric_rows,signals:Object.keys(r.series)});}return out;}")
   assert all(set(j['signals'])==set(case.ids.values()) and j['rows']==j['numeric'] for j in replay),replay
   report['recordings']=replay;report['checks'].append('Both FDA and CSV replay all four protocol signals with exactly their own original row counts')
   assert not errors,errors
   report['result']='PASS';browser.close()
 finally:
  gate.set();writer_patch.stop();stop.set();thread.join(3);http.shutdown();http.server_close();case.tearDown();Sources.tearDownClass();app.recording.stop(timeout=20)
  args.report.write_text(json.dumps(report,indent=2)+'\n')
  print(json.dumps(report,indent=2))

if __name__=='__main__':main()
