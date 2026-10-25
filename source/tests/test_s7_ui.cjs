const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base,port]=process.argv.slice(2),errors=[],pages=[];
const wait=async(fn,label)=>{const end=Date.now()+12000;while(Date.now()<end){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,30));}throw Error('Timeout '+label+' '+errors.join(';'));};
async function page(){
 const html=await(await fetch(base)).text(),vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});pages.push(dom);
 const w=dom.window,$=id=>w.document.getElementById(id);
 w.fetch=(url,options)=>fetch(new URL(url,base),options);w.AbortController=globalThis.AbortController;
 w.ResizeObserver=class{observe(){}disconnect(){}};
 const ctx=new Proxy({measureText:t=>({width:String(t).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});
 w.HTMLCanvasElement.prototype.getContext=()=>ctx;
 w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
 w.addEventListener('error',e=>errors.push(e.message));
 const scripts=[...w.document.querySelectorAll('script[src]')].map(s=>path.basename(new URL(s.src).pathname));
 w.eval(scripts.map(f=>fs.readFileSync(path.join(__dirname,'../static',f),'utf8')).join('\n')+'\nwindow.s7Test={switchSource,saveWorkspace,config,get selected(){return selected},get connected(){return connected},get charts(){return liveCharts},get record(){return recordState},stop(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
 await wait(()=>$('endpoint').value,'configuration loaded');await w.opcuaConfigReady;
 return {w,$,t:w.s7Test};
}
(async()=>{
 let {w,$,t}=await page();
 try{
  $('nav-connection').click();assert($('connection-dialog').open);
  await t.switchSource('s7');assert(!$('s7-connection-fields').hidden);assert($('ads-connection-fields').hidden);assert($('opcua-connection-fields').hidden);
  $('s7_family').value='300';$('s7_family').dispatchEvent(new w.Event('change'));assert.equal($('s7_slot').value,'2');
  $('s7_family').value='1200/1500';$('s7_family').dispatchEvent(new w.Event('change'));assert.equal($('s7_slot').value,'1');
  $('s7_host').value='127.0.0.1';$('s7_tcp_port').value=port;
  $('s7-tag-name').value='Carico Siemens';$('s7-tag-address').value='DB1.DBD0';$('s7-tag-type').value='REAL';$('s7-tag-add').click();
  await wait(()=>w.s7Tags.length===1&&!$('s7-tag-add').disabled,'catalog saved');assert($('s7-tag-list').textContent.includes('Carico Siemens'));
  $('s7-tag-list').querySelector('button').click();$('s7-tag-name').value='Carico A';$('s7-tag-add').click();
  await wait(()=>w.s7Tags[0].display_name==='Carico A'&&!$('s7-tag-add').disabled,'catalog alias edited');
  $('s7-test').click();await wait(()=>$('s7-info').textContent.includes('42.5'),'S7 handshake and real ReadVar test');
  $('connect').click();await wait(()=>t.connected,'S7 connected');assert($('s7_host').disabled);assert($('s7-tag-add').disabled);
  $('scan').click();await wait(()=>$('scan-body').textContent.includes('Carico A'),'catalog scan');
  $('manual-node').value='DB1.DBD0:REAL';$('add-node').click();
  await wait(()=>t.selected.has('s7:DB1.DBD0:REAL')&&!$('add-node').disabled,'absolute variable selected');
  await wait(()=>t.charts.charts[0].trend.series['s7:DB1.DBD0:REAL']?.length>15,'live S7 chart');
  assert($('toolbar-endpoint').textContent.includes('Rack'));
  $('record').click();await wait(()=>!$('record-modal').hidden,'record dialog');
  w.document.querySelector('input[name="record-format"][value="parquet"]').checked=true;
  $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>t.record.active&&t.record.cycles_received>15,'S7 binary recording');
  $('record-stop').click();await wait(()=>t.record.state==='stopped','record finalized');assert.equal(t.record.error,'');
  $('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'S7 disconnected');
  t.charts.active.name='Siemens trend';await t.saveWorkspace(true);
  await t.switchSource('ads');assert(!$('ads-connection-fields').hidden);assert.equal(t.selected.size,0);
  await t.switchSource('s7');assert(t.selected.has('s7:DB1.DBD0:REAL'));assert.equal(t.charts.active.name,'Siemens trend');
  await t.saveWorkspace(true);t.stop();await new Promise(r=>setTimeout(r,600));
  const old=w;({w,$,t}=await page());old.close();assert.equal(t.config().source,'s7');assert.equal(t.config().s7_tcp_port,Number(port));assert.equal(w.s7Tags[0].display_name,'Carico A');assert.equal(t.charts.active.name,'Siemens trend');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'PASS',checks:['S7 connection popup/presets','catalog add/edit/persistence','native S7 test connection','configured scan and live graph','Parquet Record/Stop','source profiles and workspace reload','no frontend exceptions']},null,2));
 }finally{t.stop();await new Promise(r=>setTimeout(r,600));for(const d of pages)d.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
