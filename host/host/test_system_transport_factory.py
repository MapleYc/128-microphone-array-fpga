"""No devices: blocked construction, cancellation/version and close barriers."""
import threading,time,types,unittest
from audio_source_router import AudioSourceRouter
from board_follow import BoardLockAdapter
from legacy_adapter import PlaybackPreparationCancelled
from test_board_follow import raw,uart,Parser,wait

class FakeThread:
    def __init__(self,closed):self.closed=closed
    def is_alive(self):return not self.closed.is_set()
class RemoteSink:
    def __init__(self,closed,auto_close=True):
        self.closed_event=closed;self.auto_close=auto_close;self.close_requested=False;self.gain=1.
        self.adapter=types.SimpleNamespace(shared=types.SimpleNamespace(native_closed=closed));self.thread=FakeThread(closed)
    def request_close(self,reason='close'):
        self.close_requested=True
        if self.auto_close:self.closed_event.set()
    def close(self):self.request_close()
    def close_with_reason(self,reason):self.request_close(reason)
    def snapshot(self):return dict(closed=self.closed_event.is_set(),monitor_gain=self.gain,completed_samples=0,error=None)

class Candidate:
    def __init__(self,az,kwargs,auto_close=True):
        self.az=az;self.el=kwargs.get('lock_elevation_deg',0);self.proxy=kwargs.get('playback_router')
        self.closed=threading.Event();self.done=threading.Event();self.calls=[];self.owner=None;self.sink=None;self.auto_close=auto_close
        if self.proxy is not None:
            self.owner=self.proxy.register_pc_owner();self.sink=RemoteSink(self.closed,auto_close)
            if not self.proxy.install_remote_pc(self.owner,self.sink):self.close()
    def complete(self):
        self.closed.set();self.done.set()
        if self.proxy is not None:self.proxy.release_remote_pc(self.owner,self.sink)
    def close_with_reason(self,reason):
        if self.sink is not None:self.sink.request_close(reason)
        if self.auto_close:self.complete()
    def close(self):self.close_with_reason('close')
    def wait_closed(self,timeout):return self.done.wait(timeout)
    def wait_ready(self,timeout):return not self.closed.is_set()
    def health(self):return dict(state='closed' if self.closed.is_set() else 'ready',error=None)
    def snapshot(self):return dict(self.health(),az=self.az,calls=len(self.calls))
    def consume_datagram(self,data):self.calls.append(data);return not self.closed.is_set()

class Factory:
    def __init__(self,stage='before',first_auto_close=True,raise_after_install=False):
        self.stage=stage;self.first_auto_close=first_auto_close;self.raise_after_install=raise_after_install
        self.entered=threading.Event();self.release=threading.Event();self.started=0;self.made=[]
    def __call__(self,cfg,*,lock_deg,**kw):
        self.started+=1;first=self.started==1
        if first and self.stage=='before':self.entered.set();self.release.wait(2)
        child=Candidate(lock_deg,kw,self.first_auto_close if first else True);self.made.append(child)
        if self.raise_after_install and first:raise OSError('injected constructor failure after remote install')
        if first and self.stage=='after':self.entered.set();self.release.wait(2)
        return child

class FactoryTests(unittest.TestCase):
    def setUp(self):self.bridges=[];self.routers=[];self.factories=[]
    def tearDown(self):
        for f in self.factories:
            f.release.set()
            for c in f.made:c.complete()
        for b in self.bridges:b.close()
        for b in self.bridges:self.assertTrue(b.wait_closed(3))
        for r in self.routers:r.close()
    def create(self,f,router=False):
        self.factories.append(f);r=None
        if router:
            r=AudioSourceRouter(sink_factory=lambda mode:RemoteSink(threading.Event(),True));r.select(4);self.routers.append(r)
        b=BoardLockAdapter({},status_timeout_s=2,child_factory=f,allow_test_factory=True,playback_router=r)
        self.bridges.append(b)
        if r:b.set_listen_mode(4)
        b.consume_datagram(raw());self.status(b);self.assertTrue(f.entered.wait(1))
        return b,r
    def status(self,b,**kw):
        for m in Parser().feed(uart(**kw)):b.consume_uart_message(m)
    def test_slow_factory_does_not_hold_uart_or_udp_authority_lock(self):
        f=Factory('before');b,r=self.create(f)
        start=time.perf_counter();self.assertFalse(b.consume_datagram(raw(1)));self.status(b,mode=0,seq=1,frame=6)
        self.assertLess(time.perf_counter()-start,.05)
        self.assertEqual(b.status['mode'],'SEARCH');self.assertIsNone(b.child);self.assertIsNotNone(b.pending)
        f.release.set();wait(lambda:b.pending is None)
        self.assertEqual(b.counts['candidate_superseded'],1)
        self.assertTrue(f.made[0].closed.is_set());self.assertFalse(f.made[0].calls)
    def test_superseded_before_registration_cannot_change_real_owner(self):
        f=Factory('before');b,r=self.create(f,True);self.assertIsNone(r.pc_owner)
        self.status(b,mode=0,seq=1,frame=6);f.release.set();wait(lambda:b.pending is None)
        self.assertIsNone(r.pc_owner);self.assertFalse(f.made);self.assertIsNone(r.player)
    def test_target_jump_waits_for_real_retirement_before_latest_publication(self):
        f=Factory('after',False);b,r=self.create(f,True);first=f.made[0]
        self.status(b,az=-30,seq=1,frame=6);self.status(b,az=45,seq=2,frame=9)
        self.assertTrue(first.sink.close_requested);f.release.set();wait(lambda:b.retiring is first)
        time.sleep(.025);self.assertEqual(f.started,1);self.assertIsNone(b.child)
        first.complete();wait(lambda:b.child is not None)
        self.assertEqual(b.child.az,45);self.assertIs(b.child,f.made[-1]);self.assertFalse(first.calls)
        b.consume_datagram(raw(1));b.consume_datagram(raw(2));self.assertTrue(b.consume_datagram(raw(3)))
        self.assertEqual(len(b.child.calls),1)
    def test_controller_epoch_reset_rejects_late_result(self):
        f=Factory('before');b,r=self.create(f)
        self.status(b,az=-20,epoch=8,seq=0,frame=3);f.release.set();wait(lambda:b.child is not None)
        self.assertEqual(b.active_key,(8,-20.,0.));self.assertTrue(f.made[0].closed.is_set())
        self.assertIs(b.child,f.made[-1]);self.assertEqual(b.counts['candidate_superseded'],1)
    def test_router_ABA_invalidates_old_ticket(self):
        f=Factory('after');b,r=self.create(f,True);first=f.made[0];old_ticket=r.ticket()
        r.select(2);b.set_listen_mode(2);r.select(4);b.set_listen_mode(4)
        new_ticket=r.ticket()
        self.status(b,seq=1,frame=6);f.release.set();wait(lambda:b.child is not None)
        self.assertNotEqual(r.ticket(),old_ticket);self.assertIsNot(b.child,first);self.assertTrue(first.closed.is_set())
        self.assertEqual(r.ticket(),new_ticket,'late old cleanup must not advance the new selection ticket')
        self.assertEqual(b.active_reservation['router_ticket'],r.ticket())
    def test_shutdown_during_factory_never_publishes_or_registers_late_owner(self):
        f=Factory('before');b,r=self.create(f,True);b.close();f.release.set()
        self.assertTrue(b.wait_closed(2));self.assertIsNone(r.pc_owner);self.assertIsNone(r.player);self.assertIsNone(b.child)
    def test_cancel_during_cold_native_prepare_closes_unreturned_sink(self):
        entered=threading.Event();release=threading.Event();native_closed=threading.Event();made=[]
        def native_factory(mode):
            entered.set();release.wait(2);sink=RemoteSink(native_closed,True);made.append(sink);return sink
        r=AudioSourceRouter(sink_factory=native_factory);r.select(4);self.routers.append(r)
        def factory(cfg,**kw):
            proxy=kw['playback_router'];owner=proxy.register_pc_owner()
            if not proxy.prepare_pc(owner):raise PlaybackPreparationCancelled('superseded during native prepare')
            raise AssertionError('cancelled native prepare must never return ready')
        b=BoardLockAdapter({},status_timeout_s=2,child_factory=factory,allow_test_factory=True,playback_router=r);self.bridges.append(b)
        b.set_listen_mode(4);b.consume_datagram(raw());self.status(b);self.assertTrue(entered.wait(1))
        try:
            start=time.perf_counter();self.status(b,mode=0,seq=1,frame=6)
            self.assertLess(time.perf_counter()-start,.05);self.assertEqual(b.status['mode'],'SEARCH')
            release.set();wait(lambda:b.pending is None)
            self.assertTrue(native_closed.is_set());self.assertIsNone(r.player);self.assertIsNone(b.child)
        finally:release.set()
    def test_existing_router_player_is_not_reowned_by_candidate(self):
        native_closed=threading.Event();sink=RemoteSink(native_closed,True)
        r=AudioSourceRouter(sink_factory=lambda mode:sink);r.select(4);self.assertTrue(r.prepare_pc());self.routers.append(r)
        owner=r.pc_owner
        def factory(cfg,**kw):
            kw['playback_router'].register_pc_owner()
            raise AssertionError('busy router registration must not succeed')
        b=BoardLockAdapter({},status_timeout_s=2,child_factory=factory,allow_test_factory=True,playback_router=r);self.bridges.append(b)
        b.set_listen_mode(4);b.consume_datagram(raw());self.status(b)
        wait(lambda:b.counts['candidate_construction_cancelled']>0)
        self.assertIs(r.player,sink);self.assertEqual(r.pc_owner,owner);self.assertFalse(native_closed.is_set())
    def test_constructor_exception_reclaims_only_its_remote_sink(self):
        f=Factory('after',True,True);self.factories.append(f)
        r=AudioSourceRouter(sink_factory=lambda mode:None);r.select(4);self.routers.append(r)
        b=BoardLockAdapter({},status_timeout_s=2,child_factory=f,allow_test_factory=True,playback_router=r);self.bridges.append(b)
        b.set_listen_mode(4);b.consume_datagram(raw());self.status(b)
        wait(lambda:b.sticky_failure)
        self.assertIsNone(r.player);self.assertTrue(f.made[0].closed.is_set());self.assertEqual(b.state,'dsp_creation_failed')
    def test_compact_does_not_evaluate_full_audit(self):
        class AuditCandidate(Candidate):
            def snapshot_full_cached(self):self.full_calls+=1;return dict(self.health(),full='only on request')
        made=[]
        def factory(cfg,*,lock_deg,**kw):c=AuditCandidate(lock_deg,kw);c.full_calls=0;made.append(c);return c
        b=BoardLockAdapter({},status_timeout_s=2,child_factory=factory,allow_test_factory=True);self.bridges.append(b)
        b.consume_datagram(raw());self.status(b);wait(lambda:b.child is not None)
        self.assertEqual(b.snapshot_compact()['backend']['state'],'ready');self.assertEqual(made[0].full_calls,0)
        self.assertEqual(b.snapshot_full_cached()['backend']['full'],'only on request');self.assertEqual(made[0].full_calls,1)
    def test_first_transport_fault_survives_burst_until_healthy_admission(self):
        f=Factory('before');b,r=self.create(f);f.release.set();wait(lambda:b.child is not None)
        self.assertTrue(b.consume_datagram(raw(1)));self.assertFalse(b.consume_datagram(raw(3)))
        first=b.first_transport_failure.copy();self.assertEqual(first['expected_sequence'],2);self.assertEqual(first['sequence'],3)
        self.assertFalse(b.consume_datagram(raw(5)));self.assertEqual(b.first_transport_failure,first)
        self.status(b,seq=1,frame=18);wait(lambda:b.child is not None)
        self.assertTrue(b.consume_datagram(raw(6)));self.assertIsNone(b.first_transport_failure)
        self.assertEqual(b.transport_failure_history[0],first)
    def test_callback_pause_is_diagnostic_not_a_transport_invalidation(self):
        f=Factory('before');b,r=self.create(f);f.release.set();wait(lambda:b.child is not None);child=b.child
        original=child.consume_datagram
        def slow(data):time.sleep(.015);return original(data)
        child.consume_datagram=slow
        self.assertTrue(b.consume_datagram(raw(1)));self.assertIs(b.child,child)
        self.assertGreater(b.first_callback_pause['elapsed_ms'],10);self.assertIsNone(b.first_transport_failure)

if __name__=='__main__':unittest.main()
