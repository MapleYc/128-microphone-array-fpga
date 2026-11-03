"""Explicit preparation, source ownership and compact metadata; no devices."""
import threading,time,unittest
from audio_source_router import AudioSourceRouter

class DeadThread:
    def is_alive(self):return False

class Sink:
    def __init__(self,mode):
        self.mode=mode;self.closed=False;self.device_closed=False;self.thread=DeadThread()
        self.error=None;self.samples=0;self.full_calls=0;self.requested=None
    def feed(self,data):
        if self.error:raise RuntimeError(self.error)
        if self.closed:raise RuntimeError('closed sink feed')
        self.samples+=len(data)//2
    def request_close(self,reason):self.closed=True;self.requested=reason
    def close(self):self.request_close('normal close');self.device_closed=True;return True
    def snapshot_compact(self):
        return dict(fed_samples=self.samples,submitted_samples=self.samples,completed_samples=self.samples,
            queued_samples=0,dropped_samples=0,error=self.error,closed=self.closed,device_closed=self.device_closed,
            native_closed_ack=self.device_closed,completion_age_seconds=0.,rate=16000,strict_no_drop=True)
    def snapshot(self):
        self.full_calls+=1;return dict(self.snapshot_compact(),events=['large-retained-audit'],feed_trace=['large'])

class Tests(unittest.TestCase):
    def test_select_prepares_once_and_feed_never_calls_factory(self):
        made=[]
        def factory(mode):s=Sink(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory)
        try:
            r.select(2);self.assertEqual(len(made),1);self.assertEqual(r.mode,2)
            r.feed_board(2,b'\1\0'*128,r.ticket());self.assertEqual(made[0].samples,128)
            r.select(2);self.assertEqual(len(made),1)
            r.select(3);self.assertTrue(made[0].device_closed);self.assertEqual(len(made),2)
        finally:r.close()
    def test_cold_open_is_outside_router_lock_and_source_not_admitted(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def factory(mode):entered.set();release.wait(2);return Sink(mode)
        r=AudioSourceRouter(sink_factory=factory)
        def select():
            try:r.select(2)
            except Exception as e:errors.append(e)
        t=threading.Thread(target=select);t.start()
        try:
            self.assertTrue(entered.wait(1));begin=time.perf_counter();snap=r.snapshot_compact()
            self.assertLess(time.perf_counter()-begin,.1);self.assertTrue(snap['board_preparing']);self.assertEqual(snap['mode'],0)
            self.assertFalse(r.feed_board(2,b'\0\0'*128,r.ticket()))
            release.set();t.join(2);self.assertFalse(t.is_alive());self.assertFalse(errors);self.assertEqual(r.mode,2)
        finally:release.set();t.join(2);r.close()
    def test_superseded_board_open_closes_orphan_before_new_factory(self):
        entered=threading.Event();release=threading.Event();made=[];errors=[]
        def factory(mode):
            if mode==2:entered.set();release.wait(2)
            if mode==3:self.assertTrue(made[0].device_closed)
            s=Sink(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory)
        def select(mode):
            try:r.select(mode)
            except Exception as e:errors.append(e)
        a=threading.Thread(target=lambda:select(2));a.start();self.assertTrue(entered.wait(1));old_ticket=r.ticket()
        b=threading.Thread(target=lambda:select(3));b.start()
        try:
            end=time.monotonic()+1
            while r.ticket()==old_ticket and time.monotonic()<end:time.sleep(.001)
            release.set();a.join(2);b.join(2)
            self.assertFalse(errors);self.assertEqual(r.mode,3);self.assertEqual([x.mode for x in made],[2,3])
            self.assertFalse(r.feed_board(2,b'\0\0'*128,old_ticket))
        finally:release.set();a.join(2);b.join(2);r.close()
    def test_fail_board_seals_only_current_ticket_and_explicit_retry_prepares(self):
        made=[]
        def factory(mode):s=Sink(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory)
        try:
            r.select(2);ticket=r.ticket();made[0].error='injected overflow; no discard'
            with self.assertRaisesRegex(RuntimeError,'overflow'):r.feed_board(2,b'\0\0'*128,ticket)
            self.assertTrue(r.fail_board(2,'injected overflow',ticket));self.assertEqual(r.mode,0)
            self.assertTrue(made[0].closed);self.assertFalse(made[0].device_closed)
            r.select(2);self.assertTrue(made[0].device_closed);self.assertEqual(len(made),2)
            self.assertFalse(r.fail_board(2,'late old failure',ticket));self.assertFalse(made[1].closed)
            self.assertTrue(r.feed_board(2,b'\0\0'*128,r.ticket()))
        finally:r.close()
    def test_native_close_ack_failure_prevents_new_source_even_when_thread_dead(self):
        class Uncertain(Sink):
            def close(self):self.request_close('close failed');return False
        made=[]
        def factory(mode):s=Uncertain(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory);r.select(2)
        with self.assertRaisesRegex(RuntimeError,'shutdown is pending'):r.select(3)
        self.assertEqual(len(made),1);self.assertEqual(r.mode,0)
        # Test-only acknowledgement releases the fixture, never inferred from
        # the fake thread being dead.
        made[0].device_closed=True;r.close()
    def test_compact_poll_never_reads_full_native_or_retired_arrays(self):
        s=Sink(2);r=AudioSourceRouter(sink_factory=lambda mode:s)
        try:
            r.select(2)
            r.last_player=dict(reason='old',owner=9,epoch=0,native=dict(events=['huge'],feed_trace=['huge'],fed_samples=17))
            snap=r.snapshot_compact();self.assertEqual(s.full_calls,0)
            self.assertNotIn('events',snap['player']);self.assertNotIn('events',snap['last_player']['native'])
            self.assertEqual(snap['last_player']['native']['fed_samples'],17)
            self.assertIn('events',r.snapshot()['player']['backend_snapshot'])
        finally:r.close()
    def test_unprepared_feed_cannot_open_a_native_device(self):
        calls=[];r=AudioSourceRouter(sink_factory=lambda mode:calls.append(mode))
        r.mode=r.requested=2
        try:
            with self.assertRaisesRegex(RuntimeError,'not prepared'):r.feed_board(2,b'\0\0'*128,r.ticket())
            self.assertFalse(calls)
        finally:r.mode=0;r.requested=0;r.close()

if __name__=='__main__':unittest.main()
