"""Independent MQTT session alongside OPC UA; common PC arrival clock."""
import asyncio
import time
from contextlib import suppress
from mqtt_source import MQTTClient, value_row


class ParallelMQTT:
    def __init__(self, runtime, sink, selection, fatal):
        self.runtime, self.sink, self.selection = runtime, sink, selection
        self.fatal = fatal
        self.client = None
        self.task = None
        self.enabled = False
        self.state = 'disconnected'
        self.error = ''
        self.reconnections = 0

    async def start(self, cfg):
        await self.stop()
        self.cfg = dict(cfg, source='mqtt', mqtt_time_basis='arrival')
        self.enabled = True
        self.state = 'connecting'
        try:
            await self.open()
        except BaseException:
            await self.stop()
            raise
        self.task = asyncio.create_task(self.monitor())

    async def open(self):
        c = MQTTClient(self.cfg)
        try:
            await c.connect()
        except BaseException:
            with suppress(Exception):
                await c.disconnect()
            raise
        self.client = c
        c.configure_acquisition(self.selection, self.sink)
        self.state, self.error = 'connected', ''

    async def stop(self):
        self.enabled = False
        task, self.task = self.task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        c, self.client = self.client, None
        if c:
            with suppress(Exception):
                await c.disconnect()
        self.state = 'disconnected'

    async def monitor(self):
        while self.enabled:
            try:
                if self.client is None:
                    await self.open()
                    self.reconnections += 1
                await self.client.read_state()
                await asyncio.sleep(.2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = str(exc)
                c, self.client = self.client, None
                if c:
                    if c.error:self.fatal(c.error)
                    with suppress(Exception):
                        await c.disconnect()
                ids, period, generation = self.selection()
                ts = time.time()*1000
                rows = [value_row(n, None, 'Bad', 'MQTT collegamento interrotto', None, ts) for n in ids]
                if rows:
                    self.sink(ts, rows, period, generation)
                self.state = 'reconnecting' if self.cfg.get('auto_reconnect', True) else 'disconnected'
                if self.state == 'disconnected':
                    break
                await asyncio.sleep(1)

    def status(self):
        return dict(enabled=self.enabled, state=self.state, error=self.error,
                    reconnections=self.reconnections,
                    diagnostics=self.client.diagnostics() if self.client else {})
