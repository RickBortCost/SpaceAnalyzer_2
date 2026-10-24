"""HTTP -> real worker -> mixed recordings -> graphs, formulas, FFT and export."""
import io
import json
from pathlib import Path
import time
import unittest
from test_integration import client
from test_recording_compare import fixture, START


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client.initialize()
        cls.http=client.app.test_client(); cls.headers={'X-OPCUA-Token':client.SESSION_TOKEN}
        client.RECORDINGS_DIR.mkdir(parents=True,exist_ok=True)
        cls.a,_=fixture(client.RECORDINGS_DIR,'compare_fast.csv',count=1024)
        cls.b,_=fixture(client.RECORDINGS_DIR,'compare_slow.parquet',count=1024,value_offset=20)

    @classmethod
    def tearDownClass(cls): client.comparison_service.close()

    def post(self, route, body, code=200):
        r=self.http.post(route,json=body,headers=self.headers)
        self.assertEqual(r.status_code,code,r.json)
        return r.json

    def wait(self, ident):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            r=self.http.get('/api/recordings/compare/'+ident)
            self.assertEqual(r.status_code,200,r.json)
            if r.json['job']['state']!='running': return r.json['job']
            time.sleep(.03)
        self.fail('Comparison worker timed out')

    def compare(self, **extra):
        started=self.post('/api/recordings/compare',{'files':[{'name':self.a.name},{'name':self.b.name}],**extra})['job']
        ready=self.wait(started['id'])
        self.assertEqual(ready['state'],'ready',ready)
        return ready['recording']

    def test_worker_graph_units_markers_fft_and_export(self):
        file=self.compare(); ids=[s['nodeid'] for s in file['signals']]
        body={'source':'local','name':file['name'],'signal_ids':ids,'start_ms':START,'end_ms':START+10230}
        data=self.post('/api/recordings/data',body)['recording']
        self.assertEqual(len(data['series']),2)
        self.assertEqual(data['signals'][0]['unit'],'bar')
        self.assertEqual(data['signals'][1]['points_original'],1024)
        marker=self.post('/api/recordings/markers',{**body,'x1':START+30,'x2':START+90})['markers']
        self.assertAlmostEqual(marker['signals'][1]['x1']['value']-marker['signals'][0]['x1']['value'],20)
        fft=self.post('/api/analysis/fft',{**body,'signal_ids':[ids[0]],'x1':START,'x2':START+10230,'options':{'block_size':1024}})['spectrum']
        self.assertAlmostEqual(fft['peak_hz'],3.125)
        out=self.post('/api/analysis/export',{**body,'format':'csv','x1':START+100,'x2':START+200})
        self.assertEqual(out['rows'],22)
        files=self.http.get('/api/recordings').json['files']
        self.assertTrue(any(f['name']==file['name'] and f['source']=='local' for f in files))

    def test_formula_across_distinct_files_and_persistent_workspace(self):
        file=self.compare(); ids=[s['nodeid'] for s in file['signals']]
        formula=self.post('/api/calculations/save',{'name':'Delta tra file','expression':'[B]-[A]',
            'bindings':{'A':{'nodeid':ids[0]},'B':{'nodeid':ids[1]}}})['formula']
        try:
            data=self.post('/api/recordings/data',{'source':'local','name':file['name'],'signal_ids':[formula['nodeid']]})['recording']
            self.assertEqual(len(data['series'][formula['nodeid']]),1024)
            self.assertTrue(all(abs(p[1]-20)<1e-10 for p in data['series'][formula['nodeid']]))
            saved={'schema':1,'config':{},'selected_nodes':[],'history':{'charts':[{'context':{'file':file,'chosen':ids,'loaded':True}}]},'live':{'charts':[]}}
            self.post('/api/workspace',saved)
            client._clear_imported_recordings()
            restored=client.load_workspace()['history']['charts'][0]['context']['file']
            data=self.post('/api/recordings/data',{'source':'local','name':restored['name'],'signal_ids':ids})['recording']
            self.assertEqual(data['numeric_rows'],2048)
        finally: self.post('/api/calculations/delete',{'id':formula['id']})

    def test_validation_auth_and_clock_errors_are_explicit(self):
        for body in ({},{'files':[{'name':self.a.name}]},{'files':[{'name':self.a.name}]*2},
            {'files':[{'name':'../outside.csv'},{'name':self.b.name}]},
            {'files':[{'name':self.a.name},{'name':self.b.name,'offset_ms':'NaN'}]},
            {'files':[{'name':self.a.name},{'name':self.b.name}],'mode':'guess'}):
            self.post('/api/recordings/compare',body,400)
        self.assertEqual(self.http.post('/api/recordings/compare',json={}).status_code,403)
        self.assertEqual(self.http.get('/api/recordings/compare/missing').status_code,400)
        started=self.post('/api/recordings/compare',{'files':[{'name':self.a.name},{'name':self.b.name,'offset_ms':3600000}],'coverage':'intersection'})['job']
        failed=self.wait(started['id']); self.assertEqual(failed['state'],'error')
        self.assertIn('intervallo temporale comune',failed['error'])

    def test_multiple_external_csv_parquet_imports_can_be_compared(self):
        imported=self.http.post('/api/recordings/import',headers=self.headers,data={'files':[
            (io.BytesIO(self.a.read_bytes()),'external.csv'),(io.BytesIO(self.b.read_bytes()),'external.parquet')]})
        self.assertEqual(imported.status_code,200,imported.json)
        files=imported.json['recordings']; self.assertEqual(len(files),2)
        self.assertEqual(files[0]['duration_seconds'],10.23)
        request_files=[{'source':'local','name':f['name']} for f in files]
        ident=self.post('/api/recordings/compare',{'files':request_files})['job']['id']
        ready=self.wait(ident); self.assertEqual(ready['state'],'ready',ready)
        self.assertTrue(any('external.csv' in s['display_name'] for s in ready['recording']['signals']))
        self.assertTrue(any('external.parquet' in s['display_name'] for s in ready['recording']['signals']))


if __name__=='__main__':unittest.main(verbosity=2)
