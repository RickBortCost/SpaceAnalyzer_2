"""Native PDA3 codec/container/window/replay tests; no claim of ibaAnalyzer execution."""
import io,json,math,random,shutil,struct,tempfile,threading,time,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import pyarrow as pa
import pyarrow.parquet as pq
from pda3.exporter import PDA3TemplateWriter
from pda3.template_loader import template,profile,validate_profile,valid_sample_count
from pda3.service import reader,details,iter_rows,sequence_info
from pda3.storage import atomic_json,digest
from pda3.window_export import process_job
from pda3.recording import run_job,rebuild_pending
from recording_reader import RecordingLibrary
from recordings import Recorder,prepare_format

BASE=1792584000000.
NODES=[{'nodeid':'ads:MAIN.Load','display_name':'Carico','unit':'kN'}]

def fixture(root,identity='window',changes=None,count=60000):
    root=Path(root);parquet=root/'Parquet';parquet.mkdir(parents=True,exist_ok=True)
    times=[BASE+i*10 for i in range(count)];values=[float((i//50)%2) for i in range(count)];quality=['Good']*count
    if changes:changes(times,values,quality)
    path=parquet/(identity+'.parquet')
    table=pa.table({'timestamp_ms':times,'node_id':[NODES[0]['nodeid']]*len(times),'signal_name':['Carico']*len(times),
        'value_numeric':values,'quality':quality,'sample_ms':[10]*len(times),'cycle':list(range(1,len(times)+1)),
        'source_timestamp':['2026-10-21T12:00:00+00:00']*len(times),'ads_filetime':['134370576000000000']*len(times)})
    pq.write_table(table,path,compression='zstd')
    folder=root/'DAT'/'2026-10-21';folder.mkdir(parents=True,exist_ok=True)
    job=folder/(identity+'.pda3job.json')
    atomic_json(job,{'id':identity,'name':identity+'.dat','sources':[path.relative_to(root).as_posix()],
        'nodes':NODES,'start_ms':BASE,'state':'queued','tolerance_ms':1,'allow_partial':count<60000,'session':{'session_name':'Test'}})
    return path,job

class Container(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.data={i:b''.join(struct.pack('<f',float(i+1+(j//100)%2)) for j in range(60000)) for i in range(64)}
        cls.output=cls.root/'64channels.dat';t=time.perf_counter();cls.result=PDA3TemplateWriter().write(cls.output,cls.data)
        cls.seconds=time.perf_counter()-t

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_all_64_channels_every_float32_bit_and_all_indexes(self):
        r=reader(self.output);validate_profile(r)
        self.assertEqual(len(r.pages),11);self.assertEqual(len(r.blocks),2752)
        self.assertEqual(r.raw[:24850],template().raw[:24850]);self.assertGreater(self.result['reencoded_blocks'],0)
        for i,c in enumerate(profile()['channels']):
            self.assertEqual(b''.join(r.block_bits(r.blocks[b]) for b in c['blocks']),self.data[i])
        print('PDA3 64-channel exact writer seconds:',round(self.seconds,3),flush=True)

    def test_active_1_8_32_and_unused_channels_zero(self):
        for count in (1,8,32):
            p=self.root/f'{count}channels.dat';PDA3TemplateWriter().write(p,{i:struct.pack('<f',float(i+1))*60000 for i in range(count)})
            r=reader(p)
            for i,c in enumerate(profile()['channels']):
                expected=struct.pack('<f',float(i+1) if i<count else 0.)*60000
                self.assertEqual(b''.join(r.block_bits(r.blocks[b]) for b in c['blocks']),expected)

    def test_no_overwrite_and_uncompressible_rejected(self):
        before=digest(self.output)
        with self.assertRaisesRegex(ValueError,'esistente'):PDA3TemplateWriter().write(self.output,self.data)
        self.assertEqual(digest(self.output),before)
        rng=random.Random(761);bad=b''.join(struct.pack('<f',rng.random()) for _ in range(60000));out=self.root/'cannot_fit.dat'
        with self.assertRaisesRegex(ValueError,'comprimibili|capacita'):PDA3TemplateWriter().write(out,{0:bad})
        self.assertFalse(out.exists());self.assertFalse(list(self.root.glob('*.part')))

    def test_explicit_raw_timezone_manifest_hash_and_known_profile_only(self):
        with self.assertRaisesRegex(ValueError,'fuso'):details(self.output,{})
        d=details(self.output,{'pda3_timezone_offset_minutes':120});self.assertEqual(len(d['signals']),64)
        with self.assertRaisesRegex(ValueError,'hash'):details(self.output,{'pda3_profile':profile()['id'],'dat_sha256':'bad'})
        modified=bytearray(self.output.read_bytes());modified[100]^=1;p=self.root/'unknown.dat';p.write_bytes(modified)
        with self.assertRaises(ValueError):details(p,{'pda3_timezone_offset_minutes':120})

class Window(unittest.TestCase):
    def test_short_durations_exact_count_clock_and_reserved_nan_tail(self):
        for count in (64,1000,8140,59999):
            with self.subTest(samples=count),tempfile.TemporaryDirectory() as d:
                source,job=fixture(d,count=count);before=digest(source);rec=process_job(job)
                self.assertEqual(rec['status'],'ready',rec);self.assertEqual(digest(source),before)
                self.assertEqual(rec['expected_samples_per_channel'],count);self.assertEqual(rec['duration_seconds'],count/100)
                self.assertEqual(rec['missing_samples_per_channel'][NODES[0]['nodeid']],0)
                p=job.parent/'window.dat';r=reader(p);self.assertEqual(valid_sample_count(r),count)
                self.assertEqual(r.start_ticks,template().start_ticks);self.assertEqual(r.end_ticks-r.start_ticks,count*100000)
                actual=list(iter_rows(p,rec));self.assertEqual(len(actual),count)
                self.assertEqual(actual[-1]['timestamp_ms'],BASE+(count-1)*10)
                self.assertTrue(all(row['value_numeric']==float((i//50)%2) for i,row in enumerate(actual)))
                raw=b''.join(r.block_bits(r.blocks[b]) for b in profile()['channels'][0]['blocks'])
                self.assertEqual(raw[count*4:],bytes.fromhex('0000c07f')*(60000-count))
                self.assertTrue(rec['internal_end_modified']);self.assertFalse(rec['internal_start_modified'])

    def test_short_gap_and_invalid_data_remain_explicit_errors(self):
        def changes(t,v,q):t[10]=t[11];q[20]='Bad';v[30]=None
        with tempfile.TemporaryDirectory() as d:
            source,job=fixture(d,count=1000,changes=changes);rec=process_job(job)
            self.assertEqual(rec['status'],'skipped');self.assertIn('Gap',rec['error'])
            self.assertGreater(rec['issues']['duplicates'],0);self.assertGreater(rec['issues']['bad_or_non_numeric'],0)
            self.assertTrue(source.is_file());self.assertFalse((job.parent/'window.dat').exists())

    def test_short_header_manifest_validation_and_historical_export(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,job=fixture(root,count=1000);rec=process_job(job);p=job.parent/'window.dat'
            with self.assertRaisesRegex(ValueError,'durata'):details(p,{**rec,'valid_samples_per_channel':1001})
            for ticks in (template().start_ticks+99999,template().end_ticks+100000):
                raw=bytearray(p.read_bytes());struct.pack_into('<Q',raw,48,ticks);bad=root/'bad.dat';bad.write_bytes(raw)
                with self.assertRaises(ValueError):validate_profile(__import__('pda3.reader',fromlist=['PDA3Reader']).PDA3Reader(bad))
            from pda3.historical_export import export_interval
            out=export_interval(RecordingLibrary(root),p.relative_to(root).as_posix(),[NODES[0]['nodeid']],BASE,BASE+4990,root,root/'Exports')
            self.assertEqual(out['rows'],500);self.assertEqual(out['duration_seconds'],5.)

    def test_full_then_short_sequence_reports_true_duration_and_count(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,job=fixture(root,count=1000);rec=process_job(job);p=job.parent/'window.dat'
            other=job.parent/'next.dat';shutil.copy2(p,other);atomic_json(other.with_suffix('.json'),{**rec,'real_start_ms':BASE+10000})
            seq=root/'short.pda3seq';atomic_json(seq,{'parts':[p.relative_to(root).as_posix(),other.relative_to(root).as_posix()]})
            parts,info=sequence_info(RecordingLibrary(root),seq);self.assertEqual(info['duration_seconds'],20);self.assertEqual(info['rows_written'],2000)
            actual=list(RecordingLibrary(root)._iter_rows(seq));self.assertEqual(len(actual),2000);self.assertEqual(actual[-1]['timestamp_ms'],BASE+19990)

    def test_export_does_not_hold_original_recording_queue_and_source_windows_rotate(self):
        entered=threading.Event();release=threading.Event()
        def delayed_export(path):
            entered.set();release.wait(8)
            return {'status':'skipped','error':'Explicit test stub: incomplete fixture'}
        with tempfile.TemporaryDirectory() as d,patch('pda3.recording.run_job',side_effect=delayed_export):
            prepare_format('pda3');r=Recorder(d,'test');r.start('pda3',NODES,sample_ms=10,options={'pda3_accept_template_time':True})
            sample={'nodeid':NODES[0]['nodeid'],'value':1.,'plot_value':1.,'good':True,'status':'Good'}
            for t in (BASE,BASE+599990,BASE+600000):self.assertTrue(r.enqueue(t,[sample],10))
            self.assertTrue(entered.wait(5))
            for i in range(100):self.assertTrue(r.enqueue(BASE+600010+i*10,[sample],10))
            deadline=time.monotonic()+5
            while r.status()['rows_written']<103 and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(r.status()['rows_written'],103);self.assertTrue(r.busy)
            release.set();end=r.stop(timeout=10);self.assertEqual(len(end['original_files']),2)
            self.assertEqual([f['window_index'] for f in end['original_files']],[0,1])
            self.assertEqual(sum(pq.read_table(p).num_rows for p in Path(d).rglob('*.parquet')),103)

    def test_isolated_process_exact_window_real_utc_fft_and_interval_export(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job=fixture(root);before=digest(source);record=run_job(job)
            self.assertEqual(record['status'],'ready',record);self.assertEqual(digest(source),before)
            self.assertTrue(record['float32_roundtrip_exact']);self.assertFalse(record['internal_timestamps_changed'])
            self.assertEqual(json.loads(job.read_text())['state'],'ready');dat=job.parent/'window.dat'
            lib=RecordingLibrary(root);loaded=lib.load(dat.relative_to(root).as_posix(),[NODES[0]['nodeid']],max_points=100000)
            points=loaded['series'][NODES[0]['nodeid']]
            self.assertEqual(len(points),60000);self.assertEqual(points[0][0],BASE);self.assertEqual(points[-1][0],BASE+599990)
            from analysis_tools import spectrum,export_rows
            rows=list(iter_rows(dat,record));fft=spectrum(rows,NODES[0]['nodeid'],BASE,BASE+599990,options={'block_size':10000})
            self.assertAlmostEqual(fft['sample_hz'],100);self.assertAlmostEqual(fft['peak_hz'],1.,delta=.03)
            exported=export_rows((r for r in rows if BASE+100<=r['timestamp_ms']<=BASE+200),root/'range.csv','csv');self.assertEqual(exported,11)
            from pda3.historical_export import export_interval
            result=export_interval(lib,dat.relative_to(root).as_posix(),[NODES[0]['nodeid']],BASE,BASE+599990,root,root/'Exports')
            self.assertTrue(Path(result['path']).is_file());self.assertTrue(result['name'].endswith('.zip'))

    def test_gaps_duplicate_bad_null_overflow_off_grid_preserve_original(self):
        def change(t,v,q):
            t[1]=t[0];t[2]+=4;q[3]='Bad';v[4]=None;v[5]=1e100
        with tempfile.TemporaryDirectory() as d:
            source,job=fixture(d,changes=change);before=digest(source);record=process_job(job)
            self.assertEqual(record['status'],'skipped');self.assertFalse((job.parent/'window.dat').exists())
            for kind in ('duplicates','off_grid','bad_or_non_numeric','float32_overflows'):self.assertGreater(record['issues'][kind],0)
            self.assertGreater(record['missing_samples_per_channel'][NODES[0]['nodeid']],0);self.assertEqual(digest(source),before)
            self.assertTrue((job.parent/'window.json').is_file())

    def test_sequence_requires_same_signals_and_exact_continuity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,job=fixture(root);rec=process_job(job);p=job.parent/'window.dat'
            other=job.parent/'next.dat';shutil.copy2(p,other);atomic_json(other.with_suffix('.json'),{**rec,'real_start_ms':BASE+600000})
            names=[p.relative_to(root).as_posix(),other.relative_to(root).as_posix()];seq=root/'test.pda3seq';atomic_json(seq,{'parts':names})
            lib=RecordingLibrary(root);parts,info=sequence_info(lib,seq);self.assertEqual(info['duration_seconds'],1200)
            atomic_json(other.with_suffix('.json'),{**rec,'real_start_ms':BASE+600010})
            with self.assertRaisesRegex(ValueError,'gap'):sequence_info(lib,seq)
            with self.assertRaises(ValueError):lib._path('../escape.dat')

    def test_partial_stop_and_wrong_rate_no_invented_samples(self):
        with tempfile.TemporaryDirectory() as d:
            prepare_format('pda3');r=Recorder(d,'test')
            with self.assertRaisesRegex(ValueError,'10 ms'):r.start('pda3',NODES,sample_ms=1,options={'pda3_accept_template_time':True})
            r.start('pda3',NODES,sample_ms=10,options={'pda3_accept_template_time':True})
            r.enqueue(BASE,[{'nodeid':NODES[0]['nodeid'],'value':3.,'plot_value':3.,'good':True,'status':'Good','data_type':'Double'}],10)
            end=r.stop(timeout=20);self.assertEqual(end['state'],'stopped',end);self.assertFalse(end['busy']);self.assertEqual(end['error'],'')
            dats=list(Path(d).rglob('*.dat'));self.assertEqual(len(dats),1);self.assertEqual(sum(pq.read_table(p).num_rows for p in Path(d).rglob('*.parquet')),1)
            self.assertEqual(end['files'][0]['rows'],1);self.assertEqual(end['files'][0]['duration_seconds'],.01)
            rows=list(RecordingLibrary(d)._iter_rows(dats[0]));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['value_numeric'],3.)
            self.assertEqual(len(end['manifests']),1)

    def test_recovery_rebuilds_closed_windows_reports_partials_once(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job=fixture(root);job.unlink()
            atomic_json(source.with_suffix('.metadata.json'),{'pda3_requested':True,'pda3_window_index':0,'pda3_window_origin_ms':BASE,
                'session_id':'recover','signals':NODES,'pda3_tolerance_ms':.5})
            partial=source.with_name('recover_p0002.parquet.part');partial.write_bytes(b'preserve crash fragment')
            atomic_json(partial.with_name('recover_p0002.metadata.json'),{'pda3_requested':True,'session_id':'recover'})
            state=rebuild_pending(root);self.assertEqual(state['rebuilt_jobs'],1);self.assertEqual(state['partial_originals'],[str(partial)])
            self.assertEqual(rebuild_pending(root)['rebuilt_jobs'],0);pending=list(root.rglob('*.pda3job.json'))[0]
            rec=run_job(pending);self.assertEqual(rec['status'],'skipped');self.assertIn('incompleta',rec['error']);self.assertTrue(partial.is_file())

if __name__=='__main__':unittest.main(verbosity=2)
