import io,threading,types,unittest
import numpy as np
import launcher
from audio_source_router import AudioSourceRouter
from test_audio_source_router import Sink

class Tests(unittest.TestCase):
    def test_actual_session_buttons_route_real_pcm_sources(self):
        m=launcher.load_monitor();s=m.Session.__new__(m.Session);events=[];sinks=[]
        def factory(mode):
            assert all(x.closed for x in sinks)
            x=Sink(mode,events);sinks.append(x);return x
        route=AudioSourceRouter(sink_factory=factory)
        modes=[];s.raw_preview=types.SimpleNamespace(set_listen_mode=modes.append,health=lambda:dict(state='unsupported_nonzero_elevation'))
        s.audio_router=route;s.play_lock=threading.Lock();s.lock=threading.Lock();s.player=None;s.audio_mode=0
        s.listen_gain=m.ListenGain(0);s.output_level=m.PcmLevel();s.audio_levels={2:m.PcmLevel(),3:m.PcmLevel()}
        s.listen_last={};s.audio_log=io.StringIO();s.errors={'host_preview':'old guard'};s.board_audio_processing={}
        pcm=b'\1\0'*16
        try:
            for mode in (2,3,2):
                s.listen(mode);s.play_packet((2,0,0,1,pcm));s.play_packet((3,0,0,1,pcm))
                self.assertEqual(route.snapshot()['source'],{2:'board_mono_16k',3:'board_beam_16k'}[mode])
                self.assertEqual(sinks[-1].mode,mode);self.assertEqual(sinks[-1].samples,16)
            self.assertNotIn('host_preview',s.errors)
            s.listen(4);s.play_packet((3,0,0,1,pcm));self.assertIsNone(route.player)
            snap=s.audio_snapshot();self.assertEqual(snap['mode'],4);self.assertEqual(snap['gain']['gain_db'],0.)
            self.assertEqual(snap['pc_state']['state'],'unsupported_nonzero_elevation')
            self.assertTrue(route.prepare_pc());self.assertTrue(route.feed_pc(np.zeros(512),route.ticket()))
            s.listen(0);self.assertTrue(sinks[-1].closed);self.assertEqual(s.audio_snapshot()['mode'],0)
            self.assertEqual(modes,[2,3,2,4,0])
        finally:route.close()

if __name__=='__main__':unittest.main()
