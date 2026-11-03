"""No devices: full failure audit is final, generation-bound and retained."""
import itertools,threading,unittest
from board_follow import BoardLockAdapter
from test_board_follow import raw,uart,Parser,wait

class Child:
    all=[];ids=itertools.count(1000)
    def __init__(self,cfg,**kw):
        self.generation=next(self.ids);self.failed=False;self.close_requested=False
        self.native_closed=threading.Event();self.done=threading.Event();self.auto_done=True;self.full_calls=0;Child.all.append(self)
    def health(self):return dict(state='failed' if self.failed else 'closed' if self.done.is_set() else 'ready',error='fake supply error' if self.failed else None,generation=self.generation)
    def snapshot_compact(self):return self.health()
    def snapshot(self):return self.snapshot_compact()
    def snapshot_full_cached(self):
        self.full_calls+=1;return dict(self.health(),full_marker=self.generation,final=self.done.is_set())
    def wait_ready(self,t):return not self.close_requested
    def wait_closed(self,t):return self.done.wait(t)
    def close_with_reason(self,reason):
        self.close_requested=True;self.native_closed.set()
        if self.auto_done:self.done.set()
    def close(self):self.close_with_reason('close')
    def consume_datagram(self,data):return not self.close_requested

class Forensics(unittest.TestCase):
    def setUp(self):
        Child.all=[];self.seq=0
        self.b=BoardLockAdapter({},status_timeout_s=2,child_factory=Child,allow_test_factory=True)
        self.b.consume_datagram(raw());self.status(frame=3);wait(lambda:self.b.child is not None)
    def tearDown(self):
        for child in Child.all:child.done.set()
        self.b.close();self.assertTrue(self.b.wait_closed(2))
    def status(self,**kw):
        kw.setdefault('seq',self.seq);self.seq+=1
        for message in Parser().feed(uart(**kw)):self.b.consume_uart_message(message)
    def fail(self,delayed=False):
        c=self.b.child;c.auto_done=not delayed;c.failed=True
        wait(lambda:self.b.sticky_failure);wait(lambda:c.native_closed.is_set());return c
    def test_native_close_is_not_final_process_audit(self):
        c=self.fail(True)
        snap=self.b.snapshot_full_cached()
        self.assertTrue(snap['fault_forensic_pending']);self.assertFalse(snap['fault_forensic_ready'])
        self.assertEqual(snap['backend_owner'],'failed_pending');self.assertEqual(c.full_calls,0)
        c.done.set();wait(lambda:self.b.snapshot_compact()['fault_forensic_ready'])
        first=self.b.snapshot_full_cached()
        self.assertEqual(first['backend']['full_marker'],c.generation);self.assertTrue(first['backend']['final'])
        self.assertEqual(c.full_calls,1)
        for i in range(5):self.b.snapshot_compact();self.b.snapshot_full_cached()
        self.assertEqual(c.full_calls,1,'full audit copied once after real close barrier')
    def test_new_active_and_normal_retirement_cannot_replace_failed_full(self):
        failed=self.fail();wait(lambda:self.b.snapshot_compact()['fault_forensic_ready'])
        self.b.set_listen_mode(4);self.status(frame=9);wait(lambda:self.b.child is not None)
        active=self.b.child;self.assertNotEqual(active.generation,failed.generation)
        compact=self.b.snapshot_compact();full=self.b.snapshot_full_cached()
        self.assertEqual(compact['backend']['generation'],active.generation)
        self.assertEqual(full['backend']['full_marker'],failed.generation)
        self.assertEqual(full['forensic_child_generation'],failed.generation);self.assertEqual(full['backend_owner'],'failed_cache')
        self.status(mode=0,frame=12);wait(lambda:self.b.retiring is None)
        final=self.b.snapshot_full_cached()
        self.assertEqual(final['backend']['full_marker'],failed.generation)
        self.assertEqual(failed.full_calls,1)

if __name__=='__main__':unittest.main()
