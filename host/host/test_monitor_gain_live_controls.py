"""No devices: real router/Session gain selection and applied-value contract."""
import io,json,math,threading,types,unittest
import numpy as np
from audio_source_router import AudioSourceRouter
import launcher

class Thread:
    def is_alive(self):return False
class GainSink:
    def __init__(self):self.gain=1.;self.closed=False;self.thread=Thread();self.pcm=None
    def feed(self,a):self.pcm=np.rint(np.clip(a*self.gain,-1,1)*32767).astype('<i2')
    def close(self):self.closed=True
    def snapshot(self):return dict(monitor_gain=self.gain,closed=self.closed,completed_samples=0,error=None)

class Tests(unittest.TestCase):
    def test_current_native_gain_is_real_and_applied_once(self):
        sink=GainSink();r=AudioSourceRouter(sink_factory=lambda mode:sink)
        try:
            r.select(4);r.set_monitor_gain(2);r.prepare_pc()
            self.assertEqual(sink.gain,2)
            r.feed_pc(np.full(512,.1),r.ticket())
            self.assertTrue(np.all(sink.pcm==6553))
            r.set_monitor_gain(4);r.feed_pc(np.full(512,.1),r.ticket())
            self.assertTrue(np.all(sink.pcm==13107))
            self.assertEqual(r.snapshot()['player']['monitor_gain'],4)
        finally:r.close()
    def test_invalid_gain_never_updates_the_sink(self):
        sink=GainSink();r=AudioSourceRouter(sink_factory=lambda mode:sink)
        try:
            r.select(4);r.prepare_pc()
            for bad in (0,-1,8.01,float('nan'),float('inf')):
                with self.assertRaises(ValueError):r.set_monitor_gain(bad)
            self.assertEqual(sink.gain,1.)
        finally:r.close()
    def test_cold_open_change_is_not_lost(self):
        opening=threading.Event();release=threading.Event();sink=GainSink()
        def factory(mode):opening.set();release.wait(1);return sink
        r=AudioSourceRouter(sink_factory=factory);r.select(4)
        t=threading.Thread(target=r.prepare_pc);t.start();self.assertTrue(opening.wait(1))
        r.set_monitor_gain(3);release.set();t.join(2)
        try:self.assertFalse(t.is_alive());self.assertEqual(sink.gain,3.)
        finally:r.close()
    def session(self):
        m=launcher.load_monitor();s=m.Session.__new__(m.Session)
        s.audio_mode=4;s.raw_preview=None;s.audio_router=AudioSourceRouter(sink_factory=lambda mode:GainSink())
        s.audio_router.select(4);s.listen_gain=m.ListenGain(12);s.pc_monitor_gain_db=12.
        s.audio_log=io.StringIO();s.audio_levels={2:m.PcmLevel(),3:m.PcmLevel()};s.output_level=m.PcmLevel()
        s.board_audio_processing={};s.player=None
        return s
    def test_session_gain_pending_and_native_applied_are_distinct(self):
        s=self.session()
        try:
            s.set_listen_gain(18)
            before=s.audio_snapshot()['gain'];self.assertTrue(before['pending']);self.assertIsNone(before['applied_gain_db'])
            self.assertEqual(s.listen_gain.snapshot()['gain_db'],12.)
            s.audio_router.prepare_pc()
            after=s.audio_snapshot()['gain'];self.assertFalse(after['pending']);self.assertAlmostEqual(after['applied_gain_db'],18.)
            self.assertEqual(after['max_db'],18)
            with self.assertRaises(ValueError):s.set_listen_gain(24)
        finally:s.audio_router.close()
    def test_source_gain_preferences_remain_separate(self):
        s=self.session()
        try:
            s.set_listen_gain(6);s.audio_mode=2;s.set_listen_gain(36)
            self.assertEqual(s.pc_monitor_gain_db,6)
            self.assertEqual(s.listen_gain.snapshot()['gain_db'],36)
            s.audio_mode=4;self.assertEqual(s.audio_snapshot()['gain']['gain_db'],6)
        finally:s.audio_router.close()

if __name__=='__main__':unittest.main()
