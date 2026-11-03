"""Delayed fake sink tests: no GUI, network, serial or real audio devices."""
import importlib.util,threading,time,types,unittest
from pathlib import Path
import numpy as np
from audio_source_router import AudioSourceRouter
from legacy_adapter import RawPreviewAdapter,_Output
from board_follow import BoardLockAdapter
from test_board_follow import raw,wait,uart,Parser
from unittest.mock import patch,Mock

class DeadThread:
    def is_alive(self):return False

class FakeSink:
    def __init__(self,mode):self.mode=mode;self.closed=False;self.samples=0;self.thread=DeadThread()
    def feed(self,data):
        if self.closed:raise AssertionError('feeding closed sink')
        self.samples+=512 if self.mode==4 else len(data)//2
    def snapshot(self):return dict(fed_samples=self.samples,completed_samples=self.samples,error=None)
    def close(self):self.closed=True

class Null:
    def feed(self,a):pass
    def snapshot(self):return {}

BASE=types.SimpleNamespace(NullSink=Null)

class PreparedBackend:
    def __init__(self,config,lock,allowed,*,enable_playback,monitor_gain,playback_router,**kw):
        self.output=_Output(BASE,allowed,enable_playback,monitor_gain,playback_router);self.calls=0;self.pending_samples=0
    def consume(self,data):
        # One RAW packet contains THREE samples, not one 512-sample output
        # block. The former fake amplified output work by 512/3 and could
        # overflow under a full-suite load while testing a different contract.
        self.calls+=1;self.pending_samples+=3
        while self.pending_samples>=512:
            self.output.feed(np.ones(512,dtype=np.float32)*.01);self.pending_samples-=512
    def idle(self):pass
    def fault(self,reason):pass
    def snapshot(self):return dict(counts=dict(consumed=self.calls),output=self.output.snapshot())
    def finish(self):return self.snapshot()
    def close(self):self.output.close()

def paced(adapter,seconds=.12):
    n=int(seconds*16000);start=time.perf_counter();accepted=0
    for first in range(0,n,16):
        left=start+first/16000-time.perf_counter()
        if left>0:time.sleep(left)
        for seq in range(first,min(first+16,n)):accepted+=bool(adapter.consume_datagram(raw(seq)))
    return n,accepted

class SinkPreparation(unittest.TestCase):
    def test_60_and_200ms_open_finish_before_dsp_ready(self):
        for delay in (.06,.20):
            with self.subTest(delay=delay):
                entered=threading.Event();sinks=[]
                def factory(mode):
                    entered.set();time.sleep(delay);s=FakeSink(mode);sinks.append(s);return s
                route=AudioSourceRouter(sink_factory=factory);route.select(4)
                a=RawPreviewAdapter({},lock_deg=0,capacity=1024,enable_playback=True,playback_router=route,backend_factory=PreparedBackend,allow_test_factory=True)
                try:
                    self.assertTrue(entered.wait(1));self.assertFalse(a.wait_ready(0))
                    self.assertFalse(a.consume_datagram(raw(0)))
                    self.assertTrue(a.wait_ready(2));self.assertEqual(len(sinks),1)
                    n,accepted=paced(a);self.assertEqual(n,accepted,repr(a.health()))
                    wait(lambda:a.health()['processed_packets']==n)
                    snap=a.snapshot();self.assertEqual(snap['counts'].get('inbox_overflow_packets',0),0)
                    self.assertEqual(snap['counts']['startup_packets_not_queued'],1)
                    self.assertEqual(len(sinks),1);self.assertGreater(sinks[0].samples,0)
                    self.assertEqual(sinks[0].samples,(n*3//512)*512)
                    self.assertGreaterEqual(route.snapshot()['pc_prepare_ms_max'],delay*1000-3)
                finally:a.close();self.assertTrue(a.wait_closed(2));route.close()

    def test_factory_open_does_not_hold_router_lock(self):
        entered=threading.Event();release=threading.Event();results=[]
        def factory(mode):entered.set();self.assertTrue(release.wait(2));return FakeSink(mode)
        route=AudioSourceRouter(sink_factory=factory);route.select(4);owner=route.register_pc_owner()
        worker=threading.Thread(target=lambda:results.append(route.prepare_pc(owner)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1));read_done=threading.Event()
            reader=threading.Thread(target=lambda:(route.ticket(),route.snapshot(),read_done.set()));reader.start()
            self.assertTrue(read_done.wait(.2),'diagnostic read blocked behind factory open')
            release.set();worker.join(2);reader.join(2);self.assertEqual(results,[True])
        finally:release.set();worker.join(2);route.close()

    def test_selection_during_open_closes_orphan_and_cannot_publish_ABA(self):
        entered=threading.Event();release=threading.Event();sinks=[];prepared=[]
        def factory(mode):
            entered.set();self.assertTrue(release.wait(2));s=FakeSink(mode);sinks.append(s);return s
        route=AudioSourceRouter(sink_factory=factory);route.select(4);owner=route.register_pc_owner()
        opener=threading.Thread(target=lambda:prepared.append(route.prepare_pc(owner)));opener.start()
        selector=None
        try:
            self.assertTrue(entered.wait(1));selector=threading.Thread(target=lambda:route.select(2));selector.start()
            wait(lambda:route.snapshot()['mode']==0)
            release.set();opener.join(2);selector.join(2)
            self.assertEqual(prepared,[False]);self.assertTrue(sinks[0].closed)
            self.assertEqual(route.snapshot()['mode'],2)
            self.assertEqual(len(sinks),2,'mono must prepare after the superseded PC device is closed')
            self.assertIs(route.player,sinks[1]);self.assertFalse(sinks[1].closed);self.assertEqual(sinks[1].samples,0)
            route.select(4);new_owner=route.register_pc_owner();self.assertNotEqual(owner,new_owner)
            self.assertTrue(sinks[1].closed,'new PC selection must release the prepared mono device')
            self.assertTrue(route.prepare_pc(new_owner));current=route.player
            self.assertEqual(len(sinks),3);self.assertIs(current,sinks[2]);self.assertFalse(current.closed)
            self.assertFalse(route.feed_pc(np.zeros(512),route.ticket(),owner));route.flush_pc(owner)
            self.assertIs(route.player,current);self.assertFalse(current.closed)
        finally:release.set();opener.join(2);selector and selector.join(2);route.close()

    def test_unprepared_pc_feed_fails_without_inline_factory_call(self):
        calls=[];route=AudioSourceRouter(sink_factory=lambda mode:calls.append(mode))
        try:
            route.select(4);owner=route.register_pc_owner()
            with self.assertRaisesRegex(RuntimeError,'not prepared'):route.feed_pc(np.zeros(512),route.ticket(),owner)
            self.assertEqual(calls,[])
        finally:route.close()

    def test_open_failure_prevents_ready_and_is_visible(self):
        def factory(mode):raise OSError('synthetic open rejected')
        route=AudioSourceRouter(sink_factory=factory);route.select(4)
        a=RawPreviewAdapter({},lock_deg=0,capacity=1024,enable_playback=True,playback_router=route,backend_factory=PreparedBackend,allow_test_factory=True)
        try:
            self.assertTrue(a.wait_closed(2));self.assertFalse(a.wait_ready(0));self.assertEqual(a.snapshot()['state'],'failed')
            self.assertIn('synthetic open rejected',a.snapshot()['error']);self.assertIn('synthetic open rejected',route.snapshot()['error'])
        finally:a.close();a.wait_closed(2);route.close()

    def test_old_inline_open_reproduces_queue_overflow_with_same_delay(self):
        source=Path(__file__).parent/'regression_baselines/inline_open/audio_source_router.py'
        spec=importlib.util.spec_from_file_location('test_original_inline_router',source);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        def factory(mode):time.sleep(.20);return FakeSink(mode)
        route=old.AudioSourceRouter(sink_factory=factory);route.select(4)
        class InlineBackend:
            def __init__(self,cfg,lock,allowed,**kw):self.owner=route.register_pc_owner();self.allowed=allowed
            def consume(self,data):
                if self.allowed():route.feed_pc(np.zeros(512),route.ticket(),self.owner)
            def idle(self):pass
            def fault(self,r):pass
            def snapshot(self):return {}
            def finish(self):return {}
            def close(self):route.close()
        a=RawPreviewAdapter({},lock_deg=0,capacity=1024,backend_factory=InlineBackend,allow_test_factory=True)
        try:
            self.assertTrue(a.wait_ready(1));n,accepted=paced(a,.15)
            self.assertLess(accepted,n);self.assertGreater(a.snapshot()['counts'].get('inbox_overflow_packets',0),0)
        finally:a.close();self.assertTrue(a.wait_closed(3));route.close()

class PreparationCancellation(unittest.TestCase):
    def setUp(self):
        self.entered=threading.Event();self.release=threading.Event();self.sinks=[];self.children=[];self.seq=0;self.frame=3;self.parser=Parser()
        def sink_factory(mode):
            self.entered.set()
            if not self.release.wait(2):raise RuntimeError('test release deadline')
            s=FakeSink(mode);self.sinks.append(s);return s
        self.route=AudioSourceRouter(sink_factory=sink_factory);self.route.select(4)
        def child_factory(cfg,**kw):
            child=RawPreviewAdapter(cfg,backend_factory=PreparedBackend,allow_test_factory=True,**kw);self.children.append(child);return child
        self.bridge=BoardLockAdapter({},capacity=1024,enable_playback=True,playback_router=self.route,child_factory=child_factory,allow_test_factory=True,status_timeout_s=2.)
        self.bridge.set_listen_mode(4);self.bridge.consume_datagram(raw(0));self.status(1)
        self.assertTrue(self.entered.wait(1));wait(lambda:len(self.children)==1)
    def status(self,mode):
        wire=uart(mode=mode,az=12,el=8,frame=self.frame,seq=self.seq);self.seq+=1;self.frame+=3
        for m in self.parser.feed(wire):self.bridge.consume_uart_message(m)
        for e in self.parser.take_diagnostics():self.bridge.consume_uart_diagnostic(e)
    def tearDown(self):
        self.release.set();self.bridge.close();self.assertTrue(self.bridge.wait_closed(3));self.route.close()
    def assert_cancelled_without_fault(self,child):
        self.assertTrue(child.wait_closed(2));snap=child.snapshot()
        self.assertEqual(snap['state'],'cancelled');self.assertIsNone(snap['error'])
        self.assertEqual(snap['counts'].get('playback_preparation_cancelled'),1)
        self.assertFalse(child.wait_ready(0));self.assertFalse(self.bridge.snapshot()['sticky_failure'])
    def test_session_switch_to_mono_during_open_then_fresh_pc_lock_recovers(self):
        old=self.children[0];done=threading.Event();switch_errors=[]
        def switch():
            try:
                # Same order as the actual monitor Session.listen method.
                self.route.select(2);self.bridge.set_listen_mode(2)
            except Exception as exc:switch_errors.append(exc)
            finally:done.set()
        selector=threading.Thread(target=switch);selector.start()
        try:
            wait(lambda:self.route.snapshot()['mode']==0)
            self.status(1);time.sleep(.02)
            self.assertEqual(len(self.children),1,'route no longer selected; no interim backend may start')
            self.release.set();self.assertTrue(done.wait(2));selector.join(2)
            self.assertEqual(switch_errors,[]);self.assert_cancelled_without_fault(old)
            self.assertEqual(self.route.snapshot()['mode'],2);self.assertTrue(self.sinks[0].closed)
            self.assertEqual(len(self.sinks),2);self.assertIs(self.route.player,self.sinks[1]);self.assertFalse(self.sinks[1].closed)
            self.route.select(4);self.bridge.set_listen_mode(4);self.status(1)
            wait(lambda:len(self.children)==2)
            self.assertTrue(self.children[1].wait_ready(2));self.assertFalse(self.bridge.snapshot()['sticky_failure'])
            self.assertEqual(len(self.sinks),3);self.assertTrue(self.sinks[1].closed);self.assertFalse(self.sinks[2].closed)
        finally:self.release.set();selector.join(2)
    def test_no_during_open_releases_sink_and_next_yes_lock_recovers(self):
        old=self.children[0];self.status(0)
        self.assertTrue(old.health()['done'] or old.health()['state']=='stopping')
        self.release.set();self.assert_cancelled_without_fault(old)
        self.assertTrue(self.sinks[0].closed,'a successful native open after NO must be reclaimed')
        self.assertIsNone(self.route.player)
        self.status(1);wait(lambda:len(self.children)==2)
        self.assertTrue(self.children[1].wait_ready(2));self.assertFalse(self.bridge.snapshot()['sticky_failure'])

class BackendInitializationCleanup(unittest.TestCase):
    def test_pipeline_closed_when_output_preparation_fails(self):
        from legacy_adapter import OriginalBackend
        pipeline=types.SimpleNamespace(close=Mock())
        base=types.SimpleNamespace(Pipeline=lambda *a,**k:pipeline,NullSink=Null)
        frozen=types.SimpleNamespace(configure=lambda:None)
        def factory(mode):raise OSError('test native open fails')
        route=AudioSourceRouter(sink_factory=factory);route.select(4)
        try:
            with patch('legacy_adapter.load_base',return_value=(frozen,base,{})):
                with self.assertRaisesRegex(OSError,'test native open fails'):
                    OriginalBackend({},0.,lambda:True,enable_playback=True,playback_router=route)
            pipeline.close.assert_called_once_with()
        finally:route.close()

if __name__=='__main__':unittest.main(verbosity=2)
