"""Verify live XY zoom in Chromium against four local protocol peers.

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
    document.getElementById('security').value='None';document.getElementById('security_mode').value='None';document.getElementById('sample_ms').value='20';
    document.getElementById('mqtt_host').value='127.0.0.1';document.getElementById('mqtt_topics').value='plant/#';window.s7Tags=cfg.s7_tags;
   }''',cfg)
   page.locator('#connect').click()
   wait("Object.values(window.sourceSessionsStatus||{}).length===4&&Object.values(window.sourceSessionsStatus).every(s=>s.state==='connected')")
   print('PASS four browser sessions connected',flush=True)
   page.locator('.address-drawer > summary').click()
   page.locator('details.secondary-controls').filter(has=page.locator('#manual-node')).locator('summary').click()
   for protocol,nid in case.ids.items():
    page.locator('#browse-source').select_option(protocol)
    wait('p=>currentSource()===p',arg=protocol)
    page.locator('#manual-node').fill(nid);page.locator('#add-node').click()
    wait('id=>selected.has(id)',arg=nid)
   wait("liveCharts.active.trend.domain&&Object.values(liveCharts.active.trend.series).every(a=>a.length>5)")
   layout=page.evaluate('()=>liveCharts.active.trend.layout')
   box=page.locator('[data-chart-role="canvas"]').first.bounding_box();p=layout['p'];w=layout['w'];h=layout['h']
   page.mouse.move(box['x']+p['l']+.15*(w-p['l']-p['r']),box['y']+p['t']+.15*(h-p['t']-p['b']))
   page.mouse.down();page.mouse.move(box['x']+p['l']+.65*(w-p['l']-p['r']),box['y']+p['t']+.75*(h-p['t']-p['b']),steps=5);page.mouse.up()
   wait('liveCharts.active.trend.frozen&&liveCharts.active.trend.zoom')
   before=page.evaluate('()=>({width:liveCharts.active.trend.domain.xmax-liveCharts.active.trend.domain.xmin,axes:liveCharts.active.trend.domain.axes})')
   page.locator('#global-live-zoom').click()
   wait('!liveCharts.active.trend.frozen&&liveCharts.liveZoom')
   first=page.evaluate('liveCharts.active.trend.domain.xmax')
   wait('end=>liveCharts.active.trend.domain.xmax>end+500',arg=first)
   after=page.evaluate('()=>({width:liveCharts.active.trend.domain.xmax-liveCharts.active.trend.domain.xmin,axes:liveCharts.active.trend.domain.axes})')
   assert abs(before['width']-after['width'])<.001,(before,after)
   assert before['axes']==after['axes'],(before,after)
   print('PASS browser phase',len(report['checks'])+1,flush=True);report['checks'].append('Real mouse XY zoom pauses by default; Zoom live resumes with fixed duration/Y limits and advancing data from four local protocols')
   page.locator('#global-pause').click();wait('liveCharts.active.trend.frozen')
   held=page.evaluate('liveCharts.active.trend.domain.xmax');time.sleep(.4)
   assert page.evaluate('liveCharts.active.trend.domain.xmax')==held
   page.locator('#global-pause').click();wait('!liveCharts.active.trend.frozen')
   resumed=page.evaluate('()=>({width:liveCharts.active.trend.domain.xmax-liveCharts.active.trend.domain.xmin,axes:liveCharts.active.trend.domain.axes})')
   assert resumed['axes']==before['axes'] and abs(resumed['width']-before['width'])<.001
   print('PASS browser phase',len(report['checks'])+1,flush=True);report['checks'].append('Actual Pausa/Play controls preserve the XY zoom')
   page.locator('#record').click();page.locator('input[name="record-format"][value="csv"]').check();page.locator('#record-start').click()
   wait('recordState.busy')
   start=page.evaluate('liveCharts.active.trend.domain.xmax')
   wait('end=>liveCharts.active.trend.domain.xmax>end+500',arg=start)
   page.screenshot(path=str(args.screenshot))
   page.locator('#record-stop').click();wait('!recordState.busy')
   result=page.evaluate('''async()=>{
    const rec=(await request('/api/record/status')).recording;
    const replay=(await request('/api/recordings/data',{source:'repository',name:rec.files[0].name,max_points:200})).recording;
    return {state:rec.state,rows:rec.rows_written,ids:Object.keys(replay.series)};
   }''')
   assert result['state']=='stopped' and set(result['ids'])==set(case.ids.values()),result
   report['recording']=result;report['checks'].append('Actual CSV Record runs alongside live zoom and replays all four signals')
   page.set_viewport_size({'width':1366,'height':900});page.wait_for_timeout(100)
   button=page.locator('#global-live-zoom').bounding_box()
   assert button and button['x']>=0 and button['x']+button['width']<=1366,button
   print('PASS browser phase',len(report['checks'])+1,flush=True);report['checks'].append('Dedicated button is visible at 1366 px width')
   assert not errors,errors
   report['result']='PASS';browser.close()
 finally:
  stop.set();thread.join(3);http.shutdown();http.server_close();case.tearDown();Sources.tearDownClass()
  args.report.write_text(json.dumps(report,indent=2)+'\n')
  print(json.dumps(report,indent=2))

if __name__=='__main__':main()
