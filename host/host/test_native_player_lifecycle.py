"""Actual WaveOut48 methods against a fake native API; never load WinDLL."""
import collections,ctypes as C,threading,time,unittest
import numpy as np
from base.playback48 import WaveOut48
from audio_source_router import AudioSourceRouter

class Header(C.Structure):
    _fields_=[('data',C.c_void_p),('flags',C.c_uint)]

class Lib:
    def __init__(self):self.prepare_code=0;self.before_unprepare=None
    def waveOutPrepareHeader(self,*a):return self.prepare_code
    def waveOutWrite(self,*a):return 0
    def waveOutUnprepareHeader(self,*a):
        if self.before_unprepare:
            f=self.before_unprepare;self.before_unprepare=None;f()
        return 0
    def waveOutReset(self,*a):return 0
    def waveOutClose(self,*a):return 0

def player():
    p=WaveOut48.__new__(WaveOut48);p.lib=Lib();p.handle=C.c_void_p();p.buffers=[C.create_string_buffer(1024) for _ in range(4)];p.headers=[Header(C.addressof(b),0) for b in p.buffers];p.size=C.sizeof(Header)
    p.busy=[False]*4;p.prepared=[False]*4;p.q=collections.deque();p.capacity=12;p.prefill=4;p.gain=1.;p.started=True;p.lock=threading.Lock();p.closed=False;p.error=None;p.error_info=None;p.counts=collections.Counter();p.thread=threading.current_thread()
    return p

class NativeLifecycle(unittest.TestCase):
    def test_original_native_error_survives_feed_rejection(self):
        p=player();p.error='waveOut error 7';p.error_info=dict(stage='waveOutWrite',at_ns=19)
        with self.assertRaisesRegex(RuntimeError,'waveOut error 7'):p.feed(np.zeros(512))
        s=p.snapshot();self.assertEqual(s['error_info']['stage'],'waveOutWrite');self.assertEqual(s['events'][-1]['event'],'feed_rejected_native_error')
    def test_real_run_api_error_preserves_first_stage_through_cleanup(self):
        p=player();p.q.append(bytes(1024));p.lib.prepare_code=7;p._run();s=p.snapshot()
        self.assertEqual(s['error'],'waveOut error 7');self.assertEqual(s['error_info']['stage'],'waveOutPrepareHeader');self.assertIsNotNone(s['worker_exit_ns']);self.assertEqual(s.get('underflow',0),0)
        self.assertTrue(any(x['event']=='native_error' for x in s['events']))
    def test_genuine_starvation_is_not_hidden(self):
        p=player();p._run();s=p.snapshot()
        self.assertEqual(s['underflow'],1);self.assertIn('underflow',s['error']);self.assertEqual(s['error_info']['stage'],'starvation')
    def test_close_during_iteration_is_not_false_underflow(self):
        p=player();p.busy[0]=True;p.prepared[0]=True;p.headers[0].flags=1
        p.lib.before_unprepare=lambda:p.close('test_owner_cancel')
        p._run();s=p.snapshot();self.assertIsNone(s['error']);self.assertEqual(s.get('underflow',0),0)
        self.assertTrue(s['closed']);self.assertEqual(s['close_reason'],'test_owner_cancel');self.assertLessEqual(s['close_requested_ns'],s['worker_exit_ns'])
    def test_router_keeps_native_reason_owner_and_close_history(self):
        class Sink:
            def __init__(self):self.error=None;self.closed=False;self.reason=None
            def feed(self,a):self.error='waveOut error 9';raise RuntimeError(self.error)
            def snapshot(self):return dict(error=self.error,error_info=dict(stage='waveOutWrite'),closed=self.closed,close_reason=self.reason,completed_samples=0)
            def close_with_reason(self,r):self.closed=True;self.reason=r
        made=[]
        def factory(mode):p=Sink();made.append(p);return p
        r=AudioSourceRouter(sink_factory=factory);r.select(4);owner=r.register_pc_owner();self.assertTrue(r.prepare_pc(owner))
        with self.assertRaisesRegex(RuntimeError,'waveOut error 9'):r.feed_pc(np.zeros(512),r.ticket(),owner)
        r.flush_pc(owner);s=r.snapshot();self.assertIsNone(s['player']);self.assertEqual(s['last_player']['owner'],owner);self.assertEqual(s['last_player']['native']['error'],'waveOut error 9');self.assertEqual(s['last_player']['native']['close_reason'],'pc_owner_flush')
        self.assertTrue(any(x['reason']=='feed_error' for x in s['player_lifecycle']))
        self.assertEqual(r.owner_diagnostics(owner)['last_player']['native']['error_info']['stage'],'waveOutWrite')
        r.close()

if __name__=='__main__':unittest.main()
