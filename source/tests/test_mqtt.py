"""MQTT contract and real Paho TCP/TLS wire tests; no public broker."""
import asyncio
import ipaddress
import json
from pathlib import Path
import ssl
import socket
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mqtt_source import (DEFAULTS, MQTTClient, address, parse_address, topic_folder,
    topic_filters, pointer_value, validate_config, timestamp, flatten, value_row)
from mqtt_peer import Peer, until


class Contract(unittest.TestCase):
    def test_config_and_address_validation(self):
        self.assertEqual(topic_filters('plant/+/data\n$SYS/#\nplant/+/data'),['plant/+/data','$SYS/#'])
        for bad in ('a+','a/#/b','a/b#','', '\0'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):topic_filters(bad)
        for change in (dict(mqtt_qos=3),dict(mqtt_port=True),dict(mqtt_tls='false'),
                       dict(mqtt_host='mqtt://broker'),dict(mqtt_time_basis='payload'),
                       dict(mqtt_timestamp_pointer='value'),dict(mqtt_cert_file='cert'),
                       dict(mqtt_password='secret')):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_config({**DEFAULTS,**change})
        nid=address('plant/A','/a~1b/~0key/0')
        self.assertEqual(parse_address(nid)['topic'],'plant/A')
        self.assertEqual(pointer_value({'a/b':{'~key':[42]}},'/a~1b/~0key/0'),42)
        self.assertEqual(parse_address(topic_folder('plant/A'),True)['topic'],'plant/A')
        for bad in ('mqtt:/','mqtt:plant/+#','mqtt:plant/A#/bad~2','mqtt:plant/A'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_address(bad)

    def test_exact_timestamp_and_json_types(self):
        expected='2026-10-21T12:00:00.123456789+00:00'
        ms,exact=timestamp('2026-10-21T14:00:00.123456789+02:00','iso')
        self.assertEqual(exact,expected)
        self.assertEqual(timestamp('1792584000123456789','ns'),(ms,expected))
        self.assertEqual(timestamp('1792584000.123456789','s'),(ms,expected))
        for v,u in [('2026-10-21T12:00:00','iso'),(True,'ms'),('1.2','ns'),(-1,'ms'),('NaN','ms')]:
            with self.subTest(v=v),self.assertRaises(ValueError):timestamp(v,u)
        self.assertIn(('/a~1b/0',True),flatten({'a/b':[True,2]}))
        r=value_row('mqtt:a#',2**63+123,'Good','Good',None,0)
        self.assertEqual(r['value'],str(2**63+123));self.assertEqual(r['data_type'],'UInt64');self.assertTrue(r['precision_warning'])
        self.assertEqual(value_row('mqtt:a#',float('nan'),'Good','Good',None,0)['quality'],'Bad')
        json.dumps(value_row('mqtt:a#',float('nan'),'Good','Good',None,0),allow_nan=False)

    def test_queue_bounds_are_explicit_errors(self):
        cfg=validate_config({**DEFAULTS,'mqtt_queue':32,'mqtt_max_payload_kb':1})
        c=MQTTClient(cfg)
        message=SimpleNamespace(payload=b'1',topic='plant/A',retain=False,dup=False,mid=1,qos=1)
        for _ in range(33):c._on_message(None,None,message)
        self.assertEqual(c.diagnostics()['queue_samples'],32);self.assertEqual(c.counts['queue_dropped'],1)
        self.assertTrue(c.error);self.assertFalse(c.flush(.01))
        other=MQTTClient(cfg);message.payload=b'0'*1025;other._on_message(None,None,message)
        self.assertEqual(other.counts['oversized'],1);self.assertTrue(other.error)


class Wire(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.clients=[];self.peers=[]
    async def asyncTearDown(self):
        for c in self.clients:await c.disconnect()
        for p in self.peers:p.close()
    def peer(self,**kw):
        p=Peer(**kw);self.peers.append(p);return p
    async def connect(self,p,ids,**extra):
        cfg=validate_config({**DEFAULTS,'mqtt_port':p.port,'mqtt_topics':'plant/#','mqtt_timeout_ms':1500,**extra})
        c=MQTTClient(cfg);self.clients.append(c);rows=[]
        c.configure_acquisition(lambda:(ids,10,7),lambda t,r,period,g:rows.append((t,r,period,g)))
        await c.connect();return c,rows
    async def wait(self,predicate,timeout=5):await asyncio.to_thread(until,predicate,timeout)

    async def test_311_qos_json_browse_and_no_polling(self):
        p=self.peer();ids=['mqtt:plant/motor#/pressure','mqtt:plant/motor#/run','mqtt:plant/motor#/count','mqtt:plant/motor#/a~1b/0','mqtt:plant/text#']
        c,received=await self.connect(p,ids,mqtt_qos=2)
        for qos in (0,1,2):p.publish('plant/motor',{'pressure':12.5,'run':True,'count':2**63+123,'a/b':[7]},qos=qos)
        p.publish('plant/text','Caffè Ω',qos=1)
        await self.wait(lambda:len(received)==4 and len(p.acks)==3)
        self.assertEqual([q for q,_ in p.acks].count(2),1)
        self.assertEqual(received[0][1][2]['value'],str(2**63+123))
        self.assertEqual(next(r for _,batch,_,_ in received for r in batch if r['nodeid']=='mqtt:plant/text#')['value'],'Caffè Ω')
        self.assertTrue(all(r[3]==7 for r in received));self.assertNotIn(3,p.opcodes)
        root=await c.browse('mqtt:/');self.assertEqual(len(root),2)
        self.assertEqual(len(await c.scan_items('mqtt:/')),5)
        self.assertEqual((await c.inspect('mqtt:plant/notyet#/value'))['node_class'],'Variable')
        before=len(received);await asyncio.sleep(.15);self.assertEqual(len(received),before)
        self.assertFalse(p.errors,p.errors)

    async def test_v5_auth_retained_duplicate_and_quality(self):
        p=self.peer(auth=('user',' pass '),retained={'plant/data':b'{"value":1,"quality":"Good"}'})
        c,rows=await self.connect(p,['mqtt:plant/data#/value'],mqtt_version='5',mqtt_username='user',mqtt_password=' pass ',mqtt_qos=2,mqtt_quality_pointer='/quality')
        await self.wait(lambda:c.counts['processed']==1)
        self.assertFalse(rows);self.assertIn('mqtt:plant/data#/value',c.catalog);self.assertEqual(c.counts['retained_excluded'],1)
        p.publish('plant/data',{'value':4,'quality':'Uncertain'},qos=1)
        p.publish('plant/data',{'value':4,'quality':'Bad'},qos=1,dup=True)
        await self.wait(lambda:len(rows)==2)
        self.assertEqual([r[1][0]['quality'] for r in rows],['Uncertain','Bad'])
        self.assertEqual(c.counts['dup_flag'],1);self.assertFalse(p.errors,p.errors)
        c2,r2=await self.connect(p,['mqtt:plant/data#/value'],mqtt_include_retained=True,mqtt_username='user',mqtt_password=' pass ')
        await self.wait(lambda:len(r2)==1)
        self.assertEqual(r2[0][1][0]['quality'],'Uncertain');self.assertIsNone(r2[0][1][0]['plot_value'])

    async def test_payload_timestamps_and_bad_fields(self):
        p=self.peer();c,rows=await self.connect(p,['mqtt:plant/data#/value','mqtt:plant/data#/absent'],mqtt_time_basis='payload',mqtt_timestamp_pointer='/ts',mqtt_timestamp_unit='ns')
        p.publish('plant/data',{'ts':1792584000123456789,'value':3})
        await self.wait(lambda:len(rows)==1)
        self.assertEqual(rows[0][1][0]['source_ts'],'2026-10-21T12:00:00.123456789+00:00')
        self.assertEqual(rows[0][1][1]['quality'],'Bad')
        p.publish('plant/data',{'value':4});await self.wait(lambda:c.error)
        self.assertEqual(len(rows),1);self.assertEqual(c.counts['invalid_payloads'],1)
        with self.assertRaises(ConnectionError):await c.read_state()

    async def test_invalid_json_utf8_null_and_nonfinite(self):
        p=self.peer();c,rows=await self.connect(p,['mqtt:plant/data#/value'])
        for value in (b'{bad',b'\xff',{'value':None},b'{"value":NaN}'):
            p.publish('plant/data',value)
        await self.wait(lambda:len(rows)==4)
        self.assertTrue(all(r[1][0]['quality']=='Bad' for r in rows));self.assertFalse(c.error)

    async def test_auth_acl_and_connection_loss(self):
        for version in ('3.1.1','5'):
            p=self.peer(auth=('user','right'))
            with self.assertRaisesRegex(ValueError,'rifiutata'):await self.connect(p,[],mqtt_version=version,mqtt_username='user',mqtt_password='wrong')
            p=self.peer(reject_subscribe=True)
            with self.assertRaisesRegex(ValueError,'ACL'):await self.connect(p,[],mqtt_version=version)
        p=self.peer();c,_=await self.connect(p,[]);p.drop_all();await self.wait(lambda:c.lost.is_set())
        with self.assertRaises(ConnectionError):await c.read_state()

    async def test_tls_handshake_timeout_closes_transport(self):
        # A TCP listener that deliberately never speaks TLS.
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(4)
        c=MQTTClient(validate_config({**DEFAULTS,'mqtt_port':listener.getsockname()[1],'mqtt_tls':True,'mqtt_timeout_ms':1000}));self.clients.append(c)
        start=time.monotonic()
        try:
            with self.assertRaises(ValueError):await c.connect()
            self.assertLess(time.monotonic()-start,3)
            accepted,_=listener.accept();accepted.settimeout(1)
            try:
                while accepted.recv(65536):pass
            finally:accepted.close()
            self.assertTrue(c.stop_event.is_set())
        finally:listener.close()

    async def test_tls_verified_ca_hostname_and_client_cert(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
        with tempfile.TemporaryDirectory(prefix='mqtt-tls-') as temp:
            root=Path(temp);now=datetime.now(timezone.utc)
            ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Test CA')])
            ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),False).add_extension(x509.KeyUsage(digital_signature=False,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=False,decipher_only=False),True).sign(ca_key,hashes.SHA256()))
            (root/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
            for kind in ('server','client'):
                key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
                name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,kind)])
                builder=x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
                if kind=='server':builder=builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
                cert=builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if kind=='server' else ExtendedKeyUsageOID.CLIENT_AUTH]),False).sign(ca_key,hashes.SHA256())
                (root/(kind+'.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM));(root/(kind+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(root/'server.pem',root/'server.key');tls.load_verify_locations(root/'ca.pem');tls.verify_mode=ssl.CERT_REQUIRED
            p=self.peer(tls=tls)
            config=dict(mqtt_tls=True,mqtt_ca_file=str(root/'ca.pem'),mqtt_cert_file=str(root/'client.pem'),mqtt_key_file=str(root/'client.key'))
            c,rows=await self.connect(p,['mqtt:plant/a#'],**config);p.publish('plant/a',3);await self.wait(lambda:len(rows)==1)
            self.assertIn('TLS',c.info['transport'])
            with self.assertRaisesRegex(ValueError,'CERTIFICATE_VERIFY_FAILED'):await self.connect(p,[],**{**config,'mqtt_ca_file':''})
            with self.assertRaisesRegex(ValueError,'CERTIFICATE_VERIFY_FAILED'):await self.connect(p,[],**config,mqtt_host='localhost')


if __name__=='__main__':unittest.main(verbosity=2)
