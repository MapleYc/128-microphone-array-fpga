"""Playback gain metadata: fake worker boundary, no native/device/process."""
import math,multiprocessing as mp,unittest
from process_adapter import SharedInbox,ProcessRawAdapter,RemotePCSink,_apply_monitor_gain

class FakeRouter:
    def __init__(self):self.calls=[];self.on_apply=None
    def set_monitor_gain(self,g):
        self.calls.append(g)
        if self.on_apply:self.on_apply()

class GainIPC(unittest.TestCase):
    def setUp(self):
        self.box=SharedInbox(mp.get_context('spawn'),16)
        self.actor=ProcessRawAdapter.__new__(ProcessRawAdapter);self.actor.shared=self.box
        self.router=FakeRouter()
    def test_request_is_pending_until_worker_batch_boundary(self):
        r=self.actor.set_monitor_gain(4.)
        self.assertTrue(r['pending']);self.assertIsNone(self.box.health()['playback_gain']['applied'])
        self.assertEqual(self.router.calls,[])
        self.assertTrue(_apply_monitor_gain(self.box,self.router))
        h=self.box.health()['playback_gain'];self.assertFalse(h['pending']);self.assertEqual(h['applied'],4.)
        self.assertEqual(self.router.calls,[4.]);self.assertEqual(self.box.admitted.value,0)
    def test_same_request_is_not_a_new_serial_or_duplicate_apply(self):
        first=self.actor.set_monitor_gain(2.);second=self.actor.set_monitor_gain(2.)
        self.assertEqual(first['requested_serial'],second['requested_serial'])
        self.assertTrue(_apply_monitor_gain(self.box,self.router));self.assertFalse(_apply_monitor_gain(self.box,self.router))
        self.assertEqual(self.router.calls,[2.])
    def test_newer_request_during_apply_remains_pending_for_next_boundary(self):
        self.actor.set_monitor_gain(2.)
        self.router.on_apply=lambda:self.actor.set_monitor_gain(3.)
        self.assertTrue(_apply_monitor_gain(self.box,self.router))
        h=self.box.health()['playback_gain'];self.assertEqual(h['applied'],2.);self.assertEqual(h['requested'],3.);self.assertTrue(h['pending'])
        self.router.on_apply=None;self.assertTrue(_apply_monitor_gain(self.box,self.router))
        self.assertEqual(self.router.calls,[2.,3.]);self.assertFalse(self.box.health()['playback_gain']['pending'])
    def test_stopped_owner_does_not_apply_or_falsely_ack(self):
        self.actor.set_monitor_gain(2.);self.box.request_stop('test stopped')
        self.assertFalse(_apply_monitor_gain(self.box,self.router));self.assertEqual(self.router.calls,[])
        self.assertTrue(self.box.health()['playback_gain']['pending'])
    def test_missing_sink_keeps_request_pending(self):
        self.actor.set_monitor_gain(2.)
        self.assertFalse(_apply_monitor_gain(self.box,None,object()))
        self.assertIsNone(self.box.health()['playback_gain']['applied'])
    def test_remote_sink_forwards_exactly_one_request(self):
        result=RemotePCSink(self.actor).set_monitor_gain(3.)
        self.assertEqual(result['requested'],3.);self.assertEqual(result['requested_serial'],1)
    def test_invalid_gains_do_not_change_control(self):
        for value in (0.,-1.,8.01,float('nan'),float('inf'),-float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.actor.set_monitor_gain(value)
        self.assertEqual(self.box.gain_serial.value,0)
    def test_validation_includes_maximum_eight(self):
        self.actor.set_monitor_gain(8.);self.assertTrue(_apply_monitor_gain(self.box,self.router))
        self.assertEqual(self.router.calls,[8.])

if __name__=='__main__':unittest.main(verbosity=2)
