"""Actual UART packet parser + M128 packets; fake DSP, no device/network calls."""
import copy,json,struct,sys,threading,time,unittest
from pathlib import Path
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from meeting_monitor import Parser,packet
from board_follow import BoardLockAdapter,status_message

def uart(mode=1,az=30,el=0,frame=3,epoch=7,seq=0,live_az=0):
    v=[0]*24;v[0]=mode;v[3]=round(live_az*256)&0xffffffff;v[22]=round(az*256)&0xffffffff;v[23]=round(el*256)&0xffffffff
    return packet(1,seq,frame,epoch,struct.pack('<24I',*v))
def raw(seq=0,epoch=400,first=None,flags=0):
    first=seq*3 if first is None else first
    b=bytearray(struct.pack('>4sBBBBIHHQII',b'M128',1,32,1,128,48000,3,392,seq,epoch,flags))
    for i in range(3):b+=struct.pack('>Q',first+i)+bytes(384)
    return bytes(b)
def wait(fn,t=2):
    until=time.monotonic()+t
    while time.monotonic()<until:
        if fn():return
        time.sleep(.002)
    raise AssertionError('timed out')
class Child:
    all=[]
    def __init__(self,cfg,*,lock_deg,**kw):self.az=lock_deg;self.el=kw.get('lock_elevation_deg',0.);self.calls=[];self.closed=False;self.reject=False;Child.all.append(self)
    def close(self):self.closed=True
    def wait_closed(self,t):return self.closed
    def wait_ready(self,t):return not self.closed
    def consume_datagram(self,b):
        if self.reject:return False
        self.calls.append(b);return True
    def snapshot(self):return dict(state='closed' if self.closed else 'ready',lock=self.az,calls=len(self.calls))
class Tests(unittest.TestCase):
    def setUp(self):
        Child.all=[];self.a=BoardLockAdapter({},child_factory=Child,allow_test_factory=True,status_timeout_s=.2);self.p=Parser()
    def tearDown(self):self.a.close();self.assertTrue(self.a.wait_closed(3))
    def status(self,**kw):
        b=uart(**kw);msgs=[]
        for at in range(0,len(b),7):msgs+=self.p.feed(b[at:at+7])
        for m in msgs:self.a.consume_uart_message(m)
        for d in self.p.take_diagnostics():self.a.consume_uart_diagnostic(d)
    def active(self,az=30,epoch=7):
        self.assertFalse(self.a.consume_datagram(raw()))
        self.status(az=az,epoch=epoch);wait(lambda:self.a.snapshot()['state']=='board_lock_3d_active')
        return Child.all[-1]
    def test_board_lock_uses_locked_az_not_current_strongest(self):
        c=self.active();self.status(az=30,live_az=-60,seq=1,frame=6)
        self.assertTrue(self.a.consume_datagram(raw(1)));self.assertEqual(c.az,30);self.assertEqual(len(Child.all),1)
        self.assertEqual(self.a.snapshot()['controller_epoch'],7);self.assertEqual(self.a.snapshot()['raw_network_epoch'],400)
    def test_no_search_immediately_closes_generation(self):
        c=self.active();self.status(mode=0,seq=1,frame=6);self.assertTrue(c.closed)
        self.assertFalse(self.a.consume_datagram(raw(1)));self.assertEqual(len(c.calls),0)
    def test_nonzero_elevation_rebuilds_real_3d_generation(self):
        c=self.active();self.status(el=1/256,seq=1,frame=6)
        self.assertTrue(c.closed);wait(lambda:len(Child.all)==2)
        self.assertTrue(self.a.snapshot()['supports_nonzero_elevation'])
        self.assertEqual(Child.all[-1].el,1/256)
    def test_new_lock_closes_old_no_overlap(self):
        c=self.active();self.status(az=-30,seq=1,frame=6);self.assertTrue(c.closed)
        wait(lambda:len(Child.all)==2);self.assertEqual(Child.all[-1].az,-30)
    def test_status_timeout_stops_without_udp_callback(self):
        c=self.active();wait(lambda:c.closed,1);self.assertEqual(self.a.snapshot()['state'],'board_status_timeout')
    def test_gap_requires_status_after_gap_and_new_state(self):
        c=self.active();self.assertTrue(self.a.consume_datagram(raw(1)))
        self.assertFalse(self.a.consume_datagram(raw(3)));self.assertTrue(c.closed)
        self.assertFalse(self.a.consume_datagram(raw(4)));self.status(seq=1,frame=15)
        wait(lambda:len(Child.all)==2);self.assertTrue(self.a.consume_datagram(raw(5)))
    def test_epoch_change_does_not_compare_controller_epoch(self):
        c=self.active();self.a.consume_datagram(raw(0,epoch=401,first=100));self.assertTrue(c.closed)
        self.status(seq=1,frame=103,epoch=7);wait(lambda:len(Child.all)==2)
        self.assertTrue(self.a.consume_datagram(raw(1,epoch=401,first=103)))
    def test_invalid_mode_not_clamped_to_locked(self):
        self.a.consume_datagram(raw());self.status(mode=2)
        self.assertEqual(self.a.snapshot()['state'],'invalid_uart_status');self.assertFalse(Child.all)
    def test_future_source_frame_gate_no_backfill(self):
        self.a.consume_datagram(raw());self.status(frame=30);wait(lambda:bool(Child.all))
        self.assertFalse(self.a.consume_datagram(raw(1)));self.assertEqual(self.a.snapshot()['counts']['packets_before_lock_source_frame'],1)
    def test_crc_and_sequence_gap_revoke_lock(self):
        c=self.active();bad=bytearray(uart(seq=1,frame=6));bad[-1]^=1;self.p.feed(bad)
        for d in self.p.take_diagnostics():self.a.consume_uart_diagnostic(d)
        self.assertTrue(c.closed);self.assertEqual(self.a.snapshot()['state'],'uart_integrity_fault')
    def test_duplicate_reorder_fatal_and_unknown_flags(self):
        c=self.active();self.assertTrue(self.a.consume_datagram(raw(1)));self.assertFalse(self.a.consume_datagram(raw(1)));self.assertTrue(c.closed)
        self.assertFalse(self.a.consume_datagram(raw(2,flags=128)));self.assertEqual(self.a.snapshot()['state'],'raw_malformed')
    def test_inbox_failure_is_visible_sticky_not_silent_drop(self):
        c=self.active();c.reject=True;self.assertFalse(self.a.consume_datagram(raw(1)))
        self.assertTrue(self.a.snapshot()['sticky_failure']);self.status(seq=1,frame=6);self.assertIsNone(self.a.child)
    def test_controller_frame_regression_isolation(self):
        c=self.active();self.status(seq=1,frame=2);self.assertTrue(c.closed)
        self.assertEqual(self.a.snapshot()['state'],'uart_source_frame_regression')
    def test_status_sequence_duplicate_not_reused(self):
        c=self.active();self.status(seq=0,frame=6);self.assertTrue(c.closed)
        self.assertEqual(self.a.snapshot()['state'],'uart_status_sequence_regression')
    def test_raw_fatal_flags_stop_output(self):
        c=self.active();self.assertFalse(self.a.consume_datagram(raw(1,flags=1)));self.assertTrue(c.closed)
        self.assertEqual(self.a.snapshot()['state'],'raw_fatal_flags')

if __name__=='__main__':unittest.main()
