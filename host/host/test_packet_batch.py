"""Independent packet-credit/order regressions; no DSP or device access."""
import collections,hashlib,importlib.util,queue,struct,sys,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import legacy_adapter as new

HERE=Path(__file__).resolve().parent
BASE=HERE/'regression_baselines'/'packet_batch'
spec=importlib.util.spec_from_file_location('packet_batch_old_legacy',BASE/'legacy_adapter.py')
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
HEADER=struct.Struct('>4sBBBBIHHQII')

def packet(seq):
    value=((seq*37)%(1<<20))-(1<<19);pcm=value.to_bytes(3,'little',signed=True)+bytes(381)
    return HEADER.pack(b'M128',1,32,1,128,48000,3,392,seq,1,0)+b''.join(struct.pack('>Q',seq*3+j)+pcm for j in range(3))

def wait(fn,timeout=3.):
    end=time.perf_counter()+timeout
    while time.perf_counter()<end:
        if fn():return
        time.sleep(.001)
    raise AssertionError('timeout')

class Backend:
    def __init__(self,*a,**kw):
        self.seen=[];self.events=[];self.gain=1.;self.state=0.;self.digest=hashlib.sha256();self.entered=threading.Event();self.release=threading.Event();self.block_first=False;self.raise_first=False;self.closed=False
    def consume(self,p):
        data=p.raw if hasattr(p,'raw') else p;seq=struct.unpack_from('>Q',data,16)[0]
        if not self.seen:
            self.entered.set()
            if self.block_first and not self.release.wait(3):raise AssertionError('test consume release timed out')
            if self.raise_first:raise RuntimeError('TEST_ONLY injected consume failure')
        self.seen.append(seq);self.events.append(('consume',seq))
        sample=int.from_bytes(data[40:43],'little',signed=True)/(1<<23)
        self.state=.93*self.state+self.gain*sample;self.digest.update(struct.pack('<Qd',seq,self.state))
    def fault(self,reason):self.events.append(('fault',reason));self.state=0.
    def idle(self):pass
    def snapshot(self):return dict(seen=list(self.seen),events=list(self.events),state=self.state,digest=self.digest.hexdigest())
    def finish(self):return self.snapshot()
    def close(self):self.closed=True

class PacketBatch(unittest.TestCase):
    def make(self,cls=new.RawPreviewAdapter,capacity=1024,**settings):
        made=[]
        def factory(*a,**kw):
            b=Backend(*a,**kw)
            for k,v in settings.items():setattr(b,k,v)
            made.append(b);return b
        a=cls({},lock_deg=0,capacity=capacity,backend_factory=factory,allow_test_factory=True)
        self.assertTrue(a.wait_ready(2));wait(lambda:bool(made))
        self.addCleanup(lambda:(made[0].release.set(),a.close(),self.assertTrue(a.wait_closed(4))))
        return a,made[0]
    def gated_queue(self):
        entered=threading.Event();release=threading.Event();original=new._PacketInbox.get_batch
        def gated(q,*a,**kw):
            entered.set()
            if not release.wait(3):raise AssertionError('test dequeue release timed out')
            return original(q,*a,**kw)
        self.addCleanup(release.set)
        return entered,release,patch.object(new._PacketInbox,'get_batch',gated)
    def test_bulk_fifo_timeout_and_limit(self):
        q=new._PacketInbox(40)
        for n in range(35):q.put_nowait(n)
        self.assertEqual(q.get_batch(32,0),list(range(32)));self.assertEqual(q.get_batch(32,0),[32,33,34])
        with self.assertRaises(queue.Empty):q.get_batch(32,0)
        for limit in (0,33):
            with self.assertRaises(ValueError):q.get_batch(limit,0)
    def test_held_batch_keeps_effective_capacity_at_1024(self):
        entered,release,gate=self.gated_queue()
        with gate:
            a,b=self.make(block_first=True);self.assertTrue(entered.wait(1))
            for n in range(1024):self.assertTrue(a.consume_datagram(packet(n)))
            self.assertEqual(a.health()['admitted_packets'],1024)
            release.set();self.assertTrue(b.entered.wait(1));h=a.health()
            self.assertEqual(h['consumer_batch_held'],32);self.assertEqual(h['inbox_current'],992)
            self.assertEqual(h['inbox_current']+h['consumer_batch_held'],1024)
            self.assertFalse(a.consume_datagram(packet(1024)),'batch dequeue must not grant 32 hidden credits')
            self.assertEqual(a.snapshot()['counts']['inbox_overflow_packets'],1)
            a.close();b.release.set();self.assertTrue(a.wait_closed(3))
            s=a.snapshot();self.assertEqual(s['admitted_packets'],0);self.assertEqual(s['consumer_batch_held'],0)
            self.assertEqual(s['counts']['enqueued_packets'],s['counts']['processed_packets']+s['counts'].get('queued_old_generation_discarded',0)+s['counts']['queued_discarded_on_close'])
    def test_fixed_input_control_digest_matches_baseline(self):
        results=[]
        for cls in (old.RawPreviewAdapter,new.RawPreviewAdapter):
            a,b=self.make(cls)
            for phase,gain in enumerate((1.,.5,1.2)):
                b.gain=gain
                for n in range(phase*128,(phase+1)*128):self.assertTrue(a.consume_datagram(packet(n)))
                wait(lambda:a.health()['processed_packets']==(phase+1)*128)
                self.assertEqual(b.seen,list(range((phase+1)*128)))
            a.close();self.assertTrue(a.wait_closed(3));results.append(b.snapshot())
        self.assertEqual(results[0],results[1])
    def test_length_fault_does_not_splice_held_old_generation(self):
        entered,release,gate=self.gated_queue()
        with gate:
            a,b=self.make(capacity=64,block_first=True);self.assertTrue(entered.wait(1))
            for n in range(10):self.assertTrue(a.consume_datagram(packet(n)))
            release.set();self.assertTrue(b.entered.wait(1))
            self.assertFalse(a.consume_datagram(b'M128'+bytes(1200)))
            for n in range(100,104):self.assertTrue(a.consume_datagram(packet(n)))
            b.release.set();wait(lambda:a.health()['processed_packets']==5)
            self.assertEqual(b.seen,[0,100,101,102,103]);self.assertEqual(b.events[1][0],'fault')
            self.assertEqual(a.snapshot()['counts']['queued_old_generation_discarded'],9)
            self.assertEqual(a.snapshot()['counts']['backend_fault_resets'],1)
            a.close();self.assertTrue(a.wait_closed(3));self.assertEqual(a.health()['admitted_packets'],0)
    def test_stop_accounts_for_held_and_queued_packets(self):
        entered,release,gate=self.gated_queue()
        with gate:
            a,b=self.make(capacity=64,block_first=True);self.assertTrue(entered.wait(1))
            for n in range(48):self.assertTrue(a.consume_datagram(packet(n)))
            release.set();self.assertTrue(b.entered.wait(1));self.assertEqual(a.health()['consumer_batch_held'],32)
            a.close();b.release.set();self.assertTrue(a.wait_closed(3));s=a.snapshot()
            self.assertEqual(s['counts']['enqueued_packets'],48);self.assertEqual(s['counts']['processed_packets'],1)
            self.assertEqual(s['counts']['queued_discarded_on_close'],47)
            self.assertEqual(s['admitted_packets'],0);self.assertEqual(s['inbox_current'],0)
            self.assertFalse(a.consume_datagram(packet(99)));self.assertEqual(a.health()['admitted_packets'],0)
    def test_consume_error_keeps_explicit_packet_ledger(self):
        entered,release,gate=self.gated_queue()
        with gate:
            a,b=self.make(capacity=64,raise_first=True);self.assertTrue(entered.wait(1))
            for n in range(48):self.assertTrue(a.consume_datagram(packet(n)))
            release.set();self.assertTrue(a.wait_closed(3));s=a.snapshot();c=s['counts']
            self.assertEqual(s['state'],'failed');self.assertIn('TEST_ONLY injected consume failure',s['error'])
            self.assertEqual(c['enqueued_packets'],48);self.assertEqual(c.get('processed_packets',0),0)
            self.assertEqual(c['inflight_aborted_on_error'],32);self.assertEqual(c['queued_discarded_on_close'],16)
            self.assertEqual(c['enqueued_packets'],c['inflight_aborted_on_error']+c['queued_discarded_on_close'])
            self.assertEqual(s['admitted_packets'],0);self.assertEqual(s['consumer_batch_held'],0);self.assertEqual(s['inbox_current'],0)
    def test_per_packet_timing_is_retained_for_all_processed(self):
        a,b=self.make()
        for n in range(96):self.assertTrue(a.consume_datagram(packet(n)))
        wait(lambda:a.health()['processed_packets']==96)
        s=a.snapshot();self.assertEqual(s['queue_age_ms']['count'],96);self.assertEqual(s['consume_ms']['count'],96)
        self.assertIn('all processed packets',s['timing_scope']);self.assertGreaterEqual(s['max_batch_ms'],0)
    def test_enqueue_close_race_cannot_publish_after_done(self):
        a,b=self.make();entered=threading.Event();release=threading.Event();results=[];errors=[]
        original=a._q.put_nowait
        def gated_put(item):
            entered.set()
            if not release.wait(3):raise AssertionError('test publish release timed out')
            return original(item)
        a._q.put_nowait=gated_put
        def produce():
            try:results.append(a.consume_datagram(packet(0)))
            except Exception as exc:errors.append(exc)
        def close():
            try:a.close_with_reason('test_publish_close_race')
            except Exception as exc:errors.append(exc)
        producer=threading.Thread(target=produce);closer=threading.Thread(target=close)
        producer.start()
        try:
            self.assertTrue(entered.wait(1));closer.start();wait(lambda:a._stop.is_set())
            self.assertFalse(a._done.is_set(),'owner cannot finish before the reserved publish is accounted for')
            release.set();producer.join(3);closer.join(3);self.assertFalse(producer.is_alive());self.assertFalse(closer.is_alive());self.assertTrue(a.wait_closed(3));self.assertFalse(errors)
            self.assertEqual(results,[True]);s=a.snapshot();c=s['counts']
            self.assertEqual(c['enqueued_packets'],1);self.assertEqual(c.get('processed_packets',0),0);self.assertEqual(c['queued_discarded_on_close'],1)
            self.assertEqual(c['enqueued_packets'],c.get('processed_packets',0)+c.get('queued_old_generation_discarded',0)+c.get('queued_discarded_on_close',0)+c.get('inflight_aborted_on_error',0))
            self.assertEqual(s['admitted_packets'],0);self.assertEqual(s['consumer_batch_held'],0);self.assertEqual(s['inbox_current'],0);self.assertEqual(b.seen,[])
        finally:
            release.set();producer.join(3)
            if closer.ident is not None:closer.join(3)
            a._q.put_nowait=original

if __name__=='__main__':unittest.main(verbosity=2)
