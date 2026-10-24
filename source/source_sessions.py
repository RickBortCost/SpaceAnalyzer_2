"""Independent protocol lifecycles sharing only a selection and an arrival sink.

All control runs on the application asyncio loop. Native/MQTT/UDP callbacks
carry their owning session token; obsolete callbacks cannot enter a new session.
"""
import asyncio
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone


PROTOCOLS = ('opcua', 'ads', 'mqtt', 's7')


def configured_sources(cfg):
    explicit = cfg.get('enabled_sources')
    if explicit:
        return set(explicit)
    return {cfg['source'], 'mqtt'} if cfg.get('mqtt_parallel') else {cfg['source']}


class SourceSession:
    def __init__(self, owner, protocol, cfg):
        self.owner, self.protocol = owner, protocol
        self.cfg = dict(cfg, source=protocol, mqtt_parallel=False)
        self.period = cfg.get('source_intervals', {}).get(protocol, max(1 if protocol=='ads' else 10,cfg['sample_ms']))
        self.cfg['sample_ms'] = self.period
        self.client = self.task = self.subscription = self.notification_worker = None
        # Clear while close() runs: Stop must not finish a recording before the
        # closing client's losses are checked (they would reach no session).
        self.idle = threading.Event(); self.idle.set()
        self.enabled = True
        self.state, self.error = 'connecting', ''
        self.reconnections = self.received = self.late_samples = 0
        self.last_time = None
        self.diag, self.info = {}, {}
        self.token = 0
        self.revision = None
        self.ever_opened = False
        self.ua_latest = {}

    def selection(self):
        ids, _, generation = self.owner.selection()
        return [n for n in ids if self.owner.protocol_of(n) == self.protocol], self.period, generation

    def accept(self, token, original_ms, rows, period, generation):
        if not self.enabled or token != self.token or not rows:
            return
        # One shared PC UTC arrival axis. Preserve the exact transport clock too.
        now_mono_ns=time.monotonic_ns();now_ns=time.time_ns();now=now_ns/1e6
        groups = {}
        for incoming in rows:
            row = dict(incoming)
            t = row.get('received_ms') or now
            row.setdefault('received_ns',now_ns);row.setdefault('_received_mono_ns',now_mono_ns)
            row['source_protocol']=self.protocol
            row['source_epoch']=str(token)+':'+str(row.get('source_epoch',0))
            row.setdefault('acquisition_timestamp_ms',original_ms)
            if row.get('acquisition_origin')=='subscription_snapshot':row['received_ns']=now_ns;row['_received_mono_ns']=now_mono_ns
            row.setdefault('local_ts', datetime.fromtimestamp(t/1000, timezone.utc).isoformat())
            groups.setdefault(t, []).append(row)
        for t, batch in groups.items():
            if self.last_time is not None and t < self.last_time:
                self.late_samples += len(batch)
            self.last_time = max(t, self.last_time or t)
            self.received += len(batch)
            self.owner.sink(t, batch, period, generation)

    def sink(self, token):
        return lambda t, rows, period, generation: self.accept(token, t, rows, period, generation)

    async def open(self):
        self.state = 'reconnecting' if self.ever_opened else 'connecting'
        c = await self.owner.factory(self.cfg)
        self.client = c
        self.token += 1
        token = self.token
        self.info = dict(getattr(c, 'info', {}))
        self.diag = {}
        self.heartbeat = 0
        self.state, self.error = 'connected', ''
        if getattr(self.owner,'events',None):self.owner.events.emit('source_connected',connection_id=self.owner.connection_id(self.protocol) if hasattr(self.owner,'connection_id') else None,protocol=self.protocol,epoch=token,reconnections=self.reconnections)
        self.owner.state_changed()
        if self.protocol == 'mqtt' or (self.protocol == 's7' and self.cfg['s7_mode'] == 'udp'):
            c.configure_acquisition(self.selection, self.sink(token))
        elif self.protocol == 'ads' and self.cfg['ads_mode'] == 'notify':
            from ads_notifications import NotificationAcquisition
            self.notification_worker = NotificationAcquisition(
                self.cfg, self.selection, self.sink(token), self.report)
            self.notification_worker.start()

    def report(self, diag):
        from ads_notifications import gap_report
        missing, gaps = gap_report(self.diag, diag)
        if gaps and getattr(self.owner, 'events', None):
            self.owner.events.emit('ads_source_gap', connection_id=self.owner.connection_id(self.protocol) if hasattr(self.owner, 'connection_id') else None,
                                   protocol=self.protocol, missing_samples=missing, gaps=gaps,
                                   policy='notifications missing upstream of the callback; recording continues and the gap is documented')
        self.diag = dict(diag)

    async def close(self):
        # Attribute a loss already visible before the client is detached: a Stop
        # running meanwhile would otherwise find no client to check.
        self.check_loss()
        self.idle.clear()
        try:
            await self._close()
        finally:
            self.idle.set()

    async def _close(self):
        # Keep the token valid while the owning queue drains on disconnect.
        worker, self.notification_worker = self.notification_worker, None
        if worker:
            worker.stop()
            await asyncio.to_thread(worker.thread.join)
        sub, self.subscription = self.subscription, None
        if sub:
            with suppress(Exception):
                await asyncio.wait_for(sub.delete(), 3)
        c, self.client = self.client, None
        if c:
            with suppress(Exception):
                await asyncio.wait_for(c.disconnect(), 5)
            self.check_loss(c)
        self.token += 1
        self.revision = None

    def check_loss(self, client=None):
        c = client or self.client
        if c and hasattr(c, 'diagnostics'):
            self.diag = c.diagnostics()
        if any(self.diag.get(k, 0) for k in ('queue_dropped', 'dropped', 'lost', 'timestamp_errors')):
            if getattr(self.owner,'events',None) and self.diag!=getattr(self,'reported_loss',None):
                self.owner.events.emit('source_loss',connection_id=self.owner.connection_id(self.protocol) if hasattr(self.owner,'connection_id') else None,protocol=self.protocol,diagnostics=self.diag);self.reported_loss=dict(self.diag)
            self.owner.fatal(self.protocol.upper() + ': perdita campioni rilevata; originali parziali conservati')
        if self.protocol == 'mqtt' and c and c.error:
            self.owner.fatal(c.error)

    async def subscribe(self, ids, generation):
        if self.subscription:
            await self.subscription.delete()
            self.subscription = None
        self.revision = (tuple(ids), generation[0])
        self.ua_latest.clear()
        if not ids:
            return
        session, token = self, self.token

        class Handler:
            def datachange_notification(self, node, value, data):
                if not session.enabled or token!=session.token or generation[0]!=session.selection()[2][0]: return
                nid=node.nodeid.to_string()
                row = session.owner.ua_row(nid, data.monitored_item.Value)
                row['acquisition_origin']='notification'
                row['_received_mono_ns']=time.monotonic_ns();row['received_ns']=time.time_ns()
                received=row['received_ns']/1e6
                session.ua_latest[nid]=(dict(row),received)
                if row.get('status_code', 0) & 0x80:
                    session.owner.fatal('OPC UA: overflow della coda MonitoredItem')
                # A clear invalidates display callbacks, never queued original rows.
                current = session.selection()[2]
                session.accept(token, received, [row], session.period,
                               (generation[0], current[1]))

            def status_change_notification(self, status):
                session.error = str(status)

        sub = await self.client.create_subscription(float(self.period), Handler())
        try:
            result = await sub.subscribe_data_change([self.client.get_node(n) for n in ids],
                queuesize=1024, sampling_interval=float(self.period))
            from asyncua import ua
            if any(isinstance(r, ua.StatusCode) and not r.is_good() for r in (result if isinstance(result, list) else [result])):
                raise ValueError('OPC UA: MonitoredItem rifiutato; uso Read polling')
            self.subscription = sub
            self.info['read_mode'] = 'OPC UA Subscription'
        except BaseException:
            with suppress(Exception):
                await sub.delete()
            raise

    async def acquire(self):
        c = self.client
        ids, period, generation = self.selection()
        if self.protocol == 'opcua':
            await c.nodes.server_state.read_value()
            if self.revision != (tuple(ids), generation[0]):
                try:
                    await self.subscribe(ids, generation)
                except Exception as exc:
                    self.error = str(exc)
                    self.info['read_mode'] = 'OPC UA Read fallback'
            if self.subscription is not None:
                # Keep the established periodic OPC UA snapshot for exact-aligned
                # formulas, while independently archiving every notification.
                stamp=time.time()*1000
                snapshots=[dict(self.ua_latest[n][0],acquisition_origin='subscription_snapshot',
                    acquisition_timestamp_ms=self.ua_latest[n][1]) for n in ids if n in self.ua_latest]
                if snapshots: self.accept(self.token,stamp,snapshots,period,generation)
            if ids and self.subscription is None:
                from asyncua import ua
                rows = []
                for nid in ids:
                    dv = await c.get_node(nid).read_data_value(raise_on_bad_status=False)
                    rows.append(self.owner.ua_row(nid, dv))
                self.accept(self.token, time.time()*1000, rows, period, generation)
        elif self.notification_worker:
            if self.notification_worker.error:
                self.owner.fatal('ADS: '+self.notification_worker.error)
                raise RuntimeError(self.notification_worker.error)
            if not self.notification_worker.thread.is_alive():
                raise RuntimeError('ADS: worker notifiche terminato')
            await c.read_state()
        elif self.protocol == 'mqtt' or (self.protocol == 's7' and self.cfg['s7_mode'] == 'udp'):
            await c.read_state()
        else:
            if time.monotonic() - getattr(self, 'heartbeat', 0) > 1:
                await c.read_state()
                await c.check_symbols()
                self.heartbeat = time.monotonic()
            if ids:
                rows = await c.read_values(ids)
                self.accept(self.token, time.time()*1000, rows, period, generation)
        self.check_loss()
        self.info.update(getattr(c,'info',{}))

    async def run(self):
        try:
            while self.enabled:
                try:
                    if self.client is None:
                        await self.open()
                        if self.ever_opened: self.reconnections += 1
                        self.ever_opened = True
                    start = time.monotonic()
                    await self.acquire()
                    await asyncio.sleep(max(.001, self.period/1000 - (time.monotonic()-start)))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.error = str(exc) or type(exc).__name__
                    if getattr(self.owner,'events',None):self.owner.events.emit('source_disconnected',connection_id=self.owner.connection_id(self.protocol) if hasattr(self.owner,'connection_id') else None,protocol=self.protocol,epoch=self.token,error=self.error)
                    await self.close()
                    ids, period, generation = self.selection()
                    rows = [dict(nodeid=n, value=None, good=False, quality='Bad', numeric=False,
                        plot_value=None, data_type='Null', status='ConnectionLost', error=self.error) for n in ids]
                    self.accept(self.token, time.time()*1000, rows, period, generation)
                    self.state = 'reconnecting' if self.cfg.get('auto_reconnect', True) else 'disconnected'
                    self.owner.state_changed()
                    if self.state == 'disconnected':
                        break
                    await asyncio.sleep(1)
        finally:
            await self.close()

    async def stop(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        else:
            await self.close()
        self.enabled = False
        self.state = 'disconnected'
        self.owner.state_changed()

    def status(self):
        return dict(enabled=self.enabled, state=self.state, error=self.error,
                    reconnections=self.reconnections, received_rows=self.received,
                    late_samples=self.late_samples, sample_ms=self.period,
                    diagnostics=dict(self.diag), info=dict(self.info))


class SourceSessions:
    def __init__(self, factory, selection, sink, fatal, protocol_of, ua_row, state_changed=lambda:None):
        self.state_changed=state_changed
        self.factory, self.selection, self.sink, self.fatal = factory, selection, sink, fatal
        self.protocol_of, self.ua_row = protocol_of, ua_row
        self.sessions = {}

    @property
    def enabled(self):
        return bool(self.sessions)

    async def start(self, cfg):
        await self.stop()
        self.sessions = {p: SourceSession(self, p, cfg) for p in PROTOCOLS if p in configured_sources(cfg)}
        # Failed sources keep their own retry loop; healthy sources remain usable.
        for s in self.sessions.values():
            s.task = asyncio.create_task(s.run(), name='source-' + s.protocol)
        await asyncio.sleep(0)

    async def stop(self):
        await asyncio.gather(*(s.stop() for s in self.sessions.values()))
        self.sessions = {}

    def client(self, protocol):
        s = self.sessions.get(protocol)
        if not s or not s.client or s.state != 'connected':
            raise ValueError(protocol.upper() + ': sorgente non connessa')
        return s.client

    def flush(self, timeout=5):
        # Runs off the event loop (finish_recording is always called via to_thread).
        deadline = time.monotonic() + timeout
        for s in list(self.sessions.values()):
            if not s.idle.wait(max(0, deadline-time.monotonic())):
                self.fatal(s.protocol.upper() + ': chiusura sorgente non completata alla chiusura della registrazione')
            if s.client and hasattr(s.client, 'flush'):
                if not s.client.flush(max(0, deadline-time.monotonic())):
                    self.fatal(s.protocol.upper() + ': coda non acquisita completamente alla chiusura')
            worker=s.notification_worker
            if worker:
                target=worker.accepted
                while worker.processed<target and worker.thread.is_alive() and time.monotonic()<deadline:
                    time.sleep(.005)
                if worker.processed<target: self.fatal('ADS: coda non drenata alla chiusura')
                s.report(worker.stats())
            s.check_loss()

    def status(self):
        return {p: s.status() for p, s in self.sessions.items()}
