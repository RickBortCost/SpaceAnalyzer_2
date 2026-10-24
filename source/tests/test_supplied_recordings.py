"""Optional exhaustive checks against the actual attachments, never altered."""
import hashlib,math,os,struct,unittest
from pathlib import Path
from test_binary_recordings import RecordingLibrary
import fda_das,rbf

ROOT=Path(os.environ.get('SPACE_ANALYZER_REFERENCE_DIR',Path(__file__).resolve().parents[4]/'upload'))


class SuppliedRecordings(unittest.TestCase):
    @unittest.skipUnless((ROOT/'TEST DAS 2.DAS').is_file(),'Set SPACE_ANALYZER_REFERENCE_DIR to supplied files')
    def test_all_fda_mixed_values_against_independent_binary_layout(self):
        p=ROOT/'TEST DAS 2.DAS';raw=p.read_bytes();before=hashlib.sha256(raw).hexdigest()
        h=fda_das.inspect(p)
        self.assertEqual((h['frames'],len(h['channels']),h['record_size'],h['data_offset']),(289198,18,60,1734))
        reference=struct.Struct('<I4fh2f2h4fhf2h')
        actual=iter(fda_das.iter_rows(p));count=0
        for frame in reference.iter_unpack(raw[1734:]):
            for value in frame[1:]:
                row=next(actual);count+=1
                if row['timestamp_ms']!=1423680896000+frame[0] or row['value_numeric']!=value:
                    raise AssertionError(f'FDA mismatch at value {count}')
        self.assertIsNone(next(actual,None));self.assertEqual(count,4916366)
        info=RecordingLibrary(p.parent).describe(p.name)
        self.assertAlmostEqual(info['duration_seconds'],3314.893)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)

    @unittest.skipUnless((ROOT/'TEST_RBF.rbf').is_file(),'Set SPACE_ANALYZER_REFERENCE_DIR to supplied files')
    def test_all_rbf_values_and_timestamps_against_independent_binary_layout(self):
        p=ROOT/'TEST_RBF.rbf';raw=p.read_bytes();before=hashlib.sha256(raw).hexdigest()
        count=struct.unpack_from('<I',raw,8)[0];pos=20;types=[]
        for _ in range(count):
            n=raw[pos];types.append(raw[pos+n+1]);pos+=n+5
        codes={0:'B',2:'h',7:'f',9:'81s'};reference=struct.Struct('<II'+''.join(codes[t] for t in types))
        self.assertEqual((pos+12*count,reference.size),(14706,1244))
        actual=iter(rbf.iter_rows(p));values=0
        for frame in reference.iter_unpack(raw[14706:32736882]):
            t=frame[0]*1000+frame[1]/1e6
            for kind,value in zip(types,frame[2:]):
                row=next(actual);values+=1
                expected=value.split(b'\0',1)[0].decode('cp1252') if kind==9 else float(value)
                if row['timestamp_ms']!=t or (row['value_text'] if kind==9 else row['value_numeric'])!=expected:
                    raise AssertionError(f'RBF mismatch at value {values}')
        self.assertIsNone(next(actual,None));self.assertEqual(values,10100736)
        info=RecordingLibrary(p.parent).describe(p.name)
        self.assertEqual(len(info['signals']),384);self.assertAlmostEqual(info['duration_seconds'],3285.137639,places=6)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main(verbosity=2)
