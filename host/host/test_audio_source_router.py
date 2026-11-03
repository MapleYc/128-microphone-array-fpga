import collections,threading,time,unittest,sys,types,io
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
import numpy as np
from audio_source_router import AudioSourceRouter
from board_follow import BoardLockAdapter
from meeting_monitor import Parser
from test_board_follow import Child,uart,raw,wait
from legacy_adapter import _Output,RawPreviewAdapter

class DeadThread:
    def is_alive(self):return False
class Sink:
    def __init__(self,mode,events):self.mode=mode;self.events=events;self.closed=False;self.thread=DeadThread();self.samples=0;events.append(('open',mode))
    def feed(self,a):
        if self.closed:raise AssertionError('closed sink fed')
        self.samples+=512 if self.mode==4 else len(a)//2;self.events.append(('feed',self.mode))
    def close(self):self.closed=True;self.events.append(('close_reset_complete',self.mode))
    def snapshot(self):return dict(fed_samples=self.samples,completed_samples=self.samples,error=None)

class Tests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.sinks=[]
        def factory(mode):
            self.assertTrue(all(s.closed for s in self.sinks));s=Sink(mode,self.events);self.sinks.append(s);return s
        self.r=AudioSourceRouter(sink_factory=factory)
    def tearDown(self):self.r.close()
    def test_default_mute_no_device_open(self):self.assertEqual(self.r.snapshot()['mode'],0);self.assertFalse(self.sinks)
    def test_board_2_3_2_actual_source_and_reset(self):
        for mode in (2,3,2):
            self.r.select(mode);ticket=self.r.ticket()
            self.assertFalse(self.r.feed_board(3 if mode==2 else 2,b'\0\0'*16,ticket))
            self.assertTrue(self.r.feed_board(mode,b'\1\0'*16,ticket));self.assertEqual(self.sinks[-1].mode,mode)
        self.assertEqual(self.r.snapshot()['counts']['board_mono_16k_fed_samples'],32)
        self.assertEqual(self.r.snapshot()['counts']['board_beam_16k_fed_samples'],16)
    def test_mute_closes_and_suppresses_both_rates(self):
        self.r.select(4);ticket=self.r.ticket();self.assertTrue(self.r.prepare_pc());self.assertTrue(self.r.feed_pc(np.ones(512)*.1,ticket))
        self.r.select(0);self.assertTrue(self.sinks[-1].closed)
        self.assertFalse(self.r.feed_pc(np.ones(512),ticket));self.assertFalse(self.r.feed_board(2,b'\0\0',self.r.ticket()))
    def test_old_ticket_rejected_after_pc_mono_pc_ABA(self):
        self.r.select(4);old=self.r.ticket();self.r.select(2);self.r.select(4)
        self.assertFalse(self.r.feed_pc(np.ones(512)*.1,old))
        self.assertEqual(len(self.sinks),1,'mono was explicitly prepared during the intervening selection')
        self.assertEqual(self.sinks[0].mode,2);self.assertTrue(self.sinks[0].closed)
        self.assertEqual(self.sinks[0].samples,0,'old PC ticket must not feed the prepared mono sink')
        self.assertTrue(self.r.prepare_pc());self.assertTrue(self.r.feed_pc(np.ones(512)*.1,self.r.ticket()))
        self.assertEqual(len(self.sinks),2);self.assertEqual(self.sinks[-1].mode,4)
        self.assertFalse(self.sinks[-1].closed);self.assertEqual(self.sinks[-1].samples,512)
    def test_pc_flush_rejects_pre_fault_block(self):
        self.r.select(4);old=self.r.ticket();self.r.prepare_pc();self.r.feed_pc(np.zeros(512),old);self.r.flush_pc()
        self.assertEqual(self.r.snapshot()['mode'],4);self.assertTrue(self.sinks[-1].closed)
        self.assertFalse(self.r.feed_pc(np.zeros(512),old))
    def test_pc_backend_close_does_not_flush_board_sink(self):
        self.r.select(3);self.r.feed_board(3,b'\0\0'*4,self.r.ticket());self.r.flush_pc()
        self.assertFalse(self.sinks[-1].closed);self.assertEqual(self.r.snapshot()['mode'],3)
    def test_old_backend_late_close_cannot_flush_new_pc_owner(self):
        class Null:
            def feed(self,a):pass
            def snapshot(self):return {}
        base=types.SimpleNamespace(NullSink=Null)
        self.r.select(4);old=_Output(base,lambda:True,True,1.,self.r)
        old.feed(np.zeros(512));self.r.select(2);self.r.select(4)
        new=_Output(base,lambda:True,True,1.,self.r);new.feed(np.ones(512)*.1)
        sink=self.sinks[-1];epoch=self.r.ticket();old.close()
        self.assertFalse(sink.closed);self.assertEqual(self.r.ticket(),epoch)
        old.feed(np.zeros(512));self.assertIs(self.r.player,sink)
        self.assertEqual(sink.samples,512)
    def test_actual_output_mid_callback_selection_rejects_ticket(self):
        class Null:
            def feed(self,a):pass
            def snapshot(self):return {}
        self.r.select(4)
        def allowed():self.r.select(2);self.r.select(4);return True
        output=_Output(types.SimpleNamespace(NullSink=Null),allowed,True,1.,self.r)
        output.feed(np.zeros(512));self.assertTrue(all(s.closed and s.samples==0 for s in self.sinks))
    def test_manual_route_method_does_not_change_angle_or_create_device(self):
        a=types.SimpleNamespace(lock_deg=23)
        RawPreviewAdapter.set_listen_mode(a,4);self.assertEqual(a.lock_deg,23);self.assertEqual(a.requested_listen_mode,4)
    def test_board_mono_unlocked_is_audible_pc_waits_for_real_lock(self):
        Child.all=[];a=BoardLockAdapter({},playback_router=self.r,child_factory=Child,allow_test_factory=True,status_timeout_s=.5);p=Parser()
        def status(**kw):
            for m in p.feed(uart(**kw)):a.consume_uart_message(m)
        try:
            a.consume_datagram(raw());status(mode=0)
            self.r.select(2);a.set_listen_mode(2)
            self.assertTrue(self.r.feed_board(2,b'\1\0'*16,self.r.ticket()));self.assertFalse(Child.all)
            self.r.select(4);a.set_listen_mode(4);status(mode=0,seq=1,frame=6)
            time.sleep(.02);self.assertFalse(Child.all)
            status(mode=1,el=1,seq=2,frame=9);wait(lambda:len(Child.all)==1)
            self.assertEqual(Child.all[-1].el,1.)
            status(mode=1,el=0,seq=3,frame=12);wait(lambda:len(Child.all)==2);old=Child.all[-1]
            self.r.select(2);a.set_listen_mode(2);self.assertTrue(old.closed)
            self.r.select(4);a.set_listen_mode(4);time.sleep(.02);self.assertEqual(len(Child.all),2)
            status(mode=1,el=0,seq=4,frame=15);wait(lambda:len(Child.all)==3)
            status(mode=0,seq=5,frame=18);self.assertTrue(Child.all[-1].closed)
        finally:a.close();self.assertTrue(a.wait_closed(2))

if __name__=='__main__':unittest.main()
