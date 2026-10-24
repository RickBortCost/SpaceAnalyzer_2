"""Display-only reduction, original quality/counts and cache invalidation."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_recordings import Recorder,NODES,ROWS
from recording_reader import RecordingLibrary
from recording_preview import PeakSeries,ReplayCache,VIEWS

class Previews(unittest.TestCase):
    def test_bounded_envelope_preserves_peaks_quality_and_duplicates(self):
        reducer=PeakSeries(1000)
        for i in range(50000):
            v=1e6 if i==17000 else -1e6 if i==31000 else i%10
            q='Bad' if i==15000 else 'Uncertain' if i==25000 else 'Good'
            reducer.add(i,v if q=='Good' else None,q)
        points=reducer.result();self.assertLessEqual(len(points),1000)
        self.assertEqual(points[0][0],0);self.assertEqual(points[-1][0],49999)
        self.assertIn(1e6,[p[1] for p in points]);self.assertIn(-1e6,[p[1] for p in points])
        self.assertTrue({'Bad','Uncertain'}.issubset({p[2] for p in points}))
        unordered=PeakSeries(1000)
        for i in range(10000):unordered.add(-50 if i==5003 else i,1,'Good')
        self.assertEqual(unordered.result()[0][0],-50)
        exact=PeakSeries(1000);exact.add(1,1,'Good');exact.add(1,2,'Good')
        self.assertEqual(exact.result(),[[1,1,'Good'],[1,2,'Good']])

    def test_repeated_view_does_not_rescan_and_changed_file_does(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.csv';path.write_text('timestamp_ms,node_id,value_numeric,good\n1,A,1,True\n2,A,2,True\n')
            library=RecordingLibrary(folder)
            first=library.load(path.name,['A'])
            with patch.object(library,'_iter_rows',side_effect=AssertionError('unexpected full scan')):
                again=library.load(path.name,['A']);self.assertEqual(again,first)
                again['series']['A'][0][1]=999
                self.assertEqual(library.load(path.name,['A'])['series']['A'][0][1],1)
            with path.open('a') as stream:stream.write('3,A,3,True\n')
            self.assertEqual(library.load(path.name,['A'])['numeric_rows'],3)
            self.assertEqual(library.describe(path.name)['signals'][0]['display_name'],'A')
            path.with_suffix('.metadata.json').write_text(json.dumps({'signals':[{'nodeid':'A','display_name':'Renamed'}],'started_at':'2026-01-01T00:00:00Z','duration_seconds':1}))
            self.assertEqual(library.describe(path.name)['signals'][0]['display_name'],'Renamed')

    def test_parquet_interval_reads_only_intersecting_row_groups(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.parquet'
            table=pa.table({'timestamp_ms':[float(i) for i in range(1000)],'node_id':['A']*1000,'value_numeric':[float(i) for i in range(1000)]})
            pq.write_table(table,path,row_group_size=100)
            library=RecordingLibrary(folder);seen=[];real=pq.ParquetFile.iter_batches
            def batches(reader,*args,**kwargs):seen.extend(kwargs.get('row_groups',[]));return real(reader,*args,**kwargs)
            with patch.object(pq.ParquetFile,'iter_batches',batches):result=library.load(path.name,['A'],205,215)
            self.assertEqual(seen,[2]);self.assertEqual(result['numeric_rows'],11)
            self.assertEqual(result['series']['A'][0],[205.,205.,'Good'])

    def test_cache_is_bounded(self):
        cache=ReplayCache(max_points=4,max_items=2)
        cache.put('a',{'series':{'A':[[1,1,'Good']]*3}})
        cache.put('b',{'series':{'B':[[1,1,'Good']]*3}})
        self.assertIsNone(cache.get('a'));self.assertLessEqual(cache.points,4)

if __name__=='__main__':unittest.main(verbosity=2)
