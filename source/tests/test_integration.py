"""Protocol integration checks against an isolated local asyncua server."""
import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DIR = tempfile.TemporaryDirectory(prefix='opcua-v1111-test-')
os.environ['OPCUA_DATA_DIR'] = TEST_DIR.name + '/settings-root'
os.environ['OPCUA_APP_DIR'] = TEST_DIR.name + '/application'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as client
from asyncua import Server, ua
from asyncua.crypto.permission_rules import User, UserRole
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from datetime import datetime, timedelta, timezone

logging.disable(logging.CRITICAL)

class TestUsers:
    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username is None or (username == 'test_operator' and password == 'test_password'):
            return User(role=UserRole.User)
        return None

async def make_server():
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=Server(user_manager=TestUsers());await server.init()
    endpoint=f'opc.tcp://127.0.0.1:{port}'
    server.set_endpoint(endpoint)
    uri='urn:opcua-web-client:integration-server';await server.set_application_uri(uri)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Integration server')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=30))
          .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri),x509.DNSName(socket.gethostname())]),critical=False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.KeyUsage(True,False,True,True,False,False,False,None,None),critical=True)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
          .sign(key,hashes.SHA256()))
    certpath=Path(TEST_DIR.name)/'test-server.der';keypath=Path(TEST_DIR.name)/'test-server.pem'
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.DER));keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    await server.load_certificate(certpath);await server.load_private_key(keypath)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity,ua.SecurityPolicyType.Basic256Sha256_Sign,ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    server.set_identity_tokens([ua.AnonymousIdentityToken,ua.UserNameIdentityToken])
    ns=await server.register_namespace('urn:opcua-web-client:tests')
    root=await server.nodes.objects.add_object(ua.NodeId('Line',ns),'Linea di collaudo')
    folder=await root.add_folder(ua.NodeId('Nested',ns),'Dati annidati')
    a=await folder.add_variable(ua.NodeId('Temperature',ns),'Temperatura',21.5)
    b=await folder.add_variable(ua.NodeId('Running',ns),'Marcia',True)
    text=await folder.add_variable(ua.NodeId('Text',ns),'Stato testuale','In produzione')
    arr=await folder.add_variable(ua.NodeId('Array',ns),'Array',[1.,2.,3.])
    bad=await folder.add_variable(ua.NodeId('Bad',ns),'Qualità scadente',1.)
    await bad.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(99.,ua.VariantType.Double),ua.StatusCode(ua.StatusCodes.BadOutOfService)))
    nan=await folder.add_variable(ua.NodeId('NaN',ns),'Non finito',float('nan'))
    prop=await a.add_property(ua.NodeId('Unit',ns),'Unità','°C')
    await folder.add_reference(root.nodeid,ua.ObjectIds.Organizes,forward=True,bidirectional=False)
    await server.start()
    return server,endpoint,root,[a,b,text,arr,bad,nan,prop]

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client.initialize();cls.http=client.app.test_client();cls.headers={'X-OPCUA-Token':client.SESSION_TOKEN}
        cls.server,cls.endpoint,cls.root,cls.nodes=client.runtime.run(make_server(),timeout=35)
        cls.ids=[n.nodeid.to_string() for n in cls.nodes]
    @classmethod
    def tearDownClass(cls):
        client.runtime.run(client.disconnect_command());client.runtime.run(cls.server.stop())
    def post(self,url,data=None,expected=200):
        response=self.http.post(url,json={} if data is None else data,headers=self.headers)
        self.assertEqual(response.status_code,expected,response.get_json());return response.get_json()
    def connect(self,secure=False,mode='SignAndEncrypt',**extra):
        cfg={'endpoint':self.endpoint,'security':'Basic256Sha256' if secure else 'None','security_mode':mode if secure else 'None','sample_ms':10,'auto_reconnect':True,**extra}
        if secure:self.post('/api/server-certificate/fetch',cfg)
        return self.post('/api/connect',cfg)
    def wait(self,predicate,seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.03)
        self.fail('Condition did not become true before timeout')
    def tearDown(self):
        self.post('/api/disconnect')
        self.post('/api/select',{'nodeids':[]})
    def test_01_scan_descendants_and_cycle(self):
        self.connect();self.post('/api/scan',{'root_nodeid':self.root.nodeid.to_string()})
        result=self.wait(lambda:(lambda j:j if j['state']!='running' else None)(self.http.get('/api/scan').get_json()))
        self.assertEqual(result['state'],'completed',result);nids=[n['nodeid'] for n in result['nodes']]
        self.assertEqual(len(nids),len(set(nids)));self.assertTrue(set(self.ids)<=set(nids));self.assertEqual(len(nids),9)
        page=self.http.get('/api/scan?offset=2&count=3').get_json();self.assertEqual(len(page['nodes']),3)
        self.assertEqual(self.http.get('/api/scan?id=stale').status_code,409)
    def test_02_batch_10ms_quality_and_types(self):
        self.connect();self.post('/api/select',{'nodeids':self.ids})
        data=self.wait(lambda:(lambda j:j if j['diagnostics']['read_count']>=35 else None)(self.http.get('/api/values').get_json()))
        rows={r['nodeid']:r for r in data['values']}
        self.assertEqual(rows[self.ids[0]]['plot_value'],21.5)
        self.assertEqual(rows[self.ids[1]]['plot_value'],1.)
        self.assertIsNone(rows[self.ids[2]]['plot_value']);self.assertEqual(rows[self.ids[3]]['value'],[1.,2.,3.])
        self.assertEqual(rows[self.ids[4]]['status'],'BadOutOfService');self.assertIsNone(rows[self.ids[4]]['plot_value'])
        self.assertIsNone(rows[self.ids[5]]['plot_value'])
        self.assertEqual(data['diagnostics']['requested_ms'],10)
        print('Measured diagnostics:',json.dumps(data['diagnostics']))
        self.assertLess(data['diagnostics']['mean_period_ms'],100)
        before=client.runtime.read_count;self.post('/api/select',{'nodeids':[self.ids[1]]});time.sleep(.05)
        data=self.http.get('/api/values').get_json();self.assertEqual(set(data['samples']),{self.ids[1]});self.assertGreater(client.runtime.read_count,before)
    def test_03_secure_sign_and_encrypt_with_user(self):
        self.connect(secure=True,username='test_operator',password='test_password')
        self.post('/api/select',{'nodeids':[self.ids[0]]})
        self.wait(lambda:client.runtime.read_count>2);self.assertTrue(client.runtime.connected)
        self.post('/api/certificate/regenerate',{},400)
    def test_04_secure_sign(self):
        self.connect(secure=True,mode='Sign',username='',password='');self.assertTrue(client.runtime.connected)
        result=client.runtime.run(client.read_selected_once([self.ids[0]]));self.assertTrue(result[0]['good'])
    def test_05_profile_and_certificate_restore(self):
        cfg={'endpoint':self.endpoint,'security':'Basic256Sha256','security_mode':'Sign','username':'test_operator','password':'test_password','root_nodeid':self.root.nodeid.to_string(),'sample_ms':10,'window_seconds':120,'scan_limit':10000,'auto_reconnect':False}
        nodes=[{'nodeid':self.ids[0],'display_name':'Temperatura completa'}]
        self.post('/api/settings/save',{'config':cfg,'selected_nodes':nodes,'server_data':{'discovered_endpoints':[{'security':'Basic256Sha256'}]}})
        loaded=self.http.get('/api/config').get_json()
        for k,v in cfg.items():self.assertEqual(loaded[k],v)
        self.assertEqual(loaded['selected_nodes'][0]['display_name'],'Temperatura completa')
        for f in [client.CERT_FILE,client.KEY_FILE,client.SERVER_CERT_FILE]:
            before=f.read_bytes();f.unlink();client.restore_saved_certificate_files();self.assertEqual(f.read_bytes(),before)
        before=client.CERT_FILE.read_bytes();client.initialize();self.assertEqual(client.CERT_FILE.read_bytes(),before)
        self.assertEqual(client.runtime.selected,[self.ids[0]])
    def test_06_validation_and_local_api(self):
        self.post('/api/config',{'sample_ms':0},400)
        self.post('/api/select',{'nodeids':['bad-node']},400)
        self.post('/api/select',{'nodeids':['i=85']*65},400)
        self.post('/api/config',{'security':'Unknown'},400)
        self.assertEqual(self.http.post('/api/disconnect',json={}).status_code,403)
        self.assertEqual(self.http.get('/api/config',headers={'Host':'attacker.invalid'}).status_code,403)
        self.assertEqual(self.http.get('/').status_code,200)
        with self.http.get('/static/app.js') as response:self.assertEqual(response.status_code,200)
    def test_07_manual_node_and_failed_browse(self):
        self.connect();result=self.http.get('/api/node',query_string={'nodeid':self.ids[0]}).get_json()
        self.assertEqual(result['node']['node_class'],'Variable')
        self.assertNotEqual(self.http.get('/api/browse?nodeid=ns=2;s=DoesNotExist').status_code,200)
    def test_08_clear_buffer_race(self):
        self.connect();self.post('/api/select',{'nodeids':[self.ids[0]]});self.wait(lambda:client.runtime.read_count>2)
        self.post('/api/samples/clear');epoch=client.runtime.buffer_epoch
        time.sleep(.04);self.assertEqual(client.runtime.buffer_epoch,epoch)
        points=list(client.runtime.samples[self.ids[0]]);self.assertLess(len(points),15)
    def test_09_connection_failure_and_recovery(self):
        self.connect();self.post('/api/select',{'nodeids':[self.ids[0]]});self.wait(lambda:client.runtime.read_count>2)
        # Terminate this test client's socket; server remains available for reconnect.
        async def lose():client.runtime.client.disconnect_socket()
        client.runtime.run(lose());self.wait(lambda:client.runtime.state=='reconnecting',seconds=12)
        self.wait(lambda:client.runtime.connected,seconds=15)
        self.assertTrue(any(p[1] is None for p in client.runtime.samples[self.ids[0]]))
    def test_10_cancel_and_limit_scan(self):
        self.connect()
        async def run_limited():
            await client.start_scan(self.root.nodeid.to_string(),3)
            await client.runtime.scan_task
        client.runtime.run(run_limited());self.assertEqual(client.runtime.scan['state'],'limited')
        async def run_cancel():
            await client.start_scan('i=84',50000)
            await asyncio.sleep(.001)
            await client.stop_scan()
        client.runtime.run(run_cancel());self.assertEqual(client.runtime.scan['state'],'cancelled')
    def test_11_extrema_and_gaps_preserved(self):
        points=[[i,1.] for i in range(10000)];points[4333][1]=1000;points[6555][1]=-1200;points[7400][1]=None
        compressed=client.compress_points(points)
        self.assertIn([4333,1000],compressed);self.assertIn([6555,-1200],compressed);self.assertIn([7400,None],compressed)
        self.assertLessEqual(len(compressed),1700)
    def test_12_large_integer_text_preserved(self):
        dv=ua.DataValue(ua.Variant(2**63-1,ua.VariantType.Int64));row=client.value_row('i=1',dv)
        self.assertEqual(row['value'],str(2**63-1));self.assertTrue(row['precision_warning'])

    def test_13_browse_next_and_release(self):
        self.connect()
        c=client.runtime.client
        original=c.uaclient.browse
        state={'rest':[], 'next_calls':0, 'released':False}
        async def paged(params):
            results=await original(params)
            result=results[0]
            state['rest']=list(result.References[1:])
            result.References=result.References[:1]
            result.ContinuationPoint=b'test-page' if state['rest'] else None
            return [result]
        async def next_page(params):
            state['next_calls']+=1
            if params.ReleaseContinuationPoints:
                state['released']=True
                return [ua.BrowseResult()]
            refs=state['rest'];state['rest']=[]
            return [ua.BrowseResult(References=refs)]
        async def exercise():
            with patch.object(c.uaclient,'browse',paged), patch.object(c.uaclient,'browse_next',next_page):
                result=await client.browse_node('ns=2;s=Nested')
                self.assertGreater(len(result),1)
                self.assertGreater(state['next_calls'],0)
                pages=client.reference_pages('ns=2;s=Nested')
                await anext(pages)
                await pages.aclose()
                self.assertTrue(state['released'])
        client.runtime.run(exercise())

    def test_14_record_csv_at_acquisition_rate_and_selection_lock(self):
        import csv
        self.connect()
        chosen=self.ids[:2]
        self.post('/api/select', {'nodeids':chosen})
        self.wait(lambda:len(client.runtime.latest_values)==2)
        nodes=[{'nodeid':nid,'display_name':'Segnale '+str(i)} for i,nid in enumerate(chosen)]
        start=self.post('/api/record/start', {'format':'csv','selected_nodes':nodes})['recording']
        self.assertEqual(start['state'],'recording')
        self.post('/api/record/start', {'format':'csv','selected_nodes':nodes},400)
        self.post('/api/select', {'nodeids':chosen[:1]},400)
        # Failed profile edits cannot alter disk settings while recording.
        saved=client.CONFIG_FILE.read_bytes()
        self.post('/api/settings/save', {'config':{'sample_ms':20},'selected_nodes':nodes[:1]},400)
        self.assertEqual(client.CONFIG_FILE.read_bytes(),saved)
        # A page reload sees the current recording selection, even if it was never saved.
        self.assertEqual({n['nodeid'] for n in self.http.get('/api/config').get_json()['selected_nodes']},set(chosen))
        self.wait(lambda:client.recording.status()['cycles_received']>=120)
        before=client.recording.status()['cycles_received']
        self.post('/api/samples/clear')
        self.wait(lambda:client.recording.status()['cycles_received']>=before+25)
        end=self.post('/api/record/stop')['recording']
        self.wait(lambda:not client.recording.busy)
        end=self.http.get('/api/record/status').get_json()['recording']
        self.assertEqual(end['state'],'stopped',end)
        self.assertEqual(Path(end['path']).parent,Path(os.environ['OPCUA_APP_DIR'])/'Recordings')
        self.assertFalse(str(end['path']).startswith(str(client.DATA_DIR)))
        with open(end['path'],encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),end['cycles_received']*2)
        self.assertEqual({r['node_id'] for r in rows},set(chosen))
        self.assertTrue(all(r['sample_ms']=='10' for r in rows))
        self.assertEqual(self.http.get('/api/values').get_json()['diagnostics']['acquisition_mode'],'OPC UA Subscription')
        self.post('/api/select', {'nodeids':chosen[:1]})

    def test_15_parquet_disconnect_finalizes(self):
        import pyarrow.parquet as pq
        self.connect()
        self.post('/api/select', {'nodeids':self.ids[:2]})
        self.wait(lambda:len(client.runtime.latest_values)==2)
        self.post('/api/record/start', {'format':'parquet','selected_nodes':self.ids[:2]})
        self.wait(lambda:client.recording.status()['cycles_received']>=35)
        self.post('/api/disconnect')
        end=client.recording.status()
        self.wait(lambda:not client.recording.busy)
        end=self.http.get('/api/record/status').get_json()['recording']
        self.assertEqual(end['state'],'stopped',end)
        self.assertEqual(end['reason'],'disconnect')
        table=pq.read_table(end['path'])
        self.assertEqual(table.num_rows,end['cycles_received']*2)
        self.assertEqual(table.column('value_numeric')[0].as_py(),21.5)

    def test_16_javascript_mime_remains_executable(self):
        import mimetypes
        previous=mimetypes.guess_type('app.js')[0]
        try:
            mimetypes.add_type('text/plain','.js')
            response=self.http.get('/static/app.js')
            self.assertIn(response.mimetype,('application/javascript','text/javascript'))
            response.close()
            html=self.http.get('/').text
            for element in ('pause-play','record','record-stop','record-modal','marker-x1','marker-x2'):
                self.assertIn('id="'+element+'"',html)
            self.assertNotIn('type="module"',html)
        finally:
            mimetypes.add_type(previous or 'application/javascript','.js')

    def test_17_external_recording_import_and_read(self):
        import io
        content=(
            'timestamp_ms,node_id,signal_name,value_numeric,good,data_type,sample_ms\n'
            '1000,A,External A,1.5,true,Double,10\n'
            '1010,A,External A,2.5,true,Double,10\n'
            '1000,B,External B,1000,true,Double,10\n'
            '1010,B,External B,1200,true,Double,10\n'
        ).encode('utf-8')
        response=self.http.post('/api/recordings/import',data={'file':(io.BytesIO(content),'external_capture.csv')},headers=self.headers,content_type='multipart/form-data')
        self.assertEqual(response.status_code,200,response.get_json())
        info=response.get_json()['recording']
        self.assertEqual(info['source'],'local');self.assertEqual(info['display_name'],'external_capture.csv')
        self.assertFalse((Path(os.environ['OPCUA_APP_DIR'])/'Recordings'/info['name']).exists())
        loaded=self.post('/api/recordings/data',{'source':'local','name':info['name'],'signal_ids':None,'max_points':5000})['recording']
        self.assertEqual(set(loaded['series']),{'A','B'});self.assertEqual(loaded['series']['A'][1][1],2.5)
        markers=self.post('/api/recordings/markers',{'source':'local','name':info['name'],'signal_ids':['A','B'],'x1':1000,'x2':1010})['markers']
        self.assertEqual(markers['signals'][0]['x2']['timestamp_ms'],1010)
        bad=self.http.post('/api/recordings/import',data={'file':(io.BytesIO(b'x'),'not_allowed.txt')},headers=self.headers,content_type='multipart/form-data')
        self.assertEqual(bad.status_code,400)

if __name__=='__main__':unittest.main(verbosity=2)
