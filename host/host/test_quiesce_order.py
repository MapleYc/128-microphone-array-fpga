"""Stop-order regression using real owner lifecycle and an in-memory deadline sink.

No device, WinDLL, UDP, serial, GUI, or media output. Slow finish and pipeline
shutdown are injected to isolate the previous cancellation-starvation defect.
"""
import importlib.util,sys,threading,time,types,unittest
from pathlib import Path
import numpy as np
from audio_source_router import AudioSourceRouter
import legacy_adapter as new

HERE=Path(__file__).resolve().parent
OLD=HERE/'regression_baselines'/'quiesce_order'
spec=importlib.util.spec_from_file_location('quiesce_old_legacy',OLD/'legacy_adapter.py')
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)

class DeadlineSink:
    def __init__(self):
        self.lock=threading.Lock();self.fed=threading.Event();self.stop=threading.Event();self.error_event=threading.Event();self.last_feed_ns=None;self.close_requested_ns=None;self.error_ns=None;self.error=None;self.closed=False
        self.thread=threading.Thread(target=self._run,name='test-deadline-sink',daemon=True);self.thread.start()
    def _run(self):
        while not self.stop.wait(.001):
            with self.lock:
                if self.last_feed_ns is not None and time.perf_counter_ns()-self.last_feed_ns>20_000_000:
                    self.error='playback underflow; injected 20ms deadline';self.error_ns=time.perf_counter_ns();self.error_event.set();return
    def feed(self,a):
        with self.lock:self.last_feed_ns=time.perf_counter_ns()
        self.fed.set()
    def request_close(self,reason='unspecified'):
        with self.lock:
            if self.close_requested_ns is None:self.close_requested_ns=time.perf_counter_ns()
            self.closed=True
        self.stop.set()
    def close(self,reason='unspecified'):
        self.request_close(reason);self.thread.join(.2)
    def close_with_reason(self,reason):self.close(reason)
    def snapshot(self):
        with self.lock:return dict(error=self.error,last_feed_ns=self.last_feed_ns,close_requested_ns=self.close_requested_ns,error_ns=self.error_ns,closed=self.closed)

class SlowBackend:
    def __init__(self,cfg,lock,allowed,**kw):
        self.allowed=allowed;self.sink=DeadlineSink();self.calls=0;self.timeline=[]
    def consume(self,data):
        self.calls+=1
        if self.allowed():self.sink.feed(None)
    def idle(self):pass
    def fault(self,reason):pass
    # Both aliases express a nonblocking device stop request. Parent selects the
    # production method name; neither invokes slow finish/shutdown or joins.
    def request_output_stop(self,reason):self.timeline.append(('request_output_stop',time.perf_counter_ns()));self.sink.request_close(reason)
    def request_output_quiesce(self,reason):self.request_output_stop(reason)
    def quiesce_output(self,reason):self.timeline.append(('quiesce_output',time.perf_counter_ns()));self.sink.close(reason)
    def snapshot(self):return dict(counts={'consumed':self.calls},sink=self.sink.snapshot(),timeline=list(self.timeline))
    def finish(self):
        self.timeline.append(('finish_begin',time.perf_counter_ns()));time.sleep(.08);self.timeline.append(('finish_end',time.perf_counter_ns()));return self.snapshot()
    def close_with_reason(self,reason):
        self.timeline.append(('pipeline_close_begin',time.perf_counter_ns()));time.sleep(.06);self.timeline.append(('pipeline_close_end',time.perf_counter_ns()));self.sink.close(reason)
    def close(self):self.close_with_reason('fake_backend_close')

def wait(fn,timeout=1.):
    end=time.perf_counter()+timeout
    while time.perf_counter()<end:
        if fn():return
        time.sleep(.001)
    raise AssertionError('timed out')

class QuiesceOrder(unittest.TestCase):
    def adapter(self,cls):
        made=[]
        def factory(*args,**kw):b=SlowBackend(*args,**kw);made.append(b);return b
        a=cls({},lock_deg=0,backend_factory=factory,allow_test_factory=True)
        self.assertTrue(a.wait_ready(1));self.assertTrue(a.consume_datagram(b'M128'+bytes(1204)));self.assertTrue(made[0].sink.fed.wait(1))
        return a,made[0]
    def test_old_slow_finish_reproduces_cancellation_underflow(self):
        a,b=self.adapter(old.RawPreviewAdapter)
        try:
            a.close_with_reason('test_normal_cancel');self.assertTrue(a.wait_closed(2))
            self.assertIsNotNone(b.sink.error);self.assertLess(b.sink.error_ns,b.sink.close_requested_ns)
        finally:b.sink.close()
    def test_new_nonblocking_request_precedes_slow_finish(self):
        a,b=self.adapter(new.RawPreviewAdapter)
        try:
            t=time.perf_counter();a.close_with_reason('test_normal_cancel');elapsed=time.perf_counter()-t
            self.assertLess(elapsed,.02,'control close must not wait for finish or sink thread')
            self.assertTrue(a.wait_closed(2));self.assertIsNone(b.sink.error)
            begin=dict(b.timeline)['finish_begin'];self.assertLessEqual(b.sink.close_requested_ns,begin)
            self.assertLessEqual(b.sink.close_requested_ns,dict(b.timeline)['pipeline_close_begin'])
            print('QUIESCE_ORDER_TIMING',dict(close_ms=elapsed*1000,native_request_ns=b.sink.close_requested_ns,finish_begin_ns=begin),flush=True)
        finally:b.sink.close()
    def test_existing_underflow_before_stop_is_retained(self):
        a,b=self.adapter(new.RawPreviewAdapter)
        try:
            self.assertTrue(b.sink.error_event.wait(1));first=b.sink.error_ns;a.close();self.assertTrue(a.wait_closed(2))
            self.assertEqual(b.sink.error_ns,first);self.assertIn('underflow',b.sink.error);self.assertLess(first,b.sink.close_requested_ns)
        finally:b.sink.close()
    def test_original_backend_output_closes_before_pipeline_shutdown(self):
        for cls,should_close_first in [(old.OriginalBackend,False),(new.OriginalBackend,True)]:
            timeline=[];b=cls.__new__(cls)
            def pipe_close():timeline.append('pipeline_begin');time.sleep(.02);timeline.append('pipeline_end')
            b.pipeline=types.SimpleNamespace(close=pipe_close)
            b.output=types.SimpleNamespace(close=lambda reason:timeline.append('output_close'))
            b.close('test_order')
            self.assertEqual(timeline.index('output_close')<timeline.index('pipeline_begin'),should_close_first)
    def test_late_old_owner_request_cannot_quiesce_new_sink(self):
        made=[]
        def factory(mode):s=DeadlineSink();made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory);r.select(4);old_owner=r.register_pc_owner();self.assertTrue(r.prepare_pc(old_owner));r.select(0);r.select(4);new_owner=r.register_pc_owner();self.assertTrue(r.prepare_pc(new_owner));current=r.player
        try:
            self.assertFalse(r.request_pc_quiesce(None,'test_no_owner'))
            self.assertFalse(r.request_pc_quiesce(0,'test_zero_owner'))
            self.assertFalse(r.request_pc_quiesce(old_owner,'test_late_old_stop'))
            self.assertIs(r.player,current);self.assertIsNone(current.close_requested_ns)
        finally:r.close()
    def test_passed_allowed_feed_barrier_is_quiesced_before_native_feed(self):
        entered=threading.Event();release=threading.Event();errors=[];made=[];allowed_calls=[]
        class BarrierNull:
            def feed(self,a):
                entered.set()
                if not release.wait(1):raise AssertionError('feed barrier timeout')
            def snapshot(self):return {}
        class Sink:
            def __init__(self):self.closed=False;self.feed_calls=0
            def feed(self,a):
                self.feed_calls+=1
                if self.closed:raise AssertionError('late feed reached closed native sink')
            def request_close(self,reason):self.closed=True
            def close(self):self.closed=True
            def snapshot(self):return dict(error=None,closed=self.closed)
        def factory(mode):s=Sink();made.append(s);return s
        def allowed():allowed_calls.append(True);return True
        r=AudioSourceRouter(sink_factory=factory);r.select(4)
        output=new._Output(types.SimpleNamespace(NullSink=BarrierNull),allowed,True,1.,r)
        def feed():
            try:output.feed(np.ones(512,dtype=np.float32)*.01)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=feed);worker.start()
        try:
            self.assertTrue(entered.wait(1));self.assertEqual(len(allowed_calls),2,'ctor allowed plus current feed allowed have both returned')
            epoch=r.ticket();self.assertTrue(output.request_stop('test_stop_after_allowed'))
            self.assertTrue(made[0].closed);self.assertEqual(r.ticket(),epoch,'must test quiesce gate without relying on epoch change')
            release.set();worker.join(1);self.assertFalse(worker.is_alive());self.assertFalse(errors)
            self.assertEqual(made[0].feed_calls,0);self.assertIsNone(r.error)
            self.assertEqual(r.snapshot()['counts']['quiesced_pc_blocks'],1)
        finally:release.set();worker.join(1);r.close()
    def test_real_native_request_close_preserves_prior_error_and_stage(self):
        # Call actual WaveOut48.request_close, with a constructor-free state;
        # no WinDLL load and no native API calls are involved.
        from base.playback48 import WaveOut48
        class DeadThread:
            def is_alive(self):return False
            def join(self,t):pass
        p=WaveOut48.__new__(WaveOut48);p.lock=threading.Lock();p.closed=False;p.error='playback underflow; original event'
        p.error_info=dict(stage='starvation',at_ns=101);p.q=[];p.busy=[False]*4;p.counts={'underflow':1};p.gain=1.;p.thread=DeadThread()
        r=AudioSourceRouter(sink_factory=lambda mode:p);r.select(4);owner=r.register_pc_owner();self.assertTrue(r.prepare_pc(owner));r.error=p.error
        try:
            self.assertTrue(r.request_pc_quiesce(owner,'normal_stop_after_error'))
            self.assertEqual(p.error,'playback underflow; original event');self.assertEqual(p.error_info,dict(stage='starvation',at_ns=101))
            self.assertEqual(r.error,p.error);self.assertTrue(p.closed)
        finally:r.close()
        self.assertEqual(r.snapshot()['last_player']['native']['error_info']['at_ns'],101)

if __name__=='__main__':unittest.main(verbosity=2)
