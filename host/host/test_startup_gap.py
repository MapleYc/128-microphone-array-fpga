"""Startup noncoverage is explicit; continuous DSP gaps remain strict.

No device, GUI, soundcard, socket or real DSP backend is opened. Tests use the
actual BoardLockAdapter, CRC-valid UART parser and strict M128 wire packets.
"""
import threading,time,unittest
from board_follow import BoardLockAdapter
from test_board_follow import Child,Parser,uart,raw,wait


class WarmChild(Child):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.ready=threading.Event()
    def wait_ready(self,timeout):return self.ready.is_set() and not self.closed
    def snapshot(self):
        return dict(state='closed' if self.closed else ('ready' if self.ready.is_set() else 'starting'),calls=len(self.calls),error=None)


class StartupGap(unittest.TestCase):
    def setUp(self):
        Child.all=[];self.a=BoardLockAdapter({},child_factory=WarmChild,allow_test_factory=True,status_timeout_s=2.)
        self.parser=Parser();self.uart_seq=0
    def tearDown(self):
        self.a.close();self.assertTrue(self.a.wait_closed(3))
    def status(self,**kw):
        kw.setdefault('seq',self.uart_seq);self.uart_seq+=1
        for m in self.parser.feed(uart(**kw)):self.a.consume_uart_message(m)
        for d in self.parser.take_diagnostics():self.a.consume_uart_diagnostic(d)
    def warming(self):
        self.assertFalse(self.a.consume_datagram(raw()))
        self.status(az=20,el=12)
        wait(lambda:self.a.child is not None)
        return self.a.child
    def counts(self):return self.a.snapshot()['counts']
    def assert_gap_counted_not_hidden(self,n=1):
        counts=self.counts()
        self.assertEqual(counts.get('raw_sequence_or_group_gap',0),n)
        self.assertEqual(counts.get('warmup_noncovered_gaps',0),n)
        self.assertTrue(any('warmup' in x.get('type','') or 'warmup' in x.get('reason','') for x in self.a.snapshot()['recent_events']))
    def test_starting_forward_gap_is_counted_but_does_not_cancel_child(self):
        child=self.warming();generation=self.a.generation
        self.assertFalse(self.a.consume_datagram(raw(2)))
        self.assertFalse(child.closed);self.assertIs(self.a.child,child);self.assertEqual(self.a.generation,generation)
        self.assertEqual(child.calls,[]);self.assert_gap_counted_not_hidden()
        # Still no DSP input: subsequent arriving data is explicitly warmup.
        self.assertFalse(self.a.consume_datagram(raw(3)));self.assertEqual(child.calls,[])
        child.ready.set()
        self.assertFalse(self.a.consume_datagram(raw(4)))
        time.sleep(.11) # explicit noncovered transport quiet interval after gap
        self.assertTrue(self.a.consume_datagram(raw(5)))
        self.assertEqual(len(child.calls),1);self.assertEqual(self.counts().get('enqueued_to_dsp'),1)
    def test_ready_before_first_accepted_gap_remains_noncoverage(self):
        child=self.warming();child.ready.set()
        self.assertFalse(self.a.consume_datagram(raw(2)))
        self.assertFalse(child.closed);self.assertEqual(child.calls,[]);self.assert_gap_counted_not_hidden()
        self.assertFalse(self.a.consume_datagram(raw(3)))
        time.sleep(.11) # first admission occurs only after the fixed quiet gate
        self.assertTrue(self.a.consume_datagram(raw(4)));self.assertEqual(len(child.calls),1)
    def test_after_first_accepted_gap_is_strict_and_requires_new_status(self):
        child=self.warming();child.ready.set();self.assertTrue(self.a.consume_datagram(raw(1)))
        self.assertFalse(self.a.consume_datagram(raw(3)));self.assertTrue(child.closed)
        self.assertIsNone(self.a.desired);self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
        self.assertFalse(self.a.consume_datagram(raw(4)));self.assertEqual(len(Child.all),1)
        self.status(az=20,el=12,frame=15);wait(lambda:len(Child.all)==2)
        new=Child.all[-1];new.ready.set();self.assertTrue(self.a.consume_datagram(raw(5)))
        self.assertEqual(len(new.calls),1)
    def test_gap_exemption_is_per_child_not_global_history(self):
        old=self.warming();old.ready.set();self.assertTrue(self.a.consume_datagram(raw(1)))
        self.status(mode=0,frame=6);self.assertTrue(old.closed)
        self.status(az=-20,el=-12,frame=6);wait(lambda:len(Child.all)==2)
        child=Child.all[-1];generation=self.a.generation
        self.assertFalse(self.a.consume_datagram(raw(3)))
        self.assertFalse(child.closed);self.assertEqual(self.a.generation,generation)
        self.assert_gap_counted_not_hidden()
    def test_fatal_flag_is_never_warmup_exempt(self):
        child=self.warming()
        self.assertFalse(self.a.consume_datagram(raw(2,flags=1)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('raw_fatal_flags'),1);self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_network_epoch_change_is_never_warmup_exempt(self):
        child=self.warming()
        self.assertFalse(self.a.consume_datagram(raw(1,epoch=401)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('raw_network_epoch_change'),1);self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_duplicate_or_backwards_frame_is_not_an_ordinary_forward_gap(self):
        child=self.warming()
        self.assertFalse(self.a.consume_datagram(raw(0)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_forward_sequence_with_backwards_frame_is_not_exempt(self):
        child=self.warming()
        self.assertFalse(self.a.consume_datagram(raw(2,first=0)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_source_reset_to_sequence_zero_is_never_exempt(self):
        child=self.warming();self.assertFalse(self.a.consume_datagram(raw(1)))
        self.assertFalse(self.a.consume_datagram(raw(0)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_stale_lock_does_not_authorize_warmup_gap_exemption(self):
        child=self.warming()
        with self.a.lock:self.a.status_at=time.monotonic()-self.a.timeout-1.
        self.assertFalse(self.a.consume_datagram(raw(2)));self.assertTrue(child.closed)
        self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)
    def test_target_or_controller_change_closes_starting_generation(self):
        old=self.warming();self.status(az=-20,el=-12,frame=6);self.assertTrue(old.closed)
        wait(lambda:len(Child.all)==2);current=Child.all[-1]
        self.status(az=-20,el=-12,epoch=8,seq=0,frame=9);self.assertTrue(current.closed)
        self.assertEqual(self.counts().get('warmup_noncovered_gaps',0),0)


if __name__=='__main__':unittest.main(verbosity=2)
