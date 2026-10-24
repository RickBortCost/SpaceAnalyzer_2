"""Original full-session marker/formula regression beyond the raw FIFO."""
import math
from pathlib import Path
import tempfile
import unittest
from test_integration import client as app
from live_archive import LiveArchive

A='ns=2;s=IntervalA';B='ads:MAIN.IntervalB';BASE=1700000000000.


def point(t,v,q='Good'):
    return [t,v,q,0,None,None,str(v),'Double',q,10,None,None]


class FullSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize();cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    def setUp(self):
        app.runtime.run(app.disconnect_command());app.live_archive.new_session()
        with app.runtime.lock:
            app.runtime.sample_ms=10
            app.runtime.samples={n:app.live_buffer(n,maxlen=5) for n in (A,B)}
            for i in range(2400):
                app.runtime.samples[A].append(point(BASE+i*10,float(i)))
                app.runtime.samples[B].append(point(BASE+i*10,float(2*i)))
    def post(self,path,data,code=200):
        result=self.http.post(path,json=data,headers=self.headers)
        self.assertEqual(result.status_code,code,result.json);return result.json
    def body(self,**extra):
        return dict(source='live',signal_ids=[A,B],x1=BASE+1000,x2=BASE+2000,**extra)
    def test_old_markers_and_statistics_use_every_original(self):
        self.assertEqual(len(app.runtime.samples[A]),5)
        body=self.body();markers=self.post('/api/analysis/markers',body)['results']
        self.assertEqual(markers[0]['x1']['value'],100);self.assertEqual(markers[0]['x2']['value'],200)
        self.assertEqual(markers[1]['x1']['value'],200);self.assertEqual(markers[1]['x2']['value'],400)
        rows=self.post('/api/analysis/quick',body)['results']
        self.assertEqual(rows[0]['count'],101);self.assertAlmostEqual(rows[0]['statistics']['mean'],150)
        self.assertEqual(rows[1]['statistics']['delta'],200)
    def test_formula_combines_protocols_before_interval_statistics(self):
        for expr,mean,last in [('([A]-[B])*2',-300,-400),('mean([A],[B])',225,300)]:
            formula=dict(expression=expr,bindings={'A':{'nodeid':A},'B':{'nodeid':B}},alignment='hold',max_age_ms=10)
            row=self.post('/api/analysis/quick',self.body(formula=formula))['results'][0]
            self.assertEqual(row['count'],101);self.assertAlmostEqual(row['statistics']['mean'],mean)
            self.assertEqual(row['statistics']['last'],last)
    def test_snapshot_preserves_history_and_original_revision(self):
        snap=self.post('/api/analysis/live-snapshot',dict(source='live',signal_ids=[A],x1=BASE,x2=BASE+23990))['snapshot_id']
        with app.runtime.lock:app.runtime.samples[A].append(point(BASE+1000,999.))
        body=self.body()
        self.assertEqual(self.post('/api/analysis/markers',body)['results'][0]['x1']['value'],999)
        frozen=self.post('/api/analysis/markers',{**body,'snapshot_id':snap})['results']
        self.assertEqual(frozen[0]['x1']['value'],100)
        # A chart snapshot also covers other acquired inputs for interval formulas.
        self.assertEqual(frozen[1]['x1']['value'],200)
        app.live_archive.new_session()
        self.post('/api/analysis/markers',{**body,'snapshot_id':snap},400)
    def test_derived_revisions_are_not_counted_twice(self):
        nid='calc:'+'a'*32
        with app.runtime.lock:
            p=app.live_buffer(nid,maxlen=5);app.runtime.samples[nid]=p
            p.append(point(BASE+1000,1.))
        snap=self.post('/api/analysis/live-snapshot',dict(source='live',signal_ids=[nid],x1=BASE,x2=BASE+2000))['snapshot_id']
        with app.runtime.lock:p[-1]=point(BASE+1000,2.)
        body=dict(source='live',signal_ids=[nid],x1=BASE,x2=BASE+2000)
        row=self.post('/api/analysis/quick',body)['results'][0]
        self.assertEqual(row['count'],1);self.assertEqual(row['statistics']['last'],2)
        row=self.post('/api/analysis/quick',{**body,'snapshot_id':snap})['results'][0]
        self.assertEqual(row['statistics']['last'],1)
    def test_causal_alignment_bad_quality_and_division_by_zero(self):
        app.live_archive.new_session()
        with app.runtime.lock:
            app.runtime.samples={n:app.live_buffer(n,maxlen=2) for n in (A,B)}
            for t,v in [(0,1),(10,2),(20,3),(30,4)]:app.runtime.samples[A].append(point(BASE+t,v))
            for t,v,q in [(5,10,'Good'),(15,20,'Bad'),(25,30,'Good')]:app.runtime.samples[B].append(point(BASE+t,v,q))
        body={**self.body(),'x1':BASE,'x2':BASE+30}
        f=dict(expression='[A]-[B]',bindings={'A':{'nodeid':A},'B':{'nodeid':B}},alignment='hold',max_age_ms=10)
        row=self.post('/api/analysis/quick',{**body,'formula':f})['results'][0]
        self.assertEqual(row['statistics']['last'],-26);self.assertEqual(row['count'],4);self.assertEqual(row['excluded'],3)
        exact=self.post('/api/analysis/quick',{**body,'formula':{**f,'alignment':'exact'}})['results'][0]
        self.assertEqual(exact['count'],0)
        zero=self.post('/api/analysis/quick',{**body,'formula':{**f,'expression':'[A]/([B]-[B])'}})['results'][0]
        self.assertEqual(zero['count'],0);self.assertIn('zero',zero['last_status'])
    def test_formula_does_not_allow_python_execution(self):
        for expr in ['__import__("os")','[A].real','open("x")']:
            self.post('/api/analysis/quick',self.body(formula=dict(expression=expr,bindings={'A':{'nodeid':A}})),400)


class ArchiveLimits(unittest.TestCase):
    def test_storage_failure_is_explicit_and_does_not_throw_on_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=LiveArchive(folder,max_bytes=10)
            try:
                generation=archive.new_session();archive.append(generation,A,point(BASE,1.))
                with self.assertRaisesRegex(ValueError,'spazio'):
                    archive.flush(generation)
            finally:archive.close()
            self.assertFalse(list(Path(folder).rglob('*.sqlite')))
    def test_bounded_pending_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=LiveArchive(folder,max_pending_bytes=10)
            try:
                gen=archive.new_session();archive.append(gen,A,point(BASE,1.))
                self.assertIn('coda',archive.error);self.assertEqual(archive.sequence,0)
            finally:archive.close()


if __name__=='__main__':unittest.main(verbosity=2)
