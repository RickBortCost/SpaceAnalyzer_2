"""Reference-derived format tests; no claim of validation inside the original FDA software."""
import io,json,math,struct,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_binary_recordings import Recorder,RecordingLibrary,sample,NODES,BASE,pq
from fda_das import inspect,read_header,header,iter_rows,export_parquet

class Format(unittest.TestCase):
    def test_mixed_float32_int16_scaling_and_nonfinite_values(self):
        channels=[dict(name='TIME',size=4),dict(name='Float',size=4,type=4,gain=2.,offset=-1.),dict(name='Int',size=2)]
        data=header(channels,1700000000)+struct.pack('<Ifh',0,1.25,-120)+struct.pack('<Ifh',12,float('nan'),3490)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mixed.das';p.write_bytes(data)
            self.assertEqual(inspect(p)['record_size'],10)
            rows=list(iter_rows(p));self.assertEqual(len(rows),4)
            self.assertEqual([r['value_numeric'] for r in rows],[1.5,-120.,None,3490.])
            self.assertEqual(rows[2]['quality'],'Bad')
            self.assertEqual(len(list(iter_rows(p,signal_ids=[rows[0]['node_id']],start_ms=1700000000012))),1)
            self.assertIn('Float32',RecordingLibrary(d).describe(p.name)['format_label'])

    def test_reference_header_rebuilds_byte_for_byte(self):
        raw=(Path(__file__).parent/'fixtures/fda_reference_header.bin').read_bytes()
        h=read_header(io.BytesIO(raw))
        self.assertEqual(h['record_size'],26);self.assertEqual(h['data_offset'],661)
        self.assertEqual(header(h['channels'],h['epoch'],settings=h['settings']),raw)
        self.assertEqual([c['name'] for c in h['channels']][1], 'ActPos1')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'known.das';p.write_bytes(raw+struct.pack('<I11h',752,-55,-55,0,1168,847,76,76,0,678,0,-131))
            rows=list(iter_rows(p));self.assertEqual(len(rows),11)
            self.assertAlmostEqual(rows[0]['value_numeric'],-.55)
            self.assertAlmostEqual(rows[-1]['value_numeric'],-1.31)
            self.assertEqual(inspect(p)['frames'],1)

    def test_external_das_import_and_replay_through_http(self):
        from test_ads import app
        http=app.app.test_client()
        raw=(Path(__file__).parent/'fixtures/fda_reference_header.bin').read_bytes()
        data=raw+struct.pack('<I11h',752,-55,-55,0,1168,847,76,76,0,678,0,-131)
        response=http.post('/api/recordings/import',data={'file':(io.BytesIO(data),'FDA.DAS')},headers={'X-OPCUA-Token':app.SESSION_TOKEN})
        self.assertEqual(response.status_code,200,response.json)
        info=response.json['recording'];self.assertEqual(info['format'],'das');self.assertEqual(len(info['signals']),11)
        result=http.post('/api/recordings/data',json={'source':'local','name':info['name']},headers={'X-OPCUA-Token':app.SESSION_TOKEN})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json['recording']['numeric_rows'],11)

    def test_malformed_variants_are_rejected(self):
        raw=(Path(__file__).parent/'fixtures/fda_reference_header.bin').read_bytes()
        with tempfile.TemporaryDirectory() as d:
            for data in (b'bad',raw[:-1],raw+b'1',raw.replace(b'DAS4',b'DAS9')):
                p=Path(d)/'bad.das';p.write_bytes(data)
                with self.assertRaises(ValueError):inspect(p)

class Record(unittest.TestCase):
    def test_fda_record_lossless_archive_scaled_values_quality_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');prepare=[dict(n,unit='kN') for n in NODES]
            try:
                state=r.start('das',prepare,sample_ms=1,options={'max_file_mb':0})
                self.assertEqual(state['format'],'das');self.assertTrue(r.busy)
                with self.assertRaises(ValueError):r.start('csv',prepare)
                expected=[]
                for i in range(256):
                    rows=[sample(math.sin(i*.1)),sample(2**64-1-i,NODES[1])]
                    expected.extend(rows);self.assertTrue(r.enqueue(BASE+i,rows,1))
                r.enqueue(BASE+256,[sample(0.3,quality='Uncertain')],1)
                end=r.stop(timeout=20);self.assertEqual(end['state'],'stopped',end)
                self.assertEqual(end['fda_phase'],'ready');self.assertEqual(end['cycles_received'],257)
                self.assertEqual(len(end['original_files']),1);self.assertEqual(len(end['files']),1)
                archive=Path(d)/end['original_files'][0]['name'];raw=pq.read_table(archive).to_pylist()
                self.assertEqual(len(raw),513)
                self.assertEqual(raw[1]['value_text'],str(2**64-1))
                self.assertEqual(raw[0]['ads_filetime'],'134037504001234567')
                self.assertEqual(raw[0]['timestamp_ms'],BASE)
                p=Path(end['path']);self.assertEqual(inspect(p)['frames'],257)
                meta=json.loads(p.with_suffix('.metadata.json').read_text());rows=list(iter_rows(p,meta))
                self.assertEqual(len(rows),514)
                self.assertEqual(rows[-2]['quality'],'Uncertain');self.assertIsNone(rows[-2]['value_numeric'])
                self.assertEqual(rows[-1]['status'],'FDA_Missing');self.assertIsNone(rows[-1]['value_numeric'])
                for i in range(256):
                    error=meta['fda_channels'][0]['maximum_rounding_error']
                    self.assertLessEqual(abs(rows[2*i]['value_numeric']-expected[2*i]['value']),error+1e-12)
                library=RecordingLibrary(d);view=library.load(p.name)
                self.assertEqual(len(view['signals']),2)
                self.assertEqual(view['signals'][0]['points_original'],257)
                self.assertFalse(list(Path(d).glob('.fda_sort_*')))
                # Switching recording formats after conversion remains possible.
                r.start('csv',NODES);self.assertEqual(r.stop(timeout=10)['state'],'stopped')
            finally:r.stop(timeout=20)

    def test_fda_fft_has_the_original_frequency_and_no_resampling(self):
        from analysis_tools import spectrum
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('das',NODES[:1],sample_ms=1)
            for i in range(256):r.enqueue(BASE+i,[sample(math.sin(2*math.pi*125*i/1000))],1)
            state=r.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
            p=Path(state['path']);meta=json.loads(p.with_suffix('.metadata.json').read_text())
            fft=spectrum(iter_rows(p,meta),NODES[0]['nodeid'],round(BASE),round(BASE)+255,options={'block_size':256})
            self.assertEqual(fft['sample_hz'],1000);self.assertAlmostEqual(fft['peak_hz'],125)
            self.assertFalse(fft['resampled'])
            # Copying only the DAS remains readable with _Q semantics.
            meta_path=p.with_suffix('.metadata.json');meta_path.unlink()
            rows=list(iter_rows(p));self.assertEqual(len(rows),256)
            self.assertTrue(all(row['quality']=='Good' for row in rows))
            self.assertEqual(len(RecordingLibrary(d).describe(p.name)['signals']),1)

    def test_separate_notifications_at_identical_source_time_share_frame(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('das',NODES,sample_ms=1)
            r.enqueue(BASE,[sample(1.)],1);r.enqueue(BASE,[sample(2.,NODES[1])],1)
            state=r.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
            self.assertEqual(inspect(state['path'])['frames'],1)
            self.assertEqual(len(list(RecordingLibrary(d)._iter_rows(Path(state['path'])))),2)

    def test_native_one_ms_notifications_keep_originals_and_fda_samples(self):
        from test_ads_notifications import app,NotifyDLL,configure_library,until,NotificationAcquisition,FILETIME_EPOCH
        dll=NotifyDLL();configure_library(dll)
        cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_mode':'notify'}
        ids=['ads:MAIN.Wave','ads:MAIN.Running']
        with tempfile.TemporaryDirectory() as d,patch('ads_native.load_library',return_value=(dll,'fake.dll')):
            r=Recorder(d,'test');r.start('das',[{'nodeid':n,'display_name':n} for n in ids],sample_ms=1)
            w=NotificationAcquisition(cfg,lambda:(ids,1,(1,1)),lambda ts,rows,ms,g:r.enqueue(ts,rows,ms),lambda diag:None)
            try:
                w.start();until(lambda:len(dll.subs)==2)
                base=FILETIME_EPOCH+1700000000*10000000+1234567
                for i in range(1000):dll.emit(base+i*10000)
                until(lambda:w.processed==2000)
                w.stop();w.thread.join(5);self.assertIsNone(w.error);self.assertEqual(w.dropped,0)
                state=r.stop(timeout=15);self.assertEqual(state['state'],'stopped',state)
                original=pq.read_table(Path(d)/state['original_files'][0]['name']).to_pylist()
                for nid in ids:
                    points=[row for row in original if row['node_id']==nid]
                    self.assertEqual([int(row['ads_filetime']) for row in points],[base+i*10000 for i in range(1000)])
                data=list(RecordingLibrary(d)._iter_rows(Path(state['path'])))
                self.assertEqual(inspect(state['path'])['frames'],1000)
                self.assertEqual(sum(row['quality']=='Good' for row in data),2000)
            finally:
                w.stop();w.thread.join(5);r.stop(timeout=15)

    def test_out_of_order_cycles_are_sorted_without_merging_equal_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('parquet',NODES,sample_ms=1)
            for t in (BASE+2,BASE,BASE+1,BASE+1):r.enqueue(t,[sample(1.)],1)
            end=r.stop(timeout=10);files=export_parquet(Path(end['path']),NODES)
            p=Path(d)/files[0]['name'];meta=json.loads(p.with_suffix('.metadata.json').read_text())
            values=list(iter_rows(p,meta));self.assertEqual([v['timestamp_ms'] for v in values[::2]],[round(BASE),round(BASE+1),round(BASE+1),round(BASE+2)])
            self.assertEqual(inspect(p)['frames'],4)

    def test_rotation_never_splits_fda_frames_and_respects_size(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('parquet',NODES,sample_ms=1)
            for i in range(200):r.enqueue(BASE+i,[sample(1.),sample(2.,NODES[1])],1)
            end=r.stop(timeout=10);files=export_parquet(Path(end['path']),NODES,max_bytes=900)
            self.assertGreater(len(files),1)
            self.assertEqual(sum(inspect(Path(d)/f['name'])['frames'] for f in files),200)
            self.assertTrue(all(f['bytes']<=900 for f in files))

    def test_conversion_failure_is_visible_and_keeps_originals(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test')
            with patch('fda_recording.export_parquet',side_effect=OSError('disco pieno')):
                r.start('das',NODES);r.enqueue(BASE,[sample(1.)],1)
                end=r.stop(timeout=10)
            self.assertEqual(end['state'],'error');self.assertIn('Parquet originale conservato',end['error'])
            self.assertEqual(pq.read_table(end['original_path']).num_rows,1)
            self.assertFalse(r.busy)

    def test_original_queue_overflow_never_generates_a_complete_fda(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test',queue_capacity=1);gate=threading.Event();run=Recorder._run
            with patch.object(Recorder,'_run',lambda self:(gate.wait(3),run(self))):
                r.start('das',NODES)
                self.assertTrue(r.enqueue(BASE,[sample(1.)],1))
                self.assertFalse(r.enqueue(BASE+1,[sample(2.)],1))
                gate.set();end=r.stop(timeout=10)
            self.assertEqual(end['state'],'error');self.assertEqual(end['dropped_cycles'],1)
            self.assertEqual(end['files'],[]);self.assertTrue(Path(end['original_path']).exists())

if __name__=='__main__':unittest.main(verbosity=2)
