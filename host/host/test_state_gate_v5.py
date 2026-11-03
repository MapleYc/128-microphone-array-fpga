"""State gate V5: no devices/network/DSP workload, explicit spawn test actor."""
import multiprocessing as mp,queue,threading,time,unittest
from process_adapter import (SharedInbox,ProcessRawAdapter,_worker_main,CI,
                            STARTING,READY,STOPPING,CLOSED,FAILED,CANCELLED)

PACKET=b'M128'+bytes(1204)

class ForbiddenEvent:
    def is_set(self):raise AssertionError('scalar state gate must not call Event.is_set')
    def wait(self,timeout=None):raise AssertionError('zero-time state gate must not call Event.wait')

class ObservedStop:
    def __init__(self,box):self.box=box;self.original=box.stop;self.sets=[]
    def set(self):
        self.sets.append((self.box.state.value,self.box.lock._semlock._is_mine()))
        self.original.set()
    def is_set(self):return self.original.is_set()
    def wait(self,timeout=None):return self.original.wait(timeout)

class StateGateV5(unittest.TestCase):
    def box(self,state=STARTING):
        box=SharedInbox(mp.get_context('spawn'),16)
        with box.lock:box.state.value=state
        return box
    def actor_stub(self,box):
        actor=object.__new__(ProcessRawAdapter);actor.shared=box;return actor
    def test_enqueue_state_truth_table_including_unknown(self):
        for state in (STARTING,READY,STOPPING,CLOSED,FAILED,CANCELLED,99,-1):
            with self.subTest(state=state):
                box=self.box(state);box.ready.set()
                self.assertEqual(box.enqueue(PACKET),state==READY)
                self.assertEqual(box.admitted.value,int(state==READY))
                self.assertEqual(box.counts[CI['startup_packets_not_queued']],int(state==STARTING))
                self.assertEqual(box.counts[CI['rejected_after_stop_or_error']],int(state not in (STARTING,READY)))
    def test_wait_ready_zero_state_truth_table_including_unknown(self):
        for state in (STARTING,READY,STOPPING,CLOSED,FAILED,CANCELLED,99,-1):
            with self.subTest(state=state):
                box=self.box(state);box.ready.set()
                self.assertEqual(self.actor_stub(box).wait_ready(0),state==READY)
    def test_enqueue_and_wait_zero_do_not_query_events(self):
        for state in (STARTING,READY,STOPPING,CLOSED,FAILED,CANCELLED,99):
            with self.subTest(state=state):
                box=self.box(state);box.ready=ForbiddenEvent();box.stop=ForbiddenEvent()
                self.assertEqual(box.enqueue(PACKET),state==READY)
                self.assertEqual(self.actor_stub(box).wait_ready(0),state==READY)
    def test_close_linearizes_before_wait_zero_via_same_lock(self):
        box=self.box(READY);box.ready.set();actor=self.actor_stub(box)
        began=threading.Event();done=threading.Event();result=[]
        def poll():began.set();result.append(actor.wait_ready(0));done.set()
        with box.lock:
            t=threading.Thread(target=poll);t.start();self.assertTrue(began.wait(.5))
            self.assertFalse(done.wait(.02),'wait_ready(0) skipped shared lock')
            box.request_stop('close wins same-lock ordering')
        self.assertTrue(done.wait(.5));t.join(.5);self.assertEqual(result,[False])
    def test_close_linearizes_before_enqueue_without_credit_leak(self):
        box=self.box(READY);box.ready.set();began=threading.Event();done=threading.Event();result=[]
        def put():began.set();result.append(box.enqueue(PACKET));done.set()
        with box.lock:
            t=threading.Thread(target=put);t.start();self.assertTrue(began.wait(.5))
            self.assertFalse(done.wait(.02),'enqueue skipped shared lock')
            box.request_stop('close before reserve')
        self.assertTrue(done.wait(.5));t.join(.5)
        self.assertEqual(result,[False]);self.assertEqual(box.admitted.value,0)
        self.assertEqual(box.counts[CI['enqueued_packets']],0)
    def test_enqueue_before_close_remains_explicitly_accounted(self):
        box=self.box(READY);box.ready.set();self.assertTrue(box.enqueue(PACKET))
        box.request_stop('after reserve');self.assertFalse(box.enqueue(PACKET))
        self.assertEqual(box.admitted.value,1)
        box.discard();self.assertEqual(box.admitted.value,0)
        self.assertEqual(box.counts[CI['queued_discarded_on_close']],1)
    def test_positive_timeout_still_waits_for_event_and_stop_interrupts(self):
        box=self.box();actor=self.actor_stub(box);started=time.monotonic()
        self.assertFalse(actor.wait_ready(.025));self.assertGreaterEqual(time.monotonic()-started,.015)
        result=[];t=threading.Thread(target=lambda:result.append(actor.wait_ready(.5)));t.start()
        with box.lock:box.state.value=READY;box.ready.set()
        t.join(1);self.assertFalse(t.is_alive());self.assertEqual(result,[True])
        box.request_stop('stopped');self.assertFalse(actor.wait_ready(.1))
    def test_worker_finally_publishes_nonready_under_lock_before_stop_set(self):
        box=self.box();observed=ObservedStop(box);box.stop=observed
        telemetry=queue.Queue(8)
        t=threading.Thread(target=_worker_main,args=(box,telemetry,{},0.,0.,False,1.,1,{}))
        t.start()
        try:
            self.assertTrue(box.ready.wait(1))
            # Inject Event-only termination to exercise the final guard itself;
            # production request_stop publishes STOPPING under lock beforehand.
            observed.original.set();box.wake.set()
            t.join(2);self.assertFalse(t.is_alive())
            self.assertTrue(observed.sets)
            self.assertTrue(all(state not in (STARTING,READY) and held for state,held in observed.sets),observed.sets)
            self.assertEqual(box.state.value,CLOSED)
            self.assertTrue(box.done.is_set());self.assertTrue(box.native_closed.is_set())
        finally:
            if t.is_alive():box.request_stop('test cleanup');t.join(2)
    def test_worker_keeps_perpacket_stop_gate_inside_held_batch(self):
        from unittest.mock import patch
        from process_test_support import TestBackend
        class StopAfterFirst(TestBackend):
            def consume(self,data):
                super().consume(data)
                self.shared.request_stop('first consume requests cancel')
        box=self.box();telemetry=queue.Queue(8)
        with patch('process_test_support.TestBackend',StopAfterFirst):
            t=threading.Thread(target=_worker_main,args=(box,telemetry,{},0.,0.,False,1.,1,{}));t.start()
            try:
                self.assertTrue(box.ready.wait(1))
                # Reserve the entire held batch before its owner may take it.
                with box.lock:
                    for _ in range(16):self.assertTrue(box.enqueue(PACKET))
                t.join(2);self.assertFalse(t.is_alive())
                self.assertEqual(box.counts[CI['processed_packets']],1)
                self.assertEqual(box.counts[CI['queued_discarded_on_close']],15)
                self.assertEqual(box.admitted.value,0)
                self.assertEqual(box.state.value,CLOSED)
            finally:
                if t.is_alive():box.request_stop('test cleanup');t.join(2)
    def test_cold_cancel_spawn_never_becomes_ready_and_keeps_native_ack(self):
        actor=ProcessRawAdapter({},lock_deg=0,enable_playback=True,
             test_worker_spec=dict(startup_ms=100),allow_test_worker=True)
        try:
            actor.close_with_reason('cancel cold before startup')
            self.assertFalse(actor.wait_ready(0));self.assertFalse(actor.wait_ready(.2))
            self.assertTrue(actor.wait_closed(5));h=actor.health()
            self.assertEqual(h['state'],'cancelled');self.assertTrue(h['native_closed_ack'])
            self.assertEqual(h['admitted_packets'],0)
            self.assertIsNone(actor.snapshot()['backend']['output']['player']['native_open_ns'])
        finally:actor.close();self.assertTrue(actor.wait_closed(5))
    def test_worker_failure_stays_failed_through_finally(self):
        actor=ProcessRawAdapter({},lock_deg=0,
             test_worker_spec=dict(raise_at=1),allow_test_worker=True)
        try:
            self.assertTrue(actor.wait_ready(5));self.assertTrue(actor.consume_datagram(PACKET))
            self.assertTrue(actor.wait_closed(5));h=actor.health()
            self.assertEqual(h['state'],'failed');self.assertFalse(actor.wait_ready(0))
            self.assertIn('injected real backend failure',h['error'])
            self.assertTrue(h['native_closed_ack']);self.assertEqual(h['admitted_packets'],0)
        finally:actor.close();self.assertTrue(actor.wait_closed(5))

if __name__=='__main__':unittest.main(verbosity=2)
