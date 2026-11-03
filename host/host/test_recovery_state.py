"""No devices: explicit retry, true heartbeat and bounded transport recovery."""
import threading,time,unittest
from board_follow import BoardLockAdapter,TRANSPORT_RECOVERY_HOLDOFF_S
from test_board_follow import Parser,uart,raw,wait

class ControlledChild:
    all=[]
    def __init__(self,cfg,**kw):
        self.kw=kw;self.failed=False;self.ready=True;self.close_requested=False
        self.allow_close=True;self.closed=threading.Event();self.calls=[]
        self.snapshot_entered=threading.Event();self.snapshot_release=None
        self.callback_entered=threading.Event();self.callback_release=None
        self.created=time.monotonic();ControlledChild.all.append(self)
    def close_with_reason(self,reason):
        self.reason=reason;self.close_requested=True
        if self.allow_close:self.closed.set()
    def close(self):self.close_with_reason('fake_close')
    def wait_closed(self,t):return self.closed.wait(t)
    def wait_ready(self,t):return self.ready and not self.close_requested
    def health(self):return dict(state='failed' if self.failed else 'closed' if self.closed.is_set() else 'ready',error='injected native underflow' if self.failed else None)
    def snapshot(self):
        if self.close_requested and self.snapshot_release is not None:
            self.snapshot_entered.set();self.snapshot_release.wait(1.)
        return dict(self.health(),calls=len(self.calls))
    def consume_datagram(self,data):
        if self.callback_release is not None:
            self.callback_entered.set();self.callback_release.wait(1.);return False
        self.calls.append(data);return True

class Recovery(unittest.TestCase):
    def setUp(self):
        ControlledChild.all=[];self.p=Parser();self.seq=0
        self.a=BoardLockAdapter({},child_factory=ControlledChild,allow_test_factory=True,status_timeout_s=2.)
    def tearDown(self):
        for c in ControlledChild.all:
            c.closed.set()
            if c.snapshot_release:c.snapshot_release.set()
            if c.callback_release:c.callback_release.set()
        self.a.close();self.assertTrue(self.a.wait_closed(3))
    def status(self,**kw):
        kw.setdefault('seq',self.seq);self.seq=max(self.seq,kw['seq']+1)
        for message in self.p.feed(uart(**kw)):self.a.consume_uart_message(message)
        for diagnostic in self.p.take_diagnostics():self.a.consume_uart_diagnostic(diagnostic)
    def active(self):
        self.a.consume_datagram(raw());self.status(frame=3);wait(lambda:self.a.child is not None)
        self.assertTrue(self.a.consume_datagram(raw(1)))
        return self.a.child
    def fail(self,c):
        c.failed=True;wait(lambda:self.a.sticky_failure);wait(lambda:c.close_requested)
    def test_failure_updates_real_heartbeat_without_automatic_restart(self):
        c=self.active();self.fail(c);before=self.a.status_at
        time.sleep(.02);self.status(frame=30)
        self.assertEqual(self.a.status['source_frame'],30);self.assertGreater(self.a.status_at,before)
        self.assertTrue(self.a.sticky_failure);self.assertIsNone(self.a.desired)
        time.sleep(.02);self.assertEqual(len(ControlledChild.all),1)
        self.assertGreater(self.a.counts['status_received_while_failed'],0)
    def test_same_selected_mode4_can_explicitly_retry_after_failure(self):
        c=self.active();self.fail(c);self.status(frame=30)
        self.a.set_listen_mode(4);self.assertFalse(self.a.sticky_failure);self.assertIsNone(self.a.error)
        self.assertEqual(self.a.last_failure['error'],'injected native underflow')
        self.assertIsNone(self.a.desired)
        # Same-tick/new UART sequence without source progress is not new evidence.
        self.status(frame=30);self.assertIsNone(self.a.desired)
        self.status(frame=33);wait(lambda:len(ControlledChild.all)==2)
        # Real RAW transport continued while the operator was retrying. Earlier
        # contiguous groups cannot be backfilled into the new locked segment.
        for seq in range(2,11):self.assertFalse(self.a.consume_datagram(raw(seq)))
        self.assertTrue(self.a.consume_datagram(raw(11)))
        self.assertEqual(self.a.counts['operator_retries'],1)
        self.assertTrue(any(e.get('previous_error')=='injected native underflow' for e in self.a.events))
    def test_healthy_same_mode4_does_not_rebuild(self):
        c=self.active();generation=self.a.generation;self.a.set_listen_mode(4)
        self.assertIs(self.a.child,c);self.assertEqual(self.a.generation,generation)
    def test_retry_search_does_not_restore_old_locked_target(self):
        c=self.active();self.fail(c);self.a.set_listen_mode(4)
        self.status(mode=0,frame=30);time.sleep(.03)
        self.assertIsNone(self.a.desired);self.assertEqual(len(ControlledChild.all),1)
        self.status(frame=33);wait(lambda:len(ControlledChild.all)==2)
    def test_retry_waits_for_complete_retirement_ack(self):
        c=self.active();c.allow_close=False;self.fail(c)
        self.a.set_listen_mode(4);self.status(frame=30);time.sleep(.04)
        self.assertEqual(len(ControlledChild.all),1);self.assertIs(self.a.retiring,c)
        c.closed.set();wait(lambda:len(ControlledChild.all)==2)
    def test_retry_invalid_uart_never_starts(self):
        c=self.active();self.fail(c);self.a.set_listen_mode(4)
        self.status(mode=2,frame=30);time.sleep(.03)
        self.assertEqual(len(ControlledChild.all),1);self.assertIsNone(self.a.desired)
    def test_strict_gap_stops_and_repeated_gap_extends_creation_quiet_gate(self):
        c=self.active();self.assertFalse(self.a.consume_datagram(raw(3)));self.assertTrue(c.close_requested)
        self.status(frame=12);time.sleep(.04);self.assertEqual(len(ControlledChild.all),1)
        second_gap=time.monotonic();self.assertFalse(self.a.consume_datagram(raw(5)))
        self.status(frame=18);time.sleep(.05);self.assertEqual(len(ControlledChild.all),1)
        wait(lambda:len(ControlledChild.all)==2)
        self.assertGreaterEqual(ControlledChild.all[-1].created-second_gap,TRANSPORT_RECOVERY_HOLDOFF_S-.012)
        self.assertTrue(self.a.consume_datagram(raw(6)))
        self.assertEqual(len(c.calls),1);self.assertEqual(self.a.counts['raw_sequence_or_group_gap'],2)
    def test_warm_gap_delays_first_admission_without_destroying_uncovered_child(self):
        self.a.consume_datagram(raw());self.status(frame=3);wait(lambda:self.a.child is not None)
        c=self.a.child;self.assertFalse(self.a.consume_datagram(raw(2)))
        self.assertFalse(c.close_requested);self.assertFalse(self.a.consume_datagram(raw(3)))
        self.assertEqual(c.calls,[]);self.assertEqual(self.a.generation_enqueued_packets,0)
        time.sleep(.11);self.assertTrue(self.a.consume_datagram(raw(4)))
        self.assertEqual(len(c.calls),1);self.assertEqual(self.a.counts['warmup_noncovered_gaps'],1)
        self.assertGreater(self.a.counts['packets_during_transport_settle'],0)
    def test_slow_final_snapshot_is_not_under_callback_or_heartbeat_lock(self):
        c=self.active();c.snapshot_release=threading.Event()
        start=time.perf_counter();self.assertFalse(self.a.consume_datagram(raw(3)))
        self.assertLess(time.perf_counter()-start,.05)
        self.assertTrue(c.snapshot_entered.wait(.5))
        completed=threading.Event()
        t=threading.Thread(target=lambda:(self.status(frame=12),completed.set()));t.start()
        try:self.assertTrue(completed.wait(.05),'final diagnostic snapshot held UART authority lock')
        finally:c.snapshot_release.set();t.join(1.)
    def test_old_packet_callback_cannot_fail_a_new_generation(self):
        c=self.active();c.callback_release=threading.Event();done=threading.Event()
        t=threading.Thread(target=lambda:(self.a.consume_datagram(raw(2)),done.set()));t.start()
        self.assertTrue(c.callback_entered.wait(.5))
        self.status(mode=0,frame=9);self.status(frame=9);wait(lambda:len(ControlledChild.all)==2)
        new=self.a.child;c.callback_release.set();self.assertTrue(done.wait(1.));t.join(1.)
        self.assertFalse(self.a.sticky_failure);self.assertIs(self.a.child,new)
        self.assertEqual(self.a.counts['old_generation_callback_returns'],1)
    def test_rearm_after_close_is_not_a_new_attempt(self):
        self.active();self.a.close();self.assertTrue(self.a.wait_closed(2));generation=self.a.generation
        self.a.set_listen_mode(4);self.assertEqual(self.a.generation,generation)
    def test_gain_cache_survives_child_rebuild_and_forwards_once(self):
        c=self.active()
        class Route:
            def __init__(self):self.calls=[]
            def is_selected(self,mode):return mode==4
            def ticket(self):return 0 # no source changes in this gain-cache fixture
            def set_monitor_gain(self,gain):self.calls.append(gain);return {'requested':gain}
        route=Route();self.a.playback_router=route
        self.assertEqual(self.a.set_monitor_gain(3.)['requested'],3.)
        self.assertEqual(route.calls,[3.]);self.assertEqual(self.a.monitor_gain,3.)
        self.status(mode=0,frame=9);self.status(frame=9)
        wait(lambda:len(ControlledChild.all)==2)
        self.assertEqual(self.a.child.kw['monitor_gain'],3.)
    def test_invalid_gain_cannot_modify_cached_or_current_owner(self):
        self.active();original=self.a.monitor_gain
        for gain in (0.,-1.,8.01,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.a.set_monitor_gain(gain)
        self.assertEqual(self.a.monitor_gain,original)

if __name__=='__main__':unittest.main(verbosity=2)
