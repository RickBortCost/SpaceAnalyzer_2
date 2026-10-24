"""Verify old full-session markers and interval formulas in real Chromium.

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


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--chromium',required=True)
 parser.add_argument('--report',type=Path,required=True)
 parser.add_argument('--screenshot',type=Path,required=True)
 args=parser.parse_args()
 args.report.parent.mkdir(parents=True,exist_ok=True)
 args.screenshot.parent.mkdir(parents=True,exist_ok=True)
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
   wait("async()=>{const v=await request('/api/values');return [...selected.keys()].every(id=>v.samples[id]?.length>15);}")
   initial=page.evaluate("async()=>{const v=await request('/api/values'),ids=[...selected.keys()];return {id:ids[0],x1:Math.max(...ids.map(id=>v.samples[id][0][0]))+40,x2:Math.min(...ids.map(id=>v.samples[id].at(-1)[0]))};}")
   wait("async p=>{const v=await request('/api/values?view=window');return v.samples[p.id][0][0]>p.x2+100;}",arg=initial,timeout=18)
   assert page.locator('#live-time-view').input_value()=='all'
   page.locator('#global-pause').click();wait('liveCharts.active.analysisSnapshotId')
   page.locator('.marker-analysis > summary').first.click()
   page.evaluate("p=>{const t=liveCharts.active.trend;t.setMarker('x1',p.x1);t.setMarker('x2',p.x2);}",initial)
   wait("id=>liveCharts.active.trend.exactMarkerRows?.get(id)?.x1",arg=initial['id'])
   rows=page.evaluate("()=>[...liveCharts.active.trend.exactMarkerRows.values()]")
   assert len(rows)==4 and all(r['x1'] and r['x2'] for r in rows),rows
   report['checks'].append('Real full-acquisition view: four protocol marker values resolve from originals after eviction from the recent FIFO')
   print('PASS old full-session markers',flush=True)
   page.locator('#quick-add-variable').click()
   page.locator('[data-quick-binding="A"]').select_option(case.ids['opcua'])
   page.locator('[data-quick-binding="B"]').select_option(case.ids['ads'])
   page.locator('#quick-name').fill('Differenza carichi')
   page.locator('#quick-unit').fill('kN')
   page.locator('#quick-expression').fill('([A]-[B])*2')
   page.locator('#quick-operation').select_option('mean')
   page.locator('#quick-run').click()
   wait("document.getElementById('quick-value').textContent!=='—'&&!document.getElementById('quick-run').disabled")
   assert 'campioni validi' in page.locator('#quick-status').inner_text()
   report['value']=page.locator('#quick-value').inner_text();report['markers']=rows
   report['checks'].append('Actual add-variable, binding, expression, metric and Calculate controls evaluate a two-protocol formula over the old marker interval')
   page.locator('#quick-save').click()
   wait("document.getElementById('quick-status').textContent.includes('Formula salvata')")
   wait("[...liveCharts.active.ids].some(n=>n.startsWith('calc:'))")
   report['checks'].append('Actual Save and add to chart control registers and plots the formula')
   page.locator('#quick-calculations').scroll_into_view_if_needed()
   page.locator('#quick-calculations').screenshot(path=str(args.screenshot))
   assert not errors,errors
   report['result']='PASS';browser.close()
 finally:
  stop.set();thread.join(3);http.shutdown();http.server_close();case.tearDown();Sources.tearDownClass()
  args.report.write_text(json.dumps(report,indent=2)+'\n')
  print(json.dumps(report,indent=2))

if __name__=='__main__':main()
