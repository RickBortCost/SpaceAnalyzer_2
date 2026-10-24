import math,random,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fft_engine import analyze
from fft_service import calculate
NODE='ads:MAIN.Signal'
def rows(values,fs=100):
    epoch=116444736000000000+1700000000*10000000
    return [dict(node_id=NODE,timestamp_ms=1700000000000+i*1000/fs,value_numeric=v,quality='Good',ads_filetime=str(epoch+round(i*10000000/fs))) for i,v in enumerate(values)]
def run(data,**o):return analyze(data,NODE,0,1e15,o.pop('window','hann'),o.pop('detrend',True),o)
def sine(n,fs=100,f=100*100/1024,amp=3,dc=0):return [dc+amp*math.sin(2*math.pi*f*i/fs) for i in range(n)]
class FFT(unittest.TestCase):
    def test_non_power_two_preserves_fs(self):
        r=run(rows(sine(1500)),block_size=1024,overlap_percent=0)
        self.assertEqual(r['sample_hz'],100);self.assertFalse(r['resampled']);self.assertEqual(r['samples_unused'],476)
        self.assertAlmostEqual(r['peak_hz'],100*100/1024);self.assertAlmostEqual(r['peak_amplitude'],3,places=7)
    def test_tone_units_and_windows(self):
        for window in ('rectangular','hann','blackmanharris','flattop'):
            for output,expected in [('peak',3),('rms',3/math.sqrt(2)),('power',4.5)]:
                r=run(rows(sine(2048,dc=10)),window=window,block_size=1024,output=output)
                self.assertAlmostEqual(r['peak_amplitude'],expected,places=7);self.assertAlmostEqual(r['overall_rms'],3/math.sqrt(2),places=7)
    def test_psd_asd_band_rms(self):
        data=rows(sine(4096));r=run(data,block_size=1024,output='psd',band_min_hz=9,band_max_hz=11)
        self.assertAlmostEqual(r['band_rms'],3/math.sqrt(2),places=7)
        self.assertAlmostEqual(r['peak_amplitude'],4.5/r['noise_bandwidth_hz'],places=7)
        a=run(data,block_size=1024,output='asd');self.assertAlmostEqual(a['peak_amplitude']**2,r['peak_amplitude'],places=7)
    def test_dc_and_nyquist(self):
        for output in ('peak','rms'):
            r=run(rows([7.]*1024),detrend=False,output=output)
            self.assertAlmostEqual(r['bins'][0][1],7);self.assertAlmostEqual(r['overall_rms'],7)
            r=run(rows([2*(-1)**i for i in range(1024)]),window='rectangular',output=output)
            self.assertAlmostEqual(r['bins'][-1][1],2);self.assertAlmostEqual(r['overall_rms'],2)
        self.assertAlmostEqual(run(rows([7.]*1024))['overall_rms'],0)
    def test_averages(self):
        data=rows(sine(1024,amp=1)+sine(1024,amp=3))
        r=run(data,block_size=1024,overlap_percent=0);self.assertAlmostEqual(r['peak_amplitude'],math.sqrt(5),places=7)
        r=run(data,block_size=1024,overlap_percent=0,averaging='maxhold');self.assertAlmostEqual(r['peak_amplitude'],3,places=7)
        r=run(data,block_size=1024,overlap_percent=0,averaging='exponential',tau_seconds=10.24)
        self.assertAlmostEqual(r['peak_amplitude'],math.sqrt(1+8*(1-math.exp(-1))),places=7)
        r=run(data,block_size=1024,overlap_percent=0,average_count=1);self.assertEqual(r['blocks_used'],1);self.assertAlmostEqual(r['peak_amplitude'],3,places=7)
    def test_quality_and_timing(self):
        data=rows(sine(4096));data[1100]['quality']='Bad'
        with self.assertRaisesRegex(ValueError,'Bad/Uncertain'):run(data,block_size=1024)
        r=run(data,block_size=1024,gap_policy='skip');self.assertGreater(r['blocks_excluded'],0);self.assertEqual(r['bad_samples'],1)
        data=rows(sine(4096));del data[1500:1510]
        with self.assertRaisesRegex(ValueError,'buco'):run(data,block_size=1024)
        r=run(data,block_size=1024,gap_policy='skip');self.assertEqual(r['timing_breaks'],1);self.assertEqual(r['sample_hz'],100)
        data[10]['ads_filetime']=data[9]['ads_filetime']
        with self.assertRaisesRegex(ValueError,'crescenti'):run(data,gap_policy='skip')
    def test_resolution_overlap_limits(self):
        data=rows(sine(4096));r=run(data,resolution_mode='hz',resolution_value=.1)
        self.assertEqual(r['samples_fft'],1024);self.assertEqual(r['blocks_total'],7)
        self.assertEqual(run(data,resolution_mode='seconds',resolution_value=15)['samples_fft'],2048)
        with self.assertRaises(ValueError):run(data,block_size=65536)
        with self.assertRaises(ValueError):run(data,average_count=.5)
    def test_noise_normalization(self):
        rng=random.Random(42);data=rows([rng.gauss(0,2) for _ in range(32768)],1024)
        for n in (512,2048):
            r=run(data,block_size=n,output='psd');self.assertAlmostEqual(r['overall_rms'],2,delta=.06)
            self.assertAlmostEqual(sum(p[1] for p in r['bins'])*r['resolution_hz'],4,delta=.25)
    def test_flat_top_off_bin(self):self.assertAlmostEqual(run(rows(sine(4096,f=10.123)),block_size=1024,window='flattop')['peak_amplitude'],3,delta=.004)
    def test_subprocess(self):
        r=calculate(rows(sine(1024)),NODE,0,1e15,'hann',True,{'block_size':1024})
        self.assertAlmostEqual(r['peak_amplitude'],3,places=7);self.assertEqual(r['time_basis'],'ADS FILETIME originale')
if __name__=='__main__':unittest.main(verbosity=2)
