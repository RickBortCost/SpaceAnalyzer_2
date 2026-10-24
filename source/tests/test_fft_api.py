import sys,math,unittest
from pathlib import Path
from collections import deque
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_ads import app
from test_fft_engine import sine
class API(unittest.TestCase):
    def setUp(self):
        app.initialize();self.http=app.app.test_client();self.headers={'X-OPCUA-Token':app.SESSION_TOKEN};self.nid='ads:FFT.Signal';self.start=1700000000000
        with app.runtime.lock:
            app.runtime.samples={self.nid:deque([[self.start+i*10,v,'Good',0,None,None,str(v),'Double','ADS_OK',10,None,str(116444736000000000+1700000000*10000000+i*100000)] for i,v in enumerate(sine(4096))])}
    def post(self,**extra):
        body=dict(source='live',signal_ids=[self.nid],x1=self.start,x2=self.start+40950,options={'block_size':1024,'output':'rms'})
        body.update(extra)
        if 'live_seconds' not in body and 'snapshot_id' not in body:
            snap=self.http.post('/api/analysis/live-snapshot',json=body,headers=self.headers)
            if snap.status_code==200:body['snapshot_id']=snap.json['snapshot_id']
        return self.http.post('/api/analysis/fft',json=body,headers=self.headers)
    def test_fft_requires_pause_and_keeps_source_time(self):
        self.assertEqual(self.post(live_seconds=25).status_code,400)
        r=self.post(x1=self.start+15950);self.assertEqual(r.status_code,200,r.json)
        data=r.json['spectrum'];self.assertEqual(data['sample_hz'],100);self.assertEqual(data['samples_original'],2501)
        self.assertAlmostEqual(data['peak_amplitude'],3/math.sqrt(2),places=7)
        self.assertEqual(data['time_basis'],'ADS FILETIME originale')
    def test_exclusion_and_errors(self):
        app.runtime.samples[self.nid][1500][2]='Bad'
        r=self.post(options={'block_size':1024,'gap_policy':'skip'});self.assertEqual(r.status_code,200,r.json);self.assertGreater(r.json['spectrum']['blocks_excluded'],0)
        self.assertEqual(self.post(options={'block_size':1024,'gap_policy':'reject'}).status_code,400)
        self.assertEqual(self.post(live_seconds=-1).status_code,400)
    def test_paused_originals_survive_live_buffer_eviction(self):
        end=self.start+10000
        response=self.http.post('/api/analysis/live-snapshot',json={'signal_ids':[self.nid],'x1':self.start,'x2':end},headers=self.headers)
        self.assertEqual(response.status_code,200,response.json);key=response.json['snapshot_id'];self.assertEqual(response.json['samples'],1001)
        # Acquisition moves on while the chart is paused; original interval ages out.
        with app.runtime.lock:app.runtime.samples[self.nid]=deque(list(app.runtime.samples[self.nid])[2000:])
        missing=self.post(x2=end,snapshot_id=None,options={'block_size':512});self.assertEqual(missing.status_code,400);self.assertIn('Pa',missing.json['error'])
        saved=self.post(x2=end,snapshot_id=key,options={'block_size':512});self.assertEqual(saved.status_code,200,saved.json);self.assertEqual(saved.json['spectrum']['samples_original'],1001);self.assertEqual(saved.json['spectrum']['sample_hz'],100)
        exported=self.http.post('/api/analysis/export',json={'source':'live','signal_ids':[self.nid],'x1':self.start,'x2':end,'snapshot_id':key,'format':'csv'},headers=self.headers)
        self.assertEqual(exported.status_code,200,exported.json);self.assertEqual(exported.json['rows'],1001)
        self.http.post('/api/analysis/live-snapshot/release',json={'snapshot_id':key},headers=self.headers)
        self.assertEqual(self.post(x2=end,snapshot_id=key).status_code,400)
    def test_snapshot_capacity_and_validation(self):
        from live_analysis import Snapshots
        store=Snapshots(max_items=2,max_points=4);a,_=store.create({'n':[[1,2],[2,3]]});b,_=store.create({'n':[[3,4],[4,5]]});c,_=store.create({'n':[[5,6]]})
        with self.assertRaises(ValueError):store.get(a)
        self.assertEqual(store.get(b)['n'][0][0],3);self.assertEqual(store.get(c)['n'][0][0],5)
        for body in [{}, {'signal_ids':'bad'}, {'signal_ids':[self.nid],'x1':0,'x2':1}]:
            self.assertEqual(self.http.post('/api/analysis/live-snapshot',json=body,headers=self.headers).status_code,400)
    def test_concurrent_calculation_is_bounded(self):
        import fft_service
        fft_service._busy.acquire()
        try:self.assertEqual(self.post().status_code,400)
        finally:fft_service._busy.release()
if __name__=='__main__':unittest.main(verbosity=2)
