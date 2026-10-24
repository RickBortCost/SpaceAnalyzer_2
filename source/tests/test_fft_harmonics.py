"""Known harmonic mixtures; automatic dominant peak is not claimed to be F1."""
import math,unittest
from test_fft_engine import rows,run
class Harmonics(unittest.TestCase):
    def mixture(self,amps,f1=50,fs=1024,n=2048):
        return rows([sum(a*math.sin(2*math.pi*(j+1)*f1*i/fs) for j,a in enumerate(amps)) for i in range(n)],fs)
    def test_five_harmonics_and_calibrated_levels(self):
        # FILETIME rounds the 1024 Hz sampling interval to 100 ns; allow
        # the resulting median-period frequency uncertainty, never nominal Fs.
        amps=[3,1.5,.7,.4,.2]
        for output,factor in [('peak',1),('rms',1/math.sqrt(2))]:
            r=run(self.mixture(amps),block_size=1024,output=output)
            h=r['harmonics'];self.assertAlmostEqual(h['fundamental_hz'],50,delta=.003);self.assertEqual(len(h['rows']),5)
            for j,row in enumerate(h['rows']):
                self.assertAlmostEqual(row['target_hz'],50*(j+1),delta=.003*(j+1));self.assertAlmostEqual(row['measured_hz'],50*(j+1),delta=.003*(j+1));self.assertAlmostEqual(row['amplitude'],amps[j]*factor,places=8)
    def test_manual_fundamental_when_second_is_stronger(self):
        data=self.mixture([1,5,.7,.4,.2]);auto=run(data,block_size=1024)['harmonics'];self.assertAlmostEqual(auto['fundamental_hz'],100,delta=.005)
        manual=run(data,block_size=1024,fundamental_mode='manual',fundamental_hz=50)['harmonics'];self.assertEqual(manual['fundamental_hz'],50);self.assertAlmostEqual(manual['rows'][1]['amplitude'],5,places=8)
    def test_missing_lines_and_nyquist(self):
        h=run(self.mixture([3,0,0,0,0],f1=150),block_size=1024)['harmonics']
        self.assertEqual(h['rows'][1]['status'],'Nessun picco in banda');self.assertIsNone(h['rows'][1]['amplitude']);self.assertEqual(h['rows'][3]['status'],'Oltre Nyquist');self.assertIsNone(h['rows'][3]['measured_hz'])
    def test_zero_signal_resolution_and_validation(self):
        h=run(rows([7.]*1024),block_size=1024)['harmonics'];self.assertEqual(h['rows'],[])
        h=run(self.mixture([3],f1=1),block_size=1024)['harmonics'];self.assertEqual(h['rows'][0]['status'],'Risoluzione insufficiente')
        for options in [{'fundamental_mode':'bad'},{'fundamental_mode':'manual','fundamental_hz':0},{'fundamental_mode':'manual','fundamental_hz':600},{'harmonic_tolerance_percent':-1}]:
            with self.assertRaises(ValueError):run(self.mixture([3]),block_size=1024,**options)
if __name__=='__main__':unittest.main(verbosity=2)
