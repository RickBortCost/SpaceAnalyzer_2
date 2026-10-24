"""Cyclic ADS callback simulation through the actual Flask, buffers and recorder."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ads import app
from test_ads_notifications import NotifyDLL,until,FILETIME_EPOCH
from ads_native import configure_library

class ADSCalculation(unittest.TestCase):
    def test_cyclic_plc_timestamps_and_quality_feed_the_shared_engine(self):
        dll=NotifyDLL();configure_library(dll)
        with patch('ads_native.load_library',return_value=(dll,'fake64.dll')):
            app.initialize();http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
            def post(path,data):
                result=http.post(path,json=data,headers=headers);self.assertEqual(result.status_code,200,result.json);return result.json
            f=None
            try:
                post('/api/select',{'nodeids':['ads:MAIN.Wave','ads:MAIN.Running']})
                f=post('/api/calculations/save',dict(name='Wave calcolata',expression='[Wave]*2 + [Run]',
                    bindings={'Wave':dict(nodeid='ads:MAIN.Wave'),'Run':dict(nodeid='ads:MAIN.Running')}))['formula']
                post('/api/connect',{**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_mode':'notify','sample_ms':10})
                until(lambda:len(dll.subs)==2)
                base=FILETIME_EPOCH+1700000000*10000000+1234567
                for i in range(100):dll.emit(base+i*100000)
                until(lambda:app.runtime.read_count>=100)
                app.calculations.flush()
                until(lambda:len(app.runtime.samples[f['nodeid']])==100)
                points=list(app.runtime.samples[f['nodeid']]);self.assertEqual(len(points),100)
                self.assertTrue(all(p[2]=='Good' for p in points));self.assertAlmostEqual(points[-1][0]-points[0][0],990,places=3)
                raw=list(app.runtime.samples['ads:MAIN.Wave']);self.assertEqual([p[0] for p in points],[p[0] for p in raw])
                self.assertTrue(all(p[5] is None for p in points));self.assertEqual(raw[0][11],str(base))
                post('/api/record/start',dict(format='parquet',selected_nodes=[{'nodeid':n} for n in ('ads:MAIN.Wave','ads:MAIN.Running',f['nodeid'])]))
                for i in range(100,140):dll.emit(base+i*100000)
                until(lambda:app.runtime.read_count>=140)
                post('/api/record/stop',{});until(lambda:not app.recording.busy,30)
                state=app.recording.status();self.assertEqual(state['state'],'stopped',state)
                rows=list(app.recording_library._iter_rows(Path(state['path'])));self.assertEqual(len(rows),120)
                calc=[r for r in rows if r['node_id']==f['nodeid']];self.assertEqual(len(calc),40)
                self.assertTrue(all(r['source_timestamp'] and r['local_timestamp'] and r['server_timestamp'] is None for r in calc))
                dll.peer.ads_state=6;until(lambda:getattr(app.runtime,'ads_notification_diag',{}).get('ads_state')==6)
                final_ticks = base+140*100000
                dll.emit(final_ticks)
                # A callback group is not necessarily a complete PLC cycle.
                # Wait for both original samples, not a cumulative group count.
                until(lambda:all(app.runtime.samples.get(n) and app.runtime.samples[n][-1][11]==str(final_ticks)
                    for n in ('ads:MAIN.Wave','ads:MAIN.Running')))
                app.calculations.flush()
                until(lambda:app.runtime.calculated_latest[f['nodeid']]['quality']=='Uncertain')
                self.assertEqual(app.runtime.calculated_latest[f['nodeid']]['quality'],'Uncertain')
                self.assertIsNone(app.runtime.calculated_latest[f['nodeid']]['value'])
            finally:
                app.runtime.run(app.disconnect_command())
                if f:post('/api/calculations/delete',{'id':f['id']})

if __name__=='__main__':unittest.main()
