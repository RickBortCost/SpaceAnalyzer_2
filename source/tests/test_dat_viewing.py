"""iba DAT parts written by 2.x stay viewable in 3.0 through their companion Parquet."""
import json,math,tempfile,unittest
from pathlib import Path
from test_binary_recordings import Recorder,RecordingLibrary,sample,NODES,BASE
from iba_dat import digest


def legacy_dat(directory,count=256):
    """Recreate the 2.x layout: DAT part, metadata sidecar, lossless original Parquet."""
    r=Recorder(directory,'test');r.start('parquet',NODES,sample_ms=1)
    for i in range(count):
        rows=[sample(math.sin(2*math.pi*125*i/1000)),sample(i,NODES[1])]
        for row in rows:row['source_filetime']=str(int(sample(0)['source_filetime'])+i*10000)
        r.enqueue(BASE+i,rows,1)
    state=r.stop(timeout=15);assert state['state']=='stopped',state
    original=Path(state['path']);dat=original.with_name('Legacy_p0001.dat')
    dat.write_bytes(b'LEGACY IBA DAT TEST PART\n')
    meta=dict(format='dat',original_archive=original.name,original_sha256=digest(original),
              original_range_keys=[[BASE,0,0],[BASE+count-1,10**9,10**9]],
              signals=[dict(nodeid=n['nodeid'],display_name=n.get('display_name',n['nodeid'])) for n in NODES])
    dat.with_suffix('.metadata.json').write_text(json.dumps(meta),encoding='utf-8')
    return dat,original


class LegacyDAT(unittest.TestCase):
    def test_dat_part_replays_originals_markers_and_fft(self):
        with tempfile.TemporaryDirectory() as d:
            dat,_=legacy_dat(d);library=RecordingLibrary(d)
            self.assertIn(dat.name,[f['name'] for f in library.list()])
            rows=list(library._iter_rows(dat));self.assertEqual(len(rows),512)
            from analysis_tools import spectrum
            fft=spectrum(rows,NODES[0]['nodeid'],BASE,BASE+255,options={'block_size':128})
            self.assertEqual(fft['samples_original'],256);self.assertAlmostEqual(fft['peak_hz'],125)

    def test_modified_companion_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            dat,original=legacy_dat(d,16)
            with original.open('ab') as stream:stream.write(b'x')
            with self.assertRaisesRegex(ValueError,'checksum'):list(RecordingLibrary(d)._iter_rows(dat))


if __name__=='__main__':unittest.main()
