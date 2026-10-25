"""Isolated asyncua test server for the C# application tests (test tooling only, not shipped)."""
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from asyncua import Server, ua
from asyncua.crypto.permission_rules import User, UserRole
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

TEST_DIR = tempfile.TemporaryDirectory(prefix='opcua-test-server-')


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
