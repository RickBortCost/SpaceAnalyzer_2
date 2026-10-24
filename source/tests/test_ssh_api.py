import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
DATA=tempfile.TemporaryDirectory()
os.environ['OPCUA_DATA_DIR']=DATA.name;os.environ['OPCUA_APP_DIR']=DATA.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class API(unittest.TestCase):
    def setUp(self):self.http=app.app.test_client();self.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    def test_token_and_origin_required(self):
        self.assertEqual(self.http.get('/api/ssh/status').status_code,403)
        self.assertEqual(self.http.post('/api/ssh/execute',json={'text':'x'}).status_code,403)
        self.assertEqual(self.http.get('/api/ssh/config',headers=self.headers,base_url='http://remote.example').status_code,403)
    def test_profile_filters_credentials_and_default_target(self):
        r=self.http.get('/api/ssh/config',headers=self.headers);self.assertEqual(r.json['config']['host'],'192.168.5.220')
        r=self.http.post('/api/ssh/config',headers=self.headers,json={'host':'192.168.5.220','password':'do-not-save','private_key':'secret'});self.assertEqual(r.status_code,200)
        saved=(app.SETTINGS_DIR/'ssh/ssh_profile.json').read_text();self.assertNotIn('password',json.loads(saved));self.assertNotIn('do-not-save',saved);self.assertNotIn('private_key',saved)
    def test_validation_and_bounds(self):
        for url,data in [('/api/ssh/connect',{'password':''}),('/api/ssh/config',{'port':0}),('/api/ssh/execute',{'text':'uname -a'}),('/api/ssh/trust',{'accept':True})]:
            self.assertEqual(self.http.post(url,json=data,headers=self.headers).status_code,400)
        self.assertEqual(self.http.post('/api/ssh/execute',data='x'*600000,headers=self.headers).status_code,413)
    def test_ssh_page_and_assets(self):
        html=self.http.get('/').text;self.assertIn('id="nav-ssh"',html);self.assertIn('id="ssh-page"',html)
        self.assertIn('/static/ssh.js',html);self.assertIn('/static/ssh.css',html)
        response=self.http.get('/static/ssh.js');self.assertEqual(response.status_code,200);response.close()

if __name__=='__main__':unittest.main(verbosity=2)
