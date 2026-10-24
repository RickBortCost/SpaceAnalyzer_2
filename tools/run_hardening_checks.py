"""Run each regression suite in an independent interpreter with isolated fixtures."""
import argparse,os,subprocess,json,re,sys,time
from pathlib import Path
names=['recordings','binary_recordings','fda','rbf','recording_reader','recording_compare','calculation_api','live_interval_calculations','recording_manager','recording_preview','source_sessions','s7','s7_extended','ads','ads_native','ads_types','ads_notifications','mqtt','mqtt_api','advanced','i18n','fft_api','iba_dat','pda3_api','pda3_lifecycle','acquisition_hardening','calculation_ads','integration']
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('hardening-evidence'));args=parser.parse_args()
results={};root=Path(__file__).resolve().parents[1];out=args.output.resolve();out.mkdir(parents=True,exist_ok=True);start=time.monotonic()
for name in names:
 path=out/(name+'.log')
 try:
  with path.open('w') as f:r=subprocess.run([sys.executable,'-m','unittest','discover','-s','source/tests','-p','test_'+name+'.py'],cwd=root,env={**os.environ,'PYTHONPATH':str(root/'source')},stdout=f,stderr=subprocess.STDOUT,timeout=100)
  text=path.read_text();m=re.search(r'Ran (\d+) tests?',text)
  results[name]=dict(result='PASS' if r.returncode==0 else 'FAIL',tests=int(m[1]) if m else 0)
 except subprocess.TimeoutExpired:results[name]=dict(result='TIMEOUT',tests=0)
 print(name,results[name],flush=True)
 (out/'backend_results.json').write_text(json.dumps(dict(result='PASS' if all(r['result']=='PASS' for r in results.values()) else 'FAIL',tests=sum(r['tests'] for r in results.values()),elapsed_seconds=time.monotonic()-start,suites=results),indent=2))

raise SystemExit(0 if all(r['result']=='PASS' for r in results.values()) else 1)
