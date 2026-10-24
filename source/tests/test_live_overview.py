"""Session display history must outlive raw FIFO eviction without feeding analysis."""
import unittest
from test_ads import app
from live_overview import LiveBuffer,Overview

class OverviewTests(unittest.TestCase):
    def test_bounded_history_retains_first_latest_extrema_and_quality(self):
        b=LiveBuffer(maxlen=10)
        for i in range(50000):
            value=-9999 if i==123 else 9999 if i==222 else i%100
            q='Bad' if i==140 else 'Uncertain' if i==150 else 'Good'
            b.append([i,None if q!='Good' else value,q,0,'source',None,str(value),'Double','OK',1])
        self.assertEqual(len(b),10)
        p=b.overview.points()
        self.assertEqual(p[0][0],0);self.assertEqual(p[-1][0],49999)
        self.assertIn((123,-9999,'Good'),p);self.assertIn((222,9999,'Good'),p)
        self.assertTrue(any(x[2]=='Bad' for x in p));self.assertTrue(any(x[2]=='Uncertain' for x in p))
        self.assertLessEqual(len(p),7*257+1);self.assertEqual(b.overview.count,50000)
        self.assertEqual(b[-1][4],'source')

    def test_resize_does_not_replay_or_reset_history(self):
        b=LiveBuffer(maxlen=3)
        for i in range(1000):b.append([i,float(i),'Good'])
        resized=LiveBuffer(b,maxlen=2);resized.append([1000,2.,'Good'])
        self.assertEqual(len(resized),2);self.assertEqual(resized.overview.count,1001)
        self.assertEqual(resized.overview.points()[0][0],0)
        self.assertEqual(resized.overview.points()[-1][0],1000)

    def test_formula_latest_revision_does_not_keep_stale_peak(self):
        b=LiveBuffer(maxlen=20)
        b.append([0,1.,'Good']);b.append([1,999.,'Good'])
        b[-1]=[1,2.,'Good'];b.append([2,3.,'Good'])
        self.assertEqual(b.overview.points(),[(0,1.,'Good'),(1,2.,'Good'),(2,3.,'Good')])
        self.assertEqual(b.overview.count,3)

class APITests(unittest.TestCase):
    def setUp(self):
        self.http=app.app.test_client();self.old=app.runtime.samples
        self.nid='ads:MAIN.Wave';self.base=1700000000000
        b=LiveBuffer(maxlen=16)
        for i in range(3000):b.append([self.base+i, float(i%100),'Good',0,None,None,str(i),'Double','OK',1])
        with app.runtime.lock:app.runtime.samples={self.nid:b}
    def tearDown(self):
        with app.runtime.lock:app.runtime.samples=self.old
    def test_api_all_outlives_fifo_and_recent_mode_still_returns_originals(self):
        all_data=self.http.get('/api/values?view=all').get_json()
        recent=self.http.get('/api/values?view=window').get_json()
        self.assertEqual(all_data['samples'][self.nid][0][0],self.base)
        self.assertEqual(all_data['samples'][self.nid][-1][0],self.base+2999)
        self.assertEqual(len(recent['samples'][self.nid]),16)
        self.assertEqual(recent['samples'][self.nid][0][0],self.base+2984)
        self.assertEqual(all_data['diagnostics']['display_range'],{'xmin':self.base,'xmax':self.base+2999})
        self.assertEqual(all_data['diagnostics']['session_samples'][self.nid],3000)
        self.assertEqual(self.http.get('/api/values?view=wrong').status_code,400)
        # FFT/export input stays raw, never the aggregated history.
        raw=list(app.analysis_rows({'source':'live','signal_ids':[self.nid]}))
        self.assertEqual(len(raw),16);self.assertEqual(raw[0]['timestamp_ms'],self.base+2984)
        with self.assertRaisesRegex(ValueError,'Campioni originali'):
            app.analysis_rows({'source':'live','signal_ids':[self.nid],'x1':self.base,'x2':self.base+2999})
    def test_clear_removes_old_session_display(self):
        response=self.http.post('/api/samples/clear',json={},headers={'X-OPCUA-Token':app.SESSION_TOKEN})
        self.assertEqual(response.status_code,200)
        data=self.http.get('/api/values').get_json()
        self.assertIsNone(data['diagnostics']['display_range'])
        self.assertTrue(all(not p for p in data['samples'].values()))

if __name__=='__main__':unittest.main(verbosity=2)
