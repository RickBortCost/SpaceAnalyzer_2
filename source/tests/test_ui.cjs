// DOM/component test of the real HTML and JS against a running local test service.
// Canvas calls are stubbed: this test does not claim pixel rendering or native Windows QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base, endpoint, rootNode, ...ids] = process.argv.slice(2);
const errors = [];
const wait = async (condition, label, timeout=12000) => {
  const end=Date.now()+timeout;
  while(Date.now()<end){if(await condition()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,35));}
  throw new Error('Timeout: '+label+'; errors: '+errors.join('; '));
};
const api = async route => (await fetch(base+route)).json();

async function page(){
  const html=await (await fetch(base)).text();
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const w=dom.window;
  w.AbortController=globalThis.AbortController;
  w.fetch=async(url,options)=>{try{return await fetch(new URL(url,base),options);}catch(e){errors.push('fetch '+url+': '+e.message);throw e;}};
  w.ResizeObserver=class{observe(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;
  w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  return {dom,w,$,t:w.uiTest};
}

(async()=>{
  const {dom,w,$,t}=await page();
  try{
    $('nav-connection').click();assert.equal($('connection-dialog').open,true);$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
    $('connect').click();await wait(()=>t.connected,'Connect button');assert.equal($('connection-dialog').open,false);
    for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'add NodeId');}
    $('scan').click();await wait(()=>$('scan-status').textContent.includes('Scansione completata'),'scan button');
    await wait(()=>t.trend.axisPlan.dual,'preserved automatic dual Y axes');
    assert.equal($('legend').children.length,2);
    // Record always asks for a format. Cancel/Escape leave the recorder idle.
    $('record').click();await wait(()=>!$('record-modal').hidden,'Record format popup');
    assert.equal((await api('/api/record/status')).recording.state,'idle');
    $('record-cancel').click();assert.equal($('record-modal').hidden,true);
    $('record').click();await wait(()=>!$('record-modal').hidden,'reopen format popup');
    $('record-modal').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal($('record-modal').hidden,true);
    const files=[];
    for(const format of ['csv','parquet']){
      $('record').click();await wait(()=>!$('record-modal').hidden,'format popup '+format);
      w.document.querySelector('input[name="record-format"][value="'+format+'"]').checked=true;
      $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await wait(()=>t.record.active,'Record '+format);
      assert.equal($('record').disabled,true);assert.equal($('record-stop').disabled,false);
      assert.equal($('remove-all').disabled,true);assert.equal($('add-node').disabled,true);
      assert.ok([...w.document.querySelectorAll('input[data-nodeid]')].every(e=>e.disabled));
      await wait(()=>t.record.cycles_received>=40,'recording receives acquisition cycles');
      if(format==='csv'){
        // Reload restores the unsaved active selection without interrupting Record.
        const reloaded=await page();
        await wait(()=>reloaded.t.record.active,'record status after page reload');
        assert.equal(reloaded.t.selected.size,2);assert.equal(reloaded.$('remove-all').disabled,true);
        reloaded.dom.window.close();
        $('pause-play').click();await wait(()=>t.trend.frozen,'Pause button');
        const snapshot=JSON.stringify(t.trend.series),before=t.record.cycles_received;
        assert.match($('pause-play').textContent,/Play/);
        for(const [id,x] of [['marker-x1',290],['marker-x2',650]]){
          $(id).click();$('chart').dispatchEvent(new (w.PointerEvent||w.MouseEvent)(w.PointerEvent?'pointerdown':'mousedown',{bubbles:true,clientX:x,clientY:200,button:0}));
        }
        assert.equal($('marker-data-body').children.length,2);
        assert.notEqual($('marker-delta-time').textContent,'—');
        await wait(()=>t.record.cycles_received>=before+35,'Record continues while paused');
        assert.equal(JSON.stringify(t.trend.series),snapshot);
        assert.notEqual(JSON.stringify(t.trend.latestSeries),snapshot);
        $('chart').dispatchEvent(new (w.PointerEvent||w.MouseEvent)(w.PointerEvent?'pointerdown':'mousedown',{bubbles:true,clientX:250,clientY:120,button:0}));
        $('chart').dispatchEvent(new (w.PointerEvent||w.MouseEvent)(w.PointerEvent?'pointermove':'mousemove',{bubbles:true,clientX:700,clientY:450,button:0}));
        (w.PointerEvent?$('chart'):w).dispatchEvent(new (w.PointerEvent||w.MouseEvent)(w.PointerEvent?'pointerup':'mouseup',{bubbles:true,clientX:700,clientY:450,button:0}));
        assert.ok(t.trend.zoom);assert.ok(t.trend.frozen);
        assert.equal($('time-scroll').disabled,false);
        const oldZoomMin=t.trend.zoom.xmin;$('time-scroll').value='850';$('time-scroll').dispatchEvent(new w.Event('input',{bubbles:true}));
        assert.notEqual(t.trend.zoom.xmin,oldZoomMin);assert.ok(t.trend.zoom.xmax<=t.trend.fullDomain.xmax+1e-6);
        const zoomCycles=t.record.cycles_received;
        await wait(()=>t.record.cycles_received>=zoomCycles+20,'Record continues while zoomed');
      }
      $('record-stop').click();await wait(()=>t.record.state==='stopped','Stop '+format);
      assert.ok(t.record.rows_written>=80);
      assert.equal(t.record.error,'');files.push({...t.record});
      assert.equal($('remove-all').disabled,false);
      if(t.trend.frozen){$('pause-play').click();await wait(()=>!t.trend.frozen,'Play button');assert.equal(t.trend.zoom,null);}
    }
    $('save').click();await wait(()=>$('savestatus').textContent.includes('Salvat')||$('savestatus').textContent.includes('salvat'),'save settings');
    $('disconnect').click();await wait(()=>!t.connected,'Disconnect button');
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({result:'PASS',checks:['connect','node selection','scan','autoscale dual Y','format popup/cancel','CSV and Parquet Record/Stop','page reload','Pause/Play','markers X1/X2','zoom','X-axis scroll/pan','record continues while frozen','save settings','disconnect'],files}));
  }finally{dom.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
