"""Deterministic projection checks: asynchronous originals, expiry and quality."""
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import pyarrow as pa
import pyarrow.parquet as pq
from pda3.resampling import project
from pda3.window_export import process_job
from pda3.service import iter_rows

BASE=1792584000000.

class Resampling(unittest.TestCase):
    def test_causal_grid_quality_duplicates_and_expiration(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'mixed.parquet'
            rows=[]
            for n,t,v,q in [('ads:A',0,5.,'Good'),('ads:A',15,10.,'Good'),('ads:A',15,11.,'Good'),
                             ('ads:A',25,None,'Bad'),('ads:A',80,9.,'Good'),('ns=2;s=B',5,3.,'Good')]:
                rows.append(dict(node_id=n,timestamp_ms=BASE+t,value_numeric=v,quality=q))
            # Disk sorting must handle late originals, without rewriting the archive.
            rows[0],rows[-1]=rows[-1],rows[0]
            pq.write_table(pa.Table.from_pylist(rows),path)
            before=path.read_bytes()
            result=project([path],{'ads:A':0,'ns=2;s=B':1},BASE,12,False,
                {'method':'previous','grid_ms':10,'max_age_ms':{'ads:A':30,'ns=2;s=B':30}})
            a,b=[struct.unpack('<12f',data) for data in result['data']]
            self.assertEqual(a[:3],(5.,5.,11.))
            self.assertTrue(all(math.isnan(v) for v in a[3:8]))
            self.assertEqual(a[8:],(9.,9.,9.,9.))
            self.assertTrue(math.isnan(b[0]));self.assertEqual(b[1:4],(3.,3.,3.))
            self.assertTrue(all(math.isnan(v) for v in b[4:]))
            self.assertEqual(result['duplicate_original_times'],1)
            self.assertEqual(path.read_bytes(),before)

    def test_real_dat_codec_replays_nan_gaps_and_manifest_policy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);parquet=root/'Parquet';parquet.mkdir()
            path=parquet/'mixed.parquet'
            rows=[dict(node_id='mqtt:plant/data#',timestamp_ms=BASE+t,value_numeric=v,quality=q)
                  for t,v,q in [(0,1.,'Good'),(15,2.,'Good'),(25,None,'Bad'),(80,9.,'Good')]]
            pq.write_table(pa.Table.from_pylist(rows),path)
            folder=root/'DAT'/'2026-10-21';folder.mkdir(parents=True)
            job=folder/'mixed.pda3job.json'
            job.write_text(json.dumps(dict(id='mixed',name='mixed.dat',sources=['Parquet/mixed.parquet'],
                nodes=[dict(nodeid='mqtt:plant/data#',display_name='MQTT')],start_ms=BASE,
                expected_samples=12,resampling={'method':'previous','grid_ms':10,'max_age_ms':{'mqtt:plant/data#':30}})))
            result=process_job(job)
            self.assertEqual(result['status'],'ready',result)
            output=folder/'mixed.dat'
            replay=list(iter_rows(output,result))
            self.assertEqual(len(replay),12)
            self.assertTrue(any(r['quality']=='Bad' for r in replay))
            self.assertEqual(replay[2]['value_numeric'],2.)
            self.assertIn('Previous Good',result['gap_policy'])
            self.assertTrue(result['original_archive_lossless'])

    def test_window_context_and_segmented_nan_metadata(self):
        from pda3.exporter import PDA3TemplateWriter
        from pda3.errors import PDA3CapacityError
        from pda3.template_loader import template
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'Parquet').mkdir();folder=root/'DAT'/'2026-10-21';folder.mkdir(parents=True)
            current=root/'Parquet/current.parquet';context=root/'Parquet/context.parquet'
            def write(path,t,value):
                pq.write_table(pa.Table.from_pylist([dict(node_id='ads:A',timestamp_ms=t,value_numeric=value,quality='Good')]),path)
            write(context,BASE-10,2.);write(current,BASE+50,4.)
            job=folder/'segment.pda3job.json'
            job.write_text(json.dumps(dict(id='segment',name='segment.dat',sources=['Parquet/current.parquet'],context_sources=['Parquet/context.parquet'],
                nodes=[dict(nodeid='ads:A',display_name='A')],start_ms=BASE,expected_samples=1001,
                resampling={'method':'previous','grid_ms':10,'max_age_ms':{'ads:A':30}})))
            native_write=PDA3TemplateWriter.write
            template()
            def force_split(self,destination,signals,sample_count=60000):
                if sample_count>1000:raise PDA3CapacityError('Test: esercitare suddivisione con NaN')
                return native_write(self,destination,signals,sample_count)
            with patch.object(PDA3TemplateWriter,'write',force_split):result=process_job(job)
            self.assertEqual(result['status'],'ready',result)
            self.assertEqual(len(result['dat_files']),2)
            part=root/result['dat_files'][0]['name']
            metadata=json.loads(part.with_suffix('.json').read_text())
            self.assertEqual(metadata['actual_samples_per_channel']['ads:A'],7)
            self.assertEqual(metadata['missing_samples_per_channel']['ads:A'],993)
            replay=list(iter_rows(part,metadata))
            self.assertEqual(replay[0]['value_numeric'],2.)
            self.assertEqual(replay[3]['quality'],'Bad')

if __name__=='__main__':unittest.main()
