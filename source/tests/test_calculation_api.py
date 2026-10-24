"""End-to-end HTTP, actual OPC UA acquisition, replay, FFT and CSV/Parquet integration."""
import json
import math
import time
import unittest
from pathlib import Path
from test_integration import client, make_server
from asyncua import ua
from analysis_tools import export_rows
from test_calculations import definition, A, B, series_rows

class CalculationAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client.initialize();cls.http=client.app.test_client();cls.headers={'X-OPCUA-Token':client.SESSION_TOKEN}
        cls.server,cls.endpoint,cls.root,cls.nodes=client.runtime.run(make_server(),timeout=35)
        async def second():return await cls.root.add_variable(ua.NodeId('Power',2),'Potenza',2.)
        cls.power=client.runtime.run(second())
    @classmethod
    def tearDownClass(cls):
        client.runtime.run(client.disconnect_command());client.runtime.run(cls.server.stop())
    def setUp(self):
        client.runtime.run(client.disconnect_command());client.runtime.active_config=None
        client.save_config(client.validated_config({'source':'opcua'}))
        with client.formula_store.lock:
            client.formula_store.formulas=[];client.formula_store.revision=0;client.formula_store._write()
        with client.runtime.lock:client.reset_calculations('opcua')
        client.runtime.run(client.apply_selection([]))
    def tearDown(self):
        client.runtime.run(client.disconnect_command())
        with client.formula_store.lock:client.formula_store.formulas=[];client.formula_store._write()
        with client.runtime.lock:client.reset_calculations('opcua')
    def post(self,url,data,code=200):
        result=self.http.post(url,json=data,headers=self.headers)
        self.assertEqual(result.status_code,code,result.json);return result.json
    def wait(self,fn,timeout=8):
        end=time.time()+timeout
        while time.time()<end:
            if fn():return
            time.sleep(.03)
        self.fail('Timeout acquisizione calcolata: '+str(client.calculations.status()))
    def test_validation_crud_modes_cycle_and_workspace_persistence(self):
        f=self.post('/api/calculations/save',definition())['formula']
        self.post('/api/calculations/validate',{'formula':definition(expression='__import__("os")')},400)
        g=self.post('/api/calculations/save',dict(name='P',expression='[D]*100',bindings={'D':dict(nodeid=f['nodeid'])}))['formula']
        self.post('/api/calculations/save',{**f,'expression':'[P]','bindings':{'P':dict(nodeid=g['nodeid'])}},400)
        self.post('/api/calculations/delete',{'id':f['id']},400)
        duplicate=self.post('/api/calculations/duplicate',{'id':g['id']})['formula'];self.assertFalse(duplicate['active'])
        self.post('/api/workspace',dict(schema=1,config={},selected_nodes=[],live={'charts':[]},history={'charts':[]}))
        saved=client.load_workspace();self.assertEqual(len(saved['calculations']['formulas']),3)
        self.assertEqual(json.loads(client.formula_store.path.read_text())['formulas'][0]['nodeid'],f['nodeid'])
        self.post('/api/calculations/delete',{'id':duplicate['id']});self.post('/api/calculations/delete',{'id':g['id']});self.post('/api/calculations/delete',{'id':f['id']})
    def test_actual_live_acquisition_preview_record_stop_quality_and_snapshot(self):
        first=self.nodes[0].nodeid.to_string();second=self.power.nodeid.to_string()
        data=dict(name='Delta live',expression='[A]-[B]',bindings={'A':dict(nodeid=first),'B':dict(nodeid=second)})
        f=self.post('/api/calculations/save',data)['formula']
        self.post('/api/select',{'nodeids':[first,second]});self.post('/api/connect',dict(endpoint=self.endpoint,security='None',sample_ms=10))
        self.wait(lambda:len(client.runtime.samples.get(f['nodeid'],[]))>20)
        values=self.http.get('/api/values').json;derived=next(v for v in values['values'] if v['nodeid']==f['nodeid'])
        self.assertEqual(derived['value'],19.5);self.assertTrue(derived['good']);self.assertEqual(client.runtime.selected,[first,second])
        preview=self.post('/api/calculations/preview',dict(formula=f,mode='live'));self.assertEqual(preview['value'],19.5)
        for fmt in ('csv','parquet'):
            state=self.post('/api/record/start',dict(format=fmt,selected_nodes=[dict(nodeid=n,display_name=n) for n in (first,second,f['nodeid'])]))['recording']
            self.post('/api/calculations/save',{**f,'name':'Durante Record'},400)
            before=client.recording.status()['cycles_received'];self.wait(lambda:client.recording.status()['cycles_received']>=before+40)
            self.post('/api/record/stop',{});self.wait(lambda:not client.recording.busy,30)
            state=client.recording.status();self.assertEqual(state['state'],'stopped',state)
            path=Path(state['path']);rows=list(client.recording_library._iter_rows(path))
            raw={float(r['timestamp_ms']) for r in rows if r['node_id']==first}
            calc={float(r['timestamp_ms']) for r in rows if r['node_id']==f['nodeid']}
            self.assertEqual(raw,calc,'Stop drains calculated samples through the last acquired timestamp')
            self.assertTrue(all(float(r['value_numeric'])==19.5 for r in rows if r['node_id']==f['nodeid']))
            meta=json.loads(path.with_suffix('.metadata.json').read_text());self.assertTrue(meta['calculation_formulas'])
            self.assertTrue(all(r['source_timestamp'] and r['local_timestamp'] and not r.get('server_timestamp') for r in rows if r['node_id']==f['nodeid']))
        before=client.runtime.calculation_generation;self.post('/api/samples/clear',{});self.assertGreater(client.runtime.calculation_generation,before)
        self.wait(lambda:len(client.runtime.samples.get(f['nodeid'],[]))>3)
        self.post('/api/select',{'nodeids':[first]});self.wait(lambda:client.runtime.calculated_latest.get(f['nodeid'],{}).get('quality')=='Bad')
    def test_historical_preview_graph_markers_fft_interval_export_and_materialization(self):
        f=self.post('/api/calculations/save',definition())['formula'];nid=f['nodeid'];start=1700000000000;end=start+10230
        for fmt in ('csv','parquet'):
            path=client.RECORDINGS_DIR/('calculation_api.'+fmt);path.unlink(missing_ok=True);export_rows(series_rows(1024),path,fmt)
            original=path.read_bytes();body=dict(source='repository',name=path.name,signal_ids=[A,B,nid],start_ms=start,end_ms=end,max_points=1000)
            preview=self.post('/api/calculations/preview',dict(formula=f,mode='recorded',source='repository',name=path.name,start_ms=start,end_ms=end))
            self.assertTrue(preview['series']);self.assertEqual(preview['quality'],'Good')
            view=self.post('/api/recordings/data',body)['recording'];self.assertEqual(len(view['signals']),3);self.assertEqual(view['signals'][-1]['points_original'],1024)
            expected=8+3*math.sin(2*math.pi*10/32)
            markers=self.post('/api/recordings/markers',{**body,'x1':start+100,'x2':start+200})['markers'];m=next(s for s in markers['signals'] if s['nodeid']==nid)
            self.assertAlmostEqual(m['x1']['value'],expected)
            fft=self.post('/api/analysis/fft',dict(source='repository',name=path.name,signal_ids=[nid],x1=start,x2=end,options={'block_size':1024}))['spectrum']
            self.assertAlmostEqual(fft['peak_hz'],3.125,places=5);self.assertAlmostEqual(fft['peak_amplitude'],3,delta=.02)
            for output in ('csv','parquet'):
                exported=self.post('/api/analysis/export',dict(source='repository',name=path.name,signal_ids=[nid],x1=start+100,x2=start+300,format=output));self.assertEqual(exported['rows'],21)
                saved=self.post('/api/calculations/materialize',{**body,'start_ms':start+100,'end_ms':start+300,'format':output})['recording'];self.assertEqual(saved['rows_written'],63)
                self.assertEqual(len(saved['signals']),3)
            self.assertEqual(path.read_bytes(),original)

if __name__=='__main__':unittest.main()
