import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ssh_service import SSHService,DEFAULTS,profile
from ssh_peer import Peer

class SSH(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.peer=Peer();self.s=SSHService(self.temp.name)
        self.cfg={**DEFAULTS,'host':'127.0.0.1','port':self.peer.port,'password':'test-password'}
    def tearDown(self):self.s.close();self.peer.close();self.temp.cleanup()
    def wait(self,fn):
        end=time.monotonic()+10
        while time.monotonic()<end:
            if fn():return
            time.sleep(.01)
        self.fail(str(self.s.status()))
    def connect(self,cfg=None):
        self.s.connect(cfg or self.cfg);self.wait(lambda:self.s.state in ('awaiting_trust','connected','error'))
        if self.s.state=='awaiting_trust':
            p=self.s.status()['pending'];self.s.trust(dict(id=p['id'],fingerprint=p['fingerprint'],accept=True))
        self.wait(lambda:self.s.state in ('connected','error'));self.assertEqual(self.s.state,'connected',self.s.error)
    def run_job(self,**args):
        j=self.s.execute(args);self.wait(lambda:self.s.job['state'] not in ('starting','running'))
        self.s.run_thread.join(2);return self.s.status(0)
    def test_trust_precedes_auth_and_secrets_not_saved(self):
        self.s.connect(self.cfg);self.wait(lambda:self.s.state=='awaiting_trust');self.assertEqual(self.peer.auth_attempts,0)
        p=self.s.pending
        with self.assertRaises(ValueError):self.s.trust(dict(id=p['id'],fingerprint='wrong',accept=True))
        self.s.trust(dict(id=p['id'],fingerprint=p['fingerprint'],accept=True));self.wait(lambda:self.s.state=='connected')
        saved=''.join(p.read_text() for p in Path(self.temp.name).glob('*'));self.assertNotIn('test-password',saved);self.assertNotIn('private_key',saved)
        self.s.disconnect();self.s.conn_thread.join(2);self.connect();self.assertEqual(self.peer.auth_attempts,2)
    def test_reject_key_and_failed_auth(self):
        self.s.connect(self.cfg);self.wait(lambda:self.s.pending);p=self.s.pending;self.s.trust(dict(id=p['id'],fingerprint=p['fingerprint'],accept=False));self.wait(lambda:self.s.state=='error');self.assertEqual(self.peer.auth_attempts,0)
        self.s.conn_thread.join(2);self.s.connect({**self.cfg,'password':'wrong'});self.wait(lambda:self.s.pending);p=self.s.pending;self.s.trust(dict(id=p['id'],fingerprint=p['fingerprint'],accept=True));self.wait(lambda:self.s.state=='error');self.assertIn('Autenticazione',self.s.error)
    def test_changed_host_key_rejected(self):
        self.connect();self.s.disconnect();self.s.conn_thread.join(2);self.peer.run(self.peer.rotate());attempts=self.peer.auth_attempts
        self.s.connect(self.cfg);self.wait(lambda:self.s.state=='error');self.assertIn('diversa',self.s.error);self.assertEqual(self.peer.auth_attempts,attempts)
    def test_encrypted_private_key(self):
        text=self.peer.client_key.export_private_key('openssh',passphrase='key-pass').decode()
        with self.assertRaises(ValueError):self.s.connect({**self.cfg,'auth':'key','private_key':text})
        self.connect({**self.cfg,'auth':'key','private_key':text,'passphrase':'key-pass'})
        self.assertNotIn(text,Path(self.temp.name,'ssh_profile.json').read_text())
    def test_streams_before_exit_and_unicode(self):
        self.connect();self.s.execute(dict(text='streams'));self.wait(lambda:bool(self.s.events));self.assertEqual(self.s.job['state'],'running')
        self.wait(lambda:self.s.job['state']=='finished');self.s.run_thread.join(2);s=self.s.status(0);self.assertEqual(s['job']['exit_code'],7)
        text=''.join(e['text'] for e in s['events']);self.assertIn('42 °C',text);self.assertTrue(any(e['stream']=='stderr' for e in s['events']))
        self.s.clear();r=self.run_job(text='unicode');self.assertEqual(''.join(e['text'] for e in r['events']),'è € 🟠')
    def test_large_both_streams_bounded_and_no_deadlock(self):
        self.connect();r=self.run_job(text='large');self.assertEqual(r['job']['exit_code'],0);self.assertEqual(r['job']['bytes_received'],180*32768*2)
        self.assertGreater(r['dropped_bytes'],0);self.assertLessEqual(self.s.output_bytes,self.s.output_limit);self.assertTrue(r['gap'])
    def test_script_multiline_normalized(self):
        self.connect();r=self.run_job(mode='script',text='echo A\r\necho B',interpreter='/bin/sh');self.assertEqual(r['job']['exit_code'],0)
        self.assertIn('SCRIPT:echo A\necho B\n',''.join(e['text'] for e in r['events']))
    def test_shell_input_and_isolation(self):
        self.connect();j=self.s.execute(dict(mode='shell',timeout=30));self.wait(lambda:self.s.job['state']=='running' and bool(self.s.events))
        self.assertTrue(self.s.job['pty']);self.s.send_input(dict(id=j['id'],text='cd /tmp'));self.wait(lambda:any('input:cd /tmp' in e['text'] for e in self.s.events))
        with self.assertRaises(ValueError):self.s.execute(dict(text='second'))
        with self.assertRaises(ValueError):self.s.send_input(dict(id='stale',text='x'))
        self.s.send_input(dict(id=j['id'],text='exit'));self.wait(lambda:self.s.job['state']=='finished')
    def test_stop_timeout_and_reuse(self):
        self.connect();j=self.s.execute(dict(text='slow'));self.wait(lambda:bool(self.s.events));self.s.stop(j['id']);self.wait(lambda:self.s.job['state']=='cancelled');self.s.run_thread.join(2)
        r=self.run_job(text='slow',timeout=1);self.assertEqual(r['job']['state'],'timed_out');self.assertEqual(self.s.state,'connected');r=self.run_job(text='next');self.assertEqual(r['job']['exit_code'],0)
    def test_validation_and_disconnect_pending(self):
        for cfg in ({'port':0},{'host':'https://host'},{'auth':'none'},{'connect_timeout':False}):
            with self.assertRaises(ValueError):profile(cfg)
        with self.assertRaises(ValueError):self.s.execute(dict(text='x'))
        self.s.connect(self.cfg);self.wait(lambda:self.s.pending);self.s.disconnect();self.s.conn_thread.join(2);self.assertEqual(self.s.state,'disconnected');self.assertEqual(self.peer.auth_attempts,0)

if __name__=='__main__':unittest.main(verbosity=2)
