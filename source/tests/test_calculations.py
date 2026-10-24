"""Calculation contracts: safe expressions, time/quality semantics and real replay files."""
import json
import math
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formula_engine import Expression, Plan, Engine
from calculation_store import FormulaStore, normalize
from calculation_service import CalculationService
from analysis_tools import export_rows
from recording_reader import RecordingLibrary

A='ns=2;s=Load1';B='ns=2;s=Load2'
def definition(name='Delta',expression='[Carico 1] - [Carico 2]',**extra):
    return dict(name=name,expression=expression,bindings={'Carico 1':dict(nodeid=A,name='Carico 1',unit='kN'),
        'Carico 2':dict(nodeid=B,name='Carico 2',unit='kN')},**extra)
def checked(data):
    f=normalize(data);FormulaStore.check([f]);return f
def row(n,v,q='Good'):return dict(node_id=n,value_numeric=v,quality=q)
def series_rows(count=256):
    for i in range(count):
        for nid,value in ((A,10+3*math.sin(2*math.pi*i/32)),(B,2)):
            yield dict(node_id=nid,signal_name=nid,timestamp_ms=1700000000000+i*10,
                value_numeric=value,good=True,quality='Good',data_type='Double',sample_ms=10)

class Expressions(unittest.TestCase):
    def test_arithmetic_functions_and_lazy_conditional(self):
        values={'x':-4.,'y':2.};bindings={n:dict(nodeid=n) for n in values}
        cases={'([x] - [y]) / 2':-3,'abs([x]) + sqrt([y]*[y])':6,
            'mean([x], [y])':-1,'min([x], [y])+max([x], [y])':-2,
            'pow([y], 3) + [y]**2':12,'ifelse([y] > 0, [x], 1/0)':-4}
        for text,value in cases.items():self.assertEqual(Expression(text,bindings).evaluate(values),value)

    def test_untrusted_syntax_and_complexity_are_rejected(self):
        for text in ['__import__("os")','[x].real','open(1)','(lambda: 3)()',
            '{1:2}','[x][0]','[n for n in x]','True','"text"','sum(1,2)','1e999','sqrt(x=2)',
            '1'+(' + 1'*220),'('*210+'1'+')'*210]:
            with self.subTest(text=text[:40]),self.assertRaises(ValueError):Expression(text,{'x':dict(nodeid=A)})
        with self.assertRaises(ValueError):Expression('2 ** 100',{}).evaluate({})
        with self.assertRaises(ValueError):Expression('pow(2, 100)',{}).evaluate({})

    def test_unit_inference_is_conservative(self):
        self.assertEqual(Expression('[x]-[y]',{'x':{},'y':{}}).infer_unit({'x':'kN','y':'kN'}),'kN')
        self.assertEqual(Expression('[x]-[y]',{'x':{},'y':{}}).infer_unit({'x':'kN','y':'mm'}),'')

class Registry(unittest.TestCase):
    def test_atomic_persistence_versions_and_stable_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            store=FormulaStore(Path(directory)/'formulas.json');f=store.save(definition())
            f2=store.save({**f,'name':'Delta rinominato','expected_revision':f['revision']})
            self.assertEqual(f2['nodeid'],f['nodeid']);self.assertEqual(f2['revision'],2)
            with self.assertRaisesRegex(ValueError,'altra finestra'):store.save({**f,'expected_revision':1})
            restored=FormulaStore(store.path);restored.load();self.assertEqual(restored.snapshot(),store.snapshot())
            duplicate=store.duplicate(f['id']);self.assertNotEqual(duplicate['nodeid'],f['nodeid']);self.assertFalse(duplicate['active'])
            with self.assertRaises(ValueError):store.save(definition(name='Delta rinominato'))

    def test_dependency_cycles_missing_refs_and_delete_in_use(self):
        with tempfile.TemporaryDirectory() as directory:
            store=FormulaStore(Path(directory)/'f.json');f=store.save(definition())
            g=store.save(dict(name='Percentuale',expression='[Delta] / [Carico 1]*100',bindings={'Delta':dict(nodeid=f['nodeid']),'Carico 1':dict(nodeid=A)}))
            with self.assertRaisesRegex(ValueError,'circolare'):store.save({**f,'expression':'[P]','bindings':{'P':dict(nodeid=g['nodeid'])}})
            with self.assertRaisesRegex(ValueError,'dipendenti'):store.delete(f['id'])
            with self.assertRaisesRegex(ValueError,'non esiste'):store.save(dict(name='Orfana',expression='[O]',bindings={'O':dict(nodeid='calc:'+'a'*32)}))
            store.delete(g['id']);store.delete(f['id']);self.assertFalse(store.snapshot()['formulas'])

    def test_live_source_modes_and_corrupt_store(self):
        mixed=definition();mixed['bindings']['Carico 2']['nodeid']='ads:MAIN.Load'
        self.assertEqual(checked(mixed)['protocol'],'any')
        f=checked({**mixed,'modes':dict(live=False,recorded=True)});self.assertEqual(f['protocol'],'recorded')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'f.json';path.write_text('{broken');store=FormulaStore(path);store.load()
            self.assertIn('non caricate',store.error);self.assertEqual(store.formulas,[])

class Alignment(unittest.TestCase):
    def test_causal_hold_missing_zero_quality_and_age(self):
        f=checked(definition(expression='[Carico 1] / [Carico 2]',max_age_ms=20));engine=Engine(Plan([f]))
        self.assertIsNone(engine.process(100,[row(A,10)])[0]['value_numeric'])
        self.assertEqual(engine.process(110,[row(B,2)])[0]['value_numeric'],5)
        expired=engine.process(130,[row(B,4)])[0];self.assertIsNone(expired['value_numeric']);self.assertIn('tolleranza',expired['status'])
        zero=engine.process(140,[row(A,10),row(B,0)])[0];self.assertEqual(zero['status'],'Divisione per zero')
        bad=engine.process(150,[row(A,10,'Uncertain'),row(B,2)])[0];self.assertEqual(bad['quality'],'Uncertain');self.assertIsNone(bad['value_numeric'])
        null=engine.process(160,[row(A,None),row(B,2)])[0];self.assertEqual(null['quality'],'Bad')
        self.assertEqual(engine.process(159,[row(A,50)]),[]);self.assertEqual(engine.late_samples,1)

    def test_exact_and_clock_on_different_sample_rates(self):
        f=checked(definition(alignment='exact'));engine=Engine(Plan([f]))
        engine.process(100,[row(A,10)]);self.assertIsNone(engine.process(110,[row(B,2)])[0]['value_numeric'])
        self.assertEqual(engine.process(120,[row(A,11),row(B,3)])[0]['value_numeric'],8)
        f=checked(definition(clock_nodeid=A));engine=Engine(Plan([f]));self.assertEqual(engine.process(100,[row(B,2)]),[])
        self.assertEqual(engine.process(110,[row(A,10)])[0]['value_numeric'],8)

    def test_chained_formulas_share_timeline_and_quality(self):
        f=checked(definition());g=normalize(dict(name='Percentuale',expression='([Delta] / [Originale])*100',bindings={'Delta':dict(nodeid=f['nodeid']),'Originale':dict(nodeid=A)}))
        FormulaStore.check([f,g]);engine=Engine(Plan([f,g]))
        result=engine.process(100,[row(A,10),row(B,2)]);self.assertEqual([r['value_numeric'] for r in result],[8,80])
        result=engine.process(110,[row(A,10),row(B,None)]);self.assertTrue(all(r['quality']=='Bad' for r in result))

class Processes(unittest.TestCase):
    def test_unicode_names_units_and_paths_with_a_non_utf8_worker_locale(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'PYTHONIOENCODING':'cp1252','PYTHONUTF8':'0'}):
            folder=Path(directory)/'Dati_à_Ω';folder.mkdir();outputs=[]
            f=checked(dict(name='Δ Caricò',unit='°C',expression='[Caricò] - [Valore]',bindings={'Caricò':dict(nodeid='ns=2;s=Caricò'),'Valore':dict(nodeid=B)}))
            service=CalculationService(folder/'cache',lambda rows,*_:outputs.extend(rows))
            try:
                service.configure([f],'opcua');service.submit(100,[dict(nodeid='ns=2;s=Caricò',plot_value=10,good=True),dict(nodeid=B,plot_value=2,good=True)],10);service.flush()
                self.assertEqual(outputs[-1]['value_numeric'],8);self.assertEqual(outputs[-1]['signal_name'],'Δ Caricò')
                original=folder/'prova_à.csv';export_rows([dict(node_id=n,timestamp_ms=100,value_numeric=v,quality='Good') for n,v in [('ns=2;s=Caricò',10),(B,2)]],original,'csv')
                library,name,_=service.history(RecordingLibrary(folder),original.name,[f['nodeid']],[f])
                view=library.load(name);self.assertEqual(view['series'][f['nodeid']][0][1],8)
                self.assertEqual(library._metadata(library._path(name))['signals'][0]['unit'],'°C')
            finally:service.close()

    def test_worker_failure_marks_a_gap_and_recovers_without_losing_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs=[];service=CalculationService(directory,lambda rows,*_:outputs.extend(rows))
            try:
                service.configure([checked(definition())],'opcua')
                def submit(t,a):service.submit(t,[dict(nodeid=A,plot_value=a,good=True),dict(nodeid=B,plot_value=2,good=True)],10)
                submit(100,10);service.flush();self.assertEqual(outputs[-1]['value_numeric'],8)
                service.process.kill();service.process.wait(timeout=2)
                submit(120,12);service.flush()
                self.assertTrue(any(r['quality']=='Bad' and 'CalculationWorkerError' in r['status'] for r in outputs))
                time.sleep(.2);submit(140,14);service.flush()
                self.assertEqual(outputs[-1]['value_numeric'],12);self.assertEqual(service.status()['error'],'')
            finally:service.close()

    def test_backpressure_does_not_block_producer_and_emits_quality_gap(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            outputs=[];ready=threading.Event();service=CalculationService(directory,lambda rows,*_:outputs.extend(rows),capacity=2)
            original=service._start
            def slow(config):ready.set();time.sleep(.2);original(config)
            try:
                with patch.object(service,'_start',side_effect=slow):
                    service.configure([checked(definition())],'opcua');self.assertTrue(ready.wait(2))
                    start=time.perf_counter()
                    for i in range(1000):service.submit(100+i,[dict(nodeid=A,plot_value=10,good=True),dict(nodeid=B,plot_value=2,good=True)],10)
                    self.assertLess(time.perf_counter()-start,.5);service.flush()
                    self.assertGreater(service.status()['dropped_batches'],0)
                    self.assertTrue(any(r['status']=='CalculationQueueOverflow' and r['quality']=='Bad' for r in outputs))
            finally:service.close()

    def test_live_process_reorders_samples_flushes_and_isolates_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs=[];service=CalculationService(directory,lambda rows,g,ms:outputs.extend((g,r) for r in rows))
            try:
                f=checked(definition());gen=service.configure([f],'opcua')
                for t,n,v in [(110,B,2),(100,A,10),(120,A,12)]:service.submit(t,[dict(nodeid=n,plot_value=v,good=True)],10)
                service.flush();self.assertEqual([r['timestamp_ms'] for g,r in outputs],[100,110,120]);self.assertEqual(outputs[-1][1]['value_numeric'],10)
                outputs.clear();gen2=service.configure([f],'opcua');service.submit(200,[dict(nodeid=A,plot_value=4,good=True),dict(nodeid=B,plot_value=1,good=True)],10);service.flush()
                self.assertTrue(outputs);self.assertTrue(all(g==gen2 for g,r in outputs));self.assertFalse(service.status()['error'])
            finally:service.close()

    def test_replay_matches_live_original_samples_and_materialized_columns_survive(self):
        for fmt in ('csv','parquet'):
            with self.subTest(fmt=fmt),tempfile.TemporaryDirectory() as directory:
                folder=Path(directory);f=checked(definition());original=list(series_rows());path=folder/('original.'+fmt)
                # Reverse input row order: replay must align by time rather than file position.
                export_rows(reversed(original),path,fmt);original_bytes=path.read_bytes();lib=RecordingLibrary(folder);service=CalculationService(folder/'cache',lambda *_:None)
                try:
                    start=1700000000100;end=1700000001000
                    cache,name,_=service.history(lib,path.name,[A,f['nodeid']],[f],start,end)
                    data=cache.load(name);actual=data['series'][f['nodeid']]
                    self.assertTrue(actual);self.assertEqual(actual[0][0],start);self.assertEqual(actual[-1][0],end)
                    self.assertAlmostEqual(actual[0][1],original[20]['value_numeric']-2)
                    output=folder/('result.'+fmt);saved,new_name,_=service.history(lib,path.name,[A,B,f['nodeid']],[f],destination=output,fmt=fmt)
                    # A stored calc column is a physical source even after registry deletion.
                    cache2,name2,_=service.history(saved,new_name,[f['nodeid']],[])
                    self.assertEqual(cache2.load(name2)['series'][f['nodeid']],saved.load(new_name,[f['nodeid']])['series'][f['nodeid']])
                    self.assertIn('calculation_formulas',saved._metadata(output));self.assertEqual(path.read_bytes(),original_bytes)
                finally:service.close()

if __name__=='__main__':unittest.main()
