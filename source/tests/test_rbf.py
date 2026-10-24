"""Independent binary fixture plus recording lifecycle/quality regressions."""
import io,json,math,struct,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from test_binary_recordings import Recorder,RecordingLibrary,sample,NODES,BASE,pq
import rbf


def external_fixture():
    # This does not use the production encoder: Float32, Int16, Bool, String[81].
    names=[(b'Current',7,4),(b'State',2,2),(b'Running',0,1),(b'Text',9,81)]
    head=struct.pack('<5I',3,192,4,125000,57600)
    for name,kind,width in names:head+=bytes([len(name)])+name+bytes([kind,width,1,10])
    frames=struct.pack('<IIfhB81s',1761312560,944603000,1.25,-120,1,b'caff\xe8')
    frames+=struct.pack('<IIfhB81s',1761312561,69499000,-0.0,3490,0,b'done')
    return head+bytes(48)+frames+b'\r\nPlotter = 1\r\nEND SAVESTATE\r\n'


class Format(unittest.TestCase):
    def test_native_types_timestamps_filter_and_library(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'native.rbf';p.write_bytes(external_fixture());h=rbf.inspect(p)
            self.assertEqual((h['frames'],h['frame'].size),(2,96))
            rows=list(rbf.iter_rows(p));self.assertEqual(len(rows),8)
            self.assertEqual([r['value_numeric'] for r in rows[:3]],[1.25,-120.,1.])
            self.assertEqual(rows[3]['value_text'],'caffè')
            self.assertEqual(rows[0]['source_timestamp'],'2025-10-24T13:29:20.944603000+00:00')
            chosen=[rows[0]['node_id']]
            selected=list(rbf.iter_rows(p,signal_ids=chosen,start_ms=rows[4]['timestamp_ms']))
            self.assertEqual(len(selected),1)
            self.assertEqual(struct.pack('<f',selected[0]['value_numeric']),struct.pack('<f',-0.0))
            lib=RecordingLibrary(d);info=lib.describe(p.name)
            self.assertEqual(len(info['signals']),4);self.assertEqual(info['sample_ms'],125)
            self.assertAlmostEqual(info['duration_seconds'],.124896,places=6)
            self.assertEqual(lib.load(p.name,signal_ids=chosen)['numeric_rows'],2)

    def test_invalid_variants_and_truncated_data_are_rejected(self):
        raw=external_fixture()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.rbf'
            for value in [b'bad',raw[:-1],bytes([4])+raw[1:],raw[:4]+struct.pack('<I',193)+raw[8:]]:
                p.write_bytes(value)
                with self.assertRaises(ValueError):rbf.inspect(p)
            p.write_bytes(raw);h=rbf.inspect(p)
            for offset in [28,h['data_offset']-1]:
                value=bytearray(raw);value[offset]=255;p.write_bytes(value)
                with self.assertRaises(ValueError):rbf.inspect(p)
            value=bytearray(raw);struct.pack_into('<I',value,h['data_offset']+4,1000000000);p.write_bytes(value)
            with self.assertRaises(ValueError):list(rbf.iter_rows(p))

    def test_http_import_and_replay(self):
        from test_ads import app
        http=app.app.test_client();token={'X-OPCUA-Token':app.SESSION_TOKEN}
        response=http.post('/api/recordings/import',data={'file':(io.BytesIO(external_fixture()),'Reference.RBF')},headers=token)
        self.assertEqual(response.status_code,200,response.json)
        info=response.json['recording'];self.assertEqual(info['format'],'rbf')
        response=http.post('/api/recordings/data',json={'source':'local','name':info['name'],'signal_ids':[info['signals'][0]['nodeid']]},headers=token)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['recording']['numeric_rows'],2)

    def test_mixed_comparison_keeps_rbf_fda_csv_samples_and_aligns_starts(self):
        from fda_das import header as fda_header
        from recording_compare import build_comparison
        from test_recording_compare import fixture
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);native=root/'native.rbf';native.write_bytes(external_fixture())
            fda=root/'mixed.das';channels=[dict(name='TIME',size=4),dict(name='Float',size=4,type=4),dict(name='Int',size=2)]
            fda.write_bytes(fda_header(channels,1700000000)+struct.pack('<Ifh',0,2.5,-20)+struct.pack('<Ifh',12,3.5,100))
            csv,_=fixture(root,'data.csv',count=4)
            paths=[native,fda,csv];out=root/'comparison.parquet'
            desc=build_comparison({'files':[dict(path=str(p),name=p.name,display_name=p.name,format=p.suffix[1:]) for p in paths],
                                   'output':str(out),'mode':'starts','coverage':'union'})
            self.assertEqual(len(desc['signals']),7);self.assertEqual(desc['rows_written'],16)
            rows=list(RecordingLibrary(root)._iter_rows(out))
            firsts={}
            for row in rows:firsts.setdefault(row['source_file'],row['timestamp_ms'])
            self.assertEqual(len(set(firsts.values())),1)
            self.assertEqual([r['value_numeric'] for r in rows if r['original_node_id']=='das:Float:1'],[2.5,3.5])


class Record(unittest.TestCase):
    def test_original_precision_quality_lifecycle_and_standalone_replay(self):
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('rbf',NODES,sample_ms=1)
            value=math.nextafter(1.,2.)
            for i in range(256):rec.enqueue(BASE+i,[sample(value+i),sample(2**64-1-i,NODES[1])],1)
            rec.enqueue(BASE+256,[sample(.3,quality='Uncertain')],1)
            state=rec.stop(timeout=20);self.assertEqual(state['state'],'stopped',state)
            self.assertEqual(state['rbf_phase'],'ready');self.assertFalse(rec.busy)
            originals=pq.read_table(state['original_path']).to_pylist()
            self.assertEqual(originals[0]['value_numeric'],value)
            self.assertEqual(originals[1]['value_text'],str(2**64-1))
            self.assertEqual(originals[0]['source_timestamp'],sample(value)['source_ts'])
            p=Path(state['path']);self.assertEqual(rbf.inspect(p)['frames'],257)
            meta=json.loads(p.with_suffix('.metadata.json').read_text())
            rows=list(rbf.iter_rows(p,meta));self.assertEqual(len(rows),514)
            self.assertEqual(rows[0]['value_numeric'],1.);self.assertEqual(rows[0]['timestamp_ms'],BASE)
            self.assertEqual(rows[-2]['quality'],'Uncertain');self.assertIsNone(rows[-2]['value_numeric'])
            self.assertEqual(rows[-1]['status'],'RBF_Missing');self.assertIsNone(rows[-1]['value_numeric'])
            p.with_suffix('.metadata.json').unlink();native=list(rbf.iter_rows(p))
            self.assertEqual(len(native),514);self.assertEqual(native[-1]['status'],'RBF_Missing')
            self.assertEqual(len(RecordingLibrary(d).describe(p.name)['signals']),2)
            self.assertFalse(list(Path(d).glob('.rbf_sort_*')))
            rec.start('csv',NODES);self.assertEqual(rec.stop(timeout=10)['state'],'stopped')

    def test_boolean_string_and_bad_null_updates(self):
        nodes=[{'nodeid':'b','display_name':'Running'},{'nodeid':'s','display_name':'Text'}]
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('rbf',nodes,sample_ms=10)
            rec.enqueue(BASE,[{'nodeid':'b','value':None,'good':False,'data_type':'Null'}],10)
            rec.enqueue(BASE+10,[{'nodeid':'b','value':True,'plot_value':1.,'good':True,'data_type':'Boolean'},
                                {'nodeid':'s','value':'caffè','good':True,'data_type':'String'}],10)
            state=rec.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
            rows=list(RecordingLibrary(d)._iter_rows(Path(state['path'])))
            self.assertEqual(rows[2]['value_numeric'],1.);self.assertEqual(rows[3]['value_text'],'caffè')
            self.assertEqual([c['type'] for c in rbf.inspect(state['path'])['channels']],[0,2,9,2])

    def test_out_of_order_notifications_and_duplicates_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('rbf',NODES,sample_ms=1)
            rec.enqueue(BASE+1,[sample(4.)],1);rec.enqueue(BASE,[sample(1.)],1)
            rec.enqueue(BASE,[sample(2.,NODES[1])],1);rec.enqueue(BASE,[sample(3.)],1)
            state=rec.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
            self.assertEqual(rbf.inspect(state['path'])['frames'],3)
            rows=list(RecordingLibrary(d)._iter_rows(Path(state['path'])))
            self.assertEqual([r['value_numeric'] for r in rows if r['good']],[1.,2.,3.,4.])

    def test_overflow_failure_keeps_original_archive(self):
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('rbf',NODES[:1]);rec.enqueue(BASE,[sample(1e100)],1)
            state=rec.stop(timeout=10);self.assertEqual(state['state'],'error');self.assertIn('Float32',state['error'])
            self.assertEqual(pq.read_table(state['original_path']).num_rows,1)
            self.assertFalse(list(Path(d).glob('*.rbf.part')))

    def test_rotation_keeps_complete_frames(self):
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('parquet',NODES,sample_ms=1)
            for i in range(200):rec.enqueue(BASE+i,[sample(i),sample(i+1,NODES[1])],1)
            state=rec.stop(timeout=10);files=rbf.export_parquet(Path(state['path']),NODES,1300)
            self.assertGreater(len(files),1)
            self.assertEqual(sum(rbf.inspect(Path(d)/f['name'])['frames'] for f in files),200)
            self.assertTrue(all(f['bytes']<=1300 for f in files))

    def test_reference_frame_limit_rotates_without_losing_updates(self):
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('parquet',NODES[:1],sample_ms=1)
            for i in range(8):rec.enqueue(BASE+i,[sample(i)],1)
            state=rec.stop(timeout=10)
            with patch('rbf.MAX_FRAMES',3):files=rbf.export_parquet(Path(state['path']),NODES[:1])
            self.assertEqual([f['frames'] for f in files],[3,3,2])
            values=[r['value_numeric'] for f in files for r in RecordingLibrary(d)._iter_rows(Path(d)/f['name'])]
            self.assertEqual(values,list(range(8)))

    def test_fft_uses_recorded_grid_without_interpolation(self):
        from analysis_tools import spectrum
        with tempfile.TemporaryDirectory() as d:
            rec=Recorder(d,'test');rec.start('rbf',NODES[:1],sample_ms=1)
            for i in range(256):rec.enqueue(BASE+i,[sample(math.sin(2*math.pi*125*i/1000))],1)
            state=rec.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
            fft=spectrum(RecordingLibrary(d)._iter_rows(Path(state['path'])),NODES[0]['nodeid'],BASE,BASE+255,options={'block_size':256})
            self.assertEqual(fft['peak_hz'],125);self.assertFalse(fft['resampled'])

if __name__=='__main__':unittest.main(verbosity=2)
