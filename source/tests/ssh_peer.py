"""Independent AsyncSSH protocol peer, loopback only; no production target."""
import asyncio
import threading
import asyncssh

class Peer:
    def __init__(self):
        self.loop=asyncio.new_event_loop();self.thread=threading.Thread(target=self.loop.run_forever,daemon=True);self.thread.start()
        self.key=asyncssh.generate_private_key('ssh-ed25519');self.client_key=asyncssh.generate_private_key('ssh-ed25519')
        self.auth_attempts=0;self.commands=[];self.connections=[];self.listener=None
        self.port=self.run(self.start())
    def run(self,coro):return asyncio.run_coroutine_threadsafe(coro,self.loop).result(10)
    async def start(self,port=0):
        peer=self
        class Auth(asyncssh.SSHServer):
            def connection_made(self,conn):peer.connections.append(conn)
            def begin_auth(self,username):return True
            def password_auth_supported(self):return True
            def public_key_auth_supported(self):return True
            def validate_password(self,username,password):peer.auth_attempts+=1;return username=='Administrator' and password=='test-password'
            def validate_public_key(self,username,key):peer.auth_attempts+=1;return username=='Administrator' and key==peer.client_key.convert_to_public()
        self.listener=await asyncssh.create_server(Auth,'127.0.0.1',port,server_host_keys=[self.key],process_factory=self.process,line_editor=False,encoding=None)
        return self.listener.get_port()
    async def process(self,p):
        cmd=p.command;self.commands.append(cmd)
        try:
            if cmd is None:
                p.stdout.write(b'test-shell$ ')
                while True:
                    line=await p.stdin.readline()
                    if not line:break
                    if line.strip()==b'exit':break
                    p.stdout.write(b'input:'+line+b'test-shell$ ')
                p.exit(0)
            elif cmd in ('/bin/sh -s','/usr/bin/python3 -s'):
                script=await p.stdin.read();p.stdout.write(b'SCRIPT:'+script);p.exit(0)
            elif cmd=='streams':
                p.stdout.write(b'first\n');await p.stdout.drain();await asyncio.sleep(.25)
                p.stderr.write(b'warning\n');p.stdout.write('temperatura è 42 °C\n'.encode());p.exit(7)
            elif cmd=='large':
                for _ in range(180):p.stdout.write(b'A'*32768);p.stderr.write(b'B'*32768);await p.stdout.drain();await p.stderr.drain()
                p.exit(0)
            elif cmd=='input':p.stdout.write(b'prompt\n');p.stdout.write(b'READ:'+await p.stdin.readline());p.exit(0)
            elif cmd=='slow':
                for _ in range(100):p.stdout.write(b'tick\n');await p.stdout.drain();await asyncio.sleep(.1)
                p.exit(0)
            elif cmd=='unicode':
                for b in 'è € 🟠'.encode():p.stdout.write(bytes([b]));await p.stdout.drain();await asyncio.sleep(.02)
                p.exit(0)
            elif cmd=='markup':p.stdout.write(b'<img src=x onerror=alert(1)>\n');p.stderr.write(b'err\n');p.exit(0)
            else:p.stdout.write(b'EXEC:'+str(cmd).encode()+b'\n');p.exit(0)
        except (asyncssh.Error,ConnectionError,asyncio.CancelledError):pass
    async def rotate(self):
        self.listener.close();await self.listener.wait_closed();self.key=asyncssh.generate_private_key('ssh-ed25519');await self.start(self.port)
    async def shutdown(self):
        self.listener.close();await self.listener.wait_closed()
        for c in self.connections:c.close()
        for c in self.connections:await c.wait_closed()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():task.cancel()
    def close(self):self.run(self.shutdown());self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(3);self.loop.close()
