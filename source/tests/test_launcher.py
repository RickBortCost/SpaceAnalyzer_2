"""Run the real HTTP service, exercise port collision, repeat launch and shutdown."""
import json
import os
from pathlib import Path
from html.parser import HTMLParser
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler, Request

root=Path(__file__).resolve().parents[1]
class TokenParser(HTMLParser):
    token=None
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='meta' and values.get('name')=='opcua-token':
            self.token=values.get('content')

with tempfile.TemporaryDirectory(prefix='opcua-launcher-') as directory:
    with socket.socket() as busy:
        busy.bind(('127.0.0.1',0));busy.listen();first_port=busy.getsockname()[1]
        env={**os.environ,'OPCUA_DATA_DIR':directory,'OPCUA_APP_DIR':directory,'OPCUA_NO_BROWSER':'1','OPCUA_WEB_PORT':str(first_port)}
        process=subprocess.Popen([sys.executable,str(root/'app.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            path=Path(directory)/'instance.json';deadline=time.monotonic()+12
            while not path.exists() and time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.05)
            info=json.loads(path.read_text());assert info['port']!=first_port
            url=f"http://127.0.0.1:{info['port']}";opener=build_opener(ProxyHandler({}))
            with opener.open(url,timeout=5) as response:html=response.read().decode()
            parser=TokenParser();parser.feed(html);token=parser.token
            assert token, 'Missing local API token in page metadata'
            with opener.open(url+'/api/status') as response:assert json.load(response)['app']=='Space Analyzer'
            second=subprocess.run([sys.executable,str(root/'app.py')],env=env,capture_output=True,text=True,timeout=10)
            assert second.returncode==0,(second.stdout,second.stderr)
            assert 'già avviata' in second.stdout
            workspace={'schema':1,'config':{'window_seconds':3600,'max_samples':10000,'buffer_mode':'samples'},'selected_nodes':[], 'live':{'charts':[{'name':'Saved','ids':[]}]},'history':{'charts':[]}}
            req_save=Request(url+'/api/workspace',data=json.dumps(workspace).encode(),headers={'Content-Type':'application/json','X-OPCUA-Token':token},method='POST')
            with opener.open(req_save,timeout=5) as response:assert json.load(response)['ok']
            imported=Path(directory)/'recording_imports'/'retained.csv';imported.write_text('timestamp_ms,node_id,value_numeric\n1,ns=2;s=X,1\n')
            req=Request(url+'/api/shutdown' ,data=b'{}',headers={'Content-Type':'application/json','X-OPCUA-Token':token},method='POST')
            with opener.open(req,timeout=5) as response:assert json.load(response)['ok']
            stdout,stderr=process.communicate(timeout=10)
            assert process.returncode==0,(stdout,stderr)
            assert (Path(directory)/'certificates/client.der').exists()
            assert (Path(directory)/'config.json').exists()
            path.unlink(missing_ok=True)
            process=subprocess.Popen([sys.executable,str(root/'app.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+12
            while not path.exists() and time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.05)
            info=json.loads(path.read_text());url=f"http://127.0.0.1:{info['port']}"
            with opener.open(url+'/api/config',timeout=5) as response:configuration=json.load(response)
            assert configuration['workspace']['live']['charts'][0]['name']=='Saved'
            assert configuration['workspace']['config']['window_seconds']==3600
            assert imported.exists(), 'Workspace original was deleted at restart'
            with opener.open(url,timeout=5) as response:html=response.read().decode()
            parser=TokenParser();parser.feed(html)
            req=Request(url+'/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-OPCUA-Token':parser.token},method='POST')
            with opener.open(req,timeout=5) as response:assert json.load(response)['ok']
            process.communicate(timeout=10);assert process.returncode==0
            print('PASS: HTTP startup, busy-port fallback, single instance, shutdown, workspace and imported originals across process restart')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
