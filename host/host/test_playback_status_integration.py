"""Actual snapshot/feed methods with in-memory state; no WinDLL/device/thread construction."""
import collections,sys,threading,time,unittest,importlib.util
from pathlib import Path
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
import numpy as np
legacy_spec=importlib.util.spec_from_file_location('status_historical_waveout16',Path(__file__).parent/'regression_baselines/legacy_audio16/audio_playback.py')
legacy_player=importlib.util.module_from_spec(legacy_spec);legacy_spec.loader.exec_module(legacy_player)
WaveOut=legacy_player.WaveOut # Preserve the original 16k legacy-status compatibility case.
from base.playback48 import WaveOut48
from audio_source_router import AudioSourceRouter
from audio_status import playback_text
from ui_audio_controls import playback_caption


class MemorySink:
    def __init__(self,mode):
        self.mode=mode;self.closed=False
        if mode==4:
            p=WaveOut48.__new__(WaveOut48);p.counts=collections.Counter();p.q=collections.deque();p.busy=[False]*4
            p.gain=1.;p.capacity=12
        else:
            p=WaveOut.__new__(WaveOut);p.fed_samples=p.submitted_samples=p.completed_samples=0
            p.last_completed=None;p.data=bytearray();p.dropped=0
        p.lock=threading.Lock();p.error=None;p.closed=False;self.native=p
    def feed(self,data):self.native.feed(data)
    def snapshot(self):return self.native.snapshot()
    def close(self):self.closed=True;self.native.closed=True
    def complete(self,count):
        if self.mode==4:
            self.native.counts.update(completed_samples=count,submitted_samples=count);self.native.q.clear()
        else:
            self.native.completed_samples=count;self.native.submitted_samples=count;self.native.last_completed=time.monotonic()


def status(player,mode=4,route=None):
    level=dict(packets=1,age_seconds=.01,ac_dbfs=-20)
    return dict(mode=mode,raw=level,beam=level,output=level,player=player,audio_route=route,
                pc_state={'state':'spatial_3d_board_lock_active'},gain={'gain_db':0,'limiter_reduction_db':0})


class PlaybackStatus(unittest.TestCase):
    def test_actual_sparse48_initial_and_first_feed_do_not_claim_completion_or_crash(self):
        sink=MemorySink(4)
        for raw in (sink.snapshot(),self._feed_and_snapshot(sink)):
            self.assertNotIn('completed_samples',raw);self.assertNotIn('submitted_samples',raw)
            text=playback_text(status(raw));caption=playback_caption(status(raw))
            self.assertNotIn('近期更新',text);self.assertNotIn('正在播放',caption)
            self.assertIn('等待',caption)
    @staticmethod
    def _feed_and_snapshot(sink):sink.feed(np.zeros(512));return sink.snapshot()
    def test_actual48_completed_counter_without_timestamp_is_reported_honestly(self):
        sink=MemorySink(4);sink.complete(512);raw=sink.snapshot()
        self.assertNotIn('completion_age_seconds',raw)
        self.assertIn('完成 512 样本',playback_text(status(raw)))
        self.assertIn('完成时刻未知',playback_text(status(raw)))
        self.assertIn('完成记录',playback_caption(status(raw)))
        self.assertNotIn('正在播放',playback_caption(status(raw)))
    def test_router_completion_requires_counter_progress_and_flush_resets_baseline(self):
        made=[]
        def factory(mode):s=MemorySink(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory);r.select(4);owner=r.register_pc_owner()
        r.prepare_pc(owner);r.feed_pc(np.zeros(512),r.ticket(),owner);made[-1].complete(512)
        first=r.snapshot()['player'];self.assertIsNone(first['completion_age_seconds'])
        self.assertIsNone(r.snapshot()['player']['completion_age_seconds'])
        made[-1].complete(512);progress=r.snapshot()['player']
        self.assertEqual(progress['completion_age_source'],'counter-observation')
        self.assertFalse(progress['completion_age_is_event']);self.assertIn('正在播放',playback_caption(status(progress)))
        r.flush_pc(owner);r.prepare_pc(owner);r.feed_pc(np.zeros(512),r.ticket(),owner);made[-1].complete(512)
        self.assertIsNone(r.snapshot()['player']['completion_age_seconds'])
        r.close()
    def test_sink_and_router_errors_remain_visible_for_main_and_diagnostics(self):
        sink=MemorySink(4);sink.native.error='playback underflow; no concealment';sink.native.counts['underflow']=1
        self.assertIn('播放错误',playback_text(status(sink.snapshot())))
        self.assertIn('播放异常',playback_caption(status(sink.snapshot())))
        muted=status(None,mode=0,route={'error':'Previous audio device shutdown pending'})
        self.assertIn('播放通路异常',playback_caption(muted));self.assertIn('shutdown pending',playback_text(muted))
    def test_actual16_to_48_to_mute_paths_render_both_interfaces(self):
        made=[]
        def factory(mode):s=MemorySink(mode);made.append(s);return s
        r=AudioSourceRouter(sink_factory=factory)
        r.select(2);r.feed_board(2,b'\x01\x00'*512,r.ticket());made[-1].complete(512)
        route=r.snapshot();self.assertEqual(route['player']['backend_schema'],'waveout16')
        self.assertIn('正在播放',playback_caption(status(route['player'],2,route)))
        playback_text(status(route['player'],2,route))
        r.select(4);self.assertTrue(made[-1].closed);owner=r.register_pc_owner();r.prepare_pc(owner);r.feed_pc(np.zeros(512),r.ticket(),owner)
        route=r.snapshot();self.assertEqual(route['player']['backend_schema'],'waveout48')
        self.assertIsNone(route['player']['dropped_samples']);self.assertNotIn('completed_samples',route['player']['backend_snapshot'])
        playback_text(status(route['player'],4,route));playback_caption(status(route['player'],4,route))
        r.select(0);self.assertTrue(made[-1].closed);route=r.snapshot()
        self.assertEqual(playback_caption(status(route['player'],0,route)),'已静音')
        playback_text(status(route['player'],0,route))
    def test_partial_unknown_metrics_are_not_invented_or_allowed_to_crash_text(self):
        partial={'error':None}
        text=playback_text(status(partial));caption=playback_caption(status(partial))
        self.assertIn('等待',text);self.assertIn('等待',caption)


if __name__=='__main__':unittest.main()
