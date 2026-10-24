"""HTTP DAT import, hash-linked time axis, multiple files, calculations and safety."""
import io,json,shutil,tempfile,unittest
from pathlib import Path
from test_integration import client
from test_pda3 import fixture,BASE,NODES
from pda3_fixture.window_export import process_job
from pda3.storage import atomic_json

class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client.initialize();cls.http=client.app.test_client();cls.headers={'X-OPCUA-Token':client.SESSION_TOKEN}
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);_,job=fixture(cls.root)
        cls.manifest=process_job(job);cls.data=(job.parent/'window.dat').read_bytes()
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def upload(self,meta=None,offset=None,two=False):
        files=[(io.BytesIO(self.data),'one.dat')]
        if meta is not None:files.append((io.BytesIO(json.dumps(meta).encode()),'one.json'))
        if two:files.extend([(io.BytesIO(self.data),'two.dat'),(io.BytesIO(json.dumps({**meta,'real_start_ms':BASE+600000}).encode()),'two.json')])
        data={'files':files,'join_pda3':'true'}
        if offset is not None:data['pda3_timezone_offset_minutes']=str(offset)
        return self.http.post('/api/recordings/import',data=data,headers=self.headers)
    def post(self,route,data):
        r=self.http.post(route,json=data,headers=self.headers);self.assertEqual(r.status_code,200,r.json);return r.json
    def test_multifile_import_utc_sequence_replay_markers_and_formula(self):
        result=self.upload(self.manifest,two=True);self.assertEqual(result.status_code,200,result.json)
        seq=result.json['sequence'];self.assertIsNotNone(seq);self.assertEqual(seq['duration_seconds'],1200)
        args={'source':'local','name':seq['name'],'signal_ids':[NODES[0]['nodeid']]}
        loaded=self.post('/api/recordings/data',{**args,'start_ms':BASE,'end_ms':BASE+100})['recording']
        self.assertEqual(loaded['series'][NODES[0]['nodeid']][0][0],BASE);self.assertEqual(len(loaded['series'][NODES[0]['nodeid']]),11)
        markers=self.post('/api/recordings/markers',{**args,'x1':BASE+10,'x2':BASE+600010})['markers']
        self.assertEqual(markers['signals'][0]['x2']['timestamp_ms'],BASE+600010)
        f=self.post('/api/calculations/save',{'name':'PDA3 doubled','expression':'[A]*2','bindings':{'A':{'nodeid':NODES[0]['nodeid']}}})['formula']
        calculated=self.post('/api/recordings/data',{**args,'signal_ids':[f['nodeid']],'start_ms':BASE,'end_ms':BASE+1000})['recording']
        self.assertTrue(calculated['series'][f['nodeid']]);self.post('/api/calculations/delete',{'id':f['id']})
        listed=self.http.get('/api/recordings').json['files'];self.assertTrue(any(f['source']=='local' and f['name']==seq['name'] for f in listed))
    def test_hash_mismatch_unknown_timezone_and_unknown_profile_rejected(self):
        self.assertEqual(self.upload({**self.manifest,'dat_sha256':'bad'}).status_code,400)
        self.assertEqual(self.upload().status_code,400)
        accepted=self.upload(offset=120);self.assertEqual(accepted.status_code,200,accepted.json)
        self.assertEqual(len(accepted.json['recording']['signals']),64)
        bad=bytearray(self.data);bad[100]^=1
        result=self.http.post('/api/recordings/import',data={'files':(io.BytesIO(bad),'unknown.dat'),'pda3_timezone_offset_minutes':'120'},headers=self.headers)
        self.assertEqual(result.status_code,400)
    def test_dat_recording_and_export_are_removed(self):
        for route in ('/api/pda3/status','/api/record/iba/status'):self.assertEqual(self.http.get(route).status_code,404)
        for fmt in ('pda3','dat'):
            r=self.http.post('/api/analysis/export',json={'source':'live','format':fmt,'x1':BASE,'x2':BASE+599990,'signal_ids':[NODES[0]['nodeid']]},headers=self.headers)
            self.assertEqual(r.status_code,400);self.assertIn('CSV o Parquet',r.json['error'])
        from recording_storage import storage_profile
        for fmt in ('pda3','dat'):
            with self.assertRaisesRegex(ValueError,'rimossa nella 3.0'):storage_profile(fmt)

if __name__=='__main__':unittest.main(verbosity=2)
