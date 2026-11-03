"""Stop gate ordering under a deliberately blocked diagnostic audit."""
import threading,unittest
from board_follow import BoardLockAdapter
from test_board_follow import Child,raw,uart,Parser,wait

class SlowClosedAudit(Child):
    entered=threading.Event();release=threading.Event()
    def snapshot(self):
        if self.closed:
            self.entered.set();self.release.wait(2)
        return super().snapshot()

class StopLatch(unittest.TestCase):
    def test_no_returns_and_closes_child_while_retirement_audit_is_blocked(self):
        Child.all=[];SlowClosedAudit.entered.clear();SlowClosedAudit.release.clear()
        b=BoardLockAdapter({},child_factory=SlowClosedAudit,allow_test_factory=True,status_timeout_s=2)
        p=Parser();thread=None
        try:
            b.consume_datagram(raw())
            for m in p.feed(uart()):b.consume_uart_message(m)
            wait(lambda:b.health()['state']=='board_lock_3d_active');child=Child.all[-1]
            def no():
                for m in p.feed(uart(mode=0,seq=1,frame=6)):b.consume_uart_message(m)
            thread=threading.Thread(target=no);thread.start()
            self.assertTrue(SlowClosedAudit.entered.wait(1))
            self.assertTrue(child.closed,'stop must latch before the full audit is available')
            thread.join(.25)
            self.assertFalse(thread.is_alive(),'NO callback must not wait for the retirement diagnostic')
            for m in p.feed(uart(mode=0,seq=2,frame=9)):b.consume_uart_message(m)
            self.assertEqual(b.status['source_frame'],9,'heartbeat must stay live while retirement audit is blocked')
        finally:
            SlowClosedAudit.release.set()
            if thread is not None:thread.join(2)
            b.close();self.assertTrue(b.wait_closed(3))

if __name__=='__main__':unittest.main()
